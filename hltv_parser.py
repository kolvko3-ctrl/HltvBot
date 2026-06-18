import logging
import asyncio
from datetime import datetime, timezone, timedelta
import aiohttp

logger = logging.getLogger(__name__)
BASE = "https://api.pandascore.co"


class HLTVParser:
    def __init__(self, token: str):
        self.token = token
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    async def _get(self, endpoint: str, params: dict = None) -> list | dict | None:
        url = f"{BASE}{endpoint}"
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
                async with s.get(url, headers=self.headers, params=params or {}) as r:
                    if r.status == 200:
                        return await r.json()
                    logger.warning(f"PandaScore {r.status} {endpoint}")
                    return None
        except Exception as e:
            logger.error(f"Запрос {endpoint}: {e}")
            return None

    # ── МАТЧИ ────────────────────────────────────────────────────────
    async def get_today_matches(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=3)
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        upcoming, live = await asyncio.gather(
            self._get("/csgo/matches/upcoming", {
                "range[scheduled_at]": f"{now.strftime(fmt)},{end.strftime(fmt)}",
                "sort": "scheduled_at", "per_page": 50,
            }),
            self._get("/csgo/matches/running", {"per_page": 20}),
        )
        matches = []
        for m in (live or []):
            p = self._parse_match(m, live=True)
            if p: matches.append(p)
        for m in (upcoming or []):
            p = self._parse_match(m, live=False)
            if p: matches.append(p)
        return matches

    def _parse_match(self, m, live):
        try:
            opp = m.get("opponents", [])
            if len(opp) < 2: return None
            t1d = opp[0].get("opponent", {})
            t2d = opp[1].get("opponent", {})
            t1, t2 = t1d.get("name"), t2d.get("name")
            if not t1 or not t2: return None
            sched = m.get("scheduled_at") or m.get("begin_at")
            time_str = "LIVE"
            if not live and sched:
                try:
                    dt = datetime.fromisoformat(sched.replace("Z", "+00:00")) + timedelta(hours=3)
                    time_str = dt.strftime("%H:%M")
                except: time_str = "TBD"
            league = m.get("league", {}).get("name") or ""
            serie = m.get("serie", {}).get("full_name") or ""
            event = serie or league or "CS2"
            ng = m.get("number_of_games")
            return {
                "team1": t1, "team2": t2,
                "team1_id": t1d.get("id"), "team2_id": t2d.get("id"),
                "match_id": m.get("id"),
                "tournament_id": (m.get("tournament") or {}).get("id"),
                "event": event,
                "time": time_str, "maps": f"BO{ng}" if ng else "",
                "stars": self._tier(m), "live": live,
            }
        except Exception as e:
            logger.debug(f"parse_match: {e}"); return None

    def _tier(self, m):
        ln = (m.get("league", {}).get("name") or "").lower()
        if any(x in ln for x in ["major", "blast", "iem", "pro league", "esl"]): return 3
        t = (m.get("tournament", {}).get("tier") or "").lower()
        return {"s": 3, "a": 2, "b": 1}.get(t, 0)

    # ── РЕАЛЬНЫЙ АКТИВНЫЙ СОСТАВ ─────────────────────────────────────
    async def get_team_players(self, team_id: int | None, tournament_id: int | None = None) -> list[str]:
        """
        Получает АКТИВНЫЙ состав команды на турнире.
        Источник в порядке приоритета:
          1. /tournaments/{id}/rosters — официальный способ PandaScore
             для получения заявленных составов на конкретный турнир.
             Это решает проблему "старых составов": ростер привязан
             к турниру, а не к команде вообще.
          2. /csgo/games/{id} последней сыгранной карты — реальные
             игроки которые физически были в игре.
          3. /teams/{id} как последний fallback (полный список, без
             привязки к актуальности — отсюда и были старые составы).
        """
        if not team_id: return []

        # Способ 1: официальный ростер турнира
        if tournament_id:
            roster = await self._get_tournament_roster(tournament_id, team_id)
            if roster: return roster

        # Способ 2: реальные игроки из последней сыгранной карты
        roster = await self._get_roster_from_last_game(team_id)
        if roster: return roster

        # Способ 3 (последний fallback): общий список команды
        data = await self._get(f"/teams/{team_id}")
        if not data or not isinstance(data, dict): return []
        players = data.get("players") or []
        names = [p["name"] for p in players if p.get("name")]
        return names[:5] if names else []

    async def _get_tournament_roster(self, tournament_id: int, team_id: int) -> list[str]:
        """/tournaments/{id}/rosters — официальный эндпоинт ожидаемых составов."""
        data = await self._get(f"/tournaments/{tournament_id}/rosters")
        if not data or not isinstance(data, list): return []
        for entry in data:
            team = entry.get("team", {})
            if team.get("id") != team_id:
                continue
            players = entry.get("players") or []
            names = [p["name"] for p in players if p.get("name")]
            if names: return names[:5]
        return []

    async def _get_roster_from_last_game(self, team_id: int) -> list[str]:
        """
        Берёт игроков из реального game-объекта последней сыгранной карты.
        В CS2 players[] на уровне игры содержит тех, кто физически играл —
        это надёжнее чем команда вообще, т.к. отражает текущий состав.
        """
        recent = await self._get(f"/teams/{team_id}/matches", {
            "filter[videogame]": "cs-go",
            "sort": "-scheduled_at", "per_page": 3,
            "filter[status]": "finished",
        })
        if not recent: return []

        for m in recent:
            games = m.get("games") or []
            for g in games:
                game_id = g.get("id")
                if not game_id: continue
                game_data = await self._get(f"/csgo/games/{game_id}")
                if not game_data or not isinstance(game_data, dict): continue

                # Игроки лежат в players[] на уровне игры, у каждого team_id
                all_players = game_data.get("players") or []
                team_players = [
                    p for p in all_players
                    if (p.get("team", {}) or {}).get("id") == team_id
                ]
                names = [p["name"] for p in team_players if p.get("name")]
                if names: return list(dict.fromkeys(names))[:5]  # убираем дубли, сохраняя порядок
        return []

    async def get_both_rosters(self, team1_id, team2_id, tournament_id: int | None = None) -> tuple[list[str], list[str]]:
        """Оба состава параллельно, привязаны к турниру если он известен."""
        r1, r2 = await asyncio.gather(
            self.get_team_players(team1_id, tournament_id),
            self.get_team_players(team2_id, tournament_id),
        )
        return r1, r2

    # ── СТАТИСТИКА КОМАНДЫ ──────────────────────────────────────────
    async def get_team_stats(self, team_id, team_name) -> dict:
        """
        Берём последние 7 матчей.
        Вычисляем ВЗВЕШЕННЫЙ winrate: последний матч весит 1.0,
        самый старый — 0.4. Свежая форма важнее.
        """
        base = {
            "name": team_name, "id": team_id,
            "winrate": None,
            "weighted_winrate": None,   # главный показатель — взвешенный
            "form": None,
            "maps_played": None,
            "avg_round_diff": None,
            "recent_matches": [],       # детали для Groq
            "_estimated": False,
        }
        if not team_id:
            base["_estimated"] = True; return base

        data = await self._get(f"/teams/{team_id}/matches", {
            "filter[videogame]": "cs-go",
            "sort": "-scheduled_at", "per_page": 7,   # только 7 последних
            "filter[status]": "finished",
        })
        if not data:
            base["_estimated"] = True; return base

        # Убывающие веса: самый свежий матч = 1.0, самый старый = 0.4
        WEIGHTS = [1.0, 0.85, 0.7, 0.6, 0.5, 0.45, 0.4]

        weighted_score = 0.0
        total_weight = 0.0
        wins = losses = 0
        form = ""
        round_diffs = []
        maps_played = 0
        recent = []

        for i, m in enumerate(data):
            w_id = (m.get("winner") or {}).get("id")
            if w_id is None: continue
            won = (w_id == team_id)
            w = WEIGHTS[i] if i < len(WEIGHTS) else 0.35

            wins += won; losses += (not won)
            weighted_score += w * (1 if won else 0)
            total_weight += w
            if len(form) < 7: form += ("W" if won else "L")

            # Opponent name for context
            opps = m.get("opponents") or []
            opp_name = ""
            for o in opps:
                n = o.get("opponent", {}).get("name", "")
                if n and n.lower() != team_name.lower():
                    opp_name = n; break

            recent.append({
                "result": "W" if won else "L",
                "opponent": opp_name,
                "weight": round(w, 2),
            })

            for game in (m.get("games") or []):
                maps_played += 1
                res = game.get("results") or []
                scores = {r["team"]["id"]: r["score"] for r in res
                          if r.get("team") and r.get("score") is not None}
                if len(scores) == 2:
                    s_us = scores.get(team_id, 0)
                    s_them = next((v for k,v in scores.items() if k != team_id), 0)
                    round_diffs.append((s_us - s_them, w))  # сохраняем вес матча

        total = wins + losses
        if total == 0: base["_estimated"] = True; return base

        base["winrate"] = round(wins / total * 100, 1)
        if total_weight > 0:
            base["weighted_winrate"] = round(weighted_score / total_weight * 100, 1)
        base["form"] = form or "???????"
        base["maps_played"] = maps_played
        base["recent_matches"] = recent

        # Взвешенная разница раундов — те же веса что и winrate
        if round_diffs:
            num = sum(diff * w for diff, w in round_diffs)
            den = sum(w for _, w in round_diffs)
            base["avg_round_diff"] = round(num / den, 1) if den > 0 else None
        return base

    # ── H2H ────────────────────────────────────────────────────────
    async def get_h2h(self, team1_id, team2_id, team1_name, team2_name) -> dict:
        result = {"team1_wins": 0, "team2_wins": 0, "total": 0, "last_matches": []}
        if not team1_id or not team2_id: return result
        data = await self._get("/csgo/matches", {
            "filter[opponent_id]": f"{team1_id},{team2_id}",
            "filter[status]": "finished",
            "sort": "-scheduled_at", "per_page": 10,
        })
        for m in (data or []):
            opp_ids = {o.get("opponent", {}).get("id") for o in m.get("opponents", [])}
            if team1_id not in opp_ids or team2_id not in opp_ids: continue
            w_id = (m.get("winner") or {}).get("id")
            if w_id == team1_id: result["team1_wins"] += 1
            elif w_id == team2_id: result["team2_wins"] += 1
            result["total"] += 1
            if len(result["last_matches"]) < 3:
                try:
                    sched = (m.get("scheduled_at") or "")[:10]
                    ng = m.get("number_of_games", "?")
                    result["last_matches"].append({
                        "date": sched,
                        "winner": team1_name if w_id == team1_id else team2_name,
                        "format": f"BO{ng}",
                    })
                except: pass
        return result

    # ── ТОП КОМАНДЫ ────────────────────────────────────────────────
    async def get_top_teams(self, limit=20) -> list[dict]:
        # Актуальный рейтинг HLTV июнь 2026 (Valve ranking + HLTV)
        HLTV_TOP = [
            {"rank":1,  "name":"Team Vitality",    "flag":"🇫🇷"},
            {"rank":2,  "name":"Team Spirit",       "flag":"🇷🇺"},
            {"rank":3,  "name":"Team Falcons",      "flag":"🇸🇦"},
            {"rank":4,  "name":"Natus Vincere",     "flag":"🇺🇦"},
            {"rank":5,  "name":"BetBoom Team",      "flag":"🇷🇺"},
            {"rank":6,  "name":"FURIA",             "flag":"🇧🇷"},
            {"rank":7,  "name":"The MongolZ",       "flag":"🇲🇳"},
            {"rank":8,  "name":"9z Team",           "flag":"🇦🇷"},
            {"rank":9,  "name":"Aurora Gaming",     "flag":"🌍"},
            {"rank":10, "name":"MOUZ",              "flag":"🇩🇪"},
            {"rank":11, "name":"G2 Esports",        "flag":"🇪🇸"},
            {"rank":12, "name":"Team Liquid",       "flag":"🇺🇸"},
            {"rank":13, "name":"Heroic",            "flag":"🇩🇰"},
            {"rank":14, "name":"Virtus.pro",        "flag":"🇷🇺"},
            {"rank":15, "name":"FaZe Clan",         "flag":"🌍"},
            {"rank":16, "name":"Astralis",          "flag":"🇩🇰"},
            {"rank":17, "name":"ENCE",              "flag":"🇫🇮"},
            {"rank":18, "name":"Cloud9",            "flag":"🇺🇸"},
            {"rank":19, "name":"MIBR",              "flag":"🇧🇷"},
            {"rank":20, "name":"paiN Gaming",       "flag":"🇧🇷"},
        ]
        return HLTV_TOP[:limit]

    async def inject_ranks(self, matches): return matches
