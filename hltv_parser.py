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
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
                async with s.get(url, headers=self.headers, params=params or {}) as r:
                    if r.status == 200:
                        return await r.json()
                    logger.warning(f"PandaScore {r.status} {endpoint}")
                    return None
        except Exception as e:
            logger.error(f"Запрос {endpoint}: {e}")
            return None

    # ── МАТЧИ ──────────────────────────────────────────────────────
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

    def _parse_match(self, m: dict, live: bool) -> dict | None:
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
            maps = f"BO{ng}" if ng else ""

            return {
                "team1": t1, "team2": t2,
                "team1_id": t1d.get("id"), "team2_id": t2d.get("id"),
                "match_id": m.get("id"), "event": event,
                "time": time_str, "maps": maps,
                "stars": self._tier(m), "live": live,
            }
        except Exception as e:
            logger.debug(f"parse_match: {e}")
            return None

    def _tier(self, m):
        ln = (m.get("league", {}).get("name") or "").lower()
        if any(x in ln for x in ["major", "blast", "iem", "pro league", "esl"]): return 3
        t = (m.get("tournament", {}).get("tier") or "").lower()
        return {"s": 3, "a": 2, "b": 1}.get(t, 0)

    # ── ГЛУБОКАЯ СТАТИСТИКА КОМАНДЫ ─────────────────────────────────
    async def get_team_stats(self, team_id: int | None, team_name: str) -> dict:
        base = {"name": team_name, "rank": None, "winrate": None, "winrate_last5": None,
                "avg_rating": None, "form": None, "streak": None,
                "maps_played": None, "avg_round_diff": None,
                "h2h": None, "_estimated": False, "id": team_id}

        if not team_id:
            base["_estimated"] = True
            return base

        # Берём последние 20 матчей
        data = await self._get(f"/teams/{team_id}/matches", {
            "filter[videogame]": "cs-go",
            "sort": "-scheduled_at", "per_page": 20,
            "filter[status]": "finished",
        })
        if not data:
            base["_estimated"] = True
            return base

        wins = losses = 0
        last5_wins = last5_total = 0
        form = ""
        streak = 0
        streak_char = None
        round_diffs = []
        maps_played = 0

        for i, m in enumerate(data):
            winner = (m.get("winner") or {})
            w_id = winner.get("id")
            if w_id is None:
                continue

            won = (w_id == team_id)
            wins += won
            losses += (not won)
            ch = "W" if won else "L"

            if len(form) < 5:
                form += ch
            if i < 5:
                last5_wins += won
                last5_total += 1

            # Стрик
            if streak_char is None:
                streak_char = ch
                streak = 1
            elif ch == streak_char:
                streak += 1
            # else стрик сломан

            # Карты и разница раундов
            for game in (m.get("games") or []):
                maps_played += 1
                res = game.get("results") or []
                scores = {r["team"]["id"]: r["score"] for r in res if r.get("team") and r.get("score") is not None}
                if len(scores) == 2:
                    ids = list(scores.keys())
                    s_us = scores.get(team_id, 0)
                    s_them = scores.get([x for x in ids if x != team_id][0], 0)
                    round_diffs.append(s_us - s_them)

        total = wins + losses
        if total == 0:
            base["_estimated"] = True
            return base

        base["winrate"] = round(wins / total * 100, 1)
        if last5_total > 0:
            base["winrate_last5"] = round(last5_wins / last5_total * 100, 1)
        base["form"] = form if form else "?????"
        base["streak"] = f"{streak_char}{streak}" if streak_char else None
        base["maps_played"] = maps_played
        if round_diffs:
            base["avg_round_diff"] = round(sum(round_diffs) / len(round_diffs), 1)

        return base

    async def get_h2h(self, team1_id: int, team2_id: int, team1_name: str, team2_name: str) -> dict:
        """История личных встреч двух команд."""
        result = {"team1_wins": 0, "team2_wins": 0, "total": 0, "last_matches": []}
        if not team1_id or not team2_id:
            return result

        # Ищем матчи где участвовали обе команды
        data = await self._get("/csgo/matches", {
            "filter[opponent_id]": f"{team1_id},{team2_id}",
            "filter[status]": "finished",
            "sort": "-scheduled_at", "per_page": 10,
        })

        for m in (data or []):
            opp_ids = {o.get("opponent", {}).get("id") for o in m.get("opponents", [])}
            if team1_id not in opp_ids or team2_id not in opp_ids:
                continue
            winner = (m.get("winner") or {})
            w_id = winner.get("id")
            if w_id == team1_id:
                result["team1_wins"] += 1
            elif w_id == team2_id:
                result["team2_wins"] += 1
            result["total"] += 1
            if len(result["last_matches"]) < 3:
                try:
                    sched = m.get("scheduled_at") or ""
                    date = sched[:10] if sched else "?"
                    won = w_id == team1_id
                    ng = m.get("number_of_games", "?")
                    result["last_matches"].append({
                        "date": date,
                        "winner": team1_name if won else team2_name,
                        "format": f"BO{ng}",
                    })
                except: pass

        return result

    async def get_top_teams(self, limit=10) -> list[dict]:
        data = await self._get("/csgo/teams", {"sort": "-current_videogame_title", "per_page": limit})
        return [{"rank": i+1, "name": t.get("name", "?"), "id": t.get("id")}
                for i, t in enumerate(data or [])][:limit]

    async def inject_ranks(self, matches):
        return matches
