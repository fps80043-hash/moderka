"""
Действия — ConversationHandler логика + колбэки пользователей/чатов/ролей.
"""

import time
import logging
from telegram import Update, ChatPermissions
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

import database as db
from config import (
    MAX_WARNS, INTERFACE_BUTTONS, USERS_PER_PAGE,
)
from utils import (
    parse_duration_text, parse_short_duration, format_duration,
    escape_html, role_name, can_moderate, can_admin,
    format_user_profile, format_user_short,
)
from keyboards import (
    cancel_kb, back_to_main_kb, duration_kb, users_list_kb,
    chat_manage_kb, chats_list_kb,
)
from staff_log import log_punishment, log_report

logger = logging.getLogger(__name__)

AWAIT_TARGET = 0
AWAIT_DURATION = 1
AWAIT_REASON = 2
AWAIT_SEARCH = 3
AWAIT_WORD_FILTER = 4
AWAIT_REPORT_USER = 5
AWAIT_REPORT_REASON = 6
AWAIT_ROLE_TARGET = 7


def _action_label(action):
    labels = {
        "ban": "бана", "unban": "разбана", "editban": "изменения бана",
        "globalban": "глобального бана", "warn": "варна", "unwarn": "сброса варнов",
        "mute": "мута", "unmute": "снятия мута", "editmute": "изменения мута",
    }
    return labels.get(action, action)


# ===================== CB: action start =====================

async def cb_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action = q.data.split(":")[1]
    role = await db.get_role(q.from_user.id)
    if not can_moderate(role):
        await q.edit_message_text("⛔ Нет прав.")
        return ConversationHandler.END
    context.user_data["action"] = action
    await q.edit_message_text(f"Введи ID или @username для {_action_label(action)}:",
                              reply_markup=cancel_kb())
    return AWAIT_TARGET


# ===================== Target =====================

async def conv_target_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lstrip("@")
    action = context.user_data.get("action", "")

    target = await db.find_user(text)
    if not target and text.isdigit():
        await db.ensure_user(int(text))
        target = await db.get_user(int(text))
    if not target:
        await update.message.reply_text("❌ Не найден. Ещё раз:", reply_markup=cancel_kb())
        return AWAIT_TARGET

    context.user_data["target_id"] = target["user_id"]
    context.user_data["target_name"] = target.get("first_name") or str(target["user_id"])

    if action in ("unban", "unwarn", "unmute"):
        return await _do_simple(update, context, action)
    if action in ("warn", "globalban"):
        await update.message.reply_text("Причина (или <code>-</code>):",
                                        reply_markup=cancel_kb(), parse_mode=ParseMode.HTML)
        return AWAIT_REASON

    iface = await db.get_interface(update.effective_user.id)
    if iface == INTERFACE_BUTTONS:
        await update.message.reply_text("Выбери срок:", reply_markup=duration_kb(action))
        return AWAIT_DURATION
    else:
        await update.message.reply_text(
            "Срок (<code>5m 2h 1d 7d 0</code>=навсегда):", parse_mode=ParseMode.HTML)
        return AWAIT_DURATION


async def _do_simple(update, context, action):
    tid = context.user_data["target_id"]
    tname = context.user_data.get("target_name", str(tid))
    iface = await db.get_interface(update.effective_user.id)
    kb = back_to_main_kb() if iface == INTERFACE_BUTTONS else None
    issuer = update.effective_user

    if action == "unban":
        await db.remove_ban(tid)
        for c in await db.get_all_chats():
            try:
                await context.bot.unban_chat_member(c["chat_id"], tid, only_if_banned=True)
            except:
                pass
        await update.message.reply_text(f"✅ Бан снят: {escape_html(tname)}",
                                        reply_markup=kb, parse_mode=ParseMode.HTML)
        await log_punishment(context.bot, "unban", tid, tname, issuer.id, issuer.first_name or "")

    elif action == "unwarn":
        await db.reset_warns(tid)
        await update.message.reply_text(f"✅ Варны сброшены: {escape_html(tname)}",
                                        reply_markup=kb, parse_mode=ParseMode.HTML)
        await log_punishment(context.bot, "unwarn", tid, tname, issuer.id, issuer.first_name or "")

    elif action == "unmute":
        await db.remove_mute(tid)
        for c in await db.get_all_chats():
            try:
                perms = ChatPermissions(can_send_messages=True, can_send_media_messages=True,
                                        can_send_polls=True, can_send_other_messages=True,
                                        can_add_web_page_previews=True, can_invite_users=True)
                await context.bot.restrict_chat_member(c["chat_id"], tid, permissions=perms)
            except:
                pass
        await update.message.reply_text(f"🔊 Мут снят: {escape_html(tname)}",
                                        reply_markup=kb, parse_mode=ParseMode.HTML)
        await log_punishment(context.bot, "unmute", tid, tname, issuer.id, issuer.first_name or "")

    context.user_data.clear()
    return ConversationHandler.END


