"""
Утилиты — форматирование, время, роли.
"""

import time
import datetime
from config import ROLE_NAMES, ROLE_MODERATOR, ROLE_ADMIN, ROLE_OWNER, role_name_by_level

DURATION_MAP = {
    "1 минута": 60, "5 минут": 300, "15 минут": 900, "30 минут": 1800,
    "1 час": 3600, "6 часов": 21600, "12 часов": 43200,
    "1 день": 86400, "3 дня": 259200, "7 дней": 604800, "14 дней": 1209600,
    "1 месяц": 2592000, "3 месяца": 7776000, "6 месяцев": 15552000,
    "1 год": 31536000, "Навсегда": 0,
}


def parse_duration_text(text: str) -> float:
    text = text.strip().lower()
    for label, seconds in DURATION_MAP.items():
        if text == label.lower():
            return 0 if seconds == 0 else time.time() + seconds
    return parse_short_duration(text)


def parse_short_duration(text: str) -> float:
    text = text.strip().lower()
    if text in ("0", "forever", "навсегда", "perm"):
        return 0
    multipliers = {"m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}
    for suffix, mult in multipliers.items():
        if text.endswith(suffix):
            try:
                return time.time() + int(text[:-1]) * mult
            except ValueError:
                pass
    try:
        s = int(text)
        return 0 if s == 0 else time.time() + s
    except ValueError:
        return time.time() + 3600


def format_duration(until: float) -> str:
    if until == 0:
        return "навсегда"
    remaining = until - time.time()
    if remaining <= 0:
        return "истекло"
    if remaining < 60:
        return f"{int(remaining)} сек."
    if remaining < 3600:
        return f"{int(remaining // 60)} мин."
    if remaining < 86400:
        h = int(remaining // 3600)
        m = int((remaining % 3600) // 60)
        return f"{h} ч. {m} мин." if m else f"{h} ч."
    if remaining < 2592000:
        return f"{int(remaining // 86400)} дн."
    if remaining < 31536000:
        return f"{int(remaining // 2592000)} мес."
    return f"{int(remaining // 31536000)} г."


def format_timestamp(ts: float) -> str:
    if ts == 0:
        return "—"
    return datetime.datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")


def role_name(role: int) -> str:
    return role_name_by_level(role)


def can_moderate(role: int) -> bool:
    return role >= ROLE_MODERATOR


def can_admin(role: int) -> bool:
    return role >= ROLE_ADMIN


def is_owner(role: int) -> bool:
    return role >= ROLE_OWNER


def format_user_profile(u: dict) -> str:
    lines = [
        f"👤 <b>{escape_html(u.get('first_name', ''))}</b>",
        f"🆔 <code>{u['user_id']}</code>",
    ]
    if u.get("username"):
        lines.append(f"📎 @{u['username']}")
    lines.append(f"🏷 Роль: {role_name(u.get('role', 0))}")
    lines.append(f"💬 Сообщений: {u.get('messages_count', 0)}")
    lines.append(f"⚠️ Варнов: {u.get('warns', 0)}")
    if u.get("is_banned"):
        lines.append(f"🚫 Бан: {format_duration(u.get('ban_until', 0))}")
    if u.get("is_muted"):
        lines.append(f"🔇 Мут: {format_duration(u.get('mute_until', 0))}")
    if u.get("joined_at"):
        lines.append(f"📅 Регистрация: {format_timestamp(u['joined_at'])}")
    if u.get("last_seen"):
        lines.append(f"🕐 Активность: {format_timestamp(u['last_seen'])}")
    return "\n".join(lines)


def format_user_short(u: dict) -> str:
    name = u.get("first_name", "") or u.get("username", "") or str(u["user_id"])
    return f"{escape_html(name)} ({u['user_id']})"


def escape_html(text: str) -> str:
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
