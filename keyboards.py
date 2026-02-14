"""
Клавиатуры — InlineKeyboardMarkup.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config import (
    ROLE_MODERATOR, ROLE_ADMIN, ROLE_OWNER,
    USERS_PER_PAGE, ROLE_NAMES, role_name_by_level,
)
from utils import DURATION_MAP


def interface_choice_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖲 Кнопки", callback_data="set_iface:buttons")],
        [InlineKeyboardButton("⌨️ Команды", callback_data="set_iface:commands")],
    ])


def main_menu_kb(role: int):
    """Главное меню.

    По требованиям:
    - "Модерация", "Админка" и "Пользователи" — это кнопки-меню, а не разделители.
    - Пользователи — отдельная кнопка, не внутри "Модерации".
    """

    rows = [
        [InlineKeyboardButton("👤 Профиль", callback_data="menu:profile")],
        [InlineKeyboardButton("🏆 Топ по сообщениям", callback_data="menu:top")],
        [InlineKeyboardButton("📩 Отправить репорт", callback_data="menu:report")],
    ]

    if role >= ROLE_MODERATOR:
        rows.append([InlineKeyboardButton("🛡 Модерация", callback_data="menu:moderation")])
        rows.append([InlineKeyboardButton("👥 Пользователи", callback_data="menu:users")])

    if role >= ROLE_ADMIN:
        rows.append([InlineKeyboardButton("👑 Админка", callback_data="menu:admin")])

    rows.append([InlineKeyboardButton("⚙️ Настройки", callback_data="menu:settings")])
    return InlineKeyboardMarkup(rows)


def moderation_menu_kb():
    """Меню модерации (всё, что было в "разделе модерации")."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚫 Блокировки", callback_data="menu:bans"),
            InlineKeyboardButton("⚠️ Варны", callback_data="menu:warns"),
        ],
        [
            InlineKeyboardButton("🔇 Муты", callback_data="menu:mutes"),
            InlineKeyboardButton("💬 Чаты", callback_data="menu:chats"),
        ],
        [InlineKeyboardButton("📋 Репорты", callback_data="menu:reports")],
        [InlineKeyboardButton("◀️ Назад", callback_data="menu:main")],
    ])


def admin_menu_kb():
    """Меню админки."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡 Управление ролями", callback_data="menu:roles")],
        [InlineKeyboardButton("◀️ Назад", callback_data="menu:main")],
    ])


def settings_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖲 Кнопки", callback_data="set_iface:buttons"),
         InlineKeyboardButton("⌨️ Команды", callback_data="set_iface:commands")],
        [InlineKeyboardButton("◀️ Назад", callback_data="menu:main")],
    ])


def bans_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔨 Выдать бан", callback_data="act:ban")],
        [InlineKeyboardButton("✅ Снять бан", callback_data="act:unban")],
        [InlineKeyboardButton("🕐 Изменить срок бана", callback_data="act:editban")],
        [InlineKeyboardButton("🌍 Глобальный бан", callback_data="act:globalban")],
        [InlineKeyboardButton("◀️ Назад", callback_data="menu:moderation")],
    ])


def warns_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚠️ Выдать варн", callback_data="act:warn")],
        [InlineKeyboardButton("✅ Снять варны", callback_data="act:unwarn")],
        [InlineKeyboardButton("◀️ Назад", callback_data="menu:moderation")],
    ])


def mutes_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔇 Выдать мут", callback_data="act:mute")],
        [InlineKeyboardButton("🔊 Снять мут", callback_data="act:unmute")],
        [InlineKeyboardButton("🕐 Изменить срок мута", callback_data="act:editmute")],
        [InlineKeyboardButton("◀️ Назад", callback_data="menu:moderation")],
    ])


def duration_kb(action_prefix: str):
    rows = []
    items = list(DURATION_MAP.keys())
    for i in range(0, len(items), 2):
        row = [InlineKeyboardButton(items[i], callback_data=f"dur:{action_prefix}:{items[i]}")]
        if i + 1 < len(items):
            row.append(InlineKeyboardButton(items[i+1], callback_data=f"dur:{action_prefix}:{items[i+1]}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("◀️ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


def users_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Все пользователи", callback_data="users:list:0")],
        [InlineKeyboardButton("🔍 Найти пользователя", callback_data="users:search")],
        [InlineKeyboardButton("🟢 Онлайн", callback_data="users:online")],
        [InlineKeyboardButton("🛡 Стафф онлайн", callback_data="users:staff")],
        [InlineKeyboardButton("◀️ Назад", callback_data="menu:main")],
    ])


def users_list_kb(users: list, page: int, total: int):
    rows = []
    for u in users:
        name = u.get("first_name", "") or u.get("username", "") or str(u["user_id"])
        rows.append([InlineKeyboardButton(
            f"{name} — {u.get('messages_count', 0)} сообщ.",
            callback_data=f"userinfo:{u['user_id']}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"users:list:{page-1}"))
    max_page = max(0, (total - 1) // USERS_PER_PAGE)
    nav.append(InlineKeyboardButton(f"{page+1}/{max_page+1}", callback_data="noop"))
    if page < max_page:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"users:list:{page+1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="menu:users")])
    return InlineKeyboardMarkup(rows)


def chats_list_kb(chats: list):
    rows = []
    for c in chats:
        title = c.get("title", "") or str(c["chat_id"])
        rows.append([InlineKeyboardButton(f"💬 {title}", callback_data=f"chat:{c['chat_id']}")])
    if len(chats) > 1:
        rows.append([InlineKeyboardButton("📢 Все чаты сразу", callback_data="chat:all")])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="menu:moderation")])
    return InlineKeyboardMarkup(rows)


def chat_manage_kb(chat_id, chat_info: dict = None):
    cid = str(chat_id)
    ro = chat_info.get("read_only", 0) if chat_info else 0
    antispam = chat_info.get("antispam", 0) if chat_info else 0
    ai_mod = chat_info.get("ai_moderation", 0) if chat_info else 0

    ro_icon = "🔴" if ro else "🟢"
    sp_icon = "🔴" if antispam else "🟢"
    ai_icon = "🔴" if ai_mod else "🟢"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{ro_icon} Режим чтения", callback_data=f"chtog:readonly:{cid}")],
        [InlineKeyboardButton(f"{sp_icon} Антиспам", callback_data=f"chtog:antispam:{cid}")],
        [InlineKeyboardButton(f"{ai_icon} ИИ-модерация", callback_data=f"chtog:aimod:{cid}")],
        [InlineKeyboardButton("📝 Фильтр слов", callback_data=f"chfilter:{cid}")],
        [InlineKeyboardButton("◀️ Назад", callback_data="menu:chats")],
    ])


def roles_menu_kb():
    assignable = [5, 7, 8, 9]
    rows = []
    for level in assignable:
        rows.append([InlineKeyboardButton(
            f"Назначить: {role_name_by_level(level)}", callback_data=f"setrole:{level}")])
    rows.append([InlineKeyboardButton("Снять все роли", callback_data="setrole:0")])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="menu:admin")])
    return InlineKeyboardMarkup(rows)


def back_to_main_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Главное меню", callback_data="menu:main")]])


def cancel_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="cancel")]])


def report_confirm_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Отправить", callback_data="report:confirm"),
         InlineKeyboardButton("❌ Отмена", callback_data="report:cancel")],
    ])
