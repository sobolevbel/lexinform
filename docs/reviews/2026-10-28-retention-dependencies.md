# Зависимости retention: сохранить существующие guards

Исторический аудит · 28 октября 2026. Исходная проверка: schema 55, код
`1f4331a3b190a7b711414f89362dfbb22dd5b49c`; расширение inventory: текущая schema 56.

Решение DA8.4 — **сохранить все существующие guards**. Среди проверенных потребителей
нет основания удалить хотя бы один из них. Очистка, миграция незавершённой работы и
исчерпание legacy inputs не объявляются выполненными. Это продолжение
[решения против общего scheduler](2026-10-27-unfinished-work.md), а не закрытие всего DA8.

Правила и сроки остаются в [state retention](../state-retention.md). Здесь зафиксированы
конечная карта зависимостей и решение по ней. Production state, paid API, сеть и Telegram
для проверки не использовались; состояние нынешней production очереди из неё не следует.

## Четыре операции очистки

`Pipeline` вызывает `prune_runs` и `prune_history` с injected Clock; delivery drain отдельно
вызывает только `prune_runs`. `prune_history` атомарен, dry run откатывает очистку.

| Операция | Зависимость, ради которой guard остаётся |
| --- | --- |
| Удаление старых `runs` | Последний успешный discovery watermark переживает длительную паузу. |
| Очистка `publications.delivery_json` | Только подтверждённый старый sent payload; late DocumentWork, незавершённые digest/continuation и amendment revisions сохраняют нужный input. Строка и delivery identity остаются. |
| Удаление старого `analysis_memo` | Нужен известный settled owner без ожидающих analysis/review/document/delivery/batch dependencies. Отсутствующий owner или legacy unknown не разрешают удаление. |
| Очистка тяжёлых полей `llm_batch_items` | Только после consumption, accounting и provider cleanup, без ready/awaiting primary input. Ledger, request SHA, bindings, ошибки и submission budget остаются. |

У memo и consumed batch body разные blockers: первое сохраняет повторно используемый ответ,
второе уже передало ответ и нужный input следующим владельцам. Их нельзя заменить одним
возрастным DELETE. Полные predicates приведены в canonical policy и в
[проверенном repository](https://github.com/sobolevbel/lexinform/blob/b1d22cbc7afc33bef6f4618b70d2919e0bcfb5bb/src/lexinform/adapters/sqlite_repo.py).

## Потребители и владельцы

| Потребитель | Сохраняемая связь и решение |
| --- | --- |
| Primary analysis/reanalysis | `(term, number)`, generation, ready/awaiting input, intent и unconsumed/unaccounted item. Superseded projection не отменяет paid receipt и accounting. |
| Amendments, supplement, joint, name, explanation | Сначала memo, затем разрешённый exact-purpose/subject/input-key history. Приоритет legacy memo и неизвестные bindings сохраняются. Triage/change-review cache не получает выдуманную immutable history. |
| Consultation, commission и act reviews | Frozen context, baseline/comparison snapshot и тот же act ELI. Pause не истекает от возраста; любой non-NULL ReviewWork удерживает owner memo. |
| Поздние Senate amendments | Historical change/publication и immutable DocumentWork context. Waiting/unsupported удерживают input; complete означает атомарный handoff в revision, а не её отправку. |
| Ручные/автоматические revisions | Original publication, source и edit target; submitting intent и queued/failed/pending/unknown revision запрещают замену. Sent amendment revision сохраняет delivery JSON для поиска по original ID. |
| Delivery, multipart и digest | Frozen HTML/targets, physical membership, held IDs и effect reservations. Unknown требует audited решения; unfinished child удерживает sent root и siblings. |
| Linking и rollover | Mutable work owner отделён от original paid/input owner. Передаются только допустимые primary work/generation; secondary history не переименовывается. |
| Commands и read-only doctors | Команды не повторяются; metadata и historical references остаются доступны. Doctor показывает отсутствие legacy input, но не восстанавливает его и не разрешает очистку. |

`abandoned(work, now)` очищает `context` и `input_bill_json`. Keys, dates, limitation,
ELI/source fingerprint и существующий `comparison_document` остаются. Поэтому прежнее
«все документы удалены» было слишком сильным утверждением. Это сохранённый comparison
snapshot, без нового скачивания или обещания raw archive. Его удаление потребовало бы
отдельного изменения контракта; этот аудит его не предлагает.

## Inventory всех 30 таблиц schema 56

| Группа | Таблицы | Решение |
| --- | --- | --- |
| Четыре очищаемых хранилища | `runs`, `analysis_memo`, `publications`, `llm_batch_items` | Только операции выше; identity rows двух последних не удаляются. |
| Case/event/work | `bills`, `status_changes`, `document_work` | Нет TTL DELETE; current state и historical baseline остаются. |
| Provider ownership | `llm_batches`, `llm_batch_intents` | Accounting, retry budget и uncertain submit ownership остаются; intent удаляется своим lifecycle, не таймером. |
| Paid history | `computation_results`, `result_applications`, `result_applicability` | Result/failure, usage и receipts не очищаются вместе с memo. |
| Editorial history | `content_snapshots`, `content_versions`, `publication_content` | Переживает payload retention и explicit publication deletion; version не доказывает delivery. |
| Source graph | `document_selections`, `source_observations`, `artifacts`, `selection_artifacts`, `artifact_reads`, `source_checks` | Identities, relations, refs и freshness ledger остаются; SHA не является сохранённым body. |
| Extraction metadata | `extraction_results`, `selection_extractions` | Две новые таблицы schema 56 добавляют immutable summaries/FK links, без нового cleanup или body retention. |
| Delivery ownership/audit | `publication_sequence`, `delivery_resolutions`, `delivery_alerts`, `digest_event_links`, `effective_rule_links` | IDs, resolutions, alert cursor и effect dedup не зависят от возраста payload. |
| Commands/link index | `commands`, `rcl_wykaz_numbers` | Нет TTL DELETE; command IDs и dated associations не заменяются нынешними значениями. |

Новые extraction summaries ссылаются на `artifacts`, links — на `document_selections` и
`extraction_results`. Эти родители и связи сохраняются. `not_retained` и
`manifest_not_retained` не обещают сохранность raw/text/block bodies; состав хранения и
backend остаются в [принятом текущем профиле](2026-10-27-storage-profile.md).

## Проверка и границы решения

AST `prune_runs`/`prune_history` совпадает с schema-55 исходниками J130 и
`b1d22cbc7afc33bef6f4618b70d2919e0bcfb5bb`; прежние migrations не менялись. Schema-56
extension проверена read-only на новой in-memory БД. Она не добавляет cleanup rule.
Исходные regression suites, source/XML hashes, abandoned-field probe, таблицы и FK extension
сохранены в [validation.json](../assets/audits/2026-10-28-retention-dependencies/validation.json).

Unsupported/abandoned/legacy work, unknown publications и orphan/NULL-last-used memo могут
удерживать данные бессрочно. Audit не доказывает их исчерпания или runnable state. Explicit
`/forget`/`/republish` остаются отдельными operator lifecycles. Любое будущее упрощение требует
отдельного before/after proof для нужного owner, старых restore inputs, unknown/submitting
states и transfer; закрытие DA8.4 с retain-решением не разрешает удалять guards.
