"""
Database module for Group Moderation Bot
"""

import aiosqlite
from typing import Optional, Any
from datetime import datetime


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
        """Создание таблиц"""
        await self.db.executescript("""
            -- Зарегистрированные чаты
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                title TEXT,
                created_at INTEGER DEFAULT (strftime('%s', 'now'))
            );
            
            -- Глобальные роли (для всей команды)
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
            
            -- Роли пользователей в чатах
            CREATE TABLE IF NOT EXISTS user_roles (
                user_id INTEGER,
                chat_id INTEGER,
                role INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, chat_id)
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
            
            -- Предупреждения
            CREATE TABLE IF NOT EXISTS warns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                warned_by INTEGER,
                reason TEXT,
                warned_at INTEGER DEFAULT (strftime('%s', 'now'))
            );
            
            -- Настройки чатов
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER,
                key TEXT,
                value TEXT,
                PRIMARY KEY (chat_id, key)
            );
            
            -- Запрещённые слова
            CREATE TABLE IF NOT EXISTS banwords (
                chat_id INTEGER,
                word TEXT,
                PRIMARY KEY (chat_id, word)
            );
        """)
        await self.db.commit()
    
    # =========================================================================
    # ЧАТЫ
    # =========================================================================
    
    async def register_chat(self, chat_id: int, title: str):
        """Регистрация чата"""
        await self.db.execute(
            "INSERT OR REPLACE INTO chats (chat_id, title) VALUES (?, ?)",
            (chat_id, title)
        )
        await self.db.commit()
    
    async def get_all_chats(self) -> list[dict]:
        """Получить все чаты"""
        async with self.db.execute("SELECT * FROM chats") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    # =========================================================================
    # ГЛОБАЛЬНЫЕ РОЛИ
    # =========================================================================
    
    async def set_global_role(self, user_id: int, role: int, username: str = None):
        """Установить глобальную роль"""
        await self.db.execute(
            "INSERT OR REPLACE INTO global_roles (user_id, username, role) VALUES (?, ?, ?)",
            (user_id, username, role)
        )
        await self.db.commit()
    
    async def get_global_role(self, user_id: int) -> int:
        """Получить глобальную роль"""
        async with self.db.execute(
            "SELECT role FROM global_roles WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row['role'] if row else 0
    
    async def remove_global_role(self, user_id: int):
        """Удалить глобальную роль"""
        await self.db.execute(
            "DELETE FROM global_roles WHERE user_id = ?",
            (user_id,)
        )
        await self.db.commit()
    
    async def is_global_admin(self, user_id: int) -> bool:
        """Проверить является ли глобальным админом (роль 9+)"""
        role = await self.get_global_role(user_id)
        return role >= 9
    
    async def get_all_staff(self) -> list[dict]:
        """Получить весь состав команды"""
        async with self.db.execute(
            "SELECT * FROM global_roles WHERE role > 0 ORDER BY role DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    async def add_global_admin(self, user_id: int):
        """Добавить глобального админа (для совместимости)"""
        await self.set_global_role(user_id, 9)
    
    # =========================================================================
    # ГЛОБАЛЬНЫЙ БАН
    # =========================================================================
    
    async def add_global_ban(self, user_id: int, banned_by: int, reason: str):
        """Добавить в глобальный бан"""
        await self.db.execute(
            "INSERT OR REPLACE INTO global_bans (user_id, banned_by, reason) VALUES (?, ?, ?)",
            (user_id, banned_by, reason)
        )
        await self.db.commit()
    
    async def remove_global_ban(self, user_id: int):
        """Удалить из глобального бана"""
        await self.db.execute(
            "DELETE FROM global_bans WHERE user_id = ?",
            (user_id,)
        )
        await self.db.commit()
    
    async def get_global_ban(self, user_id: int) -> Optional[dict]:
        """Получить информацию о глобальном бане"""
        async with self.db.execute(
            "SELECT * FROM global_bans WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None
    
    async def get_global_bans(self) -> list[dict]:
        """Получить список глобальных банов"""
        async with self.db.execute(
            "SELECT * FROM global_bans ORDER BY banned_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    # =========================================================================
    # РОЛИ
    # =========================================================================
    
    async def get_user_role(self, user_id: int, chat_id: int) -> int:
        """Получить роль пользователя"""
        async with self.db.execute(
            "SELECT role FROM user_roles WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        ) as cursor:
            row = await cursor.fetchone()
            return row['role'] if row else 0
    
    async def set_user_role(self, user_id: int, chat_id: int, role: int):
        """Установить роль пользователя"""
        await self.db.execute(
            "INSERT OR REPLACE INTO user_roles (user_id, chat_id, role) VALUES (?, ?, ?)",
            (user_id, chat_id, role)
        )
        await self.db.commit()
    
    # =========================================================================
    # ЛОКАЛЬНЫЕ БАНЫ
    # =========================================================================
    
    async def add_ban(self, user_id: int, chat_id: int, banned_by: int, reason: str):
        """Добавить бан"""
        await self.db.execute(
            "INSERT OR REPLACE INTO bans (user_id, chat_id, banned_by, reason) VALUES (?, ?, ?, ?)",
            (user_id, chat_id, banned_by, reason)
        )
        await self.db.commit()
    
    async def remove_ban(self, user_id: int, chat_id: int):
        """Удалить бан"""
        await self.db.execute(
            "DELETE FROM bans WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        )
        await self.db.commit()
    
    async def get_ban(self, user_id: int, chat_id: int) -> Optional[dict]:
        """Получить информацию о бане"""
        async with self.db.execute(
            "SELECT * FROM bans WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None
    
    # =========================================================================
    # МУТЫ
    # =========================================================================
    
    async def add_mute(self, user_id: int, chat_id: int, muted_by: int, reason: str, until: float):
        """Добавить мут"""
        await self.db.execute(
            "INSERT OR REPLACE INTO mutes (user_id, chat_id, muted_by, reason, until) VALUES (?, ?, ?, ?, ?)",
            (user_id, chat_id, muted_by, reason, int(until))
        )
        await self.db.commit()
    
    async def remove_mute(self, user_id: int, chat_id: int):
        """Удалить мут"""
        await self.db.execute(
            "DELETE FROM mutes WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        )
        await self.db.commit()
    
    async def get_mute(self, user_id: int, chat_id: int) -> Optional[dict]:
        """Получить информацию о муте"""
        async with self.db.execute(
            "SELECT * FROM mutes WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None
    
    # =========================================================================
    # ПРЕДУПРЕЖДЕНИЯ
    # =========================================================================
    
    async def add_warn(self, user_id: int, chat_id: int, warned_by: int, reason: str):
        """Добавить варн"""
        await self.db.execute(
            "INSERT INTO warns (user_id, chat_id, warned_by, reason) VALUES (?, ?, ?, ?)",
            (user_id, chat_id, warned_by, reason)
        )
        await self.db.commit()
    
    async def remove_warn(self, user_id: int, chat_id: int):
        """Удалить один варн"""
        await self.db.execute(
            "DELETE FROM warns WHERE id = (SELECT id FROM warns WHERE user_id = ? AND chat_id = ? ORDER BY warned_at DESC LIMIT 1)",
            (user_id, chat_id)
        )
        await self.db.commit()
    
    async def get_warns_count(self, user_id: int, chat_id: int) -> int:
        """Получить количество варнов"""
        async with self.db.execute(
            "SELECT COUNT(*) as count FROM warns WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        ) as cursor:
            row = await cursor.fetchone()
            return row['count'] if row else 0
    
    async def clear_warns(self, user_id: int, chat_id: int):
        """Очистить все варны"""
        await self.db.execute(
            "DELETE FROM warns WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        )
        await self.db.commit()
    
    # =========================================================================
    # НАСТРОЙКИ
    # =========================================================================
    
    async def get_setting(self, chat_id: int, key: str) -> Optional[str]:
        """Получить настройку"""
        async with self.db.execute(
            "SELECT value FROM chat_settings WHERE chat_id = ? AND key = ?",
            (chat_id, key)
        ) as cursor:
            row = await cursor.fetchone()
            return row['value'] if row else None
    
    async def set_setting(self, chat_id: int, key: str, value: str):
        """Установить настройку"""
        await self.db.execute(
            "INSERT OR REPLACE INTO chat_settings (chat_id, key, value) VALUES (?, ?, ?)",
            (chat_id, key, value)
        )
        await self.db.commit()
    
    # =========================================================================
    # ЗАПРЕЩЁННЫЕ СЛОВА
    # =========================================================================
    
    async def add_banword(self, chat_id: int, word: str):
        """Добавить запрещённое слово"""
        await self.db.execute(
            "INSERT OR REPLACE INTO banwords (chat_id, word) VALUES (?, ?)",
            (chat_id, word.lower())
        )
        await self.db.commit()
    
    async def remove_banword(self, chat_id: int, word: str):
        """Удалить запрещённое слово"""
        await self.db.execute(
            "DELETE FROM banwords WHERE chat_id = ? AND word = ?",
            (chat_id, word.lower())
        )
        await self.db.commit()
    
    async def get_banwords(self, chat_id: int) -> list[str]:
        """Получить запрещённые слова"""
        async with self.db.execute(
            "SELECT word FROM banwords WHERE chat_id = ?",
            (chat_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [row['word'] for row in rows]
    
    async def close(self):
        """Закрыть соединение"""
        if self.db:
            await self.db.close()
