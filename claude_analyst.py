"""
Groq API — ТОЛЬКО текстовый анализ.
Проценты считает analyzer.py — Groq их даже не видит, чтобы не было соблазна
подгонять текст под несуществующие у себя цифры.

Принимает уже готовый p1/p2 от модели и объясняет ПОЧЕМУ именно такой расклад,
с упором на карты и текущую форму команд. Составы игроков убраны — PandaScore
давал слишком неточные/устаревшие ростеры, это создавало путаницу.
"""
import aiohttp
import json
import logging

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.3-70b-versatile"

MAP_POOL = "Mirage, Inferno, Nuke, Ancient, Anubis, Dust2, Overpass"
BANNED_MAPS = "Train, Vertigo, Cache, Cobblestone"

COLOGNE_CONTEXT = """
IEM COLOGNE MAJOR 2026 — КОНТЕКСТ (15 июня 2026):
Прошли в плей-офф: Spirit 3-0, FURIA 3-0, Falcons 3-1, BetBoom 3-1
Финал: Falcons vs Vitality
Сенсации: 9z (#35) обыграли Vitality (#1); BetBoom обыграли и Falcons и Vitality
Valve Ranking: #1 Vitality, #2 Spirit
"""


async def claude_analyze(
    team1: str, team2: str, event: str,
    t1_stats: dict, t2_stats: dict,
    h2h: dict, maps_format: str,
    api_key: str,
    p1: float, p2: float,  # ГОТОВЫЕ проценты от модели — Groq их объясняет, не придумывает
) -> dict:

    h2h_str = "нет данных"
    if h2h and h2h.get("total", 0) > 0:
        lm = h2h.get("last_matches", [])
        lm_str = ", ".join(f"{x['date']} {x['format']}→{x['winner']}" for x in lm)
        h2h_str = f"{team1}: {h2h['team1_wins']}п, {team2}: {h2h['team2_wins']}п. Последние: {lm_str}"

    def fmt(t):
        parts = []
        if t.get("weighted_winrate") is not None:
            parts.append(f"взвешенная форма (7 матчей)={t['weighted_winrate']:.0f}%")
        if t.get("form"): parts.append(f"результаты={t['form']}")
        if t.get("avg_round_diff") is not None:
            s = "+" if t["avg_round_diff"] > 0 else ""
            parts.append(f"avg_раунды={s}{t['avg_round_diff']:.1f}")
        recent = t.get("recent_matches") or []
        if recent:
            rm = "; ".join(f"{m['result']} vs {m['opponent']}" for m in recent[:4])
            parts.append(f"последние матчи: {rm}")
        return ", ".join(parts) or "нет данных"

    winner_name = team1 if p1 >= p2 else team2
    winner_pct = max(p1, p2)

    prompt = f"""Ты эксперт-аналитик CS2. ПРОЦЕНТЫ УЖЕ ПОСЧИТАНЫ нашей моделью — твоя задача ОБЪЯСНИТЬ их через карты и текущую форму, а не придумать свои цифры.

{COLOGNE_CONTEXT}

━━━ МАТЧ ━━━
{team1} vs {team2}
Турнир: {event} | Формат: {maps_format}

━━━ ГОТОВЫЙ ПРОГНОЗ МОДЕЛИ (не меняй эти числа!) ━━━
{team1}: {p1}%
{team2}: {p2}%
Фаворит по модели: {winner_name} ({winner_pct:.0f}%)

━━━ ДАННЫЕ ЗА ПОСЛЕДНИЕ МАТЧИ ━━━
{team1}: {fmt(t1_stats)}
{team2}: {fmt(t2_stats)}
H2H: {h2h_str}

━━━ ПУЛ КАРТ ━━━
Актуальные: {MAP_POOL}
НЕ упоминать: {BANNED_MAPS}

━━━ ЗАДАЧА ━━━
1. Объясни ПОЧЕМУ модель дала именно такой процент {winner_name} ({winner_pct:.0f}%) — опирайся на форму команд и карты
2. НЕ предлагай свои проценты — используй ТОЛЬКО {p1}% и {p2}% что даны
3. Для каждой карты пула дай короткую оценку — у кого преимущество и почему (форма, история на карте, стиль игры)
4. Не упоминай конкретных игроков по именам — фокус только на КОМАНДНОМ уровне (форма, темп, стабильность)
5. Карты — только из актуального пула
6. Всё строго на русском

Ответь ТОЛЬКО валидным JSON без markdown:
{{"verdict": "<1 строка — почему {winner_name} фаворит, с конкретной причиной из формы/статистики>", "maps_analysis": [{{"map": "<название карты>", "favored": "<{team1}/{team2}/равно>", "reason": "<короткая причина 1 строка>"}}], "key_factors": ["<факт1 про форму/статистику>", "<факт2>", "<факт3>"], "summary": "<2-3 предложения — итоговый разбор формы обеих команд и почему расклад {p1}/{p2}>"}}"""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.15,
                    "max_tokens": 1400,
                    "response_format": {"type": "json_object"},
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 429:
                    logger.warning("Groq 429"); return None
                if resp.status != 200:
                    txt = await resp.text()
                    logger.error(f"Groq {resp.status}: {txt[:300]}")
                    return None
                data = await resp.json()
                text = data["choices"][0]["message"]["content"].strip()
                result = json.loads(text)

                # ВАЖНО: гарантируем что проценты не изменились
                result["team1_win_pct"] = p1
                result["team2_win_pct"] = p2
                return result

    except json.JSONDecodeError as e:
        logger.error(f"Groq JSON ошибка: {e}"); return None
    except Exception as e:
        logger.error(f"Groq ошибка: {e}"); return None
