"""
CS2 Match Prediction Model v4.0 — "Числа от модели, текст от AI"

Принцип: Groq НЕ участвует в расчёте процентов. Только наша математика.
Groq используется исключительно для текстового анализа (verdict, факторы, составы).

Веса (после ревизии по итогам Cologne Major апсетов):
  Взвешенная форма (7 матчей)     50%  — последний матч весит вдвое больше старого
  Взвешенная разница раундов      25%  — те же веса, качество побед
  HLTV/Valve Points (якорь)       15%  — не даёт топ-команде стать 50/50 против нонейма
  H2H встречи                     10%  — только если ≥3 встреч

Диапазон вывода: [34%, 72%] — CS2 слишком волатилен для экстремальных чисел.
"""
import math
import logging
from hltv_parser import HLTVParser

logger = logging.getLogger(__name__)

# Valve/HLTV ranking points (июнь 2026)
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
        """
        Считает финальный процент ИСКЛЮЧИТЕЛЬНО из объективных данных.
        Groq сюда не вмешивается — только эта математика.
        """
        t1n, t2n = t1["name"], t2["name"]
        logit = 0.0
        factors = []
        data_count = 0

        # ── 1. Взвешенная форма за 7 матчей (50%) — главный сигнал ──
        ww1 = t1.get("weighted_winrate")
        ww2 = t2.get("weighted_winrate")
        if ww1 is not None and ww2 is not None:
            diff = (ww1 - ww2) / 22.0   # 22pp разницы = 1 логит-юнит
            logit += diff * 5.0          # вес 50%
            data_count += 1
            if abs(ww1 - ww2) >= 15:
                b = t1n if ww1 > ww2 else t2n
                factors.append(f"📈 {b} в лучшей форме последних матчей: {max(ww1,ww2):.0f}% vs {min(ww1,ww2):.0f}%")
        else:
            # fallback на обычный winrate если взвешенного нет
            w1, w2 = t1.get("winrate"), t2.get("winrate")
            if w1 is not None and w2 is not None:
                diff = (w1 - w2) / 25.0
                logit += diff * 3.0
                data_count += 1

        # ── 2. Взвешенная разница раундов (25%) ──────────────────────
        rd1 = t1.get("avg_round_diff")
        rd2 = t2.get("avg_round_diff")
        if rd1 is not None and rd2 is not None:
            diff = (rd1 - rd2) / 5.0
            logit += diff * 2.0           # вес 25%
            data_count += 1
            if abs(rd1 - rd2) >= 3:
                b = t1n if rd1 > rd2 else t2n
                s1 = "+" if rd1 > 0 else ""
                s2 = "+" if rd2 > 0 else ""
                factors.append(f"🎯 {b} доминирует на раундах ({s1}{rd1:.1f} vs {s2}{rd2:.1f} в среднем)")

        # ── 3. HLTV Points — якорь, не главный фактор (15%) ──────────
        pts1 = _get_pts(t1n)
        pts2 = _get_pts(t2n)
        if pts1 and pts2:
            log_ratio = math.log(pts1 / pts2)
            logit += log_ratio * 0.55     # вес 15%
            data_count += 1
            gap = abs(pts1 - pts2)
            if gap > 400:
                b = t1n if pts1 > pts2 else t2n
                factors.append(f"📊 {b} выше в рейтинге ({max(pts1,pts2)} vs {min(pts1,pts2)} pts)")

        # ── 4. H2H — только при достаточной выборке (10%) ────────────
        h_total = (h2h or {}).get("total", 0)
        if h2h and h_total >= 3:
            hw1 = h2h.get("team1_wins", 0)
            hw2 = h2h.get("team2_wins", 0)
            th = hw1 + hw2 or 1
            if hw1 != hw2:
                ratio = hw1 / th
                logit += (ratio - 0.5) * 2.0   # вес 10%
                data_count += 1
                b = t1n if hw1 > hw2 else t2n
                factors.append(f"🤝 H2H: {b} ведёт {max(hw1,hw2)}-{min(hw1,hw2)}")

        # ── Итог ────────────────────────────────────────────────────
        if data_count == 0:
            p1 = 50.0
            factors.append("⚠️ Недостаточно данных — равный прогноз")
        else:
            raw = _sigmoid(logit) * 100
            p1 = round(min(max(raw, 34.0), 72.0), 1)

        p2 = round(100 - p1, 1)

        return {
            "team1_win_chance": p1,
            "team2_win_chance": p2,
            "key_factors": factors[:4],
            "data_points_used": data_count,  # для отладки/прозрачности
        }
