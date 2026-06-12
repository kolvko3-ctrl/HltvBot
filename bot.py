import asyncio
import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from hltv_parser import HLTVParser
from analyzer import MatchAnalyzer

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PANDASCORE_TOKEN = os.getenv("PANDASCORE_TOKEN", "")


def make_services():
    p = HLTVParser(token=PANDASCORE_TOKEN)
    return p, MatchAnalyzer(parser=p)


# ── КОМАНДЫ ─────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *HLTV Match Predictor Bot*\n\n"
        "Анализирую CS2 матчи по реальным данным PandaScore:\n"
        "• Статистика каждого игрока (K/D, HS%)\n"
        "• Форма команды за 20 матчей\n"
        "• Разница раундов, H2H встречи\n\n"
        "📋 *Команды:*\n"
        "/today — матчи на сегодня/завтра\n"
        "/top — топ команды\n"
        "/help — как работает анализ",
        parse_mode="Markdown"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Что анализируется:*\n\n"
        "📊 Общий винрейт команды — 20%\n"
        "📈 Винрейт последних 5 матчей — 20%\n"
        "🔥 Форма (последние 5 результатов) — 15%\n"
        "🎯 Средняя разница раундов в картах — 20%\n"
        "⚡ Avg K/D всего состава — 15%\n"
        "🤝 H2H личные встречи — 10%\n\n"
        "Для каждого игрока показываю:\n"
        "K/D, HS%, раундов сыграно, форму\n\n"
        "⚠️ Только для развлечения, не для ставок.",
        parse_mode="Markdown"
    )


async def today_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not PANDASCORE_TOKEN:
        await update.message.reply_text(
            "❌ Не задан `PANDASCORE_TOKEN` в Railway Variables.\n"
            "Получить бесплатно: https://app.pandascore.co/signup",
            parse_mode="Markdown"
        )
        return
    msg = await update.message.reply_text("⏳ Загружаю матчи CS2...")
    try:
        parser, _ = make_services()
        matches = await parser.get_today_matches()
        if not matches:
            await msg.edit_text(
                "😔 *Матчей не найдено*\n\nНа сегодня/завтра матчей CS2 нет.\nПопробуй завтра.",
                parse_mode="Markdown"
            )
            return
        context.user_data["matches"] = matches
        await msg.edit_text(
            f"📅 *Матчи CS2* — {len(matches)} шт. (МСК)\n\n👇 Нажми для анализа:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(_matches_kb(matches))
        )
    except Exception as e:
        logger.error(f"today_matches: {e}", exc_info=True)
        await msg.edit_text("❌ Ошибка загрузки матчей. Попробуй через минуту.")


def _matches_kb(matches):
    kb = []
    for i, m in enumerate(matches):
        stars = "⭐" * min(m.get("stars", 0), 3)
        live = "🔴 " if m.get("live") else ""
        t1, t2 = m["team1"][:13], m["team2"][:13]
        maps = m.get("maps", "")
        label = f"{live}{stars} {m.get('time','?')} {maps} | {t1} vs {t2}"
        kb.append([InlineKeyboardButton(label, callback_data=f"match_{i}")])
    return kb


async def analyze_match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    matches = context.user_data.get("matches", [])
    if idx >= len(matches):
        await query.edit_message_text("❌ Матч не найден. Обнови: /today"); return

    match = matches[idx]
    await query.edit_message_text(
        f"🔍 Загружаю глубокую статистику...\n"
        f"*{match['team1']}* vs *{match['team2']}*\n\n"
        f"_(составы, K/D игроков, разница раундов, H2H)_",
        parse_mode="Markdown"
    )
    try:
        _, analyzer = make_services()
        analysis = await analyzer.analyze(match)
        pages = _build_pages(analysis)
        context.user_data[f"pages_{idx}"] = pages
        context.user_data[f"page_{idx}"] = 0
        await query.edit_message_text(
            pages[0],
            parse_mode="Markdown",
            reply_markup=_page_kb(0, len(pages), idx)
        )
    except Exception as e:
        logger.error(f"analyze_match: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка загрузки статистики. Попробуй позже.")


async def page_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")  # page_MATCHIDX_PAGEIDX
    match_idx, page_idx = int(parts[1]), int(parts[2])
    pages = context.user_data.get(f"pages_{match_idx}", [])
    if not pages:
        await query.edit_message_text("Нажми /today для обновления"); return
    page_idx = max(0, min(page_idx, len(pages) - 1))
    context.user_data[f"page_{match_idx}"] = page_idx
    await query.edit_message_text(
        pages[page_idx],
        parse_mode="Markdown",
        reply_markup=_page_kb(page_idx, len(pages), match_idx)
    )


