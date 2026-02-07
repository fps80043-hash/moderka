"""
🔵 Модерация Анонимные сообщения | Георгиевка
Telegram бот для модерации групп - ВЕРСИЯ 4.1 (ПОЛНЫЙ ФИКс)

Обновления v4.1:
- Команда /ro теперь работает для всего чата (кроме staff)
- Команда /unro снимает режим RO для всего чата
- Staff может писать даже в режиме RO

Обновления v4:
- preset_staff работает по ID вместо username
- Улучшена статистика (больше информации)
- Глобальный бан требует снятия роли у членов команды
- Исправлена команда help и все связанные команды
- Добавлены инлайн-кнопки для всех команд модерации
- Поддержка команд без @botusername
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
PRESET_STAFF = config.get("preset_staff", {})  # {"user_id": role, ...}  # ИЗМЕНЕНО: теперь ID
MAX_WARNS = config.get("max_warns", 3)
SPAM_INTERVAL = config.get("spam_interval_seconds", 2)
SPAM_COUNT = config.get("spam_messages_count", 3)
ANON_ADMIN_ROLE = config.get("anon_admin_role", 10)

ANONYMOUS_BOT_ID = 1087968824  # @GroupAnonymousBot

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

# Лимиты мута по ролям (0 = без лимита)
MUTE_LIMITS = {1: 3600, 2: 3600, 3: 86400, 4: 86400, 5: 0, 6: 0, 7: 0, 8: 0, 9: 0, 10: 0}


# =============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =============================================================================

def is_anon(message: Message) -> bool:
    """Сообщение от анонимного админа?"""
    if message.from_user and message.from_user.id == ANONYMOUS_BOT_ID:
        return True
    if message.sender_chat and message.sender_chat.id == message.chat.id:
        return True
    return False


def get_args(message: Message, maxsplit: int = -1) -> list:
    """
    Получить аргументы команды, убирая @botusername если он есть.
    Поддерживает команды с / ! и другими префиксами.
    """
    if not message.text:
        return []
    
    text = message.text
    parts = text.split(maxsplit=1)
    
    if not parts:
        return []
    
    # Убираем @botusername из команды если он есть
    command = parts[0]
    if '@' in command:
        command = command.split('@')[0]
    
    # Пересобираем текст без @botusername
    if len(parts) > 1:
        clean_text = command + ' ' + parts[1]
    else:
        clean_text = command
    
    # Применяем maxsplit если указан
    if maxsplit >= 0:
        return clean_text.split(maxsplit=maxsplit)
    return clean_text.split()


async def get_caller_role(message: Message) -> int:
    """Получить роль вызывающего команду"""
    if is_anon(message):
        return ANON_ADMIN_ROLE

    if not message.from_user:
        return 0

    uid = message.from_user.id
    return await get_role(uid, message.chat.id)


async def get_caller_id_safe(message: Message) -> int:
    """Получить ID вызывающего"""
    if is_anon(message):
        return 0
    if message.from_user:
        return message.from_user.id
    return 0


async def get_role(user_id: int, chat_id: int = 0) -> int:
    """Получить роль пользователя (глобальная приоритетнее)"""
    if user_id == 0 or user_id == ANONYMOUS_BOT_ID:
        return 0
    global_role = await db.get_global_role(user_id)
    if global_role > 0:
        return global_role
    if chat_id:
        return await db.get_user_role(user_id, chat_id)
    return 0


async def get_user_info(user_id: int) -> dict:
    """Получить инфо о пользователе"""
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
    """Резолв username → user_id (кэш + API)"""
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
    """Парсер пользователя"""
    # 1. Реплай
    if message.reply_to_message:
        reply = message.reply_to_message
        if not is_anon(reply):
            if reply.from_user:
                return reply.from_user.id

    # 2. Forward
    if message.forward_from:
        return message.forward_from.id

    # 3-6. Из аргументов
    if len(args) <= start_idx:
        return None

    arg = args[start_idx].strip()

    # Username
    if arg.startswith("@"):
        return await resolve_username(arg)

    # ID
    if arg.isdigit():
        return int(arg)

    # Ник в чате
    nick_user = await db.get_user_by_nick(arg, message.chat.id)
    if nick_user:
        return nick_user

    # Username без @
    return await resolve_username(arg)


def muted_permissions() -> ChatPermissions:
    """Права для мута"""
    return ChatPermissions(
        can_send_messages=False,
        can_send_audios=False,
        can_send_documents=False,
        can_send_photos=False,
        can_send_videos=False,
        can_send_video_notes=False,
        can_send_voice_notes=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
        can_change_info=False,
        can_invite_users=False,
        can_pin_messages=False,
        can_manage_topics=False
    )


def readonly_permissions() -> ChatPermissions:
    """Только чтение (RO)"""
    return ChatPermissions(
        can_send_messages=False,
        can_send_audios=False,
        can_send_documents=False,
        can_send_photos=False,
        can_send_videos=False,
        can_send_video_notes=False,
        can_send_voice_notes=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
        can_change_info=False,
        can_invite_users=True,
        can_pin_messages=False,
        can_manage_topics=False
    )


def full_permissions() -> ChatPermissions:
    """Полные права"""
    return ChatPermissions(
        can_send_messages=True,
        can_send_audios=True,
        can_send_documents=True,
        can_send_photos=True,
        can_send_videos=True,
        can_send_video_notes=True,
        can_send_voice_notes=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_change_info=False,
        can_invite_users=True,
        can_pin_messages=False,
        can_manage_topics=False
    )


# =============================================================================
# СОЗДАНИЕ ИНЛАЙН-КНОПОК
# =============================================================================

def create_duration_keyboard(action: str, target_id: int, chat_id: int) -> InlineKeyboardBuilder:
    """Создать клавиатуру выбора времени"""
    builder = InlineKeyboardBuilder()
    
    durations = [
        ("5 мин", 300),
        ("30 мин", 1800),
        ("1 час", 3600),
        ("6 часов", 21600),
        ("1 день", 86400),
        ("7 дней", 604800),
        ("30 дней", 2592000),
        ("Навсегда", 0)
    ]
    
    for label, seconds in durations:
        callback_data = f"{action}:{target_id}:{chat_id}:{seconds}"
        builder.button(text=label, callback_data=callback_data)
    
    builder.adjust(2)
    return builder


# =============================================================================
# РЕГИСТРАЦИЯ КОМАНД
# =============================================================================

async def register_commands():
    """Регистрация команд бота"""
    # Команды для групп
    group_commands = [
        BotCommand(command="start", description="🚀 Запустить бота"),
        BotCommand(command="help", description="❓ Помощь"),
        BotCommand(command="stats", description="📊 Статистика пользователя"),
        BotCommand(command="warn", description="⚠️ Выдать предупреждение"),
        BotCommand(command="unwarn", description="✅ Снять предупреждение"),
        BotCommand(command="mute", description="🔇 Замутить пользователя"),
        BotCommand(command="unmute", description="🔊 Размутить пользователя"),
        BotCommand(command="ban", description="🚫 Забанить пользователя"),
        BotCommand(command="unban", description="✅ Разбанить пользователя"),
        BotCommand(command="kick", description="👢 Кикнуть пользователя"),
        BotCommand(command="ro", description="👁 Режим RO для чата"),
        BotCommand(command="unro", description="✍️ Снять режим RO"),
        BotCommand(command="setnick", description="📝 Установить ник"),
        BotCommand(command="clear", description="🧹 Очистить сообщения"),
        BotCommand(command="gban", description="🌐 Глобальный бан"),
        BotCommand(command="ungban", description="🌐 Снять глобальный бан"),
        BotCommand(command="setrole", description="⭐ Назначить роль"),
        BotCommand(command="removerole", description="❌ Снять роль"),
        BotCommand(command="staff", description="👥 Список команды"),
    ]

    # Команды для ЛС
    private_commands = [
        BotCommand(command="start", description="🚀 Запустить бота"),
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
# ИНИЦИАЛИЗАЦИЯ КОМАНДЫ (ИЗМЕНЕНО: работа с ID)
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
# КОМАНДЫ - START, HELP
# =============================================================================

@router.message(Command("start"))
async def cmd_start(message: Message):
    text = (
        "👋 <b>Привет!</b>\n\n"
        "Я бот для модерации групп.\n\n"
        "📋 <b>Основные команды:</b>\n"
        "• /warn - выдать предупреждение\n"
        "• /mute - замутить\n"
        "• /ban - забанить\n"
        "• /kick - кикнуть\n"
        "• /stats - статистика\n\n"
        "ℹ️ Используй /help для подробной справки"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "📖 <b>Справка по командам бота</b>\n\n"
        "<b>Модерация:</b>\n"
        "• /warn @user [причина] - предупреждение\n"
        "• /unwarn @user - снять варн\n"
        "• /mute @user - мут (выбор времени)\n"
        "• /unmute @user - размутить\n"
        "• /ban @user - бан (выбор времени)\n"
        "• /unban @user - разбанить\n"
        "• /kick @user [причина] - кикнуть\n"
        "• /ro - режим RO для всего чата (кроме staff)\n"
        "• /unro - снять режим RO\n\n"
        "<b>Глобальные команды (7+):</b>\n"
        "• /gban @user [причина] - глобальный бан\n"
        "• /ungban @user - снять глобальный бан\n\n"
        "<b>Управление (7+):</b>\n"
        "• /setrole @user <роль> - назначить роль (0-10)\n"
        "• /removerole @user - снять роль\n"
        "• /setnick @user <ник> - установить ник\n"
        "• /clear <N> - очистить N сообщений\n\n"
        "<b>Информация:</b>\n"
        "• /stats [@user] - статистика\n"
        "• /staff - список команды\n\n"
        "💡 Можно использовать команды без @botusername"
    )
    await message.answer(text, parse_mode="HTML")


# =============================================================================
# КОМАНДА - STATS (УЛУЧШЕННАЯ)
# =============================================================================

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Статистика пользователя - УЛУЧШЕННАЯ ВЕРСИЯ"""
    if message.chat.type == ChatType.PRIVATE:
        if not message.from_user:
            return
        user_id = message.from_user.id
        role = await get_role(user_id)
        
        # Получаем дополнительную информацию
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
    
    # Получаем информацию о муте
    mute_info = await db.get_mute_info(target, chat_id) if is_muted else None
    ban_info = await db.get_ban_info(target, chat_id) if is_banned else None
    
    # Получаем ник
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
# КОМАНДА - WARN/UNWARN (С КНОПКАМИ)
# =============================================================================

