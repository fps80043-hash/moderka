"""
🔵 Модерация Анонимные сообщения | Георгиевка
Telegram бот для модерации групп - ВЕРСИЯ 5.0 (ЧИСТАЯ РЕАЛИЗАЦИЯ)

Основные возможности:
- Полная система модерации с ролями (0-10)
- Инлайн-кнопки под сообщениями (контекстные действия)
- Режим RO для всего чата
- Глобальный бан с проверкой ролей
- Статистика и управление командой
- Поддержка анонимных админов
"""

import asyncio
import logging
import json
import os
import time
from datetime import datetime, timedelta
from typing import Optional, List

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, ChatMemberUpdatedFilter, IS_NOT_MEMBER, IS_MEMBER
from aiogram.types import (
    Message, CallbackQuery, ChatMemberUpdated,
    ChatPermissions, BotCommand, BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats
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
MODERATED_CHATS = config.get("moderated_chats", [])
PRESET_STAFF = config.get("preset_staff", {})  # {"user_id": role}
MAX_WARNS = config.get("max_warns", 3)
SPAM_INTERVAL = config.get("spam_interval_seconds", 2)
SPAM_COUNT = config.get("spam_messages_count", 3)
ANON_ADMIN_ROLE = config.get("anon_admin_role", 10)

ANONYMOUS_BOT_ID = 1087968824

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

db: Database = None

# =============================================================================
# РОЛИ (0-10)
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

MUTE_LIMITS = {1: 3600, 2: 3600, 3: 86400, 4: 86400, 5: 0, 6: 0, 7: 0, 8: 0, 9: 0, 10: 0}

# =============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =============================================================================

def is_anon(message: Message) -> bool:
    if message.from_user and message.from_user.id == ANONYMOUS_BOT_ID:
        return True
    if message.sender_chat and message.sender_chat.id == message.chat.id:
        return True
    return False

def get_args(message: Message, maxsplit: int = -1) -> list:
    """Парсинг аргументов команды (убирает @botusername)"""
    if not message.text:
        return []
    
    text = message.text.strip()
    parts = text.split(maxsplit=1)
    
    if not parts:
        return []
    
    command = parts[0]
    if '@' in command:
        command = command.split('@')[0]
    
    if len(parts) > 1:
        clean_text = command + ' ' + parts[1]
    else:
        clean_text = command
    
    if maxsplit >= 0:
        result = clean_text.split(maxsplit=maxsplit)
    else:
        result = clean_text.split()
    
    return result

async def get_caller_role(message: Message) -> int:
    if is_anon(message):
        return ANON_ADMIN_ROLE
    if not message.from_user:
        return 0
    return await get_role(message.from_user.id, message.chat.id)

async def get_caller_id_safe(message: Message) -> int:
    if is_anon(message):
        return 0
    if message.from_user:
        return message.from_user.id
    return 0

async def get_role(user_id: int, chat_id: int = 0) -> int:
    if user_id == 0 or user_id == ANONYMOUS_BOT_ID:
        return 0
    global_role = await db.get_global_role(user_id)
    if global_role > 0:
        return global_role
    if chat_id:
        return await db.get_user_role(user_id, chat_id)
    return 0

async def get_user_info(user_id: int) -> dict:
    if user_id == 0 or user_id == ANONYMOUS_BOT_ID:
        return {"id": user_id, "first_name": "Аноним", "last_name": "",
                "username": "", "full_name": "Анонимный администратор"}
    try:
        user = await bot.get_chat(user_id)
        return {
            "id": user_id,
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "username": user.username or "",
            "full_name": user.full_name or f"User {user_id}"
        }
    except Exception:
        cached = await db.get_username_by_id(user_id)
        return {
            "id": user_id, "first_name": "Пользователь", "last_name": "",
            "username": cached or "",
            "full_name": f"@{cached}" if cached else f"Пользователь {user_id}"
        }

async def get_user_name(user_id: int, chat_id: int = 0) -> str:
    if chat_id:
        nick = await db.get_nick(user_id, chat_id)
        if nick:
            return nick
    info = await get_user_info(user_id)
    return info["full_name"]

async def mention(user_id: int, chat_id: int = 0) -> str:
    if user_id == 0 or user_id == ANONYMOUS_BOT_ID:
        return "<i>Анонимный администратор</i>"
    name = await get_user_name(user_id, chat_id)
    return f'<a href="tg://user?id={user_id}">{name}</a>'

async def resolve_username(username: str) -> Optional[int]:
    username = username.lower().lstrip('@')
    cached = await db.get_user_by_username(username)
    if cached:
        return cached
    try:
        user = await bot.get_chat(f"@{username}")
        if user and user.id:
            await db.cache_username(user.id, username)
            return user.id
    except Exception:
        pass
    return None

async def parse_user(message: Message, args: list, start_idx: int = 1) -> Optional[int]:
    # Реплай
    if message.reply_to_message:
        reply = message.reply_to_message
        if not is_anon(reply) and reply.from_user:
            return reply.from_user.id

    # Forward
    if message.forward_from:
        return message.forward_from.id

    # Из аргументов
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

def muted_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=False, can_send_audios=False, can_send_documents=False,
        can_send_photos=False, can_send_videos=False, can_send_video_notes=False,
        can_send_voice_notes=False, can_send_polls=False, can_send_other_messages=False,
        can_add_web_page_previews=False, can_change_info=False, can_invite_users=False,
        can_pin_messages=False, can_manage_topics=False
    )

def readonly_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=False, can_send_audios=False, can_send_documents=False,
        can_send_photos=False, can_send_videos=False, can_send_video_notes=False,
        can_send_voice_notes=False, can_send_polls=False, can_send_other_messages=False,
        can_add_web_page_previews=False, can_change_info=False, can_invite_users=True,
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
# СОЗДАНИЕ ИНЛАЙН-КНОПОК (КОНТЕКСТНЫЕ)
# =============================================================================

