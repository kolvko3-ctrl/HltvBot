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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *HLTV Match Predictor Bot*\n\n"
        "Анализирую CS2 матчи по реальным данным PandaScore API.\n\n"
        "📋 *Команды:*\n"
        "/today — матчи на сегодня/завтра\n"
        "/top — топ команды\n"
        "/help — как работает анализ",
        parse_mode="Markdown"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Как работает анализ:*\n\n"
        "Бот собирает 20 последних матчей каждой команды и считает:\n\n"
        "📊 *Общий winrate* — 25%\n"
        "📈 *Winrate последних 5 матчей* — 20%\n"
        "🔥 *Форма (5 посл. результатов)* — 20%\n"
        "🎯 *Средняя разница раундов* — 20%\n"
        "🤝 *H2H личные встречи* — 15%\n\n"
        "Также показывает текущий стрик побед/поражений.\n\n"
        "⚠️ Только для развлечения.",
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
                "😔 *Матчей не найдено*\n\n"
                "Сегодня и завтра нет матчей CS2 в расписании.\n"
                "Попробуй завтра или загляни на hltv.org",
                parse_mode="Markdown"
            )
            return

        context.user_data["matches"] = matches
        keyboard = _matches_keyboard(matches)
        await msg.edit_text(
            f"📅 *Матчи CS2* — {len(matches)} шт. (МСК)\n\n👇 Нажми для анализа:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"today_matches: {e}", exc_info=True)
        await msg.edit_text("❌ Ошибка загрузки матчей. Попробуй через минуту.")


def _matches_keyboard(matches):
    keyboard = []
    for i, m in enumerate(matches):
        stars = "⭐" * min(m.get("stars", 0), 3)
        live = "🔴 " if m.get("live") else ""
        t1, t2 = m["team1"][:14], m["team2"][:14]
        maps = m.get("maps", "")
        label = f"{live}{stars} {m.get('time','?')} {maps} | {t1} vs {t2}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"match_{i}")])
    return keyboard


async def analyze_match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    idx = int(query.data.split("_")[1])
    matches = context.user_data.get("matches", [])
    if idx >= len(matches):
        await query.edit_message_text("❌ Матч не найден. Обнови: /today")
        return

    match = matches[idx]
    await query.edit_message_text(
        f"🔍 Загружаю глубокую статистику...\n"
        f"*{match['team1']}* vs *{match['team2']}*\n\n"
        f"_(20 матчей + H2H + разница раундов)_",
        parse_mode="Markdown"
    )

    try:
        _, analyzer = make_services()
        analysis = await analyzer.analyze(match)
        text = _format_analysis(analysis)
        kb = [[InlineKeyboardButton("◀️ Назад к матчам", callback_data="back")]]
        await query.edit_message_text(text, parse_mode="Markdown",
                                      reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        logger.error(f"analyze_match: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка загрузки статистики. Попробуй позже.")


def _bar(p: float) -> str:
    f = round(p / 10)
    return "█" * f + "░" * (10 - f)


def _v(val, suffix="", dec=1, prefix=""):
    if val is None: return "—"
    return f"{prefix}{val:.{dec}f}{suffix}"


def _format_analysis(a: dict) -> str:
    t1, t2 = a["team1"], a["team2"]
    p = a["prediction"]
    p1, p2 = p["team1_win_chance"], p["team2_win_chance"]
    winner = t1["name"] if p1 >= p2 else t2["name"]
    conf = max(p1, p2)

    if conf >= 72: verdict = "🔥 Явный фаворит"
    elif conf >= 62: verdict = "✅ Небольшое преимущество"
    elif conf >= 55: verdict = "📊 Лёгкое преимущество"
    else: verdict = "⚖️ Примерно равные шансы"

    maps = a.get("maps", "")
    maps_str = f" • {maps}" if maps else ""

    # Стрик-строчка
    def streak_str(s):
        if not s: return "—"
        icon = "🔥" if s[0] == "W" else "❄️"
        return f"{icon} {s[0]}×{s[1:]}"

    # Разница раундов со знаком
    def rd_str(v):
        if v is None: return "—"
        sign = "+" if v > 0 else ""
        return f"{sign}{v:.1f}"

    # H2H блок
    h2h = a.get("h2h", {}) or {}
    h2h_total = h2h.get("total", 0)

    text = (
        f"🎮 *{t1['name']}* vs *{t2['name']}*\n"
        f"🏆 _{a.get('event','CS2')}{maps_str}_\n"
        f"{'—'*28}\n\n"

        f"📊 *Шансы на победу:*\n"
        f"`{_bar(p1)}` *{t1['name']}*  {p1:.0f}%\n"
        f"`{_bar(p2)}` *{t2['name']}*  {p2:.0f}%\n\n"
        f"🎯 *{winner}* — {verdict}\n\n"
        f"{'—'*28}\n"

        f"📈 *Детальная статистика:*\n\n"

        f"*{t1['name']}*\n"
        f"  🏅 Winrate (всего): {_v(t1.get('winrate'), '%')}\n"
        f"  📈 Winrate посл. 5: {_v(t1.get('winrate_last5'), '%')}\n"
        f"  🔥 Форма: `{t1.get('form') or '?????'}`\n"
        f"  ⚡ Стрик: {streak_str(t1.get('streak'))}\n"
        f"  🎯 Avg разница раундов: {rd_str(t1.get('avg_round_diff'))}\n"
        f"  🗺 Карт сыграно: {t1.get('maps_played') or '—'}\n\n"

        f"*{t2['name']}*\n"
        f"  🏅 Winrate (всего): {_v(t2.get('winrate'), '%')}\n"
        f"  📈 Winrate посл. 5: {_v(t2.get('winrate_last5'), '%')}\n"
        f"  🔥 Форма: `{t2.get('form') or '?????'}`\n"
        f"  ⚡ Стрик: {streak_str(t2.get('streak'))}\n"
        f"  🎯 Avg разница раундов: {rd_str(t2.get('avg_round_diff'))}\n"
        f"  🗺 Карт сыграно: {t2.get('maps_played') or '—'}\n"
    )

    # H2H
    if h2h_total >= 1:
        text += f"\n{'—'*28}\n🤝 *Личные встречи (H2H):*\n"
        text += f"  {t1['name']}: {h2h.get('team1_wins',0)} побед\n"
        text += f"  {t2['name']}: {h2h.get('team2_wins',0)} побед\n"
        for lm in h2h.get("last_matches", []):
            text += f"  › {lm['date']} {lm['format']} → 🏆 {lm['winner']}\n"
    else:
        text += f"\n{'—'*28}\n🤝 *H2H:* нет данных о встречах\n"

    # Ключевые факторы
    factors = p.get("key_factors", [])
    if factors:
        text += f"\n{'—'*28}\n🔑 *Ключевые факторы:*\n"
        for f in factors:
            text += f"  {f}\n"

    text += "\n⚠️ _Данные: PandaScore API. Только для развлечения._"
    return text


async def back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    matches = context.user_data.get("matches", [])
    if not matches:
        await query.edit_message_text("Введи /today"); return
    keyboard = _matches_keyboard(matches)
    await query.edit_message_text(
        f"📅 *Матчи CS2* — {len(matches)} шт.\n\n👇 Нажми для анализа:",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def top_teams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not PANDASCORE_TOKEN:
        await update.message.reply_text("❌ Не задан PANDASCORE_TOKEN.")
        return
    msg = await update.message.reply_text("⏳ Загружаю...")
    try:
        parser, _ = make_services()
        teams = await parser.get_top_teams(10)
        if not teams:
            await msg.edit_text("❌ Не удалось загрузить. Попробуй позже.")
            return
        medals = ["🥇","🥈","🥉"] + ["🔹"]*7
        text = "🏆 *CS2 команды:*\n\n"
        for i, t in enumerate(teams):
            text += f"{medals[i]} {t['name']}\n"
        await msg.edit_text(text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"top_teams: {e}")
        await msg.edit_text("❌ Ошибка.")


def main():
    if not BOT_TOKEN:
        print("❌ Укажи BOT_TOKEN!"); return

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("today", today_matches))
    app.add_handler(CommandHandler("top", top_teams))
    app.add_handler(CallbackQueryHandler(analyze_match, pattern=r"^match_\d+$"))
    app.add_handler(CallbackQueryHandler(back_handler, pattern="^back$"))

    print("🤖 Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
