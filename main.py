"""
🔵 Модерация Анонимные сообщения | Георгиевка
Telegram бот для модерации групп - ВЕРСИЯ 2 (ПОЛНЫЙ ФИКС)

Исправления v2:
- Анонимные админы (sender_chat) могут использовать ВСЕ команды
- preset_staff работает по username (как в оригинале)
- Меню команд через set_my_commands (кнопка "/")
- Все ChatPermissions обновлены под новый API
- Все 11 ролей (0-10) корректно
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
PRESET_STAFF = config.get("preset_staff", {})  # {"username": role, ...}
MAX_WARNS = config.get("max_warns", 3)
SPAM_INTERVAL = config.get("spam_interval_seconds", 2)
SPAM_COUNT = config.get("spam_messages_count", 3)
ANON_ADMIN_ROLE = config.get("anon_admin_role", 10)  # Роль для анонимных админов

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
    Например: '/stats@mybot @user' -> ['/stats', '@user']
    
    Args:
        message: Сообщение с командой
        maxsplit: Максимальное количество разделений (-1 = без ограничений)
    """
    if not message.text:
        return []
    
    text = message.text
    parts = text.split(maxsplit=1)
    
    if not parts:
        return []
    
    # Убираем @botusername из команды если он есть
    # Например: /stats@mybot -> /stats
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
    """
    Получить роль вызывающего команду.
    Если анонимный админ — возвращаем ANON_ADMIN_ROLE.
    Если обычный пользователь — из БД.
    """
    if is_anon(message):
        return ANON_ADMIN_ROLE

    if not message.from_user:
        return 0

    uid = message.from_user.id
    return await get_role(uid, message.chat.id)


async def get_caller_id_safe(message: Message) -> int:
    """
    Получить ID вызывающего.
    Для анонимных — возвращает 0 (неизвестен), но это OK для модерации.
    """
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
    """
    Парсер пользователя:
    1. Реплай (не на анонима)
    2. Forward (пересланное сообщение)
    3. @username
    4. Числовой ID
    5. Ник в чате
    6. Username без @
    """
    # 1. Реплай
    if message.reply_to_message:
        reply = message.reply_to_message
        # Проверяем от кого сообщение
        if reply.from_user and reply.from_user.id != ANONYMOUS_BOT_ID:
            user = reply.from_user
            if user.username:
                await db.cache_username(user.id, user.username)
            return user.id
        # Проверяем forward_from (пересланное от пользователя)
        if reply.forward_from and reply.forward_from.id != ANONYMOUS_BOT_ID:
            user = reply.forward_from
            if user.username:
                await db.cache_username(user.id, user.username)
            return user.id

    # 2. Аргументы
    if len(args) <= start_idx:
        return None

    arg = args[start_idx].strip()

    # Числовой ID
    if arg.lstrip('-').isdigit():
        return int(arg)

    # @username - сначала проверяем кэш
    if arg.startswith('@'):
        username = arg[1:].lower()
        # Проверяем кэш
        cached = await db.get_user_by_username(username)
        if cached:
            return cached
        # Пытаемся через API
        resolved = await resolve_username(username)
        if resolved:
            return resolved
        # Не нашли - сообщаем пользователю
        logger.warning(f"Не удалось найти пользователя @{username}")
        return None

    # Ник в чате
    if message.chat.id:
        by_nick = await db.get_user_by_nick(arg, message.chat.id)
        if by_nick:
            return by_nick

    # Username без @ - сначала кэш
    username_lower = arg.lower()
    cached = await db.get_user_by_username(username_lower)
    if cached:
        return cached
    
    # Пытаемся через API
    return await resolve_username(arg)


def parse_time(s: str) -> Optional[int]:
    if not s:
        return None
    s = s.lower().strip()
    mult = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800}
    for suffix, m in mult.items():
        if s.endswith(suffix):
            try:
                return int(s[:-1]) * m
            except Exception:
                return None
    try:
        return int(s) * 60
    except Exception:
        return None


def format_time(sec: int) -> str:
    if sec < 60: return f"{sec}с"
    if sec < 3600: return f"{sec // 60}м"
    if sec < 86400: return f"{sec // 3600}ч"
    return f"{sec // 86400}д"


def format_dt(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")


def muted_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=False, can_send_audios=False,
        can_send_documents=False, can_send_photos=False,
        can_send_videos=False, can_send_video_notes=False,
        can_send_voice_notes=False, can_send_polls=False,
        can_send_other_messages=False, can_add_web_page_previews=False,
        can_change_info=False, can_invite_users=False,
        can_pin_messages=False, can_manage_topics=False
    )


def full_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=True, can_send_audios=True,
        can_send_documents=True, can_send_photos=True,
        can_send_videos=True, can_send_video_notes=True,
        can_send_voice_notes=True, can_send_polls=True,
        can_send_other_messages=True, can_add_web_page_previews=True,
        can_change_info=False, can_invite_users=True,
        can_pin_messages=False, can_manage_topics=False
    )


def has_reply_target(message: Message) -> bool:
    """Есть ли реплай на НЕ-анонимное сообщение"""
    return (message.reply_to_message is not None
            and message.reply_to_message.from_user is not None
            and message.reply_to_message.from_user.id != ANONYMOUS_BOT_ID)


async def init_staff():
    """
    Инициализация команды из конфига.
    Поддерживает формат: {"username": role, ...}
    Резолвит username → user_id через API.
    """
    for key, role_val in PRESET_STAFF.items():
        try:
            role = int(role_val)
            if role < 1 or role > 10:
                continue

            # Ключ — это username или user_id?
            if key.lstrip('-').isdigit():
                # Числовой ID
                user_id = int(key)
                await db.set_global_role(user_id, role, None)
                logger.info(f"Staff init: ID {user_id} → роль {role}")
            else:
                # Username — резолвим
                username = key.lstrip('@').lower()
                try:
                    user = await bot.get_chat(f"@{username}")
                    await db.set_global_role(user.id, role, username)
                    await db.cache_username(user.id, username)
                    logger.info(f"Staff init: @{username} (ID {user.id}) → роль {role} ({ROLE_NAMES.get(role)})")
                except Exception as e:
                    # Не удалось резолвить — сохраняем только username, без ID
                    # Когда пользователь напишет в чат, его ID закэшируется
                    logger.warning(f"Не удалось найти @{username}: {e}. "
                                   f"Роль будет назначена когда пользователь напишет в чат.")
                    await db.save_pending_staff(username, role)

        except (ValueError, TypeError) as e:
            logger.warning(f"Ошибка инициализации стаффа {key}: {e}")


async def register_commands():
    """Регистрация меню команд (кнопка /)"""
    # Команды для групп
    group_commands = [
        BotCommand(command="help", description="📋 Команды бота"),
        BotCommand(command="id", description="🆔 Узнать ID"),
        BotCommand(command="mod", description="🛡 Панель модерации"),
        BotCommand(command="stats", description="📊 Статистика пользователя"),
        BotCommand(command="mystatus", description="👤 Мой статус"),
        BotCommand(command="staff", description="👥 Состав команды"),
        BotCommand(command="top", description="🏆 Топ по сообщениям"),
        BotCommand(command="mute", description="🔇 Замутить пользователя"),
        BotCommand(command="unmute", description="🔊 Снять мут"),
        BotCommand(command="warn", description="⚠️ Выдать предупреждение"),
        BotCommand(command="unwarn", description="✅ Снять варн"),
        BotCommand(command="kick", description="👢 Кикнуть"),
        BotCommand(command="ban", description="🚫 Забанить"),
        BotCommand(command="unban", description="✅ Разбанить"),
        BotCommand(command="setnick", description="📝 Установить ник"),
        BotCommand(command="del", description="🗑 Удалить сообщение"),
        BotCommand(command="clear", description="🧹 Очистить сообщения"),
        BotCommand(command="setrole", description="⚙️ Установить роль"),
        BotCommand(command="gban", description="🌐 Глобальный бан"),
        BotCommand(command="addstaff", description="➕ Добавить в команду"),
        BotCommand(command="quiet", description="🔇 Режим тишины"),
        BotCommand(command="antiflood", description="🛡 Антифлуд"),
        BotCommand(command="filter", description="🔠 Фильтр слов"),
        BotCommand(command="banword", description="🚫 Запретить слово"),
        BotCommand(command="welcome", description="👋 Приветствие"),
        BotCommand(command="broadcast", description="📢 Рассылка"),
    ]

    # Команды для ЛС
    private_commands = [
        BotCommand(command="start", description="▶️ Запуск бота"),
        BotCommand(command="help", description="📋 Команды бота"),
        BotCommand(command="mystatus", description="👤 Мой статус"),
        BotCommand(command="staff", description="👥 Состав команды"),
    ]

    try:
        await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())
        await bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())
        logger.info("Команды бота зарегистрированы")
    except Exception as e:
        logger.warning(f"Не удалось зарегистрировать команды: {e}")


