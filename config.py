"""Конфигурация — загружается из config.json.

⚠️ ВАЖНО: config.json хранит значения (bot_token, moderated_chats, preset_staff, ...),
а config.py только читает их и экспортирует константы для кода.
"""

from __future__ import annotations

import json
import os
from typing import Any


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")


def _load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


_cfg: dict[str, Any] = _load_config(CONFIG_PATH)

# ==============================
# ОСНОВНЫЕ
# ==============================

BOT_TOKEN: str = str(_cfg.get("bot_token", "")).strip()
if not BOT_TOKEN:
    raise KeyError("config.json: отсутствует ключ 'bot_token'")

# Чаты, которые бот модерирует (ID супергрупп/групп)
MODERATED_CHATS: list[int] = [int(x) for x in _cfg.get("moderated_chats", [])]

# Стафф-чат (логи, репорты)
STAFF_CHAT_ID: int = int(_cfg.get("staff_chat_id", 0) or 0)
LOG_TOPIC_ID: int = int(_cfg.get("log_topic_id", 0) or 0)
GBAN_TOPIC_ID: int = int(_cfg.get("gban_topic_id", 0) or 0)
PUNISH_TOPIC_ID: int = int(_cfg.get("punish_topic_id", 0) or 0)
REPORT_TOPIC_ID: int = int(_cfg.get("report_topic_id", 0) or 0)

# Ссылка на поддержку
SUPPORT_LINK: str = str(_cfg.get("support_link", "")).strip()

# ==============================
# PERPLEXITY AI
# ==============================

PERPLEXITY_API_KEY: str = str(_cfg.get("perplexity_api_key", "")).strip()
PERPLEXITY_MODEL: str = str(_cfg.get("perplexity_model", "llama-3.1-sonar-large-128k-online")).strip()

# ==============================
# БАЗА ДАННЫХ
# ==============================

# По умолчанию храним БД в ./data/bot.db (папка будет создана автоматически).
DATABASE_PATH: str = os.path.join(BASE_DIR, "data", "bot.db")

# ==============================
# ПРЕДУСТАНОВЛЕННЫЕ РОЛИ (user_id -> level)
# ==============================

PRESET_STAFF: dict[int, int] = {
    int(uid): int(level) for uid, level in (_cfg.get("preset_staff", {}) or {}).items()
}

# ==============================
# РОЛИ
# ==============================

ROLE_USER = 0
# Порог модерации/админки в проекте (согласован с utils.can_moderate/can_admin)
ROLE_MODERATOR = 5
ROLE_ADMIN = 8
ROLE_SENIOR_ADMIN = 9
ROLE_OWNER = 10

# Человеческие названия ролей (можно расширять)
ROLE_NAMES: dict[int, str] = {
    0: "Пользователь",
    5: "Модератор",
    7: "Старший модератор",
    8: "Администратор",
    9: "Главный модератор",
    10: "Владелец",
}


def role_name_by_level(level: int) -> str:
    """Возвращает ближайшее название роли по уровню."""
    if level in ROLE_NAMES:
        return ROLE_NAMES[level]
    below = [k for k in ROLE_NAMES if k <= level]
    return ROLE_NAMES[max(below)] if below else f"Уровень {level}"


ANON_ADMIN_ROLE: int = int(_cfg.get("anon_admin_role", ROLE_OWNER) or ROLE_OWNER)

# ==============================
# АНТИСПАМ
# ==============================

SPAM_INTERVAL_SECONDS: int = int(_cfg.get("spam_interval_seconds", 2) or 2)
SPAM_MESSAGES_COUNT: int = int(_cfg.get("spam_messages_count", 3) or 3)
ANTISPAM_WARN_THRESHOLD = 3

# ==============================
# ВАРНЫ
# ==============================

MAX_WARNS: int = int(_cfg.get("max_warns", 3) or 3)

# ==============================
# ИНТЕРФЕЙС
# ==============================

INTERFACE_COMMANDS = "commands"
INTERFACE_BUTTONS = "buttons"

# ==============================
# ПАГИНАЦИЯ
# ==============================

USERS_PER_PAGE = 8
