# Батч для всего, кроме триажа: amendments, supplement, joint

23.09.2026. План проверен; исполнение начато.

## Контекст

Сейчас в батч идут первый анализ и реанализ (`llm_batch_enabled`, включено в `daily.yml`):
`AnalysisService._prepare` → `_enqueue` → фаза `submit batches`. Сбор — `collect_batches` в начале
каждого прогона и прогон `batch-ready` от VPS-поллера. Остальные платные вызовы синхронные:

| вызов | где | модель | статус |
|---|---|---|---|
| triage | `AnalysisService._triage_verdict` | Sonnet 5 | синхронный намеренно: гейт перед анализом, вне плана |
| amendments | `StatusTrackingService._attach_amendments` → `summarize_amendments` | Opus 5.5 | **этот план** |
| supplement | `_attach_supplements` → `digest_supplement` | Opus 5.5 | **этот план** |
| joint | `PublishingService._compared` → `compare_joint` | Opus 5.5 | **этот план** |
| `/analyze`, срочный билл, dry run | — | — | синхронные намеренно: кто-то ждёт, окно закрывается или откат |

«Всё, кроме триажа» — это три вызова. Решено с владельцем:
1. Полный план, по фазам, с гейтом для amendments.
2. Пока ответ в батче, пост **ждёт**. После дедлайна модель спрашивают синхронно.

## Стоит ли: цифры

Прод, 113 прогонов 07–23.09 (`runs.report_json.llm_calls`, цены на дату вызова):

| kind | вызовов | $ |
|---|---|---|
| analysis | 34 | 12.63 |
| reanalysis | 4 | 3.14 |
| triage | 61 | 1.30 |
| amendments | 1 | 0.09 |
| joint | 2 | 0.04 |
| supplement | 0 | 0.00 |
| всего | | 17.20 |

Это первые две недели канала, в них в основном бэклог первых анализов. Поэтому ниже оценка по
корпусу. Term 10, 116 друков прошли ключевые слова и триаж, это верхняя граница отслеживаемых.
Скрипт в scratchpad; перенести в `../lexinform-corpus/checks/87_secondary_calls.py`.
- amendments: 26 постановлений Сената с поправками и 68 отчётов комиссий о поправках → ≈$6.6
  (13.7k входа и 0.8k выхода на вызов, как в проде);
- supplement: 41 документ, **все 41 — сканы**, в среднем ≈34k токенов → ≈$6.4;
- joint: от единиц до двух десятков вызовов → ≤ $0.4.

Итого ≈$13.5 за каденцию. Батч экономит ≈$6.7, то есть ≈$1.7 в год. Для сравнения: первые анализы
term 10 стоят ≈$35 по ценам Opus 5.5, и батч уже экономит половину этой суммы плюс реанализы.
**Вывод:** деньгами это не окупается. Смысл другой: у всех платных вызовов, кроме триажа, один
путь, а самые дорогие вторичные вызовы (сканы supplement) идут за полцены. Риск сосредоточен в
трекинге, поэтому фазы и гейт.

## Устройство (решение)

1. **Задание = ключ мемо.** Для трёх видов `custom_id` — ключ `_memo_key` (64 hex, допустимо у
   обоих провайдеров). Состояние задания читается из существующих таблиц:
   - intent queued/submitting или открытый item → ждём;
   - item собран с ошибкой → сразу синхронно;
   - ответ лежит в `analysis_memo` → готово.
2. **Мемо — единственная передача.** Сбор пишет запись в мемо и помечает item. Статус билла он не
   трогает. Обычный проход на следующем прогоне находит ответ в мемо и заканчивает без нового
   вызова (так уже устроен первый анализ, см. докстринг `collect_batches`).
3. **Отложить = ничего не записать.** Пока не готовы все сводки билла, трекинг не коммитит
   наблюдение: стадии, fingerprint, `status_changes`, публикацию. Публикация не создаёт строку
   `joint_bill`. Поэтому не меняются статусы публикаций, `DeliveryPlan`, `Poster` и рендер.
   Стадии, пришедшие за время ожидания, попадут в тот же пост в порядке дерева: ни второго
   поста, ни «fills in the past».