# =============================================================================
# ОБРАБОТКА ВХОДА В ГРУППУ
# =============================================================================

@router.chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def on_user_join(event: ChatMemberUpdated):
    user = event.new_chat_member.user
    chat_id = event.chat.id

    if user.is_bot:
        return

    await db.register_chat(chat_id, event.chat.title or "")

    if user.username:
        await db.cache_username(user.id, user.username)
        # Проверяем отложенную роль
        await db.apply_pending_staff(user.id, user.username)

    # Глобальный бан
    gban = await db.get_global_ban(user.id)
    if gban:
        try:
            await bot.ban_chat_member(chat_id, user.id)
            await bot.send_message(
                chat_id,
                f"🚫 <b>Глобальный бан</b>\n\n"
                f"{await mention(user.id)} заблокирован во всех группах.\n"
                f"<b>Причина:</b> {gban.get('reason', '-')}",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"GBAN kick error: {e}")
        return

    # Локальный бан
    ban = await db.get_ban(user.id, chat_id)
    if ban:
        try:
            await bot.ban_chat_member(chat_id, user.id)
            await bot.send_message(
                chat_id,
                f"🚫 {await mention(user.id)} забанен в этом чате.\n"
                f"<b>Причина:</b> {ban.get('reason', '-')}",
                parse_mode="HTML"
            )
        except Exception:
            pass
        return

    # Приветствие
    welcome = await db.get_welcome(chat_id)
    if welcome:
        welcome = welcome.replace("%name%", user.first_name or "друг")
        welcome = welcome.replace("%fullname%", user.full_name or "друг")
        welcome = welcome.replace("%mention%", await mention(user.id))
        welcome = welcome.replace("%id%", str(user.id))
        welcome = welcome.replace("%username%", f"@{user.username}" if user.username else user.full_name)
        try:
            await bot.send_message(chat_id, welcome, parse_mode="HTML")
        except Exception:
            pass


# =============================================================================
# ОСНОВНЫЕ КОМАНДЫ
# =============================================================================

@router.message(Command("start", "старт", "активировать"))
async def cmd_start(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        await message.answer(
            "🔵 <b>Модерация Анонимные сообщения | Георгиевка</b>\n\n"
            "Бот для модерации групп.\n\n"
            "📋 /help — команды\n"
            "👤 /mystatus — ваш статус\n"
            "👥 /staff — команда\n\n"
            "Добавьте бота в группу администратором.",
            parse_mode="HTML"
        )
    else:
        await db.register_chat(message.chat.id, message.chat.title or "")
        await message.answer("✅ Бот активирован в этом чате!")


@router.message(Command("help", "помощь", "хелп", "команды", "commands"))
async def cmd_help(message: Message):
    role = await get_caller_role(message)

    text = "🔵 <b>Модерация Анонимные сообщения | Георгиевка</b>\n"
    text += "👥 <b>Команды бота</b>\n\n"

    text += "<b>👤 Для всех:</b>\n"
    text += "/id — узнать ID\n"
    text += "/stats — статистика\n"
    text += "/mystatus — мой статус\n"
    text += "/staff — команда\n"
    text += "/top — топ сообщений\n\n"

    if role >= 1:
        text += "<b>🛡 Модератор (1-2):</b>\n"
        text += "/mod @user — панель модерации с кнопками\n"
        text += "/mute @user 30m причина — мут\n"
        text += "/unmute @user — снять мут\n"
        text += "/warn @user причина — варн\n"
        text += "/unwarn @user — снять варн\n"
        text += "/getwarn — инфо о варнах\n"
        text += "/warnhistory — история варнов\n"
        text += "/warnlist — список с варнами\n"
        text += "/kick @user — кик\n"
        text += "/del — удалить сообщение (реплай)\n"
        text += "/clear @user — очистить сообщения\n"
        text += "/setnick @user ник — ник\n"
        text += "/removenick @user — удалить ник\n"
        text += "/getnick — узнать ник\n"
        text += "/getacc ник — найти по нику\n"
        text += "/nlist — список ников\n"
        text += "/mutelist — список мутов\n\n"

    if role >= 3:
        text += "<b>🛡 Старший модератор (3-4):</b>\n"
        text += "/ban @user причина — бан\n"
        text += "/unban @user — разбан\n"
        text += "/getban — инфо о бане\n"
        text += "/banlist — список банов\n"
        text += "/zov — упомянуть всех\n"
        text += "/online — команда чата\n"
        text += "/addmoder @user — модер (1)\n"
        text += "/removerole @user — снять роль\n\n"

    if role >= 5:
        text += "<b>⚙️ Тех. специалист (5-6):</b>\n"
        text += "/setrole @user роль — установить\n"
        text += "/banword слово — запретить\n"
        text += "/unbanword — разрешить\n"
        text += "/banwords — список\n"
        text += "/filter — фильтр вкл/выкл\n"
        text += "/welcome текст — приветствие\n"
        text += "/quiet — тишина\n"
        text += "/antiflood — антифлуд\n"
        text += "/rnickall — удалить все ники\n\n"

    if role >= 7:
        text += "<b>👑 Куратор (7-8):</b>\n"
        text += "/addadmin @user — админ (3)\n"
        text += "/addsenadmin @user — ст. админ (5)\n\n"

    if role >= 9:
        text += "<b>🌐 Главный модератор (9-10):</b>\n"
        text += "/gban @user — глоб. бан\n"
        text += "/gunban @user — снять глоб. бан\n"
        text += "/gbanlist — список глоб. банов\n"
        text += "/addstaff @user роль — в команду\n"
        text += "/removestaff @user — из команды\n"
        text += "/broadcast текст — рассылка\n"

    await message.answer(text, parse_mode="HTML")


@router.message(Command("id", "ид", "getid"))
async def cmd_id(message: Message):
    args = get_args(message)
    target = await parse_user(message, args, 1)

    if not target:
        if is_anon(message):
            await message.answer(
                f"🆔 <b>ID чата:</b> <code>{message.chat.id}</code>\n"
                f"<b>Название:</b> {message.chat.title or '-'}\n\n"
                f"<i>Вы отправляете анонимно — ваш личный ID не виден.</i>",
                parse_mode="HTML"
            )
            return
        target = message.from_user.id

    info = await get_user_info(target)
    text = f"🆔 <b>ID:</b> <code>{target}</code>\n"
    text += f"<b>Имя:</b> {info['full_name']}\n"
    if info['username']:
        text += f"<b>Username:</b> @{info['username']}"
    await message.answer(text, parse_mode="HTML")


@router.message(Command("mod", "модер", "moderate"))
async def cmd_mod(message: Message):
    """Панель быстрой модерации с инлайн-кнопками"""
    if message.chat.type == ChatType.PRIVATE:
        await message.reply("❌ Команда работает только в группах!")
        return

    my_role = await get_caller_role(message)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message)
    target = await parse_user(message, args, 1)

    if not target:
        await message.reply(
            "❌ Укажите пользователя!\n\n"
            "<b>Использование:</b>\n"
            "• /mod (реплай на сообщение)\n"
            "• /mod @username\n"
            "• /mod ID",
            parse_mode="HTML"
        )
        return

    target_role = await get_role(target, message.chat.id)
    caller_id = await get_caller_id_safe(message)

    # Проверка прав
    if target == caller_id:
        await message.reply("❌ Нельзя модерировать самого себя!")
        return

    if target_role >= my_role:
        await message.reply("❌ Нельзя модерировать этого пользователя (роль выше или равна вашей)!")
        return

    # Получаем информацию о пользователе
    info = await get_user_info(target)
    warns = await db.get_warns_count(target, message.chat.id)
    nick = await db.get_nick(target, message.chat.id)
    msg_count = await db.get_message_count(target, message.chat.id)
    mute = await db.get_mute(target, message.chat.id)
    ban = await db.get_ban(target, message.chat.id)

    text = f"🛡 <b>Панель модерации</b>\n\n"
    text += f"<b>Пользователь:</b> {await mention(target, message.chat.id)}\n"
    text += f"<b>ID:</b> <code>{target}</code>\n"
    if info['username']:
        text += f"<b>Username:</b> @{info['username']}\n"
    if nick:
        text += f"<b>Ник:</b> {nick}\n"
    text += f"<b>Роль:</b> {ROLE_NAMES.get(target_role, '?')} ({target_role})\n"
    text += f"<b>Варнов:</b> {warns}/{MAX_WARNS}\n"
    text += f"<b>Сообщений:</b> {msg_count}\n"

    if mute and mute.get('until', 0) > time.time():
        text += f"\n🔇 <b>Замучен до:</b> {format_dt(mute['until'])}"

    if ban:
        text += f"\n🚫 <b>Забанен:</b> {ban.get('reason', '-')}"

    # Создаём кнопки модерации
    kb = InlineKeyboardBuilder()
    chat_id = message.chat.id

    # Первый ряд - варн и мут
    if my_role >= 1:
        kb.button(text="⚠️ Варн", callback_data=f"quickwarn:{target}:{chat_id}")
        kb.button(text="🔇 Мут 30м", callback_data=f"qmute:{target}:{chat_id}")

    # Второй ряд - kick и ban
    if my_role >= 1:
        kb.button(text="👢 Кик", callback_data=f"quickkick:{target}:{chat_id}")
    if my_role >= 3:
        kb.button(text="🚫 Бан", callback_data=f"quickban:{target}:{chat_id}")

    # Третий ряд - снятие наказаний (если есть)
    if warns > 0 and my_role >= 1:
        kb.button(text="✅ Снять варн", callback_data=f"unwarn:{target}:{chat_id}")
    if mute and mute.get('until', 0) > time.time() and my_role >= 1:
        kb.button(text="🔊 Размут", callback_data=f"unmute:{target}:{chat_id}")
    if ban and my_role >= 3:
        kb.button(text="✅ Разбан", callback_data=f"unban:{target}:{chat_id}")

    # Четвёртый ряд - дополнительно
    if my_role >= 1:
        kb.button(text="📜 История варнов", callback_data=f"wh:{target}:{chat_id}")
        kb.button(text="🧹 Очистить сообщения", callback_data=f"quickclear:{target}:{chat_id}")

    kb.adjust(2, 2, 2, 2)

    await message.answer(text, parse_mode="HTML", reply_markup=kb.as_markup())


