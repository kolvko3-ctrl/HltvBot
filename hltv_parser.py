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

    # ── СТАТИСТИКА КОМАНДЫ (из последних 20 матчей) ───────────────────
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
            if i < 5: last5_w += won; last5_t += 1
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
        if total == 0: base["_estimated"] = True; return base

        base["winrate"] = round(wins / total * 100, 1)
        if last5_t > 0: base["winrate_last5"] = round(last5_w / last5_t * 100, 1)
        base["form"] = form or "?????"
        base["streak"] = f"{streak_ch}{streak}" if streak_ch else None
        base["maps_played"] = maps_played
        if round_diffs: base["avg_round_diff"] = round(sum(round_diffs) / len(round_diffs), 1)
        return base

    # ── ИГРОКИ КОМАНДЫ ────────────────────────────────────────────────
    async def get_team_players(self, team_id) -> list[dict]:
        """Состав команды."""
        if not team_id: return []
        data = await self._get(f"/teams/{team_id}")
        if not data or not isinstance(data, dict): return []
        return [
            {"id": p.get("id"), "name": p.get("name"), "slug": p.get("slug"),
             "nationality": p.get("nationality"), "role": p.get("role")}
            for p in (data.get("players") or [])
        ]

    async def get_player_stats(self, player_id, player_name) -> dict:
        """Статистика игрока за последние матчи."""
        base = {
            "name": player_name, "id": player_id,
            "kills_per_round": None, "deaths_per_round": None,
            "kd_ratio": None, "headshot_pct": None,
            "rating": None, "maps_played": None,
        }
        if not player_id: return base

        # Статистика через /csgo/players/{id}/stats с games_count
        data = await self._get(f"/csgo/players/{player_id}/stats", {"games_count": 20})
        if not data or not isinstance(data, dict): return base

        try:
            stats = data  # ответ сам и есть объект статистики
            base["kills_per_round"] = stats.get("kills_per_round") or stats.get("average_kills_per_round")
            base["deaths_per_round"] = stats.get("deaths_per_round") or stats.get("average_deaths_per_round")
            base["headshot_pct"] = stats.get("headshot_percentage") or stats.get("headshots_percentage")
            base["maps_played"] = stats.get("games_count") or stats.get("maps_played")

            k = base["kills_per_round"]
            d = base["deaths_per_round"]
            if k and d and d > 0:
                base["kd_ratio"] = round(k / d, 2)
            elif k and d:
                base["kd_ratio"] = None
        except Exception as e:
            logger.debug(f"player_stats {player_name}: {e}")
        return base

    async def get_both_teams_players(self, team1_id, team2_id) -> tuple[list, list]:
        """Параллельно грузим составы обеих команд и их стату."""
        players1_raw, players2_raw = await asyncio.gather(
            self.get_team_players(team1_id),
            self.get_team_players(team2_id),
        )

        async def enrich(players):
            tasks = [self.get_player_stats(p["id"], p["name"]) for p in players]
            stats = await asyncio.gather(*tasks)
            result = []
            for p, s in zip(players, stats):
                result.append({**p, **s})
            return result

        t1_players, t2_players = await asyncio.gather(
            enrich(players1_raw),
            enrich(players2_raw),
        )
        return t1_players, t2_players

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
                    won = w_id == team1_id
                    result["last_matches"].append({
                        "date": sched, "winner": team1_name if won else team2_name,
                        "format": f"BO{ng}",
                    })
                except: pass
        return result

    async def get_top_teams(self, limit=10) -> list[dict]:
        data = await self._get("/csgo/teams", {"sort": "-current_videogame_title", "per_page": limit})
        return [{"rank": i+1, "name": t.get("name","?"), "id": t.get("id")}
                for i, t in enumerate(data or [])][:limit]

    async def inject_ranks(self, matches): return matches
