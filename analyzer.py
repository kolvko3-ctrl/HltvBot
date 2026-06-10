import asyncio
import logging
from hltv_parser import HLTVParser

logger = logging.getLogger(__name__)


class MatchAnalyzer:
    def __init__(self, parser: HLTVParser):
        self.parser = parser

    async def analyze(self, match: dict) -> dict:
        t1_name = match["team1"]
        t2_name = match["team2"]
        t1_id = match.get("team1_id")
        t2_id = match.get("team2_id")

        t1_stats, t2_stats = await asyncio.gather(
            self.parser.get_team_stats(t1_id, t1_name),
            self.parser.get_team_stats(t2_id, t2_name),
        )

        if not t1_stats.get("form"):
            t1_stats["form"] = self._wr_to_form(t1_stats.get("winrate"))
        if not t2_stats.get("form"):
            t2_stats["form"] = self._wr_to_form(t2_stats.get("winrate"))

        prediction = self._calculate(t1_stats, t2_stats)

        return {
            "team1": t1_stats,
            "team2": t2_stats,
            "event": match.get("event", "CS2 Match"),
            "maps": match.get("maps", ""),
            "prediction": prediction,
        }

    def _calculate(self, t1: dict, t2: dict) -> dict:
        s1, s2 = 0.0, 0.0
        factors = []

        # Winrate (60%) — основной критерий когда нет HLTV-ранга
        w1 = t1.get("winrate")
        w2 = t2.get("winrate")
        if w1 is not None and w2 is not None:
            total = w1 + w2 or 100
            s1 += (w1 / total) * 60
            s2 += (w2 / total) * 60
            if abs(w1 - w2) >= 10:
                better = t1["name"] if w1 > w2 else t2["name"]
                factors.append(f"📈 {better}: лучший winrate ({max(w1,w2):.0f}% vs {min(w1,w2):.0f}%)")
        else:
            s1 += 30
            s2 += 30

        # Форма (40%)
        f1 = self._form_score(t1.get("form", "?????"))
        f2 = self._form_score(t2.get("form", "?????"))
        tf = f1 + f2 or 1
        s1 += (f1 / tf) * 40
        s2 += (f2 / tf) * 40
        if abs(f1 - f2) >= 1.5:
            better = t1["name"] if f1 > f2 else t2["name"]
            factors.append(f"🔥 {better}: лучшая форма ({t1.get('form','?')} vs {t2.get('form','?')})")

        if t1.get("_estimated") or t2.get("_estimated"):
            factors.append("⚠️ Нет истории матчей — прогноз приблизительный")

        total = s1 + s2 or 100
        p1 = round(s1 / total * 100, 1)
        p2 = round(100 - p1, 1)

        return {
            "team1_win_chance": p1,
            "team2_win_chance": p2,
            "key_factors": factors[:3],
        }

    def _form_score(self, form: str) -> float:
        weights = [1.0, 0.85, 0.7, 0.55, 0.4]
        score = 0.0
        for i, ch in enumerate(form[:5]):
            w = weights[i] if i < len(weights) else 0.3
            if ch.upper() == "W":
                score += w
            elif ch == "?":
                score += w * 0.5
        return score

    def _wr_to_form(self, winrate) -> str:
        if winrate is None:
            return "?????"
        if winrate >= 70: return "WWWWW"
        if winrate >= 60: return "WWWLW"
        if winrate >= 50: return "WWLWL"
        if winrate >= 40: return "WLLWL"
        return "LLLWL"
