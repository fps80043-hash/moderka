"""Модерация v8.1 — полный рефакторинг"""

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
REPORT_TOPIC_ID: int = config.get("report_topic_id", 558)
SUPPORT_LINK: str = config.get("support_link", "")
PRESET_STAFF: dict = config.get("preset_staff", {})
MAX_WARNS: int = config.get("max_warns", 3)
SPAM_INTERVAL: int = config.get("spam_interval_seconds", 2)
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

# Минимальная роль для команд
CMD_ROLES = {
    "stats": 0, "staff": 0, "report": 0, "top": 0, "start": 0, "help": 0,
    "warn": 1, "mute": 1, "kick": 1, "unwarn": 1, "unmute": 1, "getwarn": 1, "warnlist": 1, "rep": 1,
    "reg": 3,
    "banlist": 4, "ro": 4, "unro": 4, "setnick": 4, "removenick": 4, "getnick": 4, "nlist": 4, "online": 4, "onlinelist": 4,
    "getacc": 5, "getban": 5, "ban": 5, "unban": 5, "banwords": 5, "filter": 5, "antiflood": 5, "welcometext": 5, "clear": 5,
    "gban": 7, "ungban": 7, "setrole": 7, "removerole": 7, "sremoverole": 7, "allsetnick": 7, "allremnick": 7, "pullinfo": 0, "quiet": 4,
}

def is_anon(message) -> bool:
    if hasattr(message, "from_user") and message.from_user and message.from_user.id == ANONYMOUS_BOT_ID:
        return True
    if hasattr(message, "sender_chat") and message.sender_chat:
        if hasattr(message, "chat") and message.sender_chat.id == message.chat.id:
            return True
    return False

def in_group(message: Message) -> bool:
    return message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

def in_staff(message: Message) -> bool:
    return STAFF_CHAT_ID != 0 and message.chat.id == STAFF_CHAT_ID

def get_args(message: Message, maxsplit: int = -1) -> list:
    if not message.text: return []
    text = message.text.strip()
    parts = text.split(maxsplit=1)
    if not parts: return []
    cmd = parts[0].split("@")[0]
    clean = cmd + (" " + parts[1] if len(parts) > 1 else "")
    return clean.split(maxsplit=maxsplit) if maxsplit >= 0 else clean.split()

def extract_silent(args: list) -> tuple:
    silent = False
    new = []
    for a in args:
        if a in ("--silent", "-s", "--тихо", "тихо"):
            silent = True
        else:
            new.append(a)
    return new, silent

async def get_role(user_id: int, chat_id: int = 0) -> int:
    if user_id == 0 or user_id == ANONYMOUS_BOT_ID: return 0
    g = await db.get_global_role(user_id)
    if g > 0: return g
    if chat_id: return await db.get_user_role(user_id, chat_id)
    return 0

async def caller_role(message: Message) -> int:
    if is_anon(message): return ANON_ADMIN_ROLE
    if not message.from_user: return 0
    return await get_role(message.from_user.id, message.chat.id)

async def caller_id(message: Message) -> int:
    if is_anon(message): return 0
    return message.from_user.id if message.from_user else 0

async def check_role(message: Message, cmd: str) -> int:
    """Проверяет роль. Возвращает роль если достаточно, иначе -1 + логирует"""
    role = await caller_role(message)
    needed = CMD_ROLES.get(cmd, 0)
    if role >= needed:
        return role
    # Лог запрещённой команды
    uid = await caller_id(message)
    if uid and STAFF_CHAT_ID and LOG_TOPIC_ID:
        info = await get_user_info(uid)
        rn = ROLE_NAMES.get(role, "?")
        try:
            await bot.send_message(STAFF_CHAT_ID,
                f"⛔ <b>Запрещённая команда</b>\n{info['full_name']} ({rn}, {role}) использует /{cmd}\nНужна роль: {needed}+",
                parse_mode="HTML", message_thread_id=LOG_TOPIC_ID)
        except Exception:
            pass
    await message.reply(f"❌ Недостаточно прав (нужна роль {needed}+)")
    return -1

async def get_user_info(user_id: int) -> dict:
    if user_id == 0 or user_id == ANONYMOUS_BOT_ID:
        return {"id": user_id, "username": "", "full_name": "Анонимный администратор"}
    try:
        chat = await bot.get_chat(user_id)
        uname = chat.username or ""
        if uname: await db.cache_username(user_id, uname)
        return {"id": user_id, "username": uname, "full_name": chat.full_name or f"User {user_id}"}
    except Exception:
        cached = await db.get_username_by_id(user_id)
        return {"id": user_id, "username": cached or "", "full_name": f"@{cached}" if cached else f"ID:{user_id}"}

async def mention(user_id: int, chat_id: int = 0) -> str:
    if user_id == 0 or user_id == ANONYMOUS_BOT_ID:
        return "<i>Аноним</i>"
    if chat_id:
        nick = await db.get_nick(user_id, chat_id)
        if nick:
            return f'<a href="tg://user?id={user_id}">{nick}</a>'
    info = await get_user_info(user_id)
    return f'<a href="tg://user?id={user_id}">{info["full_name"]}</a>'

async def resolve_username(username: str) -> Optional[int]:
    username = username.lower().lstrip("@")
    cached = await db.get_user_by_username(username)
    if cached: return cached
    try:
        chat = await bot.get_chat(f"@{username}")
        if chat and chat.id:
            await db.cache_username(chat.id, username)
            return chat.id
    except Exception:
        pass
    return None

async def parse_user(message: Message, args: list, idx: int = 1) -> Optional[int]:
    # Из реплая
    if message.reply_to_message:
        r = message.reply_to_message
        if r.from_user and not is_anon(r) and r.from_user.id != BOT_ID:
            if r.from_user.username:
                await db.cache_username(r.from_user.id, r.from_user.username)
            return r.from_user.id
    if message.forward_from:
        return message.forward_from.id
    if len(args) <= idx: return None
    arg = args[idx].strip()
    if arg.startswith("@"):
        return await resolve_username(arg)
    if arg.isdigit():
        return int(arg)
    # По нику
    if in_group(message):
        nick_user = await db.get_user_by_nick(arg, message.chat.id)
        if nick_user: return nick_user
    nick_user = await db.get_user_by_nick_any_chat(arg)
    if nick_user: return nick_user
    return await resolve_username(arg)

def parse_duration(s: str) -> Optional[int]:
    s = s.lower().strip()
    if s in ("0", "навсегда", "forever", "пермач"): return 0
    multi = {"s": 1, "с": 1, "m": 60, "м": 60, "min": 60, "мин": 60,
             "h": 3600, "ч": 3600, "d": 86400, "д": 86400, "дн": 86400}
    for suffix, mult in sorted(multi.items(), key=lambda x: -len(x[0])):
        if s.endswith(suffix):
            num = s[:-len(suffix)]
            try: return int(num) * mult
            except ValueError: return None
    try: return int(s) * 60
    except ValueError: return None

def fmt_dur(seconds):
    if seconds <= 0: return "навсегда"
    if seconds < 60: return f"{seconds} сек"
    if seconds < 3600: return f"{seconds // 60} мин"
    if seconds < 86400: return f"{seconds // 3600} ч"
    return f"{seconds // 86400} дн"

def fmt_ts(ts):
    if not ts: return "—"
    try: return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")
    except Exception: return "—"

def now_str():
    return datetime.now().strftime("%d.%m.%Y %H:%M")

def end_date_str(duration):
    if duration <= 0: return "никогда"
    return fmt_ts(int(time.time()) + duration)

def muted_perms():
    return ChatPermissions(can_send_messages=False, can_send_audios=False, can_send_documents=False,
        can_send_photos=False, can_send_videos=False, can_send_video_notes=False,
        can_send_voice_notes=False, can_send_polls=False, can_send_other_messages=False,
        can_add_web_page_previews=False, can_change_info=False, can_invite_users=False,
        can_pin_messages=False, can_manage_topics=False)