4. **Метка против водяного знака** (класс B35). `bills.awaiting_batch_since` ставится, когда
   наблюдение отложено, и снимается в атомарном блоке `_detect`. `list_tracked(changed_since)`
   берёт такие биллы всегда.
5. **Дедлайн** `llm_batch_max_wait_hours` = 6. Для задания он считается от создания intent (включая queued/submitting), для билла — от
   `awaiting_batch_since`. После дедлайна или ошибки item — синхронный вызов, ровно как сегодня.
   Поздний ответ ляжет в мемо (`ON CONFLICT DO NOTHING`) и будет учтён в стоимости: это редкая
   двойная оплата, принятая сознательно.
6. **Кто никогда не ждёт:**
   - dry run;
   - команды: `/refresh` (`check_bill`), `/republish` (`publish_bill`);
   - срочный билл (`_batches(bill)` → `window_closes_within`);
   - вид, которого нет в `llm_batch_kinds`;
   - вид, модель которого (`llm_*_model`) не у `llm_batch_provider`.
7. **Отличие от реанализа намеренное.** У реанализа пост о стадии уходит сразу, а «текст
   обновился» приходит отдельным событием (`test_a_batched_re_analysis_posts_nothing_until_collected`).
   Здесь сводка и есть содержимое поста, поэтому ждёт весь пост.

## Фаза 0 — побочные находки и замер (без батча)

- **0.1** `models/events.py::amendments_stage` сейчас считает отчёт «uchwalić projekt ustawy bez
  poprawek» документом с поправками. Такой отчёт оплачивается и получает блок «Что меняют поправки
  комиссии». В term 10 таких отчётов 95, среди 116 кандидатов — 6. Исключить. Тест дописать в
  `tests/unit/test_events.py::test_amendments_stage_is_the_senate_print_or_a_report_on_amendments`,
  строку добавить в `docs/BUGS.md` (P2).
- **0.2** `digest_supplement`: включить мемо и для скана (`_memo_key` уже учитывает
  `scan.sha256/pages/of_pages`). Батчу это нужно для передачи результата, а сбой до чекпойнта
  перестаёт оплачивать скан дважды. Тест: скан, сбой записи → повтор без вызова модели. В
  `docs/process-plans.md` убрать «A filed document read as a scan is still not memoized».
- **0.3** Замер через неделю батчей анализа в проде: `completed_at − submitted_at` в
  `llm_batches`. Это время до сбора, включая поллер, то есть то, что увидит читатель. Проверить,
  что таймер поллера активен на VPS. **Гейт для фазы 5:** не меньше 10 собранных батчей, p90 не
  больше 2 ч, поллер работает. По замеру уточнить дефолт `llm_batch_max_wait_hours`.

## Фаза 1 — типизированный контракт батча (поведение не меняется)

- `models/batch.py`:
  - `BatchRequest.ctx` → дискриминированная сумма вопросов:
    - analysis|reanalysis → `BillContext`;
    - amendments → `AmendmentsContext`;
    - supplement → `SupplementContext`;
    - joint → `JointContext`.

    `call_kind` выводится из вопроса. `model_validator(mode="before")` поднимает legacy JSON
    `{call_kind, ctx}` из `llm_batch_intents.request_json`.
  - `BatchResult.analysis` → сумма ответов (`Analysis | Amendments | DocumentDigest |
    JointComparison`). Legacy `{"analysis": …}` из v29 `result_json` грузится.
  - `BatchItemMeta` остаётся для анализа. Для трёх видов — `DigestItemMeta(memo_key,
    prompt_version, source_kind, title, compared_with)`: при подаче intent удаляется, а item хранит
    только meta, так что meta должно хватать, чтобы собрать запись. Тип выбирается по `call_kind`
    в `sqlite_repo._row_to_llm_batch_item` и `_batch_intents`.
- `ports.py::BatchBackend`: `fetch_results(batch_id, kind)`. Батч однороден по виду, потому что
  `_submission_groups` уже делит по нему. Поправить докстринг порта.
- `adapters/llm_anthropic.py`:
  - общий `_batch_params(model, system, user_prompt, output_model, scan)`: adaptive thinking и
    `output_config.format` через `_json_output_format`;
  - `prepare_request` берёт по виду те же `*_system_prompt`/`build_*_prompt`, что синхронные
    вызовы;
  - модель — `request.model or self._model`;
  - `_batch_result(item, output_model)`.
