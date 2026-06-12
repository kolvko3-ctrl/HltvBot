"""
Google Gemini API для анализа матча.
Бесплатный план: gemini-1.5-flash — 1500 запросов/день, 15 RPM.
"""
import aiohttp
import json
import asyncio
import logging

logger = logging.getLogger(__name__)

# gemini-1.5-flash имеет выше лимиты чем gemini-2.0-flash на бесплатном плане
MODELS = [
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
    "gemini-2.0-flash-lite",
]
BASE = "https://generativelanguage.googleapis.com/v1beta/models"


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
            lines.append(f"Winrate (20м): {t['winrate']:.0f}%")
        if t.get("winrate_last5") is not None:
            lines.append(f"Winrate (посл.5): {t['winrate_last5']:.0f}%")
        if t.get("form"):
            lines.append(f"Форма: {t['form']}")
        if t.get("streak"):
            lines.append(f"Стрик: {t['streak']}")
        if t.get("avg_round_diff") is not None:
            sign = "+" if t["avg_round_diff"] > 0 else ""
            lines.append(f"Avg ±раундов: {sign}{t['avg_round_diff']:.1f}")
        return "; ".join(lines) if lines else "нет данных"

    prompt = (
        f"Ты аналитик CS2. Прогноз на матч: {team1} vs {team2}, турнир: {event}, формат: {maps_format}.\n"
        f"Статистика — {team1}: {fmt_team(t1_stats)}. {team2}: {fmt_team(t2_stats)}. H2H: {h2h_str}.\n"
        f"Используй знания о текущих составах, HLTV рейтингах игроков и форме команд.\n"
        f"Ответь ТОЛЬКО валидным JSON без markdown:\n"
        f'{{"team1_win_pct":<25-80>,"team2_win_pct":<25-80>,"verdict":"<1 строка>","team1_players":[{{"name":"ник","role":"роль","rating":<1.0-1.5>,"form":"горячая/хорошая/средняя/слабая","note":"факт"}}],"team2_players":[{{"name":"ник","role":"роль","rating":<1.0-1.5>,"form":"горячая/хорошая/средняя/слабая","note":"факт"}}],"key_maps":"<карты>","key_factors":["ф1","ф2","ф3"],"summary":"<2 предложения>"}}'
    )

    # Пробуем модели по очереди
    for model in MODELS:
        url = f"{BASE}/{model}:generateContent?key={api_key}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers={"Content-Type": "application/json"},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1200},
                    },
                    timeout=aiohttp.ClientTimeout(total=25),
                ) as resp:
                    if resp.status == 429:
                        logger.warning(f"Gemini {model}: 429, пробуем следующую модель")
                        await asyncio.sleep(2)
                        continue
                    if resp.status != 200:
                        txt = await resp.text()
                        logger.error(f"Gemini {model}: {resp.status}: {txt[:200]}")
                        continue

                    data = await resp.json()
                    text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                    # Убираем markdown если есть
                    if "```" in text:
                        text = text.split("```")[1]
                        if text.startswith("json"):
                            text = text[4:]
                    text = text.strip()

                    result = json.loads(text)
                    # Проверяем что суммы сходятся
                    p1 = result.get("team1_win_pct", 50)
                    p2 = result.get("team2_win_pct", 50)
                    if abs(p1 + p2 - 100) > 2:
                        result["team2_win_pct"] = round(100 - p1, 1)
                    logger.info(f"Gemini {model}: успешно")
                    return result

        except json.JSONDecodeError as e:
            logger.error(f"Gemini {model} JSON ошибка: {e} | текст: {text[:200]}")
            continue
        except Exception as e:
            logger.error(f"Gemini {model} ошибка: {e}")
            continue

    logger.error("Все модели Gemini недоступны")
    return None
