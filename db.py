"""Database module v8.1 — полный рефакторинг"""

import aiosqlite
from typing import Optional, List, Dict, Tuple
import time
import logging

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.db: Optional[aiosqlite.Connection] = None

    async def init(self):
        self.db = await aiosqlite.connect(self.db_path)
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA busy_timeout=5000")
        await self._create_tables()
        await self._migrate()

    async def close(self):
        if self.db:
            await self.db.close()

    async def _create_tables(self):
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                title TEXT DEFAULT '',
                welcome_text TEXT DEFAULT '',
                antiflood INTEGER DEFAULT 0,
                filter INTEGER DEFAULT 0,
                ro_mode INTEGER DEFAULT 0,
                quiet_mode INTEGER DEFAULT 0,
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
                user_id INTEGER, chat_id INTEGER, role INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, chat_id)
            );
            CREATE TABLE IF NOT EXISTS nicks (
                user_id INTEGER, chat_id INTEGER, nick TEXT,
                PRIMARY KEY (user_id, chat_id)
            );
            CREATE TABLE IF NOT EXISTS username_cache (
                user_id INTEGER PRIMARY KEY,
                username TEXT COLLATE NOCASE,
                updated_at INTEGER DEFAULT (strftime('%s', 'now'))
            );
            CREATE TABLE IF NOT EXISTS bans (
                user_id INTEGER, chat_id INTEGER, banned_by INTEGER,
                reason TEXT, until INTEGER DEFAULT 0,
                banned_at INTEGER DEFAULT (strftime('%s', 'now')),
                PRIMARY KEY (user_id, chat_id)
            );
            CREATE TABLE IF NOT EXISTS mutes (
                user_id INTEGER, chat_id INTEGER, muted_by INTEGER,
                reason TEXT, until INTEGER,
                muted_at INTEGER DEFAULT (strftime('%s', 'now')),
                PRIMARY KEY (user_id, chat_id)
            );
            CREATE TABLE IF NOT EXISTS warns (
                user_id INTEGER, chat_id INTEGER, count INTEGER DEFAULT 0,
                warned_by INTEGER, reason TEXT,
                warned_at INTEGER DEFAULT (strftime('%s', 'now')),
                PRIMARY KEY (user_id, chat_id)
            );
            CREATE TABLE IF NOT EXISTS banwords (
                chat_id INTEGER, word TEXT COLLATE NOCASE,
                PRIMARY KEY (chat_id, word)
            );
            CREATE TABLE IF NOT EXISTS action_cache (
                key TEXT PRIMARY KEY, data TEXT,
                cached_at INTEGER DEFAULT (strftime('%s', 'now'))
            );
            CREATE TABLE IF NOT EXISTS user_reg (
                user_id INTEGER, chat_id INTEGER,
                reg_at INTEGER DEFAULT (strftime('%s', 'now')),
                PRIMARY KEY (user_id, chat_id)
            );
            CREATE TABLE IF NOT EXISTS message_counts (
                user_id INTEGER, chat_id INTEGER, count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, chat_id)
            );
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id INTEGER,
                chat_id INTEGER,
                message_id INTEGER,
                thread_id INTEGER DEFAULT 0,
                reason TEXT DEFAULT '',
                status TEXT DEFAULT 'open',
                accepted_by INTEGER DEFAULT 0,
                created_at INTEGER DEFAULT (strftime('%s', 'now'))
            );
            CREATE TABLE IF NOT EXISTS spam_track (
                user_id INTEGER, chat_id INTEGER, ts INTEGER,
                PRIMARY KEY (user_id, chat_id)
            );
            CREATE INDEX IF NOT EXISTS idx_uname_cache ON username_cache(username);
            CREATE INDEX IF NOT EXISTS idx_msg_counts ON message_counts(chat_id, count);
        """)
        await self.db.commit()

    async def _migrate(self):
        try:
            async with self.db.execute("PRAGMA table_info(chats)") as cur:
                cols = [row[1] for row in await cur.fetchall()]
            for col, default in [("quiet_mode", 0), ("filter", 0), ("antiflood", 0)]:
                if col not in cols:
                    await self.db.execute(f"ALTER TABLE chats ADD COLUMN {col} INTEGER DEFAULT {default}")
            await self.db.commit()
        except Exception as e:
            logger.warning(f"migrate: {e}")

    # === ЧАТЫ ===
    async def register_chat(self, chat_id, title=""):
        await self.db.execute("INSERT INTO chats (chat_id, title) VALUES (?,?) ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title", (chat_id, title))
        await self.db.commit()

    async def get_all_chat_ids(self):
        async with self.db.execute("SELECT chat_id FROM chats") as cur:
            return [r[0] for r in await cur.fetchall()]

    async def get_chat_title(self, chat_id):
        async with self.db.execute("SELECT title FROM chats WHERE chat_id=?", (chat_id,)) as cur:
            r = await cur.fetchone()
            return r[0] if r else str(chat_id)

    async def get_chat_count(self):
        async with self.db.execute("SELECT COUNT(*) FROM chats") as cur:
            return (await cur.fetchone())[0]

    # === USERNAME CACHE ===
    async def cache_username(self, user_id, username):
        if not username: return
        username = username.lower().lstrip("@")
        await self.db.execute("INSERT INTO username_cache (user_id,username,updated_at) VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, updated_at=excluded.updated_at", (user_id, username, int(time.time())))
        await self.db.commit()

    async def get_user_by_username(self, username):
        username = username.lower().lstrip("@")
        async with self.db.execute("SELECT user_id FROM username_cache WHERE username=? COLLATE NOCASE ORDER BY updated_at DESC LIMIT 1", (username,)) as cur:
            r = await cur.fetchone()
            return r[0] if r else None

    async def get_username_by_id(self, user_id):
        async with self.db.execute("SELECT username FROM username_cache WHERE user_id=?", (user_id,)) as cur:
            r = await cur.fetchone()
            if r and r[0]: return r[0]
        async with self.db.execute("SELECT username FROM global_roles WHERE user_id=? AND username IS NOT NULL AND username!=''", (user_id,)) as cur:
            r = await cur.fetchone()
            return r[0] if r else None

    # === РОЛИ ===
    async def set_global_role(self, user_id, role, username=None):
        await self.db.execute("INSERT INTO global_roles (user_id,username,role) VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET role=excluded.role, username=COALESCE(excluded.username, global_roles.username)", (user_id, username, role))
        await self.db.commit()
        if username: await self.cache_username(user_id, username)

    async def get_global_role(self, user_id):
        async with self.db.execute("SELECT role FROM global_roles WHERE user_id=?", (user_id,)) as cur:
            r = await cur.fetchone()
            return r[0] if r else 0

    async def get_all_staff(self):
        async with self.db.execute("SELECT user_id, role FROM global_roles WHERE role>0 ORDER BY role DESC") as cur:
            return [(r[0], r[1]) for r in await cur.fetchall()]

    async def set_user_role(self, user_id, chat_id, role):
        if role == 0:
            await self.db.execute("DELETE FROM user_roles WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        else:
            await self.db.execute("INSERT INTO user_roles (user_id,chat_id,role) VALUES (?,?,?) ON CONFLICT(user_id,chat_id) DO UPDATE SET role=excluded.role", (user_id, chat_id, role))
        await self.db.commit()

    async def get_user_role(self, user_id, chat_id):
        async with self.db.execute("SELECT role FROM user_roles WHERE user_id=? AND chat_id=?", (user_id, chat_id)) as cur:
            r = await cur.fetchone()
            return r[0] if r else 0

    async def remove_all_user_roles(self, user_id):
        await self.db.execute("DELETE FROM user_roles WHERE user_id=?", (user_id,))
        await self.db.execute("DELETE FROM global_roles WHERE user_id=?", (user_id,))
        await self.db.commit()

    # === ГЛОБАЛЬНЫЙ БАН ===
    async def add_global_ban(self, user_id, banned_by, reason):
        await self.db.execute("INSERT INTO global_bans (user_id,banned_by,reason) VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET banned_by=excluded.banned_by, reason=excluded.reason, banned_at=strftime('%s','now')", (user_id, banned_by, reason))
        await self.db.commit()

    async def remove_global_ban(self, user_id):
        await self.db.execute("DELETE FROM global_bans WHERE user_id=?", (user_id,))
        await self.db.commit()

    async def is_globally_banned(self, user_id):
        async with self.db.execute("SELECT 1 FROM global_bans WHERE user_id=?", (user_id,)) as cur:
            return await cur.fetchone() is not None

    async def get_global_ban_info(self, user_id):
        async with self.db.execute("SELECT * FROM global_bans WHERE user_id=?", (user_id,)) as cur:
            r = await cur.fetchone()
            return dict(r) if r else None

    # === БАНЫ ===
    async def add_ban(self, user_id, chat_id, banned_by, reason, until=0):
        await self.db.execute("INSERT INTO bans (user_id,chat_id,banned_by,reason,until) VALUES (?,?,?,?,?) ON CONFLICT(user_id,chat_id) DO UPDATE SET banned_by=excluded.banned_by, reason=excluded.reason, until=excluded.until, banned_at=strftime('%s','now')", (user_id, chat_id, banned_by, reason, until))
        await self.db.commit()

    async def remove_ban(self, user_id, chat_id):
        await self.db.execute("DELETE FROM bans WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        await self.db.commit()

    async def is_banned(self, user_id, chat_id):
        async with self.db.execute("SELECT 1 FROM bans WHERE user_id=? AND chat_id=?", (user_id, chat_id)) as cur:
            return await cur.fetchone() is not None

    async def get_ban_info(self, user_id, chat_id):
        async with self.db.execute("SELECT * FROM bans WHERE user_id=? AND chat_id=?", (user_id, chat_id)) as cur:
            r = await cur.fetchone()
            return dict(r) if r else None

    async def get_all_bans_paginated(self, page=0, per_page=5, chat_id=0):
        offset = page * per_page
        q = "WHERE chat_id=?" if chat_id else ""
        p = (chat_id,) if chat_id else ()
        async with self.db.execute(f"SELECT COUNT(*) FROM bans {q}", p) as cur:
            total = (await cur.fetchone())[0]
        async with self.db.execute(f"SELECT * FROM bans {q} ORDER BY banned_at DESC LIMIT ? OFFSET ?", p + (per_page, offset)) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        return rows, total

    async def get_all_global_bans_paginated(self, page=0, per_page=5):
        offset = page * per_page
        async with self.db.execute("SELECT COUNT(*) FROM global_bans") as cur:
            total = (await cur.fetchone())[0]
        async with self.db.execute("SELECT * FROM global_bans ORDER BY banned_at DESC LIMIT ? OFFSET ?", (per_page, offset)) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        return rows, total

    # === МУТЫ ===
    async def add_mute(self, user_id, chat_id, muted_by, reason, until):
        await self.db.execute("INSERT INTO mutes (user_id,chat_id,muted_by,reason,until) VALUES (?,?,?,?,?) ON CONFLICT(user_id,chat_id) DO UPDATE SET muted_by=excluded.muted_by, reason=excluded.reason, until=excluded.until, muted_at=strftime('%s','now')", (user_id, chat_id, muted_by, reason, until))
        await self.db.commit()

    async def remove_mute(self, user_id, chat_id):
        await self.db.execute("DELETE FROM mutes WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        await self.db.commit()

    async def is_muted(self, user_id, chat_id):
        now = int(time.time())
        async with self.db.execute("SELECT until FROM mutes WHERE user_id=? AND chat_id=?", (user_id, chat_id)) as cur:
            r = await cur.fetchone()
            if not r: return False
            until = r[0]
            if until == 0: return True
            if until > now: return True
            await self.remove_mute(user_id, chat_id)
            return False

    async def get_mute_info(self, user_id, chat_id):
        async with self.db.execute("SELECT * FROM mutes WHERE user_id=? AND chat_id=?", (user_id, chat_id)) as cur:
            r = await cur.fetchone()
            return dict(r) if r else None

    # === ВАРНЫ ===
    async def add_warn(self, user_id, chat_id, warned_by, reason):
        async with self.db.execute("SELECT count FROM warns WHERE user_id=? AND chat_id=?", (user_id, chat_id)) as cur:
            r = await cur.fetchone()
            current = r[0] if r else 0
        nc = current + 1
        await self.db.execute("INSERT INTO warns (user_id,chat_id,count,warned_by,reason) VALUES (?,?,?,?,?) ON CONFLICT(user_id,chat_id) DO UPDATE SET count=excluded.count, warned_by=excluded.warned_by, reason=excluded.reason, warned_at=strftime('%s','now')", (user_id, chat_id, nc, warned_by, reason))
        await self.db.commit()
        return nc

    async def remove_warn(self, user_id, chat_id):
        async with self.db.execute("SELECT count FROM warns WHERE user_id=? AND chat_id=?", (user_id, chat_id)) as cur:
            r = await cur.fetchone()
            current = r[0] if r else 0
        if current <= 0: return 0
        nc = current - 1
        if nc == 0:
            await self.db.execute("DELETE FROM warns WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        else:
            await self.db.execute("UPDATE warns SET count=? WHERE user_id=? AND chat_id=?", (nc, user_id, chat_id))
        await self.db.commit()
        return nc

    async def get_warns(self, user_id, chat_id):
        async with self.db.execute("SELECT count FROM warns WHERE user_id=? AND chat_id=?", (user_id, chat_id)) as cur:
            r = await cur.fetchone()
            return r[0] if r else 0

    async def get_warn_info(self, user_id, chat_id):
        async with self.db.execute("SELECT * FROM warns WHERE user_id=? AND chat_id=?", (user_id, chat_id)) as cur:
            r = await cur.fetchone()
            return dict(r) if r else None

    async def clear_warns(self, user_id, chat_id):
        await self.db.execute("DELETE FROM warns WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        await self.db.commit()

    async def get_all_warns_paginated(self, page=0, per_page=5, chat_id=0):
        offset = page * per_page
        q = "WHERE chat_id=? AND count>0" if chat_id else "WHERE count>0"
        p = (chat_id,) if chat_id else ()
        async with self.db.execute(f"SELECT COUNT(*) FROM warns {q}", p) as cur:
            total = (await cur.fetchone())[0]
        async with self.db.execute(f"SELECT * FROM warns {q} ORDER BY warned_at DESC LIMIT ? OFFSET ?", p + (per_page, offset)) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        return rows, total

    async def get_user_all_punishments(self, user_id):
        result = {"warns": [], "mutes": [], "bans": [], "global_ban": None}
        async with self.db.execute("SELECT * FROM warns WHERE user_id=? AND count>0", (user_id,)) as cur:
            result["warns"] = [dict(r) for r in await cur.fetchall()]
        now = int(time.time())
        async with self.db.execute("SELECT * FROM mutes WHERE user_id=?", (user_id,)) as cur:
            for r in await cur.fetchall():
                d = dict(r)
                if d["until"] == 0 or d["until"] > now:
                    result["mutes"].append(d)
        async with self.db.execute("SELECT * FROM bans WHERE user_id=?", (user_id,)) as cur:
            result["bans"] = [dict(r) for r in await cur.fetchall()]
        result["global_ban"] = await self.get_global_ban_info(user_id)
        return result

    # === КЭШ ===
    async def cache_action(self, key, data):
        await self.db.execute("INSERT INTO action_cache (key,data) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data, cached_at=strftime('%s','now')", (key, data))
        await self.db.commit()

    async def get_cached_action(self, key):
        async with self.db.execute("SELECT data FROM action_cache WHERE key=?", (key,)) as cur:
            r = await cur.fetchone()
            return r[0] if r else None

    async def clear_cached_action(self, key):
        await self.db.execute("DELETE FROM action_cache WHERE key=?", (key,))
        await self.db.commit()

    async def cleanup_old_cache(self, max_age=3600):
        cutoff = int(time.time()) - max_age
        await self.db.execute("DELETE FROM action_cache WHERE cached_at<?", (cutoff,))
        await self.db.commit()

    # === НИКИ ===
    async def set_nick(self, user_id, chat_id, nick):
        await self.db.execute("INSERT INTO nicks (user_id,chat_id,nick) VALUES (?,?,?) ON CONFLICT(user_id,chat_id) DO UPDATE SET nick=excluded.nick", (user_id, chat_id, nick))
        await self.db.commit()

    async def get_nick(self, user_id, chat_id):
        async with self.db.execute("SELECT nick FROM nicks WHERE user_id=? AND chat_id=?", (user_id, chat_id)) as cur:
            r = await cur.fetchone()
            return r[0] if r else None

    async def remove_nick(self, user_id, chat_id):
        await self.db.execute("DELETE FROM nicks WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        await self.db.commit()

    async def remove_nick_all(self, user_id):
        await self.db.execute("DELETE FROM nicks WHERE user_id=?", (user_id,))
        await self.db.commit()

    async def set_nick_all(self, user_id, nick, chat_ids):
        for cid in chat_ids:
            await self.db.execute("INSERT INTO nicks (user_id,chat_id,nick) VALUES (?,?,?) ON CONFLICT(user_id,chat_id) DO UPDATE SET nick=excluded.nick", (user_id, cid, nick))
        await self.db.commit()

    async def get_user_by_nick(self, nick, chat_id):
        async with self.db.execute("SELECT user_id FROM nicks WHERE nick=? COLLATE NOCASE AND chat_id=?", (nick, chat_id)) as cur:
            r = await cur.fetchone()
            return r[0] if r else None

    async def get_all_nicks(self, chat_id):
        async with self.db.execute("SELECT user_id, nick FROM nicks WHERE chat_id=? ORDER BY nick", (chat_id,)) as cur:
            return [(r[0], r[1]) for r in await cur.fetchall()]

    async def get_user_by_nick_any_chat(self, nick):
        async with self.db.execute("SELECT user_id FROM nicks WHERE nick=? COLLATE NOCASE LIMIT 1", (nick,)) as cur:
            r = await cur.fetchone()
            return r[0] if r else None

    # === НАСТРОЙКИ ЧАТА ===
    async def get_welcome(self, chat_id):
        async with self.db.execute("SELECT welcome_text FROM chats WHERE chat_id=?", (chat_id,)) as cur:
            r = await cur.fetchone()
            return r[0] if r and r[0] else None

    async def set_welcome(self, chat_id, text):
        await self.db.execute("UPDATE chats SET welcome_text=? WHERE chat_id=?", (text, chat_id))
        await self.db.commit()

    async def set_ro_mode(self, chat_id, enabled):
        await self.db.execute("UPDATE chats SET ro_mode=? WHERE chat_id=?", (1 if enabled else 0, chat_id))
        await self.db.commit()

    async def is_ro_mode(self, chat_id):
        try:
            async with self.db.execute("SELECT ro_mode FROM chats WHERE chat_id=?", (chat_id,)) as cur:
                r = await cur.fetchone()
                return bool(r[0]) if r else False
        except: return False

    async def set_quiet_mode(self, chat_id, enabled):
        await self.db.execute("UPDATE chats SET quiet_mode=? WHERE chat_id=?", (1 if enabled else 0, chat_id))
        await self.db.commit()

    async def is_quiet_mode(self, chat_id):
        try:
            async with self.db.execute("SELECT quiet_mode FROM chats WHERE chat_id=?", (chat_id,)) as cur:
                r = await cur.fetchone()
                return bool(r[0]) if r else False
        except: return False

    async def set_antiflood(self, chat_id, enabled):
        await self.db.execute("UPDATE chats SET antiflood=? WHERE chat_id=?", (1 if enabled else 0, chat_id))
        await self.db.commit()

    async def is_antiflood(self, chat_id):
        async with self.db.execute("SELECT antiflood FROM chats WHERE chat_id=?", (chat_id,)) as cur:
            r = await cur.fetchone()
            return bool(r[0]) if r else False

    async def set_filter(self, chat_id, enabled):
        await self.db.execute("UPDATE chats SET filter=? WHERE chat_id=?", (1 if enabled else 0, chat_id))
        await self.db.commit()

    async def is_filter(self, chat_id):
        async with self.db.execute("SELECT filter FROM chats WHERE chat_id=?", (chat_id,)) as cur:
            r = await cur.fetchone()
            return bool(r[0]) if r else False

    # === BANWORDS ===
    async def get_banwords(self, chat_id):
        async with self.db.execute("SELECT word FROM banwords WHERE chat_id=?", (chat_id,)) as cur:
            return [r[0] for r in await cur.fetchall()]

    async def add_banword(self, chat_id, word):
        try:
            await self.db.execute("INSERT INTO banwords (chat_id,word) VALUES (?,?)", (chat_id, word.lower()))
            await self.db.commit()
            return True
        except: return False

    async def remove_banword(self, chat_id, word):
        async with self.db.execute("SELECT 1 FROM banwords WHERE chat_id=? AND word=? COLLATE NOCASE", (chat_id, word)) as cur:
            if not await cur.fetchone(): return False
        await self.db.execute("DELETE FROM banwords WHERE chat_id=? AND word=? COLLATE NOCASE", (chat_id, word))
        await self.db.commit()
        return True

    # === РЕГИСТРАЦИЯ ===
    async def register_user(self, user_id, chat_id):
        await self.db.execute("INSERT OR IGNORE INTO user_reg (user_id,chat_id) VALUES (?,?)", (user_id, chat_id))
        await self.db.commit()

    async def get_user_reg(self, user_id, chat_id):
        async with self.db.execute("SELECT reg_at FROM user_reg WHERE user_id=? AND chat_id=?", (user_id, chat_id)) as cur:
            r = await cur.fetchone()
            return r[0] if r else None

    async def get_user_reg_all(self, user_id):
        async with self.db.execute("SELECT chat_id, reg_at FROM user_reg WHERE user_id=?", (user_id,)) as cur:
            return [(r[0], r[1]) for r in await cur.fetchall()]

    # === СООБЩЕНИЯ ===
    async def increment_message_count(self, user_id, chat_id):
        await self.db.execute("INSERT INTO message_counts (user_id,chat_id,count) VALUES (?,?,1) ON CONFLICT(user_id,chat_id) DO UPDATE SET count=count+1", (user_id, chat_id))
        await self.db.commit()

    async def get_message_count(self, user_id, chat_id=0):
        if chat_id:
            async with self.db.execute("SELECT count FROM message_counts WHERE user_id=? AND chat_id=?", (user_id, chat_id)) as cur:
                r = await cur.fetchone()
                return r[0] if r else 0
        else:
            async with self.db.execute("SELECT SUM(count) FROM message_counts WHERE user_id=?", (user_id,)) as cur:
                r = await cur.fetchone()
                return r[0] if r else 0

    async def get_top_messagers(self, chat_id, limit=10):
        async with self.db.execute("SELECT user_id, count FROM message_counts WHERE chat_id=? ORDER BY count DESC LIMIT ?", (chat_id, limit)) as cur:
            return [(r[0], r[1]) for r in await cur.fetchall()]

    # === РЕПОРТЫ ===
    async def create_report(self, reporter_id, chat_id, message_id, thread_id=0, reason=""):
        await self.db.execute("INSERT INTO reports (reporter_id,chat_id,message_id,thread_id,reason) VALUES (?,?,?,?,?)", (reporter_id, chat_id, message_id, thread_id, reason))
        await self.db.commit()
        async with self.db.execute("SELECT last_insert_rowid()") as cur:
            return (await cur.fetchone())[0]

    async def get_report(self, report_id):
        async with self.db.execute("SELECT * FROM reports WHERE id=?", (report_id,)) as cur:
            r = await cur.fetchone()
            return dict(r) if r else None

    async def accept_report(self, report_id, accepted_by):
        await self.db.execute("UPDATE reports SET status='accepted', accepted_by=? WHERE id=?", (accepted_by, report_id))
        await self.db.commit()

    async def get_open_reports(self, limit=10):
        async with self.db.execute("SELECT * FROM reports WHERE status='open' ORDER BY created_at DESC LIMIT ?", (limit,)) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # === АНТИСПАМ ===
    async def check_spam(self, user_id, chat_id, now, interval=2):
        async with self.db.execute("SELECT ts FROM spam_track WHERE user_id=? AND chat_id=?", (user_id, chat_id)) as cur:
            r = await cur.fetchone()
        if r:
            last_ts = r[0]
            if now - last_ts < interval:
                await self.db.execute("UPDATE spam_track SET ts=? WHERE user_id=? AND chat_id=?", (int(now), user_id, chat_id))
                await self.db.commit()
                return True
        await self.db.execute("INSERT INTO spam_track (user_id,chat_id,ts) VALUES (?,?,?) ON CONFLICT(user_id,chat_id) DO UPDATE SET ts=excluded.ts", (user_id, chat_id, int(now)))
        await self.db.commit()
        return False