@router.message(Command("warn"))
async def cmd_warn(message: Message):
    """Выдать предупреждение с инлайн-кнопками"""
    role = await get_caller_role(message)
    if role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)

    if not target:
        await message.reply("❌ Укажите пользователя: /warn @user [причина]")
        return

    target_role = await get_role(target, message.chat.id)
    if target_role >= role:
        await message.reply("❌ Нельзя выдать предупреждение пользователю с такой же или более высокой ролью!")
        return

    reason = args[2] if len(args) > 2 else "Нарушение правил"
    caller_id = await get_caller_id_safe(message)

    # Создаем кнопки
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить варн", callback_data=f"confirmwarn:{target}:{message.chat.id}:{caller_id}")
    builder.button(text="❌ Отмена", callback_data=f"cancelaction:{target}:{message.chat.id}")
    builder.adjust(1)

    target_name = await mention(target, message.chat.id)
    text = (
        f"⚠️ <b>Выдать предупреждение?</b>\n\n"
        f"Пользователь: {target_name}\n"
        f"Причина: {reason}\n\n"
        f"Выберите действие:"
    )

    # Сохраняем причину
    await db.cache_warn_reason(target, message.chat.id, reason)

    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())


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
            
            await call.message.edit_text(
                f"⚠️ {target_name} получил предупреждение!\n"
                f"Причина: {reason}\n\n"
                f"👢 <b>Кикнут за {MAX_WARNS} предупреждения!</b>",
                parse_mode="HTML"
            )
            await call.answer(f"✅ Варн выдан! Кикнут за {MAX_WARNS} варна.", show_alert=True)
        else:
            await call.message.edit_text(
                f"⚠️ {target_name} получил предупреждение!\n"
                f"Причина: {reason}\n"
                f"Предупреждений: {warns}/{MAX_WARNS}",
                parse_mode="HTML"
            )
            await call.answer(f"✅ Варн выдан! Всего: {warns}/{MAX_WARNS}", show_alert=True)
        
        await db.clear_cached_warn_reason(target, chat_id)
        
    except Exception as e:
        await call.answer(f"❌ Ошибка: {e}", show_alert=True)


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
    
    await message.answer(
        f"✅ Предупреждение снято!\n"
        f"Пользователь: {target_name}\n"
        f"Осталось предупреждений: {warns}/{MAX_WARNS}",
        parse_mode="HTML"
    )


