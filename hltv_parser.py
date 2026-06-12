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

    # ── ИГРОКИ ───────────────────────────────────────────────────────
    async def get_both_teams_players(self, team1_id, team2_id) -> tuple[list, list]:
        t1p, t2p = await asyncio.gather(
            self._get_players_all_methods(team1_id),
            self._get_players_all_methods(team2_id),
        )
        return t1p, t2p

    async def _get_players_all_methods(self, team_id) -> list[dict]:
        """Пробуем все способы получить статистику игроков."""
        if not team_id: return []

        # Метод 1: /csgo/players с фильтром по команде
        result = await self._players_via_csgo_players(team_id)
        if result:
            logger.info(f"Players via /csgo/players: {len(result)} for team {team_id}")
            return result

        # Метод 2: из игр матчей
        result = await self._players_via_games(team_id)
        if result:
            logger.info(f"Players via games: {len(result)} for team {team_id}")
            return result

        # Метод 3: только имена из /teams/{id}
        result = await self._players_names_only(team_id)
        logger.info(f"Players names only: {len(result)} for team {team_id}")
        return result

    async def _players_via_csgo_players(self, team_id) -> list[dict]:
        """GET /csgo/players?filter[team_id]=X"""
        data = await self._get("/csgo/players", {
            "filter[team_id]": team_id,
            "per_page": 10,
        })
        if not data or not isinstance(data, list): return []

        players = []
        # Грузим стату каждого игрока параллельно
        async def get_stat(p):
            pid = p.get("id")
            pname = p.get("name") or "?"
            stat = await self._get(f"/csgo/players/{pid}/stats", {"games_count": 20})
            if not stat or not isinstance(stat, dict):
                return {"id": pid, "name": pname, "kd_ratio": None,
                        "headshot_pct": None, "maps_played": None,
                        "kills_per_round": None}
            # Логируем ключи первый раз
            logger.info(f"Stats keys for {pname}: {list(stat.keys())[:15]}")
            kpr  = stat.get("kills_per_round") or stat.get("average_kills_per_round") or stat.get("kill_per_round")
            dpr  = stat.get("deaths_per_round") or stat.get("average_deaths_per_round")
            hs   = stat.get("headshot_percentage") or stat.get("headshots_percentage") or stat.get("hs_percentage")
            maps = stat.get("games_count") or stat.get("maps_played")
            kd   = round(float(kpr)/float(dpr), 2) if kpr and dpr and float(dpr) > 0 else None
            return {
                "id": pid, "name": pname,
                "kd_ratio": kd,
                "kills_per_round": float(kpr) if kpr else None,
                "headshot_pct": float(hs) if hs else None,
                "maps_played": int(maps) if maps else None,
            }

        results = await asyncio.gather(*[get_stat(p) for p in data[:7]])
        valid = [r for r in results if r.get("name")]
        valid.sort(key=lambda x: x.get("kd_ratio") or 0, reverse=True)
        return valid

    async def _players_via_games(self, team_id) -> list[dict]:
        """Берём данные игроков из /csgo/games/{id} — поля players[]."""
        matches = await self._get(f"/teams/{team_id}/matches", {
            "filter[videogame]": "cs-go", "filter[status]": "finished",
            "sort": "-scheduled_at", "per_page": 5,
        })
        if not matches: return []

        game_ids = []
        for m in matches:
            for g in (m.get("games") or []):
                gid = g.get("id")
                if gid and len(game_ids) < 8:
                    game_ids.append(gid)
        if not game_ids: return []

        game_datas = await asyncio.gather(*[self._get(f"/csgo/games/{gid}") for gid in game_ids])

        agg: dict[int, dict] = {}
        for gd in game_datas:
            if not gd or not isinstance(gd, dict): continue

            # Пробуем разные места где могут быть игроки
            players_raw = gd.get("players") or []

            # Иногда игроки внутри teams
            if not players_raw:
                for team in (gd.get("teams") or []):
                    if (team.get("id") == team_id or
                        (team.get("team") or {}).get("id") == team_id):
                        players_raw = team.get("players") or []
                        break

            for p in players_raw:
                # Проверяем принадлежность к команде
                p_team = (p.get("team") or {}).get("id") or p.get("team_id")
                if p_team and p_team != team_id:
                    continue

                pid  = p.get("id") or (p.get("player") or {}).get("id")
                name = p.get("name") or (p.get("player") or {}).get("name") or p.get("nickname")
                if not pid or not name: continue

                k  = int(p.get("kills") or p.get("kill_count") or 0)
                d  = int(p.get("deaths") or p.get("death_count") or 0)
                a  = int(p.get("assists") or p.get("assist_count") or 0)
                hs = int(p.get("headshots") or p.get("headshot_kills") or 0)

                if pid not in agg:
                    agg[pid] = {"id": pid, "name": name, "k": 0, "d": 0, "a": 0, "hs": 0, "g": 0}
                agg[pid]["k"] += k
                agg[pid]["d"] += d
                agg[pid]["a"] += a
                agg[pid]["hs"] += hs
                agg[pid]["g"] += 1

        if not agg: return []

        result = []
        for p in agg.values():
            k, d, g = p["k"], p["d"], p["g"]
            kd  = round(k / d, 2) if d > 0 else (float(k) if k > 0 else None)
            hs_pct = round(p["hs"] / k * 100, 1) if k > 0 else None
            result.append({
                "id": p["id"], "name": p["name"],
                "kd_ratio": kd,
                "kills_per_round": round(k / g, 1) if g > 0 else None,
                "headshot_pct": hs_pct,
                "maps_played": g,
                "assists": p["a"],
            })
        result.sort(key=lambda x: x.get("kd_ratio") or 0, reverse=True)
        return result

    async def _players_names_only(self, team_id) -> list[dict]:
        """Фолбэк — хотя бы имена из /teams/{id}."""
        data = await self._get(f"/teams/{team_id}")
        if not data or not isinstance(data, dict): return []
        return [
            {"id": p.get("id"), "name": p.get("name") or "?",
             "kd_ratio": None, "headshot_pct": None, "maps_played": None,
             "kills_per_round": None}
            for p in (data.get("players") or [])
        ]

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
        # Метод 1: прямой список команд с сортировкой
        data = await self._get("/csgo/teams", {
            "sort": "-current_team_ranking",
            "per_page": limit,
        })
        if data:
            result = [{"rank": i+1, "name": t.get("name","?"), "id": t.get("id")}
                      for i, t in enumerate(data)][:limit]
            if result:
                return result

        # Метод 2 (fallback): собираем уникальные команды из последних матчей
        matches = await self._get("/csgo/matches", {
            "filter[status]": "finished",
            "sort": "-scheduled_at",
            "per_page": 50,
        })
        if not matches:
            return []

        seen: dict[int, str] = {}
        for m in matches:
            for opp in (m.get("opponents") or []):
                t = opp.get("opponent", {})
                tid = t.get("id")
                name = t.get("name", "?")
                if tid and tid not in seen:
                    seen[tid] = name
            if len(seen) >= limit:
                break

        return [{"rank": i+1, "name": name, "id": tid}
                for i, (tid, name) in enumerate(list(seen.items())[:limit])]

    async def inject_ranks(self, matches): return matches
