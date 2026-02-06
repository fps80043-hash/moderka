"""
Database module - полная версия для модерации Telegram
Исправленная версия: улучшена работа с кэшем, ролями, анонимными ботами.
"""

import aiosqlite
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime
import time


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.db: Optional[aiosqlite.Connection] = None

    async def init(self):
        """Инициализация БД"""
        self.db = await aiosqlite.connect(self.db_path)
        self.db.row_factory = aiosqlite.Row
        await self._create_tables()

    async def _create_tables(self):
        """Создание всех таблиц"""
        await self.db.executescript("""
            -- Зарегистрированные чаты
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                title TEXT,
                welcome_text TEXT DEFAULT '',
                silence INTEGER DEFAULT 0,
                antiflood INTEGER DEFAULT 0,
                filter INTEGER DEFAULT 0,
                invite_kick INTEGER DEFAULT 0,
                leave_kick INTEGER DEFAULT 0,
                pull_id INTEGER DEFAULT 0,
                created_at INTEGER DEFAULT (strftime('%s', 'now'))
            );

            -- Глобальные роли команды
            CREATE TABLE IF NOT EXISTS global_roles (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                role INTEGER DEFAULT 0,
                added_at INTEGER DEFAULT (strftime('%s', 'now'))
            );

            -- Глобальный бан-лист
            CREATE TABLE IF NOT EXISTS global_bans (
                user_id INTEGER PRIMARY KEY,
                banned_by INTEGER,
                reason TEXT,
                banned_at INTEGER DEFAULT (strftime('%s', 'now'))
            );

            -- Локальные роли в чатах
            CREATE TABLE IF NOT EXISTS user_roles (
                user_id INTEGER,
                chat_id INTEGER,
                role INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, chat_id)
            );

            -- Ники пользователей
            CREATE TABLE IF NOT EXISTS nicks (
                user_id INTEGER,
                chat_id INTEGER,
                nick TEXT,
                PRIMARY KEY (user_id, chat_id)
            );

            -- Кэш username -> user_id (двусторонний)
            CREATE TABLE IF NOT EXISTS username_cache (
                username TEXT PRIMARY KEY COLLATE NOCASE,
                user_id INTEGER,
                updated_at INTEGER DEFAULT (strftime('%s', 'now'))
            );

            -- Локальные баны
            CREATE TABLE IF NOT EXISTS bans (
                user_id INTEGER,
                chat_id INTEGER,
                banned_by INTEGER,
                reason TEXT,
                banned_at INTEGER DEFAULT (strftime('%s', 'now')),
                PRIMARY KEY (user_id, chat_id)
            );

            -- Муты
            CREATE TABLE IF NOT EXISTS mutes (
                user_id INTEGER,
                chat_id INTEGER,
                muted_by INTEGER,
                reason TEXT,
                until INTEGER,
                muted_at INTEGER DEFAULT (strftime('%s', 'now')),
                PRIMARY KEY (user_id, chat_id)
            );

            -- Активные предупреждения
            CREATE TABLE IF NOT EXISTS warns (
                user_id INTEGER,
                chat_id INTEGER,
                count INTEGER DEFAULT 0,
                warned_by INTEGER,
                reason TEXT,
                warned_at INTEGER DEFAULT (strftime('%s', 'now')),
                PRIMARY KEY (user_id, chat_id)
            );

            -- История предупреждений
            CREATE TABLE IF NOT EXISTS warn_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                warned_by INTEGER,
                reason TEXT,
                warned_at INTEGER DEFAULT (strftime('%s', 'now'))
            );

            -- Запрещённые слова
            CREATE TABLE IF NOT EXISTS banwords (
                chat_id INTEGER,
                word TEXT COLLATE NOCASE,
                PRIMARY KEY (chat_id, word)
            );

            -- Статистика сообщений
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                message_id INTEGER,
                sent_at INTEGER DEFAULT (strftime('%s', 'now'))
            );

            -- Маппинг анонимных сообщений (message_id -> real_user_id)
            CREATE TABLE IF NOT EXISTS anon_message_map (
                chat_id INTEGER,
                message_id INTEGER,
                user_id INTEGER,
                PRIMARY KEY (chat_id, message_id)
            );

            -- Индексы для быстрого поиска
            CREATE INDEX IF NOT EXISTS idx_messages_user_chat ON messages(user_id, chat_id);
            CREATE INDEX IF NOT EXISTS idx_messages_sent ON messages(sent_at);
            CREATE INDEX IF NOT EXISTS idx_username_cache ON username_cache(username COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_username_cache_uid ON username_cache(user_id);
        """)
        await self.db.commit()

    # =========================================================================
    # ЧАТЫ
    # =========================================================================

    async def register_chat(self, chat_id: int, title: str = ""):
        """Зарегистрировать чат"""
        await self.db.execute("""
            INSERT INTO chats (chat_id, title) VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET title = excluded.title
        """, (chat_id, title))
        await self.db.commit()

    async def get_chat(self, chat_id: int) -> Optional[Dict]:
        """Получить информацию о чате"""
        async with self.db.execute(
            "SELECT * FROM chats WHERE chat_id = ?", (chat_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_all_chats(self) -> List[Dict]:
        """Получить все чаты"""
        async with self.db.execute("SELECT * FROM chats") as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def chat_exists(self, chat_id: int) -> bool:
        """Проверить существует ли чат"""
        async with self.db.execute(
            "SELECT 1 FROM chats WHERE chat_id = ?", (chat_id,)
        ) as cursor:
            return await cursor.fetchone() is not None

    async def remove_chat(self, chat_id: int):
        """Удалить чат из базы"""
        await self.db.execute("DELETE FROM chats WHERE chat_id = ?", (chat_id,))
        await self.db.commit()

    # =========================================================================
    # КЭШИРОВАНИЕ USERNAME
    # =========================================================================

    async def cache_username(self, user_id: int, username: str):
        """Кэшировать username -> user_id"""
        if not username:
            return
        username = username.lower().lstrip('@')
        await self.db.execute("""
            INSERT INTO username_cache (username, user_id, updated_at) 
            VALUES (?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET 
                user_id = excluded.user_id,
                updated_at = excluded.updated_at
        """, (username, user_id, int(time.time())))
        await self.db.commit()

    async def get_user_by_username(self, username: str) -> Optional[int]:
        """Получить user_id по username"""
        username = username.lower().lstrip('@')
        async with self.db.execute(
            "SELECT user_id FROM username_cache WHERE username = ? COLLATE NOCASE",
            (username,)
        ) as cursor:
            row = await cursor.fetchone()
            return row['user_id'] if row else None

    async def get_username_by_id(self, user_id: int) -> Optional[str]:
        """Получить username по user_id"""
        async with self.db.execute(
            "SELECT username FROM username_cache WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row['username'] if row else None

    # =========================================================================
    # АНОНИМНЫЕ СООБЩЕНИЯ
    # =========================================================================

    async def map_anon_message(self, chat_id: int, message_id: int, user_id: int):
        """Маппинг анонимного сообщения к реальному user_id"""
        await self.db.execute("""
            INSERT OR REPLACE INTO anon_message_map (chat_id, message_id, user_id)
            VALUES (?, ?, ?)
        """, (chat_id, message_id, user_id))
        await self.db.commit()

    async def get_anon_user(self, chat_id: int, message_id: int) -> Optional[int]:
        """Получить реального отправителя анонимного сообщения"""
        async with self.db.execute(
            "SELECT user_id FROM anon_message_map WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id)
        ) as cursor:
            row = await cursor.fetchone()
            return row['user_id'] if row else None

    async def cleanup_anon_map(self, days: int = 7):
        """Очистить старые записи маппинга"""
        # Удаляем записи старше N дней, ориентируемся на message_id (приблизительно)
        await self.db.execute("DELETE FROM anon_message_map WHERE rowid IN (SELECT rowid FROM anon_message_map ORDER BY rowid ASC LIMIT 10000)")
        await self.db.commit()

    # =========================================================================
    # ГЛОБАЛЬНЫЕ РОЛИ
    # =========================================================================

    async def set_global_role(self, user_id: int, role: int, username: str = None):
        """Установить глобальную роль"""
        await self.db.execute("""
            INSERT INTO global_roles (user_id, username, role) VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET role = excluded.role, username = COALESCE(excluded.username, global_roles.username)
        """, (user_id, username, role))
        await self.db.commit()
        if username:
            await self.cache_username(user_id, username)

    async def get_global_role(self, user_id: int) -> int:
        """Получить глобальную роль"""
        async with self.db.execute(
            "SELECT role FROM global_roles WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row['role'] if row else 0

    async def remove_global_role(self, user_id: int):
        """Удалить глобальную роль"""
        await self.db.execute("DELETE FROM global_roles WHERE user_id = ?", (user_id,))
        await self.db.commit()

    async def get_all_staff(self) -> List[Dict]:
        """Получить всю команду"""
        async with self.db.execute(
            "SELECT * FROM global_roles WHERE role > 0 ORDER BY role DESC"
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def get_all_staff_with_chat(self, chat_id: int) -> List[Tuple[int, int]]:
        """Получить команду для конкретного чата (user_id, role)"""
        result = []
        # Глобальные роли
        async with self.db.execute(
            "SELECT user_id, role FROM global_roles WHERE role > 0"
        ) as cursor:
            for row in await cursor.fetchall():
                result.append((row['user_id'], row['role']))

        # Локальные роли чата
        async with self.db.execute(
            "SELECT user_id, role FROM user_roles WHERE chat_id = ? AND role > 0",
            (chat_id,)
        ) as cursor:
            for row in await cursor.fetchall():
                if not any(r[0] == row['user_id'] for r in result):
                    result.append((row['user_id'], row['role']))

        return sorted(result, key=lambda x: x[1], reverse=True)

    # =========================================================================
    # ЛОКАЛЬНЫЕ РОЛИ
    # =========================================================================

    async def set_user_role(self, user_id: int, chat_id: int, role: int):
        """Установить роль в чате"""
        if role == 0:
            await self.db.execute(
                "DELETE FROM user_roles WHERE user_id = ? AND chat_id = ?",
                (user_id, chat_id)
            )
        else:
            await self.db.execute("""
                INSERT INTO user_roles (user_id, chat_id, role) VALUES (?, ?, ?)
                ON CONFLICT(user_id, chat_id) DO UPDATE SET role = excluded.role
            """, (user_id, chat_id, role))
        await self.db.commit()

    async def get_user_role(self, user_id: int, chat_id: int) -> int:
        """Получить роль в чате"""
        async with self.db.execute(
            "SELECT role FROM user_roles WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        ) as cursor:
            row = await cursor.fetchone()
            return row['role'] if row else 0

    async def get_chat_staff(self, chat_id: int) -> List[Dict]:
        """Получить команду чата"""
        async with self.db.execute(
            "SELECT * FROM user_roles WHERE chat_id = ? AND role > 0 ORDER BY role DESC",
            (chat_id,)
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    # =========================================================================
    # ГЛОБАЛЬНЫЙ БАН
    # =========================================================================

    async def add_global_ban(self, user_id: int, banned_by: int, reason: str):
        """Добавить глобальный бан"""
        await self.db.execute("""
            INSERT INTO global_bans (user_id, banned_by, reason) VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET 
                banned_by = excluded.banned_by,
                reason = excluded.reason,
                banned_at = strftime('%s', 'now')
        """, (user_id, banned_by, reason))
        await self.db.commit()

    async def remove_global_ban(self, user_id: int):
        """Удалить глобальный бан"""
        await self.db.execute("DELETE FROM global_bans WHERE user_id = ?", (user_id,))
        await self.db.commit()

    async def get_global_ban(self, user_id: int) -> Optional[Dict]:
        """Получить глобальный бан"""
        async with self.db.execute(
            "SELECT * FROM global_bans WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_global_bans(self) -> List[Dict]:
        """Получить все глобальные баны"""
        async with self.db.execute(
            "SELECT * FROM global_bans ORDER BY banned_at DESC"
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def is_global_banned(self, user_id: int) -> bool:
        """Проверить глобальный бан"""
        async with self.db.execute(
            "SELECT 1 FROM global_bans WHERE user_id = ?", (user_id,)
        ) as cursor:
            return await cursor.fetchone() is not None

    # =========================================================================
    # ЛОКАЛЬНЫЙ БАН
    # =========================================================================

    async def add_ban(self, user_id: int, chat_id: int, banned_by: int, reason: str):
        """Добавить бан в чате"""
        await self.db.execute("""
            INSERT INTO bans (user_id, chat_id, banned_by, reason) VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET
                banned_by = excluded.banned_by,
                reason = excluded.reason,
                banned_at = strftime('%s', 'now')
        """, (user_id, chat_id, banned_by, reason))
        await self.db.commit()

    async def remove_ban(self, user_id: int, chat_id: int):
        """Удалить бан"""
        await self.db.execute(
            "DELETE FROM bans WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        )
        await self.db.commit()

    async def get_ban(self, user_id: int, chat_id: int) -> Optional[Dict]:
        """Получить бан"""
        async with self.db.execute(
            "SELECT * FROM bans WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_bans(self, chat_id: int) -> List[Dict]:
        """Получить все баны чата"""
        async with self.db.execute(
            "SELECT * FROM bans WHERE chat_id = ? ORDER BY banned_at DESC",
            (chat_id,)
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def is_banned(self, user_id: int, chat_id: int) -> bool:
        """Проверить локальный бан"""
        async with self.db.execute(
            "SELECT 1 FROM bans WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        ) as cursor:
            return await cursor.fetchone() is not None

    # =========================================================================
    # МУТЫ
    # =========================================================================

    async def add_mute(self, user_id: int, chat_id: int, muted_by: int, reason: str, until: int):
        """Добавить мут"""
        await self.db.execute("""
            INSERT INTO mutes (user_id, chat_id, muted_by, reason, until) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET
                muted_by = excluded.muted_by,
                reason = excluded.reason,
                until = excluded.until,
                muted_at = strftime('%s', 'now')
        """, (user_id, chat_id, muted_by, reason, until))
        await self.db.commit()

    async def remove_mute(self, user_id: int, chat_id: int):
        """Удалить мут"""
        await self.db.execute(
            "DELETE FROM mutes WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        )
        await self.db.commit()

    async def get_mute(self, user_id: int, chat_id: int) -> Optional[Dict]:
        """Получить мут"""
        async with self.db.execute(
            "SELECT * FROM mutes WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_mutes(self, chat_id: int) -> List[Dict]:
        """Получить все муты чата"""
        now = int(time.time())
        async with self.db.execute(
            "SELECT * FROM mutes WHERE chat_id = ? AND until > ? ORDER BY until",
            (chat_id, now)
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def cleanup_expired_mutes(self, chat_id: int):
        """Удалить истёкшие муты"""
        now = int(time.time())
        await self.db.execute(
            "DELETE FROM mutes WHERE chat_id = ? AND until <= ?",
            (chat_id, now)
        )
        await self.db.commit()

    async def is_muted(self, user_id: int, chat_id: int) -> bool:
        """Проверить мут"""
        now = int(time.time())
        async with self.db.execute(
            "SELECT 1 FROM mutes WHERE user_id = ? AND chat_id = ? AND until > ?",
            (user_id, chat_id, now)
        ) as cursor:
            return await cursor.fetchone() is not None

    # =========================================================================
    # ПРЕДУПРЕЖДЕНИЯ
    # =========================================================================

    async def add_warn(self, user_id: int, chat_id: int, warned_by: int, reason: str) -> int:
        """Добавить варн, вернуть новое количество"""
        await self.db.execute("""
            INSERT INTO warn_history (user_id, chat_id, warned_by, reason)
            VALUES (?, ?, ?, ?)
        """, (user_id, chat_id, warned_by, reason))

        async with self.db.execute(
            "SELECT count FROM warns WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        ) as cursor:
            row = await cursor.fetchone()
            current = row['count'] if row else 0

        new_count = current + 1
        await self.db.execute("""
            INSERT INTO warns (user_id, chat_id, count, warned_by, reason) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET
                count = excluded.count,
                warned_by = excluded.warned_by,
                reason = excluded.reason,
                warned_at = strftime('%s', 'now')
        """, (user_id, chat_id, new_count, warned_by, reason))
        await self.db.commit()
        return new_count

    async def remove_warn(self, user_id: int, chat_id: int) -> int:
        """Снять один варн, вернуть оставшееся количество"""
        async with self.db.execute(
            "SELECT count FROM warns WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        ) as cursor:
            row = await cursor.fetchone()
            current = row['count'] if row else 0

        if current <= 1:
            await self.db.execute(
                "DELETE FROM warns WHERE user_id = ? AND chat_id = ?",
                (user_id, chat_id)
            )
            await self.db.commit()
            return 0
        else:
            await self.db.execute(
                "UPDATE warns SET count = count - 1 WHERE user_id = ? AND chat_id = ?",
                (user_id, chat_id)
            )
            await self.db.commit()
            return current - 1

    async def clear_warns(self, user_id: int, chat_id: int):
        """Очистить все варны"""
        await self.db.execute(
            "DELETE FROM warns WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        )
        await self.db.commit()

    async def get_warns_count(self, user_id: int, chat_id: int) -> int:
        """Получить количество варнов"""
        async with self.db.execute(
            "SELECT count FROM warns WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        ) as cursor:
            row = await cursor.fetchone()
            return row['count'] if row else 0

    async def get_warn_info(self, user_id: int, chat_id: int) -> Optional[Dict]:
        """Получить информацию о варнах"""
        async with self.db.execute(
            "SELECT * FROM warns WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_warn_history(self, user_id: int, chat_id: int, limit: int = 10) -> List[Dict]:
        """Получить историю варнов"""
        async with self.db.execute(
            "SELECT * FROM warn_history WHERE user_id = ? AND chat_id = ? ORDER BY warned_at DESC LIMIT ?",
            (user_id, chat_id, limit)
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def get_warns_list(self, chat_id: int) -> List[Dict]:
        """Получить всех с варнами"""
        async with self.db.execute(
            "SELECT * FROM warns WHERE chat_id = ? AND count > 0 ORDER BY count DESC",
            (chat_id,)
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    # =========================================================================
    # НИКИ
    # =========================================================================

    async def set_nick(self, user_id: int, chat_id: int, nick: str):
        """Установить ник"""
        await self.db.execute("""
            INSERT INTO nicks (user_id, chat_id, nick) VALUES (?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET nick = excluded.nick
        """, (user_id, chat_id, nick))
        await self.db.commit()

    async def remove_nick(self, user_id: int, chat_id: int):
        """Удалить ник"""
        await self.db.execute(
            "DELETE FROM nicks WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        )
        await self.db.commit()

    async def get_nick(self, user_id: int, chat_id: int) -> Optional[str]:
        """Получить ник"""
        async with self.db.execute(
            "SELECT nick FROM nicks WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        ) as cursor:
            row = await cursor.fetchone()
            return row['nick'] if row else None

    async def get_user_by_nick(self, nick: str, chat_id: int) -> Optional[int]:
        """Найти пользователя по нику"""
        async with self.db.execute(
            "SELECT user_id FROM nicks WHERE nick = ? COLLATE NOCASE AND chat_id = ?",
            (nick, chat_id)
        ) as cursor:
            row = await cursor.fetchone()
            return row['user_id'] if row else None

    async def get_nicks(self, chat_id: int) -> List[Dict]:
        """Получить все ники"""
        async with self.db.execute(
            "SELECT * FROM nicks WHERE chat_id = ?", (chat_id,)
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def clear_all_nicks(self, chat_id: int):
        """Очистить все ники в чате"""
        await self.db.execute("DELETE FROM nicks WHERE chat_id = ?", (chat_id,))
        await self.db.commit()

    # =========================================================================
    # ЗАПРЕЩЁННЫЕ СЛОВА
    # =========================================================================

    async def add_banword(self, chat_id: int, word: str):
        """Добавить запрещённое слово"""
        await self.db.execute("""
            INSERT OR IGNORE INTO banwords (chat_id, word) VALUES (?, ?)
        """, (chat_id, word.lower()))
        await self.db.commit()

    async def remove_banword(self, chat_id: int, word: str):
        """Удалить запрещённое слово"""
        await self.db.execute(
            "DELETE FROM banwords WHERE chat_id = ? AND word = ? COLLATE NOCASE",
            (chat_id, word.lower())
        )
        await self.db.commit()

    async def get_banwords(self, chat_id: int) -> List[str]:
        """Получить запрещённые слова"""
        async with self.db.execute(
            "SELECT word FROM banwords WHERE chat_id = ?", (chat_id,)
        ) as cursor:
            return [row['word'] for row in await cursor.fetchall()]

    async def clear_banwords(self, chat_id: int):
        """Очистить все запрещённые слова"""
        await self.db.execute("DELETE FROM banwords WHERE chat_id = ?", (chat_id,))
        await self.db.commit()

    # =========================================================================
    # НАСТРОЙКИ ЧАТА
    # =========================================================================

    async def set_welcome(self, chat_id: int, text: str):
        """Установить приветствие"""
        await self.db.execute(
            "UPDATE chats SET welcome_text = ? WHERE chat_id = ?",
            (text, chat_id)
        )
        await self.db.commit()

    async def get_welcome(self, chat_id: int) -> Optional[str]:
        """Получить приветствие"""
        async with self.db.execute(
            "SELECT welcome_text FROM chats WHERE chat_id = ?", (chat_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row and row['welcome_text']:
                return row['welcome_text']
            return None

    async def toggle_silence(self, chat_id: int) -> bool:
        """Переключить режим тишины"""
        chat = await self.get_chat(chat_id)
        new_val = 0 if chat and chat.get('silence') else 1
        await self.db.execute(
            "UPDATE chats SET silence = ? WHERE chat_id = ?", (new_val, chat_id)
        )
        await self.db.commit()
        return bool(new_val)

    async def is_silence(self, chat_id: int) -> bool:
        """Проверить режим тишины"""
        chat = await self.get_chat(chat_id)
        return bool(chat and chat.get('silence'))

    async def toggle_antiflood(self, chat_id: int) -> bool:
        """Переключить антифлуд"""
        chat = await self.get_chat(chat_id)
        new_val = 0 if chat and chat.get('antiflood') else 1
        await self.db.execute(
            "UPDATE chats SET antiflood = ? WHERE chat_id = ?", (new_val, chat_id)
        )
        await self.db.commit()
        return bool(new_val)

    async def is_antiflood(self, chat_id: int) -> bool:
        """Проверить антифлуд"""
        chat = await self.get_chat(chat_id)
        return bool(chat and chat.get('antiflood'))

    async def toggle_filter(self, chat_id: int) -> bool:
        """Переключить фильтр слов"""
        chat = await self.get_chat(chat_id)
        new_val = 0 if chat and chat.get('filter') else 1
        await self.db.execute(
            "UPDATE chats SET filter = ? WHERE chat_id = ?", (new_val, chat_id)
        )
        await self.db.commit()
        return bool(new_val)

    async def is_filter(self, chat_id: int) -> bool:
        """Проверить фильтр"""
        chat = await self.get_chat(chat_id)
        return bool(chat and chat.get('filter'))

    # =========================================================================
    # СТАТИСТИКА СООБЩЕНИЙ
    # =========================================================================

    async def add_message(self, user_id: int, chat_id: int, message_id: int):
        """Записать сообщение"""
        await self.db.execute(
            "INSERT INTO messages (user_id, chat_id, message_id) VALUES (?, ?, ?)",
            (user_id, chat_id, message_id)
        )
        await self.db.commit()

    async def get_message_count(self, user_id: int, chat_id: int) -> int:
        """Получить количество сообщений"""
        async with self.db.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        ) as cursor:
            row = await cursor.fetchone()
            return row['cnt'] if row else 0

    async def get_last_messages(self, user_id: int, chat_id: int, count: int = 3) -> List[Dict]:
        """Получить последние N сообщений пользователя"""
        async with self.db.execute(
            "SELECT * FROM messages WHERE user_id = ? AND chat_id = ? ORDER BY sent_at DESC LIMIT ?",
            (user_id, chat_id, count)
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def check_spam(self, user_id: int, chat_id: int, interval: int = 2, count: int = 3) -> bool:
        """Проверить спам (count сообщений за interval секунд)"""
        msgs = await self.get_last_messages(user_id, chat_id, count)
        if len(msgs) < count:
            return False
        oldest = msgs[-1]['sent_at']
        newest = msgs[0]['sent_at']
        return (newest - oldest) < interval

    async def get_user_messages(self, user_id: int, chat_id: int, limit: int = 100) -> List[int]:
        """Получить ID сообщений пользователя"""
        async with self.db.execute(
            "SELECT message_id FROM messages WHERE user_id = ? AND chat_id = ? ORDER BY sent_at DESC LIMIT ?",
            (user_id, chat_id, limit)
        ) as cursor:
            return [row['message_id'] for row in await cursor.fetchall()]

    async def clear_user_messages(self, user_id: int, chat_id: int):
        """Очистить записи сообщений пользователя"""
        await self.db.execute(
            "DELETE FROM messages WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        )
        await self.db.commit()

    async def get_top_users(self, chat_id: int, limit: int = 10) -> List[Tuple[int, int]]:
        """Получить топ пользователей по сообщениям"""
        async with self.db.execute(
            """SELECT user_id, COUNT(*) as cnt FROM messages 
               WHERE chat_id = ? GROUP BY user_id ORDER BY cnt DESC LIMIT ?""",
            (chat_id, limit)
        ) as cursor:
            return [(row['user_id'], row['cnt']) for row in await cursor.fetchall()]

    async def cleanup_old_messages(self, days: int = 30):
        """Удалить старые записи сообщений"""
        cutoff = int(time.time()) - (days * 86400)
        await self.db.execute(
            "DELETE FROM messages WHERE sent_at < ?", (cutoff,)
        )
        await self.db.commit()

    async def close(self):
        """Закрыть соединение"""
        if self.db:
            await self.db.close()