# =============================================================================
# КОМАНДА - MUTE/UNMUTE (С КНОПКАМИ)
# =============================================================================

@router.message(Command("mute"))
async def cmd_mute(message: Message):
    """Замутить с выбором времени"""
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
        await message.reply("❌ Нельзя замутить пользователя с такой же или более высокой ролью!")
        return

    # Кнопки выбора времени
    builder = create_duration_keyboard("applymute", target, message.chat.id)
    builder.button(text="❌ Отмена", callback_data=f"cancelaction:{target}:{message.chat.id}")
    builder.adjust(2)

    target_name = await mention(target, message.chat.id)
    text = (
        f"🔇 <b>Выберите время мута</b>\n\n"
        f"Пользователь: {target_name}\n\n"
        f"Выберите продолжительность:"
    )

    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())


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

    # Проверка лимита
    limit = MUTE_LIMITS.get(role, 0)
    if limit > 0 and (seconds == 0 or seconds > limit):
        await call.answer(f"❌ Ваш лимит мута: {limit // 60} минут!", show_alert=True)
        return

    try:
        until = int(time.time()) + seconds if seconds > 0 else 0
        duration_delta = timedelta(seconds=seconds) if seconds > 0 else None
        
        await bot.restrict_chat_member(
            chat_id, target,
            permissions=muted_permissions(),
            until_date=duration_delta
        )
        
        await db.add_mute(target, chat_id, call.from_user.id, "Мут", until)
        
        target_name = await mention(target, chat_id)
        duration_text = f"{seconds // 60} минут" if seconds < 3600 else f"{seconds // 3600} часов" if seconds < 86400 else f"{seconds // 86400} дней" if seconds > 0 else "навсегда"
        
        await call.message.edit_text(
            f"🔇 {target_name} замучен на {duration_text}",
            parse_mode="HTML"
        )
        await call.answer("✅ Мут применен!", show_alert=True)
        
    except Exception as e:
        await call.answer(f"❌ Ошибка: {e}", show_alert=True)


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
        await bot.restrict_chat_member(
            message.chat.id, target,
            permissions=full_permissions()
        )
        await db.remove_mute(target, message.chat.id)
        
        target_name = await mention(target, message.chat.id)
        await message.answer(f"🔊 {target_name} размучен!", parse_mode="HTML")
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


