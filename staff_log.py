"""
Логирование действий в стафф-чат с поддержкой топиков.
"""

import logging
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from config import STAFF_CHAT_ID, LOG_TOPIC_ID, GBAN_TOPIC_ID, PUNISH_TOPIC_ID, REPORT_TOPIC_ID
from utils import escape_html

logger = logging.getLogger(__name__)


async def _send_to_topic(bot, topic_id: int, text: str):
    if not STAFF_CHAT_ID:
        return
    try:
        kwargs = {"chat_id": STAFF_CHAT_ID, "text": text, "parse_mode": ParseMode.HTML}
        if topic_id:
            kwargs["message_thread_id"] = topic_id
        await bot.send_message(**kwargs)
    except Exception as e:
        logger.warning(f"Не удалось отправить лог: {e}")


async def log_action(bot, text: str):
    await _send_to_topic(bot, LOG_TOPIC_ID, text)


async def log_punishment(bot, action: str, target_id: int, target_name: str,
                         issuer_id: int, issuer_name: str, duration: str = "",
                         reason: str = ""):
    icons = {"ban": "🔨", "unban": "✅", "mute": "🔇", "unmute": "🔊",
             "warn": "⚠️", "unwarn": "♻️", "globalban": "🌍"}
    icon = icons.get(action, "📋")
    text = (
        f"{icon} <b>{action.upper()}</b>\n"
        f"Кому: {escape_html(target_name)} (<code>{target_id}</code>)\n"
        f"Выдал: {escape_html(issuer_name)} (<code>{issuer_id}</code>)"
    )
    if duration:
        text += f"\nСрок: {duration}"
    if reason:
        text += f"\nПричина: {escape_html(reason)}"

    topic = GBAN_TOPIC_ID if action == "globalban" else PUNISH_TOPIC_ID or LOG_TOPIC_ID
    await _send_to_topic(bot, topic, text)


async def log_report(bot, reporter_id: int, reporter_name: str,
                     reported_id: int, reason: str):
    text = (
        f"📩 <b>Новый репорт</b>\n"
        f"От: {escape_html(reporter_name)} (<code>{reporter_id}</code>)\n"
        f"На: <code>{reported_id}</code>\n"
        f"Причина: {escape_html(reason)}"
    )
    await _send_to_topic(bot, REPORT_TOPIC_ID or LOG_TOPIC_ID, text)