def _page_kb(page: int, total: int, match_idx: int) -> InlineKeyboardMarkup:
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"page_{match_idx}_{page-1}"))
    if page < total - 1:
        row.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"page_{match_idx}_{page+1}"))
    back_row = [InlineKeyboardButton("◀️ К матчам", callback_data="back")]
    return InlineKeyboardMarkup([row, back_row] if row else [back_row])


# ── ФОРМАТИРОВАНИЕ ──────────────────────────────────────────────────
def _bar(p: float) -> str:
    f = round(p / 10)
    return "█" * f + "░" * (10 - f)


def _v(val, suffix="", dec=1):
    if val is None: return "—"
    return f"{val:.{dec}f}{suffix}"


def _rd(val):
    if val is None: return "—"
    return f"{'+'if val>0 else ''}{val:.1f}"


def _streak(s):
    if not s: return "—"
    icon = "🔥" if s[0] == "W" else "❄️"
    return f"{icon} {s[0]}×{s[1:]}"


def _build_pages(a: dict) -> list[str]:
    """Строит список страниц сообщения."""
    t1, t2 = a["team1"], a["team2"]
    p = a["prediction"]
    p1, p2 = p["team1_win_chance"], p["team2_win_chance"]
    winner = t1["name"] if p1 >= p2 else t2["name"]
    conf = max(p1, p2)
    verdict = (
        "🔥 Явный фаворит" if conf >= 72 else
        "✅ Небольшое преимущество" if conf >= 62 else
        "📊 Лёгкое преимущество" if conf >= 55 else
        "⚖️ Равные шансы"
    )
    maps_str = f" • {a['maps']}" if a.get("maps") else ""
    h2h = a.get("h2h") or {}

    # ── СТРАНИЦА 1: Прогноз + команды ──────────────────────────────
    pg1 = (
        f"🎮 *{t1['name']}* vs *{t2['name']}*\n"
        f"🏆 _{a.get('event','CS2')}{maps_str}_\n"
        f"{'—'*28}\n\n"
        f"📊 *Шансы на победу:*\n"
        f"`{_bar(p1)}` *{t1['name']}*  {p1:.0f}%\n"
        f"`{_bar(p2)}` *{t2['name']}*  {p2:.0f}%\n\n"
        f"🎯 *{winner}* — {verdict}\n\n"
        f"{'—'*28}\n"
        f"📈 *Статистика команд:*\n\n"

        f"*{t1['name']}*\n"
        f"  🏅 Winrate (20 матчей): {_v(t1.get('winrate'), '%')}\n"
        f"  📈 Winrate (посл. 5): {_v(t1.get('winrate_last5'), '%')}\n"
        f"  🔥 Форма: `{t1.get('form') or '?????'}`\n"
        f"  ⚡ Стрик: {_streak(t1.get('streak'))}\n"
        f"  🎯 Avg ±раундов: {_rd(t1.get('avg_round_diff'))}\n"
        f"  💀 Avg K/D состава: {_v(t1.get('avg_kd'), dec=2)}\n"
        f"  🎯 Avg HS%: {_v(t1.get('avg_hs'), '%')}\n\n"

        f"*{t2['name']}*\n"
        f"  🏅 Winrate (20 матчей): {_v(t2.get('winrate'), '%')}\n"
        f"  📈 Winrate (посл. 5): {_v(t2.get('winrate_last5'), '%')}\n"
        f"  🔥 Форма: `{t2.get('form') or '?????'}`\n"
        f"  ⚡ Стрик: {_streak(t2.get('streak'))}\n"
        f"  🎯 Avg ±раундов: {_rd(t2.get('avg_round_diff'))}\n"
        f"  💀 Avg K/D состава: {_v(t2.get('avg_kd'), dec=2)}\n"
        f"  🎯 Avg HS%: {_v(t2.get('avg_hs'), '%')}\n"
    )

    # H2H
    h_total = h2h.get("total", 0)
    if h_total >= 1:
        pg1 += f"\n{'—'*28}\n🤝 *H2H ({h_total} встреч):*\n"
        pg1 += f"  {t1['name']}: {h2h.get('team1_wins',0)} побед\n"
        pg1 += f"  {t2['name']}: {h2h.get('team2_wins',0)} побед\n"
        for lm in h2h.get("last_matches", []):
            pg1 += f"  › {lm['date']} {lm['format']} → 🏆 {lm['winner']}\n"
    else:
        pg1 += f"\n🤝 *H2H:* нет данных\n"

    # Факторы
    factors = p.get("key_factors", [])
    if factors:
        pg1 += f"\n{'—'*28}\n🔑 *Ключевые факторы:*\n"
        for f in factors:
            pg1 += f"  {f}\n"

    pg1 += f"\n_Стр. 1/3 • Свайп → для состава_"

    # ── СТРАНИЦА 2: Игроки команды 1 ────────────────────────────────
    pg2 = _players_page(t1, t1["name"])

    # ── СТРАНИЦА 3: Игроки команды 2 ────────────────────────────────
    pg3 = _players_page(t2, t2["name"])

    return [pg1, pg2, pg3]