# =============================================================================
# КОМАНДА - BAN/UNBAN (С КНОПКАМИ)
# =============================================================================

@router.message(Command("ban"))
async def cmd_ban(message: Message):
    """Забанить с выбором времени"""
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
        await message.reply("❌ Нельзя забанить пользователя с такой же или более высокой ролью!")
        return

    # Кнопки
    builder = create_duration_keyboard("applyban", target, message.chat.id)
    builder.button(text="❌ Отмена", callback_data=f"cancelaction:{target}:{message.chat.id}")
    builder.adjust(2)

    target_name = await mention(target, message.chat.id)
    text = (
        f"🚫 <b>Выберите время бана</b>\n\n"
        f"Пользователь: {target_name}\n\n"
        f"Выберите продолжительность:"
    )

    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("applyban:"))
async def cb_apply_ban(call: CallbackQuery):
    """Применить бан"""
    parts = call.data.split(":")
    target, chat_id, seconds = int(parts[1]), int(parts[2]), int(parts[3])
    
    role = await get_role(call.from_user.id, chat_id)
    if role < 3:
        await call.answer("❌ Недостаточно прав! Требуется уровень 3+", show_alert=True)
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
        
        await call.message.edit_text(
            f"🚫 {target_name} забанен на {duration_text}",
            parse_mode="HTML"
        )
        await call.answer("✅ Бан применен!", show_alert=True)
        
    except Exception as e:
        await call.answer(f"❌ Ошибка: {e}", show_alert=True)


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
        await message.answer(f"✅ {target_name} разбанен!", parse_mode="HTML")
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
        await message.reply("❌ Нельзя кикнуть пользователя с такой же или более высокой ролью!")
        return

    reason = args[2] if len(args) > 2 else "Кик"

    try:
        await bot.ban_chat_member(message.chat.id, target)
        await asyncio.sleep(0.5)
        await bot.unban_chat_member(message.chat.id, target)
        
        target_name = await mention(target, message.chat.id)
        await message.answer(
            f"👢 {target_name} кикнут!\nПричина: {reason}",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


# =============================================================================
# КОМАНДА - RO/UNRO (РЕЖИМ ТОЛЬКО ЧТЕНИЕ ДЛЯ ВСЕГО ЧАТА)
# =============================================================================

@router.message(Command("ro"))
async def cmd_ro(message: Message):
    """Включить режим только чтение для всего чата (кроме staff)"""
    role = await get_caller_role(message)
    if role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    chat_id = message.chat.id
    
    try:
        # Включаем режим RO в базе
        await db.set_ro_mode(chat_id, True)
        
        await message.answer(
            "👁 <b>Режим только чтение включен!</b>\n\n"
            "Обычные пользователи не могут отправлять сообщения.\n"
            "Staff может продолжать работу.",
            parse_mode="HTML"
        )
        logger.info(f"Режим RO включен в чате {chat_id}")
        
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


@router.message(Command("unro"))
async def cmd_unro(message: Message):
    """Выключить режим только чтение"""
    role = await get_caller_role(message)
    if role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    chat_id = message.chat.id
    
    try:
        # Выключаем режим RO в базе
        await db.set_ro_mode(chat_id, False)
        
        await message.answer(
            "✍️ <b>Режим только чтение выключен!</b>\n\n"
            "Все пользователи могут отправлять сообщения.",
            parse_mode="HTML"
        )
        logger.info(f"Режим RO выключен в чате {chat_id}")
        
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


# =============================================================================
# КОМАНДА - GBAN/UNGBAN (ИСПРАВЛЕНО С КНОПКАМИ)
# =============================================================================

@router.message(Command("gban"))
async def cmd_gban(message: Message):
    """Глобальный бан - требуется снятие роли для членов команды"""
    role = await get_caller_role(message)
    if role < 7:
        await message.reply("❌ Недостаточно прав! Требуется уровень 7+")
        return

    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)

    if not target:
        await message.reply("❌ Укажите пользователя: /gban @user [причина]")
        return

    target_role = await get_role(target)
    
    # Проверка уровня роли
    if target_role >= role:
        await message.reply(
            f"❌ Нельзя забанить пользователя с такой же или более высокой ролью!\n"
            f"Ваша роль: {ROLE_NAMES.get(role)} ({role})\n"
            f"Роль цели: {ROLE_NAMES.get(target_role)} ({target_role})"
        )
        return
    
    # НОВОЕ: Если у цели есть роль (член команды), требуем сначала снять роль
    if target_role > 0:
        await message.reply(
            f"⚠️ <b>Внимание!</b>\n\n"
            f"Пользователь является членом команды:\n"
            f"Роль: {ROLE_NAMES.get(target_role)} ({target_role})\n\n"
            f"Для глобального бана сначала необходимо снять роль:\n"
            f"<code>/removerole @user</code>",
            parse_mode="HTML"
        )
        return

    reason = args[2] if len(args) > 2 else "Глобальный бан"
    caller_id = await get_caller_id_safe(message)

    # Создаем кнопки подтверждения
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить глобальный бан", callback_data=f"confirmgban:{target}:{caller_id}")
    builder.button(text="❌ Отмена", callback_data=f"cancelaction:{target}:0")
    builder.adjust(1)

    target_name = await mention(target)
    text = (
        f"🌐 <b>Подтвердите глобальный бан</b>\n\n"
        f"Пользователь: {target_name}\n"
        f"Причина: {reason}\n\n"
        f"⚠️ Пользователь будет забанен во всех модерируемых чатах!\n\n"
        f"Выберите действие:"
    )

    # Сохраняем причину
    await db.cache_warn_reason(target, 0, reason)  # chat_id=0 для глобального

    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())


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
    if target_role >= role:
        await call.answer("❌ Нельзя забанить!", show_alert=True)
        return
    
    # Проверка что роль снята
    if target_role > 0:
        await call.answer("❌ Сначала снимите роль пользователя!", show_alert=True)
        return

    try:
        reason = await db.get_cached_warn_reason(target, 0) or "Глобальный бан"
        await db.add_global_ban(target, caller_id, reason)
        
        target_name = await mention(target)
        await call.message.edit_text(
            f"🌐 <b>Глобальный бан применен!</b>\n\n"
            f"Пользователь: {target_name}\n"
            f"Причина: {reason}\n\n"
            f"✅ Пользователь будет забанен во всех модерируемых чатах при следующей активности.",
            parse_mode="HTML"
        )
        await call.answer("✅ Глобальный бан применен!", show_alert=True)
        
        await db.clear_cached_warn_reason(target, 0)
        logger.info(f"Глобальный бан: user_id={target}, by={caller_id}, reason={reason}")
        
    except Exception as e:
        await call.answer(f"❌ Ошибка: {e}", show_alert=True)


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
        await message.answer(
            f"✅ Глобальный бан снят!\n\nПользователь: {target_name}",
            parse_mode="HTML"
        )
        logger.info(f"Глобальный бан снят: user_id={target}")
        
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
            "<b>Доступные роли:</b>\n" + 
            "\n".join([f"{k}: {v}" for k, v in ROLE_NAMES.items()]),
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
        await message.reply(
            f"❌ Вы не можете назначить роль выше или равную вашей!\n"
            f"Ваша роль: {ROLE_NAMES.get(caller_role)} ({caller_role})"
        )
        return

    if target_current_role >= caller_role:
        await message.reply(
            f"❌ Вы не можете изменить роль этого пользователя!\n"
            f"Текущая роль пользователя: {ROLE_NAMES.get(target_current_role)} ({target_current_role})\n"
            f"Ваша роль: {ROLE_NAMES.get(caller_role)} ({caller_role})"
        )
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
        logger.info(f"Роль изменена: user_id={target}, new_role={new_role}, by={message.from_user.id if message.from_user else 'anon'}")
        
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
        await message.reply(
            f"❌ Вы не можете снять роль у этого пользователя!\n"
            f"Текущая роль пользователя: {ROLE_NAMES.get(target_current_role)} ({target_current_role})\n"
            f"Ваша роль: {ROLE_NAMES.get(caller_role)} ({caller_role})"
        )
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
            f"Предыдущая роль: {ROLE_NAMES.get(target_current_role)} ({target_current_role})\n"
            f"Новая роль: {ROLE_NAMES.get(0)} (0)",
            parse_mode="HTML"
        )
        logger.info(f"Роль снята: user_id={target}, old_role={target_current_role}, by={message.from_user.id if message.from_user else 'anon'}")
        
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
    await message.answer(
        f"📝 Ник установлен!\n"
        f"Пользователь: {target_name}\n"
        f"Новый ник: {nick}",
        parse_mode="HTML"
    )


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
            await bot.send_message(
                chat_id,
                f"🚫 Пользователь {await mention(user_id)} имеет глобальный бан и был удален из чата.",
                parse_mode="HTML"
            )
            logger.info(f"Глобально забаненный пользователь {user_id} удален из чата {chat_id}")
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
        await bot.send_message(chat_id, text)


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
            await bot.send_message(
                chat_id,
                f"🚫 {await mention(user_id)} забанен глобально!",
                parse_mode="HTML"
            )
        except Exception:
            pass
        return

    # Проверка режима RO (только чтение для всего чата)
    if role < 1 and await db.is_ro_mode(chat_id):
        try:
            await message.delete()
            # Опционально: можно отправить предупреждение (закомментировано чтобы не спамить)
            # await bot.send_message(
            #     chat_id,
            #     f"👁 Режим только чтение! {await mention(user_id)}",
            #     parse_mode="HTML"
            # )
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
                await bot.send_message(
                    chat_id,
                    f"🔇 {await mention(user_id)} замучен на 30 мин за спам",
                    parse_mode="HTML"
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
                    await bot.send_message(
                        chat_id,
                        f"🔇 {await mention(user_id)} замучен за запрещённое слово",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
                return


# =============================================================================
# CALLBACK HANDLERS
# =============================================================================

@router.callback_query(F.data.startswith("cancelaction:"))
async def cb_cancel_action(call: CallbackQuery):
    """Отмена действия"""
    await call.message.edit_text("❌ Действие отменено", reply_markup=None)
    await call.answer("Отменено", show_alert=False)


# =============================================================================
# ЗАПУСК
# =============================================================================

async def main():
    global db
    db = Database("database.db")
    await db.init()

    logger.info("🔵 Модерация Анонимные сообщения | Георгиевка v4.1")
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
