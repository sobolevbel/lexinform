# База данных и миграции

Статус: актуальный справочник · проверен 27 октября 2026 по локальному коду, схема **v53**.

Источник истины — append-only
[`MIGRATIONS` в sqlite_repo.py](https://github.com/sobolevbel/lexinform/blob/main/src/lexinform/adapters/sqlite_repo.py).
`SCHEMA_VERSION = len(MIGRATIONS)`, версия конкретной базы — `PRAGMA user_version`.
После всех миграций: **24 таблицы, 37 явных индексов
(20 обычных и 17 уникальных), 10 внешних ключей**. Автоиндексы `PRIMARY KEY` и
`UNIQUE` в эти 37 не входят. Копия production dump также восстановлена в v53:
`integrity_check=ok`, `foreign_key_check` не вернул ошибок.

## Визуальная схема

[Открыть схему, все колонки и индексы](../database.html).
HTML содержит снимок v34, включая SQL ограничений и ленту миграций.
При изменении `MIGRATIONS` обновляйте этот справочник; HTML остаётся историческим снимком.

## Таблицы и связи

| Таблица | Ключ | Что хранит |
| --- | --- | --- |
| `artifacts` (v53) | `sha256`; индекс recorded_at | Известная идентичность полного body: SHA, размер, первое время записи; без bytes, locator или retention promise. |
| `selection_artifacts` (v53) | `(selection_id, part_scope, part_index)`; индекс artifact SHA | Проверенный outer-body ref выбранной/наблюдённой части; FK к immutable selection и artifact metadata. |
| `document_selections` (v52) | `selection_id` | Неизменяемые метаданные решения: question, policy version, state, parts/facts/coverage; без extracted bodies и provider scan input. |
| `source_observations` (v52) | `observation_id`; индексы owner/purpose и selection | Историческая связь дела, официальных document relations и решения; первое время записи сохраняется при повторной проверке. |
| `document_work` | `key` | Поздний документ: исходное событие, исторический baseline, контекст batch, backoff и состояние. |
| `bills` | `(term, number)` | Снимки источников, анализ, обработанное наблюдение и состояние очередей. Номер может быть druk, `RPW/…`, `RCL/…` или `WPL/…`. |
| `status_changes` | `id`; уникальный `(term, number, new_fingerprint)` | Изменения, новые стадии, поправки и приложения. |
| `publications` | `id` | Доставка по каналу: вид сообщения, статус, Telegram ID, сохранённый payload и ключ дедупликации. |
| `runs` | `id` | Прогоны, отчёты и `discovery_ok` для последней успешной границы discovery. |
| `commands` | `update_id` | Команды Telegram; выполнение (`executed_at`) отделено от ответа (`handled_at`). |
| `rcl_wykaz_numbers` | `(wykaz_number, project_id)` | Номер wykaz, проект RCL и дата создания; номера wykaz могут использоваться повторно. |
| `analysis_memo` | `key` | Результат LLM по содержимому, цели и контексту; владелец `(term, number)` и `last_used_at`. |
| `computation_results` (v49) | `result_id`; уникальный `(provider, batch_id, custom_id)` | Неизменяемый normalized primary/secondary result либо failure receipt, исходный usage и schema version; без файлов и полных входов. |
| `result_applications` (v49/v50) | Local `id`; unique primary generation identity или explicit target namespace/ref | Collection decision и projection receipt; target хранится в JSON и индексируется без дополнительных columns. Primary applications используют `apply_analysis`; secondary producers ещё подключаются. Доставку не подтверждает. |
| `result_applicability` (v51) | `observation_id`; FK на `result_id`, owner index `(term, number)` | Неизменяемая связь известного paid result с наблюдаемой версией source: URL/kind, записанные fingerprints, metadata SHA и первое время записи; без текстов, raw и копии ответа. Primary producer записывает связь атомарно с проекцией через `apply_analysis`; unknown legacy пропускается. |
| `llm_batches` | `batch_id` | Провайдер, состояние batch, оценка стоимости и даты опроса, завершения и очистки. |
| `llm_batch_items` | `(batch_id, custom_id)` | Задание для bill, метаданные, результат, `consumed_at` и отдельный `accounted_at`. |
| `llm_batch_intents` | `custom_id` | Сохранённые запросы до отправки провайдеру и состояние попытки. |
| `delivery_resolutions` | `id`; уникальный `command_update_id` | Аудит решения оператора: публикация, команда, действие и основание в JSON. |
| `bills.editorial_json` (v36) | Дело `(term, number)` | Выбранное имя, основание, предложения, прежние имена и приоритет оператора с датой окончания. |
| `publications.attention_json` (v37) | Публикация `id` | Оценка события и происхождение срока; сохраняются после очистки отправленного payload. |
| `publication_sequence` | `id = 1` | Последний выделенный ID публикации: удалённые ID не используются повторно. |
| `source_checks` | `(term, number, aspect)` | Последние попытка и успех проверки, число сбоев и дата outage. |
| `delivery_alerts` (v38) | `channel_id` | Отпечаток проблем доставки и время последнего успешно отправленного техотчёта о них. |

SQL объявляет внешние ключи:
`publications.status_change_id → status_changes.id` и
`llm_batch_items.batch_id → llm_batches.batch_id`;
`digest_event_links.source_publication_id/owner_publication_id → publications.id` (v40) и
`result_applications.result_id → computation_results.result_id` (v49) и
`result_applicability.result_id → computation_results.result_id` (v51) и
`source_observations.selection_id → document_selections.selection_id` (v52) и
`selection_artifacts.selection_id/artifact_sha256 → document_selections.selection_id/artifacts.sha256` (v53).
Связи по `(term, number)`, ссылки аудита на публикацию и команду — логические,
без `FOREIGN KEY`. Дайджест относится к неделе и каналу, а не к одному законопроекту.

В v39 `status_changes.decision_changed` сохраняет уточнение исхода при прежней дате
и дереве стадий. Неизвестный исход не считается отказом; `passed` включён в ключ
обработанного наблюдения.

В v45 индекс `ux_pub_amendments_revision` обеспечивает уникальность исправления
по `(channel_id, kind, ref)`. Вид `amendments_revision` хранит отдельный payload
редактирования старого сообщения; `delivery.revision_of` ссылается на исходную
публикацию. Историческая публикация и `status_changes` сохраняются. Batch metadata
содержит неизменяемую цель редактирования. Payload исправления не удаляется retention.

В v47 `bills.commission_review_json` хранит общий разбор документов комиссий RCL, Сейма и Сената, контекст
источников, цитаты и дату проверки. `ux_pub_commission_review` обеспечивает уникальность
публикации по `(term, number, channel_id, ref)`. Незавершённая работа использует
`review_work_json`; в ней сохраняется исходный comparison snapshot, а подготовленный
batch context неизменяем. Старые dump получают `commission_review=NULL`.

С 21 октября совместимые JSON-поля `body`/`forum` сохраняют орган и источник документа,
`recommendation`/`recommendation_evidence` отделяют рекомендацию комиссии от принятия
замечания. `source_fingerprint` в review/work отслеживает проверенный каталог, а
`senate_json.review_documents` — отчёты и официальные заключения со страницы закона.
В v48 `bills.committee_documents_json` сохраняет каталог отдельных материалов комиссий
из Sejm `additionalPrints`: URL, роль, орган, forum и версию метаданных. NULL в старом dump
означает неизвестный каталог, пустой список — проверенный пустой. Первичное заполнение
учитывает прежние `seen_supplements` и не создаёт исторический batch.

V49 начинает историю новых primary collections без backfill. Результат, collection decision и
consumed checkpoint сохраняются в существующей atomic transaction; старый usage accounting и
generation guard сохранены. Новое nullable поле `AnalysisRecord.computation_id` совместимо со
старыми JSON. История сохраняется после очистки memo/batch bodies; состав и временная
избыточность dual write описаны в [retention](../state-retention.md).
Projection receipts добавлены без новой SQL-миграции в [J062](../plans/2026-10-22-document-architecture-progress.md#j062):
сначала `_persist` и direct `reanalyze_bill`. [J063](../plans/2026-10-22-document-architecture-progress.md#j063)
переводит все service writes основного анализа на общий command, включая RCL/wykaz/linking;
identity relocation при rollover остаётся открытым.
[J066](../plans/2026-10-22-document-architecture-progress.md#j066) сохраняет secondary
amendments/supplement/joint/name/explanation в той же таблице без новой SQL-миграции, schema v2
в JSON и без primary generation. Secondary application receipts открыты.
[J067](../plans/2026-10-22-document-architecture-progress.md#j067) добавляет reader review JSON v3
с отдельными input refs и normalized answer;
[J068](../plans/2026-10-22-document-architecture-progress.md#j068) включает их запись в review
collection checkpoint без source text, новой SQL-миграции или изменения submit/pause settings.

V50 добавляет совместимый reader explicit application targets: generation=NULL требует
namespace/ref и projection phase. Два partial unique indexes разделяют primary и target scopes;
SQL CHECK исключает incomplete targets и NULL-identity duplicates. Primary JSON остаётся
прежним, порядок старых rows сохраняется. Local id не является бизнес-идентификатором.
Новых target producers и application backfill эта миграция не включает. См.
[J069](../plans/2026-10-22-document-architecture-progress.md#j069).

V51 добавляет только reader и новую пустую таблицу applicability observations, без backfill.
Identity включает paid ref, owner scope и source fingerprints; время первой записи исключено
из identity. Повторное сохранение сохраняет первое время и не увеличивает dump. Записанные
text/law fingerprints остаются opaque versioned значениями. Metadata SHA исключает текст и
`scan_input`: исходный оплаченный scan manifest не доказывает новый фактический input.
Изолированная свежая копия state сохранила все 457 Bill и 141 memo; прирост пустой схемы —
350 SQL bytes / 50 gzip bytes. J082 reader сохраняет заполненную v51 побайтно при restore/dump,
без schema downgrade. Приёмка: [J083](../plans/2026-10-22-document-architecture-progress.md#j083).

[J084](../plans/2026-10-22-document-architecture-progress.md#j084) включает primary producer
через общий `apply_analysis`: связь пишется с проекцией, включая reuse неизменного результата
для нового source pointer. Collect и preparation без применения связи не создают.
Отдельный дублирующий application receipt не добавляется. Rollback reader J083 сохраняет
заполненную таблицу; source selection, paid input и current reuse policy прежние.

## Снимки и обработанное состояние

JSON хранится в колонках `TEXT`; структуру и совместимые значения по умолчанию задают
[модели](https://github.com/sobolevbel/lexinform/tree/main/src/lexinform/models).
SQL-колонки нужны для ключей, фильтрации и очередей, JSON — для вложенных снимков.

- `summary_json`, `submission_json`, `rcl_json`, `wykaz_json`, `stages_json`,
  `act_json`, `agenda_json` и `senate_json` описывают источники и процесс;
  `analysis_json`, `joint_json`, `supplements_json`, `authors_json` — сохранённые сведения и результаты.
- `observed_process_json` — уже обработанное наблюдение. Sejm checkpoint сохраняет его
  вместе с работой по доставке; сетевых вызовов внутри этой транзакции нет.
- `observed_closure_date` — отдельная база сравнения трекера. Discovery не должен
  перезаписывать её: закрытие из первоначального снимка не является новой новостью.
- `analysis_generation` защищает от применения устаревшего batch-ответа после reset;
  `ready_analysis_json` сохраняет результат со снимком источника для последующего применения.
- `awaiting_batch_since` остаётся до фиксации наблюдения: время в очереди входит
  в срок ожидания вторичного анализа. Ответы `amendments`, `supplement`, `joint`
  поступают через `analysis_memo`, не меняя статус bill.
- Операторский `/explain` использует отдельный purpose `explanation` и `analysis_memo`;
  исходные сведения о тексте сохраняются в batch metadata. Результат не заменяет
  `bills.analysis_json` и не создаёт публикацию. Новая SQL-миграция для этого не нужна.

С 20 октября `rcl_json.stages[].loose_documents` сохраняет файлы непосредственно в
каталоге стадии. `null` в старом снимке означает, что такие файлы ещё не проверялись;
каталог Komisja Prawnicza читается повторно один раз даже при прежней дате изменения.
Пустой список означает проверенный каталог без таких файлов. Поле совместимо со старыми
dump и не требует SQL-миграции.

Подробности — в [наблюдениях и доставке](../process-plans.md).

## Доставка и дедупликация

`queued` означает ожидающую доставку, `skipped` — редакционное удержание.
Перед обращением к Telegram создаётся `pending`; успех даёт `sent`, подтверждённая
ошибка — `failed`. Зависший `pending` становится `unknown` и автоматически не повторяется.
`failed` повторяется в пределах лимита попыток.

`delivery_json` сохраняет payload по каналам, родителя и включённые удержанные изменения.
Если карточка или joint-ответ уже содержит `rendered_text`, повтор использует этот HTML
при любом attention mode и legacy policy. Изменение formatter не пересобирает такой payload.
Новые карточки сохраняют HTML до первой отправки во всех modes; все multipart-части
готовятся заранее. Уже попытанные legacy-записи без HTML обслуживаются прежним handler.
Сводка стадий хранит также `update_group` (ID и снимки участников) и готовый `group_text`;
решение оператора применяется ко всей группе. Его `publication_ids` остаются в аудите
после очистки отправленного payload. `wait_until` задаёт фиксированный предел ожидания
текста, а `absorbed_by` связывает включённую стадию с её доставкой. Эти поля добавлены
в совместимые JSON-модели. Редакционные имена и внимание добавлены в v36/v37.
Общий `ref=updates/<первый-ID>` у статусных записей сохраняет принадлежность к сводке
после retention, чтобы исторический дайджест не терял участников.
Суточная очередь хранится в совместимом `DeliveryPlan.schedule`: маршрут
`daily_updates`/`daily_sittings`, время обнаружения, `not_before` и `deliver_by` в UTC.
SQL-миграция для самого расписания не нужна. Ещё не начатые события остаются
`queued`; payload очереди не удаляется retention. При поглощении рутинных стадий
сохраняются ранняя граница выпуска и конкретные `held_change_ids`; только успешная
или подтверждённая оператором доставка освобождает включённые записи.
Повтор использует его, а не новое состояние законопроекта. Ручные `confirm`, `retry`
и `dismiss` записываются в `delivery_resolutions`; `dismissed` отличен от `sent`
и `skipped`. См. [команды оператора](../operator-commands.md).

До первой попытки `DeliveryPlan.participation` может сохранить актуальное окно участия
отдельно от исходного `bill_json`: даты, ссылку, адрес подачи и число дней из письма RCL.
Отсутствующий snapshot означает прежний формат; snapshot с `consultation=null` означает,
что окно больше не подтверждено. Стадии, анализ и история источника не заменяются текущим
bill. Исчезнувший срок удерживает напоминание в `queued` без расхода попыток;
истёкший срок снимает ещё не начатое напоминание. После фиксации HTML payload неизменен.

В v38 `delivery_alerts` отделяет подтверждённое уведомление оператора от состояния отправки.
Часовой выпуск повторяет ту же проблему через 24 часа; изменение набора, статуса, попыток
или ошибки сообщает сразу. Неудача техотчёта и dry run не продвигают эту отметку.
Она переживает dump/restore и очистку `runs`, не меняя аудит `unknown`.

Частичные уникальные индексы `publications` задают отдельные правила для карточки,
изменения, напоминаний, заседаний и дайджеста. Повторяющиеся события используют `ref`;
SQL всех условий приведён в [визуальной схеме](../database.html#indexes).

В v40 `digest_event_links` сохраняет единственную физическую часть дайджеста для
каждого `digest_only` события. Source остаётся `queued` до подтверждения его части;
технический draft его не покрывает. После успеха source получает настоящий message ID
части, membership остаётся после retention. Failed/unknown части удерживают источники;
`/delivery` разрешает физическую часть, а не отдельный включённый факт. Политика off
и operator override освобождают только unreserved, never-attempted события.

## Дамп и восстановление

Рабочий файл SQLite использует WAL; авторитетный снимок для GitHub Actions —
`lexinform.sql` в ветке `state`. `dump()` возвращает SQL и добавляет
`PRAGMA user_version = N;`. Команда `db dump` сохраняет вместе с ним производный
`pending-batches.json`; batch checkpoints также сохраняют оба файла.
Финальный workflow использует `db checkpoint PATH --branch state`: SQL и manifest
попадают в один commit, а push допускается только от ожидаемого tip. Чужой tip
останавливает writer, включая случай неизменившегося dump; fetch/reset/redump поверх
чужого state не выполняется. Неопределённый ответ push считается успехом только после
проверки, что remote указывает ровно на подготовленный commit. При ошибке локальный
commit сохраняется; автоматического обхода конфликта нет. Удаления обработанных inbox
файлов публикуются только после подтверждённого финального checkpoint.
Ошибка `StateCheckpointError` прекращает текущий pipeline/command, CLI возвращает **75**;
workflow после этого не запускает следующий commands process. Диагностический отчёт
текущего запуска сохраняется, а неисполненная команда не помечается executed/handled.
Workflow задаёт `LEXINFORM_STATE_CHECKPOINT_REQUIRED=true`; отсутствие пути SQL, допустимой
ветки, Git worktree или `origin` останавливает создание sender. Локально без настроенного
state работает noop. Dry-run не делает push; `--no-publish` по-прежнему выполняет batch-анализ
и сохраняет его обязательный pre-submit checkpoint. Общий writer собирается один раз в Container.

Shared checkpoint также охватывает NEW_BILL/JOINT_BILL и их MESSAGE_PART: frozen pending
сохраняется до API, receipt и связанные effects — после local atomic и до следующей отправки.
Продолжение сохраняет реальный reply target из receipt родителя до своего API; восстановленные
failed/queued части идут через ту же границу. При post-save failure удалённый pending после
потери runner становится unknown, а не разрешением повторить отправку. Operator retry сохраняет
audit вместе с pending до повторного API. Attempted legacy card без HTML сохраняет прежний
render callback; exact HTML этой старой попытки задним числом не восстанавливается.

Shared update groups и сохранённые sitting groups также checkpoint'ят всех участников до API
и общий receipt после него. Новые sitting groups сохраняют HTML/membership в off/shadow/on;
старые attempted legacy без membership остаются на прежнем callback.

Новые reader digests во всех attention modes сохраняют части и source membership до первого
API. Каждая часть checkpoint'ит pending до API и receipt/source completion после него.
Команда и финальная digest phase разделяют scope попыток: одна physical часть отправляется
не более одного раза за запуск; следующий запуск может повторить definite failure.
Attempted legacy digest без частей checkpoint'ит статус, сохраняя прежний render callback.
Technical draft не резервирует source events и не получает reader checkpoints.

CommandService checkpoint'ит started до исполнения, unhandled outcome до ответа и handled
до inbox.done, включая повтор уже обработанного файла. Replayed executed command повторяет
только технический ответ. StateCheckpointError фатален; unavailable reply остаётся для повтора,
generic reply error сохраняет прежний handled/failure-note контракт. Dry suppresses новые CP/ack.

Новые одиночные STATUS_UPDATE без edit target сохраняют HTML и все части в off/shadow/on.
Root и MESSAGE_PART checkpoint'ят pending до API и receipt/effects после него; последняя часть
освобождает только saved held IDs и завершает связанный ACT_PUBLISHED. Исходный FAILED без HTML
не сбрасывается в QUEUED и сохраняет legacy callback с checkpoints статуса.
Reader использует любой saved `rendered_text`, включая короткий legacy payload и edits.
Shared attempted set сохраняется между end-of-term, commands, publishing и tracking;
одна physical попытка за execution, следующий запуск может повторить definite failure.

Reader frozen sittings использует сохранённый HTML до re-render/grouping/expiry и сохраняет
legacy target. Новый длинный одиночный AGENDA/AGENDA_CANCELLED сохраняет roundup HTML с card
link и top-level root reply_to=None, children используют receipt parent; каждый physical send
проходит pre/post checkpoint. Frozen sitting с item_json доступен операторскому recovery
независимо от reply. Attempted legacy noHTML не переписывается и может сохранить прежний refusal.

Новые notices об акте, вступлении в силу, итогах консультаций и consultation/decision/hearing
deadlines сохраняют HTML/parts и pre/post checkpoints во всех modes. Legacy dated first-send
сохраняет plan.today; attention-v1 сохраняет прежние refresh/expiry и текущую дату при freeze.
Attempted legacy без HTML сохраняет исходные snapshot/date/callback. Incomplete reminder
не отправляет preview error вместо читательского сообщения.

Card revision reader использует MESSAGE_PART с frozen edit target и logical revision_of;
legacy card/ref тоже распознаётся. Owner должен соответствовать bill/term/channel/target.
Explicit revision_of применяет operation.rendered_sha256 к карточке атомарно с последним
receipt/confirm; legacy projection пока остаётся у прежнего CardRefresher. Edit confirm
принимает только сохранённый target. Обычный single-to-single refresh сохраняет отдельную
MESSAGE_PART operation с compact snapshot, HTML, today, desired hash и explicit revision_of;
pre/post checkpoint покрывает edit. Общий attempted set не повторяет failure того же запуска.
Общий editor поддерживает single/multipart, рост и сокращение во всех callers. Exact parts и
membership фиксируются до первого API; при сокращении старые messages получают superseded text.
Retag/rollover используют ту же операцию; наследуемый alias становится logical owner,
а physical target остаётся прежним. SENT aliases читаются из существующей таблицы.
Edition validation проверяет owner и child root/parent/index перед повторным использованием
physical targets, включая pruned SENT root с сохранившимся DISMISSED child.
Active card-edit root/child блокирует forget/republish и прежние direct card editors того же
channel/physical target, включая aliases. SENT root с unresolved child остаётся защищённым.
CLI safety error перечисляет operations, которые сначала нужно разрешить.
FAILED frozen card-edit root/child допускает audited retry/dismiss независимо от числа попыток;
общая чистая проверка service/repository требует действительного owner/target и membership.
Confirm остаётся только для UNKNOWN. Retry сохраняет exact payload; dismiss не применяет digest
и останавливает прежнюю desired edition при refresh. Новая desired edition допустима.
Повторное чтение уже SENT child из прежнего retry snapshot не считается delivery failure.
Audited retry/dismiss также допускает FAILED frozen continuation первоначального NEW_BILL или
JOINT_BILL с SENT root, exact membership/parent/index и совпадающими bill/term/channel.
Service и repository используют общий pure qualifier; root receipt и payload не переписываются.

Новые status update edits также сохраняют HTML/parts и исходный today; каждый edit/child
использует общий checkpoint. Узкий freeze для FAILED legacy edit описан ниже.

Сохранённые consultation/commission reviews и amendments revisions с HTML, включая parts,
используют ту же pre/post boundary. Review-work handler пробрасывает fatal checkpoint error.
Paused flags и прежнее отклонение FAILED legacy без HTML сохраняются.
Автоматическая amendments revision создаёт свежий semantic plan из исторического контекста;
она не наследует старые HTML, physical membership и held/digest effects исходной публикации.

Legacy attempted sitting callback и STATUS_UPDATE edit без HTML также используют pre/post
checkpoint. Их исторические HTML и shared membership не восстанавливаются из предположений;
старый sitting без membership сохраняет per-row operator recovery.
Одночастный FAILED STATUS_UPDATE edit с legacy policy, исходными Bill/change/today/target,
без participation override, HTML и physical membership может сохранить первый HTML перед retry.
Узкий writer добавляет только HTML и optional `render_origin=legacy_snapshot`; status, attempts,
source fields, flags и target неизменны. UNKNOWN/PENDING и иные shapes не переписываются.
Неизвестные JSON-поля сохраняются; CAS сравнивает исходный raw payload и счётчик попыток.
Этот origin означает новый freeze сохранённого контекста, а не восстановленный HTML прежней
попытки; `/delivery` показывает это явно. Старый reader использует обычный prepared payload.
Некоторые attempted legacy rows без HTML сохраняют прежние callbacks. Полная граница остаётся задачей
[архитектурного плана](../plans/2026-10-22-document-architecture.md#da1).

`restore()` сначала проигрывает SQL в отдельной базе в памяти. После успешной проверки
заменяет таблицы целевой базы, временно отключив внешние ключи, восстанавливает версию
и выполняет недостающие миграции. Дамп без строки версии считается v1.
Каждая миграция выполняется в своей транзакции; всё восстановление не является одной
атомарной транзакцией. Затем JSON-снимки приводятся к компактному представлению.

Хранение ограничено правилами [state-retention.md](../state-retention.md): старые отчёты,
payload и ненужные memo очищаются, но идентичности публикаций, учёт batch и неопределённая
работа сохраняются. Старые memo без известного владельца защищены от удаления.

## Изменение схемы

Добавляйте SQL только в конец `MIGRATIONS`, сохраняйте совместимые значения по умолчанию
в моделях и row mapping, расширяйте проверку восстановления старого дампа
`test_restore_of_a_v1_dump_applies_every_later_migration`.
Полная процедура — в
[CONTRIBUTING.md](https://github.com/sobolevbel/lexinform/blob/main/CONTRIBUTING.md#database-schema-and-migrations).
Downgrade нет: откат требует прежнего кода и прежнего дампа из истории `state`.

## История версий v8–v35

Сентябрь 2026. Более поздние версии описаны в разделах выше.

| Версия | Что добавила |
| --- | --- |
| v8 | `rcl_json` |
| v9 | `bills.discontinued_at`, `status_changes.discontinued` — конец каденции Сейма |
| v10 | `bills.linked_wykaz_number` — druk, продолжающий RCL-ветку, сохраняет номер wykaz для тега |
| v11 | уникальный индекс напоминаний о слушаниях (bill, channel, дата); `status_changes.amendments_json` — сводка поправок Сената или комиссии |
| v12 | уникальный индекс ответов `joint_bill` (bill, channel) |
| v13 | таблица `commands` — команды оператора по Telegram update id |
| v14 | `commands.executed_at` — команда без ответа отвечается снова, но не выполняется повторно |
| v15 | `bills.wykaz_json` — записи `WPL/UD408` |
| v16 | `publications.rendered_sha256` — последний рендер карточки, чтобы найти расхождение без запроса к Telegram |
| v17 | уникальный индекс напоминаний о конституционных сроках (bill, channel, фаза) |
| v18 | `bills.supplements_json` (о каких поданных документах канал уже сказал), `status_changes.supplements_json` (дайджесты в обновлении) |
| v19 | уникальный индекс отзыва заседания (bill, channel, `ref`) |
| v20 | `bills.joint_json` — чем принт отличается от совместно рассматриваемых; повтор, `/preview` и `/republish` не платят снова |
| v21 | статус `skipped_joint` упразднён: строка старого дампа переходит в `analysis_pending` |
| v22 | `rcl_wykaz_numbers` — номер wykaz каждого проекта листинга RCL с датой создания (номера переиспользуются) |
| v23 | недельный дайджест: `ux_pub_digest` на `(channel_id, kind, ref)`; `ix_pub_channel_sent` на `(channel_id, status, sent_at)` — без него SQLite выбирал `ix_pub_status` |
| v24 | `bills.observed_closure_date` — заполнена из `closure_date` у строк со `stages_fingerprint`, поэтому деплой не объявил закрытий (инцидент 2111) |
| v25 | `bills.observed_process_json` (неизменяемый baseline `ObservedProcess`), `publications.delivery_json` (факты для повтора, замороженные при отправке), `status_changes.consultation_opened`, `analysis_memo` |
| v26 | `llm_batches`, `llm_batch_items` — отправка в batch API и bill каждого `custom_id` |
| v27 | `llm_batch_intents` — запрос, сохранённый до вызова провайдера (`queued` повторяется, `submitting` ждёт сверки) |
| v28 | `bills.analysis_generation` (reset делает старый ответ устаревшим), `bills.ready_analysis_json` |
| v29 | `llm_batch_items.result_json`/`accounted_at` — ответ сохраняется до применения |
| v30 | `llm_batches.forgotten_at` — собран и удалён у провайдера |
| v31 | `bills.senate_json` — страница акта на senat.gov.pl: принт Сената, комиссии с e-mail, их заседания |
| v32 | `awaiting_batch_since` — отложенные наблюдения не теряются за watermark discovery |
| v35 | `bills.observation_mode`/`observation_basis` (NULL — неизвестно, а не выключено), `source_checks` (последний успех каждого аспекта, отказы и outages раздельно) |

## Разбор документов и продолжения (v41–v43)

`bills.consultation_review_json` и `act_application_review_json` содержат исходный
ReviewContext и проверенный DocumentReview, точные источники и hashes, время и baseline.
`review_work_json` сохраняет work до скачивания и затем до результата batch. У secondary
ответа собственный purpose/memo; он не меняет статус основного анализа. Pending work
защищает memo от retention. Неизменный retained review применяется без новых токенов.

`message_part` имеет отдельный ref `message/ROOT/INDEX`; `DeliveryPlan.continuation_ids`,
`continuation_root`, `continuation_parent`, `continuation_index` связывают физическую цепочку.
Все части готовятся до первой попытки; следующая ждёт sent предыдущей. Parent и HTML
первой попытки сохраняются для retry; unknown разрешается оператором. Незавершённая цепочка
защищает payload sent родителей от retention. Held effects завершаются последней частью.
Первая часть может быть sent при failed/unknown продолжении: это состояние физических
частей, а не подтверждение прочтения полного сообщения.

`effective_rule_links` (v43) связывает `(term, number, channel_id, rule_ref)` с публикацией.
Связь не удаляется при очистке payload; unknown или dismissed нельзя обойти другим ref.
Стабильный ref содержит ELI, locator и подтверждённую дату, без wording/prompt version.
Canonical history привязана к тому же ELI; первичное заполнение старого акта исключает
прошедшие дополнительные даты до baseline.

## Повторная загрузка RPW (v44)

Миграция возвращает в `text_prefilter_pending` записи RPW, отсеянные из-за Orka HTTP 404,
если у них нет анализа, связи с продолжением и признака завершения. Ошибка загрузки
сохраняется для диагностики до успешной проверки. Решения оператора и содержательные
отказы префильтра не меняются. После обновления следующий прогон повторяет загрузку файла.

V52 добавляет совместимый слой чтения source observations и deduplicated selection metadata.
Новые таблицы пусты после миграции; producers ещё не включены, старые результаты не backfilled.
Сохраняется достаточное адресуемое evidence официальных связей (`TextDocument`), а не полный
каталог сайта. Identity решения включает точные метаданные и policy version; observation identity
включает owner/purpose/catalog relations/selection ref, исключая время первой записи.
Обе записи атомарны, SQL identity columns проверяются против JSON. Полные тела и scan provider
input отвергаются; `from_selection` сохраняет metadata view. Это не legal edition и не raw archive.
Свежий pin `2f310768040c39deb87f9de1e40e1440cb396aad`: все 458 Bill и прежние rows сохранены,
пустая схема +814 SQL / 201 gzip bytes; J094 reader сохраняет populated v52 dump побайтно.
См. [J095](../plans/2026-10-22-document-architecture-progress.md#j095).

[J096](../plans/2026-10-22-document-architecture-progress.md#j096) включает первый producer:
text prefilter сохраняет завершённое source decision и outcome одной transaction. Worker
передаёт metadata, запись идёт на calling thread в input order, время берётся из Clock.
Новые строки не требуют paid result; missing fetch не становится успешным наблюдением.
Повтор не создаёт историю polling и не меняет первое время. Остальные producers ещё открыты.

V53 добавляет совместимый reader known complete-body metadata и нормализованных part refs.
Точный размер не восстанавливается из text/law hashes, страниц или существующего агрегата bytes.
Старые refs не получают guessed size/date. Первое recorded_at берётся у caller Clock и хранится
UTC; повтор SHA/размера сохраняет первое время, конфликт размера отвергается. Ref указывает
на точный part scope/index immutable selection; archive member SHA не подменяет container SHA.
Обе FK цели должны существовать. Поля ownership/identity доступны SQL; это metadata projection,
не новая derived business status, raw store или GC pin.

Production producers пока не включены. Fresh state `dc97f63814eaa967287e7f8c834798d86b88888e`:
все прежние rows и **462 Bill JSON** exact; пустая схема +709 SQL / 173 gzip bytes. J098 reader
сохраняет populated v53 dump без schema conversion. См.
[J099](../plans/2026-10-22-document-architecture-progress.md#j099).

[J100](../plans/2026-10-22-document-architecture-progress.md#j100) включает metadata capture:
TextLoader отдаёт уже известные complete-body SHA/size без дополнительных скачиваний. Text
prefilter пишет metadata/refs вместе с source decision/status; pipeline добавляет остальные
известные bodies в finish-run transaction. Metadata survive counter reset/cache reuse, без raw
bytes; первое время сохраняется. `DocumentPart.source_sha256` — тот же outer-container/member
выбор, без нового serialized field. Неполные/failed responses не получают guessed SHA/size.
[J102](../plans/2026-10-22-document-architecture-progress.md#j102) добавляет observations категории
analysis для first/re-analysis/explanation, включая standalone calls. Metadata view исходного
selection формируется до input fitting и сохраняется после prepare, независимо от paid input/result identity. Worker возвращает metadata,
caller transaction записывает decision/observation/known outer refs; ошибка модели сохраняет
завершённое source decision, source outage — нет. Batch-ready reuse без нового source read не
создаёт фиктивное observation. [J103](../plans/2026-10-22-document-architecture-progress.md#j103) использует общий
`DocumentSourceRead`/recording helper в prefilter, primary analysis и review. Review worker
возвращает исходный file selection и physical identities отдельно от ReviewDocument; запись
идёт на caller thread в input order, до подготовки paid review. Snapshot reuse без нового
чтения не создаёт observation. Неизвестный policy остаётся unknown; source history не объявляет
файл допустимым для конкретного role/digest.
[J104](../plans/2026-10-22-document-architecture-progress.md#j104) использует тот же writer перед
обычными committee amendments, рекомендациями и supplements. Source metadata сохраняются до
full-input budget refusal и подготовки model context; memo reuse без чтения не получает новый
source observation. Senate special path и worker historical-comparison reads этим не меняются.
