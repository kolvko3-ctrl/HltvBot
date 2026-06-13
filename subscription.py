"""
Система подписки на основе кодов доступа.
Хранение в JSON-файле (работает на Railway).
"""
import json
import os
import hashlib
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Путь к файлу с данными (Railway сохраняет между деплоями если в /app)
DATA_FILE = "/app/subscription_data.json"

# Коды доступа — задаются через переменную окружения SUBSCRIPTION_CODES
# Формат: "КОД1:30,КОД2:30,КОД3:7" (код:дней)
# Пример: SUBSCRIPTION_CODES=ALPHA2026:30,BETA123:7,VIP999:90
ADMIN_IDS_ENV = os.getenv("ADMIN_IDS", "")  # ID администраторов через запятую


def _load() -> dict:
    """Загружаем данные из файла."""
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка загрузки данных: {e}")
    return {"users": {}, "codes": {}}


def _save(data: dict):
    """Сохраняем данные в файл."""
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Ошибка сохранения данных: {e}")


def _get_codes_from_env() -> dict:
    """Парсим коды из переменной окружения."""
    raw = os.getenv("SUBSCRIPTION_CODES", "")
    codes = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if ":" in entry:
            parts = entry.split(":")
            code = parts[0].strip().upper()
            try:
                days = int(parts[1].strip())
                codes[code] = days
            except ValueError:
                pass
    return codes


def is_admin(user_id: int) -> bool:
    """Проверяем является ли пользователь администратором."""
    admin_ids = [x.strip() for x in ADMIN_IDS_ENV.split(",") if x.strip()]
    return str(user_id) in admin_ids


def check_subscription(user_id: int) -> dict:
    """
    Проверяет подписку пользователя.
    Возвращает: {"active": bool, "expires": str|None, "days_left": int}
    """
    if is_admin(user_id):
        return {"active": True, "expires": "∞", "days_left": 9999}

    data = _load()
    user_str = str(user_id)
    user_data = data.get("users", {}).get(user_str)

    if not user_data:
        return {"active": False, "expires": None, "days_left": 0}

    expires = datetime.fromisoformat(user_data["expires"])
    now = datetime.utcnow()

    if now > expires:
        return {"active": False, "expires": user_data["expires"][:10], "days_left": 0}

    days_left = (expires - now).days
    return {"active": True, "expires": expires.strftime("%d.%m.%Y"), "days_left": days_left}


def activate_code(user_id: int, code: str) -> dict:
    """
    Активирует код для пользователя.
    Возвращает: {"success": bool, "message": str, "days": int}
    """
    code = code.strip().upper()
    codes = _get_codes_from_env()

    if code not in codes:
        return {"success": False, "message": "❌ Неверный код доступа.", "days": 0}

    days = codes[code]
    data = _load()

    # Проверяем не был ли код уже использован этим же пользователем
    used_by = data.get("codes", {}).get(code, [])
    user_str = str(user_id)

    # Считаем сколько раз код был использован (можно ограничить)
    # Пока разрешаем каждому пользователю использовать код 1 раз
    if user_str in used_by:
        return {"success": False, "message": "❌ Этот код вы уже использовали.", "days": 0}

    # Активируем подписку
    now = datetime.utcnow()
    existing = data.get("users", {}).get(user_str)

    if existing:
        # Продлеваем существующую
        current_expires = datetime.fromisoformat(existing["expires"])
        if current_expires > now:
            new_expires = current_expires + timedelta(days=days)
        else:
            new_expires = now + timedelta(days=days)
    else:
        new_expires = now + timedelta(days=days)

    # Сохраняем
    if "users" not in data:
        data["users"] = {}
    if "codes" not in data:
        data["codes"] = {}

    data["users"][user_str] = {
        "expires": new_expires.isoformat(),
        "activated_at": now.isoformat(),
        "code_used": code,
    }

    if code not in data["codes"]:
        data["codes"][code] = []
    data["codes"][code].append(user_str)

    _save(data)

    return {
        "success": True,
        "message": f"✅ Подписка активирована на {days} дней!",
        "days": days,
        "expires": new_expires.strftime("%d.%m.%Y"),
    }


def get_stats() -> dict:
    """Статистика для администратора."""
    data = _load()
    users = data.get("users", {})
    now = datetime.utcnow()

    active = sum(1 for u in users.values()
                 if datetime.fromisoformat(u["expires"]) > now)
    total = len(users)
    codes_used = {k: len(v) for k, v in data.get("codes", {}).items()}

    return {"total_users": total, "active_users": active, "codes_used": codes_used}
