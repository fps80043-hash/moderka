# Design Document: Inline Moderation Buttons

## Overview

Данный документ описывает техническое решение для добавления инлайн-кнопок модерации и улучшения системы кэширования пользователей в Telegram боте модерации. Решение включает:

1. Добавление inline-кнопок к сообщениям пользователей для быстрой модерации
2. Улучшенную систему кэширования участников чата
3. Команду принудительного обновления кэша
4. Оптимизацию функции parse_user() для использования кэша

## Architecture

### Компоненты системы

```
┌─────────────────────────────────────────────────────────────┐
│                     Telegram Bot (aiogram 3.x)              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────────┐      ┌──────────────────┐           │
│  │  Message Handler │─────▶│  Button Builder  │           │
│  │  (on_message)    │      │  (add_mod_btns)  │           │
│  └──────────────────┘      └──────────────────┘           │
│           │                         │                       │
│           ▼                         ▼                       │
│  ┌──────────────────┐      ┌──────────────────┐           │
│  │ Callback Handler │      │  Moderation Menu │           │
│  │ (cb_mod_action)  │      │  (show_mod_menu) │           │
│  └──────────────────┘      └──────────────────┘           │
│           │                         │                       │
│           └─────────┬───────────────┘                       │
│                     ▼                                       │
│           ┌──────────────────┐                             │
│           │  Cache Manager   │                             │
│           │  (UserCache)     │                             │
│           └──────────────────┘                             │
│                     │                                       │
│                     ▼                                       │
│           ┌──────────────────┐                             │
│           │  Database Layer  │                             │
│           │  (db.py)         │                             │
│           └──────────────────┘                             │
└─────────────────────────────────────────────────────────────┘
```

### Поток данных

1. **Получение сообщения**: `on_message()` → проверка прав модератора → добавление inline-кнопок
2. **Клик по кнопке**: callback → `show_mod_menu()` → отображение меню действий
3. **Выбор действия**: callback → `execute_mod_action()` → выполнение действия → обновление меню
4. **Кэширование**: `UserCache.refresh()` → Telegram API → сохранение в БД

## Components and Interfaces

### 1. UserCache (Менеджер кэша пользователей)

**Назначение**: Управление кэшем участников чата для быстрого поиска

**Класс**: `UserCache`

```python
class UserCache:
    def __init__(self, bot: Bot, db: Database):
        self.bot = bot
        self.db = db
        self.refresh_tasks = {}  # chat_id -> asyncio.Task
    
    async def refresh_chat(self, chat_id: int) -> dict:
        """Обновить кэш участников чата"""
        pass
    
    async def get_user_by_username(self, username: str, chat_id: int) -> Optional[int]:
        """Найти user_id по username в кэше"""
        pass
    
    async def schedule_auto_refresh(self, chat_id: int, interval: int = 21600):
        """Запланировать автоматическое обновление кэша (каждые 6 часов)"""
        pass
    
    async def stop_auto_refresh(self, chat_id: int):
        """Остановить автоматическое обновление для чата"""
        pass
```

**Методы**:

- `refresh_chat(chat_id: int) -> dict`: Получает всех участников через `bot.get_chat_administrators()` и `bot.get_chat_member_count()`, сохраняет в БД
- `get_user_by_username(username: str, chat_id: int) -> Optional[int]`: Ищет в кэше, возвращает user_id
- `schedule_auto_refresh(chat_id: int, interval: int)`: Создает фоновую задачу для периодического обновления
- `stop_auto_refresh(chat_id: int)`: Отменяет фоновую задачу

### 2. Inline Button Builder

**Назначение**: Создание inline-кнопок для сообщений

**Функция**: `add_moderation_buttons(message: Message, moderator_role: int) -> InlineKeyboardMarkup`

