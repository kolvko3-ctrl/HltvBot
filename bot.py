import asyncio
import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from hltv_parser import HLTVParser
from analyzer import MatchAnalyzer

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PANDASCORE_TOKEN = os.getenv("PANDASCORE_TOKEN", "")

# Создаём объекты только внутри функций — не на уровне модуля!
def make_services():
    parser = HLTVParser(token=PANDASCORE_TOKEN)
    analyzer = MatchAnalyzer(parser=parser)
    return parser, analyzer


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *HLTV Match Predictor Bot*\n\n"
        "Использую официальный PandaScore API для реальной статистики CS2.\n\n"
        "📋 *Команды:*\n"
        "/today — матчи на сегодня/завтра\n"
        "/top — топ команды\n"
        "/help — помощь",
        parse_mode="Markdown"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Как пользоваться:*\n\n"
        "1. /today — список матчей\n"
        "2. Нажми на матч — получишь анализ\n\n"
        "📊 *Анализ основан на:*\n"
        "• Winrate последних 10 матчей — 60%\n"
        "• Форма (посл. 5 результатов) — 40%\n\n"
        "Данные берутся из PandaScore API в реальном времени.\n\n"
        "⚠️ Только для развлечения, не для ставок.",
        parse_mode="Markdown"
    )


async def today_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not PANDASCORE_TOKEN:
        await update.message.reply_text(
            "❌ *Не задан PANDASCORE\\_TOKEN*\n\n"
            "Добавь его в Railway → Variables:\n"
            "`PANDASCORE_TOKEN = твой_токен`\n\n"
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
                "Сегодня и завтра нет запланированных матчей CS2,\n"
                "либо они ещё не добавлены в расписание.\n\n"
                "Попробуй завтра или загляни на hltv.org",
                parse_mode="Markdown"
            )
            return

        context.user_data["matches"] = matches

        keyboard = []
        for i, m in enumerate(matches):
            stars = "⭐" * min(m.get("stars", 0), 3)
            time_str = m.get("time", "?")
            t1 = m["team1"][:14]
            t2 = m["team2"][:14]
            maps = m.get("maps", "")
            live_icon = "🔴 " if m.get("live") else ""
            label = f"{live_icon}{stars} {time_str} {maps} | {t1} vs {t2}"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"match_{i}")])

        await msg.edit_text(
            f"📅 *Матчи CS2* — {len(matches)} шт. (МСК)\n\n👇 Нажми для анализа:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"today_matches: {e}", exc_info=True)
        await msg.edit_text("❌ Ошибка загрузки. Проверь PANDASCORE_TOKEN в переменных Railway.")


async def analyze_match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    idx = int(query.data.split("_")[1])
    matches = context.user_data.get("matches", [])
    if idx >= len(matches):
        await query.edit_message_text("❌ Матч не найден. Обнови: /today")
        return

    match = matches[idx]
    t1, t2 = match["team1"], match["team2"]
    await query.edit_message_text(
        f"🔍 Анализирую *{t1}* vs *{t2}*...\n_(загружаю историю матчей)_",
        parse_mode="Markdown"
    )

    try:
        _, analyzer = make_services()
        analysis = await analyzer.analyze(match)
        text = _format(analysis)
        kb = [[InlineKeyboardButton("◀️ Назад", callback_data="back")]]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        logger.error(f"analyze_match: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка загрузки статистики. Попробуй позже.")


def _bar(p: float) -> str:
    f = round(p / 10)
    return "█" * f + "░" * (10 - f)


def _val(v, suffix="", dec=1):
    if v is None:
        return "нет данных"
    return f"{v:.{dec}f}{suffix}"


def _format(a: dict) -> str:
    t1, t2 = a["team1"], a["team2"]
    p1 = a["prediction"]["team1_win_chance"]
    p2 = a["prediction"]["team2_win_chance"]
    winner = t1["name"] if p1 >= p2 else t2["name"]
    conf = max(p1, p2)
    label = ("🔥 Явный фаворит" if conf >= 70
             else "✅ Небольшое преимущество" if conf >= 60
             else "⚖️ Примерно равные шансы")

    maps = a.get("maps", "")
    maps_str = f" • {maps}" if maps else ""

    text = (
        f"🎮 *{t1['name']}* vs *{t2['name']}*\n"
        f"🏆 _{a.get('event','CS2')}{maps_str}_\n"
        f"{'—'*26}\n\n"
        f"📊 *Шансы на победу:*\n"
        f"`{_bar(p1)}` *{t1['name']}*: *{p1:.0f}%*\n"
        f"`{_bar(p2)}` *{t2['name']}*: *{p2:.0f}%*\n\n"
        f"🎯 *{winner}* — {label}\n\n"
        f"{'—'*26}\n"
        f"📈 *Статистика (PandaScore API):*\n\n"
        f"*{t1['name']}*\n"
        f"  🏅 Winrate: {_val(t1.get('winrate'),'%')}\n"
        f"  🔥 Форма: `{t1.get('form') or '?????'}`\n\n"
        f"*{t2['name']}*\n"
        f"  🏅 Winrate: {_val(t2.get('winrate'),'%')}\n"
        f"  🔥 Форма: `{t2.get('form') or '?????'}`\n"
    )

    factors = a["prediction"].get("key_factors", [])
    if factors:
        text += f"\n{'—'*26}\n🔑 *Ключевые факторы:*\n"
        for f in factors:
            text += f"  {f}\n"

    text += "\n⚠️ _Только для развлечения._"
    return text


async def back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    matches = context.user_data.get("matches", [])
    if not matches:
        await query.edit_message_text("Введи /today")
        return

    keyboard = []
    for i, m in enumerate(matches):
        stars = "⭐" * min(m.get("stars", 0), 3)
        time_str = m.get("time", "?")
        t1, t2 = m["team1"][:14], m["team2"][:14]
        maps = m.get("maps", "")
        live_icon = "🔴 " if m.get("live") else ""
        keyboard.append([InlineKeyboardButton(
            f"{live_icon}{stars} {time_str} {maps} | {t1} vs {t2}",
            callback_data=f"match_{i}"
        )])

    await query.edit_message_text(
        f"📅 *Матчи CS2* — {len(matches)} шт.\n\n👇 Нажми для анализа:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def top_teams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not PANDASCORE_TOKEN:
        await update.message.reply_text("❌ Не задан PANDASCORE_TOKEN в переменных Railway.")
        return
    msg = await update.message.reply_text("⏳ Загружаю команды...")
    try:
        parser, _ = make_services()
        teams = await parser.get_top_teams(10)
        if not teams:
            await msg.edit_text("❌ Не удалось загрузить. Попробуй позже.")
            return
        medals = ["🥇","🥈","🥉"] + ["🔹"]*7
        text = "🏆 *CS2 команды (PandaScore):*\n\n"
        for i, t in enumerate(teams):
            text += f"{medals[i]} {t['name']}\n"
        await msg.edit_text(text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"top_teams: {e}")
        await msg.edit_text("❌ Ошибка загрузки.")


def main():
    if not BOT_TOKEN:
        print("❌ Укажи BOT_TOKEN в переменных Railway!")
        return
    if not PANDASCORE_TOKEN:
        print("⚠️ PANDASCORE_TOKEN не задан — бот запустится, но данные не загрузит")

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