def full_perms():
    return ChatPermissions(can_send_messages=True, can_send_audios=True, can_send_documents=True,
        can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
        can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
        can_add_web_page_previews=True, can_change_info=False, can_invite_users=True,
        can_pin_messages=False, can_manage_topics=False)

async def build_chat_selector(action_key):
    b = InlineKeyboardBuilder()
    for cid in await db.get_all_chat_ids():
        if cid == STAFF_CHAT_ID: continue
        title = await db.get_chat_title(cid)
        short = title[:25] + "…" if len(title) > 25 else title
        b.button(text=f"💬 {short}", callback_data=f"cs:{action_key}:{cid}")
    b.button(text="🌐 Все чаты", callback_data=f"cs:{action_key}:all")
    b.button(text="❌ Отмена", callback_data="cancel:x")
    b.adjust(1)
    return b

async def log_action(action, target, caller_id_val, reason="", duration=-1, chat_id=0):
    if not STAFF_CHAT_ID or not LOG_TOPIC_ID: return
    try:
        ti = await get_user_info(target)
        ci = await get_user_info(caller_id_val)
        ct = await db.get_chat_title(chat_id) if chat_id else "все чаты"
        tu = f" (@{ti['username']})" if ti["username"] else ""
        cu = f" (@{ci['username']})" if ci["username"] else ""
        text = f"📋 <b>{action}</b>\n━━━━━━━━━━━━━━━━\n👤 Кому: {ti['full_name']}{tu}\n🆔 <code>{target}</code>\n"
        if duration >= 0:
            text += f"⏱ {fmt_dur(duration)}\n📅 До: {end_date_str(duration)}\n"
        if reason: text += f"📝 {reason}\n"
        text += f"👮 {ci['full_name']}{cu}\n💬 {ct}\n🕐 {now_str()}"
        await bot.send_message(STAFF_CHAT_ID, text, parse_mode="HTML", message_thread_id=LOG_TOPIC_ID)
    except Exception as e:
        logger.error(f"log_action: {e}")

async def log_punish(action, target, caller_id_val, reason="", duration=-1, chat_id=0):
    if not STAFF_CHAT_ID or not PUNISH_TOPIC_ID: return
    try:
        ti = await get_user_info(target)
        ct = await db.get_chat_title(chat_id) if chat_id else "все чаты"
        tu = f" (@{ti['username']})" if ti["username"] else ""
        text = f"📋 <b>{action}</b>\n👤 {ti['full_name']}{tu} (<code>{target}</code>)\n"
        if duration >= 0: text += f"⏱ {fmt_dur(duration)}\n"
        if reason: text += f"📝 {reason}\n"
        text += f"💬 {ct} | 🕐 {now_str()}"
        await bot.send_message(STAFF_CHAT_ID, text, parse_mode="HTML", message_thread_id=PUNISH_TOPIC_ID)
    except Exception as e:
        logger.error(f"log_punish: {e}")

async def notify_dm(user_id, action_name, reason, duration, cid):
    try:
        ci = await get_user_info(cid)
        text = f"⚠️ <b>{action_name}</b>\n\n📅 {now_str()}\n📅 До: {end_date_str(duration)}\n📝 {reason}\n👮 {ci['full_name']}\n"
        if SUPPORT_LINK: text += f"\n{SUPPORT_LINK}"
        await bot.send_message(user_id, text, parse_mode="HTML")
    except Exception: pass

async def apply_warn(target, chat_ids, cid, reason, silent=False):
    for c in chat_ids:
        warns = await db.add_warn(target, c, cid, reason)
        name = await mention(target, c)
        if warns >= MAX_WARNS:
            try:
                await bot.ban_chat_member(c, target)
                await asyncio.sleep(0.5)
                await bot.unban_chat_member(c, target)
            except Exception: pass
            await db.clear_warns(target, c)
            if not silent:
                try: await bot.send_message(c, f"⚠️ {name} — ({MAX_WARNS}/{MAX_WARNS})\n{reason}\n\n👢 Кик!", parse_mode="HTML")
                except Exception: pass
        else:
            if not silent:
                try: await bot.send_message(c, f"⚠️ {name} — ({warns}/{MAX_WARNS})\n{reason}", parse_mode="HTML")
                except Exception: pass
        await log_action("ВАРН", target, cid, reason, chat_id=c)
        if not silent: await log_punish("ВАРН", target, cid, reason, chat_id=c)
    await notify_dm(target, "Предупреждение", reason, -1, cid)

async def apply_mute(target, chat_ids, cid, reason, seconds, silent=False):
    for c in chat_ids:
        try:
            until = int(time.time()) + seconds if seconds > 0 else 0
            delta = timedelta(seconds=seconds) if seconds > 0 else None
            await bot.restrict_chat_member(c, target, permissions=muted_perms(), until_date=delta)
            await db.add_mute(target, c, cid, reason, until)
            if not silent:
                name = await mention(target, c)
                await bot.send_message(c, f"🔇 {name} замучен на {fmt_dur(seconds)}\n{reason}", parse_mode="HTML")
        except Exception as e:
            logger.error(f"mute {target} in {c}: {e}")
        await log_action("МУТ", target, cid, reason, seconds, c)
        if not silent: await log_punish("МУТ", target, cid, reason, seconds, c)
    await notify_dm(target, "Вы замучены", reason, seconds, cid)

async def apply_ban(target, chat_ids, cid, reason, seconds, silent=False):
    for c in chat_ids:
        try:
            delta = timedelta(seconds=seconds) if seconds > 0 else None
            until = int(time.time()) + seconds if seconds > 0 else 0
            await bot.ban_chat_member(c, target, until_date=delta)
            await db.add_ban(target, c, cid, reason, until)
            if not silent:
                name = await mention(target, c)
                await bot.send_message(c, f"🚫 {name} забанен на {fmt_dur(seconds)}\n{reason}", parse_mode="HTML")
        except Exception as e:
            logger.error(f"ban {target} in {c}: {e}")
        await log_action("БАН", target, cid, reason, seconds, c)
        if not silent: await log_punish("БАН", target, cid, reason, seconds, c)
    await notify_dm(target, "Вы заблокированы", reason, seconds, cid)

async def apply_kick(target, chat_ids, cid, reason, silent=False):
    for c in chat_ids:
        try:
            await bot.ban_chat_member(c, target)
            await asyncio.sleep(0.5)
            await bot.unban_chat_member(c, target)
            if not silent:
                name = await mention(target, c)
                await bot.send_message(c, f"👢 {name} кикнут\n{reason}", parse_mode="HTML")
        except Exception: pass
        await log_action("КИК", target, cid, reason, chat_id=c)
        if not silent: await log_punish("КИК", target, cid, reason, chat_id=c)
    await notify_dm(target, "Вы кикнуты", reason, -1, cid)

async def apply_unmute(target, chat_ids, cid):
    for c in chat_ids:
        try:
            await bot.restrict_chat_member(c, target, permissions=full_perms())
            await db.remove_mute(target, c)
        except Exception: pass
    await log_action("РАЗМУТ", target, cid)

async def apply_unban(target, chat_ids, cid):
    for c in chat_ids:
        try:
            await bot.unban_chat_member(c, target, only_if_banned=True)
            await db.remove_ban(target, c)
        except Exception: pass
    await log_action("РАЗБАН", target, cid)

async def apply_unwarn(target, chat_ids, cid):
    for c in chat_ids:
        await db.remove_warn(target, c)
    await log_action("СНЯТИЕ ВАРНА", target, cid)


# =============================================================================
# КОМАНДЫ: Общие (0+)
# =============================================================================

