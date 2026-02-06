# Implementation Plan: Inline Moderation Buttons

## Overview

Данный план описывает пошаговую реализацию системы inline-кнопок модерации и улучшенного кэширования пользователей для Telegram бота. Реализация разбита на логические этапы с инкрементальной интеграцией.

## Tasks

- [ ] 1. Расширить схему базы данных для кэша участников
  - Создать таблицу `chat_members_cache` с полями: user_id, chat_id, username, first_name, last_name, is_bot, updated_at
  - Добавить индексы для оптимизации поиска по username и chat_id
  - Добавить методы в класс Database для работы с кэшем
  - _Requirements: 2.5, 5.3_

- [ ]* 1.1 Написать property test для кэширования участников
  - **Property 6: Cache update on member join**
  - **Validates: Requirements 2.2, 2.5**

- [ ] 2. Реализовать класс UserCache для управления кэшем
  - [ ] 2.1 Создать класс UserCache с методами refresh_chat, get_user_by_username, cache_user
    - Реализовать метод refresh_chat() для получения участников через Telegram API
    - Реализовать метод get_user_by_username() для поиска в кэше
    - Реализовать метод cache_user() для добавления/обновления записей
    - _Requirements: 2.1, 2.2, 2.4_
  
  - [ ]* 2.2 Написать property test для cache-first поиска
    - **Property 8: Cache-first user search**
    - **Validates: Requirements 2.4, 5.1, 5.2**
  
  - [ ] 2.3 Добавить методы для фонового обновления кэша
    - Реализовать schedule_auto_refresh() с asyncio.create_task
    - Реализовать stop_auto_refresh() для отмены задач
    - Добавить фильтрацию по активности чата (последние 24 часа)
    - _Requirements: 6.1, 6.2, 6.5_
  
  - [ ]* 2.4 Написать property test для фонового обновления
    - **Property 14: Background refresh filtering**
    - **Validates: Requirements 6.5**

- [ ] 3. Checkpoint - Проверить работу кэша
  - Убедиться что кэш корректно сохраняет и извлекает данные
  - Проверить что фоновые задачи запускаются и останавливаются
  - Спросить пользователя если возникли вопросы

- [ ] 4. Улучшить функцию parse_user() для использования кэша
  - [ ] 4.1 Модифицировать parse_user() для приоритетного использования кэша
    - Добавить параметр user_cache: UserCache
    - Изменить порядок поиска: reply → ID → nick → cache → API
    - Добавить логирование cache hits/misses
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_
  
  - [ ]* 4.2 Написать property test для порядка поиска
    - **Property 9: Search method priority**
    - **Validates: Requirements 5.3, 5.4**
  
  - [ ] 4.3 Добавить обработку ошибок и fallback на API
    - Обернуть cache queries в try-except
    - При ошибке кэша использовать прямой API запрос
    - Логировать все ошибки с деталями
    - _Requirements: 8.1, 8.2_
  
  - [ ]* 4.4 Написать property test для error handling
    - **Property 15: Error logging and fallback**
    - **Validates: Requirements 8.1, 8.2**

- [ ] 5. Реализовать команду обновления кэша
  - [ ] 5.1 Создать обработчик команды /refreshcache
    - Проверить роль >= 5
    - Показать сообщение "Обновление кэша..."
    - Вызвать user_cache.refresh_chat()
    - Отобразить статистику (всего, обновлено, добавлено, время)
    - _Requirements: 3.1, 3.2, 3.4, 3.5_
  
  - [ ]* 5.2 Написать property test для проверки прав
    - **Property 5: Permission error handling**
    - **Validates: Requirements 1.5, 3.5**

- [ ] 6. Checkpoint - Проверить команду обновления кэша
  - Выполнить /refreshcache в тестовом чате
  - Проверить что кэш обновляется корректно
  - Убедиться что статистика отображается правильно

- [ ] 7. Реализовать функцию добавления inline-кнопок к сообщениям
  - [ ] 7.1 Создать функцию add_moderation_buttons()
    - Принимать message и moderator_role
    - Возвращать InlineKeyboardMarkup с кнопкой "⚙️ Модерация"
    - Callback data формата: mod_menu:{user_id}:{chat_id}
    - _Requirements: 1.1_
  
  - [ ]* 7.2 Написать property test для добавления кнопок
    - **Property 1: Inline buttons attachment for moderators**
    - **Validates: Requirements 1.1**
  
  - [ ] 7.3 Модифицировать обработчик on_message()
    - Получить роль отправителя сообщения
    - Если есть модераторы в чате, добавить кнопки через add_moderation_buttons()
    - Отправить кнопки как reply_markup к сообщению (если возможно) или отдельным сообщением
    - _Requirements: 1.1_