- `adapters/llm_openai.py`: то же с `gpt51_*` промптами и `_AMENDMENTS_SCHEMA`,
  `_DOCUMENT_DIGEST_SCHEMA`, `_JOINT_COMPARISON_SCHEMA`; `_batch_result_line(line, output_model)`.
- `services/analysis.py`:
  - `_submission_groups` делит по паре (вид, модель): у OpenAI один файл — одна модель;
  - `_collect_batch` передаёт `batch.call_kind`;
  - `_consume` ветвится: анализ как есть, остальные виды — в `_consume_digest`. Там запись с
    происхождением (модель ответа, `prompt_version` из meta, токены); затем в одном `atomic()`
    `save_analysis_memo` и `mark_llm_batch_item_consumed`; потом `_charge_batch_result` и
    `self._memo.setdefault`. Статус и поколение билла не проверяются;
  - `submit_queued_batches` проверяет устаревание (status/generation) только у
    analysis/reanalysis.
- `cli.py::attach_batch_intents`: отказывать при смешении видов, как при смешении провайдеров.
  `services/commands.py::_status`: «orphaned» считать только по заданиям analysis/reanalysis.
- `tests/fakes.py::FakeBatchBackend`: отвечать по виду, скрипт по паре (номер, вид).
- Тесты:
  - legacy intent, item и result загружаются;
  - payload каждого вида у обоих адаптеров: system prompt, схема, имя схемы (рядом с
    `test_the_batch_request_sends_the_same_analysis_schema_as_the_synchronous_call`);
  - для каждого вида невалидный ответ одного item даёт `error`, остальные собираются (семантика
    B40);
  - группы по (вид, модель);
  - все существующие batch-тесты зелёные; в них меняются только сигнатуры (`fetch_results`,
    `ctx`), ожидания те же.

## Фаза 2 — «спросить или подождать», настройки, v32

- `services/analysis.py`:
  - значение `Waiting(since)`;
  - общий `_ask_or_wait(bill, kind, ctx, key, sync_call, *, may_wait)`:
    1. ответ в мемо → `_reused`;
    2. иначе проверить, можно ли батчить: `may_wait`, `_batches(bill)`, вид в `batch_kinds`,
       модель у провайдера, не dry run;
    3. задания нет → подать intent (`prepare_request` с моделью вида, `DigestItemMeta`, резерв в
       `CostLedger`) → `Waiting`. Если резерв не влез в лимит прогона → синхронно: сводки этим
       лимитом и сегодня не держат;
    4. задание открыто и моложе дедлайна → `Waiting`;
    5. ошибка или дедлайн прошёл → синхронная ветка, это сегодняшний код (`charge` +
       `_remember_analysis`).
  - `summarize_amendments`, `digest_supplement`, `compare_joint` получают `may_wait=False` и
    возвращают `Record | Waiting | None`. У joint появляется ключ мемо
    (`_memo_key(bill, "joint", ctx)`); `bills.joint_json` и `compared_with` не меняются.
  - `AnalysisOptions`: `batch_kinds`, `batch_models` (модель каждого вида), `batch_max_wait`.
- `adapters/sqlite_repo.py` (и порт `BillRepository`):
  - миграция **v32**: `ALTER TABLE bills ADD COLUMN awaiting_batch_since TEXT` и
    `CREATE INDEX ix_llm_batch_items_custom ON llm_batch_items(custom_id)`;
  - `batch_job(custom_id)` → состояние (queued, submitting, open, failed) и время подачи;
  - `set_awaiting_batch(term, number, since | None)`; поле в `_row_to_bill`;
  - в `list_tracked(changed_since)` добавить `OR b.awaiting_batch_since IS NOT NULL`.
- `settings.py`:
  - `llm_batch_kinds`: через запятую, по умолчанию `analysis,reanalysis` (сегодняшнее
    поведение). Тип `Annotated[frozenset[CallKind], NoDecode]` плюс валидатор `mode="before"`:
    без `NoDecode` pydantic-settings 2.15 сначала пытается разобрать значение из env как JSON.
    `llm_batch_enabled` остаётся главным выключателем;
  - `llm_batch_max_wait_hours` = 6.
