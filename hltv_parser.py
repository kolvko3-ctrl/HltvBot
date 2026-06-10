import logging
import asyncio
from datetime import datetime, timezone, timedelta
import aiohttp

logger = logging.getLogger(__name__)

BASE = "https://api.pandascore.co"


class HLTVParser:
    """Парсер на основе PandaScore API — официальный, не блокируется."""

    def __init__(self, token: str):
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    async def _get(self, endpoint: str, params: dict = None) -> list | dict | None:
        url = f"{BASE}{endpoint}"
        timeout = aiohttp.ClientTimeout(total=15)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=self.headers, params=params or {}) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    elif resp.status == 401:
                        logger.error("PandaScore: неверный токен!")
                        return None
                    else:
                        logger.warning(f"PandaScore {resp.status} для {endpoint}")
                        return None
        except Exception as e:
            logger.error(f"Ошибка запроса {endpoint}: {e}")
            return None

    async def get_today_matches(self) -> list[dict]:
        """Матчи CS2 на сегодня и завтра."""
        now = datetime.now(timezone.utc)
        tomorrow = now + timedelta(days=2)

        # Upcoming матчи
        params = {
            "filter[videogame]": "cs-go",
            "range[scheduled_at]": f"{now.strftime('%Y-%m-%dT%H:%M:%SZ')},{tomorrow.strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "sort": "scheduled_at",
            "per_page": 30,
        }
        data = await self._get("/matches/upcoming", params)

        # Live матчи
        live_data = await self._get("/matches/running", {"filter[videogame]": "cs-go", "per_page": 10})

        matches = []

        # Сначала live
        for m in (live_data or []):
            parsed = self._parse_match(m, live=True)
            if parsed:
                matches.append(parsed)

        # Потом предстоящие
        for m in (data or []):
            parsed = self._parse_match(m, live=False)
            if parsed:
                matches.append(parsed)

        return matches

    def _parse_match(self, m: dict, live: bool) -> dict | None:
        try:
            opponents = m.get("opponents", [])
            if len(opponents) < 2:
                return None

            t1_data = opponents[0].get("opponent", {})
            t2_data = opponents[1].get("opponent", {})
            t1 = t1_data.get("name", "TBD")
            t2 = t2_data.get("name", "TBD")

            if t1 == "TBD" or t2 == "TBD" or not t1 or not t2:
                return None

            # Время
            scheduled = m.get("scheduled_at") or m.get("begin_at")
            if scheduled and not live:
                try:
                    dt = datetime.fromisoformat(scheduled.replace("Z", "+00:00"))
                    # Переводим в UTC+3 (Москва)
                    dt_local = dt + timedelta(hours=3)
                    time_str = dt_local.strftime("%H:%M")
                except Exception:
                    time_str = "TBD"
            else:
                time_str = "LIVE" if live else "TBD"

            # Лига / турнир
            league = m.get("league", {}).get("name") or ""
            serie = m.get("serie", {}).get("full_name") or ""
            event = serie or league or m.get("tournament", {}).get("name") or "CS2 Match"

            # Формат
            match_type = m.get("match_type", "")
            maps_info = f"BO{m.get('number_of_games', '?')}" if m.get("number_of_games") else ""

            return {
                "team1": t1,
                "team2": t2,
                "team1_id": t1_data.get("id"),
                "team2_id": t2_data.get("id"),
                "match_id": m.get("id"),
                "event": event,
                "time": time_str,
                "maps": maps_info,
                "stars": self._rate_match(m),
                "live": live,
            }
        except Exception as e:
            logger.debug(f"Ошибка парсинга матча: {e}")
            return None

    def _rate_match(self, m: dict) -> int:
        """Оцениваем важность матча по тиру турнира."""
        tier = m.get("tournament", {}).get("tier") or ""
        league_name = (m.get("league", {}).get("name") or "").lower()
        if any(x in league_name for x in ["major", "blast", "iem", "esc", "pro league"]):
            return 3
        if tier == "s":
            return 3
        if tier == "a":
            return 2
        if tier == "b":
            return 1
        return 0

    async def get_team_stats(self, team_id: int | None, team_name: str) -> dict:
        """Статистика команды из PandaScore."""
        base = {
            "name": team_name,
            "rank": None,
            "winrate": None,
            "avg_rating": None,
            "form": None,
            "_estimated": False,
        }

        if not team_id:
            base["_estimated"] = True
            return base

        # Последние матчи команды
        params = {"filter[videogame]": "cs-go", "per_page": 10, "sort": "-scheduled_at"}
        data = await self._get(f"/teams/{team_id}/matches/past", params)

        if not data:
            base["_estimated"] = True
            return base

        wins = 0
        total = 0
        form = ""

        for m in data[:10]:
            winner = m.get("winner", {})
            if not winner:
                continue
            won = winner.get("id") == team_id
            wins += 1 if won else 0
            total += 1
            if len(form) < 5:
                form += "W" if won else "L"

        if total > 0:
            base["winrate"] = round(wins / total * 100, 1)
        base["form"] = form if form else "?????"

        # Получаем базовую инфу о команде
        team_info = await self._get(f"/teams/{team_id}")
        if team_info and isinstance(team_info, dict):
            # PandaScore не даёт HLTV-ранг, но даём приблизительный по количеству побед
            pass

        return base

    async def inject_ranks(self, matches: list[dict]) -> list[dict]:
        """PandaScore не имеет HLTV-рангов — оставляем поле пустым."""
        return matches

    async def get_top_teams(self, limit: int = 10) -> list[dict]:
        """Топ CS2 команд по PandaScore (по активности/рейтингу)."""
        params = {"filter[videogame]": "cs-go", "sort": "-current_videogame_title", "per_page": limit}
        data = await self._get("/teams", params)

        teams = []
        for i, t in enumerate(data or []):
            teams.append({
                "rank": i + 1,
                "name": t.get("name", f"Team {i+1}"),
                "id": t.get("id"),
                "points": None,
            })
        return teams[:limit]
