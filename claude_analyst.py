"""
Groq API — бесплатно, быстро, без жёстких лимитов.
Получить ключ: https://console.groq.com
"""
import aiohttp
import json
import logging

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.3-70b-versatile"


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
        if t.get("streak"): parts.append(f"streak={t['streak']}")
        if t.get("avg_round_diff") is not None:
            s = "+" if t["avg_round_diff"] > 0 else ""
            parts.append(f"round_diff={s}{t['avg_round_diff']:.1f}")
        return ", ".join(parts) or "нет данных"

    prompt = f"""Ты профессиональный аналитик CS2 матчей. Проанализируй матч и ответь ТОЛЬКО валидным JSON на русском языке.

Матч: {team1} vs {team2}
Турнир: {event}
Формат: {maps_format}

Статистика:
{team1}: {fmt(t1_stats)}
{team2}: {fmt(t2_stats)}
H2H: {h2h_str}

Используй свои знания о текущих составах команд, рейтингах игроков на HLTV, последних результатах на турнирах и стиле игры каждой команды.

Ответь ТОЛЬКО этим JSON без markdown и пояснений, все текстовые поля на русском:
{{"team1_win_pct": <целое 25-80>, "team2_win_pct": <целое 25-80, сумма=100>, "verdict": "<1 строка — кто фаворит и почему>", "team1_players": [{{"name": "<игровой ник>", "role": "<роль на русском>", "rating": <float 0.9-1.5>, "form": "<горячая/хорошая/средняя/слабая>", "note": "<1 факт об игроке на русском>"}}], "team2_players": [{{"name": "<игровой ник>", "role": "<роль на русском>", "rating": <float 0.9-1.5>, "form": "<горячая/хорошая/средняя/слабая>", "note": "<1 факт об игроке на русском>"}}], "key_maps": "<подробно: {team1} силён на [карты]; {team2} силён на [карты]; спорные карты: [карты]>", "key_factors": ["<фактор 1 на русском>", "<фактор 2>", "<фактор 3>"], "summary": "<2 предложения итогового анализа на русском>"}}"""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                GROQ_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 1500,
                    "response_format": {"type": "json_object"},
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 429:
                    logger.warning("Groq 429 — слишком много запросов")
                    return None
                if resp.status != 200:
                    txt = await resp.text()
                    logger.error(f"Groq {resp.status}: {txt[:300]}")
                    return None

                data = await resp.json()
                text = data["choices"][0]["message"]["content"].strip()
                result = json.loads(text)
                p1 = int(result.get("team1_win_pct", 50))
                if p1 + int(result.get("team2_win_pct", 50)) != 100:
                    result["team2_win_pct"] = 100 - p1
                return result

    except json.JSONDecodeError as e:
        logger.error(f"Groq JSON ошибка: {e}")
        return None
    except Exception as e:
        logger.error(f"Groq ошибка: {e}")
        return None
