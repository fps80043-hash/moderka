"""
🔵 Модерация — v7.2

1. Кнопки ТОЛЬКО для выбора чата (из стафф-чата)
2. Логи в топик 1049 — с датой окончания
3. ЛС уведомления — с датами, причиной, модератором, ссылкой
4. Блокировка по @username и ID
5. /clear → роль 4+
6. /start — панель наказаний
7. Глобальный бан → топик 307
8. /getban /getwarn — просмотр наказаний
9. Выбор чата для действий из стафф-чата
"""

import asyncio
import json
import logging
import math
import os
import time
from datetime import datetime, timedelta
from typing import Optional, List

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, ChatMemberUpdatedFilter, IS_NOT_MEMBER, IS_MEMBER
from aiogram.types import (
    Message, CallbackQuery, ChatMemberUpdated,
    ChatPermissions, BotCommand, BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ChatType

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
STAFF_CHAT_ID: int = config.get("staff_chat_id", 0)
LOG_TOPIC_ID: int = config.get("log_topic_id", 0)
GBAN_TOPIC_ID: int = config.get("gban_topic_id", 0)
SUPPORT_LINK: str = config.get("support_link", "")
PRESET_STAFF: dict = config.get("preset_staff", {})
MAX_WARNS: int = config.get("max_warns", 3)
SPAM_INTERVAL: int = config.get("spam_interval_seconds", 2)
SPAM_COUNT: int = config.get("spam_messages_count", 3)
ANON_ADMIN_ROLE: int = config.get("anon_admin_role", 10)
PER_PAGE = 5

ANONYMOUS_BOT_ID = 1087968824

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

db: Database = None
BOT_ID: int = 0  # заполнится при старте

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


def is_staff_chat(message: Message) -> bool:
    return STAFF_CHAT_ID != 0 and message.chat.id == STAFF_CHAT_ID


def is_mod_context(message: Message) -> bool:
    if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
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
        return {"id": user_id, "username": cached or "", "full_name": f"@{cached}" if cached else f"ID:{user_id}"}


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
    # Реплай на сообщение — берём автора (но НЕ бота!)
    if message.reply_to_message:
        r = message.reply_to_message
        if r.from_user and not is_anon(r) and r.from_user.id != BOT_ID:
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


def parse_duration(s: str) -> Optional[int]:
    s = s.lower().strip()
    if s in ('0', 'навсегда', 'forever', 'пермач'):
        return 0
    multi = {'s': 1, 'с': 1, 'm': 60, 'м': 60, 'min': 60, 'мин': 60,
             'h': 3600, 'ч': 3600, 'd': 86400, 'д': 86400, 'дн': 86400}
    for suffix, mult in sorted(multi.items(), key=lambda x: -len(x[0])):
        if s.endswith(suffix):
            num = s[:-len(suffix)]
            try:
                return int(num) * mult
            except ValueError:
                return None
    try:
        return int(s) * 60
    except ValueError:
        return None


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


def now_str() -> str:
    return datetime.now().strftime('%d.%m.%Y %H:%M')


def end_date_str(duration: int) -> str:
    """Дата окончания наказания"""
    if duration <= 0:
        return "никогда"
    return fmt_ts(int(time.time()) + duration)


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
# ВЫБОР ЧАТА (кнопки из стафф-чата)
# =============================================================================

async def build_chat_selector(action_key: str) -> InlineKeyboardBuilder:
    """Строит кнопки с чатами + Все чаты + Отмена"""
    b = InlineKeyboardBuilder()
    chat_ids = await db.get_all_chat_ids()
    for cid in chat_ids:
        # Не показываем стафф-чат в списке
        if cid == STAFF_CHAT_ID:
            continue
        title = await db.get_chat_title(cid)
        # Обрезаем длинные названия
        short = title[:25] + "…" if len(title) > 25 else title
        b.button(text=f"💬 {short}", callback_data=f"chatsel:{action_key}:{cid}")
    b.button(text="🌐 Все чаты", callback_data=f"chatsel:{action_key}:all")
    b.button(text="❌ Отмена", callback_data="cancel:x")
    # По 1 кнопке на строку
    b.adjust(1)
    return b


# =============================================================================
# ЛОГ В ТОПИК — с датой окончания
# =============================================================================

async def log_action(action: str, target: int, caller: int,
                     reason: str = "", duration: int = -1, chat_id: int = 0):
    if not STAFF_CHAT_ID or not LOG_TOPIC_ID:
        return
    try:
        target_info = await get_user_info(target)
        caller_info = await get_user_info(caller)
        chat_title = await db.get_chat_title(chat_id) if chat_id else "все чаты"

        t_name = target_info['full_name']
        t_uname = f" (@{target_info['username']})" if target_info['username'] else ""
        c_name = caller_info['full_name']
        c_uname = f" (@{caller_info['username']})" if caller_info['username'] else ""

        text = f"📋 <b>{action}</b>\n━━━━━━━━━━━━━━━━\n"
        text += f"👤 Кому: {t_name}{t_uname}\n🆔 ID: <code>{target}</code>\n"
        if duration >= 0:
            text += f"⏱ Срок: {fmt_dur(duration)}\n"
            text += f"📅 Окончание: {end_date_str(duration)}\n"
        if reason:
            text += f"📝 Причина: {reason}\n"
        text += f"👮 Модератор: {c_name}{c_uname}\n"
        text += f"💬 Чат: {chat_title}\n"
        text += f"🕐 {now_str()}"

        await bot.send_message(STAFF_CHAT_ID, text, parse_mode="HTML",
                               message_thread_id=LOG_TOPIC_ID)
    except Exception as e:
        logger.error(f"log_action: {e}")


# =============================================================================
# ЛС УВЕДОМЛЕНИЕ — с датами и ссылкой
# =============================================================================

async def notify_user_dm(user_id: int, action_name: str, reason: str,
                         duration: int, caller_id: int):
    try:
        caller_info = await get_user_info(caller_id)
        mod_name = caller_info['full_name']

        text = f"⚠️ <b>{action_name}</b>\n\n"
        text += f"📅 Дата: {now_str()}\n"
        text += f"📅 Окончание: {end_date_str(duration)}\n"
        text += f"📝 Причина: {reason}\n"
        text += f"👮 Модератор: {mod_name}\n"
        if SUPPORT_LINK:
            text += f"\n{SUPPORT_LINK}"

        await bot.send_message(user_id, text, parse_mode="HTML")
    except Exception:
        pass


# =============================================================================
# ПРИМЕНЕНИЕ ДЕЙСТВИЙ
# =============================================================================

async def apply_warn(target: int, chat_ids: List[int], caller_id: int, reason: str):
    for cid in chat_ids:
        warns = await db.add_warn(target, cid, caller_id, reason)
        name = await mention(target, cid)
        if warns >= MAX_WARNS:
            try:
                await bot.ban_chat_member(cid, target)
                await asyncio.sleep(0.5)
                await bot.unban_chat_member(cid, target)
            except Exception:
                pass
            await db.clear_warns(target, cid)
            try:
                await bot.send_message(cid,
                    f"⚠️ {name} — предупреждение ({MAX_WARNS}/{MAX_WARNS})\nПричина: {reason}\n\n👢 Кикнут за {MAX_WARNS} варнов!",
                    parse_mode="HTML")
            except Exception:
                pass
        else:
            try:
                await bot.send_message(cid,
                    f"⚠️ {name} — предупреждение ({warns}/{MAX_WARNS})\nПричина: {reason}",
                    parse_mode="HTML")
            except Exception:
                pass
        await log_action("ВАРН", target, caller_id, reason, chat_id=cid)
    await notify_user_dm(target, "Вам выдано предупреждение", reason, -1, caller_id)


async def apply_mute(target: int, chat_ids: List[int], caller_id: int, reason: str, seconds: int):
    for cid in chat_ids:
        try:
            until = int(time.time()) + seconds if seconds > 0 else 0
            delta = timedelta(seconds=seconds) if seconds > 0 else None
            await bot.restrict_chat_member(cid, target, permissions=muted_permissions(), until_date=delta)
            await db.add_mute(target, cid, caller_id, reason, until)
            name = await mention(target, cid)
            await bot.send_message(cid,
                f"🔇 {name} замучен на {fmt_dur(seconds)}\nПричина: {reason}", parse_mode="HTML")
        except Exception as e:
            logger.error(f"mute {target} in {cid}: {e}")
        await log_action("МУТ", target, caller_id, reason, seconds, cid)
    await notify_user_dm(target, "Вы замучены", reason, seconds, caller_id)


async def apply_ban(target: int, chat_ids: List[int], caller_id: int, reason: str, seconds: int):
    for cid in chat_ids:
        try:
            delta = timedelta(seconds=seconds) if seconds > 0 else None
            until = int(time.time()) + seconds if seconds > 0 else 0
            await bot.ban_chat_member(cid, target, until_date=delta)
            await db.add_ban(target, cid, caller_id, reason, until)
            name = await mention(target, cid)
            await bot.send_message(cid,
                f"🚫 {name} забанен на {fmt_dur(seconds)}\nПричина: {reason}", parse_mode="HTML")
        except Exception as e:
            logger.error(f"ban {target} in {cid}: {e}")
        await log_action("БАН", target, caller_id, reason, seconds, cid)
    await notify_user_dm(target, "Вы заблокированы", reason, seconds, caller_id)


async def apply_kick(target: int, chat_ids: List[int], caller_id: int, reason: str):
    for cid in chat_ids:
        try:
            await bot.ban_chat_member(cid, target)
            await asyncio.sleep(0.5)
            await bot.unban_chat_member(cid, target)
            name = await mention(target, cid)
            await bot.send_message(cid, f"👢 {name} кикнут\nПричина: {reason}", parse_mode="HTML")
        except Exception:
            pass
        await log_action("КИК", target, caller_id, reason, chat_id=cid)
    await notify_user_dm(target, "Вы кикнуты из группы", reason, -1, caller_id)


async def apply_unmute(target: int, chat_ids: List[int], caller_id: int):
    for cid in chat_ids:
        try:
            await bot.restrict_chat_member(cid, target, permissions=full_permissions())
            await db.remove_mute(target, cid)
        except Exception:
            pass
    await log_action("РАЗМУТ", target, caller_id)


async def apply_unban(target: int, chat_ids: List[int], caller_id: int):
    for cid in chat_ids:
        try:
            await bot.unban_chat_member(cid, target, only_if_banned=True)
            await db.remove_ban(target, cid)
        except Exception:
            pass
    await log_action("РАЗБАН", target, caller_id)


async def apply_unwarn(target: int, chat_ids: List[int], caller_id: int):
    for cid in chat_ids:
        await db.remove_warn(target, cid)
    await log_action("СНЯТИЕ ВАРНА", target, caller_id)


# =============================================================================
# /START — ЛС
# =============================================================================

@router.message(Command("start"))
async def cmd_start(message: Message):
    if message.chat.type != ChatType.PRIVATE:
        return
    if not message.from_user:
        return

    uid = message.from_user.id
    punishments = await db.get_user_all_punishments(uid)

    text = "👋 <b>Привет!</b>\nЯ бот модерации группы.\n\n"
    found = False

    if punishments["global_ban"]:
        gb = punishments["global_ban"]
        text += f"🌐 <b>Глобальный бан</b>\n  Дата: {fmt_ts(gb.get('banned_at', 0))}\n  Окончание: никогда\n  Причина: {gb.get('reason', '—')}\n\n"
        found = True

    for ban in punishments["bans"]:
        chat_title = await db.get_chat_title(ban['chat_id'])
        until = ban.get('until', 0)
        unblock = fmt_ts(until) if until and until > int(time.time()) else ("никогда" if not until else "истёк")
        text += f"🚫 <b>Бан</b> — {chat_title}\n  Дата: {fmt_ts(ban.get('banned_at', 0))}\n  Окончание: {unblock}\n  Причина: {ban.get('reason', '—')}\n\n"
        found = True

    for mute in punishments["mutes"]:
        chat_title = await db.get_chat_title(mute['chat_id'])
        until = mute.get('until', 0)
        unblock = fmt_ts(until) if until and until > int(time.time()) else ("никогда" if not until else "истёк")
        text += f"🔇 <b>Мут</b> — {chat_title}\n  Дата: {fmt_ts(mute.get('muted_at', 0))}\n  Окончание: {unblock}\n  Причина: {mute.get('reason', '—')}\n\n"
        found = True

    for warn in punishments["warns"]:
        chat_title = await db.get_chat_title(warn['chat_id'])
        text += f"⚠️ <b>Варны: {warn['count']}/{MAX_WARNS}</b> — {chat_title}\n  Причина: {warn.get('reason', '—')}\n\n"
        found = True

    if not found:
        text += "✅ У вас нет активных наказаний!\n"
    if SUPPORT_LINK:
        text += f"\n📞 Поддержка: {SUPPORT_LINK}"
    text += "\n\n/start — обновить"
    await message.answer(text, parse_mode="HTML")


# =============================================================================
# /HELP
# =============================================================================

@router.message(Command("help"))
async def cmd_help(message: Message):
    role = await get_caller_role(message)
    text = f"📖 <b>Команды модерации</b>\nВаша роль: <b>{ROLE_NAMES.get(role, '?')} ({role})</b>\n\n"
    if role >= 1:
        text += (
            "<b>Роль 1+:</b>\n"
            "/warn @user [причина]\n/unwarn @user\n"
            "/mute @user 30m [причина]\n/unmute @user\n"
            "/kick @user [причина]\n"
            "/getwarn @user — инфо о варнах\n"
            "/ro | /unro — RO режим\n"
            "/setnick @user Ник\n/warnlist [стр]\n\n"
        )
    if role >= 3:
        text += (
            "<b>Роль 3+:</b>\n"
            "/ban @user 7d [причина]\n/unban @user\n"
            "/getban @user — инфо о бане\n"
            "/banlist [стр] | /banlist global [стр]\n\n"
        )
    if role >= 4:
        text += "<b>Роль 4+:</b>\n/clear 10\n\n"
    if role >= 7:
        text += (
            "<b>Роль 7+:</b>\n"
            "/gban @user [причина] | /ungban @user\n"
            "/setrole @user ЧИСЛО | /removerole @user\n\n"
        )
    text += "/stats [@user]\n/staff — команда"
    await message.answer(text, parse_mode="HTML")


# =============================================================================
# /STATS
# =============================================================================

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
            f"Роль: {ROLE_NAMES.get(role, '?')} ({role})\nГлоб. бан: {'да' if is_gb else 'нет'}",
            parse_mode="HTML")

    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        target = message.from_user.id if message.from_user else None
    if not target:
        return await message.reply("❌ Не удалось определить пользователя")
    info = await get_user_info(target)
    cid = message.chat.id if not is_staff_chat(message) else 0
    role = await get_role(target, cid) if cid else await get_role(target)
    is_gb = await db.is_globally_banned(target)

    t = f"📊 <b>Статистика</b>\n\nID: <code>{target}</code>\n"
    if info['username']:
        t += f"Username: @{info['username']}\n"
    t += f"Роль: {ROLE_NAMES.get(role, '?')} ({role})\n"
    if cid:
        warns = await db.get_warns(target, cid)
        is_muted = await db.is_muted(target, cid)
        is_banned = await db.is_banned(target, cid)
        t += f"\nВарны: {warns}/{MAX_WARNS}\nМут: {'да' if is_muted else 'нет'}\nБан: {'да' if is_banned else 'нет'}\n"
    t += f"Глоб. бан: {'да' if is_gb else 'нет'}"
    await message.answer(t, parse_mode="HTML")


