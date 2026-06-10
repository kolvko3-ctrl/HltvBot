import logging
import asyncio
import re
import json
from datetime import datetime
import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Ротация User-Agent чтобы не получить бан
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]

BASE = "https://www.hltv.org"


class HLTVParser:
    """Парсер HLTV без сторонних библиотек — только aiohttp + BeautifulSoup."""

    def __init__(self):
        self._ua_index = 0

    def _headers(self) -> dict:
        ua = USER_AGENTS[self._ua_index % len(USER_AGENTS)]
        self._ua_index += 1
        return {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "no-cache",
            "Referer": "https://www.google.com/",
        }

    async def _get(self, url: str, retries: int = 3) -> BeautifulSoup | None:
        timeout = aiohttp.ClientTimeout(total=20)
        for attempt in range(retries):
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url, headers=self._headers(), allow_redirects=True) as resp:
                        if resp.status == 200:
                            html = await resp.text()
                            return BeautifulSoup(html, "lxml")
                        elif resp.status == 403:
                            logger.warning(f"HLTV вернул 403 для {url}, попытка {attempt+1}")
                            await asyncio.sleep(3 * (attempt + 1))
                        else:
                            logger.warning(f"HTTP {resp.status} для {url}")
                            return None
            except Exception as e:
                logger.error(f"Ошибка запроса {url}: {e}, попытка {attempt+1}")
                await asyncio.sleep(2)
        return None

    # ────────────────────────────────────────────────────────────────
    # МАТЧИ
    # ────────────────────────────────────────────────────────────────
    async def get_today_matches(self) -> list[dict]:
        soup = await self._get(f"{BASE}/matches")
        if not soup:
            return []

        matches = []

        # Upcoming матчи
        for section in soup.select(".upcomingMatchesSection"):
            for row in section.select(".upcomingMatch"):
                m = self._parse_row(row, live=False)
                if m:
                    matches.append(m)

        # Live матчи
        for row in soup.select(".liveMatch, .live-match"):
            m = self._parse_row(row, live=True)
            if m:
                matches.insert(0, m)

        return matches

    def _parse_row(self, row, live: bool) -> dict | None:
        try:
            # Команды
            teams = row.select(".matchTeamName")
            if len(teams) < 2:
                teams = row.select(".team-name, .teamName")
            if len(teams) < 2:
                return None

            t1 = teams[0].get_text(strip=True)
            t2 = teams[1].get_text(strip=True)
            if not t1 or not t2 or "TBD" in (t1, t2):
                return None

            # Ивент
            ev_el = row.select_one(".matchEventName, .event-name, .matchEvent span")
            event = ev_el.get_text(strip=True) if ev_el else "Unknown"

            # Время
            time_el = row.select_one(".matchTime, .time")
            time_str = "LIVE" if live else (time_el.get_text(strip=True) if time_el else "TBD")

            # Звёзды
            stars = len(row.select(".matchStar, .star"))

            # ID команд и матча из ссылки
            link = row.select_one("a[href*='/matches/']")
            match_url = BASE + link["href"] if link else None

            # ID команд из атрибутов
            t1_id = row.get("team1id") or row.get("data-team1id")
            t2_id = row.get("team2id") or row.get("data-team2id")

            return {
                "team1": t1,
                "team2": t2,
                "team1_id": int(t1_id) if t1_id else None,
                "team2_id": int(t2_id) if t2_id else None,
                "event": event,
                "time": time_str,
                "stars": min(int(stars), 5),
                "url": match_url,
                "live": live,
            }
        except Exception as e:
            logger.debug(f"Ошибка _parse_row: {e}")
            return None

    # ────────────────────────────────────────────────────────────────
    # РАНГИ → добавляем к матчам
    # ────────────────────────────────────────────────────────────────
    async def inject_ranks(self, matches: list[dict]) -> list[dict]:
        rank_map = await self._get_rank_map(30)
        for m in matches:
            for key in ("team1", "team2"):
                nl = m[key].lower()
                if nl in rank_map:
                    m[f"{key}_rank"] = rank_map[nl]["rank"]
                    if not m.get(f"{key}_id"):
                        m[f"{key}_id"] = rank_map[nl]["id"]
        return matches

    async def _get_rank_map(self, limit: int = 30) -> dict:
        soup = await self._get(f"{BASE}/ranking/teams")
        if not soup:
            return {}
        rank_map = {}
        for row in soup.select(".ranked-team")[:limit]:
            try:
                name_el = row.select_one(".teamLine .name, .name")
                pos_el = row.select_one(".position")
                link_el = row.select_one("a[href*='/team/']")
                if not name_el:
                    continue
                name = name_el.get_text(strip=True).lower()
                rank = int(re.sub(r"[^\d]", "", pos_el.get_text())) if pos_el else 99
                tid = None
                if link_el:
                    m = re.search(r"/team/(\d+)/", link_el["href"])
                    tid = int(m.group(1)) if m else None
                rank_map[name] = {"rank": rank, "id": tid}
            except Exception:
                continue
        return rank_map

    # ────────────────────────────────────────────────────────────────
    # СТАТИСТИКА КОМАНДЫ
    # ────────────────────────────────────────────────────────────────
    async def get_team_stats(self, team_id: int | None, team_name: str) -> dict:
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

        # Страница статистики команды
        url = f"{BASE}/stats/teams/{team_id}/{team_name.lower().replace(' ', '-')}"
        soup = await self._get(url)
        if not soup:
            base["_estimated"] = True
            return base

        try:
            # Winrate
            for row in soup.select(".stats-row"):
                label = row.select_one("span:first-child")
                value = row.select_one("span:last-child")
                if not label or not value:
                    continue
                lbl = label.get_text(strip=True).lower()
                val = value.get_text(strip=True)

                if "win" in lbl and "%" in val:
                    try:
                        base["winrate"] = float(val.replace("%", "").strip())
                    except ValueError:
                        pass
                elif "k/d" in lbl or "kd" in lbl:
                    try:
                        base["avg_rating"] = float(val)
                    except ValueError:
                        pass
                elif "rating" in lbl:
                    try:
                        base["avg_rating"] = float(val)
                    except ValueError:
                        pass

            # Форма из последних матчей на странице команды
            team_url = f"{BASE}/team/{team_id}/{team_name.lower().replace(' ', '-')}#tab-matchesBox"
            team_soup = await self._get(team_url)
            if team_soup:
                base["form"] = self._parse_form(team_soup, team_name)

        except Exception as e:
            logger.error(f"Ошибка парсинга статистики {team_name}: {e}")
            base["_estimated"] = True

        return base

    def _parse_form(self, soup: BeautifulSoup, team_name: str) -> str:
        form = ""
        name_lower = team_name.lower()
        for row in soup.select(".recentMatches tr, .match-table tr")[:5]:
            try:
                result_el = row.select_one(".teamResult, .result-won, .result-lost, .won, .lost")
                if not result_el:
                    continue
                cls = " ".join(result_el.get("class", []))
                if "won" in cls or "win" in cls:
                    form += "W"
                elif "lost" in cls or "loss" in cls:
                    form += "L"
                else:
                    form += "?"
            except Exception:
                form += "?"
        return form if form else "?????"

    # ────────────────────────────────────────────────────────────────
    # ТОП КОМАНДЫ
    # ────────────────────────────────────────────────────────────────
    async def get_top_teams(self, limit: int = 10) -> list[dict]:
        rank_map = await self._get_rank_map(limit)
        teams = []
        for name, data in rank_map.items():
            teams.append({
                "rank": data["rank"],
                "name": name.title(),
                "id": data["id"],
                "points": None,
            })
        teams.sort(key=lambda x: x["rank"])
        return teams[:limit]
