"""
🔵 Модерация Анонимные сообщения | Георгиевка
Telegram бот для модерации групп - ПОЛНАЯ ВЕРСИЯ

Весь функционал адаптирован из VK бота:
- Глобальный бан
- Мут/бан/варн/кик
- Ники
- Запрещённые слова
- Антифлуд/антиспам
- Режим тишины
- Приветствия
- Статистика
- Сетки бесед
"""

import asyncio
import logging
import json
import os
import time
from datetime import datetime, timedelta
from typing import Optional, Union, List

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, ChatMemberUpdatedFilter, IS_NOT_MEMBER, IS_MEMBER
from aiogram.types import (
    Message, CallbackQuery, ChatMemberUpdated,
    InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions
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
MODERATED_CHATS = config.get("moderated_chats", [])  # Список ID чатов
PRESET_STAFF = config.get("preset_staff", {})
MAX_WARNS = config.get("max_warns", 3)
SPAM_INTERVAL = config.get("spam_interval_seconds", 2)
SPAM_COUNT = config.get("spam_messages_count", 3)

# Логирование
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# Инициализация
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

# Права по ролям
# 1-2: мут (1ч), варн, удаление, кик
# 3-4: мут (24ч), снятие варнов
# 5-6: мут без лимита, настройки
# 7-8: бан/разбан, роли до 6
# 9-10: глобальный бан, всё

MUTE_LIMITS = {1: 3600, 2: 3600, 3: 86400, 4: 86400, 5: 0, 6: 0, 7: 0, 8: 0, 9: 0, 10: 0}

# =============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =============================================================================

async def get_role(user_id: int, chat_id: int = 0) -> int:
    """Получить роль пользователя (глобальная приоритетнее)"""
    global_role = await db.get_global_role(user_id)
    if global_role > 0:
        return global_role
    if chat_id:
        return await db.get_user_role(user_id, chat_id)
    return 0


async def get_effective_role(user_id: int, chat_id: int) -> int:
    """Роль с учётом Telegram-админки.

    Если в БД роль 0, но пользователь является администратором/создателем чата,
    даём базовую роль 1, чтобы команды модерации работали "из коробки".
    """
    role = await get_role(user_id, chat_id)
    if role > 0:
        return role
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        if m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            return 1
    except Exception:
        pass
    return 0


def get_actor_id(message: Message) -> Optional[int]:
    """Вернуть user_id отправителя команды.

    Если админ пишет анонимно (message.sender_chat), Telegram скрывает его user_id.
    В таком режиме команды, завязанные на роли/права, нельзя корректно применять.
    """
    # Типовой случай: обычный пользователь
    if message.from_user and not message.sender_chat:
        return message.from_user.id

    # Анонимный админ / пост от имени канала/чата
    if message.sender_chat is not None:
        # В некоторых клиентах from_user может быть GroupAnonymousBot
        if message.from_user is None:
            return None
        if getattr(message.from_user, "is_bot", False) and (message.from_user.username or "") == "GroupAnonymousBot":
            return None
        # Если вдруг Telegram всё-таки прислал from_user вместе с sender_chat
        return message.from_user.id

    return message.from_user.id if message.from_user else None


async def get_user_info(user_id: int) -> dict:
    """Получить инфо о пользователе через Telegram API"""
    try:
        user = await bot.get_chat(user_id)
        return {
            "id": user_id,
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "username": user.username or "",
            "full_name": user.full_name or f"User {user_id}"
        }
    except:
        return {
            "id": user_id,
            "first_name": "Пользователь",
            "last_name": "",
            "username": "",
            "full_name": f"Пользователь {user_id}"
        }


async def get_user_name(user_id: int, chat_id: int = 0) -> str:
    """Получить имя (ник или реальное)"""
    if chat_id:
        nick = await db.get_nick(user_id, chat_id)
        if nick:
            return nick
    info = await get_user_info(user_id)
    return info["full_name"]


async def mention(user_id: int, chat_id: int = 0) -> str:
    """HTML-упоминание"""
    name = await get_user_name(user_id, chat_id)
    return f'<a href="tg://user?id={user_id}">{name}</a>'


async def parse_user(message: Message, args: list, start_idx: int = 1) -> Optional[int]:
    """
    Универсальный парсер пользователя:
    1. Реплай на сообщение
    2. @username (из кэша или API)
    3. Числовой ID
    4. Ник в чате
    """
    # 1. Реплай
    if message.reply_to_message and message.reply_to_message.from_user:
        user = message.reply_to_message.from_user
        # Кэшируем
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
    
    # @username
    if arg.startswith('@'):
        username = arg[1:]
        # Из кэша
        cached = await db.get_user_by_username(username)
        if cached:
            return cached
        # Через API (работает только если бот "видел" пользователя)
        try:
            user = await bot.get_chat(f"@{username}")
            await db.cache_username(user.id, username)
            return user.id
        except:
            return None
    
    # Ник в чате
    if message.chat.id:
        by_nick = await db.get_user_by_nick(arg, message.chat.id)
        if by_nick:
            return by_nick
    
    # Может быть username без @
    cached = await db.get_user_by_username(arg)
    if cached:
        return cached
    
    return None


def parse_time(s: str) -> Optional[int]:
    """Парсинг времени: 30, 30m, 1h, 1d, 1w"""
    if not s:
        return None
    s = s.lower().strip()
    mult = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800}
    for suffix, m in mult.items():
        if s.endswith(suffix):
            try:
                return int(s[:-1]) * m
            except:
                return None
    try:
        return int(s) * 60  # По умолчанию минуты
    except:
        return None


