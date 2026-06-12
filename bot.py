import asyncio
import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from hltv_parser import HLTVParser
from analyzer import MatchAnalyzer
from claude_analyst import claude_analyze

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN         = os.getenv("BOT_TOKEN", "")
PANDASCORE_TOKEN  = os.getenv("PANDASCORE_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")


def make_services():
    p = HLTVParser(token=PANDASCORE_TOKEN)
    return p, MatchAnalyzer(parser=p)


# ── СТАРТ / ПОМОЩЬ ──────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *HLTV Match Predictor Bot*\n\n"
        "Анализирую CS2 матчи:\n"
        "• Реальная статистика команд (PandaScore)\n"
        "• Состав и форма игроков (AI анализ)\n"
        "• H2H история встреч\n\n"
        "📋 /today — матчи на сегодня/завтра\n"
        "/top — топ команды\n"
        "/help — как работает анализ",
        parse_mode="Markdown"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ai = "✅ подключён" if GROQ_API_KEY else "❌ не настроен (добавь ANTHROPIC\\_API\\_KEY)"
    await update.message.reply_text(
        "🤖 *Как работает анализ:*\n\n"
        "📊 Winrate 20 матчей — 20%\n"
        "📈 Winrate посл. 5 — 20%\n"
        "🔥 Форма взвешенная — 15%\n"
        "🎯 Avg разница раундов — 20%\n"
        "⚡ K/D состава (AI) — 15%\n"
        "🤝 H2H встречи — 10%\n\n"
        f"🤖 AI анализ игроков: {ai}\n\n"
        "⚠️ Только для развлечения.",
        parse_mode="Markdown"
    )


# ── МАТЧИ ────────────────────────────────────────────────────────────
async def today_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not PANDASCORE_TOKEN:
        await update.message.reply_text("❌ Не задан `PANDASCORE_TOKEN` в Railway Variables.", parse_mode="Markdown")
        return
    msg = await update.message.reply_text("⏳ Загружаю матчи CS2...")
    try:
        parser, _ = make_services()
        matches = await parser.get_today_matches()
        if not matches:
            await msg.edit_text("😔 *Матчей не найдено*\n\nНа сегодня/завтра матчей CS2 нет.", parse_mode="Markdown")
            return
        context.user_data["matches"] = matches
        await msg.edit_text(
            f"📅 *Матчи CS2* — {len(matches)} шт. (МСК)\n\n👇 Нажми для анализа:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(_matches_kb(matches))
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


# ── АНАЛИЗ МАТЧА ─────────────────────────────────────────────────────
async def analyze_match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    matches = context.user_data.get("matches", [])
    if idx >= len(matches):
        await query.edit_message_text("❌ Матч не найден. Обнови: /today"); return

    match = matches[idx]
    t1n, t2n = match["team1"], match["team2"]

    ai_note = " + AI анализ игроков" if GROQ_API_KEY else ""
    await query.edit_message_text(
        f"🔍 Анализирую *{t1n}* vs *{t2n}*...\n"
        f"_(статистика{ai_note})_",
        parse_mode="Markdown"
    )

    try:
        parser, analyzer = make_services()

        # Загружаем статистику команд и H2H параллельно
        t1_stats, t2_stats, h2h = await asyncio.gather(
            parser.get_team_stats(match.get("team1_id"), t1n),
            parser.get_team_stats(match.get("team2_id"), t2n),
            parser.get_h2h(match.get("team1_id"), match.get("team2_id"), t1n, t2n),
        )

        # Базовый прогноз из статистики
        base_pred = analyzer._calc_from_stats(t1_stats, t2_stats, h2h)

        # AI анализ игроков (если ключ есть)
        ai_result = None
        if GROQ_API_KEY:
            ai_result = await claude_analyze(
                t1n, t2n,
                match.get("event", "CS2"),
                t1_stats, t2_stats, h2h,
                match.get("maps", "BO?"),
                GROQ_API_KEY,
            )

        # Финальный прогноз: смешиваем базу и AI
        if ai_result:
            # 40% вес базовой статистики + 60% AI (знает игроков)
            p1 = round(base_pred["team1_win_chance"] * 0.4 + ai_result["team1_win_pct"] * 0.6, 1)
            p2 = round(100 - p1, 1)
        else:
            p1 = base_pred["team1_win_chance"]
            p2 = base_pred["team2_win_chance"]

        pages = _build_pages(t1n, t2n, match, t1_stats, t2_stats, h2h, p1, p2, base_pred, ai_result)
        context.user_data[f"pages_{idx}"] = pages
        context.user_data[f"page_{idx}"] = 0
        await query.edit_message_text(
            pages[0], parse_mode="Markdown",
            reply_markup=_page_kb(0, len(pages), idx)
        )
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
    await query.edit_message_text(
        pages[page_idx], parse_mode="Markdown",
        reply_markup=_page_kb(page_idx, len(pages), match_idx)
    )


def _page_kb(page, total, match_idx):
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"page_{match_idx}_{page-1}"))
    if page < total - 1:
        row.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"page_{match_idx}_{page+1}"))
    back = [InlineKeyboardButton("◀️ К матчам", callback_data="back")]
    return InlineKeyboardMarkup([row, back] if row else [back])