- [ ] 8. Реализовать обработчик меню модерации
  - [ ] 8.1 Создать функцию show_moderation_menu()
    - Парсить callback data для получения target_user_id и chat_id
    - Проверить права модератора (роль >= 1, target_role < moderator_role)
    - Получить статус пользователя (варны, мут, роль, сообщения)
    - Сформировать текст с информацией о пользователе
    - Создать кнопки: Варн, Мут, Кик, Бан (по ролям), Очистить, Закрыть
    - _Requirements: 1.2, 4.1, 4.2, 7.1, 7.2, 7.3, 7.4_
  
  - [ ]* 8.2 Написать property test для отображения меню
    - **Property 2: Moderation menu display on button click**
    - **Validates: Requirements 1.2, 4.1, 4.2**
  
  - [ ]* 8.3 Написать property test для полноты информации
    - **Property 11: Menu information completeness**
    - **Validates: Requirements 7.1, 7.2, 7.3, 7.4**
  
  - [ ] 8.2 Создать callback handler для mod_menu
    - Зарегистрировать @router.callback_query(F.data.startswith("mod_menu:"))
    - Вызвать show_moderation_menu() с параметрами из callback data
    - _Requirements: 1.2_

- [ ] 9. Реализовать меню выбора длительности мута
  - [ ] 9.1 Создать функцию show_mute_duration_menu()
    - Показать варианты: 30 минут, 1 час, 3 часа, 24 часа
    - Учитывать лимиты роли модератора (MUTE_LIMITS)
    - Callback data формата: mod_mute_do:{user_id}:{chat_id}:{duration}
    - Добавить кнопку "Назад" для возврата в главное меню
    - _Requirements: 4.5_
  
  - [ ] 9.2 Создать callback handler для mod_mute_menu
    - Зарегистрировать @router.callback_query(F.data.startswith("mod_mute_menu:"))
    - Вызвать show_mute_duration_menu()
    - _Requirements: 4.5_

- [ ] 10. Реализовать исполнители действий модерации
  - [ ] 10.1 Создать функцию execute_warn()
    - Проверить права (роль >= 1)
    - Вызвать db.add_warn() с причиной "Нарушение правил (inline)"
    - Проверить автокик при достижении MAX_WARNS
    - Показать уведомление с количеством варнов
    - Обновить меню через show_moderation_menu()
    - _Requirements: 1.4, 4.4_
  
  - [ ]* 10.2 Написать property test для выполнения действий
    - **Property 4: Moderation action execution**
    - **Validates: Requirements 1.4, 4.4, 4.6**
  
  - [ ] 10.3 Создать функцию execute_mute()
    - Проверить права и лимиты
    - Вызвать bot.restrict_chat_member() с muted_permissions()
    - Сохранить мут в БД через db.add_mute()
    - Показать уведомление с длительностью
    - Обновить меню
    - _Requirements: 1.4_
  
  - [ ] 10.4 Создать функцию execute_kick()
    - Проверить права (роль >= 1)
    - Вызвать bot.ban_chat_member() и bot.unban_chat_member()
    - Показать уведомление об успехе
    - Удалить сообщение с меню
    - _Requirements: 1.4, 4.6_
  
  - [ ] 10.5 Создать функцию execute_ban()
    - Проверить права (роль >= 3)
    - Вызвать bot.ban_chat_member()
    - Сохранить бан в БД через db.add_ban()
    - Показать уведомление об успехе
    - Удалить сообщение с меню
    - _Requirements: 1.4_
  
  - [ ] 10.6 Создать функцию execute_clear()
    - Проверить права (роль >= 1)
    - Получить список сообщений через db.get_user_messages()
    - Удалить каждое сообщение через bot.delete_message()
    - Очистить записи в БД через db.clear_user_messages()
    - Показать уведомление с количеством удаленных
    - Обновить меню
    - _Requirements: 1.4_

- [ ] 11. Создать callback handlers для действий модерации
  - [ ] 11.1 Зарегистрировать handler для mod_warn
    - @router.callback_query(F.data.startswith("mod_warn:"))
    - Вызвать execute_warn()
    - _Requirements: 1.4_
  
  - [ ] 11.2 Зарегистрировать handler для mod_mute_do
    - @router.callback_query(F.data.startswith("mod_mute_do:"))
    - Вызвать execute_mute()
    - _Requirements: 1.4_
  
  - [ ] 11.3 Зарегистрировать handler для mod_kick
    - @router.callback_query(F.data.startswith("mod_kick:"))
    - Вызвать execute_kick()
    - _Requirements: 1.4_
  
  - [ ] 11.4 Зарегистрировать handler для mod_ban
    - @router.callback_query(F.data.startswith("mod_ban:"))
    - Вызвать execute_ban()
    - _Requirements: 1.4_
  
  - [ ] 11.5 Зарегистрировать handler для mod_clear
    - @router.callback_query(F.data.startswith("mod_clear:"))
    - Вызвать execute_clear()
    - _Requirements: 1.4_
  
  - [ ] 11.6 Зарегистрировать handler для mod_close
    - @router.callback_query(F.data == "mod_close")
    - Удалить сообщение с меню
    - _Requirements: 1.2_

- [ ] 12. Checkpoint - Проверить inline-кнопки и меню
  - Отправить тестовое сообщение в чат
  - Проверить что кнопка "Модерация" появляется
  - Кликнуть на кнопку и проверить меню
  - Выполнить каждое действие (варн, мут, кик) и проверить результат