def format_time(sec: int) -> str:
    """Форматирование секунд"""
    if sec < 60: return f"{sec}с"
    if sec < 3600: return f"{sec // 60}м"
    if sec < 86400: return f"{sec // 3600}ч"
    return f"{sec // 86400}д"


def format_dt(ts: int) -> str:
    """Форматирование timestamp"""
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")


async def init_staff():
    """Инициализация предустановленной команды"""
    for username, role in PRESET_STAFF.items():
        try:
            user = await bot.get_chat(f"@{username}")
            await db.set_global_role(user.id, role, username)
            logger.info(f"Staff init: @{username} -> role {role}")
        except Exception as e:
            logger.warning(f"Could not init @{username}: {e}")


# =============================================================================
# ОБРАБОТКА ВХОДА В ГРУППУ
# =============================================================================

@router.chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def on_user_join(event: ChatMemberUpdated):
    """Проверка при входе в группу"""
    user = event.new_chat_member.user
    chat_id = event.chat.id
    
    # Регистрируем чат
    await db.register_chat(chat_id, event.chat.title or "")
    
    # Кэшируем username
    if user.username:
        await db.cache_username(user.id, user.username)
    
    # Проверяем глобальный бан
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
        except:
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
        except:
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
        await message.answer("✅ Бот активирован!")


