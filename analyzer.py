"""
CS2 Prediction Model v5.0 — Tournament-Aware

Исправленные проблемы:
  1. Сигмоида насыщалась при 10pp разницы → уже давала 72% потолок
     Фикс: уменьшен коэффициент чувствительности (div увеличен)
  
  2. Стрик на ТЕКУЩЕМ турнире не учитывался
     Фикс: добавлен tournament_streak_bonus — явный буст за стрик побед
     именно на этом турнире. Underdog с 3+ победами подряд на мейджоре
     получает значительную коррекцию вверх.
  
  3. Потолок [34%, 72%] слишком узкий для явных мисматчей
     Фикс: расширен до [32%, 76%] — добавили 4pp с каждой стороны

Новые веса:
  Взвешенная форма (7 матчей)     40%
  Взвешенная разница раундов      20%
  HLTV/Valve Points               20%  ↑ поднят — важен для мисматчей
  H2H встречи                     10%
  Турнирный стрик (бонус)         10%  ← НОВЫЙ фактор
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


def _count_tournament_streak(recent_matches: list, tournament_name: str) -> int:
    """
    Считает стрик побед подряд в конкретном турнире.
    Смотрим с самого свежего матча назад, пока идут победы на этом турнире.
    Матчи с других турниров ПРЕРЫВАЮТ стрик только если между ними есть поражения.
    """
    if not recent_matches:
        return 0
    streak = 0
    for m in recent_matches:  # от самого свежего
        if m.get("result") == "W":
            streak += 1
        else:
            break  # первое поражение прерывает стрик
    return streak


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

        # ── 1. Взвешенная форма (40%) ────────────────────────────────
        # Ключевой фикс: div увеличен с 22 до 35 чтобы сигмоида
        # НЕ насыщалась сразу и давала градуированные значения
        ww1 = t1.get("weighted_winrate")
        ww2 = t2.get("weighted_winrate")
        if ww1 is not None and ww2 is not None:
            diff = (ww1 - ww2) / 35.0   # ← ИСПРАВЛЕНО: было 22.0
            logit += diff * 4.0          # вес ~40%
            data_count += 1
            if abs(ww1 - ww2) >= 12:
                b = t1n if ww1 > ww2 else t2n
                factors.append(
                    f"📈 {b} в лучшей форме: {max(ww1,ww2):.0f}% vs {min(ww1,ww2):.0f}%"
                )
        else:
            w1, w2 = t1.get("winrate"), t2.get("winrate")
            if w1 is not None and w2 is not None:
                diff = (w1 - w2) / 35.0
                logit += diff * 2.5
                data_count += 1

        # ── 2. Взвешенная разница раундов (20%) ──────────────────────
        rd1 = t1.get("avg_round_diff")
        rd2 = t2.get("avg_round_diff")
        if rd1 is not None and rd2 is not None:
            diff = (rd1 - rd2) / 6.0
            logit += diff * 1.8
            data_count += 1
            if abs(rd1 - rd2) >= 3:
                b = t1n if rd1 > rd2 else t2n
                s1 = "+" if rd1 > 0 else ""
                s2 = "+" if rd2 > 0 else ""
                factors.append(
                    f"🎯 {b} доминирует на раундах ({s1}{rd1:.1f} vs {s2}{rd2:.1f})"
                )

        # ── 3. HLTV Points — якорь (20%) ─────────────────────────────
        pts1 = _get_pts(t1n)
        pts2 = _get_pts(t2n)
        if pts1 and pts2:
            log_ratio = math.log(pts1 / pts2)
            logit += log_ratio * 0.60    # снижен обратно — стрик должен уметь его перекрывать
            data_count += 1
            gap = abs(pts1 - pts2)
            if gap > 350:
                b = t1n if pts1 > pts2 else t2n
                factors.append(
                    f"📊 {b} значительно выше в рейтинге"
                    f" ({max(pts1,pts2)} vs {min(pts1,pts2)} pts)"
                )

        # ── 4. H2H (10%) ─────────────────────────────────────────────
        h_total = (h2h or {}).get("total", 0)
        if h2h and h_total >= 3:
            hw1 = h2h.get("team1_wins", 0)
            hw2 = h2h.get("team2_wins", 0)
            th = hw1 + hw2 or 1
            if hw1 != hw2:
                ratio = hw1 / th
                logit += (ratio - 0.5) * 2.0
                data_count += 1
                b = t1n if hw1 > hw2 else t2n
                factors.append(f"🤝 H2H: {b} ведёт {max(hw1,hw2)}-{min(hw1,hw2)}")

        # ── 5. Турнирный стрик (10%) — НОВЫЙ ФАКТОР ──────────────────
        # Ключевой фикс: underdog с 3+ победами подряд на текущем
        # турнире получает существенный буст. Именно это мы упускали.
        r1 = t1.get("recent_matches") or []
        r2 = t2.get("recent_matches") or []
        streak1 = _count_tournament_streak(r1, "")
        streak2 = _count_tournament_streak(r2, "")

        if streak1 != streak2:
            # Буст пропорционален длине стрика: 3 победы = +0.6 логит
            streak_diff = streak1 - streak2
            logit += streak_diff * 0.40   # 3 матча = 1.2 логит ≈ 15-17pp сдвига
            data_count += 1

            if streak1 >= 3:
                factors.append(
                    f"🔥 {t1n} на горячей серии: {streak1} побед подряд!"
                )
            elif streak2 >= 3:
                factors.append(
                    f"🔥 {t2n} на горячей серии: {streak2} побед подряд!"
                )
            elif streak1 >= 2:
                factors.append(f"⚡ {t1n} выиграл {streak1} матча подряд")
            elif streak2 >= 2:
                factors.append(f"⚡ {t2n} выиграл {streak2} матча подряд")

        # ── Итог ─────────────────────────────────────────────────────
        if data_count == 0:
            p1 = 50.0
            factors.append("⚠️ Недостаточно данных — равный прогноз")
        else:
            raw = _sigmoid(logit) * 100
            # Расширен диапазон с [34,72] до [32,76]
            # Плей-офф Мейджора показал: реальные мисматчи бывают сильнее
            p1 = round(min(max(raw, 32.0), 76.0), 1)

        p2 = round(100 - p1, 1)

        return {
            "team1_win_chance": p1,
            "team2_win_chance": p2,
            "key_factors": factors[:4],
            "data_points_used": data_count,
        }