- `container.py::analysis_options`: модель каждого вида берётся из `llm_*_model`, провайдер — из
  префикса модели, как в `_llm_backend`. Дописать `.env.example`.
- Тесты:
  - `test_restore_of_a_v1_dump_applies_every_later_migration` (v32);
  - unit на каждую ветку `_ask_or_wait`;
  - `test_container`: правила вида и провайдера.

## Фаза 3 — joint (публикация)

- `services/publishing.py`: цепочка `publish_new(..., may_wait=True)` → `publish_bill(bill, result, *,
  may_wait=False)` → `_publish_joint` → `_compared(bill, may_wait)`.
  - При `Waiting` строка не создаётся, и это третий исход, не failed: `PublishingResult.waiting`.
  - На следующем прогоне кандидата снова приносят `list_publish_candidates` и
    `list_joint_reply_candidates`; водяного знака там нет. Ответ берётся из мемо, и реплика уходит
    с различиями.
  - Исключения `_compared` по-прежнему ловятся: реплика уходит без сравнения. Сравнение теперь
    может задержать реплику, но не остановить её. Инвариант в CLAUDE.md поправить так же.
- `services/pipeline.py::_publish` передаёт `may_wait=True`. В `RunReport` добавить `batch_waiting`
  и строку в отчёт (`telegram_format`).
- `daily.yml`: `LEXINFORM_LLM_BATCH_KINDS: analysis,reanalysis,joint`.
- Сценарии в `tests/scenario/test_joint_batch.py`, `World(batch=True, batch_kinds=…)` (расширить
  `tests/harness.py`):
  - реплика ждёт, после `resolve` уходит с блоком различий, синхронных joint-вызовов 0;
  - дедлайн → синхронно, а поздний ответ второй реплики не даёт;
  - ошибка item → синхронно;
  - `/republish` и dry run → синхронно;
  - workers 1 и 4 дают одно и то же.

## Фаза 4 — supplement (трекинг)

- `services/tracking/service.py`:
  - `check_updates(..., may_wait=True)` зовёт пайплайн; `check_bill` (`/refresh`) передаёт
    `may_wait=False`; `_check_processes` пробрасывает флаг.
  - `_detect_change`: порядок не менять. Сначала `_reanalyze_new_text`, он подаёт своё задание,
    потом сводки. Если `bill.awaiting_batch_since` старше дедлайна, `may_wait` гасится. Если хоть
    одна сводка вернула `Waiting`, возвращается признак «отложить». Intents подаются в вызывающем
    потоке, в теле цикла, а не в `_fetch`.
  - `_detect` при «отложить»:
    1. `set_awaiting_batch(..., since=now)`, если метка ещё не стоит;
    2. `result.waiting += 1`;
    3. вернуть None до атомарного блока. Ничего не сохраняется, кроме синхронного реанализа, если
       он случился: `docs/process-plans.md` это разрешает.

    В атомарном блоке метка снимается.
  - `_attach_supplements` принимает `Waiting`. `bare_supplement` остаётся только для ошибок.
- `TrackingResult.waiting` → `RunReport`. `/status` (`StatusSnapshot` и рендер) показывает биллы с
  `awaiting_batch_since` и их возраст.
- `daily.yml`: добавить `supplement`.
- Сценарии в `tests/scenario/test_tracking_batch.py`:
  - подан OSR → поста нет, стадии не записаны, метка стоит;
  - `resolve` → collect → один пост с дайджестом, синхронных вызовов 0, метка снята;
  - **водяной знак сдвинулся** (как в
    `test_collected_reanalysis_is_applied_even_after_discovery_watermark_moves`), но билл всё
    равно проверен;
  - во время ожидания пришла новая стадия → один пост, стадии в порядке дерева;
  - дедлайн → синхронно; ошибка item → синхронно;
  - pilny, `/refresh`, dry run → синхронно;
  - dump/restore между прогонами → ответ применён один раз, стоимость учтена один раз;
  - workers 1 и 4 дают одно и то же.

## Фаза 5 — amendments (включение в production за гейтом 0.3)

- Путь тот же, что в фазе 4: `_summarize_amendments` принимает `Waiting`, новых механизмов нет.
- В `daily.yml` добавлять `amendments` только после гейта.
- Сценарии:
  - постановление Сената с поправками и отчёт «-A» ждут и уходят одним постом с блоком «Что
    меняют поправки»;
  - вид не включён → синхронно, как сегодня.