@router.message(Command("help", "помощь", "хелп", "команды", "commands"))
async def cmd_help(message: Message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    role = await get_role(user_id, chat_id)
    
    text = "🔵 <b>Команды бота</b>\n\n"
    
    # Уровень 0
    text += "<b>👤 Для всех:</b>\n"
    text += "/id — узнать ID\n"
    text += "/stats — статистика\n"
    text += "/mystatus — мой статус\n"
    text += "/staff — команда\n\n"
    
    # Уровень 1-2
    if role >= 1:
        text += "<b>🛡 Модератор (1-2):</b>\n"
        text += "/mute время причина — мут\n"
        text += "/unmute — снять мут\n"
        text += "/warn причина — варн\n"
        text += "/unwarn — снять варн\n"
        text += "/getwarn — инфо о варнах\n"
        text += "/warnhistory — история варнов\n"
        text += "/warnlist — список с варнами\n"
        text += "/kick причина — кик\n"
        text += "/del — удалить сообщение\n"
        text += "/clear — очистить сообщения\n"
        text += "/setnick ник — установить ник\n"
        text += "/removenick — удалить ник\n"
        text += "/getnick — узнать ник\n"
        text += "/getacc ник — найти по нику\n"
        text += "/nlist — список ников\n"
        text += "/mutelist — список мутов\n"
        text += "/reg — дата регистрации\n\n"
    
    # Уровень 3-4
    if role >= 3:
        text += "<b>🛡 Старший модератор (3-4):</b>\n"
        text += "/ban причина — бан\n"
        text += "/unban — разбан\n"
        text += "/getban — инфо о бане\n"
        text += "/banlist — список банов\n"
        text += "/zov — упомянуть всех\n"
        text += "/online — кто онлайн\n"
        text += "/addmoder — выдать модера\n"
        text += "/removerole — снять роль\n\n"
    
    # Уровень 5-6
    if role >= 5:
        text += "<b>⚙️ Тех. специалист (5-6):</b>\n"
        text += "/setrole уровень — установить роль\n"
        text += "/banword — запретить слово\n"
        text += "/unbanword — разрешить слово\n"
        text += "/banwords — запрещённые слова\n"
        text += "/filter — вкл/выкл фильтр слов\n"
        text += "/welcome текст — приветствие\n"
        text += "/quiet — режим тишины\n"
        text += "/antiflood — антифлуд\n"
        text += "/rnickall — удалить все ники\n\n"
    
    # Уровень 7-8
    if role >= 7:
        text += "<b>👑 Куратор (7-8):</b>\n"
        text += "/addadmin — выдать админа\n"
        text += "/addsenadmin — выдать ст. админа\n\n"
    
    # Уровень 9-10
    if role >= 9:
        text += "<b>🌐 Главный модератор (9-10):</b>\n"
        text += "/gban причина — глобальный бан\n"
        text += "/gunban — снять глоб. бан\n"
        text += "/gbanlist — список глоб. банов\n"
        text += "/addstaff роль — добавить в команду\n"
        text += "/removestaff — удалить из команды\n"
        text += "/broadcast — рассылка\n"
    
    await message.answer(text, parse_mode="HTML")


@router.message(Command("id", "ид", "getid"))
async def cmd_id(message: Message):
    """Узнать ID"""
    args = message.text.split()
    target = await parse_user(message, args, 1)
    if not target:
        actor_id = get_actor_id(message)
        if actor_id is None:
            await message.reply(
                "❗️Вы написали команду как <b>анонимный администратор</b>. "
                "Укажите пользователя или ответьте на сообщение.",
                parse_mode="HTML",
            )
            return
        target = actor_id
    
    info = await get_user_info(target)
    text = f"🆔 <b>ID:</b> <code>{target}</code>\n"
    text += f"<b>Имя:</b> {info['full_name']}\n"
    if info['username']:
        text += f"<b>Username:</b> @{info['username']}"
    
    await message.answer(text, parse_mode="HTML")


@router.message(Command("stats", "стата", "статистика"))
async def cmd_stats(message: Message):
    """Статистика пользователя"""
    args = message.text.split()
    target = await parse_user(message, args, 1)
    if not target:
        actor_id = get_actor_id(message)
        if actor_id is None:
            await message.reply(
                "❗️Вы написали команду как <b>анонимный администратор</b> — Telegram скрывает ваш ID.\n\n"
                "Чтобы получить статистику:\n"
                "• ответьте на сообщение пользователя: <code>/stats</code>\n"
                "• или укажите ID/username: <code>/stats @username</code>",
                parse_mode="HTML",
            )
            return
        target = actor_id
    
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
    actor_id = get_actor_id(message)
    my_role = await get_effective_role(actor_id, chat_id) if actor_id else 0
    if my_role >= 1 and target != message.from_user.id:
        kb = InlineKeyboardBuilder()
        kb.button(text="📜 История варнов", callback_data=f"wh:{target}:{chat_id}")
        kb.button(text="🔇 Мут", callback_data=f"qmute:{target}:{chat_id}")
        kb.adjust(2)
        await message.answer(text, parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await message.answer(text, parse_mode="HTML")


@router.message(Command("mystatus"))
async def cmd_mystatus(message: Message):
    """Мой статус"""
    # Просто проксируем в /stats без аргументов.
    # Если команда написана анонимным админом — /stats сам попросит указать цель.
    message.text = "/stats"
    await cmd_stats(message)


@router.message(Command("staff", "стафф", "команда"))
async def cmd_staff(message: Message):
    """Состав команды"""
    # По умолчанию показываем состав текущего чата + глобальный состав.
    chat_staff = []
    if message.chat.type != ChatType.PRIVATE:
        chat_staff = await db.get_chat_staff(message.chat.id)
    global_staff = await db.get_all_staff()

    if not chat_staff and not global_staff:
        await message.answer("📋 Команда пуста")
        return

    def build_block(title: str, rows: List[dict]) -> str:
        if not rows:
            return f"<b>{title}</b>\n(пусто)\n\n"
        by_role: dict[int, list] = {}
        for s in rows:
            r = int(s.get('role', 0))
            by_role.setdefault(r, []).append(s)
        out = f"<b>{title}</b>\n\n"
        for role_num in sorted(by_role.keys(), reverse=True):
            out += f"<b>{role_num:02d}. {ROLE_NAMES.get(role_num, '?')}</b>\n"
            for s in by_role[role_num]:
                uname = s.get('username')
                uid = s.get('user_id')
                out += f"   {'@' + uname if uname else 'ID: ' + str(uid)}\n"
            out += "\n"
        return out

    text = "👥 <b>Состав команды</b>\n\n"
    if message.chat.type != ChatType.PRIVATE:
        text += build_block("Чат", chat_staff)
    text += build_block("Глобально", global_staff)

    await message.answer(text, parse_mode="HTML")


@router.message(Command("reg", "registration", "регистрация"))
async def cmd_reg(message: Message):
    """Дата регистрации (приблизительно по ID)"""
    args = message.text.split()
    target = await parse_user(message, args, 1)
    if not target:
        actor_id = get_actor_id(message)
        if actor_id is None:
            await message.reply(
                "❗️Вы написали команду как <b>анонимный администратор</b>. "
                "Укажите пользователя или ответьте на сообщение.",
                parse_mode="HTML",
            )
            return
        target = actor_id
    
    # В Telegram нет API для даты регистрации, показываем просто ID
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
    """Замутить пользователя"""
    if message.chat.type == ChatType.PRIVATE:
        return

    actor_id = get_actor_id(message)
    if actor_id is None:
        await message.reply(
            "❗️Команды модерации не работают в режиме <b>анонимного администратора</b>.\n"
            "Отключите анонимность (Администраторы → Ваша роль → Анонимность) и попробуйте снова.",
            parse_mode="HTML",
        )
        return
    
    my_role = await get_effective_role(actor_id, message.chat.id)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split()
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply(
            "❌ Укажите пользователя!\n\n"
            "<b>Примеры:</b>\n"
            "• /mute @username 30 спам\n"
            "• /mute 123456789 1h причина\n"
            "• Ответьте на сообщение: /mute 30 причина",
            parse_mode="HTML"
        )
        return
    
    target_role = await get_role(target, message.chat.id)
    if target_role >= my_role:
        await message.reply("❌ Нельзя замутить пользователя с такой же или выше ролью!")
        return
    
    # Парсим аргументы
    has_reply = message.reply_to_message is not None
    time_idx = 1 if has_reply else 2
    reason_idx = time_idx + 1
    
    time_str = args[time_idx] if len(args) > time_idx else "30"
    reason = " ".join(args[reason_idx:]) if len(args) > reason_idx else "Нарушение правил"
    
    duration = parse_time(time_str)
    if not duration:
        duration = 30 * 60
    
    # Проверяем лимит
    limit = MUTE_LIMITS.get(my_role, 0)
    if limit > 0 and duration > limit:
        await message.reply(f"❌ Ваш лимит: {format_time(limit)}")
        return
    
    until = int(time.time()) + duration
    
    try:
        await bot.restrict_chat_member(
            message.chat.id, target,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until
        )
    except TelegramBadRequest as e:
        await message.reply(f"❌ Ошибка: {e.message}")
        return
    except TelegramForbiddenError:
        await message.reply("❌ Нет прав бота!")
        return
    
    await db.add_mute(target, message.chat.id, actor_id, reason, until)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="🔓 Снять мут", callback_data=f"unmute:{target}:{message.chat.id}")
    kb.button(text="🧹 Очистить", callback_data=f"clear:{target}:{message.chat.id}")
    
    await message.answer(
        f"🔇 <b>Мут</b>\n\n"
        f"<b>Кто:</b> {await mention(target, message.chat.id)}\n"
        f"<b>Время:</b> {format_time(duration)}\n"
        f"<b>Причина:</b> {reason}\n"
        f"<b>Модератор:</b> {await mention(actor_id)}",
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )


@router.message(Command("unmute", "размут", "анмут"))
async def cmd_unmute(message: Message):
    """Снять мут"""
    if message.chat.type == ChatType.PRIVATE:
        return
    
    actor_id = get_actor_id(message)
    if actor_id is None:
        await message.reply(
            "❗️Команды модерации не работают в режиме <b>анонимного администратора</b>.",
            parse_mode="HTML",
        )
        return

    my_role = await get_effective_role(actor_id, message.chat.id)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split()
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return
    
    try:
        await bot.restrict_chat_member(
            message.chat.id, target,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
        )
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
        await bot.restrict_chat_member(
            chat_id, target,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
        )
        await db.remove_mute(target, chat_id)
        await call.answer("✅ Мут снят!", show_alert=True)
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception as e:
        await call.answer(f"Ошибка: {e}", show_alert=True)


@router.message(Command("getmute", "gmute", "гетмут"))
async def cmd_getmute(message: Message):
    """Информация о муте"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split()
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return
    
    mute = await db.get_mute(target, message.chat.id)
    if not mute or mute.get('until', 0) <= time.time():
        await message.answer(f"✅ У {await mention(target)} нет мута", parse_mode="HTML")
        return
    
    await message.answer(
        f"🔇 <b>Мут пользователя</b>\n\n"
        f"<b>Кто:</b> {await mention(target)}\n"
        f"<b>До:</b> {format_dt(mute['until'])}\n"
        f"<b>Причина:</b> {mute.get('reason', '-')}\n"
        f"<b>Модератор:</b> {await mention(mute['muted_by'])}",
        parse_mode="HTML"
    )


@router.message(Command("mutelist", "мутлист"))
async def cmd_mutelist(message: Message):
    """Список замученных"""
    my_role = await get_role(message.from_user.id, message.chat.id)
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
    """Выдать предупреждение"""
    if message.chat.type == ChatType.PRIVATE:
        return
    
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split()
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return
    
    target_role = await get_role(target, message.chat.id)
    if target_role >= my_role:
        await message.reply("❌ Нельзя выдать варн этому пользователю!")
        return
    
    has_reply = message.reply_to_message is not None
    reason_idx = 1 if has_reply else 2
    reason = " ".join(args[reason_idx:]) if len(args) > reason_idx else "Нарушение правил"
    
    warns = await db.add_warn(target, message.chat.id, message.from_user.id, reason)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="🔓 Снять варн", callback_data=f"unwarn:{target}:{message.chat.id}")
    kb.button(text="🧹 Очистить", callback_data=f"clear:{target}:{message.chat.id}")
    
    text = (
        f"⚠️ <b>Предупреждение</b>\n\n"
        f"<b>Кто:</b> {await mention(target, message.chat.id)}\n"
        f"<b>Причина:</b> {reason}\n"
        f"<b>Варнов:</b> {warns}/{MAX_WARNS}\n"
        f"<b>Модератор:</b> {await mention(message.from_user.id)}"
    )
    
    # Автокик при MAX_WARNS
    if warns >= MAX_WARNS:
        try:
            await bot.ban_chat_member(message.chat.id, target)
            await bot.unban_chat_member(message.chat.id, target)
            await db.clear_warns(target, message.chat.id)
            text += f"\n\n👢 <b>Кикнут за {MAX_WARNS} варна!</b>"
            kb = None
        except Exception as e:
            text += f"\n\n⚠️ Не удалось кикнуть: {e}"
    
    await message.answer(text, parse_mode="HTML", reply_markup=kb.as_markup() if kb else None)


@router.message(Command("unwarn", "унварн", "снятьпред"))
async def cmd_unwarn(message: Message):
    """Снять предупреждение"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split()
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
        f"✅ Варн снят: {await mention(target)}\n"
        f"Осталось: {remaining}/{MAX_WARNS}",
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
    """Информация о варнах"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split()
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return
    
    warn_info = await db.get_warn_info(target, message.chat.id)
    if not warn_info:
        await message.answer(f"✅ У {await mention(target)} нет активных варнов", parse_mode="HTML")
        return
    
    kb = InlineKeyboardBuilder()
    kb.button(text="📜 История", callback_data=f"wh:{target}:{message.chat.id}")
    
    await message.answer(
        f"⚠️ <b>Варны пользователя</b>\n\n"
        f"<b>Кто:</b> {await mention(target)}\n"
        f"<b>Варнов:</b> {warn_info['count']}/{MAX_WARNS}\n"
        f"<b>Последняя причина:</b> {warn_info.get('reason', '-')}\n"
        f"<b>Модератор:</b> {await mention(warn_info['warned_by'])}\n"
        f"<b>Когда:</b> {format_dt(warn_info['warned_at'])}",
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )


@router.message(Command("warnhistory", "whistory", "историяварнов"))
async def cmd_warnhistory(message: Message):
    """История варнов"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split()
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
    """Список пользователей с варнами"""
    my_role = await get_role(message.from_user.id, message.chat.id)
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
    """Забанить"""
    if message.chat.type == ChatType.PRIVATE:
        return
    
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 3:
        await message.reply("❌ Недостаточно прав! Нужен уровень 3+")
        return
    
    args = message.text.split()
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return
    
    target_role = await get_role(target, message.chat.id)
    if target_role >= my_role:
        await message.reply("❌ Нельзя забанить этого пользователя!")
        return
    
    has_reply = message.reply_to_message is not None
    reason = " ".join(args[1 if has_reply else 2:]) or "Нарушение правил"
    
    try:
        await bot.ban_chat_member(message.chat.id, target)
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")
        return
    
    await db.add_ban(target, message.chat.id, message.from_user.id, reason)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="🔓 Разбан", callback_data=f"unban:{target}:{message.chat.id}")
    
    await message.answer(
        f"🚫 <b>Бан</b>\n\n"
        f"<b>Кто:</b> {await mention(target, message.chat.id)}\n"
        f"<b>Причина:</b> {reason}\n"
        f"<b>Модератор:</b> {await mention(message.from_user.id)}",
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )


