import logging
import asyncio
from hltv_async_api import Hltv

logger = logging.getLogger(__name__)


class HLTVParser:
    def __init__(self):
        # min_delay/max_delay — задержка между запросами чтобы не получить бан
        self.hltv = Hltv(min_delay=2, max_delay=8, max_retries=5)

    async def get_today_matches(self) -> list[dict]:
        """Получить матчи на сегодня через hltv-async-api."""
        try:
            # get_matches возвращает список ближайших матчей
            raw = await self.hltv.get_matches()
            if not raw:
                logger.warning("hltv.get_matches() вернул пустой список")
                return []

            matches = []
            for m in raw:
                try:
                    team1 = m.get("team1") or "TBD"
                    team2 = m.get("team2") or "TBD"

                    # Пропускаем матчи без команд
                    if team1 == "TBD" or team2 == "TBD":
                        continue

                    matches.append({
                        "team1": team1,
                        "team2": team2,
                        "team1_id": m.get("team1_id"),
                        "team2_id": m.get("team2_id"),
                        "event": m.get("event", "Unknown Event"),
                        "time": m.get("time", "TBD"),
                        "stars": m.get("stars", 0),
                        "match_id": m.get("id"),
                        "live": m.get("live", False),
                    })
                except Exception as e:
                    logger.debug(f"Ошибка разбора матча: {e}")
                    continue

            return matches
        except Exception as e:
            logger.error(f"Ошибка get_today_matches: {e}")
            return []

    async def get_team_stats(self, team_id: int | None, team_name: str) -> dict:
        """Получить статистику команды по ID."""
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
            info = await self.hltv.get_team_info(team_id, team_name)
            if not info:
                base["_estimated"] = True
                return base

            # Парсим статистику из ответа
            stats = info.get("stats", {})

            # K/D → приближение к рейтингу
            kd_str = stats.get("K/D Ratio", "")
            try:
                base["avg_rating"] = float(kd_str)
            except (ValueError, TypeError):
                pass

            # Wins/draws/losses → winrate
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

            # Последние матчи → форма
            recent = info.get("matches", [])
            if recent:
                base["form"] = self._build_form(recent, team_name)

            return base

        except Exception as e:
            logger.error(f"Ошибка get_team_stats для {team_name}: {e}")
            base["_estimated"] = True
            return base

    async def get_top_teams(self, limit: int = 10) -> list[dict]:
        """Топ команд по рейтингу HLTV."""
        try:
            raw = await self.hltv.get_top_teams(limit)
            teams = []
            for i, t in enumerate(raw or []):
                teams.append({
                    "name": t.get("team_name") or t.get("name", f"Team {i+1}"),
                    "rank": t.get("team_rank") or (i + 1),
                    "points": t.get("points", "N/A"),
                    "id": t.get("team_id"),
                })
            return teams
        except Exception as e:
            logger.error(f"Ошибка get_top_teams: {e}")
            return []

    async def inject_ranks(self, matches: list[dict]) -> list[dict]:
        """Добавляет актуальный ранг HLTV к каждой команде в списке матчей."""
        try:
            top = await self.hltv.get_top_teams(30)
            rank_map = {}
            for t in (top or []):
                name = (t.get("team_name") or t.get("name", "")).lower()
                rank = t.get("team_rank") or 0
                tid = t.get("team_id")
                if name:
                    rank_map[name] = {"rank": rank, "id": tid}

            for m in matches:
                for key in ("team1", "team2"):
                    name_lower = m[key].lower()
                    if name_lower in rank_map:
                        m[f"{key}_rank"] = rank_map[name_lower]["rank"]
                        if not m.get(f"{key}_id"):
                            m[f"{key}_id"] = rank_map[name_lower]["id"]
        except Exception as e:
            logger.warning(f"inject_ranks ошибка: {e}")
        return matches

    def _build_form(self, recent_matches: list, team_name: str) -> str:
        """Строит строку формы из последних матчей."""
        form = ""
        name_lower = team_name.lower()
        for m in recent_matches[:5]:
            try:
                teams = m.get("teams", {})
                t1 = (teams.get("team_1") or "").lower()
                t2 = (teams.get("team_2") or "").lower()
                winner = (m.get("winner") or "").lower()
                if not winner:
                    form += "?"
                    continue
                is_t1 = name_lower in t1
                won = name_lower in winner
                form += "W" if won else "L"
            except Exception:
                form += "?"
        return form or "?????"
