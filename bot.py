import asyncio
import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from hltv_parser import HLTVParser
from analyzer import MatchAnalyzer
from claude_analyst import claude_analyze
from subscription import check_subscription, activate_code, get_stats, is_admin

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN        = os.getenv("BOT_TOKEN", "")
PANDASCORE_TOKEN = os.getenv("PANDASCORE_TOKEN", "")
GROQ_API_KEY     = os.getenv("GROQ_API_KEY", "")


def make_services():
    p = HLTVParser(token=PANDASCORE_TOKEN)
    return p, MatchAnalyzer(parser=p)


# ── ПРОВЕРКА ПОДПИСКИ ────────────────────────────────────────────────
def require_sub(func):
    """Декоратор — проверяет подписку перед выполнением команды."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        sub = check_subscription(user_id)
        if not sub["active"]:
            await update.effective_message.reply_text(
                "🔒 *Доступ закрыт*\n\n"
                "Для использования бота нужна подписка.\n\n"
                "Введи код доступа командой:\n"
                "`/activate КОД`\n\n"
                "Для получения кода свяжись с администратором.",
                parse_mode="Markdown"
            )
            return
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper


# ── СТАРТ ────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    sub = check_subscription(user_id)

    if sub["active"]:
        days_str = f"до {sub['expires']}" if sub['expires'] != "∞" else "безлимитная"
        await update.message.reply_text(
            "👋 *HLTV Match Predictor Bot*\n\n"
            f"✅ Подписка активна ({days_str})\n\n"
            "📋 *Команды:*\n"
            "/today — матчи на сегодня\n"
            "/top — топ команды\n"
            "/sub — статус подписки\n"
            "/help — как работает анализ",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "👋 *HLTV Match Predictor Bot*\n\n"
            "🔒 Для использования бота нужна подписка.\n\n"
            "Введи код доступа:\n"
            "`/activate КОД`\n\n"
            "Для получения кода свяжись с администратором.",
            parse_mode="Markdown"
        )


# ── АКТИВАЦИЯ КОДА ───────────────────────────────────────────────────
async def activate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "Введи код так:\n`/activate КОД`",
            parse_mode="Markdown"
        )
        return

    code = args[0].strip().upper()
    user_id = update.effective_user.id
    result = activate_code(user_id, code)

    if result["success"]:
        await update.message.reply_text(
            f"{result['message']}\n\n"
            f"📅 Подписка активна до: *{result['expires']}*\n\n"
            "Теперь тебе доступны все функции бота!\n"
            "/today — матчи на сегодня",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(result["message"])


# ── СТАТУС ПОДПИСКИ ──────────────────────────────────────────────────
async def sub_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    sub = check_subscription(user_id)

    if sub["active"]:
        days = sub["days_left"]
        expires = sub["expires"]
        if expires == "∞":
            text = "✅ *Подписка активна*\n\n🔑 Статус: Администратор (безлимитно)"
        else:
            text = (
                f"✅ *Подписка активна*\n\n"
                f"📅 Действует до: *{expires}*\n"
                f"⏳ Осталось дней: *{days}*"
            )
    else:
        text = (
            "❌ *Подписка не активна*\n\n"
            "Введи код доступа:\n`/activate КОД`"
        )
    await update.message.reply_text(text, parse_mode="Markdown")


# ── ПОМОЩЬ ───────────────────────────────────────────────────────────
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Как работает анализ:*\n\n"
        "📊 Winrate 20 матчей — 20%\n"
        "📈 Winrate посл. 5 — 20%\n"
        "🔥 Форма взвешенная — 15%\n"
        "🎯 Avg разница раундов — 20%\n"
        "⚡ Avg K/D состава (AI) — 15%\n"
        "🤝 H2H встречи — 10%\n\n"
        "Данные: PandaScore API + Groq AI\n\n"
        "⚠️ Только для развлечения.",
        parse_mode="Markdown"
    )


# ── МАТЧИ ────────────────────────────────────────────────────────────
@require_sub
async def today_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.effective_message.reply_text("⏳ Загружаю матчи CS2...")
    try:
        parser, _ = make_services()
        matches = await parser.get_today_matches()
        if not matches:
            await msg.edit_text("😔 *Матчей не найдено*\n\nНет матчей на ближайшие 3 дня.", parse_mode="Markdown")
            return
        context.user_data["matches"] = matches
        kb = _matches_kb(matches)
        kb.append([InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")])
        await msg.edit_text(
            f"📅 *Матчи CS2* — {len(matches)} шт. (МСК)\n\n👇 Нажми для анализа:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb)
        )
    except Exception as e:
        logger.error(f"today_matches: {e}", exc_info=True)
        await msg.edit_text("❌ Ошибка загрузки матчей.")


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


# ── АНАЛИЗ ───────────────────────────────────────────────────────────
async def analyze_match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    sub = check_subscription(user_id)
    if not sub["active"]:
        await query.edit_message_text(
            "🔒 Подписка истекла.\nВведи новый код: `/activate КОД`",
            parse_mode="Markdown"
        )
        return

    idx = int(query.data.split("_")[1])
    matches = context.user_data.get("matches", [])
    if idx >= len(matches):
        await query.edit_message_text("❌ Матч не найден. Обнови: /today"); return

    match = matches[idx]
    t1n, t2n = match["team1"], match["team2"]
    ai_note = " + AI анализ" if GROQ_API_KEY else ""
    await query.edit_message_text(
        f"🔍 Анализирую *{t1n}* vs *{t2n}*...{ai_note}",
        parse_mode="Markdown"
    )

    try:
        parser, analyzer = make_services()
        # Шаг 1: статистика, H2H, реальные составы — параллельно
        t1_stats, t2_stats, h2h, rosters = await asyncio.gather(
            parser.get_team_stats(match.get("team1_id"), t1n),
            parser.get_team_stats(match.get("team2_id"), t2n),
            parser.get_h2h(match.get("team1_id"), match.get("team2_id"), t1n, t2n),
            parser.get_both_rosters(match.get("team1_id"), match.get("team2_id"), match.get("match_id")),
        )
        t1_roster, t2_roster = rosters

        # Шаг 2: модель считает финальные проценты
        base_pred = analyzer._calc_from_stats(t1_stats, t2_stats, h2h)
        p1 = base_pred["team1_win_chance"]
        p2 = base_pred["team2_win_chance"]

        # Шаг 3: Groq получает готовые p1/p2 и пишет только текст
        ai_result = None
        if GROQ_API_KEY:
            ai_result = await claude_analyze(
                t1n, t2n, match.get("event", "CS2"),
                t1_stats, t2_stats, h2h,
                match.get("maps", "BO?"), GROQ_API_KEY,
                p1=p1, p2=p2,
                team1_roster=t1_roster,
                team2_roster=t2_roster,
            )

        pages = _build_pages(t1n, t2n, match, t1_stats, t2_stats, h2h, p1, p2, base_pred, ai_result)
        context.user_data[f"pages_{idx}"] = pages
        context.user_data[f"page_{idx}"] = 0
        await query.edit_message_text(pages[0], parse_mode="Markdown", reply_markup=_page_kb(0, len(pages), idx))
    except Exception as e:
        logger.error(f"analyze_match: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка анализа. Попробуй позже.")


# ── НАВИГАЦИЯ ────────────────────────────────────────────────────────
async def page_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    match_idx, page_idx = int(parts[1]), int(parts[2])
    pages = context.user_data.get(f"pages_{match_idx}", [])
    if not pages:
        await query.edit_message_text("Нажми /today"); return
    page_idx = max(0, min(page_idx, len(pages) - 1))
    await query.edit_message_text(pages[page_idx], parse_mode="Markdown", reply_markup=_page_kb(page_idx, len(pages), match_idx))


def _page_kb(page, total, match_idx):
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"page_{match_idx}_{page-1}"))
    if page < total - 1:
        row.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"page_{match_idx}_{page+1}"))
    nav = [InlineKeyboardButton("◀️ К матчам", callback_data="back"),
           InlineKeyboardButton("🏠 Меню", callback_data="main_menu")]
    return InlineKeyboardMarkup([row, nav] if row else [nav])


# ── ФОРМАТИРОВАНИЕ ───────────────────────────────────────────────────
def _bar(p):
    f = round(p / 10)
    return "█" * f + "░" * (10 - f)

def _v(val, suffix="", dec=1):
    if val is None: return "—"
    return f"{val:.{dec}f}{suffix}"

def _rd(val):
    if val is None: return "—"
    return f"{'+'if val>0 else ''}{val:.1f}"


def _build_pages(t1n, t2n, match, t1s, t2s, h2h, p1, p2, base_pred, ai) -> list[str]:
    winner = t1n if p1 >= p2 else t2n
    conf = max(p1, p2)
    verdict = (ai.get("verdict") if ai else None) or (
        "🔥 Явный фаворит" if conf >= 72 else
        "✅ Небольшое преимущество" if conf >= 62 else
        "📊 Лёгкое преимущество" if conf >= 55 else
        "⚖️ Равные шансы"
    )
    maps_str = f" • {match['maps']}" if match.get("maps") else ""
    h2h_total = (h2h or {}).get("total", 0)

    pg1 = (
        f"🎮 *{t1n}* vs *{t2n}*\n"
        f"🏆 _{match.get('event','CS2')}{maps_str}_\n"
        f"{'—'*28}\n\n"
        f"📊 *Шансы на победу:*\n"
        f"`{_bar(p1)}` *{t1n}*  {p1:.0f}%\n"
        f"`{_bar(p2)}` *{t2n}*  {p2:.0f}%\n\n"
        f"🎯 *{winner}* — {verdict}\n\n"
        f"{'—'*28}\n"
        f"📈 *Статистика команд:*\n\n"
        f"*{t1n}*\n"
        f"  🏅 Winrate (20м): {_v(t1s.get('winrate'), '%')}\n"
        f"  📈 Winrate (посл.5): {_v(t1s.get('winrate_last5'), '%')}\n"
        f"  🔥 Форма: `{t1s.get('form') or '?????'}`\n"
        f"  🎯 Avg ±раундов: {_rd(t1s.get('avg_round_diff'))}\n\n"
        f"*{t2n}*\n"
        f"  🏅 Winrate (20м): {_v(t2s.get('winrate'), '%')}\n"
        f"  📈 Winrate (посл.5): {_v(t2s.get('winrate_last5'), '%')}\n"
        f"  🔥 Форма: `{t2s.get('form') or '?????'}`\n"
        f"  🎯 Avg ±раундов: {_rd(t2s.get('avg_round_diff'))}\n"
    )
    if h2h_total >= 1:
        pg1 += f"\n{'—'*28}\n🤝 *H2H ({h2h_total} встреч):*\n"
        pg1 += f"  {t1n}: {h2h.get('team1_wins',0)} побед\n"
        pg1 += f"  {t2n}: {h2h.get('team2_wins',0)} побед\n"
        for lm in h2h.get("last_matches", []):
            pg1 += f"  › {lm['date']} {lm['format']} → 🏆 {lm['winner']}\n"
    else:
        pg1 += f"\n🤝 *H2H:* нет данных\n"

    factors = (ai.get("key_factors") if ai else None) or base_pred.get("key_factors", [])
    if factors:
        pg1 += f"\n{'—'*28}\n🔑 *Ключевые факторы:*\n"
        for f in factors[:4]:
            pg1 += f"  • {f}\n"
    if ai and ai.get("summary"):
        pg1 += f"\n💬 _{ai['summary']}_\n"
    pg1 += f"\n_Стр. 1/3 — следующая: состав {t1n}_"

    key_maps = ai.get("key_maps") if ai else None
    pg2 = _players_page(t1n, t2n, ai.get("team1_players") if ai else None, key_maps)
    pg3 = _players_page(t2n, t1n, ai.get("team2_players") if ai else None, key_maps)
    return [pg1, pg2, pg3]


def _players_page(team_name, opp_name, players, key_maps) -> str:
    text = f"👥 *Состав {team_name}*\n{'—'*28}\n\n"
    if not players:
        text += "_Нет данных о составе._\n"
    else:
        form_icons = {"горячая": "🔥", "хорошая": "✅", "средняя": "😐", "слабая": "❄️"}
        sorted_p = sorted(players, key=lambda p: p.get("rating") or 0, reverse=True)
        for i, p in enumerate(sorted_p):
            icon = form_icons.get((p.get("form") or "средняя").lower(), "❓")
            star = "⭐ " if i == 0 else "👤 "
            text += f"{star}*{p.get('name','?')}* {icon}"
            if p.get("role"): text += f" _({p['role']})_"
            text += "\n"
            if p.get("rating"): text += f"  📊 HLTV рейтинг: ~{p['rating']:.2f}\n"
            if p.get("note"):   text += f"  💬 {p['note']}\n"
            text += "\n"
    if key_maps:
        text += f"{'—'*28}\n🗺 *Карты:*\n"
        for part in key_maps.replace(";", "\n").split("\n"):
            part = part.strip()
            if not part: continue
            if team_name.lower() in part.lower():
                text += f"  🟢 {part}\n"
            elif opp_name.lower() in part.lower():
                text += f"  🔴 {part}\n"
            else:
                text += f"  • {part}\n"
        text += "\n"
    text += "⚠️ _Только для развлечения._"
    return text


# ── МЕНЮ ─────────────────────────────────────────────────────────────
async def back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    matches = context.user_data.get("matches", [])
    if not matches:
        await query.edit_message_text("Введи /today"); return
    kb = _matches_kb(matches)
    kb.append([InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")])
    await query.edit_message_text(
        f"📅 *Матчи CS2* — {len(matches)} шт.\n\n👇 Нажми для анализа:",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
    )


async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🏠 *Главное меню*\n\n"
        "📋 *Команды:*\n"
        "/today — матчи\n/top — топ команды\n/sub — подписка",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📅 Матчи", callback_data="goto_today"),
            InlineKeyboardButton("🏆 Топ команды", callback_data="goto_top"),
        ]])
    )


async def goto_today_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    matches = context.user_data.get("matches", [])
    if matches:
        kb = _matches_kb(matches)
        kb.append([InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")])
        await query.edit_message_text(
            f"📅 *Матчи CS2* — {len(matches)} шт.\n\n👇 Нажми для анализа:",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
        )
    else:
        await query.edit_message_text("Введи /today для загрузки матчей")


# ── ТОП КОМАНДЫ ──────────────────────────────────────────────────────
@require_sub
async def top_teams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    is_cb = update.callback_query is not None
    if is_cb:
        await update.callback_query.answer()
        edit = update.callback_query.edit_message_text
    else:
        msg = await update.effective_message.reply_text("⏳ Загружаю...")
        edit = msg.edit_text

    try:
        parser, _ = make_services()
        teams = await parser.get_top_teams(20)
        if not teams:
            await edit("❌ Не удалось загрузить."); return
        medals = ["🥇","🥈","🥉"] + [f"`#{i+4}`" for i in range(17)]
        text = "🏆 *Топ CS2 команды (HLTV):*\n\n"
        for i, t in enumerate(teams):
            flag = t.get("flag", "🔹")
            medal = ["🥇","🥈","🥉"][i] if i < 3 else f"`#{t['rank']}`"
            text += f"{medal} {flag} *{t['name']}*\n"
        text += "\n_Рейтинг HLTV.org • обновлён 12 июня 2026_"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📅 Матчи", callback_data="goto_today"),
            InlineKeyboardButton("🏠 Меню", callback_data="main_menu"),
        ]])
        await edit(text, parse_mode="Markdown", reply_markup=kb)
    except Exception as e:
        logger.error(f"top_teams: {e}", exc_info=True)
        await edit("❌ Ошибка.")


# ── ADMIN ─────────────────────────────────────────────────────────────
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Нет доступа.")
        return
    stats = get_stats()
    text = (
        "📊 *Статистика бота:*\n\n"
        f"👥 Всего пользователей: {stats['total_users']}\n"
        f"✅ Активных подписок: {stats['active_users']}\n\n"
        "🔑 *Использование кодов:*\n"
    )
    for code, count in stats.get("codes_used", {}).items():
        text += f"  `{code}`: {count} активаций\n"
    await update.message.reply_text(text, parse_mode="Markdown")


# ── CHECKENV ─────────────────────────────────────────────────────────
async def checkenv(update, context):
    def chk(val, name):
        if val: return f"✅ `{name}` = `{val[:8]}...{val[-4:]}`"
        return f"❌ `{name}` — НЕ ЗАДАН"
    text = "🔧 *Переменные:*\n\n"
    text += chk(BOT_TOKEN, "BOT_TOKEN") + "\n"
    text += chk(PANDASCORE_TOKEN, "PANDASCORE_TOKEN") + "\n"
    text += chk(GROQ_API_KEY, "GROQ_API_KEY") + "\n"
    sub_codes = os.getenv("SUBSCRIPTION_CODES", "")
    text += f"\n🔑 `SUBSCRIPTION_CODES`: `{sub_codes[:40] if sub_codes else 'не задан'}`"
    admin_ids = os.getenv("ADMIN_IDS", "")
    text += f"\n👑 `ADMIN_IDS`: `{admin_ids or 'не задан'}`"
    await update.message.reply_text(text, parse_mode="Markdown")


# ── MAIN ─────────────────────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        print("❌ Укажи BOT_TOKEN!"); return

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("activate", activate))
    app.add_handler(CommandHandler("sub", sub_status))
    app.add_handler(CommandHandler("today", today_matches))
    app.add_handler(CommandHandler("top", top_teams))
    app.add_handler(CommandHandler("admin", admin_stats))
    app.add_handler(CommandHandler("checkenv", checkenv))
    app.add_handler(CallbackQueryHandler(analyze_match, pattern=r"^match_\d+$"))
    app.add_handler(CallbackQueryHandler(page_nav, pattern=r"^page_\d+_\d+$"))
    app.add_handler(CallbackQueryHandler(back_handler, pattern="^back$"))
    app.add_handler(CallbackQueryHandler(main_menu_handler, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(goto_today_handler, pattern="^goto_today$"))
    app.add_handler(CallbackQueryHandler(top_teams, pattern="^goto_top$"))
    print("🤖 Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