@router.message(Command("unban", "разбан"))
async def cmd_unban(message: Message):
    """Разбанить"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 3:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split()
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
    """Информация о бане"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split()
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
    """Список банов"""
    my_role = await get_role(message.from_user.id, message.chat.id)
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


@router.message(Command("kick", "кик"))
async def cmd_kick(message: Message):
    """Кикнуть"""
    if message.chat.type == ChatType.PRIVATE:
        return
    
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split()
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
        await bot.unban_chat_member(message.chat.id, target)
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")
        return
    
    has_reply = message.reply_to_message is not None
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
    """Установить ник"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split()
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return
    
    target_role = await get_role(target, message.chat.id)
    if target_role > my_role:
        await message.reply("❌ Нельзя установить ник этому пользователю!")
        return
    
    has_reply = message.reply_to_message is not None
    nick = " ".join(args[1 if has_reply else 2:])
    if not nick:
        await message.reply("❌ Укажите ник!")
        return
    
    await db.set_nick(target, message.chat.id, nick)
    await message.answer(
        f"✅ Ник установлен\n\n"
        f"<b>Кто:</b> {await mention(target)}\n"
        f"<b>Ник:</b> {nick}",
        parse_mode="HTML"
    )


@router.message(Command("removenick", "rnick", "удалитьник"))
async def cmd_removenick(message: Message):
    """Удалить ник"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split()
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return
    
    await db.remove_nick(target, message.chat.id)
    await message.answer(f"✅ Ник удалён: {await mention(target)}", parse_mode="HTML")