def _players_page(team: dict, team_name: str) -> str:
    players = team.get("players") or []
    star = team.get("star_player")

    text = (
        f"👥 *Состав {team_name}*\n"
        f"{'—'*28}\n\n"
    )

    if not players:
        text += "_Нет данных по игрокам_\n"
    else:
        # Сортируем по K/D
        sorted_p = sorted(players, key=lambda p: p.get("kd_ratio") or 0, reverse=True)
        for p in sorted_p:
            name = p.get("name") or "Unknown"
            kd   = p.get("kd_ratio")
            hs   = p.get("headshot_pct")
            maps = p.get("maps_played")
            kpg  = p.get("kills_per_round")   # у нас это kills per map/game
            assists = p.get("assists")

            is_star = star and star.get("id") == p.get("id") and kd is not None
            icon = "⭐ " if is_star else "👤 "

            if kd is not None:
                if kd >= 1.3:   form_icon = "🔥"
                elif kd >= 1.0: form_icon = "✅"
                elif kd >= 0.85:form_icon = "😐"
                else:           form_icon = "❄️"
            else:
                form_icon = "❓"

            text += f"{icon}*{name}* {form_icon}\n"
            # Строка 1: K/D и убийств за карту
            kd_str = _v(kd, dec=2)
            kpg_str = f"  Kills/map: {_v(kpg, dec=1)}" if kpg is not None else ""
            text += f"  K/D: {kd_str}{kpg_str}\n"
            # Строка 2: HS% и карты
            hs_str  = f"  HS%: {_v(hs, '%')}" if hs is not None else ""
            map_str = f"  Карт: {maps}" if maps is not None else ""
            ast_str = f"  Assists: {assists}" if assists else ""
            extra = (hs_str + map_str + ast_str).strip()
            if extra:
                text += f"  {extra.strip()}\n"
            text += "\n"

    # Средняя стата команды
    avg_kd = team.get("avg_kd")
    avg_hs = team.get("avg_hs")
    if avg_kd or avg_hs:
        text += f"{'—'*28}\n"
        text += f"📊 *Средняя по составу:*\n"
        if avg_kd: text += f"  K/D: {_v(avg_kd, dec=2)}\n"
        if avg_hs: text += f"  HS%: {_v(avg_hs, '%')}\n"
        if star and star.get("name"):
            text += f"  ⭐ Лучший: {star['name']} (K/D {_v(star.get('kd_ratio'), dec=2)})\n"

    text += f"\n_⚠️ Только для развлечения._"
    return text


async def back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    matches = context.user_data.get("matches", [])
    if not matches:
        await query.edit_message_text("Введи /today"); return
    await query.edit_message_text(
        f"📅 *Матчи CS2* — {len(matches)} шт.\n\n👇 Нажми для анализа:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(_matches_kb(matches))
    )


async def top_teams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not PANDASCORE_TOKEN:
        await update.message.reply_text("❌ Не задан PANDASCORE_TOKEN."); return
    msg = await update.message.reply_text("⏳ Загружаю...")
    try:
        parser, _ = make_services()
        teams = await parser.get_top_teams(10)
        if not teams:
            await msg.edit_text("❌ Не удалось загрузить. Попробуй позже."); return
        medals = ["🥇","🥈","🥉"] + ["🔹"]*7
        text = "🏆 *CS2 команды (PandaScore):*\n\n"
        for i, t in enumerate(teams):
            text += f"{medals[i]} {t['name']}\n"
        await msg.edit_text(text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"top_teams: {e}")
        await msg.edit_text("❌ Ошибка.")



