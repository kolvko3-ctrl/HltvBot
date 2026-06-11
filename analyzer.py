import asyncio
import logging
from hltv_parser import HLTVParser

logger = logging.getLogger(__name__)


class MatchAnalyzer:
    def __init__(self, parser: HLTVParser):
        self.parser = parser

    async def analyze(self, match: dict) -> dict:
        t1_id, t2_id = match.get("team1_id"), match.get("team2_id")
        t1_name, t2_name = match["team1"], match["team2"]

        # Грузим всё параллельно
        t1_stats, t2_stats, h2h = await asyncio.gather(
            self.parser.get_team_stats(t1_id, t1_name),
            self.parser.get_team_stats(t2_id, t2_name),
            self.parser.get_h2h(t1_id, t2_id, t1_name, t2_name),
        )

        prediction = self._calculate(t1_stats, t2_stats, h2h)

        return {
            "team1": t1_stats, "team2": t2_stats,
            "event": match.get("event", "CS2"),
            "maps": match.get("maps", ""),
            "h2h": h2h,
            "prediction": prediction,
        }

    def _calculate(self, t1: dict, t2: dict, h2h: dict) -> dict:
        """
        Многофакторная модель из реальных данных:
          Winrate overall       25%
          Winrate last 5        20%
          Форма (взвешенная)    20%
          Avg round diff        20%
          H2H                   15%
        """
        s1 = s2 = 0.0
        factors = []

        # ── Winrate overall (25%) ──────────────────────────────────
        w1, w2 = t1.get("winrate"), t2.get("winrate")
        if w1 is not None and w2 is not None:
            t = w1 + w2 or 100
            s1 += w1 / t * 25; s2 += w2 / t * 25
            diff = abs(w1 - w2)
            if diff >= 8:
                b = t1["name"] if w1 > w2 else t2["name"]
                factors.append(f"📊 {b} лучший общий winrate: {max(w1,w2):.0f}% vs {min(w1,w2):.0f}%")
        else:
            s1 += 12.5; s2 += 12.5

        # ── Winrate последних 5 (20%) ──────────────────────────────
        w1r, w2r = t1.get("winrate_last5"), t2.get("winrate_last5")
        if w1r is not None and w2r is not None:
            t = w1r + w2r or 100
            s1 += w1r / t * 20; s2 += w2r / t * 20
            diff = abs(w1r - w2r)
            if diff >= 20:
                b = t1["name"] if w1r > w2r else t2["name"]
                factors.append(f"📈 {b} горячая форма последних 5: {max(w1r,w2r):.0f}% vs {min(w1r,w2r):.0f}%")
        else:
            s1 += 10; s2 += 10

        # ── Форма взвешенная (20%) ─────────────────────────────────
        f1 = self._form_score(t1.get("form", "?????"))
        f2 = self._form_score(t2.get("form", "?????"))
        tf = f1 + f2 or 1
        s1 += f1 / tf * 20; s2 += f2 / tf * 20
        if abs(f1 - f2) >= 1.2:
            b = t1["name"] if f1 > f2 else t2["name"]
            factors.append(f"🔥 {b} в лучшей форме: {t1.get('form','?')} vs {t2.get('form','?')}")

        # ── Avg round diff (20%) ───────────────────────────────────
        rd1, rd2 = t1.get("avg_round_diff"), t2.get("avg_round_diff")
        if rd1 is not None and rd2 is not None:
            # Нормализуем: чем выше diff тем лучше
            # Диапазон примерно от -15 до +15 → сдвигаем в 0..30
            n1 = max(0.0, rd1 + 16)
            n2 = max(0.0, rd2 + 16)
            tn = n1 + n2 or 1
            s1 += n1 / tn * 20; s2 += n2 / tn * 20
            if abs(rd1 - rd2) >= 2:
                b = t1["name"] if rd1 > rd2 else t2["name"]
                factors.append(
                    f"🎯 {b} побеждает с большим счётом "
                    f"(avg {'+' if rd1>0 else ''}{rd1:.1f} vs {'+' if rd2>0 else ''}{rd2:.1f} раундов)")
        else:
            s1 += 10; s2 += 10

        # ── H2H (15%) ─────────────────────────────────────────────
        h2h_t = h2h.get("total", 0) if h2h else 0
        if h2h and h2h_t >= 2:
            hw1 = h2h.get("team1_wins", 0)
            hw2 = h2h.get("team2_wins", 0)
            th = hw1 + hw2 or 1
            s1 += hw1 / th * 15; s2 += hw2 / th * 15
            if hw1 != hw2:
                b = t1["name"] if hw1 > hw2 else t2["name"]
                factors.append(f"🤝 H2H: {b} ведёт {max(hw1,hw2)}-{min(hw1,hw2)} в личных встречах")
        else:
            s1 += 7.5; s2 += 7.5

        # Нормализация
        total = s1 + s2 or 100
        p1 = round(s1 / total * 100, 1)
        p2 = round(100 - p1, 1)

        # Стрик-бонус (информационный, не меняет вес)
        st1, st2 = t1.get("streak"), t2.get("streak")
        if st1 and st1.startswith("W") and int(st1[1:] or 0) >= 3:
            factors.append(f"⚡ {t1['name']} на серии побед: {st1}")
        if st2 and st2.startswith("W") and int(st2[1:] or 0) >= 3:
            factors.append(f"⚡ {t2['name']} на серии побед: {st2}")
        if st1 and st1.startswith("L") and int(st1[1:] or 0) >= 3:
            factors.append(f"❄️ {t1['name']} на серии поражений: {st1}")
        if st2 and st2.startswith("L") and int(st2[1:] or 0) >= 3:
            factors.append(f"❄️ {t2['name']} на серии поражений: {st2}")

        if t1.get("_estimated") or t2.get("_estimated"):
            factors.append("⚠️ Нет истории матчей — прогноз неточный")

        return {"team1_win_chance": p1, "team2_win_chance": p2, "key_factors": factors[:5]}

    def _form_score(self, form: str) -> float:
        weights = [1.0, 0.85, 0.7, 0.55, 0.4]
        score = 0.0
        for i, ch in enumerate(form[:5]):
            w = weights[i] if i < len(weights) else 0.3
            if ch.upper() == "W": score += w
            elif ch == "?": score += w * 0.5
        return score