@router.message(Command("getnick", "gnick", "гетник"))
async def cmd_getnick(message: Message):
    """Узнать ник"""
    args = message.text.split()
    target = await parse_user(message, args, 1)
    if not target:
        target = message.from_user.id
    
    nick = await db.get_nick(target, message.chat.id)
    if nick:
        await message.answer(f"📝 Ник {await mention(target)}: <b>{nick}</b>", parse_mode="HTML")
    else:
        await message.answer(f"📝 У {await mention(target)} нет ника", parse_mode="HTML")


@router.message(Command("getacc", "acc", "аккаунт"))
async def cmd_getacc(message: Message):
    """Найти по нику"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split(maxsplit=1)
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
        f"<b>Ник:</b> {nick}\n"
        f"<b>ID:</b> <code>{user_id}</code>\n"
        f"<b>Имя:</b> {info['full_name']}",
        parse_mode="HTML"
    )


@router.message(Command("nlist", "nicks", "ники"))
async def cmd_nlist(message: Message):
    """Список ников"""
    my_role = await get_role(message.from_user.id, message.chat.id)
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
    """Удалить все ники"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 5:
        await message.reply("❌ Недостаточно прав! Нужен уровень 5+")
        return
    
    await db.clear_all_nicks(message.chat.id)
    await message.answer("✅ Все ники удалены")


