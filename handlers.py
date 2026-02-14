"""
Хендлеры — /start, меню, навигация.
"""

import time
import logging
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

import database as db
from config import (
    INTERFACE_COMMANDS, INTERFACE_BUTTONS, SUPPORT_LINK,
    ROLE_MODERATOR, ROLE_ADMIN,
)
from utils import (
    role_name, can_moderate, can_admin, escape_html,
    format_user_profile, format_user_short,
)
from keyboards import (
    interface_choice_kb, main_menu_kb, settings_kb,
    moderation_menu_kb, admin_menu_kb,
    bans_menu_kb, warns_menu_kb, mutes_menu_kb, users_menu_kb,
    chats_list_kb, roles_menu_kb, back_to_main_kb, cancel_kb,
)

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


async def _send_commands_help(msg, role):
    lines = [
        "📋 <b>Доступные команды:</b>\n",
        "/start — Главное меню",
        "/profile — Профиль",
        "/top — Топ по сообщениям",
        "/report — Репорт",
        "/settings — Настройки",
    ]
    if can_moderate(role):
        lines += [
            "\n🛡 <b>Модерация:</b>",
            "/ban &lt;user&gt; &lt;время&gt; [причина]",
            "/unban &lt;user&gt;",
            "/editban &lt;user&gt; &lt;время&gt;",
            "/globalban &lt;user&gt; [причина]",
            "/warn &lt;user&gt; [причина]",
            "/unwarn &lt;user&gt;",
            "/mute &lt;user&gt; &lt;время&gt; [причина]",
            "/unmute &lt;user&gt;",
            "/editmute &lt;user&gt; &lt;время&gt;",
            "/users /find /online /staff",
            "/reports /chatmod",
        ]
    if can_admin(role):
        lines += ["\n👑 <b>Админ:</b>", "/setrole &lt;user&gt; &lt;уровень&gt;"]
    if SUPPORT_LINK:
        lines.append(f"\n💬 <a href='{SUPPORT_LINK}'>Поддержка</a>")
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await db.ensure_user(user.id, user.username or "", user.first_name or "")
    iface = await db.get_interface(user.id)

    if not iface:
        await update.message.reply_text(
            "👋 Добро пожаловать!\n\nВыбери удобный интерфейс управления:",
            reply_markup=interface_choice_kb(), parse_mode=ParseMode.HTML)
        return

    role = await db.get_role(user.id)
    if iface == INTERFACE_BUTTONS:
        await update.message.reply_text(
            "📋 <b>Главное меню</b>",
            reply_markup=main_menu_kb(role), parse_mode=ParseMode.HTML)
    else:
        # При интерфейсе команд показываем список команд
        await _send_commands_help(update.message, role)