# ===================== Duration =====================

async def conv_duration_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["until"] = parse_short_duration(update.message.text.strip())
    await update.message.reply_text("Причина (или <code>-</code>):",
                                    reply_markup=cancel_kb(), parse_mode=ParseMode.HTML)
    return AWAIT_REASON


async def cb_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split(":", 2)
    context.user_data["action"] = parts[1]
    context.user_data["until"] = parse_duration_text(parts[2])
    await q.edit_message_text("Причина (или <code>-</code>):",
                              reply_markup=cancel_kb(), parse_mode=ParseMode.HTML)
    return AWAIT_REASON


# ===================== Reason → Execute =====================

async def conv_reason_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text.strip()
    if reason == "-":
        reason = ""
    context.user_data["reason"] = reason
    action = context.user_data.get("action", "")
    tid = context.user_data.get("target_id")
    tname = context.user_data.get("target_name", str(tid))
    until = context.user_data.get("until", 0)
    issuer = update.effective_user
    iface = await db.get_interface(issuer.id)
    kb = back_to_main_kb() if iface == INTERFACE_BUTTONS else None
    dur_str = format_duration(until) if until is not None else ""
    reason_str = f"\nПричина: {escape_html(reason)}" if reason else ""

    if action == "ban":
        await db.set_ban(tid, until, reason, issuer.id)
        kicked = 0
        for c in await db.get_all_chats():
            try:
                await context.bot.ban_chat_member(c["chat_id"], tid,
                                                  until_date=int(until) if until > 0 else 0)
                kicked += 1
            except Exception as e:
                logger.warning(f"Ban fail {tid} in {c['chat_id']}: {e}")
        await update.message.reply_text(
            f"🔨 <b>Бан</b>\n{escape_html(tname)} (<code>{tid}</code>)\n"
            f"Срок: {dur_str}{reason_str}\nЧатов: {kicked}",
            reply_markup=kb, parse_mode=ParseMode.HTML)
        await log_punishment(context.bot, "ban", tid, tname, issuer.id,
                             issuer.first_name or "", dur_str, reason)

    elif action == "editban":
        await db.update_ban_duration(tid, until)
        await update.message.reply_text(
            f"🕐 Бан изменён: {escape_html(tname)} — {dur_str}",
            reply_markup=kb, parse_mode=ParseMode.HTML)

    elif action == "globalban":
        await db.add_global_ban(tid, reason, issuer.id)
        for c in await db.get_all_chats():
            try:
                await context.bot.ban_chat_member(c["chat_id"], tid)
            except:
                pass
        await update.message.reply_text(
            f"🌍 <b>Глобальный бан</b>\n{escape_html(tname)} (<code>{tid}</code>){reason_str}",
            reply_markup=kb, parse_mode=ParseMode.HTML)
        await log_punishment(context.bot, "globalban", tid, tname, issuer.id,
                             issuer.first_name or "", reason=reason)

    elif action == "warn":
        warns = await db.add_warn(tid, reason, issuer.id)
        text = f"⚠️ <b>Варн</b>\n{escape_html(tname)} — {warns}/{MAX_WARNS}{reason_str}"
        if warns >= MAX_WARNS:
            await db.set_ban(tid, 0, f"Автобан: {warns} варнов", issuer.id)
            for c in await db.get_all_chats():
                try:
                    await context.bot.ban_chat_member(c["chat_id"], tid)
                except:
                    pass
            text += "\n\n🔨 <b>Автобан!</b>"
        await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        await log_punishment(context.bot, "warn", tid, tname, issuer.id,
                             issuer.first_name or "", reason=reason)

    elif action == "mute":
        await db.set_mute(tid, until, reason, issuer.id)
        for c in await db.get_all_chats():
            try:
                await context.bot.restrict_chat_member(
                    c["chat_id"], tid,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=int(until) if until > 0 else 0)
            except Exception as e:
                logger.warning(f"Mute fail: {e}")
        await update.message.reply_text(
            f"🔇 <b>Мут</b>\n{escape_html(tname)} (<code>{tid}</code>)\n"
            f"Срок: {dur_str}{reason_str}",
            reply_markup=kb, parse_mode=ParseMode.HTML)
        await log_punishment(context.bot, "mute", tid, tname, issuer.id,
                             issuer.first_name or "", dur_str, reason)

    elif action == "editmute":
        await db.update_mute_duration(tid, until)
        await update.message.reply_text(
            f"🕐 Мут изменён: {dur_str}", reply_markup=kb, parse_mode=ParseMode.HTML)

    context.user_data.clear()
    return ConversationHandler.END