# =============================================================================
# /GETBAN /GETWARN
# =============================================================================

@router.message(Command("getban"))
async def cmd_getban(message: Message):
    if not is_mod_context(message):
        return
    role = await get_caller_role(message)
    if role < 3:
        return await message.reply("❌ Недостаточно прав (3+)")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ /getban @user или ID")
    info = await get_user_info(target)
    name = info['full_name']
    text = f"🔍 <b>Информация о банах</b>\n👤 {name} (<code>{target}</code>)\n\n"
    found = False

    # Глобальный бан
    gb = await db.get_global_ban_info(target)
    if gb:
        gb_mod = await mention(gb.get('banned_by', 0))
        text += f"🌐 <b>Глобальный бан</b>\n  Дата: {fmt_ts(gb.get('banned_at', 0))}\n  Окончание: никогда\n  Причина: {gb.get('reason', '—')}\n  👮 Модератор: {gb_mod}\n\n"
        found = True

    # Баны по чатам
    chat_ids = await db.get_all_chat_ids()
    for cid in chat_ids:
        ban = await db.get_ban_info(target, cid)
        if ban:
            chat_title = await db.get_chat_title(cid)
            until = ban.get('until', 0)
            if until and until > 0:
                end = fmt_ts(until) if until > int(time.time()) else "истёк"
            else:
                end = "навсегда"
            text += f"🚫 <b>Бан</b> — {chat_title}\n  Дата: {fmt_ts(ban.get('banned_at', 0))}\n  Окончание: {end}\n  Причина: {ban.get('reason', '—')}\n  👮 Модератор: {await mention(ban.get('banned_by', 0))}\n\n"
            found = True

    if not found:
        text += "✅ Банов нет"
    await message.answer(text, parse_mode="HTML")


