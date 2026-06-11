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

        # Грузим всё параллельно одним залпом
        t1_stats, t2_stats, h2h, players = await asyncio.gather(
            self.parser.get_team_stats(t1_id, t1_name),
            self.parser.get_team_stats(t2_id, t2_name),
            self.parser.get_h2h(t1_id, t2_id, t1_name, t2_name),
            self.parser.get_both_teams_players(t1_id, t2_id),
        )

        t1_players, t2_players = players

        # Средний K/D по игрокам → добавляем в стат команды
        t1_stats["players"] = t1_players
        t2_stats["players"] = t2_players
        t1_stats["avg_kd"] = self._avg_kd(t1_players)
        t2_stats["avg_kd"] = self._avg_kd(t2_players)
        t1_stats["avg_hs"] = self._avg_hs(t1_players)
        t2_stats["avg_hs"] = self._avg_hs(t2_players)
        t1_stats["star_player"] = self._star_player(t1_players)
        t2_stats["star_player"] = self._star_player(t2_players)

        prediction = self._calculate(t1_stats, t2_stats, h2h)

        return {
            "team1": t1_stats,
            "team2": t2_stats,
            "event": match.get("event", "CS2"),
            "maps": match.get("maps", ""),
            "h2h": h2h,
            "prediction": prediction,
        }

    # ── РАСЧЁТ ПРОГНОЗА ─────────────────────────────────────────────
    def _calculate(self, t1: dict, t2: dict, h2h: dict) -> dict:
        """
        Многофакторная модель:
          Winrate overall      20%
          Winrate last 5       20%
          Форма взвешенная     15%
          Avg round diff       20%
          Avg K/D игроков      15%
          H2H                  10%
        """
        s1 = s2 = 0.0
        factors = []

        # ── Winrate overall (20%) ──────────────────────────────────
        w1, w2 = t1.get("winrate"), t2.get("winrate")
        if w1 is not None and w2 is not None:
            t = w1 + w2 or 100
            s1 += w1 / t * 20; s2 += w2 / t * 20
            if abs(w1 - w2) >= 8:
                b = t1["name"] if w1 > w2 else t2["name"]
                factors.append(f"📊 {b} — лучший общий винрейт: {max(w1,w2):.0f}% vs {min(w1,w2):.0f}%")
        else:
            s1 += 10; s2 += 10

        # ── Winrate last 5 (20%) ───────────────────────────────────
        w1r, w2r = t1.get("winrate_last5"), t2.get("winrate_last5")
        if w1r is not None and w2r is not None:
            t = w1r + w2r or 100
            s1 += w1r / t * 20; s2 += w2r / t * 20
            if abs(w1r - w2r) >= 20:
                b = t1["name"] if w1r > w2r else t2["name"]
                factors.append(f"📈 {b} — горячая форма посл. 5: {max(w1r,w2r):.0f}% vs {min(w1r,w2r):.0f}%")
        else:
            s1 += 10; s2 += 10

        # ── Форма взвешенная (15%) ─────────────────────────────────
        f1 = self._form_score(t1.get("form", "?????"))
        f2 = self._form_score(t2.get("form", "?????"))
        tf = f1 + f2 or 1
        s1 += f1 / tf * 15; s2 += f2 / tf * 15
        if abs(f1 - f2) >= 1.0:
            b = t1["name"] if f1 > f2 else t2["name"]
            factors.append(f"🔥 {b} — лучшая форма: {t1.get('form','?')} vs {t2.get('form','?')}")

        # ── Avg round diff (20%) ───────────────────────────────────
        rd1, rd2 = t1.get("avg_round_diff"), t2.get("avg_round_diff")
        if rd1 is not None and rd2 is not None:
            n1 = max(0.0, rd1 + 16)
            n2 = max(0.0, rd2 + 16)
            tn = n1 + n2 or 1
            s1 += n1 / tn * 20; s2 += n2 / tn * 20
            if abs(rd1 - rd2) >= 2:
                b = t1["name"] if rd1 > rd2 else t2["name"]
                sign1 = "+" if rd1 > 0 else ""
                sign2 = "+" if rd2 > 0 else ""
                factors.append(f"🎯 {b} — побеждает с большим счётом "
                                f"({sign1}{rd1:.1f} vs {sign2}{rd2:.1f} раундов avg)")
        else:
            s1 += 10; s2 += 10

        # ── Avg K/D игроков (15%) ──────────────────────────────────
        kd1, kd2 = t1.get("avg_kd"), t2.get("avg_kd")
        if kd1 and kd2:
            t = kd1 + kd2 or 1
            s1 += kd1 / t * 15; s2 += kd2 / t * 15
            if abs(kd1 - kd2) >= 0.05:
                b = t1["name"] if kd1 > kd2 else t2["name"]
                factors.append(f"⚡ {b} — лучший avg K/D состава: {max(kd1,kd2):.2f} vs {min(kd1,kd2):.2f}")
        else:
            s1 += 7.5; s2 += 7.5

        # ── H2H (10%) ──────────────────────────────────────────────
        h_total = (h2h or {}).get("total", 0)
        if h2h and h_total >= 2:
            hw1 = h2h.get("team1_wins", 0)
            hw2 = h2h.get("team2_wins", 0)
            th = hw1 + hw2 or 1
            s1 += hw1 / th * 10; s2 += hw2 / th * 10
            if hw1 != hw2:
                b = t1["name"] if hw1 > hw2 else t2["name"]
                factors.append(f"🤝 {b} — лидирует в H2H: {max(hw1,hw2)}-{min(hw1,hw2)}")
        else:
            s1 += 5; s2 += 5

        # Стрик-бонусы (информационно)
        for team, sign in [(t1, "t1"), (t2, "t2")]:
            st = team.get("streak", "")
            if not st: continue
            try:
                count = int(st[1:])
                if st[0] == "W" and count >= 3:
                    factors.append(f"🔴 {team['name']} — серия {count} побед подряд!")
                elif st[0] == "L" and count >= 3:
                    factors.append(f"❄️ {team['name']} — серия {count} поражений подряд")
            except: pass

        total = s1 + s2 or 100
        p1 = round(s1 / total * 100, 1)
        p2 = round(100 - p1, 1)

        if t1.get("_estimated") or t2.get("_estimated"):
            factors.append("⚠️ Нет истории матчей — прогноз неточный")

        return {
            "team1_win_chance": p1,
            "team2_win_chance": p2,
            "key_factors": factors[:5],
        }

    # ── ВСПОМОГАТЕЛЬНЫЕ ────────────────────────────────────────────
    def _form_score(self, form: str) -> float:
        weights = [1.0, 0.85, 0.7, 0.55, 0.4]
        score = 0.0
        for i, ch in enumerate(form[:5]):
            w = weights[i] if i < len(weights) else 0.3
            if ch.upper() == "W": score += w
            elif ch == "?": score += w * 0.5
        return score

    def _avg_kd(self, players: list) -> float | None:
        vals = [p["kd_ratio"] for p in players if p.get("kd_ratio")]
        return round(sum(vals) / len(vals), 2) if vals else None

    def _avg_hs(self, players: list) -> float | None:
        vals = [p["headshot_pct"] for p in players if p.get("headshot_pct")]
        return round(sum(vals) / len(vals), 1) if vals else None

    def _star_player(self, players: list) -> dict | None:
        """Лучший игрок по K/D."""
        valid = [p for p in players if p.get("kd_ratio")]
        if not valid: return None
        return max(valid, key=lambda p: p["kd_ratio"])
