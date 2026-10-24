# Обслуживание canonical state

Статус: поддерживаемый путь и открытые ограничения · проверено 24 октября 2026.

Canonical state — SQL и polling manifest ветки `state`. Поддерживаемый writer — job `run`
из [daily.yml](https://github.com/sobolevbel/lexinform/blob/main/.github/workflows/daily.yml).
Scheduled run, inbox commands, collect, drain и workflow maintenance используют его общий
`concurrency.group: lexinform-daily`. Checkout и restore находятся внутри этого job.
Протокол сохранения описан в [базе данных](../reference/database.md#дамп-и-восстановление).

## Обычное обслуживание

1. Проверьте `/status`, нужный `/delivery ID` и сохранённый outcome прежней команды.
   `queued` означает постановку в очередь, а не получение права менять state.
2. Используйте соответствующую [команду оператора](../operator-commands.md) через inbox
   либо поддерживаемый manual dispatch существующего workflow. Команда выполняется после
   получения общего job lock и восстановления актуального snapshot.
3. Сохраните command update ID и URL запуска. Исполнение, ответ и удаление inbox-файла —
   разные границы: started/outcome/handled сохраняются checkpoint'ами; итоговый SQL и manifest
   должны быть подтверждены до публикации удаления inbox.
4. Проверяйте записанный outcome, состояние нужной publication и итоговый checkpoint.
   Успешный job сам по себе не доказывает успех каждого операторского действия.

Отдельная `/reprefilter` и backfill `/run reprefilter=N` выполняются внутри ограниченного
Run step. Ошибка backfill останавливает последующие index/run/commands, но не final save:
при исправном checkpoint уже обработанные строки сохраняются. Backfill и следующие фазы
делят 35 минут Run step; job оставляет время на финальное сохранение до своего timeout.
Dry run по-прежнему не сохраняет локальные изменения.

## Изолированная локальная копия

Подготовка копии описана в
[CONTRIBUTING](https://github.com/sobolevbel/lexinform/blob/main/CONTRIBUTING.md#against-production-data).
Фиксируйте SHA code/state, используйте отдельную SQLite и путь dump. `db restore` меняет
локальную SQLite; `db dump` создаёт локальные SQL/manifest. Эти команды не получают job lock.
`run --dry-run` всё равно может обращаться к платному triage; это не обязательный шаг проверки.

Локальный `db checkpoint` проверяет CAS, но не приобретает GitHub concurrency lock и не
доказывает, из какого snapshot получена произвольно переданная SQLite. Его наличие не делает
локальную копию поддерживаемым вторым writer canonical state.

## Исключительное изменение

Операция, которой нет среди поддерживаемых команд, сначала получает проверяемую реализацию
в том же locked job: fresh checkout/restore → ограниченная mutation → общий checkpoint SQL
и manifest → подтверждение remote SHA. Не добавляйте параллельный workflow или ручной push
как обход этого порядка. Миграции остаются append-only; правила rollback — в
[контракте базы](../reference/database.md).

Готовой процедуры arbitrary SQL/disaster restore с отдельным захватом exclusive ownership
пока нет. Она остаётся открытой частью DA1.8; отключение расписания само по себе не доказывает
отсутствие queued/manual writers. Новое backend-хранилище или lease service этим не выбирается.
