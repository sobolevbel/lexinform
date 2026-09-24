# База данных и миграции

Статус: актуальный указатель на схему. Проверен 25 сентября 2026.

Источник истины — append-only
[`MIGRATIONS` в sqlite_repo.py](https://github.com/sobolevbel/lexinform/blob/main/src/lexinform/adapters/sqlite_repo.py).
Версия SQLite хранится в `PRAGMA user_version`; `dump()` записывает её,
а `restore()` применяет недостающие миграции после восстановления.

Помимо законопроектов и публикаций, схема содержит `analysis_memo`, `llm_batches`,
`llm_batch_items` и `llm_batch_intents`. Memo хранит повторно используемый результат;
batch-таблицы — задания, результаты, учёт затрат и неопределённые попытки отправки.
Миграция v34 добавляет `delivery_resolutions` для аудита ручного восстановления и
`publication_sequence`, чтобы удалённый ID публикации не достался новому сообщению.
Маркер `awaiting_batch_since` сохраняется до фиксации наблюдения: время в очереди
входит в срок ожидания вторичного анализа. См. [B50 в архиве](../archive/bugs.md#b50).

## Визуальная схема

[Открыть историческую визуализацию](../database.html).
Она описывает раннюю схему из пяти таблиц и **не соответствует текущему набору миграций**.
Не используйте её как спецификацию полей или индексов.

## Изменение схемы

Добавляйте SQL только в конец `MIGRATIONS`, сохраняйте совместимые значения по умолчанию
в моделях и расширяйте проверку восстановления старого дампа.
Полная процедура — в
[CONTRIBUTING.md](https://github.com/sobolevbel/lexinform/blob/main/CONTRIBUTING.md#database-schema-and-migrations).
Правила удержания данных — в [state-retention.md](../state-retention.md).
