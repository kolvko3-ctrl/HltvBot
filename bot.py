"""
CS2 Match Prediction Model v3.0
Уроки из Cologne Major 2026:
  - 9z (#35) обыграли Vitality (#1) → HLTV рейтинг не главное
  - BetBoom победили Falcons И Vitality → форма на турнире > рейтинг
  - Swiss Major = психология давит на фаворитов

Новая модель весов:
  Winrate последних 5 матчей   40%  (самое свежее и важное)
  HLTV очки / рейтинг          30%  (база, но не всё)
  Avg разница раундов           20%  (качество побед)
  H2H встречи                  10%  (история)
  
Диапазон: [34%, 72%] — CS2 слишком непредсказуем для >72%
"""
import math
import logging
from hltv_parser import HLTVParser

logger = logging.getLogger(__name__)

# Valve/HLTV ranking points (июнь 2026, актуально)
HLTV_POINTS: dict[str, int] = {
    "team vitality": 2000, "vitality": 2000,
    "team spirit": 1998,   "spirit": 1998,
    "team falcons": 1200,  "falcons": 1200,
    "natus vincere": 900,  "navi": 900,
    "betboom team": 750,   "betboom": 750,
    "furia": 680,          "furia esports": 680,
    "the mongolz": 620,    "mongolz": 620,
    "9z team": 580,        "9z": 580,
    "aurora gaming": 520,  "aurora": 520,
    "mouz": 500,           "mousesports": 500,
    "g2 esports": 480,     "g2": 480,
    "team liquid": 450,    "liquid": 450,
    "heroic": 420,
    "virtus.pro": 400,     "vp": 400,
    "faze clan": 380,      "faze": 380,
    "astralis": 360,
    "ence": 320,
    "cloud9": 300,
    "mibr": 280,
    "pain gaming": 260,    "pain": 260,
    "nrg": 240,            "nrg esports": 240,
    "big": 220,            "big clan": 220,
    "eternal fire": 200,
    "3dmax": 190,
    "gaimin gladiators": 175, "gaimin": 175,
    "sinners": 160,
    "legacy": 150,
    "b8 esports": 130,     "b8": 130,
    "parivision": 120,
    "fut esports": 110,    "fut": 110,
    "monte": 100,
    "flyquest": 95,
    "m80": 85,
    "sharks": 75,
    "tyloo": 70,
    "lynn vision": 65,
}


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _get_pts(name: str) -> int | None:
    key = name.lower().strip()
    if key in HLTV_POINTS: return HLTV_POINTS[key]
    for k, v in HLTV_POINTS.items():
        if k in key or key in k: return v
    return None


class MatchAnalyzer:
    def __init__(self, parser: HLTVParser):
        self.parser = parser

    def _calc_from_stats(self, t1: dict, t2: dict, h2h: dict) -> dict:
        return self._calculate(t1, t2, h2h)

    def _calculate(self, t1: dict, t2: dict, h2h: dict) -> dict:
        t1n, t2n = t1["name"], t2["name"]
        logit = 0.0
        factors = []
        data_count = 0

        # ── 1. Winrate последних 5 (40%) — самый актуальный сигнал ──
        w1r = t1.get("winrate_last5")
        w2r = t2.get("winrate_last5")
        if w1r is not None and w2r is not None:
            diff = (w1r - w2r) / 25.0  # 25pp разницы = 1 логит-единица
            logit += diff * 3.5  # вес 40%
            data_count += 1
            if abs(w1r - w2r) >= 20:
                b = t1n if w1r > w2r else t2n
                factors.append(f"📈 {b} горячее в последних 5: {max(w1r,w2r):.0f}% vs {min(w1r,w2r):.0f}%")
        else:
            # Нет данных — берём общий winrate с меньшим весом
            w1 = t1.get("winrate")
            w2 = t2.get("winrate")
            if w1 and w2:
                diff = (w1 - w2) / 30.0
                logit += diff * 2.0
                data_count += 1

        # ── 2. HLTV Points (30%) ─────────────────────────────────────
        pts1 = _get_pts(t1n)
        pts2 = _get_pts(t2n)
        if pts1 and pts2:
            # Логарифм разницы — сглаживает экстремальные значения
            log_ratio = math.log(pts1 / pts2)
            logit += log_ratio * 1.5  # вес 30%
            data_count += 1
            gap = abs(pts1 - pts2)
            if gap > 300:
                b = t1n if pts1 > pts2 else t2n
                factors.append(f"📊 {b} значительно выше в рейтинге ({max(pts1,pts2)} vs {min(pts1,pts2)} pts)")

        # ── 3. Avg разница раундов (20%) ─────────────────────────────
        rd1 = t1.get("avg_round_diff")
        rd2 = t2.get("avg_round_diff")
        if rd1 is not None and rd2 is not None:
            diff = (rd1 - rd2) / 5.0  # 5 раундов = 1 единица
            logit += diff * 1.5  # вес 20%
            data_count += 1
            if abs(rd1 - rd2) >= 3:
                b = t1n if rd1 > rd2 else t2n
                s1 = "+" if rd1 > 0 else ""
                s2 = "+" if rd2 > 0 else ""
                factors.append(f"🎯 {b} доминирует на картах ({s1}{rd1:.1f} vs {s2}{rd2:.1f} раундов в среднем)")

        # ── 4. H2H (10%) ─────────────────────────────────────────────
        h_total = (h2h or {}).get("total", 0)
        if h2h and h_total >= 3:
            hw1 = h2h.get("team1_wins", 0)
            hw2 = h2h.get("team2_wins", 0)
            th = hw1 + hw2 or 1
            # Только если есть явное преимущество
            if hw1 != hw2:
                ratio = hw1 / th
                logit += (ratio - 0.5) * 3.0  # вес 10%
                data_count += 1
                b = t1n if hw1 > hw2 else t2n
                factors.append(f"🤝 H2H: {b} ведёт {max(hw1,hw2)}-{min(hw1,hw2)} в личных встречах")

        # ── Итоговая вероятность ──────────────────────────────────────
        if data_count == 0:
            p1 = 50.0
            factors.append("⚠️ Нет данных — равный прогноз по умолчанию")
        else:
            raw = _sigmoid(logit) * 100
            # CS2 Major реальность: даже #1 может проиграть #35
            # Диапазон [34%, 72%] — честнее чем 80%+
            p1 = round(min(max(raw, 34.0), 72.0), 1)

        p2 = round(100 - p1, 1)

        return {
            "team1_win_chance": p1,
            "team2_win_chance": p2,
            "key_factors": factors[:4],
        }
