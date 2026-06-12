"""
Groq API — бесплатно, быстро.
Получить ключ: https://console.groq.com
"""
import aiohttp
import json
import logging

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.3-70b-versatile"

# Актуальный пул карт CS2 (Premier Season 4, январь 2026)
MAP_POOL = "Mirage, Inferno, Nuke, Ancient, Anubis, Dust2, Overpass"
BANNED_MAPS = "Train, Vertigo, Cache, Cobblestone (убраны из пула)"

# Актуальные составы топ-команд (июнь 2026, источник: HLTV.org)
HLTV_ROSTERS = {
    "vitality": "apEX (IGL), ropz, ZywOo (AWP), flameZ, mezii",
    "team vitality": "apEX (IGL), ropz, ZywOo (AWP), flameZ, mezii",
    "natus vincere": "Aleksib (IGL), iM, b1t, w0nderful (AWP), makazze",
    "navi": "Aleksib (IGL), iM, b1t, w0nderful (AWP), makazze",
    "spirit": "donk, sh1ro (AWP), magixx, zont1x, tN1R (IGL)",
    "team spirit": "donk, sh1ro (AWP), magixx, zont1x, tN1R (IGL)",
    "falcons": "karrigan (IGL), NiKo, m0NESY (AWP), TeSeS, kyousuke",
    "team falcons": "karrigan (IGL), NiKo, m0NESY (AWP), TeSeS, kyousuke",
    "furia": "FalleN (IGL/AWP), yuurih, YEKINDAR, KSCERATO, molodoy",
    "aurora": "MAJ3R (IGL), XANTARES, woxic (AWP), soulfly, Wicadia",
    "mouz": "xertioN, Spinx, jL, xelex, torzsi (AWP)",
    "mousesports": "xertioN, Spinx, jL, xelex, torzsi (AWP)",
    "g2": "huNter- (IGL), malbsMd, torzsi (AWP), Snax, kyxsan",
    "g2 esports": "huNter- (IGL), malbsMd, torzsi (AWP), Snax, kyxsan",
    "faze": "ropz, rain, frozen, EliGE, karrigan (IGL) — возможны изменения",
    "faze clan": "ropz, rain, frozen, EliGE",
    "heroic": "stavn, sjuush, jabbi, cadiaN (IGL/AWP), br0",
    "the mongolz": "Techno4K, mzinho, buster, Senzu, 910 (IGL)",
    "mongolz": "Techno4K, mzinho, buster, Senzu, 910 (IGL)",
    "liquid": "NAF, YEKINDAR, oSee (AWP), Twistzz, s1n",
    "team liquid": "NAF, YEKINDAR, oSee (AWP), Twistzz, s1n",
    "virtus.pro": "Jame (IGL/AWP), FL1T, FAME, electroNic, n0rb3r7",
    "vp": "Jame (IGL/AWP), FL1T, FAME, electroNic, n0rb3r7",
    "astralis": "device (AWP), Xyp9x, gla1ve (IGL), br0, draken",
    "eternal fire": "XANTARES, Wicadia, MAJ3R (IGL), xfl0ud, imoRR",
    "big": "tabseN, k1to, faveN, prosus, hyped",
    "3dmax": "Ex3rcice, Graviti, Djoko (IGL), Lucky, afro",
    "monte": "DemQQ, hades, Pumpkin66, SELLTER, sdy",
    "pain": "hardzao, chelo, biguzera, skullz, nqz",
    "pain gaming": "hardzao, chelo, biguzera, skullz, nqz",
    "ence": "hades, gla1ve, dycha, HENU, b0RUP",
    "nip": "device, REZ, hampus, Plopski, headtr1ck",
    "ninjas in pyjamas": "device, REZ, hampus, Plopski, headtr1ck",
    "cloud9": "Ax1Le, HObbit, nafany, Krad, buster",
    "legacy": "arT, dumau, latto, n1ssim, saadzin",
    "complexity": "floppy, EliGE, hallzerk, Grim, neaLaN",
    "betboom": "KaiR0N, nafany, s1ren, Krad, SELLTER",
}


def get_known_roster(team_name: str) -> str | None:
    """Ищем состав по имени команды (без учёта регистра)."""
    key = team_name.lower().strip()
    # Прямое совпадение
    if key in HLTV_ROSTERS:
        return HLTV_ROSTERS[key]
    # Частичное совпадение
    for roster_key, roster in HLTV_ROSTERS.items():
        if roster_key in key or key in roster_key:
            return roster
    return None