# ── ФОРМАТИРОВАНИЕ СТРАНИЦ ───────────────────────────────────────────
def _bar(p):
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
    return f"{'🔥'if s[0]=='W' else '❄️'} {s[0]}×{s[1:]}"


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

    # ── Страница 1: Прогноз ─────────────────────────────────────────
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
        f"  ⚡ Стрик: {_streak(t1s.get('streak'))}\n"
        f"  🎯 Avg ±раундов: {_rd(t1s.get('avg_round_diff'))}\n\n"
        f"*{t2n}*\n"
        f"  🏅 Winrate (20м): {_v(t2s.get('winrate'), '%')}\n"
        f"  📈 Winrate (посл.5): {_v(t2s.get('winrate_last5'), '%')}\n"
        f"  🔥 Форма: `{t2s.get('form') or '?????'}`\n"
        f"  ⚡ Стрик: {_streak(t2s.get('streak'))}\n"
        f"  🎯 Avg ±раундов: {_rd(t2s.get('avg_round_diff'))}\n"
    )

    # H2H
    if h2h_total >= 1:
        pg1 += f"\n{'—'*28}\n🤝 *H2H ({h2h_total} встреч):*\n"
        pg1 += f"  {t1n}: {h2h.get('team1_wins',0)} побед\n"
        pg1 += f"  {t2n}: {h2h.get('team2_wins',0)} побед\n"
        for lm in h2h.get("last_matches", []):
            pg1 += f"  › {lm['date']} {lm['format']} → 🏆 {lm['winner']}\n"
    else:
        pg1 += f"\n🤝 *H2H:* нет данных\n"

    # Ключевые факторы
    factors = []
    if ai: factors = ai.get("key_factors", [])
    if not factors: factors = base_pred.get("key_factors", [])
    if factors:
        pg1 += f"\n{'—'*28}\n🔑 *Ключевые факторы:*\n"
        for f in factors[:4]:
            pg1 += f"  • {f}\n"

    # Карты (если AI знает)
    if ai and ai.get("key_maps"):
        pg1 += f"\n🗺 *Карты:* {ai['key_maps']}\n"

    # Итог от AI
    if ai and ai.get("summary"):
        pg1 += f"\n💬 _{ai['summary']}_\n"

    pg1 += f"\n_Стр. 1/3 — следующая: состав {t1n}_"

    # ── Страница 2: Игроки команды 1 ────────────────────────────────
    pg2 = _players_page(t1n, ai.get("team1_players") if ai else None)

    # ── Страница 3: Игроки команды 2 ────────────────────────────────
    pg3 = _players_page(t2n, ai.get("team2_players") if ai else None)

    return [pg1, pg2, pg3]


