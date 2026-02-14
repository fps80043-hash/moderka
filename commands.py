"""
Текстовые команды и групповой обработчик.
"""

import time
import logging
from telegram import Update, ChatPermissions
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import database as db
from config import (
    INTERFACE_BUTTONS, MAX_WARNS, SUPPORT_LINK,
    SPAM_MESSAGES_COUNT, SPAM_INTERVAL_SECONDS, ANTISPAM_WARN_THRESHOLD,
    USERS_PER_PAGE, MODERATED_CHATS,
)
from utils import (
    parse_short_duration, format_duration, format_user_profile, format_user_short,
    escape_html, role_name, can_moderate, can_admin,
)
from keyboards import back_to_main_kb, users_list_kb, chats_list_kb, settings_kb, cancel_kb
from ai_moderation import analyze_message
from staff_log import log_punishment, log_action

logger = logging.getLogger(__name__)


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await db.ensure_user(user.id, user.username or "", user.first_name or "")
    u = await db.get_user(user.id)
    iface = await db.get_interface(user.id)
    kb = back_to_main_kb() if iface == INTERFACE_BUTTONS else None
    if u:
        await update.message.reply_text(format_user_profile(u), reply_markup=kb, parse_mode=ParseMode.HTML)


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    top = await db.get_top_users(10)
    lines = ["🏆 <b>Топ:</b>\n"]
    for i, u in enumerate(top, 1):
        name = escape_html(u.get("first_name") or u.get("username") or str(u["user_id"]))
        lines.append(f"{i}. {name} — {u['messages_count']}")
    iface = await db.get_interface(update.effective_user.id)
    kb = back_to_main_kb() if iface == INTERFACE_BUTTONS else None
    await update.message.reply_text("\n".join(lines), reply_markup=kb, parse_mode=ParseMode.HTML)


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚙️ <b>Настройки</b>\n\nВыбери интерфейс:",
                                    reply_markup=settings_kb(), parse_mode=ParseMode.HTML)


async def _resolve_target(args, idx=0):
    if not args:
        return None
    text = args[idx].lstrip("@")
    target = await db.find_user(text)
    if not target and text.isdigit():
        await db.ensure_user(int(text))
        target = await db.get_user(int(text))
    return target


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = await db.get_role(update.effective_user.id)
    if not can_moderate(role): return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Формат: /ban &lt;user&gt; &lt;время&gt; [причина]", parse_mode=ParseMode.HTML); return
    target = await _resolve_target(args)
    if not target:
        await update.message.reply_text("❌ Не найден."); return
    until = parse_short_duration(args[1])
    reason = " ".join(args[2:]) if len(args) > 2 else ""
    await db.set_ban(target["user_id"], until, reason, update.effective_user.id)
    for c in await db.get_all_chats():
        try: await context.bot.ban_chat_member(c["chat_id"], target["user_id"],
                                               until_date=int(until) if until > 0 else 0)
        except: pass
    iface = await db.get_interface(update.effective_user.id)
    kb = back_to_main_kb() if iface == INTERFACE_BUTTONS else None
    name = escape_html(target.get("first_name") or str(target["user_id"]))
    await update.message.reply_text(f"🔨 Бан: {name} на {format_duration(until)}",
                                    reply_markup=kb, parse_mode=ParseMode.HTML)
    await log_punishment(context.bot, "ban", target["user_id"], name,
                         update.effective_user.id, update.effective_user.first_name or "",
                         format_duration(until), reason)


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = await db.get_role(update.effective_user.id)
    if not can_moderate(role): return
    target = await _resolve_target(context.args)
    if not target:
        await update.message.reply_text("❌ Не найден."); return
    await db.remove_ban(target["user_id"])
    for c in await db.get_all_chats():
        try: await context.bot.unban_chat_member(c["chat_id"], target["user_id"], only_if_banned=True)
        except: pass
    iface = await db.get_interface(update.effective_user.id)
    kb = back_to_main_kb() if iface == INTERFACE_BUTTONS else None
    await update.message.reply_text(f"✅ Бан снят: <code>{target['user_id']}</code>",
                                    reply_markup=kb, parse_mode=ParseMode.HTML)
    await log_punishment(context.bot, "unban", target["user_id"], "",
                         update.effective_user.id, update.effective_user.first_name or "")