async def cb_set_interface(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    iface = q.data.split(":")[1]
    uid = q.from_user.id
    await db.set_interface(uid, iface)
    role = await db.get_role(uid)

    if iface == INTERFACE_BUTTONS:
        await q.edit_message_text(
            "✅ Интерфейс: <b>Кнопки</b>\n\n📋 <b>Главное меню</b>",
            reply_markup=main_menu_kb(role), parse_mode=ParseMode.HTML)
    else:
        # Сразу показываем список команд при выборе интерфейса "Команды"
        text = "✅ Интерфейс: <b>Команды</b>\n\n"
        text += "📋 <b>Доступные команды:</b>\n\n"
        text += "/start — Главное меню\n"
        text += "/profile — Профиль\n"
        text += "/top — Топ по сообщениям\n"
        text += "/report — Репорт\n"
        text += "/settings — Настройки\n"
        
        if can_moderate(role):
            text += "\n🛡 <b>Модерация:</b>\n"
            text += "/ban &lt;user&gt; &lt;время&gt; [причина]\n"
            text += "/unban &lt;user&gt;\n"
            text += "/editban &lt;user&gt; &lt;время&gt;\n"
            text += "/globalban &lt;user&gt; [причина]\n"
            text += "/warn &lt;user&gt; [причина]\n"
            text += "/unwarn &lt;user&gt;\n"
            text += "/mute &lt;user&gt; &lt;время&gt; [причина]\n"
            text += "/unmute &lt;user&gt;\n"
            text += "/editmute &lt;user&gt; &lt;время&gt;\n"
            text += "/users /find /online /staff\n"
            text += "/reports /chatmod\n"
        
        if can_admin(role):
            text += "\n👑 <b>Админ:</b>\n"
            text += "/setrole &lt;user&gt; &lt;уровень&gt;\n"
        
        if SUPPORT_LINK:
            text += f"\n💬 <a href='{SUPPORT_LINK}'>Поддержка</a>"
        
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cb_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    section = q.data.split(":")[1]
    uid = q.from_user.id
    role = await db.get_role(uid)

    if section == "main":
        await q.edit_message_text("📋 <b>Главное меню</b>",
                                  reply_markup=main_menu_kb(role), parse_mode=ParseMode.HTML)
    elif section == "moderation" and can_moderate(role):
        await q.edit_message_text("🛡 <b>Модерация</b>",
                                  reply_markup=moderation_menu_kb(), parse_mode=ParseMode.HTML)
    elif section == "admin" and can_admin(role):
        await q.edit_message_text("👑 <b>Админка</b>",
                                  reply_markup=admin_menu_kb(), parse_mode=ParseMode.HTML)
    elif section == "profile":
        u = await db.get_user(uid)
        if u:
            await q.edit_message_text(format_user_profile(u),
                                      reply_markup=back_to_main_kb(), parse_mode=ParseMode.HTML)
    elif section == "top":
        top = await db.get_top_users(10)
        lines = ["🏆 <b>Топ по сообщениям:</b>\n"]
        for i, u in enumerate(top, 1):
            name = escape_html(u.get("first_name") or u.get("username") or str(u["user_id"]))
            lines.append(f"{i}. {name} — {u['messages_count']}")
        await q.edit_message_text("\n".join(lines),
                                  reply_markup=back_to_main_kb(), parse_mode=ParseMode.HTML)
    elif section == "report":
        context.user_data["action"] = "report"
        await q.edit_message_text("📩 Введи ID или @username нарушителя:",
                                  reply_markup=cancel_kb(), parse_mode=ParseMode.HTML)
        return AWAIT_REPORT_USER
    elif section == "settings":
        await q.edit_message_text("⚙️ <b>Настройки</b>\n\nВыбери интерфейс:",
                                  reply_markup=settings_kb(), parse_mode=ParseMode.HTML)
    elif section == "bans" and can_moderate(role):
        await q.edit_message_text("🚫 <b>Блокировки</b>",
                                  reply_markup=bans_menu_kb(), parse_mode=ParseMode.HTML)
    elif section == "warns" and can_moderate(role):
        await q.edit_message_text("⚠️ <b>Варны</b>",
                                  reply_markup=warns_menu_kb(), parse_mode=ParseMode.HTML)
    elif section == "mutes" and can_moderate(role):
        await q.edit_message_text("🔇 <b>Муты</b>",
                                  reply_markup=mutes_menu_kb(), parse_mode=ParseMode.HTML)
    elif section == "users" and can_moderate(role):
        await q.edit_message_text("👥 <b>Пользователи</b>",
                                  reply_markup=users_menu_kb(), parse_mode=ParseMode.HTML)
    elif section == "chats" and can_moderate(role):
        # Гарантируем, что все чаты из config.json зарегистрированы в БД
        from config import MODERATED_CHATS
        for cid in MODERATED_CHATS:
            try:
                await db.ensure_chat(int(cid))
            except Exception as e:
                logger.warning(f"Failed to ensure chat {cid}: {e}")
        
        # Получаем все чаты из БД
        chats = await db.get_all_chats()
        
        if not chats:
            await q.edit_message_text(
                "❌ Нет чатов в базе данных.\n\n"
                "Добавь бота в группы или проверь config.json.",
                reply_markup=back_to_main_kb())
        else:
            # Показываем список чатов
            await q.edit_message_text(
                f"💬 <b>Выбери чат:</b>\n\n"
                f"Всего чатов: {len(chats)}",
                reply_markup=chats_list_kb(chats), 
                parse_mode=ParseMode.HTML)
    elif section == "reports" and can_moderate(role):
        reports = await db.get_open_reports(10)
        if not reports:
            await q.edit_message_text("Нет открытых репортов.",
                                      reply_markup=moderation_menu_kb())
        else:
            lines = ["📋 <b>Репорты:</b>\n"]
            for r in reports:
                lines.append(f"#{r['id']} | На: <code>{r['reported_id']}</code> | {escape_html(r.get('reason','—'))}")
            await q.edit_message_text("\n".join(lines),
                                      reply_markup=moderation_menu_kb(), parse_mode=ParseMode.HTML)
    elif section == "roles" and can_admin(role):
        await q.edit_message_text("🛡 <b>Управление ролями</b>",
                                  reply_markup=roles_menu_kb(), parse_mode=ParseMode.HTML)


async def cb_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


async def cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data.clear()
    role = await db.get_role(q.from_user.id)
    iface = await db.get_interface(q.from_user.id)
    if iface == INTERFACE_BUTTONS:
        await q.edit_message_text("📋 <b>Главное меню</b>",
                                  reply_markup=main_menu_kb(role), parse_mode=ParseMode.HTML)
    else:
        await q.edit_message_text("❌ Отменено.")
    return ConversationHandler.END