@router.message(Command("start"))
async def cmd_start(message: Message):
    if message.chat.type != ChatType.PRIVATE: return
    if not message.from_user: return
    uid = message.from_user.id
    p = await db.get_user_all_punishments(uid)
    text = "👋 <b>Привет!</b>\nБот модерации.\n\n"
    found = False
    if p["global_ban"]:
        gb = p["global_ban"]
        text += f"🌐 <b>Глоб. бан</b>\n  Дата: {fmt_ts(gb.get('banned_at',0))}\n  Причина: {gb.get('reason','—')}\n\n"
        found = True
    for ban in p["bans"]:
        ct = await db.get_chat_title(ban["chat_id"])
        until = ban.get("until", 0)
        end = fmt_ts(until) if until and until > int(time.time()) else ("навсегда" if not until else "истёк")
        text += f"🚫 <b>Бан</b> — {ct}\n  До: {end}\n  Причина: {ban.get('reason','—')}\n\n"
        found = True
    for mute in p["mutes"]:
        ct = await db.get_chat_title(mute["chat_id"])
        until = mute.get("until", 0)
        end = fmt_ts(until) if until and until > int(time.time()) else ("навсегда" if not until else "истёк")
        text += f"🔇 <b>Мут</b> — {ct}\n  До: {end}\n  Причина: {mute.get('reason','—')}\n\n"
        found = True
    for w in p["warns"]:
        ct = await db.get_chat_title(w["chat_id"])
        text += f"⚠️ <b>Варны: {w['count']}/{MAX_WARNS}</b> — {ct}\n\n"
        found = True
    if not found: text += "✅ Наказаний нет!\n"
    if SUPPORT_LINK: text += f"\n📞 {SUPPORT_LINK}"
    await message.answer(text, parse_mode="HTML")

@router.message(Command("help"))
async def cmd_help(message: Message):
    role = await caller_role(message)
    text = f"📖 <b>Команды модерации</b>\nРоль: <b>{ROLE_NAMES.get(role,'?')} ({role})</b>\n\n"

    text += "<b>[0] Пользователь:</b>\n"
    text += "/stats - статистика\n"
    text += "/staff - список модераторов\n"
    text += "/report - репорт\n"
    text += "/top - топ по сообщениям\n\n"

    if role >= 1:
        text += "<b>[1-2] Младший модератор - Модератор:</b>\n"
        text += "/warn - выдать варн\n"
        text += "/mute - блокировка чата\n"
        text += "/kick - кикнуть с группы\n"
        text += "/unwarn - снять варн\n"
        text += "/unmute - снять мут\n"
        text += "/getwarn - проверить на варн\n"
        text += "/warnlist - список предупреждений\n"
        text += "/rep - принять репорт\n\n"

    if role >= 3:
        text += "<b>[3] Старший модератор:</b>\n"
        text += "/reg - дата регистрации в группе\n\n"

    if role >= 4:
        text += "<b>[4] Куратор модерации:</b>\n"
        text += "/banlist - список заблокированных\n"
        text += "/ro\n/unro\n"
        text += "/setnick - выдать ник\n"
        text += "/removenick - убрать ник\n"
        text += "/getnick - найти по нику\n"
        text += "/nlist - список ников\n"
        text += "/online - модераторы онлайн\n"
        text += "/onlinelist - модераторы онлайн\n\n"

    if role >= 5:
        text += "<b>[5-6] Технический специалист - Гл. Тех. Специалист:</b>\n"
        text += "/getacc - проверить аккаунт\n"
        text += "/reg - дата регистрации в группе\n"
        text += "/getban - проверить на блокировку\n"
        text += "/ban [--silent] - забанить пользователя\n"
        text += "/unban - разбанить пользователя\n"
        text += "/banwords\n"
        text += "/filter - фильтр слов\n"
        text += "/antiflood - антифлуд\n"
        text += "/welcometext - текст при заходе в группу\n"
        text += "/clear - очистить сообщения\n\n"

    if role >= 7:
        text += "<b>[7-10] Куратор групп, Зам. главного модератора, Главный модератор, Владелец:</b>\n"
        text += "/gban - блокировка во всех чатах\n"
        text += "/ungban - снятие блокировки во всех чатах\n"
        text += "/setrole - выдать роль пользователю\n"
        text += "/removerole - снять роль пользователю\n"
        text += "/sremoverole - снять роль везде\n"
        text += "/allsetnick - поставить ник везде\n"
        text += "/allremnick - убрать ник везде\n\n"

    text += "💡 <code>--silent</code> — тихое наказание"
    await message.answer(text, parse_mode="HTML")

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    args = get_args(message)
    if message.chat.type == ChatType.PRIVATE:
        if not message.from_user: return
        uid = message.from_user.id
        role = await get_role(uid)
        mc = await db.get_message_count(uid)
        return await message.answer(f"👤 <b>Статистика</b>\n\nID: <code>{uid}</code>\nРоль: {ROLE_NAMES.get(role,'?')} ({role})\n📨 Сообщений: {mc}", parse_mode="HTML")
    target = await parse_user(message, args)
    if not target:
        target = message.from_user.id if message.from_user else None
    if not target: return await message.reply("❌ Не найден")
    info = await get_user_info(target)
    cid = message.chat.id if not in_staff(message) else 0
    role = await get_role(target, cid) if cid else await get_role(target)
    mc_chat = await db.get_message_count(target, cid) if cid else 0
    mc_total = await db.get_message_count(target)
    t = f"📊 <b>Статистика</b>\n\nID: <code>{target}</code>\n"
    if info["username"]: t += f"@{info['username']}\n"
    t += f"Роль: {ROLE_NAMES.get(role,'?')} ({role})\n"
    if cid:
        t += f"📨 В чате: {mc_chat}\n"
        warns = await db.get_warns(target, cid)
        is_m = await db.is_muted(target, cid)
        is_b = await db.is_banned(target, cid)
        t += f"Варны: {warns}/{MAX_WARNS}\nМут: {'да' if is_m else 'нет'}\nБан: {'да' if is_b else 'нет'}\n"
    t += f"📨 Всего: {mc_total}"
    await message.answer(t, parse_mode="HTML")

@router.message(Command("staff"))
async def cmd_staff(message: Message):
    staff = await db.get_all_staff()
    if not staff: return await message.answer("ℹ️ Список пуст")
    by_role = {}
    for uid, r in staff:
        by_role.setdefault(r, []).append(uid)
    text = "👥 <b>Команда</b>\n\n"
    for r in sorted(by_role.keys(), reverse=True):
        text += f"<b>{ROLE_NAMES.get(r,'?')} ({r}):</b>\n"
        for uid in by_role[r]:
            text += f"  • {await mention(uid)}\n"
        text += "\n"
    await message.answer(text, parse_mode="HTML")

@router.message(Command("top"))
async def cmd_top(message: Message):
    if not in_group(message): return
    cid = message.chat.id
    top = await db.get_top_messagers(cid, 10)
    if not top: return await message.reply("ℹ️ Нет данных")
    text = "🏆 <b>Топ по сообщениям</b>\n\n"
    for i, (uid, count) in enumerate(top, 1):
        name = await mention(uid, cid)
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        text += f"{medal} {name} — {count}\n"
    await message.answer(text, parse_mode="HTML")

@router.message(Command("pullinfo"))
async def cmd_pullinfo(message: Message):
    chat_ids = await db.get_all_chat_ids()
    text = f"🌐 <b>Сетка</b>\n📊 Чатов: <b>{len(chat_ids)}</b>\n\n"
    for cid in chat_ids:
        title = await db.get_chat_title(cid)
        m = "📌" if cid == STAFF_CHAT_ID else "💬"
        text += f"{m} {title}\n   <code>{cid}</code>\n"
    await message.reply(text, parse_mode="HTML")

# =============================================================================
# РЕПОРТЫ: /report (0+), /rep (1+)
# =============================================================================