@router.message(Command("getwarn"))
async def cmd_getwarn(message: Message):
    if not is_mod_context(message):
        return
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав (1+)")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ /getwarn @user или ID")
    info = await get_user_info(target)
    name = info['full_name']
    text = f"🔍 <b>Информация о варнах</b>\n👤 {name} (<code>{target}</code>)\n\n"
    found = False

    chat_ids = await db.get_all_chat_ids()
    for cid in chat_ids:
        wi = await db.get_warn_info(target, cid)
        if wi and wi['count'] > 0:
            chat_title = await db.get_chat_title(cid)
            mod = await mention(wi.get('warned_by', 0))
            text += f"⚠️ <b>{wi['count']}/{MAX_WARNS}</b> — {chat_title}\n  Причина: {wi.get('reason', '—')}\n  👮 Модератор: {mod}\n\n"
            found = True

    mute_info_list = []
    for cid in chat_ids:
        mi = await db.get_mute_info(target, cid)
        if mi:
            chat_title = await db.get_chat_title(cid)
            until = mi.get('until', 0)
            end = fmt_ts(until) if until and until > int(time.time()) else ("навсегда" if not until else "истёк")
            mute_info_list.append(f"🔇 <b>Мут</b> — {chat_title}\n  Окончание: {end}\n  Причина: {mi.get('reason', '—')}\n  👮 Модератор: {await mention(mi.get('muted_by', 0))}")

    if mute_info_list:
        text += "\n" + "\n".join(mute_info_list) + "\n"
        found = True

    if not found:
        text += "✅ Варнов и мутов нет"
    await message.answer(text, parse_mode="HTML")


