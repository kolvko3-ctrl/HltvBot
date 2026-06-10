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
        "Анализирую реальную статистику CS2 с HLTV.org и предсказываю исходы матчей.\n\n"
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
        "1️⃣ Введи /today — увидишь матчи\n"
        "2️⃣ Нажми на кнопку матча — получишь анализ\n"
        "3️⃣ Бот покажет реальные шансы на победу\n\n"
        "📊 *Что анализируется (реальные данные HLTV):*\n"
        "• Рейтинг HLTV (40%)\n"
        "• Winrate за последние матчи (35%)\n"
        "• Средний K/D команды (15%)\n"
        "• Форма — последние 5 результатов (10%)\n\n"
        "⚠️ *Дисклеймер:* Прогнозы для развлечения, не ставочные советы."
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def today_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Загружаю матчи с HLTV.org...")

    try:
        matches = await parser.get_today_matches()

        if not matches:
            await msg.edit_text(
                "😔 *Матчей не найдено*\n\n"
                "Возможные причины:\n"
                "• Сегодня нет запланированных матчей\n"
                "• HLTV временно недоступен\n\n"
                "Попробуй через несколько минут.",
                parse_mode="Markdown"
            )
            return

        # Обогащаем матчи рангами из топ-30
        matches = await parser.inject_ranks(matches)
        context.user_data["matches"] = matches

        text = f"📅 *Матчи CS2* — найдено {len(matches)}\n\n"
        keyboard = []

        for i, m in enumerate(matches):
            stars = "⭐" * min(m.get("stars", 0), 5)
            time_str = m.get("time", "TBD")
            t1 = m["team1"][:13]
            t2 = m["team2"][:13]
            live_icon = "🔴 " if m.get("live") else ""
            r1 = m.get("team1_rank")
            r2 = m.get("team2_rank")
            rank_str = f" (#{r1} vs #{r2})" if r1 and r2 else ""

            btn = f"{live_icon}{stars} {time_str} | {t1} vs {t2}{rank_str}"
            keyboard.append([InlineKeyboardButton(btn, callback_data=f"match_{i}")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await msg.edit_text(
            text + "👇 Нажми на матч для анализа:",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Ошибка today_matches: {e}", exc_info=True)
        await msg.edit_text(
            "❌ *Ошибка загрузки матчей*\n\n"
            "HLTV временно недоступен или блокирует запросы.\n"
            "Попробуй через 1–2 минуты.",
            parse_mode="Markdown"
        )


async def analyze_match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    match_idx = int(query.data.split("_")[1])
    matches = context.user_data.get("matches", [])

    if match_idx >= len(matches):
        await query.edit_message_text("❌ Матч не найден. Обнови список: /today")
        return

    match = matches[match_idx]
    t1_name = match["team1"]
    t2_name = match["team2"]

    await query.edit_message_text(
        f"🔍 Загружаю статистику *{t1_name}* и *{t2_name}* с HLTV...\n"
        f"_(это может занять 10–20 секунд)_",
        parse_mode="Markdown"
    )

    try:
        analysis = await analyzer.analyze(match)
        text = _format_analysis(analysis)
        keyboard = [[InlineKeyboardButton("◀️ Назад к матчам", callback_data="back_to_matches")]]
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Ошибка analyze_match: {e}", exc_info=True)
        await query.edit_message_text(
            f"❌ Не удалось загрузить статистику для *{t1_name}* vs *{t2_name}*.\n"
            "Попробуй чуть позже.",
            parse_mode="Markdown"
        )


def _format_analysis(analysis: dict) -> str:
    t1 = analysis["team1"]
    t2 = analysis["team2"]
    pred = analysis["prediction"]

    p1 = pred["team1_win_chance"]
    p2 = pred["team2_win_chance"]
    winner = t1["name"] if p1 >= p2 else t2["name"]
    confidence = max(p1, p2)

    if confidence >= 70:
        conf_label = "🔥 Высокая уверенность"
    elif confidence >= 60:
        conf_label = "✅ Средняя уверенность"
    else:
        conf_label = "⚖️ Примерно равные шансы"

    def bar(p):
        filled = round(p / 10)
        return "█" * filled + "░" * (10 - filled)

    def rank_str(r):
        return f"#{r}" if r else "N/A"

    def wr_str(w):
        return f"{w:.0f}%" if w is not None else "N/A"

    def kd_str(k):
        return f"{k:.2f}" if k is not None else "N/A"

    def form_str(f):
        return f if f else "?????"

    text = (
        f"🎮 *{t1['name']}* vs *{t2['name']}*\n"
        f"🏆 _{analysis.get('event', 'Unknown Event')}_\n"
        f"{'—'*28}\n\n"
        f"📊 *Шансы на победу:*\n"
        f"`{bar(p1)}` *{t1['name']}*: *{p1:.0f}%*\n"
        f"`{bar(p2)}` *{t2['name']}*: *{p2:.0f}%*\n\n"
        f"🎯 Прогноз: *{winner}* — {conf_label}\n\n"
        f"{'—'*28}\n"
        f"📈 *Статистика (реальные данные HLTV):*\n\n"
        f"*{t1['name']}*\n"
        f"  📍 Рейтинг: {rank_str(t1.get('rank'))}\n"
        f"  🏅 Winrate: {wr_str(t1.get('winrate'))}\n"
        f"  ⚡ K/D: {kd_str(t1.get('avg_rating'))}\n"
        f"  🔥 Форма: `{form_str(t1.get('form'))}`\n\n"
        f"*{t2['name']}*\n"
        f"  📍 Рейтинг: {rank_str(t2.get('rank'))}\n"
        f"  🏅 Winrate: {wr_str(t2.get('winrate'))}\n"
        f"  ⚡ K/D: {kd_str(t2.get('avg_rating'))}\n"
        f"  🔥 Форма: `{form_str(t2.get('form'))}`\n"
    )

    factors = pred.get("key_factors", [])
    if factors:
        text += f"\n{'—'*28}\n🔑 *Ключевые факторы:*\n"
        for f in factors:
            text += f"  {f}\n"

    text += "\n⚠️ _Прогноз для развлечения. Не является ставочным советом._"
    return text


async def back_to_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    matches = context.user_data.get("matches", [])

    if not matches:
        await query.edit_message_text("Список устарел. Введи /today")
        return

    keyboard = []
    for i, m in enumerate(matches):
        stars = "⭐" * min(m.get("stars", 0), 5)
        time_str = m.get("time", "TBD")
        t1 = m["team1"][:13]
        t2 = m["team2"][:13]
        live_icon = "🔴 " if m.get("live") else ""
        r1 = m.get("team1_rank")
        r2 = m.get("team2_rank")
        rank_str = f" (#{r1} vs #{r2})" if r1 and r2 else ""
        btn = f"{live_icon}{stars} {time_str} | {t1} vs {t2}{rank_str}"
        keyboard.append([InlineKeyboardButton(btn, callback_data=f"match_{i}")])

    await query.edit_message_text(
        f"📅 *Матчи CS2* — {len(matches)} шт.\n\n👇 Нажми на матч для анализа:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def top_teams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Загружаю рейтинг с HLTV...")
    try:
        teams = await parser.get_top_teams(10)
        if not teams:
            await msg.edit_text("❌ Не удалось загрузить рейтинг. Попробуй позже.")
            return

        text = "🏆 *Топ-10 команд HLTV прямо сейчас:*\n\n"
        medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 7
        for i, team in enumerate(teams):
            pts = team.get("points", "N/A")
            text += f"{medals[i]} *#{i+1}* {team['name']}"
            if pts and pts != "N/A":
                text += f" — {pts} pts"
            text += "\n"

        await msg.edit_text(text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка top_teams: {e}")
        await msg.edit_text("❌ Не удалось загрузить рейтинг. Попробуй позже.")


def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Укажи токен: export BOT_TOKEN='your_token'")
        return

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("today", today_matches))
    app.add_handler(CommandHandler("top", top_teams))
    app.add_handler(CallbackQueryHandler(analyze_match, pattern=r"^match_\d+$"))
    app.add_handler(CallbackQueryHandler(back_to_matches, pattern="^back_to_matches$"))

    print("🤖 Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
