"""
Groq API — бесплатно, быстро, без жёстких лимитов.
Получить ключ: https://console.groq.com (войти через Google, ключ сразу)
Модель: llama-3.3-70b-versatile — отлично знает CS2
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
        return ", ".join(parts) or "no data"

    prompt = f"""You are a professional CS2 match analyst. Analyze this match and respond ONLY with valid JSON.

Match: {team1} vs {team2}
Tournament: {event}
Format: {maps_format}

Recent stats:
{team1}: {fmt(t1_stats)}
{team2}: {fmt(t2_stats)}
H2H: {h2h_str}

Use your knowledge of current rosters, HLTV player ratings, recent tournament results, and team playstyles.

Respond ONLY with this exact JSON structure, no markdown, no explanation:
{{"team1_win_pct": <integer 25-80>, "team2_win_pct": <integer 25-80, sum must equal 100>, "verdict": "<one line who is favorite and why>", "team1_players": [{{"name": "<ingame name>", "role": "<role>", "rating": <float 0.9-1.5>, "form": "<горячая/хорошая/средняя/слабая>", "note": "<one key fact>"}}], "team2_players": [{{"name": "<ingame name>", "role": "<role>", "rating": <float 0.9-1.5>, "form": "<горячая/хорошая/средняя/слабая>", "note": "<one key fact>"}}], "key_maps": "<maps each team is strong on>", "key_factors": ["<factor1>", "<factor2>", "<factor3>"], "summary": "<2 sentence analysis>"}}"""

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
                    logger.warning("Groq 429 — слишком много запросов, подождите")
                    return None
                if resp.status != 200:
                    txt = await resp.text()
                    logger.error(f"Groq {resp.status}: {txt[:300]}")
                    return None

                data = await resp.json()
                text = data["choices"][0]["message"]["content"].strip()
                result = json.loads(text)

                # Проверяем что сумма = 100
                p1 = int(result.get("team1_win_pct", 50))
                p2 = int(result.get("team2_win_pct", 50))
                if p1 + p2 != 100:
                    result["team2_win_pct"] = 100 - p1
                return result

    except json.JSONDecodeError as e:
        logger.error(f"Groq JSON ошибка: {e}")
        return None
    except Exception as e:
        logger.error(f"Groq ошибка: {e}")
        return None