```python
async def add_moderation_buttons(message: Message, moderator_role: int) -> Optional[InlineKeyboardMarkup]:
    """
    Добавляет inline-кнопки модерации к сообщению
    
    Args:
        message: Сообщение пользователя
        moderator_role: Роль модератора (для фильтрации доступных действий)
    
    Returns:
        InlineKeyboardMarkup или None если кнопки не нужны
    """
    if moderator_role < 1:
        return None
    
    kb = InlineKeyboardBuilder()
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Кнопка открытия меню модерации
    kb.button(
        text="⚙️ Модерация",
        callback_data=f"mod_menu:{user_id}:{chat_id}"
    )
    
    return kb.as_markup()
```

### 3. Moderation Menu Handler

**Назначение**: Отображение и обработка меню модерации

**Функция**: `show_moderation_menu(callback: CallbackQuery, target_user_id: int, chat_id: int)`

```python
async def show_moderation_menu(callback: CallbackQuery, target_user_id: int, chat_id: int):
    """
    Показывает меню модерации с доступными действиями
    
    Args:
        callback: Callback query от нажатия кнопки
        target_user_id: ID пользователя для модерации
        chat_id: ID чата
    """
    moderator_role = await get_role(callback.from_user.id, chat_id)
    target_role = await get_role(target_user_id, chat_id)
    
    # Проверка прав
    if moderator_role < 1 or target_role >= moderator_role:
        await callback.answer("❌ Недостаточно прав!", show_alert=True)
        return
    
    # Получение статуса пользователя
    warns = await db.get_warns_count(target_user_id, chat_id)
    mute = await db.get_mute(target_user_id, chat_id)
    msg_count = await db.get_message_count(target_user_id, chat_id)
    
    # Формирование текста
    user_info = await get_user_info(target_user_id)
    text = f"⚙️ <b>Модерация пользователя</b>\n\n"
    text += f"<b>Пользователь:</b> {user_info['full_name']}\n"
    text += f"<b>Варнов:</b> {warns}/{MAX_WARNS}\n"
    text += f"<b>Сообщений:</b> {msg_count}\n"
    
    if mute and mute.get('until', 0) > time.time():
        text += f"🔇 <b>Мут до:</b> {format_dt(mute['until'])}\n"
    
    # Создание кнопок
    kb = InlineKeyboardBuilder()
    
    # Варн (роль >= 1)
    if moderator_role >= 1:
        kb.button(text="⚠️ Варн", callback_data=f"mod_warn:{target_user_id}:{chat_id}")
    
    # Мут (роль >= 1)
    if moderator_role >= 1:
        kb.button(text="🔇 Мут", callback_data=f"mod_mute_menu:{target_user_id}:{chat_id}")
    
    # Кик (роль >= 1)
    if moderator_role >= 1:
        kb.button(text="👢 Кик", callback_data=f"mod_kick:{target_user_id}:{chat_id}")
    
    # Бан (роль >= 3)
    if moderator_role >= 3:
        kb.button(text="🚫 Бан", callback_data=f"mod_ban:{target_user_id}:{chat_id}")
    
    # Очистить сообщения (роль >= 1)
    if moderator_role >= 1:
        kb.button(text="🧹 Очистить", callback_data=f"mod_clear:{target_user_id}:{chat_id}")
    
    # Закрыть меню
    kb.button(text="❌ Закрыть", callback_data="mod_close")
    
    kb.adjust(2)  # 2 кнопки в ряд
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb.as_markup())
```

### 4. Mute Duration Menu

**Назначение**: Выбор длительности мута

**Функция**: `show_mute_duration_menu(callback: CallbackQuery, target_user_id: int, chat_id: int)`

```python
async def show_mute_duration_menu(callback: CallbackQuery, target_user_id: int, chat_id: int):
    """
    Показывает меню выбора длительности мута
    """
    moderator_role = await get_role(callback.from_user.id, chat_id)
    limit = MUTE_LIMITS.get(moderator_role, 0)
    
    text = "🔇 <b>Выберите длительность мута</b>\n\n"
    if limit > 0:
        text += f"<i>Ваш лимит: {format_time(limit)}</i>"
    
    kb = InlineKeyboardBuilder()
    
    # Стандартные варианты
    durations = [
        ("30 минут", "30m"),
        ("1 час", "1h"),
        ("3 часа", "3h"),
        ("24 часа", "24h"),
    ]
    
    for label, duration in durations:
        duration_sec = parse_time(duration)
        if limit == 0 or duration_sec <= limit:
            kb.button(
                text=label,
                callback_data=f"mod_mute_do:{target_user_id}:{chat_id}:{duration}"
            )
    
    kb.button(text="◀️ Назад", callback_data=f"mod_menu:{target_user_id}:{chat_id}")
    kb.adjust(2)
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb.as_markup())
```

