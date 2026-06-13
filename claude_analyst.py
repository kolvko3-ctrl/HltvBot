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

MAP_POOL = "Mirage, Inferno, Nuke, Ancient, Anubis, Dust2, Overpass"
BANNED_MAPS = "Train, Vertigo, Cache, Cobblestone (убраны из пула)"

# Составы всех 32 команд IEM Cologne Major 2026 (апрель 2026, Wikipedia)
HLTV_ROSTERS = {
    # === LEGENDS (Stage 3) ===
    "team vitality": "apEX (IGL), flameZ, mezii, ropz, ZywOo (AWP)",
    "vitality": "apEX (IGL), flameZ, mezii, ropz, ZywOo (AWP)",
    "natus vincere": "Aleksib (IGL), b1t, iM, makazze, w0nderful (AWP)",
    "navi": "Aleksib (IGL), b1t, iM, makazze, w0nderful (AWP)",
    "parivision": "BELCHONOKK, Jame (IGL/AWP), nota, Patsi, relaxa",
    "team falcons": "kyousuke, kyxsan, m0NESY (AWP), NiKo, karrigan (IGL, замена kyxsan)",
    "falcons": "kyousuke, kyxsan, m0NESY (AWP), NiKo, karrigan (IGL, замена kyxsan)",
    "aurora gaming": "MAJ3R (IGL), soulfly, Wicadia, XANTARES, jottAAA",
    "aurora": "MAJ3R (IGL), soulfly, Wicadia, XANTARES, jottAAA",
    "mouz": "Brollan, jimpphat, Spinx, torzsi (AWP), xelex",
    "mousesports": "Brollan, jimpphat, Spinx, torzsi (AWP), xelex",
    "furia esports": "FalleN (IGL/AWP), KSCERATO, molodoy, YEKINDAR, yuurih",
    "furia": "FalleN (IGL/AWP), KSCERATO, molodoy, YEKINDAR, yuurih",
    "the mongolz": "910 (IGL), bLitz, cobraze, maaRaa, Techno4K",
    "mongolz": "910 (IGL), bLitz, cobraze, maaRaa, Techno4K",
    # === CHALLENGERS (Stage 2) ===
    "team spirit": "donk, magixx, sh1ro (AWP), tN1R (IGL), zont1x",
    "spirit": "donk, magixx, sh1ro (AWP), tN1R (IGL), zont1x",
    "astralis": "HooXi (IGL), jabbi, phzy, RAALZ, ztr",
    "g2 esports": "HeavyGod, huNter-, MATYS, malbsMd, Snax",
    "g2": "HeavyGod, huNter-, MATYS, malbsMd, Snax",
    "fut esports": "cmtry, dem0n, dziugss, Krabeni, npl",
    "fut": "cmtry, dem0n, dziugss, Krabeni, npl",
    "monte": "afro, AZUWU, Bymas, Gizmoe, kakafu",
    "9z team": "dgt, HUASOPEEK, luchov, max, BIT",
    "9z": "dgt, HUASOPEEK, luchov, max, BIT",
    "pain gaming": "biguzera, nqz, piriajr, saffee (AWP), torzsi",
    "pain": "biguzera, nqz, piriajr, saffee (AWP), torzsi",
    "legacy": "arT (IGL), dumau, latto, saadzin, n1ssim",
    # === CONTENDERS (Stage 1) ===
    "gamerlegion": "hypex, PR, REZ, Snax, BOROS",
    "big clan": "blameF, faveN, gr1ks, JDC, JBOEN",
    "big": "blameF, faveN, gr1ks, JDC, JBOEN",
    "betboom team": "Boombl4, FL4MUS, Magnojez, Polt, d1Ledez",
    "betboom": "Boombl4, FL4MUS, Magnojez, Polt, d1Ledez",
    "b8 esports": "alex666, esenthial, kensizor, mASKED, sdy",
    "b8": "alex666, esenthial, kensizor, mASKED, sdy",
    "heroic": "Chr1zN, nilo, susp, Yase, TOBIZ",
    "sinners esports": "beastik, kisserek, MoDo, Poljanoj, CacaNito",
    "sinners": "beastik, kisserek, MoDo, Poljanoj, CacaNito",
    "m80": "JBa, Lake, s1n, slaxz-, JDC",
    "nrg esports": "br0, Grim, nitr0, oSee (AWP), RUSH",
    "nrg": "br0, Grim, nitr0, oSee (AWP), RUSH",
    "sharks esports": "doc, gafolo, koala, max, n1ssim",
    "sharks": "doc, gafolo, koala, max, n1ssim",
    "gaimin gladiators": "felps, HEN1, JOTA, Lucas1, horvy",
    "gaimin": "felps, HEN1, JOTA, Lucas1, horvy",
    "mibr": "brnz4n, insani, kl1m, lnk, brn",
    "team liquid": "EliGE, malbsMd, NAF, oSee (AWP), jokasteve",
    "liquid": "EliGE, malbsMd, NAF, oSee (AWP), jokasteve",
    "tyloo": "JamYoung, Jee, Mercury, Moseyuh, Attacker",
    "lynn vision gaming": "C4LLM3SU3, EmiliaQAQ, Starry, Westmelon, GUM",
    "lynn vision": "C4LLM3SU3, EmiliaQAQ, Starry, Westmelon, GUM",
    "thunder downunder": "aliStair, asap, dexter, sliimey, viridian",
    "flyquest": "INS, jks, nettik, story, AZR",
    # === Другие известные команды ===
    "virtus.pro": "Jame (IGL/AWP), FL1T, FAME, electroNic, n0rb3r7",
    "vp": "Jame (IGL/AWP), FL1T, FAME, electroNic, n0rb3r7",
    "faze clan": "ropz, rain, frozen, EliGE, karrigan (IGL)",
    "faze": "ropz, rain, frozen, EliGE, karrigan (IGL)",
    "cloud9": "Ax1Le, HObbit, nafany, Krad, buster",
    "heroic old": "stavn, sjuush, jabbi, cadiaN (IGL/AWP), TeSeS",
    "ence": "hades, dycha, HENU, b0RUP, sLowi",
    "eternal fire": "XANTARES, Wicadia, MAJ3R (IGL), xfl0ud, imoRR",
}


