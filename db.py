"""
Database module - v2 для модерации Telegram
Добавлено: pending_staff, get_username_by_id, apply_pending_staff
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

            CREATE INDEX IF NOT EXISTS idx_messages_user_chat ON messages(user_id, chat_id);
            CREATE INDEX IF NOT EXISTS idx_messages_sent ON messages(sent_at);
            CREATE INDEX IF NOT EXISTS idx_username_cache_uid ON username_cache(user_id);
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

    async def get_all_staff(self) -> List[Dict]:
        async with self.db.execute("SELECT * FROM global_roles WHERE role > 0 ORDER BY role DESC") as cur:
            return [dict(r) for r in await cur.fetchall()]

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

    async def get_global_ban(self, user_id: int) -> Optional[Dict]:
        async with self.db.execute("SELECT * FROM global_bans WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_global_bans(self) -> List[Dict]:
        async with self.db.execute("SELECT * FROM global_bans ORDER BY banned_at DESC") as cur:
            return [dict(r) for r in await cur.fetchall()]

    # =========================================================================
    # ЛОКАЛЬНЫЙ БАН
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

    async def get_ban(self, user_id: int, chat_id: int) -> Optional[Dict]:
        async with self.db.execute(
            "SELECT * FROM bans WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_bans(self, chat_id: int) -> List[Dict]:
        async with self.db.execute(
            "SELECT * FROM bans WHERE chat_id = ? ORDER BY banned_at DESC", (chat_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

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

    async def get_mute(self, user_id: int, chat_id: int) -> Optional[Dict]:
        async with self.db.execute(
            "SELECT * FROM mutes WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_mutes(self, chat_id: int) -> List[Dict]:
        now = int(time.time())
        async with self.db.execute(
            "SELECT * FROM mutes WHERE chat_id = ? AND until > ? ORDER BY until", (chat_id, now)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # =========================================================================
    # ВАРНЫ
    # =========================================================================

    async def add_warn(self, user_id: int, chat_id: int, warned_by: int, reason: str) -> int:
        await self.db.execute(
            "INSERT INTO warn_history (user_id, chat_id, warned_by, reason) VALUES (?, ?, ?, ?)",
            (user_id, chat_id, warned_by, reason)
        )
        async with self.db.execute(
            "SELECT count FROM warns WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
        ) as cur:
            row = await cur.fetchone()
            current = row['count'] if row else 0

        new_count = current + 1
        await self.db.execute("""
            INSERT INTO warns (user_id, chat_id, count, warned_by, reason) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET count = excluded.count,
                warned_by = excluded.warned_by, reason = excluded.reason,
                warned_at = strftime('%s', 'now')
        """, (user_id, chat_id, new_count, warned_by, reason))
        await self.db.commit()
        return new_count

    async def remove_warn(self, user_id: int, chat_id: int) -> int:
        async with self.db.execute(
            "SELECT count FROM warns WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
        ) as cur:
            row = await cur.fetchone()
            current = row['count'] if row else 0
        if current <= 1:
            await self.db.execute("DELETE FROM warns WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
            await self.db.commit()
            return 0
        await self.db.execute(
            "UPDATE warns SET count = count - 1 WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
        )
        await self.db.commit()
        return current - 1

    async def clear_warns(self, user_id: int, chat_id: int):
        await self.db.execute("DELETE FROM warns WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
        await self.db.commit()

    async def get_warns_count(self, user_id: int, chat_id: int) -> int:
        async with self.db.execute(
            "SELECT count FROM warns WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
        ) as cur:
            row = await cur.fetchone()
            return row['count'] if row else 0

    async def get_warn_info(self, user_id: int, chat_id: int) -> Optional[Dict]:
        async with self.db.execute(
            "SELECT * FROM warns WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_warn_history(self, user_id: int, chat_id: int, limit: int = 10) -> List[Dict]:
        async with self.db.execute(
            "SELECT * FROM warn_history WHERE user_id = ? AND chat_id = ? ORDER BY warned_at DESC LIMIT ?",
            (user_id, chat_id, limit)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_warns_list(self, chat_id: int) -> List[Dict]:
        async with self.db.execute(
            "SELECT * FROM warns WHERE chat_id = ? AND count > 0 ORDER BY count DESC", (chat_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # =========================================================================
    # НИКИ
    # =========================================================================

    async def set_nick(self, user_id: int, chat_id: int, nick: str):
        await self.db.execute("""
            INSERT INTO nicks (user_id, chat_id, nick) VALUES (?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET nick = excluded.nick
        """, (user_id, chat_id, nick))
        await self.db.commit()

    async def remove_nick(self, user_id: int, chat_id: int):
        await self.db.execute("DELETE FROM nicks WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
        await self.db.commit()

    async def get_nick(self, user_id: int, chat_id: int) -> Optional[str]:
        async with self.db.execute(
            "SELECT nick FROM nicks WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
        ) as cur:
            row = await cur.fetchone()
            return row['nick'] if row else None

    async def get_user_by_nick(self, nick: str, chat_id: int) -> Optional[int]:
        async with self.db.execute(
            "SELECT user_id FROM nicks WHERE nick = ? COLLATE NOCASE AND chat_id = ?", (nick, chat_id)
        ) as cur:
            row = await cur.fetchone()
            return row['user_id'] if row else None

    async def get_nicks(self, chat_id: int) -> List[Dict]:
        async with self.db.execute("SELECT * FROM nicks WHERE chat_id = ?", (chat_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def clear_all_nicks(self, chat_id: int):
        await self.db.execute("DELETE FROM nicks WHERE chat_id = ?", (chat_id,))
        await self.db.commit()

    # =========================================================================
    # ЗАПРЕЩЁННЫЕ СЛОВА
    # =========================================================================

    async def add_banword(self, chat_id: int, word: str):
        await self.db.execute("INSERT OR IGNORE INTO banwords (chat_id, word) VALUES (?, ?)", (chat_id, word.lower()))
        await self.db.commit()

    async def remove_banword(self, chat_id: int, word: str):
        await self.db.execute("DELETE FROM banwords WHERE chat_id = ? AND word = ? COLLATE NOCASE", (chat_id, word.lower()))
        await self.db.commit()

    async def get_banwords(self, chat_id: int) -> List[str]:
        async with self.db.execute("SELECT word FROM banwords WHERE chat_id = ?", (chat_id,)) as cur:
            return [r['word'] for r in await cur.fetchall()]

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

    async def toggle_silence(self, chat_id: int) -> bool:
        chat = await self.get_chat(chat_id)
        new_val = 0 if chat and chat.get('silence') else 1
        await self.db.execute("UPDATE chats SET silence = ? WHERE chat_id = ?", (new_val, chat_id))
        await self.db.commit()
        return bool(new_val)

    async def is_silence(self, chat_id: int) -> bool:
        chat = await self.get_chat(chat_id)
        return bool(chat and chat.get('silence'))

    async def toggle_antiflood(self, chat_id: int) -> bool:
        chat = await self.get_chat(chat_id)
        new_val = 0 if chat and chat.get('antiflood') else 1
        await self.db.execute("UPDATE chats SET antiflood = ? WHERE chat_id = ?", (new_val, chat_id))
        await self.db.commit()
        return bool(new_val)

    async def is_antiflood(self, chat_id: int) -> bool:
        chat = await self.get_chat(chat_id)
        return bool(chat and chat.get('antiflood'))

    async def toggle_filter(self, chat_id: int) -> bool:
        chat = await self.get_chat(chat_id)
        new_val = 0 if chat and chat.get('filter') else 1
        await self.db.execute("UPDATE chats SET filter = ? WHERE chat_id = ?", (new_val, chat_id))
        await self.db.commit()
        return bool(new_val)

    async def is_filter(self, chat_id: int) -> bool:
        chat = await self.get_chat(chat_id)
        return bool(chat and chat.get('filter'))

    # =========================================================================
    # СООБЩЕНИЯ
    # =========================================================================

    async def add_message(self, user_id: int, chat_id: int, message_id: int):
        await self.db.execute(
            "INSERT INTO messages (user_id, chat_id, message_id) VALUES (?, ?, ?)",
            (user_id, chat_id, message_id)
        )
        await self.db.commit()

    async def get_message_count(self, user_id: int, chat_id: int) -> int:
        async with self.db.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
        ) as cur:
            row = await cur.fetchone()
            return row['cnt'] if row else 0

    async def get_last_messages(self, user_id: int, chat_id: int, count: int = 3) -> List[Dict]:
        async with self.db.execute(
            "SELECT * FROM messages WHERE user_id = ? AND chat_id = ? ORDER BY sent_at DESC LIMIT ?",
            (user_id, chat_id, count)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def check_spam(self, user_id: int, chat_id: int, interval: int = 2, count: int = 3) -> bool:
        msgs = await self.get_last_messages(user_id, chat_id, count)
        if len(msgs) < count:
            return False
        return (msgs[0]['sent_at'] - msgs[-1]['sent_at']) < interval

    async def get_user_messages(self, user_id: int, chat_id: int, limit: int = 100) -> List[int]:
        async with self.db.execute(
            "SELECT message_id FROM messages WHERE user_id = ? AND chat_id = ? ORDER BY sent_at DESC LIMIT ?",
            (user_id, chat_id, limit)
        ) as cur:
            return [r['message_id'] for r in await cur.fetchall()]

    async def clear_user_messages(self, user_id: int, chat_id: int):
        await self.db.execute("DELETE FROM messages WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
        await self.db.commit()

    async def get_top_users(self, chat_id: int, limit: int = 10) -> List[Tuple[int, int]]:
        async with self.db.execute(
            "SELECT user_id, COUNT(*) as cnt FROM messages WHERE chat_id = ? GROUP BY user_id ORDER BY cnt DESC LIMIT ?",
            (chat_id, limit)
        ) as cur:
            return [(r['user_id'], r['cnt']) for r in await cur.fetchall()]

    async def close(self):
        if self.db:
            await self.db.close()
