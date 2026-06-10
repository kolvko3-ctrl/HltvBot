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

parser = HLTVParser()
analyzer = MatchAnalyzer()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *HLTV Match Predictor Bot*\n\n"
        "Я анализирую статистику с HLTV.org и предсказываю исходы матчей CS2.\n\n"
        "📋 *Команды:*\n"
        "/today — матчи на сегодня\n"
        "/top — топ-10 команд по рейтингу\n"
        "/help — помощь\n\n"
        "Выбери матч и получи детальный анализ! 🎯"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 *Как пользоваться ботом:*\n\n"
        "1️⃣ Введи /today — увидишь матчи на сегодня\n"
        "2️⃣ Нажми на кнопку матча — получишь анализ\n"
        "3️⃣ Бот покажет шансы каждой команды на победу\n\n"
        "📊 *Что анализируется:*\n"
        "• Текущий рейтинг HLTV\n"
        "• Winrate за последние 3 месяца\n"
        "• Форма команды (последние 10 матчей)\n"
        "• Результаты личных встреч (H2H)\n"
        "• Средний рейтинг игроков (Rating 2.0)\n"
        "• Разница в опыте и уровне турнира\n\n"
        "⚠️ *Дисклеймер:* Прогнозы носят развлекательный характер и не являются ставочными советами."
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def today_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Загружаю матчи с HLTV...")

    try:
        matches = await parser.get_today_matches()

        if not matches:
            await msg.edit_text(
                "😔 Сегодня матчей не найдено или HLTV недоступен.\n\n"
                "Попробуй позже или проверь hltv.org вручную."
            )
            return

        text = f"📅 *Матчи CS2 на сегодня* ({len(matches)} шт.)\n\n"

        keyboard = []
        for i, match in enumerate(matches):
            team1 = match["team1"]
            team2 = match["team2"]
            event = match.get("event", "Unknown Event")
            time_str = match.get("time", "TBD")
            stars = "⭐" * match.get("stars", 0)

            label = f"{team1} vs {team2}"
            if len(label) > 30:
                label = f"{team1[:12]} vs {team2[:12]}"

            button_text = f"{stars} {time_str} | {label}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"match_{i}")])

        context.user_data["matches"] = matches

        reply_markup = InlineKeyboardMarkup(keyboard)
        await msg.edit_text(text + "👇 Нажми на матч для анализа:", parse_mode="Markdown", reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Error fetching matches: {e}")
        await msg.edit_text(
            "❌ Ошибка при загрузке матчей.\n\n"
            "HLTV иногда блокирует запросы. Попробуй через несколько минут.\n"
            "Также проверь, что установлены все зависимости (см. README)."
        )


async def analyze_match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    match_idx = int(query.data.split("_")[1])
    matches = context.user_data.get("matches", [])

    if match_idx >= len(matches):
        await query.edit_message_text("❌ Матч не найден. Обнови список командой /today")
        return

    match = matches[match_idx]
    team1 = match["team1"]
    team2 = match["team2"]

    await query.edit_message_text(f"🔍 Анализирую матч *{team1}* vs *{team2}*...", parse_mode="Markdown")

    try:
        analysis = await analyzer.analyze(match)
        text = format_analysis(analysis)

        keyboard = [[InlineKeyboardButton("◀️ Назад к матчам", callback_data="back_to_matches")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Error analyzing match: {e}")
        await query.edit_message_text(
            f"❌ Не удалось загрузить статистику для матча *{team1}* vs *{team2}*.\n\n"
            "Попробуй позже.",
            parse_mode="Markdown"
        )


def format_analysis(analysis: dict) -> str:
    t1 = analysis["team1"]
    t2 = analysis["team2"]
    pred = analysis["prediction"]

    winner = t1["name"] if pred["team1_win_chance"] >= 50 else t2["name"]
    confidence = max(pred["team1_win_chance"], pred["team2_win_chance"])

    if confidence >= 70:
        conf_label = "🔥 Высокая уверенность"
    elif confidence >= 60:
        conf_label = "✅ Средняя уверенность"
    else:
        conf_label = "⚖️ Неопределённость"

    bar1 = make_bar(pred["team1_win_chance"])
    bar2 = make_bar(pred["team2_win_chance"])

    text = (
        f"🎮 *{t1['name']}* vs *{t2['name']}*\n"
        f"🏆 *{analysis.get('event', 'Unknown Event')}*\n"
        f"{'—' * 28}\n\n"
        f"📊 *Шансы на победу:*\n"
        f"`{bar1}` {t1['name']}: *{pred['team1_win_chance']:.0f}%*\n"
        f"`{bar2}` {t2['name']}: *{pred['team2_win_chance']:.0f}%*\n\n"
        f"🎯 *Прогноз: {winner}* — {conf_label}\n\n"
        f"{'—' * 28}\n"
        f"📈 *Статистика команд:*\n\n"
        f"*{t1['name']}*\n"
        f"  📍 Рейтинг HLTV: #{t1.get('rank', 'N/A')}\n"
        f"  🏅 Winrate (3 мес): {t1.get('winrate', 'N/A')}%\n"
        f"  🔥 Форма: {t1.get('form', 'N/A')}\n"
        f"  ⭐ Avg Rating 2.0: {t1.get('avg_rating', 'N/A')}\n\n"
        f"*{t2['name']}*\n"
        f"  📍 Рейтинг HLTV: #{t2.get('rank', 'N/A')}\n"
        f"  🏅 Winrate (3 мес): {t2.get('winrate', 'N/A')}%\n"
        f"  🔥 Форма: {t2.get('form', 'N/A')}\n"
        f"  ⭐ Avg Rating 2.0: {t2.get('avg_rating', 'N/A')}\n\n"
    )

    h2h = analysis.get("h2h")
    if h2h:
        text += (
            f"{'—' * 28}\n"
            f"🤝 *H2H (последние встречи):*\n"
            f"  {t1['name']}: {h2h.get('team1_wins', 0)} побед\n"
            f"  {t2['name']}: {h2h.get('team2_wins', 0)} побед\n\n"
        )

    factors = pred.get("key_factors", [])
    if factors:
        text += f"{'—' * 28}\n🔑 *Ключевые факторы:*\n"
        for f in factors:
            text += f"  • {f}\n"

    text += "\n⚠️ _Прогноз для развлечения. Не является ставочным советом._"
    return text


def make_bar(percent: float, length: int = 10) -> str:
    filled = round(percent / 100 * length)
    return "█" * filled + "░" * (length - filled)


async def back_to_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    matches = context.user_data.get("matches", [])

    if not matches:
        await query.edit_message_text("Список матчей устарел. Введи /today")
        return

    keyboard = []
    for i, match in enumerate(matches):
        team1 = match["team1"]
        team2 = match["team2"]
        time_str = match.get("time", "TBD")
        stars = "⭐" * match.get("stars", 0)
        label = f"{team1[:12]} vs {team2[:12]}"
        keyboard.append([InlineKeyboardButton(f"{stars} {time_str} | {label}", callback_data=f"match_{i}")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        f"📅 *Матчи CS2 на сегодня* ({len(matches)} шт.)\n\n👇 Нажми на матч для анализа:",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )


async def top_teams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Загружаю рейтинг команд...")
    try:
        teams = await parser.get_top_teams(limit=10)
        text = "🏆 *Топ-10 команд HLTV прямо сейчас:*\n\n"
        medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 7
        for i, team in enumerate(teams):
            text += f"{medals[i]} *#{i+1}* {team['name']} — {team.get('points', '?')} pts\n"
        await msg.edit_text(text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error fetching top teams: {e}")
        await msg.edit_text("❌ Не удалось загрузить рейтинг. Попробуй позже.")


def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Укажи токен бота в переменной BOT_TOKEN!")
        print("   export BOT_TOKEN='your_token_here'")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("today", today_matches))
    app.add_handler(CommandHandler("top", top_teams))
    app.add_handler(CallbackQueryHandler(analyze_match, pattern=r"^match_\d+$"))
    app.add_handler(CallbackQueryHandler(back_to_matches, pattern="^back_to_matches$"))

    print("🤖 Бот запущен! Нажми Ctrl+C для остановки.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
