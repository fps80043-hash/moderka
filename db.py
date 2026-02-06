"""
Database module - v3 для модерации Telegram
Добавлено: кэш причин варнов, улучшенные методы управления ролями
"""

import aiosqlite
from typing import Optional, List, Dict, Tuple
import time


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.db: Optional[aiosqlite.Connection] = None

    async def init(self):
        self.db = await aiosqlite.connect(self.db_path)
        self.db.row_factory = aiosqlite.Row
        await self._create_tables()

    async def _create_tables(self):
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                title TEXT,
                welcome_text TEXT DEFAULT '',
                silence INTEGER DEFAULT 0,
                antiflood INTEGER DEFAULT 0,
                filter INTEGER DEFAULT 0,
                created_at INTEGER DEFAULT (strftime('%s', 'now'))
            );

            CREATE TABLE IF NOT EXISTS global_roles (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                role INTEGER DEFAULT 0,
                added_at INTEGER DEFAULT (strftime('%s', 'now'))
            );

            CREATE TABLE IF NOT EXISTS global_bans (
                user_id INTEGER PRIMARY KEY,
                banned_by INTEGER,
                reason TEXT,
                banned_at INTEGER DEFAULT (strftime('%s', 'now'))
            );

            CREATE TABLE IF NOT EXISTS user_roles (
                user_id INTEGER,
                chat_id INTEGER,
                role INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, chat_id)
            );

            CREATE TABLE IF NOT EXISTS nicks (
                user_id INTEGER,
                chat_id INTEGER,
                nick TEXT,
                PRIMARY KEY (user_id, chat_id)
            );

            CREATE TABLE IF NOT EXISTS username_cache (
                username TEXT PRIMARY KEY COLLATE NOCASE,
                user_id INTEGER,
                updated_at INTEGER DEFAULT (strftime('%s', 'now'))
            );

            CREATE TABLE IF NOT EXISTS bans (
                user_id INTEGER,
                chat_id INTEGER,
                banned_by INTEGER,
                reason TEXT,
                banned_at INTEGER DEFAULT (strftime('%s', 'now')),
                PRIMARY KEY (user_id, chat_id)
            );

            CREATE TABLE IF NOT EXISTS mutes (
                user_id INTEGER,
                chat_id INTEGER,
                muted_by INTEGER,
                reason TEXT,
                until INTEGER,
                muted_at INTEGER DEFAULT (strftime('%s', 'now')),
                PRIMARY KEY (user_id, chat_id)
            );

            CREATE TABLE IF NOT EXISTS warns (
                user_id INTEGER,
                chat_id INTEGER,
                count INTEGER DEFAULT 0,
                warned_by INTEGER,
                reason TEXT,
                warned_at INTEGER DEFAULT (strftime('%s', 'now')),
                PRIMARY KEY (user_id, chat_id)
            );

            CREATE TABLE IF NOT EXISTS warn_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                warned_by INTEGER,
                reason TEXT,
                warned_at INTEGER DEFAULT (strftime('%s', 'now'))
            );

            CREATE TABLE IF NOT EXISTS banwords (
                chat_id INTEGER,
                word TEXT COLLATE NOCASE,
                PRIMARY KEY (chat_id, word)
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                message_id INTEGER,
                sent_at INTEGER DEFAULT (strftime('%s', 'now'))
            );

            -- Отложенные роли (username известен, user_id ещё нет)
            CREATE TABLE IF NOT EXISTS pending_staff (
                username TEXT PRIMARY KEY COLLATE NOCASE,
                role INTEGER DEFAULT 0
            );

            -- Кэш причин варнов (для callback)
            CREATE TABLE IF NOT EXISTS warn_reason_cache (
                user_id INTEGER,
                chat_id INTEGER,
                reason TEXT,
                cached_at INTEGER DEFAULT (strftime('%s', 'now')),
                PRIMARY KEY (user_id, chat_id)
            );

            CREATE INDEX IF NOT EXISTS idx_messages_user_chat ON messages(user_id, chat_id);
            CREATE INDEX IF NOT EXISTS idx_messages_sent ON messages(sent_at);
            CREATE INDEX IF NOT EXISTS idx_username_cache_uid ON username_cache(user_id);
            CREATE INDEX IF NOT EXISTS idx_warn_cache_time ON warn_reason_cache(cached_at);
        """)
        await self.db.commit()

    # =========================================================================
    # ЧАТЫ
    # =========================================================================

    async def register_chat(self, chat_id: int, title: str = ""):
        await self.db.execute("""
            INSERT INTO chats (chat_id, title) VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET title = excluded.title
        """, (chat_id, title))
        await self.db.commit()

    async def get_chat(self, chat_id: int) -> Optional[Dict]:
        async with self.db.execute("SELECT * FROM chats WHERE chat_id = ?", (chat_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_all_chats(self) -> List[Dict]:
        async with self.db.execute("SELECT * FROM chats") as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def chat_exists(self, chat_id: int) -> bool:
        async with self.db.execute("SELECT 1 FROM chats WHERE chat_id = ?", (chat_id,)) as cur:
            return await cur.fetchone() is not None

    async def remove_chat(self, chat_id: int):
        await self.db.execute("DELETE FROM chats WHERE chat_id = ?", (chat_id,))
        await self.db.commit()

    # =========================================================================
    # USERNAME CACHE
    # =========================================================================

    async def cache_username(self, user_id: int, username: str):
        if not username:
            return
        username = username.lower().lstrip('@')
        await self.db.execute("""
            INSERT INTO username_cache (username, user_id, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET user_id = excluded.user_id, updated_at = excluded.updated_at
        """, (username, user_id, int(time.time())))
        await self.db.commit()

    async def get_user_by_username(self, username: str) -> Optional[int]:
        username = username.lower().lstrip('@')
        async with self.db.execute(
            "SELECT user_id FROM username_cache WHERE username = ? COLLATE NOCASE", (username,)
        ) as cur:
            row = await cur.fetchone()
            return row['user_id'] if row else None

    async def get_username_by_id(self, user_id: int) -> Optional[str]:
        """Получить username по user_id"""
        # Сначала из global_roles
        async with self.db.execute(
            "SELECT username FROM global_roles WHERE user_id = ? AND username IS NOT NULL AND username != ''",
            (user_id,)
        ) as cur:
            row = await cur.fetchone()
            if row and row['username']:
                return row['username']
        # Затем из кэша
        async with self.db.execute(
            "SELECT username FROM username_cache WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1",
            (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row['username'] if row else None

    # =========================================================================
    # PENDING STAFF (отложенные роли)
    # =========================================================================

    async def init_pending_staff(self, staff_dict: dict):
        """Инициализация отложенных ролей из конфига"""
        for username, role in staff_dict.items():
            await self.save_pending_staff(username, role)

    async def save_pending_staff(self, username: str, role: int):
        """Сохранить отложенную роль (username известен, user_id нет)"""
        username = username.lower().lstrip('@')
        await self.db.execute("""
            INSERT INTO pending_staff (username, role) VALUES (?, ?)
            ON CONFLICT(username) DO UPDATE SET role = excluded.role
        """, (username, role))
        await self.db.commit()

    async def apply_pending_staff(self, user_id: int, username: str):
        """Применить отложенную роль когда узнали user_id"""
        if not username:
            return
        username = username.lower().lstrip('@')
        async with self.db.execute(
            "SELECT role FROM pending_staff WHERE username = ? COLLATE NOCASE", (username,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                role = row['role']
                await self.set_global_role(user_id, role, username)
                await self.db.execute(
                    "DELETE FROM pending_staff WHERE username = ? COLLATE NOCASE", (username,)
                )
                await self.db.commit()

    async def get_all_pending_staff(self) -> List[Dict]:
        """Получить все отложенные роли"""
        async with self.db.execute("SELECT * FROM pending_staff WHERE role > 0") as cur:
            return [dict(r) for r in await cur.fetchall()]

    # =========================================================================
    # ГЛОБАЛЬНЫЕ РОЛИ
    # =========================================================================

    async def set_global_role(self, user_id: int, role: int, username: str = None):
        await self.db.execute("""
            INSERT INTO global_roles (user_id, username, role) VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET role = excluded.role,
                username = COALESCE(excluded.username, global_roles.username)
        """, (user_id, username, role))
        await self.db.commit()
        if username:
            await self.cache_username(user_id, username)

    async def get_global_role(self, user_id: int) -> int:
        async with self.db.execute("SELECT role FROM global_roles WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return row['role'] if row else 0

    async def remove_global_role(self, user_id: int):
        await self.db.execute("DELETE FROM global_roles WHERE user_id = ?", (user_id,))
        await self.db.commit()

    async def get_all_staff(self) -> List[Tuple[int, int]]:
        """Получить всех сотрудников (user_id, role)"""
        async with self.db.execute("SELECT user_id, role FROM global_roles WHERE role > 0 ORDER BY role DESC") as cur:
            rows = await cur.fetchall()
            return [(row['user_id'], row['role']) for row in rows]

    # =========================================================================
    # ЛОКАЛЬНЫЕ РОЛИ
    # =========================================================================

    async def set_user_role(self, user_id: int, chat_id: int, role: int):
        if role == 0:
            await self.db.execute("DELETE FROM user_roles WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
        else:
            await self.db.execute("""
                INSERT INTO user_roles (user_id, chat_id, role) VALUES (?, ?, ?)
                ON CONFLICT(user_id, chat_id) DO UPDATE SET role = excluded.role
            """, (user_id, chat_id, role))
        await self.db.commit()

    async def get_user_role(self, user_id: int, chat_id: int) -> int:
        async with self.db.execute(
            "SELECT role FROM user_roles WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
        ) as cur:
            row = await cur.fetchone()
            return row['role'] if row else 0

    async def get_chat_staff(self, chat_id: int) -> List[Dict]:
        async with self.db.execute(
            "SELECT * FROM user_roles WHERE chat_id = ? AND role > 0 ORDER BY role DESC", (chat_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # =========================================================================
    # ГЛОБАЛЬНЫЙ БАН
    # =========================================================================

    async def add_global_ban(self, user_id: int, banned_by: int, reason: str):
        await self.db.execute("""
            INSERT INTO global_bans (user_id, banned_by, reason) VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET banned_by = excluded.banned_by,
                reason = excluded.reason, banned_at = strftime('%s', 'now')
        """, (user_id, banned_by, reason))
        await self.db.commit()

    async def remove_global_ban(self, user_id: int):
        await self.db.execute("DELETE FROM global_bans WHERE user_id = ?", (user_id,))
        await self.db.commit()

    async def is_globally_banned(self, user_id: int) -> bool:
        async with self.db.execute("SELECT 1 FROM global_bans WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone() is not None

    async def get_global_ban_info(self, user_id: int) -> Optional[Dict]:
        async with self.db.execute("SELECT * FROM global_bans WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    # =========================================================================
    # БАНЫ
    # =========================================================================

    async def add_ban(self, user_id: int, chat_id: int, banned_by: int, reason: str):
        await self.db.execute("""
            INSERT INTO bans (user_id, chat_id, banned_by, reason) VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET banned_by = excluded.banned_by,
                reason = excluded.reason, banned_at = strftime('%s', 'now')
        """, (user_id, chat_id, banned_by, reason))
        await self.db.commit()

    async def remove_ban(self, user_id: int, chat_id: int):
        await self.db.execute("DELETE FROM bans WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
        await self.db.commit()

    async def is_banned(self, user_id: int, chat_id: int) -> bool:
        async with self.db.execute(
            "SELECT 1 FROM bans WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
        ) as cur:
            return await cur.fetchone() is not None

    async def get_ban_info(self, user_id: int, chat_id: int) -> Optional[Dict]:
        async with self.db.execute(
            "SELECT * FROM bans WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    # =========================================================================
    # МУТЫ
    # =========================================================================

    async def add_mute(self, user_id: int, chat_id: int, muted_by: int, reason: str, until: int):
        await self.db.execute("""
            INSERT INTO mutes (user_id, chat_id, muted_by, reason, until) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET muted_by = excluded.muted_by,
                reason = excluded.reason, until = excluded.until, muted_at = strftime('%s', 'now')
        """, (user_id, chat_id, muted_by, reason, until))
        await self.db.commit()

    async def remove_mute(self, user_id: int, chat_id: int):
        await self.db.execute("DELETE FROM mutes WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
        await self.db.commit()

    async def is_muted(self, user_id: int, chat_id: int) -> bool:
        now = int(time.time())
        async with self.db.execute(
            "SELECT until FROM mutes WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return False
            until = row['until']
            if until == 0:
                return True
            if until > now:
                return True
            # Мут истёк
            await self.remove_mute(user_id, chat_id)
            return False

    async def get_mute_info(self, user_id: int, chat_id: int) -> Optional[Dict]:
        async with self.db.execute(
            "SELECT * FROM mutes WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    # =========================================================================
    # ВАРНЫ
    # =========================================================================

    async def add_warn(self, user_id: int, chat_id: int, warned_by: int, reason: str) -> int:
        """Добавить варн, вернуть новое количество"""
        # Получаем текущее количество
        async with self.db.execute(
            "SELECT count FROM warns WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
        ) as cur:
            row = await cur.fetchone()
            current = row['count'] if row else 0

        new_count = current + 1

        # Обновляем таблицу warns
        await self.db.execute("""
            INSERT INTO warns (user_id, chat_id, count, warned_by, reason) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET count = excluded.count,
                warned_by = excluded.warned_by, reason = excluded.reason,
                warned_at = strftime('%s', 'now')
        """, (user_id, chat_id, new_count, warned_by, reason))

        # Добавляем в историю
        await self.db.execute("""
            INSERT INTO warn_history (user_id, chat_id, warned_by, reason) VALUES (?, ?, ?, ?)
        """, (user_id, chat_id, warned_by, reason))

        await self.db.commit()
        return new_count

    async def remove_warn(self, user_id: int, chat_id: int) -> int:
        """Снять варн, вернуть новое количество"""
        async with self.db.execute(
            "SELECT count FROM warns WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
        ) as cur:
            row = await cur.fetchone()
            current = row['count'] if row else 0

        if current <= 0:
            return 0

        new_count = current - 1

        if new_count == 0:
            await self.db.execute("DELETE FROM warns WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
        else:
            await self.db.execute("""
                UPDATE warns SET count = ?, warned_at = strftime('%s', 'now')
                WHERE user_id = ? AND chat_id = ?
            """, (new_count, user_id, chat_id))

        await self.db.commit()
        return new_count

    async def get_warns(self, user_id: int, chat_id: int) -> int:
        async with self.db.execute(
            "SELECT count FROM warns WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
        ) as cur:
            row = await cur.fetchone()
            return row['count'] if row else 0

    async def clear_warns(self, user_id: int, chat_id: int):
        await self.db.execute("DELETE FROM warns WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
        await self.db.commit()

    async def get_warn_history(self, user_id: int, chat_id: int) -> List[Dict]:
        async with self.db.execute(
            "SELECT * FROM warn_history WHERE user_id = ? AND chat_id = ? ORDER BY warned_at DESC",
            (user_id, chat_id)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # =========================================================================
    # КЭШ ПРИЧИН ВАРНОВ (для callback)
    # =========================================================================

    async def cache_warn_reason(self, user_id: int, chat_id: int, reason: str):
        """Сохранить причину варна для последующего использования в callback"""
        await self.db.execute("""
            INSERT INTO warn_reason_cache (user_id, chat_id, reason) VALUES (?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET reason = excluded.reason,
                cached_at = strftime('%s', 'now')
        """, (user_id, chat_id, reason))
        await self.db.commit()

    async def get_cached_warn_reason(self, user_id: int, chat_id: int) -> Optional[str]:
        """Получить сохраненную причину варна"""
        async with self.db.execute(
            "SELECT reason FROM warn_reason_cache WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        ) as cur:
            row = await cur.fetchone()
            return row['reason'] if row else None

    async def clear_cached_warn_reason(self, user_id: int, chat_id: int):
        """Очистить кэш причины варна"""
        await self.db.execute(
            "DELETE FROM warn_reason_cache WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        )
        await self.db.commit()

    async def cleanup_old_warn_cache(self, max_age_seconds: int = 3600):
        """Очистить старые записи кэша (старше max_age_seconds)"""
        cutoff = int(time.time()) - max_age_seconds
        await self.db.execute(
            "DELETE FROM warn_reason_cache WHERE cached_at < ?", (cutoff,)
        )
        await self.db.commit()

    # =========================================================================
    # НИКИ
    # =========================================================================

    async def set_nick(self, user_id: int, chat_id: int, nick: str):
        await self.db.execute("""
            INSERT INTO nicks (user_id, chat_id, nick) VALUES (?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET nick = excluded.nick
        """, (user_id, chat_id, nick))
        await self.db.commit()

    async def get_nick(self, user_id: int, chat_id: int) -> Optional[str]:
        async with self.db.execute(
            "SELECT nick FROM nicks WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
        ) as cur:
            row = await cur.fetchone()
            return row['nick'] if row else None

    async def remove_nick(self, user_id: int, chat_id: int):
        await self.db.execute("DELETE FROM nicks WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
        await self.db.commit()

    async def get_user_by_nick(self, nick: str, chat_id: int) -> Optional[int]:
        async with self.db.execute(
            "SELECT user_id FROM nicks WHERE nick = ? COLLATE NOCASE AND chat_id = ?", (nick, chat_id)
        ) as cur:
            row = await cur.fetchone()
            return row['user_id'] if row else None

    # =========================================================================
    # НАСТРОЙКИ ЧАТА
    # =========================================================================

    async def set_welcome(self, chat_id: int, text: str):
        await self.db.execute("UPDATE chats SET welcome_text = ? WHERE chat_id = ?", (text, chat_id))
        await self.db.commit()

    async def get_welcome(self, chat_id: int) -> Optional[str]:
        async with self.db.execute("SELECT welcome_text FROM chats WHERE chat_id = ?", (chat_id,)) as cur:
            row = await cur.fetchone()
            return row['welcome_text'] if row and row['welcome_text'] else None

    async def set_silence(self, chat_id: int, enabled: bool):
        await self.db.execute("UPDATE chats SET silence = ? WHERE chat_id = ?", (1 if enabled else 0, chat_id))
        await self.db.commit()

    async def is_silence(self, chat_id: int) -> bool:
        async with self.db.execute("SELECT silence FROM chats WHERE chat_id = ?", (chat_id,)) as cur:
            row = await cur.fetchone()
            return bool(row['silence']) if row else False

    async def set_antiflood(self, chat_id: int, enabled: bool):
        await self.db.execute("UPDATE chats SET antiflood = ? WHERE chat_id = ?", (1 if enabled else 0, chat_id))
        await self.db.commit()

    async def is_antiflood(self, chat_id: int) -> bool:
        async with self.db.execute("SELECT antiflood FROM chats WHERE chat_id = ?", (chat_id,)) as cur:
            row = await cur.fetchone()
            return bool(row['antiflood']) if row else False

    async def set_filter(self, chat_id: int, enabled: bool):
        await self.db.execute("UPDATE chats SET filter = ? WHERE chat_id = ?", (1 if enabled else 0, chat_id))
        await self.db.commit()

    async def is_filter(self, chat_id: int) -> bool:
        async with self.db.execute("SELECT filter FROM chats WHERE chat_id = ?", (chat_id,)) as cur:
            row = await cur.fetchone()
            return bool(row['filter']) if row else False

    # =========================================================================
    # BANWORDS
    # =========================================================================

    async def add_banword(self, chat_id: int, word: str):
        word = word.lower()
        await self.db.execute("""
            INSERT OR IGNORE INTO banwords (chat_id, word) VALUES (?, ?)
        """, (chat_id, word))
        await self.db.commit()

    async def remove_banword(self, chat_id: int, word: str):
        word = word.lower()
        await self.db.execute("DELETE FROM banwords WHERE chat_id = ? AND word = ?", (chat_id, word))
        await self.db.commit()

    async def get_banwords(self, chat_id: int) -> List[str]:
        async with self.db.execute("SELECT word FROM banwords WHERE chat_id = ?", (chat_id,)) as cur:
            return [row['word'] for row in await cur.fetchall()]

    # =========================================================================
    # СПАМ (ANTIFLOOD)
    # =========================================================================

    async def check_spam(self, user_id: int, chat_id: int, now: float) -> int:
        """Проверить и обновить счётчик спама"""
        # Удаляем старые сообщения (старше SPAM_INTERVAL)
        cutoff = now - 2  # SPAM_INTERVAL по умолчанию
        await self.db.execute(
            "DELETE FROM messages WHERE user_id = ? AND chat_id = ? AND sent_at < ?",
            (user_id, chat_id, int(cutoff))
        )
        
        # Добавляем новое сообщение
        await self.db.execute(
            "INSERT INTO messages (user_id, chat_id, message_id, sent_at) VALUES (?, ?, ?, ?)",
            (user_id, chat_id, 0, int(now))
        )
        
        # Считаем сообщения за последние SPAM_INTERVAL секунд
        async with self.db.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE user_id = ? AND chat_id = ? AND sent_at >= ?",
            (user_id, chat_id, int(cutoff))
        ) as cur:
            row = await cur.fetchone()
            count = row['cnt'] if row else 0
        
        await self.db.commit()
        return count

    async def clear_spam(self, user_id: int, chat_id: int):
        """Очистить счётчик спама"""
        await self.db.execute(
            "DELETE FROM messages WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
        )
        await self.db.commit()

    async def cleanup_old_messages(self, max_age_seconds: int = 3600):
        """Очистить старые записи сообщений"""
        cutoff = int(time.time()) - max_age_seconds
        await self.db.execute("DELETE FROM messages WHERE sent_at < ?", (cutoff,))
        await self.db.commit()