@router.message(Command("report"))
async def cmd_report(message: Message):
    if not in_group(message): return
    if not message.from_user: return
    args = get_args(message, maxsplit=1)
    reason = args[1] if len(args) > 1 else ""
    # Нужен реплай на сообщение
    reply_msg_id = 0
    thread_id = 0
    if message.reply_to_message:
        reply_msg_id = message.reply_to_message.message_id
        thread_id = getattr(message.reply_to_message, "message_thread_id", 0) or 0
    else:
        reply_msg_id = message.message_id
        thread_id = getattr(message, "message_thread_id", 0) or 0
    report_id = await db.create_report(message.from_user.id, message.chat.id, reply_msg_id, thread_id, reason)
    reporter = await mention(message.from_user.id)
    chat_title = await db.get_chat_title(message.chat.id)
    # Отправляем в стафф-чат → топик репортов
    if STAFF_CHAT_ID and REPORT_TOPIC_ID:
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Принять", callback_data=f"rep_accept:{report_id}")
        try:
            rep_text = f"🚨 <b>Репорт #{report_id}</b>\n\n👤 От: {reporter}\n💬 Чат: {chat_title}\n"
            if reason: rep_text += f"📝 Причина: {reason}\n"
            rep_text += f"\n/rep {report_id} — принять"
            await bot.send_message(STAFF_CHAT_ID, rep_text, parse_mode="HTML", message_thread_id=REPORT_TOPIC_ID, reply_markup=kb.as_markup())
        except Exception as e:
            logger.error(f"report send: {e}")
    await message.reply("✅ Репорт отправлен модераторам!")

@router.message(Command("rep"))
async def cmd_rep(message: Message):
    if not in_group(message) and not in_staff(message): return
    role = await check_role(message, "rep")
    if role < 0: return
    args = get_args(message)
    if len(args) < 2: return await message.reply("❌ /rep <номер_репорта>")
    try:
        report_id = int(args[1])
    except ValueError:
        return await message.reply("❌ Укажите номер репорта")
    report = await db.get_report(report_id)
    if not report: return await message.reply("❌ Репорт не найден")
    if report["status"] != "open": return await message.reply("ℹ️ Репорт уже обработан")
    mod_id = await caller_id(message)
    await db.accept_report(report_id, mod_id)
    mod_name = await mention(mod_id)
    chat_title = await db.get_chat_title(report["chat_id"])
    # Ссылка на сообщение
    chat_id_str = str(report["chat_id"]).replace("-100", "")
    msg_link = f"https://t.me/c/{chat_id_str}/{report['message_id']}"
    if report.get("thread_id"):
        msg_link = f"https://t.me/c/{chat_id_str}/{report['thread_id']}/{report['message_id']}"
    await message.reply(
        f"✅ Репорт #{report_id} принят!\n👮 {mod_name}\n💬 {chat_title}\n\n🔗 <a href=\"{msg_link}\">Перейти к сообщению</a>",
        parse_mode="HTML")

@router.callback_query(F.data.startswith("rep_accept:"))
async def cb_rep_accept(call: CallbackQuery):
    report_id = int(call.data.split(":")[1])
    report = await db.get_report(report_id)
    if not report: return await call.answer("❌ Не найден")
    if report["status"] != "open": return await call.answer("ℹ️ Уже обработан")
    mod_id = call.from_user.id
    mod_role = await get_role(mod_id)
    if mod_role < 1: return await call.answer("❌ Недостаточно прав")
    await db.accept_report(report_id, mod_id)
    mod_name = await mention(mod_id)
    chat_title = await db.get_chat_title(report["chat_id"])
    chat_id_str = str(report["chat_id"]).replace("-100", "")
    msg_link = f"https://t.me/c/{chat_id_str}/{report['message_id']}"
    if report.get("thread_id"):
        msg_link = f"https://t.me/c/{chat_id_str}/{report['thread_id']}/{report['message_id']}"
    try:
        await call.message.edit_text(
            f"✅ Репорт #{report_id} принят!\n👮 {mod_name}\n💬 {chat_title}\n🔗 <a href=\"{msg_link}\">Перейти</a>",
            parse_mode="HTML")
    except Exception: pass
    await call.answer("✅ Принято!")

# =============================================================================
# МОДЕРАЦИЯ (1+): warn/unwarn/mute/unmute/kick/getwarn/warnlist
# =============================================================================

