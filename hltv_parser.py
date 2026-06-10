import asyncio
import re
import logging
from datetime import datetime, timezone
from typing import Optional
import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.hltv.org/",
}

BASE_URL = "https://www.hltv.org"


class HLTVParser:
    def __init__(self, timeout: int = 15):
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    async def _fetch(self, url: str, session: aiohttp.ClientSession) -> Optional[BeautifulSoup]:
        try:
            async with session.get(url, headers=HEADERS, timeout=self.timeout) as resp:
                if resp.status != 200:
                    logger.warning(f"HTTP {resp.status} for {url}")
                    return None
                html = await resp.text()
                return BeautifulSoup(html, "html.parser")
        except Exception as e:
            logger.error(f"Fetch error for {url}: {e}")
            return None

    async def get_today_matches(self) -> list[dict]:
        async with aiohttp.ClientSession() as session:
            soup = await self._fetch(f"{BASE_URL}/matches", session)
            if not soup:
                return self._get_fallback_matches()

            matches = []
            today_sections = soup.select(".upcomingMatchesSection")

            for section in today_sections:
                date_el = section.select_one(".matchDayHeadline")
                if not date_el:
                    continue

                match_rows = section.select(".upcomingMatch")
                for row in match_rows:
                    match = self._parse_match_row(row)
                    if match:
                        matches.append(match)

            if not matches:
                # fallback: try live matches too
                live_rows = soup.select(".liveMatch")
                for row in live_rows:
                    match = self._parse_match_row(row, live=True)
                    if match:
                        matches.append(match)

            return matches if matches else self._get_fallback_matches()

    def _parse_match_row(self, row, live: bool = False) -> Optional[dict]:
        try:
            teams = row.select(".matchTeamName")
            if len(teams) < 2:
                teams = row.select(".team")
            if len(teams) < 2:
                return None

            team1 = teams[0].get_text(strip=True)
            team2 = teams[1].get_text(strip=True)

            if not team1 or not team2 or team1 == "TBD" or team2 == "TBD":
                return None

            event_el = row.select_one(".matchEventName") or row.select_one(".event-name")
            event = event_el.get_text(strip=True) if event_el else "Unknown Event"

            time_el = row.select_one(".matchTime") or row.select_one(".time")
            time_str = "LIVE" if live else (time_el.get_text(strip=True) if time_el else "TBD")

            stars_el = row.select(".matchStar")
            stars = len(stars_el)

            link_el = row.select_one("a[href*='/matches/']")
            match_url = BASE_URL + link_el["href"] if link_el else None

            maps_el = row.select_one(".matchMeta")
            maps_info = maps_el.get_text(strip=True) if maps_el else ""

            return {
                "team1": team1,
                "team2": team2,
                "event": event,
                "time": time_str,
                "stars": min(stars, 5),
                "url": match_url,
                "maps": maps_info,
                "live": live,
            }
        except Exception as e:
            logger.debug(f"Error parsing match row: {e}")
            return None

    async def get_team_stats(self, team_name: str) -> dict:
        async with aiohttp.ClientSession() as session:
            # Search for team
            search_url = f"{BASE_URL}/search?query={team_name.replace(' ', '+')}&type=team"
            soup = await self._fetch(search_url, session)

            team_url = None
            if soup:
                result = soup.select_one(".col-search-result a[href*='/team/']")
                if result:
                    team_url = BASE_URL + result["href"]

            if not team_url:
                # Try direct stats URL guess
                return self._get_estimated_stats(team_name)

            stats_url = team_url.replace("/team/", "/stats/teams/") + "#tab-statsBox"
            stats_soup = await self._fetch(stats_url, session)

            return self._parse_team_stats(stats_soup, team_name) if stats_soup else self._get_estimated_stats(team_name)

    def _parse_team_stats(self, soup: BeautifulSoup, team_name: str) -> dict:
        try:
            stats = {"name": team_name}

            # Winrate
            winrate_el = soup.select_one(".stats-row span:contains('Win rate')")
            if winrate_el:
                parent = winrate_el.find_parent(".stats-row")
                if parent:
                    val = parent.select_one("span:last-child")
                    if val:
                        wr = val.get_text(strip=True).replace("%", "")
                        stats["winrate"] = float(wr) if wr.replace(".", "").isdigit() else 50.0

            # Ranking
            rank_el = soup.select_one(".ranking-position span") or soup.select_one(".teamPosition")
            if rank_el:
                rank_text = re.sub(r"[^\d]", "", rank_el.get_text())
                if rank_text:
                    stats["rank"] = int(rank_text)

            # Average rating
            rating_els = soup.select(".stats-row")
            for row in rating_els:
                if "Rating" in row.get_text():
                    spans = row.select("span")
                    if len(spans) >= 2:
                        try:
                            stats["avg_rating"] = float(spans[-1].get_text(strip=True))
                        except ValueError:
                            pass
                    break

            return self._fill_defaults(stats)
        except Exception as e:
            logger.error(f"Error parsing team stats: {e}")
            return self._get_estimated_stats(team_name)

    async def get_h2h(self, team1: str, team2: str) -> dict:
        """Get head-to-head results between two teams."""
        async with aiohttp.ClientSession() as session:
            # HLTV doesn't have a direct H2H page, so we estimate from general stats
            return {"team1_wins": 0, "team2_wins": 0, "available": False}

    async def get_top_teams(self, limit: int = 10) -> list[dict]:
        async with aiohttp.ClientSession() as session:
            soup = await self._fetch(f"{BASE_URL}/ranking/teams", session)
            if not soup:
                return self._get_fallback_ranking(limit)

            teams = []
            rows = soup.select(".ranked-team")
            for row in rows[:limit]:
                try:
                    name_el = row.select_one(".teamLine .name") or row.select_one(".name")
                    points_el = row.select_one(".points")
                    rank_el = row.select_one(".position")

                    if not name_el:
                        continue

                    name = name_el.get_text(strip=True)
                    points = points_el.get_text(strip=True) if points_el else "?"
                    points = re.sub(r"[^\d]", "", points) or "?"

                    teams.append({"name": name, "points": points})
                except Exception:
                    continue

            return teams if teams else self._get_fallback_ranking(limit)

    def _get_estimated_stats(self, team_name: str) -> dict:
        """Return placeholder stats when scraping fails."""
        return {
            "name": team_name,
            "rank": 50,
            "winrate": 50.0,
            "avg_rating": 1.0,
            "form": "?????",
            "_estimated": True,
        }

    def _fill_defaults(self, stats: dict) -> dict:
        defaults = {
            "rank": 50,
            "winrate": 50.0,
            "avg_rating": 1.0,
            "form": "?????",
        }
        for k, v in defaults.items():
            stats.setdefault(k, v)
        return stats

    def _get_fallback_matches(self) -> list[dict]:
        """Return empty list with a hint that data is unavailable."""
        return []

    def _get_fallback_ranking(self, limit: int) -> list[dict]:
        top = [
            "Natus Vincere", "Virtus.pro", "FaZe", "G2", "Astralis",
            "Team Vitality", "MOUZ", "Spirit", "Heroic", "Cloud9"
        ]
        return [{"name": name, "points": "N/A"} for name in top[:limit]]