# ===================== Users =====================

async def cb_users_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    page = int(q.data.split(":")[2]) if len(q.data.split(":")) > 2 else 0
    total = await db.count_users()
    users = await db.get_all_users(page * USERS_PER_PAGE, USERS_PER_PAGE)
    await q.edit_message_text("👥 <b>Пользователи</b>",
                              reply_markup=users_list_kb(users, page, total), parse_mode=ParseMode.HTML)


async def cb_user_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    tid = int(q.data.split(":")[1])
    u = await db.get_user(tid)
    if u:
        await q.edit_message_text(format_user_profile(u),
                                  reply_markup=back_to_main_kb(), parse_mode=ParseMode.HTML)
    else:
        await q.edit_message_text("Не найден.", reply_markup=back_to_main_kb())


async def cb_users_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["action"] = "search"
    await q.edit_message_text("🔍 Введи ID или @username:", reply_markup=cancel_kb())
    return AWAIT_SEARCH


async def conv_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lstrip("@")
    u = await db.find_user(text)
    iface = await db.get_interface(update.effective_user.id)
    kb = back_to_main_kb() if iface == INTERFACE_BUTTONS else None
    if u:
        await update.message.reply_text(format_user_profile(u), reply_markup=kb, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("❌ Не найден.", reply_markup=kb)
    context.user_data.clear()
    return ConversationHandler.END


async def cb_users_online(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    users = await db.get_online_users(300)
    if not users:
        await q.edit_message_text("Нет активных.", reply_markup=back_to_main_kb())
        return
    lines = [f"🟢 <b>Онлайн ({len(users)}):</b>\n"]
    for u in users:
        lines.append(f"• {format_user_short(u)}")
    await q.edit_message_text("\n".join(lines), reply_markup=back_to_main_kb(), parse_mode=ParseMode.HTML)


async def cb_users_staff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    staff = await db.get_staff_users()
    if not staff:
        await q.edit_message_text("Нет стаффа.", reply_markup=back_to_main_kb())
        return
    lines = ["🛡 <b>Стафф:</b>\n"]
    for u in staff:
        status = "🟢" if (time.time() - u.get("last_seen", 0)) < 300 else "⚪"
        lines.append(f"{status} {format_user_short(u)} — {role_name(u['role'])}")
    await q.edit_message_text("\n".join(lines), reply_markup=back_to_main_kb(), parse_mode=ParseMode.HTML)


# ===================== Chats =====================

async def cb_chat_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    cid_str = q.data.split(":")[1]
    if cid_str == "all":
        context.user_data["manage_chat"] = "all"
        await q.edit_message_text("📢 <b>Все чаты</b>",
                                  reply_markup=chat_manage_kb("all"), parse_mode=ParseMode.HTML)
    else:
        cid = int(cid_str)
        context.user_data["manage_chat"] = cid
        ci = await db.get_chat(cid)
        title = ci.get("title", str(cid)) if ci else str(cid)
        await q.edit_message_text(f"💬 <b>{escape_html(title)}</b>",
                                  reply_markup=chat_manage_kb(cid, ci), parse_mode=ParseMode.HTML)


async def cb_chat_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split(":")
    toggle = parts[1]
    cid_str = parts[2]

    chat_ids = [c["chat_id"] for c in await db.get_all_chats()] if cid_str == "all" else [int(cid_str)]

    for cid in chat_ids:
        ci = await db.get_chat(cid)
        if not ci:
            continue
        if toggle == "readonly":
            nv = not bool(ci.get("read_only", 0))
            await db.set_chat_read_only(cid, nv)
            try:
                if nv:
                    perms = ChatPermissions(can_send_messages=False)
                else:
                    perms = ChatPermissions(can_send_messages=True, can_send_media_messages=True,
                                            can_send_polls=True, can_send_other_messages=True,
                                            can_add_web_page_previews=True, can_invite_users=True)
                await context.bot.set_chat_permissions(cid, perms)
            except Exception as e:
                logger.warning(f"Perms fail {cid}: {e}")
        elif toggle == "antispam":
            await db.set_chat_antispam(cid, not bool(ci.get("antispam", 0)))
        elif toggle == "aimod":
            await db.set_chat_ai_moderation(cid, not bool(ci.get("ai_moderation", 0)))

    if cid_str == "all":
        await q.edit_message_text("📢 <b>Обновлено</b>",
                                  reply_markup=chat_manage_kb("all"), parse_mode=ParseMode.HTML)
    else:
        ci = await db.get_chat(int(cid_str))
        title = ci.get("title", cid_str) if ci else cid_str
        await q.edit_message_text(f"💬 <b>{escape_html(title)}</b>",
                                  reply_markup=chat_manage_kb(int(cid_str), ci), parse_mode=ParseMode.HTML)


async def cb_chat_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    cid_str = q.data.split(":")[1]
    context.user_data["filter_chat"] = cid_str
    existing = await db.get_word_filters(int(cid_str)) if cid_str != "all" else []
    text = "📝 <b>Фильтр слов</b>\n\n"
    if existing:
        text += "Текущие: " + ", ".join(f"<code>{w}</code>" for w in existing) + "\n\n"
    text += "Введи слово (или <code>-слово</code> для удаления):"
    await q.edit_message_text(text, reply_markup=cancel_kb(), parse_mode=ParseMode.HTML)
    return AWAIT_WORD_FILTER


async def conv_word_filter_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    cid_str = context.user_data.get("filter_chat", "")
    iface = await db.get_interface(update.effective_user.id)
    kb = back_to_main_kb() if iface == INTERFACE_BUTTONS else None
    chat_ids = [c["chat_id"] for c in await db.get_all_chats()] if cid_str == "all" else [int(cid_str)]

    if text.startswith("-"):
        word = text[1:].strip()
        for cid in chat_ids:
            await db.remove_word_filter(cid, word)
        await update.message.reply_text(f"✅ <code>{escape_html(word)}</code> удалено.",
                                        reply_markup=kb, parse_mode=ParseMode.HTML)
    else:
        for cid in chat_ids:
            await db.add_word_filter(cid, text)
        await update.message.reply_text(f"✅ <code>{escape_html(text)}</code> добавлено.",
                                        reply_markup=kb, parse_mode=ParseMode.HTML)
    context.user_data.clear()
    return ConversationHandler.END


# ===================== Roles =====================

async def cb_set_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    level = int(q.data.split(":")[1])
    context.user_data["action"] = "setrole"
    context.user_data["new_role"] = level
    await q.edit_message_text(f"Введи ID или @username для роли <b>{role_name(level)}</b>:",
                              reply_markup=cancel_kb(), parse_mode=ParseMode.HTML)
    return AWAIT_ROLE_TARGET


async def conv_role_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lstrip("@")
    new_role = context.user_data.get("new_role", 0)
    target = await db.find_user(text)
    if not target and text.isdigit():
        await db.ensure_user(int(text))
        target = await db.get_user(int(text))
    iface = await db.get_interface(update.effective_user.id)
    kb = back_to_main_kb() if iface == INTERFACE_BUTTONS else None
    if not target:
        await update.message.reply_text("❌ Не найден.", reply_markup=kb)
        context.user_data.clear()
        return ConversationHandler.END
    await db.set_role(target["user_id"], new_role)
    name = target.get("first_name") or str(target["user_id"])
    await update.message.reply_text(
        f"✅ <b>{role_name(new_role)}</b> → {escape_html(name)} (<code>{target['user_id']}</code>)",
        reply_markup=kb, parse_mode=ParseMode.HTML)
    context.user_data.clear()
    return ConversationHandler.END


# ===================== Report =====================

async def conv_report_user_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lstrip("@")
    target = await db.find_user(text)
    if not target and text.isdigit():
        await db.ensure_user(int(text))
        target = await db.get_user(int(text))
    if not target:
        await update.message.reply_text("❌ Не найден. Ещё раз:", reply_markup=cancel_kb())
        return AWAIT_REPORT_USER
    context.user_data["report_target"] = target["user_id"]
    await update.message.reply_text("Опиши нарушение:", reply_markup=cancel_kb())
    return AWAIT_REPORT_REASON


async def conv_report_reason_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text.strip()
    tid = context.user_data.get("report_target")
    reporter = update.effective_user
    await db.add_report(reporter.id, tid, reason)
    iface = await db.get_interface(reporter.id)
    kb = back_to_main_kb() if iface == INTERFACE_BUTTONS else None
    await update.message.reply_text("✅ Репорт отправлен!", reply_markup=kb)
    await log_report(context.bot, reporter.id, reporter.first_name or "", tid, reason)
    context.user_data.clear()
    return ConversationHandler.END
