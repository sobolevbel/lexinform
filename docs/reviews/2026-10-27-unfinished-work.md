# DA8: владельцы незавершённой работы

Исторический аудит · 27 октября 2026 · J078 `4ed9a94403a78f0ffe1f5dea540bf3d53b5558af`.

Общий `WorkItem` scheduler сейчас не вводим. У позднего документа Senate и document review
разные правила ожидания, обновления источника и применения ответа. Уже общие batch intents,
оплаченные results/applications и Publication ledger дают им один submit/send path. Ещё один
engine потребовал бы большого набора purpose callbacks, не устранив эти различия.

| Контракт | Поздний Senate документ | Document review |
| --- | --- | --- |
| Identity/owner | `DocumentWork.key=amendments/{change.id}`, отдельная SQL row, term/number/change_id | `ReviewWork` в Bill JSON; source key, context memo key, purpose |
| Baseline | Frozen Bill/detail/stage и original SENT publication | Frozen input Bill до context; затем context, baseline day, source fingerprint, comparison snapshot и ELI |
| Ожидание источника | До семи неудачных probes, backoff `min(7, 2**attempts)` дней; затем unsupported | Context без документов проверяется не чаще раза в сутки; общего source-attempt budget нет |
| Provider retry | Existing amendments batch/overflow engine; Waiting не расходует source attempts | Existing review batch/overflow; exhausted submissions оставляют abandoned marker для dedup |
| Outage | ServiceUnavailableError останавливает фазу без defer/attempt | Partial error и выход из advance без abandonment/attempt |
| Dependencies | Historical change, SENT single-message original; отсутствие queued/uncertain revision, группы или digest owner | Frozen context, purpose flag; final-act result применим только к тому же текущему ELI; commission findings имеют собственный scope |
| Refresh | Поиск late document до подтверждения, затем документ/context закреплены | Новый commission source fingerprint может потребовать отдельной review work |
| Apply | Amendments в StatusChange, revision queue и complete work в одной транзакции | Review projection, result receipt, delivery preparation и removal work в одном checkpoint |
| Terminal outcome | Complete значит handoff в revision queue; unsupported сохраняется | Removal значит application/handoff; abandoned marker сохраняет dedup, не означает delivery |
| Effects | Общие intents/items и Publication ledger | Те же shared owners, отдельного submit/send path нет |

`BillStatus`, provider job state и `PublicationStatus` остаются разными состояниями.
Потерянный source не разрешает подменить historical baseline новым Bill или другой редакцией.
Final-act pause сохраняется: наличие legacy work не разрешает новый paid purpose.

DA8.1 выполнен этим сравнением и повторным запуском существующих регрессий. DA8.2 завершён
решением против универсального execution kernel. Nullable result refs и application targets
уже общие; диагностику ownership продолжаем отдельно в DA10. DA8.3 перенос через общий scheduler
неприменим при этом решении: оба existing owners остаются единственными. В DA8.4 retention
не упрощаем: защиту зависимостей сохраняем, а любое последующее сокращение требует отдельного
измерения legacy inventory и before/after регрессии. Это не перенос work и не migration.

`prune_history` сохраняет original delivery, пока DocumentWork не complete, в том числе
unsupported. Review work защищает bill memos, включая abandoned markers. Дополнительно
защищаются pending/uncertain publications, intents, unconsumed/unaccounted items и ready/awaiting
analysis. В коде abandoned marker может удерживать unrelated memo: это observation, а не
доказанный безопасный кандидат удаления. Размеры и точные dependencies потребуют отдельного gate.

Проверяемые исходники на J078:
[DocumentWork](https://github.com/sobolevbel/lexinform/blob/4ed9a94403a78f0ffe1f5dea540bf3d53b5558af/src/lexinform/models/documents.py),
[ReviewWork](https://github.com/sobolevbel/lexinform/blob/4ed9a94403a78f0ffe1f5dea540bf3d53b5558af/src/lexinform/models/reviews.py),
[Senate retry/apply](https://github.com/sobolevbel/lexinform/blob/4ed9a94403a78f0ffe1f5dea540bf3d53b5558af/src/lexinform/services/tracking/service.py),
[review request/advance/apply](https://github.com/sobolevbel/lexinform/blob/4ed9a94403a78f0ffe1f5dea540bf3d53b5558af/src/lexinform/services/document_reviews.py),
[retention](https://github.com/sobolevbel/lexinform/blob/4ed9a94403a78f0ffe1f5dea540bf3d53b5558af/src/lexinform/adapters/sqlite_repo.py).
Эти пять modules не изменены относительно J077; J078 добавляет caller history snapshots
в analysis service, сохраняя перечисленные ownership/retry/application contracts.

Повторно пройденные регрессии (137 passed): late attachment после restore и без новой стадии; frozen context при
исчезнувшем source; повтор late work без новой/заменённой revision; unknown edit с exact target;
review unknown submission и сохранность BillStatus; pause и queued legacy work; comparison
snapshot после restore; independent purpose flags; generic retention и protected paid inputs.
Результаты, source hashes и воспроизводимый inventory observer сохранены в [acceptance artifact](../assets/audits/2026-10-27-unfinished-work/validation.json).

Ни корпусная accuracy разметка, ни live pending-work migration этим не подтверждаются.
Новый scheduler, DDL, retry budget, retention defaults и reader payload не меняются.

Изолированный replay state `f34e63e7` (SQL SHA `73e37426…3b71f`) содержит три waiting
DocumentWork, две consultation review work с context и семь abandoned markers
(пять consultation, два act). Intents и unconsumed/unaccounted items в этом pin отсутствуют.
Это срез 27.10 06:26 UTC, а не свежая оценка production. Корректность live ELI/dependencies
из одних counters не следует; существующие guards защищают эти записи и не удаляются.

По действующим owner predicates review work удерживает 18 memo rows; DocumentWork не
удерживает memo rows или sent delivery payloads в этом pin. Эти counters не доказывают, что
какую-либо из 18 записей можно удалить: abandoned/live work и прочие guards пересекаются.
Отсутствие сохранённого original payload также не разрешает реконструировать его по сегодняшнему Bill.

Связи: [план DA8](../plans/2026-10-22-document-architecture.md#da8), [журнал J079](../plans/2026-10-22-document-architecture-progress.md#j079).