# =============================================================================
# ГЛОБАЛЬНЫЙ БАН
# =============================================================================

@router.message(Command("gban", "глобан"))
async def cmd_gban(message: Message):
    """Глобальный бан"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 9:
        await message.reply("❌ Недостаточно прав! Нужен уровень 9+")
        return
    
    args = message.text.split()
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return
    
    # Нельзя банить команду
    target_role = await db.get_global_role(target)
    if target_role > 0:
        await message.reply("❌ Нельзя забанить члена команды!")
        return
    
    has_reply = message.reply_to_message is not None
    reason = " ".join(args[1 if has_reply else 2:]) or "Глобальное нарушение"
    
    await db.add_global_ban(target, message.from_user.id, reason)
    
    # Банить во всех чатах
    chats = await db.get_all_chats()
    banned_count = 0
    for chat in chats:
        try:
            await bot.ban_chat_member(chat['chat_id'], target)
            banned_count += 1
        except:
            pass
    
    await message.answer(
        f"🚫 <b>Глобальный бан</b>\n\n"
        f"<b>Кто:</b> {await mention(target)}\n"
        f"<b>Причина:</b> {reason}\n"
        f"<b>Забанен в:</b> {banned_count} чатах\n"
        f"<b>Модератор:</b> {await mention(message.from_user.id)}",
        parse_mode="HTML"
    )
    logger.info(f"GBAN: user={target}, by={message.from_user.id}")


@router.message(Command("gunban", "глобразбан"))
async def cmd_gunban(message: Message):
    """Снять глобальный бан"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 9:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split()
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return
    
    await db.remove_global_ban(target)
    
    # Разбанить везде
    chats = await db.get_all_chats()
    for chat in chats:
        try:
            await bot.unban_chat_member(chat['chat_id'], target, only_if_banned=True)
        except:
            pass
    
    await message.answer(f"✅ Глобальный бан снят: {await mention(target)}", parse_mode="HTML")


@router.message(Command("gbanlist", "глобанлист"))
async def cmd_gbanlist(message: Message):
    """Список глобальных банов"""
    my_role = await get_role(message.from_user.id, message.chat.id)
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
    """Удалить сообщение"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return
    
    if not message.reply_to_message:
        await message.reply("❌ Ответьте на сообщение!")
        return
    
    try:
        await message.reply_to_message.delete()
        await message.delete()
    except:
        await message.reply("❌ Не удалось удалить")


@router.message(Command("clear", "очистить"))
async def cmd_clear(message: Message):
    """Очистить сообщения пользователя"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 1:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split()
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return
    
    # Получаем ID сообщений из БД
    msg_ids = await db.get_user_messages(target, message.chat.id, 100)
    if not msg_ids:
        await message.answer("📋 Сообщений не найдено")
        return
    
    deleted = 0
    for msg_id in msg_ids:
        try:
            await bot.delete_message(message.chat.id, msg_id)
            deleted += 1
        except:
            pass
    
    await db.clear_user_messages(target, message.chat.id)
    await message.answer(f"🧹 Удалено {deleted} сообщений пользователя <code>{target}</code>", parse_mode="HTML")


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
        except:
            pass
    
    await db.clear_user_messages(target, chat_id)
    await call.answer(f"🧹 Удалено {deleted} сообщений", show_alert=True)


# =============================================================================
# РОЛИ
# =============================================================================

@router.message(Command("setrole", "роль"))
async def cmd_setrole(message: Message):
    """Установить роль в чате"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 5:
        await message.reply("❌ Недостаточно прав! Нужен уровень 5+")
        return
    
    args = message.text.split()
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return
    
    has_reply = message.reply_to_message is not None
    role_idx = 1 if has_reply else 2
    
    if len(args) <= role_idx:
        await message.reply("❌ Укажите роль (0-10)!")
        return
    
    try:
        new_role = int(args[role_idx])
    except:
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
        f"✅ Роль установлена\n\n"
        f"<b>Кто:</b> {await mention(target)}\n"
        f"<b>Роль:</b> {ROLE_NAMES.get(new_role, '?')} ({new_role})",
        parse_mode="HTML"
    )


@router.message(Command("addmoder", "мод"))
async def cmd_addmoder(message: Message):
    """Выдать модератора (роль 1)"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 3:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split()
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return
    
    await db.set_user_role(target, message.chat.id, 1)
    await message.answer(f"✅ {await mention(target)} теперь Младший модератор (1)", parse_mode="HTML")