### 5. Action Executors

**Назначение**: Выполнение действий модерации

**Функции**:

```python
async def execute_warn(callback: CallbackQuery, target_user_id: int, chat_id: int):
    """Выдать варн"""
    moderator_id = callback.from_user.id
    moderator_role = await get_role(moderator_id, chat_id)
    
    if moderator_role < 1:
        await callback.answer("❌ Недостаточно прав!", show_alert=True)
        return
    
    # Выдаем варн
    new_count = await db.add_warn(target_user_id, chat_id, moderator_id, "Нарушение правил (inline)")
    
    # Проверяем автокик
    if new_count >= MAX_WARNS:
        try:
            await bot.ban_chat_member(chat_id, target_user_id)
            await bot.unban_chat_member(chat_id, target_user_id)
            await callback.answer(f"✅ Варн выдан ({new_count}/{MAX_WARNS}). Пользователь кикнут!", show_alert=True)
        except Exception as e:
            await callback.answer(f"❌ Ошибка кика: {e}", show_alert=True)
    else:
        await callback.answer(f"✅ Варн выдан ({new_count}/{MAX_WARNS})", show_alert=True)
    
    # Обновляем меню
    await show_moderation_menu(callback, target_user_id, chat_id)


async def execute_mute(callback: CallbackQuery, target_user_id: int, chat_id: int, duration: str):
    """Замутить пользователя"""
    moderator_id = callback.from_user.id
    moderator_role = await get_role(moderator_id, chat_id)
    
    if moderator_role < 1:
        await callback.answer("❌ Недостаточно прав!", show_alert=True)
        return
    
    duration_sec = parse_time(duration)
    limit = MUTE_LIMITS.get(moderator_role, 0)
    
    if limit > 0 and duration_sec > limit:
        await callback.answer(f"❌ Превышен лимит: {format_time(limit)}", show_alert=True)
        return
    
    try:
        await bot.restrict_chat_member(
            chat_id, target_user_id,
            permissions=muted_permissions(),
            until_date=timedelta(seconds=duration_sec)
        )
        
        until = int(time.time()) + duration_sec
        await db.add_mute(target_user_id, chat_id, moderator_id, "Мут (inline)", until)
        
        await callback.answer(f"✅ Мут на {format_time(duration_sec)}", show_alert=True)
        await show_moderation_menu(callback, target_user_id, chat_id)
    except Exception as e:
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)


async def execute_kick(callback: CallbackQuery, target_user_id: int, chat_id: int):
    """Кикнуть пользователя"""
    moderator_id = callback.from_user.id
    moderator_role = await get_role(moderator_id, chat_id)
    
    if moderator_role < 1:
        await callback.answer("❌ Недостаточно прав!", show_alert=True)
        return
    
    try:
        await bot.ban_chat_member(chat_id, target_user_id)
        await bot.unban_chat_member(chat_id, target_user_id)
        await callback.answer("✅ Пользователь кикнут", show_alert=True)
        await callback.message.delete()
    except Exception as e:
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)


async def execute_ban(callback: CallbackQuery, target_user_id: int, chat_id: int):
    """Забанить пользователя"""
    moderator_id = callback.from_user.id
    moderator_role = await get_role(moderator_id, chat_id)
    
    if moderator_role < 3:
        await callback.answer("❌ Недостаточно прав!", show_alert=True)
        return
    
    try:
        await bot.ban_chat_member(chat_id, target_user_id)
        await db.add_ban(target_user_id, chat_id, moderator_id, "Бан (inline)")
        await callback.answer("✅ Пользователь забанен", show_alert=True)
        await callback.message.delete()
    except Exception as e:
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)


async def execute_clear(callback: CallbackQuery, target_user_id: int, chat_id: int):
    """Очистить сообщения пользователя"""
    moderator_id = callback.from_user.id
    moderator_role = await get_role(moderator_id, chat_id)
    
    if moderator_role < 1:
        await callback.answer("❌ Недостаточно прав!", show_alert=True)
        return
    
    messages = await db.get_user_messages(target_user_id, chat_id, limit=100)
    deleted = 0
    
    for msg_id in messages:
        try:
            await bot.delete_message(chat_id, msg_id)
            deleted += 1
        except Exception:
            pass
    
    await db.clear_user_messages(target_user_id, chat_id)
    await callback.answer(f"✅ Удалено сообщений: {deleted}", show_alert=True)
    await show_moderation_menu(callback, target_user_id, chat_id)
```

