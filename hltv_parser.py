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
        end = now + timedelta(days=2)
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        upcoming, live = await asyncio.gather(
            self._get("/csgo/matches/upcoming", {
                "range[scheduled_at]": f"{now.strftime(fmt)},{end.strftime(fmt)}",
                "sort": "scheduled_at", "per_page": 30,
            }),
            self._get("/csgo/matches/running", {"per_page": 10}),
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
                "match_id": m.get("id"), "event": event,
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

    # ── СТАТИСТИКА КОМАНДЫ ───────────────────────────────────────────
    async def get_team_stats(self, team_id, team_name) -> dict:
        base = {
            "name": team_name, "id": team_id,
            "winrate": None, "winrate_last5": None,
            "form": None, "streak": None,
            "maps_played": None, "avg_round_diff": None,
            "_estimated": False,
        }
        if not team_id:
            base["_estimated"] = True; return base

        data = await self._get(f"/teams/{team_id}/matches", {
            "filter[videogame]": "cs-go",
            "sort": "-scheduled_at", "per_page": 20,
            "filter[status]": "finished",
        })
        if not data:
            base["_estimated"] = True; return base

        wins = losses = 0
        last5_w = last5_t = 0
        form = ""
        streak = 0; streak_ch = None
        round_diffs = []; maps_played = 0

        for i, m in enumerate(data):
            w_id = (m.get("winner") or {}).get("id")
            if w_id is None: continue
            won = (w_id == team_id)
            wins += won; losses += (not won)
            ch = "W" if won else "L"
            if len(form) < 5: form += ch
            if i < 5:
                last5_w += won; last5_t += 1
            if streak_ch is None: streak_ch = ch; streak = 1
            elif ch == streak_ch: streak += 1
            for game in (m.get("games") or []):
                maps_played += 1
                res = game.get("results") or []
                scores = {r["team"]["id"]: r["score"] for r in res
                          if r.get("team") and r.get("score") is not None}
                if len(scores) == 2:
                    s_us = scores.get(team_id, 0)
                    s_them = next((v for k, v in scores.items() if k != team_id), 0)
                    round_diffs.append(s_us - s_them)

        total = wins + losses
        if total == 0:
            base["_estimated"] = True; return base

        base["winrate"] = round(wins / total * 100, 1)
        if last5_t > 0: base["winrate_last5"] = round(last5_w / last5_t * 100, 1)
        base["form"] = form or "?????"
        base["streak"] = f"{streak_ch}{streak}" if streak_ch else None
        base["maps_played"] = maps_played
        if round_diffs: base["avg_round_diff"] = round(sum(round_diffs) / len(round_diffs), 1)
        return base

    # ── ИГРОКИ: добываем статистику из game-level данных ─────────────
    async def get_team_players(self, team_id) -> list[dict]:
        """Состав команды."""
        if not team_id: return []
        data = await self._get(f"/teams/{team_id}")
        if not data or not isinstance(data, dict): return []
        return [
            {"id": p.get("id"), "name": p.get("name"), "slug": p.get("slug")}
            for p in (data.get("players") or [])
        ]

    async def get_players_stats_from_games(self, team_id: int, team_name: str) -> list[dict]:
        """
        Собираем статистику игроков из последних 5 матчей команды.
        Endpoint /csgo/games/{id} возвращает players[] с kills/deaths/assists
        прямо внутри объекта игры — это бесплатно.
        """
        if not team_id: return []

        # Берём последние 5 завершённых матчей
        matches = await self._get(f"/teams/{team_id}/matches", {
            "filter[videogame]": "cs-go",
            "sort": "-scheduled_at", "per_page": 5,
            "filter[status]": "finished",
        })
        if not matches: return []

        # Собираем ID всех игр из этих матчей
        game_ids = []
        for m in matches:
            for g in (m.get("games") or []):
                gid = g.get("id")
                if gid and len(game_ids) < 10:
                    game_ids.append(gid)

        if not game_ids: return []

        # Грузим детальные данные игр параллельно
        game_data_list = await asyncio.gather(*[
            self._get(f"/csgo/games/{gid}") for gid in game_ids
        ])

        # Агрегируем статистику по игрокам
        player_stats: dict[int, dict] = {}

        for game_data in game_data_list:
            if not game_data or not isinstance(game_data, dict): continue

            # Игроки лежат в players[] прямо в объекте игры
            for player in (game_data.get("players") or []):
                pid = player.get("id")
                pname = player.get("name") or player.get("player", {}).get("name")
                if not pid or not pname: continue

                # Проверяем что игрок из нашей команды
                p_team_id = (player.get("team") or {}).get("id")
                if p_team_id and p_team_id != team_id: continue

                kills = player.get("kills") or 0
                deaths = player.get("deaths") or 0
                assists = player.get("assists") or 0
                hs = player.get("headshots") or 0

                if pid not in player_stats:
                    player_stats[pid] = {
                        "id": pid, "name": pname,
                        "kills": 0, "deaths": 0,
                        "assists": 0, "headshots": 0,
                        "games": 0,
                    }

                player_stats[pid]["kills"] += kills
                player_stats[pid]["deaths"] += deaths
                player_stats[pid]["assists"] += assists
                player_stats[pid]["headshots"] += hs
                player_stats[pid]["games"] += 1

        # Считаем K/D и HS%
        result = []
        for p in player_stats.values():
            k, d, a = p["kills"], p["deaths"], p["assists"]
            hs = p["headshots"]
            games = p["games"]
            kd = round(k / d, 2) if d > 0 else (float(k) if k > 0 else None)
            hs_pct = round(hs / k * 100, 1) if k > 0 else None
            kpr = round(k / games, 2) if games > 0 else None   # kills per map
            result.append({
                "id": p["id"],
                "name": p["name"],
                "kd_ratio": kd,
                "kills_per_round": kpr,   # здесь это kills per game/map
                "headshot_pct": hs_pct,
                "assists": a,
                "maps_played": games,
            })

        # Сортируем по K/D
        result.sort(key=lambda x: x.get("kd_ratio") or 0, reverse=True)
        return result

    async def get_both_teams_players(self, team1_id, team2_id) -> tuple[list, list]:
        t1p, t2p = await asyncio.gather(
            self.get_players_stats_from_games(team1_id, "t1"),
            self.get_players_stats_from_games(team2_id, "t2"),
        )
        return t1p, t2p

    # ── H2H ──────────────────────────────────────────────────────────
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

    async def get_top_teams(self, limit=10) -> list[dict]:
        data = await self._get("/csgo/teams", {"sort": "-current_videogame_title", "per_page": limit})
        return [{"rank": i+1, "name": t.get("name","?"), "id": t.get("id")}
                for i, t in enumerate(data or [])][:limit]

    async def inject_ranks(self, matches): return matches
