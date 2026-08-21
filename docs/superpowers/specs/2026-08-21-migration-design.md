# Архітектурний дизайн міграції: SQLite -> PostgreSQL + Redis

## Огляд
Поточна архітектура бота (SQLite як база даних і FSM-сховище, хардкод-цикли для фонових задач) обмежує масштабованість і надійність проекту. Даний документ описує "тіньовий підхід" (Shadow Deployment) для міграції існуючого бота (що вже працює в Docker на VPS) на повноцінний production-ready стек.

## Основні зміни
1. **База даних**: Заміна `SQLite` на `PostgreSQL` (оновлення `alembic`, `sqlalchemy` URL).
2. **FSM (aiogram)**: Заміна `SQLiteStorage` на `RedisStorage`.
3. **Фонові задачі**: Перехід від `asyncio.sleep()`-циклів у `main.py` до використання планувальника (наприклад, `APScheduler`).
4. **OAuth Безпека**: Видалення логіки фолбеку в `web/routes.py`, яка при відсутності `oauth_token` зв'язувала всіх адміністраторів з новим акаунтом Poster. Тепер відсутність токену відхилятиме запит (HTTP 400).
5. **Тестування**: Створення структури папок для `pytest` та додавання базових юніт-тестів для критичних компонентів (наприклад, перевірка OAuth-маршруту та логіки формування корзини).

## Підхід "Тіньового розгортання" (Shadow Deployment)
Щоб мінімізувати даунтайм і гарантувати цілісність даних існуючого клієнта, процес складатиметься з таких кроків:

1. Оновлення інфраструктури:
   - Розширення `docker-compose.yml` сервісами `db` (PostgreSQL) та `redis` (Redis).
   - Встановлення відповідних бібліотек (наприклад, `asyncpg`, `redis`).
2. Розробка скрипта міграції:
   - Створення одноразового Python-скрипта (`scripts/migrate_sqlite_to_pg.py`), який підключиться до старого SQLite (`data/writeoff.db`) і до нового PostgreSQL.
   - Скрипт витягне всі рядки з усіх таблиць (`alembic_version`, `admins`, `clients`, `staff`, `writeoffs` тощо) і вставить їх в Postgres, зі збереженням ID.
3. Процес деплою на VPS:
   - Зробити пулл оновленого коду на VPS.
   - Запустити `docker compose up -d db redis` (підняти лише бази).
   - Виконати Alembic-міграції для створення схем у PostgreSQL.
   - Зупинити поточний контейнер бота: `docker stop writeoff-bot`.
   - Запустити `python scripts/migrate_sqlite_to_pg.py`.
   - Запустити повністю нову інфраструктуру: `docker compose up -d`.

## Структура змін по компонентах

### `docker-compose.yml`
- [MODIFY] Додавання сервісів `db` (postgres:15-alpine) та `redis` (redis:7-alpine).
- [MODIFY] Оновлення environment-змінних сервісу бота (`DB_URL`, `REDIS_URL`).

### `core/config.py`
- [MODIFY] Додавання `redis_url`, зміна значення за замовчуванням `db_url` на PostgreSQL.

### `main.py`
- [MODIFY] Заміна `SQLiteStorage` на `RedisStorage(redis=Redis.from_url(...))`.
- [MODIFY] Видалення функцій `catalog_refresh_loop` і `reminder_loop`.
- [MODIFY] Ініціалізація `APScheduler` для виконання цих задач за розкладом замість циклів.

### `web/routes.py`
- [MODIFY] В ендпоінті `/oauth/callback` видалення блоку `else: for admin_id in settings.admin_chat_ids: ...`, і додавання повернення `HTTP 400`, якщо не знайдено ініціатора (`oauth_token`).

### `tests/`
- [NEW] Створення `tests/conftest.py` для ініціалізації тестової БД або моків.
- [NEW] Створення `tests/test_oauth.py` для перевірки, що діру в безпеці закрито.

## Відкат (Rollback)
Якщо під час деплою щось йде не так:
1. Вимикаємо новий бот: `docker compose stop bot`
2. Перемикаємо `.env` назад на SQLite.
3. Перезапускаємо `docker compose up -d bot` (стара версія). Дані в SQLite залишаються недоторканими, оскільки міграційний скрипт працює лише на читання.

## Наступні кроки
Після затвердження цього документа, я згенерую детальний План Реалізації (Implementation Plan), де будуть вказані точні зміни в коді по кожному файлу.