async def debug_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Диагностика: проверяем все эндпоинты и структуру данных."""
    msg = await update.message.reply_text("🔍 Диагностика...")
    try:
        parser = HLTVParser(token=PANDASCORE_TOKEN)
        lines = []

        # Шаг 1: ищем команду через матчи (более надёжно)
        matches_raw = await parser._get("/csgo/matches", {
            "filter[status]": "finished", "sort": "-scheduled_at", "per_page": 1
        })
        if not matches_raw:
            lines.append("❌ /csgo/matches вернул пусто")
            await msg.edit_text("\n".join(lines)); return

        m = matches_raw[0]
        opps = m.get("opponents", [])
        if not opps:
            lines.append("❌ Нет opponents в матче")
            await msg.edit_text("\n".join(lines)); return

        team = opps[0].get("opponent", {})
        team_id = team.get("id")
        team_name = team.get("name", "?")
        lines.append(f"✅ Команда: *{team_name}* (id=`{team_id}`)")

        # Шаг 2: состав команды
        team_info = await parser._get(f"/teams/{team_id}")
        players_list = (team_info or {}).get("players", [])
        lines.append(f"👥 Игроков в составе: {len(players_list)}")
        if players_list:
            p = players_list[0]
            lines.append(f"   Первый: *{p.get('name')}* id=`{p.get('id')}`")

        # Шаг 3: игры матча
        games = m.get("games") or []
        lines.append(f"🎮 Игр в матче: {len(games)}")

        if games:
            game_id = games[0].get("id")
            lines.append(f"   Game ID: `{game_id}`")

            # Шаг 4: детали игры
            gd = await parser._get(f"/csgo/games/{game_id}")
            if gd:
                lines.append(f"\n📋 *Ключи /csgo/games/{game_id}:*")
                for k, v in sorted(gd.items()):
                    if k == "players" and isinstance(v, list):
                        lines.append(f"  `players`: {len(v)} шт.")
                        if v:
                            lines.append(f"  *Ключи игрока[0]:*")
                            for pk, pv in list(v[0].items())[:20]:
                                lines.append(f"    `{pk}`: `{str(pv)[:60]}`")
                    elif k == "teams" and isinstance(v, list):
                        lines.append(f"  `teams`: {len(v)} шт.")
                        if v:
                            lines.append(f"  *Ключи teams[0]:*")
                            for tk, tv in list(v[0].items())[:15]:
                                if tk == "players" and isinstance(tv, list):
                                    lines.append(f"    `players`: {len(tv)} шт.")
                                    if tv:
                                        lines.append(f"    *Ключи teams[0].players[0]:*")
                                        for ppk, ppv in list(tv[0].items())[:20]:
                                            lines.append(f"      `{ppk}`: `{str(ppv)[:50]}`")
                                elif tv is not None:
                                    lines.append(f"    `{tk}`: `{str(tv)[:60]}`")
                    elif v is not None and v != [] and v != {}:
                        lines.append(f"  `{k}`: `{str(v)[:80]}`")
            else:
                lines.append("❌ /csgo/games/{id} вернул пусто")

        # Шаг 5: попробуем /csgo/players/{id}/stats напрямую
        if players_list:
            pid = players_list[0].get("id")
            pname = players_list[0].get("name", "?")
            pstats = await parser._get(f"/csgo/players/{pid}/stats")
            lines.append(f"\n📊 */csgo/players/{pid}/stats ({pname}):*")
            if pstats and isinstance(pstats, dict):
                for k, v in list(pstats.items())[:25]:
                    if v is not None and v != {} and v != []:
                        lines.append(f"  `{k}`: `{str(v)[:70]}`")
            else:
                lines.append(f"  Ответ: `{str(pstats)[:200]}`")

        text = "\n".join(lines)
        if len(text) > 4000: text = text[:3900] + "\n...(обрезано)"
        await msg.edit_text(text, parse_mode="Markdown")
    except Exception as e:
        import traceback
        tb = traceback.format_exc()[-800:]
        await msg.edit_text(f"❌ Exception:\n`{tb}`", parse_mode="Markdown")

def main():
    if not BOT_TOKEN:
        print("❌ Укажи BOT_TOKEN!"); return
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("today", today_matches))
    app.add_handler(CommandHandler("top", top_teams))
    app.add_handler(CallbackQueryHandler(analyze_match, pattern=r"^match_\d+$"))
    app.add_handler(CallbackQueryHandler(page_nav, pattern=r"^page_\d+_\d+$"))
    app.add_handler(CallbackQueryHandler(back_handler, pattern="^back$"))
    app.add_handler(CommandHandler("debug", debug_api))
    print("🤖 Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