### 6. Улучшенная функция parse_user()

**Назначение**: Оптимизированный поиск пользователя с использованием кэша

```python
async def parse_user_improved(message: Message, args: list, start_idx: int = 1, user_cache: UserCache = None) -> Optional[int]:
    """
    Улучшенный парсер пользователя с использованием кэша
    
    Приоритет поиска:
    1. Реплай (не на анонима)
    2. Числовой ID
    3. Ник в чате (БД)
    4. Username через кэш
    5. Username через API (fallback)
    """
    # 1. Реплай
    if message.reply_to_message:
        reply = message.reply_to_message
        if reply.from_user and reply.from_user.id != ANONYMOUS_BOT_ID:
            user = reply.from_user
            if user.username and user_cache:
                await user_cache.cache_user(user.id, user.username, message.chat.id)
            return user.id

    # 2. Аргументы
    if len(args) <= start_idx:
        return None

    arg = args[start_idx].strip()

    # 3. Числовой ID
    if arg.lstrip('-').isdigit():
        return int(arg)

    # 4. Ник в чате
    if message.chat.id:
        by_nick = await db.get_user_by_nick(arg, message.chat.id)
        if by_nick:
            return by_nick

    # 5. Username через кэш
    username = arg.lstrip('@').lower()
    if user_cache:
        cached_id = await user_cache.get_user_by_username(username, message.chat.id)
        if cached_id:
            logger.info(f"Cache HIT: {username} -> {cached_id}")
            return cached_id
        logger.info(f"Cache MISS: {username}")

    # 6. Username через API (fallback)
    return await resolve_username(username)
```

### 7. Команда обновления кэша

**Назначение**: Ручное обновление кэша участников

**Команда**: `/refreshcache` или `/updatecache`

```python
@router.message(Command("refreshcache", "updatecache", "обновитькэш"))
async def cmd_refresh_cache(message: Message):
    """Обновить кэш участников чата"""
    if message.chat.type == ChatType.PRIVATE:
        return
    
    my_role = await get_caller_role(message)
    if my_role < 5:
        await message.reply("❌ Недостаточно прав! Требуется роль >= 5")
        return
    
    status_msg = await message.reply("🔄 Обновление кэша участников...")
    
    try:
        result = await user_cache.refresh_chat(message.chat.id)
        
        text = "✅ <b>Кэш обновлен</b>\n\n"
        text += f"<b>Всего участников:</b> {result['total']}\n"
        text += f"<b>Обновлено:</b> {result['updated']}\n"
        text += f"<b>Добавлено:</b> {result['added']}\n"
        text += f"<b>Время:</b> {result['duration']:.2f}с"
        
        await status_msg.edit_text(text, parse_mode="HTML")
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка обновления кэша: {e}")
        logger.error(f"Cache refresh error: {e}", exc_info=True)
```

## Data Models

### Расширение таблицы username_cache

Текущая структура:
```sql
CREATE TABLE IF NOT EXISTS username_cache (
    username TEXT PRIMARY KEY COLLATE NOCASE,
    user_id INTEGER,
    updated_at INTEGER DEFAULT (strftime('%s', 'now'))
);
```

