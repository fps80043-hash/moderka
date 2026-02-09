"""
🔵 Модерация — v6.1

- /help через интерактивное меню кнопок
- Глобальный бан МГНОВЕННО во всех чатах
- Улучшенный resolve username
- /banlist с пагинацией
- Кнопки только где нужны (выбор срока, подтверждение, help-меню, пагинация)
- Никаких лишних панелей
"""

import asyncio
import json
import logging
import os
import time
import math
from datetime import datetime, timedelta
from typing import Optional, List

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, ChatMemberUpdatedFilter, IS_NOT_MEMBER, IS_MEMBER
from aiogram.types import (
    Message, CallbackQuery, ChatMemberUpdated,
    ChatPermissions, BotCommand, BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats, InlineKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest

from db import Database

# =============================================================================
# КОНФИГУРАЦИЯ
# =============================================================================

CONFIG_FILE = "config.json"
config = {}
if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

BOT_TOKEN = config.get("bot_token", os.getenv("BOT_TOKEN", ""))
MODERATED_CHATS: List[int] = config.get("moderated_chats", [])
PRESET_STAFF: dict = config.get("preset_staff", {})
MAX_WARNS: int = config.get("max_warns", 3)
SPAM_INTERVAL: int = config.get("spam_interval_seconds", 2)
SPAM_COUNT: int = config.get("spam_messages_count", 3)
ANON_ADMIN_ROLE: int = config.get("anon_admin_role", 10)
BANLIST_PER_PAGE = 5

ANONYMOUS_BOT_ID = 1087968824

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

db: Database = None

# =============================================================================
# РОЛИ
# =============================================================================

ROLE_NAMES = {
    0: "Пользователь", 1: "Младший модератор", 2: "Модератор",
    3: "Старший модератор", 4: "Куратор модерации", 5: "Технический специалист",
    6: "Главный тех. специалист", 7: "Куратор групп/каналов",
    8: "Зам. главного модератора", 9: "Главный модератор", 10: "Владелец"
}

MUTE_LIMITS = {1: 3600, 2: 3600, 3: 86400, 4: 86400, 5: 0, 6: 0, 7: 0, 8: 0, 9: 0, 10: 0}


# =============================================================================
# ХЕЛПЕРЫ
# =============================================================================

def is_anon(message) -> bool:
    if hasattr(message, 'from_user') and message.from_user and message.from_user.id == ANONYMOUS_BOT_ID:
        return True
    if hasattr(message, 'sender_chat') and message.sender_chat:
        if hasattr(message, 'chat') and message.sender_chat.id == message.chat.id:
            return True
    return False


def get_args(message: Message, maxsplit: int = -1) -> list:
    if not message.text:
        return []
    text = message.text.strip()
    parts = text.split(maxsplit=1)
    if not parts:
        return []
    cmd = parts[0].split('@')[0]
    clean = cmd + (' ' + parts[1] if len(parts) > 1 else '')
    return clean.split(maxsplit=maxsplit) if maxsplit >= 0 else clean.split()


async def get_caller_role(message: Message) -> int:
    if is_anon(message):
        return ANON_ADMIN_ROLE
    if not message.from_user:
        return 0
    return await get_role(message.from_user.id, message.chat.id)


async def get_caller_id(message: Message) -> int:
    if is_anon(message):
        return 0
    return message.from_user.id if message.from_user else 0


async def get_role(user_id: int, chat_id: int = 0) -> int:
    if user_id == 0 or user_id == ANONYMOUS_BOT_ID:
        return 0
    g = await db.get_global_role(user_id)
    if g > 0:
        return g
    if chat_id:
        return await db.get_user_role(user_id, chat_id)
    return 0


async def get_user_info(user_id: int) -> dict:
    if user_id == 0 or user_id == ANONYMOUS_BOT_ID:
        return {"id": user_id, "username": "", "full_name": "Анонимный администратор"}
    try:
        chat = await bot.get_chat(user_id)
        uname = chat.username or ""
        if uname:
            await db.cache_username(user_id, uname)
        return {"id": user_id, "username": uname, "full_name": chat.full_name or f"User {user_id}"}
    except Exception:
        cached = await db.get_username_by_id(user_id)
        return {
            "id": user_id, "username": cached or "",
            "full_name": f"@{cached}" if cached else f"ID:{user_id}"
        }


async def mention(user_id: int, chat_id: int = 0) -> str:
    if user_id == 0 or user_id == ANONYMOUS_BOT_ID:
        return "<i>Анонимный администратор</i>"
    if chat_id:
        nick = await db.get_nick(user_id, chat_id)
        if nick:
            return f'<a href="tg://user?id={user_id}">{nick}</a>'
    info = await get_user_info(user_id)
    return f'<a href="tg://user?id={user_id}">{info["full_name"]}</a>'


async def resolve_username(username: str) -> Optional[int]:
    username = username.lower().lstrip('@')
    cached = await db.get_user_by_username(username)
    if cached:
        return cached
    try:
        chat = await bot.get_chat(f"@{username}")
        if chat and chat.id:
            await db.cache_username(chat.id, username)
            return chat.id
    except Exception:
        pass
    return None


async def parse_user(message: Message, args: list, start_idx: int = 1) -> Optional[int]:
    """Парсинг target: reply > forward > аргумент (@user / ID / ник)"""
    if message.reply_to_message:
        r = message.reply_to_message
        if r.from_user and not is_anon(r):
            if r.from_user.username:
                await db.cache_username(r.from_user.id, r.from_user.username)
            return r.from_user.id

    if message.forward_from:
        return message.forward_from.id

    if len(args) <= start_idx:
        return None

    arg = args[start_idx].strip()

    if arg.startswith("@"):
        return await resolve_username(arg)

    if arg.isdigit():
        return int(arg)

    nick_user = await db.get_user_by_nick(arg, message.chat.id)
    if nick_user:
        return nick_user

    return await resolve_username(arg)


def fmt_dur(seconds: int) -> str:
    if seconds <= 0:
        return "навсегда"
    if seconds < 60:
        return f"{seconds} сек"
    if seconds < 3600:
        return f"{seconds // 60} мин"
    if seconds < 86400:
        return f"{seconds // 3600} ч"
    return f"{seconds // 86400} дн"


def fmt_ts(ts: int) -> str:
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(ts).strftime('%d.%m.%Y %H:%M')
    except Exception:
        return "—"


# =============================================================================
# PERMISSIONS
# =============================================================================

def muted_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=False, can_send_audios=False, can_send_documents=False,
        can_send_photos=False, can_send_videos=False, can_send_video_notes=False,
        can_send_voice_notes=False, can_send_polls=False, can_send_other_messages=False,
        can_add_web_page_previews=False, can_change_info=False, can_invite_users=False,
        can_pin_messages=False, can_manage_topics=False
    )