async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = await db.get_role(update.effective_user.id)
    if not can_moderate(role): return
    if not context.args:
        await update.message.reply_text("Формат: /warn &lt;user&gt; [причина]", parse_mode=ParseMode.HTML); return
    target = await _resolve_target(context.args)
    if not target:
        await update.message.reply_text("❌ Не найден."); return
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else ""
    warns = await db.add_warn(target["user_id"], reason, update.effective_user.id)
    name = escape_html(target.get("first_name") or str(target["user_id"]))
    text = f"⚠️ Варн: {name} ({warns}/{MAX_WARNS})"
    if warns >= MAX_WARNS:
        await db.set_ban(target["user_id"], 0, f"Автобан: {warns} варнов", update.effective_user.id)
        for c in await db.get_all_chats():
            try: await context.bot.ban_chat_member(c["chat_id"], target["user_id"])
            except: pass
        text += "\n🔨 Автобан!"
    iface = await db.get_interface(update.effective_user.id)
    kb = back_to_main_kb() if iface == INTERFACE_BUTTONS else None
    await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await log_punishment(context.bot, "warn", target["user_id"], name,
                         update.effective_user.id, update.effective_user.first_name or "", reason=reason)


async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = await db.get_role(update.effective_user.id)
    if not can_moderate(role): return
    target = await _resolve_target(context.args)
    if not target:
        await update.message.reply_text("❌ Не найден."); return
    await db.reset_warns(target["user_id"])
    iface = await db.get_interface(update.effective_user.id)
    kb = back_to_main_kb() if iface == INTERFACE_BUTTONS else None
    await update.message.reply_text(f"✅ Варны сброшены: <code>{target['user_id']}</code>",
                                    reply_markup=kb, parse_mode=ParseMode.HTML)


async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = await db.get_role(update.effective_user.id)
    if not can_moderate(role): return
    if len(context.args) < 2:
        await update.message.reply_text("Формат: /mute &lt;user&gt; &lt;время&gt; [причина]", parse_mode=ParseMode.HTML); return
    target = await _resolve_target(context.args)
    if not target:
        await update.message.reply_text("❌ Не найден."); return
    until = parse_short_duration(context.args[1])
    reason = " ".join(context.args[2:]) if len(context.args) > 2 else ""
    await db.set_mute(target["user_id"], until, reason, update.effective_user.id)
    for c in await db.get_all_chats():
        try:
            await context.bot.restrict_chat_member(c["chat_id"], target["user_id"],
                permissions=ChatPermissions(can_send_messages=False),
                until_date=int(until) if until > 0 else 0)
        except: pass
    iface = await db.get_interface(update.effective_user.id)
    kb = back_to_main_kb() if iface == INTERFACE_BUTTONS else None
    name = escape_html(target.get("first_name") or str(target["user_id"]))
    await update.message.reply_text(f"🔇 Мут: {name} на {format_duration(until)}",
                                    reply_markup=kb, parse_mode=ParseMode.HTML)
    await log_punishment(context.bot, "mute", target["user_id"], name,
                         update.effective_user.id, update.effective_user.first_name or "",
                         format_duration(until), reason)


async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = await db.get_role(update.effective_user.id)
    if not can_moderate(role): return
    target = await _resolve_target(context.args)
    if not target:
        await update.message.reply_text("❌ Не найден."); return
    await db.remove_mute(target["user_id"])
    for c in await db.get_all_chats():
        try:
            perms = ChatPermissions(can_send_messages=True, can_send_media_messages=True,
                                    can_send_polls=True, can_send_other_messages=True,
                                    can_add_web_page_previews=True, can_invite_users=True)
            await context.bot.restrict_chat_member(c["chat_id"], target["user_id"], permissions=perms)
        except: pass
    iface = await db.get_interface(update.effective_user.id)
    kb = back_to_main_kb() if iface == INTERFACE_BUTTONS else None
    await update.message.reply_text(f"🔊 Мут снят: <code>{target['user_id']}</code>",
                                    reply_markup=kb, parse_mode=ParseMode.HTML)


