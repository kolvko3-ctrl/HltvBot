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

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# НЕ создаём никаких объектов на уровне модуля — только после запуска event loop
_parser: HLTVParser | None = None
_analyzer: MatchAnalyzer | None = None


def get_parser() -> HLTVParser:
    global _parser
    if _parser is None:
        _parser = HLTVParser()
    return _parser


def get_analyzer() -> MatchAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = MatchAnalyzer()
    return _analyzer


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *HLTV Match Predictor Bot*\n\n"
        "Анализирую реальную статистику CS2 с HLTV.org.\n\n"
        "📋 *Команды:*\n"
        "/today — матчи на сегодня/завтра\n"
        "/top — топ-10 команд\n"
        "/help — помощь\n\n"
        "Выбери матч → получи анализ шансов 🎯"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 *Как пользоваться:*\n\n"
        "1. /today — список матчей\n"
        "2. Нажми кнопку матча — получишь прогноз\n\n"
        "📊 *Анализируется (данные HLTV):*\n"
        "• Рейтинг HLTV — 40%\n"
        "• Winrate — 35%\n"
        "• K/D / Rating — 15%\n"
        "• Форма (посл. 5 матчей) — 10%\n\n"
        "⚠️ Только для развлечения, не для ставок."
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def today_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Загружаю матчи с HLTV.org...")
    try:
        parser = get_parser()
        matches = await parser.get_today_matches()

        if not matches:
            await msg.edit_text(
                "😔 *Матчей не найдено*\n\n"
                "Возможные причины:\n"
                "• Нет матчей на сегодня/завтра\n"
                "• HLTV временно недоступен\n\n"
                "Попробуй через несколько минут.",
                parse_mode="Markdown"
            )
            return

        matches = await parser.inject_ranks(matches)
        context.user_data["matches"] = matches

        keyboard = []
        for i, m in enumerate(matches):
            stars = "⭐" * min(m.get("stars", 0), 5)
            time_str = m.get("time", "TBD")
            t1 = m["team1"][:14]
            t2 = m["team2"][:14]
            live_icon = "🔴 " if m.get("live") else ""
            r1 = m.get("team1_rank")
            r2 = m.get("team2_rank")
            rank_str = f" [#{r1}v#{r2}]" if r1 and r2 else ""
            btn = f"{live_icon}{stars} {time_str} | {t1} vs {t2}{rank_str}"
            keyboard.append([InlineKeyboardButton(btn, callback_data=f"match_{i}")])

        await msg.edit_text(
            f"📅 *Матчи CS2* — {len(matches)} шт.\n\n👇 Нажми для анализа:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"today_matches ошибка: {e}", exc_info=True)
        await msg.edit_text("❌ Ошибка загрузки. Попробуй через минуту.")


async def analyze_match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    idx = int(query.data.split("_")[1])
    matches = context.user_data.get("matches", [])
    if idx >= len(matches):
        await query.edit_message_text("❌ Матч не найден. Обнови: /today")
        return

    match = matches[idx]
    t1 = match["team1"]
    t2 = match["team2"]

    await query.edit_message_text(
        f"🔍 Загружаю статистику *{t1}* и *{t2}*...\n_(10–20 сек)_",
        parse_mode="Markdown"
    )

    try:
        analysis = await get_analyzer().analyze(match)
        text = _format(analysis)
        kb = [[InlineKeyboardButton("◀️ Назад", callback_data="back")]]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        logger.error(f"analyze_match ошибка: {e}", exc_info=True)
        await query.edit_message_text(f"❌ Не удалось загрузить статистику.\nПопробуй позже.")


def _bar(p: float) -> str:
    f = round(p / 10)
    return "█" * f + "░" * (10 - f)


def _fmt(val, suffix="", decimals=1):
    if val is None:
        return "N/A"
    return f"{val:.{decimals}f}{suffix}"


def _format(a: dict) -> str:
    t1, t2 = a["team1"], a["team2"]
    p1 = a["prediction"]["team1_win_chance"]
    p2 = a["prediction"]["team2_win_chance"]
    winner = t1["name"] if p1 >= p2 else t2["name"]
    conf = max(p1, p2)
    label = "🔥 Высокая уверенность" if conf >= 70 else ("✅ Средняя" if conf >= 60 else "⚖️ Примерно равно")

    text = (
        f"🎮 *{t1['name']}* vs *{t2['name']}*\n"
        f"🏆 _{a.get('event','?')}_\n"
        f"{'—'*26}\n\n"
        f"📊 *Шансы:*\n"
        f"`{_bar(p1)}` *{t1['name']}* {p1:.0f}%\n"
        f"`{_bar(p2)}` *{t2['name']}* {p2:.0f}%\n\n"
        f"🎯 Победит: *{winner}* — {label}\n\n"
        f"{'—'*26}\n"
        f"📈 *Статистика HLTV:*\n\n"
        f"*{t1['name']}*\n"
        f"  📍 Рейтинг: {_fmt(t1.get('rank'), decimals=0)}\n"
        f"  🏅 Winrate: {_fmt(t1.get('winrate'),'%')}\n"
        f"  ⚡ K/D: {_fmt(t1.get('avg_rating'), decimals=2)}\n"
        f"  🔥 Форма: `{t1.get('form') or '?????'}`\n\n"
        f"*{t2['name']}*\n"
        f"  📍 Рейтинг: {_fmt(t2.get('rank'), decimals=0)}\n"
        f"  🏅 Winrate: {_fmt(t2.get('winrate'),'%')}\n"
        f"  ⚡ K/D: {_fmt(t2.get('avg_rating'), decimals=2)}\n"
        f"  🔥 Форма: `{t2.get('form') or '?????'}`\n"
    )
    factors = a["prediction"].get("key_factors", [])
    if factors:
        text += f"\n{'—'*26}\n🔑 *Факторы:*\n"
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
        stars = "⭐" * min(m.get("stars", 0), 5)
        t1 = m["team1"][:14]
        t2 = m["team2"][:14]
        time_str = m.get("time", "TBD")
        live_icon = "🔴 " if m.get("live") else ""
        r1, r2 = m.get("team1_rank"), m.get("team2_rank")
        rank_str = f" [#{r1}v#{r2}]" if r1 and r2 else ""
        keyboard.append([InlineKeyboardButton(
            f"{live_icon}{stars} {time_str} | {t1} vs {t2}{rank_str}",
            callback_data=f"match_{i}"
        )])

    await query.edit_message_text(
        f"📅 *Матчи CS2* — {len(matches)} шт.\n\n👇 Нажми для анализа:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def top_teams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Загружаю рейтинг...")
    try:
        teams = await get_parser().get_top_teams(10)
        if not teams:
            await msg.edit_text("❌ Не удалось загрузить. Попробуй позже.")
            return
        medals = ["🥇","🥈","🥉"] + ["🔹"]*7
        text = "🏆 *Топ-10 команд HLTV:*\n\n"
        for i, t in enumerate(teams):
            text += f"{medals[i]} *#{t['rank']}* {t['name']}\n"
        await msg.edit_text(text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"top_teams ошибка: {e}")
        await msg.edit_text("❌ Ошибка загрузки рейтинга.")


def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Укажи токен: export BOT_TOKEN='твой_токен'")
        return

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