# =============================================================================
# КОМАНДЫ МОДЕРАЦИИ
# =============================================================================

@router.message(Command("warn"))
async def cmd_warn(message: Message):
    if not is_mod_context(message):
        return
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав (1+)")
    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ /warn @user [причина] или ответ на сообщение")
    tr = await get_role(target)
    if tr >= role:
        return await message.reply("❌ Роль цели ≥ вашей")
    reason = args[2] if len(args) > 2 else "Нарушение правил"
    caller_id = await get_caller_id(message)

    if is_staff_chat(message):
        key = f"w:{caller_id}:{target}:{reason}"
        await db.cache_action(key, json.dumps({"t": target, "c": caller_id, "r": reason, "a": "warn"}))
        kb = await build_chat_selector(key)
        name = await mention(target)
        await message.reply(f"⚠️ Варн для {name}\nПричина: {reason}\n\nВыберите чат:", parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await apply_warn(target, [message.chat.id], caller_id, reason)
        await message.reply("✅ Варн выдан")


@router.message(Command("unwarn"))
async def cmd_unwarn(message: Message):
    if not is_mod_context(message):
        return
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ /unwarn @user или ответ")
    caller_id = await get_caller_id(message)

    if is_staff_chat(message):
        key = f"uw:{caller_id}:{target}"
        await db.cache_action(key, json.dumps({"t": target, "c": caller_id, "a": "unwarn"}))
        kb = await build_chat_selector(key)
        name = await mention(target)
        await message.reply(f"✅ Снять варн: {name}\n\nВыберите чат:", parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await apply_unwarn(target, [message.chat.id], caller_id)
        name = await mention(target, message.chat.id)
        await message.reply(f"✅ Варн снят! {name}", parse_mode="HTML")


@router.message(Command("mute"))
async def cmd_mute(message: Message):
    if not is_mod_context(message):
        return
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав")
    args = get_args(message, maxsplit=3)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ /mute @user 30m [причина]\nВремя: 5m, 1h, 6h, 1d, 7d, 30d, 0=навсегда")
    tr = await get_role(target)
    if tr >= role:
        return await message.reply("❌ Роль цели ≥ вашей")

    dur_arg = args[2] if len(args) > 2 else "1h"
    seconds = parse_duration(dur_arg)
    if seconds is None:
        seconds = 3600
        reason = " ".join(args[2:]) if len(args) > 2 else "Мут"
    else:
        reason = args[3] if len(args) > 3 else "Мут"

    limit = MUTE_LIMITS.get(role, 0)
    if limit > 0 and (seconds == 0 or seconds > limit):
        return await message.reply(f"❌ Ваш лимит мута: {fmt_dur(limit)}")

    caller_id = await get_caller_id(message)

    if is_staff_chat(message):
        key = f"m:{caller_id}:{target}:{seconds}"
        await db.cache_action(key, json.dumps({"t": target, "c": caller_id, "r": reason, "s": seconds, "a": "mute"}))
        kb = await build_chat_selector(key)
        name = await mention(target)
        await message.reply(
            f"🔇 Мут для {name} на {fmt_dur(seconds)}\nПричина: {reason}\n\nВыберите чат:",
            parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await apply_mute(target, [message.chat.id], caller_id, reason, seconds)
        await message.reply("✅ Мут применён")


@router.message(Command("unmute"))
async def cmd_unmute(message: Message):
    if not is_mod_context(message):
        return
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ /unmute @user")
    caller_id = await get_caller_id(message)

    if is_staff_chat(message):
        key = f"um:{caller_id}:{target}"
        await db.cache_action(key, json.dumps({"t": target, "c": caller_id, "a": "unmute"}))
        kb = await build_chat_selector(key)
        name = await mention(target)
        await message.reply(f"🔊 Размут: {name}\n\nВыберите чат:", parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await apply_unmute(target, [message.chat.id], caller_id)
        name = await mention(target, message.chat.id)
        await message.reply(f"🔊 {name} размучен!", parse_mode="HTML")


@router.message(Command("ban"))
async def cmd_ban(message: Message):
    if not is_mod_context(message):
        return
    role = await get_caller_role(message)
    if role < 3:
        return await message.reply("❌ Недостаточно прав (3+)")
    args = get_args(message, maxsplit=3)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ /ban @user 7d [причина]\nВремя: 5m, 1h, 7d, 30d, 0=навсегда")
    tr = await get_role(target)
    if tr >= role:
        return await message.reply("❌ Роль цели ≥ вашей")

    dur_arg = args[2] if len(args) > 2 else "0"
    seconds = parse_duration(dur_arg)
    if seconds is None:
        seconds = 0
        reason = " ".join(args[2:]) if len(args) > 2 else "Бан"
    else:
        reason = args[3] if len(args) > 3 else "Бан"

    caller_id = await get_caller_id(message)

    if is_staff_chat(message):
        key = f"b:{caller_id}:{target}:{seconds}"
        await db.cache_action(key, json.dumps({"t": target, "c": caller_id, "r": reason, "s": seconds, "a": "ban"}))
        kb = await build_chat_selector(key)
        name = await mention(target)
        await message.reply(
            f"🚫 Бан для {name} на {fmt_dur(seconds)}\nПричина: {reason}\n\nВыберите чат:",
            parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await apply_ban(target, [message.chat.id], caller_id, reason, seconds)
        await message.reply("✅ Бан применён")


@router.message(Command("unban"))
async def cmd_unban(message: Message):
    if not is_mod_context(message):
        return
    role = await get_caller_role(message)
    if role < 3:
        return await message.reply("❌ Недостаточно прав (3+)")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ /unban @user или ID")
    caller_id = await get_caller_id(message)

    if is_staff_chat(message):
        key = f"ub:{caller_id}:{target}"
        await db.cache_action(key, json.dumps({"t": target, "c": caller_id, "a": "unban"}))
        kb = await build_chat_selector(key)
        name = await mention(target)
        await message.reply(f"✅ Разбан: {name}\n\nВыберите чат:", parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await apply_unban(target, [message.chat.id], caller_id)
        name = await mention(target, message.chat.id)
        await message.reply(f"✅ {name} разбанен!", parse_mode="HTML")


@router.message(Command("kick"))
async def cmd_kick(message: Message):
    if not is_mod_context(message):
        return
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав")
    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ /kick @user [причина]")
    tr = await get_role(target)
    if tr >= role:
        return await message.reply("❌ Роль цели ≥ вашей")
    reason = args[2] if len(args) > 2 else "Кик"
    caller_id = await get_caller_id(message)

    if is_staff_chat(message):
        key = f"k:{caller_id}:{target}"
        await db.cache_action(key, json.dumps({"t": target, "c": caller_id, "r": reason, "a": "kick"}))
        kb = await build_chat_selector(key)
        name = await mention(target)
        await message.reply(f"👢 Кик: {name}\nПричина: {reason}\n\nВыберите чат:", parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await apply_kick(target, [message.chat.id], caller_id, reason)
        await message.reply("✅ Кикнут")


# --- /gban /ungban ---

@router.message(Command("gban"))
async def cmd_gban(message: Message):
    if not is_mod_context(message):
        return
    role = await get_caller_role(message)
    if role < 7:
        return await message.reply("❌ Недостаточно прав (7+)")
    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ /gban @user [причина] или ID")
    tr = await get_role(target)
    if tr >= role:
        return await message.reply(f"❌ Роль цели: {ROLE_NAMES.get(tr)} ({tr})")
    if tr > 0:
        return await message.reply("⚠️ Сначала снимите роль: /removerole")
    reason = args[2] if len(args) > 2 else "Глобальный бан"
    caller_id = await get_caller_id(message)

    await db.add_global_ban(target, caller_id, reason)
    chat_ids = await db.get_all_chat_ids()
    ok, fail = 0, 0
    for cid in chat_ids:
        try:
            await bot.ban_chat_member(cid, target)
            await db.add_ban(target, cid, caller_id, "Глобальный бан")
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.1)

    name = await mention(target)
    result = f"🌐 Глобальный бан!\n{name} — <code>{target}</code>\nПричина: {reason}\n✅ В {ok} чатах"
    if fail:
        result += f" | ⚠️ {fail} неудач"
    await message.reply(result, parse_mode="HTML")

    if STAFF_CHAT_ID and GBAN_TOPIC_ID:
        try:
            ci = await get_user_info(caller_id)
            c_tag = f" (@{ci['username']})" if ci['username'] else ""
            await bot.send_message(STAFF_CHAT_ID,
                f"🌐 <b>ГЛОБАЛЬНЫЙ БАН</b>\n━━━━━━━━━━━━━━━━\n"
                f"👤 {name}\n🆔 <code>{target}</code>\n"
                f"📅 Окончание: никогда\n"
                f"📝 {reason}\n👮 {ci['full_name']}{c_tag}\n"
                f"✅ В {ok} чатах\n🕐 {now_str()}",
                parse_mode="HTML", message_thread_id=GBAN_TOPIC_ID)
        except Exception as e:
            logger.error(f"gban log: {e}")

    await log_action("ГЛОБАЛЬНЫЙ БАН", target, caller_id, reason, 0)
    await notify_user_dm(target, "Вы получили глобальную блокировку", reason, 0, caller_id)


@router.message(Command("ungban"))
async def cmd_ungban(message: Message):
    if not is_mod_context(message):
        return
    role = await get_caller_role(message)
    if role < 7:
        return await message.reply("❌ Недостаточно прав (7+)")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ /ungban @user или ID")
    if not await db.is_globally_banned(target):
        return await message.reply("ℹ️ Нет глобального бана")
    await db.remove_global_ban(target)
    chat_ids = await db.get_all_chat_ids()
    ok = 0
    for cid in chat_ids:
        try:
            await bot.unban_chat_member(cid, target, only_if_banned=True)
            await db.remove_ban(target, cid)
            ok += 1
        except Exception:
            pass
        await asyncio.sleep(0.1)
    name = await mention(target)
    await message.reply(f"✅ Глобальный бан снят! {name}\nРазбанен в {ok} чатах", parse_mode="HTML")
    await log_action("СНЯТИЕ ГЛОБ. БАНА", target, await get_caller_id(message))
    if STAFF_CHAT_ID and GBAN_TOPIC_ID:
        try:
            await bot.send_message(STAFF_CHAT_ID,
                f"✅ <b>СНЯТИЕ ГЛОБ. БАНА</b>\n{name} — <code>{target}</code>\n🕐 {now_str()}",
                parse_mode="HTML", message_thread_id=GBAN_TOPIC_ID)
        except Exception:
            pass


# --- /ro /unro ---

@router.message(Command("ro"))
async def cmd_ro(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        return
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав")
    await db.set_ro_mode(message.chat.id, True)
    await message.answer("👁 Режим RO включён!")

@router.message(Command("unro"))
async def cmd_unro(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        return
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав")
    await db.set_ro_mode(message.chat.id, False)
    await message.answer("✍️ Режим RO выключен!")


# --- /setrole /removerole ---

@router.message(Command("setrole"))
async def cmd_setrole(message: Message):
    cr = await get_caller_role(message)
    if cr < 7:
        return await message.reply("❌ Недостаточно прав (7+)")
    args = get_args(message)
    if len(args) < 3:
        roles_text = "\n".join([f"  {k}: {v}" for k, v in ROLE_NAMES.items()])
        return await message.reply(f"/setrole @user ЧИСЛО\n\nРоли:\n{roles_text}")
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ Пользователь не найден")
    try:
        nr = int(args[2])
        if not (0 <= nr <= 10):
            return await message.reply("❌ Роль: 0-10")
    except ValueError:
        return await message.reply("❌ Число 0-10")
    tr = await get_role(target)
    if nr >= cr:
        return await message.reply(f"❌ Нельзя назначить ≥ вашей ({cr})")
    if tr >= cr:
        return await message.reply("❌ Нельзя менять роль этого пользователя")
    await db.set_global_role(target, nr)
    name = await mention(target)
    await message.reply(f"⭐ {name}: {ROLE_NAMES.get(tr,'?')} ({tr}) → {ROLE_NAMES.get(nr,'?')} ({nr})", parse_mode="HTML")
    await log_action("СМЕНА РОЛИ", target, await get_caller_id(message), f"{tr} → {nr}")

@router.message(Command("removerole"))
async def cmd_removerole(message: Message):
    cr = await get_caller_role(message)
    if cr < 7:
        return await message.reply("❌ Недостаточно прав (7+)")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        return await message.reply("❌ /removerole @user")
    tr = await get_role(target)
    if tr >= cr:
        return await message.reply("❌ Нельзя")
    if tr == 0:
        return await message.reply("ℹ️ Нет роли")
    await db.set_global_role(target, 0)
    name = await mention(target)
    await message.reply(f"✅ Роль снята! {name} (была: {ROLE_NAMES.get(tr,'?')})", parse_mode="HTML")
    await log_action("СНЯТИЕ РОЛИ", target, await get_caller_id(message), f"Была: {tr}")


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
    if message.chat.type == ChatType.PRIVATE:
        return
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав")
    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)
    if not target or len(args) < 3:
        return await message.reply("❌ /setnick @user НикВЧате")
    await db.set_nick(target, message.chat.id, args[2])
    await message.reply(f"📝 Ник: {args[2]}")

# --- /clear ---

@router.message(Command("clear"))
async def cmd_clear(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        return
    role = await get_caller_role(message)
    if role < 4:
        return await message.reply("❌ Недостаточно прав (4+ — куратор модерации)")
    args = get_args(message)
    if len(args) < 2:
        return await message.reply("❌ /clear <число 1-100>")
    try:
        count = int(args[1])
        if not (1 <= count <= 100):
            return await message.reply("❌ 1-100")
    except ValueError:
        return await message.reply("❌ 1-100")
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
    await log_action("ОЧИСТКА", 0, await get_caller_id(message), f"{deleted} сообщений", chat_id=message.chat.id)


# --- /banlist /warnlist ---

@router.message(Command("banlist"))
async def cmd_banlist(message: Message):
    if not is_mod_context(message):
        return
    role = await get_caller_role(message)
    if role < 3:
        return await message.reply("❌ Недостаточно прав (3+)")
    args = get_args(message)
    mode = "chat"
    page = 0
    for a in args[1:]:
        if a == "global":
            mode = "global"
        elif a.isdigit():
            page = max(0, int(a) - 1)
    chat_id = message.chat.id if mode == "chat" and not is_staff_chat(message) else 0
    if mode == "global":
        rows, total = await db.get_all_global_bans_paginated(page, PER_PAGE)
        title = "🌐 <b>Глобальные баны</b>"
    else:
        rows, total = await db.get_all_bans_paginated(page, PER_PAGE, chat_id)
        title = "💬 <b>Баны</b>" + (" (все чаты)" if not chat_id else "")
    total_pages = max(1, math.ceil(total / PER_PAGE))
    if not rows:
        return await message.answer(f"{title}\n\nСписок пуст.\n/banlist global — глобальные", parse_mode="HTML")
    text = f"{title} — стр. {page + 1}/{total_pages}\n\n"
    for i, row in enumerate(rows, start=page * PER_PAGE + 1):
        uid = row['user_id']
        info = await get_user_info(uid)
        reason = row.get('reason', '—') or '—'
        until = row.get('until', 0)
        if until and until > 0:
            end = fmt_ts(until) if until > int(time.time()) else "истёк"
        elif mode != "global":
            end = "навсегда"
        else:
            end = "навсегда"
        text += f"<b>{i}.</b> {info['full_name']} — <code>{uid}</code>\n    Причина: {reason}\n    Дата: {fmt_ts(row.get('banned_at', 0))}\n    Окончание: {end}\n\n"
    text += f"📄 Всего: {total}"
    if total_pages > 1:
        text += f"\n/banlist {'global ' if mode == 'global' else ''}{page + 2} — след."
    await message.answer(text, parse_mode="HTML")


@router.message(Command("warnlist"))
async def cmd_warnlist(message: Message):
    if not is_mod_context(message):
        return
    role = await get_caller_role(message)
    if role < 1:
        return await message.reply("❌ Недостаточно прав (1+)")
    args = get_args(message)
    page = 0
    for a in args[1:]:
        if a.isdigit():
            page = max(0, int(a) - 1)
    chat_id = message.chat.id if not is_staff_chat(message) else 0
    rows, total = await db.get_all_warns_paginated(page, PER_PAGE, chat_id)
    total_pages = max(1, math.ceil(total / PER_PAGE))
    if not rows:
        return await message.answer("⚠️ <b>Предупреждения</b>\n\nСписок пуст.", parse_mode="HTML")
    text = f"⚠️ <b>Предупреждения</b> — стр. {page + 1}/{total_pages}\n\n"
    for i, row in enumerate(rows, start=page * PER_PAGE + 1):
        uid = row['user_id']
        info = await get_user_info(uid)
        text += f"<b>{i}.</b> {info['full_name']} — <code>{uid}</code>\n    Варнов: {row['count']}/{MAX_WARNS}\n    Причина: {row.get('reason', '—') or '—'}\n\n"
    text += f"📄 Всего: {total}"
    if total_pages > 1:
        text += f"\n/warnlist {page + 2} — след."
    await message.answer(text, parse_mode="HTML")


# =============================================================================
# CALLBACK: ВЫБОР ЧАТА
# =============================================================================

@router.callback_query(F.data.startswith("chatsel:"))
async def cb_chat_select(call: CallbackQuery):
    # chatsel:ACTION_KEY:CHAT_ID_OR_ALL
    parts = call.data.split(":", 2)
    if len(parts) < 3:
        return await call.answer("❌ Ошибка")
    action_key = parts[1]
    chat_part = parts[2]

    cached = await db.get_cached_action(action_key)
    if not cached:
        try:
            await call.message.edit_text("⏳ Действие устарело. Повторите команду.")
        except Exception:
            pass
        return await call.answer()

    data = json.loads(cached)
    target = data["t"]
    caller_id = data["c"]
    action = data["a"]
    reason = data.get("r", "")
    seconds = data.get("s", 0)

    # Проверим что нажал тот же модератор
    if call.from_user.id != caller_id and caller_id != 0:
        return await call.answer("❌ Не ваше действие!", show_alert=True)

    if chat_part == "all":
        chat_ids = [cid for cid in await db.get_all_chat_ids() if cid != STAFF_CHAT_ID]
    else:
        chat_ids = [int(chat_part)]

    chat_names = []
    for cid in chat_ids:
        chat_names.append(await db.get_chat_title(cid))

    name = await mention(target)
    result = ""

    if action == "warn":
        await apply_warn(target, chat_ids, caller_id, reason)
        result = f"✅ Варн выдан: {name}"
    elif action == "unwarn":
        await apply_unwarn(target, chat_ids, caller_id)
        result = f"✅ Варн снят: {name}"
    elif action == "mute":
        await apply_mute(target, chat_ids, caller_id, reason, seconds)
        result = f"✅ Мут: {name} на {fmt_dur(seconds)}"
    elif action == "unmute":
        await apply_unmute(target, chat_ids, caller_id)
        result = f"✅ Размут: {name}"
    elif action == "ban":
        await apply_ban(target, chat_ids, caller_id, reason, seconds)
        result = f"✅ Бан: {name} на {fmt_dur(seconds)}"
    elif action == "unban":
        await apply_unban(target, chat_ids, caller_id)
        result = f"✅ Разбан: {name}"
    elif action == "kick":
        await apply_kick(target, chat_ids, caller_id, reason)
        result = f"✅ Кик: {name}"

    chats_str = ", ".join(chat_names) if chat_part != "all" else "все чаты"
    result += f"\n💬 {chats_str}"

    await db.clear_cached_action(action_key)

    try:
        await call.message.edit_text(result, parse_mode="HTML")
    except Exception:
        pass
    await call.answer()


@router.callback_query(F.data.startswith("cancel:"))
async def cb_cancel(call: CallbackQuery):
    try:
        await call.message.edit_text("❌ Отменено")
    except Exception:
        pass
    await call.answer()


# =============================================================================
# СОБЫТИЯ
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
            logger.error(f"gban join {uid}: {e}")
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

    if await db.is_globally_banned(uid):
        try:
            await bot.ban_chat_member(cid, uid)
            await message.delete()
            name = await mention(uid)
            await bot.send_message(cid, f"🚫 {name} — глобальный бан!", parse_mode="HTML")
        except Exception:
            pass
        return

    if role < 1 and await db.is_ro_mode(cid):
        try:
            await message.delete()
        except Exception:
            pass
        return

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
                await bot.send_message(cid, f"🔇 {name} замучен на 30 мин (антиспам)", parse_mode="HTML")
                await notify_user_dm(uid, "Вы замучены (антиспам)", "Флуд", 1800, 0)
            except Exception:
                pass
            return

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
                    await bot.send_message(cid, f"🔇 {name} замучен (запрещённое слово)", parse_mode="HTML")
                    await notify_user_dm(uid, "Вы замучены", "Запрещённое слово", 1800, 0)
                except Exception:
                    pass
                return


# =============================================================================
# ЗАПУСК
# =============================================================================

async def register_commands():
    group_cmds = [
        BotCommand(command="help", description="❓ Помощь"),
        BotCommand(command="stats", description="📊 Статистика"),
        BotCommand(command="warn", description="⚠️ Варн"),
        BotCommand(command="unwarn", description="✅ Снять варн"),
        BotCommand(command="mute", description="🔇 Мут"),
        BotCommand(command="unmute", description="🔊 Размут"),
        BotCommand(command="ban", description="🚫 Бан"),
        BotCommand(command="unban", description="✅ Разбан"),
        BotCommand(command="kick", description="👢 Кик"),
        BotCommand(command="getban", description="🔍 Инфо о бане"),
        BotCommand(command="getwarn", description="🔍 Инфо о варнах"),
        BotCommand(command="banlist", description="📋 Банлист"),
        BotCommand(command="warnlist", description="📋 Варнлист"),
        BotCommand(command="clear", description="🧹 Очистить"),
        BotCommand(command="ro", description="👁 RO"),
        BotCommand(command="unro", description="✍️ Снять RO"),
        BotCommand(command="setnick", description="📝 Ник"),
        BotCommand(command="gban", description="🌐 Глобальный бан"),
        BotCommand(command="ungban", description="🌐 Снять глоб."),
        BotCommand(command="setrole", description="⭐ Роль"),
        BotCommand(command="removerole", description="❌ Снять роль"),
        BotCommand(command="staff", description="👥 Команда"),
    ]
    priv_cmds = [
        BotCommand(command="start", description="🏠 Мои наказания"),
        BotCommand(command="help", description="❓ Помощь"),
        BotCommand(command="stats", description="📊 Статистика"),
    ]
    try:
        await bot.set_my_commands(group_cmds, scope=BotCommandScopeAllGroupChats())
        await bot.set_my_commands(priv_cmds, scope=BotCommandScopeAllPrivateChats())
    except Exception as e:
        logger.error(f"register_commands: {e}")


async def init_staff():
    if not PRESET_STAFF:
        return
    for uid_str, role in PRESET_STAFF.items():
        try:
            await db.set_global_role(int(uid_str), role)
        except Exception as e:
            logger.error(f"Preset staff {uid_str}: {e}")
    logger.info(f"✅ Preset staff: {len(PRESET_STAFF)}")


async def periodic_cleanup():
    while True:
        await asyncio.sleep(3600)
        try:
            await db.cleanup_old_cache(3600)
        except Exception:
            pass


async def main():
    global db, BOT_ID
    db = Database("database.db")
    await db.init()

    me = await bot.get_me()
    BOT_ID = me.id
    logger.info(f"🔵 Модерация v7.2 — @{me.username} (ID: {BOT_ID})")

    await init_staff()

    for cid in MODERATED_CHATS:
        try:
            chat = await bot.get_chat(cid)
            await db.register_chat(cid, chat.title or "")
            logger.info(f"Чат: {cid} ({chat.title})")
        except Exception as e:
            logger.warning(f"Чат {cid}: {e}")

    if STAFF_CHAT_ID:
        try:
            chat = await bot.get_chat(STAFF_CHAT_ID)
            await db.register_chat(STAFF_CHAT_ID, chat.title or "STAFF")
            logger.info(f"Стафф-чат: {STAFF_CHAT_ID} ({chat.title})")
        except Exception as e:
            logger.warning(f"Стафф-чат: {e}")

    await register_commands()
    asyncio.create_task(periodic_cleanup())
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("✅ Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