@router.message(Command("stats", "стата", "статистика"))
async def cmd_stats(message: Message):
    args = get_args(message)
    target = await parse_user(message, args, 1)

    if not target:
        if is_anon(message):
            await message.reply("❌ Укажите пользователя: /stats @username или /stats ID")
            return
        target = message.from_user.id

    chat_id = message.chat.id
    info = await get_user_info(target)
    role = await get_role(target, chat_id)
    warns = await db.get_warns_count(target, chat_id)
    nick = await db.get_nick(target, chat_id)
    msg_count = await db.get_message_count(target, chat_id)
    mute = await db.get_mute(target, chat_id)
    ban = await db.get_ban(target, chat_id)
    gban = await db.get_global_ban(target)

    text = f"📊 <b>Статистика</b>\n\n"
    text += f"<b>ID:</b> <code>{target}</code>\n"
    text += f"<b>Имя:</b> {info['full_name']}\n"
    if info['username']:
        text += f"<b>Username:</b> @{info['username']}\n"
    text += f"<b>Ник:</b> {nick or 'Нет'}\n"
    text += f"<b>Роль:</b> {ROLE_NAMES.get(role, '?')} ({role})\n"
    text += f"<b>Варнов:</b> {warns}/{MAX_WARNS}\n"
    text += f"<b>Сообщений:</b> {msg_count}\n"

    if mute and mute.get('until', 0) > time.time():
        text += f"🔇 <b>Мут до:</b> {format_dt(mute['until'])}\n"
    if ban:
        text += f"🚫 <b>Бан:</b> {ban.get('reason', '-')}\n"
    if gban:
        text += f"🚫 <b>Глобальный бан:</b> {gban.get('reason', '-')}\n"

    # Кнопки для модераторов
    my_role = await get_caller_role(message)
    caller_id = await get_caller_id_safe(message)
    if my_role >= 1 and target != caller_id:
        kb = InlineKeyboardBuilder()
        kb.button(text="📜 История варнов", callback_data=f"wh:{target}:{chat_id}")
        kb.button(text="🔇 Мут 30м", callback_data=f"qmute:{target}:{chat_id}")
        kb.adjust(2)
        await message.answer(text, parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await message.answer(text, parse_mode="HTML")


@router.message(Command("mystatus"))
async def cmd_mystatus(message: Message):
    if is_anon(message):
        await message.reply(
            "❌ Вы отправляете анонимно — не могу определить ваш аккаунт.\n"
            "Используйте: /stats @ваш_username"
        )
        return
    # Подменяем текст и вызываем stats
    message.text = f"/stats {message.from_user.id}"
    await cmd_stats(message)


@router.message(Command("staff", "стафф", "команда"))
async def cmd_staff(message: Message):
    chat_id = message.chat.id

    global_staff = await db.get_all_staff()
    local_staff = await db.get_chat_staff(chat_id) if message.chat.type != ChatType.PRIVATE else []

    if not global_staff and not local_staff:
        await message.answer("📋 Команда пуста")
        return

    text = "🔵 <b>Модерация Анонимные сообщения | Георгиевка</b>\n"
    text += "👥 <b>Состав команды</b>\n\n"

    # Собираем всех
    all_members = {}

    for s in global_staff:
        uid = s['user_id']
        all_members[uid] = {'role': s['role'], 'username': s.get('username', ''), 'source': 'global'}

    for s in local_staff:
        uid = s['user_id']
        if uid not in all_members:
            cached = await db.get_username_by_id(uid)
            all_members[uid] = {'role': s['role'], 'username': cached or '', 'source': 'local'}

    # Также показываем отложенные (pending) роли
    pending = await db.get_all_pending_staff()
    for p in pending:
        # pending не имеют user_id, показываем только username
        found = False
        for uid, data in all_members.items():
            if data.get('username', '').lower() == p['username'].lower():
                found = True
                break
        if not found:
            all_members[f"pending_{p['username']}"] = {
                'role': p['role'], 'username': p['username'],
                'source': 'pending', 'is_pending': True
            }

    # Группируем по ролям
    by_role = {}
    for uid, data in all_members.items():
        r = data['role']
        if r < 1:
            continue
        if r not in by_role:
            by_role[r] = []
        by_role[r].append((uid, data))

    for role_num in sorted(by_role.keys(), reverse=True):
        text += f"<b>{role_num:02d}. {ROLE_NAMES.get(role_num, '?')}</b>\n"
        for uid, data in by_role[role_num]:
            uname = data.get('username', '')
            if uname:
                text += f"   @{uname}\n"
            elif isinstance(uid, int):
                text += f"   ID: <code>{uid}</code>\n"
        text += "\n"

    await message.answer(text, parse_mode="HTML")


@router.message(Command("reg", "registration", "регистрация"))
async def cmd_reg(message: Message):
    args = get_args(message)
    target = await parse_user(message, args, 1)
    if not target:
        target = message.from_user.id if message.from_user and not is_anon(message) else 0

    if not target:
        await message.reply("❌ Укажите пользователя: /reg @username")
        return

    await message.answer(
        f"🆔 ID: <code>{target}</code>\n"
        f"<i>Telegram не предоставляет дату регистрации</i>",
        parse_mode="HTML"
    )


# =============================================================================
# МУТ
# =============================================================================

@router.message(Command("mute", "мут"))
async def cmd_mute(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        return

    my_role = await get_caller_role(message)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message)
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply(
            "❌ Укажите пользователя!\n\n"
            "<b>Примеры:</b>\n"
            "• /mute @username 30m спам\n"
            "• /mute 123456789 1h причина\n"
            "• Ответьте на сообщение: /mute 30 причина",
            parse_mode="HTML"
        )
        return

    target_role = await get_role(target, message.chat.id)
    if target_role >= my_role:
        await message.reply("❌ Нельзя замутить пользователя с такой же или выше ролью!")
        return

    has_reply = has_reply_target(message)
    time_idx = 1 if has_reply else 2
    reason_idx = time_idx + 1

    time_str = args[time_idx] if len(args) > time_idx else "30"
    reason = " ".join(args[reason_idx:]) if len(args) > reason_idx else "Нарушение правил"

    duration = parse_time(time_str)
    if not duration:
        duration = 30 * 60

    limit = MUTE_LIMITS.get(my_role, 0)
    if limit > 0 and duration > limit:
        await message.reply(f"❌ Ваш лимит: {format_time(limit)}")
        return

    until = int(time.time()) + duration

    try:
        await bot.restrict_chat_member(
            message.chat.id, target,
            permissions=muted_permissions(),
            until_date=timedelta(seconds=duration)
        )
    except TelegramBadRequest as e:
        await message.reply(f"❌ Ошибка: {e.message}")
        return
    except TelegramForbiddenError:
        await message.reply("❌ У бота нет прав!")
        return
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")
        return

    caller_id = await get_caller_id_safe(message)
    await db.add_mute(target, message.chat.id, caller_id, reason, until)

    kb = InlineKeyboardBuilder()
    kb.button(text="🔓 Снять мут", callback_data=f"unmute:{target}:{message.chat.id}")
    kb.button(text="🧹 Очистить", callback_data=f"clear:{target}:{message.chat.id}")

    await message.answer(
        f"🔇 <b>Мут</b>\n\n"
        f"<b>Кто:</b> {await mention(target, message.chat.id)}\n"
        f"<b>Время:</b> {format_time(duration)}\n"
        f"<b>Причина:</b> {reason}\n"
        f"<b>Модератор:</b> {await mention(caller_id)}",
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )


@router.message(Command("unmute", "размут", "анмут"))
async def cmd_unmute(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        return

    my_role = await get_caller_role(message)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message)
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return

    try:
        await bot.restrict_chat_member(message.chat.id, target, permissions=full_permissions())
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")
        return

    await db.remove_mute(target, message.chat.id)
    await message.answer(f"✅ Мут снят: {await mention(target)}", parse_mode="HTML")


@router.callback_query(F.data.startswith("unmute:"))
async def cb_unmute(call: CallbackQuery):
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    role = await get_role(call.from_user.id, chat_id)
    if role < 1:
        await call.answer("Недостаточно прав!", show_alert=True)
        return
    try:
        await bot.restrict_chat_member(chat_id, target, permissions=full_permissions())
        await db.remove_mute(target, chat_id)
        await call.answer("✅ Мут снят!", show_alert=True)
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception as e:
        await call.answer(f"Ошибка: {e}", show_alert=True)


@router.message(Command("getmute", "gmute", "гетмут"))
async def cmd_getmute(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message)
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return

    mute = await db.get_mute(target, message.chat.id)
    if not mute or mute.get('until', 0) <= time.time():
        await message.answer(f"✅ У {await mention(target)} нет мута", parse_mode="HTML")
        return

    await message.answer(
        f"🔇 <b>Мут</b>\n\n"
        f"<b>Кто:</b> {await mention(target)}\n"
        f"<b>До:</b> {format_dt(mute['until'])}\n"
        f"<b>Причина:</b> {mute.get('reason', '-')}\n"
        f"<b>Модератор:</b> {await mention(mute['muted_by'])}",
        parse_mode="HTML"
    )


@router.message(Command("mutelist", "мутлист"))
async def cmd_mutelist(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    mutes = await db.get_mutes(message.chat.id)
    if not mutes:
        await message.answer("📋 Замученных нет")
        return

    text = "🔇 <b>Список мутов</b>\n\n"
    for m in mutes[:15]:
        text += f"• <code>{m['user_id']}</code> — до {format_dt(m['until'])}\n"
    if len(mutes) > 15:
        text += f"\n<i>...и ещё {len(mutes) - 15}</i>"
    await message.answer(text, parse_mode="HTML")


# =============================================================================
# ВАРНЫ
# =============================================================================

@router.message(Command("warn", "пред", "варн"))
async def cmd_warn(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        return

    my_role = await get_caller_role(message)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message)
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return

    target_role = await get_role(target, message.chat.id)
    if target_role >= my_role:
        await message.reply("❌ Нельзя выдать варн этому пользователю!")
        return

    has_reply = has_reply_target(message)
    reason_idx = 1 if has_reply else 2
    reason = " ".join(args[reason_idx:]) if len(args) > reason_idx else "Нарушение правил"

    caller_id = await get_caller_id_safe(message)
    warns = await db.add_warn(target, message.chat.id, caller_id, reason)

    kb = InlineKeyboardBuilder()
    kb.button(text="🔓 Снять варн", callback_data=f"unwarn:{target}:{message.chat.id}")
    kb.button(text="🧹 Очистить", callback_data=f"clear:{target}:{message.chat.id}")

    text = (
        f"⚠️ <b>Предупреждение</b>\n\n"
        f"<b>Кто:</b> {await mention(target, message.chat.id)}\n"
        f"<b>Причина:</b> {reason}\n"
        f"<b>Варнов:</b> {warns}/{MAX_WARNS}\n"
        f"<b>Модератор:</b> {await mention(caller_id)}"
    )

    if warns >= MAX_WARNS:
        try:
            await bot.ban_chat_member(message.chat.id, target)
            await asyncio.sleep(0.5)
            await bot.unban_chat_member(message.chat.id, target)
            await db.clear_warns(target, message.chat.id)
            text += f"\n\n👢 <b>Кикнут за {MAX_WARNS} варна!</b>"
            kb = None
        except Exception as e:
            text += f"\n\n⚠️ Не удалось кикнуть: {e}"

    await message.answer(text, parse_mode="HTML", reply_markup=kb.as_markup() if kb else None)


@router.message(Command("unwarn", "унварн", "снятьпред"))
async def cmd_unwarn(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message)
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return

    current = await db.get_warns_count(target, message.chat.id)
    if current < 1:
        await message.answer(f"✅ У {await mention(target)} нет варнов", parse_mode="HTML")
        return

    remaining = await db.remove_warn(target, message.chat.id)
    await message.answer(
        f"✅ Варн снят: {await mention(target)}\nОсталось: {remaining}/{MAX_WARNS}",
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("unwarn:"))
async def cb_unwarn(call: CallbackQuery):
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    role = await get_role(call.from_user.id, chat_id)
    if role < 1:
        await call.answer("Недостаточно прав!", show_alert=True)
        return
    remaining = await db.remove_warn(target, chat_id)
    await call.answer(f"✅ Варн снят. Осталось: {remaining}/{MAX_WARNS}", show_alert=True)
    await call.message.edit_reply_markup(reply_markup=None)


@router.message(Command("getwarn", "gwarn", "гетварн"))
async def cmd_getwarn(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message)
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return

    warn_info = await db.get_warn_info(target, message.chat.id)
    if not warn_info:
        await message.answer(f"✅ У {await mention(target)} нет варнов", parse_mode="HTML")
        return

    kb = InlineKeyboardBuilder()
    kb.button(text="📜 История", callback_data=f"wh:{target}:{message.chat.id}")

    await message.answer(
        f"⚠️ <b>Варны</b>\n\n"
        f"<b>Кто:</b> {await mention(target)}\n"
        f"<b>Варнов:</b> {warn_info['count']}/{MAX_WARNS}\n"
        f"<b>Причина:</b> {warn_info.get('reason', '-')}\n"
        f"<b>Модератор:</b> {await mention(warn_info['warned_by'])}\n"
        f"<b>Когда:</b> {format_dt(warn_info['warned_at'])}",
        parse_mode="HTML", reply_markup=kb.as_markup()
    )


@router.message(Command("warnhistory", "whistory", "историяварнов"))
async def cmd_warnhistory(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message)
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return

    history = await db.get_warn_history(target, message.chat.id, 10)
    if not history:
        await message.answer(f"📋 История варнов {await mention(target)} пуста", parse_mode="HTML")
        return

    text = f"📜 <b>История варнов</b> {await mention(target)}\n\n"
    for i, w in enumerate(history, 1):
        text += f"{i}) {await mention(w['warned_by'])} | {w.get('reason', '-')[:30]} | {format_dt(w['warned_at'])}\n"
    await message.answer(text, parse_mode="HTML")


@router.callback_query(F.data.startswith("wh:"))
async def cb_warnhistory(call: CallbackQuery):
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    history = await db.get_warn_history(target, chat_id, 5)
    if not history:
        await call.answer("История пуста", show_alert=True)
        return
    text = "📜 Последние варны:\n\n"
    for i, w in enumerate(history, 1):
        text += f"{i}) {w.get('reason', '-')[:25]} | {format_dt(w['warned_at'])}\n"
    await call.answer(text, show_alert=True)


@router.message(Command("warnlist", "варнлист"))
async def cmd_warnlist(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    warns = await db.get_warns_list(message.chat.id)
    if not warns:
        await message.answer("📋 Нет пользователей с варнами")
        return

    text = "⚠️ <b>Пользователи с варнами</b>\n\n"
    for w in warns[:15]:
        text += f"• <code>{w['user_id']}</code> — {w['count']}/{MAX_WARNS} | {w.get('reason', '-')[:20]}\n"
    await message.answer(text, parse_mode="HTML")


# =============================================================================
# БАН / КИК
# =============================================================================

@router.message(Command("ban", "бан"))
async def cmd_ban(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        return

    my_role = await get_caller_role(message)
    if my_role < 3:
        await message.reply("❌ Недостаточно прав! Нужен уровень 3+")
        return

    args = get_args(message)
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return

    target_role = await get_role(target, message.chat.id)
    if target_role >= my_role:
        await message.reply("❌ Нельзя забанить этого пользователя!")
        return

    has_reply = has_reply_target(message)
    reason = " ".join(args[1 if has_reply else 2:]) or "Нарушение правил"

    try:
        await bot.ban_chat_member(message.chat.id, target)
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")
        return

    caller_id = await get_caller_id_safe(message)
    await db.add_ban(target, message.chat.id, caller_id, reason)

    kb = InlineKeyboardBuilder()
    kb.button(text="🔓 Разбан", callback_data=f"unban:{target}:{message.chat.id}")

    await message.answer(
        f"🚫 <b>Бан</b>\n\n"
        f"<b>Кто:</b> {await mention(target, message.chat.id)}\n"
        f"<b>Причина:</b> {reason}\n"
        f"<b>Модератор:</b> {await mention(caller_id)}",
        parse_mode="HTML", reply_markup=kb.as_markup()
    )


@router.message(Command("unban", "разбан"))
async def cmd_unban(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 3:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message)
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return

    try:
        await bot.unban_chat_member(message.chat.id, target, only_if_banned=True)
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")
        return

    await db.remove_ban(target, message.chat.id)
    await message.answer(f"✅ Разбан: {await mention(target)}", parse_mode="HTML")


@router.callback_query(F.data.startswith("unban:"))
async def cb_unban(call: CallbackQuery):
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    role = await get_role(call.from_user.id, chat_id)
    if role < 3:
        await call.answer("Недостаточно прав!", show_alert=True)
        return
    try:
        await bot.unban_chat_member(chat_id, target, only_if_banned=True)
        await db.remove_ban(target, chat_id)
        await call.answer("✅ Разбанен!", show_alert=True)
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception as e:
        await call.answer(f"Ошибка: {e}", show_alert=True)


@router.message(Command("getban", "гетбан"))
async def cmd_getban(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message)
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return

    ban = await db.get_ban(target, message.chat.id)
    if not ban:
        await message.answer(f"✅ {await mention(target)} не забанен", parse_mode="HTML")
        return

    await message.answer(
        f"🚫 <b>Бан</b>\n\n"
        f"<b>Кто:</b> {await mention(target)}\n"
        f"<b>Причина:</b> {ban.get('reason', '-')}\n"
        f"<b>Модератор:</b> {await mention(ban['banned_by'])}\n"
        f"<b>Когда:</b> {format_dt(ban['banned_at'])}",
        parse_mode="HTML"
    )


@router.message(Command("banlist", "банлист"))
async def cmd_banlist(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 3:
        await message.reply("❌ Недостаточно прав!")
        return

    bans = await db.get_bans(message.chat.id)
    if not bans:
        await message.answer("📋 Забаненных нет")
        return

    text = "🚫 <b>Забаненные</b>\n\n"
    for b in bans[:15]:
        text += f"• <code>{b['user_id']}</code> | {b.get('reason', '-')[:25]}\n"
    await message.answer(text, parse_mode="HTML")


@router.message(Command("online", "онлайн"))
async def cmd_online(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 3:
        await message.reply("❌ Недостаточно прав!")
        return

    chat_staff = await db.get_chat_staff(message.chat.id)
    global_staff = await db.get_all_staff()

    if not chat_staff and not global_staff:
        await message.answer("📋 Команда пуста")
        return

    text = "👥 <b>Команда чата</b>\n\n"

    if global_staff:
        text += "<b>🌐 Глобальная:</b>\n"
        for s in global_staff[:10]:
            uname = s.get('username')
            name = f"@{uname}" if uname else f"ID: <code>{s['user_id']}</code>"
            text += f"• {name} — {ROLE_NAMES.get(s['role'], '?')} ({s['role']})\n"
        text += "\n"

    if chat_staff:
        text += "<b>🏠 Локальная:</b>\n"
        for s in chat_staff[:10]:
            cached = await db.get_username_by_id(s['user_id'])
            name = f"@{cached}" if cached else f"ID: <code>{s['user_id']}</code>"
            text += f"• {name} — {ROLE_NAMES.get(s['role'], '?')} ({s['role']})\n"

    await message.answer(text, parse_mode="HTML")


@router.message(Command("kick", "кик"))
async def cmd_kick(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        return

    my_role = await get_caller_role(message)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message)
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return

    target_role = await get_role(target, message.chat.id)
    if target_role >= my_role:
        await message.reply("❌ Нельзя кикнуть этого пользователя!")
        return

    try:
        await bot.ban_chat_member(message.chat.id, target)
        await asyncio.sleep(0.5)
        await bot.unban_chat_member(message.chat.id, target)
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")
        return

    has_reply = has_reply_target(message)
    reason = " ".join(args[1 if has_reply else 2:]) or ""

    text = f"👢 {await mention(target, message.chat.id)} кикнут"
    if reason:
        text += f"\n<b>Причина:</b> {reason}"
    await message.answer(text, parse_mode="HTML")


# =============================================================================
# НИКИ
# =============================================================================

@router.message(Command("setnick", "snick", "ник"))
async def cmd_setnick(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message)
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return

    target_role = await get_role(target, message.chat.id)
    if target_role > my_role:
        await message.reply("❌ Нельзя установить ник этому пользователю!")
        return

    has_reply = has_reply_target(message)
    nick = " ".join(args[1 if has_reply else 2:])
    if not nick:
        await message.reply("❌ Укажите ник!")
        return

    await db.set_nick(target, message.chat.id, nick)
    await message.answer(
        f"✅ Ник установлен\n<b>Кто:</b> {await mention(target)}\n<b>Ник:</b> {nick}",
        parse_mode="HTML"
    )


@router.message(Command("removenick", "rnick", "удалитьник"))
async def cmd_removenick(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return
    args = get_args(message)
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return
    await db.remove_nick(target, message.chat.id)
    await message.answer(f"✅ Ник удалён: {await mention(target)}", parse_mode="HTML")


@router.message(Command("getnick", "gnick", "гетник"))
async def cmd_getnick(message: Message):
    args = get_args(message)
    target = await parse_user(message, args, 1)
    if not target:
        if is_anon(message):
            await message.reply("❌ Укажите пользователя: /getnick @username")
            return
        target = message.from_user.id

    nick = await db.get_nick(target, message.chat.id)
    if nick:
        await message.answer(f"📝 Ник {await mention(target)}: <b>{nick}</b>", parse_mode="HTML")
    else:
        await message.answer(f"📝 У {await mention(target)} нет ника", parse_mode="HTML")


@router.message(Command("getacc", "acc", "аккаунт"))
async def cmd_getacc(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message, maxsplit=1)
    if len(args) < 2:
        await message.reply("❌ Укажите ник!")
        return

    nick = args[1]
    user_id = await db.get_user_by_nick(nick, message.chat.id)
    if not user_id:
        await message.answer(f"❌ Пользователь с ником «{nick}» не найден")
        return

    info = await get_user_info(user_id)
    await message.answer(
        f"🔍 <b>Найден по нику</b>\n\n"
        f"<b>Ник:</b> {nick}\n<b>ID:</b> <code>{user_id}</code>\n<b>Имя:</b> {info['full_name']}",
        parse_mode="HTML"
    )


@router.message(Command("nlist", "nicks", "ники"))
async def cmd_nlist(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return
    nicks = await db.get_nicks(message.chat.id)
    if not nicks:
        await message.answer("📋 Ников нет")
        return
    text = "📝 <b>Ники в чате</b>\n\n"
    for i, n in enumerate(nicks[:20], 1):
        text += f"{i}) <code>{n['user_id']}</code> — {n['nick']}\n"
    if len(nicks) > 20:
        text += f"\n<i>...и ещё {len(nicks) - 20}</i>"
    await message.answer(text, parse_mode="HTML")


@router.message(Command("rnickall", "clearnicks"))
async def cmd_rnickall(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 5:
        await message.reply("❌ Недостаточно прав!")
        return
    await db.clear_all_nicks(message.chat.id)
    await message.answer("✅ Все ники удалены")


# =============================================================================
# ГЛОБАЛЬНЫЙ БАН
# =============================================================================

@router.message(Command("gban", "глобан"))
async def cmd_gban(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 9:
        await message.reply("❌ Недостаточно прав! Нужен уровень 9+")
        return

    args = get_args(message)
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return

    target_role = await db.get_global_role(target)
    if target_role > 0:
        await message.reply("❌ Нельзя забанить члена команды!")
        return

    has_reply = has_reply_target(message)
    reason = " ".join(args[1 if has_reply else 2:]) or "Глобальное нарушение"

    caller_id = await get_caller_id_safe(message)
    await db.add_global_ban(target, caller_id, reason)

    chats = await db.get_all_chats()
    banned_count = 0
    for chat in chats:
        try:
            await bot.ban_chat_member(chat['chat_id'], target)
            banned_count += 1
        except Exception:
            pass

    await message.answer(
        f"🚫 <b>Глобальный бан</b>\n\n"
        f"<b>Кто:</b> {await mention(target)}\n"
        f"<b>Причина:</b> {reason}\n"
        f"<b>Забанен в:</b> {banned_count} чатах\n"
        f"<b>Модератор:</b> {await mention(caller_id)}",
        parse_mode="HTML"
    )


@router.message(Command("gunban", "глобразбан"))
async def cmd_gunban(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 9:
        await message.reply("❌ Недостаточно прав!")
        return
    args = get_args(message)
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return
    await db.remove_global_ban(target)
    chats = await db.get_all_chats()
    for chat in chats:
        try:
            await bot.unban_chat_member(chat['chat_id'], target, only_if_banned=True)
        except Exception:
            pass
    await message.answer(f"✅ Глобальный бан снят: {await mention(target)}", parse_mode="HTML")


@router.message(Command("gbanlist", "глобанлист"))
async def cmd_gbanlist(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 9:
        await message.reply("❌ Недостаточно прав!")
        return
    bans = await db.get_global_bans()
    if not bans:
        await message.answer("📋 Глобальных банов нет")
        return
    text = "🚫 <b>Глобальные баны</b>\n\n"
    for b in bans[:20]:
        text += f"• <code>{b['user_id']}</code> — {b.get('reason', '-')[:30]}\n"
    await message.answer(text, parse_mode="HTML")


# =============================================================================
# УДАЛЕНИЕ / ОЧИСТКА
# =============================================================================

@router.message(Command("del", "delete", "удалить"))
async def cmd_del(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return
    if not message.reply_to_message:
        await message.reply("❌ Ответьте на сообщение!")
        return
    try:
        await message.reply_to_message.delete()
        await message.delete()
    except Exception:
        await message.reply("❌ Не удалось удалить")


@router.message(Command("clear", "очистить"))
async def cmd_clear(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return
    args = get_args(message)
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return
    msg_ids = await db.get_user_messages(target, message.chat.id, 100)
    if not msg_ids:
        await message.answer("📋 Сообщений не найдено")
        return
    deleted = 0
    for msg_id in msg_ids:
        try:
            await bot.delete_message(message.chat.id, msg_id)
            deleted += 1
        except Exception:
            pass
    await db.clear_user_messages(target, message.chat.id)
    await message.answer(f"🧹 Удалено {deleted} сообщений", parse_mode="HTML")


@router.callback_query(F.data.startswith("clear:"))
async def cb_clear(call: CallbackQuery):
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    role = await get_role(call.from_user.id, chat_id)
    if role < 1:
        await call.answer("Недостаточно прав!", show_alert=True)
        return
    msg_ids = await db.get_user_messages(target, chat_id, 50)
    deleted = 0
    for msg_id in msg_ids:
        try:
            await bot.delete_message(chat_id, msg_id)
            deleted += 1
        except Exception:
            pass
    await db.clear_user_messages(target, chat_id)
    await call.answer(f"🧹 Удалено {deleted} сообщений", show_alert=True)


# =============================================================================
# РОЛИ
# =============================================================================

@router.message(Command("setrole", "роль"))
async def cmd_setrole(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 5:
        await message.reply("❌ Недостаточно прав! Нужен уровень 5+")
        return

    args = get_args(message)
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return

    has_reply = has_reply_target(message)
    role_idx = 1 if has_reply else 2

    if len(args) <= role_idx:
        await message.reply("❌ Укажите роль (0-10)!")
        return

    try:
        new_role = int(args[role_idx])
    except Exception:
        await message.reply("❌ Роль должна быть числом!")
        return

    if new_role < 0 or new_role > 10:
        await message.reply("❌ Роль от 0 до 10!")
        return

    if new_role >= my_role:
        await message.reply("❌ Нельзя выдать роль >= своей!")
        return

    await db.set_user_role(target, message.chat.id, new_role)

    await message.answer(
        f"✅ Роль установлена\n<b>Кто:</b> {await mention(target)}\n"
        f"<b>Роль:</b> {ROLE_NAMES.get(new_role, '?')} ({new_role})",
        parse_mode="HTML"
    )


@router.message(Command("addmoder", "мод"))
async def cmd_addmoder(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 3:
        await message.reply("❌ Недостаточно прав!")
        return
    args = get_args(message)
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return
    await db.set_user_role(target, message.chat.id, 1)
    await message.answer(f"✅ {await mention(target)} теперь Младший модератор (1)", parse_mode="HTML")


@router.message(Command("removerole", "снятьроль"))
async def cmd_removerole(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 3:
        await message.reply("❌ Недостаточно прав!")
        return
    args = get_args(message)
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return
    target_role = await get_role(target, message.chat.id)
    if target_role >= my_role:
        await message.reply("❌ Нельзя снять роль у этого пользователя!")
        return
    await db.set_user_role(target, message.chat.id, 0)
    await message.answer(f"✅ Роль снята: {await mention(target)}", parse_mode="HTML")


@router.message(Command("addadmin"))
async def cmd_addadmin(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 7:
        await message.reply("❌ Недостаточно прав!")
        return
    args = get_args(message)
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return
    await db.set_user_role(target, message.chat.id, 3)
    await message.answer(f"✅ {await mention(target)} теперь Старший модератор (3)", parse_mode="HTML")


@router.message(Command("addsenadmin", "senadm"))
async def cmd_addsenadmin(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 7:
        await message.reply("❌ Недостаточно прав!")
        return
    args = get_args(message)
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return
    await db.set_user_role(target, message.chat.id, 5)
    await message.answer(f"✅ {await mention(target)} теперь Тех. специалист (5)", parse_mode="HTML")


@router.message(Command("addstaff"))
async def cmd_addstaff(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 9:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message)
    if len(args) < 3:
        await message.reply(
            "❌ Использование: /addstaff @username роль\n"
            "<b>Пример:</b> /addstaff @username 5",
            parse_mode="HTML"
        )
        return

    username = args[1].lstrip("@")
    try:
        new_role = int(args[2])
    except Exception:
        await message.reply("❌ Роль должна быть числом!")
        return

    if new_role >= my_role or new_role < 1:
        await message.reply("❌ Некорректная роль!")
        return

    target_id = await resolve_username(username)
    if not target_id:
        await message.reply(f"❌ Пользователь @{username} не найден!")
        return

    await db.set_global_role(target_id, new_role, username)
    await message.answer(
        f"✅ Добавлен в команду\n"
        f"<b>Кто:</b> @{username} (<code>{target_id}</code>)\n"
        f"<b>Роль:</b> {ROLE_NAMES.get(new_role)} ({new_role})",
        parse_mode="HTML"
    )


@router.message(Command("removestaff"))
async def cmd_removestaff(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 9:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message)
    if len(args) < 2:
        await message.reply("❌ Использование: /removestaff @username")
        return

    username = args[1].lstrip("@")
    target_id = await resolve_username(username)
    if not target_id:
        await message.reply("❌ Пользователь не найден")
        return

    target_role = await db.get_global_role(target_id)
    if target_role >= my_role:
        await message.reply("❌ Нельзя удалить этого пользователя!")
        return

    await db.remove_global_role(target_id)
    await message.answer(f"✅ @{username} удалён из команды")


# =============================================================================
# НАСТРОЙКИ ЧАТА
# =============================================================================

@router.message(Command("welcome", "приветствие", "wtext"))
async def cmd_welcome(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 5:
        await message.reply("❌ Недостаточно прав!")
        return

    args = get_args(message, maxsplit=1)
    if len(args) < 2:
        current = await db.get_welcome(message.chat.id)
        await message.reply(
            f"<b>Текущее:</b>\n{current or 'Не установлено'}\n\n"
            f"<b>Переменные:</b> %name%, %fullname%, %mention%, %username%, %id%\n"
            f"<b>Установить:</b> /welcome Привет, %name%!\n"
            f"<b>Удалить:</b> /welcome off",
            parse_mode="HTML"
        )
        return

    text = args[1]
    if text.lower() in ["off", "выкл", "удалить", "0"]:
        await db.set_welcome(message.chat.id, "")
        await message.answer("✅ Приветствие удалено")
    else:
        await db.set_welcome(message.chat.id, text)
        await message.answer(f"✅ Приветствие установлено:\n{text}", parse_mode="HTML")


@router.message(Command("quiet", "тишина", "silence"))
async def cmd_quiet(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 5:
        await message.reply("❌ Недостаточно прав!")
        return
    enabled = await db.toggle_silence(message.chat.id)
    if enabled:
        await message.answer("🔇 Режим тишины <b>включён</b>", parse_mode="HTML")
    else:
        await message.answer("🔊 Режим тишины <b>выключен</b>", parse_mode="HTML")


@router.message(Command("antiflood", "антифлуд"))
async def cmd_antiflood(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 5:
        await message.reply("❌ Недостаточно прав!")
        return
    enabled = await db.toggle_antiflood(message.chat.id)
    if enabled:
        await message.answer("🛡 Антифлуд <b>включён</b>", parse_mode="HTML")
    else:
        await message.answer("🛡 Антифлуд <b>выключен</b>", parse_mode="HTML")


@router.message(Command("filter", "фильтр"))
async def cmd_filter(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 5:
        await message.reply("❌ Недостаточно прав!")
        return
    enabled = await db.toggle_filter(message.chat.id)
    if enabled:
        await message.answer("🔠 Фильтр слов <b>включён</b>", parse_mode="HTML")
    else:
        await message.answer("🔠 Фильтр слов <b>выключен</b>", parse_mode="HTML")


@router.message(Command("banword", "запретить"))
async def cmd_banword(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 5:
        await message.reply("❌ Недостаточно прав!")
        return
    args = get_args(message, maxsplit=1)
    if len(args) < 2:
        await message.reply("❌ Укажите слово: /banword слово")
        return
    word = args[1].lower()
    await db.add_banword(message.chat.id, word)
    await message.answer(f"✅ Слово «{word}» запрещено")


@router.message(Command("unbanword", "разрешить"))
async def cmd_unbanword(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 5:
        await message.reply("❌ Недостаточно прав!")
        return
    args = get_args(message, maxsplit=1)
    if len(args) < 2:
        await message.reply("❌ Укажите слово!")
        return
    word = args[1].lower()
    await db.remove_banword(message.chat.id, word)
    await message.answer(f"✅ Слово «{word}» разрешено")


@router.message(Command("banwords", "bws", "запрещённые"))
async def cmd_banwords(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 5:
        await message.reply("❌ Недостаточно прав!")
        return
    words = await db.get_banwords(message.chat.id)
    if not words:
        await message.answer("📋 Запрещённых слов нет")
        return
    await message.answer(f"🚫 <b>Запрещённые:</b>\n{', '.join(words)}", parse_mode="HTML")


@router.message(Command("zov", "зов"))
async def cmd_zov(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 3:
        await message.reply("❌ Недостаточно прав!")
        return
    args = get_args(message, maxsplit=1)
    reason = args[1] if len(args) > 1 else "Вызов"
    caller_id = await get_caller_id_safe(message)
    await message.answer(
        f"📣 <b>Внимание всем участникам!</b>\n\n"
        f"<b>Причина:</b> {reason}\n"
        f"<b>Вызвал:</b> {await mention(caller_id)}",
        parse_mode="HTML"
    )


@router.message(Command("broadcast", "рассылка"))
async def cmd_broadcast(message: Message):
    my_role = await get_caller_role(message)
    if my_role < 9:
        await message.reply("❌ Недостаточно прав!")
        return
    args = get_args(message, maxsplit=1)
    if len(args) < 2:
        await message.reply("❌ Укажите текст: /broadcast текст")
        return
    text = args[1]
    chats = await db.get_all_chats()
    sent = 0
    for chat in chats:
        try:
            await bot.send_message(chat['chat_id'], f"📢 <b>Объявление</b>\n\n{text}", parse_mode="HTML")
            sent += 1
        except Exception:
            pass
    await message.answer(f"✅ Отправлено в {sent} чатов")


@router.message(Command("top", "топ"))
async def cmd_top(message: Message):
    top_users = await db.get_top_users(message.chat.id, 10)
    if not top_users:
        await message.answer("📋 Нет данных")
        return
    text = "🏆 <b>Топ по сообщениям</b>\n\n"
    for i, (user_id, count) in enumerate(top_users, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        text += f"{medal} {await mention(user_id, message.chat.id)} — {count}\n"
    await message.answer(text, parse_mode="HTML")


# =============================================================================
# ОБРАБОТКА ВСЕХ СООБЩЕНИЙ
# =============================================================================

@router.message(F.chat.type.in_([ChatType.GROUP, ChatType.SUPERGROUP]))
async def on_message(message: Message):
    if not message.from_user:
        return

    user_id = message.from_user.id
    chat_id = message.chat.id

    # Регистрация чата
    await db.register_chat(chat_id, message.chat.title or "")

    # Анонимный бот — пропускаем фильтры
    if user_id == ANONYMOUS_BOT_ID:
        return

    # Кэширование username
    if message.from_user.username:
        await db.cache_username(user_id, message.from_user.username)
        # Проверяем отложенную роль
        await db.apply_pending_staff(user_id, message.from_user.username)

    # Записываем сообщение
    if message.message_id:
        await db.add_message(user_id, chat_id, message.message_id)

    role = await get_role(user_id, chat_id)

    # Режим тишины
    if await db.is_silence(chat_id) and role < 1:
        try:
            await message.delete()
        except Exception:
            pass
        return

    # Проверяем мут
    mute = await db.get_mute(user_id, chat_id)
    if mute and mute.get('until', 0) > time.time():
        try:
            await message.delete()
        except Exception:
            pass
        return

    # Антифлуд
    if role < 1 and await db.is_antiflood(chat_id):
        if await db.check_spam(user_id, chat_id, SPAM_INTERVAL, SPAM_COUNT):
            until = int(time.time()) + 1800
            await db.add_mute(user_id, chat_id, 0, "Антифлуд", until)
            try:
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
# QUICK MUTE CALLBACK
# =============================================================================

@router.callback_query(F.data.startswith("qmute:"))
async def cb_quick_mute(call: CallbackQuery):
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    role = await get_role(call.from_user.id, chat_id)
    if role < 1:
        await call.answer("Недостаточно прав!", show_alert=True)
        return
    target_role = await get_role(target, chat_id)
    if target_role >= role:
        await call.answer("Нельзя замутить!", show_alert=True)
        return
    until = int(time.time()) + 1800
    try:
        await bot.restrict_chat_member(
            chat_id, target,
            permissions=muted_permissions(),
            until_date=timedelta(minutes=30)
        )
        await db.add_mute(target, chat_id, call.from_user.id, "Быстрый мут", until)
        await call.answer("✅ Мут 30 мин!", show_alert=True)
    except Exception as e:
        await call.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("quickwarn:"))
async def cb_quick_warn(call: CallbackQuery):
    """Быстрый варн через инлайн-кнопку"""
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    role = await get_role(call.from_user.id, chat_id)
    
    if role < 1:
        await call.answer("❌ Недостаточно прав!", show_alert=True)
        return
    
    target_role = await get_role(target, chat_id)
    if target_role >= role:
        await call.answer("❌ Нельзя выдать варн!", show_alert=True)
        return
    
    try:
        warns = await db.add_warn(target, chat_id, call.from_user.id, "Быстрый варн")
        
        if warns >= MAX_WARNS:
            # Кик за превышение варнов
            await bot.ban_chat_member(chat_id, target)
            await asyncio.sleep(0.5)
            await bot.unban_chat_member(chat_id, target)
            await db.clear_warns(target, chat_id)
            await call.answer(f"⚠️ Варн выдан! Пользователь кикнут за {MAX_WARNS} варна.", show_alert=True)
            
            # Обновляем сообщение
            await call.message.edit_text(
                f"{call.message.text}\n\n👢 <b>Кикнут за {MAX_WARNS} варна!</b>",
                parse_mode="HTML"
            )
        else:
            await call.answer(f"✅ Варн выдан! Всего: {warns}/{MAX_WARNS}", show_alert=True)
            # Можно обновить кнопки если нужно
    except Exception as e:
        await call.answer(f"❌ Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("quickkick:"))
async def cb_quick_kick(call: CallbackQuery):
    """Быстрый кик через инлайн-кнопку"""
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    role = await get_role(call.from_user.id, chat_id)
    
    if role < 1:
        await call.answer("❌ Недостаточно прав!", show_alert=True)
        return
    
    target_role = await get_role(target, chat_id)
    if target_role >= role:
        await call.answer("❌ Нельзя кикнуть!", show_alert=True)
        return
    
    try:
        await bot.ban_chat_member(chat_id, target)
        await asyncio.sleep(0.5)
        await bot.unban_chat_member(chat_id, target)
        await call.answer("✅ Кикнут!", show_alert=True)
        
        # Обновляем сообщение
        await call.message.edit_text(
            f"{call.message.text}\n\n👢 <b>Кикнут модератором</b> {await mention(call.from_user.id)}",
            parse_mode="HTML",
            reply_markup=None
        )
    except Exception as e:
        await call.answer(f"❌ Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("quickban:"))
async def cb_quick_ban(call: CallbackQuery):
    """Быстрый бан через инлайн-кнопку"""
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    role = await get_role(call.from_user.id, chat_id)
    
    if role < 3:
        await call.answer("❌ Недостаточно прав! Нужен уровень 3+", show_alert=True)
        return
    
    target_role = await get_role(target, chat_id)
    if target_role >= role:
        await call.answer("❌ Нельзя забанить!", show_alert=True)
        return
    
    try:
        await bot.ban_chat_member(chat_id, target)
        await db.add_ban(target, chat_id, call.from_user.id, "Быстрый бан")
        await call.answer("✅ Забанен!", show_alert=True)
        
        # Обновляем сообщение
        await call.message.edit_text(
            f"{call.message.text}\n\n🚫 <b>Забанен модератором</b> {await mention(call.from_user.id)}",
            parse_mode="HTML",
            reply_markup=None
        )
    except Exception as e:
        await call.answer(f"❌ Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("quickclear:"))
async def cb_quick_clear(call: CallbackQuery):
    """Быстрая очистка сообщений через инлайн-кнопку"""
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    role = await get_role(call.from_user.id, chat_id)
    
    if role < 1:
        await call.answer("❌ Недостаточно прав!", show_alert=True)
        return
    
    target_role = await get_role(target, chat_id)
    if target_role >= role:
        await call.answer("❌ Нельзя очистить сообщения!", show_alert=True)
        return
    
    await call.answer("🧹 Очистка последних 10 сообщений...", show_alert=False)
    
    # Очищаем последние 10 сообщений
    deleted = 0
    try:
        # Получаем ID текущего сообщения
        current_msg_id = call.message.message_id
        
        # Пытаемся удалить последние 10 сообщений назад
        for i in range(1, 11):
            try:
                await bot.delete_message(chat_id, current_msg_id - i)
                deleted += 1
                await asyncio.sleep(0.3)  # Небольшая задержка чтобы не словить rate limit
            except Exception:
                pass
        
        await call.message.edit_text(
            f"{call.message.text}\n\n🧹 <b>Очищено {deleted} сообщений</b>",
            parse_mode="HTML",
            reply_markup=None
        )
    except Exception as e:
        await call.answer(f"❌ Ошибка: {e}", show_alert=True)



# =============================================================================
# ЗАПУСК
# =============================================================================

async def main():
    global db
    db = Database("database.db")
    await db.init()

    logger.info("🔵 Модерация Анонимные сообщения | Георгиевка")
    logger.info("Инициализация...")

    await init_staff()

    for chat_id in MODERATED_CHATS:
        try:
            chat = await bot.get_chat(chat_id)
            await db.register_chat(chat_id, chat.title or "")
            logger.info(f"Чат зарегистрирован: {chat_id} ({chat.title})")
        except Exception as e:
            logger.warning(f"Ошибка регистрации чата {chat_id}: {e}")

    # Регистрируем меню команд
    await register_commands()

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("✅ Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
