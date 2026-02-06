# Requirements Document

## Introduction

Данный документ описывает требования к улучшению Telegram бота модерации группы путем добавления инлайн-кнопок для быстрой модерации и исправления проблемы поиска пользователей по username. Бот построен на Python с использованием aiogram 3.x и SQLite базы данных.

## Glossary

- **Bot**: Telegram бот модерации на базе aiogram 3.x
- **Moderator**: Пользователь с ролью >= 1, имеющий права на модерацию
- **Inline_Button**: Кнопка под сообщением пользователя для быстрого доступа к действиям модерации
- **User_Cache**: Кэш участников чата в базе данных для быстрого поиска
- **parse_user()**: Функция поиска пользователя по различным идентификаторам
- **Moderation_Action**: Действие модерации (бан, варн, мут, кик)
- **Chat_Member**: Участник группового чата
- **Username**: Уникальный идентификатор пользователя в Telegram (начинается с @)

## Requirements

### Requirement 1: Инлайн-кнопки модерации на сообщениях

**User Story:** Как модератор, я хочу видеть кнопки модерации под сообщениями пользователей, чтобы быстро применять действия без ввода команд.

#### Acceptance Criteria

1. WHEN a Chat_Member sends a message in a moderated chat, THE Bot SHALL attach inline buttons to the message for moderators
2. WHEN a Moderator clicks an inline button, THE Bot SHALL display a moderation menu with available actions
3. THE Bot SHALL include buttons for ban, warn, mute, and kick actions in the moderation menu
4. WHEN a Moderator selects a moderation action, THE Bot SHALL execute the action on the target user
5. WHEN a Moderator with insufficient role tries to use a button, THE Bot SHALL display an error message

### Requirement 2: Улучшенный кэш участников чата

**User Story:** Как модератор, я хочу чтобы бот находил пользователей по @username без ошибок, чтобы команды модерации работали надежно.

#### Acceptance Criteria

1. WHEN the Bot starts, THE Bot SHALL fetch and cache all chat members for each moderated chat
2. WHEN a new Chat_Member joins a chat, THE Bot SHALL add the member to the User_Cache immediately
3. WHEN a Chat_Member changes their username, THE Bot SHALL update the User_Cache with the new username
4. WHEN parse_user() searches for a username, THE Bot SHALL first check the User_Cache before calling Telegram API
5. THE Bot SHALL store user_id, username, first_name, and last_name in the User_Cache

### Requirement 3: Команда обновления кэша

**User Story:** Как администратор, я хочу иметь команду для принудительного обновления кэша участников, чтобы синхронизировать данные при необходимости.

#### Acceptance Criteria

1. WHEN an administrator executes the refresh cache command, THE Bot SHALL fetch all current chat members from Telegram API
2. WHEN the cache refresh completes, THE Bot SHALL update the User_Cache with fresh data
3. WHEN the cache refresh is in progress, THE Bot SHALL display a progress indicator
4. WHEN the cache refresh completes, THE Bot SHALL display statistics about updated members
5. THE Bot SHALL require role >= 5 to execute the cache refresh command

### Requirement 4: Inline-меню модерации

**User Story:** Как модератор, я хочу видеть контекстное меню с опциями модерации при клике на кнопку, чтобы выбрать нужное действие.

#### Acceptance Criteria

1. WHEN a Moderator clicks the moderation button, THE Bot SHALL display an inline menu with action options
2. THE Bot SHALL show different actions based on the Moderator's role level
3. WHEN a Moderator selects "Ban", THE Bot SHALL prompt for a reason or use a default reason
4. WHEN a Moderator selects "Warn", THE Bot SHALL increment the user's warning count and display the total
5. WHEN a Moderator selects "Mute", THE Bot SHALL display duration options (30m, 1h, 24h, custom)
6. WHEN a Moderator selects "Kick", THE Bot SHALL remove the user from the chat immediately
7. WHEN a moderation action completes, THE Bot SHALL update the inline menu to show the action result

### Requirement 5: Оптимизация функции parse_user()

**User Story:** Как разработчик, я хочу чтобы функция parse_user() использовала кэш эффективно, чтобы минимизировать обращения к Telegram API.

#### Acceptance Criteria

1. WHEN parse_user() receives a username, THE Bot SHALL query the User_Cache first
2. IF the username is not in User_Cache, THEN THE Bot SHALL query Telegram API and cache the result
3. WHEN parse_user() receives a user_id, THE Bot SHALL verify it exists in the User_Cache
4. WHEN parse_user() receives a nickname, THE Bot SHALL check the nicks table before other methods
5. THE Bot SHALL log cache hits and misses for monitoring purposes

### Requirement 6: Фоновое обновление кэша

**User Story:** Как системный администратор, я хочу чтобы кэш обновлялся автоматически, чтобы данные оставались актуальными без ручного вмешательства.

#### Acceptance Criteria

1. THE Bot SHALL schedule automatic cache refresh every 6 hours for each moderated chat
2. WHEN a scheduled refresh runs, THE Bot SHALL update the User_Cache in the background
3. WHEN a background refresh fails, THE Bot SHALL log the error and retry after 30 minutes
4. THE Bot SHALL not block message processing during background cache updates
5. THE Bot SHALL update only chats that have been active in the last 24 hours

### Requirement 7: Визуальная индикация статуса пользователя

**User Story:** Как модератор, я хочу видеть текущий статус пользователя в inline-меню, чтобы принимать обоснованные решения о модерации.

#### Acceptance Criteria

1. WHEN the inline menu opens, THE Bot SHALL display the user's current warning count
2. WHEN the inline menu opens, THE Bot SHALL display if the user is currently muted
3. WHEN the inline menu opens, THE Bot SHALL display the user's role level
4. WHEN the inline menu opens, THE Bot SHALL display the user's message count in the chat
5. THE Bot SHALL update the status information in real-time when the menu is refreshed

### Requirement 8: Обработка ошибок кэширования

**User Story:** Как разработчик, я хочу чтобы система корректно обрабатывала ошибки кэширования, чтобы бот продолжал работать при проблемах с API.

#### Acceptance Criteria

1. WHEN Telegram API returns an error during caching, THE Bot SHALL log the error with details
2. WHEN a cache operation fails, THE Bot SHALL fall back to direct API queries
3. WHEN the Bot cannot fetch chat members, THE Bot SHALL notify administrators
4. WHEN the User_Cache is corrupted, THE Bot SHALL rebuild it from scratch
5. THE Bot SHALL implement exponential backoff for failed API requests