## Фаза 6 — документы (идут с каждой фазой; в конце сверка)

- `CLAUDE.md`:
  - инвариант о батче: вместо «triage, amendments, digests and joint comparisons stay
    synchronous» — новое правило (мемо-передача, отложенное наблюдение, `awaiting_batch_since`,
    дедлайн, кто не ждёт);
  - инвариант про joint: сравнение «delays but never stops» реплику;
  - список миграций: v32.
- `docs/llm-cost.md`: цифры вторичных вызовов — прод и корпус.
- `docs/process-plans.md`, `docs/database.html` (v32), `docs/operator-commands.md` (`/status`),
  `.env.example`, `docs/BUGS.md` (0.1).

## Инварианты, которые нельзя сломать

- Pending-before-send; запись и рассказ идут парой: пока ответа нет в мемо, ни одной строки нет.
- Первый взгляд только засевает базу. Сводки бывают только у отслеживаемых биллов с базой:
  `seen_supplements` и `stages_fingerprint` уже требуются.
- `_stage_key` не трогать. Миграции только дописываются. Дампы с legacy-строками батча грузятся.
- Параллелизм только вокруг сети: подача intents и запись метки — в вызывающем потоке.
- Сбой подачи — не вердикт: intents остаются queued (фикс B30), билл ждёт до дедлайна.

## Проверка

- Перед каждым коммитом: `uv run pytest -q && uv run mypy && uv run ruff check src tests && uv run
  ruff format src tests`.
- Настоящий дамп:
  1. `git show origin/state:lexinform.sql > /tmp/s.sql`;
  2. `LEXINFORM_DB_PATH=/tmp/t.db uv run lexinform db init`, затем `… db restore /tmp/s.sql`;
  3. `sqlite3 /tmp/t.db "PRAGMA user_version"` → 32; `lexinform show 2699`;
  4. если в дампе уже есть строки батча, `lexinform batch-intents` и `/status` их читают.
- `lexinform run --dry-run --since YYYY-MM-DD` на настоящих данных: сводки синхронные, в батч
  ничего не подаётся.
- По желанию — live-смоук как integration-тест (по умолчанию отключён): один joint-запрос через
  настоящий batch Anthropic, сбор и разбор. Стоит около цента.
- Замер для гейта 0.3:
  `sqlite3 /tmp/t.db "SELECT call_kind, (julianday(completed_at)-julianday(submitted_at))*24 FROM
  llm_batches WHERE completed_at IS NOT NULL ORDER BY 2"`.
- Выкатка по одному виду через `LEXINFORM_LLM_BATCH_KINDS` в `daily.yml`. Смотреть строку отчёта
  «ждут ответа батча», `/status` и `llm_calls` с `batched: true` по видам.
- Откат: убрать вид из `LEXINFORM_LLM_BATCH_KINDS`. Новые вызовы пойдут синхронно, открытые
  задания всё равно соберутся, а ждущие биллы на следующем прогоне получат синхронный вызов и
  снимут метку.

## Правила исполнения

- Коммит после каждой фазы, сообщения на английском, без `Co-Authored-By`. Не пушить: пуш `main`
  деплоит.
- Комментарии и докстринги — одна строка.

## Не делаем

- Правку поста на месте и отдельный ответ со сводкой: решено, пост ждёт.
- Батч триажа.
- Ожидание батча внутри прогона (опрос несколько минут в конце) — только если замер покажет p50 в
  минутах.
- Ссылку на документ поправок, которая пропадает, когда сводки нет (`_update_links` берёт URL из
  записи), — отдельная мелочь вне плана.

## Уточнения при реализации

- Гейт 0.3 ограничивает включение amendments в production; код и offline-тесты реализуются сейчас.
- Таймаут включает очередь и неопределённую подачу: сбой отправки не продлевает ожидание.
- Цифры корпуса и production выше — исходные оценки плана, не новый замер.
- Подзадачи и коммиты соответствуют фазам 0–5; документация обновляется вместе с кодом.

- Гейт amendments проверен на свежем `origin/state` 23.09: завершённых батчей **0**.
  p90 не вычисляется; включение amendments в workflow отложено. Реализация и тесты входят в фазу 5.
