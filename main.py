import asyncio
import os
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

from telegram import Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp
from telegram.ext import Application, CommandHandler, ContextTypes

from hltv_parser import HLTVParser
from analyzer import MatchAnalyzer
from claude_analyst import claude_analyze
from subscription import check_subscription, activate_code, get_stats, is_admin

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN        = os.getenv("BOT_TOKEN", "")
PANDASCORE_TOKEN = os.getenv("PANDASCORE_TOKEN", "")
GROQ_API_KEY     = os.getenv("GROQ_API_KEY", "")
WEBAPP_URL       = os.getenv("WEBAPP_URL", "")  # https://your-app.up.railway.app

# ── CACHE ────────────────────────────────────────────────────────────
_cache: dict = {}

def cache_get(key: str, ttl_min: int = 5):
    if key in _cache:
        data, ts = _cache[key]
        if datetime.utcnow() - ts < timedelta(minutes=ttl_min):
            return data
    return None

def cache_set(key: str, data):
    _cache[key] = (data, datetime.utcnow())

# ── BOT HANDLERS ─────────────────────────────────────────────────────
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not WEBAPP_URL:
        logger.error("WEBAPP_URL не задан! Кнопка Mini App не будет работать.")
        await update.message.reply_text(
            "⚠️ Бот настроен неправильно: не задан WEBAPP_URL.\n"
            "Администратору нужно добавить эту переменную в Railway."
        )
        return
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🎮 Открыть CS2 Predictor", web_app=WebAppInfo(url=WEBAPP_URL))
    ]])
    await update.message.reply_text(
        "👋 *CS2 Match Predictor*\n\n"
        "Анализирую матчи CS2 по реальной статистике.\n"
        "Нажми кнопку ниже чтобы открыть приложение 👇",
        parse_mode="Markdown",
        reply_markup=kb
    )

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Нажми /start чтобы открыть приложение.\n\n"
        "Для активации подписки используй вкладку *Профиль* в приложении.",
        parse_mode="Markdown"
    )

# ── BOT LIFECYCLE ────────────────────────────────────────────────────
_bot_app: Application | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bot_app
    logger.info("=" * 50)
    logger.info("ЗАПУСК main.py (Mini App / FastAPI режим)")
    logger.info(f"BOT_TOKEN задан: {bool(BOT_TOKEN)}")
    logger.info(f"WEBAPP_URL: {WEBAPP_URL or '⚠️ НЕ ЗАДАН — кнопка не появится!'}")
    logger.info(f"PANDASCORE_TOKEN задан: {bool(PANDASCORE_TOKEN)}")
    logger.info(f"GROQ_API_KEY задан: {bool(GROQ_API_KEY)}")
    logger.info("=" * 50)
    if BOT_TOKEN:
        _bot_app = Application.builder().token(BOT_TOKEN).build()
        _bot_app.add_handler(CommandHandler("start", start_handler))
        _bot_app.add_handler(CommandHandler("help", help_handler))
        await _bot_app.initialize()
        await _bot_app.start()
        await _bot_app.updater.start_polling(drop_pending_updates=True)
        logger.info("Бот запущен (polling активен)")
        # Устанавливаем кнопку меню
        if WEBAPP_URL:
            try:
                await _bot_app.bot.set_chat_menu_button(
                    menu_button=MenuButtonWebApp(text="🎮 Открыть", web_app=WebAppInfo(url=WEBAPP_URL))
                )
                logger.info("Кнопка меню (синяя, слева от поля ввода) установлена")
            except Exception as e:
                logger.warning(f"Не удалось установить кнопку меню: {e}")
    yield
    if _bot_app:
        await _bot_app.updater.stop()
        await _bot_app.stop()
        await _bot_app.shutdown()

# ── FASTAPI APP ───────────────────────────────────────────────────────
app = FastAPI(lifespan=lifespan)

_static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")
    logger.info(f"Папка static найдена: {_static_dir}")
else:
    logger.error(
        f"⚠️ Папка static НЕ НАЙДЕНА по пути {_static_dir}. "
        "Mini App не будет работать — проверь что static/index.html залит в репозиторий."
    )

# ── HEALTH CHECK (Railway) ─────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok"}