def get_known_roster(team_name: str) -> str | None:
    key = team_name.lower().strip()
    if key in HLTV_ROSTERS:
        return HLTV_ROSTERS[key]
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

    t1_roster = get_known_roster(team1)
    t2_roster = get_known_roster(team2)

    roster_block = ""
    if t1_roster:
        roster_block += f"СОСТАВ {team1} (проверено, IEM Cologne 2026): {t1_roster}\n"
    if t2_roster:
        roster_block += f"СОСТАВ {team2} (проверено, IEM Cologne 2026): {t2_roster}\n"
    if roster_block:
        roster_block = f"\n⚠️ ОБЯЗАТЕЛЬНО использовать эти составы:\n{roster_block}"

    prompt = f"""Ты профессиональный аналитик CS2. Проанализируй матч и ответь ТОЛЬКО валидным JSON на русском языке.

МАТЧ: {team1} vs {team2}
ТУРНИР: {event}
ФОРМАТ: {maps_format}

СТАТИСТИКА (реальные данные PandaScore):
{team1}: {fmt(t1_stats)}
{team2}: {fmt(t2_stats)}
H2H: {h2h_str}
{roster_block}
АКТУАЛЬНЫЙ ПУЛ КАРТ CS2 (январь 2026): {MAP_POOL}
ЗАПРЕЩЕНО упоминать: {BANNED_MAPS}

ПРАВИЛА:
1. Если состав указан выше — используй ТОЛЬКО этих игроков, никаких замен и выдуманных ников.
2. Если состав НЕ указан — пиши реальные ники игроков из своих знаний 2025-2026.
3. НИКОГДА не пиши "Неизвестен", "Unknown", "Игрок1", "овнер" — только реальные ники.
4. Карты только из актуального пула 2026.
5. Все поля строго на русском языке.

Ответь ТОЛЬКО валидным JSON без markdown:
{{"team1_win_pct": <целое 25-80>, "team2_win_pct": <целое 25-80, сумма=100>, "verdict": "<1 строка>", "team1_players": [{{"name": "<реальный ник>", "role": "<роль>", "rating": <0.9-1.5>, "form": "<горячая/хорошая/средняя/слабая>", "note": "<факт>"}}], "team2_players": [{{"name": "<реальный ник>", "role": "<роль>", "rating": <0.9-1.5>, "form": "<горячая/хорошая/средняя/слабая>", "note": "<факт>"}}], "key_maps": "{team1} силён на: [карты]; {team2} силён на: [карты]; спорные: [карты]", "key_factors": ["<фактор1>", "<фактор2>", "<фактор3>"], "summary": "<2 предложения>"}}"""

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
                    logger.warning("Groq 429")
                    return None
                if resp.status != 200:
                    txt = await resp.text()
                    logger.error(f"Groq {resp.status}: {txt[:300]}")
                    return None
                data = await resp.json()
                text = data["choices"][0]["message"]["content"].strip()
                result = json.loads(text)
                bad = {"неизвестен","unknown","tbd","?","игрок","player",
                       "игрок1","игрок2","игрок3","игрок4","игрок5",
                       "овнер","овнер2","name","n/a"}
                for key in ("team1_players", "team2_players"):
                    result[key] = [p for p in result.get(key, [])
                                   if p.get("name","").lower().strip() not in bad]
                p1 = int(result.get("team1_win_pct", 50))
                if int(result.get("team2_win_pct", 50)) + p1 != 100:
                    result["team2_win_pct"] = 100 - p1
                return result
    except json.JSONDecodeError as e:
        logger.error(f"JSON ошибка: {e}"); return None
    except Exception as e:
        logger.error(f"Groq ошибка: {e}"); return None