async def cmd_editban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = await db.get_role(update.effective_user.id)
    if not can_moderate(role): return
    if len(context.args) < 2:
        await update.message.reply_text("Формат: /editban &lt;user&gt; &lt;время&gt;", parse_mode=ParseMode.HTML); return
    target = await _resolve_target(context.args)
    if not target:
        await update.message.reply_text("❌ Не найден."); return
    until = parse_short_duration(context.args[1])
    await db.update_ban_duration(target["user_id"], until)
    iface = await db.get_interface(update.effective_user.id)
    kb = back_to_main_kb() if iface == INTERFACE_BUTTONS else None
    await update.message.reply_text(f"🕐 Бан изменён: {format_duration(until)}",
                                    reply_markup=kb, parse_mode=ParseMode.HTML)


async def cmd_editmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = await db.get_role(update.effective_user.id)
    if not can_moderate(role): return
    if len(context.args) < 2:
        await update.message.reply_text("Формат: /editmute &lt;user&gt; &lt;время&gt;", parse_mode=ParseMode.HTML); return
    target = await _resolve_target(context.args)
    if not target:
        await update.message.reply_text("❌ Не найден."); return
    until = parse_short_duration(context.args[1])
    await db.update_mute_duration(target["user_id"], until)
    iface = await db.get_interface(update.effective_user.id)
    kb = back_to_main_kb() if iface == INTERFACE_BUTTONS else None
    await update.message.reply_text(f"🕐 Мут изменён: {format_duration(until)}",
                                    reply_markup=kb, parse_mode=ParseMode.HTML)