Новая структура с поддержкой чатов:
```sql
CREATE TABLE IF NOT EXISTS chat_members_cache (
    user_id INTEGER,
    chat_id INTEGER,
    username TEXT COLLATE NOCASE,
    first_name TEXT,
    last_name TEXT,
    is_bot INTEGER DEFAULT 0,
    updated_at INTEGER DEFAULT (strftime('%s', 'now')),
    PRIMARY KEY (user_id, chat_id)
);

CREATE INDEX IF NOT EXISTS idx_chat_members_username ON chat_members_cache(username, chat_id);
CREATE INDEX IF NOT EXISTS idx_chat_members_updated ON chat_members_cache(updated_at);
```

### Методы БД для кэша

```python
# В db.py

async def cache_chat_member(self, user_id: int, chat_id: int, username: str = None, 
                            first_name: str = None, last_name: str = None, is_bot: bool = False):
    """Кэшировать участника чата"""
    await self.db.execute("""
        INSERT INTO chat_members_cache (user_id, chat_id, username, first_name, last_name, is_bot, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, chat_id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name,
            last_name = excluded.last_name,
            is_bot = excluded.is_bot,
            updated_at = excluded.updated_at
    """, (user_id, chat_id, username, first_name, last_name, int(is_bot), int(time.time())))
    await self.db.commit()


async def get_cached_member_by_username(self, username: str, chat_id: int) -> Optional[int]:
    """Найти user_id по username в кэше чата"""
    username = username.lower().lstrip('@')
    async with self.db.execute(
        "SELECT user_id FROM chat_members_cache WHERE username = ? COLLATE NOCASE AND chat_id = ?",
        (username, chat_id)
    ) as cur:
        row = await cur.fetchone()
        return row['user_id'] if row else None


async def get_cached_members(self, chat_id: int) -> List[Dict]:
    """Получить всех кэшированных участников чата"""
    async with self.db.execute(
        "SELECT * FROM chat_members_cache WHERE chat_id = ? ORDER BY updated_at DESC",
        (chat_id,)
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def clear_chat_cache(self, chat_id: int):
    """Очистить кэш чата"""
    await self.db.execute("DELETE FROM chat_members_cache WHERE chat_id = ?", (chat_id,))
    await self.db.commit()


async def get_cache_stats(self, chat_id: int) -> Dict:
    """Получить статистику кэша"""
    async with self.db.execute("""
        SELECT 
            COUNT(*) as total,
            COUNT(CASE WHEN username IS NOT NULL AND username != '' THEN 1 END) as with_username,
            MAX(updated_at) as last_update
        FROM chat_members_cache WHERE chat_id = ?
    """, (chat_id,)) as cur:
        row = await cur.fetchone()
        return dict(row) if row else {}
```

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system-essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*



### Property 1: Inline buttons attachment for moderators
*For any* message sent by a Chat_Member in a moderated chat, when a Moderator is present, the Bot should attach inline moderation buttons to the message.
**Validates: Requirements 1.1**

### Property 2: Moderation menu display on button click
*For any* Moderator clicking an inline moderation button, the Bot should display a moderation menu with available actions based on the Moderator's role level.
**Validates: Requirements 1.2, 4.1, 4.2**

### Property 3: Required moderation actions in menu
*For any* moderation menu displayed, the menu should include buttons for warn, mute, kick actions (for role >= 1) and ban action (for role >= 3).
**Validates: Requirements 1.3**

### Property 4: Moderation action execution
*For any* moderation action selected by a Moderator with sufficient permissions, the Bot should execute the action on the target user and update the user's state accordingly.
**Validates: Requirements 1.4, 4.4, 4.6**

### Property 5: Permission error handling
*For any* Moderator with insufficient role attempting to use a moderation button, the Bot should display an error message and prevent the action.
**Validates: Requirements 1.5, 3.5**

### Property 6: Cache update on member join
*For any* new Chat_Member joining a chat, the Bot should immediately add the member's information (user_id, username, first_name, last_name) to the User_Cache.
**Validates: Requirements 2.2, 2.5**

### Property 7: Cache update on username change
*For any* Chat_Member changing their username, the Bot should update the User_Cache with the new username while preserving other user information.
**Validates: Requirements 2.3**

