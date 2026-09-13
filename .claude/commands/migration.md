---
description: Добавить миграцию схемы SQLite по чек-листу проекта и проверить её на реальном дампе
argument-hint: <что нужно начать хранить>
---

Добавь миграцию схемы: $ARGUMENTS

Источник правды о версии — кортеж `MIGRATIONS` в `src/lexinform/adapters/sqlite_repo.py`:
скрипт с индексом `i` приводит базу к версии `i + 1`, `SCHEMA_VERSION = len(MIGRATIONS)`.
Миграции append-only: **ни одну существующую строку не редактировать и не переставлять** —
развёрнутые дампы несут свою версию, и прод обновляется накатом на первом же прогоне.

## Порядок

1. Сначала убедись, что колонка вообще нужна: не выводится ли новое значение из того, что уже
   лежит (`summary_json`, `stages_json`, `analysis_json`, `rcl_json`, `wykaz_json`). Производное
   не хранят — «что дальше» в этом проекте считается, а не пишется в базу.
2. Допиши **одну** строку в конец `MIGRATIONS`. Только простой SQL: `ALTER TABLE … ADD COLUMN`
   (nullable или с DEFAULT), новые таблицы, индексы, `UPDATE`-бэкфилл. SQLite не меняет тип и
   ограничения колонки: для этого — новая таблица, `INSERT … SELECT`, drop, rename.
3. Расширь модель и маппинг (`_row_to_bill` и то, что пишет строку). Поля внутри JSON-колонок —
   это дампы pydantic: новому полю нужен дефолт, иначе старые строки перестанут грузиться;
   переименование поля в JSON — это миграция данных (`json_set` или разовый шаг на Python).
4. Расширь `test_restore_of_a_v1_dump_applies_every_later_migration` в
   `tests/unit/test_sqlite_repo.py`: он восстанавливает собранный руками дамп v1 и проверяет,
   что новые колонки на месте. Плюс тест на само поведение, ради которого колонка заводится.
5. Прогони гейт: `uv run ruff format src tests && uv run ruff check src tests && uv run mypy &&
   uv run pytest -q`.
6. **Проверь на реальном дампе** (без этого миграция не считается проверенной):

   ```bash
   git fetch -q origin state
   git show origin/state:lexinform.sql > /tmp/lexinform-state.sql
   export LEXINFORM_DB_PATH=/tmp/lexinform-migration.db
   rm -f "$LEXINFORM_DB_PATH"
   uv run lexinform db init && uv run lexinform db restore /tmp/lexinform-state.sql
   sqlite3 "$LEXINFORM_DB_PATH" "PRAGMA user_version"   # должна быть новая версия
   uv run lexinform show 2699                            # строки читаются
   ```

7. Обнови раздел «Database versioning and migrations» в `CLAUDE.md`: номер версии и одна строка
   о том, что она хранит и зачем (эти строки читаются как история решений, а не как changelog).
   Если колонка меняет то, что видит читатель, — скажи об этом и в инвариантах.
8. Закоммить одним коммитом (миграция + модель + тесты + заметка в CLAUDE.md), без пуша.

Отката нет: чтобы откатиться, откатывают код и восстанавливают предыдущий дамп из истории
ветки `state`. Поэтому скрипт должен быть таким, чтобы падение внутри него оставляло базу на
прежней версии — каждая миграция идёт в своей транзакции, не клади в одну строку два
несвязанных изменения.
