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

# Актуальные составы топ-команд на июнь 2026
KNOWN_ROSTERS = """
АКТУАЛЬНЫЕ СОСТАВЫ (2025-2026, проверено):
- Team Spirit: donk, sh1ro, magixx, zont1x, chopper
- Natus Vincere: b1t, jL, iM, Aleksib, w0nderful
- G2 Esports: huNter-, nexa, malbsMd, torzsi, Snax (NiKo покинул G2 в конце 2024)
- FaZe Clan: karrigan, ropz, rain, frozen, EliGE
- Team Vitality: ZywOo, apEX, mezii, flameZ, Spinx
- MOUZ: xertioN, torzsi, siuhy, JDC, jimpphat
- Heroic: stavn, sjuush, TeSeS, jabbi, cadiaN
- Team Liquid: NAF, YEKINDAR, oSee, Twistzz, s1n
- Virtus.pro: Jame, FL1T, FAME, electroNic, n0rb3r7
- Astralis: device, Xyp9x, gla1ve, br0, draken
- FURIA: yuurih, KSCERATO, FalleN, skullz, chelo
- Team Falcons: karrigan(нет), dupreeh, MagiskB, Refrezh, hallzerk

АКТУАЛЬНЫЙ ПУЛ КАРТ CS2 (2025-2026):
Mirage, Inferno, Nuke, Ancient, Anubis, Dust2, Train
ЗАПРЕЩЕНО упоминать: Vertigo, Cache, Cobblestone, Overpass (убраны из пула)
"""


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

    prompt = f"""Ты профессиональный аналитик CS2 матчей. Проанализируй матч и ответь ТОЛЬКО валидным JSON.

{KNOWN_ROSTERS}

МАТЧ: {team1} vs {team2}
ТУРНИР: {event}
ФОРМАТ: {maps_format}

СТАТИСТИКА (реальные данные PandaScore):
{team1}: {fmt(t1_stats)}
{team2}: {fmt(t2_stats)}
H2H: {h2h_str}

ПРАВИЛА:
1. Составы — ТОЛЬКО из списка выше. Если команды нет в списке — укажи известных игроков 2025-2026 и добавь в note "состав уточнить".
2. Карты — ТОЛЬКО из актуального пула: Mirage, Inferno, Nuke, Ancient, Anubis, Dust2, Train. Vertigo/Cache/Overpass не существуют.
3. Рейтинг игрока — HLTV Rating 2.0 (~1.0 средний, ~1.2+ топ).
4. Форма игрока — основывай на реальных результатах последних месяцев.
5. Все текстовые поля на русском языке.

Ответь ТОЛЬКО валидным JSON без markdown:
{{"team1_win_pct": <целое 25-80>, "team2_win_pct": <целое 25-80, сумма=100>, "verdict": "<1 строка кто фаворит>", "team1_players": [{{"name": "<ник>", "role": "<роль>", "rating": <0.9-1.5>, "form": "<горячая/хорошая/средняя/слабая>", "note": "<факт>"}}], "team2_players": [{{"name": "<ник>", "role": "<роль>", "rating": <0.9-1.5>, "form": "<горячая/хорошая/средняя/слабая>", "note": "<факт>"}}], "key_maps": "{team1} силён на: [карты]; {team2} силён на: [карты]; спорные: [карты]", "key_factors": ["<фактор1>", "<фактор2>", "<фактор3>"], "summary": "<2 предложения анализа>"}}"""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
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