### Property 8: Cache-first user search
*For any* username search via parse_user(), the Bot should query the User_Cache first before calling the Telegram API, and cache any API results for future searches.
**Validates: Requirements 2.4, 5.1, 5.2**

### Property 9: Search method priority
*For any* user search, the Bot should check methods in this order: reply, numeric ID, nickname (nicks table), username (cache), username (API), ensuring the most efficient path is used.
**Validates: Requirements 5.3, 5.4**

### Property 10: Cache refresh completeness
*For any* cache refresh operation (manual or scheduled), the Bot should fetch all current chat members from Telegram API and update the User_Cache with fresh data.
**Validates: Requirements 3.1, 3.2, 6.2**

### Property 11: Menu information completeness
*For any* inline moderation menu opened, the menu should display the target user's current warning count, mute status, role level, and message count.
**Validates: Requirements 7.1, 7.2, 7.3, 7.4**

### Property 12: Real-time menu updates
*For any* moderation action completed, the Bot should update the inline menu to reflect the new state of the target user.
**Validates: Requirements 4.7, 7.5**

### Property 13: Cache operation logging
*For any* user search operation, the Bot should log whether the result came from cache (hit) or API (miss) for monitoring purposes.
**Validates: Requirements 5.5**

### Property 14: Background refresh filtering
*For any* scheduled background cache refresh, the Bot should update only chats that have been active in the last 24 hours.
**Validates: Requirements 6.5**

### Property 15: Error logging and fallback
*For any* Telegram API error during caching operations, the Bot should log the error with details and fall back to direct API queries for user searches.
**Validates: Requirements 8.1, 8.2**

### Property 16: Administrator notification on fetch failure
*For any* failure to fetch chat members, the Bot should notify administrators of the error.
**Validates: Requirements 8.3**

### Property 17: Cache corruption recovery
*For any* detected User_Cache corruption, the Bot should rebuild the cache from scratch by fetching fresh data from Telegram API.
**Validates: Requirements 8.4**

### Property 18: Exponential backoff on API failures
*For any* sequence of failed Telegram API requests, the Bot should implement exponential backoff with increasing delays between retry attempts.
**Validates: Requirements 8.5**

### Property 19: Background refresh error retry
*For any* background cache refresh that fails, the Bot should log the error and schedule a retry after 30 minutes.
**Validates: Requirements 6.3**

## Error Handling

### API Errors

1. **Rate Limiting**: Implement exponential backoff (1s, 2s, 4s, 8s, 16s, max 60s)
2. **Network Errors**: Retry up to 3 times with 5-second delays
3. **Permission Errors**: Log and notify administrators
4. **User Not Found**: Fall back to direct API query, then return None

### Cache Errors

1. **Database Lock**: Retry with exponential backoff (max 3 attempts)
2. **Corruption Detection**: Rebuild cache from scratch
3. **Incomplete Data**: Log warning, continue with available data

### Button Interaction Errors

1. **Callback Timeout**: Display "Operation timed out" message
2. **Insufficient Permissions**: Display role requirement message
3. **Target User Left**: Display "User no longer in chat" message
4. **Action Failed**: Display specific error message, log details

### Background Task Errors

1. **Refresh Failure**: Log error, schedule retry in 30 minutes
2. **Task Cancellation**: Clean up resources, log cancellation reason
3. **Memory Issues**: Limit concurrent refresh tasks to 5

## Testing Strategy

### Unit Tests

Unit tests should focus on:
- Individual function behavior (parse_user, add_moderation_buttons)
- Database operations (cache_chat_member, get_cached_member_by_username)
- Permission checking logic
- Error handling paths
- Edge cases (empty cache, missing data, invalid inputs)

### Property-Based Tests

Property-based tests should verify universal properties across all inputs:
- Each property test must run minimum 100 iterations
- Each test must reference its design document property
- Tag format: **Feature: inline-moderation-buttons, Property {number}: {property_text}**

**Property Test Examples**:

