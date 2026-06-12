import asyncio
import math
import logging
from hltv_parser import HLTVParser

logger = logging.getLogger(__name__)


def _sigmoid_spread(diff: float, scale: float = 1.0) -> float:
    """
    Превращает разницу показателей в вероятность через сигмоиду.
    scale управляет крутизной — чем выше, тем резче отличие.
    Возвращает значение от 0 до 1.
    """
    return 1.0 / (1.0 + math.exp(-diff * scale))


class MatchAnalyzer:
    def __init__(self, parser: HLTVParser):
        self.parser = parser

    async def analyze(self, match: dict) -> dict:
        t1_id, t2_id = match.get("team1_id"), match.get("team2_id")
        t1_name, t2_name = match["team1"], match["team2"]

        t1_stats, t2_stats, h2h, players = await asyncio.gather(
            self.parser.get_team_stats(t1_id, t1_name),
            self.parser.get_team_stats(t2_id, t2_name),
            self.parser.get_h2h(t1_id, t2_id, t1_name, t2_name),
            self.parser.get_both_teams_players(t1_id, t2_id),
        )

        t1_players, t2_players = players
        t1_stats["players"] = t1_players
        t2_stats["players"] = t2_players
        t1_stats["avg_kd"] = self._avg_kd(t1_players)
        t2_stats["avg_kd"] = self._avg_kd(t2_players)
        t1_stats["avg_hs"] = self._avg_hs(t1_players)
        t2_stats["avg_hs"] = self._avg_hs(t2_players)
        t1_stats["avg_kpr"] = self._avg_kpr(t1_players)
        t2_stats["avg_kpr"] = self._avg_kpr(t2_players)
        t1_stats["star_player"] = self._star_player(t1_players)
        t2_stats["star_player"] = self._star_player(t2_players)

        prediction = self._calculate(t1_stats, t2_stats, h2h)

        return {
            "team1": t1_stats, "team2": t2_stats,
            "event": match.get("event", "CS2"),
            "maps": match.get("maps", ""),
            "h2h": h2h, "prediction": prediction,
        }


    def _calc_from_stats(self, t1: dict, t2: dict, h2h: dict) -> dict:
        """Публичная обёртка для вызова из bot.py."""
        return self._calculate(t1, t2, h2h)

    def _calculate(self, t1: dict, t2: dict, h2h: dict) -> dict:
        """
        Каждый показатель конвертируется через сигмоиду в диапазон [0..1],
        затем взвешивается. Итог → логит-агрегация → финальный % без 50/50 коллапса.
        """
        logit_sum = 0.0   # сумма логитов (log-odds) в пользу t1
        factors = []
        data_count = 0    # сколько реальных показателей удалось получить

        # ── 1. Winrate overall (вес 1.8) ─────────────────────────────
        w1, w2 = t1.get("winrate"), t2.get("winrate")
        if w1 is not None and w2 is not None:
            diff = (w1 - w2) / 20.0   # нормализуем: 20pp разницы = +1.0
            p = _sigmoid_spread(diff, scale=1.6)
            logit_sum += math.log(p / (1 - p + 1e-9)) * 1.8
            data_count += 1
            if abs(w1 - w2) >= 8:
                b = t1["name"] if w1 > w2 else t2["name"]
                factors.append(f"📊 {b} лучший winrate: {max(w1,w2):.0f}% vs {min(w1,w2):.0f}%")

        # ── 2. Winrate последних 5 (вес 2.2 — свежее = важнее) ───────
        w1r, w2r = t1.get("winrate_last5"), t2.get("winrate_last5")
        if w1r is not None and w2r is not None:
            diff = (w1r - w2r) / 20.0
            p = _sigmoid_spread(diff, scale=1.8)
            logit_sum += math.log(p / (1 - p + 1e-9)) * 2.2
            data_count += 1
            if abs(w1r - w2r) >= 20:
                b = t1["name"] if w1r > w2r else t2["name"]
                factors.append(f"📈 {b} горячая форма последних 5: {max(w1r,w2r):.0f}% vs {min(w1r,w2r):.0f}%")

        # ── 3. Форма взвешенная (вес 1.5) ────────────────────────────
        f1 = self._form_score(t1.get("form", "?????"))
        f2 = self._form_score(t2.get("form", "?????"))
        if f1 + f2 > 0:
            diff = (f1 - f2) / 2.0
            p = _sigmoid_spread(diff, scale=1.4)
            logit_sum += math.log(p / (1 - p + 1e-9)) * 1.5
            data_count += 1
            if abs(f1 - f2) >= 0.9:
                b = t1["name"] if f1 > f2 else t2["name"]
                factors.append(f"🔥 {b} в лучшей форме: {t1.get('form','?')} vs {t2.get('form','?')}")

        # ── 4. Avg разница раундов (вес 2.0) ─────────────────────────
        rd1, rd2 = t1.get("avg_round_diff"), t2.get("avg_round_diff")
        if rd1 is not None and rd2 is not None:
            diff = (rd1 - rd2) / 4.0   # 4 раунда разницы = +1.0
            p = _sigmoid_spread(diff, scale=1.5)
            logit_sum += math.log(p / (1 - p + 1e-9)) * 2.0
            data_count += 1
            if abs(rd1 - rd2) >= 2:
                b = t1["name"] if rd1 > rd2 else t2["name"]
                s1 = "+" if rd1 > 0 else ""
                s2 = "+" if rd2 > 0 else ""
                factors.append(f"🎯 {b} побеждает с большим счётом ({s1}{rd1:.1f} vs {s2}{rd2:.1f} раундов)")

        # ── 5. Avg K/D состава (вес 1.8) ──────────────────────────────
        kd1, kd2 = t1.get("avg_kd"), t2.get("avg_kd")
        if kd1 and kd2:
            diff = (kd1 - kd2) / 0.15   # 0.15 разница K/D = +1.0
            p = _sigmoid_spread(diff, scale=1.3)
            logit_sum += math.log(p / (1 - p + 1e-9)) * 1.8
            data_count += 1
            if abs(kd1 - kd2) >= 0.05:
                b = t1["name"] if kd1 > kd2 else t2["name"]
                factors.append(f"⚡ {b} лучший avg K/D: {max(kd1,kd2):.2f} vs {min(kd1,kd2):.2f}")

        # ── 6. Avg KPR (вес 1.2) ──────────────────────────────────────
        kpr1, kpr2 = t1.get("avg_kpr"), t2.get("avg_kpr")
        if kpr1 and kpr2:
            diff = (kpr1 - kpr2) / 0.08
            p = _sigmoid_spread(diff, scale=1.2)
            logit_sum += math.log(p / (1 - p + 1e-9)) * 1.2
            data_count += 1

        # ── 7. H2H (вес зависит от количества матчей) ─────────────────
        h_total = (h2h or {}).get("total", 0)
        if h2h and h_total >= 2:
            hw1 = h2h.get("team1_wins", 0)
            hw2 = h2h.get("team2_wins", 0)
            th = hw1 + hw2 or 1
            h2h_wr = hw1 / th
            diff = h2h_wr - 0.5           # отклонение от 50%
            weight = min(1.8, 0.6 * h_total)   # чем больше встреч тем выше вес, макс 1.8
            p = _sigmoid_spread(diff * 6, scale=1.0)
            logit_sum += math.log(p / (1 - p + 1e-9)) * weight
            data_count += 1
            if hw1 != hw2:
                b = t1["name"] if hw1 > hw2 else t2["name"]
                factors.append(f"🤝 {b} лидирует в H2H: {max(hw1,hw2)}-{min(hw1,hw2)}")

        # ── Финальный расчёт ──────────────────────────────────────────
        if data_count == 0:
            p1 = 50.0
        else:
            # Конвертируем суммарный логит в вероятность
            raw_p1 = _sigmoid_spread(logit_sum, scale=1.0)
            # Применяем min-max чтобы не было 50/50 при реальных данных
            # Минимальный разброс от центра зависит от количества данных
            min_spread = 0.03 * data_count   # больше данных → больший возможный разброс
            if raw_p1 > 0.5:
                p1 = max(raw_p1, 0.5 + min_spread) * 100
            elif raw_p1 < 0.5:
                p1 = min(raw_p1, 0.5 - min_spread) * 100
            else:
                p1 = 50.0
            p1 = round(min(max(p1, 30.0), 85.0), 1)  # зажимаем в [30%, 85%]

        p2 = round(100 - p1, 1)

        # Стрики
        for team in [t1, t2]:
            st = team.get("streak", "")
            if not st: continue
            try:
                count = int(st[1:])
                if st[0] == "W" and count >= 3:
                    factors.append(f"🔴 {team['name']} — {count} побед подряд!")
                elif st[0] == "L" and count >= 3:
                    factors.append(f"❄️ {team['name']} — {count} поражений подряд")
            except: pass

        if t1.get("_estimated") or t2.get("_estimated"):
            factors.append("⚠️ Нет истории матчей — прогноз неточный")

        return {
            "team1_win_chance": p1,
            "team2_win_chance": p2,
            "key_factors": factors[:5],
            "data_richness": data_count,  # для отладки
        }

    # ── ВСПОМОГАТЕЛЬНЫЕ ──────────────────────────────────────────────
    def _form_score(self, form: str) -> float:
        weights = [1.0, 0.85, 0.7, 0.55, 0.4]
        score = 0.0
        for i, ch in enumerate(form[:5]):
            w = weights[i] if i < len(weights) else 0.3
            if ch.upper() == "W": score += w
            elif ch == "?": score += w * 0.45
        return score

    def _avg_kd(self, players) -> float | None:
        vals = [p["kd_ratio"] for p in players if p.get("kd_ratio")]
        return round(sum(vals) / len(vals), 2) if vals else None

    def _avg_hs(self, players) -> float | None:
        vals = [p["headshot_pct"] for p in players if p.get("headshot_pct")]
        return round(sum(vals) / len(vals), 1) if vals else None

    def _avg_kpr(self, players) -> float | None:
        vals = [p["kills_per_round"] for p in players if p.get("kills_per_round")]
        return round(sum(vals) / len(vals), 3) if vals else None

    def _star_player(self, players) -> dict | None:
        valid = [p for p in players if p.get("kd_ratio")]
        return max(valid, key=lambda p: p["kd_ratio"]) if valid else None