def _players_page(team_name: str, players: list | None) -> str:
    text = f"👥 *Состав {team_name}*\n{'—'*28}\n\n"

    if not players:
        text += (
            "_Данные о составе недоступны._\n\n"
            "Добавь `GROQ_API_KEY` в Railway Variables\n"
            "для AI-анализа игроков."
        )
        text += "\n\n⚠️ _Только для развлечения._"
        return text

    form_icons = {"горячая": "🔥", "хорошая": "✅", "средняя": "😐", "слабая": "❄️"}
    # Сортируем по рейтингу
    sorted_p = sorted(players, key=lambda p: p.get("rating") or 0, reverse=True)

    for i, p in enumerate(sorted_p):
        name = p.get("name", "?")
        role = p.get("role", "")
        rating = p.get("rating")
        form = p.get("form", "средняя").lower()
        note = p.get("note", "")
        icon = form_icons.get(form, "❓")
        star = "⭐ " if i == 0 else "👤 "

        text += f"{star}*{name}* {icon}"
        if role: text += f" _({role})_"
        text += "\n"
        if rating: text += f"  📊 HLTV рейтинг: ~{rating:.2f}\n"
        if note:   text += f"  💬 {note}\n"
        text += "\n"

    text += "⚠️ _Только для развлечения._"
    return text


# ── BACK ─────────────────────────────────────────────────────────────
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


# ── ТОП ──────────────────────────────────────────────────────────────
async def top_teams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not PANDASCORE_TOKEN:
        await update.message.reply_text("❌ Не задан PANDASCORE_TOKEN."); return
    msg = await update.message.reply_text("⏳ Загружаю...")
    try:
        parser, _ = make_services()
        teams = await parser.get_top_teams(10)
        if not teams:
            await msg.edit_text("❌ Не удалось загрузить."); return
        medals = ["🥇","🥈","🥉"] + ["🔹"]*7
        text = "🏆 *CS2 команды (PandaScore):*\n\n"
        for i, t in enumerate(teams):
            text += f"{medals[i]} {t['name']}\n"
        await msg.edit_text(text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"top_teams: {e}")
        await msg.edit_text("❌ Ошибка.")


# ── CHECKENV ─────────────────────────────────────────────────────────
async def checkenv(update, context):
    def chk(val, name):
        if val:
            return f"✅ `{name}` = `{val[:8]}...{val[-4:]}`"
        return f"❌ `{name}` — НЕ ЗАДАН"
    text = "🔧 *Переменные окружения:*\n\n"
    text += chk(BOT_TOKEN, "BOT_TOKEN") + "\n"
    text += chk(PANDASCORE_TOKEN, "PANDASCORE_TOKEN") + "\n"
    text += chk(GROQ_API_KEY, "GROQ_API_KEY") + "\n\n"
    if GROQ_API_KEY:
        try:
            import aiohttp as _aio
            async with _aio.ClientSession() as s:
                async with s.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                    json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
                    timeout=_aio.ClientTimeout(total=10),
                ) as r:
                    if r.status == 200:
                        text += "🤖 Claude API: ✅ работает"
                    else:
                        body = await r.text()
                        text += f"🤖 Claude API: ❌ статус {r.status}\n`{body[:200]}`"
        except Exception as e:
            text += f"🤖 Claude API: ❌ `{e}`"
    else:
        text += "🤖 Claude API: ❌ ключ не задан"
    await update.message.reply_text(text, parse_mode="Markdown")


# ── MAIN ─────────────────────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        print("❌ Укажи BOT_TOKEN!"); return
    if not GROQ_API_KEY:
        print("⚠️ GROQ_API_KEY не задан — AI анализ игроков отключён")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("today", today_matches))
    app.add_handler(CommandHandler("top", top_teams))
    app.add_handler(CommandHandler("checkenv", checkenv))
    app.add_handler(CallbackQueryHandler(analyze_match, pattern=r"^match_\d+$"))
    app.add_handler(CallbackQueryHandler(page_nav, pattern=r"^page_\d+_\d+$"))
    app.add_handler(CallbackQueryHandler(back_handler, pattern="^back$"))
    print("🤖 Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
