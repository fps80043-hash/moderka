"""
\U0001f535 Модерация — v8.0

НОВОЕ v8.0:
1. /sremoverole — снять роль во всех чатах сетки
2. /allsetnick /allremnick — массовые ники
3. /removenick /getnick /getacc /nlist — работа с никами
4. /reg — дата регистрации
5. /online /onlinelist — онлайн участники
6. /quiet — режим тишины
7. /pullinfo — инфо о сетке
8. /banwords /filter /antiflood /welcometext — владелец
9. Тихие наказания: --silent (без сообщения в чате, только лог+ЛС)
10. Публичный лог наказаний в топик punish_topic_id
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
PUNISH_TOPIC_ID: int = config.get("punish_topic_id", 0)
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
BOT_ID: int = 0

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
    return message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

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

def extract_silent_flag(args: list) -> tuple:
    silent = False
    new_args = []
    for a in args:
        if a in ("--silent", "-s", "--тихо", "тихо"):
            silent = True
        else:
            new_args.append(a)
    return new_args, silent

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


async def build_chat_selector(action_key: str) -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    chat_ids = await db.get_all_chat_ids()
    for cid in chat_ids:
        if cid == STAFF_CHAT_ID:
            continue
        title = await db.get_chat_title(cid)
        short = title[:25] + "…" if len(title) > 25 else title
        b.button(text=f"💬 {short}", callback_data=f"chatsel:{action_key}:{cid}")
    b.button(text="🌐 Все чаты", callback_data=f"chatsel:{action_key}:all")
    b.button(text="❌ Отмена", callback_data="cancel:x")
    b.adjust(1)
    return b


async def log_action(action, target, caller, reason="", duration=-1, chat_id=0):
    if not STAFF_CHAT_ID or not LOG_TOPIC_ID:
        return
    try:
        ti = await get_user_info(target)
        ci = await get_user_info(caller)
        ct = await db.get_chat_title(chat_id) if chat_id else "все чаты"
        tu = f" (@{ti['username']})" if ti['username'] else ""
        cu = f" (@{ci['username']})" if ci['username'] else ""
        text = f"📋 <b>{action}</b>\n━━━━━━━━━━━━━━━━\n👤 Кому: {ti['full_name']}{tu}\n🆔 ID: <code>{target}</code>\n"
        if duration >= 0:
            text += f"⏱ Срок: {fmt_dur(duration)}\n📅 Окончание: {end_date_str(duration)}\n"
        if reason:
            text += f"📝 Причина: {reason}\n"
        text += f"👮 Модератор: {ci['full_name']}{cu}\n💬 Чат: {ct}\n🕐 {now_str()}"
        await bot.send_message(STAFF_CHAT_ID, text, parse_mode="HTML", message_thread_id=LOG_TOPIC_ID)
    except Exception as e:
        logger.error(f"log_action: {e}")


async def log_punish_public(action, target, caller, reason="", duration=-1, chat_id=0):
    if not STAFF_CHAT_ID or not PUNISH_TOPIC_ID:
        return
    try:
        ti = await get_user_info(target)
        ct = await db.get_chat_title(chat_id) if chat_id else "все чаты"
        tu = f" (@{ti['username']})" if ti['username'] else ""
        text = f"📋 <b>{action}</b>\n👤 {ti['full_name']}{tu} (<code>{target}</code>)\n"
        if duration >= 0:
            text += f"⏱ {fmt_dur(duration)}\n"
        if reason:
            text += f"📝 {reason}\n"
        text += f"💬 {ct} | 🕐 {now_str()}"
        await bot.send_message(STAFF_CHAT_ID, text, parse_mode="HTML", message_thread_id=PUNISH_TOPIC_ID)
    except Exception as e:
        logger.error(f"log_punish_public: {e}")


async def notify_user_dm(user_id, action_name, reason, duration, caller_id):
    try:
        ci = await get_user_info(caller_id)
        text = f"⚠️ <b>{action_name}</b>\n\n📅 Дата: {now_str()}\n📅 Окончание: {end_date_str(duration)}\n📝 Причина: {reason}\n👮 Модератор: {ci['full_name']}\n"
        if SUPPORT_LINK:
            text += f"\n{SUPPORT_LINK}"
        await bot.send_message(user_id, text, parse_mode="HTML")
    except Exception:
        pass


# =============================================================================
# ПРИМЕНЕНИЕ ДЕЙСТВИЙ (silent → без msg в чат)
# =============================================================================

async def apply_warn(target, chat_ids, caller_id, reason, silent=False):
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
            if not silent:
                try:
                    await bot.send_message(cid, f"⚠️ {name} — предупреждение ({MAX_WARNS}/{MAX_WARNS})\nПричина: {reason}\n\n👢 Кикнут за {MAX_WARNS} варнов!", parse_mode="HTML")
                except Exception:
                    pass
        else:
            if not silent:
                try:
                    await bot.send_message(cid, f"⚠️ {name} — предупреждение ({warns}/{MAX_WARNS})\nПричина: {reason}", parse_mode="HTML")
                except Exception:
                    pass
        await log_action("ВАРН", target, caller_id, reason, chat_id=cid)
        if not silent:
            await log_punish_public("ВАРН", target, caller_id, reason, chat_id=cid)
    await notify_user_dm(target, "Вам выдано предупреждение", reason, -1, caller_id)

async def apply_mute(target, chat_ids, caller_id, reason, seconds, silent=False):
    for cid in chat_ids:
        try:
            until = int(time.time()) + seconds if seconds > 0 else 0
            delta = timedelta(seconds=seconds) if seconds > 0 else None
            await bot.restrict_chat_member(cid, target, permissions=muted_permissions(), until_date=delta)
            await db.add_mute(target, cid, caller_id, reason, until)
            if not silent:
                name = await mention(target, cid)
                await bot.send_message(cid, f"🔇 {name} замучен на {fmt_dur(seconds)}\nПричина: {reason}", parse_mode="HTML")
        except Exception as e:
            logger.error(f"mute {target} in {cid}: {e}")
        await log_action("МУТ", target, caller_id, reason, seconds, cid)
        if not silent:
            await log_punish_public("МУТ", target, caller_id, reason, seconds, cid)
    await notify_user_dm(target, "Вы замучены", reason, seconds, caller_id)

async def apply_ban(target, chat_ids, caller_id, reason, seconds, silent=False):
    for cid in chat_ids:
        try:
            delta = timedelta(seconds=seconds) if seconds > 0 else None
            until = int(time.time()) + seconds if seconds > 0 else 0
            await bot.ban_chat_member(cid, target, until_date=delta)
            await db.add_ban(target, cid, caller_id, reason, until)
            if not silent:
                name = await mention(target, cid)
                await bot.send_message(cid, f"🚫 {name} забанен на {fmt_dur(seconds)}\nПричина: {reason}", parse_mode="HTML")
        except Exception as e:
            logger.error(f"ban {target} in {cid}: {e}")
        await log_action("БАН", target, caller_id, reason, seconds, cid)
        if not silent:
            await log_punish_public("БАН", target, caller_id, reason, seconds, cid)
    await notify_user_dm(target, "Вы заблокированы", reason, seconds, caller_id)

async def apply_kick(target, chat_ids, caller_id, reason, silent=False):
    for cid in chat_ids:
        try:
            await bot.ban_chat_member(cid, target)
            await asyncio.sleep(0.5)
            await bot.unban_chat_member(cid, target)
            if not silent:
                name = await mention(target, cid)
                await bot.send_message(cid, f"👢 {name} кикнут\nПричина: {reason}", parse_mode="HTML")
        except Exception:
            pass
        await log_action("КИК", target, caller_id, reason, chat_id=cid)
        if not silent:
            await log_punish_public("КИК", target, caller_id, reason, chat_id=cid)
    await notify_user_dm(target, "Вы кикнуты из группы", reason, -1, caller_id)

async def apply_unmute(target, chat_ids, caller_id):
    for cid in chat_ids:
        try:
            await bot.restrict_chat_member(cid, target, permissions=full_permissions())
            await db.remove_mute(target, cid)
        except Exception:
            pass
    await log_action("РАЗМУТ", target, caller_id)

async def apply_unban(target, chat_ids, caller_id):
    for cid in chat_ids:
        try:
            await bot.unban_chat_member(cid, target, only_if_banned=True)
            await db.remove_ban(target, cid)
        except Exception:
            pass
    await log_action("РАЗБАН", target, caller_id)

async def apply_unwarn(target, chat_ids, caller_id):
    for cid in chat_ids:
        await db.remove_warn(target, cid)
    await log_action("СНЯТИЕ ВАРНА", target, caller_id)


# =============================================================================
# КОМАНДЫ — /start /help /stats
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

@router.message(Command("help"))
async def cmd_help(message: Message):
    role = await get_caller_role(message)
    text = f"📖 <b>Команды v8.0</b>\nРоль: <b>{ROLE_NAMES.get(role, '?')} ({role})</b>\n\n"
    if role >= 1:
        text += "<b>⚠️ Опасные (1+):</b>\n/warn /mute /kick [--silent]\n\n<b>📋 (1+):</b>\n/unwarn /unmute /getwarn /warnlist\n/ro /unro /setnick /removenick\n/getnick /allsetnick /allremnick\n/nlist /getacc /reg /online /onlinelist\n\n"
    if role >= 3:
        text += "<b>⚠️ Опасные (3+):</b>\n/ban [--silent]\n\n<b>📋 (3+):</b>\n/unban /getban /banlist\n\n"
    if role >= 4:
        text += "<b>⚠️ (4+):</b> /clear\n\n"
    if role >= 7:
        text += "<b>⚠️ (7+):</b>\n/gban /ungban /setrole /removerole /sremoverole\n/banwords /filter /antiflood /welcometext\n\n"
    text += "<b>📋 Общие:</b>\n/stats /staff /pullinfo\n\n💡 <code>--silent</code> — тихое наказание"
    await message.answer(text, parse_mode="HTML")

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        if not message.from_user:
            return
        uid = message.from_user.id
        role = await get_role(uid)
        is_gb = await db.is_globally_banned(uid)
        return await message.answer(f"👤 <b>Ваша информация</b>\n\nID: <code>{uid}</code>\nРоль: {ROLE_NAMES.get(role, '?')} ({role})\nГлоб. бан: {'да' if is_gb else 'нет'}", parse_mode="HTML")
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

@router.message(Command("getban"))
async def cmd_getban(message: Message):
    if not is_mod_context(message): return
    role = await get_caller_role(message)
    if role < 3: return await message.reply("❌ 3+")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /getban @user")
    info = await get_user_info(target)
    text = f"🔍 <b>Баны</b>\n👤 {info['full_name']} (<code>{target}</code>)\n\n"
    found = False
    gb = await db.get_global_ban_info(target)
    if gb:
        text += f"🌐 <b>Глобальный бан</b>\n  Дата: {fmt_ts(gb.get('banned_at',0))}\n  Причина: {gb.get('reason','—')}\n  👮 {await mention(gb.get('banned_by',0))}\n\n"
        found = True
    for cid in await db.get_all_chat_ids():
        ban = await db.get_ban_info(target, cid)
        if ban:
            ct = await db.get_chat_title(cid)
            until = ban.get('until',0)
            end = fmt_ts(until) if until and until > int(time.time()) else ("истёк" if until else "навсегда")
            text += f"🚫 <b>Бан</b> — {ct}\n  Окончание: {end}\n  Причина: {ban.get('reason','—')}\n  👮 {await mention(ban.get('banned_by',0))}\n\n"
            found = True
    if not found: text += "✅ Банов нет"
    await message.answer(text, parse_mode="HTML")

@router.message(Command("getwarn"))
async def cmd_getwarn(message: Message):
    if not is_mod_context(message): return
    role = await get_caller_role(message)
    if role < 1: return await message.reply("❌ 1+")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /getwarn @user")
    info = await get_user_info(target)
    text = f"🔍 <b>Варны/муты</b>\n👤 {info['full_name']} (<code>{target}</code>)\n\n"
    found = False
    for cid in await db.get_all_chat_ids():
        wi = await db.get_warn_info(target, cid)
        if wi and wi['count'] > 0:
            ct = await db.get_chat_title(cid)
            text += f"⚠️ <b>{wi['count']}/{MAX_WARNS}</b> — {ct}\n  Причина: {wi.get('reason','—')}\n  👮 {await mention(wi.get('warned_by',0))}\n\n"
            found = True
    for cid in await db.get_all_chat_ids():
        mi = await db.get_mute_info(target, cid)
        if mi:
            ct = await db.get_chat_title(cid)
            until = mi.get('until',0)
            end = fmt_ts(until) if until and until > int(time.time()) else ("навсегда" if not until else "истёк")
            text += f"🔇 <b>Мут</b> — {ct}\n  Окончание: {end}\n  Причина: {mi.get('reason','—')}\n\n"
            found = True
    if not found: text += "✅ Варнов и мутов нет"
    await message.answer(text, parse_mode="HTML")


# =============================================================================
# МОДЕРАЦИЯ: warn/mute/ban/kick/unban/unmute/unwarn с --silent
# =============================================================================

@router.message(Command("warn"))
async def cmd_warn(message: Message):
    if not is_mod_context(message): return
    role = await get_caller_role(message)
    if role < 1: return await message.reply("❌ 1+")
    args = get_args(message, maxsplit=2)
    args, silent = extract_silent_flag(args)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /warn @user [причина] [--silent]")
    tr = await get_role(target)
    if tr >= role: return await message.reply("❌ Роль цели ≥ вашей")
    reason = args[2] if len(args) > 2 else "Нарушение правил"
    caller_id = await get_caller_id(message)
    if is_staff_chat(message):
        key = f"w:{caller_id}:{target}:{int(silent)}"
        await db.cache_action(key, json.dumps({"t":target,"c":caller_id,"r":reason,"a":"warn","silent":silent}))
        kb = await build_chat_selector(key)
        name = await mention(target)
        sl = " 🔕" if silent else ""
        await message.reply(f"⚠️ Варн для {name}{sl}\nПричина: {reason}\n\nВыберите чат:", parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await apply_warn(target, [message.chat.id], caller_id, reason, silent)
        sl = " (тихо 🔕)" if silent else ""
        await message.reply(f"✅ Варн выдан{sl}")

@router.message(Command("unwarn"))
async def cmd_unwarn(message: Message):
    if not is_mod_context(message): return
    role = await get_caller_role(message)
    if role < 1: return await message.reply("❌ Недостаточно прав")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /unwarn @user")
    caller_id = await get_caller_id(message)
    if is_staff_chat(message):
        key = f"uw:{caller_id}:{target}"
        await db.cache_action(key, json.dumps({"t":target,"c":caller_id,"a":"unwarn"}))
        kb = await build_chat_selector(key)
        await message.reply(f"✅ Снять варн: {await mention(target)}\n\nВыберите чат:", parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await apply_unwarn(target, [message.chat.id], caller_id)
        await message.reply(f"✅ Варн снят! {await mention(target, message.chat.id)}", parse_mode="HTML")

@router.message(Command("mute"))
async def cmd_mute(message: Message):
    if not is_mod_context(message): return
    role = await get_caller_role(message)
    if role < 1: return await message.reply("❌ Недостаточно прав")
    args = get_args(message, maxsplit=3)
    args, silent = extract_silent_flag(args)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /mute @user 30m [причина] [--silent]")
    tr = await get_role(target)
    if tr >= role: return await message.reply("❌ Роль цели ≥ вашей")
    dur_arg = args[2] if len(args) > 2 else "1h"
    seconds = parse_duration(dur_arg)
    if seconds is None:
        seconds = 3600
        reason = " ".join(args[2:]) if len(args) > 2 else "Мут"
    else:
        reason = args[3] if len(args) > 3 else "Мут"
    limit = MUTE_LIMITS.get(role, 0)
    if limit > 0 and (seconds == 0 or seconds > limit):
        return await message.reply(f"❌ Лимит мута: {fmt_dur(limit)}")
    caller_id = await get_caller_id(message)
    if is_staff_chat(message):
        key = f"m:{caller_id}:{target}:{seconds}:{int(silent)}"
        await db.cache_action(key, json.dumps({"t":target,"c":caller_id,"r":reason,"s":seconds,"a":"mute","silent":silent}))
        kb = await build_chat_selector(key)
        sl = " 🔕" if silent else ""
        await message.reply(f"🔇 Мут для {await mention(target)} на {fmt_dur(seconds)}{sl}\nПричина: {reason}\n\nВыберите чат:", parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await apply_mute(target, [message.chat.id], caller_id, reason, seconds, silent)
        sl = " (тихо 🔕)" if silent else ""
        await message.reply(f"✅ Мут применён{sl}")

@router.message(Command("unmute"))
async def cmd_unmute(message: Message):
    if not is_mod_context(message): return
    role = await get_caller_role(message)
    if role < 1: return await message.reply("❌ Недостаточно прав")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /unmute @user")
    caller_id = await get_caller_id(message)
    if is_staff_chat(message):
        key = f"um:{caller_id}:{target}"
        await db.cache_action(key, json.dumps({"t":target,"c":caller_id,"a":"unmute"}))
        kb = await build_chat_selector(key)
        await message.reply(f"🔊 Размут: {await mention(target)}\n\nВыберите чат:", parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await apply_unmute(target, [message.chat.id], caller_id)
        await message.reply(f"🔊 {await mention(target, message.chat.id)} размучен!", parse_mode="HTML")

@router.message(Command("ban"))
async def cmd_ban(message: Message):
    if not is_mod_context(message): return
    role = await get_caller_role(message)
    if role < 3: return await message.reply("❌ 3+")
    args = get_args(message, maxsplit=3)
    args, silent = extract_silent_flag(args)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /ban @user 7d [причина] [--silent]")
    tr = await get_role(target)
    if tr >= role: return await message.reply("❌ Роль цели ≥ вашей")
    dur_arg = args[2] if len(args) > 2 else "0"
    seconds = parse_duration(dur_arg)
    if seconds is None:
        seconds = 0
        reason = " ".join(args[2:]) if len(args) > 2 else "Бан"
    else:
        reason = args[3] if len(args) > 3 else "Бан"
    caller_id = await get_caller_id(message)
    if is_staff_chat(message):
        key = f"b:{caller_id}:{target}:{seconds}:{int(silent)}"
        await db.cache_action(key, json.dumps({"t":target,"c":caller_id,"r":reason,"s":seconds,"a":"ban","silent":silent}))
        kb = await build_chat_selector(key)
        sl = " 🔕" if silent else ""
        await message.reply(f"🚫 Бан для {await mention(target)} на {fmt_dur(seconds)}{sl}\nПричина: {reason}\n\nВыберите чат:", parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await apply_ban(target, [message.chat.id], caller_id, reason, seconds, silent)
        sl = " (тихо 🔕)" if silent else ""
        await message.reply(f"✅ Бан применён{sl}")

@router.message(Command("unban"))
async def cmd_unban(message: Message):
    if not is_mod_context(message): return
    role = await get_caller_role(message)
    if role < 3: return await message.reply("❌ 3+")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /unban @user")
    caller_id = await get_caller_id(message)
    if is_staff_chat(message):
        key = f"ub:{caller_id}:{target}"
        await db.cache_action(key, json.dumps({"t":target,"c":caller_id,"a":"unban"}))
        kb = await build_chat_selector(key)
        await message.reply(f"✅ Разбан: {await mention(target)}\n\nВыберите чат:", parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await apply_unban(target, [message.chat.id], caller_id)
        await message.reply(f"✅ {await mention(target, message.chat.id)} разбанен!", parse_mode="HTML")

@router.message(Command("kick"))
async def cmd_kick(message: Message):
    if not is_mod_context(message): return
    role = await get_caller_role(message)
    if role < 1: return await message.reply("❌ Недостаточно прав")
    args = get_args(message, maxsplit=2)
    args, silent = extract_silent_flag(args)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /kick @user [причина] [--silent]")
    tr = await get_role(target)
    if tr >= role: return await message.reply("❌ Роль цели ≥ вашей")
    reason = args[2] if len(args) > 2 else "Кик"
    caller_id = await get_caller_id(message)
    if is_staff_chat(message):
        key = f"k:{caller_id}:{target}:{int(silent)}"
        await db.cache_action(key, json.dumps({"t":target,"c":caller_id,"r":reason,"a":"kick","silent":silent}))
        kb = await build_chat_selector(key)
        sl = " 🔕" if silent else ""
        await message.reply(f"👢 Кик: {await mention(target)}{sl}\nПричина: {reason}\n\nВыберите чат:", parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await apply_kick(target, [message.chat.id], caller_id, reason, silent)
        sl = " (тихо 🔕)" if silent else ""
        await message.reply(f"✅ Кикнут{sl}")


# =============================================================================
# /GBAN /UNGBAN
# =============================================================================

@router.message(Command("gban"))
async def cmd_gban(message: Message):
    if not is_mod_context(message): return
    role = await get_caller_role(message)
    if role < 7: return await message.reply("❌ 7+")
    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /gban @user [причина]")
    tr = await get_role(target)
    if tr >= role: return await message.reply(f"❌ Роль цели: {ROLE_NAMES.get(tr)} ({tr})")
    if tr > 0: return await message.reply("⚠️ Сначала /removerole")
    reason = args[2] if len(args) > 2 else "Глобальный бан"
    caller_id = await get_caller_id(message)
    await db.add_global_ban(target, caller_id, reason)
    ok, fail = 0, 0
    for cid in await db.get_all_chat_ids():
        try:
            await bot.ban_chat_member(cid, target)
            await db.add_ban(target, cid, caller_id, "Глобальный бан")
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.1)
    name = await mention(target)
    result = f"🌐 Глобальный бан!\n{name} — <code>{target}</code>\nПричина: {reason}\n✅ В {ok} чатах"
    if fail: result += f" | ⚠️ {fail} неудач"
    await message.reply(result, parse_mode="HTML")
    if STAFF_CHAT_ID and GBAN_TOPIC_ID:
        try:
            ci = await get_user_info(caller_id)
            ct = f" (@{ci['username']})" if ci['username'] else ""
            await bot.send_message(STAFF_CHAT_ID, f"🌐 <b>ГЛОБАЛЬНЫЙ БАН</b>\n━━━━━━━━━━━━━━━━\n👤 {name}\n🆔 <code>{target}</code>\n📅 Окончание: никогда\n📝 {reason}\n👮 {ci['full_name']}{ct}\n✅ В {ok} чатах\n🕐 {now_str()}", parse_mode="HTML", message_thread_id=GBAN_TOPIC_ID)
        except Exception as e:
            logger.error(f"gban log: {e}")
    await log_action("ГЛОБАЛЬНЫЙ БАН", target, caller_id, reason, 0)
    await notify_user_dm(target, "Вы получили глобальную блокировку", reason, 0, caller_id)

@router.message(Command("ungban"))
async def cmd_ungban(message: Message):
    if not is_mod_context(message): return
    role = await get_caller_role(message)
    if role < 7: return await message.reply("❌ 7+")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /ungban @user")
    if not await db.is_globally_banned(target): return await message.reply("ℹ️ Нет глоб. бана")
    await db.remove_global_ban(target)
    ok = 0
    for cid in await db.get_all_chat_ids():
        try:
            await bot.unban_chat_member(cid, target, only_if_banned=True)
            await db.remove_ban(target, cid)
            ok += 1
        except Exception: pass
        await asyncio.sleep(0.1)
    name = await mention(target)
    await message.reply(f"✅ Глоб. бан снят! {name}\nРазбанен в {ok} чатах", parse_mode="HTML")
    await log_action("СНЯТИЕ ГЛОБ. БАНА", target, await get_caller_id(message))
    if STAFF_CHAT_ID and GBAN_TOPIC_ID:
        try:
            await bot.send_message(STAFF_CHAT_ID, f"✅ <b>СНЯТИЕ ГЛОБ. БАНА</b>\n{name} — <code>{target}</code>\n🕐 {now_str()}", parse_mode="HTML", message_thread_id=GBAN_TOPIC_ID)
        except Exception: pass

# =============================================================================
# /RO /UNRO /QUIET
# =============================================================================

@router.message(Command("ro"))
async def cmd_ro(message: Message):
    if message.chat.type == ChatType.PRIVATE: return
    role = await get_caller_role(message)
    if role < 1: return await message.reply("❌ Недостаточно прав")
    await db.set_ro_mode(message.chat.id, True)
    await message.answer("👁 Режим RO включён!")

@router.message(Command("unro"))
async def cmd_unro(message: Message):
    if message.chat.type == ChatType.PRIVATE: return
    role = await get_caller_role(message)
    if role < 1: return await message.reply("❌ Недостаточно прав")
    await db.set_ro_mode(message.chat.id, False)
    await message.answer("✍️ Режим RO выключен!")

@router.message(Command("quiet"))
async def cmd_quiet(message: Message):
    if message.chat.type == ChatType.PRIVATE: return
    role = await get_caller_role(message)
    if role < 1: return await message.reply("❌ Недостаточно прав")
    is_quiet = await db.is_quiet_mode(message.chat.id)
    await db.set_quiet_mode(message.chat.id, not is_quiet)
    if not is_quiet:
        try:
            await bot.set_chat_permissions(message.chat.id, muted_permissions())
        except Exception as e:
            logger.error(f"quiet on: {e}")
        await message.answer("🔇 Режим тишины включён!")
    else:
        try:
            await bot.set_chat_permissions(message.chat.id, full_permissions())
        except Exception as e:
            logger.error(f"quiet off: {e}")
        await message.answer("🔊 Режим тишины выключен!")

# =============================================================================
# /SETROLE /REMOVEROLE /SREMOVEROLE
# =============================================================================

@router.message(Command("setrole"))
async def cmd_setrole(message: Message):
    cr = await get_caller_role(message)
    if cr < 7: return await message.reply("❌ 7+")
    args = get_args(message)
    if len(args) < 3:
        roles_text = "\n".join([f"  {k}: {v}" for k, v in ROLE_NAMES.items()])
        return await message.reply(f"/setrole @user ЧИСЛО\n\nРоли:\n{roles_text}")
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ Не найден")
    try:
        nr = int(args[2])
        if not (0 <= nr <= 10): return await message.reply("❌ 0-10")
    except ValueError: return await message.reply("❌ Число 0-10")
    tr = await get_role(target)
    if nr >= cr: return await message.reply(f"❌ Нельзя ≥ вашей ({cr})")
    if tr >= cr: return await message.reply("❌ Нельзя менять")
    await db.set_global_role(target, nr)
    name = await mention(target)
    await message.reply(f"⭐ {name}: {ROLE_NAMES.get(tr,'?')} ({tr}) → {ROLE_NAMES.get(nr,'?')} ({nr})", parse_mode="HTML")
    await log_action("СМЕНА РОЛИ", target, await get_caller_id(message), f"{tr} → {nr}")

@router.message(Command("removerole"))
async def cmd_removerole(message: Message):
    cr = await get_caller_role(message)
    if cr < 7: return await message.reply("❌ 7+")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /removerole @user")
    tr = await get_role(target)
    if tr >= cr: return await message.reply("❌ Нельзя")
    if tr == 0: return await message.reply("ℹ️ Нет роли")
    await db.set_global_role(target, 0)
    name = await mention(target)
    await message.reply(f"✅ Роль снята! {name} (была: {ROLE_NAMES.get(tr,'?')})", parse_mode="HTML")
    await log_action("СНЯТИЕ РОЛИ", target, await get_caller_id(message), f"Была: {tr}")

@router.message(Command("sremoverole"))
async def cmd_sremoverole(message: Message):
    cr = await get_caller_role(message)
    if cr < 7: return await message.reply("❌ 7+")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /sremoverole @user — снять роль во всех чатах")
    tr = await get_role(target)
    if tr >= cr: return await message.reply("❌ Нельзя")
    if tr == 0: return await message.reply("ℹ️ Нет роли")
    await db.remove_all_user_roles(target)
    name = await mention(target)
    await message.reply(f"✅ Роль снята во всех чатах! {name} (была: {ROLE_NAMES.get(tr,'?')})", parse_mode="HTML")
    await log_action("СНЯТИЕ РОЛИ (ВСЕ ЧАТЫ)", target, await get_caller_id(message), f"Была: {tr}")

@router.message(Command("staff"))
async def cmd_staff(message: Message):
    staff = await db.get_all_staff()
    if not staff: return await message.answer("ℹ️ Список пуст")
    by_role = {}
    for uid, r in staff:
        by_role.setdefault(r, []).append(uid)
    text = "👥 <b>Команда модерации</b>\n\n"
    for r in sorted(by_role.keys(), reverse=True):
        text += f"<b>{ROLE_NAMES.get(r, '?')} ({r}):</b>\n"
        for uid in by_role[r]:
            text += f"  • {await mention(uid)}\n"
        text += "\n"
    await message.answer(text, parse_mode="HTML")


# =============================================================================
# НИКИ: setnick removenick getnick allsetnick allremnick nlist getacc
# =============================================================================

@router.message(Command("setnick"))
async def cmd_setnick(message: Message):
    if message.chat.type == ChatType.PRIVATE: return
    role = await get_caller_role(message)
    if role < 1: return await message.reply("❌ Недостаточно прав")
    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)
    if not target or len(args) < 3: return await message.reply("❌ /setnick @user Ник")
    await db.set_nick(target, message.chat.id, args[2])
    await message.reply(f"📝 Ник: {args[2]}")

@router.message(Command("removenick"))
async def cmd_removenick(message: Message):
    if message.chat.type == ChatType.PRIVATE: return
    role = await get_caller_role(message)
    if role < 1: return await message.reply("❌ Недостаточно прав")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /removenick @user")
    nick = await db.get_nick(target, message.chat.id)
    if not nick: return await message.reply("ℹ️ Ник не установлен")
    await db.remove_nick(target, message.chat.id)
    await message.reply(f"✅ Ник «{nick}» удалён")

@router.message(Command("getnick"))
async def cmd_getnick(message: Message):
    if message.chat.type == ChatType.PRIVATE: return
    role = await get_caller_role(message)
    if role < 1: return await message.reply("❌ Недостаточно прав")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /getnick @user")
    info = await get_user_info(target)
    nick = await db.get_nick(target, message.chat.id)
    if nick:
        await message.reply(f"📝 Ник: <b>{nick}</b>\n👤 {info['full_name']} (<code>{target}</code>)", parse_mode="HTML")
    else:
        await message.reply(f"ℹ️ Ник не установлен для {info['full_name']}", parse_mode="HTML")

@router.message(Command("allsetnick"))
async def cmd_allsetnick(message: Message):
    role = await get_caller_role(message)
    if role < 1: return await message.reply("❌ Недостаточно прав")
    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)
    if not target or len(args) < 3: return await message.reply("❌ /allsetnick @user Ник")
    nick = args[2]
    chat_ids = [c for c in await db.get_all_chat_ids() if c != STAFF_CHAT_ID]
    await db.set_nick_all(target, nick, chat_ids)
    await message.reply(f"📝 Ник «{nick}» установлен во всех {len(chat_ids)} чатах")

@router.message(Command("allremnick"))
async def cmd_allremnick(message: Message):
    role = await get_caller_role(message)
    if role < 1: return await message.reply("❌ Недостаточно прав")
    args = get_args(message)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /allremnick @user")
    await db.remove_nick_all(target)
    await message.reply(f"✅ Ник {await mention(target)} удалён из всех чатов", parse_mode="HTML")

@router.message(Command("nlist"))
async def cmd_nlist(message: Message):
    if message.chat.type == ChatType.PRIVATE: return
    role = await get_caller_role(message)
    if role < 1: return await message.reply("❌ Недостаточно прав")
    nicks = await db.get_all_nicks(message.chat.id)
    if not nicks: return await message.reply("ℹ️ Список ников пуст")
    text = "📝 <b>Список ников</b>\n\n"
    for uid, nick in nicks:
        text += f"• <b>{nick}</b> — {await mention(uid)} (<code>{uid}</code>)\n"
    await message.answer(text, parse_mode="HTML")

@router.message(Command("getacc"))
async def cmd_getacc(message: Message):
    args = get_args(message, maxsplit=1)
    if len(args) < 2: return await message.reply("❌ /getacc <имя>")
    name = args[1]
    cid = message.chat.id if is_mod_context(message) else 0
    uid = None
    if cid:
        uid = await db.get_user_by_nick(name, cid)
    if not uid:
        uid = await db.get_user_by_nick_any_chat(name)
    if not uid:
        uid = await resolve_username(name)
    if not uid: return await message.reply(f"❌ «{name}» не найден")
    info = await get_user_info(uid)
    role = await get_role(uid, cid) if cid else await get_role(uid)
    text = f"🔍 <b>Результат</b>\n\n👤 {info['full_name']}\n🆔 <code>{uid}</code>\n"
    if info['username']: text += f"📎 @{info['username']}\n"
    text += f"⭐ {ROLE_NAMES.get(role,'?')} ({role})"
    await message.reply(text, parse_mode="HTML")

# =============================================================================
# /REG /ONLINE /ONLINELIST /PULLINFO
# =============================================================================

@router.message(Command("reg"))
async def cmd_reg(message: Message):
    if message.chat.type == ChatType.PRIVATE: return
    args = get_args(message)
    target = await parse_user(message, args)
    if not target:
        target = message.from_user.id if message.from_user else None
    if not target: return await message.reply("❌ /reg @user")
    info = await get_user_info(target)
    regs = await db.get_user_reg_all(target)
    if not regs: return await message.reply(f"ℹ️ Нет данных о регистрации {info['full_name']}", parse_mode="HTML")
    text = f"📅 <b>Регистрация</b>\n👤 {info['full_name']} (<code>{target}</code>)\n\n"
    for cid, reg_at in regs:
        ct = await db.get_chat_title(cid)
        text += f"💬 {ct}: {fmt_ts(reg_at)}\n"
    await message.reply(text, parse_mode="HTML")

@router.message(Command("online"))
async def cmd_online(message: Message):
    if message.chat.type == ChatType.PRIVATE: return
    role = await get_caller_role(message)
    if role < 1: return await message.reply("❌ Недостаточно прав")
    args = get_args(message, maxsplit=1)
    reason = args[1] if len(args) > 1 else "Онлайн-проверка"
    try:
        count = await bot.get_chat_member_count(message.chat.id)
        await message.reply(f"📢 <b>Внимание!</b>\n{reason}\n\n👥 Участников: {count}", parse_mode="HTML")
    except Exception as e:
        await message.reply(f"❌ {e}")

@router.message(Command("onlinelist"))
async def cmd_onlinelist(message: Message):
    if message.chat.type == ChatType.PRIVATE: return
    role = await get_caller_role(message)
    if role < 1: return await message.reply("❌ Недостаточно прав")
    try:
        count = await bot.get_chat_member_count(message.chat.id)
        await message.reply(f"👥 Участников: <b>{count}</b>\n\nℹ️ Telegram Bot API не даёт список онлайн.", parse_mode="HTML")
    except Exception as e:
        await message.reply(f"❌ {e}")

@router.message(Command("pullinfo"))
async def cmd_pullinfo(message: Message):
    chat_ids = await db.get_all_chat_ids()
    text = f"🌐 <b>Сетка</b>\n\n📊 Чатов: <b>{len(chat_ids)}</b>\n\n"
    for cid in chat_ids:
        title = await db.get_chat_title(cid)
        m = "📌" if cid == STAFF_CHAT_ID else "💬"
        text += f"{m} {title}\n   ID: <code>{cid}</code>\n"
    if STAFF_CHAT_ID:
        text += f"\n🛡 Стафф: <code>{STAFF_CHAT_ID}</code>"
    await message.reply(text, parse_mode="HTML")

# =============================================================================
# /CLEAR
# =============================================================================

@router.message(Command("clear"))
async def cmd_clear(message: Message):
    if message.chat.type == ChatType.PRIVATE: return
    role = await get_caller_role(message)
    if role < 4: return await message.reply("❌ 4+")
    args = get_args(message)
    if len(args) < 2: return await message.reply("❌ /clear <1-100>")
    try:
        count = int(args[1])
        if not (1 <= count <= 100): return await message.reply("❌ 1-100")
    except ValueError: return await message.reply("❌ 1-100")
    deleted = 0
    mid = message.message_id
    for i in range(1, count + 1):
        try:
            await bot.delete_message(message.chat.id, mid - i)
            deleted += 1
        except Exception: pass
        if i % 5 == 0: await asyncio.sleep(0.3)
    try:
        st = await message.answer(f"🧹 Очищено {deleted}/{count}")
        await asyncio.sleep(3)
        await st.delete()
        await message.delete()
    except Exception: pass
    await log_action("ОЧИСТКА", 0, await get_caller_id(message), f"{deleted} сообщений", chat_id=message.chat.id)

# =============================================================================
# /BANLIST /WARNLIST
# =============================================================================

@router.message(Command("banlist"))
async def cmd_banlist(message: Message):
    if not is_mod_context(message): return
    role = await get_caller_role(message)
    if role < 3: return await message.reply("❌ 3+")
    args = get_args(message)
    mode, page = "chat", 0
    for a in args[1:]:
        if a == "global": mode = "global"
        elif a.isdigit(): page = max(0, int(a) - 1)
    chat_id = message.chat.id if mode == "chat" and not is_staff_chat(message) else 0
    if mode == "global":
        rows, total = await db.get_all_global_bans_paginated(page, PER_PAGE)
        title = "🌐 <b>Глоб. баны</b>"
    else:
        rows, total = await db.get_all_bans_paginated(page, PER_PAGE, chat_id)
        title = "💬 <b>Баны</b>"
    tp = max(1, math.ceil(total / PER_PAGE))
    if not rows: return await message.answer(f"{title}\n\nПусто.\n/banlist global", parse_mode="HTML")
    text = f"{title} — стр. {page+1}/{tp}\n\n"
    for i, row in enumerate(rows, start=page*PER_PAGE+1):
        uid = row['user_id']
        info = await get_user_info(uid)
        reason = row.get('reason','—') or '—'
        until = row.get('until', 0)
        end = fmt_ts(until) if until and until > int(time.time()) else ("истёк" if until else "навсегда")
        text += f"<b>{i}.</b> {info['full_name']} — <code>{uid}</code>\n    {reason} | {end}\n\n"
    text += f"📄 Всего: {total}"
    if tp > 1: text += f"\n/banlist {'global ' if mode=='global' else ''}{page+2}"
    await message.answer(text, parse_mode="HTML")

@router.message(Command("warnlist"))
async def cmd_warnlist(message: Message):
    if not is_mod_context(message): return
    role = await get_caller_role(message)
    if role < 1: return await message.reply("❌ 1+")
    args = get_args(message)
    page = 0
    for a in args[1:]:
        if a.isdigit(): page = max(0, int(a) - 1)
    chat_id = message.chat.id if not is_staff_chat(message) else 0
    rows, total = await db.get_all_warns_paginated(page, PER_PAGE, chat_id)
    tp = max(1, math.ceil(total / PER_PAGE))
    if not rows: return await message.answer("⚠️ <b>Варны</b>\n\nПусто.", parse_mode="HTML")
    text = f"⚠️ <b>Варны</b> — стр. {page+1}/{tp}\n\n"
    for i, row in enumerate(rows, start=page*PER_PAGE+1):
        uid = row['user_id']
        info = await get_user_info(uid)
        text += f"<b>{i}.</b> {info['full_name']} — <code>{uid}</code>\n    {row['count']}/{MAX_WARNS} | {row.get('reason','—') or '—'}\n\n"
    text += f"📄 Всего: {total}"
    if tp > 1: text += f"\n/warnlist {page+2}"
    await message.answer(text, parse_mode="HTML")


# =============================================================================
# ВЛАДЕЛЕЦ: banwords filter antiflood welcometext
# =============================================================================

@router.message(Command("banwords"))
async def cmd_banwords(message: Message):
    if message.chat.type == ChatType.PRIVATE: return
    role = await get_caller_role(message)
    if role < 7: return await message.reply("❌ 7+")
    args = get_args(message, maxsplit=2)
    if len(args) < 2: return await message.reply("❌ /banwords add|del|list [слово]")
    sub = args[1].lower()
    cid = message.chat.id
    if sub == "list":
        words = await db.get_banwords(cid)
        if not words: return await message.reply("ℹ️ Список пуст")
        return await message.reply("🚫 <b>Запрещённые:</b>\n\n" + "\n".join([f"• {w}" for w in words]), parse_mode="HTML")
    if len(args) < 3: return await message.reply("❌ Укажите слово")
    word = args[2].lower()
    if sub == "add":
        ok = await db.add_banword(cid, word)
        await message.reply(f"✅ «{word}» добавлено" if ok else f"ℹ️ «{word}» уже есть")
    elif sub in ("del","delete","rm","remove"):
        ok = await db.remove_banword(cid, word)
        await message.reply(f"✅ «{word}» удалено" if ok else f"ℹ️ «{word}» не найдено")
    else:
        await message.reply("❌ /banwords add|del|list [слово]")

@router.message(Command("filter"))
async def cmd_filter(message: Message):
    if message.chat.type == ChatType.PRIVATE: return
    role = await get_caller_role(message)
    if role < 7: return await message.reply("❌ 7+")
    is_on = await db.is_filter(message.chat.id)
    await db.set_filter(message.chat.id, not is_on)
    await message.reply(f"{'✅ Фильтр включён' if not is_on else '❌ Фильтр выключен'}")

@router.message(Command("antiflood"))
async def cmd_antiflood(message: Message):
    if message.chat.type == ChatType.PRIVATE: return
    role = await get_caller_role(message)
    if role < 7: return await message.reply("❌ 7+")
    is_on = await db.is_antiflood(message.chat.id)
    await db.set_antiflood(message.chat.id, not is_on)
    await message.reply(f"{'✅ Антифлуд включён' if not is_on else '❌ Антифлуд выключен'}")

@router.message(Command("welcometext"))
async def cmd_welcometext(message: Message):
    if message.chat.type == ChatType.PRIVATE: return
    role = await get_caller_role(message)
    if role < 7: return await message.reply("❌ 7+")
    args = get_args(message, maxsplit=1)
    if len(args) < 2:
        current = await db.get_welcome(message.chat.id)
        if current: return await message.reply(f"📝 Приветствие:\n\n{current}\n\n/welcometext off — выкл")
        return await message.reply("ℹ️ Не установлено.\n/welcometext <текст>\n{user} — имя юзера")
    text = args[1]
    if text.lower() in ("off","выкл","0","нет"):
        await db.set_welcome(message.chat.id, "")
        return await message.reply("✅ Приветствие выключено")
    await db.set_welcome(message.chat.id, text)
    await message.reply(f"✅ Приветствие:\n\n{text}")


# =============================================================================
# CALLBACK: ВЫБОР ЧАТА
# =============================================================================

@router.callback_query(F.data.startswith("chatsel:"))
async def cb_chat_select(call: CallbackQuery):
    parts = call.data.split(":", 2)
    if len(parts) < 3: return await call.answer("❌ Ошибка")
    action_key = parts[1]
    chat_part = parts[2]
    cached = await db.get_cached_action(action_key)
    if not cached:
        try: await call.message.edit_text("⏳ Устарело. Повторите.")
        except Exception: pass
        return await call.answer()
    data = json.loads(cached)
    target, caller_id, action = data["t"], data["c"], data["a"]
    reason, seconds, silent = data.get("r",""), data.get("s",0), data.get("silent",False)
    if call.from_user.id != caller_id and caller_id != 0:
        return await call.answer("❌ Не ваше!", show_alert=True)
    if chat_part == "all":
        chat_ids = [c for c in await db.get_all_chat_ids() if c != STAFF_CHAT_ID]
    else:
        chat_ids = [int(chat_part)]
    chat_names = [await db.get_chat_title(c) for c in chat_ids]
    name = await mention(target)
    sl = " 🔕" if silent else ""
    result = ""
    if action == "warn":
        await apply_warn(target, chat_ids, caller_id, reason, silent)
        result = f"✅ Варн: {name}{sl}"
    elif action == "unwarn":
        await apply_unwarn(target, chat_ids, caller_id)
        result = f"✅ Варн снят: {name}"
    elif action == "mute":
        await apply_mute(target, chat_ids, caller_id, reason, seconds, silent)
        result = f"✅ Мут: {name} на {fmt_dur(seconds)}{sl}"
    elif action == "unmute":
        await apply_unmute(target, chat_ids, caller_id)
        result = f"✅ Размут: {name}"
    elif action == "ban":
        await apply_ban(target, chat_ids, caller_id, reason, seconds, silent)
        result = f"✅ Бан: {name} на {fmt_dur(seconds)}{sl}"
    elif action == "unban":
        await apply_unban(target, chat_ids, caller_id)
        result = f"✅ Разбан: {name}"
    elif action == "kick":
        await apply_kick(target, chat_ids, caller_id, reason, silent)
        result = f"✅ Кик: {name}{sl}"
    chats_str = ", ".join(chat_names) if chat_part != "all" else "все чаты"
    result += f"\n💬 {chats_str}"
    await db.clear_cached_action(action_key)
    try: await call.message.edit_text(result, parse_mode="HTML")
    except Exception: pass
    await call.answer()

@router.callback_query(F.data.startswith("cancel:"))
async def cb_cancel(call: CallbackQuery):
    try: await call.message.edit_text("❌ Отменено")
    except Exception: pass
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
    await db.register_user(uid, cid)
    if await db.is_globally_banned(uid):
        try:
            await bot.ban_chat_member(cid, uid)
            await bot.send_message(cid, f"🚫 {await mention(uid)} — глоб. бан, удалён.", parse_mode="HTML")
        except Exception as e:
            logger.error(f"gban join {uid}: {e}")
        return
    welcome = await db.get_welcome(cid)
    if welcome:
        await bot.send_message(cid, welcome.replace("{user}", event.new_chat_member.user.full_name or ""))

@router.message(F.text)
async def on_message(message: Message):
    if message.chat.type == ChatType.PRIVATE: return
    if not message.from_user: return
    uid = message.from_user.id
    cid = message.chat.id
    if message.from_user.username:
        await db.cache_username(uid, message.from_user.username)
    await db.register_user(uid, cid)
    role = await get_role(uid, cid)

    if await db.is_globally_banned(uid):
        try:
            await bot.ban_chat_member(cid, uid)
            await message.delete()
            await bot.send_message(cid, f"🚫 {await mention(uid)} — глоб. бан!", parse_mode="HTML")
        except Exception: pass
        return

    if role < 1 and await db.is_quiet_mode(cid):
        try: await message.delete()
        except Exception: pass
        return

    if role < 1 and await db.is_ro_mode(cid):
        try: await message.delete()
        except Exception: pass
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
                await bot.send_message(cid, f"🔇 {await mention(uid)} замучен 30 мин (антиспам)", parse_mode="HTML")
                await notify_user_dm(uid, "Вы замучены (антиспам)", "Флуд", 1800, 0)
            except Exception: pass
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
                    await bot.send_message(cid, f"🔇 {await mention(uid)} замучен (запрещённое слово)", parse_mode="HTML")
                    await notify_user_dm(uid, "Вы замучены", "Запрещённое слово", 1800, 0)
                except Exception: pass
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
        BotCommand(command="gban", description="🌐 Глоб. бан"),
        BotCommand(command="ungban", description="🌐 Снять глоб."),
        BotCommand(command="clear", description="🧹 Очистить"),
        BotCommand(command="setrole", description="⭐ Роль"),
        BotCommand(command="removerole", description="❌ Снять роль"),
        BotCommand(command="sremoverole", description="❌ Роль (все чаты)"),
        BotCommand(command="getban", description="🔍 Баны"),
        BotCommand(command="getwarn", description="🔍 Варны"),
        BotCommand(command="banlist", description="📋 Банлист"),
        BotCommand(command="warnlist", description="📋 Варнлист"),
        BotCommand(command="ro", description="👁 RO"),
        BotCommand(command="unro", description="✍️ Снять RO"),
        BotCommand(command="quiet", description="🔇 Тишина"),
        BotCommand(command="setnick", description="📝 Ник"),
        BotCommand(command="removenick", description="❌ Удалить ник"),
        BotCommand(command="getnick", description="🔍 Ник"),
        BotCommand(command="allsetnick", description="📝 Ник (все)"),
        BotCommand(command="allremnick", description="❌ Ник удалить (все)"),
        BotCommand(command="nlist", description="📋 Ники"),
        BotCommand(command="getacc", description="🔍 Поиск"),
        BotCommand(command="reg", description="📅 Регистрация"),
        BotCommand(command="online", description="📢 Онлайн"),
        BotCommand(command="onlinelist", description="👥 Участники"),
        BotCommand(command="pullinfo", description="🌐 Сетка"),
        BotCommand(command="staff", description="👥 Команда"),
        BotCommand(command="banwords", description="🚫 Слова"),
        BotCommand(command="filter", description="🔤 Фильтр"),
        BotCommand(command="antiflood", description="🌊 Антифлуд"),
        BotCommand(command="welcometext", description="👋 Приветствие"),
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
    if not PRESET_STAFF: return
    for uid_str, role in PRESET_STAFF.items():
        try: await db.set_global_role(int(uid_str), role)
        except Exception as e: logger.error(f"Preset staff {uid_str}: {e}")
    logger.info(f"✅ Preset staff: {len(PRESET_STAFF)}")


async def periodic_cleanup():
    while True:
        await asyncio.sleep(3600)
        try: await db.cleanup_old_cache(3600)
        except Exception: pass


async def main():
    global db, BOT_ID
    db = Database("database.db")
    await db.init()
    me = await bot.get_me()
    BOT_ID = me.id
    logger.info(f"🔵 Модерация v8.0 — @{me.username} (ID: {BOT_ID})")
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
            logger.info(f"Стафф: {STAFF_CHAT_ID} ({chat.title})")
        except Exception as e:
            logger.warning(f"Стафф: {e}")
    await register_commands()
    asyncio.create_task(periodic_cleanup())
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("✅ Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
