"""
Groq API анализ матчей CS2.
КЛЮЧЕВОЕ УЛУЧШЕНИЕ: составы приходят из PandaScore (реальное время),
Groq только анализирует этих конкретных игроков.
"""
import aiohttp
import json
import logging

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.3-70b-versatile"

MAP_POOL = "Mirage, Inferno, Nuke, Ancient, Anubis, Dust2, Overpass"
BANNED_MAPS = "Train, Vertigo, Cache, Cobblestone"

# Контекст IEM Cologne Major 2026 (актуально на 15 июня 2026)
COLOGNE_CONTEXT = """
IEM COLOGNE MAJOR 2026 — АКТУАЛЬНЫЙ КОНТЕКСТ (15 июня 2026):

ПЛЕЙ-ОФФ УЧАСТНИКИ (уже прошли):
- Team Spirit 3-0: победили NaVi, Aurora, 9z — donk лучший на турнире
- FURIA 3-0: победили MongolZ, G2, BetBoom — команда в пике формы
- Falcons 3-1: проиграли BetBoom, потом NiKo "проснулся", победили Aurora/G2/NaVi
- BetBoom 3-1: СЕНСАЦИЯ — победили Falcons И Vitality, проиграли FURIA

ФИНАЛИСТЫ (Grand Final уже объявлен):
- Falcons vs Vitality — Grand Final Cologne Major 2026

ЧТО ВАЖНО ЗНАТЬ:
- 9z (#35 мир) обыграли Vitality (#1 мир) 2:1 — рейтинг не гарантирует победу
- BetBoom обыграли и Falcons и Vitality — сенсационный Major
- NaVi выглядели нестабильно: проиграли Spirit и Falcons
- Spirit были лучшей командой по форме но проиграли на стадии плей-офф
- NiKo (Falcons) вышел на пик формы именно в плей-офф
- ZywOo (Vitality) всё ещё лучший AWP мира

VALVE RANKING (10 июня 2026):
#1 Vitality (2000pts): apEX, ropz, ZywOo, flameZ, mezii
#2 Spirit (1998pts): sh1ro, magixx, tN1R, zont1x, donk
"""


async def claude_analyze(
    team1: str, team2: str, event: str,
    t1_stats: dict, t2_stats: dict,
    h2h: dict, maps_format: str,
    api_key: str,
    team1_roster: list[str] | None = None,
    team2_roster: list[str] | None = None,
) -> dict:

    # H2H строка
    h2h_str = "нет данных"
    if h2h and h2h.get("total", 0) > 0:
        lm = h2h.get("last_matches", [])
        lm_str = ", ".join(f"{x['date']} {x['format']}→{x['winner']}" for x in lm)
        h2h_str = f"{team1}: {h2h['team1_wins']}п, {team2}: {h2h['team2_wins']}п. Последние: {lm_str}"

    # Статистика
    def fmt(t):
        parts = []
        if t.get("winrate") is not None: parts.append(f"winrate={t['winrate']:.0f}%")
        if t.get("winrate_last5") is not None: parts.append(f"last5={t['winrate_last5']:.0f}%")
        if t.get("form"): parts.append(f"form={t['form']}")
        if t.get("avg_round_diff") is not None:
            s = "+" if t["avg_round_diff"] > 0 else ""
            parts.append(f"round_diff={s}{t['avg_round_diff']:.1f}")
        return ", ".join(parts) or "нет данных"

    # Реальные составы из PandaScore
    def roster_block(name, roster):
        if not roster: return f"{name}: состав неизвестен"
        return f"{name} (РЕАЛЬНЫЙ СОСТАВ из PandaScore): {', '.join(roster)}"

    r1_block = roster_block(team1, team1_roster)
    r2_block = roster_block(team2, team2_roster)

    prompt = f"""Ты эксперт-аналитик по CS2. Анализируй матч и дай ЧЁТКИЙ, РЕШИТЕЛЬНЫЙ прогноз.

{COLOGNE_CONTEXT}

━━━ МАТЧ ━━━
{team1} vs {team2}
Турнир: {event} | Формат: {maps_format}

━━━ РЕАЛЬНЫЕ СОСТАВЫ (данные PandaScore, реальное время) ━━━
{r1_block}
{r2_block}

━━━ СТАТИСТИКА ━━━
{team1}: {fmt(t1_stats)}
{team2}: {fmt(t2_stats)}
H2H: {h2h_str}

━━━ ПУЛ КАРТ CS2 (2026) ━━━
Актуальные: {MAP_POOL}
НЕ упоминать: {BANNED_MAPS}

━━━ ЗАДАЧА ━━━
1. Составы уже даны выше — НЕ придумывай игроков, используй ТОЛЬКО тех что в списке
2. Для каждого игрока дай реальный HLTV Rating 2.0 (~1.0 средний, ~1.2 хороший, ~1.35+ топ)
3. Учти контекст Кёльна — кто в форме прямо сейчас
4. Будь РЕШИТЕЛЬНЫМ: не пиши 50/50 если есть реальное преимущество
5. Upsets случаются (9z vs Vitality!) — учитывай психологию и текущую форму
6. В BO3 побеждает команда с лучшим map pool — укажи конкретные карты
7. Все текстовые поля СТРОГО НА РУССКОМ

Ответь ТОЛЬКО валидным JSON без markdown:
{{"team1_win_pct": <целое 28-76>, "team2_win_pct": <целое 28-76, сумма=100>, "verdict": "<КОНКРЕТНО кто фаворит — упомяни ключевого игрока>", "team1_players": [{{"name": "<ник из списка выше>", "role": "<роль>", "rating": <0.85-1.5>, "form": "<горячая/хорошая/средняя/слабая>", "note": "<1 конкретный факт>"}}], "team2_players": [{{"name": "<ник из списка выше>", "role": "<роль>", "rating": <0.85-1.5>, "form": "<горячая/хорошая/средняя/слабая>", "note": "<1 конкретный факт>"}}], "key_maps": "{team1} силён: [карты]; {team2} силён: [карты]; ключевая карта серии: [карта]", "key_factors": ["<фактор с именем игрока>", "<фактор2>", "<фактор3>"], "summary": "<2 предложения — почему именно эта команда победит>"}}"""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.15,
                    "max_tokens": 1800,
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

                # Фильтруем выдуманных игроков
                valid_names_1 = {n.lower() for n in (team1_roster or [])}
                valid_names_2 = {n.lower() for n in (team2_roster or [])}
                bad = {"неизвестен","unknown","tbd","?","игрок","player","n/a","name"}

                if valid_names_1:
                    result["team1_players"] = [
                        p for p in result.get("team1_players", [])
                        if p.get("name","").lower().strip() not in bad
                        and (not valid_names_1 or p.get("name","").lower() in valid_names_1
                             or any(v in p.get("name","").lower() for v in valid_names_1))
                    ]
                if valid_names_2:
                    result["team2_players"] = [
                        p for p in result.get("team2_players", [])
                        if p.get("name","").lower().strip() not in bad
                        and (not valid_names_2 or p.get("name","").lower() in valid_names_2
                             or any(v in p.get("name","").lower() for v in valid_names_2))
                    ]

                # Фикс суммы
                p1 = int(result.get("team1_win_pct", 50))
                result["team2_win_pct"] = 100 - p1
                return result

    except json.JSONDecodeError as e:
        logger.error(f"Groq JSON ошибка: {e}"); return None
    except Exception as e:
        logger.error(f"Groq ошибка: {e}"); return None
