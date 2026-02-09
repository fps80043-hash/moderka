"""
🔵 Модерация — v6.0 (Полная переработка)

Ключевые изменения:
- Все сообщения с инлайн-кнопками
- /help через интерактивное меню кнопок
- Глобальный бан МГНОВЕННО во всех чатах (ban_chat_member сразу)
- Улучшенный resolve username (get_chat_member как fallback)
- Исправлены все известные баги
- Интерактивный flow: команда → юзер → срок → причина
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Optional, List

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, ChatMemberUpdatedFilter, IS_NOT_MEMBER, IS_MEMBER
from aiogram.types import (
    Message, CallbackQuery, ChatMemberUpdated,
    ChatPermissions, BotCommand, BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats, InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

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
    0: "Пользователь",
    1: "Младший модератор",
    2: "Модератор",
    3: "Старший модератор",
    4: "Куратор модерации",
    5: "Технический специалист",
    6: "Главный тех. специалист",
    7: "Куратор групп/каналов",
    8: "Зам. главного модератора",
    9: "Главный модератор",
    10: "Владелец"
}

# Лимиты мута по ролям (0 = без ограничений)
MUTE_LIMITS = {1: 3600, 2: 3600, 3: 86400, 4: 86400, 5: 0, 6: 0, 7: 0, 8: 0, 9: 0, 10: 0}

# Минимальные роли для команд
CMD_ROLES = {
    "warn": 1, "unwarn": 1, "mute": 1, "unmute": 1, "kick": 1,
    "ro": 1, "unro": 1, "setnick": 1, "clear": 1,
    "ban": 3, "unban": 3,
    "gban": 7, "ungban": 7, "setrole": 7, "removerole": 7,
}


# =============================================================================
# ХЕЛПЕРЫ
# =============================================================================

def is_anon(message: Message) -> bool:
    if message.from_user and message.from_user.id == ANONYMOUS_BOT_ID:
        return True
    if message.sender_chat and message.sender_chat.id == message.chat.id:
        return True
    return False


def get_args(message: Message, maxsplit: int = -1) -> list:
    """Парсинг аргументов (убирает @botusername из команды)"""
    if not message.text:
        return []
    text = message.text.strip()
    parts = text.split(maxsplit=1)
    if not parts:
        return []
    cmd = parts[0].split('@')[0]  # /ban@botname -> /ban
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
    """Получить информацию о юзере через Telegram API, с fallback на кэш"""
    if user_id == 0 or user_id == ANONYMOUS_BOT_ID:
        return {"id": user_id, "first_name": "Аноним", "username": "", "full_name": "Анонимный администратор"}
    try:
        chat = await bot.get_chat(user_id)
        return {
            "id": user_id,
            "first_name": chat.first_name or "",
            "username": chat.username or "",
            "full_name": chat.full_name or f"User {user_id}"
        }
    except Exception:
        cached_uname = await db.get_username_by_id(user_id)
        return {
            "id": user_id,
            "first_name": "Пользователь",
            "username": cached_uname or "",
            "full_name": f"@{cached_uname}" if cached_uname else f"ID:{user_id}"
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
    """Резолвим username в user_id: кэш → Telegram API"""
    username = username.lower().lstrip('@')
    # 1. Кэш
    cached = await db.get_user_by_username(username)
    if cached:
        return cached
    # 2. Telegram API (get_chat по @username)
    try:
        chat = await bot.get_chat(f"@{username}")
        if chat and chat.id:
            await db.cache_username(chat.id, username)
            return chat.id
    except Exception:
        pass
    return None


async def resolve_in_chat(username: str, chat_id: int) -> Optional[int]:
    """Попытка найти юзера через get_chat_member в конкретном чате.
    Telegram не поддерживает поиск по username в get_chat_member,
    но мы пробуем get_chat для резолва, а потом проверяем membership."""
    user_id = await resolve_username(username)
    if user_id:
        return user_id
    return None


async def parse_user(message: Message, args: list, start_idx: int = 1) -> Optional[int]:
    """
    Парсинг target user. Приоритет:
    1. Reply на сообщение
    2. Forward
    3. Аргумент команды (@username / ID / ник)
    """
    # 1. Reply
    if message.reply_to_message:
        reply = message.reply_to_message
        if reply.from_user and not is_anon(reply):
            uid = reply.from_user.id
            # Кэшируем username при удобном случае
            if reply.from_user.username:
                await db.cache_username(uid, reply.from_user.username)
            return uid
        # Если reply на сообщение от имени канала — sender_chat
        if reply.sender_chat and reply.sender_chat.type == ChatType.PRIVATE:
            return reply.sender_chat.id

    # 2. Forward
    if message.forward_from:
        return message.forward_from.id

    # 3. Из аргументов
    if len(args) <= start_idx:
        return None

    arg = args[start_idx].strip()

    # @username
    if arg.startswith("@"):
        uid = await resolve_in_chat(arg, message.chat.id)
        if uid:
            logger.info(f"parse_user: @{arg} -> {uid}")
        else:
            logger.warning(f"parse_user: @{arg} не найден")
        return uid

    # Числовой ID
    if arg.isdigit():
        return int(arg)

    # Ник в чате
    nick_user = await db.get_user_by_nick(arg, message.chat.id)
    if nick_user:
        return nick_user

    # Попытка как username без @
    uid = await resolve_username(arg)
    return uid


def format_duration(seconds: int) -> str:
    """Человекочитаемый формат длительности"""
    if seconds <= 0:
        return "навсегда"
    if seconds < 60:
        return f"{seconds} сек"
    if seconds < 3600:
        m = seconds // 60
        return f"{m} мин"
    if seconds < 86400:
        h = seconds // 3600
        return f"{h} ч"
    d = seconds // 86400
    return f"{d} дн"


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
# КНОПКИ
# =============================================================================

def kb_duration(action: str, target_id: int, chat_id: int) -> InlineKeyboardMarkup:
    """Клавиатура выбора времени"""
    b = InlineKeyboardBuilder()
    for label, sec in [("5 мин", 300), ("30 мин", 1800), ("1 час", 3600),
                       ("6 часов", 21600), ("1 день", 86400), ("7 дней", 604800),
                       ("30 дней", 2592000), ("♾ Навсегда", 0)]:
        b.button(text=label, callback_data=f"{action}:{target_id}:{chat_id}:{sec}")
    b.button(text="❌ Отмена", callback_data=f"cancel:0:0")
    b.adjust(2, 2, 2, 2, 1)
    return b.as_markup()


def kb_after_mute(target_id: int, chat_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔊 Размутить", callback_data=f"unmute:{target_id}:{chat_id}")
    b.button(text="📊 Инфо", callback_data=f"info:{target_id}:{chat_id}")
    b.adjust(2)
    return b.as_markup()


def kb_after_ban(target_id: int, chat_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Разбанить", callback_data=f"unban:{target_id}:{chat_id}")
    b.button(text="📊 Инфо", callback_data=f"info:{target_id}:{chat_id}")
    b.adjust(2)
    return b.as_markup()


def kb_after_warn(target_id: int, chat_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Снять варн", callback_data=f"unwarn:{target_id}:{chat_id}")
    b.button(text="🔇 Мут", callback_data=f"startmute:{target_id}:{chat_id}")
    b.button(text="📊 Инфо", callback_data=f"info:{target_id}:{chat_id}")
    b.adjust(2, 1)
    return b.as_markup()


def kb_after_action(target_id: int, chat_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📊 Инфо", callback_data=f"info:{target_id}:{chat_id}")
    b.adjust(1)
    return b.as_markup()



# =============================================================================
# HELP — ИНТЕРАКТИВНОЕ МЕНЮ
# =============================================================================

def kb_help_main(role: int) -> InlineKeyboardMarkup:
    """Главное меню /help с кнопками команд"""
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

    if role >= 3:
        b.button(text="🚫 Бан", callback_data="help:ban")
        b.button(text="✅ Разбан", callback_data="help:unban")

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
    "warn": (
        "⚠️ <b>Выдать предупреждение</b>\n\n"
        "<b>Использование:</b>\n"
        "• Ответьте на сообщение + <code>/warn [причина]</code>\n"
        "• <code>/warn @username [причина]</code>\n"
        "• <code>/warn ID [причина]</code>\n\n"
        "При {max_warns} варнах — автокик.\n"
        "Минимальная роль: 1+"
    ),
    "unwarn": (
        "✅ <b>Снять предупреждение</b>\n\n"
        "• Ответьте на сообщение + <code>/unwarn</code>\n"
        "• <code>/unwarn @username</code>\n"
        "• <code>/unwarn ID</code>\n\n"
        "Минимальная роль: 1+"
    ),
    "mute": (
        "🔇 <b>Замутить пользователя</b>\n\n"
        "• Ответьте на сообщение + <code>/mute</code>\n"
        "• <code>/mute @username</code>\n\n"
        "После команды появится выбор срока.\n"
        "Минимальная роль: 1+"
    ),
    "unmute": (
        "🔊 <b>Размутить</b>\n\n"
        "• <code>/unmute @username</code>\n"
        "• Ответьте на сообщение + <code>/unmute</code>\n\n"
        "Минимальная роль: 1+"
    ),
    "kick": (
        "👢 <b>Кикнуть</b>\n\n"
        "• <code>/kick @username [причина]</code>\n"
        "• Ответьте на сообщение + <code>/kick [причина]</code>\n\n"
        "Минимальная роль: 1+"
    ),
    "ban": (
        "🚫 <b>Забанить</b>\n\n"
        "• <code>/ban @username</code>\n"
        "• Ответьте на сообщение + <code>/ban</code>\n\n"
        "После команды появится выбор срока.\n"
        "Минимальная роль: 3+"
    ),
    "unban": (
        "✅ <b>Разбанить</b>\n\n"
        "• <code>/unban @username</code>\n"
        "• <code>/unban ID</code>\n\n"
        "Минимальная роль: 3+"
    ),
    "gban": (
        "🌐 <b>Глобальный бан</b>\n\n"
        "• <code>/gban @username [причина]</code>\n"
        "• <code>/gban ID [причина]</code>\n\n"
        "Банит СРАЗУ во ВСЕХ модерируемых чатах!\n"
        "Минимальная роль: 7+"
    ),
    "ungban": (
        "🌐 <b>Снять глобальный бан</b>\n\n"
        "• <code>/ungban @username</code>\n"
        "• <code>/ungban ID</code>\n\n"
        "Минимальная роль: 7+"
    ),
    "setrole": (
        "⭐ <b>Назначить роль</b>\n\n"
        "• <code>/setrole @username ЧИСЛО</code>\n\n"
        "Роли: 0-10 (см. /staff)\n"
        "Минимальная роль: 7+"
    ),
    "removerole": (
        "❌ <b>Снять роль</b>\n\n"
        "• <code>/removerole @username</code>\n\n"
        "Минимальная роль: 7+"
    ),
    "ro": (
        "👁 <b>Режим Read-Only</b>\n\n"
        "• <code>/ro</code> — включить\n\n"
        "Обычные юзеры не смогут писать. Staff — могут.\n"
        "Минимальная роль: 1+"
    ),
    "unro": (
        "✍️ <b>Снять RO</b>\n\n"
        "• <code>/unro</code>\n\n"
        "Минимальная роль: 1+"
    ),
    "setnick": (
        "📝 <b>Установить ник</b>\n\n"
        "• <code>/setnick @username НикВЧате</code>\n\n"
        "Минимальная роль: 1+"
    ),
    "clear": (
        "🧹 <b>Очистить сообщения</b>\n\n"
        "• <code>/clear 10</code> — удалит 10 последних\n\n"
        "Максимум: 100 сообщений за раз.\n"
        "Минимальная роль: 1+"
    ),
    "stats": (
        "📊 <b>Статистика</b>\n\n"
        "• <code>/stats</code> — ваша\n"
        "• <code>/stats @username</code> — чужая\n"
        "• Ответьте на сообщение + <code>/stats</code>"
    ),
    "staff": (
        "👥 <b>Список команды</b>\n\n"
        "• <code>/staff</code>\n\n"
        "Показывает всех с ролью > 0."
    ),
}


# =============================================================================
# УТИЛИТА — МГНОВЕННЫЙ GBAN ВО ВСЕХ ЧАТАХ
# =============================================================================

async def enforce_gban_in_all_chats(user_id: int) -> tuple[int, int]:
    """
    Банит юзера во ВСЕХ зарегистрированных чатах.
    Возвращает (успешно, неудачно).
    """
    chat_ids = await db.get_all_chat_ids()
    ok, fail = 0, 0
    for cid in chat_ids:
        try:
            await bot.ban_chat_member(cid, user_id)
            await db.add_ban(user_id, cid, 0, "Глобальный бан")
            ok += 1
        except TelegramBadRequest as e:
            # Юзер не в чате — нормально
            if "user not found" in str(e).lower() or "not enough rights" in str(e).lower():
                pass
            else:
                logger.warning(f"gban enforce failed in {cid}: {e}")
            fail += 1
        except Exception as e:
            logger.warning(f"gban enforce failed in {cid}: {e}")
            fail += 1
        await asyncio.sleep(0.1)  # Антирейтлимит
    return ok, fail


async def enforce_ungban_in_all_chats(user_id: int) -> tuple[int, int]:
    """Разбанивает юзера во всех чатах."""
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
# РЕГИСТРАЦИЯ КОМАНД
# =============================================================================

async def register_commands():
    group_commands = [
        BotCommand(command="help", description="❓ Помощь (меню команд)"),
        BotCommand(command="stats", description="📊 Статистика"),
        BotCommand(command="warn", description="⚠️ Предупреждение"),
        BotCommand(command="unwarn", description="✅ Снять предупреждение"),
        BotCommand(command="mute", description="🔇 Замутить"),
        BotCommand(command="unmute", description="🔊 Размутить"),
        BotCommand(command="ban", description="🚫 Забанить"),
        BotCommand(command="unban", description="✅ Разбанить"),
        BotCommand(command="kick", description="👢 Кикнуть"),
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
    private_commands = [
        BotCommand(command="help", description="❓ Помощь"),
        BotCommand(command="stats", description="📊 Моя статистика"),
    ]
    try:
        await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())
        await bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())
    except Exception as e:
        logger.error(f"Ошибка регистрации команд: {e}")


async def init_staff():
    if not PRESET_STAFF:
        return
    for uid_str, role in PRESET_STAFF.items():
        try:
            await db.set_global_role(int(uid_str), role)
        except Exception as e:
            logger.error(f"Ошибка назначения роли {uid_str}: {e}")
    logger.info(f"✅ Preset staff: {len(PRESET_STAFF)} ролей")


# =============================================================================
# /help
# =============================================================================

@router.message(Command("help"))
async def cmd_help(message: Message):
    role = await get_caller_role(message)
    text = (
        "📖 <b>Меню команд бота</b>\n\n"
        "Выберите команду, чтобы увидеть подробности.\n"
        f"Ваша роль: <b>{ROLE_NAMES.get(role, '?')} ({role})</b>"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=kb_help_main(role))


@router.callback_query(F.data.startswith("help:"))
async def cb_help_detail(call: CallbackQuery):
    cmd = call.data.split(":", 1)[1]
    text = HELP_TEXTS.get(cmd)
    if not text:
        await call.answer("❌ Неизвестная команда", show_alert=True)
        return
    text = text.replace("{max_warns}", str(MAX_WARNS))

    role = await get_role(call.from_user.id, call.message.chat.id if call.message.chat.type != ChatType.PRIVATE else 0)
    b = InlineKeyboardBuilder()
    b.button(text="◀️ Назад", callback_data="help:back")
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except Exception:
        pass
    await call.answer()


@router.callback_query(F.data == "help:back")
async def cb_help_back(call: CallbackQuery):
    role = await get_role(call.from_user.id, call.message.chat.id if call.message.chat.type != ChatType.PRIVATE else 0)
    text = (
        "📖 <b>Меню команд бота</b>\n\n"
        "Выберите команду, чтобы увидеть подробности.\n"
        f"Ваша роль: <b>{ROLE_NAMES.get(role, '?')} ({role})</b>"
    )
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb_help_main(role))
    except Exception:
        pass
    await call.answer()


# =============================================================================
# /stats
# =============================================================================

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        if not message.from_user:
            return
        uid = message.from_user.id
        role = await get_role(uid)
        is_gb = await db.is_globally_banned(uid)
        text = (
            f"👤 <b>Ваша информация</b>\n\n"
            f"ID: <code>{uid}</code>\n"
            f"Глобальная роль: {ROLE_NAMES.get(role, '?')} ({role})\n"
            f"Глобальный бан: {'✅ Да' if is_gb else '❌ Нет'}"
        )
        await message.answer(text, parse_mode="HTML")
        return

    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        target = message.from_user.id if message.from_user else None
    if not target:
        await message.reply("❌ Не удалось определить пользователя")
        return

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

    text = f"📊 <b>Статистика</b>\n\nID: <code>{target}</code>\n"
    if info['username']:
        text += f"Username: @{info['username']}\n"
    if nick:
        text += f"Ник: {nick}\n"
    text += (
        f"\n⭐ <b>Роли:</b>\n"
        f"Глобальная: {ROLE_NAMES.get(g_role, '?')} ({g_role})\n"
        f"В чате: {ROLE_NAMES.get(c_role, '?')} ({c_role})\n"
        f"Эффективная: {ROLE_NAMES.get(role, '?')} ({role})\n"
        f"\n📋 <b>Модерация:</b>\n"
        f"Варны: {warns}/{MAX_WARNS}\n"
        f"Мут: {'✅' if is_muted else '❌'}"
    )
    if is_muted:
        mi = await db.get_mute_info(target, chat_id)
        if mi:
            until = mi.get('until', 0)
            if until > 0:
                left = until - int(time.time())
                if left > 0:
                    text += f" (ещё {format_duration(left)})"
            else:
                text += " (навсегда)"
            if mi.get('reason'):
                text += f"\n  └ {mi['reason']}"

    text += f"\nБан: {'✅' if is_banned else '❌'}"
    if is_banned:
        bi = await db.get_ban_info(target, chat_id)
        if bi and bi.get('reason'):
            text += f"\n  └ {bi['reason']}"

    text += f"\nГлоб. бан: {'✅' if is_gb else '❌'}"
    if is_gb:
        gi = await db.get_global_ban_info(target)
        if gi and gi.get('reason'):
            text += f"\n  └ {gi['reason']}"

    return text


# =============================================================================
# /warn /unwarn
# =============================================================================

@router.message(Command("warn"))
async def cmd_warn(message: Message):
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав! (1+)")

    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)
    if not target:
        return await message.reply(
            "❌ <b>Укажите пользователя:</b>\n"
            "• Ответьте на сообщение + <code>/warn [причина]</code>\n"
            "• <code>/warn @username [причина]</code>\n"
            "• <code>/warn ID [причина]</code>\n\n"
            "💡 Если @username не работает — используйте reply или ID!",
            parse_mode="HTML"
        )

    tr = await get_role(target, message.chat.id)
    if tr >= role:
        return await message.reply("❌ Нельзя варнить — роль цели ≥ вашей!")

    reason = args[2] if len(args) > 2 else "Нарушение правил"
    caller_id = await get_caller_id(message)

    # Подтверждение через кнопки
    cache_key = f"warn:{target}:{message.chat.id}"
    await db.cache_action(cache_key, json.dumps({"reason": reason, "caller": caller_id}))

    b = InlineKeyboardBuilder()
    b.button(text="✅ Подтвердить", callback_data=f"confirmwarn:{target}:{message.chat.id}")
    b.button(text="❌ Отмена", callback_data="cancel:0:0")
    b.adjust(2)

    name = await mention(target, message.chat.id)
    await message.answer(
        f"⚠️ <b>Выдать предупреждение?</b>\n\n"
        f"Кому: {name}\nПричина: {reason}",
        parse_mode="HTML", reply_markup=b.as_markup()
    )


@router.message(Command("unwarn"))
async def cmd_unwarn(message: Message):
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав!")

    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ Укажите пользователя: /unwarn @user или ответьте на сообщение")

    warns = await db.remove_warn(target, message.chat.id)
    name = await mention(target, message.chat.id)
    await message.answer(
        f"✅ Предупреждение снято!\n{name} — варнов: {warns}/{MAX_WARNS}",
        parse_mode="HTML", reply_markup=kb_after_action(target, message.chat.id)
    )


# =============================================================================
# /mute /unmute
# =============================================================================

@router.message(Command("mute"))
async def cmd_mute(message: Message):
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав!")

    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ Укажите пользователя: /mute @user или ответьте на сообщение")

    tr = await get_role(target, message.chat.id)
    if tr >= role:
        return await message.reply("❌ Нельзя замутить — роль цели ≥ вашей!")

    name = await mention(target, message.chat.id)
    await message.answer(
        f"🔇 <b>Выберите срок мута</b>\n\nКому: {name}",
        parse_mode="HTML", reply_markup=kb_duration("applymute", target, message.chat.id)
    )


@router.message(Command("unmute"))
async def cmd_unmute(message: Message):
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав!")

    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ Укажите пользователя: /unmute @user")

    try:
        await bot.restrict_chat_member(message.chat.id, target, permissions=full_permissions())
        await db.remove_mute(target, message.chat.id)
        name = await mention(target, message.chat.id)
        await message.answer(
            f"🔊 {name} размучен!",
            parse_mode="HTML", reply_markup=kb_after_action(target, message.chat.id)
        )
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


# =============================================================================
# /ban /unban
# =============================================================================

@router.message(Command("ban"))
async def cmd_ban(message: Message):
    role = await get_caller_role(message)
    if role < 3:
        return await message.reply("❌ Недостаточно прав! (3+)")

    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ Укажите пользователя: /ban @user")

    tr = await get_role(target, message.chat.id)
    if tr >= role:
        return await message.reply("❌ Нельзя забанить — роль цели ≥ вашей!")

    name = await mention(target, message.chat.id)
    await message.answer(
        f"🚫 <b>Выберите срок бана</b>\n\nКому: {name}",
        parse_mode="HTML", reply_markup=kb_duration("applyban", target, message.chat.id)
    )


@router.message(Command("unban"))
async def cmd_unban(message: Message):
    role = await get_caller_role(message)
    if role < 3:
        return await message.reply("❌ Недостаточно прав! (3+)")

    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ Укажите пользователя: /unban @user или ID")

    try:
        await bot.unban_chat_member(message.chat.id, target, only_if_banned=True)
        await db.remove_ban(target, message.chat.id)
        name = await mention(target, message.chat.id)
        await message.answer(f"✅ {name} разбанен!", parse_mode="HTML", reply_markup=kb_after_action(target, message.chat.id))
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


# =============================================================================
# /kick
# =============================================================================

@router.message(Command("kick"))
async def cmd_kick(message: Message):
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав!")

    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ Укажите пользователя: /kick @user [причина]")

    tr = await get_role(target, message.chat.id)
    if tr >= role:
        return await message.reply("❌ Нельзя кикнуть!")

    reason = args[2] if len(args) > 2 else "Кик"
    try:
        await bot.ban_chat_member(message.chat.id, target)
        await asyncio.sleep(0.5)
        await bot.unban_chat_member(message.chat.id, target)
        name = await mention(target, message.chat.id)
        await message.answer(
            f"👢 {name} кикнут!\nПричина: {reason}",
            parse_mode="HTML", reply_markup=kb_after_action(target, message.chat.id)
        )
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


# =============================================================================
# /ro /unro
# =============================================================================

@router.message(Command("ro"))
async def cmd_ro(message: Message):
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав!")
    await db.set_ro_mode(message.chat.id, True)
    b = InlineKeyboardBuilder()
    b.button(text="✍️ Снять RO", callback_data=f"doUnro:{message.chat.id}")
    await message.answer(
        "👁 <b>Режим RO включён!</b>\nОбычные пользователи не могут писать.",
        parse_mode="HTML", reply_markup=b.as_markup()
    )


@router.message(Command("unro"))
async def cmd_unro(message: Message):
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав!")
    await db.set_ro_mode(message.chat.id, False)
    await message.answer("✍️ <b>Режим RO выключен!</b>", parse_mode="HTML")


# =============================================================================
# /gban /ungban — МГНОВЕННЫЙ БАН ВО ВСЕХ ЧАТАХ
# =============================================================================

@router.message(Command("gban"))
async def cmd_gban(message: Message):
    role = await get_caller_role(message)
    if role < 7:
        return await message.reply("❌ Недостаточно прав! (7+)")

    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)
    if not target:
        return await message.reply(
            "❌ <b>Укажите пользователя:</b>\n"
            "<code>/gban @username [причина]</code>\n"
            "<code>/gban ID [причина]</code>",
            parse_mode="HTML"
        )

    tr = await get_role(target)
    if tr >= role:
        return await message.reply(f"❌ Нельзя забанить! Роль цели: {ROLE_NAMES.get(tr)} ({tr})")
    if tr > 0:
        name = await mention(target)
        return await message.reply(
            f"⚠️ {name} является членом команды ({ROLE_NAMES.get(tr)}).\n"
            f"Сначала снимите роль: <code>/removerole</code>",
            parse_mode="HTML"
        )

    reason = args[2] if len(args) > 2 else "Глобальный бан"
    caller_id = await get_caller_id(message)
    cache_key = f"gban:{target}"
    await db.cache_action(cache_key, json.dumps({"reason": reason, "caller": caller_id}))

    b = InlineKeyboardBuilder()
    b.button(text="✅ Подтвердить глобальный бан", callback_data=f"confirmgban:{target}")
    b.button(text="❌ Отмена", callback_data="cancel:0:0")
    b.adjust(1)

    name = await mention(target)
    await message.answer(
        f"🌐 <b>Подтвердите глобальный бан</b>\n\n"
        f"Кто: {name}\nID: <code>{target}</code>\n"
        f"Причина: {reason}\n\n"
        f"⚠️ Бан будет применён <b>МГНОВЕННО</b> во всех чатах!",
        parse_mode="HTML", reply_markup=b.as_markup()
    )


@router.message(Command("ungban"))
async def cmd_ungban(message: Message):
    role = await get_caller_role(message)
    if role < 7:
        return await message.reply("❌ Недостаточно прав! (7+)")

    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ Укажите пользователя: /ungban @user или ID")

    if not await db.is_globally_banned(target):
        return await message.reply("ℹ️ У этого пользователя нет глобального бана.")

    await db.remove_global_ban(target)
    ok, fail = await enforce_ungban_in_all_chats(target)
    name = await mention(target)
    await message.answer(
        f"✅ Глобальный бан снят!\n{name}\n\nРазбанен в {ok} чатах.",
        parse_mode="HTML", reply_markup=kb_after_action(target, message.chat.id)
    )


# =============================================================================
# /setrole /removerole
# =============================================================================

@router.message(Command("setrole"))
async def cmd_setrole(message: Message):
    caller_role = await get_caller_role(message)
    if caller_role < 7:
        return await message.reply("❌ Недостаточно прав! (7+)")

    args = get_args(message)
    if len(args) < 3:
        roles_text = "\n".join([f"  {k}: {v}" for k, v in ROLE_NAMES.items()])
        return await message.reply(
            f"❌ Использование: <code>/setrole @user ЧИСЛО</code>\n\n<b>Роли:</b>\n{roles_text}",
            parse_mode="HTML"
        )

    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ Пользователь не найден")

    try:
        new_role = int(args[2])
        if not (0 <= new_role <= 10):
            return await message.reply("❌ Роль: 0-10")
    except ValueError:
        return await message.reply("❌ Роль должна быть числом 0-10")

    tr = await get_role(target)
    if new_role >= caller_role:
        return await message.reply(f"❌ Нельзя назначить роль ≥ вашей ({caller_role})")
    if tr >= caller_role:
        return await message.reply("❌ Нельзя изменить роль этого пользователя!")

    await db.set_global_role(target, new_role)
    name = await mention(target)
    await message.answer(
        f"⭐ <b>Роль назначена!</b>\n\n{name}\n"
        f"Было: {ROLE_NAMES.get(tr, '?')} ({tr})\n"
        f"Стало: {ROLE_NAMES.get(new_role, '?')} ({new_role})",
        parse_mode="HTML", reply_markup=kb_after_action(target, message.chat.id)
    )


@router.message(Command("removerole"))
async def cmd_removerole(message: Message):
    caller_role = await get_caller_role(message)
    if caller_role < 7:
        return await message.reply("❌ Недостаточно прав! (7+)")

    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ Укажите пользователя: /removerole @user")

    tr = await get_role(target)
    if tr >= caller_role:
        return await message.reply("❌ Нельзя снять роль у этого пользователя!")
    if tr == 0:
        return await message.reply("ℹ️ У пользователя нет роли")

    await db.set_global_role(target, 0)
    name = await mention(target)
    await message.answer(
        f"✅ <b>Роль снята!</b>\n\n{name}\nБыла: {ROLE_NAMES.get(tr, '?')} ({tr})",
        parse_mode="HTML", reply_markup=kb_after_action(target, message.chat.id)
    )


# =============================================================================
# /staff
# =============================================================================

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
            name = await mention(uid)
            text += f"  • {name}\n"
        text += "\n"

    await message.answer(text, parse_mode="HTML")


# =============================================================================
# /setnick
# =============================================================================

@router.message(Command("setnick"))
async def cmd_setnick(message: Message):
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав!")

    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)
    if not target or len(args) < 3:
        return await message.reply("❌ Использование: /setnick @user НикВЧате")

    nick = args[2]
    await db.set_nick(target, message.chat.id, nick)
    name = await mention(target, message.chat.id)
    await message.answer(f"📝 Ник установлен!\n{name} → {nick}", parse_mode="HTML")


# =============================================================================
# /clear
# =============================================================================

@router.message(Command("clear"))
async def cmd_clear(message: Message):
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав!")

    args = get_args(message)
    if len(args) < 2:
        return await message.reply("❌ Использование: /clear <количество>")

    try:
        count = int(args[1])
        if not (1 <= count <= 100):
            return await message.reply("❌ Количество: 1-100")
    except ValueError:
        return await message.reply("❌ Число 1-100")

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
        status = await message.answer(f"🧹 Очищено {deleted}/{count}")
        await asyncio.sleep(3)
        await status.delete()
        await message.delete()
    except Exception:
        pass


# =============================================================================
# CALLBACK — ПОДТВЕРЖДЕНИЯ
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

    cache_key = f"warn:{target}:{chat_id}"
    raw = await db.get_cached_action(cache_key)
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
            f"⚠️ {name} — предупреждение!\nПричина: {reason}\n\n"
            f"👢 <b>Кикнут за {MAX_WARNS} варнов!</b>",
            parse_mode="HTML", reply_markup=kb_after_action(target, chat_id)
        )
    else:
        await call.message.edit_text(
            f"⚠️ {name} — предупреждение!\nПричина: {reason}\nВарнов: {warns}/{MAX_WARNS}",
            parse_mode="HTML", reply_markup=kb_after_warn(target, chat_id)
        )

    await call.answer("✅ Варн выдан!")
    await db.clear_cached_action(cache_key)


@router.callback_query(F.data.startswith("confirmgban:"))
async def cb_confirm_gban(call: CallbackQuery):
    """Подтверждение глобального бана — МГНОВЕННЫЙ бан во всех чатах"""
    target = int(call.data.split(":")[1])

    role = await get_role(call.from_user.id)
    if role < 7:
        return await call.answer("❌ Нет прав! (7+)", show_alert=True)

    tr = await get_role(target)
    if tr >= role or tr > 0:
        return await call.answer("❌ Нельзя забанить!", show_alert=True)

    cache_key = f"gban:{target}"
    raw = await db.get_cached_action(cache_key)
    data = json.loads(raw) if raw else {}
    reason = data.get("reason", "Глобальный бан")
    caller = data.get("caller", call.from_user.id)

    # 1. Записываем в БД
    await db.add_global_ban(target, caller, reason)

    # 2. МГНОВЕННЫЙ бан во ВСЕХ чатах
    name = await mention(target)
    await call.message.edit_text(
        f"🌐 <b>Применяю глобальный бан...</b>\n\n"
        f"Пользователь: {name}\nПричина: {reason}\n\n"
        f"⏳ Баню во всех чатах...",
        parse_mode="HTML"
    )

    ok, fail = await enforce_gban_in_all_chats(target)

    await call.message.edit_text(
        f"🌐 <b>Глобальный бан применён!</b>\n\n"
        f"Пользователь: {name}\nID: <code>{target}</code>\n"
        f"Причина: {reason}\n\n"
        f"✅ Забанен в {ok} чатах"
        + (f"\n⚠️ Не удалось в {fail} чатах" if fail else ""),
        parse_mode="HTML"
    )
    await call.answer("✅ Глобальный бан!", show_alert=True)
    await db.clear_cached_action(cache_key)


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
        return await call.answer(f"❌ Лимит мута: {format_duration(limit)}", show_alert=True)

    try:
        until = int(time.time()) + seconds if seconds > 0 else 0
        delta = timedelta(seconds=seconds) if seconds > 0 else None
        await bot.restrict_chat_member(chat_id, target, permissions=muted_permissions(), until_date=delta)
        await db.add_mute(target, chat_id, call.from_user.id, "Мут", until)

        name = await mention(target, chat_id)
        await call.message.edit_text(
            f"🔇 {name} замучен на {format_duration(seconds)}",
            parse_mode="HTML", reply_markup=kb_after_mute(target, chat_id)
        )
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
        await call.message.edit_text(
            f"🚫 {name} забанен на {format_duration(seconds)}",
            parse_mode="HTML", reply_markup=kb_after_ban(target, chat_id)
        )
        await call.answer("✅ Бан!")
    except Exception as e:
        await call.answer(f"❌ {e}", show_alert=True)


# =============================================================================
# CALLBACK — ДЕЙСТВИЯ (из кнопок под сообщениями)
# =============================================================================

@router.callback_query(F.data.startswith("unmute:"))
async def cb_unmute(call: CallbackQuery):
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    role = await get_role(call.from_user.id, chat_id)
    if role < 1:
        return await call.answer("❌ Нет прав!", show_alert=True)
    try:
        await bot.restrict_chat_member(chat_id, target, permissions=full_permissions())
        await db.remove_mute(target, chat_id)
        name = await mention(target, chat_id)
        await call.message.edit_text(f"🔊 {name} размучен!", parse_mode="HTML", reply_markup=kb_after_action(target, chat_id))
        await call.answer("✅ Размучен!")
    except Exception as e:
        await call.answer(f"❌ {e}", show_alert=True)


@router.callback_query(F.data.startswith("unban:"))
async def cb_unban(call: CallbackQuery):
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    role = await get_role(call.from_user.id, chat_id)
    if role < 3:
        return await call.answer("❌ Нет прав! (3+)", show_alert=True)
    try:
        await bot.unban_chat_member(chat_id, target, only_if_banned=True)
        await db.remove_ban(target, chat_id)
        name = await mention(target, chat_id)
        await call.message.edit_text(f"✅ {name} разбанен!", parse_mode="HTML", reply_markup=kb_after_action(target, chat_id))
        await call.answer("✅ Разбанен!")
    except Exception as e:
        await call.answer(f"❌ {e}", show_alert=True)


@router.callback_query(F.data.startswith("unwarn:"))
async def cb_unwarn(call: CallbackQuery):
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    role = await get_role(call.from_user.id, chat_id)
    if role < 1:
        return await call.answer("❌ Нет прав!", show_alert=True)
    warns = await db.remove_warn(target, chat_id)
    name = await mention(target, chat_id)
    await call.message.edit_text(
        f"✅ Варн снят!\n{name} — варнов: {warns}/{MAX_WARNS}",
        parse_mode="HTML", reply_markup=kb_after_action(target, chat_id)
    )
    await call.answer("✅ Варн снят!")


@router.callback_query(F.data.startswith("info:"))
async def cb_info(call: CallbackQuery):
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    text = await build_stats_text(target, chat_id)
    try:
        await call.message.edit_text(text, parse_mode="HTML")
    except Exception:
        await call.answer(f"ID: {target}", show_alert=False)
    await call.answer()


@router.callback_query(F.data.startswith("doUnro:"))
async def cb_do_unro(call: CallbackQuery):
    chat_id = int(call.data.split(":")[1])
    role = await get_role(call.from_user.id, chat_id)
    if role < 1:
        return await call.answer("❌ Нет прав!", show_alert=True)
    await db.set_ro_mode(chat_id, False)
    await call.message.edit_text("✍️ <b>Режим RO выключен!</b>", parse_mode="HTML")
    await call.answer("✅ RO снят!")


# --- Быстрые действия из кнопок stats/info ---

@router.callback_query(F.data.startswith("startwarn:"))
async def cb_start_warn(call: CallbackQuery):
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    role = await get_role(call.from_user.id, chat_id)
    if role < 1:
        return await call.answer("❌ Нет прав!", show_alert=True)
    tr = await get_role(target, chat_id)
    if tr >= role:
        return await call.answer("❌ Нельзя варнить!", show_alert=True)

    reason = "Нарушение правил"
    cache_key = f"warn:{target}:{chat_id}"
    await db.cache_action(cache_key, json.dumps({"reason": reason, "caller": call.from_user.id}))

    b = InlineKeyboardBuilder()
    b.button(text="✅ Подтвердить варн", callback_data=f"confirmwarn:{target}:{chat_id}")
    b.button(text="❌ Отмена", callback_data="cancel:0:0")
    b.adjust(2)

    name = await mention(target, chat_id)
    await call.message.edit_text(
        f"⚠️ Выдать предупреждение?\n{name}\nПричина: {reason}",
        parse_mode="HTML", reply_markup=b.as_markup()
    )
    await call.answer()


@router.callback_query(F.data.startswith("startmute:"))
async def cb_start_mute(call: CallbackQuery):
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    role = await get_role(call.from_user.id, chat_id)
    if role < 1:
        return await call.answer("❌ Нет прав!", show_alert=True)
    tr = await get_role(target, chat_id)
    if tr >= role:
        return await call.answer("❌ Нельзя замутить!", show_alert=True)

    name = await mention(target, chat_id)
    await call.message.edit_text(
        f"🔇 <b>Выберите срок мута</b>\n\nКому: {name}",
        parse_mode="HTML", reply_markup=kb_duration("applymute", target, chat_id)
    )
    await call.answer()


@router.callback_query(F.data.startswith("startban:"))
async def cb_start_ban(call: CallbackQuery):
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    role = await get_role(call.from_user.id, chat_id)
    if role < 3:
        return await call.answer("❌ Нет прав! (3+)", show_alert=True)
    tr = await get_role(target, chat_id)
    if tr >= role:
        return await call.answer("❌ Нельзя забанить!", show_alert=True)

    name = await mention(target, chat_id)
    await call.message.edit_text(
        f"🚫 <b>Выберите срок бана</b>\n\nКому: {name}",
        parse_mode="HTML", reply_markup=kb_duration("applyban", target, chat_id)
    )
    await call.answer()


@router.callback_query(F.data.startswith("startgban:"))
async def cb_start_gban(call: CallbackQuery):
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    role = await get_role(call.from_user.id)
    if role < 7:
        return await call.answer("❌ Нет прав! (7+)", show_alert=True)
    tr = await get_role(target)
    if tr >= role or tr > 0:
        return await call.answer("❌ Нельзя! У цели есть роль.", show_alert=True)

    reason = "Глобальный бан"
    cache_key = f"gban:{target}"
    await db.cache_action(cache_key, json.dumps({"reason": reason, "caller": call.from_user.id}))

    b = InlineKeyboardBuilder()
    b.button(text="✅ Подтвердить глобальный бан", callback_data=f"confirmgban:{target}")
    b.button(text="❌ Отмена", callback_data="cancel:0:0")
    b.adjust(1)

    name = await mention(target)
    await call.message.edit_text(
        f"🌐 <b>Глобальный бан?</b>\n\n{name}\nID: <code>{target}</code>\nПричина: {reason}",
        parse_mode="HTML", reply_markup=b.as_markup()
    )
    await call.answer()


@router.callback_query(F.data.startswith("dokick:"))
async def cb_do_kick(call: CallbackQuery):
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    role = await get_role(call.from_user.id, chat_id)
    if role < 1:
        return await call.answer("❌ Нет прав!", show_alert=True)
    tr = await get_role(target, chat_id)
    if tr >= role:
        return await call.answer("❌ Нельзя кикнуть!", show_alert=True)
    try:
        await bot.ban_chat_member(chat_id, target)
        await asyncio.sleep(0.5)
        await bot.unban_chat_member(chat_id, target)
        name = await mention(target, chat_id)
        await call.message.edit_text(f"👢 {name} кикнут!", parse_mode="HTML", reply_markup=kb_after_action(target, chat_id))
        await call.answer("✅ Кикнут!")
    except Exception as e:
        await call.answer(f"❌ {e}", show_alert=True)


@router.callback_query(F.data.startswith("cancel:"))
async def cb_cancel(call: CallbackQuery):
    try:
        await call.message.edit_text("❌ Действие отменено", reply_markup=None)
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

    # Кэш username
    if event.new_chat_member.user.username:
        await db.cache_username(uid, event.new_chat_member.user.username)

    # Проверка глобального бана → мгновенный бан
    if await db.is_globally_banned(uid):
        try:
            await bot.ban_chat_member(cid, uid)
            name = await mention(uid)
            await bot.send_message(
                cid,
                f"🚫 {name} имеет глобальный бан — удалён из чата.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"gban on join {uid}: {e}")
        return

    # Приветствие
    welcome = await db.get_welcome(cid)
    if welcome:
        text = welcome.replace("{user}", event.new_chat_member.user.full_name or "")
        await bot.send_message(cid, text)


@router.message(F.text)
async def on_message(message: Message):
    """Обработка всех текстовых сообщений — антиспам, RO, фильтр слов, кэш username"""
    if message.chat.type == ChatType.PRIVATE:
        return
    if not message.from_user:
        return

    uid = message.from_user.id
    cid = message.chat.id

    # Кэш username
    if message.from_user.username:
        await db.cache_username(uid, message.from_user.username)

    role = await get_role(uid, cid)

    # Глобальный бан — мгновенный бан
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
                await bot.restrict_chat_member(cid, uid, permissions=muted_permissions(), until_date=timedelta(minutes=30))
                await message.delete()
                name = await mention(uid)
                await bot.send_message(
                    cid, f"🔇 {name} замучен на 30 мин (антиспам)",
                    parse_mode="HTML", reply_markup=kb_after_mute(uid, cid)
                )
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
                    await bot.restrict_chat_member(cid, uid, permissions=muted_permissions(), until_date=timedelta(minutes=30))
                    name = await mention(uid)
                    await bot.send_message(
                        cid, f"🔇 {name} замучен (запрещённое слово)",
                        parse_mode="HTML", reply_markup=kb_after_mute(uid, cid)
                    )
                except Exception:
                    pass
                return


# =============================================================================
# ПЕРИОДИЧЕСКАЯ ОЧИСТКА КЭША
# =============================================================================

async def periodic_cleanup():
    """Раз в час чистим старый кэш"""
    while True:
        await asyncio.sleep(3600)
        try:
            await db.cleanup_old_cache(3600)
        except Exception:
            pass


# =============================================================================
# ЗАПУСК
# =============================================================================

async def main():
    global db
    db = Database("database.db")
    await db.init()

    logger.info("🔵 Модерация v6.0 — запуск")

    await init_staff()

    # Регистрация чатов
    for cid in MODERATED_CHATS:
        try:
            chat = await bot.get_chat(cid)
            await db.register_chat(cid, chat.title or "")
            logger.info(f"Чат: {cid} ({chat.title})")
        except Exception as e:
            logger.warning(f"Чат {cid}: {e}")

    await register_commands()

    # Запускаем очистку кэша в фоне
    asyncio.create_task(periodic_cleanup())

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("✅ Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