```python
# Property 1: Inline buttons attachment
@given(st.text(), st.integers(min_value=1, max_value=10))
async def test_inline_buttons_attached_for_moderators(message_text, moderator_role):
    """
    Feature: inline-moderation-buttons, Property 1: Inline buttons attachment for moderators
    For any message sent by a Chat_Member, when a Moderator is present,
    the Bot should attach inline moderation buttons.
    """
    # Generate random message
    # Check if buttons are attached when moderator_role >= 1
    # Verify buttons are not attached when moderator_role < 1
    pass

# Property 8: Cache-first user search
@given(st.text(min_size=3, max_size=32))
async def test_cache_checked_before_api(username):
    """
    Feature: inline-moderation-buttons, Property 8: Cache-first user search
    For any username search, the Bot should query the User_Cache first
    before calling the Telegram API.
    """
    # Mock cache and API
    # Perform search
    # Verify cache was checked first
    # Verify API called only if cache miss
    pass

# Property 18: Exponential backoff
@given(st.integers(min_value=1, max_value=10))
async def test_exponential_backoff_on_failures(failure_count):
    """
    Feature: inline-moderation-buttons, Property 18: Exponential backoff on API failures
    For any sequence of failed API requests, the Bot should implement
    exponential backoff with increasing delays.
    """
    # Simulate API failures
    # Measure delays between retries
    # Verify delays follow exponential pattern: 1s, 2s, 4s, 8s, 16s, max 60s
    pass
```

### Integration Tests

Integration tests should verify:
- End-to-end button click → menu display → action execution flow
- Cache refresh → database update → search improvement
- Background task scheduling and execution
- Multi-chat cache management

### Manual Testing Checklist

- [ ] Inline buttons appear on user messages
- [ ] Moderation menu displays correct actions for each role
- [ ] Warn action increments count and auto-kicks at MAX_WARNS
- [ ] Mute action restricts user permissions for specified duration
- [ ] Kick action removes user from chat
- [ ] Ban action permanently blocks user
- [ ] Clear action deletes user messages
- [ ] Cache refresh command updates member list
- [ ] Username search works without "user not found" errors
- [ ] Background cache refresh runs every 6 hours
- [ ] Error messages display for insufficient permissions
- [ ] Menu updates after action completion

## Implementation Notes

### Performance Considerations

1. **Cache Size**: Limit cache to 10,000 members per chat (oldest entries removed first)
2. **Batch Operations**: Fetch members in batches of 200 to avoid API limits
3. **Concurrent Refreshes**: Limit to 5 concurrent chat refreshes
4. **Database Indexing**: Ensure indexes on (username, chat_id) and (user_id, chat_id)

### Security Considerations

1. **Permission Validation**: Always verify moderator role before executing actions
2. **Target Role Check**: Prevent moderating users with equal or higher roles
3. **Rate Limiting**: Implement per-user rate limits on button clicks (max 10/minute)
4. **Input Sanitization**: Sanitize all user inputs in callback data

### Deployment Strategy

1. **Database Migration**: Add chat_members_cache table before deploying code
2. **Initial Cache Population**: Run cache refresh for all chats on first startup
3. **Gradual Rollout**: Enable inline buttons for one chat at a time
4. **Monitoring**: Track cache hit rate, API call frequency, error rates
5. **Rollback Plan**: Feature flag to disable inline buttons if issues arise

### Configuration

Add to `config.json`:
```json
{
  "inline_buttons_enabled": true,
  "cache_refresh_interval": 21600,
  "cache_max_age": 86400,
  "cache_max_size_per_chat": 10000,
  "background_refresh_enabled": true,
  "button_rate_limit": 10
}
```

## Future Enhancements

1. **Custom Mute Durations**: Allow moderators to input custom mute times
2. **Reason Input**: Add inline text input for ban/warn reasons
3. **Action History**: Display recent moderation actions in menu
4. **Bulk Actions**: Select multiple users for batch moderation
5. **Statistics Dashboard**: Show cache performance metrics
6. **Smart Caching**: Prioritize caching active users over inactive ones
7. **Cross-Chat Cache**: Share cache across related chats in a network
