import asyncio
import logging
from hltv_parser import HLTVParser

logger = logging.getLogger(__name__)


class MatchAnalyzer:
    def __init__(self):
        self.parser = HLTVParser()

    async def analyze(self, match: dict) -> dict:
        team1_name = match["team1"]
        team2_name = match["team2"]
        team1_id = match.get("team1_id")
        team2_id = match.get("team2_id")

        # Подтягиваем статистику обеих команд параллельно
        t1_stats, t2_stats = await asyncio.gather(
            self.parser.get_team_stats(team1_id, team1_name),
            self.parser.get_team_stats(team2_id, team2_name),
        )

        # Ранги из матча (если inject_ranks был вызван)
        if not t1_stats.get("rank") and match.get("team1_rank"):
            t1_stats["rank"] = match["team1_rank"]
        if not t2_stats.get("rank") and match.get("team2_rank"):
            t2_stats["rank"] = match["team2_rank"]

        # Форма по умолчанию если не получили
        if not t1_stats.get("form"):
            t1_stats["form"] = self._wr_to_form(t1_stats.get("winrate"))
        if not t2_stats.get("form"):
            t2_stats["form"] = self._wr_to_form(t2_stats.get("winrate"))

        prediction = self._calculate(t1_stats, t2_stats)

        return {
            "team1": t1_stats,
            "team2": t2_stats,
            "event": match.get("event", "Unknown Event"),
            "prediction": prediction,
        }

    def _calculate(self, t1: dict, t2: dict) -> dict:
        """
        Взвешенная модель:
          Рейтинг HLTV  → 40%
          Winrate        → 35%
          Avg K/D        → 15%
          Форма          → 10%
        Если данных нет — используем нейтральный вес 50/50 по этому критерию.
        """
        s1, s2 = 0.0, 0.0
        factors = []
        total_weight = 0.0

        # ── Рейтинг (40%) ──────────────────────────────────────────
        r1 = t1.get("rank")
        r2 = t2.get("rank")
        if r1 and r2:
            # Меньший ранг = лучше, нормализуем через 1/rank
            inv1 = 1 / r1
            inv2 = 1 / r2
            p1 = inv1 / (inv1 + inv2)
            s1 += p1 * 40
            s2 += (1 - p1) * 40
            total_weight += 40
            diff = abs(r1 - r2)
            if diff >= 5:
                better = t1["name"] if r1 < r2 else t2["name"]
                factors.append(f"📍 {better} выше в рейтинге HLTV (#{min(r1,r2)} vs #{max(r1,r2)})")
        else:
            s1 += 20
            s2 += 20
            total_weight += 40

        # ── Winrate (35%) ───────────────────────────────────────────
        w1 = t1.get("winrate")
        w2 = t2.get("winrate")
        if w1 is not None and w2 is not None:
            total_wr = w1 + w2 or 100
            s1 += (w1 / total_wr) * 35
            s2 += (w2 / total_wr) * 35
            total_weight += 35
            if abs(w1 - w2) >= 10:
                better = t1["name"] if w1 > w2 else t2["name"]
                factors.append(f"📈 {better} лучший winrate ({max(w1,w2):.0f}% vs {min(w1,w2):.0f}%)")
        else:
            s1 += 17.5
            s2 += 17.5
            total_weight += 35

        # ── K/D (15%) ────────────────────────────────────────────────
        k1 = t1.get("avg_rating")
        k2 = t2.get("avg_rating")
        if k1 and k2:
            total_kd = k1 + k2 or 2
            s1 += (k1 / total_kd) * 15
            s2 += (k2 / total_kd) * 15
            total_weight += 15
            if abs(k1 - k2) >= 0.05:
                better = t1["name"] if k1 > k2 else t2["name"]
                factors.append(f"⚡ {better} лучший K/D ({max(k1,k2):.2f} vs {min(k1,k2):.2f})")
        else:
            s1 += 7.5
            s2 += 7.5
            total_weight += 15

        # ── Форма (10%) ─────────────────────────────────────────────
        f1 = self._form_score(t1.get("form", "?????"))
        f2 = self._form_score(t2.get("form", "?????"))
        tf = f1 + f2
        if tf > 0:
            s1 += (f1 / tf) * 10
            s2 += (f2 / tf) * 10
            if abs(f1 - f2) >= 1.5:
                better = t1["name"] if f1 > f2 else t2["name"]
                factors.append(f"🔥 {better} в лучшей форме ({t1.get('form','?')} vs {t2.get('form','?')})")
        else:
            s1 += 5
            s2 += 5
        total_weight += 10

        # Нормализация
        total = s1 + s2 or 100
        p1 = round(s1 / total * 100, 1)
        p2 = round(100 - p1, 1)

        if t1.get("_estimated") or t2.get("_estimated"):
            factors.append("⚠️ Часть данных недоступна — прогноз приблизительный")

        return {
            "team1_win_chance": p1,
            "team2_win_chance": p2,
            "key_factors": factors[:4],
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
        if winrate >= 70:
            return "WWWWW"
        elif winrate >= 60:
            return "WWWLW"
        elif winrate >= 50:
            return "WWLWL"
        elif winrate >= 40:
            return "WLLWL"
        return "LLLWL"