@router.message(Command("warn"))
async def cmd_warn(message: Message):
    if not in_group(message): return
    role = await check_role(message, "warn")
    if role < 0: return
    args = get_args(message, maxsplit=2)
    args, silent = extract_silent(args)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /warn @user [причина] [--silent]")
    tr = await get_role(target)
    if tr > 0 and tr >= role: return await message.reply("❌ Нельзя: роль цели ≥ вашей")
    reason = args[2] if len(args) > 2 else "Нарушение правил"
    cid = await caller_id(message)
    if in_staff(message):
        key = f"w:{cid}:{target}:{int(time.time())}"
        await db.cache_action(key, json.dumps({"t":target,"c":cid,"r":reason,"a":"warn","silent":silent}))
        kb = await build_chat_selector(key)
        sl = " 🔕" if silent else ""
        await message.reply(f"⚠️ Варн: {await mention(target)}{sl}\n{reason}\n\nВыберите чат:", parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await apply_warn(target, [message.chat.id], cid, reason, silent)
        sl = " (тихо 🔕)" if silent else ""
        await message.reply(f"✅ Варн выдан{sl}")

@router.message(Command("unwarn"))
async def cmd_unwarn(message: Message):
    if not in_group(message): return
    role = await check_role(message, "unwarn")
    if role < 0: return
    args = get_args(message)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /unwarn @user")
    cid = await caller_id(message)
    if in_staff(message):
        key = f"uw:{cid}:{target}:{int(time.time())}"
        await db.cache_action(key, json.dumps({"t":target,"c":cid,"a":"unwarn"}))
        kb = await build_chat_selector(key)
        await message.reply(f"Снять варн: {await mention(target)}\nВыберите чат:", parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await apply_unwarn(target, [message.chat.id], cid)
        await message.reply(f"✅ Варн снят")

@router.message(Command("mute"))
async def cmd_mute(message: Message):
    if not in_group(message): return
    role = await check_role(message, "mute")
    if role < 0: return
    args = get_args(message, maxsplit=3)
    args, silent = extract_silent(args)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /mute @user 30m [причина] [--silent]")
    tr = await get_role(target)
    if tr > 0 and tr >= role: return await message.reply("❌ Нельзя: роль цели ≥ вашей")
    dur_arg = args[2] if len(args) > 2 else "1h"
    seconds = parse_duration(dur_arg)
    if seconds is None:
        seconds = 3600
        reason = " ".join(args[2:]) if len(args) > 2 else "Мут"
    else:
        reason = args[3] if len(args) > 3 else "Мут"
    limit = MUTE_LIMITS.get(role, 0)
    if limit > 0 and (seconds == 0 or seconds > limit):
        return await message.reply(f"❌ Лимит: {fmt_dur(limit)}")
    cid = await caller_id(message)
    if in_staff(message):
        key = f"m:{cid}:{target}:{int(time.time())}"
        await db.cache_action(key, json.dumps({"t":target,"c":cid,"r":reason,"s":seconds,"a":"mute","silent":silent}))
        kb = await build_chat_selector(key)
        sl = " 🔕" if silent else ""
        await message.reply(f"🔇 Мут: {await mention(target)} {fmt_dur(seconds)}{sl}\n{reason}\n\nВыберите чат:", parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await apply_mute(target, [message.chat.id], cid, reason, seconds, silent)
        sl = " (тихо 🔕)" if silent else ""
        await message.reply(f"✅ Мут{sl}")

@router.message(Command("unmute"))
async def cmd_unmute(message: Message):
    if not in_group(message): return
    role = await check_role(message, "unmute")
    if role < 0: return
    args = get_args(message)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /unmute @user")
    cid = await caller_id(message)
    if in_staff(message):
        key = f"um:{cid}:{target}:{int(time.time())}"
        await db.cache_action(key, json.dumps({"t":target,"c":cid,"a":"unmute"}))
        kb = await build_chat_selector(key)
        await message.reply(f"Размут: {await mention(target)}\nВыберите чат:", parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await apply_unmute(target, [message.chat.id], cid)
        await message.reply("✅ Размут")

@router.message(Command("kick"))
async def cmd_kick(message: Message):
    if not in_group(message): return
    role = await check_role(message, "kick")
    if role < 0: return
    args = get_args(message, maxsplit=2)
    args, silent = extract_silent(args)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /kick @user [причина] [--silent]")
    tr = await get_role(target)
    if tr > 0 and tr >= role: return await message.reply("❌ Нельзя: роль цели ≥ вашей")
    reason = args[2] if len(args) > 2 else "Кик"
    cid = await caller_id(message)
    if in_staff(message):
        key = f"k:{cid}:{target}:{int(time.time())}"
        await db.cache_action(key, json.dumps({"t":target,"c":cid,"r":reason,"a":"kick","silent":silent}))
        kb = await build_chat_selector(key)
        sl = " 🔕" if silent else ""
        await message.reply(f"👢 Кик: {await mention(target)}{sl}\n{reason}\n\nВыберите чат:", parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await apply_kick(target, [message.chat.id], cid, reason, silent)
        sl = " (тихо 🔕)" if silent else ""
        await message.reply(f"✅ Кик{sl}")

@router.message(Command("getwarn"))
async def cmd_getwarn(message: Message):
    if not in_group(message): return
    role = await check_role(message, "getwarn")
    if role < 0: return
    args = get_args(message)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /getwarn @user")
    info = await get_user_info(target)
    text = f"🔍 <b>Варны/муты</b>\n👤 {info['full_name']} (<code>{target}</code>)\n\n"
    found = False
    for c in await db.get_all_chat_ids():
        wi = await db.get_warn_info(target, c)
        if wi and wi["count"] > 0:
            ct = await db.get_chat_title(c)
            text += f"⚠️ {wi['count']}/{MAX_WARNS} — {ct}\n"
            found = True
    for c in await db.get_all_chat_ids():
        mi = await db.get_mute_info(target, c)
        if mi:
            ct = await db.get_chat_title(c)
            until = mi.get("until", 0)
            end = fmt_ts(until) if until and until > int(time.time()) else ("навсегда" if not until else "истёк")
            text += f"🔇 Мут — {ct} до {end}\n"
            found = True
    if not found: text += "✅ Чисто"
    await message.answer(text, parse_mode="HTML")

@router.message(Command("warnlist"))
async def cmd_warnlist(message: Message):
    if not in_group(message): return
    role = await check_role(message, "warnlist")
    if role < 0: return
    args = get_args(message)
    page = 0
    for a in args[1:]:
        if a.isdigit(): page = max(0, int(a) - 1)
    chat_id = message.chat.id if not in_staff(message) else 0
    rows, total = await db.get_all_warns_paginated(page, PER_PAGE, chat_id)
    tp = max(1, math.ceil(total / PER_PAGE))
    if not rows: return await message.answer("⚠️ <b>Варны</b>\n\nПусто.", parse_mode="HTML")
    text = f"⚠️ <b>Варны</b> — стр. {page+1}/{tp}\n\n"
    for i, row in enumerate(rows, start=page*PER_PAGE+1):
        info = await get_user_info(row["user_id"])
        text += f"<b>{i}.</b> {info['full_name']} — <code>{row['user_id']}</code>\n    {row['count']}/{MAX_WARNS} | {row.get('reason','—')}\n\n"
    text += f"Всего: {total}"
    if tp > 1: text += f"\n/warnlist {page+2}"
    await message.answer(text, parse_mode="HTML")

# =============================================================================
# 3+: /reg
# =============================================================================

@router.message(Command("reg"))
async def cmd_reg(message: Message):
    if not in_group(message): return
    role = await check_role(message, "reg")
    if role < 0: return
    args = get_args(message)
    target = await parse_user(message, args)
    if not target: target = message.from_user.id if message.from_user else None
    if not target: return await message.reply("❌ /reg @user")
    info = await get_user_info(target)
    regs = await db.get_user_reg_all(target)
    if not regs: return await message.reply(f"ℹ️ Нет данных")
    text = f"📅 <b>Регистрация</b>\n👤 {info['full_name']}\n\n"
    for c, reg_at in regs:
        ct = await db.get_chat_title(c)
        text += f"💬 {ct}: {fmt_ts(reg_at)}\n"
    await message.reply(text, parse_mode="HTML")

# =============================================================================
# 4+: banlist, ro, unro, quiet, setnick, removenick, getnick, nlist, online, onlinelist
# =============================================================================

@router.message(Command("banlist"))
async def cmd_banlist(message: Message):
    if not in_group(message): return
    role = await check_role(message, "banlist")
    if role < 0: return
    args = get_args(message)
    mode, page = "chat", 0
    for a in args[1:]:
        if a == "global": mode = "global"
        elif a.isdigit(): page = max(0, int(a) - 1)
    chat_id = message.chat.id if mode == "chat" and not in_staff(message) else 0
    if mode == "global":
        rows, total = await db.get_all_global_bans_paginated(page, PER_PAGE)
        title = "🌐 <b>Глоб. баны</b>"
    else:
        rows, total = await db.get_all_bans_paginated(page, PER_PAGE, chat_id)
        title = "💬 <b>Баны</b>"
    tp = max(1, math.ceil(total / PER_PAGE))
    if not rows: return await message.answer(f"{title}\nПусто. /banlist global", parse_mode="HTML")
    text = f"{title} — стр. {page+1}/{tp}\n\n"
    for i, row in enumerate(rows, start=page*PER_PAGE+1):
        info = await get_user_info(row["user_id"])
        until = row.get("until", 0)
        end = fmt_ts(until) if until and until > int(time.time()) else ("истёк" if until else "навсегда")
        text += f"<b>{i}.</b> {info['full_name']} — <code>{row['user_id']}</code>\n    {row.get('reason','—')} | {end}\n\n"
    text += f"Всего: {total}"
    await message.answer(text, parse_mode="HTML")

@router.message(Command("ro"))
async def cmd_ro(message: Message):
    if not in_group(message) or in_staff(message): return
    role = await check_role(message, "ro")
    if role < 0: return
    await db.set_ro_mode(message.chat.id, True)
    await message.answer("👁 RO включён!")

@router.message(Command("unro"))
async def cmd_unro(message: Message):
    if not in_group(message) or in_staff(message): return
    role = await check_role(message, "unro")
    if role < 0: return
    await db.set_ro_mode(message.chat.id, False)
    await message.answer("✍️ RO выключен!")

@router.message(Command("quiet"))
async def cmd_quiet(message: Message):
    if not in_group(message) or in_staff(message): return
    role = await check_role(message, "quiet")
    if role < 0: return
    is_q = await db.is_quiet_mode(message.chat.id)
    await db.set_quiet_mode(message.chat.id, not is_q)
    if not is_q:
        try: await bot.set_chat_permissions(message.chat.id, muted_perms())
        except Exception as e: logger.error(f"quiet: {e}")
        await message.answer("🔇 Тишина!")
    else:
        try: await bot.set_chat_permissions(message.chat.id, full_perms())
        except Exception as e: logger.error(f"quiet: {e}")
        await message.answer("🔊 Тишина снята!")

@router.message(Command("setnick"))
async def cmd_setnick(message: Message):
    if not in_group(message): return
    role = await check_role(message, "setnick")
    if role < 0: return
    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)
    if not target or len(args) < 3: return await message.reply("❌ /setnick @user Ник")
    nick = args[2]
    if in_staff(message):
        # В стафф-чате ставим во все чаты
        chat_ids = [c for c in await db.get_all_chat_ids() if c != STAFF_CHAT_ID]
        await db.set_nick_all(target, nick, chat_ids)
        await message.reply(f"📝 Ник «{nick}» → все чаты ({len(chat_ids)})")
    else:
        await db.set_nick(target, message.chat.id, nick)
        await message.reply(f"📝 Ник: {nick}")

@router.message(Command("removenick"))
async def cmd_removenick(message: Message):
    if not in_group(message): return
    role = await check_role(message, "removenick")
    if role < 0: return
    args = get_args(message)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /removenick @user")
    if in_staff(message):
        await db.remove_nick_all(target)
        await message.reply("✅ Ник удалён из всех чатов")
    else:
        nick = await db.get_nick(target, message.chat.id)
        if not nick: return await message.reply("ℹ️ Ник не установлен")
        await db.remove_nick(target, message.chat.id)
        await message.reply(f"✅ Ник «{nick}» удалён")

@router.message(Command("getnick"))
async def cmd_getnick(message: Message):
    if not in_group(message): return
    role = await check_role(message, "getnick")
    if role < 0: return
    args = get_args(message)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /getnick @user")
    info = await get_user_info(target)
    # Ищем ник в текущем чате или любом
    nick = await db.get_nick(target, message.chat.id) if not in_staff(message) else None
    if not nick:
        # Ищем в любом чате
        for c in await db.get_all_chat_ids():
            nick = await db.get_nick(target, c)
            if nick: break
    if nick:
        await message.reply(f"📝 Ник: <b>{nick}</b>\n👤 {info['full_name']} (<code>{target}</code>)", parse_mode="HTML")
    else:
        await message.reply(f"ℹ️ Ник не установлен для {info['full_name']}", parse_mode="HTML")

@router.message(Command("nlist"))
async def cmd_nlist(message: Message):
    if not in_group(message): return
    role = await check_role(message, "nlist")
    if role < 0: return
    # Показываем ники текущего чата, или всех если стафф
    if in_staff(message):
        text = "📝 <b>Ники (все чаты)</b>\n\n"
        found = False
        for c in await db.get_all_chat_ids():
            if c == STAFF_CHAT_ID: continue
            nicks = await db.get_all_nicks(c)
            if nicks:
                ct = await db.get_chat_title(c)
                text += f"<b>{ct}:</b>\n"
                for uid, nick in nicks:
                    text += f"  • <b>{nick}</b> — {await mention(uid)}\n"
                text += "\n"
                found = True
        if not found: return await message.reply("ℹ️ Пусто")
    else:
        nicks = await db.get_all_nicks(message.chat.id)
        if not nicks: return await message.reply("ℹ️ Пусто")
        text = "📝 <b>Ники</b>\n\n"
        for uid, nick in nicks:
            text += f"• <b>{nick}</b> — {await mention(uid)} (<code>{uid}</code>)\n"
    await message.answer(text, parse_mode="HTML")

@router.message(Command("online"))
async def cmd_online(message: Message):
    if not in_group(message): return
    role = await check_role(message, "online")
    if role < 0: return
    args = get_args(message, maxsplit=1)
    reason = args[1] if len(args) > 1 else "Проверка"
    try:
        count = await bot.get_chat_member_count(message.chat.id)
        await message.reply(f"📢 <b>{reason}</b>\n👥 Участников: {count}", parse_mode="HTML")
    except Exception as e:
        await message.reply(f"❌ {e}")

@router.message(Command("onlinelist"))
async def cmd_onlinelist(message: Message):
    if not in_group(message): return
    role = await check_role(message, "onlinelist")
    if role < 0: return
    try:
        count = await bot.get_chat_member_count(message.chat.id)
        await message.reply(f"👥 Участников: <b>{count}</b>\nℹ️ Telegram не даёт список онлайн.", parse_mode="HTML")
    except Exception as e:
        await message.reply(f"❌ {e}")

# =============================================================================
# 5+: ban, unban, getban, getacc, banwords, filter, antiflood, welcometext, clear
# =============================================================================

@router.message(Command("ban"))
async def cmd_ban(message: Message):
    if not in_group(message): return
    role = await check_role(message, "ban")
    if role < 0: return
    args = get_args(message, maxsplit=3)
    args, silent = extract_silent(args)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /ban @user 7d [причина] [--silent]")
    tr = await get_role(target)
    if tr > 0 and tr >= role: return await message.reply("❌ Нельзя: роль цели ≥ вашей")
    dur_arg = args[2] if len(args) > 2 else "0"
    seconds = parse_duration(dur_arg)
    if seconds is None:
        seconds = 0
        reason = " ".join(args[2:]) if len(args) > 2 else "Бан"
    else:
        reason = args[3] if len(args) > 3 else "Бан"
    cid = await caller_id(message)
    if in_staff(message):
        key = f"b:{cid}:{target}:{int(time.time())}"
        await db.cache_action(key, json.dumps({"t":target,"c":cid,"r":reason,"s":seconds,"a":"ban","silent":silent}))
        kb = await build_chat_selector(key)
        sl = " 🔕" if silent else ""
        await message.reply(f"🚫 Бан: {await mention(target)} {fmt_dur(seconds)}{sl}\n{reason}\nВыберите чат:", parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await apply_ban(target, [message.chat.id], cid, reason, seconds, silent)
        sl = " (тихо 🔕)" if silent else ""
        await message.reply(f"✅ Бан{sl}")

@router.message(Command("unban"))
async def cmd_unban(message: Message):
    if not in_group(message): return
    role = await check_role(message, "unban")
    if role < 0: return
    args = get_args(message)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /unban @user")
    cid = await caller_id(message)
    if in_staff(message):
        key = f"ub:{cid}:{target}:{int(time.time())}"
        await db.cache_action(key, json.dumps({"t":target,"c":cid,"a":"unban"}))
        kb = await build_chat_selector(key)
        await message.reply(f"Разбан: {await mention(target)}\nВыберите чат:", parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await apply_unban(target, [message.chat.id], cid)
        await message.reply("✅ Разбан")

@router.message(Command("getban"))
async def cmd_getban(message: Message):
    if not in_group(message): return
    role = await check_role(message, "getban")
    if role < 0: return
    args = get_args(message)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /getban @user")
    info = await get_user_info(target)
    text = f"🔍 <b>Баны</b>\n👤 {info['full_name']} (<code>{target}</code>)\n\n"
    found = False
    gb = await db.get_global_ban_info(target)
    if gb:
        text += f"🌐 <b>Глоб. бан</b>\n  {fmt_ts(gb.get('banned_at',0))} | {gb.get('reason','—')}\n\n"
        found = True
    for c in await db.get_all_chat_ids():
        ban = await db.get_ban_info(target, c)
        if ban:
            ct = await db.get_chat_title(c)
            until = ban.get("until",0)
            end = fmt_ts(until) if until and until > int(time.time()) else ("навсегда" if not until else "истёк")
            text += f"🚫 {ct} — до {end}\n  {ban.get('reason','—')}\n\n"
            found = True
    if not found: text += "✅ Банов нет"
    await message.answer(text, parse_mode="HTML")

@router.message(Command("getacc"))
async def cmd_getacc(message: Message):
    if not in_group(message): return
    role = await check_role(message, "getacc")
    if role < 0: return
    args = get_args(message, maxsplit=1)
    if len(args) < 2: return await message.reply("❌ /getacc <имя>")
    name = args[1]
    cid = message.chat.id if not in_staff(message) else 0
    uid = None
    if cid: uid = await db.get_user_by_nick(name, cid)
    if not uid: uid = await db.get_user_by_nick_any_chat(name)
    if not uid: uid = await resolve_username(name)
    if not uid: return await message.reply(f"❌ «{name}» не найден")
    info = await get_user_info(uid)
    r = await get_role(uid, cid) if cid else await get_role(uid)
    mc = await db.get_message_count(uid)
    text = f"🔍 <b>Аккаунт</b>\n\n👤 {info['full_name']}\n🆔 <code>{uid}</code>\n"
    if info["username"]: text += f"📎 @{info['username']}\n"
    text += f"⭐ {ROLE_NAMES.get(r,'?')} ({r})\n📨 Сообщений: {mc}"
    await message.reply(text, parse_mode="HTML")

@router.message(Command("banwords"))
async def cmd_banwords(message: Message):
    if not in_group(message) or in_staff(message): return
    role = await check_role(message, "banwords")
    if role < 0: return
    args = get_args(message, maxsplit=2)
    if len(args) < 2: return await message.reply("❌ /banwords add|del|list [слово]")
    sub = args[1].lower()
    cid = message.chat.id
    if sub == "list":
        words = await db.get_banwords(cid)
        if not words: return await message.reply("ℹ️ Пусто")
        return await message.reply("🚫 <b>Слова:</b>\n" + "\n".join(f"• {w}" for w in words), parse_mode="HTML")
    if len(args) < 3: return await message.reply("❌ Укажите слово")
    word = args[2].lower()
    if sub == "add":
        ok = await db.add_banword(cid, word)
        await message.reply(f"✅ «{word}»" if ok else f"ℹ️ Уже есть")
    elif sub in ("del","rm","remove","delete"):
        ok = await db.remove_banword(cid, word)
        await message.reply(f"✅ Удалено" if ok else f"ℹ️ Не найдено")

@router.message(Command("filter"))
async def cmd_filter(message: Message):
    if not in_group(message) or in_staff(message): return
    role = await check_role(message, "filter")
    if role < 0: return
    is_on = await db.is_filter(message.chat.id)
    await db.set_filter(message.chat.id, not is_on)
    await message.reply("✅ Фильтр вкл" if not is_on else "❌ Фильтр выкл")

@router.message(Command("antiflood"))
async def cmd_antiflood(message: Message):
    if not in_group(message) or in_staff(message): return
    role = await check_role(message, "antiflood")
    if role < 0: return
    is_on = await db.is_antiflood(message.chat.id)
    await db.set_antiflood(message.chat.id, not is_on)
    await message.reply("✅ Антифлуд вкл" if not is_on else "❌ Антифлуд выкл")

@router.message(Command("welcometext"))
async def cmd_welcometext(message: Message):
    if not in_group(message) or in_staff(message): return
    role = await check_role(message, "welcometext")
    if role < 0: return
    args = get_args(message, maxsplit=1)
    if len(args) < 2:
        current = await db.get_welcome(message.chat.id)
        if current: return await message.reply(f"📝 Приветствие:\n{current}\n\n/welcometext off")
        return await message.reply("ℹ️ Не установлено.\n/welcometext <текст>\n{user} = имя")
    text = args[1]
    if text.lower() in ("off","выкл","0","нет"):
        await db.set_welcome(message.chat.id, "")
        return await message.reply("✅ Выключено")
    await db.set_welcome(message.chat.id, text)
    await message.reply(f"✅ Приветствие:\n{text}")

@router.message(Command("clear"))
async def cmd_clear(message: Message):
    if not in_group(message): return
    role = await check_role(message, "clear")
    if role < 0: return
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
        st = await message.answer(f"🧹 {deleted}/{count}")
        await asyncio.sleep(3)
        await st.delete()
        await message.delete()
    except Exception: pass
    await log_action("ОЧИСТКА", 0, await caller_id(message), f"{deleted} сообщений", chat_id=message.chat.id)

# =============================================================================
# 7+: gban, ungban, setrole, removerole, sremoverole, allsetnick, allremnick
# =============================================================================

@router.message(Command("gban"))
async def cmd_gban(message: Message):
    if not in_group(message): return
    role = await check_role(message, "gban")
    if role < 0: return
    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /gban @user [причина]")
    tr = await get_role(target)
    if tr > 0 and tr >= role: return await message.reply(f"❌ Роль цели: {tr}")
    if tr > 0: return await message.reply("⚠️ Сначала /removerole")
    reason = args[2] if len(args) > 2 else "Глобальный бан"
    cid = await caller_id(message)
    await db.add_global_ban(target, cid, reason)
    ok, fail = 0, 0
    for c in await db.get_all_chat_ids():
        try:
            await bot.ban_chat_member(c, target)
            await db.add_ban(target, c, cid, "Глобальный бан")
            ok += 1
        except Exception: fail += 1
        await asyncio.sleep(0.1)
    name = await mention(target)
    result = f"🌐 Глобальный бан!\n{name} — <code>{target}</code>\n{reason}\n✅ {ok} чатов"
    if fail: result += f" | ⚠️ {fail} неудач"
    await message.reply(result, parse_mode="HTML")
    if STAFF_CHAT_ID and GBAN_TOPIC_ID:
        try:
            ci = await get_user_info(cid)
            await bot.send_message(STAFF_CHAT_ID, f"🌐 <b>ГЛОБ. БАН</b>\n{name} (<code>{target}</code>)\n📝 {reason}\n👮 {ci['full_name']}\n✅ {ok} чатов | 🕐 {now_str()}", parse_mode="HTML", message_thread_id=GBAN_TOPIC_ID)
        except Exception: pass
    await log_action("ГЛОБ. БАН", target, cid, reason, 0)
    await notify_dm(target, "Глобальная блокировка", reason, 0, cid)

@router.message(Command("ungban"))
async def cmd_ungban(message: Message):
    if not in_group(message): return
    role = await check_role(message, "ungban")
    if role < 0: return
    args = get_args(message)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /ungban @user")
    if not await db.is_globally_banned(target): return await message.reply("ℹ️ Нет глоб. бана")
    await db.remove_global_ban(target)
    ok = 0
    for c in await db.get_all_chat_ids():
        try:
            await bot.unban_chat_member(c, target, only_if_banned=True)
            await db.remove_ban(target, c)
            ok += 1
        except Exception: pass
        await asyncio.sleep(0.1)
    name = await mention(target)
    await message.reply(f"✅ Глоб. бан снят! {name}\n{ok} чатов", parse_mode="HTML")
    await log_action("СНЯТИЕ ГЛОБ. БАНА", target, await caller_id(message))
    if STAFF_CHAT_ID and GBAN_TOPIC_ID:
        try: await bot.send_message(STAFF_CHAT_ID, f"✅ <b>СНЯТИЕ ГЛОБ. БАНА</b>\n{name} (<code>{target}</code>)\n🕐 {now_str()}", parse_mode="HTML", message_thread_id=GBAN_TOPIC_ID)
        except Exception: pass

@router.message(Command("setrole"))
async def cmd_setrole(message: Message):
    cr = await check_role(message, "setrole")
    if cr < 0: return
    args = get_args(message)
    if len(args) < 3:
        roles_text = "\n".join(f"  {k}: {v}" for k, v in ROLE_NAMES.items())
        return await message.reply(f"/setrole @user ЧИСЛО\n\n{roles_text}")
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ Не найден")
    try:
        nr = int(args[2])
        if not (0 <= nr <= 10): return await message.reply("❌ 0-10")
    except ValueError: return await message.reply("❌ 0-10")
    tr = await get_role(target)
    if nr >= cr: return await message.reply(f"❌ Нельзя ≥ вашей ({cr})")
    if tr >= cr: return await message.reply("❌ Нельзя менять")
    await db.set_global_role(target, nr)
    name = await mention(target)
    await message.reply(f"⭐ {name}: {ROLE_NAMES.get(tr,'?')} → {ROLE_NAMES.get(nr,'?')} ({nr})", parse_mode="HTML")
    await log_action("СМЕНА РОЛИ", target, await caller_id(message), f"{tr} → {nr}")

@router.message(Command("removerole"))
async def cmd_removerole(message: Message):
    cr = await check_role(message, "removerole")
    if cr < 0: return
    args = get_args(message)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /removerole @user")
    tr = await get_role(target)
    if tr >= cr: return await message.reply("❌ Нельзя")
    if tr == 0: return await message.reply("ℹ️ Нет роли")
    await db.set_global_role(target, 0)
    await message.reply(f"✅ Роль снята! (была: {ROLE_NAMES.get(tr,'?')})", parse_mode="HTML")
    await log_action("СНЯТИЕ РОЛИ", target, await caller_id(message), f"Была: {tr}")

@router.message(Command("sremoverole"))
async def cmd_sremoverole(message: Message):
    cr = await check_role(message, "sremoverole")
    if cr < 0: return
    args = get_args(message)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /sremoverole @user")
    tr = await get_role(target)
    if tr >= cr: return await message.reply("❌ Нельзя")
    if tr == 0: return await message.reply("ℹ️ Нет роли")
    await db.remove_all_user_roles(target)
    await message.reply(f"✅ Роль снята везде! (была: {ROLE_NAMES.get(tr,'?')})", parse_mode="HTML")
    await log_action("СНЯТИЕ РОЛИ (ВСЕ)", target, await caller_id(message), f"Была: {tr}")

@router.message(Command("allsetnick"))
async def cmd_allsetnick(message: Message):
    role = await check_role(message, "allsetnick")
    if role < 0: return
    args = get_args(message, maxsplit=2)
    target = await parse_user(message, args)
    if not target or len(args) < 3: return await message.reply("❌ /allsetnick @user Ник")
    nick = args[2]
    chat_ids = [c for c in await db.get_all_chat_ids() if c != STAFF_CHAT_ID]
    await db.set_nick_all(target, nick, chat_ids)
    await message.reply(f"📝 «{nick}» → {len(chat_ids)} чатов")

@router.message(Command("allremnick"))
async def cmd_allremnick(message: Message):
    role = await check_role(message, "allremnick")
    if role < 0: return
    args = get_args(message)
    target = await parse_user(message, args)
    if not target: return await message.reply("❌ /allremnick @user")
    await db.remove_nick_all(target)
    await message.reply(f"✅ Ник {await mention(target)} удалён везде", parse_mode="HTML")

# =============================================================================
# CALLBACKS
# =============================================================================

@router.callback_query(F.data.startswith("cs:"))
async def cb_chat_select(call: CallbackQuery):
    parts = call.data.split(":", 2)
    if len(parts) < 3: return await call.answer("❌")
    action_key = parts[1]
    chat_part = parts[2]
    cached = await db.get_cached_action(action_key)
    if not cached:
        try: await call.message.edit_text("⏳ Устарело.")
        except Exception: pass
        return await call.answer()
    data = json.loads(cached)
    target, cid_val, action = data["t"], data["c"], data["a"]
    reason, seconds, silent = data.get("r",""), data.get("s",0), data.get("silent",False)
    if call.from_user.id != cid_val and cid_val != 0:
        return await call.answer("❌ Не ваше!", show_alert=True)
    if chat_part == "all":
        chat_ids = [c for c in await db.get_all_chat_ids() if c != STAFF_CHAT_ID]
    else:
        chat_ids = [int(chat_part)]
    name = await mention(target)
    sl = " 🔕" if silent else ""
    result = ""
    if action == "warn":
        await apply_warn(target, chat_ids, cid_val, reason, silent)
        result = f"✅ Варн: {name}{sl}"
    elif action == "unwarn":
        await apply_unwarn(target, chat_ids, cid_val)
        result = f"✅ Варн снят: {name}"
    elif action == "mute":
        await apply_mute(target, chat_ids, cid_val, reason, seconds, silent)
        result = f"✅ Мут: {name} {fmt_dur(seconds)}{sl}"
    elif action == "unmute":
        await apply_unmute(target, chat_ids, cid_val)
        result = f"✅ Размут: {name}"
    elif action == "ban":
        await apply_ban(target, chat_ids, cid_val, reason, seconds, silent)
        result = f"✅ Бан: {name} {fmt_dur(seconds)}{sl}"
    elif action == "unban":
        await apply_unban(target, chat_ids, cid_val)
        result = f"✅ Разбан: {name}"
    elif action == "kick":
        await apply_kick(target, chat_ids, cid_val, reason, silent)
        result = f"✅ Кик: {name}{sl}"
    cn = ", ".join([await db.get_chat_title(c) for c in chat_ids]) if chat_part != "all" else "все чаты"
    result += f"\n💬 {cn}"
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
            await bot.send_message(cid, f"🚫 {await mention(uid)} — глоб. бан!", parse_mode="HTML")
        except Exception: pass
        return
    welcome = await db.get_welcome(cid)
    if welcome:
        name = event.new_chat_member.user.full_name or ""
        try:
            await bot.send_message(cid, welcome.replace("{user}", name))
        except Exception as e:
            logger.error(f"welcome (chat_member): {e}")

@router.message(F.new_chat_members)
async def on_new_chat_members(message: Message):
    """Fallback: обработка new_chat_members (когда chat_member update не приходит)"""
    if not message.new_chat_members: return
    cid = message.chat.id
    for member in message.new_chat_members:
        if member.is_bot: continue
        uid = member.id
        if member.username:
            await db.cache_username(uid, member.username)
        await db.register_user(uid, cid)
        if await db.is_globally_banned(uid):
            try:
                await bot.ban_chat_member(cid, uid)
                await bot.send_message(cid, f"🚫 {await mention(uid)} — глоб. бан!", parse_mode="HTML")
            except Exception: pass
            continue
        welcome = await db.get_welcome(cid)
        if welcome:
            name = member.full_name or ""
            try:
                await bot.send_message(cid, welcome.replace("{user}", name))
            except Exception as e:
                logger.error(f"welcome (new_chat_members): {e}")

@router.message(F.text)
async def on_message(message: Message):
    if message.chat.type == ChatType.PRIVATE: return
    if not message.from_user: return
    uid = message.from_user.id
    cid = message.chat.id
    if message.from_user.username:
        await db.cache_username(uid, message.from_user.username)
    await db.register_user(uid, cid)
    await db.increment_message_count(uid, cid)
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
        is_spam = await db.check_spam(uid, cid, time.time(), SPAM_INTERVAL)
        if is_spam:
            try:
                until = int(time.time()) + 1800
                await db.add_mute(uid, cid, 0, "Антиспам", until)
                await bot.restrict_chat_member(cid, uid, permissions=muted_perms(), until_date=timedelta(minutes=30))
                await message.delete()
                await bot.send_message(cid, f"🔇 {await mention(uid)} — 30 мин (антиспам)", parse_mode="HTML")
                await notify_dm(uid, "Замучены (антиспам)", "Флуд", 1800, 0)
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
                    await bot.restrict_chat_member(cid, uid, permissions=muted_perms(), until_date=timedelta(minutes=30))
                    await bot.send_message(cid, f"🔇 {await mention(uid)} (запрещённое слово)", parse_mode="HTML")
                except Exception: pass
                return


# =============================================================================
# ЗАПУСК
# =============================================================================

async def register_commands():
    group_cmds = [
        BotCommand(command="help", description="❓ Помощь"),
        BotCommand(command="stats", description="📊 Статистика"),
        BotCommand(command="report", description="🚨 Репорт"),
        BotCommand(command="top", description="🏆 Топ"),
        BotCommand(command="staff", description="👥 Команда"),
        BotCommand(command="warn", description="⚠️ Варн"),
        BotCommand(command="mute", description="🔇 Мут"),
        BotCommand(command="kick", description="👢 Кик"),
        BotCommand(command="ban", description="🚫 Бан"),
        BotCommand(command="gban", description="🌐 Глоб. бан"),
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
        logger.error(f"cmds: {e}")

async def init_staff():
    if not PRESET_STAFF: return
    for uid_str, role in PRESET_STAFF.items():
        try: await db.set_global_role(int(uid_str), role)
        except Exception as e: logger.error(f"staff {uid_str}: {e}")
    logger.info(f"Preset staff: {len(PRESET_STAFF)}")

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
    logger.info(f"Модерация v8.1 — @{me.username} ({BOT_ID})")
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
        except Exception as e:
            logger.warning(f"Стафф: {e}")
    await register_commands()
    asyncio.create_task(periodic_cleanup())
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("✅ Запущен!")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query", "chat_member", "my_chat_member"])

if __name__ == "__main__":
    asyncio.run(main())
