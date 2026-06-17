"""
Groq API — ТОЛЬКО текстовый анализ.
Проценты считает analyzer.py — Groq их даже не видит, чтобы не было соблазна
подгонять текст под несуществующие у себя цифры.

Принимает уже готовый p1/p2 от модели и объясняет ПОЧЕМУ именно такой расклад,
опираясь на реальные составы (из PandaScore) и контекст турнира.
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
Valve Ranking: #1 Vitality (apEX, ropz, ZywOo, flameZ, mezii), #2 Spirit (sh1ro, magixx, tN1R, zont1x, donk)
"""


async def claude_analyze(
    team1: str, team2: str, event: str,
    t1_stats: dict, t2_stats: dict,
    h2h: dict, maps_format: str,
    api_key: str,
    p1: float, p2: float,  # ГОТОВЫЕ проценты от модели — Groq их объясняет, не придумывает
    team1_roster: list[str] | None = None,
    team2_roster: list[str] | None = None,
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

    def roster_block(name, roster):
        if not roster: return f"{name}: состав неизвестен"
        # Жёсткий кап на 5 — это основа, не более. Если пришло больше,
        # значит фильтрация на уровне API не сработала и затесались резервисты.
        active_five = roster[:5]
        return f"{name} (АКТИВНЫЙ состав, только основа — PandaScore): {', '.join(active_five)}"

    r1_block = roster_block(team1, team1_roster)
    r2_block = roster_block(team2, team2_roster)
    winner_name = team1 if p1 >= p2 else team2
    winner_pct = max(p1, p2)

    prompt = f"""Ты эксперт-аналитик CS2. ПРОЦЕНТЫ УЖЕ ПОСЧИТАНЫ нашей моделью — твоя задача ОБЪЯСНИТЬ их, а не придумать свои.

{COLOGNE_CONTEXT}

━━━ МАТЧ ━━━
{team1} vs {team2}
Турнир: {event} | Формат: {maps_format}

━━━ ГОТОВЫЙ ПРОГНОЗ МОДЕЛИ (не меняй эти числа!) ━━━
{team1}: {p1}%
{team2}: {p2}%
Фаворит по модели: {winner_name} ({winner_pct:.0f}%)

━━━ СОСТАВЫ (PandaScore, реальное время) ━━━
{r1_block}
{r2_block}

━━━ ДАННЫЕ ЗА ПОСЛЕДНИЕ МАТЧИ ━━━
{team1}: {fmt(t1_stats)}
{team2}: {fmt(t2_stats)}
H2H: {h2h_str}

━━━ ПУЛ КАРТ ━━━
Актуальные: {MAP_POOL}
НЕ упоминать: {BANNED_MAPS}

━━━ ЗАДАЧА ━━━
1. Объясни ПОЧЕМУ модель дала именно такой процент {winner_name} ({winner_pct:.0f}%) — используй реальные данные выше
2. НЕ предлагай свои проценты — используй ТОЛЬКО {p1}% и {p2}% что даны
3. Составы — ТОЛЬКО из списков выше, не выдумывай игроков
4. РОВНО 5 игроков в каждой команде (основной состав), НЕ больше — даже если в списке выше случайно оказалось больше имён, выбери 5 самых вероятных стартовых
5. Дай рейтинг каждому игроку (HLTV Rating 2.0: ~1.0 средний, ~1.2 хороший, ~1.35+ топ)
6. Карты — только из актуального пула
7. Всё строго на русском

Ответь ТОЛЬКО валидным JSON без markdown:
{{"verdict": "<1 строка — почему {winner_name} фаворит, с конкретным фактом>", "team1_players": [{{"name": "<ник из списка>", "role": "<роль>", "rating": <0.85-1.5>, "form": "<горячая/хорошая/средняя/слабая>", "note": "<факт>"}}], "team2_players": [{{"name": "<ник из списка>", "role": "<роль>", "rating": <0.85-1.5>, "form": "<горячая/хорошая/средняя/слабая>", "note": "<факт>"}}], "key_maps": "{team1} силён: [карты]; {team2} силён: [карты]", "key_factors": ["<факт1>", "<факт2>", "<факт3>"], "summary": "<2 предложения объясняющих расклад {p1}/{p2}>"}}"""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.15,
                    "max_tokens": 1600,
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

                # Фильтр выдуманных имён
                valid1 = {n.lower() for n in (team1_roster or [])}
                valid2 = {n.lower() for n in (team2_roster or [])}
                bad = {"неизвестен","unknown","tbd","?","игрок","player","n/a","name"}

                if valid1:
                    result["team1_players"] = [
                        p for p in result.get("team1_players", [])
                        if p.get("name","").lower().strip() not in bad
                        and (p.get("name","").lower() in valid1
                             or any(v in p.get("name","").lower() for v in valid1))
                    ]
                if valid2:
                    result["team2_players"] = [
                        p for p in result.get("team2_players", [])
                        if p.get("name","").lower().strip() not in bad
                        and (p.get("name","").lower() in valid2
                             or any(v in p.get("name","").lower() for v in valid2))
                    ]

                # Финальный жёсткий кап: ровно 5 игроков максимум на команду,
                # даже если фильтрация выше пропустила больше (например, дубли).
                for key in ("team1_players", "team2_players"):
                    players = result.get(key, [])
                    if len(players) > 5:
                        players = sorted(players, key=lambda p: p.get("rating") or 0, reverse=True)[:5]
                    result[key] = players

                # ВАЖНО: гарантируем что проценты не изменились
                result["team1_win_pct"] = p1
                result["team2_win_pct"] = p2
                return result

    except json.JSONDecodeError as e:
        logger.error(f"Groq JSON ошибка: {e}"); return None
    except Exception as e:
        logger.error(f"Groq ошибка: {e}"); return None