# ── API: Serve Mini App ───────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def serve_app():
    path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    with open(path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

# ── API: Matches ──────────────────────────────────────────────────────
@app.get("/api/matches")
async def api_matches():
    cached = cache_get("matches", ttl_min=5)
    if cached:
        return JSONResponse(cached)
    try:
        parser = HLTVParser(token=PANDASCORE_TOKEN)
        matches = await parser.get_today_matches()
        # Сериализуем
        result = [
            {
                "idx": i,
                "team1": m["team1"], "team2": m["team2"],
                "team1_id": m.get("team1_id"), "team2_id": m.get("team2_id"),
                "event": m.get("event", "CS2"),
                "time": m.get("time", "TBD"),
                "maps": m.get("maps", ""),
                "stars": m.get("stars", 0),
                "live": m.get("live", False),
                "match_id": m.get("match_id"),
            }
            for i, m in enumerate(matches)
        ]
        cache_set("matches", result)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"api_matches: {e}", exc_info=True)
        raise HTTPException(500, "Ошибка загрузки матчей")

# ── API: Analysis ─────────────────────────────────────────────────────
@app.get("/api/analysis/{match_idx}")
async def api_analysis(match_idx: int):
    cache_key = f"analysis_{match_idx}"
    cached = cache_get(cache_key, ttl_min=15)
    if cached:
        return JSONResponse(cached)

    # Берём матч из кэша
    matches = cache_get("matches", ttl_min=10)
    if not matches or match_idx >= len(matches):
        raise HTTPException(404, "Матч не найден. Сначала загрузи список матчей.")

    match = matches[match_idx]
    try:
        parser = HLTVParser(token=PANDASCORE_TOKEN)
        analyzer = MatchAnalyzer(parser=parser)
        t1n, t2n = match["team1"], match["team2"]

        # Шаг 1: статистика и H2H — параллельно (составы больше не запрашиваем,
        # PandaScore давал слишком неточные/устаревшие ростеры)
        t1_stats, t2_stats, h2h = await asyncio.gather(
            parser.get_team_stats(match.get("team1_id"), t1n),
            parser.get_team_stats(match.get("team2_id"), t2n),
            parser.get_h2h(match.get("team1_id"), match.get("team2_id"), t1n, t2n),
        )

        # Шаг 2: МОДЕЛЬ считает проценты — единственный источник истины для цифр
        base_pred = analyzer._calc_from_stats(t1_stats, t2_stats, h2h)
        p1 = base_pred["team1_win_chance"]
        p2 = base_pred["team2_win_chance"]

        # Шаг 3: Groq получает готовые p1/p2 и пишет текстовое объяснение + карты
        ai_result = None
        if GROQ_API_KEY:
            ai_result = await claude_analyze(
                t1n, t2n, match.get("event", "CS2"),
                t1_stats, t2_stats, h2h,
                match.get("maps", "BO?"), GROQ_API_KEY,
                p1=p1, p2=p2,
            )

        result = {
            "team1": t1n, "team2": t2n,
            "event": match.get("event", "CS2"),
            "maps": match.get("maps", ""),
            "p1": p1, "p2": p2,
            "verdict": (ai_result or {}).get("verdict", ""),
            "team1_stats": _fmt_stats(t1_stats),
            "team2_stats": _fmt_stats(t2_stats),
            "h2h": h2h,
            "key_factors": (ai_result or {}).get("key_factors", base_pred.get("key_factors", [])),
            "summary": (ai_result or {}).get("summary", ""),
            "maps_analysis": (ai_result or {}).get("maps_analysis", []),
        }
        cache_set(cache_key, result)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"api_analysis: {e}", exc_info=True)
        raise HTTPException(500, str(e))

def _fmt_stats(s: dict) -> dict:
    return {
        "winrate": s.get("winrate"),
        "winrate_last5": s.get("winrate_last5"),
        "weighted_winrate": s.get("weighted_winrate"),
        "form": s.get("form"),
        "avg_round_diff": s.get("avg_round_diff"),
        "maps_played": s.get("maps_played"),
    }

# ── API: Top Teams ────────────────────────────────────────────────────
@app.get("/api/top-teams")
async def api_top_teams():
    cached = cache_get("top_teams", ttl_min=60)
    if cached:
        return JSONResponse(cached)
    try:
        parser = HLTVParser(token=PANDASCORE_TOKEN)
        teams = await parser.get_top_teams(20)
        cache_set("top_teams", teams)
        return JSONResponse(teams)
    except Exception as e:
        raise HTTPException(500, str(e))

# ── API: Subscription ─────────────────────────────────────────────────
@app.get("/api/subscription/{user_id}")
async def api_check_sub(user_id: int):
    return JSONResponse(check_subscription(user_id))

class ActivateBody(BaseModel):
    user_id: int
    code: str

@app.post("/api/activate")
async def api_activate(body: ActivateBody):
    result = activate_code(body.user_id, body.code)
    return JSONResponse(result)

# ── RUN ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")
