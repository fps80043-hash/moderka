"""Точка входа."""
import asyncio, logging
from telegram.constants import ParseMode
from telegram.ext import (ApplicationBuilder, CommandHandler, CallbackQueryHandler,
                          MessageHandler, ConversationHandler, filters)
from config import BOT_TOKEN, INTERFACE_BUTTONS
import database as db
from handlers import (cmd_start, cb_set_interface, cb_menu, cb_noop, cb_cancel,
                      AWAIT_TARGET, AWAIT_DURATION, AWAIT_REASON, AWAIT_SEARCH,
                      AWAIT_WORD_FILTER, AWAIT_REPORT_USER, AWAIT_REPORT_REASON, AWAIT_ROLE_TARGET)
from actions import (cb_action, conv_target_input, conv_duration_input, cb_duration,
                     conv_reason_input, cb_users_list, cb_user_info, cb_users_search,
                     conv_search_input, cb_users_online, cb_users_staff,
                     cb_chat_select, cb_chat_toggle, cb_chat_filter, conv_word_filter_input,
                     cb_set_role, conv_role_target, conv_report_user_input, conv_report_reason_input)
from commands import (cmd_profile, cmd_top, cmd_settings, cmd_ban, cmd_unban, cmd_warn,
                      cmd_unwarn, cmd_mute, cmd_unmute, cmd_editban, cmd_editmute,
                      cmd_globalban, cmd_users, cmd_find, cmd_online, cmd_staff,
                      cmd_setrole, cmd_report, cmd_reports, cmd_chatmod,
                      group_message_handler, private_fallback)

from keyboards import main_menu_kb

logging.basicConfig(format="%(asctime)s [%(name)s] %(levelname)s: %(message)s", level=logging.INFO)


def _guard_private_commands(func):
    """Запрещает пользоваться командами в ЛС, если выбран интерфейс "Кнопки".

    Требование пользователя: при "Кнопках" команды должны быть недоступны.
    /start оставляем рабочим всегда.
    """

    async def wrapper(update, context):
        try:
            if update.effective_chat and update.effective_chat.type == "private":
                text = (update.message.text or "") if update.message else ""
                if text.startswith("/start"):
                    return await func(update, context)

                iface = await db.get_interface(update.effective_user.id)
                if iface == INTERFACE_BUTTONS:
                    role = await db.get_role(update.effective_user.id)
                    await update.message.reply_text(
                        "🖲 У тебя выбран интерфейс <b>Кнопки</b>.\n\n"
                        "Команды отключены — пользуйся меню.\n"
                        "Если хочешь команды: ⚙️ Настройки → ⌨️ Команды.",
                        reply_markup=main_menu_kb(role),
                        parse_mode=ParseMode.HTML,
                    )
                    return
        except Exception as e:
            logging.getLogger(__name__).warning(f"command gate error: {e}")
        return await func(update, context)

    return wrapper

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_action, pattern=r"^act:"),
            CallbackQueryHandler(cb_menu, pattern=r"^menu:report$"),
            CallbackQueryHandler(cb_users_search, pattern=r"^users:search$"),
            CallbackQueryHandler(cb_chat_filter, pattern=r"^chfilter:"),
            CallbackQueryHandler(cb_set_role, pattern=r"^setrole:"),
        ],
        states={
            AWAIT_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_target_input),
                           CallbackQueryHandler(cb_cancel, pattern=r"^cancel$")],
            AWAIT_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_duration_input),
                             CallbackQueryHandler(cb_duration, pattern=r"^dur:"),
                             CallbackQueryHandler(cb_cancel, pattern=r"^cancel$")],
            AWAIT_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_reason_input),
                           CallbackQueryHandler(cb_cancel, pattern=r"^cancel$")],
            AWAIT_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_search_input),
                           CallbackQueryHandler(cb_cancel, pattern=r"^cancel$")],
            AWAIT_WORD_FILTER: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_word_filter_input),
                                CallbackQueryHandler(cb_cancel, pattern=r"^cancel$")],
            AWAIT_REPORT_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_report_user_input),
                                CallbackQueryHandler(cb_cancel, pattern=r"^cancel$")],
            AWAIT_REPORT_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_report_reason_input),
                                  CallbackQueryHandler(cb_cancel, pattern=r"^cancel$")],
            AWAIT_ROLE_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_role_target),
                                CallbackQueryHandler(cb_cancel, pattern=r"^cancel$")],
        },
        fallbacks=[CallbackQueryHandler(cb_cancel, pattern=r"^cancel$"), CommandHandler("start", cmd_start)],
        per_user=True, per_chat=True,
    )
    app.add_handler(conv)
    for name, func in [("start",cmd_start),("profile",cmd_profile),("top",cmd_top),
                        ("settings",cmd_settings),("ban",cmd_ban),("unban",cmd_unban),
                        ("warn",cmd_warn),("unwarn",cmd_unwarn),("mute",cmd_mute),
                        ("unmute",cmd_unmute),("editban",cmd_editban),("editmute",cmd_editmute),
                        ("globalban",cmd_globalban),("users",cmd_users),("find",cmd_find),
                        ("online",cmd_online),("staff",cmd_staff),("setrole",cmd_setrole),
                        ("report",cmd_report),("reports",cmd_reports),("chatmod",cmd_chatmod)]:
        # /start оставляем без ограничений, остальные команды в ЛС блокируем при интерфейсе "Кнопки".
        handler_fn = func if name == "start" else _guard_private_commands(func)
        app.add_handler(CommandHandler(name, handler_fn))
    app.add_handler(CallbackQueryHandler(cb_set_interface, pattern=r"^set_iface:"))
    app.add_handler(CallbackQueryHandler(cb_menu, pattern=r"^menu:"))
    app.add_handler(CallbackQueryHandler(cb_users_list, pattern=r"^users:list:"))
    app.add_handler(CallbackQueryHandler(cb_user_info, pattern=r"^userinfo:"))
    app.add_handler(CallbackQueryHandler(cb_users_online, pattern=r"^users:online$"))
    app.add_handler(CallbackQueryHandler(cb_users_staff, pattern=r"^users:staff$"))
    app.add_handler(CallbackQueryHandler(cb_chat_select, pattern=r"^chat:"))
    app.add_handler(CallbackQueryHandler(cb_chat_toggle, pattern=r"^chtog:"))
    app.add_handler(CallbackQueryHandler(cb_noop, pattern=r"^noop$"))
    app.add_handler(MessageHandler(filters.ALL & filters.ChatType.GROUPS, group_message_handler))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, private_fallback))
    asyncio.get_event_loop().run_until_complete(db.init_db())
    logging.getLogger(__name__).info("Бот запущен")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