@router.message(Command("removerole", "снятьроль"))
async def cmd_removerole(message: Message):
    """Снять роль"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 3:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split()
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
    """Выдать админа (роль 3)"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 7:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split()
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return
    
    await db.set_user_role(target, message.chat.id, 3)
    await message.answer(f"✅ {await mention(target)} теперь Старший модератор (3)", parse_mode="HTML")


@router.message(Command("addsenadmin", "senadm"))
async def cmd_addsenadmin(message: Message):
    """Выдать ст. админа (роль 5)"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 7:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split()
    target = await parse_user(message, args, 1)
    if not target:
        await message.reply("❌ Укажите пользователя!")
        return
    
    await db.set_user_role(target, message.chat.id, 5)
    await message.answer(f"✅ {await mention(target)} теперь Технический специалист (5)", parse_mode="HTML")


@router.message(Command("addstaff"))
async def cmd_addstaff(message: Message):
    """Добавить в глобальную команду"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 9:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split()
    if len(args) < 3:
        await message.reply(
            "❌ Использование: /addstaff @username роль\n\n"
            "<b>Роли:</b> 1-10",
            parse_mode="HTML"
        )
        return
    
    username = args[1].lstrip("@")
    try:
        new_role = int(args[2])
    except:
        await message.reply("❌ Роль должна быть числом!")
        return
    
    if new_role >= my_role or new_role < 1:
        await message.reply("❌ Некорректная роль!")
        return
    
    try:
        user = await bot.get_chat(f"@{username}")
        target_id = user.id
    except:
        await message.reply(f"❌ Пользователь @{username} не найден")
        return
    
    await db.set_global_role(target_id, new_role, username)
    await message.answer(
        f"✅ Добавлен в команду\n\n"
        f"<b>Кто:</b> @{username}\n"
        f"<b>Роль:</b> {ROLE_NAMES.get(new_role)} ({new_role})",
        parse_mode="HTML"
    )