async def cmd_globalban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = await db.get_role(update.effective_user.id)
    if not can_moderate(role): return
    if not context.args:
        await update.message.reply_text("Формат: /globalban &lt;user&gt; [причина]", parse_mode=ParseMode.HTML); return
    target = await _resolve_target(context.args)
    if not target:
        await update.message.reply_text("❌ Не найден."); return
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else ""
    await db.add_global_ban(target["user_id"], reason, update.effective_user.id)
    for c in await db.get_all_chats():
        try: await context.bot.ban_chat_member(c["chat_id"], target["user_id"])
        except: pass
    iface = await db.get_interface(update.effective_user.id)
    kb = back_to_main_kb() if iface == INTERFACE_BUTTONS else None
    await update.message.reply_text(f"🌍 Глобальный бан: <code>{target['user_id']}</code>",
                                    reply_markup=kb, parse_mode=ParseMode.HTML)
    await log_punishment(context.bot, "globalban", target["user_id"], "",
                         update.effective_user.id, update.effective_user.first_name or "", reason=reason)


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = await db.get_role(update.effective_user.id)
    if not can_moderate(role): return
    total = await db.count_users()
    users = await db.get_all_users(0, USERS_PER_PAGE)
    iface = await db.get_interface(update.effective_user.id)
    if iface == INTERFACE_BUTTONS:
        await update.message.reply_text("👥", reply_markup=users_list_kb(users, 0, total), parse_mode=ParseMode.HTML)
    else:
        lines = [f"👥 Всего: {total}\n"] + [format_user_short(u) for u in users]
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = await db.get_role(update.effective_user.id)
    if not can_moderate(role): return
    if not context.args:
        await update.message.reply_text("Формат: /find &lt;запрос&gt;", parse_mode=ParseMode.HTML); return
    u = await db.find_user(context.args[0].lstrip("@"))
    iface = await db.get_interface(update.effective_user.id)
    kb = back_to_main_kb() if iface == INTERFACE_BUTTONS else None
    if u:
        await update.message.reply_text(format_user_profile(u), reply_markup=kb, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("❌ Не найден.", reply_markup=kb)


async def cmd_online(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = await db.get_role(update.effective_user.id)
    if not can_moderate(role): return
    users = await db.get_online_users(300)
    lines = [f"🟢 Онлайн ({len(users)}):"] + [f"• {format_user_short(u)}" for u in users]
    iface = await db.get_interface(update.effective_user.id)
    kb = back_to_main_kb() if iface == INTERFACE_BUTTONS else None
    await update.message.reply_text("\n".join(lines) if users else "Никого.", reply_markup=kb, parse_mode=ParseMode.HTML)


async def cmd_staff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = await db.get_role(update.effective_user.id)
    if not can_moderate(role): return
    staff = await db.get_staff_users()
    lines = ["🛡 Стафф:"]
    for u in staff:
        st = "🟢" if (time.time() - u.get("last_seen", 0)) < 300 else "⚪"
        lines.append(f"{st} {format_user_short(u)} — {role_name(u['role'])}")
    iface = await db.get_interface(update.effective_user.id)
    kb = back_to_main_kb() if iface == INTERFACE_BUTTONS else None
    await update.message.reply_text("\n".join(lines) if staff else "Нет.", reply_markup=kb, parse_mode=ParseMode.HTML)


async def cmd_setrole(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = await db.get_role(update.effective_user.id)
    if not can_admin(role): return
    if len(context.args) < 2:
        await update.message.reply_text("Формат: /setrole &lt;user&gt; &lt;уровень&gt;", parse_mode=ParseMode.HTML); return
    try:
        new_role = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Уровень — число."); return
    target = await _resolve_target(context.args)
    if not target:
        await update.message.reply_text("❌ Не найден."); return
    await db.set_role(target["user_id"], new_role)
    iface = await db.get_interface(update.effective_user.id)
    kb = back_to_main_kb() if iface == INTERFACE_BUTTONS else None
    await update.message.reply_text(
        f"✅ {role_name(new_role)} → <code>{target['user_id']}</code>",
        reply_markup=kb, parse_mode=ParseMode.HTML)


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        iface = await db.get_interface(update.effective_user.id)
        if iface == INTERFACE_BUTTONS:
            await update.message.reply_text("📩 Введи ID нарушителя:", reply_markup=cancel_kb())
            return 5  # AWAIT_REPORT_USER
        await update.message.reply_text("Формат: /report &lt;user&gt; &lt;причина&gt;", parse_mode=ParseMode.HTML)
        return
    target = await _resolve_target(context.args)
    if not target:
        await update.message.reply_text("❌ Не найден."); return
    reason = " ".join(context.args[1:])
    await db.add_report(update.effective_user.id, target["user_id"], reason)
    await update.message.reply_text("✅ Репорт отправлен!")
    from staff_log import log_report
    await log_report(context.bot, update.effective_user.id, update.effective_user.first_name or "",
                     target["user_id"], reason)


async def cmd_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = await db.get_role(update.effective_user.id)
    if not can_moderate(role): return
    reports = await db.get_open_reports(10)
    if not reports:
        await update.message.reply_text("Нет репортов."); return
    lines = ["📋 Репорты:\n"]
    for r in reports:
        lines.append(f"#{r['id']} | {r['reported_id']} | {r.get('reason', '—')}")
    iface = await db.get_interface(update.effective_user.id)
    kb = back_to_main_kb() if iface == INTERFACE_BUTTONS else None
    await update.message.reply_text("\n".join(lines), reply_markup=kb, parse_mode=ParseMode.HTML)


async def cmd_chatmod(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = await db.get_role(update.effective_user.id)
    if not can_moderate(role): return
    chats = await db.get_all_chats()
    if not chats:
        await update.message.reply_text("Нет чатов."); return
    await update.message.reply_text("💬 Выбери чат:", reply_markup=chats_list_kb(chats))


# ===================== GROUP HANDLER =====================

async def group_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or not update.effective_user:
        return
    if update.effective_chat.type not in ("group", "supergroup"):
        return

    user = update.effective_user
    chat_id = update.effective_chat.id

    await db.ensure_chat(chat_id, update.effective_chat.title or "")
    await db.ensure_user(user.id, user.username or "", user.first_name or "")
    await db.increment_messages(user.id)

    if await db.is_global_banned(user.id):
        try:
            await context.bot.ban_chat_member(chat_id, user.id)
            await update.message.delete()
        except: pass
        return

    role = await db.get_role(user.id)
    if can_moderate(role):
        return

    chat_info = await db.get_chat(chat_id)
    if not chat_info:
        return

    text = (update.message.text or "").lower()

    if chat_info.get("read_only"):
        try: await update.message.delete()
        except: pass
        return

    word_filters = await db.get_word_filters(chat_id)
    for word in word_filters:
        if word in text:
            try:
                await update.message.delete()
                await context.bot.send_message(chat_id,
                    f"🚫 Сообщение от {escape_html(user.first_name or str(user.id))} удалено",
                    parse_mode=ParseMode.HTML)
            except: pass
            return

    if chat_info.get("antispam"):
        key = f"spam_{user.id}_{chat_id}"
        now = time.time()
        ts = context.bot_data.get(key, [])
        ts = [t for t in ts if now - t < SPAM_INTERVAL_SECONDS * SPAM_MESSAGES_COUNT]
        ts.append(now)
        context.bot_data[key] = ts

        if len(ts) > SPAM_MESSAGES_COUNT:
            wk = f"sw_{user.id}"
            sw = context.bot_data.get(wk, 0) + 1
            context.bot_data[wk] = sw
            try: await update.message.delete()
            except: pass

            if sw >= ANTISPAM_WARN_THRESHOLD:
                until = time.time() + 3600
                await db.set_mute(user.id, until, "Флуд", 0, chat_id)
                try:
                    await context.bot.restrict_chat_member(chat_id, user.id,
                        permissions=ChatPermissions(can_send_messages=False), until_date=int(until))
                    await context.bot.send_message(chat_id,
                        f"🔇 {escape_html(user.first_name or str(user.id))} замучен на 1 час (флуд)",
                        parse_mode=ParseMode.HTML)
                except: pass
                context.bot_data[wk] = 0
                await log_action(context.bot, f"🔇 Автомут за флуд: {user.first_name} ({user.id})")
            else:
                await db.add_warn(user.id, "Флуд", 0, chat_id)
                try:
                    await context.bot.send_message(chat_id,
                        f"⚠️ {escape_html(user.first_name or str(user.id))}: флуд ({sw}/{ANTISPAM_WARN_THRESHOLD})",
                        parse_mode=ParseMode.HTML)
                except: pass
            return

    if chat_info.get("ai_moderation") and text:
        result = await analyze_message(text)
        if result and result.get("violation"):
            action = result.get("action", "warn")
            reason = result.get("reason", "Нарушение (ИИ)")
            try: await update.message.delete()
            except: pass
            uname = escape_html(user.first_name or str(user.id))

            if action == "ban":
                until = time.time() + 86400
                await db.set_ban(user.id, until, reason, 0, chat_id)
                try:
                    await context.bot.ban_chat_member(chat_id, user.id, until_date=int(until))
                    await context.bot.send_message(chat_id,
                        f"🤖🔨 {uname} забанен: {escape_html(reason)}", parse_mode=ParseMode.HTML)
                except: pass
                await log_action(context.bot, f"🤖🔨 ИИ-бан: {user.first_name} ({user.id}) — {reason}")
            elif action == "mute":
                until = time.time() + 3600
                await db.set_mute(user.id, until, reason, 0, chat_id)
                try:
                    await context.bot.restrict_chat_member(chat_id, user.id,
                        permissions=ChatPermissions(can_send_messages=False), until_date=int(until))
                    await context.bot.send_message(chat_id,
                        f"🤖🔇 {uname} замучен: {escape_html(reason)}", parse_mode=ParseMode.HTML)
                except: pass
                await log_action(context.bot, f"🤖🔇 ИИ-мут: {user.first_name} ({user.id}) — {reason}")
            else:
                warns = await db.add_warn(user.id, reason, 0, chat_id)
                try:
                    await context.bot.send_message(chat_id,
                        f"🤖⚠️ {uname}: {escape_html(reason)} [{warns}/{MAX_WARNS}]",
                        parse_mode=ParseMode.HTML)
                except: pass
                if warns >= MAX_WARNS:
                    await db.set_ban(user.id, 0, f"Автобан: {warns} варнов", 0)
                    try: await context.bot.ban_chat_member(chat_id, user.id)
                    except: pass


async def private_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type != "private":
        return
    user = update.effective_user
    await db.ensure_user(user.id, user.username or "", user.first_name or "")
    iface = await db.get_interface(user.id)
    if not iface:
        from keyboards import interface_choice_kb
        await update.message.reply_text("Выбери интерфейс:", reply_markup=interface_choice_kb())
        return
    await update.message.reply_text("Используй /start")
