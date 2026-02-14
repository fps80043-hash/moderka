"""
БД — SQLite через aiosqlite.
"""

import os
import time
import aiosqlite
from config import DATABASE_PATH, ROLE_USER, PRESET_STAFF, MODERATED_CHATS

DB_DIR = os.path.dirname(DATABASE_PATH)


async def init_db():
    if DB_DIR and not os.path.exists(DB_DIR):
        os.makedirs(DB_DIR, exist_ok=True)

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT DEFAULT '',
                first_name TEXT DEFAULT '',
                role INTEGER DEFAULT 0,
                interface TEXT DEFAULT '',
                messages_count INTEGER DEFAULT 0,
                warns INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0,
                ban_until REAL DEFAULT 0,
                is_muted INTEGER DEFAULT 0,
                mute_until REAL DEFAULT 0,
                joined_at REAL DEFAULT 0,
                last_seen REAL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS punishments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT,
                reason TEXT DEFAULT '',
                duration REAL DEFAULT 0,
                issued_by INTEGER DEFAULT 0,
                issued_at REAL DEFAULT 0,
                chat_id INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                title TEXT DEFAULT '',
                read_only INTEGER DEFAULT 0,
                antispam INTEGER DEFAULT 0,
                ai_moderation INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS word_filters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                word TEXT
            );
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id INTEGER,
                reported_id INTEGER,
                reason TEXT DEFAULT '',
                chat_id INTEGER DEFAULT 0,
                created_at REAL DEFAULT 0,
                status TEXT DEFAULT 'open'
            );
            CREATE TABLE IF NOT EXISTS global_bans (
                user_id INTEGER PRIMARY KEY,
                reason TEXT DEFAULT '',
                banned_by INTEGER DEFAULT 0,
                banned_at REAL DEFAULT 0
            );
        """)
        await db.commit()

    # Применяем предустановленные роли
    for uid, level in PRESET_STAFF.items():
        await ensure_user(uid)
        await set_role(uid, level)

    # Регистрируем модерируемые чаты
    for cid in MODERATED_CHATS:
        await ensure_chat(cid)


# ===================== USERS =====================

async def get_user(user_id: int) -> dict | None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def ensure_user(user_id: int, username: str = "", first_name: str = ""):
    now = time.time()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)) as cur:
            exists = await cur.fetchone()
        if exists:
            if username or first_name:
                await db.execute(
                    "UPDATE users SET username=?, first_name=?, last_seen=? WHERE user_id=?",
                    (username, first_name, now, user_id))
            else:
                await db.execute("UPDATE users SET last_seen=? WHERE user_id=?", (now, user_id))
        else:
            await db.execute(
                "INSERT INTO users (user_id, username, first_name, joined_at, last_seen) VALUES (?,?,?,?,?)",
                (user_id, username, first_name, now, now))
        await db.commit()


async def set_interface(user_id: int, interface: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET interface=? WHERE user_id=?", (interface, user_id))
        await db.commit()


async def get_interface(user_id: int) -> str:
    u = await get_user(user_id)
    return u["interface"] if u and u["interface"] else ""


async def set_role(user_id: int, role: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET role=? WHERE user_id=?", (role, user_id))
        await db.commit()


async def get_role(user_id: int) -> int:
    u = await get_user(user_id)
    if not u:
        return PRESET_STAFF.get(user_id, ROLE_USER)
    return u["role"] if u["role"] > 0 else PRESET_STAFF.get(user_id, ROLE_USER)


async def increment_messages(user_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE users SET messages_count=messages_count+1, last_seen=? WHERE user_id=?",
            (time.time(), user_id))
        await db.commit()


async def get_all_users(offset: int = 0, limit: int = 10):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users ORDER BY messages_count DESC LIMIT ? OFFSET ?", (limit, offset)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def count_users() -> int:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def find_user(query: str) -> dict | None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        if query.isdigit():
            async with db.execute("SELECT * FROM users WHERE user_id=?", (int(query),)) as cur:
                row = await cur.fetchone()
                if row:
                    return dict(row)
        async with db.execute(
            "SELECT * FROM users WHERE username LIKE ? OR first_name LIKE ?",
            (f"%{query}%", f"%{query}%")
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_top_users(limit: int = 10):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users ORDER BY messages_count DESC LIMIT ?", (limit,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_staff_users():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE role > 0 ORDER BY role DESC") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_online_users(since_seconds: int = 300):
    threshold = time.time() - since_seconds
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE last_seen > ?", (threshold,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ===================== PUNISHMENTS =====================

async def add_warn(user_id: int, reason: str, issued_by: int, chat_id: int = 0) -> int:
    now = time.time()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET warns=warns+1 WHERE user_id=?", (user_id,))
        await db.execute(
            "INSERT INTO punishments (user_id,action,reason,issued_by,issued_at,chat_id) VALUES (?,?,?,?,?,?)",
            (user_id, "warn", reason, issued_by, now, chat_id))
        await db.commit()
    u = await get_user(user_id)
    return u["warns"] if u else 1


async def reset_warns(user_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET warns=0 WHERE user_id=?", (user_id,))
        await db.commit()


async def set_ban(user_id: int, until: float, reason: str, issued_by: int, chat_id: int = 0):
    now = time.time()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET is_banned=1, ban_until=? WHERE user_id=?", (until, user_id))
        await db.execute(
            "INSERT INTO punishments (user_id,action,reason,duration,issued_by,issued_at,chat_id) VALUES (?,?,?,?,?,?,?)",
            (user_id, "ban", reason, until, issued_by, now, chat_id))
        await db.commit()


async def remove_ban(user_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET is_banned=0, ban_until=0 WHERE user_id=?", (user_id,))
        await db.commit()


async def set_mute(user_id: int, until: float, reason: str, issued_by: int, chat_id: int = 0):
    now = time.time()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET is_muted=1, mute_until=? WHERE user_id=?", (until, user_id))
        await db.execute(
            "INSERT INTO punishments (user_id,action,reason,duration,issued_by,issued_at,chat_id) VALUES (?,?,?,?,?,?,?)",
            (user_id, "mute", reason, until, issued_by, now, chat_id))
        await db.commit()


async def remove_mute(user_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET is_muted=0, mute_until=0 WHERE user_id=?", (user_id,))
        await db.commit()


async def update_ban_duration(user_id: int, new_until: float):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET ban_until=? WHERE user_id=?", (new_until, user_id))
        await db.commit()


async def update_mute_duration(user_id: int, new_until: float):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET mute_until=? WHERE user_id=?", (new_until, user_id))
        await db.commit()


async def add_global_ban(user_id: int, reason: str, banned_by: int):
    now = time.time()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO global_bans (user_id,reason,banned_by,banned_at) VALUES (?,?,?,?)",
            (user_id, reason, banned_by, now))
        await db.execute("UPDATE users SET is_banned=1, ban_until=0 WHERE user_id=?", (user_id,))
        await db.commit()


async def is_global_banned(user_id: int) -> bool:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT user_id FROM global_bans WHERE user_id=?", (user_id,)) as cur:
            return await cur.fetchone() is not None


async def get_user_punishments(user_id: int, limit: int = 20):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM punishments WHERE user_id=? ORDER BY issued_at DESC LIMIT ?",
            (user_id, limit)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ===================== CHATS =====================

async def ensure_chat(chat_id: int, title: str = ""):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT chat_id FROM chats WHERE chat_id=?", (chat_id,)) as cur:
            exists = await cur.fetchone()
        if exists:
            if title:
                await db.execute("UPDATE chats SET title=? WHERE chat_id=?", (title, chat_id))
        else:
            await db.execute("INSERT INTO chats (chat_id, title) VALUES (?,?)", (chat_id, title))
        await db.commit()


async def get_chat(chat_id: int) -> dict | None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM chats WHERE chat_id=?", (chat_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_all_chats():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM chats") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def set_chat_read_only(chat_id: int, enabled: bool):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE chats SET read_only=? WHERE chat_id=?", (1 if enabled else 0, chat_id))
        await db.commit()


async def set_chat_antispam(chat_id: int, enabled: bool):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE chats SET antispam=? WHERE chat_id=?", (1 if enabled else 0, chat_id))
        await db.commit()


async def set_chat_ai_moderation(chat_id: int, enabled: bool):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE chats SET ai_moderation=? WHERE chat_id=?", (1 if enabled else 0, chat_id))
        await db.commit()


# ===================== WORD FILTERS =====================

async def add_word_filter(chat_id: int, word: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("INSERT INTO word_filters (chat_id, word) VALUES (?,?)", (chat_id, word.lower()))
        await db.commit()


async def remove_word_filter(chat_id: int, word: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("DELETE FROM word_filters WHERE chat_id=? AND word=?", (chat_id, word.lower()))
        await db.commit()


async def get_word_filters(chat_id: int) -> list[str]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT word FROM word_filters WHERE chat_id=?", (chat_id,)) as cur:
            return [r[0] for r in await cur.fetchall()]


# ===================== REPORTS =====================

async def add_report(reporter_id: int, reported_id: int, reason: str, chat_id: int = 0):
    now = time.time()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO reports (reporter_id,reported_id,reason,chat_id,created_at) VALUES (?,?,?,?,?)",
            (reporter_id, reported_id, reason, chat_id, now))
        await db.commit()


async def get_open_reports(limit: int = 20):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM reports WHERE status='open' ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def close_report(report_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE reports SET status='closed' WHERE id=?", (report_id,))
        await db.commit()