def create_duration_keyboard(action: str, target_id: int, chat_id: int) -> InlineKeyboardBuilder:
    """Клавиатура выбора времени"""
    builder = InlineKeyboardBuilder()
    durations = [
        ("5 мин", 300), ("30 мин", 1800), ("1 час", 3600), ("6 часов", 21600),
        ("1 день", 86400), ("7 дней", 604800), ("30 дней", 2592000), ("Навсегда", 0)
    ]
    for label, seconds in durations:
        builder.button(text=label, callback_data=f"{action}:{target_id}:{chat_id}:{seconds}")
    builder.adjust(2)
    return builder

def create_muted_buttons(target_id: int, chat_id: int) -> InlineKeyboardBuilder:
    """Кнопки для замученного пользователя"""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔊 Размутить", callback_data=f"unmute:{target_id}:{chat_id}")
    builder.button(text="📊 Статистика", callback_data=f"stats:{target_id}:{chat_id}")
    builder.button(text="🧹 Очистить сообщения", callback_data=f"clear:{target_id}:{chat_id}")
    builder.adjust(1)
    return builder

def create_banned_buttons(target_id: int, chat_id: int) -> InlineKeyboardBuilder:
    """Кнопки для забаненного пользователя"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Разбанить", callback_data=f"unban:{target_id}:{chat_id}")
    builder.button(text="📊 Статистика", callback_data=f"stats:{target_id}:{chat_id}")
    builder.adjust(1)
    return builder

def create_warned_buttons(target_id: int, chat_id: int) -> InlineKeyboardBuilder:
    """Кнопки после варна"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Снять варн", callback_data=f"unwarn:{target_id}:{chat_id}")
    builder.button(text="📊 Статистика", callback_data=f"stats:{target_id}:{chat_id}")
    builder.button(text="🧹 Очистить сообщения", callback_data=f"clear:{target_id}:{chat_id}")
    builder.adjust(1)
    return builder

def create_info_buttons(target_id: int, chat_id: int) -> InlineKeyboardBuilder:
    """Кнопки для информационных сообщений"""
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Статистика", callback_data=f"stats:{target_id}:{chat_id}")
    builder.button(text="🧹 Очистить сообщения", callback_data=f"clear:{target_id}:{chat_id}")
    builder.adjust(1)
    return builder

# =============================================================================
# РЕГИСТРАЦИЯ КОМАНД
# =============================================================================

async def register_commands():
    """Регистрация команд бота"""
    # Все команды для групп (staff увидит все при "/")
    group_commands = [
        BotCommand(command="help", description="❓ Помощь"),
        BotCommand(command="stats", description="📊 Статистика"),
        BotCommand(command="warn", description="⚠️ Выдать предупреждение"),
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
    
    # Только базовые для ЛС
    private_commands = [
        BotCommand(command="help", description="❓ Помощь"),
        BotCommand(command="stats", description="📊 Моя статистика"),
    ]
    
    try:
        await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())
        await bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())
        logger.info("✅ Команды зарегистрированы")
    except Exception as e:
        logger.error(f"❌ Ошибка регистрации команд: {e}")

# =============================================================================
# ИНИЦИАЛИЗАЦИЯ КОМАНДЫ
# =============================================================================

async def init_staff():
    """Инициализация preset_staff (по user_id)"""
    if not PRESET_STAFF:
        return
    logger.info("Инициализация preset_staff...")
    for user_id_str, role in PRESET_STAFF.items():
        try:
            user_id = int(user_id_str)
            await db.set_global_role(user_id, role)
            logger.info(f"Роль {role} назначена пользователю {user_id}")
        except Exception as e:
            logger.error(f"Ошибка при назначении роли для {user_id_str}: {e}")
    logger.info(f"✅ Инициализировано {len(PRESET_STAFF)} ролей")

# =============================================================================
# КОМАНДА - HELP
# =============================================================================

@router.message(Command("help"))
async def cmd_help(message: Message):
    """Справка по командам"""
    role = await get_caller_role(message)
    
    text = "📖 <b>Справка по командам бота</b>\n\n"
    
    if role >= 1:
        text += (
            "<b>Модерация (уровень 1+):</b>\n"
            "• /warn @user [причина] - предупреждение\n"
            "• /unwarn @user - снять варн\n"
            "• /mute @user - мут\n"
            "• /unmute @user - размутить\n"
            "• /kick @user [причина] - кикнуть\n"
            "• /ro - режим RO для всего чата\n"
            "• /unro - снять режим RO\n"
            "• /setnick @user <ник> - установить ник\n"
            "• /clear <N> - очистить N сообщений\n\n"
        )
    
    if role >= 3:
        text += (
            "<b>Баны (уровень 3+):</b>\n"
            "• /ban @user - бан\n"
            "• /unban @user - разбанить\n\n"
        )
    
    if role >= 7:
        text += (
            "<b>Глобальные (уровень 7+):</b>\n"
            "• /gban @user [причина] - глобальный бан\n"
            "• /ungban @user - снять глобальный бан\n"
            "• /setrole @user <роль> - назначить роль\n"
            "• /removerole @user - снять роль\n\n"
        )
    
    text += "<b>Информация:</b>\n• /stats [@user] - статистика\n"
    if role >= 1:
        text += "• /staff - список команды\n"
    
    if role == 0:
        text += "\n<i>ℹ️ Команды модерации доступны только staff</i>"
    
    await message.answer(text, parse_mode="HTML")

# (Продолжение следует...)