- [ ] 13. Добавить обработку ошибок и уведомления
  - [ ] 13.1 Реализовать exponential backoff для API запросов
    - Создать функцию retry_with_backoff()
    - Использовать задержки: 1s, 2s, 4s, 8s, 16s, max 60s
    - Применить к user_cache.refresh_chat() и API вызовам
    - _Requirements: 8.5_
  
  - [ ]* 13.2 Написать property test для exponential backoff
    - **Property 18: Exponential backoff on API failures**
    - **Validates: Requirements 8.5**
  
  - [ ] 13.3 Добавить уведомления администраторов при ошибках
    - При ошибке fetch chat members отправить сообщение админам
    - Получить список админов через db.get_all_staff() с ролью >= 5
    - Отправить уведомление каждому админу в ЛС
    - _Requirements: 8.3_
  
  - [ ]* 13.4 Написать property test для уведомлений админов
    - **Property 16: Administrator notification on fetch failure**
    - **Validates: Requirements 8.3**
  
  - [ ] 13.5 Реализовать восстановление после повреждения кэша
    - Добавить проверку целостности кэша при старте
    - При обнаружении повреждения вызвать db.clear_chat_cache() и refresh_chat()
    - Логировать событие восстановления
    - _Requirements: 8.4_
  
  - [ ]* 13.6 Написать property test для восстановления кэша
    - **Property 17: Cache corruption recovery**
    - **Validates: Requirements 8.4**

- [ ] 14. Интегрировать UserCache в основной код бота
  - [ ] 14.1 Инициализировать UserCache при старте бота
    - Создать глобальный экземпляр user_cache = UserCache(bot, db)
    - Вызвать user_cache.refresh_chat() для каждого MODERATED_CHATS
    - Запустить schedule_auto_refresh() для каждого чата
    - _Requirements: 2.1, 6.1_
  
  - [ ] 14.2 Обновить обработчик on_user_join
    - Добавить вызов user_cache.cache_user() при входе нового участника
    - Кэшировать username, first_name, last_name
    - _Requirements: 2.2_
  
  - [ ]* 14.3 Написать property test для обновления при входе
    - **Property 6: Cache update on member join**
    - **Validates: Requirements 2.2, 2.5**
  
  - [ ] 14.3 Заменить все вызовы parse_user() на parse_user_improved()
    - Найти все использования parse_user() в коде
    - Добавить параметр user_cache к каждому вызову
    - Проверить что все команды модерации работают корректно
    - _Requirements: 5.1, 5.2_

- [ ] 15. Добавить конфигурацию и feature flags
  - [ ] 15.1 Добавить настройки в config.json
    - inline_buttons_enabled: true/false
    - cache_refresh_interval: 21600 (6 часов)
    - cache_max_age: 86400 (24 часа)
    - cache_max_size_per_chat: 10000
    - background_refresh_enabled: true/false
    - button_rate_limit: 10 (кликов в минуту)
    - _Requirements: 6.1_
  
  - [ ] 15.2 Реализовать проверку feature flags
    - Проверять inline_buttons_enabled перед добавлением кнопок
    - Проверять background_refresh_enabled перед запуском фоновых задач
    - Позволить отключить функции без изменения кода
    - _Requirements: 1.1, 6.1_

- [ ] 16. Добавить rate limiting для кнопок
  - [ ] 16.1 Создать словарь для отслеживания кликов
    - Структура: {user_id: [timestamp1, timestamp2, ...]}
    - Очищать старые записи (> 1 минуты)
    - _Requirements: 1.5_
  
  - [ ] 16.2 Проверять лимит перед обработкой callback
    - Если превышен лимит (10 кликов/минуту), показать ошибку
    - Добавить текущий timestamp в список
    - _Requirements: 1.5_

- [ ] 17. Финальный checkpoint - Полное тестирование
  - Проверить все команды модерации с inline-кнопками
  - Проверить что кэш обновляется автоматически
  - Проверить что поиск пользователей работает без ошибок
  - Проверить обработку ошибок и fallback
  - Проверить rate limiting
  - Спросить пользователя если есть проблемы

- [ ] 18. Документация и логирование
  - [ ] 18.1 Обновить COMMANDS.md
    - Добавить описание inline-кнопок модерации
    - Добавить команду /refreshcache
    - Описать как использовать кнопки
    - _Requirements: 3.1_
  
  - [ ] 18.2 Добавить подробное логирование
    - Логировать все cache hits/misses
    - Логировать все действия модерации через inline-кнопки
    - Логировать ошибки с полным traceback
    - Логировать статистику фоновых обновлений
    - _Requirements: 5.5, 8.1_

## Notes

- Задачи с `*` являются опциональными property-based тестами
- Каждая задача ссылается на конкретные требования для отслеживаемости
- Checkpoints обеспечивают инкрементальную валидацию
- Property tests валидируют универсальные свойства корректности
- Unit tests валидируют конкретные примеры и граничные случаи
- Все изменения должны быть обратно совместимы с существующим кодом
- Feature flags позволяют отключить новые функции при проблемах