def full_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=True, can_send_audios=True, can_send_documents=True,
        can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
        can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
        can_add_web_page_previews=True, can_change_info=False, can_invite_users=True,
        can_pin_messages=False, can_manage_topics=False
    )


# =============================================================================
# КНОПКИ (только необходимые)
# =============================================================================

def kb_duration(action: str, target_id: int, chat_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for label, sec in [("5 мин", 300), ("30 мин", 1800), ("1 час", 3600),
                       ("6 часов", 21600), ("1 день", 86400), ("7 дней", 604800),
                       ("30 дней", 2592000), ("♾ Навсегда", 0)]:
        b.button(text=label, callback_data=f"{action}:{target_id}:{chat_id}:{sec}")
    b.button(text="❌ Отмена", callback_data="cancel:x")
    b.adjust(2, 2, 2, 2, 1)
    return b.as_markup()


def kb_confirm(action: str, data: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Подтвердить", callback_data=f"{action}:{data}")
    b.button(text="❌ Отмена", callback_data="cancel:x")
    b.adjust(2)
    return b.as_markup()


def kb_confirm_gban(target_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Подтвердить глобальный бан", callback_data=f"confirmgban:{target_id}")
    b.button(text="❌ Отмена", callback_data="cancel:x")
    b.adjust(1)
    return b.as_markup()


def kb_banlist_nav(page: int, total_pages: int, mode: str, chat_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if page > 0:
        b.button(text="◀️ Назад", callback_data=f"blist:{mode}:{chat_id}:{page - 1}")
    b.button(text=f"{page + 1}/{total_pages}", callback_data="noop:x")
    if page < total_pages - 1:
        b.button(text="Вперёд ▶️", callback_data=f"blist:{mode}:{chat_id}:{page + 1}")

    if mode == "chat":
        b.button(text="🌐 Глобальные баны", callback_data=f"blist:global:{chat_id}:0")
    else:
        b.button(text="💬 Баны в чате", callback_data=f"blist:chat:{chat_id}:0")

    nav_count = 1 + (1 if page > 0 else 0) + (1 if page < total_pages - 1 else 0)
    b.adjust(nav_count, 1)
    return b.as_markup()


def kb_warnlist_nav(page: int, total_pages: int, chat_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if page > 0:
        b.button(text="◀️ Назад", callback_data=f"wlist:{chat_id}:{page - 1}")
    b.button(text=f"{page + 1}/{total_pages}", callback_data="noop:x")
    if page < total_pages - 1:
        b.button(text="Вперёд ▶️", callback_data=f"wlist:{chat_id}:{page + 1}")
    nav_count = 1 + (1 if page > 0 else 0) + (1 if page < total_pages - 1 else 0)
    b.adjust(nav_count)
    return b.as_markup()


# =============================================================================
# HELP
# =============================================================================

def kb_help_main(role: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if role >= 1:
        b.button(text="⚠️ Варн", callback_data="help:warn")
        b.button(text="✅ Снять варн", callback_data="help:unwarn")
        b.button(text="🔇 Мут", callback_data="help:mute")
        b.button(text="🔊 Размут", callback_data="help:unmute")
        b.button(text="👢 Кик", callback_data="help:kick")
        b.button(text="👁 RO", callback_data="help:ro")
        b.button(text="✍️ Снять RO", callback_data="help:unro")
        b.button(text="📝 Ник", callback_data="help:setnick")
        b.button(text="🧹 Очистка", callback_data="help:clear")
        b.button(text="📋 Варнлист", callback_data="help:warnlist")
    if role >= 3:
        b.button(text="🚫 Бан", callback_data="help:ban")
        b.button(text="✅ Разбан", callback_data="help:unban")
        b.button(text="📋 Банлист", callback_data="help:banlist")
    if role >= 7:
        b.button(text="🌐 Глоб. бан", callback_data="help:gban")
        b.button(text="🌐 Снять глоб.", callback_data="help:ungban")
        b.button(text="⭐ Роль", callback_data="help:setrole")
        b.button(text="❌ Снять роль", callback_data="help:removerole")
    b.button(text="📊 Статистика", callback_data="help:stats")
    b.button(text="👥 Команда", callback_data="help:staff")
    b.adjust(3)
    return b.as_markup()


HELP_TEXTS = {
    "warn": "⚠️ <b>Предупреждение</b>\n\n• Ответ на сообщение + <code>/warn [причина]</code>\n• <code>/warn @user [причина]</code>\n• <code>/warn ID [причина]</code>\n\nПри {max_warns} варнах — автокик.\nРоль: 1+",
    "unwarn": "✅ <b>Снять предупреждение</b>\n\n• Ответ + <code>/unwarn</code>\n• <code>/unwarn @user</code>\n• <code>/unwarn ID</code>\n\nРоль: 1+",
    "mute": "🔇 <b>Замутить</b>\n\n• Ответ + <code>/mute</code>\n• <code>/mute @user</code>\n\nПоявится выбор срока.\nРоль: 1+",
    "unmute": "🔊 <b>Размутить</b>\n\n• <code>/unmute @user</code>\n• Ответ + <code>/unmute</code>\n\nРоль: 1+",
    "kick": "👢 <b>Кикнуть</b>\n\n• <code>/kick @user [причина]</code>\n• Ответ + <code>/kick [причина]</code>\n\nРоль: 1+",
    "ban": "🚫 <b>Забанить</b>\n\n• <code>/ban @user</code>\n• Ответ + <code>/ban</code>\n\nПоявится выбор срока.\nРоль: 3+",
    "unban": "✅ <b>Разбанить</b>\n\n• <code>/unban @user</code>\n• <code>/unban ID</code>\n\nРоль: 3+",
    "banlist": "📋 <b>Список забаненных</b>\n\n• <code>/banlist</code> — баны в текущем чате\n\nПереключение чат / глобальные — кнопками.\nРоль: 3+",
    "gban": "🌐 <b>Глобальный бан</b>\n\n• <code>/gban @user [причина]</code>\n• <code>/gban ID [причина]</code>\n\nБанит СРАЗУ во ВСЕХ чатах!\nРоль: 7+",
    "ungban": "🌐 <b>Снять глобальный бан</b>\n\n• <code>/ungban @user</code>\n• <code>/ungban ID</code>\n\nРоль: 7+",
    "setrole": "⭐ <b>Назначить роль</b>\n\n• <code>/setrole @user ЧИСЛО</code>\n\nРоли: 0-10\nРоль: 7+",
    "removerole": "❌ <b>Снять роль</b>\n\n• <code>/removerole @user</code>\n\nРоль: 7+",
    "ro": "👁 <b>Режим RO</b>\n\n• <code>/ro</code>\n\nОбычные юзеры не могут писать. Staff — могут.\nРоль: 1+",
    "unro": "✍️ <b>Снять RO</b>\n\n• <code>/unro</code>\n\nРоль: 1+",
    "setnick": "📝 <b>Установить ник</b>\n\n• <code>/setnick @user НикВЧате</code>\n\nРоль: 1+",
    "clear": "🧹 <b>Очистить сообщения</b>\n\n• <code>/clear 10</code>\n\nМакс: 100.\nРоль: 1+",
    "warnlist": "📋 <b>Список предупреждений</b>\n\n• <code>/warnlist</code> — варны в текущем чате\n\nПагинация кнопками.\nРоль: 1+",
    "stats": "📊 <b>Статистика</b>\n\n• <code>/stats</code> — ваша\n• <code>/stats @user</code> — чужая\n• Ответ + <code>/stats</code>",
    "staff": "👥 <b>Список команды</b>\n\n• <code>/staff</code>\n\nПоказывает всех с ролью > 0.",
}


# =============================================================================
# ГБАН — МГНОВЕННЫЙ ВО ВСЕХ ЧАТАХ
# =============================================================================

async def enforce_gban_all(user_id: int) -> tuple[int, int]:
    chat_ids = await db.get_all_chat_ids()
    ok, fail = 0, 0
    for cid in chat_ids:
        try:
            await bot.ban_chat_member(cid, user_id)
            await db.add_ban(user_id, cid, 0, "Глобальный бан")
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.1)
    return ok, fail


async def enforce_ungban_all(user_id: int) -> tuple[int, int]:
    chat_ids = await db.get_all_chat_ids()
    ok, fail = 0, 0
    for cid in chat_ids:
        try:
            await bot.unban_chat_member(cid, user_id, only_if_banned=True)
            await db.remove_ban(user_id, cid)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.1)
    return ok, fail


# =============================================================================
# BANLIST — форматирование
# =============================================================================

async def build_banlist_page(mode: str, chat_id: int, page: int) -> tuple[str, int]:
    if mode == "global":
        rows, total = await db.get_all_global_bans_paginated(page, BANLIST_PER_PAGE)
        title = "🌐 <b>Глобальные баны</b>"
    else:
        rows, total = await db.get_all_bans_paginated(page, BANLIST_PER_PAGE, chat_id)
        title = "💬 <b>Баны в чате</b>"

    total_pages = max(1, math.ceil(total / BANLIST_PER_PAGE))

    if not rows:
        return f"{title}\n\nСписок пуст.", total_pages

    text = f"{title}\n\n"
    for i, row in enumerate(rows, start=page * BANLIST_PER_PAGE + 1):
        uid = row['user_id']
        info = await get_user_info(uid)
        name = info['full_name']
        reason = row.get('reason', '—') or '—'
        banned_at = fmt_ts(row.get('banned_at', 0))

        text += f"<b>{i}.</b> <a href=\"tg://user?id={uid}\">{name}</a>\n"
        text += f"    ID: <code>{uid}</code>\n"
        text += f"    Причина: {reason}\n"
        text += f"    Дата: {banned_at}\n"

        if mode != "global":
            until = row.get('until', 0)
            if until and until > 0:
                left = until - int(time.time())
                if left > 0:
                    text += f"    Осталось: {fmt_dur(left)}\n"
                else:
                    text += f"    Срок: истёк\n"
            else:
                text += f"    Срок: навсегда\n"
        text += "\n"

    text += f"📄 Всего: {total}"
    return text, total_pages


async def build_warnlist_page(chat_id: int, page: int) -> tuple[str, int]:
    rows, total = await db.get_all_warns_paginated(page, BANLIST_PER_PAGE, chat_id)
    total_pages = max(1, math.ceil(total / BANLIST_PER_PAGE))
    title = "⚠️ <b>Предупреждения в чате</b>"

    if not rows:
        return f"{title}\n\nСписок пуст.", total_pages

    text = f"{title}\n\n"
    for i, row in enumerate(rows, start=page * BANLIST_PER_PAGE + 1):
        uid = row['user_id']
        info = await get_user_info(uid)
        name = info['full_name']
        count = row.get('count', 0)
        reason = row.get('reason', '—') or '—'
        warned_at = fmt_ts(row.get('warned_at', 0))

        text += f"<b>{i}.</b> <a href=\"tg://user?id={uid}\">{name}</a>\n"
        text += f"    ID: <code>{uid}</code>\n"
        text += f"    Варнов: {count}/{MAX_WARNS}\n"
        text += f"    Причина: {reason}\n"
        text += f"    Дата: {warned_at}\n\n"

    text += f"📄 Всего: {total}"
    return text, total_pages
# =============================================================================

# =============================================================================
# РЕГИСТРАЦИЯ КОМАНД
# =============================================================================

async def register_commands():
    group_cmds = [
        BotCommand(command="help", description="❓ Помощь"),
        BotCommand(command="stats", description="📊 Статистика"),
        BotCommand(command="warn", description="⚠️ Предупреждение"),
        BotCommand(command="unwarn", description="✅ Снять предупреждение"),
        BotCommand(command="mute", description="🔇 Замутить"),
        BotCommand(command="unmute", description="🔊 Размутить"),
        BotCommand(command="ban", description="🚫 Забанить"),
        BotCommand(command="unban", description="✅ Разбанить"),
        BotCommand(command="kick", description="👢 Кикнуть"),
        BotCommand(command="banlist", description="📋 Список забаненных"),
        BotCommand(command="warnlist", description="📋 Список предупреждений"),
        BotCommand(command="ro", description="👁 Режим RO"),
        BotCommand(command="unro", description="✍️ Снять RO"),
        BotCommand(command="setnick", description="📝 Установить ник"),
        BotCommand(command="clear", description="🧹 Очистить сообщения"),
        BotCommand(command="gban", description="🌐 Глобальный бан"),
        BotCommand(command="ungban", description="🌐 Снять глобальный бан"),
        BotCommand(command="setrole", description="⭐ Назначить роль"),
        BotCommand(command="removerole", description="❌ Снять роль"),
        BotCommand(command="staff", description="👥 Список команды"),
    ]
    priv_cmds = [
        BotCommand(command="help", description="❓ Помощь"),
        BotCommand(command="stats", description="📊 Моя статистика"),
    ]
    try:
        await bot.set_my_commands(group_cmds, scope=BotCommandScopeAllGroupChats())
        await bot.set_my_commands(priv_cmds, scope=BotCommandScopeAllPrivateChats())
    except Exception as e:
        logger.error(f"Ошибка регистрации команд: {e}")


async def init_staff():
    if not PRESET_STAFF:
        return
    for uid_str, role in PRESET_STAFF.items():
        try:
            await db.set_global_role(int(uid_str), role)
        except Exception as e:
            logger.error(f"Preset staff {uid_str}: {e}")
    logger.info(f"✅ Preset staff: {len(PRESET_STAFF)}")


# =============================================================================
# КОМАНДЫ
# =============================================================================

@router.message(Command("help"))
async def cmd_help(message: Message):
    role = await get_caller_role(message)
    await message.answer(
        f"📖 <b>Меню команд</b>\n\nВаша роль: <b>{ROLE_NAMES.get(role, '?')} ({role})</b>\n\nВыберите команду:",
        parse_mode="HTML", reply_markup=kb_help_main(role)
    )


@router.callback_query(F.data.startswith("help:"))
async def cb_help(call: CallbackQuery):
    cmd = call.data.split(":", 1)[1]
    if cmd == "back":
        role = await get_role(call.from_user.id,
                              call.message.chat.id if call.message.chat.type != ChatType.PRIVATE else 0)
        try:
            await call.message.edit_text(
                f"📖 <b>Меню команд</b>\n\nВаша роль: <b>{ROLE_NAMES.get(role, '?')} ({role})</b>\n\nВыберите команду:",
                parse_mode="HTML", reply_markup=kb_help_main(role)
            )
        except Exception:
            pass
        return await call.answer()

    text = HELP_TEXTS.get(cmd)
    if not text:
        return await call.answer("❌ Неизвестная команда", show_alert=True)
    text = text.replace("{max_warns}", str(MAX_WARNS))
    b = InlineKeyboardBuilder()
    b.button(text="◀️ Назад", callback_data="help:back")
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except Exception:
        pass
    await call.answer()


# --- /stats ---

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        if not message.from_user:
            return
        uid = message.from_user.id
        role = await get_role(uid)
        is_gb = await db.is_globally_banned(uid)
        return await message.answer(
            f"👤 <b>Ваша информация</b>\n\nID: <code>{uid}</code>\n"
            f"Роль: {ROLE_NAMES.get(role, '?')} ({role})\n"
            f"Глобальный бан: {'✅ Да' if is_gb else '❌ Нет'}",
            parse_mode="HTML"
        )

    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        target = message.from_user.id if message.from_user else None
    if not target:
        return await message.reply("❌ Не удалось определить пользователя")

    text = await build_stats_text(target, message.chat.id)
    await message.answer(text, parse_mode="HTML")


async def build_stats_text(target: int, chat_id: int) -> str:
    info = await get_user_info(target)
    role = await get_role(target, chat_id)
    g_role = await db.get_global_role(target)
    c_role = await db.get_user_role(target, chat_id)
    warns = await db.get_warns(target, chat_id)
    is_muted = await db.is_muted(target, chat_id)
    is_banned = await db.is_banned(target, chat_id)
    is_gb = await db.is_globally_banned(target)
    nick = await db.get_nick(target, chat_id)

    t = f"📊 <b>Статистика</b>\n\nID: <code>{target}</code>\n"
    if info['username']:
        t += f"Username: @{info['username']}\n"
    if nick:
        t += f"Ник: {nick}\n"
    t += (
        f"\n⭐ Глоб. роль: {ROLE_NAMES.get(g_role, '?')} ({g_role})\n"
        f"Роль в чате: {ROLE_NAMES.get(c_role, '?')} ({c_role})\n"
        f"Эффект. роль: {ROLE_NAMES.get(role, '?')} ({role})\n"
        f"\n📋 Варны: {warns}/{MAX_WARNS}\n"
        f"Мут: {'✅' if is_muted else '❌'}"
    )
    if is_muted:
        mi = await db.get_mute_info(target, chat_id)
        if mi:
            until = mi.get('until', 0)
            if until and until > int(time.time()):
                t += f" ({fmt_dur(until - int(time.time()))})"
            else:
                t += " (навсегда)"
            if mi.get('reason'):
                t += f" — {mi['reason']}"
    t += f"\nБан: {'✅' if is_banned else '❌'}"
    if is_banned:
        bi = await db.get_ban_info(target, chat_id)
        if bi and bi.get('reason'):
            t += f" — {bi['reason']}"
    t += f"\nГлоб. бан: {'✅' if is_gb else '❌'}"
    if is_gb:
        gi = await db.get_global_ban_info(target)
        if gi and gi.get('reason'):
            t += f" — {gi['reason']}"
    return t


# --- /warn /unwarn ---

@router.message(Command("warn"))
async def cmd_warn(message: Message):
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав (1+)")
    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)
    if not target:
        return await message.reply(
            "❌ <b>Укажите пользователя:</b>\n"
            "• Ответ на сообщение + <code>/warn [причина]</code>\n"
            "• <code>/warn @user [причина]</code>\n"
            "• <code>/warn ID [причина]</code>\n\n"
            "💡 Если @username не работает — reply или ID!",
            parse_mode="HTML"
        )
    tr = await get_role(target, message.chat.id)
    if tr >= role:
        return await message.reply("❌ Нельзя — роль цели ≥ вашей")
    reason = args[2] if len(args) > 2 else "Нарушение правил"
    caller_id = await get_caller_id(message)
    await db.cache_action(f"warn:{target}:{message.chat.id}", json.dumps({"reason": reason, "caller": caller_id}))
    name = await mention(target, message.chat.id)
    await message.answer(
        f"⚠️ <b>Выдать предупреждение?</b>\n\nКому: {name}\nПричина: {reason}",
        parse_mode="HTML", reply_markup=kb_confirm("confirmwarn", f"{target}:{message.chat.id}")
    )


@router.message(Command("unwarn"))
async def cmd_unwarn(message: Message):
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ Укажите пользователя: /unwarn @user или ответ")
    warns = await db.remove_warn(target, message.chat.id)
    name = await mention(target, message.chat.id)
    await message.answer(f"✅ Варн снят! {name} — {warns}/{MAX_WARNS}", parse_mode="HTML")


# --- /mute /unmute ---

@router.message(Command("mute"))
async def cmd_mute(message: Message):
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ Укажите пользователя: /mute @user или ответ")
    tr = await get_role(target, message.chat.id)
    if tr >= role:
        return await message.reply("❌ Нельзя — роль цели ≥ вашей")
    name = await mention(target, message.chat.id)
    await message.answer(
        f"🔇 <b>Срок мута для</b> {name}:",
        parse_mode="HTML", reply_markup=kb_duration("applymute", target, message.chat.id)
    )


@router.message(Command("unmute"))
async def cmd_unmute(message: Message):
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ Укажите пользователя: /unmute @user")
    try:
        await bot.restrict_chat_member(message.chat.id, target, permissions=full_permissions())
        await db.remove_mute(target, message.chat.id)
        name = await mention(target, message.chat.id)
        await message.answer(f"🔊 {name} размучен!", parse_mode="HTML")
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


# --- /ban /unban ---

@router.message(Command("ban"))
async def cmd_ban(message: Message):
    role = await get_caller_role(message)
    if role < 3:
        return await message.reply("❌ Недостаточно прав (3+)")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ Укажите пользователя: /ban @user или ответ")
    tr = await get_role(target, message.chat.id)
    if tr >= role:
        return await message.reply("❌ Нельзя — роль цели ≥ вашей")
    name = await mention(target, message.chat.id)
    await message.answer(
        f"🚫 <b>Срок бана для</b> {name}:",
        parse_mode="HTML", reply_markup=kb_duration("applyban", target, message.chat.id)
    )


@router.message(Command("unban"))
async def cmd_unban(message: Message):
    role = await get_caller_role(message)
    if role < 3:
        return await message.reply("❌ Недостаточно прав (3+)")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ Укажите пользователя: /unban @user или ID")
    try:
        await bot.unban_chat_member(message.chat.id, target, only_if_banned=True)
        await db.remove_ban(target, message.chat.id)
        name = await mention(target, message.chat.id)
        await message.answer(f"✅ {name} разбанен!", parse_mode="HTML")
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


# --- /kick ---

@router.message(Command("kick"))
async def cmd_kick(message: Message):
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав")
    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ Укажите пользователя: /kick @user [причина]")
    tr = await get_role(target, message.chat.id)
    if tr >= role:
        return await message.reply("❌ Нельзя кикнуть")
    reason = args[2] if len(args) > 2 else "Кик"
    try:
        await bot.ban_chat_member(message.chat.id, target)
        await asyncio.sleep(0.5)
        await bot.unban_chat_member(message.chat.id, target)
        name = await mention(target, message.chat.id)
        await message.answer(f"👢 {name} кикнут!\nПричина: {reason}", parse_mode="HTML")
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


# --- /ro /unro ---

@router.message(Command("ro"))
async def cmd_ro(message: Message):
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав")
    await db.set_ro_mode(message.chat.id, True)
    await message.answer("👁 <b>Режим RO включён!</b>\nОбычные юзеры не могут писать.", parse_mode="HTML")


@router.message(Command("unro"))
async def cmd_unro(message: Message):
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав")
    await db.set_ro_mode(message.chat.id, False)
    await message.answer("✍️ <b>Режим RO выключен!</b>", parse_mode="HTML")


# --- /gban /ungban ---

@router.message(Command("gban"))
async def cmd_gban(message: Message):
    role = await get_caller_role(message)
    if role < 7:
        return await message.reply("❌ Недостаточно прав (7+)")
    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)
    if not target:
        return await message.reply(
            "❌ <b>Укажите пользователя:</b>\n<code>/gban @user [причина]</code>\n<code>/gban ID [причина]</code>",
            parse_mode="HTML"
        )
    tr = await get_role(target)
    if tr >= role:
        return await message.reply(f"❌ Нельзя! Роль цели: {ROLE_NAMES.get(tr)} ({tr})")
    if tr > 0:
        return await message.reply(
            f"⚠️ Этот пользователь в команде ({ROLE_NAMES.get(tr)}).\nСначала: <code>/removerole</code>",
            parse_mode="HTML"
        )
    reason = args[2] if len(args) > 2 else "Глобальный бан"
    caller_id = await get_caller_id(message)
    await db.cache_action(f"gban:{target}", json.dumps({"reason": reason, "caller": caller_id}))
    name = await mention(target)
    await message.answer(
        f"🌐 <b>Подтвердите глобальный бан</b>\n\n"
        f"Кто: {name}\nID: <code>{target}</code>\nПричина: {reason}\n\n"
        f"⚠️ Бан будет применён МГНОВЕННО во всех чатах!",
        parse_mode="HTML", reply_markup=kb_confirm_gban(target)
    )


@router.message(Command("ungban"))
async def cmd_ungban(message: Message):
    role = await get_caller_role(message)
    if role < 7:
        return await message.reply("❌ Недостаточно прав (7+)")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ Укажите пользователя: /ungban @user или ID")
    if not await db.is_globally_banned(target):
        return await message.reply("ℹ️ У этого пользователя нет глобального бана")
    await db.remove_global_ban(target)
    ok, _ = await enforce_ungban_all(target)
    name = await mention(target)
    await message.answer(f"✅ Глобальный бан снят!\n{name}\nРазбанен в {ok} чатах.", parse_mode="HTML")


# --- /setrole /removerole ---

@router.message(Command("setrole"))
async def cmd_setrole(message: Message):
    cr = await get_caller_role(message)
    if cr < 7:
        return await message.reply("❌ Недостаточно прав (7+)")
    args = get_args(message)
    if len(args) < 3:
        roles_text = "\n".join([f"  {k}: {v}" for k, v in ROLE_NAMES.items()])
        return await message.reply(
            f"Использование: <code>/setrole @user ЧИСЛО</code>\n\n<b>Роли:</b>\n{roles_text}",
            parse_mode="HTML"
        )
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ Пользователь не найден")
    try:
        nr = int(args[2])
        if not (0 <= nr <= 10):
            return await message.reply("❌ Роль: 0-10")
    except ValueError:
        return await message.reply("❌ Роль — число 0-10")
    tr = await get_role(target)
    if nr >= cr:
        return await message.reply(f"❌ Нельзя назначить ≥ вашей ({cr})")
    if tr >= cr:
        return await message.reply("❌ Нельзя менять роль этого пользователя")
    await db.set_global_role(target, nr)
    name = await mention(target)
    await message.answer(
        f"⭐ <b>Роль назначена!</b>\n{name}\n{ROLE_NAMES.get(tr,'?')} ({tr}) → {ROLE_NAMES.get(nr,'?')} ({nr})",
        parse_mode="HTML"
    )


@router.message(Command("removerole"))
async def cmd_removerole(message: Message):
    cr = await get_caller_role(message)
    if cr < 7:
        return await message.reply("❌ Недостаточно прав (7+)")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ Укажите пользователя: /removerole @user")
    tr = await get_role(target)
    if tr >= cr:
        return await message.reply("❌ Нельзя снять роль у этого пользователя")
    if tr == 0:
        return await message.reply("ℹ️ У пользователя нет роли")
    await db.set_global_role(target, 0)
    name = await mention(target)
    await message.answer(f"✅ Роль снята!\n{name}\nБыла: {ROLE_NAMES.get(tr,'?')} ({tr})", parse_mode="HTML")


# --- /staff ---

@router.message(Command("staff"))
async def cmd_staff(message: Message):
    staff = await db.get_all_staff()
    if not staff:
        return await message.answer("ℹ️ Список команды пуст")
    by_role: dict[int, list] = {}
    for uid, r in staff:
        by_role.setdefault(r, []).append(uid)
    text = "👥 <b>Команда модерации</b>\n\n"
    for r in sorted(by_role.keys(), reverse=True):
        text += f"<b>{ROLE_NAMES.get(r, '?')} ({r}):</b>\n"
        for uid in by_role[r]:
            text += f"  • {await mention(uid)}\n"
        text += "\n"
    await message.answer(text, parse_mode="HTML")


# --- /setnick ---

@router.message(Command("setnick"))
async def cmd_setnick(message: Message):
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав")
    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)
    if not target or len(args) < 3:
        return await message.reply("❌ Использование: /setnick @user НикВЧате")
    nick = args[2]
    await db.set_nick(target, message.chat.id, nick)
    name = await mention(target, message.chat.id)
    await message.answer(f"📝 Ник установлен! {name} → {nick}", parse_mode="HTML")


# --- /clear ---

@router.message(Command("clear"))
async def cmd_clear(message: Message):
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав")
    args = get_args(message)
    if len(args) < 2:
        return await message.reply("❌ Использование: /clear <число>")
    try:
        count = int(args[1])
        if not (1 <= count <= 100):
            return await message.reply("❌ Число: 1-100")
    except ValueError:
        return await message.reply("❌ Число: 1-100")

    deleted = 0
    mid = message.message_id
    for i in range(1, count + 1):
        try:
            await bot.delete_message(message.chat.id, mid - i)
            deleted += 1
        except Exception:
            pass
        if i % 5 == 0:
            await asyncio.sleep(0.3)
    try:
        st = await message.answer(f"🧹 Очищено {deleted}/{count}")
        await asyncio.sleep(3)
        await st.delete()
        await message.delete()
    except Exception:
        pass


# --- /banlist ---

@router.message(Command("banlist"))
async def cmd_banlist(message: Message):
    role = await get_caller_role(message)
    if role < 3:
        return await message.reply("❌ Недостаточно прав (3+)")
    chat_id = message.chat.id
    text, total_pages = await build_banlist_page("chat", chat_id, 0)
    markup = kb_banlist_nav(0, total_pages, "chat", chat_id)
    await message.answer(text, parse_mode="HTML", reply_markup=markup)


@router.message(Command("warnlist"))
async def cmd_warnlist(message: Message):
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав (1+)")
    chat_id = message.chat.id
    text, total_pages = await build_warnlist_page(chat_id, 0)
    markup = kb_warnlist_nav(0, total_pages, chat_id)
    await message.answer(text, parse_mode="HTML", reply_markup=markup)


# =============================================================================
# CALLBACKS
# =============================================================================

@router.callback_query(F.data.startswith("confirmwarn:"))
async def cb_confirm_warn(call: CallbackQuery):
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    role = await get_role(call.from_user.id, chat_id)
    if role < 1:
        return await call.answer("❌ Нет прав!", show_alert=True)
    tr = await get_role(target, chat_id)
    if tr >= role:
        return await call.answer("❌ Нельзя варнить!", show_alert=True)

    raw = await db.get_cached_action(f"warn:{target}:{chat_id}")
    data = json.loads(raw) if raw else {}
    reason = data.get("reason", "Нарушение правил")
    caller = data.get("caller", call.from_user.id)

    warns = await db.add_warn(target, chat_id, caller, reason)
    name = await mention(target, chat_id)

    if warns >= MAX_WARNS:
        try:
            await bot.ban_chat_member(chat_id, target)
            await asyncio.sleep(0.5)
            await bot.unban_chat_member(chat_id, target)
        except Exception as e:
            logger.error(f"Kick after warns: {e}")
        await db.clear_warns(target, chat_id)
        await call.message.edit_text(
            f"⚠️ {name} — предупреждение!\nПричина: {reason}\n\n👢 <b>Кикнут за {MAX_WARNS} варнов!</b>",
            parse_mode="HTML"
        )
    else:
        await call.message.edit_text(
            f"⚠️ {name} — предупреждение!\nПричина: {reason}\nВарнов: {warns}/{MAX_WARNS}",
            parse_mode="HTML"
        )
    await call.answer("✅ Варн выдан!")
    await db.clear_cached_action(f"warn:{target}:{chat_id}")


@router.callback_query(F.data.startswith("confirmgban:"))
async def cb_confirm_gban(call: CallbackQuery):
    target = int(call.data.split(":")[1])
    role = await get_role(call.from_user.id)
    if role < 7:
        return await call.answer("❌ Нет прав! (7+)", show_alert=True)
    tr = await get_role(target)
    if tr >= role or tr > 0:
        return await call.answer("❌ Нельзя забанить!", show_alert=True)

    raw = await db.get_cached_action(f"gban:{target}")
    data = json.loads(raw) if raw else {}
    reason = data.get("reason", "Глобальный бан")
    caller = data.get("caller", call.from_user.id)

    await db.add_global_ban(target, caller, reason)
    name = await mention(target)

    await call.message.edit_text(
        f"🌐 <b>Применяю глобальный бан...</b>\n{name}\n⏳ Баню во всех чатах...",
        parse_mode="HTML"
    )
    ok, fail = await enforce_gban_all(target)
    await call.message.edit_text(
        f"🌐 <b>Глобальный бан применён!</b>\n\n"
        f"{name}\nID: <code>{target}</code>\nПричина: {reason}\n\n"
        f"✅ Забанен в {ok} чатах" + (f" | ⚠️ {fail} неудач" if fail else ""),
        parse_mode="HTML"
    )
    await call.answer("✅ Глобальный бан!", show_alert=True)
    await db.clear_cached_action(f"gban:{target}")


@router.callback_query(F.data.startswith("applymute:"))
async def cb_apply_mute(call: CallbackQuery):
    parts = call.data.split(":")
    target, chat_id, seconds = int(parts[1]), int(parts[2]), int(parts[3])
    role = await get_role(call.from_user.id, chat_id)
    if role < 1:
        return await call.answer("❌ Нет прав!", show_alert=True)
    tr = await get_role(target, chat_id)
    if tr >= role:
        return await call.answer("❌ Нельзя замутить!", show_alert=True)
    limit = MUTE_LIMITS.get(role, 0)
    if limit > 0 and (seconds == 0 or seconds > limit):
        return await call.answer(f"❌ Лимит: {fmt_dur(limit)}", show_alert=True)
    try:
        until = int(time.time()) + seconds if seconds > 0 else 0
        delta = timedelta(seconds=seconds) if seconds > 0 else None
        await bot.restrict_chat_member(chat_id, target, permissions=muted_permissions(), until_date=delta)
        await db.add_mute(target, chat_id, call.from_user.id, "Мут", until)
        name = await mention(target, chat_id)
        await call.message.edit_text(f"🔇 {name} замучен на {fmt_dur(seconds)}", parse_mode="HTML")
        await call.answer("✅ Мут!")
    except Exception as e:
        await call.answer(f"❌ {e}", show_alert=True)


@router.callback_query(F.data.startswith("applyban:"))
async def cb_apply_ban(call: CallbackQuery):
    parts = call.data.split(":")
    target, chat_id, seconds = int(parts[1]), int(parts[2]), int(parts[3])
    role = await get_role(call.from_user.id, chat_id)
    if role < 3:
        return await call.answer("❌ Нет прав! (3+)", show_alert=True)
    tr = await get_role(target, chat_id)
    if tr >= role:
        return await call.answer("❌ Нельзя забанить!", show_alert=True)
    try:
        delta = timedelta(seconds=seconds) if seconds > 0 else None
        until = int(time.time()) + seconds if seconds > 0 else 0
        await bot.ban_chat_member(chat_id, target, until_date=delta)
        await db.add_ban(target, chat_id, call.from_user.id, "Бан", until)
        name = await mention(target, chat_id)
        await call.message.edit_text(f"🚫 {name} забанен на {fmt_dur(seconds)}", parse_mode="HTML")
        await call.answer("✅ Бан!")
    except Exception as e:
        await call.answer(f"❌ {e}", show_alert=True)


@router.callback_query(F.data.startswith("blist:"))
async def cb_banlist_page(call: CallbackQuery):
    parts = call.data.split(":")
    mode, chat_id, page = parts[1], int(parts[2]), int(parts[3])
    role = await get_role(call.from_user.id, chat_id)
    if role < 3:
        return await call.answer("❌ Нет прав!", show_alert=True)
    text, total_pages = await build_banlist_page(mode, chat_id, page)
    markup = kb_banlist_nav(page, total_pages, mode, chat_id)
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    except Exception:
        pass
    await call.answer()


@router.callback_query(F.data.startswith("wlist:"))
async def cb_warnlist_page(call: CallbackQuery):
    parts = call.data.split(":")
    chat_id, page = int(parts[1]), int(parts[2])
    role = await get_role(call.from_user.id, chat_id)
    if role < 1:
        return await call.answer("❌ Нет прав!", show_alert=True)
    text, total_pages = await build_warnlist_page(chat_id, page)
    markup = kb_warnlist_nav(page, total_pages, chat_id)
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    except Exception:
        pass
    await call.answer()


@router.callback_query(F.data.startswith("noop:"))
async def cb_noop(call: CallbackQuery):
    await call.answer()


@router.callback_query(F.data.startswith("cancel:"))
async def cb_cancel(call: CallbackQuery):
    try:
        await call.message.edit_text("❌ Действие отменено")
    except Exception:
        pass
    await call.answer("Отменено")


# =============================================================================
# ХЕНДЛЕРЫ СОБЫТИЙ
# =============================================================================

@router.chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def on_user_join(event: ChatMemberUpdated):
    uid = event.new_chat_member.user.id
    cid = event.chat.id

    if event.new_chat_member.user.username:
        await db.cache_username(uid, event.new_chat_member.user.username)

    if await db.is_globally_banned(uid):
        try:
            await bot.ban_chat_member(cid, uid)
            name = await mention(uid)
            await bot.send_message(cid, f"🚫 {name} — глобальный бан, удалён.", parse_mode="HTML")
        except Exception as e:
            logger.error(f"gban on join {uid}: {e}")
        return

    welcome = await db.get_welcome(cid)
    if welcome:
        await bot.send_message(cid, welcome.replace("{user}", event.new_chat_member.user.full_name or ""))


@router.message(F.text)
async def on_message(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        return
    if not message.from_user:
        return

    uid = message.from_user.id
    cid = message.chat.id

    if message.from_user.username:
        await db.cache_username(uid, message.from_user.username)

    role = await get_role(uid, cid)

    # Глобальный бан
    if await db.is_globally_banned(uid):
        try:
            await bot.ban_chat_member(cid, uid)
            await message.delete()
            name = await mention(uid)
            await bot.send_message(cid, f"🚫 {name} — глобальный бан!", parse_mode="HTML")
        except Exception:
            pass
        return

    # RO
    if role < 1 and await db.is_ro_mode(cid):
        try:
            await message.delete()
        except Exception:
            pass
        return

    # Антифлуд
    if role < 1 and await db.is_antiflood(cid):
        spam = await db.check_spam(uid, cid, time.time(), SPAM_INTERVAL)
        if spam >= SPAM_COUNT:
            try:
                await db.clear_spam(uid, cid)
                until = int(time.time()) + 1800
                await db.add_mute(uid, cid, 0, "Антиспам", until)
                await bot.restrict_chat_member(cid, uid, permissions=muted_permissions(),
                                               until_date=timedelta(minutes=30))
                await message.delete()
                name = await mention(uid)
                await bot.send_message(cid, f"🔇 {name} замучен на 30 мин (антиспам)", parse_mode="HTML")
            except Exception:
                pass
            return

    # Фильтр слов
    if role < 1 and message.text and await db.is_filter(cid):
        words = await db.get_banwords(cid)
        low = message.text.lower()
        for w in words:
            if w in low:
                try:
                    await message.delete()
                    until = int(time.time()) + 1800
                    await db.add_mute(uid, cid, 0, "Запрещённое слово", until)
                    await bot.restrict_chat_member(cid, uid, permissions=muted_permissions(),
                                                   until_date=timedelta(minutes=30))
                    name = await mention(uid)
                    await bot.send_message(cid, f"🔇 {name} замучен (запрещённое слово)", parse_mode="HTML")
                except Exception:
                    pass
                return


# =============================================================================
# ЗАПУСК
# =============================================================================

async def periodic_cleanup():
    while True:
        await asyncio.sleep(3600)
        try:
            await db.cleanup_old_cache(3600)
        except Exception:
            pass


async def main():
    global db
    db = Database("database.db")
    await db.init()

    logger.info("🔵 Модерация v6.1")
    await init_staff()

    for cid in MODERATED_CHATS:
        try:
            chat = await bot.get_chat(cid)
            await db.register_chat(cid, chat.title or "")
            logger.info(f"Чат: {cid} ({chat.title})")
        except Exception as e:
            logger.warning(f"Чат {cid}: {e}")

    await register_commands()
    asyncio.create_task(periodic_cleanup())
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("✅ Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