async def claude_analyze(team1: str, team2: str, event: str,
                          t1_stats: dict, t2_stats: dict,
                          h2h: dict, maps_format: str,
                          api_key: str) -> dict:

    h2h_str = "нет данных"
    if h2h and h2h.get("total", 0) > 0:
        lm = h2h.get("last_matches", [])
        lm_str = ", ".join(f"{x['date']} {x['format']}→{x['winner']}" for x in lm)
        h2h_str = f"{team1}: {h2h['team1_wins']}п, {team2}: {h2h['team2_wins']}п. Последние: {lm_str}"

    def fmt(t):
        parts = []
        if t.get("winrate") is not None: parts.append(f"winrate={t['winrate']:.0f}%")
        if t.get("winrate_last5") is not None: parts.append(f"last5={t['winrate_last5']:.0f}%")
        if t.get("form"): parts.append(f"form={t['form']}")
        if t.get("avg_round_diff") is not None:
            s = "+" if t["avg_round_diff"] > 0 else ""
            parts.append(f"round_diff={s}{t['avg_round_diff']:.1f}")
        return ", ".join(parts) or "нет данных"

    # Проверяем известные составы
    t1_roster = get_known_roster(team1)
    t2_roster = get_known_roster(team2)

    roster_block = ""
    if t1_roster:
        roster_block += f"ИЗВЕСТНЫЙ СОСТАВ {team1}: {t1_roster}\n"
    if t2_roster:
        roster_block += f"ИЗВЕСТНЫЙ СОСТАВ {team2}: {t2_roster}\n"
    if roster_block:
        roster_block = f"\nПРОВЕРЕНЫЕ ДАННЫЕ СОСТАВОВ (источник: HLTV, июнь 2026):\n{roster_block}"

    prompt = f"""Ты профессиональный аналитик CS2. Проанализируй матч и ответь ТОЛЬКО валидным JSON на русском языке.

МАТЧ: {team1} vs {team2}
ТУРНИР: {event}
ФОРМАТ: {maps_format}

СТАТИСТИКА (реальные данные):
{team1}: {fmt(t1_stats)}
{team2}: {fmt(t2_stats)}
H2H: {h2h_str}
{roster_block}
АКТУАЛЬНЫЙ ПУЛ КАРТ CS2 (2026): {MAP_POOL}
ЗАПРЕЩЕНО упоминать карты: {BANNED_MAPS}

СТРОГИЕ ПРАВИЛА:
1. Если состав указан выше — используй ТОЛЬКО этих игроков, никаких замен.
2. Если состав НЕ указан — найди актуальный состав 2025-2026 из своих знаний. Пиши РЕАЛЬНЫЕ ники игроков.
3. НИКОГДА не пиши "Неизвестен", "Unknown", "Игрок1" — только реальные игровые ники.
4. Карты — ТОЛЬКО из актуального пула 2026. Train и Vertigo не существуют.
5. Рейтинг игрока — HLTV Rating 2.0 (средний 1.0, хороший 1.15+, топ 1.3+).
6. Все текстовые поля строго на русском языке.

Ответь ТОЛЬКО валидным JSON без markdown:
{{"team1_win_pct": <целое 25-80>, "team2_win_pct": <целое 25-80, сумма=100>, "verdict": "<1 строка кто фаворит>", "team1_players": [{{"name": "<реальный ник>", "role": "<роль>", "rating": <0.9-1.5>, "form": "<горячая/хорошая/средняя/слабая>", "note": "<конкретный факт>"}}], "team2_players": [{{"name": "<реальный ник>", "role": "<роль>", "rating": <0.9-1.5>, "form": "<горячая/хорошая/средняя/слабая>", "note": "<конкретный факт>"}}], "key_maps": "{team1} силён на: [карты]; {team2} силён на: [карты]; спорные: [карты]", "key_factors": ["<фактор1>", "<фактор2>", "<фактор3>"], "summary": "<2 предложения итогового анализа>"}}"""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.15,
                    "max_tokens": 1500,
                    "response_format": {"type": "json_object"},
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 429:
                    logger.warning("Groq 429 — лимит запросов")
                    return None
                if resp.status != 200:
                    txt = await resp.text()
                    logger.error(f"Groq {resp.status}: {txt[:300]}")
                    return None

                data = await resp.json()
                text = data["choices"][0]["message"]["content"].strip()
                result = json.loads(text)

                # Фильтруем "Неизвестен" на случай если модель всё равно написала
                bad_names = {"неизвестен", "unknown", "tbd", "?", "игрок", "player",
                             "игрок1", "игрок2", "игрок3", "игрок4", "игрок5",
                             "овнер", "овнер2", "name"}
                for key in ("team1_players", "team2_players"):
                    players = result.get(key, [])
                    result[key] = [p for p in players
                                   if p.get("name", "").lower().strip() not in bad_names]

                p1 = int(result.get("team1_win_pct", 50))
                if int(result.get("team2_win_pct", 50)) + p1 != 100:
                    result["team2_win_pct"] = 100 - p1
                return result

    except json.JSONDecodeError as e:
        logger.error(f"Groq JSON ошибка: {e}")
        return None
    except Exception as e:
        logger.error(f"Groq ошибка: {e}")
        return None
