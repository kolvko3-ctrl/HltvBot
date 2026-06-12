"""
Использует Claude API для глубокого анализа матча.
Claude знает статистику игроков, историю команд, форму и стиль игры.
"""
import aiohttp
import json
import logging

logger = logging.getLogger(__name__)

CLAUDE_API = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-sonnet-4-6"


async def claude_analyze(team1: str, team2: str, event: str,
                          t1_stats: dict, t2_stats: dict,
                          h2h: dict, maps_format: str,
                          anthropic_key: str) -> dict:
    """
    Передаём Claude объективные данные о командах + просим его
    использовать свои знания об игроках для финального анализа.
    """

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
        if t.get("maps_played"):
            lines.append(f"Карт сыграно (база): {t['maps_played']}")
        return "\n".join(lines) if lines else "нет данных"

    prompt = f"""Ты аналитик CS2 матчей. Тебе нужно дать точный прогноз на матч и подробный анализ.

МАТЧ: {team1} vs {team2}
ТУРНИР: {event}
ФОРМАТ: {maps_format}

ОБЪЕКТИВНАЯ СТАТИСТИКА (PandaScore API):

{team1}:
{fmt_team(t1_stats)}

{team2}:
{fmt_team(t2_stats)}

H2H: {h2h_str}

Используй свои знания о:
- Текущей форме и составах этих команд (игроки, их рейтинг на HLTV, роли)
- Стиле игры каждой команды (агрессивный/пассивный, любимые карты)
- Последних результатах на крупных турнирах
- Ключевых игроках и их текущей форме (donk, sh1ro, b1t, Aleksib и т.д.)
- Тренерском штабе и тактических изменениях

Ответь СТРОГО в JSON формате без markdown:
{{
  "team1_win_pct": <число от 25 до 80>,
  "team2_win_pct": <число от 25 до 80, сумма с team1=100>,
  "verdict": "<одна строка: кто фаворит и почему>",
  "team1_players": [
    {{"name": "никнейм", "role": "роль", "rating": <HLTV рейтинг примерно>, "form": "горячая/хорошая/средняя/слабая", "note": "1 факт"}},
    ...все 5 игроков...
  ],
  "team2_players": [
    {{"name": "никнейм", "role": "роль", "rating": <HLTV рейтинг примерно>, "form": "горячая/хорошая/средняя/слабая", "note": "1 факт"}},
    ...все 5 игроков...
  ],
  "key_maps": "<карты где каждая команда сильна>",
  "key_factors": ["фактор 1", "фактор 2", "фактор 3", "фактор 4"],
  "summary": "<2-3 предложения итогового анализа>"
}}"""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                CLAUDE_API,
                headers={
                    "x-api-key": anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": CLAUDE_MODEL,
                    "max_tokens": 1500,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    txt = await resp.text()
                    logger.error(f"Claude API {resp.status}: {txt[:200]}")
                    return None
                data = await resp.json()
                text = data["content"][0]["text"].strip()
                # Убираем возможные markdown-блоки
                text = text.replace("```json", "").replace("```", "").strip()
                return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        return None
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return None
