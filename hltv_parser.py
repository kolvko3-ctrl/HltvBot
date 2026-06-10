import logging
import asyncio
from hltv_async_api import Hltv

logger = logging.getLogger(__name__)


class HLTVParser:
    def __init__(self):
        # min_delay/max_delay — пауза между запросами чтобы не получить бан
        self.hltv = Hltv(min_delay=2, max_delay=8, max_retries=5)

    async def get_today_matches(self) -> list[dict]:
        """Матчи на ближайшие 1-2 дня через get_upcoming_matches."""
        try:
            # days=2, min_star_rating=0 — берём все матчи включая малозвёздные
            raw = await self.hltv.get_upcoming_matches(days=2, min_star_rating=0)
            if not raw:
                logger.warning("get_upcoming_matches вернул пустой список")
                return []

            matches = []
            for day_block in raw:
                # Формат: {'date': '10-6', 'matches': [...]}
                day_matches = day_block.get("matches", [])
                for m in day_matches:
                    try:
                        team1 = m.get("team1") or "TBD"
                        team2 = m.get("team2") or "TBD"
                        if team1 == "TBD" or team2 == "TBD":
                            continue

                        matches.append({
                            "team1": team1,
                            "team2": team2,
                            "team1_id": m.get("team1_id"),
                            "team2_id": m.get("team2_id"),
                            "match_id": m.get("id"),
                            "event": m.get("event") or "Unknown Event",
                            "time": m.get("time", "TBD"),
                            "stars": int(m.get("stars", 0) or 0),
                            "maps": m.get("maps", ""),
                            "live": False,
                        })
                    except Exception as e:
                        logger.debug(f"Ошибка разбора матча: {e}")
                        continue

            # Также добавляем live-матчи
            try:
                live_raw = await self.hltv.get_live_matches()
                for m in (live_raw or []):
                    try:
                        team1 = m.get("team1") or "TBD"
                        team2 = m.get("team2") or "TBD"
                        if team1 == "TBD" or team2 == "TBD":
                            continue
                        matches.insert(0, {
                            "team1": team1,
                            "team2": team2,
                            "team1_id": m.get("team1_id"),
                            "team2_id": m.get("team2_id"),
                            "match_id": m.get("id"),
                            "event": m.get("event") or "Live Match",
                            "time": "LIVE",
                            "stars": int(m.get("stars", 0) or 0),
                            "maps": m.get("maps", ""),
                            "live": True,
                        })
                    except Exception:
                        continue
            except Exception as e:
                logger.warning(f"get_live_matches ошибка: {e}")

            return matches

        except Exception as e:
            logger.error(f"Ошибка get_today_matches: {e}", exc_info=True)
            return []

    async def get_team_stats(self, team_id: int | None, team_name: str) -> dict:
        """Статистика команды: рейтинг, winrate, K/D, форма."""
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

        try:
            # get_team_info(team_id, team_name) → dict со stats и matches
            info = await self.hltv.get_team_info(int(team_id), team_name)
            if not info:
                base["_estimated"] = True
                return base

            stats = info.get("stats", {})

            # K/D Ratio
            kd = stats.get("K/D Ratio")
            if kd:
                try:
                    base["avg_rating"] = float(kd)
                except ValueError:
                    pass

            # Wins / draws / losses → winrate
            wdl = stats.get("Wins / draws / losses", "")
            if wdl:
                try:
                    parts = [p.strip() for p in wdl.split("/")]
                    wins = int(parts[0])
                    losses = int(parts[2])
                    total = wins + losses
                    if total > 0:
                        base["winrate"] = round(wins / total * 100, 1)
                except Exception:
                    pass

            # Форма из последних матчей
            recent = info.get("matches", [])
            if recent:
                base["form"] = self._build_form(recent, team_name)

            return base

        except Exception as e:
            logger.error(f"get_team_stats ошибка для {team_name}({team_id}): {e}")
            base["_estimated"] = True
            return base

    async def inject_ranks(self, matches: list[dict]) -> list[dict]:
        """Добавляет HLTV-ранг в каждый матч из топ-30."""
        try:
            top = await self.hltv.get_top_teams(30)
            # Формат: [(rank, id, name, ...)] или [{'rank':..,'name':..}]
            rank_map: dict[str, dict] = {}
            for entry in (top or []):
                if isinstance(entry, (list, tuple)):
                    # старый формат: (rank, id, name)
                    if len(entry) >= 3:
                        rank_map[str(entry[2]).lower()] = {"rank": entry[0], "id": entry[1]}
                elif isinstance(entry, dict):
                    name = (entry.get("team_name") or entry.get("name") or "").lower()
                    rank = entry.get("team_rank") or entry.get("rank")
                    tid = entry.get("team_id") or entry.get("id")
                    if name:
                        rank_map[name] = {"rank": rank, "id": tid}

            for m in matches:
                for key in ("team1", "team2"):
                    nl = m[key].lower()
                    if nl in rank_map:
                        m[f"{key}_rank"] = rank_map[nl]["rank"]
                        if not m.get(f"{key}_id"):
                            m[f"{key}_id"] = rank_map[nl]["id"]
        except Exception as e:
            logger.warning(f"inject_ranks ошибка: {e}")
        return matches

    async def get_top_teams(self, limit: int = 10) -> list[dict]:
        try:
            raw = await self.hltv.get_top_teams(limit)
            teams = []
            for i, entry in enumerate(raw or []):
                if isinstance(entry, (list, tuple)) and len(entry) >= 3:
                    teams.append({"rank": entry[0], "name": entry[2], "points": None, "id": entry[1]})
                elif isinstance(entry, dict):
                    teams.append({
                        "rank": entry.get("team_rank") or entry.get("rank") or (i + 1),
                        "name": entry.get("team_name") or entry.get("name", f"#{i+1}"),
                        "points": entry.get("points"),
                        "id": entry.get("team_id") or entry.get("id"),
                    })
            return teams[:limit]
        except Exception as e:
            logger.error(f"get_top_teams ошибка: {e}")
            return []

    def _build_form(self, recent_matches: list, team_name: str) -> str:
        form = ""
        name_lower = team_name.lower()
        for m in recent_matches[:5]:
            try:
                t1 = str(m.get("team1") or "").lower()
                t2 = str(m.get("team2") or "").lower()
                s1 = int(m.get("score1", 0) or 0)
                s2 = int(m.get("score2", 0) or 0)
                if s1 == 0 and s2 == 0:
                    form += "?"
                    continue
                is_team1 = name_lower in t1
                won = (is_team1 and s1 > s2) or (not is_team1 and s2 > s1)
                form += "W" if won else "L"
            except Exception:
                form += "?"
        return form or "?????"
