"""
Использует Google Gemini API для анализа матча.
Бесплатный план: 1500 запросов/день.
Получить ключ: https://aistudio.google.com/app/apikey
"""
import aiohttp
import json
import logging

logger = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"


async def claude_analyze(team1: str, team2: str, event: str,
                          t1_stats: dict, t2_stats: dict,
                          h2h: dict, maps_format: str,
                          api_key: str) -> dict:

    h2h_str = "нет данных"
    if h2h and h2h.get("total", 0) > 0:
        lm = h2h.get("last_matches", [])
        lm_str = ", ".join(f"{x['date']} {x['format']}→{x['winner']}" for x in lm)
        h2h_str = f"{team1}: {h2h['team1_wins']}п, {team2}: {h2h['team2_wins']}п. Последние: {lm_str}"

    def fmt_team(t):
        lines = []
        if t.get("winrate") is not None:
            lines.append(f"Winrate (20 матчей): {t['winrate']:.0f}%")
        if t.get("winrate_last5") is not None:
            lines.append(f"Winrate последних 5: {t['winrate_last5']:.0f}%")
        if t.get("form"):
            lines.append(f"Форма: {t['form']}")
        if t.get("streak"):
            lines.append(f"Стрик: {t['streak']}")
        if t.get("avg_round_diff") is not None:
            sign = "+" if t["avg_round_diff"] > 0 else ""
            lines.append(f"Avg разница раундов: {sign}{t['avg_round_diff']:.1f}")
        return "\n".join(lines) if lines else "нет данных"

    prompt = f"""Ты аналитик CS2 матчей. Дай точный прогноз на матч.

МАТЧ: {team1} vs {team2}
ТУРНИР: {event}
ФОРМАТ: {maps_format}

СТАТИСТИКА:
{team1}: {fmt_team(t1_stats)}
{team2}: {fmt_team(t2_stats)}
H2H: {h2h_str}

Используй свои знания о текущих составах, рейтингах игроков на HLTV, их форме и стиле игры команд.

Ответь ТОЛЬКО в JSON, без markdown и без пояснений:
{{"team1_win_pct": <25-80>, "team2_win_pct": <25-80, сумма=100>, "verdict": "<кто фаворит и почему, 1 строка>", "team1_players": [{{"name": "ник", "role": "роль", "rating": <1.0-1.5>, "form": "горячая/хорошая/средняя/слабая", "note": "1 факт об игроке"}}], "team2_players": [{{"name": "ник", "role": "роль", "rating": <1.0-1.5>, "form": "горячая/хорошая/средняя/слабая", "note": "1 факт"}}], "key_maps": "<карты где каждая сильна>", "key_factors": ["фактор1", "фактор2", "фактор3"], "summary": "<2 предложения итога>"}}"""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{GEMINI_URL}?key={api_key}",
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1500}},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    txt = await resp.text()
                    logger.error(f"Gemini API {resp.status}: {txt[:300]}")
                    return None
                data = await resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                text = text.replace("```json", "").replace("```", "").strip()
                return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        return None
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        return None
