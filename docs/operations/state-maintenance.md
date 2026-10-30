# Обслуживание canonical state

Статус: поддерживаемый путь и закрытый до квалификации finite maintenance · 30 октября 2026.

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

Manual dispatch `daily.yml` принимает необязательный `maintenance_spec`. Непустой spec выбирает
отдельную раннюю ветку внутри того же `lexinform-daily` job lock: fresh checkout/restore →
проверка exact state/snapshot/plan → finite mutation → общий checkpoint SQL и polling manifest.
Backfill/index/run/commands не выполняются; inbox не checkout'ится и не изменяется, переданный
`log_message_id` не подтверждается. Обычные запуски с пустым spec сохраняют прежнее поведение.

Единственная операция — уже реализованный finite legacy import/compaction. Произвольные SQL,
replacement DB, пути к скриптам или новые storage/lease modes не принимаются. Apply по умолчанию
закрыт: repository variable `LEXINFORM_STATE_MAINTENANCE` должна быть `on`. Не включайте её до
независимого review и квалификации exact candidate на актуальной изолированной копии state.
Проверка dry run не требует включения apply и не изменяет бизнес-строки или canonical state.

### Подготовка reviewed spec на копии

Сначала восстановите SQL из конкретного state commit в отдельную SQLite текущим кодом.
Сохраните checkout ветки `state`: helper проверяет его HEAD и точные tracked SQL bytes,
а затем сравнивает SQLite с scratch restore этого SQL, включая миграции.
Фиксированный UTC `imported_at` входит в план: не заменяйте его текущим временем при apply.

```sh
uv run python -m tools.state_maintenance prepare \
  --database /tmp/maintenance.db \
  --state-file /tmp/state/lexinform.sql \
  --expected-state-sha <exact-state-commit> \
  --imported-at 2026-10-30T12:00:00Z > /tmp/maintenance-spec.json
```

Spec содержит только version/operation, expected state commit, SHA-256 restored dump и полного
finite plan, фиксированный import time. В нём нет SQL или исходных тел. Проверяйте план и
совместимость копии отдельно; generation spec сама по себе не разрешает production mutation.
В manual dispatch передайте JSON в `maintenance_spec`, оставьте command=`run`, остальные
normal-run flags пустыми/default. `dry_run=true` повторно проверяет exact план без apply.

### Сохранение и отказы

Helper использует прежний transactional plan/apply: known JSON и typed views, FK, exact restore
и gzip budget проверяет сам repository. Только успешный `applied=true` выставляет step output
`maintenance_authorized=true`; no-op, stale/invalid spec, disabled apply и dry run этого не делают.
Final `always()` checkpoint требует этот output и повторяет guard в shell, поэтому даже
restore/migration delta не публикуется после отказа. Успешная локальная mutation без подтверждённого
checkpoint не считается изменением canonical state; чужой remote tip останавливает прежний CAS.
Не rebase'ьте mutation: заново получите свежий state, reviewed plan и новые exact identities.

Сохраните run URL и metadata receipt helper (source/snapshot/plan hashes, import time, applied,
manifest и SQL/gzip sizes), затем проверьте подтверждённый checkpoint SHA и manifest в том же
commit. Зелёный job с no-op/validation не означает push. Миграции остаются append-only; правила
rollback — в [контракте базы](../reference/database.md).

Готовой процедуры arbitrary SQL/disaster restore пока нет. Finite entrypoint не разрешает
замену state старым dump с потерей delivery receipts или command outcomes. Отключение расписания не доказывает
отсутствие queued/manual writers. Новое backend-хранилище или lease service этим не выбирается.