# =============================================================================
# КОМАНДА - STATS
# =============================================================================

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Статистика пользователя"""
    if message.chat.type == ChatType.PRIVATE:
        if not message.from_user:
            return
        user_id = message.from_user.id
        role = await get_role(user_id)
        is_gbanned = await db.is_globally_banned(user_id)
        
        text = (
            f"👤 <b>Ваша информация</b>\n\n"
            f"ID: <code>{user_id}</code>\n"
            f"Глобальная роль: {ROLE_NAMES.get(role, 'Неизвестно')} ({role})\n"
            f"Глобальный бан: {'✅ Да' if is_gbanned else '❌ Нет'}\n"
        )
        await message.answer(text, parse_mode="HTML")
        return

    args = get_args(message)
    target = await parse_user(message, args)

    if not target:
        if message.from_user:
            target = message.from_user.id
        else:
            await message.reply("❌ Не удалось определить пользователя")
            return

    chat_id = message.chat.id
    info = await get_user_info(target)
    role = await get_role(target, chat_id)
    global_role = await db.get_global_role(target)
    chat_role = await db.get_user_role(target, chat_id)

    warns = await db.get_warns(target, chat_id)
    is_muted = await db.is_muted(target, chat_id)
    is_banned = await db.is_banned(target, chat_id)
    is_gbanned = await db.is_globally_banned(target)
    
    mute_info = await db.get_mute_info(target, chat_id) if is_muted else None
    ban_info = await db.get_ban_info(target, chat_id) if is_banned else None
    nick = await db.get_nick(target, chat_id)

    text = (
        f"📊 <b>Статистика пользователя</b>\n\n"
        f"👤 <b>Информация:</b>\n"
        f"ID: <code>{target}</code>\n"
    )
    
    if info['username']:
        text += f"Username: @{info['username']}\n"
    
    if nick:
        text += f"Ник в чате: {nick}\n"
    
    text += (
        f"\n⭐ <b>Роли:</b>\n"
        f"Глобальная роль: {ROLE_NAMES.get(global_role, 'Пользователь')} ({global_role})\n"
        f"Роль в чате: {ROLE_NAMES.get(chat_role, 'Пользователь')} ({chat_role})\n"
        f"Эффективная роль: {ROLE_NAMES.get(role, 'Пользователь')} ({role})\n"
        f"\n📋 <b>Модерация:</b>\n"
        f"Предупреждения: {warns}/{MAX_WARNS}\n"
        f"Мут: {'✅ Да' if is_muted else '❌ Нет'}"
    )
    
    if is_muted and mute_info:
        until = mute_info.get('until', 0)
        if until > 0:
            time_left = until - int(time.time())
            if time_left > 0:
                text += f" (до {datetime.fromtimestamp(until).strftime('%d.%m.%Y %H:%M')})"
        else:
            text += " (навсегда)"
        if mute_info.get('reason'):
            text += f"\n  Причина: {mute_info['reason']}"
    
    text += f"\nБан в чате: {'✅ Да' if is_banned else '❌ Нет'}"
    
    if is_banned and ban_info:
        if ban_info.get('reason'):
            text += f"\n  Причина: {ban_info['reason']}"
    
    text += f"\nГлобальный бан: {'✅ Да' if is_gbanned else '❌ Нет'}"
    
    if is_gbanned:
        gban_info = await db.get_global_ban_info(target)
        if gban_info and gban_info.get('reason'):
            text += f"\n  Причина: {gban_info['reason']}"

    await message.answer(text, parse_mode="HTML")

# =============================================================================
# КОМАНДА - WARN/UNWARN
# =============================================================================

@router.message(Command("warn"))
async def cmd_warn(message: Message):
    """Выдать предупреждение"""
    role = await get_caller_role(message)
    if role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)

    if not target:
        await message.reply(
            "❌ <b>Не удалось определить пользователя</b>\n\n"
            "<b>Использование:</b>\n<code>/warn @username [причина]</code>",
            parse_mode="HTML"
        )
        return

    target_role = await get_role(target, message.chat.id)
    if target_role >= role:
        await message.reply("❌ Нельзя выдать предупреждение!")
        return

    reason = args[2] if len(args) > 2 else "Нарушение правил"
    caller_id = await get_caller_id_safe(message)

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data=f"confirmwarn:{target}:{message.chat.id}:{caller_id}")
    builder.button(text="❌ Отмена", callback_data=f"cancel:{target}:{message.chat.id}")
    builder.adjust(2)

    target_name = await mention(target, message.chat.id)
    text = f"⚠️ <b>Выдать предупреждение?</b>\n\nПользователь: {target_name}\nПричина: {reason}"

    await db.cache_warn_reason(target, message.chat.id, reason)
    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())

@router.message(Command("unwarn"))
async def cmd_unwarn(message: Message):
    """Снять предупреждение"""
    role = await get_caller_role(message)
    if role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message)
    target = await parse_user(message, args)

    if not target:
        await message.reply("❌ Укажите пользователя: /unwarn @user")
        return

    warns = await db.remove_warn(target, message.chat.id)
    target_name = await mention(target, message.chat.id)
    
    # Кнопки после снятия варна
    buttons = create_info_buttons(target, message.chat.id)
    
    await message.answer(
        f"✅ Предупреждение снято!\nПользователь: {target_name}\nОсталось: {warns}/{MAX_WARNS}",
        parse_mode="HTML",
        reply_markup=buttons.as_markup()
    )

# =============================================================================
# КОМАНДА - MUTE/UNMUTE
# =============================================================================

@router.message(Command("mute"))
async def cmd_mute(message: Message):
    """Замутить"""
    role = await get_caller_role(message)
    if role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message)
    target = await parse_user(message, args)

    if not target:
        await message.reply("❌ Укажите пользователя: /mute @user")
        return

    target_role = await get_role(target, message.chat.id)
    if target_role >= role:
        await message.reply("❌ Нельзя замутить!")
        return

    builder = create_duration_keyboard("applymute", target, message.chat.id)
    builder.button(text="❌ Отмена", callback_data=f"cancel:{target}:{message.chat.id}")
    builder.adjust(2)

    target_name = await mention(target, message.chat.id)
    await message.answer(
        f"🔇 <b>Выберите время мута</b>\n\nПользователь: {target_name}",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )

@router.message(Command("unmute"))
async def cmd_unmute(message: Message):
    """Размутить"""
    role = await get_caller_role(message)
    if role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message)
    target = await parse_user(message, args)

    if not target:
        await message.reply("❌ Укажите пользователя: /unmute @user")
        return

    try:
        await bot.restrict_chat_member(message.chat.id, target, permissions=full_permissions())
        await db.remove_mute(target, message.chat.id)
        
        target_name = await mention(target, message.chat.id)
        buttons = create_info_buttons(target, message.chat.id)
        
        await message.answer(
            f"🔊 {target_name} размучен!",
            parse_mode="HTML",
            reply_markup=buttons.as_markup()
        )
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")

# =============================================================================
# КОМАНДА - BAN/UNBAN
# =============================================================================

@router.message(Command("ban"))
async def cmd_ban(message: Message):
    """Забанить"""
    role = await get_caller_role(message)
    if role < 3:
        await message.reply("❌ Недостаточно прав! Требуется уровень 3+")
        return

    args = get_args(message)
    target = await parse_user(message, args)

    if not target:
        await message.reply("❌ Укажите пользователя: /ban @user")
        return

    target_role = await get_role(target, message.chat.id)
    if target_role >= role:
        await message.reply("❌ Нельзя забанить!")
        return

    builder = create_duration_keyboard("applyban", target, message.chat.id)
    builder.button(text="❌ Отмена", callback_data=f"cancel:{target}:{message.chat.id}")
    builder.adjust(2)

    target_name = await mention(target, message.chat.id)
    await message.answer(
        f"🚫 <b>Выберите время бана</b>\n\nПользователь: {target_name}",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )

@router.message(Command("unban"))
async def cmd_unban(message: Message):
    """Разбанить"""
    role = await get_caller_role(message)
    if role < 3:
        await message.reply("❌ Недостаточно прав! Требуется уровень 3+")
        return

    args = get_args(message)
    target = await parse_user(message, args)

    if not target:
        await message.reply("❌ Укажите пользователя: /unban @user")
        return

    try:
        await bot.unban_chat_member(message.chat.id, target)
        await db.remove_ban(target, message.chat.id)
        
        target_name = await mention(target, message.chat.id)
        buttons = create_info_buttons(target, message.chat.id)
        
        await message.answer(
            f"✅ {target_name} разбанен!",
            parse_mode="HTML",
            reply_markup=buttons.as_markup()
        )
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")

# =============================================================================
# КОМАНДА - KICK
# =============================================================================

@router.message(Command("kick"))
async def cmd_kick(message: Message):
    """Кикнуть"""
    role = await get_caller_role(message)
    if role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)

    if not target:
        await message.reply("❌ Укажите пользователя: /kick @user [причина]")
        return

    target_role = await get_role(target, message.chat.id)
    if target_role >= role:
        await message.reply("❌ Нельзя кикнуть!")
        return

    reason = args[2] if len(args) > 2 else "Кик"

    try:
        await bot.ban_chat_member(message.chat.id, target)
        await asyncio.sleep(0.5)
        await bot.unban_chat_member(message.chat.id, target)
        
        target_name = await mention(target, message.chat.id)
        buttons = create_info_buttons(target, message.chat.id)
        
        await message.answer(
            f"👢 {target_name} кикнут!\nПричина: {reason}",
            parse_mode="HTML",
            reply_markup=buttons.as_markup()
        )
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


# =============================================================================
# КОМАНДА - RO/UNRO
# =============================================================================

@router.message(Command("ro"))
async def cmd_ro(message: Message):
    """Режим RO для всего чата"""
    role = await get_caller_role(message)
    if role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    try:
        await db.set_ro_mode(message.chat.id, True)
        await message.answer(
            "👁 <b>Режим только чтение включен!</b>\n\n"
            "Обычные пользователи не могут отправлять сообщения.\n"
            "Staff может продолжать работу.",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")

@router.message(Command("unro"))
async def cmd_unro(message: Message):
    """Снять режим RO"""
    role = await get_caller_role(message)
    if role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    try:
        await db.set_ro_mode(message.chat.id, False)
        await message.answer(
            "✍️ <b>Режим только чтение выключен!</b>\n\n"
            "Все пользователи могут отправлять сообщения.",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")

# =============================================================================
# КОМАНДА - GBAN/UNGBAN
# =============================================================================

@router.message(Command("gban"))
async def cmd_gban(message: Message):
    """Глобальный бан"""
    role = await get_caller_role(message)
    if role < 7:
        await message.reply("❌ Недостаточно прав! Требуется уровень 7+")
        return

    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)

    if not target:
        await message.reply(
            "❌ <b>Не удалось определить пользователя</b>\n\n"
            "<b>Использование:</b>\n<code>/gban @username [причина]</code>\n<code>/gban ID [причина]</code>",
            parse_mode="HTML"
        )
        return

    target_role = await get_role(target)
    
    if target_role >= role:
        await message.reply(f"❌ Нельзя забанить! Роль цели: {ROLE_NAMES.get(target_role)} ({target_role})")
        return
    
    if target_role > 0:
        await message.reply(
            f"⚠️ <b>Внимание!</b>\n\n"
            f"Пользователь является членом команды:\n"
            f"Роль: {ROLE_NAMES.get(target_role)} ({target_role})\n\n"
            f"Для глобального бана сначала снимите роль:\n"
            f"<code>/removerole {await mention(target)}</code>",
            parse_mode="HTML"
        )
        return

    reason = args[2] if len(args) > 2 else "Глобальный бан"
    caller_id = await get_caller_id_safe(message)

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить глобальный бан", callback_data=f"confirmgban:{target}:{caller_id}")
    builder.button(text="❌ Отмена", callback_data=f"cancel:{target}:0")
    builder.adjust(1)

    target_name = await mention(target)
    text = (
        f"🌐 <b>Подтвердите глобальный бан</b>\n\n"
        f"Пользователь: {target_name}\n"
        f"ID: <code>{target}</code>\n"
        f"Причина: {reason}\n\n"
        f"⚠️ Пользователь будет забанен во всех модерируемых чатах!"
    )

    await db.cache_warn_reason(target, 0, reason)
    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())

@router.message(Command("ungban"))
async def cmd_ungban(message: Message):
    """Снять глобальный бан"""
    role = await get_caller_role(message)
    if role < 7:
        await message.reply("❌ Недостаточно прав! Требуется уровень 7+")
        return

    args = get_args(message)
    target = await parse_user(message, args)

    if not target:
        await message.reply("❌ Укажите пользователя: /ungban @user")
        return

    try:
        await db.remove_global_ban(target)
        target_name = await mention(target)
        await message.answer(f"✅ Глобальный бан снят!\n\nПользователь: {target_name}", parse_mode="HTML")
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")

# =============================================================================
# КОМАНДА - SETROLE/REMOVEROLE
# =============================================================================

@router.message(Command("setrole"))
async def cmd_setrole(message: Message):
    """Назначить роль"""
    caller_role = await get_caller_role(message)
    if caller_role < 7:
        await message.reply("❌ Недостаточно прав! Требуется уровень 7+")
        return

    args = get_args(message)
    if len(args) < 3:
        await message.reply(
            "❌ Использование: /setrole @user <роль>\n\n"
            "<b>Доступные роли:</b>\n" + "\n".join([f"{k}: {v}" for k, v in ROLE_NAMES.items()]),
            parse_mode="HTML"
        )
        return

    target = await parse_user(message, args)
    if not target:
        await message.reply("❌ Пользователь не найден")
        return

    try:
        new_role = int(args[2])
        if new_role < 0 or new_role > 10:
            await message.reply("❌ Роль должна быть от 0 до 10")
            return
    except ValueError:
        await message.reply("❌ Роль должна быть числом от 0 до 10")
        return

    target_current_role = await get_role(target)
    
    if new_role >= caller_role:
        await message.reply(f"❌ Вы не можете назначить роль выше или равную вашей! Ваша роль: {caller_role}")
        return

    if target_current_role >= caller_role:
        await message.reply(f"❌ Вы не можете изменить роль этого пользователя!")
        return

    try:
        await db.set_global_role(target, new_role)
        target_name = await mention(target)
        await message.answer(
            f"⭐ <b>Роль назначена!</b>\n\n"
            f"Пользователь: {target_name}\n"
            f"Новая роль: {ROLE_NAMES.get(new_role)} ({new_role})\n"
            f"Предыдущая роль: {ROLE_NAMES.get(target_current_role)} ({target_current_role})",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")

@router.message(Command("removerole"))
async def cmd_removerole(message: Message):
    """Снять роль"""
    caller_role = await get_caller_role(message)
    if caller_role < 7:
        await message.reply("❌ Недостаточно прав! Требуется уровень 7+")
        return

    args = get_args(message)
    target = await parse_user(message, args)

    if not target:
        await message.reply("❌ Укажите пользователя: /removerole @user")
        return

    target_current_role = await get_role(target)
    
    if target_current_role >= caller_role:
        await message.reply(f"❌ Вы не можете снять роль у этого пользователя!")
        return

    if target_current_role == 0:
        await message.reply("ℹ️ У пользователя уже нет роли")
        return

    try:
        await db.set_global_role(target, 0)
        target_name = await mention(target)
        await message.answer(
            f"✅ <b>Роль снята!</b>\n\n"
            f"Пользователь: {target_name}\n"
            f"Предыдущая роль: {ROLE_NAMES.get(target_current_role)} ({target_current_role})",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")

# =============================================================================
# КОМАНДА - STAFF
# =============================================================================

@router.message(Command("staff"))
async def cmd_staff(message: Message):
    """Список команды"""
    staff_list = await db.get_all_staff()
    
    if not staff_list:
        await message.answer("ℹ️ Список команды пуст")
        return

    by_role = {}
    for user_id, role in staff_list:
        if role not in by_role:
            by_role[role] = []
        by_role[role].append(user_id)

    text = "👥 <b>Команда модерации</b>\n\n"
    
    for role in sorted(by_role.keys(), reverse=True):
        text += f"<b>{ROLE_NAMES.get(role, 'Неизвестно')} ({role}):</b>\n"
        for user_id in by_role[role]:
            name = await mention(user_id)
            text += f"  • {name}\n"
        text += "\n"

    await message.answer(text, parse_mode="HTML")

# =============================================================================
# КОМАНДА - SETNICK
# =============================================================================

@router.message(Command("setnick"))
async def cmd_setnick(message: Message):
    """Установить ник"""
    role = await get_caller_role(message)
    if role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)

    if not target or len(args) < 3:
        await message.reply("❌ Использование: /setnick @user <ник>")
        return

    nick = args[2]
    await db.set_nick(target, message.chat.id, nick)
    
    target_name = await mention(target, message.chat.id)
    await message.answer(f"📝 Ник установлен!\nПользователь: {target_name}\nНовый ник: {nick}", parse_mode="HTML")

# =============================================================================
# КОМАНДА - CLEAR
# =============================================================================

@router.message(Command("clear"))
async def cmd_clear(message: Message):
    """Очистить сообщения"""
    role = await get_caller_role(message)
    if role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message)
    
    if len(args) < 2:
        await message.reply("❌ Использование: /clear <количество>")
        return

    try:
        count = int(args[1])
        if count < 1 or count > 100:
            await message.reply("❌ Количество должно быть от 1 до 100")
            return
    except ValueError:
        await message.reply("❌ Количество должно быть числом")
        return

    deleted = 0
    current_msg_id = message.message_id

    try:
        for i in range(1, count + 1):
            try:
                await bot.delete_message(message.chat.id, current_msg_id - i)
                deleted += 1
                await asyncio.sleep(0.3)
            except Exception:
                pass

        status_msg = await message.answer(f"🧹 Очищено {deleted} из {count} сообщений")
        await asyncio.sleep(3)
        await status_msg.delete()
        await message.delete()
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


# =============================================================================
# CALLBACK HANDLERS - ПОДТВЕРЖДЕНИЯ
# =============================================================================

@router.callback_query(F.data.startswith("confirmwarn:"))
async def cb_confirm_warn(call: CallbackQuery):
    """Подтверждение варна"""
    parts = call.data.split(":")
    target, chat_id, caller_id = int(parts[1]), int(parts[2]), int(parts[3])
    
    role = await get_role(call.from_user.id, chat_id)
    if role < 1:
        await call.answer("❌ Недостаточно прав!", show_alert=True)
        return

    target_role = await get_role(target, chat_id)
    if target_role >= role:
        await call.answer("❌ Нельзя выдать варн!", show_alert=True)
        return

    try:
        reason = await db.get_cached_warn_reason(target, chat_id) or "Нарушение правил"
        warns = await db.add_warn(target, chat_id, caller_id, reason)
        target_name = await mention(target, chat_id)
        
        if warns >= MAX_WARNS:
            await bot.ban_chat_member(chat_id, target)
            await asyncio.sleep(0.5)
            await bot.unban_chat_member(chat_id, target)
            await db.clear_warns(target, chat_id)
            
            buttons = create_info_buttons(target, chat_id)
            await call.message.edit_text(
                f"⚠️ {target_name} получил предупреждение!\n"
                f"Причина: {reason}\n\n"
                f"👢 <b>Кикнут за {MAX_WARNS} предупреждения!</b>",
                parse_mode="HTML",
                reply_markup=buttons.as_markup()
            )
        else:
            buttons = create_warned_buttons(target, chat_id)
            await call.message.edit_text(
                f"⚠️ {target_name} получил предупреждение!\n"
                f"Причина: {reason}\n"
                f"Предупреждений: {warns}/{MAX_WARNS}",
                parse_mode="HTML",
                reply_markup=buttons.as_markup()
            )
        
        await call.answer("✅ Варн выдан!")
        await db.clear_cached_warn_reason(target, chat_id)
        
    except Exception as e:
        await call.answer(f"❌ Ошибка: {e}", show_alert=True)

@router.callback_query(F.data.startswith("confirmgban:"))
async def cb_confirm_gban(call: CallbackQuery):
    """Подтверждение глобального бана"""
    parts = call.data.split(":")
    target, caller_id = int(parts[1]), int(parts[2])
    
    role = await get_role(call.from_user.id)
    if role < 7:
        await call.answer("❌ Недостаточно прав!", show_alert=True)
        return

    target_role = await get_role(target)
    if target_role >= role or target_role > 0:
        await call.answer("❌ Нельзя забанить!", show_alert=True)
        return

    try:
        reason = await db.get_cached_warn_reason(target, 0) or "Глобальный бан"
        await db.add_global_ban(target, caller_id, reason)
        
        target_name = await mention(target)
        await call.message.edit_text(
            f"🌐 <b>Глобальный бан применен!</b>\n\n"
            f"Пользователь: {target_name}\n"
            f"Причина: {reason}\n\n"
            f"✅ Пользователь будет забанен во всех модерируемых чатах.",
            parse_mode="HTML"
        )
        await call.answer("✅ Глобальный бан применен!", show_alert=True)
        await db.clear_cached_warn_reason(target, 0)
    except Exception as e:
        await call.answer(f"❌ Ошибка: {e}", show_alert=True)

@router.callback_query(F.data.startswith("applymute:"))
async def cb_apply_mute(call: CallbackQuery):
    """Применить мут"""
    parts = call.data.split(":")
    target, chat_id, seconds = int(parts[1]), int(parts[2]), int(parts[3])
    
    role = await get_role(call.from_user.id, chat_id)
    if role < 1:
        await call.answer("❌ Недостаточно прав!", show_alert=True)
        return

    target_role = await get_role(target, chat_id)
    if target_role >= role:
        await call.answer("❌ Нельзя замутить!", show_alert=True)
        return

    limit = MUTE_LIMITS.get(role, 0)
    if limit > 0 and (seconds == 0 or seconds > limit):
        await call.answer(f"❌ Ваш лимит мута: {limit // 60} минут!", show_alert=True)
        return

    try:
        until = int(time.time()) + seconds if seconds > 0 else 0
        duration_delta = timedelta(seconds=seconds) if seconds > 0 else None
        
        await bot.restrict_chat_member(chat_id, target, permissions=muted_permissions(), until_date=duration_delta)
        await db.add_mute(target, chat_id, call.from_user.id, "Мут", until)
        
        target_name = await mention(target, chat_id)
        duration_text = f"{seconds // 60} минут" if seconds < 3600 else f"{seconds // 3600} часов" if seconds < 86400 else f"{seconds // 86400} дней" if seconds > 0 else "навсегда"
        
        buttons = create_muted_buttons(target, chat_id)
        await call.message.edit_text(
            f"🔇 {target_name} замучен на {duration_text}",
            parse_mode="HTML",
            reply_markup=buttons.as_markup()
        )
        await call.answer("✅ Мут применен!")
    except Exception as e:
        await call.answer(f"❌ Ошибка: {e}", show_alert=True)

@router.callback_query(F.data.startswith("applyban:"))
async def cb_apply_ban(call: CallbackQuery):
    """Применить бан"""
    parts = call.data.split(":")
    target, chat_id, seconds = int(parts[1]), int(parts[2]), int(parts[3])
    
    role = await get_role(call.from_user.id, chat_id)
    if role < 3:
        await call.answer("❌ Недостаточно прав!", show_alert=True)
        return

    target_role = await get_role(target, chat_id)
    if target_role >= role:
        await call.answer("❌ Нельзя забанить!", show_alert=True)
        return

    try:
        duration_delta = timedelta(seconds=seconds) if seconds > 0 else None
        
        await bot.ban_chat_member(chat_id, target, until_date=duration_delta)
        await db.add_ban(target, chat_id, call.from_user.id, "Бан")
        
        target_name = await mention(target, chat_id)
        duration_text = f"{seconds // 86400} дней" if seconds >= 86400 else f"{seconds // 60} минут" if seconds > 0 else "навсегда"
        
        buttons = create_banned_buttons(target, chat_id)
        await call.message.edit_text(
            f"🚫 {target_name} забанен на {duration_text}",
            parse_mode="HTML",
            reply_markup=buttons.as_markup()
        )
        await call.answer("✅ Бан применен!")
    except Exception as e:
        await call.answer(f"❌ Ошибка: {e}", show_alert=True)

# =============================================================================
# CALLBACK HANDLERS - ДЕЙСТВИЯ ИЗ КНОПОК
# =============================================================================

@router.callback_query(F.data.startswith("unmute:"))
async def cb_unmute(call: CallbackQuery):
    """Размутить через кнопку"""
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    
    role = await get_role(call.from_user.id, chat_id)
    if role < 1:
        await call.answer("❌ Недостаточно прав!", show_alert=True)
        return

    try:
        await bot.restrict_chat_member(chat_id, target, permissions=full_permissions())
        await db.remove_mute(target, chat_id)
        
        target_name = await mention(target, chat_id)
        buttons = create_info_buttons(target, chat_id)
        
        await call.message.edit_text(
            f"🔊 {target_name} размучен!",
            parse_mode="HTML",
            reply_markup=buttons.as_markup()
        )
        await call.answer("✅ Размучен!")
    except Exception as e:
        await call.answer(f"❌ Ошибка: {e}", show_alert=True)

@router.callback_query(F.data.startswith("unban:"))
async def cb_unban(call: CallbackQuery):
    """Разбанить через кнопку"""
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    
    role = await get_role(call.from_user.id, chat_id)
    if role < 3:
        await call.answer("❌ Недостаточно прав!", show_alert=True)
        return

    try:
        await bot.unban_chat_member(chat_id, target)
        await db.remove_ban(target, chat_id)
        
        target_name = await mention(target, chat_id)
        buttons = create_info_buttons(target, chat_id)
        
        await call.message.edit_text(
            f"✅ {target_name} разбанен!",
            parse_mode="HTML",
            reply_markup=buttons.as_markup()
        )
        await call.answer("✅ Разбанен!")
    except Exception as e:
        await call.answer(f"❌ Ошибка: {e}", show_alert=True)

@router.callback_query(F.data.startswith("unwarn:"))
async def cb_unwarn(call: CallbackQuery):
    """Снять варн через кнопку"""
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    
    role = await get_role(call.from_user.id, chat_id)
    if role < 1:
        await call.answer("❌ Недостаточно прав!", show_alert=True)
        return

    try:
        warns = await db.remove_warn(target, chat_id)
        target_name = await mention(target, chat_id)
        buttons = create_info_buttons(target, chat_id)
        
        await call.message.edit_text(
            f"✅ Предупреждение снято!\nПользователь: {target_name}\nОсталось: {warns}/{MAX_WARNS}",
            parse_mode="HTML",
            reply_markup=buttons.as_markup()
        )
        await call.answer("✅ Варн снят!")
    except Exception as e:
        await call.answer(f"❌ Ошибка: {e}", show_alert=True)

@router.callback_query(F.data.startswith("stats:"))
async def cb_stats(call: CallbackQuery):
    """Статистика через кнопку"""
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    
    info = await get_user_info(target)
    role = await get_role(target, chat_id)
    warns = await db.get_warns(target, chat_id)
    is_muted = await db.is_muted(target, chat_id)
    is_banned = await db.is_banned(target, chat_id)
    
    stats_text = (
        f"📊 <b>Статистика</b>\n\n"
        f"ID: <code>{target}</code>\n"
    )
    
    if info['username']:
        stats_text += f"Username: @{info['username']}\n"
    
    stats_text += (
        f"Роль: {ROLE_NAMES.get(role, 'Пользователь')} ({role})\n"
        f"Варны: {warns}/{MAX_WARNS}\n"
        f"Мут: {'✅' if is_muted else '❌'}\n"
        f"Бан: {'✅' if is_banned else '❌'}"
    )
    
    await call.answer(stats_text, show_alert=True)

@router.callback_query(F.data.startswith("clear:"))
async def cb_clear(call: CallbackQuery):
    """Очистить сообщения через кнопку"""
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    
    role = await get_role(call.from_user.id, chat_id)
    if role < 1:
        await call.answer("❌ Недостаточно прав!", show_alert=True)
        return

    await call.answer("🧹 Очистка последних 10 сообщений...")
    
    deleted = 0
    try:
        current_msg_id = call.message.message_id
        for i in range(1, 11):
            try:
                await bot.delete_message(chat_id, current_msg_id - i)
                deleted += 1
                await asyncio.sleep(0.3)
            except Exception:
                pass
        
        await call.answer(f"✅ Очищено {deleted} сообщений", show_alert=True)
    except Exception as e:
        await call.answer(f"❌ Ошибка: {e}", show_alert=True)

@router.callback_query(F.data.startswith("cancel:"))
async def cb_cancel(call: CallbackQuery):
    """Отмена действия"""
    await call.message.edit_text("❌ Действие отменено", reply_markup=None)
    await call.answer("Отменено")


# =============================================================================
# ХЕНДЛЕРЫ СОБЫТИЙ
# =============================================================================

@router.chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def on_user_join(event: ChatMemberUpdated):
    """Обработка входа в чат"""
    user_id = event.new_chat_member.user.id
    chat_id = event.chat.id

    # Проверка на глобальный бан
    if await db.is_globally_banned(user_id):
        try:
            await bot.ban_chat_member(chat_id, user_id)
            buttons = create_info_buttons(user_id, chat_id)
            await bot.send_message(
                chat_id,
                f"🚫 Пользователь {await mention(user_id)} имеет глобальный бан и был удален из чата.",
                parse_mode="HTML",
                reply_markup=buttons.as_markup()
            )
        except Exception as e:
            logger.error(f"Ошибка при бане пользователя {user_id}: {e}")
        return

    # Кэширование username
    if event.new_chat_member.user.username:
        await db.cache_username(user_id, event.new_chat_member.user.username)

    # Приветственное сообщение
    welcome = await db.get_welcome(chat_id)
    if welcome:
        name = event.new_chat_member.user.full_name
        text = welcome.replace("{user}", name)
        buttons = create_info_buttons(user_id, chat_id)
        await bot.send_message(chat_id, text, reply_markup=buttons.as_markup())

@router.message(F.text)
async def on_message(message: Message):
    """Обработка текстовых сообщений"""
    if message.chat.type == ChatType.PRIVATE:
        return

    if not message.from_user:
        return

    user_id = message.from_user.id
    chat_id = message.chat.id

    # Кэш username
    if message.from_user.username:
        await db.cache_username(user_id, message.from_user.username)

    role = await get_role(user_id, chat_id)

    # Проверка глобального бана
    if await db.is_globally_banned(user_id):
        try:
            await bot.ban_chat_member(chat_id, user_id)
            await message.delete()
            buttons = create_info_buttons(user_id, chat_id)
            await bot.send_message(
                chat_id,
                f"🚫 {await mention(user_id)} забанен глобально!",
                parse_mode="HTML",
                reply_markup=buttons.as_markup()
            )
        except Exception:
            pass
        return

    # Проверка режима RO
    if role < 1 and await db.is_ro_mode(chat_id):
        try:
            await message.delete()
        except Exception:
            pass
        return

    # Антифлуд
    if role < 1 and await db.is_antiflood(chat_id):
        now = time.time()
        spam = await db.check_spam(user_id, chat_id, now)
        
        if spam >= SPAM_COUNT:
            try:
                await db.clear_spam(user_id, chat_id)
                until = int(time.time()) + 1800
                await db.add_mute(user_id, chat_id, 0, "Спам", until)
                await bot.restrict_chat_member(
                    chat_id, user_id,
                    permissions=muted_permissions(),
                    until_date=timedelta(minutes=30)
                )
                await message.delete()
                
                buttons = create_muted_buttons(user_id, chat_id)
                await bot.send_message(
                    chat_id,
                    f"🔇 {await mention(user_id)} замучен на 30 мин за спам",
                    parse_mode="HTML",
                    reply_markup=buttons.as_markup()
                )
            except Exception:
                pass
            return

    # Фильтр слов
    if role < 1 and message.text and await db.is_filter(chat_id):
        banwords = await db.get_banwords(chat_id)
        text_lower = message.text.lower()
        for word in banwords:
            if word in text_lower:
                try:
                    await message.delete()
                    until = int(time.time()) + 1800
                    await db.add_mute(user_id, chat_id, 0, "Запрещённое слово", until)
                    await bot.restrict_chat_member(
                        chat_id, user_id,
                        permissions=muted_permissions(),
                        until_date=timedelta(minutes=30)
                    )
                    
                    buttons = create_muted_buttons(user_id, chat_id)
                    await bot.send_message(
                        chat_id,
                        f"🔇 {await mention(user_id)} замучен за запрещённое слово",
                        parse_mode="HTML",
                        reply_markup=buttons.as_markup()
                    )
                except Exception:
                    pass
                return

# =============================================================================
# ЗАПУСК
# =============================================================================

async def main():
    global db
    db = Database("database.db")
    await db.init()

    logger.info("🔵 Модерация Анонимные сообщения | Георгиевка v5.0")
    logger.info("Инициализация...")

    await init_staff()

    for chat_id in MODERATED_CHATS:
        try:
            chat = await bot.get_chat(chat_id)
            await db.register_chat(chat_id, chat.title or "")
            logger.info(f"Чат зарегистрирован: {chat_id} ({chat.title})")
        except Exception as e:
            logger.warning(f"Ошибка регистрации чата {chat_id}: {e}")

    await register_commands()

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("✅ Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