@router.message(Command("removestaff"))
async def cmd_removestaff(message: Message):
    """Удалить из команды"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 9:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split()
    if len(args) < 2:
        await message.reply("❌ Использование: /removestaff @username")
        return
    
    username = args[1].lstrip("@")
    
    try:
        user = await bot.get_chat(f"@{username}")
        target_id = user.id
    except:
        await message.reply(f"❌ Пользователь не найден")
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
    """Установить приветствие"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 5:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        current = await db.get_welcome(message.chat.id)
        await message.reply(
            f"<b>Текущее приветствие:</b>\n{current or 'Не установлено'}\n\n"
            f"<b>Переменные:</b>\n"
            f"%name% — имя\n"
            f"%fullname% — полное имя\n"
            f"%mention% — упоминание\n"
            f"%username% — @username\n"
            f"%id% — ID\n\n"
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
    """Режим тишины"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 5:
        await message.reply("❌ Недостаточно прав!")
        return
    
    enabled = await db.toggle_silence(message.chat.id)
    if enabled:
        await message.answer("🔇 Режим тишины <b>включён</b>\nСообщения от пользователей без роли будут удаляться", parse_mode="HTML")
    else:
        await message.answer("🔊 Режим тишины <b>выключен</b>", parse_mode="HTML")


@router.message(Command("antiflood", "антифлуд"))
async def cmd_antiflood(message: Message):
    """Антифлуд"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 5:
        await message.reply("❌ Недостаточно прав!")
        return
    
    enabled = await db.toggle_antiflood(message.chat.id)
    if enabled:
        await message.answer(
            f"🛡 Антифлуд <b>включён</b>\n"
            f"При {SPAM_COUNT} сообщениях за {SPAM_INTERVAL} сек — автомут",
            parse_mode="HTML"
        )
    else:
        await message.answer("🛡 Антифлуд <b>выключен</b>", parse_mode="HTML")


@router.message(Command("filter", "фильтр"))
async def cmd_filter(message: Message):
    """Фильтр запрещённых слов"""
    my_role = await get_role(message.from_user.id, message.chat.id)
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
    """Добавить запрещённое слово"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 5:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("❌ Укажите слово: /banword слово")
        return
    
    word = args[1].lower()
    await db.add_banword(message.chat.id, word)
    await message.answer(f"✅ Слово «{word}» запрещено")


@router.message(Command("unbanword", "разрешить"))
async def cmd_unbanword(message: Message):
    """Удалить из запрещённых"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 5:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("❌ Укажите слово!")
        return
    
    word = args[1].lower()
    await db.remove_banword(message.chat.id, word)
    await message.answer(f"✅ Слово «{word}» разрешено")


@router.message(Command("banwords", "bws", "запрещённые"))
async def cmd_banwords(message: Message):
    """Список запрещённых слов"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 5:
        await message.reply("❌ Недостаточно прав!")
        return
    
    words = await db.get_banwords(message.chat.id)
    if not words:
        await message.answer("📋 Запрещённых слов нет")
        return
    
    await message.answer(f"🚫 <b>Запрещённые слова:</b>\n{', '.join(words)}", parse_mode="HTML")


@router.message(Command("zov", "зов"))
async def cmd_zov(message: Message):
    """Упомянуть всех"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 3:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split(maxsplit=1)
    reason = args[1] if len(args) > 1 else "Вызов"
    
    await message.answer(
        f"📣 <b>Внимание всем участникам!</b>\n\n"
        f"<b>Причина:</b> {reason}\n"
        f"<b>Вызвал:</b> {await mention(message.from_user.id)}",
        parse_mode="HTML"
    )


@router.message(Command("broadcast", "рассылка"))
async def cmd_broadcast(message: Message):
    """Рассылка по всем чатам"""
    my_role = await get_role(message.from_user.id, message.chat.id)
    if my_role < 9:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split(maxsplit=1)
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
        except:
            pass
    
    await message.answer(f"✅ Отправлено в {sent} чатов")


# =============================================================================
# ОБРАБОТКА ВСЕХ СООБЩЕНИЙ
# =============================================================================

@router.message(F.chat.type.in_([ChatType.GROUP, ChatType.SUPERGROUP]))
async def on_message(message: Message):
    """Обработка всех сообщений в группах"""
    if not message.from_user:
        return
    
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Регистрация чата
    await db.register_chat(chat_id, message.chat.title or "")
    
    # Кэширование username
    if message.from_user.username:
        await db.cache_username(user_id, message.from_user.username)
    
    # Записываем сообщение
    if message.message_id:
        await db.add_message(user_id, chat_id, message.message_id)
    
    # Получаем роль
    role = await get_role(user_id, chat_id)
    
    # Режим тишины (удаляем сообщения от пользователей без роли)
    if await db.is_silence(chat_id) and role < 1:
        try:
            await message.delete()
        except:
            pass
        return
    
    # Проверяем мут
    mute = await db.get_mute(user_id, chat_id)
    if mute and mute.get('until', 0) > time.time():
        try:
            await message.delete()
        except:
            pass
        return
    
    # Антифлуд (только для пользователей без роли)
    if role < 1 and await db.is_antiflood(chat_id):
        if await db.check_spam(user_id, chat_id, SPAM_INTERVAL, SPAM_COUNT):
            until = int(time.time()) + 1800  # 30 минут
            await db.add_mute(user_id, chat_id, 0, "Антифлуд: спам", until)
            try:
                await bot.restrict_chat_member(
                    chat_id, user_id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=timedelta(minutes=30)
                )
                await message.delete()
                await bot.send_message(
                    chat_id,
                    f"🔇 {await mention(user_id)} получил мут на 30 мин за спам",
                    parse_mode="HTML"
                )
            except:
                pass
            return
    
    # Фильтр запрещённых слов (только для пользователей без роли)
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
                        permissions=ChatPermissions(can_send_messages=False),
                        until_date=timedelta(minutes=30)
                    )
                    await bot.send_message(
                        chat_id,
                        f"🔇 {await mention(user_id)} получил мут на 30 мин за запрещённое слово",
                        parse_mode="HTML"
                    )
                except:
                    pass
                return


# =============================================================================
# QUICK MUTE CALLBACK
# =============================================================================

@router.callback_query(F.data.startswith("qmute:"))
async def cb_quick_mute(call: CallbackQuery):
    """Быстрый мут на 30 минут"""
    parts = call.data.split(":")
    target, chat_id = int(parts[1]), int(parts[2])
    
    role = await get_role(call.from_user.id, chat_id)
    if role < 1:
        await call.answer("Недостаточно прав!", show_alert=True)
        return
    
    target_role = await get_role(target, chat_id)
    if target_role >= role:
        await call.answer("Нельзя замутить этого пользователя!", show_alert=True)
        return
    
    until = int(time.time()) + 1800
    try:
        await bot.restrict_chat_member(
            chat_id, target,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=timedelta(minutes=30)
        )
        await db.add_mute(target, chat_id, call.from_user.id, "Быстрый мут", until)
        await call.answer("✅ Мут на 30 минут!", show_alert=True)
    except Exception as e:
        await call.answer(f"Ошибка: {e}", show_alert=True)


# =============================================================================
# ЗАПУСК
# =============================================================================

async def main():
    global db
    db = Database("database.db")
    await db.init()
    
    logger.info("🔵 Модерация Анонимные сообщения | Георгиевка")
    logger.info("Инициализация...")
    
    # Инициализация команды
    await init_staff()
    
    # Регистрация чатов из конфига
    for chat_id in MODERATED_CHATS:
        try:
            chat = await bot.get_chat(chat_id)
            await db.register_chat(chat_id, chat.title or "")
            logger.info(f"Registered chat: {chat_id} ({chat.title})")
        except Exception as e:
            logger.warning(f"Could not register chat {chat_id}: {e}")
    
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
