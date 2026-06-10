import asyncio
import logging
from hltv_parser import HLTVParser

logger = logging.getLogger(__name__)


class MatchAnalyzer:
    def __init__(self):
        self.parser = HLTVParser()

        # Approximate ranks for well-known teams (fallback when scraping fails)
        self.known_ranks = {
            "natus vincere": 3, "navi": 3,
            "virtus.pro": 8, "vp": 8,
            "faze": 2, "faze clan": 2,
            "g2": 4, "g2 esports": 4,
            "astralis": 9,
            "team vitality": 5, "vitality": 5,
            "mouz": 6, "mousesports": 6,
            "team spirit": 1, "spirit": 1,
            "heroic": 10,
            "cloud9": 12, "c9": 12,
            "ence": 15,
            "fnatic": 20,
            "nip": 14, "ninjas in pyjamas": 14,
            "liquid": 7, "team liquid": 7,
            "eternal fire": 11,
            "big": 18,
            "complexity": 22, "col": 22,
            "3dmax": 16,
        }

    async def analyze(self, match: dict) -> dict:
        team1_name = match["team1"]
        team2_name = match["team2"]

        # Fetch stats concurrently
        t1_stats, t2_stats = await asyncio.gather(
            self.parser.get_team_stats(team1_name),
            self.parser.get_team_stats(team2_name),
        )

        # Apply known ranks as fallback
        self._apply_known_rank(t1_stats, team1_name)
        self._apply_known_rank(t2_stats, team2_name)

        # Generate form if missing
        t1_stats["form"] = self._estimate_form(t1_stats)
        t2_stats["form"] = self._estimate_form(t2_stats)

        # Calculate win probabilities
        prediction = self._calculate_prediction(t1_stats, t2_stats, match)

        return {
            "team1": t1_stats,
            "team2": t2_stats,
            "event": match.get("event", "Unknown Event"),
            "prediction": prediction,
            "h2h": None,
        }

    def _apply_known_rank(self, stats: dict, team_name: str):
        """Apply known rank if scraper returned default value."""
        key = team_name.lower().strip()
        if key in self.known_ranks and stats.get("rank") == 50:
            stats["rank"] = self.known_ranks[key]

    def _estimate_form(self, stats: dict) -> str:
        """Estimate recent form from winrate."""
        wr = stats.get("winrate", 50)
        if wr >= 70:
            return "WWWWW"
        elif wr >= 60:
            return "WWWLW"
        elif wr >= 50:
            return "WWLWL"
        elif wr >= 40:
            return "WLLWL"
        else:
            return "LLLWL"

    def _calculate_prediction(self, t1: dict, t2: dict, match: dict) -> dict:
        """
        Weighted scoring model:
          - HLTV Rank difference  : 35%
          - Winrate               : 30%
          - Avg Rating 2.0        : 25%
          - Form (last 5)         : 10%
        """
        score1 = 0.0
        score2 = 0.0
        key_factors = []

        # --- Rank score (lower rank # = better) ---
        r1 = t1.get("rank", 50)
        r2 = t2.get("rank", 50)
        rank_diff = r2 - r1  # positive means t1 is higher ranked

        if rank_diff > 0:
            rank_pts = min(35, abs(rank_diff) * 0.7)
            score1 += rank_pts
            score2 += 35 - rank_pts
            if abs(rank_diff) >= 10:
                key_factors.append(f"{t1['name']} значительно выше в рейтинге (#{r1} vs #{r2})")
        elif rank_diff < 0:
            rank_pts = min(35, abs(rank_diff) * 0.7)
            score2 += rank_pts
            score1 += 35 - rank_pts
            if abs(rank_diff) >= 10:
                key_factors.append(f"{t2['name']} значительно выше в рейтинге (#{r2} vs #{r1})")
        else:
            score1 += 17.5
            score2 += 17.5

        # --- Winrate score ---
        wr1 = t1.get("winrate", 50)
        wr2 = t2.get("winrate", 50)
        total_wr = wr1 + wr2 or 100
        score1 += (wr1 / total_wr) * 30
        score2 += (wr2 / total_wr) * 30
        if abs(wr1 - wr2) >= 15:
            better = t1["name"] if wr1 > wr2 else t2["name"]
            key_factors.append(f"{better} лучше по winrate ({max(wr1, wr2):.0f}% vs {min(wr1, wr2):.0f}%)")

        # --- Rating 2.0 ---
        rat1 = t1.get("avg_rating", 1.0)
        rat2 = t2.get("avg_rating", 1.0)
        total_rat = rat1 + rat2 or 2.0
        score1 += (rat1 / total_rat) * 25
        score2 += (rat2 / total_rat) * 25
        if abs(rat1 - rat2) >= 0.1:
            better = t1["name"] if rat1 > rat2 else t2["name"]
            key_factors.append(f"{better} имеет лучший средний рейтинг игроков ({max(rat1, rat2):.2f})")

        # --- Form score ---
        form1 = self._form_score(t1.get("form", "WWLWL"))
        form2 = self._form_score(t2.get("form", "WWLWL"))
        total_form = form1 + form2 or 10
        score1 += (form1 / total_form) * 10
        score2 += (form2 / total_form) * 10
        if abs(form1 - form2) >= 2:
            better = t1["name"] if form1 > form2 else t2["name"]
            key_factors.append(f"{better} в лучшей форме ({t1['form']} vs {t2['form']})")

        # Normalize to percentages
        total = score1 + score2
        if total == 0:
            p1, p2 = 50.0, 50.0
        else:
            p1 = round((score1 / total) * 100, 1)
            p2 = round(100 - p1, 1)

        # Add note if data was estimated
        if t1.get("_estimated") or t2.get("_estimated"):
            key_factors.append("⚠️ Часть данных приблизительная (HLTV недоступен)")

        return {
            "team1_win_chance": p1,
            "team2_win_chance": p2,
            "key_factors": key_factors[:4],  # top 4 factors
        }

    def _form_score(self, form: str) -> float:
        """Convert form string like 'WWLWL' to numeric score."""
        score = 0.0
        weights = [1.0, 0.9, 0.8, 0.7, 0.6]  # more recent = higher weight
        for i, char in enumerate(form[:5]):
            w = weights[i] if i < len(weights) else 0.5
            if char.upper() == "W":
                score += w
            elif char.upper() == "L":
                score += 0
            else:
                score += 0.5  # unknown
        return score
