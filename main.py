"""
🔵 Модерация Анонимные сообщения | Георгиевка
Telegram бот для модерации групп с глобальными банами

Функции:
- Глобальный бан (бан во всех группах бота)
- Мут/бан/варн пользователей
- Автоматическая проверка при входе в группу
- Права доступа (роли)
- Запрещённые слова
- Антиспам
"""

import asyncio
import logging
import json
import os
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart, ChatMemberUpdatedFilter, IS_NOT_MEMBER, IS_MEMBER
from aiogram.types import (
    Message, CallbackQuery, ChatMemberUpdated,
    InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ChatMemberStatus, ChatType

from db import Database

# Конфигурация
with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

BOT_TOKEN = config.get("bot_token", os.getenv("BOT_TOKEN", ""))
OWNER_ID = config.get("owner_id", 0)  # ID владельца бота (глобальный админ)

# Список групп для модерации
MODERATED_GROUPS = config.get("moderated_groups", [])

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Инициализация
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

db: Database = None

# =============================================================================
# РОЛИ (Иерархия должностей)
# 10 - Владелец
# 09 - Главный модератор
# 08 - Зам. главного модератора
# 07 - Куратор групп/каналов
# 06 - Главный технический специалист
# 05 - Технический специалист
# 04 - Куратор модерации
# 03 - Старший модератор
# 02 - Модератор
# 01 - Младший модератор
# 00 - Пользователь
# =============================================================================

ROLE_NAMES = {
    0: "Пользователь",
    1: "Младший модератор",
    2: "Модератор",
    3: "Старший модератор",
    4: "Куратор модерации",
    5: "Технический специалист",
    6: "Главный технический специалист",
    7: "Куратор групп/каналов",
    8: "Зам. главного модератора",
    9: "Главный модератор",
    10: "Владелец"
}

# Начальный состав команды (username -> role)
INITIAL_STAFF = {
    "Timo4ka115": 10,      # Владелец
    "treshshshhh": 9,      # Главный модератор
    "GashiSH40": 8,        # Зам. главного модератора
    "Wisnswiw": 7,         # Куратор групп/каналов
    "ishakbest": 6,        # Главный технический специалист
}

# Права по ролям
# 1-2: мут до 1ч, варн, удаление
# 3-4: мут до 24ч, снятие варна
# 5-6: мут без лимита, кик, тех. настройки
# 7-8: бан/разбан, управление ролями 1-5
# 9-10: глобальный бан, все права

# Лимиты мута по ролям (в секундах, 0 = без лимита)
MUTE_LIMITS = {
    1: 3600,        # Младший модератор - до 1 часа
    2: 3600,        # Модератор - до 1 часа
    3: 86400,       # Старший модератор - до 24 часов
    4: 86400,       # Куратор модерации - до 24 часов
    5: 0,           # Тех. специалист+ - без лимита
    6: 0,
    7: 0,
    8: 0,
    9: 0,
    10: 0,
}


async def get_role(user_id: int, chat_id: int) -> int:
    """Получить роль пользователя"""
    # Проверяем глобальную роль из БД
    global_role = await db.get_global_role(user_id)
    if global_role and global_role > 0:
        return global_role
    # Владелец бота (fallback)
    if user_id == OWNER_ID:
        return 10
    # Роль в чате
    return await db.get_user_role(user_id, chat_id)


async def init_staff():
    """Инициализация начального состава команды"""
    for username, role in INITIAL_STAFF.items():
        try:
            # Получаем user_id по username
            chat = await bot.get_chat(f"@{username}")
            user_id = chat.id
            # Сохраняем в БД
            await db.set_global_role(user_id, role, username)
            logger.info(f"Initialized staff: @{username} (ID: {user_id}) -> role {role}")
        except Exception as e:
            logger.warning(f"Could not init staff @{username}: {e}")


async def get_user_mention(user_id: int, chat_id: int = None) -> str:
    """Получить упоминание пользователя"""
    try:
        chat = await bot.get_chat(user_id)
        name = chat.full_name or f"User {user_id}"
        return f'<a href="tg://user?id={user_id}">{name}</a>'
    except:
        return f'<a href="tg://user?id={user_id}">Пользователь {user_id}</a>'


def parse_time(time_str: str) -> Optional[int]:
    """Парсинг времени (1m, 1h, 1d, 1w)"""
    if not time_str:
        return None
    
    time_str = time_str.lower().strip()
    multipliers = {'m': 60, 'h': 3600, 'd': 86400, 'w': 604800}
    
    for suffix, mult in multipliers.items():
        if time_str.endswith(suffix):
            try:
                return int(time_str[:-1]) * mult
            except ValueError:
                return None
    
    # Если просто число - считаем минуты
    try:
        return int(time_str) * 60
    except ValueError:
        return None


def format_time(seconds: int) -> str:
    """Форматирование времени"""
    if seconds < 60:
        return f"{seconds} сек"
    elif seconds < 3600:
        return f"{seconds // 60} мин"
    elif seconds < 86400:
        return f"{seconds // 3600} ч"
    else:
        return f"{seconds // 86400} д"


# =============================================================================
# ПРОВЕРКА ПРИ ВХОДЕ В ГРУППУ
# =============================================================================

@router.chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def on_user_join(event: ChatMemberUpdated):
    """Проверка при входе пользователя в группу"""
    user_id = event.new_chat_member.user.id
    chat_id = event.chat.id
    
    # Регистрируем чат если его нет
    await db.register_chat(chat_id, event.chat.title or "Без названия")
    
    # Проверяем глобальный бан
    gban = await db.get_global_ban(user_id)
    if gban:
        reason = gban.get('reason', 'Глобальная блокировка')
        try:
            # Банить и кикнуть
            await bot.ban_chat_member(chat_id, user_id)
            logger.info(f"Глобальный бан: user={user_id} kicked from chat={chat_id}")
            
            # Уведомление в чат
            await bot.send_message(
                chat_id,
                f"🚫 <b>Глобальная блокировка</b>\n\n"
                f"Пользователь {await get_user_mention(user_id)} заблокирован во всех группах.\n"
                f"<b>Причина:</b> {reason}",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to ban user {user_id} in chat {chat_id}: {e}")
        return
    
    # Проверяем локальный бан
    local_ban = await db.get_ban(user_id, chat_id)
    if local_ban:
        try:
            await bot.ban_chat_member(chat_id, user_id)
            logger.info(f"Local ban: user={user_id} kicked from chat={chat_id}")
        except Exception as e:
            logger.error(f"Failed to ban user {user_id}: {e}")
        return
    
    # Приветствие (если включено)
    welcome = await db.get_setting(chat_id, "welcome_message")
    if welcome:
        welcome = welcome.replace("%name%", event.new_chat_member.user.first_name or "друг")
        welcome = welcome.replace("%mention%", await get_user_mention(user_id))
        try:
            await bot.send_message(chat_id, welcome, parse_mode="HTML")
        except:
            pass


# =============================================================================
# КОМАНДЫ МОДЕРАЦИИ
# =============================================================================

@router.message(Command("start"))
async def cmd_start(message: Message):
    """Команда /start"""
    if message.chat.type == ChatType.PRIVATE:
        await message.answer(
            "🔵 <b>Модерация Анонимные сообщения | Георгиевка</b>\n\n"
            "Бот для модерации групп с глобальными банами.\n\n"
            "<b>Команды:</b>\n"
            "/help - справка по командам\n"
            "/mystatus - ваш статус в группе\n\n"
            "Добавьте бота в группу с правами администратора для модерации.",
            parse_mode="HTML"
        )
    else:
        await db.register_chat(message.chat.id, message.chat.title or "")
        await message.answer("✅ Бот активирован в этой группе!")


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Справка по командам"""
    role = await get_role(message.from_user.id, message.chat.id)
    
    text = "🔵 <b>Команды модерации</b>\n\n"
    
    text += "<b>👤 Для всех:</b>\n"
    text += "/mystatus - ваш статус\n"
    text += "/rules - правила группы\n"
    text += "/staff - состав команды\n\n"
    
    if role >= 1:
        text += "<b>🛡 Младший модератор (1-2):</b>\n"
        text += "/mute @user время причина - мут (до 1ч)\n"
        text += "/warn @user причина - предупреждение\n"
        text += "/del (реплай) - удалить сообщение\n\n"
    
    if role >= 3:
        text += "<b>🛡 Старший модератор (3-4):</b>\n"
        text += "/mute - мут до 24ч\n"
        text += "/unmute @user - снять мут\n"
        text += "/unwarn @user - снять варн\n\n"
    
    if role >= 5:
        text += "<b>⚙️ Технический специалист (5-6):</b>\n"
        text += "/mute - мут без лимита\n"
        text += "/kick @user - кикнуть\n"
        text += "/settings - настройки чата\n\n"
    
    if role >= 7:
        text += "<b>👑 Куратор групп (7-8):</b>\n"
        text += "/ban @user причина - бан\n"
        text += "/unban @user - разбан\n"
        text += "/setrole @user 1-5 - управление ролями\n"
        text += "/banword слово - запрещённое слово\n\n"
    
    if role >= 9:
        text += "<b>🌐 Главный модератор / Владелец (9-10):</b>\n"
        text += "/gban @user причина - глобальный бан\n"
        text += "/gunban @user - снять глобальный бан\n"
        text += "/gbanlist - список глобальных банов\n"
        text += "/setrole @user 1-8 - все роли\n"
        text += "/addstaff @user роль - добавить в команду\n"
        text += "/broadcast текст - рассылка по группам\n"
    
    await message.answer(text, parse_mode="HTML")


@router.message(Command("mystatus"))
async def cmd_mystatus(message: Message):
    """Статус пользователя"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    role = await get_role(user_id, chat_id)
    role_name = ROLE_NAMES.get(role, "Неизвестно")
    
    # Проверяем бан/мут
    is_gbanned = await db.get_global_ban(user_id)
    is_banned = await db.get_ban(user_id, chat_id)
    mute_info = await db.get_mute(user_id, chat_id)
    warns = await db.get_warns_count(user_id, chat_id)
    
    text = f"👤 <b>Ваш статус</b>\n\n"
    text += f"<b>Роль:</b> {role_name} ({role})\n"
    text += f"<b>Предупреждений:</b> {warns}/3\n"
    
    if is_gbanned:
        text += f"🚫 <b>Глобальный бан:</b> {is_gbanned.get('reason', '-')}\n"
    if is_banned:
        text += f"🚫 <b>Бан в этом чате</b>\n"
    if mute_info:
        until = mute_info.get('until', 0)
        if until > datetime.now().timestamp():
            text += f"🔇 <b>Мут до:</b> {datetime.fromtimestamp(until).strftime('%d.%m %H:%M')}\n"
    
    await message.answer(text, parse_mode="HTML")


@router.message(Command("staff"))
async def cmd_staff(message: Message):
    """Показать состав команды"""
    staff = await db.get_all_staff()
    
    if not staff:
        await message.answer("📋 Состав команды пуст")
        return
    
    text = "👥 <b>Состав команды</b>\n\n"
    
    # Группируем по ролям
    by_role = {}
    for s in staff:
        r = s['role']
        if r not in by_role:
            by_role[r] = []
        by_role[r].append(s)
    
    for role_num in sorted(by_role.keys(), reverse=True):
        role_name = ROLE_NAMES.get(role_num, f"Роль {role_num}")
        text += f"<b>{role_num:02d}. {role_name}</b>\n"
        for s in by_role[role_num]:
            username = s.get('username', '')
            if username:
                text += f"   @{username}\n"
            else:
                text += f"   ID: {s['user_id']}\n"
        text += "\n"
    
    await message.answer(text, parse_mode="HTML")


@router.message(Command("addstaff"))
async def cmd_addstaff(message: Message):
    """Добавить в команду"""
    role = await get_role(message.from_user.id, message.chat.id)
    if role < 9:
        await message.reply("❌ Недостаточно прав! Нужен уровень 9+ (Главный модератор/Владелец)")
        return
    
    args = message.text.split()
    if len(args) < 3:
        await message.reply(
            "❌ Использование: /addstaff @username роль\n"
            "Пример: /addstaff @user 3\n\n"
            "<b>Роли:</b>\n"
            "01 - Младший модератор\n"
            "02 - Модератор\n"
            "03 - Старший модератор\n"
            "04 - Куратор модерации\n"
            "05 - Технический специалист\n"
            "06 - Главный тех. специалист\n"
            "07 - Куратор групп/каналов\n"
            "08 - Зам. главного модератора\n"
            "09 - Главный модератор\n"
            "10 - Владелец",
            parse_mode="HTML"
        )
        return
    
    target_username = args[1].lstrip("@")
    try:
        new_role = int(args[2])
    except:
        await message.reply("❌ Роль должна быть числом 1-10")
        return
    
    if new_role < 1 or new_role > 10:
        await message.reply("❌ Роль должна быть от 1 до 10")
        return
    
    if new_role >= role:
        await message.reply("❌ Нельзя выдать роль выше или равную своей!")
        return
    
    # Получаем user_id
    try:
        chat = await bot.get_chat(f"@{target_username}")
        target_id = chat.id
    except:
        await message.reply(f"❌ Пользователь @{target_username} не найден")
        return
    
    await db.set_global_role(target_id, new_role, target_username)
    role_name = ROLE_NAMES.get(new_role, f"Роль {new_role}")
    
    await message.answer(
        f"✅ <b>Добавлен в команду</b>\n\n"
        f"<b>Пользователь:</b> @{target_username}\n"
        f"<b>Роль:</b> {role_name} ({new_role})",
        parse_mode="HTML"
    )


@router.message(Command("removestaff"))
async def cmd_removestaff(message: Message):
    """Удалить из команды"""
    role = await get_role(message.from_user.id, message.chat.id)
    if role < 9:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split()
    if len(args) < 2:
        await message.reply("❌ Использование: /removestaff @username")
        return
    
    target_username = args[1].lstrip("@")
    
    try:
        chat = await bot.get_chat(f"@{target_username}")
        target_id = chat.id
    except:
        await message.reply(f"❌ Пользователь @{target_username} не найден")
        return
    
    target_role = await db.get_global_role(target_id)
    if target_role >= role:
        await message.reply("❌ Нельзя удалить пользователя с такой же или выше ролью!")
        return
    
    await db.remove_global_role(target_id)
    await message.answer(f"✅ @{target_username} удалён из команды", parse_mode="HTML")


# =============================================================================
# ГЛОБАЛЬНЫЙ БАН
# =============================================================================

@router.message(Command("gban"))
async def cmd_gban(message: Message):
    """Глобальный бан"""
    role = await get_role(message.from_user.id, message.chat.id)
    if role < 9:
        await message.reply("❌ Недостаточно прав! Нужен уровень 9+ (Главный модератор/Владелец)")
        return
    
    args = message.text.split(maxsplit=2)
    
    # Получаем user_id
    target_id = None
    if message.reply_to_message:
        target_id = message.reply_to_message.from_user.id
        reason = args[1] if len(args) > 1 else "Нарушение правил"
    elif len(args) >= 2:
        # Парсим @username или ID
        target = args[1].lstrip("@")
        if target.isdigit():
            target_id = int(target)
        else:
            try:
                chat = await bot.get_chat(f"@{target}")
                target_id = chat.id
            except:
                await message.reply("❌ Пользователь не найден")
                return
        reason = args[2] if len(args) > 2 else "Нарушение правил"
    else:
        await message.reply("❌ Укажите пользователя: /gban @user причина\nИли ответьте на сообщение")
        return
    
    if not target_id:
        await message.reply("❌ Не удалось определить пользователя")
        return
    
    # Нельзя банить админов
    if await db.is_global_admin(target_id) or target_id == OWNER_ID:
        await message.reply("❌ Нельзя заблокировать глобального администратора!")
        return
    
    # Добавляем в глобальный бан
    await db.add_global_ban(target_id, message.from_user.id, reason)
    
    # Банить во всех группах
    chats = await db.get_all_chats()
    banned_count = 0
    for chat in chats:
        try:
            await bot.ban_chat_member(chat['chat_id'], target_id)
            banned_count += 1
        except:
            pass
    
    await message.answer(
        f"🚫 <b>Глобальная блокировка</b>\n\n"
        f"<b>Пользователь:</b> {await get_user_mention(target_id)}\n"
        f"<b>Причина:</b> {reason}\n"
        f"<b>Модератор:</b> {await get_user_mention(message.from_user.id)}\n"
        f"<b>Забанен в группах:</b> {banned_count}",
        parse_mode="HTML"
    )
    logger.info(f"GBAN: user={target_id}, by={message.from_user.id}, reason={reason}")


@router.message(Command("gunban"))
async def cmd_gunban(message: Message):
    """Снять глобальный бан"""
    role = await get_role(message.from_user.id, message.chat.id)
    if role < 9:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("❌ Укажите пользователя: /gunban @user или ID")
        return
    
    target = args[1].lstrip("@")
    if target.isdigit():
        target_id = int(target)
    else:
        try:
            chat = await bot.get_chat(f"@{target}")
            target_id = chat.id
        except:
            await message.reply("❌ Пользователь не найден")
            return
    
    await db.remove_global_ban(target_id)
    
    # Разбанить во всех группах
    chats = await db.get_all_chats()
    unbanned_count = 0
    for chat in chats:
        try:
            await bot.unban_chat_member(chat['chat_id'], target_id, only_if_banned=True)
            unbanned_count += 1
        except:
            pass
    
    await message.answer(
        f"✅ <b>Глобальная разблокировка</b>\n\n"
        f"<b>Пользователь:</b> {await get_user_mention(target_id)}\n"
        f"<b>Разбанен в группах:</b> {unbanned_count}",
        parse_mode="HTML"
    )


@router.message(Command("gbanlist"))
async def cmd_gbanlist(message: Message):
    """Список глобальных банов"""
    role = await get_role(message.from_user.id, message.chat.id)
    if role < 9:
        await message.reply("❌ Недостаточно прав!")
        return
    
    bans = await db.get_global_bans()
    if not bans:
        await message.answer("📋 Список глобальных банов пуст")
        return
    
    text = "🚫 <b>Глобальные блокировки</b>\n\n"
    for ban in bans[:20]:
        text += f"• <code>{ban['user_id']}</code> - {ban.get('reason', '-')[:30]}\n"
    
    if len(bans) > 20:
        text += f"\n<i>...и ещё {len(bans) - 20}</i>"
    
    await message.answer(text, parse_mode="HTML")


# =============================================================================
# ЛОКАЛЬНЫЕ КОМАНДЫ МОДЕРАЦИИ
# =============================================================================

@router.message(Command("mute"))
async def cmd_mute(message: Message):
    """Мут пользователя"""
    if message.chat.type == ChatType.PRIVATE:
        return
    
    role = await get_role(message.from_user.id, message.chat.id)
    if role < 2:
        await message.reply("❌ Недостаточно прав! Нужен уровень 2+ (Младший модератор)")
        return
    
    args = message.text.split(maxsplit=3)
    
    # Получаем user_id
    target_id = None
    time_arg = "30m"
    reason = "Нарушение правил"
    
    if message.reply_to_message:
        target_id = message.reply_to_message.from_user.id
        if len(args) > 1:
            time_arg = args[1]
        if len(args) > 2:
            reason = args[2]
    elif len(args) >= 2:
        target = args[1].lstrip("@")
        if target.isdigit():
            target_id = int(target)
        else:
            try:
                chat = await bot.get_chat(f"@{target}")
                target_id = chat.id
            except:
                await message.reply("❌ Пользователь не найден")
                return
        if len(args) > 2:
            time_arg = args[2]
        if len(args) > 3:
            reason = args[3]
    else:
        await message.reply("❌ Укажите пользователя: /mute @user время причина")
        return
    
    # Проверяем роли
    target_role = await get_role(target_id, message.chat.id)
    if target_role >= role:
        await message.reply("❌ Нельзя замутить пользователя с такой же или выше ролью!")
        return
    
    # Парсим время
    duration = parse_time(time_arg)
    if not duration:
        duration = 30 * 60  # 30 минут по умолчанию
    
    # Проверяем лимит мута по роли
    mute_limit = MUTE_LIMITS.get(role, 0)
    if mute_limit > 0 and duration > mute_limit:
        await message.reply(f"❌ Ваш лимит мута: {format_time(mute_limit)}. Используйте меньшее время.")
        return
    
    until = datetime.now().timestamp() + duration
    
    # Мутим в Telegram
    try:
        await bot.restrict_chat_member(
            message.chat.id,
            target_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=timedelta(seconds=duration)
        )
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")
        return
    
    # Сохраняем в БД
    await db.add_mute(target_id, message.chat.id, message.from_user.id, reason, until)
    
    await message.answer(
        f"🔇 <b>Мут</b>\n\n"
        f"<b>Пользователь:</b> {await get_user_mention(target_id)}\n"
        f"<b>Время:</b> {format_time(duration)}\n"
        f"<b>Причина:</b> {reason}\n"
        f"<b>Модератор:</b> {await get_user_mention(message.from_user.id)}",
        parse_mode="HTML"
    )


@router.message(Command("unmute"))
async def cmd_unmute(message: Message):
    """Снять мут"""
    if message.chat.type == ChatType.PRIVATE:
        return
    
    role = await get_role(message.from_user.id, message.chat.id)
    if role < 4:
        await message.reply("❌ Недостаточно прав! Нужен уровень 4+ (Старший модератор)")
        return
    
    # Получаем user_id
    target_id = None
    if message.reply_to_message:
        target_id = message.reply_to_message.from_user.id
    else:
        args = message.text.split(maxsplit=1)
        if len(args) >= 2:
            target = args[1].lstrip("@")
            if target.isdigit():
                target_id = int(target)
            else:
                try:
                    chat = await bot.get_chat(f"@{target}")
                    target_id = chat.id
                except:
                    await message.reply("❌ Пользователь не найден")
                    return
        else:
            await message.reply("❌ Укажите пользователя")
            return
    
    try:
        await bot.restrict_chat_member(
            message.chat.id,
            target_id,
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
    
    await db.remove_mute(target_id, message.chat.id)
    await message.answer(f"✅ Мут снят с {await get_user_mention(target_id)}", parse_mode="HTML")


@router.message(Command("ban"))
async def cmd_ban(message: Message):
    """Бан пользователя"""
    if message.chat.type == ChatType.PRIVATE:
        return
    
    role = await get_role(message.from_user.id, message.chat.id)
    if role < 6:
        await message.reply("❌ Недостаточно прав! Нужен уровень 6+ (Администратор)")
        return
    
    args = message.text.split(maxsplit=2)
    
    target_id = None
    reason = "Нарушение правил"
    
    if message.reply_to_message:
        target_id = message.reply_to_message.from_user.id
        if len(args) > 1:
            reason = args[1]
    elif len(args) >= 2:
        target = args[1].lstrip("@")
        if target.isdigit():
            target_id = int(target)
        else:
            try:
                chat = await bot.get_chat(f"@{target}")
                target_id = chat.id
            except:
                await message.reply("❌ Пользователь не найден")
                return
        if len(args) > 2:
            reason = args[2]
    else:
        await message.reply("❌ Укажите пользователя: /ban @user причина")
        return
    
    target_role = await get_role(target_id, message.chat.id)
    if target_role >= role:
        await message.reply("❌ Нельзя забанить пользователя с такой же или выше ролью!")
        return
    
    try:
        await bot.ban_chat_member(message.chat.id, target_id)
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")
        return
    
    await db.add_ban(target_id, message.chat.id, message.from_user.id, reason)
    
    await message.answer(
        f"🚫 <b>Бан</b>\n\n"
        f"<b>Пользователь:</b> {await get_user_mention(target_id)}\n"
        f"<b>Причина:</b> {reason}\n"
        f"<b>Модератор:</b> {await get_user_mention(message.from_user.id)}",
        parse_mode="HTML"
    )


@router.message(Command("unban"))
async def cmd_unban(message: Message):
    """Разбан пользователя"""
    if message.chat.type == ChatType.PRIVATE:
        return
    
    role = await get_role(message.from_user.id, message.chat.id)
    if role < 6:
        await message.reply("❌ Недостаточно прав! Нужен уровень 6+ (Администратор)")
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("❌ Укажите пользователя: /unban @user или ID")
        return
    
    target = args[1].lstrip("@")
    if target.isdigit():
        target_id = int(target)
    else:
        try:
            chat = await bot.get_chat(f"@{target}")
            target_id = chat.id
        except:
            await message.reply("❌ Пользователь не найден")
            return
    
    try:
        await bot.unban_chat_member(message.chat.id, target_id, only_if_banned=True)
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")
        return
    
    await db.remove_ban(target_id, message.chat.id)
    await message.answer(f"✅ Разбан: {await get_user_mention(target_id)}", parse_mode="HTML")


@router.message(Command("kick"))
async def cmd_kick(message: Message):
    """Кикнуть пользователя"""
    if message.chat.type == ChatType.PRIVATE:
        return
    
    role = await get_role(message.from_user.id, message.chat.id)
    if role < 5:
        await message.reply("❌ Недостаточно прав! Нужен уровень 5+ (Младший администратор)")
        return
    
    target_id = None
    if message.reply_to_message:
        target_id = message.reply_to_message.from_user.id
    else:
        args = message.text.split(maxsplit=1)
        if len(args) >= 2:
            target = args[1].lstrip("@")
            if target.isdigit():
                target_id = int(target)
            else:
                try:
                    chat = await bot.get_chat(f"@{target}")
                    target_id = chat.id
                except:
                    pass
    
    if not target_id:
        await message.reply("❌ Укажите пользователя")
        return
    
    target_role = await get_role(target_id, message.chat.id)
    if target_role >= role:
        await message.reply("❌ Нельзя кикнуть пользователя с такой же или выше ролью!")
        return
    
    try:
        await bot.ban_chat_member(message.chat.id, target_id)
        await bot.unban_chat_member(message.chat.id, target_id)  # Разбанить чтобы мог вернуться
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")
        return
    
    await message.answer(f"👢 {await get_user_mention(target_id)} кикнут из группы", parse_mode="HTML")


@router.message(Command("warn"))
async def cmd_warn(message: Message):
    """Предупреждение"""
    if message.chat.type == ChatType.PRIVATE:
        return
    
    role = await get_role(message.from_user.id, message.chat.id)
    if role < 1:
        await message.reply("❌ Недостаточно прав!")
        return
    
    args = message.text.split(maxsplit=2)
    
    target_id = None
    reason = "Нарушение правил"
    
    if message.reply_to_message:
        target_id = message.reply_to_message.from_user.id
        if len(args) > 1:
            reason = args[1]
    elif len(args) >= 2:
        target = args[1].lstrip("@")
        if target.isdigit():
            target_id = int(target)
        else:
            try:
                chat = await bot.get_chat(f"@{target}")
                target_id = chat.id
            except:
                await message.reply("❌ Пользователь не найден")
                return
        if len(args) > 2:
            reason = args[2]
    else:
        await message.reply("❌ Укажите пользователя")
        return
    
    target_role = await get_role(target_id, message.chat.id)
    if target_role >= role:
        await message.reply("❌ Нельзя выдать варн пользователю с такой же или выше ролью!")
        return
    
    await db.add_warn(target_id, message.chat.id, message.from_user.id, reason)
    warns = await db.get_warns_count(target_id, message.chat.id)
    
    text = (
        f"⚠️ <b>Предупреждение</b>\n\n"
        f"<b>Пользователь:</b> {await get_user_mention(target_id)}\n"
        f"<b>Причина:</b> {reason}\n"
        f"<b>Варнов:</b> {warns}/3\n"
        f"<b>Модератор:</b> {await get_user_mention(message.from_user.id)}"
    )
    
    # Автобан при 3 варнах
    if warns >= 3:
        try:
            await bot.ban_chat_member(message.chat.id, target_id)
            await db.add_ban(target_id, message.chat.id, 0, "Автобан: 3 варна")
            text += "\n\n🚫 <b>Автобан: достигнут лимит варнов!</b>"
        except:
            pass
    
    await message.answer(text, parse_mode="HTML")


@router.message(Command("unwarn"))
async def cmd_unwarn(message: Message):
    """Снять варн"""
    if message.chat.type == ChatType.PRIVATE:
        return
    
    role = await get_role(message.from_user.id, message.chat.id)
    if role < 3:
        await message.reply("❌ Недостаточно прав! Нужен уровень 3+ (Модератор)")
        return
    
    target_id = None
    if message.reply_to_message:
        target_id = message.reply_to_message.from_user.id
    else:
        args = message.text.split(maxsplit=1)
        if len(args) >= 2:
            target = args[1].lstrip("@")
            if target.isdigit():
                target_id = int(target)
            else:
                try:
                    chat = await bot.get_chat(f"@{target}")
                    target_id = chat.id
                except:
                    pass
    
    if not target_id:
        await message.reply("❌ Укажите пользователя")
        return
    
    await db.remove_warn(target_id, message.chat.id)
    warns = await db.get_warns_count(target_id, message.chat.id)
    await message.answer(f"✅ Варн снят. Осталось варнов: {warns}/3", parse_mode="HTML")


@router.message(Command("del"))
async def cmd_del(message: Message):
    """Удалить сообщение"""
    if message.chat.type == ChatType.PRIVATE:
        return
    
    role = await get_role(message.from_user.id, message.chat.id)
    if role < 1:
        await message.reply("❌ Недостаточно прав!")
        return
    
    if not message.reply_to_message:
        await message.reply("❌ Ответьте на сообщение, которое нужно удалить")
        return
    
    try:
        await message.reply_to_message.delete()
        await message.delete()
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


# =============================================================================
# УПРАВЛЕНИЕ РОЛЯМИ
# =============================================================================

@router.message(Command("setrole"))
async def cmd_setrole(message: Message):
    """Установить роль"""
    if message.chat.type == ChatType.PRIVATE:
        return
    
    role = await get_role(message.from_user.id, message.chat.id)
    if role < 7:
        await message.reply("❌ Недостаточно прав! Нужен уровень 7+ (Старший администратор)")
        return
    
    args = message.text.split()
    if len(args) < 3:
        # Определяем максимальную роль которую можно выдать
        if role == 7:
            max_role = 4
        elif role == 8:
            max_role = 6
        elif role >= 9:
            max_role = 8
        else:
            max_role = 0
        await message.reply(f"❌ Использование: /setrole @user уровень (1-{max_role})")
        return
    
    target = args[1].lstrip("@")
    if target.isdigit():
        target_id = int(target)
    else:
        try:
            chat = await bot.get_chat(f"@{target}")
            target_id = chat.id
        except:
            await message.reply("❌ Пользователь не найден")
            return
    
    try:
        new_role = int(args[2])
    except:
        await message.reply("❌ Уровень должен быть числом")
        return
    
    # Определяем максимальную роль которую можно выдать
    if role == 7:
        max_assignable = 4  # Старший админ может выдать до 4
    elif role == 8:
        max_assignable = 6  # Зам владельца до 6
    elif role == 9:
        max_assignable = 8  # Владелец до 8
    elif role == 10:
        max_assignable = 9  # Глобальный админ до 9
    else:
        max_assignable = 0
    
    if new_role < 0 or new_role > max_assignable:
        await message.reply(f"❌ Вы можете выдать роль от 0 до {max_assignable}")
        return
    
    # Нельзя изменить роль того, у кого роль >= твоей
    target_role = await get_role(target_id, message.chat.id)
    if target_role >= role:
        await message.reply("❌ Нельзя изменить роль пользователя с такой же или выше ролью!")
        return
    
    await db.set_user_role(target_id, message.chat.id, new_role)
    await message.answer(
        f"✅ {await get_user_mention(target_id)} получил роль: {ROLE_NAMES.get(new_role, 'Неизвестно')} ({new_role})",
        parse_mode="HTML"
    )


@router.message(Command("setmoder"))
async def cmd_setmoder(message: Message):
    """Выдать модератора (роль 3)"""
    if message.chat.type == ChatType.PRIVATE:
        return
    
    role = await get_role(message.from_user.id, message.chat.id)
    if role < 7:
        await message.reply("❌ Недостаточно прав! Нужен уровень 7+")
        return
    
    target_id = None
    if message.reply_to_message:
        target_id = message.reply_to_message.from_user.id
    else:
        args = message.text.split(maxsplit=1)
        if len(args) >= 2:
            target = args[1].lstrip("@")
            if target.isdigit():
                target_id = int(target)
            else:
                try:
                    chat = await bot.get_chat(f"@{target}")
                    target_id = chat.id
                except:
                    pass
    
    if not target_id:
        await message.reply("❌ Укажите пользователя")
        return
    
    await db.set_user_role(target_id, message.chat.id, 3)
    await message.answer(f"✅ {await get_user_mention(target_id)} теперь Модератор (3)", parse_mode="HTML")


@router.message(Command("setadmin"))
async def cmd_setadmin(message: Message):
    """Выдать админа (роль 6)"""
    if message.chat.type == ChatType.PRIVATE:
        return
    
    role = await get_role(message.from_user.id, message.chat.id)
    if role < 8:
        await message.reply("❌ Недостаточно прав! Нужен уровень 8+")
        return
    
    target_id = None
    if message.reply_to_message:
        target_id = message.reply_to_message.from_user.id
    else:
        args = message.text.split(maxsplit=1)
        if len(args) >= 2:
            target = args[1].lstrip("@")
            if target.isdigit():
                target_id = int(target)
            else:
                try:
                    chat = await bot.get_chat(f"@{target}")
                    target_id = chat.id
                except:
                    pass
    
    if not target_id:
        await message.reply("❌ Укажите пользователя")
        return
    
    await db.set_user_role(target_id, message.chat.id, 6)
    await message.answer(f"✅ {await get_user_mention(target_id)} теперь Администратор (6)", parse_mode="HTML")


@router.message(Command("addglobal"))
async def cmd_addglobal(message: Message):
    """Добавить глобального админа"""
    role = await get_role(message.from_user.id, message.chat.id)
    if role < 9:
        await message.reply("❌ Недостаточно прав! Нужен уровень 10")
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("❌ Укажите пользователя: /addglobal @user или ID")
        return
    
    target = args[1].lstrip("@")
    if target.isdigit():
        target_id = int(target)
    else:
        try:
            chat = await bot.get_chat(f"@{target}")
            target_id = chat.id
        except:
            await message.reply("❌ Пользователь не найден")
            return
    
    await db.add_global_admin(target_id)
    await message.answer(f"✅ {await get_user_mention(target_id)} теперь Глобальный администратор (10)", parse_mode="HTML")


# =============================================================================
# ЗАПУСК
# =============================================================================

async def main():
    global db
    db = Database("database.db")
    await db.init()
    
    logger.info("🔵 Модерация Анонимные сообщения | Георгиевка - запуск...")
    
    # Инициализация начального состава команды
    logger.info("Инициализация команды...")
    await init_staff()
    
    # Удаляем вебхук если есть
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Запускаем polling
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
