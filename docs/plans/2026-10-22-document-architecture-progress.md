# Журнал реализации архитектуры документов

Открыт 23 октября 2026. Статус на открытие: подготовлены аудит и план; реализация кода
DA0–DA10 не начата. Дата 22 октября в именах файлов относится к исходному аудиту,
даты коммитов подготовки ниже — 23 октября, Europe/Warsaw.

[План и checklist](2026-10-22-document-architecture.md) ·
[Правила отметок и ведения журнала](2026-10-22-document-architecture.md#progress-tracking) ·
[Архитектурный аудит](../reviews/2026-10-22-document-architecture.md).

План содержит статусы задач; здесь сохраняются результаты и основания этих статусов.
Последующие записи добавляются по порядку. Коммиты можно проверить локально через
`git show --stat COMMIT`; наличие коммита само по себе не доказывает запуск тестов или deploy.

## Исходное состояние журнала

- DOC-01–DOC-04 выполнены как подготовка документации; соответствующие коммиты проверены.
- Пункты реализации DA0–DA10 остаются `[ ]`. Новые тесты, миграции, checkpoint, архив,
  модели истории и изменение runtime этими коммитами не добавлены.
- Решение о S3/другом внешнем хранилище отложено. Ближайшие выпуски совместимы с текущей SQLite.
- Объём production state и файлов, прогноз роста, latency checkpoint и восстановление
  новой архитектуры ещё не измерены. Числовой бюджет хранения не выбран.

## Проверенная подготовка

<a id="j001"></a>
### J001. DOC-01: аудит и план

Дата: 23 октября 2026. Статус: документы готовы локально.
Коммит: [`ec2d44a`](https://github.com/sobolevbel/lexinform/commit/ec2d44ab5d54dc5ddcc015db6a8e0a9a03001052).

Сохранены аудит и план реализации, ссылки в обзорах/roadmap и навигации портала.
Проверено по git: семь файлов документации и навигации; `src`, `tests`, workflow и SQL-схема
не менялись. Аудит различает прочитанные пути, локальное воспроизведение и предполагаемый
сценарий отказа; production-частота ошибок не установлена.

Сообщённые в исходном аудите 118 целевых тестов и coverage-воспроизведение — исторические
результаты исследования, а не выполнение DA0 нового плана. Проверки оформления — в
[сводке исходных проверок](#initial-checks).

<a id="j002"></a>
### J002. DOC-02: хранение оригиналов отложено

Дата: 23 октября 2026. Статус: уточнение плана готово локально.
Коммит: [`ae8455d`](https://github.com/sobolevbel/lexinform/commit/ae8455d773ddf7e6a1890a782e5d756dd39878d8).

Постоянный raw-архив перестал быть обязательным условием программы. В DA2 добавлено сравнение
профилей и сроков хранения с явными гарантиями восстановления; последующие этапы отвязаны
от обязательного сохранения всех оригиналов. Это изменение только плана, не реализация DA2.
Фактическое измерение объёма и выбор backend не выполнялись.

<a id="j003"></a>
### J003. DOC-03: компактность дампа

Дата: 23 октября 2026. Статус: уточнение плана готово локально.
Коммит: [`8943e32`](https://github.com/sobolevbel/lexinform/commit/8943e32cc905d4ec0b6b05315dadb2d38709d0c6).

Добавлены критерии минимального роста SQL-дампа: одна запись неизменяемого содержимого,
ссылки вместо повторных полных snapshot, измерения SQLite/SQL/compressed dump/state history,
пикового dual write и времени восстановления. Pending/unknown и обязательные доказательства
защищены. Дамп этим коммитом не уменьшался; числовые результаты не получены.

<a id="j004"></a>
### J004. DOC-04: самостоятельно deployable выпуски

Дата: 23 октября 2026. Статус: уточнение плана готово локально.
Коммит: [`6b689f8`](https://github.com/sobolevbel/lexinform/commit/6b689f824d307c3d518a3d5a80794f78aa7af000).

Этапы DA отделены от релизов/коммитов. Установлены совместимые расширения, legacy readers,
перенос одного законченного потребителя, отдельное включение и поздний cleanup.
Сохранённая работа обслуживается по своему frozen формату; bot/relay/poller не обязаны
обновляться одновременно. Разбиты крупные пакеты DA1 и уточнён рабочий режим без нового
хранилища. Сам deploy и проверка совместимости будущих миграций не выполнялись.

<a id="initial-checks"></a>
## Исходные проверки и границы

Для J001–J004 в рабочей сессии зафиксированы успешные локальные offline pytest, strict mypy,
Ruff check/format, `git diff --check` и `make docs-check`. Это запись результатов той сессии;
git-история подтверждает состав изменений, но не является логом исполнения этих команд.
При открытии журнала 23 октября проверки выполнены заново с
`UV_CACHE_DIR=/private/tmp/lexinform-uv-cache`: `uv run --offline --frozen pytest -q`,
`mypy`, `ruff check src tests`, `ruff format src tests` — успешно; mypy проверил 297 файлов,
форматирование не изменило файлы. `make docs-check` и `git diff --check` также успешны;
локальный preview журнала проверен в браузере.

| Область | Подтверждённый результат или граница |
| --- | --- |
| Локальные коммиты | Проверены хеши, даты и документационный scope J001–J004 |
| Corpus replay | Полный корпус для этих документационных изменений не запускался |
| Схема/форматы/flags | Runtime и SQL не менялись; новые migration/форматы/flags не включены |
| Размер state | Не измерен; расширенный профиль хранения не выбран |
| Rollback runtime | Для этих docs-only коммитов неприменим; совместимость будущего кода не доказана |
| Push | В этой задаче для J001–J004 не выполнялся; текущее remote-состояние не проверялось |
| CI / deploy | Не проверены; локальный gate не засчитывается за них |
| Production | Новая архитектура не включалась; pipeline/backfill/Telegram/платные LLM здесь не запускались |

## Форма следующей записи

Форма задаёт поля будущей записи; она не является начатой или завершённой задачей.
Использовать стабильный ID `J005` и далее, чтобы checkbox плана мог ссылаться на запись.

| Поле | Что записать |
| --- | --- |
| Дата и задача | `DAx.y`, при необходимости ID выпуска/связанные пункты |
| Статус | В работе / реализовано локально / принято / включено / отложено / неприменимо |
| Фактический результат | Что изменилось; какой критерий выполнен, что осталось |
| Коммит | Хеш реализации; до создания — «не зафиксировано» |
| Проверки | Точные команды, outcome, SHA/путь manifest, coverage/exclusions; не запущенное указать |
| Совместимость | Schema, defaults, поддержанные старые/new форматы, in-flight work; либо неприменимо |
| State | До/после/пиковый прирост и latency, когда требуются; иначе не измерено с причиной |
| Выпуск | Push, CI, deploy и production-наблюдение отдельными результатами/ссылками |
| Откат и остаток | Поддерживаемый rollback commit, ограничения, следующий пункт |

## Реализация

<a id="j005"></a>
### J005. DA0: исходная приёмка

Дата: 23 октября 2026. Статус: DA0.1–DA0.4 приняты локально. Исходный commit: `49ee2d0`.
Implementation commit: `28262c7f5ac3d162228287f9365d703c61402883`.
DA0.5–DA0.7 выполняются отдельно и этой записью не закрываются.

Подготовлены production-профиль World и регрессия неточного coverage; offline corpus runner
выделен в следующий фрагмент. Runtime, схема и настройки в DA0 не меняются.

#### DA0.1: исходная версия и конфигурация

Код `49ee2d0ad140da9c7b5771e7d8c860e072c561cd`, SQLite schema **48**.
`Container.analysis_options` требует batch независимо от legacy batch flags.
23 октября read-only `gh variable list` подтвердил `reader_attention=on`,
`attention_roundup=daily`, batch kinds `analysis,reanalysis,joint,supplement,amendments,consultation_review,act_application`.
Переменные включения consultation/commission reviews отсутствуют; defaults обоих — `false`.
`name_cards=true` по Settings, отдельной repository variable нет. Это снимок конфигурации,
а не доказательство успешного production run. Secrets и `.env` не читались.

| Потребитель | Владелец подготовки / точный источник |
| --- | --- |
| Prefilter | `TextPrefilterService.load` → `TextSources.locate` → `TextLoader.select_document` |
| Основной анализ, reanalysis | `AnalysisService._prepare`, `_prepare_first`, `_load_text`; batch payload и memo |
| Comparison/change review | `AnalysisService._changes_settled`: повторное чтение baseline с проверкой hash; `law_diff` и sync change review |
| Senate amendments | `senate_amendments_document`, `_load_amendments_text`, `_amendments_context`; resolution + отдельная recommendation |
| Supplements | `digest_supplement` → `_load_text(trim=False)`; свой batch purpose |
| Joint | `compare_joint`: описания уже проанализированных инициатив, не повторное чтение raw |
| Consultation/commission/final-act | `DocumentReviewService` → `review_context`, `_read_review_document`, `request_document_review`; paused purposes не включались |
| `/explain` | `CommandService` → `AnalysisService.explain_bill` → `_prepare(purpose="explanation")` |
| `/amendments` | `AmendmentsService`: исторический bill/change/date, сохранённый edit target; общий amendments input |
| Авторы | `SejmAuthorsResolver.resolve`: текст сопроводительного письма + справочник депутатов |
| Редакционные имена | `AnalysisService.name_bill`: summary/key_changes из AnalysisRecord, свой batch |

`TextSources` выбирает Sejm/RCL/submission/metadata source; чтение файлов сосредоточено
в `TextLoader`. Таблица фиксирует владельцев, не заявляет единое хранение всех их входов.

#### DA0.2: внешние эффекты

| Эффект | Владелец и текущая граница |
| --- | --- |
| Новая/joint карточка | `PublishingService.publish_bill`, `_publish_joint`, `_send` |
| Обычный update и edit update | `PublicationPoster.status_update`, `_send_update`, `deliver`, `_dispatch` |
| Группы стадий | `flush_updates`, `_flush_update_batch`, `retry_update_group`; membership в DeliveryPlan |
| Заседания при off/shadow/on | `flush_sittings`, `_send_sittings`, `_retry_sitting_group`; legacy callback ещё live-render |
| Digest при off/shadow/on | `DigestService.publish`, `_send`, `_send_parts`; legacy callback ещё live-render |
| Continuations | `message_parts.send_continuations`; дочерний reply target от receipt родителя |
| Исправление amendments | `AmendmentsService` → `amendments_revision` через Poster, исходный event сохраняется |
| Refresh/multipart card edits | `CardRefreshService.refresh`, `_edit`, `_edit_parts`; одиночный edit ещё прямой |
| Retag/source inheritance | `PublishingService._retag`; прямой card edit |
| Linking/rollover rerender | `PublicationPoster.rerender_card`; прямой card edit |
| Общий исход send | `publications.send_publication(s)`: локальная SQL-транзакция, без remote boundary |
| Технические ответы/отчёты | `RunNotifier`, `OperatorReplier`, digest drafter: вне гарантии reader queue |
| Commands/inbox | `CommandService.handle_pending`: локальные started/executed/handled; relay/GitHubInboxWriter отдельно от reader queue |

#### DA0.3–DA0.4: проверяемые контракты

`World(profile="production")` требует batch, сохраняя default обычных тестов.
Новые сценарии проверяют workers 1/4, ожидание 48 часов без sync fallback, generation,
supplement baseline, frozen payload, crash/unknown и multipart; отдельный on/daily сценарий
проверяет восстановление и выпуск группы в 18:00 Warsaw. Восстановление использует сохранённый
SQL и новый Container; потеря runner до remote checkpoint этим не доказана.

`test_prepared_input_coverage.py` закрепляет 1164 → 559 и допустимое удаление consultation
report. Strict xfail отмечает неверный analysis span; исправление остаётся DA5.

#### Дополнительные исходные измерения

На **локальном cached** `origin/state` = `78d56f17cd880ca5f41cf747f272605431a5aff7`
без fetch/pipeline восстановлена копия. SQL SHA-256:
`a550053b3617167c55ea11b877b516fc06cc42f4362b6dcb6112aae3912d9090`.
SQL: 36 779 974 bytes; SQLite: 37 732 352; gzip SQL: 8 352 242.
Локальные restore/dump: 0.438/0.192 s. Это один прогон на этом компьютере, не прогноз GitHub
latency, дневного прироста или полного raw-хранения; DA2.1/DA1.9 не закрыты.
Результат: `/private/tmp/lexinform-da0-20261023/state-baseline.json`.

Полный offline pytest прошёл с одним ожидаемым strict xfail DA5; strict mypy — 302 файла,
Ruff check/format `src tests tools` успешны. Push, CI, deploy и production run не выполнялись.
Rollback DA0: `49ee2d0`; runtime/schema/in-flight форматы этим фрагментом не меняются.

<a id="j006"></a>
### J006. DA0.5–DA0.7: offline corpus gate

Дата: 23 октября 2026. Статус: DA0.7 принято локально; DA0.5–DA0.6 частично.
Implementation commit: `43078c418b08eb362baab17110066b254a7ba378`.

Добавлен `make corpus-check` с обязательными `CORPUS`/`CORPUS_OUTPUT` и профилями
extraction/selection/comparison/delivery. Семь CI cases включают оригинальный RCL Word,
два ранее проверенных metadata fixtures, смысловые мутации и три существующих сценария
operator recovery. Manifest закрепляет SHA, официальный ID и связи, coverage и ограничения,
группу инициативы, split, ожидание и основание. Код/lock hashes и dirty flag записываются
в результат. Machine labels не допускаются как независимый holdout; использованный holdout
помечается regression. Срез целиком regression, выводов о holdout accuracy нет.

Runner запрещает сеть при выполнении cases, не перезаписывает результат и не пишет во входы.
Отсутствующий corpus/input/profile даёт `not_run` и ненулевой exit, повреждённый SHA и неизвестное
поле ожидания — failure. Связанные official IDs/relations не разделяются между splits.
Проверены также symlink traversal, network blocking и сохранность входных bytes.
CI выполняет срез и сохраняет manifest результата отдельным artifact.

Полные external replays пока не перенесены в этот manifest. Именно поэтому DA0.5–DA0.6
остаются открытыми: срез не заменяет population replay и независимую разметку.
`tests/fixtures/corpus_check/README.md` фиксирует происхождение и команды.

Дополнительно существующий `tools/replay_document_selection.py` выполнен на внешнем корпусе:
4276 RCL snapshots, 11786 print metadata rows, 69 пакетов; ошибок чтения нет, все 330
проверенных date/amount/negation/scope/reference мутаций изменили fingerprint.
Это baseline текущих решений, не доказательство правильности каждого выбора.
Artifact: `/private/tmp/lexinform-da0-20261023/document-selection.json`, SHA-256
`9f8224375f7e68fda584392dae435202d67ec19574cc4f952754243fe43be54d`.

Схема/defaults/state не меняются; rollback runtime не требуется. CI только настроен,
remote CI/deploy не запускались; push и production effects отсутствуют.
Проверки: полный offline pytest (один strict xfail DA5), mypy (302 файла), Ruff check/format,
`make docs-check`, `git diff --check` и `make corpus-check` на CI-срезе успешны.

<a id="j007"></a>
### J007. DA1: CAS для существующих state writers

Дата: 23 октября 2026. Статус: DA1.7 принято локально; DA1.8/DA1.10 частично.
Implementation commit: `f837f68`. Исходная версия: `43078c4`.

`GitStateCheckpoint` сохраняет SQL и polling manifest с CAS от local restore/последнего
подтверждённого checkpoint. До записи проверяется remote baseline; push использует явный lease.
Чужой tip не перезаписывается даже при неизменном dump. После потерянного подтверждения push
успех допустим только при remote tip = exact prepared commit. Сбой оставляет локальный commit
для расследования; reset/rebase/redump не выполняется. Ошибки snapshot/Git типизированы
`StateCheckpointError`. `GitBatchCheckpoint` сохраняет прежний callable API через общий writer.

`db checkpoint` использован в финальном шаге daily workflow. Inbox deletion push требует
`steps.dump.outcome == 'success'`. Это защищает от публикации удаления команд без финального
сохранения state, но не заменяет started/outcome checkpoints каждой команды DA1.10.

Тесты на временных Git remotes: fresh restore, absent branch, foreign tip при изменённом и
неизменном dump, гонка check/push, lost acknowledgement, отказ push, dump failure, сохранность
локального commit и чужих staged файлов; CLI init/checkpoint/restore и workflow guard.
Полный offline pytest, strict mypy, Ruff успешны; `make test-local-http` — **21 passed**.
Production remote не изменялся; тестовые push выполнялись только во временные локальные remotes.

Схема **48**, polling manifest **v1**, JSON/defaults/in-flight batch formats без изменений.
Новые ключи, secrets, архивы и таблицы не нужны; SQL dump не растёт из-за нового payload.
Имеющийся workflow lock сохранён; процедура эксклюзивного manual maintenance ещё не реализована.
Каждая физическая читательская отправка пока не защищена remote checkpoint, а fatal propagation
через services ещё выполняется следующим фрагментом. DA1 целиком не завершён.

Rollback для форматов: `43078c4` читает тот же state; при чужом tip writer нужно остановить
и сверить действия, а не возвращать прежний reset/redump. Старый dump после внешних эффектов
автоматически не восстанавливается. Push/CI/deploy/live нового кода не выполнялись.

<a id="j008"></a>
### J008. DA1.5: остановка при потере durable state

Дата: 23 октября 2026. Статус: принято локально для существующих batch checkpoints;
DA1.5 в целом остаётся частично выполненным до переноса reader senders.
Implementation commit: `560a397`. Исходная версия: `f837f68`.

`DailyPipeline._phase` и `run` пробрасывают `StateCheckpointError`; `finally` сохраняет
диагностику и учёт вызовов, затем ошибка доходит до вызывающего кода. `CommandService`
пробрасывает её до записи executed/handled, ответа и удаления inbox. Классификация обычных
outage/ошибок остаётся прежней. Прямые submit/collect уже пробрасывали этот тип; лишние
изменения в secondary analysis/publishing/tracking не потребовались.

Оба CLI entrypoint используют `cli.main`, который возвращает **75** для checkpoint failure.
Workflow не запускает следующий commands process после этого кода ни в hourly tick,
ни в ручном запуске; обычный exit 3 продолжает прежний drain commands. Для нового теста
ручного запуска исправлено безопасное раскрытие пустого ARGS при `set -u` в macOS Bash 3.2.

Новые сценарии проверяют ноль provider calls до pre-submit checkpoint, остановку следующей
model group после receipt failure, отсутствие forget/reader sends/команд после collect failure,
started-only состояние `/analyze` и `/explain` и сохранение отчёта. Отдельные subprocess tests
проверяют exit 75 реальных `lexinform` и `python -m lexinform`; KeyboardInterrupt остаётся 130.
`make test-local-http` после смены entrypoint: **21 passed**.

Первый полный gate обнаружил проблему пустого Bash массива в новом workflow тесте; она
исправлена, целевые CLI/workflow/checkpoint tests и финальный полный offline pytest прошли
(один ожидаемый strict xfail DA5). Strict mypy — 304 файла, Ruff check/format успешны.
Schema 48, polling v1, цены/defaults и сохранённые payload formats не менялись;
увеличение SQL state этим фрагментом не вводится. Rollback по форматам: `f837f68`, но он
ещё не останавливает дальнейшие фазы при этой ошибке. Push/CI/deploy/live не выполнялись.

Уточнение карты J005: под `PublicationPoster` подразумевается фактический класс `Poster`
из `services/posting.py`; перечисленные методы принадлежат ему.

<a id="j009"></a>
### J009. DA0.5–DA0.6: полные external replay

Дата: 23 октября 2026. Статус: runner принят локально; полная corpus-приёмка не завершена.
Исходная версия: `560a397`. Implementation commit: `e2dcc83`.

`make corpus-check` получил явный `CORPUS_SUITE=external` и обязательный baseline/compare mode.
Baseline записывает machine observations как `recorded`, а не правовой вердикт. Compare требует
SHA complete baseline, тот же набор profiles и отвергает изменения входов или решений.
Путь корпуса/output обязателен, overwrite запрещён; сеть, запись во входы, `.env` и symlink escape
блокируются. Для прочитанных входов записываются initial/final SHA, официальный ID, связи из INDEX,
raw/cached coverage и regression split. Изменение файла во время replay — failure.

Объединены raw Word/nested-ZIP replay, cached RCL candidates, существующие transition comparisons,
controlled mutations и fixed fake-delivery slice. Каждая обязательная операция имеет непустую
содержательную population: print metadata не заменяет raw packages/candidates, comparison pairs
не заменяют mutations. Включены regression tests этих ложнозелёных состояний и приоритет failure
над отдельными cache gaps. Missing inputs остаются явным nonzero `not_run`, сеть их не восполняет.
Команды и ограничения: `tests/fixtures/corpus_check/README.md`.

Standalone DOC/PDF, полный ZIP inventory, независимые legal labels, отдельные Senate/process/
policy inventories пока исключены. Existing transition replay исключает kind `rpw`, сохраняя
`rpw-without-rule`; полное покрытие RPW не заявляется. Все external observations — machine baseline,
не independent holdout. Связи Sejm/RPW/RCL ещё не исчерпывающе нормализованы.

Первый полный observational запуск начат на этой копии корпуса до последних API/input-hash guards;
его fingerprints фиксируют именно запущенный код. На момент фиксации tool запуск ещё выполняется,
итогового JSON нет; приёмка этим запуском не заявляется. Уже обнаружены исторические cached-text
gaps. Ожидаемый output: `/private/tmp/lexinform-da0-external-20261023-first.json`; результат и hash
будут добавлены отдельной записью после завершения. DA0.5–DA0.6 остаются частичными.

Runtime extraction/selection/comparison не менялись; schema/state/defaults и размер SQL dump
этим tool не изменяются. Push/CI/deploy/live не выполнялись.
Полный offline gate: 2775 passed и один strict xfail DA5; mypy — 310 файлов,
Ruff check/format, CI manifest slice и strict docs-check успешны.

<a id="j010"></a>
### J010. DA1.1: общий checkpoint port и production wiring

Дата: 23 октября 2026. Статус: DA1.1 принято локально. Исходная версия: `560a397`.
Implementation commit: `d90797a`.

`StateCheckpoint` — callable port; Container строит один shared writer и локальный noop.
Settings добавляет `state_checkpoint_required=false`; production workflow задаёт `true`.
Старые имена state file/branch сохранены, `.env.example` обновлён. Отсутствующие SQL path,
branch, Git parent или origin останавливают создание sender; CLI startup сохраняет fatal
exit 75. Согласованность remote tip проверяется самим writer при сохранении.
Финальный `db checkpoint` использует тот же port с явным путём из CLI.

Dry-run checkpoint — noop и не делает push; общий AnalysisService сохраняет проверку
конфигурации при построении, но `start_run(dry_run=True)` не вызывает remote checkpoint.
`--no-publish` запрещает reader sends, сохраняя обязательный pre-submit batch checkpoint:
этот режим по-прежнему анализирует документы. Локальный collector получает прежний `None`,
чтобы noop не позволил преждевременно забыть ответы провайдера без durable save.

Проверены local/configured/shared, required missing/invalid config, оба entrypoints,
dry-run ноль push/submit и no-publish batch checkpoint без reader sends. Полный offline pytest,
mypy (309 файлов), Ruff, localhost CLI (21 passed) успешны.
Новых persisted данных/форматов, SQL-миграций и изменения размера dump нет.
Rollback по формату: `560a397`; старый код игнорирует новый env flag и сохраняет более слабую
проверку конфигурации. Push/CI/deploy/live не выполнялись.

<a id="j011"></a>
### J011. DA1.2: frozen HTML новых и joint карточек

Дата: 23 октября 2026. Статус: reader и producer приняты локально для новых/joint карточек.
Исходная версия reader: `d90797a`. Reader commit: `c4fb961`.

Сначала выпущен reader, который использует уже сохранённый `rendered_text` для NEW_BILL/
JOINT_BILL во всех attention modes, включая `legacy` policy. Он не меняет producer off/shadow.
Шесть сценариев повторов по 3 modes × 2 kinds проверяют точный saved HTML и reply target.
FakePublisher теперь учитывает успешные prepared cards так же, как обычные отправки.
Reader становится поддерживаемым rollback для следующего включения producer; без этого
прежний off/shadow handler мог бы проигнорировать сохранённый HTML и снова его отрендерить.

Remote boundary reader sends, остальные эффекты и завершение DA1 этим не заявляются.
Schema/форматы/defaults не меняются; rollback reader по имеющимся production formats —
`d90797a`. Push/CI/deploy/live не выполнялись. Полный offline pytest с одним strict xfail DA5,
mypy (309 файлов) и Ruff успешны.

Следующий producer сохраняет HTML всех новых/joint карточек в off/shadow/on до отправки,
использует saved HTML при retry и готовит все physical parts до первого API call.
Attempted legacy rows без HTML остаются на прежнем callback; pending/unknown не переписываются.
19 новых сценариев проверяют parity, state/formatter/time drift, multipart и legacy fallback.
Изменения render-hook тестов следуют фактическому публичному `publish_prepared`.
Producer commit: `4e0a9d0`; поддерживаемый rollback — `c4fb961`, который уже читает
saved HTML всех modes. Сама схема v48 и JSON fields не меняются.

Для одной synthetic World card: HTML 1513 bytes; заполнение пустого поля добавляет 1545 bytes
к SQL literal в off/shadow. Режим on уже сохранял этот HTML; это не новый прирост для него.
Artifact: `/private/tmp/lexinform-da0-20261023/card-payload-growth.json`.
Это не оценка дневного production роста. Действующие retention/pending/unknown защиты сохранены;
нового архива raw файлов нет. Remote boundary перед reader API пока не добавлена.
Полный offline pytest с одним strict xfail DA5, mypy (310 файлов), Ruff check/format,
CI corpus slice и localhost CLI (21 passed) успешны.

<a id="j012"></a>
### J012. DA1.9/DA2.1: измерение копии state

Дата: 23 октября 2026. Статус: локальный benchmark принят; DA1.9/DA2.1 остаются частичными.
Implementation commit: `7988a97`.

`tools/benchmark_state_checkpoint.py` принимает существующий local SQL, новый JSON output и
1–20 iterations. Всегда создаёт собственный temporary bare remote/worktree, запрещает
сетевые Git protocols и глобальные Git config/hooks. Исходный dump не меняется, временные
SQL/Git данные удаляются. Пять тестов проверяют запреты overwrite/input writes, лимит итераций
и реальный roundtrip с точным числом changed/unchanged commits.

Копия cached state J005, input SHA `a550053b3617167c55ea11b877b516fc06cc42f4362b6dcb6112aae3912d9090`:
SQL 36 779 974 bytes, gzip 8 352 242; schema 48. Restore/dump 0.432/0.215 s.
Initial checkpoint 2.335 s, unchanged 0.369 s, шесть changed samples 1.653–2.013 s.
Три synthetic report rows увеличили SQL на 5178 bytes, gzip на 180 bytes.
Восемь вызовов дали семь commits; SQLite после них — 37 740 544 bytes.
Git objects до GC: 75 907 780 bytes в каждом repo; после GC local/remote:
8 413 888 / 8 414 370 bytes. GC занял 4.518 s.

Artifact: `/private/tmp/lexinform-checkpoint-benchmark-20261023.json`.
Результат содержит code/source hashes и dirty flag; это synthetic local filesystem transport.
Не измерены GitHub latency/repacking, production daily growth, raw-объёмы и storage budget.
Полный offline pytest с одним strict xfail DA5, mypy (310 файлов) и Ruff прошли.
Benchmark не разрешает batching физически начатых reader effects без checkpoint,
не выбирает S3 и не завершает DA2.1. Production/remote state не менялись.

<a id="j013"></a>
### J013. DA1.4–DA1.5: checkpoint contract общего send helper

Дата: 23 октября 2026. Статус: подготовительная API-стадия; production senders ещё не подключены.
Исходная версия: `e2dcc83`. Implementation commit: `4373141`.

`send_publication`/`send_publications` принимают optional `StateCheckpoint`. При подключённом
port уже сохранённый caller pending checkpoint'ится до API, receipt и связанные effects —
после local atomic. Definite failure, outage и unknown также checkpoint'ятся после записи исхода.
Checkpoint и API выполняются вне SQL atomic helper. Typed `StateCheckpointError` не превращается
в delivery failed и не расходует attempt; failure последнего checkpoint сохраняет локальный
исход и останавливает вызывающий код. Старый вызов без port сохраняет прежнее поведение.

16 сценариев проверяют single/group, exact frozen payload, pending/outcome dump/restore,
освобождение только успешных held effects, integer receipt compatibility, все delivery outcomes,
ноль API до pre-failure и сохранение pending→unknown после потери post-checkpoint.
Это проверка контракта helper; полная потеря runner с actual Git remote и подключение каждого
reader producer относятся к следующей стадии. DA1.4–DA1.5 остаются частичными.

Schema, persisted formats, цены/defaults, существующий порядок активных sends и SQL объём
не меняются. Rollback по форматам: `e2dcc83`. Push/CI/deploy/live не выполнялись.
Полный offline pytest прошёл с одним strict xfail DA5; mypy — 311 файлов,
Ruff check/format и strict docs-check успешны.

<a id="j014"></a>
### J014. DA0.5–DA0.6: результаты внешних replay

Дата: 23 октября 2026. Статус: наблюдения получены; complete baseline не принят.

Первый полный запуск J009 завершился `not_run`: 13 703 input paths, 1390 missing cached texts,
ноль error observations. Он записал 4276 RCL stage candidate decisions, 1028 transition pairs,
3067 Word files, 69 nested packages и metadata 11 786 Sejm prints. Fake delivery — три сценария
в одной aggregate observation. Он начат с HEAD `560a397` и dirty source fingerprints до последних
guards; этот результат не является проверкой финальных API/initial-final SHA guards.
Artifact: `/private/tmp/lexinform-da0-external-20261023-first.json`, 22 286 054 bytes,
SHA `c9d38599574157befb5cafaa7127ff8faade28dfbd25fc841a4fa66bf13898bf`.

Controlled mutation replay: 1698 document paths, 6594 probes. Ни одного пропуска в text digest;
547 совпадений старого law digest после мутации (528 year, 14 decimal, 5 standalone number),
553 совпадения experimental guarded signature (539 year, 14 decimal); union — 558 probes.
Это наблюдения отдельных representations, не доказательство пропущенной production публикации
и не независимая разметка правового смысла. Нужны разбор конкретных spans и acceptance DA6;
алгоритмы по этим aggregate counts не изменялись.

Финальная версия runner повторно запущена для extraction/selection/delivery: HEAD `e2dcc83`,
dirty fingerprints helper-стадии; 12 472 input paths, те же 1390 missing cached texts,
ноль изменившихся во время replay inputs и error observations. Содержательные populations:
69 packages, 3067 Word files, 4276 candidates, три fake delivery scenarios.
Artifact: `/private/tmp/lexinform-da0-external-current-20261023.json`, 21 504 690 bytes,
SHA `50f12e3eb2a096b3da88e7479d2ae42fd390ca58793f9ce8d6db1581d025f433`.
Exit 2 (`not_run`) ожидаем из-за отсутствующего кэша. Ни один из этих JSON не принимается
как complete SHA-pinned reference для compare. Comparison/mutations второй раз не выполнялись;
их алгоритмы не менялись. DA0.5–DA0.6 остаются частичными, остальные исключения J009 сохранены.
Корпус, production state и сеть не изменялись/не использовались.

<a id="j015"></a>
### J015. DA1.2/DA1.4–DA1.6: remote boundary новых карточек и их частей

Дата: 23 октября 2026. Статус: вертикаль новых/joint карточек принята локально.
Исходная версия: `4373141`. Implementation commit: `d5839f4`.

Container подключает shared checkpoint к PublishingService и Poster. Новая/joint карточка,
каждое её продолжение и повтор сохранённой части проходят pending checkpoint → API → outcome
checkpoint. Ready child сохраняет реальный reply target родителя до своего API call.
Восстановленные failed/queued continuations идут через тот же port в delivery drain/tracking;
операторский retry карточки сохраняет audit вместе с pending до повторного API.
Неподключённый локальный запуск использует прежний noop; dry-run не делает push.

Fatal persistence error пробрасывается из delivery drain с сохранением отчёта и через pipeline/
commands до CLI exit 75; после ошибки нет следующего reader effect. Best-effort joint comparison
также не проглатывает этот тип. Сегодня compare_joint сам не вызывает Git checkpoint; этот тест
фиксирует требуемый контракт ошибки, а не найденный реальный Git call внутри сравнения.

22 сценария production World с batches и временными bare Git remotes проверяют off/shadow/on,
NEW_BILL/JOINT_BILL, multipart, exact HTML/reply target до API, frozen children до root send,
потерю runner до API, принятую отправку с потерянным ответом, pre/post-save failures, foreign tip,
receipt родителя, продолжение после restore, delivery drain и audit operator retry.
Свежая World восстанавливается из нового Git clone удалённого SQL; исходная SQLite не копируется.
Checkpoint после receipt обязателен перед следующей physical send, группировка для скорости
не вводится. Localhost CLI — 21 passed.

Область гарантии — новые/joint send и их MESSAGE_PART. Updates, digest/sittings, refresh/retag/
rollover edits и остальные reader effects ещё не переведены. Attempted legacy rows без HTML
сохраняют прежний callback: pending identity теперь durable, но exact HTML задним числом
не утверждается. Commands started/executed/handled в целом ещё не checkpoint'ятся отдельно;
retry audit покрывается pre-send checkpoint только при фактической отправке этой вертикали.
Поэтому DA1.2/DA1.4–DA1.6/DA1.10 и весь DA1 не отмечены завершёнными.

Schema 48, polling manifest v1, payload fields/defaults и цены не меняются. Новых raw archives,
S3 или persisted fields нет; HTML уже добавлялся J011. Появляются два checkpoint на physical send,
что увеличивает число Git snapshots; local synthetic measurement — J012, GitHub latency и
долгосрочный production рост истории пока не измерены. Format reader `4373141` читает эти rows,
но не является безопасным active fallback: при откате сначала остановить reader effects,
сохранить remote pending/unknown и продолжать только с кодом, сохраняющим durable boundary.
Push/CI/deploy/live не выполнялись.
Полный gate: 2813 passed и один strict xfail DA5, mypy — 312 файлов, Ruff check/format,
strict docs-check и `git diff --check` успешны. Независимый read-only review call paths
не нашёл блокирующих дефектов и подтвердил отсутствие активации остальных send/edit путей.

<a id="j016"></a>
### J016. DA1.5: fatal persistence error во вложенных tracking handlers

Дата: 23 октября 2026. Статус: contract tests приняты локально. Исходная версия: `45345e8`.
Implementation commit: `7209319`.

Перед расширением reader boundary четыре best-effort catch получают явный проброс
`StateCheckpointError`: Sejm `_post_news`, pre-print `_apply`, RCL и Wykaz `_link_pending`.
Persistence failure не классифицируется как обычная ошибка одного дела; следующий bill,
reader effect и pipeline phase не продолжаются. Существующие source outages и обычные
per-bill ошибки сохраняют прежнюю изоляцию. Это самостоятельная подготовка контракта ошибки;
новые sender kinds самим этим фрагментом не активируются.

Schema, payload/defaults, цены и SQL объём не меняются. Rollback по форматам — `45345e8`,
но более слабый error contract непригоден для последующего расширенного sender wiring.
Шесть сценариев через публичные World/Poster/Linker проверяют остановку следующего дела,
reader effect, digest phase и `/refresh` command acknowledgement; обычный per-bill failure
по-прежнему изолируется. Это injected typed collaborator failures, не доказательство реального
Git call внутри каждого handler. Mutation check: без ровно четырёх guards падают все пять
typed-failure сценариев, ordinary control остаётся зелёным; source восстановлены до gate.
Artifact: `/private/tmp/lexinform-tracking-checkpoint-guard-mutation.txt`.
Общий offline gate после подготовки трёх независимых фрагментов: 2902 passed, один strict xfail
DA5; mypy — 316 файлов, Ruff check/format, localhost CLI (21 passed), CI manifest slice,
strict docs-check и `git diff --check` успешны. Parser/selection/normalisation не менялись,
полный внешний корпус повторно не запускался. Push/CI/deploy/live не выполнялись.

<a id="j017"></a>
### J017. DA1.2/DA1.4–DA1.6: shared updates и сводки заседаний

Дата: 23 октября 2026. Статус: shared-send вертикаль принята локально.
Исходный совместимый reader: `45345e8`; база выпуска: `7209319`.
Implementation commit: `a086df4`.

`retry_update_group` и `_retry_sitting_group` подключены к shared checkpoint. Все member rows
переходят в pending одной транзакцией до remote save; общий receipt/effects — одной транзакцией
после физического API и до следующей отправки. Saved text/membership immutable при retry и
переключении attention mode. Новые QUEUED/attempts=0 sitting groups теперь сохраняют group_text
и IDs также в off/shadow; существующий reader уже читает их независимо от mode.
Физический sitting roundup, включая отмену, сохраняет прежний Outgoing kind AGENDA и тот же HTML.

Attempted legacy sitting без сохранённого membership оставлен на прежнем callback. Одиночный
update и длинное одиночное sitting через `deliver` этим фрагментом не переводятся. Поэтому
DA1.2/DA1.4–DA1.6 остаются частичными. Новые гарантии относятся к перечисленным shared sends.
FakePublisher восстанавливает исторические SittingNews по узкому publication_by_id из World,
сохраняя domain bookkeeping и отказ любого участника до API; второй send/рендер не добавлен.

31 scenario test с production batches и временными Git remotes проверяет exact payload/IDs
до API, pre/post failure, runner loss, accepted/lost response, remote-only restore, operator retry
из второго member после смены mode, missing member и legacy fallback. Pipeline regressions
проверяют проброс fatal из tracking и отсутствие следующей фазы/редактирования. Ещё четыре
fake tests проверяют grouped transport и отказы второго участника. Общий gate J016 зелёный.

Фактический Poster из `45345e8` прочитал новые failed groups после fresh clone: 6/6
(updates/sittings × off/shadow/on), неизменные HTML/membership, один physical send и общий ID
при изменённых текущих Bill и clock +120 дней. Renderer/legacy callbacks были запрещены.
Artifact: `/private/tmp/lexinform-old-poster-compatibility-20261023-55194.json`,
SHA `f10f6fd5fdfee007e8bf21e0ad31d21b4146acccd538532634710d04c7672f3b`.
Это old module с текущими imported helpers/models/adapter, проверка формата, не old durability.
Безопасный active rollback не возвращает sender без checkpoint: сначала остановить эффекты
и сохранить remote pending/unknown.

Schema 48, polling v1 и persisted поля прежние; raw-архива и нового backend нет. Для synthetic
sitting из двух members новые group fields добавили 1718 bytes SQL literals суммарно в
off/shadow; on уже сохранял их. Это полевая оценка, не размер всего dump и не дневной forecast.
Artifact с source hashes: `/private/tmp/lexinform-architecture-payload-growth-20261023-groups-digest-final-values.json`,
SHA `3d35c19f4b092b3172bfbe39c239b81d667e1255407150bc31b4fe7f71174f7b`.
Каждая physical группа получает два checkpoints; история Git отдельно не измерялась сверх J012.
Push/CI/deploy/live не выполнялись.

<a id="j018"></a>
### J018. DA1.2/DA1.4–DA1.6: reader digest и одна попытка за запуск

Дата: 23 октября 2026. Статус: reader digest вертикаль принята локально.
База выпуска: `a086df4`; совместимый reader сохранённых частей: `45345e8`.
Implementation commit: `1eae096`.

Новые reader digests используют сохранённые physical parts и membership в off/shadow/on.
Все части и reservations создаются атомарно до первого pending/API; перед каждым reader
API выполняется remote checkpoint, после него receipt и completion ровно его source events
сохраняются вместе до следующей части. Container передаёт общий state writer; dry-run
сохраняет noop. Существующие saved parts читаются независимо от attention mode.
Attempted legacy digest без частей остаётся на старом callback с checkpoints статуса;
исходный HTML прежней попытки задним числом не восстанавливается. Technical draft сохраняет
прежний approval button, не резервирует source events и не получает reader checkpoints.

Полный gate выявил повтор физической попытки после definite failure: `/digest publish` и
финальная digest phase могли отправить одну часть дважды в одном pipeline execution.
Теперь DigestService хранит ephemeral attempted IDs в пределах запуска; commands и digest
phase разделяют этот scope. Следующий pipeline run, standalone command drain или direct
publish получает новый scope и может повторить подтверждённый отказ. UNKNOWN по-прежнему
не повторяется автоматически. Счётчики не ослаблены, исходный regression test сохранён.

48 новых scenario tests покрывают real formatter и synthetic multipart, checkpoints 1–6,
remote-only restore, потерю runner/response, audited retry, command fatal без acknowledgement,
dry-run/no-publish, draft и legacy fallback. На сбое последнего post-checkpoint локально
последняя часть и её source уже SENT, а в remote owner UNKNOWN/source QUEUED и зарезервирован:
recovery не присваивает receipt, не завершает source и не отправляет часть повторно.
Lifecycle matrix проверяет modes × definite/outage × одну/две команды, следующий запуск,
standalone drains и independent direct publish. Целевой gate: 103 passed.

Общий gate всех стадий J016–J018: 2902 passed + один прежний strict xfail DA5,
`/private/tmp/lexinform-architecture-delivery-continuation-final-20261023.xml`;
mypy — 316 файлов, Ruff check/format, localhost CLI — 21 passed, CI manifest slice,
strict docs-check и `git diff --check` успешны. Parser/selection/normalisation не менялись;
полный внешний corpus повторно не запускался.

Фактический DigestService из `45345e8` прочитал новый frozen payload после fresh clone
в off/shadow/on: 3/3, прежний HTML и source completion сохранены. Это old module с текущими
helpers, проверка формата, не прежней durability. Artifact:
`/private/tmp/lexinform-digest-rollback-20261023.json`,
SHA `238c31442dba15b8eff8d412c240d1931c6bca7db7fc3d8a3e282ab615fcea99`.
Активный rollback требует остановить reader effects и сохранить remote pending/unknown;
совместимость формата не разрешает возврат к sender без checkpoint.

Schema 48 и polling v1 прежние, raw/archive/backend не добавлены. Synthetic одностраничный
digest: payload SQL literal 1300 bytes, прирост к прежнему NULL в off/shadow — 1296 bytes;
on уже сохранял этот формат. Это только поле payload, без иных строк, membership links,
compression или истории Git; не весь dump и не production forecast. Artifact и SHA — J017.
DA1 остаётся частичным: одиночные updates, длинные одиночные sittings и card edits ещё
не переведены. Push/CI/deploy/live не выполнялись.

<a id="j019"></a>
### J019. DA1.2: reader коротких saved updates перед новым producer

Дата: 23 октября 2026. Статус: reader-first стадия принята локально. База: `1eae096`.
Implementation commit: `cc3b5c8`.

`Poster._send` выбирает prepared transport при любом saved `rendered_text`, а не только
при наличии continuation IDs. Короткий legacy update теперь сохраняет exact HTML и прежний
reply/edit target при изменении текущей карточки. Уже сохранённая дата edits остаётся в plan.
Generic `deliver` и attention-v1 callback уже умели читать такой payload; закрыт прямой
legacy `status_update`. Новый producer, расширение checkpoint scope и persisted поля не добавлены.
Рост dump от этой reader-only стадии отсутствует.

18 scenario cases проверяют off/shadow/on × short post/edit, exact HTML без повторного
formatter/render_delivery, saved reply 777 при текущей card 999, edit target/date, dump/restore,
повтор terminal вызова без второго send/edit, pending/unknown/dismissed. Отдельно после receipt
в одной транзакции завершены только saved held IDs и ACT_PUBLISHED с тем же message ID;
поздний held, добавленный после сохранения plan, не освобождён. Целевой gate: 67 passed,
`/private/tmp/lexinform-frozen-update-reader-focused-final-20261023.xml`.

Полный gate: 2920 passed + один прежний strict xfail DA5,
`/private/tmp/lexinform-architecture-frozen-update-reader-final-20261023.xml`;
mypy — 317 файлов, Ruff check/format, strict docs-check и `git diff --check` успешны.
Независимый reviewer не нашёл blockers и отдельно прогнал исходную 15-case матрицу.
Parser/selection/normalisation, CLI/wiring и persisted formats этой стадией не менялись;
нового полного corpus replay не было. Push/CI/deploy/live не выполнялись.

Дальнейшая producer-вертикаль требует отдельной приёмки: новые сообщения STATUS_UPDATE
и их MESSAGE_PART, pre/post checkpoints, одна попытка за запуск и last-child receipt вместе
с held/act effects. FAILED/attempts=0 после outage не является новой доставкой. При реализации
нужно сохранить domain bookkeeping `FakePublisher.updates`: canonical sent/edited уже видят
prepared delivery, а этот вспомогательный список пока её не отражает. Edit producer и обычные
правки карточки остаются отдельными задачами.

<a id="j020"></a>
### J020. DA1.2/DA1.4–DA1.6: одиночные status updates и их части

Дата: 23 октября 2026. Статус: вертикаль принята локально.
База выпуска: `8c0be61`, совместимый reader: `cc3b5c8`.
Implementation commit: `3987b83`.

Новые сообщения STATUS_UPDATE с исходным QUEUED/attempts=0 сохраняют exact HTML и все
MESSAGE_PART до первой попытки в off/shadow/on. Общая delivery boundary checkpoint'ит pending
до каждого physical API и receipt/effects после него. Root и дочерние части используют одну
границу; final child завершает только сохранённые held IDs и связанный ACT_PUBLISHED.
В этой вертикали edit_message_id остаётся вне нового checkpoint scope.

Direct status_update переиспользует publication после prepare, не сбрасывая FAILED в QUEUED
посредством upsert. FAILED/attempts=0 после outage и FAILED/attempts=1 без HTML остаются на
legacy callback, в том числе для длинного текста: исходный HTML прежней попытки не придумывается.
Статус такого retry сохраняется до/после API; exact HTML guarantee относится к frozen payload.
У остальных kinds прежняя implicit long-freeze семантика пока сохранена.

Physical update может быть начат один раз за execution. Общий attempted set сбрасывается перед
pipeline или standalone command drain, сохраняется между end-of-term, commands, publishing и
tracking. Несколько refresh в одной очереди его не очищают; standalone check_bill/publish_new
сохраняют отдельный scope по умолчанию. Следующий запуск может повторить definite failure,
а unknown требует операторского решения. FakePublisher отражает prepared single updates
в прежнем domain bookkeeping из saved JSON; edits/groups исключены, второго render/send нет.

67 новых scenario cases с production World проверяют temp Git remote/fresh clone, exact
short/multipart HTML, receipt до следующей части, root/final-child pre/post failures,
локальные и remote effects, runner loss, audited retry/confirm, unknown, outage и definite
failure. Матрица legacy FAILED0/1 × short/long × legacy/attention-v1 × off/shadow/on
защищает исходный payload; long delivery drain также проверен. Проверены excluded edits,
dry-run/no-publish, repeated direct calls, две refresh-команды и следующий execution.
Четыре rollover cases проходят end-of-term → commands (пустая очередь и /status) → publishing
→ tracking: одна physical попытка при definite/outage. Ещё восемь fake cases проверяют
domain parity, saved snapshots, отказ транспорта и отсутствие второго render/send.
Исходные recovery fault injections перенесены с legacy callback на prepared transport,
с прежними assertions unknown/audit/доставки; unit «later run» теперь явно начинает новый scope.

Фактический Poster из `8c0be61` прочитал новые frozen failed payload после fresh clone:
6/6 short/3-part × off/shadow/on. При изменённых текущих Bill/card/time сохранены HTML,
identity/порядок частей, receipt parents, included/later held и act effects; повтор не отправлен.
Artifact: `/private/tmp/lexinform-old-status-reader-qualification-20261023-81978.json`,
SHA `6a3553b4c3e5c210cd6e5b93cee0dcfc36aeeb7fd27ba62ade03b3006d68aa09`.
Это old module с текущими imported helpers/models/adapter, проверка формата, не old durability.
Безопасный rollback сохраняет remote pending/unknown и сначала останавливает reader effects.

В том же synthetic artifact измерены SQLite quote(delivery_json) literals: short off/shadow
получили +1414 bytes относительно прежнего producer; on уже сохранял этот HTML. Multipart
тоже уже сохранялся прежним producer во всех modes: incremental delta — 0. Сравнение с
unfrozen queued root отдельно отражено в JSON и не выдаётся за новый overhead этого выпуска.
Это payload literals без иных строк, gzip и истории Git, не весь dump и не production forecast.
Schema 48, polling v1, raw/archive/backend прежние.

Проверки: 67 новых remote scenario cases и восемь fake cases зелёные; existing focused suite —
200 passed. Полный gate — 2995 passed + один прежний strict xfail DA5,
`/private/tmp/lexinform-status-update-vertical-final-20261023.xml`; mypy — 319 файлов,
Ruff check/format, localhost CLI — 21 passed, strict docs-check и `git diff --check` успешны.
Независимый review не нашёл blockers после исправления nested command scope.
Parser/selection/normalisation не менялись; новый полный corpus replay не запускался.
Push/CI/deploy/live не выполнялись. Следующие вертикали: long single sitting, notices, edits.

<a id="j021"></a>
### J021. DA1.2: совместимый reader frozen sitting перед top-level producer

Дата: 23 октября 2026. Статус: reader-first стадия принята локально. База: `3987b83`.
Implementation commit: `26e6bac`.

Saved sitting с rendered_text идёт через prepared transport до любой проверки размера,
нового render, grouping и expiry. Это защищает пограничный multipart: после отделения physical
reply target от card link повторный render мог бы стать короче лимита и превратиться в другую
группу. Drop не удаляет уже frozen sitting. Legacy saved reply под карточку сохраняется;
reader также понимает top-level root с reply_to=None и receipt parents дочерних частей.
Операторское recovery разрешено для frozen AGENDA/AGENDA_CANCELLED с saved item_json,
даже когда root не является ответом. Новый producer и checkpoint scope ещё не включены.

FakePublisher записывает один логический SittingNews после successful prepared root,
не засчитывая MESSAGE_PART повторным заседанием. Ссылка карточки остаётся в canonical HTML;
при root reply_to=None доменный card_message_id тоже None, без чтения текущей карточки.
Новых полей, schema/backend/raw-архива нет, прирост persisted payload от reader отсутствует.

21 reader scenario case проверяет off/shadow/on, announcement/cancellation, short/near-limit
HTML с двумя children, exact parents, текущую card 999 и clock +120 дней, dump/restore,
запрещённые render/legacy callbacks, unknown, audited retry/confirm и сохранение legacy reply 42.
12 fake cases проверяют один логический root, detached snapshots, refusal/outage/unknown
без ложного success и canonical-only children. Полный gate: 3028 passed + один прежний
strict xfail DA5, `/private/tmp/lexinform-frozen-sitting-reader-full-20261023.xml`;
mypy — 321 файлов, Ruff check/format, strict docs-check и `git diff --check` успешны.
CLI/wiring, parser/selection/normalisation не менялись; новый полный corpus replay не запускался.
Push/CI/deploy/live не выполнялись. Следующий выпуск включает long sitting producer/checkpoints.

<a id="j022"></a>
### J022. DA1.2/DA1.4–DA1.6: длинное одиночное sitting

Дата: 23 октября 2026. Статус: вертикаль принята локально.
База выпуска и совместимый reader: `26e6bac`.
Implementation commit: `a967a9c`.

Новый QUEUED/attempts=0 AGENDA/AGENDA_CANCELLED длиннее лимита Telegram рендерится как
sitting roundup с исходной card link, затем сохраняется с physical root reply_to=None.
Все части готовы до первого pending/API, каждый child получает сохранённый receipt parent.
Общая boundary checkpoint'ит root/части до API и receipt после него. Оба публичных входа,
flush_sittings и deliver, используют этот формат; attention refresh/availability guards
выполняются до freeze. group_text/sitting_group не используются для multipart root.

Это также исправляет прежний layout: длинный root уходил ответом под карточкой, хотя сводки
заседаний должны быть top-level. Уже attempted saved HTML/targets остаются прежними;
legacy noHTML FAILED0/1 не получает придуманного frozen payload или новых parts и сохраняет
прежний refusal/error. Его полноценное восстановление не засчитывается в приёмку новой доставки.

40 production World cases с temp Git remotes проверяют modes3 × announcement/cancellation,
оба входа, exact HTML/card URL при текущей card 999, top-level root, receipt parents,
root/final pre/post failure, runner loss/unknown, fresh clone, mode change, audited retry/confirm,
legacy immutability. Вместе с reader compatibility: 61 passed.

Фактический Poster из `26e6bac` прочитал новые failed frozen roots после temp remote/fresh
clone: 6/6 off/shadow/on × announcement/cancellation. При Bill/card/time drift и запрещённых
render/legacy callbacks сохранены exact HTML/card link, top-level root, child receipt parents,
порядок частей и один logical sitting; повтор terminal доставки не отправлен. Artifact:
`/private/tmp/lexinform-old-long-sitting-reader-qualification-20261023-24221.json`,
SHA `24a22eabbe8f4ac25453829b914e3b61a0a3401d6296a26aa2860ae66ea7143b`.
Это old module с текущими imported helpers/models/adapter, только format compatibility;
active rollback не возвращается к sender без checkpoints.

В том же synthetic artifact root+children SQLite quote(delivery_json) сравнены с фактическим
старым producer `26e6bac`: +1 byte в off/shadow и −7 bytes в on для announcement/cancellation.
HTML layout совпал; разница относится к reply_to и необязательному today, не используемому
sitting renderer. Это payload literals без иных строк/gzip/Git overhead, не весь dump и не
production forecast. Schema 48/polling v1 и отсутствие raw/archive/backend сохранены.

Полный gate: 3068 passed + один прежний strict xfail DA5,
`/private/tmp/lexinform-long-sitting-activation-full-20261023.xml`; mypy — 322 файла,
Ruff check/format, strict docs-check и `git diff --check` успешны. CLI/wiring,
parser/selection/normalisation не менялись; новый полный corpus replay не запускался.
Push/CI/deploy/live не выполнялись. Следующая вертикаль — notices с сохранённой датой.

<a id="j023"></a>
### J023. DA1.2/DA1.4–DA1.6: notices и сохранённая дата напоминаний

Дата: 23 октября 2026. Статус: вертикаль принята локально.
База: `5199e89`; предыдущая архитектурная стадия: `a967a9c`.
Implementation commit: `60a4278`.

ACT_PUBLISHED, CONSULTATION_RESULTS, IN_FORCE, CONSULTATION_DEADLINE, DECISION_DEADLINE
и HEARING_DEADLINE проходят frozen root/parts и общий pre/post checkpoint во всех modes.
Существующая очередь, identities, parent receipts и atomic effects сохранены. Новые kinds,
поля, schema, polling format и backend не вводятся. Attempted legacy FAILED0/1 без HTML
сохраняет прежние snapshot/date/callback, без создания новых frozen parts.

Legacy first-send сохраняет plan.today для четырёх dated reminders. Attention-v1 сохраняет
свою прежнюю последовательность refresh → availability/absorption/expiry → freeze с текущим
днём. Переключение на frozen transport не меняет дату задержанного legacy сообщения и не
включает attention expiry в off/shadow. Уже frozen payload не пересобирается при retry.
Неполные date/phase/hearing отклоняются до API вместо отправки preview error читателю.
Prepared decision reminder сохраняет Outgoing.detail=phase.key, как legacy callback.
FakePublisher восстанавливает domain bookkeeping шести kinds только после успешного root,
из saved JSON/date/phase/hearing; edits/groups/children не становятся новыми notices.

71 scenario case проверяет modes × kinds, remote pending HTML/date/target до API, receipt,
synthetic three-part layout и root/final checkpoint failures, runner loss/unknown, audited
retry/confirm через fresh clone, исходный payload после clock +120 дней, legacy FAILED0/1,
transport outcomes/counters, incomplete inputs, dry-run/no-publish и прежние expiry guards.
27 fake cases проверяют HTML/Outgoing/detail/domain parity, detached snapshots и refusals.
Целевой набор: 98 passed; existing focused набор — 74 passed.

Фактический Poster из `5199e89` прочитал новые failed frozen payload после temp remote/fresh
clone в 18/18 случаях kinds × off/shadow/on. При изменённых Bill/card/time и запрещённых
render/legacy callbacks сохранены HTML/reply/plan, terminal retry ничего не отправил.
Это old module с текущими imported helpers/models/adapter, только format compatibility;
старый sender не является active rollback с новой durability. Artifact:
`/private/tmp/lexinform-notice-qualification-20261023.json`,
SHA `d8e6244b8e9924faa24418fe04c5dfad6d04ea1488d9725a94fd02732308a037`;
reproducer: `/private/tmp/test_lexinform_notice_qualification_20261023.py`.

В том же synthetic artifact измерен incremental SQLite quote(delivery_json) против фактического
старого producer: off/shadow +936…1432 bytes на notice, on — 0. Это один field literal,
без иных строк/gzip/Git overhead, не весь dump и не production forecast. Raw/archive/S3 нет.

Полный gate: 3172 passed + один прежний strict xfail DA5,
`/private/tmp/lexinform-notices-activation-full-20261023.xml`; mypy — 324 файла,
Ruff check/format, strict docs-check и `git diff --check` успешны. Runtime CLI/wiring не менялись.
Parser/selection/normalisation этот выпуск не меняет; полный corpus replay не повторялся.
Два соседних исправления в базе `5199e89` сохранены, их изменения не приписываются этой стадии.
Push/CI/deploy/live не выполнялись. Следующий выпуск — reader card revision и атомарный digest.

<a id="j024"></a>
### J024. DA1.3: reader card revision и атомарный desired digest

Дата: 23 октября 2026. База: `60a4278`.
Implementation commit: `8ea5084`.

MESSAGE_PART edit root получает logical owner из существующего revision_of либо legacy
card/ref. Owner должен быть SENT NEW_BILL того же bill/term/channel с тем же physical edit
target. Root/children такой операции используют общую pre/post checkpoint boundary.
Некорректный explicit owner блокирует API до pending, не оставляя ложный начатый intent.
Операторский confirm любого сохранённого edit обязан назвать именно edit_message_id.

Только новый explicit revision_of формат применяет desired digest из operation root
rendered_sha256 к логической карточке в одной транзакции с последним receipt/confirm.
Original card остаётся SENT, её identity/message/payload не меняются. Legacy card/ref reader
сохраняет прежнюю projection/report семантику: его digest обновляет прежний CardRefresher,
чтобы reader-first стадия не меняла cards_refreshed и не активировала новый producer.
Без saved desired digest, при failed/unknown/dismissed или незавершённых children projection
не меняется. Retention может удалить original payload, сохраняя unresolved operation/target.

32 seeded production World cases проверяют modes3 × single/multipart × explicit/legacy,
remote pending/HTML/targets/receipt parents, root/final pre/post failures, no-auto unknown,
audited retry/confirm/dismiss/mismatched target, retention→final confirm без API, missing digest,
пять invalid owner cases и наследуемый physical parent при отдельном logical owner.
Целевой набор с existing cards/commands/amendments: 133 passed.

Новый producer пока не включён; новых kinds, полей, schema, polling/backend/raw-архива нет.
Накладные persisted данные эта reader-first стадия не создаёт. Следующая стадия — single-card
producer, затем общий retag/rollover путь и отдельная приёмка multipart growth/projection.

Полный gate: 3204 passed + один прежний strict xfail DA5,
`/private/tmp/lexinform-card-revision-reader-full-20261023.xml`; mypy — 325 файлов,
Ruff check/format, strict docs-check и `git diff --check` успешны. Local HTTP CLI — 21 passed.
Parser/selection/normalisation не меняются; полный corpus replay не повторялся.
Соседние незакоммиченные изменения document reviews/retention в этот commit не включены.
Push/CI/deploy/live не выполнялись.

<a id="j025"></a>
### J025. DA1.2/DA1.4–DA1.6: сохранённые status-update edits

Дата: 23 октября 2026. База: `dcffe40`; предыдущая архитектурная стадия: `8ea5084`.
Статус: вертикаль принята локально. Implementation commit: `d86a377`.

Новые QUEUED0 STATUS_UPDATE edits сохраняют HTML/parts до первой попытки во всех modes.
Root редактирует исходный physical target, новые продолжения отвечают сохранённому receipt
предыдущей части; каждый API effect имеет общий pre/post checkpoint. Исходная SENT публикация
и status-change identity не переписываются. Сохранённый today относится к исходному сообщению;
multipart label «подготовлено» — ко дню первой подготовки. Retry не меняет ни одну дату.
Отсутствующая исходная дата отклоняется до pending/API. Legacy FAILED0/1 без HTML сохраняет
прежний callback/payload и не получает придуманного frozen входа. Attention=on продолжает
направлять новые late-vote enrichments в digest; reader поддерживает ранее сохранённые edits.

Новые сценарии создают late-vote intent обычным production World. Для multipart fault matrix
к нему добавлены synthetic stage lines: это приёмка транспорта, не измерение реальной длины
голосования. Проверяются remote pending/HTML/дата/target/части, root/final checkpoint failures,
fresh-clone retry/unknown при смене attention mode, invalid date и legacy callback compatibility.
Прежний тест исключения edits сужен до FAILED0/1; новая QUEUED0 ветка принята отдельно.
Целевой набор late votes/update checkpoints: 105 passed.

Фактический Poster из `dcffe40` прочитал новый frozen edit после temp remote/fresh clone
в 12/12 случаях: producer off/shadow × short/synthetic three-part × reader off/shadow/on.
Render/split/legacy callbacks запрещены; изменены текущие title/stages/card target и clock
+120 дней. Сохранены exact HTML, исходные target/date, parent chain и прежний SENT event;
terminal retry не имеет эффекта. Использованы текущие imported helpers/models/adapter:
это format compatibility, не полный исторический runtime и не прежняя durability.
Artifact: `/private/tmp/lexinform-update-edit-qualification-20261023/qualification.json`,
SHA `3aa0b54f833aa121d649c86c00c01d45f10c4e93356c9656a4a7f47c152af579`;
reproducer: `reproduce.py` в том же каталоге.

Incremental SQLite quote(delivery_json), root + все новые children: short +1108 bytes,
synthetic 75-stage/three-part +14611 bytes в off/shadow. Сравнение — перед/после freeze одного
сохранённого intent; это payload literals, без других строк/gzip/Git overhead, не production forecast.

Новых kinds/полей/schema/polling/backend/raw archive нет. Parser/selection/normalisation не
меняются; corpus replay для этой транспортной стадии не требуется. Соседний commit `dcffe40`
про очистку batch payloads сохранён и не приписывается этому выпуску. Следом — сохранённые
review/revision deliveries, command lifecycle и single-card producer.

Полный gate: 3229 passed + один прежний strict xfail DA5,
`/private/tmp/lexinform-update-edit-full-20261023.xml`; mypy — 326 файлов,
Ruff check/format, strict docs-check и `git diff --check` успешны. CLI/wiring не меняются.
Push/CI/deploy/live не выполнялись.

<a id="j026"></a>
### J026. DA1.2/DA1.4–DA1.6: checkpoints сохранённых review/revision

Дата: 23–24 октября 2026. База: `d86a377`. Статус: принято локально.
Implementation commit: `cdbd695`.

CONSULTATION_REVIEW, COMMISSION_REVIEW и AMENDMENTS_REVISION с сохранённым HTML,
включая их MESSAGE_PART children, получают общую pre/post checkpoint boundary.
DocumentReviews.advance пробрасывает StateCheckpointError через общий work handler.
Новый producer не включается, текущие flags и paused defaults не меняются.

64 новых production World cases покрывают modes × kinds, three-part remote pending/receipts,
root/final checkpoint failures с потерей local completion, frozen edit target и неизменность
исходного event, unknown/audited retry/confirm, final held effects, default pause и skip
ACT_APPLICATION/EFFECTIVE_RULES. Legacy FAILED0/1 без HTML сохраняет прежний отказ до API/CP
без переписывания payload; FAILED0/1 с HTML использует сохранённый reader. Целевой набор с
существующими review/amendment scenarios: 191 passed. Mutation отдельно подтверждает selector
и fatal guard; обычная ошибка review сохраняет прежнюю изоляцию.

Полный gate: 3293 passed + один прежний strict xfail DA5,
`/private/tmp/lexinform-review-checkpoints-full-20261023.xml`; mypy — 327 файлов,
Ruff check/format и `git diff --check` успешны. CLI/wiring не менялись. Strict docs-check
выполнен перед фиксацией. Schema 48/polling v1 прежние; новых persisted payload полей нет,
incremental payload growth — 0, dump/push overhead относится к общим checkpoint из J012.
Parser/selection/normalisation не меняются, полный corpus replay не повторялся.

Готовность push разрешена пользователем 24 октября; до следующего content fix push не
выполняется. Следующий выпуск исправляет наследование старого HTML при auto amendments.

<a id="j027"></a>
### J027. DA1.2: новый payload автоматической amendments revision

Дата: 24 октября 2026. База: `cdbd695`. Статус: принято локально.
Implementation commit: `9df9849`.

Auto amendments строит свежий DeliveryPlan из historical bill/change/participation/attention/
policy/date/reply/edit/revision_of/with_act. Старый rendered_text и physical membership/effects
не наследуются. Original SENT publication не меняется, существующая correction не пересоздаётся.

12 новых production World cases проверяют off/shadow/on, short/multipart corrected text,
неизменность historical bill/date/target и original SENT payload, отделение held effects,
QUEUED/FAILED0/FAILED1/PENDING/UNKNOWN immutability и unknown→audited retry с exact новым HTML.
Повтор work не создаёт новый batch. Целевой набор recovery/manual/review/unknown: 179 passed.
Mutation старого producer даёт семь ожидаемых corrected-text failures; пять immutability controls
проходят. Исправление восстановлено, 12/12 новых cases повторно прошли.

Полный gate: 3305 passed + один прежний strict xfail DA5,
`/private/tmp/lexinform-auto-amendments-full-20261024.xml`; mypy — 328 файлов,
Ruff check/format, strict docs-check и `git diff --check` успешны. CLI/wiring не меняются.
Schema 48/polling v1 прежние; новый план использует существующие поля. Дополнительная raw/
analysis копия не создаётся; исправленный HTML заменяет ошибочно унаследованный HTML в том же
revision payload. Размер зависит от исправленного содержания и количества его частей.
Parser/selection/normalisation не меняются; полный corpus replay не повторялся.

Push задержан независимой pre-push проверкой: подтверждено неверное abandoned для успешного
review после повторной submission в соседнем `dcffe40`. Следующая стадия устраняет его до push.

<a id="j028"></a>
### J028. Pre-push correction: успех review после повторной submission

Дата: 24 октября 2026. База: `9df9849`. Статус: принято локально.
Implementation commit: `c8feef1`.

Независимая проверка пакета выявила в `dcffe40` неверный exhaustion: batch_job.state=failed
означает и consumed successful item. После failed → success при submissions=2 проверка до
memo lookup превращала ReviewWork в abandoned, не применяя уже сохранённый успешный результат.
Подтверждено на fakes; production-инцидент этим не установлен. Reproducer artifact:
`/private/tmp/lexinform-prepush-review-20261024/review-success-after-retry.json`,
SHA `21cdc4176215cc4d290abb6535bca6cf4eddc3fd8cdbed9b465366f7fd194c15`.

Граница исправления: recoverable successful memo или retained same-context review не означает
exhaustion; обычный request_document_review переиспользует результат и применяет его.

Семь новых production World cases проверяют first failure → second success с accounted/
unaccounted и fresh restore, сохранение memo при retention/cleanup, применение retained
same-context review без memo и прежний abandon только двух действительно failed submissions.
Primary context refusal сохраняет rejection metadata после cleanup/restart; queued overflow
остаётся тем же intent, а его successful second result применяется из memo. Дополнительной
ошибки удаления context_exceeded не обнаружено, код её обработки не меняется.

Целевой набор: 148 passed. Mutation без guard: шесть ожидаемых success-recovery failures;
genuine both-failed control проходит. Final source и новый regression byte-identical проверенному
patch `1a732c71d2c7d43f71de879cc578a788ce6f0ea039d0113767bd8ac1181d8756`.
Пакетный pre-push audit других новых blockers не выявил; full gate выполняется отдельно.

Полный gate: 3312 passed + один прежний strict xfail DA5,
`/private/tmp/lexinform-review-retry-full-20261024.xml`; mypy — 329 файлов,
Ruff check/format, strict docs-check и `git diff --check` успешны. CLI/wiring не меняются.
Schema/форматы/retention/backend прежние; новых persisted данных и model calls нет. Сохранение
уже полученного результата предшествует проверке счётчика submissions. Corpus replay не нужен:
extraction/selection/normalisation не меняются. Defect [B118](../BUGS.md#b118) исправлен локально.
Пакет готов к разрешённому push; remote SHA/CI/deploy фиксируются отдельно после проверки.

<a id="j029"></a>
### J029. DA1.10: долговечный lifecycle commands/inbox

Дата: 24 октября 2026. База: `c8feef1`. Статус: принято локально.
Implementation commit: `4499492`.

Предыдущий пакет отправлен в main: `git push` и независимый `ls-remote` подтвердили
`c8feef121aaf3d592bab4ba59e8860eb833f4f2d`. Release evidence приведён ниже; live pipeline
вручную не запускался. Текущая стадия остаётся локальной до отдельного push.

Release-проверка завершена: [CI 40042313295](https://github.com/sobolevbel/lexinform/actions/runs/40042313295)
и [Deploy relay 40042607759](https://github.com/sobolevbel/lexinform/actions/runs/40042607759)
успешны с точным headSha `c8feef121aaf3d592bab4ba59e8860eb833f4f2d`. Deploy log подтверждает
`5199e8983195 -> c8feef121aaf` и `lexinform-listen up 45 s after restart`, без mismatch warning.
Это workflow/log evidence, не прямая проверка VPS/Telegram или production pipeline. Artifact:
`/private/tmp/lexinform-release-c8feef1-20261024/evidence.json`,
SHA `649ecbc46f661bfb96e2f55019901be73a5156588692c62344325df2019773cc`.

CommandService получает общий StateCheckpoint: started до исполнения, каждый unhandled outcome
до технического ответа, handled до inbox.done. Повтор уже выполненной команды сохраняет
состояние перед ответом, не запускает исполнение заново. Dry suppresses новые checkpoints/ack;
обычный dry Container по-прежнему отвечает за noop всех вложенных сервисов.

20 новых cases используют production World, реальные FileInbox files, temp bare remote и fresh
clone. Проверяются три failure boundaries, no next command, uncertain started без повторного
исполнения, потеря handled receipt с допустимым повтором только ответа, same-service outcome
revalidation, already-handled re-read, technical reply outage/generic/typed failure, dry override,
normal dry pipeline, no-publish status audit, resolution audit durability и read-only cost/runs CLI.
Прежний ordinal digest failure injection заменён на явные PENDING/SENT boundaries, с проверкой
числа physical API effects. 214 affected cases и два профильных workflow checks прошли в mirror.

Независимый DA1.8 audit: canonical active state writer только daily.yml; все workflow routes
имеют общий lexinform-daily lock, final db checkpoint пишет SQL+pending-batches manifest через
CAS. Relay пишет inbox, VPS poller читает manifest/dispatch, deploy state не пишет. 20 fake-shell
workflow tests прошли; активный обход не найден. Остаток DA1.8 — эксклюзивная maintenance
procedure для direct manual CLI, который сам GitHub lock не приобретает. Report:
`/private/tmp/lexinform-state-writer-audit-20261024/report.md`,
SHA `123a0616ebd1f5defff9dad6e067c0e45164563c5a73927de89e8ebfd40ccbbf`.

Полный gate: 3332 passed + один прежний strict xfail DA5,
`/private/tmp/lexinform-command-checkpoints-full-20261024.xml`; mypy — 330 файлов,
Ruff check/format, strict docs-check и `git diff --check` успешны. Local HTTP CLI — 21 passed.
После паузы XML целиком проверен (0 failures/errors), Ruff/format/mypy повторены; source не менялся.
Schema 48/polling v1 и inbox protocol прежние, новых persisted fields/backend/raw archive нет.
Свежая команда добавляет три checkpoint calls, replay без handled — два, handled re-read — один;
это calls существующего writer, не обещание фиксированного числа новых Git commits.
Technical reply допускает прежний повтор после outage/lost handled; reader execution не повторяется.
Extraction/selection/normalisation не меняются, corpus replay не требуется. DA1.10 принят локально;
push этой стадии, CI/deploy и отсутствие ручного live run отмечаются отдельно.

<a id="j030"></a>
### J030. DA1.3 reader-first: владельцы, барьеры и восстановление card edits

Дата: 24 октября 2026. База: `4499492`. Реализация: `4a2d62d`, запушено; CI/deploy успешны.

Общий open_card_edit_ids находит active MESSAGE_PART root/child для того же channel/physical
target, включая другой logical alias и SENT root с незавершённым child. Forget/republish не
удаляет owner такой операции; CLI показывает actionable IDs. Прежний original multipart guard
сохраняется. Старые CardRefresher/Poster.rerender/Publishing._retag проверяют тот же barrier до
source lookup/direct API, без нового producer, query port, полей или schema.

В том же deployable срезе FAILED frozen card-edit root/child получает audited retry/dismiss
при любом числе definite attempts: общий pure qualifier используется CommandService и SQLite,
проверяет owner/target/membership, исключает чужие kinds и group membership. Confirm остаётся
только для UNKNOWN. Explicit revision_of и legacy card/ref читаются одним pure owner parser.
Retry не переписывает payload, dismiss не применяет desired digest. CardRefresher сохраняет
остановку последней DISMISSED desired edition, включая SENT root с dismissed child, но разрешает
новый desired hash. Delivery drain не считает ошибкой уже SENT child из устаревшего snapshot.
Разделение барьеров и recovery оставило бы exhausted legacy edits без поддерживаемого выхода;
поэтому они выпускаются одним коммитом до активации нового producer.

Это защита unresolved work до активации нового writer. Format compatibility предыдущего reader
не означает полный active fallback старых senders: rollback останавливает новые эффекты,
сохраняет pending/unknown и использует совместимый saved-delivery recovery.

62 новых forget cases покрывают четыре active статуса, explicit/legacy roots, SENT-root child,
aliases, terminal/unrelated controls, command forget/republish и CLI safety IDs. 223 focused
regressions прошли; mutation без service guard даёт 45 ожидаемых safety failures и 17 controls.
После restoration guard+CLI suite — 115 passed, включая local HTTP.

85 новых barrier cases проверяют три direct entrypoints с active root/children/alias до lookup
и API, settled/unrelated controls и сохранность saved recovery. 337 focused regressions прошли;
mutation без entrypoint checks даёт 52 ожидаемых failures и 33 controls; restored 85/85 прошли.

68 новых recovery cases покрывают attempts 0/1/3, explicit/legacy root/child, command+repository
validation, чужие kinds/owners/targets/membership, сохранение payload/audit после remote restore
и прежний digest control. Mutation без recovery даёт 32 ожидаемых failures и 36 controls;
restored 68/68 прошли. Ещё 10 dismissed cases проверяют same-hash stop, SENT-root child,
new-hash recovery и malformed/unrelated controls; mutation даёт 2 failures и 8 controls.
Всего 225 новых cases; новых persisted данных, портов/schema/backend, producer flags или raw archive нет.
Новые намерения не создаются: используются существующие operation IDs и физические targets.

Общий gate: 3557 passed + прежний strict xfail, mypy 334 files, Ruff check/format 359 files,
diff check и strict docs build/link check прошли. Local HTTP CLI: 21 passed.
Extraction/selection/normalisation не меняются, corpus replay не требуется. DA1.3 остаётся
частичным: новый single/multipart producer и перевод остальных callers ещё впереди.

Предыдущая стадия `4499492534b76e161950fd4f30fd0bb8f818963c` запушена; CI
[40064273501](https://github.com/sobolevbel/lexinform/actions/runs/40064273501) и автоматический
Deploy relay [40064533089](https://github.com/sobolevbel/lexinform/actions/runs/40064533089)
успешны для того же SHA. Deploy log: `c8feef121aaf -> 4499492534b7`, relay up 45 s после restart.
Это проверка workflow/logs, без ручного live run, SSH и Telegram send.

<a id="j031"></a>
### J031. DA1.3: сохранённая одиночная правка карточки

Дата: 24 октября 2026. База: `4a2d62d`. Реализация: `635e7af`, запушено; CI/deploy успешны.

База `4a2d62dc860c8735ccb96c223989bb7c739fcf1d` независимо проверена: CI
[40065099743](https://github.com/sobolevbel/lexinform/actions/runs/40065099743) и автоматический
Deploy relay [40065341441](https://github.com/sobolevbel/lexinform/actions/runs/40065341441)
успешны на том же SHA; log `4499492534b7 -> 4a2d62dc860c`, relay up 45 s после restart,
CI-head mismatch отсутствует. Ручного live run, SSH или Telegram send не было.

Обычный single-to-single refresh создаёт отдельную immutable MESSAGE_PART operation с exact HTML,
компактным continuation snapshot, saved edit target, logical revision_of, today и desired hash.
Исходная карточка остаётся SENT; desired digest применяется атомарно только с receipt/confirm.
Generation ref включает предыдущую operation identity, поэтому A→B→A→B не переиспользует старую
доставку. Active и qualified dismissed barriers J030 сохраняются; новый desired hash допустим.
Legacy multipart и рост single-to-many пока сохраняют прежний путь и не считаются переведёнными.

Одна дата из Clock используется для digest и HTML даже при print lookup через полночь Warsaw.
Общий execution attempted set исключает повтор definite failure между command refresh и tracking
того же запуска; следующий запуск может повторить saved operation. Delivery retry считает
cards_refreshed по подтверждённому переходу owner digest. Pre/post checkpoint error остаётся fatal.
Полные Bill/analysis/reviews, raw файлы, новая schema и backend не добавляются.

29 новых scenarios покрывают off/shadow/on, exact remote intent/receipt, CP failures до/после
API, lost response→UNKNOWN, definite retry, audited confirm/dismiss, active barriers, A/B/A/B,
одну попытку command+tracking, Warsaw midnight и dismissed samehash/newhash после retention.
Интеграция сохранила qualified J030 guard вместо более широкого дублирующего DISMISSED check;
malformed owner/target/channel/noHTML controls продолжают разрешать настоящий refresh.
Один прежний vacatio assertion теперь проверяет actual edited HTML: saved Bill намеренно compact.
400 affected scenarios приняты в mirror с отдельным исправлением трёх новых retention fixtures;
источники runtime после broad gate менялись только эквивалентным Ruff flattening.
Root full gate проверяется отдельно. Actual reader `8ea5084` после fresh clone читает новый
payload во всех 3 modes (format/projection/no duplicate); это не квалификация всего старого runtime.
Integration patch SHA `4521c620a8a870ab495a9c8fdc109114430764c9f3bc2f47b0e1b7db6b777a83`,
qualification SHA `3be942b7e0391b9855b2a14669c60204fd8ccc1df820146615fb4999fac77dbc`.

Root gate: 3586 passed + прежний strict xfail; mypy 336 files, Ruff check/format 361 files,
local HTTP CLI 21 passed, strict docs build/link check и diff check успешны. После паузы полный
JUnit XML проверен (0 errors/failures), 20 changed/preserved files совпали с qualified manifest;
Ruff/format/mypy повторены. Извлечение, выбор и нормализация не меняются, corpus replay не нужен.

До активации выполнено bounded offline измерение frozen producer `ca08be6` на базе `dcffe40`:
50 подтверждённых single edits, 10 quiet runs, retention через Clock +91 день и равный no-edit
control. Whole dump: 33 746 → 324 707 bytes после 50 edits → 36 211 после retention; все 50
identities сохранены, terminal payloads очищены. Равный очищенный control — 20 564 bytes;
остаточная дельта 15 647 включает identities и другие final metadata/analysis, не цену одной строки.
Gzip: 6 924 → 14 164 → 7 717 bytes. Quiet runs не создают edits/revisions, но хранят run reports.
Git history сохраняет прежние snapshots: 162 reachable commits, 2 174 260 bytes loose remote;
GC на изолированной копии даёт 173 007 bytes, без удаления reachable history.
FAILED/UNKNOWN forks сохраняют exact plans после retention и fresh clone. Median local checkpoint
0.1125–0.1249 s — измерение локального Git, не прогноз GitHub latency или production роста.
Артефакт `/private/tmp/lexinform-single-card-storage-20261023/measurement.json`, SHA
`43a8e00f56cd276d2f6f0753ae587e12366ceb34efa7aa2ea274a1ed28a8a92a`.
Persisted layout single producer при интеграции не меняется; воспроизводимый repo tool следует
отдельной стадией. DA1.9 остаётся частичным; по этим данным checkpoints не объединяются.

<a id="j032"></a>
### J032. DA1.3: общий single-card editor и логический владелец наследуемой карточки

Дата: 24 октября 2026. База: `635e7af`. Реализация: `35a2dc6`, запушено; CI/deploy успешны.

CardRefresher, Poster.rerender_card и Publishing._retag используют один edit_single_card.
Наследование передаёт сохранённый alias нового bill как revision owner, сохраняя прежний
physical target. Read-only card_aliases на прежней таблице находит SENT logical owners;
никакой schema/migration не вводится. Active root/child другого alias блокирует конкурирующее
редактирование до print lookup и API. Общая qualified dismissed проверка сохраняет J030 controls.
Единые execution attempts и Clock day используются во всех трёх callers; lookup выполняется
после определения saved day. Multipart/growth пока сохраняют прежний путь.

32 новых cases покрывают двух дополнительных callers во всех modes, exact remote intent/receipt,
CP failure до/после API, UNKNOWN→audited recovery, alias ownership/active barrier, один attempt
между publishing/command и normal tracking, а также midnight during Poster print lookup.
Три существующих assertions проверяют actual edited HTML вместо восстановления из compact Bill.
Broad mirror gate: 435 passed и 3 новых fixture failures; corrected final replay: 48/48.
Fixture publishing сначала seeds process baseline, чтобы следующий tracking не создавал отдельно
ожидаемую semantic edition. Runtime этим исправлением не менялся; root full gate выполняется.
Integration patch SHA `a4a78ef74f98d0a09bb6d789c2863fa202d76c90c0a323f81deb774898581255`;
root Ruff изменил только формат нового test file. Actual reader `8ea5084` после fresh clone
прочитал 6/6 новых saved operations; это bounded format/projection qualification.
Synthetic quote(delivery_json) increment: 3721 bytes для Poster, 3586 для alias retag;
это не whole dump/production-growth estimate. Нового backend/raw archive/paid calls нет.

Root full gate: 3618 passed + прежний strict xfail, mypy 338 files, Ruff check/format 363 files,
local HTTP CLI 21 passed; strict docs build/link check и diff check успешны. Corpus replay не нужен:
извлечение, выбор, trimming и нормализация не меняются. DA1.3 остаётся частичным до multipart/growth.

Предыдущая стадия `635e7af38193d6e7d78cc1294cad64cfa802c5d0` запушена и независимо проверена:
CI [40102880393](https://github.com/sobolevbel/lexinform/actions/runs/40102880393), автоматический
Deploy relay [40103242049](https://github.com/sobolevbel/lexinform/actions/runs/40103242049) success
на том же SHA; log `4a2d62dc860c -> 635e7af38193`, relay up 45 s после restart, mismatch отсутствует.
Это только metadata/log verification, без ручного pipeline/SSH/Telegram send.

<a id="j033"></a>
### J033. DA1.3: единая сохранённая multipart edition карточки

Дата: 24 октября 2026. База: `35a2dc6`. Реализация: `6656cce`, запушено; CI/deploy успешны.

Общий edit_card заменяет direct fallback всех трёх callers для single-to-many, many-to-many,
роста и сокращения. В одной SQL transaction до первого API сохраняются immutable root/children,
весь exact HTML, компактные snapshots, desired hash, today, explicit logical owner, targets и
membership. Каждый physical intent/receipt проходит pre/post checkpoint вне SQL transaction.
Новые append parts используют receipt предыдущей части; сокращение помечает прежний хвост
superseded text. Исходная карточка остаётся SENT, её digest применяется только после всех receipts.

Физическая edition выбирается среди SENT aliases. После retention используются подтверждённые
receipts и сохранённые edit targets; audited absent appended tail не воссоздаётся сам.
Интеграция выявила дефект frozen draft: похожий ref и surviving hash позволяли подавить настоящую
карточку или использовать чужой target из malformed child. Новый выбор проверяет root owner,
bill/term/channel/receipt, полную доступную membership и child root/parent/index до API.
Повреждённая цепочка блокируется с warning. Unrelated single root не подавляет подтверждённую
исходную edition. Pruned SENT root + DISMISSED child сохраняет same-hash stop; новый hash
использует только проверенные физические targets. Это runtime validation, не schema/backfill.

Сохранены J030 early barriers, audited FAILED recovery и единые execution attempts/Clock day.
Один общий результат считает только действительно новые физические сообщения; receipt edit не
считается новым post. Rollover перед reset metrics сохраняет только свой новый delta/error slice.
Повторный stale SENT child guard из draft удалён: он уже принят в J030. Child service outage
сохраняет прежний partial_errors контракт; полного phase-abort для этой ветки стадия не обещает.

47 новых multipart cases frozen draft и 13 retention/validation cases добавлены к прежним
single/caller regressions. Первые 108 affected cases прошли в mirror, включая все 13 новых;
7 осмысленных malformed controls падали до добавления validation. После этого добавлен только
diagnostic warning и удалён дублирующий stale-child guard. Дополнительный mirror broad gate
прерван ради полного root gate; его частичный результат не считается приёмкой.
Integration patch SHA `81c5027489cab31c713a3add5b5ee268b0db6d6437ce7e3038b355576a4eaf8c`.
Schema 48/polling v1, backend и raw-storage policy прежние; полные analysis/reviews не копируются.

Первый root gate выявил 3 прежних assertions об одном direct edit после снятия reader barrier:
новый producer обязан обновлять все подтверждённые физические части известной edition, включая
superseded хвост. Два теста теперь проверяют точный список target IDs; runtime не менялся.
Corrected affected gate: 177 passed (active barriers + new multipart/retention + callers).
Полный root gate перезапущен; первый незавершённый XML не считается приёмкой.

Финальный root gate: 3678 passed + прежний strict xfail; mypy 340 files, Ruff check/format
365 files, local HTTP CLI 21 passed, strict docs build/link check и diff check успешны.
Production API sender search не находит direct card edit fallback в services. DA1.3 принят
локально; DA1 в целом остаётся частичным из-за legacy/noHTML и оставшихся recovery/acceptance задач.
Выбор, извлечение и нормализация документов не менялись; corpus replay для этого среза не нужен.

Actual Poster/publications/DeliveryService из reader-first `4a2d62d` прочитали 12/12 новых
mode/shape payload после fresh local Git clone: exact HTML/targets/replies, final logical digest,
отсутствие повторной API и чистый report. Qualification ограничена этими импортированными
модулями/форматом, не всем историческим runtime; SHA
`e23bd41571e846f32a354edbabed48563f9d5d76e4d0dac972c367652e78bba9`.
Synthetic quote(delivery_json) increments прежние: single-to-four +33548 bytes, three-to-three
+21, three-to-ten +77, shrink с сохранением хвоста +21. Whole-state/daily-growth выводов из них нет.

База `35a2dc61e367b2752d04640effdc4316c743a5e2` проверена независимо: CI
[40103492565](https://github.com/sobolevbel/lexinform/actions/runs/40103492565) и автоматический
Deploy relay [40103855001](https://github.com/sobolevbel/lexinform/actions/runs/40103855001) success
на том же SHA; log `635e7af38193 -> 35a2dc61e367`, relay up 45 s после restart, mismatch нет.
Это metadata/log verification без ручного pipeline/SSH/Telegram send.

При инвентаризации оставшихся recovery shapes отдельно воспроизведён прежний пробел:
initial NEW_BILL multipart continuation после трёх definite failures блокирует forget, но
`/delivery child retry` отвергается как не-UNKNOWN. Это не card-edit revision и не расширяется
в этой стадии; DA1.6/recovery остаётся открытым. Fake production World evidence:
`/private/tmp/lexinform-initial-card-child-recovery-gap-20261024.json`; live data не использовались.

<a id="j034"></a>
### J034. DA1.6 recovery: исчерпавшая попытки часть первоначальной карточки

Дата: 24 октября 2026. База: `6656cce`. Реализация: `d1edc19`, запушено; B119 fixed locally, CI/deploy успешны.

Общий pure failed_card_part_resolution_allowed расширяет прежний card-edit qualifier только
на frozen MESSAGE_PART исходного NEW_BILL/JOINT_BILL. Для audited retry/dismiss проверяются
SENT root с receipt/HTML, bill/term/channel, root membership, parent/index; edit/revision/group
shapes отвергаются. Возраст/число definite attempts не являются разрешением на автоматическую
повторную отправку: необходим сохранённый операторский audit. Confirm остаётся только UNKNOWN.
Root, exact child payload, другие delivery kinds и прежнее digest threshold правило не меняются.
Schema/polling/backend прежние. Service и SQLite используют тот же qualifier, SQL не добавляется.

55 новых scenarios: NEW_BILL/JOINT_BILL × off/shadow/on × attempts 0/1/3 × retry/dismiss,
audit до API через remote checkpoint/fresh clone, exact original parts после clock +120 дней,
неизменность root и 17 malformed identity/membership/parent/index/confirm controls.
Targeted 106/106 (38 новых + 68 прежних card-edit cases), затем финальные 55/55 прошли в mirror.
На прежнем qualifier шесть retry/attempts=3 cases падают без ошибок setup; source восстановлен
в finally. Root full gate проверяется отдельно. Patch SHA
`0f5934a547f71f784bf0c6c1536f75af94bb04e247e32b290e811e4fa891eee2`.

Root full gate: 3733 passed + прежний strict xfail; mypy 341 files, Ruff check/format 366 files,
local HTTP CLI 21 passed, strict docs build/link check и diff check успешны. После полного gate
уточнён только diagnostic message type-narrowing assert; его predicate и runtime не менялись,
новые 55 cases и static checks повторены перед commit. Извлечение/выбор/нормализация не меняются.

Предыдущая стадия `6656cce8193d71cc5d4b06dd5b3fd5ef1f063db9` проверена независимо: CI
[40104840853](https://github.com/sobolevbel/lexinform/actions/runs/40104840853) и автоматический
Deploy relay [40105197512](https://github.com/sobolevbel/lexinform/actions/runs/40105197512) success
на том же SHA. CI: 3678 passed + прежний xfail, mypy 340, corpus/docs/web green. Log
`35a2dc61e367 -> 6656cce8193`, relay up 45 s после restart; mismatch отсутствует.
Это metadata/log verification, без ручного pipeline/SSH/Telegram send.

<a id="j035"></a>
### J035. DA1.4/DA1.5: checkpoints старых sitting/status-edit callbacks

Дата: 24 октября 2026. База: `d1edc19`. Реализация: `512ef45`, запушено; CI/deploy успешны.

Два оставшихся legacy callback пути получают тот же pre/post checkpoint: attempted sittings
без frozen group/HTML и FAILED STATUS_UPDATE edit без HTML. Прежние IDs, payloads, targets,
attempt budgets и callbacks сохраняются; отсутствующие исторические HTML/membership не создаются.
До API remote получает PENDING; после receipt — SENT/FAILED/UNKNOWN. Failure до API не вызывает
publisher, после API останавливает дальнейшие эффекты; remote-only Pending становится UNKNOWN
и не повторяется автоматически. Это закрывает checkpoint bypass, не legacy exact-payload gap.

19 новых production World cases: off/shadow/on, FAILED0/1 status edit, обе CP boundaries,
fresh Git clone, сохранение plan/attempts и отсутствие новых API после UNKNOWN. Отдельный case
показывает прежний per-row recovery старого sitting без persisted membership: confirm одной строки
не разрешает остальные. Старые 120 affected cases прошли в mirror; 9 новых post-failure assertions
уточнены на delta, поскольку restore helper сохраняет общий FakePublisher с прежними receipts.
После исправления fixtures все 19/19 новых cases прошли; source runtime не менялся.
На прежних callbacks две pre-API fault regressions ожидаемо падают; runtime source восстановлен
в finally. Root full gate выполняется. Patch SHA
`58cf869df739734c3c2729ca9d6b3acbf1f7e1af2eea3088aea6001fdcb4fe67`.
Schema/polling/backend прежние; новых persisted fields/raw данных/paid calls нет.

Root full gate: 3752 passed + прежний strict xfail, mypy 342 files, Ruff check/format 367 files,
local HTTP CLI 21 passed, strict docs build/link check и diff check успешны.
Выбор, извлечение и нормализация не меняются. DA1.2 exact legacy HTML/membership и финальная
CLI collect/drain acceptance остаются отдельными задачами; весь DA1 не закрывается.

База `d1edc198c69067ef0358eafb2068e645d18286b7` проверена независимо: CI
[40105825118](https://github.com/sobolevbel/lexinform/actions/runs/40105825118), автоматический
Deploy relay [40106241518](https://github.com/sobolevbel/lexinform/actions/runs/40106241518) success
на том же SHA. CI: 3733 passed + прежний xfail, mypy 341, corpus/docs/web green. Log
`6656cce8193d -> d1edc198c690`, relay up 45 s после restart, mismatch отсутствует.
Только metadata/log verification, без ручного pipeline/SSH/Telegram send.

<a id="j036"></a>
### J036. DA1.9 и CLI acceptance: повторяемые offline measurements

Дата: 24 октября 2026. База: `512ef45`. Реализация: `dfa662f`, запушено; CI/deploy успешны.

Dev-only tools.measure_delivery_state использует текущий код и production World с fakes,
сохраняет code/source/fixture hashes, измеряет whole SQLite/dump/gzip, reachable Git history,
local checkpoint/push/clone/restore timings. Режим ограничен 1–50 edits и 0–20 quiet runs;
output должен быть новым. Сокеты и non-file Git transport заблокированы, environment очищен;
оригиналы, production dump и backend не используются. Временные DB/remotes/clones удаляются.
No-edit control имеет тот же initial SQL/final Clock; retained delta не объявляется ценой строки.
GC выполняется только на копии и не удаляет reachable history. FAILED/UNKNOWN forks сохраняют
exact plans/targets после 91 дней и fresh clone, без дополнительных model calls.

7 новых tests проверяют N=1/2, quiet identities, whole-state/history/retention, unresolved work,
environment restoration, bounds и refusal to overwrite. Ещё 4 subprocess cases используют
реальный cli.main/Typer с fake Container: collect-batches/drain-deliveries, reader pre/post CP,
exit 75, API delta 0/1 и remote QUEUED/PENDING. Console/module entry wrappers отдельно покрыты
прежними tests. Новые subprocess запрещают сокеты и используют явно выбранный source tree.
Финальный mirror focused gate 11/11 прошёл; root full gate выполняется.

Root CLI sample N=10/quiet=3: 47 local checkpoints/pushes; whole dump 33737 → 89956 → 95308 →
23731 bytes (baseline/edits/quiet/retention), gzip 6887 → 8589 → 8729 → 5224. Все 10 identities
сохранены, 10 terminal payloads очищены; quiet runs не создают revisions/edits. No-edit retained
delta +3176 bytes включает metadata/current analysis. Финальная reachable history: 35 commits,
332743 bytes loose files / 37369 после GC на копии. Local median CP 0.1271 s не прогнозирует
GitHub latency. Это новый bounded sample, не повтор прежнего N=50 и не production-growth forecast.
Артефакт `/private/tmp/lexinform-delivery-acceptance-j036-measurement-20261024.json`.
Integration patch SHA `2f213fcb50d28a1e4d01ccf91bf8787b44bd32547d915892b344f1b76d964daa`.
Runtime, schema/polling/storage policy прежние; полный DA1 не закрывается.

Root full gate: 3763 passed + прежний strict xfail, mypy 345 files, Ruff check/format 372 files,
strict docs build/link check и diff check успешны. Четыре новых CLI subprocess cases входят
в default gate; local HTTP gate предыдущей runtime стадии остаётся действительным, source
runtime в этом срезе не меняется. J036 measurement SHA
`681c47c81f5cd50bd1bdf1989f66318b765f0866678786658609a42f54551085`.
DA1.5 принят локально с прежними fault matrices и J035: обе известные legacy CP обходные ветки
закрыты, collect/drain exit-75 acceptance воспроизводится через CLI. DA1.9 остаётся частичным:
hosted latency и production daily-growth здесь не измерялись; granularity checkpoints не меняется.

База `512ef457ca5aff48095c8811bfd3eb3779e0dd6e` проверена независимо: CI
[40106873014](https://github.com/sobolevbel/lexinform/actions/runs/40106873014), автоматический
Deploy relay [40107287291](https://github.com/sobolevbel/lexinform/actions/runs/40107287291) success
на том же SHA. CI: 3752 passed + прежний xfail, mypy 342, corpus/docs/web green. Log
`d1edc198c690 -> 512ef457ca5a`, relay up 45 s после restart, mismatch отсутствует.
Только metadata/log verification, без ручного pipeline/SSH/Telegram send.

<a id="j037"></a>
### J037. DA1.8: поддерживаемый путь обслуживания state

Дата: 24 октября 2026. База: `dfa662f`. Реализация: `22320c5`, запушено; CI/deploy успешны.

[Инструкция](../operations/state-maintenance.md) закрепляет существующий общий daily job
для scheduled/commands/collect/drain/maintenance: restore выполняется после получения lock,
локальные db tools сами его не приобретают. Command outcome, итоговый checkpoint и удаление
inbox различаются; зелёный job не объявляется доказательством успеха каждой команды.
Сохранено известное ограничение pre-run reprefilter_limit при skipped Run/final save.
Для исключительного arbitrary SQL/disaster restore пока нет готового ownership protocol;
DA1.8 остаётся частичным. Нового writer, lease/backend, workflow dispatch или runtime mutation нет.

20 workflow unit tests passed; strict docs build/link check, diff check, sidebar/TOC и переход
к контракту dump/restore проверены в локальном браузере. Runtime не менялся; полный source gate
предыдущего коммита остаётся действительным. Локальный HTTP server остановлен после просмотра.

Предыдущая стадия `dfa662fe69056f608a455ab75c77d932b462ba7b` проверена независимо: CI
[40108352543](https://github.com/sobolevbel/lexinform/actions/runs/40108352543), автоматический
Deploy relay [40108750196](https://github.com/sobolevbel/lexinform/actions/runs/40108750196) success
на том же SHA. CI: 3763 passed + прежний xfail, mypy 345, corpus/docs/web green. Log
`512ef457ca5a -> dfa662fe6905`, relay up 45 s после restart, mismatch отсутствует.
Только metadata/log verification, без ручного pipeline/SSH/Telegram send.

<a id="j038"></a>
### J038. DA1.8: partial backfill сохраняется при неуспехе Run

Дата: 24 октября 2026. База: `22320c5`. Реализация: `af612e8`, запушено; CI/deploy успешны.

Pre-run reprefilter_limit перенесён в существующий Run script. Его неуспех оставляет Run
начатым, поэтому общий final checkpoint больше не skipped и сохраняет завершённые строки.
set -e останавливает последующие index/run/commands, включая exit 75; checkpoint и inbox guards
не ослаблены. Backfill делит 35-minute Run budget с остальными фазами, оставляя время final save.
Limit/text-skipped передаются через environment и argument array; свободный input не становится
shell code. Старый standalone step удалён, schema/state writer/backend/polling не меняются.

11 новых unit cases проверяют порядок, optional flag, пустой/нулевой limit, literal input,
partial progress и exit 1/75/124. Три scenario cases выполняют настоящий CLI db checkpoint
через финальный workflow script и свежий local Git clone: после simulated backfill failure
обработанный bill восстанавливается вместе с polling manifest, дальнейший reader process запрещён.
Targeted gate: 34 passed. На прежнем Run script три unit regressions падают; workflow восстановлен
в finally. Первый mutation probe не запустил pytest из-за cache permissions и не считается проверкой.
Отдельный raw SQL/disaster-restore ownership protocol остаётся открытым; DA1.8 не закрывается.

Root full gate: 3777 passed + прежний strict xfail, mypy 346 files, Ruff check/format 373 files,
local HTTP CLI 21 passed, strict docs build/link check и diff check успешны. Извлечение,
нормализация и выбор документов не меняются; полный внешний corpus replay не запускался.
Форматы/defaults прежние, migration не нужна; совместимый rollback — `22320c5` с возвратом
описанного ограничения backfill. Следующий шаг: проверить CI/automatic deploy этого SHA.

База `22320c536b628130d1cd7bd867a49d5371354582` проверена независимо: CI
[40109288269](https://github.com/sobolevbel/lexinform/actions/runs/40109288269), автоматический
Deploy relay [40109726656](https://github.com/sobolevbel/lexinform/actions/runs/40109726656) success
на том же SHA. CI: 3763 passed + прежний xfail, mypy 345, corpus/docs/web green. Log
`dfa662fe6905 -> 22320c536b62`, relay up 45 s после restart, mismatch отсутствует.
Только metadata/log verification, без ручного pipeline/SSH/Telegram send.

<a id="j039"></a>
### J039. DA2.1: воспроизводимая инвентаризация raw файлов корпуса

Дата: 24 октября 2026. База: `af612e8`. Реализация: `feb1266`, запушено; CI/deploy успешны.

Dev-only tools.measure_corpus_files читает пять явных каталогов: RCL, Sejm term-10 prints,
filed documents, self-amendments и RPW. Сохраняются path/hash/size каждого physical file,
общие и раздельные totals, exact-byte duplicates и cross-family aliases. ZIP не раскрывается.
Input symlinks, overwrite и output внутри корпуса отвергаются; missing roots явные,
пустая выборка не считается успешным измерением. Сеть, модели, production DB и архив не нужны.

| Family | Files | Bytes | Unique bytes внутри family |
| --- | ---: | ---: | ---: |
| RCL | 10411 | 5085328706 | 4602441005 |
| Sejm prints | 2630 | 3166113469 | 3166113469 |
| Sejm filings | 570 | 715940968 | 714127037 |
| Sejm self-amendments | 66 | 40124793 | 40124793 |
| RPW | 827 | 2577018000 | 2577018000 |

Всего 14504 files / 11584525936 bytes; global 12354 unique contents / 11098773686 bytes.
Точные дубли: 2150 files / 485752250 bytes (около 4.2%). Две cross-family duplicate groups
учитываются глобально один раз; сумма family unique totals не является global unique total.
Обе cross-family пары — `599-s`/`1768-s`, известные корпусу позиции правительства, сохранённые
также вместо отсутствующего print. Это ещё одна причина не считать inventory потоком bill inputs.
По suffix: PDF 7576, Word DOC/DOCM/DOCX 5111, ZIP 1803, XML 13, ODT 1; suffix не доказывает
валидность формата, роль или scan/text coverage. Cached text, research derivatives, ранний
архив и metadata Senate/ELI исключены. Полнота/свежесть и реальное production consumption
этим не устанавливаются; дневной прирост, raw compression, retention/backup budget не измерены.

Index SHA `76c3683bf35edbe914c5847dd4bbe9dfe63d0e0faa5047cc4a207cc0d5c1649a`,
tool SHA `fc8e85aa780f04ad96ed5d3ca1a677f9a691a39ed8f53590f9f4d8b38cef8626`.
Первый probe из temporary candidate и повтор из root tool дали одинаковый file manifest.
Артефакт `/private/tmp/lexinform-corpus-files-j039-final-20261024.json`, SHA
`c98c36a31422552665a27074ab7541e3fcba0d621690d6f836fff84f550c2f78` (3183524 bytes).
Manifest остаётся локальным измерением; в git и production dump он не добавляется.
Пять новых tests проверяют cross-family math, aliases, неизменность inputs, output guards,
file/directory symlinks и отсутствие raw bytes. Focused gate: 5 passed.
Runtime, schema и профиль хранения прежние. Выбор S3/backend отложен, DA2.1 остаётся частичным.
Миграция, production flags и persisted fields неприменимы; rollback — `af612e8`, локальный
JSON не является runtime state. Следующий шаг DA2.1: consumed-file coverage и дневной прирост,
а для DA5 нужна явная карта происхождения фрагментов до исправления анализа coverage.

Root full gate: 3782 passed + прежний strict xfail, mypy 347 files, Ruff check/format 375 files,
strict docs build/link check и diff check успешны. Local HTTP CLI предыдущей runtime стадии:
21 passed; CLI/wiring продукта здесь не меняются. Полный внешний extraction/selection replay
не запускался: tool только читает байты и не вызывает extractor/selection/runtime services.

База `af612e88339a3692dea21eac4187a534be16e25d` проверена независимо: CI
[40110347744](https://github.com/sobolevbel/lexinform/actions/runs/40110347744), автоматический
Deploy relay [40110751655](https://github.com/sobolevbel/lexinform/actions/runs/40110751655) success
на том же SHA. CI: 3777 passed + прежний xfail, mypy 346, corpus/docs/web green. Log
`22320c536b62 -> af612e88339a`, relay up 45 s после restart, mismatch отсутствует.
Только metadata/log verification, без ручного pipeline/SSH/Telegram send.

<a id="j040"></a>
### J040. DA1.2/DA1.6: оставшаяся legacy work в изолированном state snapshot

Дата: 24 октября 2026. Статус: read-only аудит выполнен; runtime implementation неприменима.
Code `feb126606ecc4a0fc62d9c8480073d3ec95268aa`; state
`eacaf4beb864becab25b5a7152aa7d5c1f6f57dc` (commit 24.10.2026 14:51:50 UTC).

SQL восстановлен в новую временную SQLite; после restore выполнялись только SELECT/model
validation. Pipeline, recovery, paid calls, Telegram, workflow dispatch и canonical state writes
не запускались. Snapshot описывает этот подтверждённый remote commit, не outcome текущего job.
Schema 48; dump 14516854 bytes, SHA
`822df39928c7116842aebf88b8bcd9f8e93566ced79b62c4b99bd5b2f2599f80`.

Publication states: 144 sent, 4 skipped, 5 queued; pending/unknown/failed отсутствуют.
Все пять active rows имеют attempts=0, saved plan/Bill snapshot и пока не имеют exact HTML.
ID 131/132 — consultation_review, 186/187 — consultation_results, destination digest_only;
ID 200 — direct status_update. Все пять DeliveryPlan/Bill читаются текущими моделями.
Нет active legacy multipart/group memberships или начатых попыток без payload в этом snapshot.
Это не доказательство отсутствия таких данных в старых backup либо следующих запусках.
101 sent и 4 skipped не имеют exact payload; терминальные rows не объявляются retry work,
а исторический exact HTML не восстанавливается из сегодняшнего formatter.

Воспроизводимый основной запрос на pinned dump:

```sql
SELECT status, COUNT(*) FROM publications GROUP BY status;
SELECT id, kind, status, attempts, delivery_json IS NOT NULL AS has_plan
FROM publications WHERE status IN ('queued', 'pending', 'failed', 'unknown') ORDER BY id;
```

Артефакт `/private/tmp/lexinform-da1-legacy-j040-20261024.json`, SHA
`eecb23fd919d94a131cdc32a750c42b03306aea48825ea0108a3ec7541648eae`;
script `/private/tmp/lexinform-da1-legacy-j040-audit.py`, SHA
`dc13eef0fa626a6d06f7fb00b8b6eb0df83648adecdd5f09ed7132eb1634304e`.
Model validation artifact SHA
`f3d64814ec4c5e2fbb466099a060ce925c7ea17df91bd3087a1b38756888b623`.
Новой таблицы, полей, defaults, миграции и runtime rollback нет; очередь сохранена как есть.
DA1.2/DA1.6 остаются частичными: следующий шаг — явный контракт чтения/попытки для старых
snapshot без exact HTML/membership, сохраняя защиту uncertain work. Один этот snapshot не даёт
основания для массового backfill; он не выполнялся.

Реализация J039 `feb126606ecc4a0fc62d9c8480073d3ec95268aa` проверена независимо: CI
[40111255110](https://github.com/sobolevbel/lexinform/actions/runs/40111255110), автоматический
Deploy relay [40111574670](https://github.com/sobolevbel/lexinform/actions/runs/40111574670) success
на том же SHA. CI: 3782 passed + прежний xfail, mypy 347, corpus/docs/web green. Log
`af612e88339a -> feb126606ecc`, relay up 45 s после restart, mismatch отсутствует.
Только metadata/log verification, без ручного pipeline/SSH/Telegram send.
Для J040 source gate остаётся действительным: изменена только документация по прочитанным
данным; strict docs build/link check и diff check успешны.

<a id="j041"></a>
### J041. DA1.2/DA1.4: exact payload для определённо failed legacy status edit

Дата: 24 октября 2026. База: `b03e418`. Статус: принят ограниченный срез; DA1 остаётся частичным.

Ограниченный single-message status edit получает HTML из saved Bill/change/today до API.
Новый узкий repository method допускает только FAILED legacy edit без физической membership,
participation override и прежнего HTML; source fields, targets, flags, attempts и status сохраняются.
Нельзя заменить UNKNOWN/PENDING, новый target или semantic context. Render origin явно отмечает
legacy snapshot: исторический exact HTML не реконструируется. Это совместимый optional JSON field,
schema 48/polling v1 и backend прежние. Формат prepared payload уже читается `b03e418`.

Multipart/group и другие attempted legacy kinds этим срезом не активируются. Прежние двенадцать
boundary tests расширяются с сохранения semantic plan на сохранение также первого exact HTML;
остальные семь legacy cases сохраняют прежний контракт. Новые guard/recovery cases приняты.

Финальный focused gate: 92 passed (45 новых repository guards, 28 новых production World cases,
19 прежних boundary/recovery cases). На прежнем `posting.py` три pre-API invariants падают без
ошибок setup; source восстановлен в finally. Исходные fixture ошибки FK/natural key/attempts
и вызов отсутствующего setter исправлены через публичные repository методы.

Дополнительная regression воспроизвела потерю unknown JSON fields при полной сериализации
только что добавленным writer. Запись теперь объединяет ровно HTML/origin с исходным raw JSON,
а CAS сравнивает исходную JSON-строку и attempts; неизвестные поля сохраняются. Первый full gate
прерван на этой новой проверке и не считается приёмкой. Следующий полный прогон выявил 10 старых
assertions на callback без HTML: 3845 passed, 10 failed, один прежний xfail, без errors.
Они расширены на pre-API remote HTML/target и сохранность исходной adoption publication,
не удалены. Оба соответствующих модуля: 89 passed; окончательный full gate зелёный.

Actual source `b03e418` прочитал prepared payload через fresh local Git clone и выполнил
6/6 сочетаний off/shadow/on × with_act. Один edit target 444, тот же HTML/attempts, без нового
post и повторного API; receipt сохранён удалённо, новый reader после old dump сохранил origin.
Это ограниченный frozen-edit путь на fakes/current dependencies, не проверка всего old pipeline.
Qualification `/private/tmp/lexinform-legacy-freeze-j041-compat-final-20261024/qualification.json`.
Source hashes в артефакте сверяются с root после финального исправления writer.
Synthetic whole-dump increments шести cases: +971/+1427/+971/+1427/+920/+1423 bytes;
это first HTML плюс origin, не production growth forecast. Optional null origin добавляет
21 byte при новой полной сериализации plan; существующие rows массово не переписываются.

SHA qualification: `3c02211b0ab2e3d42526b35e9eaf7698e0b7b3786e6200197a703187c66c58c7`.
На изолированной копии pinned J040 state все 94/94 сохранённых DeliveryPlan читаются новой
моделью; origin отсутствующих исторических HTML остаётся None, schema 48.
Artifact `/private/tmp/lexinform-legacy-freeze-j041-old-state-validation-20261024.json`, SHA
`c96d8c691abea26f6d66257467d2ff70dceb3b9d3cfa198d0cd99f6171f00b15`.
Выполнялись только SELECT/model validation; pipeline и production state не менялись.

Приёмка: 3855 passed + один прежний strict xfail DA5, errors/failures 0, mypy 350 файлов,
Ruff check/format 378 файлов, local HTTP 21 passed, strict docs build/link check и diff check green.
XML `/private/tmp/lexinform-legacy-freeze-j041-accepted-20261024.xml`.
DA1.2/DA1.4 не закрыты: остальные attempted legacy kinds, multipart/groups и связанные
исторические неопределённости не получили выдуманные HTML или membership.

<a id="j042"></a>
### J042. DA3.1: физическое извлечение до юридического выбора

Дата: 24 октября 2026. База: `1e3785114aa18c963245df002aba3aaf199f7551` (J041).
Статус: принят ограниченный механический перенос; DA3 остаётся частичным.

`DocumentTextExtractor.extract_parts` возвращает immutable `ExtractedParts`: SHA входных байтов,
прочитанные части с member chain/root-container/member/text SHA и physical rejections.
Роли остаются unknown; прежний `extract_document` назначает их после физического сбора и затем
выполняет прежний выбор. Публичный `extract` и потребители TextLoader сохраняют прежний интерфейс.
Новые 8 cases проверяют независимость от legal classifier/selector, сохранность отвергаемых
позже частей, вложенные ZIP, прежние unpacking limits, malformed bytes и отсутствие Word pagination.
Focused gate с прежними document/bundle/selection-contract tests: 133 passed.

DA3.1 остаётся частичным: допустимые расширения и существующие format-reader/physical-loss
heuristics не меняются. Не заявлены все неизвестные ZIP entries, parent-container SHA,
структурированные причины отказа, block coordinates, extractor/dependency versions и durable
ExtractionResult. Текущий root-container SHA не переименован в SHA непосредственного parent.
Модель используется в активном пути в памяти, но не добавлена в Bill/DeliveryPlan/SQLite.
Schema 48, persisted selection JSON, архив и storage backend не изменены; роста dump от среза нет.

Raw baseline `b03e418` + J041 (J041 не меняет extraction):
`/private/tmp/lexinform-extraction-j042-before-20261024.json`, SHA
`0535699cbeb8a64c2fc0442e1a5ce79c65fd1803fcce6e364829357010b80473`.
Обычный tracked runner `python -m tools.corpus_check --profiles extraction --suite external`
с явными baseline/compare, corpus/output/reference paths даёт 3067 Word/DOCM/XML и 69 сложных
ZIP, 5700 input paths. Ноль missing, error observations или изменившихся во время replay inputs.
Compare выполнен в отдельной временной копии, пока root проходил J041 gate: source fingerprints
трёх изменённых файлов зафиксированы до/после, затем файлы перенесены в root byte-for-byte.
Артефакт `/private/tmp/lexinform-extraction-j042-prototype-after-20261024.json`, SHA
`b43bcfab0d7e7e2b0264f0ac6561f9f83a03f0edd5713b4d3c328290fb88c0ad`, status passed:
ноль changed observations и changed inputs; 4185 Word и 330 ZIP date/amount/negation/scope/reference
probes меняют прежний law digest. Это machine regression baseline, не независимый legal oracle;
остальные PDF/DOC/ODT, family transitions и полный корпус raw не покрыты этим replay.
Wrapper с явным prototype PYTHONPATH/source hashes:
`/private/tmp/lexinform-extraction-j042-prototype-replay.py`.

Дополнительно на тех же 69 ZIP сравнены полные модели `ExtractedDocument`, включая все
`DocumentSelection`/parts/roles/rejections/losses, со старым actual extractor source: 69/69 equal.
Artifact `/private/tmp/lexinform-extraction-j042-selection-compare-20261024.json`, SHA
`ac4c1b8a2762d8da5c8c05ec3d37d04735e91a267c3df70efcc8af539cca95e3`;
script `/private/tmp/lexinform-extraction-j042-selection-compare.py`.
Сеть заблокирована, корпус только читается, production/paid calls не использовались.

Финальная приёмка root: 3863 passed + один прежний strict xfail DA5, errors/failures 0,
mypy 351 файл, Ruff check/format 379 файлов, local HTTP 21 passed, strict docs build/link check
и diff check green. XML `/private/tmp/lexinform-extraction-j042-full-20261024.xml`.
Source hashes после полного gate совпадают с corpus-qualified prototype.
J041 запушен; CI [40118209265](https://github.com/sobolevbel/lexinform/actions/runs/40118209265)
success на точном `1e3785114aa18c963245df002aba3aaf199f7551`.
Автоматический Deploy relay
[40118649493](https://github.com/sobolevbel/lexinform/actions/runs/40118649493) success на том же SHA:
`b03e41835534 -> 1e3785114aa1`, relay up 45 s после restart, без mismatch. Evidence
`/private/tmp/lexinform-release-j041-20261024.json`. Это CI/deploy metadata и logs;
ручной production pipeline, SSH и Telegram send не запускались.

<a id="j043"></a>
### J043. DA3.2/DA3.4/DA3.5: физические страницы PDF и совместимая строка

Дата: 24–25 октября 2026. База: `eabbc6b0679afd5f05dffa7c60460f9a3d13f92f` (J042).
Статус: принят ограниченный PDF-срез; DA3 остаётся частичным.

`PdfTextExtraction`/`PdfPageText` сохраняют raw pypdf page text до whitespace projection,
порядок всех страниц (включая пустые), input SHA, pypdf version, contract `pdf-pages-v1`,
view/projection versions и error types. Block ID `pdf-page:N:0` локален данному manifest:
это одна текстовая запись на физическую страницу, не layout/table reconstruction.
Canonical SHA и JSON round trip проверяются отдельно от прежних business fingerprints.
Ошибка страницы отличается от пустого text layer. При ошибке дерева сохраняется наблюдённый
prefix, total page count остаётся unknown; legacy consumer по-прежнему получает пустую строку.
`extract` читает результат через прежнюю whitespace projection; выбор, trimming, Fact `part:N`,
scan byte windows, persisted JSON, schema 48, defaults и storage backend не изменены.
Результат в памяти не обещает сохранённых raw bytes либо полного юридического прочтения.

Новый tracked replay: `tools/replay_pdf_pages.py`, pinned cases `tools/corpus/pdf-pages-v1.json`.
114 raw PDF / 330346602 bytes: Sejm scan/cover/text, длинный druk 948 (2025 страниц), RCL
по metadata kinds и RPW по page-count sample. Это machine metadata/library oracle, не legal truth.
Пять RPW indexed files отсутствуют либо превышают 32 MB; исключения записаны в manifest.
Первый генератор ошибочно запрашивал term-8 druk 2864 в term-10 INDEX и затем отсутствующий RPW;
baseline до исправления не создавался. Популяция теперь зафиксирована по доступным bounded raw
files, её SHA `8fc8680391deb0aff7730f01381dbda95c33f2e5d2900b4c6e104ff0d3ce5e3b`.
Generator `/private/tmp/lexinform-pdf-pages-j043-manifest.py`.

Baseline до изменения parser: 114 observations / 10670 страниц, errors 0, изменившихся inputs
и кода нет; 210 date/amount/negation/scope/reference probes меняют прежний law digest.
Artifact `/private/tmp/lexinform-pdf-pages-j043-before-20261024.json`, SHA
`c2798897a1d9e481031e7768896327c0264a3ce09b5d6ec7de2b78862e871e76`.
Adapter compare проверяет legacy text/полную selection SHA, physical positions/raw page hashes,
error types, immutable input SHA и manifest round trip. Network/input writes запрещены;
смена case population/reference/input/code не может тихо дать passed.
Отдельно повторяются прежние 3067 Word/XML и 69 ZIP по pinned J042 baseline.

Новые 23 tests покрывают blank edges, mixed/failed pages, late tree failure, real PDF с пустыми
страницами, manifest identity/serialization, reader guard и ошибки replay. Focused с прежними
PDF/document/bundle/selection/sections: 184 passed; mypy 354 файла и Ruff check/format green.
Прототипный lint с absolute paths не применил relative per-file rules и не считается gate;
финальный root lint успешен без ослабления правил. DA3 остаётся частичным: Word/ODT/DOC блоки,
связи blocks→PreparedInput, полный code/options manifest и durable retention ещё не реализованы.

Предыдущий J042 уже выпущен: CI
[40118861481](https://github.com/sobolevbel/lexinform/actions/runs/40118861481) и Deploy relay
[40119193691](https://github.com/sobolevbel/lexinform/actions/runs/40119193691) success на точном
`eabbc6b0679afd5f05dffa7c60460f9a3d13f92f`; log `1e3785114aa1 -> eabbc6b0679a`, relay up 45 s,
без mismatch. Evidence `/private/tmp/lexinform-release-j042-20261024.json` сохранён при выпуске;
это metadata/log verification, не ручной production run или Telegram delivery proof.

25 октября временные артефакты 24 октября отсутствовали; незавершённые прогоны не засчитаны.
Baseline восстановлен повторным исполнением committed J042 source из `git archive HEAD`,
не новым parser в режиме baseline. Сверены байты PDF/document extractor и models с `git show`;
proof `/private/tmp/lexinform-j043-baseline-source-20261025.json`. Export без Git metadata
честно записывает `code_sha=unknown`; его происхождение задают этот proof и source fingerprints.
Pinned case manifest, INDEX и raw input SHA остаются прежними. Приёмка ниже использует новые
артефакты 25 октября, а не исчезнувшие логи. Ручной production run и paid calls не запускались.

Повторный raw PDF baseline: 114 файлов / 10670 страниц, errors 0, changed inputs/code 0,
210 probes / missed 0. Artifact `/private/tmp/lexinform-pdf-pages-j043-before-20261025.json`,
SHA `9eac4d4325ac40bfea279c4588871d45ab57577cc893cc258f1caf8e5b6cc47b`.
Повторный Word/ZIP baseline: 3067 / 69, missing/error/unstable 0;
`/private/tmp/lexinform-extraction-j043-before-20261025.json`, SHA
`46dc8dd087083a484a62969041e71b8fd7e6a703f83db6faf3c3f935db59ce05`.
Первый full gate 25 октября: 3885 passed, один прежний strict xfail и один environmental
failure в token counter: отсутствовал удалённый вместе с `/private/tmp` словарь `o200k_base`.
Публичный SHA-проверяемый словарь tiktoken восстановлен без модельных API; полный gate
перезапущен. Код теста/adapter и правила проверки не ослаблялись.

Повторный Word/ZIP compare passed: 3067 Word/XML + 69 ZIP, ноль changed observations,
missing/error/unstable inputs, source fingerprints до/после равны. Все 4185 + 330 mutation
probes обнаружены. Artifact `/private/tmp/lexinform-extraction-j043-after-20261025.json`, SHA
`8ae8bb945f05ef77b460c0ffdedada2655d495e6efaba027f0a4fab60b224053`;
source proof в одноимённом `.sources.json`, wrapper
`/private/tmp/lexinform-extraction-compare-20261025.py`.
Отдельная сверка полных `ExtractedDocument` моделей тех же 69 ZIP: 69/69 equal,
errors/changed inputs/code 0. `/private/tmp/lexinform-zip-selection-j043-after-20261025.json`,
SHA `ab5c1f3d180c54f78e136fefa7adde1e49d9acbd719040e2a5b5d595aa3dbb5b`;
wrapper `/private/tmp/lexinform-zip-selection-20261025.py`.

PDF adapter compare passed: 114/114 legacy text/полные selection hashes и 10670 raw page
positions/hashes равны baseline; 114 manifests прошли byte-SHA/JSON round trip, errors 0,
изменившихся inputs/code нет, все 210 mutations обнаружены.
`/private/tmp/lexinform-pdf-pages-j043-after-20261025.json`, SHA
`bc38855bb1248ee537e1a7686475e22eca5ed1c2ce8260a0ad42e1b5aaa899b1`.

Финальная приёмка рабочего checkout 25 октября: 3886 passed + один прежний strict xfail DA5,
failures/errors 0, mypy 354 файла, Ruff check/format 383 файла, local HTTP 21 passed,
strict docs build/link check и diff check green. XML
`/private/tmp/lexinform-pdf-pages-j043-accepted-20261025.xml`.
Source fingerprints root после полного gate совпадают с corpus-qualified исходниками.
Сеть и запись в inputs были запрещены внутри replay; schema 48 и storage defaults прежние.

<a id="j044"></a>
### J044. DA3: страницы PDF связаны с физической частью файла

Дата: 25 октября 2026. База: `9e617937f6e558ef32b940ed323b4f62925abc69` (J043).
Статус: принят ограниченный физический PDF-срез; DA3 остаётся частичным.

`ExtractedParts.pdf_pages` связывает `PdfTextExtraction` с `part_index`: validator требует
существующую единственную часть и совпадение SHA её bytes с input SHA page manifest.
Member chain остаётся на этой части, включая nested ZIP. Одинаковые bytes в разных members
имеют отдельные физические ссылки, не сливаются по SHA. JSON round trip сохраняет связи.
Optional port `PdfPageTextExtractor` не требует новых методов от прежних `TextExtractor` fakes
или adapters; без него нет выдуманных page records.

`extract_parts` собирает PDF pages по умолчанию одним вызовом page reader; нормализованная
строка берётся из того же результата. `extract_document` явно отключает удержание page records:
обычный pipeline не накапливает raw page text всего ZIP рядом с прежними строками.
Это opt-out от удержания результата, не обход совместимой J043 projection у PDF adapter.
Прежний порядок распознавания PDF/OLE/ZIP/Word XML/preamble сохранён: embedded PDF header
в DOC/XML не превращает файл в PDF. Legal roles, selected bundle и прежние coverage losses
не исправляются скрыто. Replay теперь получает pages через `DocumentTextExtractor.extract_parts`,
а не обходным прямым вызовом PDF reader.

Новые 9 tests: root PDF, blank-edge coordinates, duplicate bytes в nested members, legacy path
без удержания pages, старый порт, OLE/XML с embedded PDF header, absent/different/duplicate
physical references. Focused 78 passed, mypy 355 файлов, Ruff check/format green.
Первый full prototype прогон остановлен: копия не содержала tracked workflow files/Git context,
которые требуют прежние tests. Эти failures не засчитаны как регрессия или green gate;
полная копия восстановлена с прежним HEAD и offline origin, после чего gate перезапущен.

DA3 остаётся частичным: это PDF page-level coordinates с coarse block `pdf-page:N:0`;
Word/ODT/DOC structure, full processing manifest, PreparedInput links и retention ещё впереди.
Нет сохранённых raw bytes, новых DB fields/migration, S3, backfill или paid calls.

Cumulative Word/ZIP compare с тем же committed J042 baseline passed: 3067 / 69,
ноль changed/missing/error/unstable inputs и source changes; все 4185 + 330 mutations обнаружены.
Artifact `/private/tmp/lexinform-extraction-j044-after-20261025.json`, SHA
`35eb28a8124760f96b12a8dab94d5bb567b04cfb97d10234b612e1297efe55b7`;
source proof `.sources.json`, wrapper J043. Полные модели 69 ZIP также равны baseline,
errors/changes/input/code 0: `/private/tmp/lexinform-zip-selection-j044-after-20261025.json`, SHA
`c217ea00928e6364d07cd02a4c8f8743a9d0c35b4b7df2e12612422dd8cb4b79`.

PDF replay через физические части passed: 114 observations/114 manifests, 10670 страниц,
ноль changed legacy text/полных selection hashes/law digest/raw page coordinates и hashes.
Все 210 mutations обнаружены; errors/changed inputs/code 0.
`/private/tmp/lexinform-pdf-pages-j044-after-20261025.json`, SHA
`fa17f6520b237b91b8f565aed3081e1a68e83a489381279b3161f55045095ed4`.
Полный prototype gate затем дал 3893 passed, один прежний strict xfail и два failures:
`lexinform` console entrypoint отсутствовал в PATH этой копии. Это не засчитано как приёмка;
окончательный полный gate выполняется после переноса в рабочий checkout через uv.

J043 выпущен: CI [40174425042](https://github.com/sobolevbel/lexinform/actions/runs/40174425042)
и Deploy relay [40174698024](https://github.com/sobolevbel/lexinform/actions/runs/40174698024)
success на точном `9e617937f6e558ef32b940ed323b4f62925abc69`; test job — 3886 passed,
32 deselected, один прежний strict xfail. Deploy log: `eabbc6b0679a -> 9e617937f6e5`,
relay up 45 s после restart, mismatch отсутствует. Evidence
`/private/tmp/lexinform-release-j043-20261025.json`. Это metadata/log verification;
ручной production pipeline, SSH и Telegram send не запускались.

В pinned PDF population 1585 страниц имеют пустой extracted text SHA; позиции всех сохранены.
Это не утверждение о визуально пустых страницах или OCR. Фактических page/tree read errors
в этих 114 corpus files нет; failed-page/late-tree-error контракты проверены synthetic tests.

Финальная приёмка рабочего checkout: 3895 passed + один прежний strict xfail DA5,
failures/errors 0, mypy 355 файлов, Ruff check/format 384 файла, local HTTP 21 passed,
strict docs build/link check и diff check green. XML
`/private/tmp/lexinform-pdf-parts-j044-root-20261025.xml`.
Все source fingerprints root после полного gate совпадают с corpus-qualified prototype.
Console entrypoint tests также проходят в рабочем uv environment; prototype PATH исправлен
отдельной проверкой 5 entrypoint tests, production code/test assertions не менялись.
Schema 48, persisted selection JSON, batch/memo semantics и storage backend прежние.

<a id="j045"></a>
### J045. DA5: исправление завышенного покрытия подготовленного текста

Дата: 25 октября 2026. База: `be49fd4f53a340230d853ba652ea2145f8f5de13` (J044).
Статус: принят локально; DA5.3 закрыт, DA5.1–DA5.2 частично.

Воспроизведён strict xfail DA0.4 с `--runxfail`: source 1164, prepared text 559,
analysis span ошибочно 0–1164. В source 512 символов проекта и удаляемый consultation report;
служебная пометка об удалении не является прочитанным фрагментом источника.

`trim_print` теперь строит in-memory `TextProjection`: source SHA/length, ordered emitted
segments с точными source offsets либо явным generated origin. Координаты задаются при
сохранении страницы/OSR-prefix, не обратным substring search. Slicing учитывает прежний strip;
соседние literal pages, включая пустые, объединяются в один range, чтобы не раздувать metadata.
Публичные text/dropped результаты должны остаться byte-for-byte прежними.

`selection_for_input` получает exact ranges только при полном совпадении упорядоченной сборки
всех parts или её детерминированной trim projection с фактическим input. Generated markers
не входят в source ranges. Неподтверждённая сборка, budget excerpts, дополнительные инструкции
и повторение одного фрагмента вместо двух частей дают 0–0 с loss `input coverage unknown`.
Прежние legal/admissibility checks и `normative_text_omitted` policy этим срезом не меняются;
совпадение substring больше не назначает analysis span до конца исходного part.
Полная цепочка budget/cost fitting и durable PreparedInput остаются DA5.1–DA5.2/DA5.4–DA5.6.

Strict xfail снят после прохождения точного assertion 0–512; тест остаётся обычной регрессией.
Новые проверки: generated marker, повтор текста/parts, table/note text, несколько excerpts,
Unicode positions, strip/slicing, copy validation, gaps/overruns и compact adjacent-page maps.
117 focused tests green; новый replay runner отдельно ловит changed bytes/reference SHA.
Mypy 358 файлов, Ruff check/format 388 файлов, без suppressions.

Tracked runner `tools/replay_input_coverage.py` берёт raw population из pinned extraction
catalog и `tools/corpus/pdf-pages-v1.json`: 3067 Word/XML, 69 ZIP, 114 PDF (3250 files).
Проверяются extracted/trimmed/budget input hashes, прежние selection decisions/limitations,
law digest и round trip новых source/generated segments. Сравнение coverage хранится отдельно
от core changes. Это machine regression oracle без новых official relations или paid calls.

Baseline исполняет committed source из `git archive be49fd4`; SHA файлов сверены с `git show`.
Proof `/private/tmp/lexinform-input-map-j045-base-source-20261025.json`.
Export честно имеет `code_sha=unknown`; source fingerprints и proof фиксируют происхождение.
Baseline `/private/tmp/lexinform-input-map-j045-before-20261025.json`, SHA
`d261569f4b4e68a154763248c45239ca203636a65bf71cf7a1774d32c639b0ad`,
3250 observations, errors/changed inputs/code 0. Network/input writes запрещены в replay.
Новый runner поймал несовпадение tuple/list в собственном JSON compare; исправлена сериализация
oracle fields, production text и test assertions не ослаблялись.

Окончательный compare passed: 3250 observations, core changes/errors/changed inputs/code 0,
3250 проверок round trip. У 842 файлов изменились coverage metadata (trim 569,
budget 1000 — 842, budget 24000 — 709), при прежних строках и selection decisions.
`/private/tmp/lexinform-input-map-j045-after2-20261025.json`, SHA
`78c0f672f35ee86d38a0e513c4e854383e3f4db8c49c1126aac46eecff1b28f3`.
Сумма text-stripped selection JSON для trim: 4789594 → 4789697 bytes (+103),
для budget 1000 +21173 bytes, budget 24000 +9830; не более четырёх analysis ranges на case.
Это измерение corpus metadata, не прогноз размера production dump. Schema 48 и payload retention прежние.

Финальный gate: 3921 passed, failures/errors/skips/xfails 0, mypy 358 файлов, Ruff 388,
local HTTP 21 passed, strict docs build/link check и diff check green.
XML `/private/tmp/lexinform-input-map-j045-final2-20261025.xml`, SHA
`281e2901ce5d45c4fbca6e9bd04092488bc54e4a2ce75967bc0070d233246a57`.
Фактический input budget без карты честно получает unknown, а не выдуманный exact range.
Изменение полноты/admissibility после cost fitting и исторические provider requests остаются открыты.

J044 выпущен: CI [40175128467](https://github.com/sobolevbel/lexinform/actions/runs/40175128467)
и Deploy relay [40175534380](https://github.com/sobolevbel/lexinform/actions/runs/40175534380)
success на точном `be49fd4f53a340230d853ba652ea2145f8f5de13`; test job 3895 passed,
32 deselected, один прежний strict xfail. Deploy log: `9e617937f6e5 -> be49fd4f53a3`,
relay up 45 s после restart, mismatch отсутствует. Evidence
`/private/tmp/lexinform-release-j044-20261025.json`; это metadata/log verification.

<a id="j046"></a>
### J046. DA3: XML-координаты абзацев, строк и ячеек Word

Дата: 25 октября 2026. База: `f2f2c374c701ff1104daac356436b9a38703d2cd` (J045). Статус: принят локально; DA3.2–DA3.5 остаются частичными из-за остальных форматов, note parts и durable manifests.

`DocxTextExtractor.extract_blocks` возвращает immutable `WordTextExtraction` (`word-blocks-v1`):
SHA входного ZIP и `word/document.xml`, Python version, legacy view и body state,
упорядоченные `WordTextBlock` с XML child-index path, kind и text SHA. Адреса фиксируются
внутри `_WordMl` до объединения строк/ячеек; content controls и вложенные таблицы сохраняют
свои реальные промежуточные XML positions. Сортировка paths задаёт XML order.

`legacy_blocks` перечисляет только верхние paragraph/row fragments. Cell paragraphs и nested
rows остаются адресуемыми, но не удваивают прежний текст. Pure `legacy_word_text` повторяет
прежний join и page-break cleanup; результат rich projection проверяется против того же
legacy чтения. Обычный `extract` не удерживает блоки. Word по-прежнему не имеет pagination.
Canonical manifest SHA и JSON round trip защищают привязку к input/XML и порядок.

View сохраняет прежние insertion/deletion/field/MC_Fallback правила. Сноски остаются inline
на месте вызова с прежними missing-note markers; адреса footnotes/endnotes parts ещё unknown.
`body_state=read` означает поддержанный root с body в legacy view, не полное прочтение документа.
Header/footer/drawing paragraph inventory, merged-cell layout и правовая полнота не заявлены.
Flat OPC, Word 2003, ODT и DOC получают новые coordinates отдельными срезами; их старое чтение
нельзя менять побочно. Здесь rich Word leaf ещё не связан с `ExtractedParts`/PreparedInput.
Нет новых SQL fields/migration, raw archive, storage decision, paid calls или backfill.

115 focused tests green: реальные paths paragraph/cell/row, nested/control structure, strict
OOXML, blank/page breaks, MC_Fallback, insertions/deletions/cached fields, notes, missing/unsafe
body, identity/JSON round trip, wrong/duplicate paths и replay reference/input guards.
Mypy 361 файлов, Ruff check/format 392 файла. Исходники подготовлены отдельно от рабочего J045;
до/после каждого replay фиксируются source fingerprints.

Tracked runner `tools/replay_word_blocks.py`: raw named DOCX/DOCM population из pinned extraction
catalog, 3054 files. Baseline — committed `be49fd4` leaf reader в изолированном export.
`/private/tmp/lexinform-word-j046-before-20261025.json`, SHA
`6c1ae787d8fc02983a85083a688244ad36b1a28cbbc05252b8fd55fa10f77335`.

Adapter compare passed: 3054 legacy text/exception observations равны baseline, errors/changed
inputs/code 0. 3052 manifests прошли input/XML SHA, JSON round trip и проверку каждого XML path
по элементу исходного дерева: 1972231 paragraph/row/cell coordinates. Два прежних ValueError
сохранены без выдуманных блоков. Report хранит manifest hashes/counts, не миллионы block texts.
`/private/tmp/lexinform-word-j046-after-20261025.json`, SHA
`d59579ae7bd51cf510350871280d692ce5e2e99121e32c8acaf9945af445bb53`.
Это machine regression/structure oracle без новых legal labels или claims о визуальной полноте.

Совместимость общего `_WordMl` дополнительно проверена прежним external extraction replay:
3067 Word/XML и 69 ZIP, все 5700 исходных путей сохранны, changes/missing/errors 0,
lost/new automatic matches 0. Это включает Flat OPC/Word 2003 и прежний выбор комплекта,
а не только новый DOCX leaf. `/private/tmp/lexinform-word-j046-legacy-20261025.json`, SHA
`5d38eefeab74ce171f71289d0fb78eb7528d5e78df0aa2194c3ecd65687be93a`;
source guard `.sources.json` подтверждает неизменность кода во время replay.
Два DOCX без манифеста (`657792.docx`, `657796.docx`) сохраняют прежний
`ValueError: document XML size limit`; лимит не повышен ради зелёного результата.
После переноса в рабочий checkout source fingerprints совпали с corpus-qualified prototype.

J045 выпущен: commit `f2f2c374c701ff1104daac356436b9a38703d2cd`,
CI [40191493135](https://github.com/sobolevbel/lexinform/actions/runs/40191493135)
и Deploy relay [40191899479](https://github.com/sobolevbel/lexinform/actions/runs/40191899479)
success на этом SHA; все test/web/docs jobs green. Test job: 3921 passed, 32 deselected,
xfail больше нет. Deploy log `be49fd4f53a3 -> f2f2c374c701`, relay up 45 s после restart,
mismatch отсутствует. Evidence `/private/tmp/lexinform-release-j045-20261025.json`;
это metadata/log verification, без ручного production run, SSH или Telegram send.

Финальный рабочий gate: 3938 passed, failures/errors/skips/xfails 0, mypy 361 файлов,
Ruff check/format 392, local HTTP 21 passed, strict docs build/link check и diff check green.
XML `/private/tmp/lexinform-word-j046-root-20261025.xml`, SHA
`8a962c9f1407c04810828fa9a51f83207aba7059589e0c6cdda68577c444d9e7`.
Source fingerprints рабочего checkout совпадают с qualified replay; обычная строка и selection
не меняются. Новые блоки ещё не привязаны к physical parts; это следующий отдельный срез.

<a id="j047"></a>
### J047. DA3: привязка Word-блоков к физическим частям

Дата: 25 октября 2026. База: `6d21a59ec9f0ee5053fc237ae0e07b040e889e93` (J046). Статус: принят локально; physical/legacy/comparison corpus checks и полный gate green. DA3 остаётся частичным.

`ExtractedParts.word_blocks` связывает `WordTextExtraction` с одним существующим physical part
через `WordPartExtraction.part_index`, input SHA и точное равенство legacy text. Повторяющиеся
bytes в разных ZIP members сохраняют отдельные связи и member chains. Неверный индекс,
дубликат связи, иной SHA или текст отклоняются validator; persisted `part:N` facts не меняются.

`WordBlockTextExtractor` — optional structural port. `extract_parts` получает Word manifests
по умолчанию, если reader поддерживает его; старые readers сохраняют прежние strings без
выдуманных blocks. `include_word_blocks=False` и обычный `extract_document` не удерживают
инвентарь блоков. Один rich Word read одновременно даёт физические блоки и прежний текст.
Неподдержанный body сохраняет SHA/state с manifest без блоков; malformed/limited XML — прежний
rejection без fake part/manifest. Обычный путь выбора/анализа не переключается на новую policy.

Расширен `tools/replay_word_blocks.py`: mode `parts` читает через physical-parts API и проверяет
те же text/exception observations и XML paths против pinned leaf baseline J046. При отсутствующем
manifest повторяется прежний leaf read: readable Word не может незаметно потерять блоки,
прежний leaf failure остаётся failure observation. Raw files, source fingerprints и reference
SHA защищены прежним read-only guard. Общий external extraction replay дополнительно проверяет
legacy selection, Word/XML и ZIP; corpus artifacts содержат hashes/counts, не block texts.

84 focused tests green, mypy 362 файлов, Ruff check/format 393. Проверены root/nested/duplicate
physical identity, opt-out и legacy reader, unsupported/malformed body и четыре повреждения
binding; runner tests проверяют leaf и physical entrypoints. SQL/schema, archive/backend,
PreparedInput и note-part addresses остаются прежними; storage decisions здесь нет.

Physical-parts compare passed: 3054 text/exception observations совпали с committed leaf
baseline J046; 3052 manifests и 1972231 XML coordinates проверены через новый entrypoint.
Changes/errors/changed inputs/code 0. Artifact
`/private/tmp/lexinform-word-parts-j047-after-20261025.json`, SHA
`1400b363be733d5045fa7b8d4a269b040586bede27eb999beee4478db9cef377`.
Два прежних size-limit failures сохраняют отсутствие part/manifest.

Legacy external replay также passed: 3067 Word/XML и 69 ZIP, 5700 inputs, 19198 observations;
changes/missing/error observations/lost or new automatic matches 0. Artifact
`/private/tmp/lexinform-word-parts-j047-legacy-20261025.json`, SHA
`a6c34989c406a66faee4a9d6e2a0b338f486f67b17d2ec11b2ff6c3bd5b35848`.
Source guard `.sources.json` подтверждает неизменность кода, SHA
`6796b9884d8817dcc2b2997a8f9c623959d78ecc0683b5c16cb94a1575f2be69`.
Обычный reader/selection contract сохранён; raw files остаются вне production state.

J046 выпущен: commit `6d21a59ec9f0ee5053fc237ae0e07b040e889e93`,
CI [40192339730](https://github.com/sobolevbel/lexinform/actions/runs/40192339730)
и Deploy relay [40192776738](https://github.com/sobolevbel/lexinform/actions/runs/40192776738)
success на этом SHA; test/web/docs jobs green, 3938 passed, 32 deselected, без xfail.
Deploy log: `f2f2c374c701 -> 6d21a59ec9f0`, relay up 45 s после restart, mismatch отсутствует.
Evidence `/private/tmp/lexinform-release-j046-20261025.json`; это metadata/log verification.

Финальный рабочий gate: 3950 passed, failures/errors/skips/xfails 0, mypy 362 файлов,
Ruff check/format 393, local HTTP 21 passed, strict docs build/link check и diff check green.
XML `/private/tmp/lexinform-word-parts-j047-root-20261025.xml`, SHA
`c629c60945232db9ccac73af0d589dc5654d404b73f81b57473cd81024dec719`.
Физический manifest/part SHA и текст проверяются до legal selection; обычный путь анализа
сохраняет прежнюю policy и не удерживает новые block inventories. Schema 48 прежняя.

Дополнительный comparison profile до/после всех трёх срезов: committed `be49fd4` и рабочий J047
независимо записаны через `run_external(..., ("comparison",), mode="baseline")`; snapshots затем
сверены существующим `compare_rows`, с проверкой source guards, population и всех input SHA.
1028 реальных пар, 6594 controlled mutations на 1698 documents, 2169 input paths;
changes/changed inputs/lost or new automatic matches 0. Семейства: сроки/перестановка сроков,
год, отрицание, десятичная запись, отдельное число, ссылка на статью и круг адресатов.
Text-digest misses 0 до/после; прежние digest/guard совпадения в synthetic probes сохранены.
Они включают реквизиты цитирования и не являются независимыми legal labels или доказательством
неизменности права. Новая normalization/selection policy этим сравнением не вводится.

Baseline `/private/tmp/lexinform-comparison-j045-before-20261025.json`, SHA
`ae00d62f9e3f5acfa690383a9d7a872b3faa60767e4c8ae32b52efd310ba7864`;
candidate `/private/tmp/lexinform-comparison-j047-candidate-20261025.json`, SHA
`3cefa8cc184f015ca286301a06d5401c5cb79abb1c794a9f6068c1ee65c3e1fa`;
comparison proof `/private/tmp/lexinform-comparison-j047-accepted-20261025.json`, SHA
`ecc3921654b79aabc65b41db6808663f1e0f0d5272a13c3c777a3703c5a8ff4f`.
Оба `.sources.json` guards green; candidate fingerprints совпадают с рабочим checkout.
Скрипты исполнения/сверки: `/private/tmp/lexinform-compare-profile-20261025.py` и
`/private/tmp/lexinform-compare-recorded-20261025.py`; policy functions остаются tracked tools.

<a id="j048"></a>
### J048. DA3: XML-источники сносок Word и места их вызова

Дата: 25 октября 2026. База: `b522a00288b25db207b00ef3a7da2de91dc64a7b` (J047).
Статус: принят локально; DA3.2–DA3.3 частично, OOXML note origins проверены.

`extract_with_notes` возвращает immutable `WordDetailedExtraction`: прежний `word-blocks-v1`
body и отдельный `WordNoteExtraction` (`word-notes-v1`) с input SHA/body manifest SHA.
Старый body schema/hash не расширяется. Note manifest содержит XML part SHA, реальные child
paths сносок в `word/footnotes.xml`/`word/endnotes.xml`, paths вызовов в основном XML и ссылку
на существующий paragraph block. Адреса фиксируются внутри исходного обхода, не поиском текста.

Вызовы различают expanded/empty/missing/unverified. Повтор ID сохраняет обе прочитанные позиции,
указывает последний legacy target и loss. Неподдержанный root/entry может сохранить прежний текст,
но не получает выдуманный exact origin. XML с entity declaration сохраняет прежний отказ чтения.
Note без ID по-прежнему не читается. MC_Fallback не дублирует вызовы; inline Word 2003 notes,
standalone XML и вызовы внутри самих note parts остаются вне этой версии reference view.
Табличная структура внутри сноски не объявляется полностью сохранённой.

Один read строит body и notes; обычные `extract`/`extract_blocks` не удерживают note manifest.
Привязка к physical parts — следующий срез. SQL/schema 48, batch/memo/provider semantics,
хранение оригиналов и backend не меняются. Модели отвергают другие input/body SHA, отсутствующий
paragraph/target, иной note kind/ID, неверный last target, повтор/отрицательные XML positions.

96 focused tests: footnotes/endnotes, вложенный hyperlink/table call, strict OOXML, duplicate,
empty/whitespace/missing, unsupported/refused XML, missing ID, MC fallback, inline notes,
JSON round trip и независимая negative-проверка XML target в runner. Изменения сроков, сумм,
отрицания, адресатов, ссылки на статью и даты сохраняют разные text/source identities.
Mypy 364 файлов, Ruff check/format 395, без suppressions.

Baseline из `git archive b522a00` записан committed reader, без `.git` и `.env` в export.
`/private/tmp/lexinform-word-notes-j048-before-20261025.json`, SHA
`ad28597cfad046689cbd92d2782370a44979b163760ee098ea64fe18cd99db6f`,
3054 raw DOCX/DOCM, status recorded. Tracked runner `tools/replay_word_blocks.py` mode `notes`
сверяет legacy text/exception observations и разрешает note/call XML coordinates по raw bytes.
Отдельный mode `parts` committed baseline фиксирует прежние body manifest SHA.
Network/input writes запрещены guards; artifacts содержат hashes/counts, не block texts.

J047 выпущен: CI [40193778298](https://github.com/sobolevbel/lexinform/actions/runs/40193778298)
и Deploy relay [40194121279](https://github.com/sobolevbel/lexinform/actions/runs/40194121279)
success на `b522a00288b25db207b00ef3a7da2de91dc64a7b`; test/web/docs jobs green,
3950 passed, 32 deselected, без xfail. Deploy `6d21a59ec9f0 -> b522a00288b2`, relay up 45 s
после restart, mismatch отсутствует. Evidence `/private/tmp/lexinform-release-j047-20261025.json`;
это metadata/log verification без ручного production run, SSH или Telegram send.

Final note replay passed: 3054 text/exception observations совпадают с baseline;
3052 manifests, 17135 note positions и 5317 reference positions проверены по исходному XML.
5313 calls expanded, 4 empty; missing/unverified встречаются в focused tests, не в этом corpus slice.
Notes parts 5576; observed note entries включают служебные/unreferenced IDs, это не число
юридических сносок. Changes/errors/changed inputs/code 0. Artifact
`/private/tmp/lexinform-word-notes-j048-after-20261025.json`, SHA
`f7ccb5018557fad02a7c57d415632d325e817d1cbe02065a8e01d7bb9506abcb`.
Все 3052 прежних body manifests/physical summaries равны committed baseline:
`/private/tmp/lexinform-word-notes-j048-body-before-20261025.json`, SHA
`4ee97ff90c7892fa0c05c86fcda5e885ebf6631b613eb9f8f6ea416d629b10b1`;
comparison proof `/private/tmp/lexinform-word-notes-j048-body-compare-20261025.json`, SHA
`b08ddd1861701f81fac8f45aebd44b45dd4678097d76ec384cac8a6a3f9472ca`.

Legacy extraction replay: 3067 Word/XML и 69 ZIP, 5700 inputs, 19198 observations,
changes/missing/error observations/lost or new automatic matches 0; source guard green.
`/private/tmp/lexinform-word-notes-j048-legacy-20261025.json`, SHA
`7192bb8a17a974dbac1e9df1daafa25a30f74592358ea3c2684abb76d7128409`.

Controlled corpus probes: восемь raw-derived документов с адресованными сносками, 56 cases
(8 controls + 48 изменений срока, суммы, отрицания, круга лиц, статьи и даты). В test derivatives
сохраняются только три Word text XML parts; control clause добавлена искусственно и не является
правовым свидетельством. Committed old reader и новый reader дают одинаковый текст во всех
cases; семь вариантов каждого source различаются, XML note origins сохраняются, inputs/code
не менялись. `/private/tmp/lexinform-note-mutations-j048-20261025/adapter.json`, SHA
`7f287901f398d3903b9e087a7df01a4e793ea27c843c24e57ddad1535081698e`;
скрипт `/private/tmp/lexinform-note-mutations-j048-20261025.py`, corpus/sample bounds явные.

Финальный gate: 3979 passed, failures/errors/skips/xfails 0, mypy 364 файлов, Ruff 395,
local HTTP 21 passed, strict docs build/link check и diff check green. XML
`/private/tmp/lexinform-notes-j048-full-20261025.xml`, SHA
`17dcfea6c22922313ed3b6d671ea99cee1823581c88efa04eceb108ae90a4971`.
Точные retained spans после trim/budget и provider input этим срезом не заявляются.

<a id="j049"></a>
### J049. DA3: сноски связаны с физическими частями и body manifests

Дата: 25 октября 2026. База: `4061e5c1d0e6dd2f62afa16c3c5fc28366039c38` (J048).
Статус: принят локально; physical/legacy/ZIP corpus checks и полный gate green. DA3 остаётся частичным.

`ExtractedParts.word_notes` содержит `WordPartNoteExtraction`: physical part index и отдельный
notes manifest. Он ссылается на существующий `word_blocks` body через input SHA/body manifest SHA;
JSON не содержит второй копии body. Orphan/duplicate/wrong-index и чужие input/body SHA отвергаются.
Word note origins по-прежнему не являются retained spans или доказательством прочтения моделью.

Optional `WordNoteTextExtractor` даёт body и notes одним read. `extract_parts` использует его
по умолчанию; `include_word_notes=False` оставляет только body, выключение body отключает и notes.
Legacy/body-only readers не получают выдуманные notes. Обычный `extract_document` сохраняет
прежнюю string-only обработку без удержания новых inventories; schema 48, persisted selections,
batch/memo/provider payloads и storage backend прежние. Word 2003/Flat OPC/ODT/DOC coordinates,
внутренняя структура таблиц сносок и unknown objects остаются открытыми в DA3.

103 focused tests green, mypy 365 файлов, Ruff check/format 396. Проверены root/nested/duplicate
byte identity, одна операция чтения, оба opt-outs, body-only reader, round trip, отсутствие копии
body в note manifest и orphan/duplicate/wrong-index/foreign SHA bindings. Existing negative XML
origin replay остаётся активен. Tracked runner получил mode `notes-parts`.

Raw note replay passed: 3054 observations совпали с J048 committed baseline text/exception
records; 3052 physical manifests сохраняют те же body/note SHA/counts/losses, что leaf J048.
Artifact `/private/tmp/lexinform-notes-parts-j049-after-20261025.json`, SHA
`e550497c2924d4e5dd46822ce555374ec5d0004d12fb0836b64d7b817a85b21f`.
Все XML paths независимо разрешены в raw input; missing/unverified branches покрыты focused
cases. Network/input writes запрещены, source fingerprints не менялись во время replay.

Legacy extraction compare passed: 3067 Word/XML и 69 ZIP, 5700 inputs, 19198 observations,
changes/missing/error observations/lost or new automatic matches 0, source guard green.
`/private/tmp/lexinform-notes-parts-j049-legacy-20261025.json`, SHA
`7e75bc5da00e04ba6a16653fd20a58f923e25f7d989414c950cecaea973e37d7`.
Дополнительный raw archive replay проверяет именно rich physical API на всех 69 ZIP,
сверяет прежние parts/body hashes и разрешает member bytes к independent leaf XML checker.

J048 выпущен: CI [40196248484](https://github.com/sobolevbel/lexinform/actions/runs/40196248484)
и Deploy relay [40196704734](https://github.com/sobolevbel/lexinform/actions/runs/40196704734)
success на `4061e5c1d0e6dd2f62afa16c3c5fc28366039c38`; test/web/docs jobs green,
3979 passed, 32 deselected, без xfail. Deploy `b522a00288b2 -> 4061e5c1d0e6`, relay up 45 s
после restart, mismatch отсутствует. Evidence `/private/tmp/lexinform-release-j048-20261025.json`;
это metadata/log verification, не ручной production run или Telegram send.

Первый дополнительный ZIP checker не засчитан: стандартный `zipfile.read` не раскрыл member
Deflate64. Application decoder поддерживает его; checker повторяется с существующим bounded
member decoder, CRC/size checks и проверкой полученных bytes против physical SHA.
Это не независимый oracle распаковки; последующая проверка XML coordinates остаётся отдельной.
Production code и test assertions ради checker не менялись.

Финальный рабочий gate: 3991 passed, failures/errors/skips/xfails 0, mypy 365 файлов,
Ruff check/format 396, local HTTP 21 passed, strict docs build/link check и diff check green.
XML `/private/tmp/lexinform-notes-parts-j049-full-20261025.xml`, SHA
`b597ee49db3a4f91401dce51ac02fe14b72bac6e63320c0fe56a49d4d19699fb`.
Проверки body/note/raw identities не меняют legal selection, source tracking или persisted state.

Дополнительный rich archive qualification passed: все 69 raw ZIP, 1697 physical parts,
1062 Word body/note manifests. Прежние part payload hashes, rejections и body manifest SHA равны
committed baseline; каждый body имеет ровно одну notes binding. 5222 note и 1296 call positions
проверены independent leaf XML checker после раскрытия member chain и сверки physical SHA.
Changes/changed inputs/missing note bindings 0; source/input guards green. Это machine regression,
не legal oracle, не независимый decoder oracle и не доказательство полноты чтения.

Baseline `/private/tmp/lexinform-note-archives-j049-before-20261025.json`, SHA
`c3199ec6af3acbc94f65432a2f60af0ba7198faca4ebde49b3dab21529c5774e`;
after `/private/tmp/lexinform-note-archives-j049-after-20261025.json`, SHA
`21ebabfa01236dd41e73b66679472bad86b315b0973d304eaca52cff3cee0a0f`;
proof `/private/tmp/lexinform-note-archives-j049-accepted-20261025.json`, SHA
`01a9eed5898e961f4421c8cb91f71ed746b6bd804caa6f2ac6529e9d3ac8ab9b`.
Скрипт `/private/tmp/lexinform-note-archives-j049-20261025.py` использует существующий bounded
member decoder и tracked note replay checker; Deflate64 CRC/size bounds сохранены.
Суммарные corpus counts описывают observations, не уникальные юридические документы.
Все source fingerprints рабочего checkout совпадают с qualified raw note replay.
Area rules теперь охватывают `models/word_notes.py`; новый storage/profile не включён.

<a id="j050"></a>
### J050. DA5: точные карты excerpts и ограничения длины текста

Дата: 25 октября 2026. База: `a6166c46cd4dddda79ea4a653b9110297e72a608` (J049).
Статус: принят локально; raw corpus compare и полный gate green.
DA5 остаётся частичным.

`excerpt_projection` выполняет прежний выбор двух heads и keyword windows, возвращая точные
source offsets после `strip` и отдельные generated segments для вставленных markers.
Даже совпавший с пропущенным исходным текстом marker остаётся generated. Прежний `excerpts`
возвращает строку через тот же путь. `TextBudget.apply` добавляет in-memory projection, включая
identity для текста без сокращения и clipping посередине marker при достижении cap.

`TextProjection.compose` переносит диапазоны через предыдущую карту к исходной строке,
проверяет SHA/длину промежуточного текста и сами copied characters. Generated fragments не
становятся цитатами источника; соседние непрерывные диапазоны объединяются. Проверены repeated
text, whitespace/Unicode, пустые входы, caps 1–1000, generated joins, две последовательные
cost-like cuts, чужая identity и forged copy с верным SHA. Replay runner имеет negative test:
неизменный output с картой от другого input отвергается.

Это отдельный deployable срез перед runtime propagation: `_Loaded`, `_fit_to_budget`, actual
analysis coverage и persisted provider request пока не получают эту карту. Прежние unknown
coverage и policy completeness сохраняются. Нет schema migration, новых SQLite payloads или
добавления файлов в dump; новые projections живут только в памяти. Storage backend не выбран.

Tracked `replay_input_coverage` версии `input-coverage-v2` проверяет восемь видов входа:
trim, caps 1000/24000 без spans и с реальными keyword spans, triage excerpts и две cost-like
cuts. Старые v1 references не принимаются за совместимые. Baseline использует committed J049
source export с новой версией только replay tool; его fingerprints явно записаны. Это regression
oracle, не независимая правовая оценка и не проверка реальной provider tokenization.

J049 выпущен: CI [40197541697](https://github.com/sobolevbel/lexinform/actions/runs/40197541697)
и Deploy relay [40198013117](https://github.com/sobolevbel/lexinform/actions/runs/40198013117)
success на `a6166c46cd4dddda79ea4a653b9110297e72a608`; test/web/docs jobs green,
3991 passed, 32 deselected. Deploy `4061e5c1d0e6 -> a6166c46cd4d`, relay up 45 s после restart,
mismatch отсутствует. Evidence `/private/tmp/lexinform-release-j049-20261025.json`;
это metadata/log verification без ручного production run, SSH или Telegram send.

J050 controlled probes: восемь raw-derived Word sources, 56 cases (8 controls и 48 изменений
срока, суммы, отрицания, круга лиц, статьи и даты). Test clauses искусственно добавлены в начало
реального extracted text, не являются юридическим свидетельством; bounds 24k–1M chars и первые
восемь подходящих Word inputs явно заданы в скрипте. Все 56 outputs совпадают с committed J049
reader на trim/budget/triage/two cost-like cuts; в каждом виде входа сохранены все семь различий.
Карты после нескольких преобразований round-trip к точным исходным позициям. Inputs/code stable,
network, corpus writes и чтение `.env` запрещены guard. Artifacts
`/private/tmp/lexinform-budget-mutations-j050-before-20261025.json`, SHA
`d648ed3f94cd45a80590361a1f3f1c8c5f2423e5dbd6b257422c16b5ec5c7e9b`, и
`/private/tmp/lexinform-budget-mutations-j050-after-20261025.json`, SHA
`75a12f00c80cb1970d53c54c13f30f08f8b861a0e2d908a00c8c568ed25c97c6`.

J050 полный gate: 4015 passed, failures/errors/skips/xfails 0, mypy 366 файлов, Ruff check/format
и strict docs build/link check green. HTTP adapters не менялись, отдельный local HTTP gate не
требовался. XML `/private/tmp/lexinform-budget-j050-full-20261025.xml`, SHA
`3ce91159849a0ec553ec5322b3301534b9237df5b41d5bdbd4544f0a07d5eac4`.

Raw compare accepted: 3067 Word/XML, 69 ZIP и 114 PDF, всего 3250 inputs и 26000 variants.
Extracted/selected/input text SHA, law digest, dropped sections, states/limitations и прежняя
coverage совпали во всех observations. Все 26000 composed maps прошли round-trip; errors,
changed inputs/code и core/coverage changes 0. Prior selected-file decisions не изменились.
Baseline `/private/tmp/lexinform-budget-j050-before-20261025.json`, SHA
`0483a5dcf887ccb11718bc513f7c63a6141cb94adb9382c395da251f9896ce21`;
after `/private/tmp/lexinform-budget-j050-after-20261025.json`, SHA
`7adb9dc298436ef1256d77502575e8e98d612806b122b6fa4ab9a72f5870c718`;
acceptance `/private/tmp/lexinform-budget-j050-accepted-20261025.json`, SHA
`a9de71eb312de8e46ddc37e748c8422a23099a45531b763f0fe750a00c432219`.
Независимые snapshots записаны baseline recorder; внешний wrapper явно включает `mapped=True`
на новом коде, acceptance script сравнивает cores/coverage и проверяет fingerprints/mutants.
Скрипты `/private/tmp/lexinform-budget-j050-mapped-replay-20261025.py` и
`/private/tmp/lexinform-budget-j050-accept-20261025.py`; hashes inputs/wrapper/mutation runner
содержатся в acceptance artifact. Никаких provider calls, corpus writes или production replay.

<a id="j051"></a>
### J051. DA5: карта первоначального бюджета доходит до анализа и batch

Дата: 25 октября 2026. База: `c7ac8c682fe403f34b3572f79dab17f82405a1ec` (J050).
Статус: принят локально; focused unit/scenario, полный gate и окончательный raw compare green.
DA5 остаётся частичным: cost fitting, amendments assembly, immutable PreparedInput refs и
разделение completeness policy требуют следующих срезов.

`selection_for_input` принимает явную projection, проверяет точный output и SHA/длину
ordered selected assembly, сверяет copied characters и пересекает source ranges с каждой part.
Чужая identity, неверный output и forged copy отвергаются. Legacy callers без карты сохраняют
осторожный inference и unknown. Repeated fragment в другом файле не даёт coverage первому;
generated markers и вырезанный consultation appendix не попадают в нормативный диапазон.

`_Loaded` несёт карту первоначального `TextBudget`, composed через trim к исходной сборке.
Она доступна только при точном равенстве file text и canonical selected assembly. Обычный
анализ и frozen batch metadata получают ranges без второй копии текста. Дополнительный cost
fitting, self-amendments assembly и иные неподтверждённые сборки остаются unknown; `_unchanged`
не расширяет новые budget claims для прежнего анализа. Normative omission/completeness policy
не менялась, state/limitations и provider/memo identity должны совпасть с baseline.

Проверены конкретные part-local ranges после нескольких excerpts, repeated parts, trim+budget,
foreign identity/output/copy, sync/batch на workers 1/4 и сохранение metadata через dump/restore.
Отдельный scenario подтверждает unknown после реального дополнительного cost fitting. Старые
J045 regressions/characterizations сохранены. Projections не сериализуются в provider request;
в существующей selection хранятся только ranges, schema 48 и storage backend прежние.

Предварительный prototype replay: 3250 inputs, 26000 variants, core/input/decision changes 0,
850 files с ожидаемым уточнением analysis coverage; extraction ranges не менялись. После этого
рабочий mypy нашёл повторное использование имени `trace` для optional value в replay runner;
исправлено переименованием, окончательные gate/raw replay запущены заново. Prototype artifact
`/private/tmp/lexinform-budget-j051-after-20261025.json` не считается финальной квалификацией.

J050 выпущен: CI [40201809960](https://github.com/sobolevbel/lexinform/actions/runs/40201809960)
и Deploy relay [40202294283](https://github.com/sobolevbel/lexinform/actions/runs/40202294283)
success на `c7ac8c682fe403f34b3572f79dab17f82405a1ec`; test/web/docs green,
4015 passed, 32 deselected. Deploy `a6166c46cd4d -> c7ac8c682fe4`, relay up 45 s после restart,
mismatch отсутствует. Evidence `/private/tmp/lexinform-release-j050-20261025.json`;
это metadata/log verification, не реальный production run или проверка Telegram delivery.

Окончательный tracked compare accepted на текущих source fingerprints: 3067 Word/XML, 69 ZIP,
114 PDF, всего 3250 inputs и 26000 variants. Text/extraction/selection/input SHA, law digest,
dropped sections, states/limitations совпали; core changes/errors/changed inputs/code 0.
Analysis coverage уточнена у 850 files: caps 1000 и keywords-1000 по 841, caps 24000 и
keywords-24000 по 385, triage 724, cost-like cuts по 850. Extraction coverage и trim-only
coverage не менялись. Все восемь maps каждого input прошли literal round-trip.
Concrete samples reviewed: `339907.docx`, `480108.zip`, `341524.pdf`; unknown 0..0 заменён
раздельными retained ranges, нормативная неполнота сохранена, generated markers исключены.

Reference `/private/tmp/lexinform-budget-j050-after-20261025.json`, SHA
`7adb9dc298436ef1256d77502575e8e98d612806b122b6fa4ab9a72f5870c718`;
final `/private/tmp/lexinform-budget-j051-final-20261025.json`, SHA
`53b56ae663f0553362b019515053c3c3c65dd1486df9a0f440c8f17859f79311`;
acceptance `/private/tmp/lexinform-budget-j051-accepted-20261025.json`, SHA
`037f50425c1d38e524a282dfc9d6c12e7263af8bafe29ae97680ab11a38a1209`.
Prototype observations совпали с final, но его прежние fingerprints не подменяют final proof.
Скрипт `/private/tmp/lexinform-budget-j051-accept-20261025.py` сверяет также full gate/default cap.

Размер stripped selection JSON: максимальная прибавка на один matrix input 431 bytes для
keywords-1000, 898 для keywords-24000, 1052 для triage; trim-only delta 0. Это metadata по
восьми тестовым входам, не восемь сохраняемых payloads и не прогноз production SQL dump.
Дополнительный raw probe берёт все семь inputs с selected-extraction upper bound >1.5M chars,
проверяет фактический default cap 1500000 и карты до исходных строк. Три входа budget-truncated;
outputs/decisions/coverage/JSON size равны J049 baseline. Это не восполняет отсутствующие
official source relations. Новые maps не дублируют ни raw files, ни полные тексты в dump.
Default before `/private/tmp/lexinform-budget-default-j051-before-20261025.json`, SHA
`827df40eb8bcdf6165213df2fc5ab7bdb04b54e3371133c10d49d8d1486293f1`;
after `/private/tmp/lexinform-budget-default-j051-after-20261025.json`, SHA
`7a717e300f5f1361573b30c99ed3ad3260594baa2fba3223323951217c52755c`.

Финальный gate: 4026 passed, failures/errors/skips/xfails 0, mypy 368 файлов,
Ruff check/format и strict docs build/link check green; HTTP adapters не менялись.
XML `/private/tmp/lexinform-budget-j051-full-20261025.xml`, SHA
`c5a375253bb41373db34ada535281d7d1ac6d074bab555d66cb5c07eb8d3601d`.
Рабочие source fingerprints совпадают с final corpus proof и default-cap probe.

<a id="j052"></a>
### J052. DA5: точные диапазоны после cost fitting и перехода на overflow

Дата: 25 октября 2026. База: `b0185b4d27f4a5e52412ca6acfa910cd8cc1b613` (J051).
Статус: принят локально; полный gate, raw/runtime compare и meaningful mutations green.
DA5 остаётся частичным: amendments assembly, полнота нормативного комплекта, scan derivatives
и immutable PreparedInput refs требуют отдельных срезов.

`_FittedContext` связывает конкретный `BillContext` с его in-memory projection, отвергает чужой
output и выдаёт stripped selection. `_shorten` возвращает traced excerpt вместо строки; оба
последовательных сокращения compose ranges к первоначальной сборке. При preflight overflow,
синхронном отказе и сохранённом batch context rejection расчёт начинается с исходного контекста
и его карты. Результат/memo и frozen batch metadata используют карту именно отправленного input.

Стоимость, token ceilings, minimum cut, overflow routing, текст/порядок requests и memo identity
сохраняются. Неподтверждённая первоначальная сборка остаётся unknown; `_unchanged` не расширяет
новые claims для прежнего анализа. Projections не сериализуются в provider JSON или SQLite;
существующая selection получает ranges. Schema 48, files/raw retention и storage backend прежние.

Focused checks: sync/batch на workers 1/4, две последовательные cuts с повторным token count,
все три пути overflow, восстановление rejected batch через dump/restore и отсутствие новой карты
в provider JSON. Подставная карта с чужой source identity вызывает per-bill failure до model call.
Первые fixtures не достигали целевых веток: preflight раньше второго cut и одинаковый provider
для primary/overflow. Fixtures уточнены под реальные условия; production policy не менялась.

`tools/replay_cost_inputs.py` проверяет все 3250 raw inputs из SHA-pinned J051 inventory,
сверяет extraction/selection/trim hashes и запускает cost cases при bill heading и >=40000
trimmed chars. Runtime использует text-only single-part fake view через настоящий pipeline;
это не новая правовая оценка bundles или проверка scans. Контексты/token recounts, frozen
request payload/custom ID, result/meta без selection и исходы сравниваются с committed baseline.
На новом коде публичный excerpt dependency наблюдается без изменения результата; source map
из observed cuts независимо проводится через trim/budget и сверяется с persisted ranges.
Network, corpus writes и `.env` reads запрещены; environment очищен, Settings harness использует
`_env_file=None`, repository SQLite только в памяти, все providers и доставки подставные.

J051 выпущен: CI [40203144650](https://github.com/sobolevbel/lexinform/actions/runs/40203144650)
и Deploy relay [40203621327](https://github.com/sobolevbel/lexinform/actions/runs/40203621327)
success на `b0185b4d27f4a5e52412ca6acfa910cd8cc1b613`; test/web/docs green,
4026 passed, 32 deselected. Deploy `c7ac8c682fe4 -> b0185b4d27f4`, relay up 45 s после restart,
mismatch отсутствует. Evidence `/private/tmp/lexinform-release-j051-20261025.json`;
это metadata/log verification без ручного production run или Telegram send.

J052 corpus accepted: все 3250 raw inputs совпали с J051 по extraction/selection/trim hashes.
Для 252 bill-heading inputs >=40000 trimmed chars проверены 504 runtime cases: sync и batch.
494 отправленных fake contexts имеют проверенную карту, 10 исходов без отправки совпали с
baseline. Все contexts/token recounts, frozen payload/custom ID, memo-derived identities и
result/meta без selection совпали с committed J051 source export. Runtime core changes 0;
errors/changed inputs/code 0. Результаты двух independent recordings сведены acceptance script.

Уточнены только analysis ranges после 111 cuts: 91 sync и 20 batch, 91 raw file. Extraction
coverage и losses кроме `input coverage unknown` прежние. Reviewed samples: `513269.docx`,
`1058__1058.pdf`, `480108.zip`; retained intervals раздельные, generated markers исключены.
Остальные 2998 inputs не входят в runtime cost cohort, их raw decisions/hash всё равно сверены;
scans и физическая bundle policy новым runtime fake view не квалифицируются.

Baseline `/private/tmp/lexinform-cost-j052-before-20261025.json`, SHA
`243703aa037b4e4eeb5ad0a01004c9344340f8d64013bf5bf20f62bda051abb7`;
after `/private/tmp/lexinform-cost-j052-after-20261025.json`, SHA
`80a5102bd6ade4d697d2a9d3f0a858cf05aa55384e4265404b4816046b963c98`;
proof `/private/tmp/lexinform-cost-j052-accepted-20261025.json`, SHA
`933e6fc3ccda41622d18ce7e425dd15756b69aed88796eb682bc9f6f9143c3ec`.
Скрипт `/private/tmp/lexinform-cost-j052-accept-20261025.py`; tracked runner воспроизводит
recordings, умеет direct compare с pinned baseline. Это machine regression, не legal oracle.

Meaningful probes: 8 raw-derived Word sources, 48 изменений срока/суммы/отрицания/круга лиц/
статьи/даты, по семь вариантов на source и два runtime modes, всего 112 cases. Контрольная
clause искусственная, не юридическое свидетельство; все семь final-input fingerprints каждого
source различны в обоих modes, новые карты проходят literal round-trip, old/new cores равны.
Первый probe не засчитан: искусственный USTAWA prefix перед обоснованием не делает его bill.
Final probe использует тот же bill-heading cohort, что tracked runner; 16 неподходящих controls
явно исключены. Production policy ради проверки не менялась. Artifacts
`/private/tmp/lexinform-cost-mutations-j052-before2-20261025.json`, SHA
`a74ba117212fe8a7f1ed5b5b253749994d54614c734da52b65a83aaf5302d03e`, и
`/private/tmp/lexinform-cost-mutations-j052-after2-20261025.json`, SHA
`b0bba313fcfb1de732d26501ed35fe220ecc9cda25bfad2560343b3f3343459e`.
Bounds/selection/exclusions заданы в `/private/tmp/lexinform-cost-mutations-j052-20261025.py`.

Metadata-only delta: максимум 6135 bytes на один stress case, суммарно 82420 bytes по обоим
modes для 252 sources. Это stripped selection JSON при fake cost limit $0.30, не прогноз
production SQL dump; оба modes одного source не сохраняются одновременно. Новых копий raw
files или полных текстов не добавлено, backfill существующих analyses не выполняется.

Финальный gate: 4037 passed, failures/errors/skips/xfails 0, mypy 369 файлов,
Ruff check/format и strict docs build/link check green. HTTP adapters не менялись.
XML `/private/tmp/lexinform-cost-j052-full-20261025.xml`, SHA
`eff85c79b7a084dc28725bd79b129f557b6dec669baf000bd8e850979dbc9a19`.
Рабочие source fingerprints совпадают с raw/runtime и final mutation recordings.

<a id="j053"></a>
### J053. DA5: карта сборки отдельно обозначенных autopoprawki

Дата: 25 октября 2026. База: `4fbce754bd0b3fc59ba7ac5b6d41513f578cfed5` (J052).
Статус: реализован и принят локально; окончание выпуска записывается отдельно.
DA5.2 остаётся частичным: неподтверждённые сборки и прочие виды PreparedInput требуют
отдельных срезов; политика полноты нормативного комплекта DA5.4 пока не меняется.

`document_input` сохраняет прежнюю строку основного текста и отдельных self-amendments.
Карта учитывает порядок parts в selection и иной порядок их показа: основной текст/support,
затем отдельно обозначенные предложения. Generated separator, инструкция «не сводный текст»
и URL labels не получают координат источника. Карта доступна только когда ordered non-amendment
parts точно воспроизводят вход `trim_print`; SHA/length и итоговый output проверяются.
При несовпадении остаётся unknown, без подбора диапазонов по похожести или совпавшим строкам.
Budget/cost fitting compose эту карту через J051–J052, анализ и batch сохраняют прежний формат
stripped selection. Дополнительных копий файлов/полных текстов в SQLite и новой схемы нет.

Unit/scenario checks покрывают повторяющиеся предложения, перестановку поддержки и поправок,
generated trim marker, неизвестную сборку/чужую identity/output, обычный и batch input,
первоначальный budget и дополнительное сокращение стоимости. Политика выбора файла, роль
предложения, стоимость и содержимое model request остаются прежними.

Inventory взят из 825 RCL projects и 4276 reached-stage snapshots: 355 исторических кандидатов
с amendment URLs. Предварительные 349 «fully cached» проверяли только наличие main/amendment/
extra paths, не нормативные ссылки и не пригодность текста. Final replay заново вызывает
настоящий `TextLoader`, включает normative URLs/альтернативы и сохраняет SHA всех прочитанных
paths. Снимки corpus заморожены; это не текущая правовая истина.

`tools/replay_amended_inputs.py` сопоставляет committed J052 source export и новый код:
выбранный документ, selection/input hashes, dropped sections, state/limitations, затем отдельно
coverage и stripped JSON size. Матрица — полный input и caps 1000/24000 с keywords и без них.
Guard запрещает сеть, файловые записи и `.env`; cache-read offsets не являются raw/page maps.
В baseline export заимствованы только replay/probe scripts. Первый before recording отвергнут:
во время его работы добавлен probe script, поэтому changed-code guard сработал. Before replay
запущен повторно на неизменных fingerprints, production policy ради проверки не ослаблялась.

J052 выпущен: CI [40207029019](https://github.com/sobolevbel/lexinform/actions/runs/40207029019)
и Deploy relay [40207415494](https://github.com/sobolevbel/lexinform/actions/runs/40207415494)
success на `4fbce754bd0b3fc59ba7ac5b6d41513f578cfed5`; test/web/docs green,
4037 passed, 32 deselected. Deploy `b0185b4d27f4 -> 4fbce754bd0b`, relay up 45 s после restart.
Evidence `/private/tmp/lexinform-release-j052-20261025.json`; это CI/relay verification,
не ручной production run, paid LLM, backfill или проверка Telegram reader delivery.

Final corpus recordings: 355 сборок, errors/changed inputs/code 0. Прочитаны 825 metadata
paths и 556 cache paths; семь кешированных извлечений отсутствуют. Из 355 сборок 292 дали
text view, 63 не дали пригодного входа. У 291 text view карта восстановлена, всего 1455 maps
с literal round-trip; это не 291 отправленный анализ. В числе карт 168 selected, 121 unverified,
две waiting views; policy/gating не меняются. Оставшаяся readable view `12397401/9` после
fallback уже не имеет отдельных amendment parts и не получает нового утверждения о карте.
Две inventory rows с normative URLs остаются blocked при прежней политике/cache availability.

Всего проверено 1460 input variants. Document/selection/input hashes, dropped sections,
states/limitations совпали; extraction coverage и losses кроме `input coverage unknown`
не менялись. Analysis ranges уточнены в 1420 variants у 284 сборок, без promotion роли
предложения до нормативного baseline. Семь mapped assemblies без нормативных analysis spans
не получают их искусственно. Reviewed: `12326400/10` (`512521.zip` cached text), keywords cap
даёт раздельные retained ranges; `12311506/11` — parts reorder без фиктивных analysis spans;
`12397401/9` — fallback без отдельного amendment. Raw ZIP extraction этим cache replay
не квалифицируется повторно, page/scans и недостающий кеш не восстанавливаются.

Inventory `/private/tmp/lexinform-amendment-cohort-j053-probe-20261025.json`, SHA
`637652a2f91dc5707fd77d80ffa075b1b72a8b5472577f9b0ffbcd070dff62a7`;
baseline `/private/tmp/lexinform-amendment-j053-before2-20261025.json`, SHA
`7d49cded87194e5e6799c4dc8e05c3a44aed6d05d51d25a47024704949b5f7df`;
after `/private/tmp/lexinform-amendment-j053-after-20261025.json`, SHA
`f050287c48004836510aa4f50964fb4695d809735f94c2b1a04b1ae2c3bf6aa6`.

Meaningful probes: восемь unique main URLs, по шесть изменений срока/суммы/отрицания/круга
лиц/статьи/даты отдельно в основном тексте и предложении. Всего 96 mutations, 16 controls,
112 cases и 336 views полного input/caps 1000/24000. Artificial clauses добавлены к реально
прочитанным cached parts; факты legal completeness для них не заявляются. Семь full-input
hashes на каждый target/source различны, старые/новые строки совпали, все карты round-trip.
Границы выбранного cohort и 11 неподходящих controls записаны в probe output.
Before `/private/tmp/lexinform-amendment-mutations-j053-before-20261025.json`, SHA
`76535e0eb7374a37841dd255ae49a97232e1c9225b9bea5515a80363a7e93d11`;
after `/private/tmp/lexinform-amendment-mutations-j053-after-20261025.json`, SHA
`2670e60253dc3356e80fc12b56c76fccc8d2aaebd42f55c26319e9bb71eba022`.
Скрипт `/private/tmp/lexinform-amendment-mutations-j053-20261025.py`.

Stripped selection JSON delta: максимум 91 byte для full input, 748 bytes для keyword-24000,
суммарно 195021 bytes по всей матрице пяти вариантов и повторных исторических стадий.
Это не прогноз production dump и не пять сохраняемых запросов на каждую стадию.
Новая projection остаётся in-memory, schema 48 и retention прежние, backfill не выполняется.

Финальный gate: 4047 passed, failures/errors/skips/xfails 0, mypy 373 файлов,
Ruff check/format для src/tests/tools и strict docs build/link check green.
Acceptance script подтвердил pinned recordings, неизменные source fingerprints и XML gate
с 4047 tests, errors/failures/skipped 0, 623.471 s. Tool outputs этой сессии содержат результат;
после смены среды временные файлы `/private/tmp/lexinform-amendment-j053-*` недоступны.
Это ограничение сохранности измерений, не повод восстанавливать их содержимое по памяти.

Перед коммитом read-only `git ls-remote origin refs/heads/main` не был выполнен: automatic
approval review не смог завершиться из-за usage limit, предложено повторить после 18:25.
Отказ не был признанием команды небезопасной. Коммит и push не выполнялись в обход проверки;
локальные изменения и журнал готовы к завершению после восстановления approval review.

После указанного времени сброса лимита проверка снова доступна: 25 октября, 19:06 Warsaw,
`git ls-remote origin refs/heads/main` успешно подтвердил точный J052 SHA `4fbce754bd0b3fc59ba7ac5b6d41513f578cfed5`.
Повтор выполнен после устранения временного ограничения, не альтернативным путём в обход review.

<a id="j054"></a>
### J054. Корпус: health report, очередь действий и обзор следующих улучшений

Дата: 25 октября 2026. Runtime-база J053: `c793c42cba320837f023081b581662071e834eba`.
Статус: инструмент, read-only проход и общий gate приняты локально.
J053 закоммичен после его 4047-test gate и повторных 38 focused tests; проверка разрешений
восстановилась после времени сброса. Следующий инструмент не меняет runtime обработки документов.

Добавлен `tools/corpus_health.py`: индексированные RCL originals/caches и required references
исторического self-amendment cohort. Сеть, writes в корпус и `.env` запрещены; output новый
и вне корпуса. Отчёт различает missing raw, missing/broken cache, empty text, size limit,
несколько raw aliases и неиндексированный кандидат. Actions содержат URL, stage ID и поле
зависимости; tool не выполняет download/re-extraction/OCR и не объявляет raw integrity.

Свежий `--check-text`: 10411 indexed documents имеют original/cache; 10234 непустых UTF-8
текста, 177 empty, broken gzip/UTF-8 0. К 355 candidates из 825 projects/4276 stages нужны
ещё семь документов без raw/cache/index: 300535, 300536, 300677, 301219 (RCL/12294859)
и 412632, 418667, 418672 (RCL/12321775). Первые два — alternatives; часть остальных также
альтернативы, остальные main/self-amendment dependencies. Доступность URL в сети не проверялась.
Прочитанные 11237 input paths и code fingerprints зафиксированы и не менялись во время прохода.

Полный JSON: `out/corpus/2026-10-25-rcl-health.json`, 5583522 bytes, SHA
`0c673c1e5972f832f2ea36af657d51db6c8e88867d34cd5e2512e942a5cf8688`.
Компактная tracked сводка —
[summary.json](../assets/audits/2026-10-25-corpus/summary.json), 14825 bytes, SHA
`00275d9f759f24840e5b9c7183c2d4986e3d6d4b2976caf8b00e6e04971106f2`.
`out/` остаётся ignored; проектный каталог устойчивее `/tmp`, но сам по себе не является backup.
Шесть новых tests проверяют offline repair/download distinction, empty/corrupt cache,
candidate provenance, сохранность входов, no overwrite и symlink/path guards.

[Обзор корпуса и предложения](../reviews/2026-10-25-corpus-workflow.md) сводит выполненные
raw/cached/CI/holdout/mutation проверки и их exclusions. Приоритеты: постоянные run manifests
и versioned generators/acceptance, общий diff поверх существующих runners, preflight/action
queue, relation/span labels и новый групповой holdout. Download paths остаются resume keys,
докачка семи files и повтор трёх consultation refusals предлагаются отдельным проходом.
Массовая перекачка и новый storage backend не вводятся. Общий SQL schema/state/dump прежние.

Первый общий gate J054 дал 4052 passed и один failure: отсутствовал vocabulary cache tiktoken,
локальный `count_input_tokens` пытался получить `o200k_base` и встретил DNS refusal.
Это не regression extraction/selection и не paid model call. Сохранён failed XML
`out/corpus/2026-10-25-corpus-tools-tests.xml`; тест не отключался и assert не ослаблялся.
Официальный vocabulary получен штатным loader с expected SHA в ignored `.cache/tiktoken`;
повторный полный gate использует этот явный persistent TIKTOKEN_CACHE_DIR.
README tools дополнен повторяемой подготовкой offline environment.

J053 выпущен: CI [40220264364](https://github.com/sobolevbel/lexinform/actions/runs/40220264364)
и Deploy relay [40220771569](https://github.com/sobolevbel/lexinform/actions/runs/40220771569)
success на `c793c42cba320837f023081b581662071e834eba`, test/web/docs green,
4047 passed, 32 deselected. Deploy `4fbce754bd0b -> c793c42cba32`, relay up 45 s после restart.
Evidence `out/corpus/2026-10-25-release-j053.json`; это CI/relay verification,
не ручной production pipeline, paid LLM, state/backfill или Telegram reader delivery.

Повторный gate после подготовки tokenizer cache: 4053 passed, errors/failures/skips/xfails 0,
mypy 374 файлов, Ruff check/format src/tests/tools, strict docs build/link check и
`git diff --check` green. CI manifest corpus slice passed; health source fingerprints прежние.
XML `out/corpus/2026-10-25-corpus-tools-tests-cached.xml`; hashes и cache identity — в
[validation.json](../assets/audits/2026-10-25-corpus/validation.json).

<a id="j055"></a>
### J055. DA3/DA5: идентичность отправленных PDF и карты страниц сканов

Дата: 25–26 октября 2026. База: `14c506d0072013083434b006ac416eaa6f883484`.
Статус: реализован и принят локально; полный gate, corpus replay и rollback compatibility green.
DA3.6/DA5.5 остаются частичными: completed derivatives не архивируются, coverage/persistence
прочих secondary consumers и версии extractor/options требуют следующих срезов.

`ScanInputManifest` разделяет source SHA/size и exact attached SHA/size, хранит source page count,
первую исходную страницу, page count и цепочку cover/triage transformations. `ScannedDocument`
проверяет, что manifest совпадает с прикреплёнными bytes. После первого cut triage сохраняет
исходную нумерацию, не начинает её с единицы. Source SHA остаётся прежним document digest;
новые метаданные не меняют memo key, batch custom ID, provider payload, prompt или цену.
Legacy snapshots без manifest остаются unknown; null-поле не добавляется в их JSON.

Primary analysis сохраняет manifest в существующем `DocumentSelection`, включая batch meta,
collect/memo и restore. Отрицательный triage сохраняет manifest реально показанного окна,
а не полного документа. Дополнительные исходники, тексты и PDF bytes в SQLite не добавляются;
schema, retention и текущий обязательный pending request прежние. Возможность исторического
replay completed PDF по одному manifest не обещается, storage backend не выбирается.

Исправлен отказ вырезания cover page: `select_pages` может вернуть исходный PDF. Раньше
`load_scan` сохранял pages/cover как при успешном cut, хотя отправлялся полный файл. Теперь
reported window соответствует полному файлу; если он превышает page limit, scan не создаётся.
При нормальном cut отправляемые bytes и прежние диагностические поля должны совпасть с baseline.
Regression использует также копию исходных bytes, чтобы не опираться только на object identity.

Работа изолирована в managed worktree `scan-input-manifests`: в общем checkout параллельно
меняются corpus tools. Своё изменение registry перенесено отдельно; соседние изменения сохранены.
Корпусные outputs лежат в постоянном `lexinform/out/corpus/j055` и `out/corpus/runs/j055-*`.
Baseline source export взят из `14c506d`; его archive SHA
`0416cb7f73cb60e1cd5410508ac7e2e2602b1589a92a1569ad2ace70295f416d`.

Новый runner `tools/replay_scan_inputs.py` фиксирует все сохранённые PDF attachments 980 prints,
59 empty RCL PDF и raster-bearing RPW. Получилось **1521 raw inputs**: 988 Sejm / 474 RPW / 59 RCL;
оба cover settings и triage проверяются отдельно. Это физический regression cohort, не 1521
подтверждённых bill/scan decisions. SHA inventory
`d5266122ca0be893d88cb57856d0ebb093b3f9112bae008ad7582d7358b49e2f`.
При наличии нового manifest проверяется соответствие content/encoded XObject streams, page boxes и
rotation исходному диапазону. Rendering, OCR, legal completeness и неподдержанные формы вне scope.
Смысловые PDF probes меняют срок, сумму, отрицание, круг лиц и ссылку внутри показанного prefix.

Focused unit/scenario: manifest round trips, legacy serialization, fallback/oversized fallback,
оба provider payloads/prices, workers 1/4, sync/batch/restore/collect без download и без повторного
triage, восемь фактически показанных страниц rejection и сохранение прежнего batch identity.

Первоначальное измерение compact state без отдельного triage manifest дало +295 SQL / +59 gzip
bytes. После сохранения provenance triage memo итог для двух одинаковых production-profile World,
одного pending scan и fake providers: SQL **14167 → 14764 bytes** (+597), gzip **3720 → 3857** (+137),
SQLite **249856 → 249856**. Оба восстановленных DB прошли integrity_check. Это bounded fixture,
не прогноз production growth. Observer `out/corpus/j055/state_probe_final.py`, результат
`state-sizes-final.json`, отдельный XML `state-final-tests.xml`; `-p tests.conftest` обнуляет credentials.
Точный исходник observer и SHA сохранены в
[acceptance evidence](../assets/audits/2026-10-26-scan-input/validation.json).

До исправления переиспользования четыре сценария primary/triage × current/legacy memo воспроизводили
ошибочную атрибуцию свежего input manifest старому ответу. Сейчас `TriageRecord` сохраняет своё
реальное окно, cached result сохраняет исходный manifest, legacy остаётся unknown. Paid call,
memo key и usage accounting не меняются. При подготовке legacy fixture нельзя перезаписать уже
существующий memo: repository сохраняет его immutable record_json. Поэтому legacy моделируется
старым untraced loader при первоначальном вычислении. Qualified before XML: четыре failures;
после исправления focused suite 50 passed. JSON source/part projections остались прежними.

Первый physical checker остановился на 47 JBIG2 files из-за отсутствующего внешнего decoder.
Failed run `j055-scan-current` сохранён. Исправленный checker сравнивает encoded payload,
filters/DecodeParms, включая JBIG2Globals, без декодирования; ресурсы/fonts и pixels не рендерятся.
Сначала пройден pilot всех 47 sources / 94 cover probes, затем повторён полный срез.

`j055-scan-baseline` / `j055-scan-current-v2`: **3042** observations, **0** differences и changed inputs,
**5880** exact full/triage manifests; все **30** meaningful pair checks passed. В **102** probes
scan не создаётся: прежние byte/page/unreadable outcomes сохранены, coverage для них не выдумывается.
Максимальный JSON manifest на этом корпусе — 335 bytes. После отдельного memo fix физический
loader, page reader, manifest model и AST `ScannedDocument` побайтово/структурно прежние;
это зафиксировано в `physical-scope-code.json`, новые consumer paths проверены сценариями.

Rollback probe: исходный код `14c506d` восстановил final pending batch, прочитал item и triage memo;
новые audit fields игнорируются, прежний source SHA остаётся. Старый reader при повторной
сериализации может потерять новые audit fields; frozen provider payload и схема совместимы.
Pydantic minimum уточнён до 2.12 для
[`exclude_if`](https://pydantic.dev/articles/pydantic-v2-12-release#exclude_if-field-option),
locked package versions не менялись;
`uv lock --check --offline` passed.

Окончательный полный gate: **4103 passed**, errors/failures/skips/xfails 0, 595,184 s;
strict mypy 385 файлов, Ruff check/format src/tests/tools, strict docs build/links и diff check green.
XML `out/corpus/j055/final-tests.xml`; данные и hashes — в acceptance evidence.
CI, push и deploy записываются отдельно после фактического выпуска.

<a id="j056"></a>
### J056. DA5: paid scan identity сохраняется при quiet tracking

Дата: 26 октября 2026. База J055: `56107503198d4a876e23baf4f7f63f3d5895163e`.
Статус: реализован и принят локально; полный gate и CI manifest slice green.

Дополнительное ревью J055 выявило второй путь обновления projection: `_unchanged` заменяет
selection при quiet reanalysis, не вызывая модель. Свежий `loaded.selection.scan_input` мог
заменить manifest прежнего оплаченного запроса, а у legacy record создать неподтверждённую
историю чтения. Корпусный PDF compare проверяет подготовку bytes/page maps, но эту lifecycle
границу не покрывает; поэтому появились отдельные stateful scenarios.

`_reused_selection` теперь задаёт общий инвариант для cached result и `_unchanged`: evidence
актуального наблюдения обновляется, `scan_input` остаётся от paid record, включая unknown.
Source pointer, source_checked_at и нынешние digests сохраняют прежнюю семантику применения;
created_at, фактический input, memo key и число вызовов модели не переписываются. Это точечная
защита scan provenance, не реализация всей immutable result/application history DA6.

`test_scan_input_reuse.py` проверяет current/legacy record после изменения сериализации PDF
с тем же source digest. Public `reanalyze_bill` обновляет source_checked_at, возвращает None,
сохраняет created_at и исходный manifest; calls остаётся один. До fix оба сценария failed;
после fix focused suite 52 passed, strict mypy 386 файлов и Ruff green. PDF loader, page reader,
ScanInputManifest и ScannedDocument не менялись; corpus acceptance J055 относится к тому же
физическому коду. Новые storage fields, schema или дополнительные PDF bytes не добавляются.

J055 отправлен в main. Его CI
[40286471212](https://github.com/sobolevbel/lexinform/actions/runs/40286471212)
был cancelled до завершения после обнаружения этого пути; docs/web passed, основной test job
остановлен. Автоматический Deploy relay
[40287027668](https://github.com/sobolevbel/lexinform/actions/runs/40287027668) skipped.
Это статусы CI/relay, не утверждение об исходах независимых scheduled bot runs.
Итоговый выпуск J055–J056 проверяется отдельно после нового полного gate.

J056 полный gate: **4105 passed**, errors/failures/skips/xfails 0; strict mypy 386 файлов,
Ruff check/format src/tests/tools и docs build/links green. XML `out/corpus/j056/tests.xml`;
CI manifest slice 7 observations passed, captured code snapshot сохранён.
[Acceptance evidence](../assets/audits/2026-10-26-scan-reuse/validation.json) фиксирует before/after XML,
неизменность физического PDF-кода J055 и canceled/skipped исход прежнего выпуска.

J055–J056 выпущены на `b168eda55e3ab71a649afa6e248773efccbacc8f`: CI
[40289298750](https://github.com/sobolevbel/lexinform/actions/runs/40289298750) success,
test/web/docs green, 4105 passed / 32 deselected, strict mypy 386 файлов. Deploy relay
[40289814162](https://github.com/sobolevbel/lexinform/actions/runs/40289814162) success:
`14c506d00720 → b168eda55e3a`, сервис up 45 s после restart.
Evidence: `out/corpus/j056/release.json`; independent scheduled bot runs не аудировались.

<a id="j057"></a>
### J057. DA3/DA5: scan provenance вторичных результатов и исправлений

Дата: 26 октября 2026. База: `b168eda55e3ab71a649afa6e248773efccbacc8f`.
Статус: реализован и принят локально; интеграция со свежим main и выпуск проверяются отдельно.

`AmendmentsRecord` и `SupplementRecord` получают необязательный компактный `scan_input`.
Manifest записывается вместе с request в `DigestItemMeta` при обычной постановке и отдельном
`revise_amendments`. Collect переносит именно frozen metadata до сохранения memo и revision
delivery. Синхронный compatibility-путь supplement сохраняет manifest до memoization.
Текстовые/bare/legacy records не получают null-поле; schema и raw/PDF storage не меняются.

Повторное применение memo сохраняет исходный manifest или unknown, включая смену сериализации
свежего PDF с прежней source identity. Pending items старого кода с отсутствующим manifest
успешно собираются после restore и не получают выдуманную историю. Повторный collect сохраняет
один memo, обходится без download; поправки по сканам по-прежнему требуют batch, а Senate source
evidence валидируется до применения. Успешный `/amendments` сохраняет manifest в frozen change;
ответ без подтверждения источника не создаёт исправление сообщения.

Область оставшихся работ: хранение completed derivatives и версии обработки DA3/DA5;
общая immutable result/application history DA6. Joint/name не принимают PDF, reviews сейчас
принимают `ReviewDocument.text`; им scan metadata не приписывается. Новых backend, retention,
платных вызовов и production backfill этот срез не вводит.

До изменения девять новых сценариев failed на отсутствии сохраняемого manifest. После:
focused suite **112 passed**; полный gate **4120 passed**, errors/failures/skips/xfails 0,
strict mypy 387 файлов, Ruff check/format src/tests/tools, strict docs build/links green.
Provider checks для amendments и supplement подтверждают равенство payload и стоимости у
OpenAI и Anthropic. Новые сценарии проверяют workers=1/4, pending restore, повторный collect,
legacy pending/memo, смену сериализации при reuse и frozen revision delivery.

Защитный replay Сената: **1825 файлов / 816 observations**, **701 selected**, **23 scan pending**;
**4903 mutations** и **11398 negative controls**. `j057-senate-baseline` и
`j057-senate-current` совпали: zero changed outcomes/inputs. Это saved text и проверка SHA
сохранённых raw, не новый extraction/OCR или чтение scan моделью. Девять file/AST checks
подтверждают неизменность физического PDF-кода, loaders и memo identity; J055 raw replay
остаётся свидетельством того же физического кода. CI manifest slice **7 observations passed**.
Ошибочный путь первого observer сохранён как `j057-ci: not_run`; правильный
`tests/fixtures/corpus_check` использован в `j057-ci-v2`.

Четыре production-profile fixtures с fake providers: pending amendments/supplement добавляют
по **299 SQL / 7 gzip bytes**; completed amendments **598 / 124**, completed supplement
**897 / 133**. SQLite не вырос у первых трёх fixtures, вырос на 8192 bytes у completed supplement.
Хеш сериализованного request, custom ID и estimated cost прежние. Все четыре DB прошли
integrity/foreign-key checks; исходный `b168eda` восстановил pending requests и прочитал
completed memos. Rollback может потерять audit fields при повторной сериализации старым reader;
сохранённый provider request остаётся пригодным. Это bounded measurements, не production forecast.

[Acceptance evidence](../assets/audits/2026-10-26-secondary-scan-input/validation.json) содержит
hashes XML/run manifests, размеры, повторяемый observer и проверенный rollback; локальные outputs
в `out/corpus/j057` и `out/corpus/runs/j057-*`. Backend/retention/defaults и SQLite schema прежние.

J057 интегрирован поверх `56b27d08b01fa3eb92ee1341e4d7835b98fb5028`: повторный gate
**4165 passed**, errors/failures/skips/xfails 0, strict mypy 393 файла, Ruff/docs green.
`j057-integrated-senate` сохранил все 816 observations и прежние source hashes;
`j057-integrated-ci` passed, 7 observations. Собственный коммит после rebase —
`93095a18d8d136302c7cf90ecece02d2f87d708b`, опубликован без чужой незавершённой работы.
CI [40340814136](https://github.com/sobolevbel/lexinform/actions/runs/40340814136) success,
test/web/docs green, 4165 passed / 32 deselected. Deploy relay
[40341580371](https://github.com/sobolevbel/lexinform/actions/runs/40341580371) success:
`56b27d08b01f → 93095a18d8d1`, сервис up 45 s после restart. Это CI/relay verification,
не аудит независимых scheduled bot runs.

<a id="j058"></a>
### J058. DA5: общий freeze и целостность сохранённого batch payload

Дата: 26 октября 2026. База: `93095a18d8d136302c7cf90ecece02d2f87d708b`.
Статус: реализован и принят локально; выпуск проверяется отдельно.

OpenAI и Anthropic используют один `BatchRequest.freeze` вместо двух копий обновления полей.
Метод сохраняет готовый payload, model, prompt version, reservation и SHA-256 от его UTF-8 bytes;
уже подготовленный request повторно не фиксируется. Model reader проверяет checksum при restore;
оба adapter перед reuse проверяют его также у переданного in-memory объекта. Изменённый payload
не достигает upload/submission. Поле необязательное и не сериализуется у legacy requests:
они читаются и отправляются по прежнему frozen контракту без пересборки из текущих defaults.

SHA относится к сохранённому `payload_json`, не к изменяемой routing metadata или wire framing
SDK. Pending bill linking может менять number в request envelope, не переписывая provider payload.
После подтверждённого submit действует прежняя очистка intents; этот срез не обещает сохранения
completed payload и не закрывает DA2/DA6. Provider options/schema/prompt уже входят в сохранённый
payload; PreparedInput refs и дальнейшая retention остаются частями DA5.6.

Fake backend использует тот же freeze/check контракт. Проверки включают text/scan у обоих
провайдеров, изменение одного пробела, отказ повторного freeze, несовместимую checksum без
payload, legacy requests, queued/submitting restore и отсутствие автоматического повтора
uncertain intent. SDK tests используют MockTransport и проверяют отсутствие любых HTTP calls
при повреждённом input. До внедрения девять unit cases failed; тесты не ослаблялись и не xfail.

Focused acceptance: **140 passed**, errors/failures/skips 0. Provider observer проверил
**8** сочетаний OpenAI/Anthropic × amendments/supplement × text/scan: exact payload SHA,
model, prompt version и reservation совпали с `93095a1`. AST checks подтверждают прежнюю
assembly до freeze и равенство всех передаваемых freeze значений. PDF loaders, scan manifests,
selection и AnalysisService побайтово прежние; это не новый extraction/comparison replay.
`j058-ci` passed, **7 observations**, с captured source snapshot.

Production-profile state fixtures: один pending secondary request добавляет **84 SQL / 57 gzip
bytes**, SQLite size прежний. У completed amendments/supplement прирост **0 / 0 / 0**.
Все четыре fixtures прошли integrity/foreign-key checks; baseline `93095a1` восстановил
новые pending payload и прочитал completed memos. Старый reader может удалить checksum при
повторной сериализации; первоначальные payload bytes сохраняют прежнюю совместимость.
Это bounded fixtures, не прогноз production dump growth. Observer sources, hashes и XML
сохраняются в acceptance evidence; локальные outputs — `out/corpus/j058`.

Полный gate: **4179 passed**, errors/failures/skips/xfails 0, 601,824 s; strict mypy
395 файлов, Ruff check/format src/tests/tools, strict docs build/links и diff check green.
[Acceptance evidence](../assets/audits/2026-10-26-batch-payload-identity/validation.json) содержит
before/after XML hashes, исходники повторяемых observers, unchanged assembly proof, state sizes,
rollback и captured CI slice. Новых конфигурационных значений, SQL schema или blob copies нет.

J058 выпущен на `580fe6956ceccb3b72ecb5f1e04624dd1a3bcd86`: CI
[40374767174](https://github.com/sobolevbel/lexinform/actions/runs/40374767174) success,
4179 passed / 32 deselected, test/web/docs green. Deploy relay
[40375766600](https://github.com/sobolevbel/lexinform/actions/runs/40375766600) success:
`93095a18d8d1 → 580fe6956cec`, сервис up 45 s после restart.
Evidence: `out/corpus/j058/release.json`; independent scheduled bot runs не аудировались.

<a id="j059"></a>
### J059. DA5: исходный request SHA переживает submit, collect и cleanup

Дата: 26 октября 2026. База: `580fe6956ceccb3b72ecb5f1e04624dd1a3bcd86`.
Статус: реализован и принят локально; выпуск проверяется отдельно.

После submit прежняя очистка intent удаляет его `payload_sha256`. Новый
`LlmBatchItem.from_intent` копирует этот SHA как `request_sha256` в metadata и используется
двумя writers: AnalysisService и операторским `attach-batch-intents`. Общий RecordFactory
переносит сохранённую identity в primary/reanalysis/explanation, amendments/supplement,
joint/name и document-review records. SHA не выводится из memo key, нового URL или свежего
Bill. Legacy requests и synchronous compatibility results остаются unknown; null-поле
в JSON не добавляется. Некорректный source hash не задаётся моделью в её output schema.

Memo reuse и quiet tracking сохраняют SHA исходного оплаченного input, включая unknown.
SHA также доходит до frozen amendment revision. Payload cleanup удаляет прежние большие
поля; batch metadata и актуальный Bill сохраняют input identity после удаления memo.
Reset/skip не превращает старый paid result в ответ для нынешней generation; его SHA
остаётся только в batch audit. Настройки, schema 48, lifetime memo и очистка intents прежние.

Это fingerprint input, не ID отдельного оплаченного вызова и не сохранённый request body.
Сроки хранения оригиналов/завершённых inputs и immutable computation/application history
остаются DA2/DA6; этот срез не выдаётся за их завершение. Reviews остаются выключенными
в production; enabled-профили используются только в fake scenarios для проверки consumer.

Qualified before: **18 failures**, все AttributeError отсутствующего поля. Initial fixtures
с неверным unconsumed-only query и пропущенным commission batch kind исключены из proof.
После: focused **88 passed**, quiet tracking **4 passed**, full **4203 passed**,
errors/failures/skips/xfails 0, 564,632 s; strict mypy 396 файлов, Ruff src/tests/tools,
strict docs build/links и diff check green. CLI local HTTP **21 passed**. Сценарии включают
legacy/current, workers=1/4, secondary purposes, explanation, revisions, operator attach,
superseded generation, повторный collect, expired memo и payload cleanup.

`j059-senate-baseline/current`: **816 observations** на **1825 сохранённых файлах**, zero changed
outcomes/inputs; mutation/negative controls сохранены. Это cached text и raw SHA replay,
не новое визуальное чтение scans. **20** file/AST checks подтверждают прежние extractors,
provider payload builders, prompts, pricing, input assembly и reuse policy. CI slice **7 passed**.

Production-profile fixture с одним завершённым primary: **+340 SQL / +75 gzip bytes**, 4 копии
короткого SHA в существующих snapshots/metadata. Добавление secondary intent даёт те же
**+340 SQL / +72–73 gzip** относительно baseline; после collect amendments **+508 / +129**,
supplement **+592 / +132**. SQLite size прежний во всех шести snapshots. Новая duplication —
компактные receipts, не request bodies; переход к result refs остаётся DA6. Все шесть snapshots
прошли integrity/FK checks и rollback reader `580fe69`. Payload/envelope SHA, custom ID и cost
не менялись. Это bounded fixture measurements, не forecast.

Изолированная копия `state@8a1ed977119ae20c2e1cfaecc58e38120aa108b7`
(26 октября 2026, 14:33:18 UTC): **455 bills / 166 analysis records / 104 paid memos** прочитаны
обоими readers; 36 иных memo вне проверки paid records. Integrity ok, FK errors 0,
изначальный и повторный SQL **14568448 bytes**, SHA
`a5f9a6657376c3ce561bcc33031aa94d2b7d1dc8950f3de17624d035510fa00c` совпал побайтово;
исторические SHA не придуманы. Pipeline, model calls, Telegram и production writes не запускались.

[Acceptance evidence](../assets/audits/2026-10-26-request-provenance/validation.json) содержит
XML/run hashes, captured source, snapshots, repeated observers и точные границы проверки.
Локальные outputs: `out/corpus/j059`, `out/corpus/runs/j059-*`.

J059 выпущен на `a5d92d8bcc2f5c9262efae30a3c28357b02c632d`: CI
[40380927692](https://github.com/sobolevbel/lexinform/actions/runs/40380927692) success,
4203 passed / 32 deselected, test/web/docs green. Deploy relay
[40381926131](https://github.com/sobolevbel/lexinform/actions/runs/40381926131) success:
`580fe6956cec → a5d92d8bcc2f`, сервис up 45 s после restart.
Evidence: `out/corpus/j059/release.json`; independent scheduled bot runs не аудировались.

<a id="j060"></a>
### J060. DA10: read-only doctor для request fingerprints и result refs

Дата: 26 октября 2026. База: `a5d92d8bcc2f5c9262efae30a3c28357b02c632d`.
Статус: реализован и принят локально; выпуск проверяется отдельно.

`tools/request_doctor.py` читает checkpointed SQLite copy через `mode=ro&immutable=1`,
сверяет SHA файла до/после и пишет отдельный exclusive JSON report через общий artifact writer.
Живой WAL/journal отклоняется; файл БД, старый отчёт и `.env` не открываются для записи.
Проверяются payload checksum intents, SHA batch metadata, связи result refs с batch receipts,
orphan batch item и malformed JSON/hash. Один повреждённый row не скрывает следующий.
Результаты охватывают Bill/ready/reviews/supplements, status changes, memo и frozen deliveries.
В отчёт попадают IDs/короткие findings; тексты закона, prompts и payload не копируются.

Legacy unknown — допустимое наблюдение. Observed SHA старого retained payload не становится
приписанным paid result fingerprint. Повтор input не доказывает duplicate billing/application;
считаются ссылки на результаты, не уникальные paid calls. Unmatched result ref — warning;
checksum/format/orphan ошибки дают failed/exit 1. Пустой/неполный scope даёт not_run/exit 2,
completed inventory — recorded/exit 0. Ремонт, retry, migration, SDK и production pipeline
инструмент не запускает. General doctor blobs/work/generation/receipt semantics остаётся DA10.2.

Первые corrupt fixtures держали SQLite writer открытым, и WAL guard их корректно отверг.
Fixtures теперь commit/close перед аудитом; guard не ослаблен. Повторяемая подготовка isolated
копий использует прежние SQL snapshots и отдельные новые DB. Первоначальная попытка открыть
J059 observer DB с живым WAL также отвергнута; исходник не checkpointed/не менялся doctor.

Отдельная fault injection воспроизвела WAL race: writer начинал после preflight и commit
оставался в WAL, основной файл/SHA не менялся. Прежний reader ошибочно возвращал recorded.
Теперь тот же journal guard применяется после чтения; regression получает failed с
`database_changed_during_audit`. Red XML `out/corpus/j060/race-before.xml` сохранён;
предыдущий незавершённый full gate прерван и не используется как acceptance.

Приёмка: focused **18 passed**, final full **4221 passed**, errors/failures/skips/xfails 0,
610,826 s; strict mypy 397 файлов, Ruff src/tests/tools, strict docs build/links и diff check green.
`j060-ci-accepted` passed, **7 observations**, captured source snapshot. Все **141 runtime source
files** совпали с J059: adapter/selection/prompt/runtime behavior не менялись, schema прежняя.

Три sealed copies прошли CLI doctor; before/after race-fix reports совпали, SHA всех DB остался
прежним. Тот же production SQL snapshot `state@8a1ed977119ae20c2e1cfaecc58e38120aa108b7`
дал **108 batch items / 395 result references**, все fingerprints legacy unknown, findings 0.
Это не 395 уникальных paid calls и не аудит актуального production runtime. Fake fixture нового
writer: 2 known items / 4 known result refs, findings 0, completed payloads не retained. Pending
fixture: 1 retained checksum verified, 1 known item / 3 result refs, findings 0. JSON отчёты не
содержат payload/законодательный текст; копия БД не меняется и не получает новых sidecars.

[Acceptance evidence](../assets/audits/2026-10-26-request-doctor/validation.json) сохраняет
qualified XML hashes, old-code snapshot и red WAL race, source SHA и повторяемую подготовку copies.
Локальные outputs — `out/corpus/j060`, `out/corpus/runs/j060-*`. Repair, storage backend и новые
production режимы не добавлены; остальные аспекты doctor остаются открытыми в DA10.2.

J060 выпущен на `7372d48037ae29a0f36a307702cb8a3200debfa7`: CI
[40387363090](https://github.com/sobolevbel/lexinform/actions/runs/40387363090) success,
4221 passed / 32 deselected, test/web/docs green. Deploy relay
[40388358450](https://github.com/sobolevbel/lexinform/actions/runs/40388358450) success:
`a5d92d8bcc2f → 7372d48037ae`, сервис up 45 s после restart.
Evidence: `out/corpus/j060/release.json`; independent scheduled bot runs не аудировались.

<a id="j061"></a>
### J061. DA6: неизменяемая история primary batch collection

Дата: 26 октября 2026. База: `7372d48037ae29a0f36a307702cb8a3200debfa7`.
Статус: реализован и принят локально; выпуск проверяется отдельно.

Append-only migration v49 добавляет `computation_results` и `result_applications` без backfill.
Новая primary collection хранит validated `AnalysisRecord` либо failure receipt, исходный
usage, provider item identity и schema version. Nullable `AnalysisRecord.computation_id`
сохраняется при reuse; legacy/sync его не придумывают. ID адресует `(provider, batch, custom)`
в существующем single-account scope, request SHA остаётся отдельной идентичностью входа.

Результат, collection decision, memo/ready и consumed checkpoint записываются в существующей
локальной atomic transaction. Generation/status guards и run cost acknowledgement прежние.
Superseded answer сохраняется без применения/публикации; invalid answer сохраняет usage без
выдуманного analysis. Повтор точного immutable write безопасен, replacement отклоняется.
`phase=collection` и outcome ready/superseded/failed не означают запись в `Bill.analysis` или
читательскую доставку. Последующая projection/reuse history, secondary purposes и полный
DA6 collect/apply protocol остаются открытыми.

Оригиналы файлов, исходные тексты и request/raw provider bodies не добавлены. История новых
primary results не наследует 180 дней memo retention. Временное дублирование validated answer
в legacy bill/memo/projections допустимо до перехода всех consumers/frozen deliveries на refs;
существующие snapshots сейчас не заменяются ссылками на изменяемый Bill.

Qualified before: **4 failures**, отсутствовали history repository API. Первые focused checks
после реализации: **120 passed / 1 failed**; единственный failure — fixture, которая вручную
возвращает новую базу к v20 и не удалила две новые таблицы. Fixture дополнена; deployed migrations
не менялись. Fault injection охватывает сбой до consumed checkpoint и после сохранения result
перед decision; оба должны откатывать ready/memo/history вместе.

Свежая изолированная копия `state@9d571b09a3ae5f67de441d0b503da12e412a1cde`
(26 октября 2026, 19:35:57 UTC), SQL SHA
`d5ae656f9c26b3a639566131b085b6de8b791c71ebee75b23d3953535af9910b`:
**457 bills / 166 analysis records / 141 memos** прочитаны обоими readers. Projection/memo hashes
совпали, known bill 2699 читается, integrity ok/FK errors 0. Старые results не скопированы:
обе новые таблицы пусты. Migration: SQL **+830 bytes**, gzip **+197**, SQLite **+32768**;
restore 0,339/0,336 s и dump 0,116/0,103 s baseline/current — один замер, не benchmark.
Production pipeline, модель и Telegram не запускались.

Одинаковые fake production sequences: pending, collected, cleaned, reanalysis, expiry 200 дней
и repeat. Status, paid answer (кроме нового ref), reader texts, frozen envelopes/payload SHA,
custom ID и cost совпали. Один свежий primary result вместе с DDL: **+4511 SQL / +403 gzip bytes**;
superseded: **+4234 / +424**. Два successive paid results: **+7897 / +560** до expiry;
после expiry **+7799 / +543**. Повтор не добавляет results/decisions: 2/2 fresh, 1/1 superseded.
Это bounded fixtures с небольшими ответами, не прогноз размера всех будущих результатов.

Приёмка: full **4234 passed**, errors/failures/skips/xfails 0, 600,036 s; strict mypy 399 файлов,
Ruff src/tests/tools, format, strict docs build/links и diff check green. CLI local HTTP:
**21 passed**. Init/restore свежего production dump выполнены CLI на отдельной БД, reader 2699
проверен отдельно. Предыдущий reader `7372d48` прочитал 11 новых snapshots и вернул идентичный
SQL; это read compatibility, не запуск старого writer и не schema downgrade.

`j061-senate-baseline/current`: **816 observations / 1825 сохранённых файлов**, zero changed
outcomes/inputs, прежние mutation/negative controls; это cached text/raw SHA replay, не новое
визуальное чтение scans. Все 48 deployed migrations неизменны, добавлена одна новая. **20**
file/AST checks подтверждают прежние extraction/input assembly, prompts, schemas, pricing и
reuse policy. `j061-ci`: **7 passed observations**, captured source snapshot.

[Acceptance evidence](../assets/audits/2026-10-26-computation-history/validation.json) содержит
qualified XML hashes, source boundaries, production/state measurements и повторяемые observers.
Локальные outputs: `out/corpus/j061`, registry runs `j061-*`. DA6 остаётся частично открытым;
следующий шаг — отдельные факты projection/reuse, затем остальные consumers и purposes.

J061 выпущен на `d235aa69e4c7a602f2506ecfe6808fb85363f111`: CI
[40394214244](https://github.com/sobolevbel/lexinform/actions/runs/40394214244) success,
4234 passed / 32 deselected, test/web/docs green. Deploy relay
[40395201863](https://github.com/sobolevbel/lexinform/actions/runs/40395201863) success:
`7372d48037ae → d235aa69e4c7`, сервис up 45 s после restart.
Evidence: `out/corpus/j061/release.json`; independent scheduled bot runs не аудировались.

<a id="j062"></a>
### J062. DA6: применение primary result отдельно от collection

Дата: 26 октября 2026. База: `d235aa69e4c7a602f2506ecfe6808fb85363f111`.
Статус: реализован и принят локально; выпуск проверяется отдельно.

`AnalysisService._persist` и direct `reanalyze_bill` сохраняют `phase=projection`, outcome applied
в одной atomic transaction с `Bill.analysis`. Collection ready остаётся отдельным фактом.
Receipt хранит generation/status дела до записи и причину batch_result/memo_reuse; payload
не дублируется. Новая SQL-миграция не нужна, схема v49 прежняя. Phase/outcome/reason проверяются
при чтении; точный повтор application identity с другой датой не заменяет первоначальный timestamp.

Сбой receipt после записи analysis откатывает всю projection, сохраняя ready result для retry.
Reanalysis preparation не утверждает применение: receipt появляется после фактической записи.
Quiet source refresh и synchronous/legacy record без computation ref ничего не добавляют.
Reset + memo reuse создаёт новый короткий application ref для новой generation, без нового
paid result, токенов или provider request. Original usage в computation history прежний.

Это ограниченный primary-service slice. Tracking/linking, которые применяют `prepare_reanalysis`
в собственных observation/delivery transactions, ещё не перенесены; отсутствие их receipt
не выдаётся за неприменение. Secondary purposes, source applicability history и reader delivery
остаются отдельными частями DA6/DA7. Никаких retroactive application rows для старых дел.

Qualified before: **5 failures** — отсутствующие projection receipts и rollback boundary.
После: focused **36 passed**, включая отдельное collection/apply, workers=1/4, crash/retry,
повтор с новой датой, reset/reuse, uncommitted reanalysis preparation и quiet/legacy paths.
Ранний focused набор: **40 passed**; оба XML сохранены отдельно.

Одинаковые 11 state snapshots до/после сохранили status, answer, reader text, request envelopes,
payload SHA, custom ID и cost. Одна application: **+439 SQL / +39 gzip bytes**, SQLite прежний;
два применения successive paid results: **+880 / +60**. Superseded и pending snapshots:
**0 bytes** прироста. После expiry/repeat число строк остаётся 2 results / 4 decisions для fresh,
1 / 1 для superseded. Это bounded fixtures, не прогноз общего роста.

На реальных current projections того же production snapshot: **166 AnalysisRecord**, суммарно
**556116 JSON bytes / 108393 gzip**, median **2169**, p90 **5237**, p95 **6818**, max **25507 bytes**.
Selection metadata занимает 122781 bytes. Это размеры существующих текущих records, не число
уникальных paid calls, не восстановленная история и не прогноз SQL/history growth. Observer
читает sealed copy без записи; source/record text в report не копируются.

Тот же pinned production snapshot `9d571b09a3ae5f67de441d0b503da12e412a1cde` восстановлен
без pipeline/model/Telegram: **457 bills / 166 analysis records / 141 memos**, projection/memo
hashes и SQL SHA совпали с J061; **0 bytes** прироста. Существующие данные не backfilled.

Приёмка: full **4241 passed**, errors/failures/skips/xfails 0, 574,666 s; strict mypy 400 файлов,
Ruff src/tests/tools, format, strict docs build/links и diff check green. CLI local HTTP:
**21 passed**. Все 49 migrations прежние. **25** file/AST checks подтверждают неизменность
extraction/input assembly, prompts/schemas/pricing, reuse и batch collection/accounting.

`j062-senate-current` против принятого J061: **816 observations / 1825 файлов**, zero changed
outcomes/inputs, прежние controls; scope — cached text/raw SHA, не визуальное чтение scans.
`j062-ci`: **7 passed observations**, captured source snapshot.

[Acceptance evidence](../assets/audits/2026-10-26-result-applications/validation.json) содержит
qualified XML/source hashes, production/state diffs и observer размеров реальных records.
Локальные outputs: `out/corpus/j062`, registry runs `j062-*`. Следующие consumers — tracking и
linking с их исходными atomic observation/delivery boundaries; DA6 целиком не закрыт.

J062 выпущен на `5d10eb44a65b2ba6067bdb0849cabc89f0b3a428`: CI
[40396407179](https://github.com/sobolevbel/lexinform/actions/runs/40396407179) success,
4241 passed / 32 deselected, test/web/docs green. Deploy relay
[40397390571](https://github.com/sobolevbel/lexinform/actions/runs/40397390571) success:
`d235aa69e4c7 → 5d10eb44a65b`, сервис up 45 s после restart.
Evidence: `out/corpus/j062/release.json`; independent scheduled bot runs не аудировались.

<a id="j063"></a>
### J063. DA6: общий application command для primary и tracking/linking

Дата: 26 октября 2026. База: `5d10eb44a65b2ba6067bdb0849cabc89f0b3a428`.
Статус: реализован и принят локально; выпуск проверяется отдельно.

Шесть service writes основного анализа теперь используют `analysis_applications.apply_analysis`:
primary persist/reanalysis, RCL watcher, wykaz handover и два места RPW/RCL→druk linker.
Только общий command вызывает repository `save_analysis`. Он читает фактическую целевую строку
перед записью: receipt получает generation/status нового владельца, а не переносит generation
предшественника. Existing result ref при tracking/linking получает причину retained_result;
оплаченный ответ и его ID не копируются в новый computation row. Primary batch/memo причины
сохранены. Отсутствующий target/legacy ref не создаёт application; quiet same-ref refresh не
добавляет trace. Изменения источника, очередей, generation guards и reader policy не добавлены.

Command сохраняет analysis/receipt через nested savepoint; существующая outer transaction RCL
или linking продолжает владеть observation и delivery work. Fault injection перед receipt и
после receipt при observation checkpoint подтверждает rollback analysis/decision/status change
вместе; collected paid result остаётся для retry без нового запроса. Это не перенос сетевой
работы в транзакцию. Существующий порядок Sejm reanalysis до observation checkpoint сохранён;
его консолидация — отдельная незакрытая часть DA6.2. Rollover identity relocation и secondary
purposes также остаются открытыми.

Qualified before: **6 failures**, нет applications при linking/RCL, fault не достигает receipt.
Initial focused **26 passed**; расширенный набор **116 passed**, включая workers=1/4,
RPW→druk с прежним paid ID, wykaz→RCL, RCL reanalysis, receipt/checkpoint crashes и повтор.
При наследовании оба новых владельца получили собственную target generation 0; исходный
computation остался неизменным. Все XML сохранены.

Восемь tracking snapshots baseline/current: Bill hashes, reader text hashes, request envelopes,
payload SHA/cost и число computations совпали. SQL после удаления только application INSERTs
идентичен. RPW→druk: **+436 SQL / +24 gzip bytes**, wykaz→RCL: **+452 / +26**, RCL apply:
**+460 / +30**. Повтор не увеличивает decisions. Ещё 11 прежних primary/superseded snapshots
совпали побайтово, включая историю. Это bounded fixtures, без input/result body duplication.
Все 16 tracking SQLite copies прошли integrity/FK; размер SQLite не вырос. Повторяемый
observer дал идентичные DB SHA/размеры. Validation результата означает output schema,
не подтверждение legal correctness/completeness; DA9 не выполнялся.

Pinned production snapshot `9d571b09a3ae5f67de441d0b503da12e412a1cde`: **457 bills /
166 analysis records / 141 memos**, те же projection/memo/SQL hashes, **0 bytes** прироста,
integrity ok/FK errors 0. Pipeline/model/Telegram и backfill не запускались.

Приёмка: full **4251 passed**, errors/failures/skips/xfails 0, 662,196 s; strict mypy 402 файла,
Ruff src/tests/tools, format, strict docs build/links и diff check green. CLI local HTTP:
**21 passed**. Все 49 migrations прежние. **25** file/AST checks сохраняют extraction/input,
prompts/schemas/pricing, reuse и batch collection/accounting; source graph подтверждает одного
service owner низкоуровневого `save_analysis`.

`j063-senate-current` против J062: **816 observations / 1825 файлов**, zero changed outcomes/inputs,
прежние controls; это cached text/raw SHA replay, не новое визуальное чтение scans. `j063-ci`:
**7 passed observations**, captured source snapshot.

[Acceptance evidence](../assets/audits/2026-10-26-shared-analysis-application/validation.json) содержит
qualified XML/source hashes, before/after profiles, SQLite integrity и runnable observers.
Локальные outputs: `out/corpus/j063`, registry runs `j063-*`. DA6 остаётся открытым для
Sejm observation/application consolidation, rollover identity и secondary results/applications.

J063 выпущен на `d35ff170ee387f08401c382e443160c82e128d53`: CI
[40416466396](https://github.com/sobolevbel/lexinform/actions/runs/40416466396) success,
4251 passed / 32 deselected, test/web/docs green. Deploy relay
[40417049937](https://github.com/sobolevbel/lexinform/actions/runs/40417049937) success:
`5d10eb44a65b → d35ff170ee38`, сервис up 45 s после restart.
Evidence сохранён в прежнем worktree: `out/corpus/j063/release.json`.

<a id="j064"></a>
### J064. DA6: Sejm analysis и observation/delivery checkpoint атомарны

Дата: 27 октября 2026. База: `d35ff170ee387f08401c382e443160c82e128d53`.
Статус: реализован и принят локально; выпуск проверяется отдельно.

Цель завершить весь применимый план создана явно по запросу пользователя. Прежний managed
worktree оказался read-only в текущем mount; он сохранён с evidence. Чистый основной checkout
fast-forward до опубликованного main, работа продолжается в `codex/document-architecture-finish`.

Sejm tracker вызывает `prepare_reanalysis` до checkpoint; подготовленный Bill используется
для fingerprint/содержания события, но текущая проекция ещё не меняется. Общий `apply_analysis`,
authors, processed observation, status change и frozen delivery work фиксируются в одной
существующей transaction. Новые schema/settings/payload/model calls не добавлены. Ready record
и memo остаются при secondary waiting или checkpoint failure. Счётчик reanalyzed относится к
успешному применению; исходная оплата и токены по-прежнему учитываются отдельным call ledger.

Qualified before: **4 failures**, каждый воспроизводит сохранение нового анализа после ошибки
delivery checkpoint. После fix targeted **105 passed**, expanded accepted **15 passed**:
workers=1/4, batch и sync compatibility, rollback/retry без повторной оплаты, ready result при
ожидании OSR, изменившийся summary и восстановление pre-upgrade частичного состояния.
Старый incident-3141 scenario усилен: waiting сохраняет старый analysis/REANALYSIS_READY;
счётчик переносится с waiting на успешное применение, immaterial reanalysis по-прежнему не news.

Before/after observer охватывает immediate sync/batch и simultaneous primary+OSR. Immediate
snapshots совпали побайтово. При ожидании меняются именно отложенные projection/receipt/counter;
final answer и processed observation совпадают. Старая версия могла отправить отчёт со старым
summary, затем отдельную «Новую версию текста». Новая собирает одно согласованное сообщение.
При изменившемся summary baseline также повторно запрашивал OSR с новым контекстом: **4 → 3**
fake requests; первые три frozen envelopes совпали. Это принятые изменения reader timing/grouping
и immutable comparison context, не утверждение о неизменности всех промежуточных состояний.

Первый observer ошибочно задавал общий fake script для всех purposes, возвращая Analysis вместо
supplement; его v1 outputs исключены из acceptance. В v2 ответ ограничен purpose reanalysis.
Первоначальный full gate прерван после добавления двух summary regressions (exit 130); принят
только final full **4261 passed**, errors/failures/skips/xfails 0, 590,867 s. Strict mypy 403 файла,
Ruff src/tests/tools, format, strict docs/links и diff check green; local HTTP **21 passed**.

Production snapshot `state@98d0bd3267eb8c110945f8fb2d44b1414c58d211`
(27 октября, 00:04:41 UTC), SQL SHA
`fc267831b0a010162299b1bd500838466a6f4f6d30618d178f66ade9d416a8da`:
**457 bills / 166 analysis records / 141 memos**, schema v49, обе history tables ещё пусты.
Baseline/current restore дали идентичный SQL, projection/memo hashes и размеры, integrity ok/FK 0.
Pipeline, model calls и Telegram против этого state не запускались.

Process replay **3294 observations**, Senate replay **816 observations / 1825 файлов**:
zero changed observations/inputs, missing inputs 0. Это защищённые прежние metadata/cached-text
профили с их exclusions, не новые human labels или полное чтение scans. `j064-ci-accepted`:
**7 passed observations**, captured source; корпусные wrappers выполнялись на том же runtime,
а после них расширился только test dataset.

[Acceptance evidence](../assets/audits/2026-10-27-sejm-analysis-checkpoint/validation.json) содержит
XML/source hashes, повторяемый observer, изменившиеся решения и production/corpus profiles.
Локальные outputs: `out/corpus/j064`, registry runs `j064-*`. Следующие части DA6 — secondary
result history/applications и rollover identity; цель всего плана остаётся active.

J064 выпущен: `beba3f2b5754de1217fb89345250ff18eb80088f`,
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40435736072) success:
**4261 passed / 32 deselected**, strict mypy 403 файла, lint green; web/docs jobs success.
[Deploy relay](https://github.com/sobolevbel/lexinform/actions/runs/40436268305) success:
лог подтверждает exact COMMIT, переход `d35ff170ee38 → beba3f2b5754` и up 45 s после restart.
Это проверка CI/relay; ручной production pipeline и end-to-end reader delivery не запускались.

<a id="j065"></a>
### J065. DA6: совместимый reader вторичных computation results

Дата: 27 октября 2026. База: `beba3f2b5754de1217fb89345250ff18eb80088f`.
Статус: реализован и принят локально; выпуск проверяется отдельно, writer — следующий этап.

Reader принимает schema v2 для amendments, supplement, joint, name, explanation и трёх review
purposes. У primary schema v1 и обязательный generation прежние; secondary не получает
выдуманный primary generation. Тип record соответствует purpose, а review/context дополнительно
совпадают по purpose; bare supplement без digest не считается валидированным paid answer.
Ссылки result/request проверяются вместе. Failure сохраняет usage без выдуманного ответа.

Nullable `computation_id` добавлен к secondary/review records и отсутствует в старом JSON.
Новые service writes, импорты истории и миграции не включены: это совместимый reader для
развёртывания перед writer, чтобы откат следующего этапа имел читающую версию. Production
по-прежнему записывает только primary history; source selection, extraction, prompts,
provider payload/schema/cost и политика reuse не меняются.

Qualified before: **17 failures** на старом reader. Before/after observer дал одинаковые JSON
всех десяти legacy records и primary computation schema v1. На том же pinned production
snapshot из J064 restore/read дал идентичные SQL/projection/memo hashes и размеры:
**457 bills / 166 analyses / 141 memos**, schema49, integrity ok/FK 0. Pipeline/LLM/Telegram
не запускались. Process replay **3294 observations**, zero changed decisions/inputs,
missing inputs 0; CI corpus slice **7 passed**. Эти profiles сохраняют прежние exclusions,
не являются independent legal labels и не доказывают полноту scans.

Acceptance: targeted **48 passed**, full **4296 passed**, errors/failures/skips/xfails 0,
643,944 s; strict mypy 404 файла, Ruff src/tests/tools, format, strict docs/links и diff check
green. Local HTTP не запускался: CLI/wiring/network adapters не менялись. Исходный docs build
выявил отсутствующий explicit anchor J065; после его добавления strict build и built links green.

[Acceptance evidence](../assets/audits/2026-10-27-secondary-result-reader/validation.json)
содержит XML/source/serialization hashes, runnable observers, corpus manifests и production
profiles. 140 runtime files совпали побайтово, AST 12 provider answer/context schemas прежний.
Локальные outputs: `out/corpus/j065`, registry runs `j065-*`. Следующий этап включает writer
без full input contexts в history; цель всего плана остаётся active.

J065 выпущен: `54aad3d90d350958ccda544e3b2516f562817856`,
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40437670571) success:
**4296 passed / 32 deselected**, strict mypy 404 файла, lint green; web/docs jobs success.
[Deploy relay](https://github.com/sobolevbel/lexinform/actions/runs/40438358632) success:
exact COMMIT, `beba3f2b5754 → 54aad3d90d35`, сервис up 45 s после restart.
Evidence: `out/corpus/j065/release.json`. Ручной production pipeline не запускался.

<a id="j066"></a>
### J066. DA6: immutable secondary results с memo/consumed checkpoint

Дата: 27 октября 2026. База: `54aad3d90d350958ccda544e3b2516f562817856`.
Статус: реализован и принят локально; выпуск проверяется отдельно.

Новые collections amendments/supplement/joint/name/explanation сохраняют normalized result
или failure, исходный usage, provider item ID и request SHA. Конструирование общей части
primary/secondary вынесено в `_retain_computation`; RecordFactory принимает nullable result ref.
Schema v1 и primary decisions прежние, secondary использует v2 с generation=None. Новых
SQL-миграций нет. History, memo, consumed и frozen amendment revision фиксируются одной
существующей transaction; network, paid usage acknowledgement и provider cleanup не перенесены
в неё. Crash не оставляет ни частичного memo, ни revision без history, ни consumed без ответа.

Новый DigestItemMeta сохраняет известный URL исходного документа и номер supplement до submit.
Они не входят в provider payload и не выводятся из позднего URL при collect. Старый pending
с отсутствующими metadata/request SHA собирается при недоступном download; неизвестное
происхождение остаётся неизвестным. Memo reuse сохраняет ID и исходный history usage, а
возвращённый cached record получает нулевые новые токены. Bill status/generation не меняются.

Это dual write пяти purposes: secondary application receipts ещё не добавлены. Review history
writer не включён, чтобы не копировать full source context в новую таблицу; history refs для
него — следующий отдельный этап. Raw/archive/backend, старые completed results и значения
settings не меняются. Минимальная версия для rollback — совместимый reader J065.

Qualified before: **14 failures** показывают отсутствие secondary history. Targeted accepted
**78 passed**: workers=1/4, dump/restore, повторный collect/reuse, memo cleanup, отсутствие
выдуманного legacy provenance, original usage при failure, три места обрыва collect и атомарная
amendment revision. Existing request-provenance, secondary scan и amendment-command scenarios
включены; normalized answers, источники и reader behavior не меняются.

Пять before/after сценариев дали одинаковые frozen requests/custom IDs/cost, ответы после
reuse, report usage, current Bill и primary history. Увеличение SQL после memo cleanup:
**1357–2959 bytes** на один новый secondary result; gzip **117–287 bytes**, SQLite +4096 bytes.
Peak dual write: SQL **1451–3053 bytes**, gzip **133–140 bytes**, SQLite +4096–8192 bytes.
Это маленькие fixtures, не линейный прогноз на corpus/production. Новые URL/number metadata
добавляют 46/71 SQL bytes при подготовке двух документных purposes. Сырые файлы, provider
response body и input text в history не копируются.

Observer v1 остановился на отсутствующем поле RunReport; acceptance использует исправленный
pytest observer с autouse credential isolation. Промежуточные v2 profiles расширены измерением
SQLite и dump/restore timings; для acceptance сохранены финальные outputs и исходник observer.
Production snapshot J064 после restore/read даёт те же SQL/projection/memo hashes и размеры,
schema49, integrity ok/FK 0. Pipeline/provider/Telegram против production copy не запускались.
Process corpus **3294 observations**, zero changed decisions/inputs, missing inputs 0;
CI slice **7 passed**, оба manifests проверены. Это прежние metadata/cached/synthetic profiles
с их exclusions, не новые human labels или доказательство полноты extraction.

Acceptance: full **4316 passed**, errors/failures/skips/xfails 0, 631,312 s; strict mypy
405 файлов, Ruff src/tests/tools, format, strict docs/links и diff check green. CLI/wiring и
network adapters прежние, local HTTP не требовался. Десять legacy record serializations и
primary schema v1 computation совпали побайтово с baseline.

Rollback fixture создал 15 новых snapshots: pending, settled и cleaned для пяти purposes.
Опубликованный reader J065 восстановил их, прочёл все results/memo refs и pending items;
повторный dump каждого совпал побайтово. Это проверка чтения/restore, не выполнение batch
или доставка старой версией. Оба observers и SHA snapshot входят в evidence.

[Acceptance evidence](../assets/audits/2026-10-27-secondary-computation-history/validation.json)
содержит source/XML hashes, before/after metrics, runnable observers, rollback snapshots hashes,
corpus manifests и production profiles. 140 runtime files совпали побайтово; provider answer
schemas прежние. Локальные outputs: `out/corpus/j066`, registry runs `j066-*`.
Следующие части DA6 — compact review result reader/writer, secondary applications и rollover;
цель всего плана остаётся active.

J066 выпущен: `0799a9242b9f82b4ca7179baef4ce91cde676d6f`,
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40439627804) success:
**4316 passed / 32 deselected**, strict mypy 405 файлов, lint green; web/docs jobs success.
[Deploy relay](https://github.com/sobolevbel/lexinform/actions/runs/40440330475) success:
exact COMMIT, `54aad3d90d35 → 0799a9242b9f`, сервис up 45 s после restart.
Evidence: `out/corpus/j066/release.json`. Ручной production pipeline не запускался.

<a id="j067"></a>
### J067. DA6: compact review result и отдельная ссылка на input

Дата: 27 октября 2026. База: `0799a9242b9f82b4ca7179baef4ce91cde676d6f`.
Статус: reader реализован и принят локально; выпуск проверяется отдельно, review writer выключен.

Schema v3 разделяет normalized `ReviewResultRecord` и `ComputationResult.review_input`.
В результате остаются проверенный answer, original usage/model/prompt/request/result refs;
input reference хранит исходный context key, owner/purpose, URL, text/normalized/file hashes,
роли, даты, authority и completeness документов. Полный input text в эти DTO не принимается.
Unknown/missing поля и исходный completeness сохраняются; hash и полнота не выводятся заново.
Это ссылки и метаданные, не полный ReviewContext для повторной проверки цитат.

Known input сохраняется независимо от наличия валидного answer. Ошибка с отсутствующим
legacy context допускает review_input=None; положительный compact result требует input ref.
Purpose, owner, format version и request/result refs проверяются согласованно. Normalizer
требует известный computation ID и не копирует projection-only baseline/source/check fields.
Primary schema v1 и secondary v2 не меняются; новый nullable field в старом JSON отсутствует.

Первоначальный prototype держал input внутри answer; до выпуска reference вынесен в computation,
чтобы failures не теряли provenance. Первый full run прерван (exit130); provisional before
fixtures и profiles раннего формата исключены из acceptance. Final schema probe на одной и
той же raw JSON: baseline **3 failures**, current **3 passed**. Targeted **93 passed**.

Synthetic size fixture: **310000 bytes** input text, normalized v3 computation **1769–1785 bytes**
JSON для трёх review purposes. Сравниваемый v2 full-context формат поддерживался reader, но его
runtime writer никогда не включался: это сравнение представлений, не экономия в production.
Answer/usage/paid result ID/request SHA совпали; SQL v3 не содержит исходный текст. Production
snapshot J064 восстановлен без изменения SQL/projection/memo hashes и размеров, integrity ok/FK 0.
Pipeline/provider/Telegram против этой копии не запускались.

Current reader также прочёл **15** сохранённых J066 pending/settled/cleaned snapshots: results,
memo refs, pending items и повторные dumps совпали побайтово. Corpus CI **7 passed**, process
**3294 observations**, profiles сохраняют прежние exclusions и не добавляют human legal labels.
Новая SQL-миграция, provider payload/schema/model/options и review settings не включены.
Acceptance: full **4341 passed**, errors/failures/skips/xfails 0, 620,635 s; strict mypy
406 файлов, Ruff src/tests/tools, format, strict docs/links и diff check green. Local HTTP не
нужен: CLI/wiring/network adapters прежние. Legacy JSON десяти records и primary computation
v1 совпал побайтово; 141 runtime file прежний, AST provider answer/context schemas не менялся.

[Acceptance evidence](../assets/audits/2026-10-27-compact-review-reader/validation.json)
содержит final schema probe/XML/source/serialization hashes, source-pinned corpus manifests,
production/previous-snapshot checks и повторяемый synthetic size observer с ограничениями.
Локальные outputs: `out/corpus/j067`, registry runs `j067-*`. Следующий этап — review writer
с сохранением known input и при failure; цель всего плана остаётся active.

J067 выпущен: `d4da77430316a7937157ff03afebfcaf15bee882`,
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40441924776) success:
**4341 passed / 32 deselected**, strict mypy 406 файлов, lint green; web/docs jobs success.
[Deploy relay](https://github.com/sobolevbel/lexinform/actions/runs/40442408900) success:
exact COMMIT, `0799a9242b9f → d4da77430316`, сервис up 45 s после restart.
Evidence: `out/corpus/j067/release.json`. Ручной production pipeline не запускался.

<a id="j068"></a>
### J068. DA6: compact review history в collection checkpoint

Дата: 27 октября 2026. База: `d4da77430316a7937157ff03afebfcaf15bee882`.
Статус: реализован и принят локально; выпуск проверяется отдельно.

Collection consultation_review/commission_review/act_application сохраняет compact answer и
отдельный input ref в immutable history schema v3. Полный источник и raw response body в новую
таблицу не копируются; detailed record остаётся в прежнем memo для действующих consumers.
Новый result ref проходит через memo reuse, исходные paid tokens/model/prompt/request SHA не
обнуляются в history. Current API возвращает detailed record с нулевыми новыми токенами.

Input строится из frozen item metadata, независимо от валидности answer. Known source refs
сохраняются при failure; отсутствие legacy review_context остаётся None. History, memo и
consumed checkpoint — одна existing transaction; network, token acknowledgement и provider
cleanup сохраняют свои границы. Primary schema v1 и пять secondary v2 прежние. Secondary
application receipts не добавлены: запись memo не объявляется применением к Bill/публикации.

Настройки reviews, submit/pause policy, LLM schema/payload/model/pricing и citation validation
не менялись. Новые act submissions по-прежнему остановлены; тесты отдельно seed уже отправленный
fake legacy job через public repository/backend APIs и проверяют collect без act/effective-rule
публикаций. Новой SQL-миграции и legacy backfill нет. Rollback reader — J067.

Qualified before: **21 failures**; targeted **145 passed**. Новые сценарии: оба providers,
workers=1/4, repeated collect, dump/restore, original paid usage при memo reuse/cleanup, known и
unknown input при failed answer, три места обрыва checkpoint. Existing review/citation/pause и
non-review secondary history scenarios включены.

Шесть World before/after cases: одинаковые frozen requests/custom IDs/cost, validated answers,
report usage, current Bill и submit counts. Source refs сохранили исходные metadata, а
normalized history не имеет full-context/text полей. Прирост SQL на collect **2410–4323 bytes**,
gzip **227–273 bytes**, SQLite **0–4096 bytes**. После memo cleanup SQL **2316–4229 bytes**,
gzip **571–1116 bytes**, SQLite **0–4096 bytes**. Это небольшие fixtures с meaningful citations,
не прогноз production growth; script replay timings не измеряют network checkpoint latency.

J067 reader восстановил **18** новых pending/settled/cleaned snapshots с v3 history, memo refs
и pending items; каждый повторный dump побайтово одинаков. Pipeline/provider/Telegram против
production copy не запускались.

Acceptance: full **4362 passed**, errors/failures/skips/xfails 0, 581,895 s; strict mypy
407 файлов, Ruff src/tests/tools, format, strict docs/links и diff check green. Local HTTP не
нужен: CLI/wiring/network adapters прежние. Production snapshot J064: идентичные SQL,
projection/memo hashes, размеры и schema49, integrity ok/FK 0. Legacy JSON десяти records и
primary computation v1 совпал побайтово. Corpus CI **7 passed**, process **3294 observations**,
zero changed decisions/inputs, missing inputs 0; оба manifests проверены. Profiles имеют
прежние metadata/cached/synthetic exclusions и не добавляют human labels.

[Acceptance evidence](../assets/audits/2026-10-27-review-computation-history/validation.json)
содержит XML/source/serialization hashes, six-flow metrics и observer, rollback snapshots hashes,
corpus manifests и production profiles. 142 runtime files прежние; AST provider answer/context
schemas не менялся. Локальные outputs: `out/corpus/j068`, registry runs `j068-*`.
Следующие части DA6 — secondary application targets/receipts, rollover и остальные consumers;
цель всего плана остаётся active.

J068 выпущен: `ada1415da9451694a1ca5dc3f1dbf8cfc8d201bb`,
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40444745837) success:
**4362 passed / 32 deselected**, strict mypy 407 файлов, lint green; web/docs jobs success.
[Deploy relay](https://github.com/sobolevbel/lexinform/actions/runs/40445434684) success:
exact COMMIT, `d4da77430316 → ada1415da945`, сервис up 45 s после restart.
Evidence: `out/corpus/j068/release.json`. Ручной production pipeline не запускался.

<a id="j069"></a>
### J069. DA6: reader application targets без выдуманного primary generation

Дата: 27 октября 2026. База: `ada1415da9451694a1ca5dc3f1dbf8cfc8d201bb`.
Статус: реализован и принят локально; выпуск проверяется отдельно.

Применение результата к нескольким событиям нельзя адресовать одним primary generation.
`ResultApplicationTarget` хранит namespace/ref; это metadata адреса, не задания исполнителю.
Неизвестный namespace читается без потери identity. Explicit target требует generation=None,
phase=projection и outcome=applied. Primary receipt оставляет прежний generation и JSON без
нового nullable поля. Ни collect, ни memo lookup не объявляются применением.

Append-only migration v50 перестраивает одну existing таблицу; прежние **49** scripts побайтово
прежние. Target хранится один раз в application_json, отдельные columns не добавлены. Два
partial unique expression indexes разделяют primary generation identity и target identity;
CHECK требует полный target при NULL generation. Поэтому SQLite NULL uniqueness не даёт
записать duplicate receipt. Старые primary JSON и порядок сохраняются; исторические target refs
не backfilled. Скрытый rowid v49 не сериализуется его dump: сохраняется фактический порядок,
а не выдуманные исходные номера. В v50 новый local id служит только порядку хранения.

На этом этапе production producers по-прежнему пишут только primary receipts. Инфраструктура
DA6.1 закончена; actual secondary applications остаются DA6.2/DA6.3. Подключение каждого writer
будет отдельным checkpoint change, после выпуска reader. Namespace/ref выбирает владеющий
сервис; data addresses никогда не используются как инструкции.

Qualified before **6 failures**, targeted accepted **88 passed**. Новые проверки: несколько
targets и namespaces у одного результата, repeated receipt с сохранением времени, dump/restore,
неизвестный namespace, запрет borrowed generation/collection target, SQL uniqueness при NULL,
SQL CHECK на incomplete targets и upgrade v49 primary rows. Legacy-v1 migration test расширен.
Expanded test сначала ошибочно ожидал hidden rowids из старого dump; исправлен контракт на
сохранение JSON и порядка. Финальный targeted run green.

**33** J066/J068 pending/settled/cleaned snapshots: общие данные всех таблиц, включая original
application JSON, совпали. SQL delta **969–977 bytes**, gzip **183–220 bytes**, SQLite +4096 bytes.
Old runtime v49 завершил pending primary batch на v50: один request/card, две primary receipts,
без errors. Это bridge до появления новых target producers, не обещание rollback v49 после их
включения. Первоначальный bridge observer использовал неканонический version footer; принят
исправленный observer с точным dump trailer и проверкой загруженного code root.

Full **4374 passed**, errors/failures/skips/xfails 0, **624,944 s**; strict mypy 408 файлов, Ruff
src/tests/tools и format green. Local HTTP CLI **21 passed**. После gate выполнен свежий fetch
state, `origin/state == FETCH_HEAD == 98d0bd3267eb8c110945f8fb2d44b1414c58d211` (00:04:41 UTC).
Копии восстановлены в `/private/tmp`: v49 baseline и v50 current. **457 bills / 166 analyses /
141 memos**, unchanged projection/memo hashes, known bill2699=analyzed, integrity ok/FK 0.
SQL +969 bytes, gzip +243 bytes, SQLite +4096 bytes. Schema50: **19 tables / 32 explicit indexes
(17 unique, 15 ordinary) / 6 FKs**. Pipeline, model calls и Telegram против production copy
не запускались; source, settings и внешнее хранилище не менялись.

Corpus CI **7 passed**, process **3294 observations**: zero changed decisions/inputs, missing
inputs 0; manifests verified. Strict docs/links и diff check green. 140 runtime files прежние;
service/provider/selection code этим срезом не меняется.

[Acceptance evidence](../assets/audits/2026-10-27-result-application-targets/validation.json)
содержит XML/source hashes, неизменность первых 49 migrations, SQL v50, 33 snapshot profiles,
old-runtime bridge, fresh production restore/read и повторяемые observers с exclusions.
DA6.1 отмечен выполненным как infrastructure contract; DA6.2/DA6.3 сохраняют подключение
secondary projection producers. Локальные outputs: `out/corpus/j069`, registry runs `j069-*`.
План: **74 задачи, 12 выполнены, 62 не закрыты**; среди оставшихся есть условные/deferred пункты.
Цель всего плана остаётся active.

J069 выпущен: `ff890aa334f61eec7c49eaa754ca0c6aa9529a28`,
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40450211690) success:
**4374 passed / 32 deselected**, strict mypy 408 файлов, lint/web/docs green.
[Deploy relay](https://github.com/sobolevbel/lexinform/actions/runs/40450874041) success:
exact COMMIT, `ada1415da945 → ff890aa334f6`, сервис up 45 s после restart.
Evidence: `out/corpus/j069/release.json`. Production processing вручную не запускался.

<a id="j070"></a>
### J070. DA6: applications к StatusChange и поздним amendments

Дата: 27 октября 2026. База: `ff890aa334f61eec7c49eaa754ca0c6aa9529a28`.
Статус: реализован и принят локально; выпуск проверяется отдельно.

`Poster.record_change` сохраняет новое событие и короткие receipts оплаченных amendments и
supplements в одной транзакции. Target использует namespace `status_change` и фактический ID
сохранённого события, без primary generation. Один оплаченный результат может примениться
к двум событиям без второго computation/request. Повтор fingerprint не создаёт новый receipt.

Поздние amendments используют тот же command boundary внутри существующего checkpoint
с revision delivery work и завершением document work. Записывается применение только новых
amendments; старые supplements в этом событии не backfilled. Legacy records без computation ref
сохраняют прежнее поведение. Receipt не означает доставку и не заменяет content versions DA7.

Qualified before: **8 failures / 1 passed**. Targeted **45 passed**: workers=1/4, несколько
событий с одним результатом, dump/restore, duplicate fingerprint, receipt fault rollback,
outer observation/delivery fault rollback, поздний batch после исчезновения source URL и
запрет implicit backfill старого supplement.

Шесть World before/after cases сохраняют exact requests, отчёты, reader messages и данные
общих таблиц. Каждый новый receipt: SQL +485 bytes, gzip +56–89 bytes, SQLite delta 0 в этих
fixtures; это не прогноз production growth. J069 reader восстановил все шесть новых snapshots
и сделал побайтово одинаковый dump. Минимальный rollback reader — J069/schema50; новой миграции
нет. Source selection/extraction, модели, настройки, provider calls и delivery policy прежние.

Corpus CI **7 passed**, process **3294 observations**, zero changed decisions/inputs.
Сохранены прежние exclusions metadata/cached/synthetic coverage; human labels не добавлялись.
Локальные outputs: `out/corpus/j070`, registry runs `j070-*`.
Следующий участок DA6 — review и остальные secondary projection checkpoints; общий goal active.

Acceptance: full **4384 passed**, errors/failures/skips/xfails 0, 632.828 s;
strict mypy 410 файлов, Ruff src/tests/tools, format, strict docs/links и diff check green.
Local HTTP не нужен: CLI/wiring/network adapters прежние. На pinned production snapshot J069
сохранились exact SQL/размеры, projection/memo hashes и **457 bills / 166 analyses / 141 memos**,
integrity ok/FK 0; выполнялся только изолированный restore/read, без свежего fetch/processing.

[Acceptance evidence](../assets/audits/2026-10-27-status-change-applications/validation.json)
содержит XML/source hashes, шесть профилей, rollback snapshots, corpus manifests и observers.
Source extraction/selection и LLM payloads не менялись. DA6.2/DA6.3 остаются partial: другие
secondary targets и rollover ещё открыты. План: **74 задачи, 12 выполнены, 62 не закрыты**.

J070 выпущен: `db6e244468657c304f578f5c190ff842e85c4274`,
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40452456234) success:
**4384 passed / 32 deselected**, strict mypy 410 файлов, lint/web/docs green.
[Deploy relay](https://github.com/sobolevbel/lexinform/actions/runs/40453059257) success:
exact COMMIT, `ff890aa334f6 → db6e24446865`, сервис up 45 s после restart.
Evidence: `out/corpus/j070/release.json`. Production processing вручную не запускался.

<a id="j071"></a>
### J071. DA6: остальные активные secondary projection checkpoints

Дата: 27 октября 2026. База: `db6e244468657c304f578f5c190ff842e85c4274`.
Статус: реализован и принят локально; выпуск проверяется отдельно.

Общий `record_secondary_application` записывает только короткую metadata связь. Владельцы
reviews, joint comparison, редакционного имени, manual amendment revision и `/explain`
сохраняют свои транзакции, source policy, submit paths и признаки завершения. StatusChange
использует тот же writer внутри границ J070. Primary generation не заимствуется.

Reviews адресуются исходным source scope и purpose; при отсутствии legacy source key используется
реальный memo key. Один paid review может примениться к двум scopes, не создавая второй paid result.
Имя и joint фиксируются с изменением соответствующего поля. Revision адресует фактический ID
новой publication; receipt следует за immutable result внутри того же checkpoint, сохраняя FK.
`/explain` связывает результат с recorded command и executed marker, не с Bill.analysis и не
с подтверждением technical reply delivery. Bare CLI/API/memo reads не объявляются проекцией.

Qualified before: **16 failures** на отсутствовавших receipts/границах. Targeted **179 passed**,
включая 17 новых сценариев: workers=1/4, source scopes, dump/restore, повторный consume, пять
мест обрыва projection/application boundary, unchanged command one-execution semantics и
неизменность original amendment publication. Strict mypy 412 файлов и Ruff green.

**12** before/after World flows: exact common table data, frozen requests, messages, edits и
technical reply outcomes совпадают. Добавлено **18** коротких receipts; delta SQL **480–1028 bytes**,
gzip **75–115 bytes**, SQLite delta 0 на fixture. J069 и J070 readers восстановили все 12 snapshots
с побайтово одинаковым dump; minimum rollback reader остаётся J069/schema50. Новых migrations,
storage backends, provider payloads и изменений review pause settings нет.

Corpus CI **7 passed**, process **3294 observations**, zero changed decisions/inputs. Human labels
не добавлялись; прежние coverage/exclusions сохранены. Outputs: `out/corpus/j071`, runs `j071-*`.
DA6.2/DA6.3 остаются partial: rollover, applicability history и переход остальных ref consumers
ещё требуют отдельных checks. Общий goal active.

При подготовке следующего rollover slice воспроизведён существующий B121: после rehome
RCL оплаченный name остаётся в history, но новый term-specific key подаёт второй request.
Current и baseline J070 дают **2 name requests / 1 retained paid name / current name None**.
Это отдельный старый дефект, а не эффект J071; probe outputs `out/corpus/j071/rollover-name-*`.
Следующий этап должен сохранить исходную identity pending name и проверить apply после переноса.

Acceptance: full **4401 passed**, errors/failures/skips/xfails 0, 640.222 s;
strict mypy 412 файлов, Ruff src/tests/tools, format и strict docs/links green. Local HTTP CLI
**21 passed**. Pinned production snapshot J069: exact SQL/размеры и projection/memo hashes прежние,
**457 bills / 166 analyses / 141 memos**, integrity ok/FK 0. Только isolated restore/read,
без свежего fetch, pipeline/model/Telegram против production copy. Corpus manifests проверены.

[Acceptance evidence](../assets/audits/2026-10-27-secondary-projection-applications/validation.json)
содержит XML/source hashes, 12 профилей, J069 rollback snapshots и повторяемые observers.
В AnalysisService изменены только collection/application methods; payload preparation, extraction,
selection и review pause methods прежние. Следующий этап — B121, затем остальной DA6 rollover.
План: **74 задачи, 12 выполнены, 62 не закрыты**; goal active.



J071 выпущен: `8b2e66c5a3bb9f4c97117fdd429060944a15179a`,
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40454360675) success:
**4401 passed / 32 deselected**, strict mypy 412 файлов, lint/web/docs green.
[Deploy relay](https://github.com/sobolevbel/lexinform/actions/runs/40454870036) success:
exact COMMIT, `db6e24446865 → 8b2e66c5a3bb`, сервис up 45 s после restart.
Evidence: `out/corpus/j071/release.json`. Production processing вручную не запускался.

<a id="j072"></a>
### J072. DA6 rollover: B121 и исходная identity name work

Дата: 27 октября 2026. База: J071 (`8b2e66c`). Статус: реализован и принят локально; выпуск проверяется отдельно.

Цель — применять уже оплаченное имя RCL после переноса каденции и сохранять единственный
submit path для pending/submitting/queued work. Repository должен отдавать фактические исходные
owner terms сохранённой работы; AnalysisService проверяет exact name context/key и владеет reuse
policy. Original request/custom ID и paid result не переписываются. Отдельно проверяются
изменившийся input, legacy records без ID, memo cleanup, providers и workers. Переносить это
правило на повторно используемые wykaz codes без доказательства identity нельзя.

Основной qualified before — **12 failures / 2 passed**, после исправления fixture provider
routing и accounting перед cleanup. Дополнительный retry boundary воспроизвёл **1 failure /
1 passed** до объединённого поиска по actual owner terms; невалидный первый probe требовал
удаления unrelated primary memo и исключён. Final targeted **96 passed**, strict mypy 413
файлов и Ruff green. Acceptance полного gate записывается ниже.

Repository query читает owner terms из сохранённых requests/results; scope/reuse policy остаётся
в AnalysisService. Только RCL names с совпадающим полным name-context key используют исходный
memo/job/result. Учитываются successful retries под новым владельцем после cleanup; original
ID/SHA/tokens не переписываются. Existing submit engine сохраняет pending/submitting и submission
budget. Legacy memo без ID не получает выдуманный ref. Новых migrations нет.

12 обычных secondary World profiles и SQL dumps совпали побайтово. 12 rollover flows (оба
providers, workers=1/4, open/ready/cleaned): вместо двух name requests/results остаётся один;
original request/paid result, primary analysis, messages и финальная card edit совпали. В cleaned
cases раньше было две edits (без имени, затем с именем), теперь одна с готовым именем.
Settled fixture delta SQL **−8496…−3513 bytes**, gzip **−460…−272 bytes**, SQLite **−12288…0 bytes**;
это локальная матрица, не прогноз production growth. Lookup на pinned production copy:
**224 RCL subjects / 2240 SELECTs**, p50 ≈0,006 ms, p95 ≈0,007 ms; dump unchanged. Это не
production phase latency. Outputs: `out/corpus/j072`; корпус и полный gate фиксируются отдельно.

Первый full: **4420 passed**, 596,314 s. Дополнительные три legacy-key regressions выявили
различие empty-text digest tag старых Name requests; все три падают до расширения lookup.
NameContext не содержит source text, поэтому legacy key сохраняет все реальные name inputs;
исключение ограничено names и не разрешает reuse legacy text-analysis результатов. Final
targeted **99 passed**; окончательный full/corpus повторяются после этого изменения. Normal
и rollover профили на финальном коде побайтово совпали с принятыми current profiles выше.

Final acceptance: **4423 passed**, errors/failures/skips/xfails 0, 635.726 s;
strict mypy 413 файлов, Ruff src/tests/tools, format, docs/links и diff check green. Corpus CI
**7 passed**, process **3294 observations**, zero changed decisions/inputs; final manifests
проверены и закрепляют окончательный source. Pinned production J069 restore/read сохраняет
exact SQL/размеры, **457 bills / 166 analyses / 141 memos**, projection/memo hashes, integrity ok/FK 0.
Нового fetch/production pipeline/model/Telegram не было. Local HTTP не повторён: CLI, wiring
и transports прежние. Все **50 migrations** побайтово прежние.

J071 reader восстановил **36** normal/rollover snapshots с одинаковым dump. Это read compatibility;
старый runtime при исполнении возвращает B121. [Acceptance evidence](../assets/audits/2026-10-27-rcl-name-rollover/validation.json)
содержит XML/source hashes, profiles, ownership query timings, corpus manifests и observers.
B121 fixed locally; DA6 продолжается с другими rollover/history consumers и legacy/retention.
План: **74 задачи, 12 выполнены, 62 не закрыты**; goal active.

<a id="j073"></a>
### J073. DA6: purpose-scoped primary handoff

Дата: 27 октября 2026. База: J072 (`f3d8ae5`). Статус: реализовано, deployment проверяется отдельно.

Ownership audit перед переносом cache metadata обнаружил общую операцию `move_batch_work`:
primary handoff одновременно меняет владельца secondary jobs/intents с отдельным frozen
контекстом. Сначала проверить mixed primary/review work и ограничить handoff primary analysis.
Cache reassignment остаётся DA6.6: без явных source/target bindings он может обойти защиту
незавершённой работы по старым координатам. Paid history, request/key/usage не переписываются;
raw storage/backend не меняются. Исторические данные не исправляются по предположению.

B122 подтверждён: **16 regressions failed** до исправления, два провайдера, workers=1/4,
consultation/commission, queued/open. Primary handoff теперь явно назван
`move_primary_analysis_work` и ограничен `analysis`/`reanalysis`; secondary input owner,
frozen context и request SHA прежние. Дополнительно проверены amendments/supplement/joint/name
и немедленно отправляемый explanation. Targeted **117 passed**; full gate запущен.

На **24** handoff profiles: восемь primary-only open/ready совпадают с J072 побайтово;
16 mixed flows ранее падали при collect, теперь сохраняют primary у successor и review у
исходного subject. Frozen provider requests одинаковые. **12** обычных profiles совпали,
включая dump, requests и reader texts. Corpus CI **7 passed**, process **3294 observations**,
zero changed inputs/decisions; manifests проверены. Extraction/selection/text policy прежние.

Свежий read-only state `f34e63e7` от 27.10 06:26 UTC: **457 bills / 141 memos / 109 batch items**,
intents/results/applications 0. Из 19 review items активных 0, у всех отсутствует сохранённый
review context: историческое соответствие subject по этому snapshot неизвестно. Автоматического
repair/backfill нет. Dump unchanged, integrity ok/FK 0. Отдельный pinned J069 restore/read
сохраняет прежние SQL/projection/memo hashes и размеры. Production pipeline не запускался.

J072 release проверен по полному `f3d8ae5715ddd250e01c4b1a60ac3d051ddca804`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40459754769) — **4423 passed**,
strict mypy 413 файлов, Ruff green; docs/web jobs success.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40460214367) — exact COMMIT,
`8b2e66c5a3bb -> f3d8ae5715dd`, listener up 45 s. Это подтверждает релиз J072, не завершённый
production цикл новых history rows: в свежем state их ещё 0.

Final acceptance: **4457 passed**, failures/errors/skips/xfails 0, 612.578 s; strict mypy
414 файлов, Ruff src/tests/tools, format, docs/links и diff check green. Все **50 migrations**
побайтово прежние. J072 reader восстановил **36** новых snapshots с exact dump; запуск старого
runtime возвращает B122, read compatibility этого не устраняет. CLI/wiring/transports прежние,
local HTTP не повторён. [Acceptance evidence](../assets/audits/2026-10-27-primary-work-handoff/validation.json)
содержит XML/source hashes, observers, corpus manifests, normal/handoff profiles и state inventory.
Исходный input subject перенесённого primary request ещё не выделен из collection coordinates;
это отдельная граница DA6. План: **74 задачи, 12 выполнены, 62 не закрыты**; goal active.

<a id="j074"></a>
### J074. DA6: exact secondary history reuse after memo cleanup

Дата: 27 октября 2026. База: J073 (`3cd8212`). Статус: реализовано, deployment проверяется отдельно.

Проверить amendments/supplement/joint/name с тем же exact question key после удаления operational
memo. История оплаченного ответа уже сохранена; текущий consumer должен использовать её без
новой submission. Scope ограничен фактическим subject/purpose/key и существующим record type;
никакого similarity reuse, новой source policy или достраивания legacy input. Primary, reviews
и explanation имеют отдельные контракты и принимаются отдельно.

B123 подтверждён: **8 regressions failed** на J073, same question после collect/200-day cleanup
снова ставится в batch. Общий `computation_reuse.retained_secondary` читает immutable history,
проверяя subject/purpose/key/type; он не меняет projection, не строит запрос и не выбирает source.
Consumer восстанавливает memo через прежний путь, возвращая `_reused` с нулевыми токенами.
Контроли changed context, другой Sejm term, legacy memo без refs и второй cleanup green;
targeted **71 passed**. Strict mypy **416** файлов и Ruff green; full gate запущен.

На **16** isolated fake flows, providers=2/workers=1/4/purposes=4: secondary requests/results
**2 → 1**, новых paid calls после cleanup нет; original request/result, нормализованное содержание
ответа, primary Bill и reader texts прежние. Settled SQL delta **−4188…−2592 bytes**,
gzip **−158…−139 bytes**, SQLite **0**; fixture measurement, не production growth forecast.
**12** обычных profiles совпали целиком. Fresh state J073 `f34e63e7` восстановлен baseline/current:
SQL/projection/memo hashes и размеры равны, **457 bills / 166 analyses / 141 memos**, v50,
integrity ok/FK 0; pipeline/model/Telegram не запускались. Corpus CI **7 passed**, process
**3294 observations**, zero changed inputs/decisions; manifests проверены.

J073 release проверен по полному `3cd82124cf1a12427897327acefd4c2d8db5dcd5`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40467348173) — **4457 passed**,
strict mypy 414 файлов, Ruff, docs/web jobs success.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40467812482) — exact COMMIT,
`f3d8ae5715dd -> 3cd82124cf1a`, listener up 45 s. История новых paid rows в production этим
не подтверждается; доступный state pin имеет results/applications 0.

Final acceptance: **4477 passed**, failures/errors/skips/xfails 0, 645.538 s; mypy **416** файлов,
Ruff src/tests/tools, format, docs/links и diff check green. Финальные **20** regressions на J073:
**8 failed / 12 passed**; current targeted **71 passed**. J073 reader восстановил **28** новых
snapshots с exact dump, все **50 migrations** прежние. Local HTTP не повторён: CLI, wiring,
transports не менялись. [Acceptance evidence](../assets/audits/2026-10-27-secondary-history-reuse/validation.json)
содержит XML/source hashes, profiles/deltas, observers, corpus manifests и production restore.
Это не общий replay всех computations: primary, reviews, explanation, applicability history и
перенос consumers на refs остаются DA6. План: **74 задачи, 12 выполнены, 62 не закрыты**;
goal active.

<a id="j075"></a>
### J075. DA6: compatible frozen input binding reader

Дата: 27 октября 2026. База: J074 (`d1cde0e`). Статус: реализовано, deployment проверяется отдельно.

Подготовить reader до будущего writer: nullable `input_binding` хранит исходные term/number
и логический reuse key отдельно от collection coordinates и точного request SHA. Сохранённый
ключ не означает наличие исходных bytes или PreparedInput. Legacy остаётся unknown, поле
с None не сериализуется. Metadata проверяет согласованность binding/memo key, review reader —
с сохранённым input/context. Collector bridge сохраняет уже известное future metadata;
текущие producers ещё не заполняют binding. Схема и backend прежние.

Targeted **122 passed**: десять purpose readers, metadata/key mismatch, primary handoff
open/ready × providers=2/workers=1/4 и consultation/commission/paused-act collect
success/failure × providers=2. Act submissions не включены: fixture читает уже отправленную
legacy работу. Primary paid row сохраняет исходный binding при другом collection target;
ready-before-handoff history остаётся у прежнего owner. Request SHA и usage прежние.
Mypy **418** файлов, Ruff/format green; full gate запущен.

**28** normal/cold profiles совпадают с J074 целиком, включая SQL, requests/results и reader
texts. State `f34e63e7` восстановлен baseline/current: SQL/projection/memo hashes и размеры
прежние, integrity ok/FK 0. Нет новой schema, source policy, raw storage или production run.
Corpus CI **7 passed**, process **3294 observations**, zero changed inputs/decisions;
manifests проверены. J074 reader читает **29** default/legacy snapshots с exact dump.
Десять populated future fixtures он отклоняет по unknown `input_binding`, current reader
принимает все десять: это явная минимальная rollback-граница до включения следующего writer.
На fixture pack из 10 results binding добавляет **965 SQL / 214 gzip / 4096 SQLite bytes**;
при None default output и production dump не растут. Это не production growth forecast.

J074 release проверен по полному `d1cde0e02128e0bbd652358ecd85c179eda33922`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40469054635) — **4477 passed**,
strict mypy 416 файлов, Ruff, docs/web jobs success.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40469781532) — exact COMMIT,
`3cd82124cf1a -> d1cde0e02128`, listener up 45 s. Live cycle новых paid history rows
этим не подтверждён; доступный state pin содержит results/applications 0.

Дополнительная проверка нашла в первой версии reader пересчёт policy key из legacy full review
context. **3 probes failed**: исторический записанный ключ должен читаться без применения
сегодняшней политики. Final reader сравнивает persisted compact refs; для full legacy context
проверяет только известную identity, сохраняя opaque key. Первичный full **4509 passed** остаётся
диагностикой промежуточного кода; после исправления повторяются full gate и corpus manifests.
Final targeted **125 passed**, historical-key probes **3 passed**.

Та же проверка расширена на collect: **3 additional probes failed**, если policy меняется
после submit. `ReviewInputReference.from_context` теперь принимает явно recorded key из
known binding; legacy fallback не меняется. Binding не пересчитывается через новый policy
и не теряется из-за различия с текущим контекстным hash. Матрица включает обе модели,
success/failure и unchanged/changed policy. Новый targeted **137 passed**, шесть policy-key
probes **6 passed**; второй full **4512 passed** — промежуточная диагностика, финальный gate
повторяется после collector correction. Исходники обеих промежуточных версий закреплены
в corpus code snapshots; initial/final/qualified runs не смешиваются.

Final acceptance: **4524 passed**, failures/errors/skips/xfails 0, 656.311 s; strict mypy
**418** файлов, Ruff src/tests/tools, format, docs/links и diff check green. Qualified corpus
CI **7 passed**, process **3294 observations**, zero changed inputs/decisions; manifests
закрепляют финальный source. Все **50 migrations** прежние. **28** финальных normal/cold
profiles совпадают с J074 и промежуточными profiles; production restore сохраняет прежние
SQL/projection/memo hashes и размеры. J074 reader читает 29 default/legacy snapshots,
но populated binding требует J075 bridge. CLI/wiring/transports прежние, local HTTP не повторён.
[Acceptance evidence](../assets/audits/2026-10-27-computation-binding-reader/validation.json)
содержит оба вида before/after probes, source snapshots, XML, observers и измерения. Writer
новых bindings ещё не включён; это следующий этап. План: **74 задачи, 12 выполнены,
62 не закрыты**; goal active.

<a id="j076"></a>
### J076. DA6: freeze input bindings at new intent creation

Дата: 27 октября 2026. База: J075 (`800ef77`). Статус: реализовано, deployment проверяется отдельно.

Пять producers используют `BatchIntent.from_request`: фабрика проверяет новый unbound input,
согласованность request/question subject и наличие recorded memo key, затем фиксирует
term/number/key до repository IO. Joint question использует собственный subject; source-specific
ветвлений нет. Restore/plain constructor остаётся совместимым: старые None не backfilled.
Фабрика не меняет payload, SHA/custom ID, provider/model, cost/retry engine или pause settings.
Новый binding проходит существующий submit/collect checkpoint и J075 bridge.

Targeted **155 passed**: primary/reset, reanalysis, пять secondary purposes, три review purposes,
providers/workers, cleanup и old unbound/new bound collect. Unit guards не позволяют rebind,
post-hoc retarget или пустой question key; nullable explanation provider явно проверяется до
reservation/queue, без догадки о backend. Strict mypy **420** файлов, Ruff/format green.

Сравнены **52** fake configuration flows: 12 normal, 16 cold reuse, 24 primary handoff.
После удаления только нового `input_binding` все SQL rows/schema, payloads/SHAs, paid outputs,
usage, application IDs и reader messages одинаковые. Settled SQL delta **246…738 bytes**,
gzip **22…62 bytes**, SQLite **−4096…4096 bytes**; это fixture/page allocation, не production
growth forecast. Stored metadata key согласуется с binding; J075 reader читает все **52**
snapshots с exact dump, включая pending intents/items. State pin `f34e63e7` restore/read
сохраняет SQL/projection/memo hashes и размеры; pipeline/provider/Telegram не запускались.

Первый full выявил девять legacy fixtures: старый key подменялся в современном bound intent,
но binding не удалялся. Legacy fixture теперь действительно unbound; дополнительно проверен
None после queued/submitting/open restore. Проверки согласованности metadata не ослаблены.
Runtime source после этого не менялся; full/corpus qualification повторяется с final tests.

J075 release проверен по полному `800ef774e61d88401014310e90f941144ca96a16`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40473733446) — **4524 passed**,
strict mypy 418 файлов, Ruff, docs/web jobs success.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40474491817) — exact COMMIT,
`d1cde0e02128 -> 800ef774e61d`, listener up 45 s. Это устанавливает доступный rollback bridge,
но не подтверждает live cycle нового writer.

Final acceptance: **4583 passed**, failures/errors/skips/xfails 0, 651.344 s; targeted **155 passed**,
strict mypy **420** файлов, Ruff src/tests/tools, format, docs/links и diff check green.
Final corpus CI **7 passed**, process **3294 observations**, zero changed inputs/decisions;
manifests фиксируют окончательный source/tests. Все **50 migrations** прежние. Из runtime
изменены только batch model factory и пять producers в analysis service. Local HTTP не повторён:
CLI/wiring/transports прежние. [Acceptance evidence](../assets/audits/2026-10-27-computation-binding-writer/validation.json)
содержит XML/source hashes, normalized SQL comparison, 52 profiles и rollback checks.
Binding writer завершён для новых requests; history consumers, applicability observations,
legacy import и retention/ref transition остаются в DA6. План: **74 задачи, 12 выполнены,
62 не закрыты**; goal active.

<a id="j077"></a>
### J077. DA6: caller-owned explanation history snapshot

Дата: 27 октября 2026. База: J076 (`28bcc83`). Статус: принято локально, deployment проверяется отдельно.

B124 воспроизведён на known bound explanation после memo cleanup: **4 failed**, providers=2,
workers=1/4. `list_computation_results` получает optional purpose filter без изменения default
reader; caller передаёт explanation-only snapshot в `_prepare`. Pure matcher общий с secondary
reuse: known bindings сопоставляются по original subject/key/type; прежний legacy custom-id
контракт четырёх secondary purposes сохранён. Memo имеет приоритет, paid record не мутируется.
Prepare workers не обращаются к repository; primary prepare получает пустой snapshot и пока
не меняет собственный history reuse. Legacy explanation без binding остаётся unknown.

Final acceptance: **4602 passed**, failures/errors/skips/xfails 0, 661.065 s; targeted
**82 passed**, strict mypy **422** файлов, Ruff src/tests/tools, format и diff check green.
Корпусный CI **7 passed**, process **3294 observations**, zero changed inputs/decisions;
qualified manifests закрепляют source/tests. Все **50 migrations** прежние.
**52** normal/secondary/handoff profiles и exact dumps целиком совпали с J076. Из **16**
explanation flows исправлены четыре cold bound; остальные **12** совпали целиком. Возвращённый
ответ равен прежнему warm reuse; original requests/results, Bill, applications и reader effects
не изменены. Memo восстановление добавляет **2299 SQL / 65–66 gzip bytes**, SQLite delta 0
в этих fixtures; это не production forecast. J076 читает **68** current snapshots с exact dump.
State pin `f34e63e7` restore/read сохраняет прежние hashes/размеры, integrity ok/FK 0.
[Acceptance evidence](../assets/audits/2026-10-27-explanation-history-reuse/validation.json)
содержит before probes, XML, observers, source hashes и измерения. CLI/wiring/transports
прежние, local HTTP не повторён. Implementation commit ещё не создан; rollback J076.

J076 release проверен по `28bcc83fd7ac9d4b9b4c51fce2ca6d6d0a5aa9b7`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40480729326) — **4583 passed**,
strict mypy 420 файлов, Ruff, docs/web jobs success.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40481344647) — exact COMMIT,
`800ef774e61d -> 28bcc83fd7ac`, listener up 45 s. Live cycle writer этим не подтверждён.
План: **74 задачи, 12 выполнены, 62 не закрыты**; goal active. Следующий фрагмент — primary
history reuse после memo cleanup с caller-owned snapshots и прежними generation guards.

<a id="j078"></a>
### J078. DA6: exact primary history reuse on the caller

Дата: 27 октября 2026. База: J077 (`9e3d891`). Статус: принято локально, deployment проверяется отдельно.

После memo cleanup/reset основной анализ повторно ставит уже оплаченный вопрос: первые
**8 probes failed**. Caller теперь читает purpose-filtered immutable snapshots перед `fan_out`,
прямым анализом и reanalysis; workers получают только данные. Exact known input binding и
существующий pure matcher сохраняют subject/key/type boundary, memo priority и free reuse.
Force с `ignore_cost_limit=True` сохраняет прежний cache bypass. Ready/batch-pending shortcuts
и atomic application/generation guards не изменены. Старые unbound results не backfilled.

Первая целевая проверка **31 passed** включает 22 новых сценария и девять explanation controls.
В reanalysis платный ответ collected после operator generation reset: он остаётся superseded
в исходной collection receipt, но повторный exact input может быть применён к новой generation.
Prepare не создаёт projection receipt; только прежний caller-owned checkpoint делает это.
Implementation commit не создан; schema/migrations и stored formats прежние. Следуют
before/after qualification, полный gate и сравнение нормальных/cold flows.

Расширенная before/after проверка на J077: **16 failed / 6 passed**, current targeted
**137 passed**. Mypy **423** файла, Ruff src/tests/tools и format green. Проверено **104**
configuration flows: прежние **68** целиком совпали, из 36 primary flows изменились только
**8 cold bound**, остальные **28** совпали вместе с SQL. New cold projections/applications
равны baseline warm reuse; original requests/results прежние. Settled SQL delta в исправленных
fixtures **−4015…−1050 bytes**, gzip **−257…−23**, SQLite **−8192…−4096**: устранена лишняя
operational work, это не production forecast. J077 читает все **104** snapshots с exact dump.
State pin `f34e63e7` восстановлен без изменения SQL/projection/memo hashes и размеров,
integrity ok/FK 0. Корпусный CI **7 passed**, process **3294 observations**, zero changed
inputs/decisions; manifests проверены. Full gate ещё выполняется, acceptance не объявлена.

J077 release проверен по `9e3d891691e6649bac5d25d1c652d5a1917a2f6a`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40485302726) — **4602 passed**,
strict mypy 422 файла, Ruff, docs/web jobs success.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40485924424) — exact COMMIT,
`28bcc83fd7ac -> 9e3d891691e6`, listener up 45 s. Это не подтверждает live history reuse.

Final acceptance: **4624 passed**, failures/errors/skips/xfails 0, 665.862 s. Targeted
**137 passed**, strict mypy **423** файла, Ruff src/tests/tools, format, docs/links и diff check
green. Corpus CI **7 passed**, process **3294 observations**, zero changed inputs/decisions;
qualified source/tests hashes совпадают. Все **50 migrations** прежние; runtime изменён
только в трёх caller paths analysis service, stored format прежний.
[Acceptance evidence](../assets/audits/2026-10-27-primary-history-reuse/validation.json)
содержит before/after XML, 104 profiles/rollback reads, observers и pinned restore. Первичные
невалидные test/observer invocations исключены и описаны отдельно. Local HTTP не повторён:
CLI/wiring/transports прежние. Implementation commit ещё не создан; rollback J077.
DA6.4 остаётся частичным: applicability observations, legacy import и ref/retention transition
не закрыты. План: **74 задачи, 12 выполнены, 62 не закрыты**; goal active.

<a id="j079"></a>
### J079. DA8: compare actual work owners and reject a universal scheduler

Дата: 27 октября 2026. База: J078 (`4ed9a94403a78f0ffe1f5dea540bf3d53b5558af`).
Статус: локальная приёмка; только документация и воспроизводимые audit artifacts.

[Сравнение](../reviews/2026-10-27-unfinished-work.md) фиксирует identity, frozen baseline,
source/provider retries, outage, dependencies, refresh, apply и terminal outcomes для позднего
Senate DocumentWork и ReviewWork. Сходство не подтверждает общий execution lifecycle:
исторический single-message correction и source-refresh/current-ELI review имеют разные
условия. Сохраняем existing typed owners и общие batch/result/application/publication refs.
DA8.1/DA8.2 приняты; DA8.3 неприменим к отказу от scheduler, перенос не объявлен выполненным.
DA8.4 не закрыт: retention guards остаются, полный dependency inventory и ref migration впереди.

Повторно выполнены **137 existing-work regressions**, failures/errors/skips/xfails 0,
10.069 s: late material/restore, frozen context, повтор work, unknown correction, review
uncertain submission, flags/pause, comparison snapshots и retention. Пять сравниваемых
model/consumer/repository modules byte-identical J077; runtime/tests J078 не менялись, его
full **4624 passed**, strict mypy/Ruff/format и corpus gates остаются применимы.
[Acceptance evidence](../assets/audits/2026-10-27-unfinished-work/validation.json)
содержит XML/source hashes, observer и pinned inventory.

Read-only SQL replay state `f34e63e7`, SHA `73e37426…3b71f`: **3 waiting DocumentWork**,
**2 consultation context work**, **7 abandoned markers** (5 consultation/2 act), intents и
unconsumed/unaccounted items 0. Review-owner predicate защищает **18 memo rows**; DocumentWork
в этом pin не защищает memo/sent-payload rows. Это не доказательство ненужности отдельных
записей и не fresh production claim. Source SHA прежний, pipeline/providers/Telegram не запускались.
Неудачные draft SQLite probes и неверный draft column исключены; финальный observer повторяет
реальные JSON predicates `prune_history`, работает in-memory/query-only и не меняет источник.

Нет DDL, новых flags/defaults, scheduler, retention changes или storage decision. State delta 0
по отсутствию runtime/migration/writer изменений; новый production рост не прогнозируется.
Implementation commit ещё не создан; rollback к J078 не требует преобразования данных.
План: **74 задачи, 14 выполнены, 60 unchecked**, среди них DA8.3 явно неприменим; goal active.
Следующий фрагмент DA10 — общий безопасный reader SQLite snapshots и диагностика ownership
незавершённой работы без repair/submit/send.

<a id="j080"></a>
### J080. DA10: shared checkpointed snapshot reader and work ownership doctor

Дата: 27 октября 2026. База: J079 (`0b59289`). Статус: принято локально, deployment проверяется отдельно.

`tools.sqlite_snapshot` выносит из request doctor чтение immutable checkpointed copy,
WAL/journal refusal и SHA race check. `tools.work_doctor` проверяет реальные document/review
owners, matching change/bill, duplicate review identity и синтаксис frozen snapshots. Вывод
содержит metadata/counters и flags неизвестного legacy, без legal context, реконструкции
recorded keys, repair или исполнения work. Memo counts могут пересекаться; publication linkage
не доказывает channel/edit eligibility. Production CLI/pipeline не меняются.

Первый fixture draft ошибочно считал `add_bill` записью в БД и использовал несуществующий enum;
**22 setup errors**, mypy error. Fixture теперь проходит реальные fake production collect/apply,
использует STATUS_UPDATE и existing paid memo. Runtime guards не ослаблены. Новый targeted
**42 passed**, strict mypy **424** файла. Идёт дальнейшая qualification и проверка legacy inputs.

Final targeted **45 passed** включает 27 новых work/snapshot guards и прежние 18 request-doctor
checks. Добавлены physical schedule/JSON mismatch и empty legacy review ownership: данные не
пересчитываются и не удаляются. Strict mypy **424** файла, Ruff src/tests/tools, format green;
full gate запущен. После выноса reader **13** complete request-doctor reports (12 normal fake
snapshots и pinned production) совпали с J079 целиком; входные DB hashes не изменены.

На state pin `f34e63e7` новый doctor показывает 3 waiting DocumentWork/9 ReviewWork, без errors,
с 3 warning `document_publication_unavailable`. Это literal missing linkage в этом историческом
pin, не автоматический verdict о возможности revision или необходимости удаления. Full legal
context, opaque question keys и provider payload в report не копируются. Идёт qualification.

J078 release проверен по `4ed9a94403a78f0ffe1f5dea540bf3d53b5558af`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40487303528) — **4624 passed**,
strict mypy 423 файла, Ruff, docs/web jobs success.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40488259345) — exact COMMIT,
`9e3d891691e6 -> 4ed9a94403a7`, listener up 45 s. Это не live history-cycle proof.
J079 implementation `0b59289521971acd3b11c16d8408fab2c341ae3d` pushed; его CI/deploy
проверяются отдельно. Незавершённая J080 work в этот push не включалась.

J079 release проверен по `0b59289521971acd3b11c16d8408fab2c341ae3d`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40488439465) — **4624 passed**,
strict mypy 423 файла, Ruff, docs/web jobs success.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40489420492) — exact COMMIT,
`4ed9a94403a7 -> 0b5928952197`, listener up 45 s. Отдельного production processing не было.

Final acceptance: **4651 passed**, failures/errors/skips/xfails 0, 656.314 s. Targeted
**45 passed**, strict mypy **424** файла, Ruff src/tests/tools, format, docs/links и diff check
green. Corpus CI **7 passed**, process **3294 observations**, zero changed inputs/decisions;
qualified source/tests/tools manifests проверены. Все runtime `src` files byte-identical J079,
поэтому схема **50**, migrations, provider/reader effects, default flags и state writers прежние.
**13 complete request-doctor reports byte-identical** baseline/current, все input DB hashes
сохранены. Новый work report не добавляет raw/context copies в state; delta state 0.
[Acceptance evidence](../assets/audits/2026-10-27-work-ownership-doctor/validation.json)
содержит XML/source hashes, сравнительные profiles, pinned inventory и observer.
Implementation commit ещё не создан; rollback J079 не требует миграции. Local HTTP не повторён:
сетевой CLI/wiring/transports прежние, новая offline CLI покрыта subprocess tests.
DA10.2 остаётся частичным: application/projection graph и blobs вне этого doctor. План:
**74 задачи, 14 выполнены, 60 unchecked** (DA8.3 неприменим); goal active. Следующий фрагмент
проверяет computation/application refs на тех же immutable snapshots без выполнения work.

<a id="j081"></a>
### J081. DA10: saved computation/application graph

Дата: 27 октября 2026. База: J080 (`795083fc23516c305d1a0cdcde3156219916752e`). Статус: принято локально, deployment проверяется отдельно.

Следующий read-only doctor проверяет result/application SQL identities и известные refs в
сохранённых проекциях. Original input scope, collection owner и application target намеренно
различаются при handoff/reuse: они не будут объявляться corruption. Legacy None остаётся
unknown, question keys не пересчитываются; opaque application targets не исполняются.
Общий обход saved JSON/columns выносится из request doctor только с сохранением его output.
Новых state writers, DDL, provider/source calls и retention изменений не планируется.

Общий `saved_records` сохраняет прежний catalogue/order и `bill_json`/`change_json` decoding
request doctor; graph reader отдельно добавляет recorded work/input snapshots и batch metadata.
Первый fixture draft создал STATUS_UPDATE без StatusChange ID: **23 setup errors**. Fixture
теперь вставляет реальное fake event; repository assertions/guards не менялись. Новый targeted
**71 passed** (26 graph + прежние 45), strict mypy **425** файлов, Ruff/format green.

**104** сохранённых J078 flows восстановлены с exact dump; graph doctor принимает все без
errors, DB hashes прежние. Включены primary reset, secondary, reviews и primary handoff;
original binding может отличаться от collection coordinates. Act purpose этим не включается.
**29 complete request-doctor reports byte-identical** J080/current: прежние 13 snapshots плюс
16 malformed/legacy/encoded/depth controls. Все входные DB hashes неизменны. На state pin
`f34e63e7` graph doctor записывает **408 unknown legacy refs / 638 saved columns**, results и
applications 0; unknown не объявлен corruption и исторический input не достраивается.
Full gate выполняется; source selection, runtime `src`, schema и state writers прежние.

J080 release проверен по `795083fc23516c305d1a0cdcde3156219916752e`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40490322145) — **4651 passed**,
strict mypy 424 файла, Ruff, docs/web jobs success.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40490962261) — exact COMMIT,
`0b5928952197 -> 795083fc2351`, listener up 45 s. Отдельного production processing не было.

Дополнительно **8 reanalysis configuration flows** (один credential-safe observer test)
проверены на providers=2/workers=1,4/prepare-or-apply: исходный superseded collection остаётся,
exact cold answer применяется отдельно к текущей generation. Итого graph QA **112 flows**,
все reports recorded/errors 0, DB hashes прежние. Существующие 104 snapshots не включали
reanalysis; дополнительная восьмёрка учитывается отдельно. Это не 112 независимых инициатив.

Final acceptance: **4677 passed**, failures/errors/skips/xfails 0, 658.529 s; targeted
**71 passed**, strict mypy **425** файлов, Ruff src/tests/tools, format, docs/links и diff check
green. Corpus CI **7 passed**, process **3294 observations**, zero changed inputs/decisions;
qualified source/tests/tools manifests проверены. Все runtime `src` files byte-identical J080,
схема **50**, migrations, default flags и state writers прежние. **112 graph flows** приняты
без изменения DB; прежний request-doctor output совпал **побайтно на 29 inputs**.
[Acceptance evidence](../assets/audits/2026-10-27-computation-graph-doctor/validation.json)
содержит XML/source hashes, profiles, observers и reproduction. Новая optional CLI не имела
предыдущей реализации; import failure не использован как искусственный before regression.
State delta 0: инструменты не пишут в input DB, raw/context archives не создаются.
Implementation commit ещё не создан; rollback J080 не требует изменения state. Local HTTP
не повторён: сетевой CLI/wiring/transports прежние, новая offline CLI покрыта subprocess tests.
DA10.2 остаётся частичным: application target/dependency resolution и blobs не заявлены этим
reader. План: **74 задачи, 14 выполнены, 60 unchecked** (DA8.3 неприменим); goal active.

<a id="j082"></a>
### J082. DA10: read-only application target linkage

Дата: 27 октября 2026. База: J081 (`3d9c420bf9ace07bfed777617e59d5bdd90e2831`). Статус: принято локально, deployment проверяется отдельно.

Проверяется наличие физических status-change/publication/command targets в их собственных
таблицах по записанным IDs. Logical bill/editorial/review targets показывают только наличие
текущего owner; старый receipt не сравнивается с нынешним содержанием slot. Unsupported kinds
и нераспознанные refs остаются opaque. Наличие/отсутствие target не исполняет application,
не меняет результат исторического применения и не разрешает повторить команду или доставку.

Targeted **90 passed** (19 новых target checks + прежние 71), strict mypy **426** файлов,
Ruff/format green; full gate выполняется. Физические refs принимаются только в exact canonical
signed SQLite-integer representation; командный ID 0 сохраняется. Нераспознанные codecs
остаются opaque, без переписывания refs или вывода их содержимого. Missing row/table даёт
warning/recorded, не команду к retry. Старый outcome и paid result остаются прежними.

На **112** прежних graph snapshots все прежние данные совпали после исключения только
новых target observations/counters и поясняющих exclusions; старые findings/status/refs/usage
сохранились. Новых warnings на этих fixtures **0**, все DB hashes прежние. Дополнительно
**4 RCL name-rollover flows** (providers=2/workers=1,4, existing batch-only fake helpers)
подтверждают `historical_owner_absent` после переноса Bill в term 11: старый editorial receipt
сохраняется, новый paid name не заказывается, corruption не объявляется. Это отдельный fake
config slice, не production cycle или независимая legal разметка.

J081 release проверен по `3d9c420bf9ace07bfed777617e59d5bdd90e2831`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40494087240) — **4677 passed**,
strict mypy 425 файлов, Ruff, docs/web jobs success.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40495029224) — exact COMMIT,
`795083fc2351 -> 3d9c420bf9ac`, listener up 45 s. Live processing этим не подтверждён.

Final acceptance: **4696 passed**, failures/errors/skips/xfails 0, 622.855 s; targeted
**90 passed**, strict mypy **426** файлов, Ruff src/tests/tools, format, docs/links и diff check
green. Corpus CI **7 passed**, process **3294 observations**, zero changed inputs/decisions;
qualified source/tests/tools hashes проверены. Runtime `src` byte-identical J081, схема **50**,
migrations/default flags/state writers прежние. Request/work doctors, checkpointed reader и
saved-record walker byte-identical J081. **116 target configuration flows** приняты; на прежних
112 graph fields прежние, разрешены только новые observations/counters и пояснения. Все входные
DB hashes сохранены, warning count на native fixtures 0. Source pin `f34e63e7`: 408 unknown refs,
638 columns, target links 0, errors/warnings 0; это inventory исторического pin, не live cycle.
[Acceptance evidence](../assets/audits/2026-10-27-application-target-linkage/validation.json)
содержит XML/source hashes, profiles, observers и reproduction. State delta 0; raw/context архив
не создаётся. Implementation commit ещё не создан; rollback J081 не требует state migration.
Local HTTP не повторён: network CLI/wiring/transports прежние. План: **74 задачи, 14 выполнены,
60 unchecked** (DA8.3 неприменим); goal active. Следующий фрагмент — DA6.4 applicability
observations, с отделением known current fingerprints от original paid-input provenance.

<a id="j083"></a>
### J083. DA6.4: reader for compact applicability observations

Дата: 27 октября 2026. База: J082 (`0b05a4f9a51bdb38c533bc0224f9254aec6f371e`). Статус: принято локально; deployment проверяется отдельно.

Derivability characterization **1 passed**: один paid ID сохраняется при pointer A → B → C,
но B исчезает из final Bill/result/applications/memo. `source_checks` содержит health/aspect
timestamps, а не связь paid result ↔ source version. Поэтому эту историю нельзя достроить из
нынешней проекции. Используется source-command-migration; append-only migration **51** добавляет
`result_applicability` с FK к paid result. Все **50** прежних SQL scripts неизменны.

Новый frozen reader хранит только scope, result ref, URL/kind, opaque recorded text/law fingerprints,
selection metadata SHA и время записи связи. Timestamp исключён из identity; повторное сохранение
сохраняет первое время и не растит dump. Paid scan_input и текст исключены из selection fingerprint:
original paid derivative не доказывает newly observed input. Unknown legacy IDs/fingerprints
не backfilled. Producer пока не подключён; reader сам не меняет текущую applicability policy.

Первый targeted выявил два fixture defects: unsupported make_analysis(summary=…) и legacy-v20
fixture, оставлявший новую таблицу перед replay migration 51. Исправлены только arrangements;
миграции и consistency guards не ослаблены. Fresh state fetched `9e836a90023fdd40e7b56499fce4ad99daf78198`
(27.10 10:57:20 UTC); последуют isolated restore/read и сравнение с J082, без pipeline/model/Telegram.
Rollback проверяется по фактическому round trip, без попытки schema downgrade.

J082 release проверен по `0b05a4f9a51bdb38c533bc0224f9254aec6f371e`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40497785248) — **4696 passed**,
strict mypy 426 файлов, Ruff, docs/web success.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40498578658) — exact COMMIT,
`3d9c420bf9ac -> 0b05a4f9a51b`, listener up 45 s. Pipeline или live processing не запускался.

Reader QA: **116** сохранённых native configuration flows после миграции сохранили все старые
табличные данные. В отдельные future-populated fixtures добавлены **98** associations только
для известных refs/current metadata. Current reader сохраняет их при точном round trip;
старый J082 reader сохраняет весь заполненный SQL **побайтно** и оставляет schema 51.
Это реальная rollback boundary, без downgrade и без заявления нового production цикла.
Временный observer сначала не нашёл annotated `MIGRATIONS` через AST Assign; исправлен на
AnnAssign, первый ошибочный запуск сохранён отдельно. Runtime guards не ослаблялись.

Fresh production pin `9e836a9`, SHA `b2c05613b361d87331203aaf4aec232428075bc7b31eb2cbfdc1bfc90cb78b47`:
**457 bills, 166 analyses, 141 memos**, known bill 2699 readable, integrity ok/FK errors 0.
Все projections/memos и старые table rows прежние; новая таблица пустая, results/applications 0.
SQL **19,509,831 → 19,510,181 bytes** (+350), gzip **3,956,261 → 3,956,311** (+50),
SQLite **20,488,192 → 20,500,480** (+12,288). Profile restore 0.419 → 0.411 s,
dump 0.132 → 0.127 s: единичные локальные измерения, не latency benchmark сети.
Corpus CI **7 passed**, process **3294 observations**, changed inputs/decisions 0;
индекс и оригиналы корпуса не менялись. Derivability report уточнён и повторён **1 passed**.
Параллельный завершённый docs-only коммит `af0bfdc` сохранён без изменения runtime/model flags;
его отчёт не является частью архитектурного эксперимента.

Дополнительный read-only source metadata profile охватил все **166** сохранённых analyses:
**71 known source signature / 95 unknown**. Записанные versioned fingerprints сохранены
дословно; legacy paid IDs отсутствуют и не создаются, dump не изменился. Это чтение текущих
метаданных, без утверждения о наличии исторических bytes или новом extraction replay.

Final acceptance J083: **4724 passed**, failures/errors/skips/xfails 0, **593.574 s**;
targeted **81 passed**, derivability **1 passed**, strict mypy **429** файлов,
Ruff src/tests/tools, format src/tests, docs/links и diff check green.
[Acceptance evidence](../assets/audits/2026-10-27-applicability-reader/validation.json)
содержит XML/source hashes, fresh state profile, old-row equality, 116 future-populated round trips,
rollback J082 и source metadata coverage. Все 50 deployed migrations byte-identical;
producer, source selection, matching, prompts, retry/delivery и default flags прежние.
Rollback сохраняет заполненную schema 51 без downgrade. Local HTTP не повторён:
network CLI/wiring/transports прежние. DA6.4 остаётся частичным до producer и остальных scopes.
План **74 задачи, 14 выполнены, 60 unchecked** (DA8.3 неприменим); goal active.

<a id="j084"></a>
### J084. DA6.4: atomic primary applicability writer

Дата: 27 октября 2026. База: J083 (`28e9b1eaca25708e6b849b4072cef10eee7c8b83`). Статус: принято локально; deployment проверяется отдельно.

Общий `apply_analysis` сохраняет компактную source association в уже существующей atomic
projection transaction. Это текущие известные metadata и связь с paid result, не исходный
оплаченный input или заявление о сохранённых bytes. Collect/preparation сами связь не создают;
нет нового scheduler, migration, worker repository I/O или изменения reuse/selection/cost policy.
Отдельный дублирующий ResultApplication не нужен: observation уже содержит result/source ref.

Регрессии **11 cases**: на reader-only J083 **9 fail / 2 controls pass** из-за отсутствия writer.
После подключения **46 focused passed**, strict mypy **430** файлов, Ruff/format green.
Покрыты providers=2/workers=1,4, A → B → C после restore, quiet repeat без роста dump,
collection-vs-projection, legacy/missing owner, post-write fault и внешний tracking rollback.
Сбой сохраняет оплаченный результат и ready work для повторного применения без нового запроса.

Свежие paired native observers baseline/current охватили **104 flows**; дополнительно **8**
reanalysis и **4** rollover проверены против прежних native snapshots: всего **116**.
Все старые table rows, requests, usage, projections, application receipts и reader payload прежние.
Новых associations **106** в **82** flows. Для fixtures с ними прирост SQL **735–1864 bytes**,
gzip **132–305 bytes**; числа включают разное количество associations и не являются прогнозом
на весь поток. Тексты, paid answers и request bodies не копируются. Повтор той же версии
сохраняет первое время. Первые paired observer запуски забыли обязательный DA_EXPECTED_CODE;
параметр исправлен, проверки import path сохранены, первые logs/XML оставлены отдельно.

Corpus CI **7 passed**, process **3294 observations**, zero changed inputs/decisions,
qualified manifests verified. Full gate выполняется. Doctor пока не валидирует новые
applicability edges; это следующий reader-only фрагмент DA10, без изменения writer semantics.

J083 release проверен по `28e9b1eaca25708e6b849b4072cef10eee7c8b83`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40508414239) — **4724 passed**,
strict mypy 429 файлов, Ruff, docs/web success.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40509541174) — exact COMMIT,
`0b05a4f9a51b -> 28e9b1eaca25`, listener up 45 s. Live processing не запускался.

Final acceptance J084: **4735 passed**, failures/errors/skips/xfails 0, **606.726 s**;
focused **46 passed**, strict mypy **430** файлов, Ruff src/tests/tools, format src/tests,
docs/links и diff check green. Результаты **116 native flows** и **106 new associations**
приняты с неизменностью всех старых rows/payload/accounting. Reader J083 сохраняет все
новые заполненные snapshots **побайтно**. Pinned production restore остаётся идентичным J083:
schema 51, SQL/gzip/SQLite delta **0**, никаких автоматических backfill или новых платных calls.
[Acceptance evidence](../assets/audits/2026-10-27-applicability-writer/validation.json)
содержит before/after XML, source hashes, native comparison, rollback и corpus manifests.
Local HTTP не повторён: сетевой CLI/wiring/transports прежние. DA6.4 остаётся частичным:
остальные source associations и legacy import ещё не объявлены принятыми.
План **74 задачи, 14 выполнены, 60 unchecked** (DA8.3 неприменим); goal active.

<a id="j085"></a>
### J085. DA10: read-only source-association graph validation

Дата: 27 октября 2026. База: J084 (`77a86df8b35eaea381a9bb6aae71ad1692a6e99f`). Статус: принято локально; deployment проверяется отдельно.

Computation doctor читает `result_applicability`, сверяет identity JSON/SQL и наличие
валидированного retained result. Invalid/future JSON, несовпадение identity, missing result,
failed result, schema-51 table/column damage дают отдельные findings. Старый schema-50 snapshot
может не иметь новой таблицы. Наличие нынешнего Bill и доступность URL не проверяются:
историческая связь не становится invalid из-за смены owner. В output попадают только row refs,
не metadata source или provider/context payload. State не меняется и work не исполняется.

Новые **13 cases**: на J084 **12 fail / 1 legacy control pass**, без fixture errors.
После реализации **58 focused passed**, strict mypy **431** файлов. Первый Ruff выявил
E501 в тексте exclusion; формулировка сокращена, итоговый Ruff green. Runtime **148 src files**
побайтно прежние, schema **51**, writers/default flags/DDL не меняются.

На **116** actual writer snapshots doctor проверил **106** source associations: errors/warnings 0,
все database hashes прежние. Старые graph fields совпали после исключения только нового counter,
новых source-edge refs и поясняющего exclusion. Первые observer запускались с верными PYTHONPATH;
qualification повторена с явными assertions импортированного src/tools, прежние guards сохранены.
Corpus CI **7 passed**, process **3294 observations**, zero changed inputs/decisions;
qualified manifests verified. Full gate выполняется. Это offline graph QA, без заключения о
юридической применимости текста, source coverage, billing или наличии архива.

Pinned production QA повторена на schema 50 и мигрированной 51 одного state pin `9e836a9`:
**408 unknown legacy refs, 638 saved columns, results/applications/associations 0**.
Errors/warnings 0, DB hashes прежние; все прежние report fields совпали, новая schema добавляет
только applicability counter 0 и пояснение. Никакие legacy refs не объявлены известными.
Это прежний зафиксированный pin, не live production processing или новый cycle.

J084 release проверен по `77a86df8b35eaea381a9bb6aae71ad1692a6e99f`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40510090025) — **4735 passed**,
strict mypy 430 файлов, Ruff, docs/web success.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40510937899) — exact COMMIT,
`28e9b1eaca25 -> 77a86df8b35e`, listener up 45 s. Отдельный live cycle не запускался.

Final acceptance J085: **4748 passed**, failures/errors/skips/xfails 0, **592.703 s**;
focused **58 passed**, strict mypy **431** файлов, Ruff src/tests/tools, format src/tests/tool,
docs/links и diff check green. **116** native snapshots, **106** source associations,
старые graph fields и input DB hashes сохранены. Pinned legacy50/reader51 QA также без writes.
[Acceptance evidence](../assets/audits/2026-10-27-applicability-doctor/validation.json)
содержит before/after XML, source hashes, qualified imports, graph comparison и corpus manifests.
Runtime `src` **148 files** byte-identical J084, migrations/schema/default flags прежние,
state delta **0**; rollback J084 не требует изменения данных. Local HTTP не повторён:
новых сетевых CLI/wiring/transports нет. DA10.2 остаётся частичным для deferred blob coverage
и полной dependency validation. План **74 задачи, 14 выполнены, 60 unchecked**
(DA8.3 неприменим); goal active.

<a id="j086"></a>
### J086. DA6.5: exact legacy inventory and shared context catalogue

Дата: 27 октября 2026. База: J085 (`21d6ea180b9682906dc266aebcf89a7813e18879`). Статус: принято локально; deployment проверяется отдельно.

Новая optional offline CLI `tools.legacy_history` считает сохранённые JSON values и точные
дубли без model defaults, изменения строк/неизвестных fields или назначения paid identity.
Report содержит SHA/size/source refs, без answer bodies; storage policy, import и deletion
не включаются. Наличие receipt и совпадение memo key не доказывают происхождение ответа.
Общий catalogue и encoded snapshot fields вынесены из computation doctor в saved_records;
его существующие решения прежние, узкий default request-doctor catalogue сохранён.

Targeted **60 passed** (16 новых cases + 44 прежних), strict mypy **432** файлов,
Ruff/format green. Первый запуск выявил отсутствие zero counter для пустого inventory и E501;
исправлены output contract/формулировка, первые XML/log сохранены. Проверены unknown fields,
absent vs null, canonical key order, placeholders, receipt-vs-answer, invalid/embedded JSON,
non-finite values, frozen batch inputs, read-only input и CLI output/overwrite guard.
Новая CLI не существовала раньше; import failure не выдаётся за before regression.

На том же production pin `9e836a9`: **408 occurrences / 321 unique JSON records / 87 duplicates**,
все **408 без computation ref**; **638 saved columns**. Occurrence bytes **3,410,164**,
unique values **2,504,003**, canonical array **2,504,325 bytes**, gzip **614,010**.
Все **109 batch rows** содержат только receipts без answer bodies. Это точный inventory
сохранённых values, не 408 независимых paid calls, не подтверждённая provenance или raw archive.
Узкий draft до общего context catalogue (399/318, 517 columns) сохранён отдельно и не принят.

Computation doctor на **116 snapshots** сохранил весь прежний report; request doctor —
**побайтно на 29** normal/negative inputs. Первое сравнение ошибочно взяло старый 13-case
report; переключено на существующий qualified 29-case baseline, все старые fields совпали.
Input DB hashes прежние, runtime **148 src files** byte-identical J085, schema **51**.
Новый inventory также проверен на 116 native snapshots; пустые источники остаются `not_run`.
Corpus CI **7 passed**, process **3294 observations**, zero changed inputs/decisions,
qualified manifests verified. Full gate выполняется; stage state delta **0**.

J085 release проверен по `21d6ea180b9682906dc266aebcf89a7813e18879`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40511928671) — **4748 passed**,
strict mypy 431 файлов, Ruff, docs/web success.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40513350632) — exact COMMIT,
`77a86df8b35e -> 21d6ea180b96`, listener up 45 s. Live processing не запускался.

Final acceptance J086: **4764 passed**, failures/errors/skips/xfails 0, **591.566 s**;
focused **60 passed**, strict mypy **432** файлов, Ruff src/tests/tools, format и docs/diff green.
[Acceptance evidence](../assets/audits/2026-10-27-legacy-history-inventory/validation.json)
содержит XML/source hashes, qualified inventory, old-doctor compatibility и corpus manifests.
Все **148 src files** byte-identical J085; schema/migrations/default flags прежние,
state delta **0**. Rollback J085 не меняет state. Local HTTP не повторён: offline CLI покрыта
subprocess test, сетевые transports/wiring прежние. Legacy import ещё не включён;
ответы не связываются с receipts по предположению. План **74 задачи, 14 выполнены, 60 unchecked**
(DA8.3 неприменим); goal active. Далее — DA3/DA4, перенос selection policy из physical extractor.

<a id="j087"></a>
### J087. DA3/DA4: pure package-selection policy

Дата: 27 октября 2026. База: J086 (`cf757aced644ab1bc28aa2ba6835bb00f2324c0f`). Статус: принято локально; deployment проверяется отдельно.

Роли, выбор главной редакции, supporting/annex membership и прежний string projection
вынесены в `document_candidates.select_document_parts`. Вход — существующий frozen
`ExtractedParts`, лимит `max_part_chars` передаётся явно. Adapter `extract_document` — совместимый
facade над физическим `extract_parts` и чистой policy. Нет новой serialization/schema,
input archive, service/source routing или изменения юридической эвристики в этом этапе.

AST qualification подтверждает идентичность **10 physical methods, 5 physical classes и 16 helpers**;
body policy совпадает после явной замены self parameter и вызова чистой функции. Это structural
proof, дополненный tests/raw replay, а не самостоятельная юридическая приёмка.
Первый focused **112 passed**; затем три boundary cases дали **115 passed** и strict mypy 434.
Полный gate выявил **4 устаревших test seams**: patch document_kind в старом adapter module.
Тест физической границы переведён на реальный новый classifier/policy, проверка не ослаблена;
qualified focused **123 passed**, mypy/Ruff green. First full XML/log сохранены отдельно;
полный gate повторяется после fixture update.

Raw QA охватывает **1865 discovered physical paths**: все **1803 ZIP paths** declared families
и до пяти одиночных файлов на extension/family. Повторы bytes извлекаются один раз,
Root/member limit **32 MB**, expanded archive limit **128 MB** прежние. Parsed outputs сравниваются целиком по SHA,
со state, selected membership и errors. Baseline/current читают отдельные code snapshots,
assert import paths и source/input SHA; проверка ещё выполняется. Первое observer сравнение
ошибочно использовало tuple `(sha,size)` как SHA; исправлено без изменений extractor или corpus.

Meaningful probes на **12 distinct extracted RCL texts <=80k chars**: **12 positive controls**,
**60 mutations** (date/amount/negation/scope/reference внутри первой статьи) на каждой версии;
все material peer pairs — ambiguous. Synthetic Word-XML ZIP frames детерминированы,
XML-invalid controls нормализуются только в harness; corpus и legal strings не меняются.
Это regression slice, не независимые инициативы/holdout и не сохранённый historical input.
Loose-URL policy остаётся отдельным следующим переносом; DA3/DA4 не объявлены завершёнными.

Raw acceptance: **1865 paths / 1837 unique bytes**, включая **1803 ZIP** и **62** одиночных
файла. Все model SHA, selected membership, states и inputs совпали; population перепроверена
после обоих runs. **4 ZIP >32 MB** исключены одинаково, их paths/size/SHA записаны отдельно.
States **1665 unverified / 191 unsupported / 5 ambiguous**, extraction errors **0**;
это этап до source evidence, а не 1861 подтверждённых legal editions.

Cached candidate acceptance: **4276 cases**, все outcomes/fields/input hashes прежние;
**1383 missing cached inputs** сохранены как exclusions, full-population accuracy не заявляется.
Первый current replay отклонён code guard при test-seam update; повтор после freeze принят.
Source corpus/index не менялся. Corpus CI **7 passed**, process **3294 observations**,
changed inputs/decisions **0**, manifests verified.

Повторный full gate: **4767 passed**, failures/errors/skips/xfails 0, **645.660 s**.
Qualified focused **123 passed**, mypy **434** файлов, Ruff src/tests/tools и format green.
Source selection/state writes/schema/default flags вне этого переноса прежние.

J086 release проверен по `cf757aced644ab1bc28aa2ba6835bb00f2324c0f`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40515925751) — **4764 passed**,
strict mypy 432 файлов, Ruff, docs/web success.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40517708448) — exact COMMIT,
`21d6ea180b96 -> cf757aced644`, listener up 45 s. Live processing не запускался.

Final acceptance J087: full **4767 passed**, qualified focused **123 passed**,
strict mypy **434** файлов, Ruff/format/docs/diff green. Raw **1865 paths** и cached **4276 cases**
сравнены без изменения inputs/решений; **12 controls + 60 meaningful mutations** на каждой версии.
[Acceptance evidence](../assets/audits/2026-10-27-pure-package-policy/validation.json)
содержит XML/source hashes, raw/cached coverage/exclusions, structural proof и reproduction.
Schema **51**, migrations/default flags/SQL data прежние, production storage delta **0**;
rollback J086 без state conversion. Local HTTP не повторён: новых сетевых wiring/transports нет.
DA3/DA4 остаются частичными: loose URLs и operation-specific admissibility ещё не перенесены.
План **74 задачи, 14 выполнены, 60 unchecked** (DA8.3 неприменим); goal active.

<a id="j088"></a>
### J088. DA4: common candidates and pure file-version choice

Дата: 27 октября 2026. База: J087 (`23367da5f75cb3d9597102aa499bf6f8fbe597cc`). Статус: принято локально; deployment проверяется отдельно.

`CandidateSet`/`DocumentCandidate` — общий transient snapshot для archive peers и отдельных
файлов. Header/body predicate и version choice принадлежат pure module document_candidates.
Loader владеет download/source order, pre-download official conflict guard и exception handling;
после pure decision дочитывает выбранный комплект и добавляет source evidence как прежде.
Неподходящие bodies отсекаются domain predicate в ходе чтения, чтобы не удерживать их сверх
старого cache footprint. DTO не сохраняет новый text/context в SQL и не назначает provenance.

Typed outcomes: selected input index + observed parts / ambiguous selection / no bill body,
при котором сохраняется прежний fallback. Порядок input priority, признаки versions и схема
DocumentSelection прежние; новый CandidateSet не является legal completeness verdict.

Focused **128 passed** (5 новых file-candidate checks + 123 прежних), strict mypy **434** файлов,
Ruff/format green. Первый Ruff UP037 на quoted forward annotation исправлен через Self.
Проверены sparse indices, равные версии и source priority, material conflict diagnostics,
scan/non-bill fallback, классификация тела отдельно от filename/role label.
Structural proof: source order, pre-download guard и read/exception control flow совпали;
bill predicate равен старому filter, остальные **10 loader methods** побайтно по AST прежние.

Cached QA **4276 cases**, все прежние fields/input hashes/counts совпали; **1383 missing inputs**
остаются exclusions. **12 controls + 60 meaningful mutations** совпали с J087.
Raw baseline переиспользует qualified J087 outputs: их src/tools hashes сначала сверены
с архивом J088 baseline (commit J087), без подмены code provenance. Fresh raw current и full gate
выполняются. Corpus/index не меняется; storage delta **0**, schema/flags прежние.

J087 release проверен по `23367da5f75cb3d9597102aa499bf6f8fbe597cc`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40523700525) — **4767 passed**,
strict mypy 434 файла, Ruff, docs/web success.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40524826351) — exact COMMIT,
`cf757aced644 -> 23367da5f75c`, listener up 45 s. Первый log fetch дал connector internal error;
повтор вернул полный log. Отдельного live processing не было.

Final corpus J088: **1865 raw paths / 1837 unique bytes**, все прежние model hashes/membership/
status и input SHA совпали; final population проверена. **4 ZIP >32 MB** — прежние явные exclusions.
Raw states **1665 unverified / 191 unsupported / 5 ambiguous**, extraction errors **0**;
не объявляются 1861 доказанной legal edition. Cached **4276 cases**, **1383 missing inputs**
сохранены; все прежние fields/input hashes прежние. **12 controls + 60 material mutations**
и Corpus CI **7** / process **3294** приняты без изменения решений; manifests verified.

Full **4772 passed**, failures/errors/skips/xfails 0, **619.138 s**; focused **128 passed**,
mypy **434** файла, Ruff/format green. DA4.3 отмечен выполненным: текущие outcomes сохранены
в обоих переносах, filename/folder/date heuristics не расширены. Это процессная гарантия;
будущее изменение эвристики требует отдельного behavioral replay, а не объявляется выполненным.
План теперь **74 задачи, 15 выполнены, 59 unchecked** (DA8.3 неприменим); goal active.

[Acceptance evidence J088](../assets/audits/2026-10-27-common-candidate-policy/validation.json)
содержит XML/source hashes, I/O/predicate structural checks, full raw/cached parity,
baseline provenance, corpus manifests и reproduction. Docs/links/diff green.
Schema **51**, migrations/serialization/default flags прежние, storage delta **0**;
rollback J087 без state conversion. Local HTTP не повторён: сетевой CLI/wiring прежний.
DA4.1/DA4.2 остаются частичными для полной provenance/dispositions, fallback/composition
и operation-specific admissibility; CandidateSet не выдается за завершённую систему этих правил.

<a id="j089"></a>
### J089. DA4.5/DA4.6: shadow bill-read admissibility

Дата: 27 октября 2026. База: J088 (`1c2ee02`). Статус: принято локально; deployment проверяется отдельно.

Pure functions `prefilter_admission` и `analysis_admission` сохраняют прежние разные правила
операций, включая blocked selection, scan attempts, unverified text и nonempty Word fallback.
OSR остаётся полезным материалом, consultation report — metadata-only для analysis.
Политика `bill-read-v1` пока не импортируется services и не влияет на runtime.

Первый fixture ошибочно отнёс OSR к APPENDIX_KINDS; исправлен на consultation report,
добавлен отдельный positive OSR case, production rule не ослаблялся. Syntax/Ruff tool issues
устранены до запуска gate. Focused **24 passed**, mypy **437** файлов, Ruff/format green.
Новая `admissibility` suite зарегистрирована в corpus_runs и читает bounded gzip caches;
output неизменяемый/outside corpus, код и inputs сверяются, bodies не печатаются.

Shadow corpus: **14691 cached views**, **13454 readable / 1237 empty**, unreadable/limit **0**.
Families: RCL **10418**, RPW **830**, filings **572**, prints **980**, self-amendments **66**,
Senate-root views **1825**. Это views declared roots, не столько независимых инициатив.
На **page probes 0/1/20** и двух operations **88146 decisions** совпали с frozen legacy
branches из baseline services. Page counts условные, selection unverified; historical pages,
полный input и legal completeness не достраиваются. Sources/corpus не менялись.

Все существующие **149 src files** byte-identical baseline, только новый unused policy module
добавлен; source schema/default flags/SQL прежние. DA4.5/DA4.6 остаются
частичными: consumer wiring, comparison/amendments/reviews и их приёмка ещё не включены.


J088 release проверен по `1c2ee02a041b0f8a9d206f3657abb59bbf810a74`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40530394227) — **4772 passed**,
strict mypy 434 файла, Ruff, docs/web success.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40531621459) — exact COMMIT,
`23367da5f75c -> 1c2ee02a041b`, listener up 45 s. Live processing не запускался.

Final acceptance J089: full **4788 passed**, failures/errors/skips 0, **580.923 s**;
focused **24 passed**, strict mypy **437** файлов, Ruff/format/docs/diff green.
Corpus CI **7**, process **3294**, shadow **14691 views / 88146 operation decisions**,
manifest verification и legacy parity приняты; input SHA/решения не изменились.
[Acceptance evidence](../assets/audits/2026-10-27-shadow-admissibility/validation.json)
содержит checks/source hashes, coverage/exclusions и reproduction. Original bodies в артефакт
не включены. Schema **51**, SQL/storage delta **0**; rollback J088 без state conversion.
Local HTTP не повторён: новая policy не подключена к consumers/transports.
План **74 задачи, 15 выполнены, 59 unchecked** (DA8.3 неприменим); goal active.


<a id="j090"></a>
### J090. DA4.5/DA4.6: activate qualified bill-read admission

Дата: 27 октября 2026. База: J089 (`96845ae`). Статус: принято локально; deployment проверяется отдельно.

Prefilter и общий analysis loader делегируют допуск pure policy `bill-read-v1`.
Loader по-прежнему владеет I/O и source exceptions; services выбирают прежние counters,
ошибки, scan/metadata fallback и подготовку текста. Shared analysis path используется также
reanalysis/baseline comparison, committee amendments и supplements; Senate verification
остаётся отдельным существующим контрактом. Review completeness ещё не перенесена.
Новых source branches, SQL writes, prompts, heuristic/selection changes нет.

Focused **104 passed**, strict mypy **437** файлов, Ruff/format green. Существующие сценарии
проверяют textless/thin PDF, Word без страниц, unverified, blocked/missing/oversize/outage,
trimming/input coverage и material reanalysis. Новых mirror tests не добавлялось.
Первый Ruff E501 исправлен сокращением one-line docstring до запуска gate.

**104 native flows**, workers **1/4**, comparison baseline/current: все SQL tables/rows,
profile fields, requests, usage, projection receipts, rendered messages/edits/replies прежние.
Остальные **71 analysis methods / 6 prefilter methods** по AST не менялись; preparation
после admission (trim/budget/projection/scan input) прежняя. Из **150 src files** изменены
только два consumer modules; physical extraction/selection/policy byte-identical baseline.

Shadow replay **14691 views / 88146 decisions**, CI slice **7**, process **3294**: прежние
observations/input SHA, manifests verified. Первый CI запуск с external corpus root дал
`not_run` (fixture manifest отсутствует); evidence сохранён, принятый rerun использует
`tests/fixtures/corpus_check`. CLI reproduction J089 исправлена: `run --suite admissibility`.
Schema **51**, storage delta **0**, rollback J089 без state conversion.


Дополнительная before/after матрица J090: **64 public-pipeline flows** — 8 body/page fixtures ×
workers 1/4 × sync/batch × нейтральный/релевантный заголовок. Между runs dump восстановлен;
все reports, exact SQL dumps, contexts, requests, usage и reader messages/edits совпали.
Fixtures: плотный bill, thin PDF, Word fallback, пустой PDF/Word, consultation report, OSR,
короткий Word. Это проверка регрессий на объявленных fixtures, не independent legal holdout.


J089 release проверен по `96845aee5dc86ada15582e7a6d61858c8560b7c0`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40535414490) — **4788 passed**,
strict mypy 437 файлов, Ruff, docs/web success.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40536672677) — exact COMMIT,
`1c2ee02a041b -> 96845aee5dc8`, listener up 45 s. Отдельный live processing вручную не запускался.


Final acceptance J090: full **4788 passed**, failures/errors/skips 0,
**587.892 s**; focused **104 passed**, strict mypy **437** файлов, Ruff/format/docs/diff green.
[Acceptance evidence](../assets/audits/2026-10-27-admission-consumers/validation.json)
содержит checks/source hashes, **104 native + 64 boundary flows**, corpus manifests и reproduction.
Необязательный local HTTP не повторён: CLI/network wiring и transports прежние.
План **74 задачи, 15 выполнены, 59 unchecked** (DA8.3 неприменим); goal active.


<a id="j091"></a>
### J091. DA4.5/DA4.6: review admission and the complete operation matrix

Дата: 27 октября 2026. База: J090 (`c14d3f4`). Статус: принято локально; deployment проверяется отдельно.

Pure `review_admission` (`review-read-v1`) сохраняет expected-digest/commission-role checks
и прежнюю complete/partial классификацию. В отличие от analysis, короткий Word остаётся partial;
partial не становится автоматическим отказом consultation review. Snapshot branch,
commission purpose gate и paused flags прежние. Политика вызывается из review reader после I/O,
без repository access. [Матрица пяти операций](../reviews/2026-10-27-document-admission.md)
фиксирует shared analysis/baseline/committee path и отдельный строгий Senate contract.

`text_digest` перенесён из analysis service в pure `document_fingerprints`, алгоритм и prefix
`text-v2:` буквально прежние; public import из analysis сохранён alias. Legacy whitespace
helper внутри analysis остаётся для своего старого compatibility case, без изменения политики.
При подготовке первый mypy выявил ошибочный import digest из sections; исправлен переносом
единственной реализации. Первый tool fixture имел misplaced assertion; исправлен до corpus/full gate.
Неуспешные logs сохранены. Baseline review observer сначала не задал обязательные Bill поля;
qualified rerun использует явные status/time из FixedClock.

До подключения review consumer принят shadow: **32 focused passed**, mypy **438** файлов,
Ruff/format green. **14691 views / 396657 decisions**, все девять role probes × pages 0/1/20
совпали с frozen legacy branches; **14691 text-v2 digests** совпали. Service class methods
до activation по AST прежние. Shadow source snapshot/manifest сохранены отдельно; после
подключения policy и fingerprint SHA не менялись. Исторические роли/pages не восстанавливались.

После подключения focused **252 passed**. Public review_context before/after: **864 cases**,
workers 1/4 × два активных purposes × девять ролей × восемь body/page fixtures × expected
none/matching/mismatch. Все contexts/complete/source calls совпали; SQL не менялся,
provider/Telegram calls отсутствуют. Act application остаётся paused, новых calls не разрешено.

**168 native flows** (104 предыдущих + 64 boundary) совпали по всем profile fields и SQL rows,
requests, usage, receipts, contexts и reader messages. Qualified J090 current использован как
baseline только после проверки SHA всех **150 src files** против git archive J090.
Кроме review reader остальные **71 analysis methods**, включая общий bill loader, прежние.
**12 actual cached texts / 12 controls / 60 meaningful mutations**: fingerprints обеих версий
одинаковы и каждая дата/сумма/отрицание/scope/reference меняет identity. Это regression probes,
не независимые инициативы или legal holdout. Финальная corpus приёмка принята.
Schema **51**, storage delta **0**, rollback J090 без state conversion.


J090 release проверен по `c14d3f4fc84cd6fb0f577606abea3ef164a182a9`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40537407667) — **4788 passed**,
strict mypy 437 файлов, Ruff, docs/web success.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40538667148) — exact COMMIT,
`96845aee5dc8 -> c14d3f4fc84c`, listener up 45 s. Отдельный live processing вручную не запускался.

Corpus final J091: bill admission **14691 views**, CI **7**, process **3294**, Senate **816**;
input hashes/observations совпали с квалифицированными previous runs. Senate replay читает
**1825 files**, outcomes **701 selected / 23 scan pending / 2 no unique date / 90 not amendments**,
**4903 mutation probes**. Scan pending не объявляются подтверждёнными. Общие пути corpus/suite
пересекаются; coverage не складывается в число независимых инициатив. Все manifests verified.
Bill observations/counts прежние; metadata replay scope уточнена, поскольку сам replay
не меняет runtime policy, уже включённую для bill consumers в J090.


Final acceptance J091: full **4800 passed**, failures/errors/skips 0,
**610.257 s**; focused **252 passed**, strict mypy **438** файлов, Ruff/format/docs/diff green.
[Acceptance evidence](../assets/audits/2026-10-27-review-admission/validation.json)
содержит source/XML hashes, pre-activation shadow manifest, 864 public review cases,
168 native flows, fingerprint mutations, Senate/corpus parity и reproduction.
DA4.5/DA4.6 отмечены выполненными: pure admission/matrix приняты для действующих операций;
полная запись решения, provenance и DocumentEdition остаются DA4.1/DA4.2/DA4.4/DA4.7.
Raw ZIP reparsing и local HTTP не повторены: physical extraction/file choice, CLI/network
wiring/transports прежние. Schema **51**, production storage delta **0**.
План **74 задачи, 17 выполнены, 57 unchecked** (DA8.3 неприменим); goal active.


<a id="j092"></a>
### J092. DA4.4: compatible selection-policy version reader

Дата: 27 октября 2026. База: J091 (`e554f1e`). Статус: принято локально; deployment проверяется отдельно.

`DocumentSelection.policy_version` — optional bounded opaque identifier; отсутствующий legacy
version остаётся None и не добавляется в JSON. Неизвестные будущие версии читаются без
догадок об их алгоритме. Метка не заменяет facts и не повышает selected; without_text
сохраняет её. Producer, backfill, migration и новые policy decisions не включены.
Совместимый reader выпускается перед будущим metadata writer.

Focused **107 passed**, strict mypy **439** файлов, Ruff/format green; **11** новых контрактных
cases проверяют legacy shape, roundtrip/text removal, unknown versions, state/evidence и bounds.
На изолированном pinned state **9e836a9** сравниваются **457 Bill / 166 analyses**: все model
JSON hashes baseline/current прежние, dump побайтно прежний, source не менялся. Будущая version
явно добавлена только в один scratch fixture; save/restore/resave и text removal сохраняют её,
остальные analysis fields прежние. Это не утверждение о версии исторического анализа.

**168 native flows** совпали по всем profiles и exact SQL dumps, requests, usage, receipts,
contexts и reader messages. Qualified J091 current использован после сверки SHA всех
**151 src files** с git archive J091. Corpus candidate replay принят.
Schema **51**, legacy state storage delta **0**, runtime source flags прежние.

Новая J091 matrix добавлена в nav и тематические обзоры по docs/authoring.md; статус/commit
указаны явно. Устаревшее «архитектура ещё не реализована» в development overview заменено
ссылкой на фактически поэтапную реализацию и журнал. Не заявляется завершение всего плана.


J091 release проверен по `e554f1e7bea4fcdddb9f90d9a7d152684040a33d`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40540766269) — **4800 passed**,
strict mypy 438 файлов, Ruff, docs/web success.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40542000049) — exact COMMIT,
`c14d3f4fc84c -> e554f1e7bea4`, listener up 45 s. Отдельный live processing вручную не запускался.

Corpus final J092: **4276 candidate cases** baseline/current, **1383 missing inputs** сохранены;
все decisions/fields/read inputs/counts прежние. CI **7** / process **3294** совпали, manifests
verified. Structural AST proof: после удаления нового optional field весь models/documents.py
равен baseline; остальные **150 src files**, включая adapters/services/pure policies/migrations,
побайтно прежние. Новых consumers этого metadata поля нет.


Final acceptance J092: full **4811 passed**, failures/errors/skips 0,
**611.361 s**; focused **107 passed**, strict mypy **439** файлов, Ruff/format/docs/diff green.
[Acceptance evidence](../assets/audits/2026-10-27-selection-policy-reader/validation.json)
содержит XML/source hashes, structural proof, legacy/future scratch reader checks,
168 native flows, 4276 candidate cases и reproduction. Runtime writer не включён;
legacy policy unknown не backfilled. Schema **51**, storage delta **0**; rollback J091
без state conversion. Будущий writer откатывается не ниже J092 compatible reader.
Raw parsing/local HTTP не повторены: physical selection code и network CLI/transport прежние.
План **74 задачи, 17 выполнены, 57 unchecked** (DA8.3 неприменим); goal active.


<a id="j093"></a>
### J093. DA4.4: version new source-selection decisions

Дата: 27 октября 2026. База: J092 (`d99702c`). Статус: принято локально; deployment проверяется отдельно.

Final TextLoader source-selection decision получает `document-selection-v1` после завершения
существующего I/O/policy пути. Старое тело select_document перенесено в private helper буквально;
новый wrapper меняет только metadata immutable LoadedFile/DocumentSelection. Physical cache
остаётся без final-source label. Метка проходит text и заново создаваемые scan/metadata selections,
batch/result/projection и restore. Никаких новых heuristics, acceptance of legal editions или
политики чтения старого dump. Она обозначает текущее решение, не время исходного paid analysis.

Первый mypy fixture передал FakeGateway вместо callable download; исправлен до gate.
Focused **106 passed**, strict mypy **440** файлов, Ruff/format green. **7** новых cases:
cache/download identity, pre-download source conflict, material version ambiguity,
workers 1/4 × text/scan batch → immutable result → projection → restore.

**168 native flows / 20 SQL tables**: requests и rendered messages/edits точны, остальные
profile/SQL значения равны после удаления только новой selection label и её доказуемых
SHA/observation-ID производных. SHA пересчитываются из конкретных selection manifests;
произвольные hashes не игнорируются. **11** selection SHA pairs / **13** observation-ID pairs.
Operator reply model содержит новую метку, его rendered text прежний. Первые comparison
observers уточнены для nested snapshot storage metrics и metadata в reply model; failed logs
сохранены, условия requests/rendered text не ослаблены. Прирост на этих fixtures:
SQL **0–332 bytes**, gzip **0–30 bytes**; **28** dumps без прироста. Это не production extrapolation.

Переход J092 → J093: **4** worker/text-scan cases, старые paid results остаются побайтно прежними,
нет model/triage/batch/reader calls. Один новый source-metadata association, далее stable.
Первый observer неверно требовал exact dump сразу после второго ручного analyze:
baseline тоже догоняет projection receipt на втором вызове. Control обеих версий:
generation **2/2/2**, applications **3/4/4**; второй/третий restored dump точен.
Нового generation/application поведения нет. Дополнительный upgrade delta относительно
такого же baseline: SQL **681–776 bytes**, gzip **129–138 bytes**.

Structural proof: old selection body exact после private rename, остальные **10 loader methods**
и **63 AnalysisService methods** прежние. `_load_text`/`_load_scan` отличаются только передачей
policy metadata и shortened docstring; pure policy module равен без новой constant.
Models/migrations/adapters прежние. Corpus **4276 cases / 1383 missing inputs**, CI **7**,
process **3294** приняты без изменения решений/input SHA; manifests verified.
Rollback reader J092 читает **168** actual new snapshots, **134** Bill policy labels,
все computation JSON values и exact read/restore SQL dump сохранены, без state conversion.

J092 release проверен по `d99702cedfc4e530a0edcc5de93895301d86430a`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40542786243) — **4811 passed**,
strict mypy 439 файлов, Ruff, docs/web success.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40544073131) — exact COMMIT,
`e554f1e7bea4 -> d99702cedfc4`, listener up 45 s. Отдельный live processing вручную не запускался.
Compatible reader floor принят до выпуска producer. Schema **51**, archive/backend flags прежние.


Final acceptance J093: full **4818 passed**, failures/errors/skips 0,
**601.328 s**; focused **106 passed**, strict mypy **440** файлов, Ruff/format/docs/diff green.
[Acceptance evidence](../assets/audits/2026-10-27-selection-policy-writer/validation.json)
содержит source/XML hashes, narrowly normalized SQL/hash graph proof, upgrade baseline control,
actual J092 rollback, corpus replay и размер metadata. Сначала подготовлен/выпущен совместимый
reader, затем producer. Полная decision history/DocumentEdition и необъявленные scopes остаются
открытыми. План **74 задачи, 17 выполнены, 57 unchecked** (DA8.3 неприменим); goal active.


<a id="j094"></a>
### J094. DA2.1/DA10: source footprint, current storage profile and a recorded production cycle

Дата: 27 октября 2026. База: J093 (`83eadcf`). Статус: в работе.

Read-only `measure_referenced_files` сопоставляет explicit physical fingerprints в сохранённых
selection/scan/review metadata с version-1 inventory. Общие saved_records/encoded snapshot
walker, SqliteSnapshot и artifact/code guards используются без копии parser logic.
Outer archive container считается один раз; text/law SHA и unscoped legacy scan digest не
выдаются за raw identity. Source/input scan bytes учитываются раздельно; manifest не означает
retention. Unknowns, missing hash matches, malformed data и empty scope различаются.
Output не содержит bodies/URLs, не перезаписывает inputs/evidence и не входит в corpus.

Первые E501 исправлены сокращением report strings. Первые **29 focused** прошли; по реальным
данным обнаружен недосчёт legacy scans: selection не равен source/input manifest. Добавлен
case с непустым selection без scan_input, счётчик исправлен, code fingerprint/guard добавлен.
Первый full gate остановлен SIGINT перед изменением, partial XML/log сохранены; accepted
repeat — **30 focused**, strict mypy **441** файлов, Ruff/format green, full выполняется.
Первоначальный tool и reports сохранены отдельно; итоговый `referenced-files-qualified.json`
пинует исправленный producer. Runtime src, schema и настройки хранения не меняются.

Новый state pin **e3e262b841805ee549f1d3dcdcbba2a77d1f6ee6** (27.10 16:51:55 UTC): SQL
**19751140 bytes**, SHA `babfb88f163cfe2823256684d09561f13ae23e5840fa5b711d3d444a9d2edec5`.
Восстановлена изолированная `/private/tmp/lexinform-j094/production.db`: **20758528 bytes**,
SHA `b49b2a24c7c6b390fcc928b48c73a4d0fd32d7c3cbb6f58641baa3ca603c2cfa`.
**458 bills / 378 runs**, source/SQLite неизменны. Никаких pipeline/paid/reader вызовов вручную.

Fresh five-family inventory: **14516 files / 11593280468 bytes**, **12366 unique / 11107528218**.
С J039 +12 files / +8754532 bytes; duplicate bytes прежние **485752250**. Для полной заявленной
страты добавлены actual catalogued Senate originals через existing senate_sources/digest/totals:
**1825 files / 479946679 bytes**, missing **0**. Six-family total **16341 / 12073227147 bytes**,
**14191 unique / 11587474897**, duplicates **2150 files / 485752250 bytes**. Это available
research inventory, не объём реально потреблённого production raw или прогноз архива.
Index SHA прежний `2030ab1d13f961fcb09bd58247cbcf97aba3a3575725eed166940620f4c60666`.

**651 saved columns / 416 saved answer occurrences / 141 selections**: **437 raw reference
occurrences / 44 unique SHA**, из них inventory matches **13 / 9585013 bytes**, unmatched **31**.
У **5 saved scan occurrences** нет source/input manifest. Они не восстановлены по text_sha/URL.
Ноль retained scan manifests в этой выборке не доказывает полный zero input footprint.
Legacy exact inventory: **415 occurrences / 325 unique / 90 duplicates**, **3434138 occurrence
bytes / 2512584 unique**, canonical unique array gzip **616106 bytes**. Known result refs **3**,
unbound **412**; новый immutable computation учитывается footprint отдельно, не создаёт paid IDs
для старых memo. Полный исторический поток скачиваний из таких сохранённых данных неизвестен.

Actual first-parent state history с 13.10: **261 commits / 261 SQL blobs**, endpoint SQL
**15258350 → 19751140 bytes**, внутри периода **14513298–46427458**. Daily gzip endpoints
измерены отдельно; спад 23→24.10 исключает линейный daily-growth прогноз. Локальная существующая
раскладка диапазона **811 objects / 1790413580 disk bytes**. В отдельном temporary non-thin pack
те же **811 objects**: **38411619 pack + 23780 index bytes**, **41.688 s**, SHA
`791809b13f8c0513074fe86455fd2818064a657304b9a08ce133ed5ddc7ad07c`.
Source Git store/refs/config не перепаковывались; temporary files удалены. Это не GitHub network,
remote storage или self-contained backup всей истории; packing dependencies описаны в report.

Fresh local checkpoint benchmark: restore **0.269059 s**, dump **0.104655 s**, eight checkpoints /
seven commits, changed **0.995456–1.202567 s**, unchanged **0.240596 s**. Три synthetic report
pairs: +**5178 SQL / 175 gzip bytes**. Temporary local objects **36752402 → 4049834 bytes**
after local GC; remote local-test objects **36752402 → 4050316**. Транспорт filesystem-only,
не production latency; pre-send/receipt checkpoints не объединялись.

В реальном сохранённом **WPL/UD491**: validated metadata-only analysis, known input binding/
request checksum, immutable result, collection ready, projection applied, тот же computation ID
в Bill и frozen new_bill snapshot. В БД sent/message ID **141**, publication **217**; channel
content независимо не читался. Result ID
`result-v1:072ab7e466c32fa05951bd10df3703bbdeb52cbf7892fa81b71c49f282914e77`.
Computation/request doctors — **0 errors / 0 warnings**; work doctor — **3 old warnings**
publication unavailable, точное совпадение с 9e836a9. Work rows не удалялись. Это bounded
history acceptance, не file extraction, legal accuracy или остальных purposes/families.

[Профиль хранения](../reviews/2026-10-27-storage-profile.md) фиксирует текущую SQLite, pending
обязательства, unknown legacy границы, измерения и дальнейшие metadata/retention действия.
S3/backend/raw retention не выбираются. DA2.1 остаётся частичным для полного наблюдаемого спроса;
DA2.3/DA2.5 metadata и DA3–DA7 можно продолжать на этом профиле.

J093 release проверен по `83eadcf87c71fb3d5aa26f6378aebddb3e51bcaf`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40546706506) — **4818 passed**,
strict mypy 440 файлов, Ruff, docs/web success.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40548007636) — exact COMMIT,
`d99702cedfc4 -> 83eadcf87c71`, listener up 45 s. Отдельный live processing вручную не запускался.

J094 завершён. Финальный guard отвергает неверную форму inventory до чтения DB; отдельный test
добавлен после полного промежуточного gate (4827). Accepted final: **31 focused / 4828 full**,
**0 failures/errors/skips**, full **607.871 s**, strict mypy **441**, Ruff/format green.
Final footprint metrics совпали с qualified report; runtime **151 src files byte-identical**.
Corpus CI **7** и process **3294**: manifests verified, observations/inputs unchanged versus J093.
[Acceptance asset](../assets/audits/2026-10-27-storage-profile/validation.json) пинует producer,
данные и проверки; interrupted/промежуточные evidence сохранены, не выданы за final.


<a id="j095"></a>
### J095. Source decision metadata и observations: совместимый reader

Статус: **завершён**, baseline `44d8a2f4e74a006c521a84f8d63472131024ff37`.
Продолжение DA2.3/DA2.5 на выбранной SQLite; новые production producers пока выключены.
Данные не выводимы из существующей БД: отвергнутые/ожидающие source decisions и прежние
официальные relations могут исчезнуть без paid result. Применён migration skill: append-only
v52, без backfill/понижения schema; источники и потребители анализов не меняются.

`DocumentSelectionRecord` адресует точное metadata view с question/policy/membership/role/
version/completeness/facts/coverage/losses. Extracted bodies и поздний provider scan input
запрещены; factory снимает их явно. Evidence quotes остаются частью решения. Unknown policy
остаётся unknown, future policy opaque. `SourceObservation` адресует owner/purpose и исходные
официальные `TextDocument` relations с selection ref. First recording time исключено из ID.
Одинаковое решение разделяется несколькими observations без потери дел/relations; новая SHA по
прежнему URL не затирает старую запись. Это не legal edition, artifact/raw store или paid identity.

Две таблицы с FK, owner/selection indexes и SQL identity columns. Атомарный repository command
повторно валидирует входы, проверяет SQL/JSON identities и сохраняет первое время без нового
row/dump изменения. Caller transaction rollback откатывает обе таблицы. Чтение, corruption,
restore, разные owners/URL и changed bytes покрыты отдельными поведениями. Legacy v20 fixture
удаляет обе новые таблицы перед объявлением старой версии; v1 restore проверяет пустой reader.

Fresh state **2f310768040c39deb87f9de1e40e1440cb396aad** восстановлен изолированно в
`/private/tmp/lexinform-j095`: все прежние rows и **458 Bill JSON** точны, integrity ok,
foreign-key errors **0**. V51→52: **+814 SQL / +201 gzip bytes**, **22 tables / 35 explicit
indexes / 8 FK**. Один synthetic selection + две owner observations: **+2217 SQL / +484 gzip**;
повторная проверка dump exact. J094 reader восстанавливает populated v52 dump побайтно без
schema conversion. Production pipeline/source/model/Telegram вручную не запускались.

**168 native flows**, workers 1/4, direct/batch/reuse/restore/handoff: requests/rendered messages,
все прежние profile fields и **20 старых SQL tables точны**. Две новые таблицы пусты во всех
flows; SQL +814 bytes, gzip +116–132. Первое comparison ожидало прежний набор таблиц внутри
profile `data`; исправлен observer с явным отдельным assertion пустоты новых таблиц, старые
rows всё равно сравниваются без исключений. Runtime consumer behavior не включён.

J094 CI **40557403601** выявил недостающий type guard перед `json.loads(column.raw)` в новом
footprint tool; deploy **40557578548** skipped. Локальный incremental mypy пропустил ошибку.
Добавлен явный `str|bytes|bytearray` guard и case с malformed non-TEXT SQLite value; прежний
invalid_json outcome сохранён. Новая проверка mypy выполняется **--no-incremental**. Никакие
runtime service/extractor semantics этим исправлением не меняются.


Корпусная metadata qualification: **4276 cached RCL cases**, **4071** с source decision,
**205** без кандидата. Captured states: selected **2273**, unverified **925**, unsupported
**669**, waiting **81**, ambiguous **123**; outcomes не менялись. **3139** unique decisions /
**3139** owner/catalog observations, metadata JSON **8538729 bytes**, изолированный SQL
**13188275 / gzip 1447431 bytes**. Это упражнение на всех исторических стадиях, не production
backfill или прогноз прироста. Repeat checks и restore дают exact dump; source decision metadata
сохранены точно после явного удаления body/provider scan input. **1383 missing inputs** остаются
missing; cached role/completeness не объявлены independent legal/raw acceptance. Guard пинует
**7600 inputs**, corpus INDEX/code не менялись. Corpus CI **7** / process **3294** verified,
inputs/observations same versus J094. Все reports и observer остаются отдельными evidence.

J095 accepted gate: **75 focused / 4840 full**, **0 failures/errors/skips**, full
**584.843 s**, strict mypy **443** (`--no-incremental`), Ruff/format green.
[Acceptance asset](../assets/audits/2026-10-27-source-observation-reader/validation.json) сохраняет
producer/evidence hashes, размеры, паритет, ограничения и reproduction. Никакие reader payload,
selection/extraction outcomes или production writer flags не менялись.


<a id="j096"></a>
### J096. Source decisions text prefilter: запись без paid result

Статус: **завершён**, baseline `2a98e294c19555a8a140d940e7129c7bb035948a`.
Первый production producer schema-52 metadata history: worker load передаёт paired source
catalog/decision metadata; calling-thread `decide` сохраняет их и прежний status outcome одной
transaction. Clock внедрён через Container. Старое keyword/admission body перенесено без
семантических изменений; source selection/extraction, requests и renderer не менялись.

Нейтральный keyword miss сохраняет адресуемое решение и official relations, хотя paid analysis
не было. Scans/unsupported и остальные возвращённые selection outcomes также сохраняют
metadata; outage/missing fetch не выдумывают complete source evidence. Body/provider scan input
не копируются. Повторная check сохраняет первое время и exact dump, status failure откатывает
обе metadata таблицы. Workers 1/4 записывают на caller в input order; отдельные scenario tests
проверяют retained rejection, scan, unreadable, outage, repeat и transaction rollback.

Первый mypy потребовал явного narrowing optional prefilter service в tests; добавлены assertions
с гарантией World default. Accepted focused **36**, strict mypy **444** (`--no-incremental`),
Ruff/format green; полный gate выполняется. Source consumers analysis/review ещё не подключены,
legacy/paid IDs и raw retention не backfilled.

**168 native flows** против J095: все requests/rendered messages, остальные profile fields и
**20 прежних SQL tables точны**. **32** metadata observations / **32** decisions в **32** final
snapshots; все IDs/selection refs валидны. SQL delta **0–2794 bytes**, gzip **0–751**; snapshots
с одинаковыми историями не считаются независимым production объёмом. Decision body и post-load
admission body AST exact; `run/check/problem` методы не менялись.


J095 release `2a98e294c19555a8a140d940e7129c7bb035948a` принят:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40559791312) **4840 passed**,
mypy/Ruff/docs/web success. [Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40560731917)
показал exact COMMIT, `83eadcf87c71 -> 2a98e294c195`, listener up **45 s**. J094 CI failure
исправлен и принят этим gate; отдельный production processing вручную не запускался.


Accepted full gate: **4849 passed / 0 failures/errors/skips**, **593.324 s**; **36 focused**,
strict mypy **444**, Ruff/format green. Actual public prefilter replay — **4276 RCL cases**:
accepted/status/reasons/hits и все **20 прежних SQL tables exact**. Input hashes равны;
**1383 missing** сохранены. Новая история **3139 decisions / 3139 observations**, SQL delta
**13176684 bytes / gzip 1445169 bytes** относительно того же fixture DB без history. Это
исторические cached stages, не прогноз production growth. Restore dump exact. Corpus CI **7** /
process **3294**: verified, observations/inputs same. Metadata/exclusions и воспроизведение —
[acceptance asset](../assets/audits/2026-10-27-prefilter-source-history/validation.json).


<a id="j097"></a>
### J097. Будущий downloader demand без хранения файлов

Статус: **завершён**, baseline `7bc500630d665537114fb0210ce0acd07f02d1a6`.
Продолжение DA2.1: новый optional RunReport `document_reads`, отдельный `DocumentReadMonitor`
port, shared TextLoader producer. Pipeline сбрасывает счётчики перед work и забирает snapshot
в finally перед finish-run checkpoint; cache не очищается. Старый report остаётся unknown,
а новый quiet run имеет observed zero. SQL schema/retention не менялись; metrics живут с runs.

По host считаются attempts, completed bodies/bytes, failed и over-limit; отдельно cache hits,
количество и bytes distinct completed bodies внутри run. SHA→size живёт только в bounded-by-work
RAM счётчике; в JSON нет URLs, raw bytes или individual SHA. Hash вычисляется с завершённого
ответа, не partial download. Ошибка extraction всё равно сохраняет известный download объём;
успешный download не выдаётся за нормативный текст. Scan derivatives не являются новой сетью.
Host/unique counters синхронизированы тем же lock; snapshot не разделяет mutable dict с loader.

Новые **26 focused** проходят: cross-URL/host duplicates, cache/reset, failed/over-limit,
extraction failure, scan window, parallel 1/4, persistent reports/restore, quiet repeat, outage,
legacy unknown и dry-run rollback. Strict mypy **447** (`--no-incremental`), Ruff/format green.
CLI gate первоначально не мог bind 127.0.0.1 в sandbox; повтор с разрешённым local fixture —
**21 passed**, без внешних source/model/Telegram calls. Initial error logs сохранены.

**168 native flows**: exact raw requests/rendered messages и **22 SQL tables** с единственной
нормализацией `RunReport.document_reads` в распознанном report object. **452** metric object views
validated; это повторные views, не independent production runs. SQL delta **188–672 bytes**,
gzip **68–96**. Initial observers не учитывали runs table digest и admission sql_sha256; hashes
не служат исключением underlying rows: все SQL rows проверены, только новый report field снимается.
Прежние source decisions, paid results, inputs, publications и остальные report fields exact.

J096 reader восстанавливает **168 populated snapshots / 324 legacy report views** без потери
SQL и schema conversion. AST qualification после удаления только IO-counter statements:
прежние loader methods/выбор/извлечение и pipeline run body exact; остальные pipeline/container
methods прежние. Corpus CI **7** / process **3294** verified, observations/inputs same.

Это downloader response bytes, не HTTP/TLS traffic, не неизвестные partial/refused bytes,
не cross-run distinct archive size. Direct gateway bypasses и отдельные CLI вызовы вне pipeline
не входят. Natural production daily profile ещё не измерен; ручной production run не запускался.
DA2.1 остаётся partial, source/backend/raw retention не выбираются.

J097 accepted full gate: **4864 passed / 0 failures/errors/skips**, **575.204 s**.
**26 focused / 21 local HTTP**, mypy **447**, Ruff/format и docs checks green.
[Acceptance asset](../assets/audits/2026-10-27-document-read-metrics/validation.json) пинует
checks/source/observer и точные границы metric normalization, bytes и restore.


<a id="j098"></a>
### J098. Read-only диагностика source history и downloader demand

Статус: **завершён**, baseline `8c343d61da2e7969add1f7c753085b8702785a7d`.
Новые `tools.source_history_doctor` и `tools.document_read_profile` используют общий immutable
SQLite snapshot, saved-column reader и exclusive artifact writer. Footprint inventory читает
также unpaid `document_selections`; ни один runtime src не менялся — **153 byte-identical**.

Source doctor проверяет model/canonical ID, SQL identity columns, selection FK и unreferenced
metadata. Legacy schema 51 без таблиц — not_run, отсутствие таблиц в v52 — error. Current owner/
source не переоцениваются; unreferenced не означает разрешение удалить. Download profile считает
только известные метрики: legacy/absent/unsupported/invalid и observed zero различаются. Неполный
object, missing host counter, URL вместо host и boolean schema version не получают zero defaults.
Группировка — run-start day UTC, naive date остаётся unknown; cross-run distinct не выдумывается.
Все outputs без file bodies/URLs/quotes, readonly, без extra downloads/model/Telegram calls.

**35 focused / 4888 full**, **0 failures/errors/skips**, full **584.072 s**; mypy **449**
(`--no-incremental`), Ruff/format green. Первые lint findings исправлены до accepted gate.
**168 native snapshots / 324 measured report views / 32 source-observation views** прошли оба
doctors без errors/warnings. Corpus metadata **3139 decisions / observations**: selected **1683**,
unverified **653**, unsupported **652**, ambiguous **101**, waiting **50**; IDs/refs valid,
unreferenced **0**. Это cached fixture history, не independent raw/legal acceptance или traffic.
Corpus CI **7** / process **3294**: verified, observations/inputs same. Все input SQL unchanged.

Fresh natural state **a317e8e7db9f7d6659e802ca4ba650aecc9d6352**, изолированная копия
`/private/tmp/lexinform-j098/production.db`: **462 bills / 380 runs / 2 source decisions /
2 observations / 2 computations / 4 applications**. SQL **20144370 bytes**, SHA
`58f911cd2fec80f032e6b9c6174fe2a6d1163142b7790a6f493a2daf7eac61f2`; SQLite **21184512**, SHA
`b208e39fd83d75560fc4144f023efd3304db8e3927d28849347c73160a1fab00`. Integrity ok, FK errors **0**,
source doctor **0 errors**, policy recorded **2**, selected **1**, unverified **1**.

Это реальные новые prefilter observations для **2995 (committee_report)** и **RCL/12416153**,
оба current batch_pending без analysis. Сохранённые metadata существуют до получения paid result;
completed model/publication cycle для них не заявлен. Только четыре metadata INSERT строки:
**12597 SQL bytes / 3756 whole-dump gzip delta** при снятии этих строк в памяти; DB/dump не
редактировались, общий рост state этим не объясняем. Download profile — **not_run**: **379**
неизмеренных reports и одна строка без готового report; старым runs не приписаны нулевые bytes.
Production вручную не запускался, прочитан лишь existing natural checkpoint.

Fresh source footprint: **662 columns / 149 selections / 450 raw refs / 48 unique SHA**,
**13 inventory matches / 9585013 bytes**, unmatched **35**; legacy scans без manifest **5**.
Из добавления нового catalogue на том же DB/inventory: +2 selection columns, +2 selections,
+5 physical ref occurrences, **0 new unique SHA / 0 matched-byte delta**. Старый producer взят
из pinned file и отдельно SHA-пинован; shared-workspace fingerprint не выдаётся за его provenance.
Raw availability/production retention не выводятся из этих refs; download missing originals не запускался.


[Acceptance asset](../assets/audits/2026-10-27-source-diagnostics/validation.json) содержит source/
evidence hashes, tests, runtime parity, production/corpus границы и reproduction.
J097 release `8c343d61da2e7969add1f7c753085b8702785a7d`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40564214424) **4864 passed**, strict mypy
447, Ruff/docs/web success; [deploy](https://github.com/sobolevbel/lexinform/actions/runs/40565174373)
exact COMMIT, `7bc500630d66 -> 8c343d61da2e`, listener up **45 s**. Это подтверждение relay,
не ручной production processing или принятие непроверенной source family.


<a id="j099"></a>
### J099. Artifact identity/size metadata и SQL part refs: совместимый reader

Статус: **завершён**, baseline `d4b01069d764981dc0bcf69c9b61c8bfc979e63d`.
Migration workflow: append-only **v53**, `artifacts` (complete-body SHA, byte_size, first recorded_at)
и `selection_artifacts` (selection/scope/index → outer artifact). Из старых text/law hashes,
page counts или aggregate bytes точный размер не выводим; legacy не backfilled. Part refs —
нормализованный immutable metadata index с FK/uniqueness, не persisted derived business status.
Body bytes, locators, backend и retention promises не добавляются; production producers выключены.

Модели отделяют physical identity от первого recording time. Repo повторно валидирует copies,
сохраняет первое UTC время при повторе, отвергает SHA/size conflict. Link требует существующие
selection/artifact и точный outer-body SHA того же scope/index; member SHA не заимствуется.
Caller transaction rollback покрывает все четыре metadata tables; SQL restore точен.

Fresh natural state **dc97f63814eaa967287e7f8c834798d86b88888e** восстановлен изолированно в
`/private/tmp/lexinform-j099`: **462 Bill JSON и все прежние rows exact**. Пустая схема
**+709 SQL / +173 gzip bytes**, integrity ok / FK errors **0**, **24 tables / 37 explicit
indexes / 10 FK**. Synthetic fixture body известен точно (**28 bytes**, SHA вычислен с этих
bytes); не приписан размер настоящему production SHA. Один artifact/ref/selection/observation:
**+1777 SQL / +436 gzip bytes**; повтор dump exact, body не хранится. J098 reader сохраняет
populated v53 dump побайтно, без downgrade/conversion.

**168 native flows** против byte-identical runtime J097 (J098 менял только tools): все requests,
rendered messages, прочие profiles и **22 прежних SQL tables exact**; две новые таблицы пусты.
SQL +709 bytes, gzip +137–156. Corpus CI **7** / process **3294** verified, observations/inputs same.
Extraction, source policy, prepared input, provider payloads и runtime producers не менялись.

J098 [CI 40567293985](https://github.com/sobolevbel/lexinform/actions/runs/40567293985) остановился
на type check нового source doctor: reused `expected` имел два tuple типа. Deploy 40567449129
skipped. Переменные разделены; три typed tools явно включены в mypy files рядом с src/tests.
Это расширение проверки, без ignores/suppressions. Cold check **454** files проходит как с
рабочим PYTHONPATH, так и без него (CI-equivalent). Первоначальное объяснение J094/J095 про
incremental cache было гипотезой: `--no-incremental` сам по себе не обеспечил проверку импортируемых
tool bodies. Теперь их explicit targeting проверяет саму реализацию в стандартном gate.

Первый full J099 остановлен SIGINT до исправления; partial logs/XML сохранены как interrupted.
Accepted повтор включает fixed tool/config и новый reader; полный gate выполняется. Production
pipeline/paid/source/Telegram work вручную не запускались.

J099 accepted qualified gate: **110 focused / 4899 full**, **0 failures/errors/skips**,
**593.533 s**. Mypy с explicit tools **454** files проходит с/без PYTHONPATH; Ruff/format
green. [Acceptance asset](../assets/audits/2026-10-27-artifact-metadata-reader/validation.json)
пинует код/данные/проверки, совместимость и границы первого recording time/нехранения raw.


<a id="j100"></a>
### J100. Capture known complete-body metadata без сохранения файлов

Статус: **завершён**, baseline `8f86c1b6d443f6d509d90d57255553111473fd53`.
Shared loader возвращает `ArtifactIdentity` уже завершённых downloader bodies; повторного
скачивания/hash calculation нет. Отдельный `DocumentArtifactObserver` port передаётся pipeline.
Known SHA/size хранятся в RAM без bytes и переживают counter reset/cache reuse, включая dry run.
Run IO counters сохраняют прежнюю per-run семантику. `DocumentPart.source_sha256` централизует
тот же container-if-members выбор, не добавляя поля в сериализованный manifest.

Caller-owned `services.document_history` пишет metadata с caller Clock и exact outer-body refs.
Prefilter source decision/refs/status — одна transaction; прочие known bodies — finish-run
transaction с report/учётом batch costs. Archive member не выдаётся за outer source. Размеры
legacy/partial/failed bytes не угадываются. Completed unsupported body сохраняет physical metadata,
не становясь нормативным текстом. Current/unknown source policy и paid input IDs не меняются.
Standalone consumers и crash-complete capture до final checkpoint ещё не заявлены.

**43 focused / 4907 full**, **0 failures/errors/skips**, full **590.253 s**; mypy **456**
с/без PYTHONPATH, Ruff/format green. Tests покрывают prefilter miss до paid result, title hits,
first recording, warm-cache/dry-run rollback, complete unreadable vs failed fetch, workers 1/4,
outer/member identity и неизменность serialized fields. Старый status-failure test также
проверяет rollback новых metadata/refs через ту же transaction.

**168 native flows**: все requests/rendered messages, остальные profiles и **22 прежних SQL
tables exact**. **194 artifact-row views / 28 links**, все models/outer refs valid; повторные views
не считаются independent sources. SQL delta **130–539 bytes**, gzip **11–77**. J099 reader:
**168 populated snapshots / 324 report views**, SQL exact без schema conversion. AST qualification:
старое download processing после снятия одного known-map update exact; прежние loader/repo
methods сохранены, serialized DocumentPart fields и validators прежние, migration не добавлялась.

Real raw corpus: **36 unique files**, ZIP **11**, DOC **5**, DOCM **2**, DOCX **6**, PDF **9**,
XML **2**, ODT **1**; RCL **21**, prints **6**, filings **2**, self-amendments **5**, RPW **2**.
Стратифицированный sample ≤1.5 MB плюс nested-member candidates ≤5 MB из pinned J087 population;
не полный raw corpus, Senate/большие файлы не объявлены принятыми. Actual bounded parser/loader,
surrogate source URLs; **140 exact outer-body links**, unlinked fingerprints **0**, errors **0**.
Read/selection/text/manifest fields и input hashes baseline/current exact. Никаких external
source downloads или body export; это metadata identity qualification, не independent legal gold.
Corpus CI **7** / process **3294** verified, observations/inputs same.

J099 [CI](https://github.com/sobolevbel/lexinform/actions/runs/40570435112) **4899 passed**, strict
mypy **454**, Ruff/docs/web success; [deploy](https://github.com/sobolevbel/lexinform/actions/runs/40571176565)
exact COMMIT `8f86c1b6d443f6d509d90d57255553111473fd53`, listener up **45 s**. J098 type failure
исправлен этим accepted gate. Отдельный production processing вручную не запускался.

[Acceptance asset](../assets/audits/2026-10-27-artifact-metadata-capture/validation.json) пинует
source/evidence, gates, native/raw parity и ограничения metadata capture/retention.


<a id="j101"></a>
### J101. Artifact metadata diagnostics и отдельный size catalogue

Статус: **завершён**, baseline `cd9b7f3a5e9f8c7f5defc4c31e455bfe8a4a3604`.
Общий typed reader валидирует recorded artifact identity/size/time. Source doctor проверяет
schema-53 tables, FK targets, part scope/index и exact outer-body SHA; schema 52 не объявляется
повреждённой из-за отсутствия будущих tables. Unlinked rows — inventory, не разрешение GC.
Download profile отдельно показывает global known unique bytes и UTC день первой записи.
Unavailable catalogue и существующий empty catalogue различаются; first recording не выдаётся
за дату скачивания, а metadata за retained bytes. Runtime: **155 src files byte-identical J100**,
схема/flags/ports/services не менялись. Четыре typed tool modules явно включены в mypy.

**35 focused / 4919 full**, 0 failures/errors/skips; strict mypy **457** files с/без PYTHONPATH,
Ruff/format green. **168 native snapshots / 324 measured report views / 32 source observations**:
оба doctors без ошибок/warnings, input SQL unchanged. Cached RCL graph **3139 selections/observations**
без ошибок; это fixture metadata, не independent raw/legal acceptance. Corpus CI **7** и process
**3294** verified, observations/input hashes same.

Exact natural state `dc97f63814eaa967287e7f8c834798d86b88888e` восстановлен standard SQLite без
current migrations: schema **52**, DB SHA `154d52897f68672453931cebb16c3ef3cdf5e674cc7b418e6d2ba63aca64fe8c`,
unchanged. Source history **2/2**, errors **0**. IO: **1 measured quiet-zero report**, **379 unknown**,
**1 row without saved report**; artifact catalogue unavailable. Не утверждаем production empty
catalogue или обычную download нагрузку. Production processing, source fetch, paid calls и sends
вручную не запускались.

J100 [CI](https://github.com/sobolevbel/lexinform/actions/runs/40574136223): **4907 passed**, mypy456,
Ruff/docs/web success. [Relay deployment](https://github.com/sobolevbel/lexinform/actions/runs/40575257249):
exact COMMIT `cd9b7f3a5e9f8c7f5defc4c31e455bfe8a4a3604`, `8f86c1b6d443 -> cd9b7f3a5e9f`, up **45 s**.
[Acceptance asset](../assets/audits/2026-10-27-artifact-diagnostics/validation.json) содержит hashes,
gates, scope и qualified snapshots. DA2/DA10 остаются частичными в явно перечисленных границах.


<a id="j102"></a>
### J102. Source observations для primary analysis и standalone calls

Статус: **завершён**, baseline `2cc16ddb9d2351b441593df9ed47bb8b11f395d4`.
First/re-analysis/explanation формируют immutable source metadata до input shaping, отдельно от
paid selection/computation ID. Worker передаёт metadata и known physical identities, caller
записывает их существующим atomic repository contract. Ошибки paid preparation возвращают тот же
тип/reason после сохранения source decision; outage до completed selection не создаёт observation.
Batch-ready result без нового read не получает вымышленное observation. Повтор сохраняет первое
время, текущий paid identity не заменяется current-policy source ID.

Admission/shaping body и already-loaded paid preparation body перенесены AST-exact; parser,
policy, prompt/provider payload и schema не менялись. `_load_text` facade сохраняет прежние типы
исключений для secondary readers. `Clock` вызывается только на caller thread. Metadata сохраняются
после возврата prepare; crash внутри prepare всё ещё может потерять source observation —
pre-paid durable checkpoint здесь не заявлен. Новых bodies/backend/retention pins нет.

**90 focused** passed, включая prefilter/source-history interactions; strict mypy **458** files
с/без PYTHONPATH, Ruff/format green.
Initial focused assertions исправлены: quiet re-analysis уже обновлял current selection pointer,
поэтому проверяется сохранность computation/request ID и ответа; standalone discovery fixture
получил past since. Это исправления ожиданий/arrange, не изменение runtime policy. Initial Ruff
нашёл имя exception без Error suffix; исправлено до accepted gate, suppressions не добавлялись.

**168 native snapshots**: все requests/rendered messages, прежние profiles и **20 business tables
exact**. Все ранее записанные metadata rows сохранены; source doctor **0 errors**. Повторные views:
**198 source observations / 186 selections / 178 part refs**. SQL delta **0–4046**, gzip **0–607** bytes.
J101 reader восстанавливает **168 snapshots / 324 run report views** с exact SQL, без conversion.
Corpus CI **7** / process **3294**, inputs/observations same, manifests verified.

Real raw **36 files**, тот же pinned bounded sample J100: actual parser + full fake-native analysis
через surrogate print sources; **36 observations**, Bill/reports/contexts/requests/messages exact,
кроме фактического `report.phase_seconds` (две небольшие duration разницы). Это не legal gold для
RCL/других source families и не full raw population. INDEX/raw input hashes exact, network/paid
calls/body export отсутствуют. Полный accepted gate: **4933 passed**, 0 failures/errors/skips, **585.287 s**.
[Acceptance asset](../assets/audits/2026-10-27-analysis-source-history/validation.json) пинует
source/evidence hashes, qualified runtime, scope и воспроизводимые observers.


J101 release verification: [CI](https://github.com/sobolevbel/lexinform/actions/runs/40576840731)
**4919 passed**, strict mypy **457**, Ruff/docs/web success.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40578004317): exact COMMIT
`2cc16ddb9d2351b441593df9ed47bb8b11f395d4`, `cd9b7f3a5e9f -> 2cc16ddb9d23`, relay up **45 s**.
Это проверка release relay, не ручная production обработка.


Первый full J102 завершился одним failed expectation в prefilter scan test: теперь существуют
prefilter и analysis observations с **одним exact selection ID**. Проверка обновлена на обе
стадии и сохранность общей physical identity/отсутствие provider input в source metadata.
Runtime не менялся после квалификаций; прежний failed full XML/log сохранён. Accepted повтор
проверяет финальные tests; исключений/xfail не добавлено.


Уточнение границы evidence J102: формулировка «body export отсутствует» была неточной.
Ignored local replay JSON сохраняли полные fake-provider contexts из 36 raw files, включая
scan payloads: baseline/current artifacts **4152689 / 4152663 bytes**, по **187661 text chars**
в **24** непустых text contexts, плюс scan data. Эти файлы — локальные qualification artifacts,
не новые production rows или raw retention policy; в Git сохранены только hashes/counts.
Пинованные evidence не переписаны. Следующий raw replay сравнивает canonical payload SHA в RAM
и сохраняет только fingerprints/counts. Production metadata и измеренные SQL deltas J102 прежние.


<a id="j103"></a>
### J103. Общее source-read ядро и observations для review

Статус: **завершён**, baseline `fa3e2d99a64a794b842f790b78579c2fe865fe97`.
`DocumentSourceRead` и `record_source_read` вынесены в общий service module; prefilter и primary
analysis используют тот же DTO/metadata transaction contract. Review worker возвращает исходный
file selection/known outer identities отдельно от прежнего ReviewDocument. Caller сохраняет
metadata в input order, включая source с refused digest/role. История не утверждает успешный
review admission и не relabels unknown policy. Saved ReviewDocument reuse не создаёт новый read.
Models/schema/ports/policies/prompt/provider payload не менялись; остальные **152 src files exact**.
Admission/context body перенесён AST-exact, остальные прежние history functions не менялись.

**161 focused** passed; strict mypy **459** с/без PYTHONPATH, Ruff/format green.
Отдельная prototype copy до применения patch: **10 new / 30 shared / 121 existing review** tests
passed. Первоначально review fixtures не включали purpose в `batch_kinds`; arrange исправлен до
accepted focused gate. Это не обход feature flags в runtime.

**168 прежних native cases**: все profiles/requests/messages/**20 business tables exact**, все
старые metadata rows сохранены, doctor errors **0**, SQL/gzip delta **0**. Новая matrix **32 cases**:
два purposes, workers1/4, compare modes, digest mismatch и snapshot reuse. Context/requests/messages/
20 business tables exact; network call **multisets** exact. Два различия порядка параллельных
скачиваний исключены из ordering oracle; caller source-row order проверен отдельно и совпал с input.
**48 review observation views**, **24 unknown policy views** без relabel; doctor **0 errors**.
SQL delta **0–7022 bytes**, gzip **0–1013**, старые metadata rows preserved.

Raw sample **36** тот же J100: surrogate print/response consumers, **26 ReviewContexts**,
**36 new review / 72 total source observations**. Canonical comparison hashes всех Bill/report
(кроме actual phase timings)/contexts/requests/messages/ReviewContext exact; INDEX/raw hashes exact.
Новый raw output содержит fingerprints/counts, **не input bodies**; payload для сравнения собран
только в RAM. Это не независимая legal acceptance response role или full raw population.
J102 reader сохраняет **200 SQL snapshots / 356 run reports** exact; schema conversion отсутствует.
CI corpus **7** / process **3294** verified, inputs/observations same. Полный gate:
**4943 passed**, 0 failures/errors/skips, **591.344 s**.
[Acceptance asset](../assets/audits/2026-10-27-review-source-history/validation.json) пинует
source/evidence, storage deltas, сравнения, rollback и scope.


J102 [CI](https://github.com/sobolevbel/lexinform/actions/runs/40580538381): **4933 passed**,
strict mypy **458**, Ruff/docs/web success. [Relay deploy](https://github.com/sobolevbel/lexinform/actions/runs/40581654012):
exact COMMIT `fa3e2d99a64a794b842f790b78579c2fe865fe97`, listener up **45 s**.
Production processing/paid calls/Telegram sends вручную не запускались.


<a id="j104"></a>
### J104. Generic secondary source observations

Статус: **завершён**, baseline `878f889f7183c90dc94739a72716014da91cf845`.
Committee amendments, amendment revision, recommendation и supplement generic reads используют
тот же caller-owned source-history helper. Metadata сохраняются до full-input budget refusal или
model context; caller Clock, first-record preservation и exact known outer-body refs прежние.
Memo reuse без нового read не создаёт observation. Senate special path и historical comparison
workers не изменены. AST всех прежних AnalysisService methods exact после раскрытия call adapters;
остальные **154 src files exact**, schema/models/ports/extractor/policies/payloads не менялись.

**64 focused** passed; strict mypy **460**, Ruff/format green. Production-profile tests проверяют
history до secondary batch submit, workers1/4/caller writes, повтор и restore, отдельный recommendation,
budget refusal, no new paid verdict для unreadable supplement и outage. Initial expectations
уточнены под прежний contract: parser exception до selection не создаёт completed decision;
пустой readable body даёт bare SupplementRecord, а не DocumentSelectionError. Runtime fallback
не менялся, exception/xfail suppression не добавлялись.

**168 native cases**: requests/messages/profiles/**20 business tables exact**, old metadata preserved,
doctor **0 errors**. **206 observation / 194 selection / 186 part-ref views**. SQL delta **0–2088**,
gzip **0–272** bytes. J103 reader восстанавливает **168 SQL snapshots / 324 reports** exact.
Corpus CI **7** / process **3294** verified, inputs/observations same.

Real raw: **72 views / 36 unique files**, generic amendments/supplement consumers с fake metadata-only
primary. Все semantic fingerprints exact (Bill/report кроме phase_seconds/answer/model contexts/
requests/calls/messages), INDEX/raw hashes exact; **72 source observations / 280 exact outer refs**.
Прежние outcomes: **43 returned / 17 ValueError / 12 DocumentSelectionError**. Это исходы разных
input-budget/admission ветвей, не 29 новых багов; baseline/current совпали. SHA/size каждого linked
artifact проверены по фактическим raw bytes. Новый output сохраняет fingerprints/counts, не payloads.
Scope — surrogate source roles, не independent legal gold и не full population. Полный gate:
**4951 passed**, 0 failures/errors/skips, **605.132 s**.
[Acceptance asset](../assets/audits/2026-10-27-secondary-source-history/validation.json) содержит
source/evidence hashes, проверки, storage deltas, backward reader и границы.


J103 [CI](https://github.com/sobolevbel/lexinform/actions/runs/40582322249): **4943 passed**,
strict mypy **459**, Ruff/docs/web success. [Relay deploy](https://github.com/sobolevbel/lexinform/actions/runs/40583433623):
exact COMMIT `878f889f7183c90dc94739a72716014da91cf845`, listener up **45 s**.
Production pipeline/paid calls/reader sends вручную не запускались.


<a id="j105"></a>
### J105. Физический source receipt для Senate special path

Статус: **завершён**, baseline `b28a898fddf013eb1c1f2f69bf9541becb0487e4`.
`resolution_source_read` формирует physical metadata с question `senate_resolution_source`,
policy `senate-source-observation-v1`, state **unverified** и unknown role. SHA actual loaded bytes
отделён от ожидаемого SHA в `TextDocument.senate`; subject/date/fullness/visual admission не
подменены этим receipt. Text helper имеет optional caller observer после существующего read;
scan использует уже возвращённые original SHA/pages. Дополнительных fetch/parse calls нет.
Observed reader-text SHA не доказывает extraction/normative coverage; provider scan input не
копируется в source metadata. Ни одно поле paid input/result identity не меняется.

**96 focused / 4960 full**, 0 failures/errors/skips; full **608.091 s**. Strict mypy **461** с/без
PYTHONPATH, Ruff/format green. Tests: workers1/4/caller-only writes, text/scan, actual vs expected
SHA после смены bytes, no new model call при отказе, first recording/exact repeated dump, outage.
Resolver AST exact; verified-text/analysis methods AST exact после удаления observer hooks.
Остальные **153 src files byte-identical**, schema/models/ports/extractors/payloads прежние.

**168 native cases**: requests/messages/profiles/**20 business tables exact**, old metadata preserved,
doctor **0 errors**. **210 observation / 198 selection / 190 part-ref views**, SQL delta **0–4343**,
gzip **0–673** bytes. Backward reader J104: **168 native SQL snapshots / 324 report views** и
отдельный corpus metadata snapshot с **724 observations** читаются и dump exact, без conversion.

Senate corpus: **816 semantic cases** current/baseline exact, включая прежние negative controls и
meaningful mutation probes. Active factory/writer — **724 selected sources: 701 text / 23 scan**;
actual SHA/size каждого linked artifact проверены по сохранённым original bytes. **724 selections /
724 observations / 724 artifacts / 724 refs**, source doctor **0 errors**, SQLite integrity/FK ok.
Metadata-only graph: **1735985 SQL / 326647 gzip bytes**, restore exact. Owner keys здесь явно
qualification-only; real production owners/paid results не импортированы. Использован cached
extracted text вместе с actual originals и прежними replay download bounds; fresh extraction,
OCR/visual verification и independent legal/source-family acceptance не заявлены.
Corpus CI **7** / process **3294** verified, inputs/observations same; Senate manifest verified.

[Acceptance asset](../assets/audits/2026-10-27-senate-source-history/validation.json) пинует code/
evidence hashes, scopes, cohort sizes, exact SHA/size links, gates и backward-reader qualification.
Unselected candidates и incomplete reads не получают вымышленный completed source receipt.

J104 [CI](https://github.com/sobolevbel/lexinform/actions/runs/40584670845): **4951 passed**,
strict mypy **460**, Ruff/docs/web success. [Relay deploy](https://github.com/sobolevbel/lexinform/actions/runs/40585767033):
exact COMMIT `b28a898fddf013eb1c1f2f69bf9541becb0487e4`, `878f889f7183 -> b28a898fddf0`, up **45 s**.
Production processing/paid calls/reader sends вручную не запускались.


<a id="j106"></a>
### J106. Requested URL → complete-body receipt: совместимый reader

Статус: **завершён**, baseline `c60f078ec138191e92ed9709e4f41d2f7c7a7ea8`.
Append-only migration **v54**, `artifact_reads`: immutable URL/raw SHA/size receipt с FK к известному
artifact. Это не выводимое из старых selections наблюдение HTTP body: старым hashes/URL не
приписаны неизвестные bytes или даты. Новая таблица пуста; production producer выключен.
`ArtifactReadIdentity` не требует Clock, `ArtifactReadReceipt` получает aware caller time.
ID исключает recorded_at; повтор сохраняет первое время и exact dump, aliases и changed body по
одному URL остаются разными. Parent artifact size обязателен и совпадает, copied inputs revalidated,
SQL identity columns проверены при read; bytes/redirect target/retention promise не добавлены.
Unique URL/SHA index одновременно покрывает URL lookup, отдельного дублирующего URL index нет.

**77 focused / 4973 full**, 0 failures/errors/skips, full **596.880 s**. Strict mypy **462** с/без
PYTHONPATH, Ruff/format green. V1/v20 legacy restore coverage обновлены; все прежние repository
methods и **53 deployed migration scripts AST exact**, appended ровно один script. Все services,
parsers/policies/payloads byte-identical; прочие **151 src files exact**.

Fresh natural pin `fe7036a09d1688d03236c936cda26c876f3ae825`: SQL **20313116 bytes**, SHA
`ab3347e4d5f480f7befbc84c02e6c0e20a5726bfde79f1f0f5ab7d04cc1e865b`. Изолированная миграция
**v53→v54**, все прежние rows и **462 Bill reads exact**, integrity ok / FK errors0. Empty schema
**+401 SQL / +106 gzip**. Parent+receipt fixture: известные **29 bytes**, SHA вычислен с literal
fixture (не приписан production SHA), **+389 SQL / +186 gzip**. Повтор/restore exact; body отсутствует.
J105 reader сохраняет populated v54 SQL exact без conversion. **168 native snapshots**:
requests/messages/profiles и **все 24 прежних tables exact**, новый table empty; delta401 SQL,
gzip46–70. Corpus CI **7** / process **3294** verified, inputs/observations same.

Точная natural copy без миграции отдельно проверена: schema53, **382 runs / 5 computations /
2 source observations / 0 artifacts**; source doctor0 errors. Один measured quiet-zero report,
**380 unmeasured / 1 row without saved report**; known-empty catalogue не заменяет download history.
Для **RCL/12416153** совпали source/paid physical membership (3 parts), immutable result/request
checksum, тот же result ID в frozen publication225: DB `sent`, message144. Для **2995** (1 part)
анализ score2, ниже min_score3, публикации нет. Это DB evidence реального сохранённого цикла,
не независимая Telegram/legal/source-family acceptance; processing/models/sends вручную не запускались.

[Acceptance asset](../assets/audits/2026-10-27-artifact-read-receipts/validation.json) пинует source/
evidence, migration/native/storage gates, backward reader и границы natural-cycle evidence.

J105 [CI](https://github.com/sobolevbel/lexinform/actions/runs/40587723748): **4960 passed**,
strict mypy **461**, Ruff/docs/web success. [Relay deploy](https://github.com/sobolevbel/lexinform/actions/runs/40588556473):
exact COMMIT `c60f078ec138191e92ed9709e4f41d2f7c7a7ea8`, listener up **45 s**.


<a id="j107"></a>
### J107. Caller-owned capture complete-response URL/SHA receipts

Статус: **готово**, baseline `cf185a03f32834e58ea87c0bf91b252ca485906e`.
TextLoader сохраняет только clock-free `ArtifactReadIdentity` (requested URL/SHA/size), без bytes
или worker Clock. Registry обновляется вместе с прежними download counters под тем же lock;
known metadata переживают counter reset/warm cache/dry-run. Отдельный `DocumentFileReadObserver`
port подключён к pipeline. Source workers передают matching URL/outer-SHA receipts через общий
DTO; caller source transaction и finish-run пишут их с Clock. Parent artifact size/first time
проверяются existing reader. Parser failure до selection сохраняет complete response на finish-run,
не становясь accepted legal source; failed/over-limit fetch не создаёт receipt.

Вне этих boundaries standalone reads и crash до recording ещё не покрыты; Container.close не
получает скрытых writes. Raw bytes, HTTP headers, source dates, backend/flags не добавлены.
Source/policy/input/result IDs не меняются. AST download processing и прежних service methods
exact после удаления observation hooks; models/schema/extractors/admission/payloads неизменны.

**78 focused / 21 local HTTP CLI** passed, strict mypy **463** с/без PYTHONPATH, Ruff/format green.
Tests: workers1/4/caller writes, parser vs fetch outage, size limit, aliases/changed body, dry-run
rollback + warm cache и standalone positive primary source transaction. Initial test использовал
несуществующий root over_limit counter; исправлен на сумму известных per-host outcomes до gate.

**168 native cases**: requests/messages/profiles и **24 прежних tables exact**, изменения только
в `artifact_reads`: **270 receipt views**, SQL delta **271–1116 bytes**, gzip **57–235**.
Source doctor **0 errors**; J106 reader сохраняет **168 SQL / 324 report views** exact.
Real raw **36 files**: read/selection/text/manifest fingerprints baseline/current exact; requested
URL/raw SHA/size каждого receipt проверены по actual bytes; **140 source part refs**, unlinked0.
Surrogate URLs, не independent legal gold или full population; output body-free, inputs unchanged.
Corpus CI **7** / process **3294** verified, inputs/observations same. Полный gate: **4981 passed**,
0 failures/errors/skips, **585.888 s**.

[Acceptance asset](../assets/audits/2026-10-27-artifact-read-capture/validation.json) пинует
исходники, native/raw fingerprints и локальные gates.

J106 [CI](https://github.com/sobolevbel/lexinform/actions/runs/40592311832): **4973 passed**,
strict mypy **462**, Ruff/docs/web success. [Relay deploy](https://github.com/sobolevbel/lexinform/actions/runs/40593342334):
exact COMMIT `cf185a03f32834e58ea87c0bf91b252ca485906e`, listener up **45 s**.


<a id="j108"></a>
### J108. Read-only diagnostics of recorded complete-response receipts

Статус: **готово**, baseline `1b3381b785eb41420cadcb433e1c2e11d4cb99a5`.
Общий metadata reader проверяет canonical read ID, aware recording time и поля receipt.
Source doctor проверяет presence/size parent artifact; отсутствие receipt table в schema54 —
ошибка, в schema53 — unknown legacy. Ошибки выводятся по row/identity без URL/body.
Download profile отдельно считает receipts, unique requested URLs, unique body identities/bytes
и UTC день первой записи. Aliases не удваивают unique bytes; новый body по прежнему URL остаётся
отдельным. Unavailable и known-empty inventories различаются; повреждённые receipts не входят
в byte estimates. Это metadata inventory, не HTTP traffic, source publication date или raw archive.

**50 focused** passed, strict mypy **463**, Ruff/format green. Новые tests покрывают damage,
missing parent/size conflict, schema absence, aliases/changed bodies, UTC boundary, missing-vs-zero
и read-only/no-URL output. На **168 saved native snapshots / 270 receipt views** оба reader
проходят без ошибок и сохраняют каждый input database byte-identical. Все **155 runtime files**
byte-identical J107. Natural state `fe7036a09d1688d03236c936cda26c876f3ae825`, original schema53,
без миграции: receipt history unavailable, один measured quiet-zero, 380 unmeasured reports и
одна row без report; findings0. Полный gate: **4996 passed**, 0 failures/errors/skips, **577.035 s**.

[Acceptance asset](../assets/audits/2026-10-27-artifact-read-diagnostics/validation.json) пинует
исходники, проверки и qualification script.

J107 [CI](https://github.com/sobolevbel/lexinform/actions/runs/40594687259): **4981 passed**,
strict mypy **463**, Ruff/docs/web success. [Relay deploy](https://github.com/sobolevbel/lexinform/actions/runs/40595808772):
exact COMMIT `1b3381b785eb41420cadcb433e1c2e11d4cb99a5`, listener up **45 s**.


<a id="j109"></a>
### J109. Durable single-message editorial history and post-retention amendments

Статус: **готово**, baseline `1763f80e3b431b81c33e847ba9445f235516a7e9`.
Schema55 добавляет content_snapshots/content_versions/publication_content: frozen editorial
Bill/change/metadata-plan отдельно, canonical version с previous/reason/known paid/event refs,
language/formatter при известности, exact rendered text или явный unknown legacy. Plan не
копирует body snapshots, повтор не меняет первую запись; binding проверяет original owner,
physical target, Bill/change/text/context. Publication IDs переживают operator deletion;
FK на semantic history сохранены, cascade от operational publication намеренно отсутствует.

Caller producer сохраняет одиночный amendments status update до pending/checkpoint и amendments revision
до edit. После retention `/amendments` восстанавливает original из history без current Bill.
Незавершённая/unknown revision по-прежнему блокирует новый force/model call. Legacy без обоих
snapshots, grouped/multipart/digest originals сохраняют прежний отказ. Длинная revision использует
прежнюю physical queue, без новой full-content-history гарантии. Нет raw archive или массового
backfill; missing old HTML/language/formatter не подставляются из текущего кода.

**118 focused / 21 local HTTP** passed; strict mypy **467**, Ruff/format green. Tests: exact JSON
и unknown fields, snapshot dedup, parent/owner/frozen semantic guards, atomic rollback, restore,
operator deletion, missing legacy, 181-day cleanup, repeated/unknown force и successive editions.
Первый full остановлен до изменения runtime: дополнительная проверка обнаружила, что raw
serialization сравнение context ошибочно отвергало бы старую version после добавления optional
DeliveryPlan defaults. Теперь typed compatible comparison не переписывает source JSON; отдельная
регрессия проверяет это. Финальный full выполняется заново с замороженными sources.

**168 native snapshots**: requests/messages/semantic profiles и **25 прежних tables exact**, кроме одного
report phase `commands` **0.0→0.1 s** и timing `seconds` соответствующего command outcome; 2 version /
6 snapshot / 2 binding views. Delta SQL **1008–11920 bytes**, gzip **160–1294**. Дополнительная
матрица **16 amendments histories** (workers1/4, attention off/on, one/two force, sent/unknown):
requests/messages/edits/outcomes exact, все прежние rows exact; timing differences отсутствуют в окончательном replay. **36 versions**, один Bill snapshot на history; delta SQL
**17349–22488**, gzip **1562–2127**. Это fake-source acceptance, не independent legal/Telegram gold.

Fresh fetched state остаётся `fe7036a09d1688d03236c936cda26c876f3ae825`, schema53 →55 в isolated copy:
все прежние rows и **462 Bill reads exact**, integrity/FK ok. Empty DDL **+1409 SQL/+304 gzip**
от production schema53 (включает ранее выпущенный v54); synthetic content fixture history
**+12536 SQL/+3315 gzip** сверх той же physical publication. Repeat/restore/post-retention
history exact; source/model/Telegram/production processing не выполнялись.


J108 [CI](https://github.com/sobolevbel/lexinform/actions/runs/40596202242): **4996 passed**,
strict mypy **463**, Ruff/docs/web success. [Relay deploy](https://github.com/sobolevbel/lexinform/actions/runs/40597163569):
exact COMMIT `1763f80e3b431b81c33e847ba9445f235516a7e9`, listener up **45 s**.


Дополнительный storage gate выявил, что широкий capture всех status updates копировал бы
мегабайты исторического `review_work`. Этот вариант не выпущен. Producer ограничен стадиями
поправок; чистый editorial Bill view исключает только unfinished `review_work`/`ready_analysis`,
сохраняя остальные frozen facts/unknown fields и не изменяя operational owners/original payload.
На **47 retained natural Bill snapshots** все остальные поля exact: **7282970→1616447** occurrence
bytes без operational work. Из 60 status rows: 13 без payload, 2 unsupported shape, 40 не о
поправках, **5 eligible originals**. Их **15 unique snapshots / 121862 UTF-8 / 19766 gzip bytes**;
с semantic+rendered content на original **24034–32423 bytes**. Это inventory, не выполненный
backfill и не прогноз нагрузки. Новые tests защищают как body projection, так и реальную команду
на legacy snapshot с большим неиспользованным ready input. Остальные release checks повторяются
на окончательных sources; полный gate и hashes находятся в `out/corpus/j109-release`,
metadata/native/migration qualification — в `out/corpus/j109-qualified`.


Окончательный full: **5026 passed**, 0 failures/errors/skips, **604.343 s**.
[Acceptance asset](../assets/audits/2026-10-28-content-history/validation.json) пинует sources/gates,
контроль размера, native parity, migration и backward reader. **DA7.3 выполнен** для existing
single-message amendments scope; broad source/application/history kinds остаются DA7.1/DA7.2.


<a id="j110"></a>
### J110. Read-only editorial history and confirmation diagnostics

Статус: **готово**, baseline `8f32030f59e83256698b7c40d19aa815b7a71cf1`.
Новый `tools.content_history_doctor` читает checkpointed snapshot через shared SqliteSnapshot;
models content и чистый editorial Bill view переиспользованы. Отдельные helpers читают records,
проверяют frozen context/physical bindings и строят confirmation graph. Canonical IDs/SQL columns,
semantic parents/kinds, previous/root и ещё сохранённый delivery payload проверяются без repair.
Current — только connected confirmed chain; unknown edit, отсутствующий original или corruption
не становятся новой current version. Operator decisions проверяют SQL publication/command IDs.

Отчёт показывает version/source/result/application refs, content checksum/size, physical attempts
и confirm/retry/dismiss; bodies/URLs/prose reasons не выводятся. Opaque unknown refs/labels остаются
в SQL и получают counts/warnings в отчёте, без переопределения identity. Missing schema54 tables и
known-empty schema55 различаются. Output immutable/separate, input read-only; никакого execution,
нового runtime writer, migration, backend или paid/source/Telegram call.

**23 focused** passed, strict mypy **469**, Ruff/format green. Tests: sent vs unknown current,
consistent-but-wrong content, ID/columns/parent corruption, current schema damage vs legacy absence,
known-empty inventory, operator confirmation/mismatched audit, missing original, opaque refs,
positive input identity и output guards. Новый tool включён в explicit mypy targets.
Полный gate выполняется; qualification проверяет 184 saved snapshots/38 version views.


На **184 saved snapshots / 38 version views** inspector прошёл без errors/warnings; все input DB
сохранены byte-identical, source URLs в output отсутствуют. **157 runtime files byte-identical**
J109. Natural exact copy `fe7036a09d1688d03236c936cda26c876f3ae825` остаётся schema53: tables
history отсутствуют, status `not_run`, errors0; migration/import/processing не запускались.

J109 [CI](https://github.com/sobolevbel/lexinform/actions/runs/40600942088): **5026 passed**,
strict mypy **467**, Ruff/docs/web success. [Relay deploy](https://github.com/sobolevbel/lexinform/actions/runs/40601871710):
exact COMMIT `8f32030f59e83256698b7c40d19aa815b7a71cf1`, listener up **45 s**.


Полный gate: **5049 passed**, 0 failures/errors/skips, **603.282 s**.
[Acceptance asset](../assets/audits/2026-10-28-content-history-diagnostics/validation.json) пинует
source/config hashes, gates, qualification и natural legacy boundary.


<a id="j111"></a>
### J111. `/delivery` references and shared editorial confirmation policy

Статус: **готово**, baseline `9fd58f6aea3142a400e23ad8147d03c35681dd9e`.
Новые ContentVersionSummary/ContentHistoryView — body-free DTO: original/current/inspected IDs
ссылаются на один набор unique metadata summaries, без повторных rendered/source bodies.
Optional CommandOutcome field отсутствует в legacy JSON. Read-only service берёт physical
publications из immutable content bindings, не из очищаемого retry-plan revision_of. Он проверяет
owners/kinds, snapshot refs/context и показывает current только по confirmed connected chain.
Повреждение optional history не меняет delivery/recovery state и не отменяет выполненный audit.

`content_versions` объединяет confirmation и exact frozen-context checks; repository, offline
doctor и operator view используют ту же политику. Metadata redaction/summary тоже общие.
Первые проверки выявили зависимость lookup от delivery_json; она устранена отдельным indexed
read-only repository query. Новая регрессия читает confirmed revision после удаления обоих
operational plans; wrong owner/kind получают unavailable. Schema55/immutable IDs остаются прежними.

**104 focused / 21 local HTTP** passed, strict mypy **473**, Ruff/format green. Pure policy tests
покрывают branch/cycle/missing predecessor; сценарии — retention, unknown→audited confirm,
legacy JSON, corruption, read-only effects и реальные formatter labels. Full gate выполняется.

**168 native snapshots**: все 28 tables и provider requests/reader messages exact, кроме одного
phase commands timing **0.1→0.0 s** и соответствующего command seconds. SQL delta0, gzip -5–0.
**24 inspection cases**: workers1/4, attention off/on, retention и original/unknown/confirmed.
Все requests/reader messages/edits и прежние outcome fields exact (кроме timing); **все 28 tables
exact**, SQL/gzip delta0. Новые metadata summaries unique/body-free, serialized view **1296–2381
bytes**. Это native fake-source evidence, не independent legal/Telegram acceptance.

J110 [CI](https://github.com/sobolevbel/lexinform/actions/runs/40602978562): **5049 passed**,
strict mypy **469**, Ruff/docs/web success. [Relay deploy](https://github.com/sobolevbel/lexinform/actions/runs/40603747181):
exact COMMIT `9fd58f6aea3142a400e23ad8147d03c35681dd9e`, listener up **45 s**.


Окончательный full: **5065 passed**, 0 failures/errors/skips, **579.132 s**.
J110 backward reader: **192 SQL snapshots / 436 run reports**, dumps exact без conversion.
Doctor parity: **184 snapshots**, прежние report fields/current decisions exact; добавлен только
`unrecognized_labels=[]`. **55 deployed migrations и content identity models AST exact**;
прочие 150 runtime files byte-identical.
[Acceptance asset](../assets/audits/2026-10-28-content-inspection/validation.json) пинует hashes,
gates, immutable lookup, confirmations, backward reader и metadata/state compactness.


<a id="j112"></a>
### J112. Compact amendment targets from the frozen editorial view

Статус: **готово**, baseline `ab63022bb7225eb11513a33edf75b3ea6ef2e061`.
Новый amendment target использует original editorial view и до operational retention cleanup.
Таким образом, старый ready/review input не копируется в batch delivery metadata, но original
publication payload, actual work owners, Bill/change/date и physical target не переписываются.
Exact provider context/request и content ID прежние. `/delivery` показывает physical attempt
count для известных editorial histories; legacy без history сохраняет прежний ответ.

**65 focused / 21 local HTTP** passed, strict mypy **473**, Ruff/format green. Расширенная
регрессия проверяет отсутствие большого unfinished input и в provider context, и в batch target,
при этом original delivery по-прежнему содержит свой historical payload. Первичная qualification
fixture неверно записывала PRAGMA без формата dump reader; исправлен только fixture export.
Full gate выполняется на замороженных sources.

**168 native snapshots**: provider requests/messages/profiles и **все 28 tables exact**, SQL/gzip
разница0; source doctors без errors. Дополнительно **8 transport cases**: workers1/4, ordinary/
heavy legacy payload, до/после cleanup. Requests/messages/edits/network calls и original content
IDs exact, все остальные Bill fields exact. Новый target не содержит unused input ни в одном
case. Размер target **5825–407958 → 5825–5864 UTF-8 bytes**; SQL delta **-402355…0**, gzip
**-4699…0**. Единственное changed column — `llm_batch_items.meta_json`; остальные rows exact.
Это synthetic legacy fixture, не production growth forecast или независимая legal acceptance.


J111 backward reader: **176 snapshots / 8 amendment targets** читаются, dump exact без schema
conversion. Paid request/prompt/extraction/identity/schema modules и прочие **157 runtime files**
byte-identical; изменения ограничены amendments request context и technical attempt-count line.

J111 [CI](https://github.com/sobolevbel/lexinform/actions/runs/40606320512): **5065 passed**,
strict mypy **473**, Ruff/docs/web success. [Relay deploy](https://github.com/sobolevbel/lexinform/actions/runs/40607059959):
exact COMMIT `ab63022bb7225eb11513a33edf75b3ea6ef2e061`, listener up **45 s**.


Полный gate: **5065 passed**, 0 failures/errors/skips, **614.123 s**.
[Acceptance asset](../assets/audits/2026-10-28-amendment-target-context/validation.json) пинует
gates, source hashes, exact provider parity, state-size reduction и backward reader.


<a id="j113"></a>
### J113. Compound correction ownership and recovery contract

Статус: **контракт готов**, runtime baseline `e588e1ed31a63d06b20f8b55514c8fda3d5baee2`.
[DA7.4](2026-10-22-document-architecture.md#da7) спроектирован в отдельном
[контракте](../reviews/2026-10-28-compound-content-revisions.md): physical owner, immutable
member/part baseline, expected-base CAS, mixed/unknown receipts и stale approval. Shared
correction не затирает исправления других members; digest edit не пересобирает неделю и
не освобождает event links. Отсутствующие historical parts не восстанавливаются из current Bill.

Определены пять самостоятельных reader-first выпусков и fault matrix, включая loss of runner,
частичный edit, append без receipt, competing results и cleanup. Новые grouped/multipart/digest
correction producers **не включены**: прежний безопасный отказ сохраняется до реализации и
прохождения соответствующих gates. DA7.5 остаётся частичным по coverage history kinds/source refs.
Это documentation-only stage; runtime и схемы J112 неизменны. Strict docs gate и diff check
обязательны; повтор полного runtime gate из-за документации не назначается.


<a id="j114"></a>
### J114. Scoped correlation logs without growing state

Статус: **готово**, baseline `244b63b4fd558424527ffe0eb1c1efe6f7b7f3c8`.
Pipeline/drain используют actual run row ID, команды — update ID; контекст восстанавливается
после вложенных scopes и исключений. `fan_out` копирует context отдельно для каждой задачи,
сохраняя прежний порядок consumption и caller-owned SQL writes. Default text и JSON logs
показывают structured IDs; detailed local source/result/application/content/publication traces
включаются существующим DEBUG level. Схемы, SQL reports, reader/model payloads не меняются.

Correlation metadata ограничены allowlist scalar IDs до 256 characters. URL/body/unknown fields
не включаются; application identity остаётся своей составной identity, отсутствующие extraction/
PreparedInput/request IDs не выдумываются. Local-write trace не доказывает remote checkpoint
или доставку. Warning summaries и `runs.report_json` не копируют подробные traces.
[Контракт эксплуатации](../process-plans.md#correlation-logs).

Первичная проверка новой failure fixture выявила неверное имя fake outage (`term` вместо
`list_terms`); исправлена fixture и добавлено явное утверждение о фактическом failed report.
Первый full gate остановлен; окончательный запускается на зафиксированных sources.

**168 native snapshots**: provider requests/messages и все 28 tables exact, кроме одного
tracking timing **0.0→0.1 s**. SQL delta0, gzip **0–4 bytes**; source doctors без errors.
Metadata/result/content identities и behaviour workers1/4 прежние. Никаких paid calls или
production processing этот этап не выполняет.

J112 [CI](https://github.com/sobolevbel/lexinform/actions/runs/40607876409): **5065 passed**,
strict mypy **473**, Ruff/docs/web success. [Relay deploy](https://github.com/sobolevbel/lexinform/actions/runs/40608728007):
exact COMMIT `e588e1ed31a63d06b20f8b55514c8fda3d5baee2`, listener up **45 s**.


Финальный gate: **5075 passed**, 0 failures/errors/skips, **595.786 s**; **54 focused / 21 local HTTP**,
strict mypy **476**, Ruff/format и strict docs green. **153 прочих runtime files byte-identical**;
repository AST exact после удаления trace calls/import. На одном amendment cycle: **35 records /
11275 UTF-8 bytes / максимум484 bytes на record**, workers1/4 canonical records exact. Это DEBUG
fixture, не прогноз роста production logs. [Acceptance asset](../assets/audits/2026-10-28-correlation-logs/validation.json).

J113 [CI](https://github.com/sobolevbel/lexinform/actions/runs/40608181382) success;
[deploy](https://github.com/sobolevbel/lexinform/actions/runs/40608978602) сообщает exact COMMIT
`244b63b4fd558424527ffe0eb1c1efe6f7b7f3c8`, already at that commit, listener running.


<a id="j115"></a>
### J115. Primary result provenance survives government-row rollover

Статус: **готово**, baseline `933f8d2f3f6ef31cc5888b498f7a35ea58ca569f`.
Government-only rollover сохраняет known primary result application и прежнюю source association
у нового term owner в одной транзакции с переносом Bill/publication/change/check rows. Общий
primary projection helper используется и обычным apply, и relocation, но rollover не вызывает
повторный save_analysis: status, generation, analysis bytes и ready/cache owners прежние.
Original paid result/input binding/old receipts не re-keyed; legacy без paid identity остаётся unknown.

Repository reader и прежний move используют один eligibility query; pending primary statuses и
linked RCL исключены по прежнему правилу. Сбой source/application persistence либо collision
откатывает move вместе с уже созданной историей, включая отказ на втором project.
New source association не означает свежего чтения URL или новой legal-applicability проверки.
Secondary relocation и дальнейший переход consumers к refs остаются открытыми.

**114 focused / 21 local HTTP**, strict mypy **477**, Ruff/format green; full gate выполняется.
**168 native snapshots**: requests/messages и все 28 tables exact, кроме одного tracking timing
**0.1→0.0 s**, SQL delta0, gzip **-4–0 bytes**; source doctors без errors.

**13 rollover profiles**: RCL/Wykaz, workers1/4, до/после cleanup, unknown legacy, pending input
и simulation на pinned production state `fe7036a09d1688d03236c936cda26c876f3ae825`.
Все прежние rows, requests/messages/edits, Bill snapshots и original result bodies exact; новые
rows только result_applications/result_applicability. SQL **0–2137 bytes**, gzip **0–145 bytes**.
На natural-data simulation: **208 eligible rows / 39 с analysis / 3 known paid refs**, добавлены
**3 applications / 1 source association**, model/reader API calls0. Это симуляция смены term на
сохранённых данных, не естественный production rollover и не independent legal/Telegram gate.

Backward reader J114 прочитал **13 snapshots / 474 Bill reads**, dumps exact без schema
conversion. Все **55 deployed migrations AST exact**, прочие **156 runtime files byte-identical**.


Full gate: **5091 passed**, 0 failures/errors/skips, **594.546 s**.
На natural-data simulation whole-dump delta **2137 SQL / 145 gzip bytes**; это metadata history
трёх известных результатов, без копирования source/result bodies.
[Acceptance asset](../assets/audits/2026-10-28-rollover-provenance/validation.json).

J114 [CI](https://github.com/sobolevbel/lexinform/actions/runs/40610158748): **5075 passed**,
strict mypy **476**, Ruff/docs/web success. [Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40610866847):
exact COMMIT `933f8d2f3f6ef31cc5888b498f7a35ea58ca569f`, listener up **45 s**.
Естественный [delivery drain](https://github.com/sobolevbel/lexinform/actions/runs/40610526550)
завершился на том же коде; его snapshot проверяется отдельно в J116, file-read production
приёмка этим drain не заявляется.


<a id="j116"></a>
### J116. Natural schema-55 drain snapshot, independently restored

Статус: **read-only проверка готова**, code в наблюдаемом job
`933f8d2f3f6ef31cc5888b498f7a35ea58ca569f`, state
`4f19d9bf7b8035f2239dece6098c4b06942f9414` (**28.10.2026 00:43:55 UTC**).
[Run](https://github.com/sobolevbel/lexinform/actions/runs/40610526550) — естественный delivery
drain, не ручной запуск. Allowlist в job logs: batch enabled=true, state checkpoint required=true,
reader attention=on, roundup=daily. Secrets/`.env` не читались; остальные runtime settings
по этим четырём значениям не выводятся.

Изолированный restore: **schema55 / 20316258 SQL bytes / 462 Bill reads**; dump byte-exact
без conversion. Последний run383: deliveries, errors0, model calls0, `document_reads=null`.
Поэтому файловый поток не объявляется измеренным нулём. Source/download/computation/content/work
doctors дали errors0; прежние **3 document_publication_unavailable** warnings сохраняются.
Known computation refs14, unknown411; paid results5. Editorial history пока known-empty0.
Ни один новый source family/purpose этим drain не объявлен принятым.

После чтения SQL и SQLite SHA не изменились. Reader methods dump/restore/migrate/get/row mapping
AST exact к наблюдаемому code; tools hashes сохранены. Первая qualification fixture использовала
relative SQLite URI; исправлена только fixture в новом output directory, initial evidence не
перезаписывалась. [Acceptance asset](../assets/audits/2026-10-28-natural-schema-55/validation.json).

Documentation-only stage; runtime gate J115 **5091 passed** остаётся действительным.
Strict docs/diff check обязательны. Raw archive/backfill, paid calls, Telegram API, workflow
dispatch и canonical state mutations не выполнялись. DA2/DA10 file coverage остаётся открытым.


<a id="j117"></a>
### J117. ODT physical block origins with the unchanged legacy view

Статус: **готово**, baseline `8e39f0629ce2ea88bb37af4b796a623a0fd8764c`.
ODT extraction возвращает отдельный transient manifest: actual child-index paths абзацев,
заголовков, rows/cells, ordered legacy projection, input/XML SHA, Python/view versions.
Ignored nodes сохраняют path/tag; inline notes, tracked changes и repeated table content явно
остаются unresolved/unexpanded legacy views. Никакой пагинации, legal completeness либо
PreparedInput spans из этих координат не выводится.

Physical package связывает manifest с existing part index/raw SHA/exact text. Optional port
сохраняет прежние custom adapters; ordinary facade отключает сбор metadata. Policy, provider
inputs, SQL и сохранённые selections не меняются. Legacy DOC и durable extraction history
остаются следующими задачами, не объявлены реализованными.

**106 focused**, strict mypy **478**, Ruff/format green. **5112 full passed**, errors/failures/
skips0, **606.484 s**. **168 native snapshots**: provider requests/messages и все 28 tables
exact, timing differences0, SQL/gzip delta0. Старые document model classes AST exact, кроме
additive transient ExtractedParts; прочие **156 runtime files byte-identical**.

Discovery проверил SHA/size всех **16341 paths / 12073227147 bytes** шести raw families.
Из ZIP roots: 5719 Word вне changed parser, **1803 containers / 1 direct ODT**. В трёх ZIP
найдены ещё **7 ODT parts**, всего8. Real text/selection replay: **6 corpus roots** (включая
2 rejection controls) и **9 variants**, из них5 meaningful mutations: date/amount/negation/
scope/reference. Все physical/selection decisions exact к предыдущему коду. **1798 других
roots** проверены только на format routing с placeholder adapters — их text/legal selection
этим replay не приняты. Independent human/legal labels не создавались.

**17 origin views / 1066 block views / 46 ignored-node views**: JSON round trip, part binding и
paths/tags в original XML подтверждены. Corpus INDEX и все 16341 input hashes перепроверены
после replay, без изменений. Первую discovery остановили из-за лишнего разбора не-ODT текстов;
отдельный format-only pass и actual ODT replay разделены. Первичная replay fixture не передала
обязательный max_part_chars; исправлена только fixture в новом output directory.

J115 [CI](https://github.com/sobolevbel/lexinform/actions/runs/40611560095) success: **5091 passed**,
strict mypy **477**. [Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40612101577):
exact `4774da8ce83e47a071453fdfaee5b0e6b19176db`, listener up **45 s**.
J116 [CI](https://github.com/sobolevbel/lexinform/actions/runs/40612084715) success;
[deploy](https://github.com/sobolevbel/lexinform/actions/runs/40612839027) сообщает exact
`8e39f0629ce2ea88bb37af4b796a623a0fd8764c`, already at that commit, listener running.


[Acceptance asset](../assets/audits/2026-10-28-odt-blocks/validation.json) пинует gates, источники,
real/routing coverage, mutations и неизменность корпуса/сохранённого state.


<a id="j118"></a>
### J118. Shared replay IO and streaming input revalidation

Статус: **готово**, baseline `1312184b96d2a015384dff70eaa09ed5e54a69ab`.
PDF/Word tools используют общий CorpusReads.changed_inputs, artifact readers/writer и observation
comparison. Исчезнувшее observation отличается от explicit unknown null. Word сохраняет
requested internal alias до чтения; initial alias rebinding обнаруживается, даже когда прежний
canonical target не изменился. Final check не открывает retargeted outside/credential paths.
Direct require также применяет credential-path запрет. Нормальные output formats/CLI остаются.

Reproduction на прежнем helper: **5 failures / 3 controls**, errors0; protected reads блокировал
test spy до фактического открытия. Исправленная группа и две integration regressions PDF/Word
прошли. **55 focused / 149 corpus-related**, strict mypy **483**, Ruff/format green. Четыре
shared/consumer tool modules добавлены в explicit mypy targets. Full gate выполняется.

Final SHA recheck теперь потоковый. Before/after на **16341 paths / 12073227147 bytes**: initial
fingerprint maps exact, changed_inputs0. На actual raw ZIP **15737127 bytes** Python allocation
peak во время одного final recheck **15738592→395327 bytes**. Это не full-process RSS и не
прогноз производительности архива; single timing sample не выдаётся за speedup.

Все **160 runtime files byte-identical**; PDF/Word snapshot functions и прежний PDF digest
algorithm AST exact. Нет extraction/selection/normalization/paid/reader/SQL изменений, поэтому
повтор text/legal corpus replay не назначается. Результаты индекса и байтов проверены напрямую.


Full gate: **5125 passed**, errors/failures/skips0, **667.055 s**.
[Acceptance asset](../assets/audits/2026-10-28-corpus-input-rechecks/validation.json) пинует
reproduction, gates, common helpers, real input maps и allocation sample.

J117 [CI](https://github.com/sobolevbel/lexinform/actions/runs/40615914088) success: **5112 passed**,
strict mypy **478**. [Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40616708707):
exact COMMIT `1312184b96d2a015384dff70eaa09ed5e54a69ab`, listener up **45 s**.


<a id="j119"></a>
### J119. Portable ODT replay and shared raw population manifest

Статус: **готово**, baseline `176c328210426e9e92ff8bc6be16dc2cb0e4cda7`.
Development-only ODT replay заменяет одноразовый observer стандартным CLI library/adapter.
Общий raw population model используется PDF/ODT; прежние PDF constructors и JSON сохранены.
ODT suite сохраняет SHA текста и bindings всех найденных leaves, archive rejections и ошибки,
проверяет XML SHA, JSON round trip, actual block/ignored paths и exact part/text binding.
Ненулевая популяция ODT обязательна; остальные форматы явно исключены из text/legal coverage.

**51 focused**, strict mypy **486**, Ruff/format green. Corpus replay: **6 natural roots /
8 ODT leaves**, **9 synthetic derived variants**; observations exact. **17 manifests,
1066 block coordinates, 46 ignored coordinates** проверены непосредственно в source XML.
Ошибок, изменения code/input либо расхождений нет. Meaningful mutations уже созданы и
зафиксированы J117; suite заново сверила те же exact bytes. Независимый legal oracle отсутствует.
Все **160 runtime files byte-identical**, PDF snapshot function AST exact. Нет SQL, prompt,
selection policy или production wiring изменений. Full gate выполняется.

J118 [CI](https://github.com/sobolevbel/lexinform/actions/runs/40619729692) success:
**5125 passed**, strict mypy **483**.
[Deploy](https://github.com/sobolevbel/lexinform/actions/runs/40620532529): exact COMMIT
`176c328210426e9e92ff8bc6be16dc2cb0e4cda7`, listener up **45 s**.

Full gate: **5144 passed**, errors/failures/skips0, **612.682 s**.
[Acceptance asset](../assets/audits/2026-10-28-odt-replay/validation.json) пинует оба cohorts,
исходные manifests, пять meaningful mutations и test/source fingerprints.


<a id="j120"></a>
### J120. ODT registry and explicit input/population differences

Статус: **готово**, baseline `a78670629787324465587a9c65f2061181b48fe7`.
ODT suite использует существующий immutable run registry, code snapshots, verify и общий diff.
Shared observation comparison применяется и в registry: missing/unknown null больше не
сливаются. Case/catalog/index SHA входят в summary differences; payload algorithms прежние.
PDF/Word/ODT emit фактический read_inputs map из shared guard, включая запрошенные aliases.

На прежнем registry **3 false same / 1 unchanged control**. Добавлены шесть forward/reverse
regressions и настоящий subprocess library→adapter→verify→diff для ODT. PDF/Word tests
подтверждают actual input hashes. Strict mypy **487**, Ruff и format всех src/tests/tools green.
Все **160 runtime files byte-identical**, три physical snapshot functions AST exact.
Full gate выполняется; runtime/SQL/prompt/legal policy не менялись.

J119 [PR CI](https://github.com/sobolevbel/lexinform/actions/runs/40622533941) success:
**5144 passed**, strict mypy **486**, docs/web green.
[Draft PR #11](https://github.com/sobolevbel/lexinform/pull/11) содержит готовый stage;
production deployment ещё не заявлен. Automatic approval review отклонил direct main push,
поскольку он запускает production deployment. Запрошено явное разрешение на main/deployment;
разработка и feature-branch pushes продолжаются, обход запрета не выполняется.

Full gate: **5151 passed**, failures/errors/skips0, **599.24 s**.
**61 focused** и real registry cycle green; 6 roots / 8 ODT manifests / 935 blocks / 37 ignored
coordinates exact с J119. Input maps library/adapter exact: **7 paths**, включая INDEX.
[Pinned cases](../assets/audits/2026-10-28-odt-replay/cases.json) byte-identical проверенному
входу и включены в репозиторий. [Acceptance asset](../assets/audits/2026-10-28-corpus-registry-inputs/validation.json)
пинует reproduction/retest, registry artifacts, gates и source fingerprints.


<a id="j121"></a>
### J121. Explicit unlocated DOC/XML projections and one physical-walk collector

Статус: **готово**, baseline `259c97b0b802433d7e095cf7896de53613f6c105`.
`unlocated-text-v1` связывает raw/text SHA и длину строки с physical part для DOC/standalone
Word XML routes. Coordinates/pagination/tracked changes/fields unknown, note origins unlocated.
Route обозначает путь decoder, не successful format/completeness verdict. Нет блоков, страниц
или source spans, выдуманных из plain text. Missing/empty projections сохраняют прежние losses.
JSON round trip и part/raw/text binding валидируются; located и unknown origins не совмещаются.
Данные transient; legacy facade явно не собирает этот inventory.

Внутренний `_PhysicalOrigins` объединяет requested lists одного обхода. Рекурсия не получает
отдельный растущий список аргументов каждого формата. После нормализации carrier access и
удаления нового metadata-only append старый `_collect` AST exact; `_extract` AST exact,
DOC parser byte-identical. Изменены три runtime files; остальные **157 byte-identical**.

**118 focused**, strict mypy **488**, Ruff/format всех src/tests/tools green.
Corpus: все **404 .doc + 13 .xml** roots J094 inventory (**134483899 raw bytes**) и **45 controls**
из остальных форматов/families. **462 profiles** — прежние physical/selection outcomes exact,
input maps exact, leaf errors0. Новых manifests **414**: DOC402 / Word XML12, empty projections0.
Это физические routes по bytes; расширения не объявляются доказательством формата.
**9 synthetic variants** реального Word XML: wrappers и пять meaningful mutations; exact
old/new profiles, needles сохранены, manifests9. Это не девять независимых документов.

**168 native profiles**: requests/messages и все 28 tables exact, timing differences0,
SQL/gzip delta0; source doctors errors0. Actual ODT suite passed against pinned J120 library.
Уже принятый native baseline J117 пригоден: между J117 и J120 все runtime sources unchanged;
его observer inputs и outcomes сохранены. Нет model calls, source refresh, publication или SQL
migration. Full gate выполняется. Другие PDF/OOXML inputs — stratified controls, не full family
acceptance; embedded DOC/XML вне 417 roots не объявлены полностью покрытыми.

J120 [PR CI](https://github.com/sobolevbel/lexinform/actions/runs/40623368145) success:
**5151 passed**, strict mypy **487**, docs/web green. Main/deployment permission remains pending;
готовый код сохранён в feature branch и [draft PR #11](https://github.com/sobolevbel/lexinform/pull/11).

Full gate: **5169 passed**, errors/failures/skips0, **597.702 s**.
[Acceptance asset](../assets/audits/2026-10-28-unlocated-text/validation.json) пинует source
fingerprints, нормализацию AST, полный DOC/XML cohort, controls/mutations, native parity и
ODT suite. Final raw/variant recheck не нашёл изменений; physical metadata остаётся transient.


<a id="j122"></a>
### J122. Explicit-clock age of saved nonterminal work

Статус: **готово**, baseline `4f64ec7a2b30e52d1d4af12f4593b01b17ccbf93`.
Existing read-only work doctor получает optional observed-at и body-free group ages для
DocumentWork waiting и ReviewWork context/snapshot. Счётчик nonterminal не обещает runnable/
enabled/due state. Terminal/abandoned markers учтены отдельно; retention/retry/apply прежние.
Отсутствующие, naive, invalid и future timestamps остаются unknown с краткой причиной.
Время наблюдения требует timezone; UTC/default source timestamps не синтезируются.

**43 focused**, strict mypy **490**. CLI valid/naive clock, DST offsets, oldest known owner,
unknown clocks, terminal exclusions и partial corrupted inventory проверены. Без observed-at
нового поля нет; default report на natural schema55 копии byte-exact с прежним tool.
**5 nonterminal owners**: document waiting3 / review context2; abandoned7 excluded. As-of
**2026-10-28T00:43:55Z**, oldest ages **628733 / 1176118 s**. Это тот же pinned J116 state,
не новое наблюдение live work/source latency. DB SHA сохранён:
`66ecc58b10c3e7ba2df1d4fc89be26e018539a8f94931f0c7aeaf12205f6fe2e`.
Все **160 runtime files byte-identical**, нет SQL/pipeline/provider/reader изменений.
Full gate и final formatting/lint приняты ниже.

J121 [PR CI](https://github.com/sobolevbel/lexinform/actions/runs/40625287381) success:
**5169 passed**, strict mypy **488**, docs/web green. Код в feature branch / draft PR #11;
main/deployment ещё не выполнены без запрошенного отдельного разрешения.

Full gate: **5185 passed**, failures/errors/skips0, **619.643 s**.
После frozen full gate разделена одна overlong scope string: literal value и полный module AST
exact. Повторные **43 focused**, strict mypy **490**, Ruff/format green.
[Acceptance asset](../assets/audits/2026-10-28-work-ages/validation.json) сохраняет tested/final
source hashes и AST proof, CLI/natural report parity, snapshot SHA и coverage limitations.


<a id="j123"></a>
### J123. Explicit exclusions for the deferred raw-storage profile

Статус: **готово**, documentation-only follow-up к J122.
По уже принятому пользовательскому ограничению пять blob-store задач DA2.2/2.6/2.7/2.8/2.9
явно помечены неприменимыми при текущем профиле. Raw часть DA2.4 условна; metadata/error
contract остаётся открытым. Не вводятся store/GC/modes и не принимается решение filesystem/S3.
SQL restore, frozen pending inputs, result/history и retention dependencies по-прежнему
обязательны в остальных applicable задачах. Это exclusions, не выполненная реализация.

Master checklist: **74 задачи / 19 отмечены выполненными / 6 неприменимы / 49 применимых
остаются открытыми либо частичными**. Шестая неприменимая задача — DA8.3, перенос через
отвергнутый общий scheduler. Новых implementation completion marks нет. Human labels,
непринятый paid A/B и natural production coverage не подменяются conditional exclusions.
Runtime/tests/tools/config не менялись; strict docs/link/build и diff gate обязательны.


<a id="j124"></a>
### J124. Prepared-input completeness requires the part's own source ranges

Статус: **готово**, baseline `14d7fa92975dfc9f3eafdfe5831c62ed251d6659`.
Reproduction: закон с четырьмя evidence checks остаётся selected, хотя exact input map содержит
только ту же строку из justification, а собственный bill range 0–0. Omission/loss отсутствуют.
Причина — completeness спрашивала глобальный substring отдельно от уже верной source map.

При known projection проверяется покрытие required ranges собственного part. Для bill/
continuation это прежний trim map без generated markers; annex сохраняет strip-only semantics.
Adjacent/overlapping actual ranges объединяются. Текст и source facts не переписываются,
нормативный SHA сохраняется; selected input с известной потерей становится unverified.
Coarse unknown-map path не усиливает своих обещаний; source selection policy version прежняя,
поскольку не изменены file choice, edition или legal admissibility.

**122 focused**, strict mypy **490**, Ruff/format green. Synthetic source-position probes:
**20 cases**, прежние 15 false completeness verdicts исправлены, 5 positive controls сохранены;
date/amount/negation/scope/reference needles остаются в exact input. Это fixture invariants,
не независимая смысловая разметка. **168 native scenarios**: requests/messages/all28 tables
exact, timing0, SQL/gzip delta0, source doctors errors0.

Full gate и raw replay приняты ниже. Pinned population **5365 inputs**: RCL DOC379 / DOCX2564 /
DOCM491 / XML13 / ZIP1803, прежние PDF114 и direct ODT1. Другие PDF/scans не входят в этот
text-projection gate. Prior decisions проверены до commit; итоговые changed cases0.
Изменён только один runtime module, остальные 159 unchanged.

J122 [PR CI](https://github.com/sobolevbel/lexinform/actions/runs/40627180662) и J123
[PR CI](https://github.com/sobolevbel/lexinform/actions/runs/40627393197) success:
**5185 passed**, strict mypy **490**, docs/web green для каждого head. Main/deployment не
выполнены; явное разрешение остаётся запрошенным, feature branch/draft PR сохранены.

Full gate: **5189 passed**, failures/errors/skips0, **641.366 s**.
Raw replay завершён: **5365 paths / 3753220922 bytes / 42920 prepared views**. Ошибок0,
changed cases0: все prior text/law digest/input/original selection hashes, projection maps,
coverage coordinates и profile values exact. Selection JSON byte delta0. Changed decisions
для просмотра отсутствуют; 15 известных false-full fixture cases исправлены, 5 positive controls
не изменены. Final input SHA/index recheck и source freeze подтверждены.
[Acceptance asset](../assets/audits/2026-10-28-input-source-completeness/validation.json) пинует
полную population, старые/новые profiles, replay/comparator, meaningful probes, native/gates.

Readonly origin/state tip проверен во время qualification: остаётся J116 pin
`4f19d9bf7b8035f2239dece6098c4b06942f9414`; новый natural file-family cycle не заявляется.


<a id="j125"></a>
### J125. Transient prepared-text identity and explicit segment origins

Статус: **готово**, baseline `9964a9ab6183c805b2c37b9aaaf7623c4937dc9e`.
`PreparedTextInput` описывает точную emitted строку хешем/длиной, source-selection identity,
упорядоченными part hashes/roles и диапазонами. Source spans адресуются внутри конкретного
legacy part string. Generated transform markers и part separators имеют system origin;
без projection вход остаётся unknown даже при совпадающем тексте. Карта не обещает полноту.
Physical/block citations, provider context/prompt/options, SQL persistence и старые paid inputs
в этот scoped контракт не входят. Документные тела в нём отсутствуют.

Общий factory проверяет declared part hash и projection identity/copied bytes, не ищет
substring origins. Он принимает source decision до fitting; scan/fitted decisions отвергаются.
Optional `tools.replay_input_coverage --prepared-manifest` включает manifest только в mapped
compare; прежний default report сохраняет форму. Replay проверяет JSON/identity round trip и
дословность каждого source span. Runtime callers, prompts, paid identity и SQLite не меняются.
Full gate и bounded real-format pilot выполняются на frozen code; результаты ниже.

J124 [PR CI](https://github.com/sobolevbel/lexinform/actions/runs/40632633527) success:
**5189 passed**, strict mypy **490**, docs/web green. Main/deployment по-прежнему ждут ранее
запрошенного разрешения; draft PR #11 не запускает production deployment.

**26 focused / 5211 full passed**, failures/errors/skips0, strict mypy **493**, Ruff/format green.
Pilot: **29 raw inputs / 178889531 bytes**, DOC4 / DOCX4 / DOCM4 / XML4 / PDF4 / ZIP8 / ODT1.
Size strata min/median/p90/max дополняются ODT ZIP и rejection controls. **232 exact input maps**,
335 source и 143 generated segments; все прежние J124 profile values exact, changed cases0.
Идентичность raw/INDEX и source freeze проверена после прогона. Compact metadata —
455–2171 bytes на manifest, суммарно184860; это transient inventory, не измерение SQL growth.
CLI help показывает opt-in flag; прежние 159 runtime modules byte-exact, ещё один меняет только
exports новых моделей. Full DA5.1 остаётся открытой до physical/context/runtime integration.
[Acceptance asset](../assets/audits/2026-10-28-prepared-text/validation.json) фиксирует population,
source hashes, exact prior-profile comparison, round trips и gates. Strict docs/build/link green.


<a id="j126"></a>
### J126. Retained review receipts follow government term rollover

Статус: **готово**, baseline `1f3f0d432353a09e3f3d9155495621e21725f254`.
Воспроизведён пробел: government Bill переносится с текущим review, но new-owner application
receipt отсутствует. Paid result, context и old-owner receipt при этом сохранены. В той же
транзакции, что move и primary receipt, переносятся только доказанные current review targets:
computation ID + точный source key/legacy context key должны совпасть с существующим receipt.
Known original address сохраняется дословно. Result/request/input не rekey'ятся; missing history
и unknown legacy не backfilled. Очереди, enable flags, retention и reader effects прежние.

**83 focused**, strict mypy **494**, Ruff/format green. RCL/Wykaz, три review purposes,
source-key/context-key variants, workers1/4, cleaned/unpruned state, restore и повторный вызов.
Failure secondary receipt откатывает move вместе с primary receipt. Unknown result, иной target
и отсутствие исходного receipt не создают новой history. Shared paused-act fixture теперь
сравнивает число requests до/после, сохраняя возможность предшествующего primary batch;
commission fixture явно включает свой batch kind, cleanup подтверждает accounting перед prune.
Первоначальные fixture failures сохранены отдельно; runtime flags для них не ослаблялись.

**12 native before/after profiles**: все requests/messages/paid results и prior application rows
exact. Все остальные 27 SQL tables exact с исключением run phase timings; каждый профиль
получает ровно один new-owner review receipt. Raw SQL delta **533–602 bytes**, gzip **15–108**;
это fixture snapshots, не прогноз production storage. Integrity/FK checks и повтор после restore
green. Other secondary scopes/name lineage остаются отдельной частью DA6.2/DA6.3.
Full gate выполняется на frozen code; corpus text replay не требуется для этой metadata-only
transfer, поскольку extraction/selection/normalization/provider text не менялись.

J125 [PR CI](https://github.com/sobolevbel/lexinform/actions/runs/40636670932) success:
**5211 passed**, strict mypy **493**, docs/web green. Feature branch/draft PR only;
main/deployment не выполнялись. Read-only state tip повторно сверён: тот же J116 pin
`4f19d9bf7b8035f2239dece6098c4b06942f9414`, нового natural cycle нет.

Full gate: **5227 passed**, failures/errors/skips0; final source freeze сохранён.
[Acceptance asset](../assets/audits/2026-10-28-rollover-review-history/validation.json) пинует
source/test hashes, 12 old/new profile pairs, all-table comparison и gate XML. Strict docs/build/
links и diff check green. Ни один новый общий scheduler, source/backend или migration не добавлен.


<a id="j127"></a>
### J127. Freeze qualified failed legacy status replies before their next API attempt

Статус: **готово**, baseline `a3d4689d1af5a59858178c293454da8d63fc6306`.
На прежнем source один remote-boundary scenario падает без setup errors: перед API durable
pending имеет semantic plan, но не точный HTML. Новый узкий writer принимает только FAILED
legacy single status reply с положительным сохранённым reply target и без membership/continuations.
UNKNOWN/PENDING, замена parent, уже frozen payload, другие kinds и oversized text не активируются.

Shared predicate и raw-JSON/CAS writer переиспользуются с existing status edit; прежний edit
contract сохраняет original today. Новый post сохраняет render day из Clock, как прежний live
callback, поэтому retry спустя дни не возвращает старую дату в будущие действия. Меняются только
HTML/origin/render day; Bill/change, reply target, flags, attempts и status остаются прежними.
Origin `legacy_snapshot` не выдаёт новый HTML за исторический payload предыдущей попытки.
Unknown JSON fields переживают запись; сети внутри SQL transaction нет. Existing pre-API/
receipt checkpoints и uncertain recovery применяются без отдельной очереди/schema.

**200 focused**, strict mypy **496**, Ruff/format green. Проверены pre/post API checkpoint failure,
restore, known parent, delayed render day, immutable retry спустя 100 дней и смену formatter,
unknown/changed fields и UTF-16 size bound; все прежние status-edit guards сохранены. Шесть
старых callback assertions расширены на frozen send, остальные legacy/long/attention controls
сохраняют прежний путь. Plain unknown parents/groups/multipart остаются отдельной работой.

**6 native before/after profiles**: requests и actual outgoing text/parent exact. Все 28 tables
exact после исключения только трёх новых frozen payload fields и run phase timings. HTML SHA
равен реальному outgoing SHA; day 7→12 сентября совпадает с прежним callback после пяти дней.
Fixture SQL delta **920–1427 bytes**, gzip **92–367**; это не production forecast.
Actual previous source archive прочитал новый payload: **6/6 old-reader** и **6/6 new-reader**,
по одному fake API call, exact remote pending/receipt, без render или повторной отправки.
Full gate выполняется на frozen code; extraction/selection/legal text/provider requests не менялись.

J126 [PR CI](https://github.com/sobolevbel/lexinform/actions/runs/40638246968) success:
**5227 passed**, strict mypy **494**, docs/web green. Feature branch/draft PR only;
main/deployment по-прежнему требуют ранее запрошенного разрешения.

Full gate: **5265 passed**, failures/errors/skips0; source freeze сохранён. Все 158 остальных
runtime files byte-exact; AST repository/migrations/row readers и posting вне narrow freeze/
_send methods, а также остального protocol, совпадает с baseline. Schema/model shape прежние.
[Acceptance asset](../assets/audits/2026-10-28-legacy-reply-freeze/validation.json) сохраняет
baseline reproduction, native/compatibility pairs, raw-JSON/date guards, source hashes и gates.
Strict docs/build/link и diff checks green. Оставшиеся legacy kinds/unknown parent/multipart/
groups этим ограниченным контрактом не объявляются переведёнными.


<a id="j128"></a>
### J128. Prepare every physical part of a failed legacy status reply before retry

Статус: **готово**, baseline `e27327c7c5189812ad541c1f3a85bc675bfdec16`.
Oversized legacy callback передаёт один input в Telegram adapter и не сохраняет физические части.
Новый путь использует существующий `freeze_parts` с узким persist callback: children + failed root
записываются в одной транзакции до pre-API checkpoint. Новая physical identity относится к
будущей попытке, не реконструирует прошлую отправку. Parent каждого child появляется только из
receipt предыдущей части и checkpoint'ится до его API. Held changes принадлежат leaf; act notice
не завершается до всей цепочки. Не вводится новая очередь или схема.

Repository проверяет ownership/ref/index/parent/root, queued0/no-receipt state, bounded payloads и
точную leaf membership. Существующая часть с иным payload/shape не усыновляется; CAS failure
откатывает подготовку всех children. Root сохраняет known target/flags/source/attempts; render day следует Clock по контракту J127.
Default `freeze_parts` callers используют прежний writer; новый callback имеет одного явного
legacy caller. Body rendering и HTML splitter прежние, других source/admissibility решений нет.

**227 focused**, strict mypy **498**, Ruff/format green. Remote pre/post root/leaf faults,
restore, definite failed continuation, unknown + audited confirmation, actual next reply parent,
foreign/attempted/malformed child, orphan collision и atomic root-write failure проверены.
При UNKNOWN ничего не auto-resend'ится; confirm не вызывает API и разрешает только следующий child.
Reproduction на actual prior source падает без setup errors из-за отсутствия prepared part calls.

**168 default native profiles**: requests/messages/all28 tables exact, phase timing differences0,
SQL/gzip delta0, source doctors errors0. Отдельные **6 oversized legacy profiles** получают по
3 bounded API inputs, exact canonical splitter output прежнего полного текста; model requests,
paid results, root IDs/receipts/attempts/parent и logical outcomes сохранены. Старый fake принимал
oversized input; успешная старая доставка в настоящий Telegram не утверждается. Fixture SQL
increment **23048–24393 bytes**, gzip **1176–1333**, в этих шести fixtures с компактными continuation snapshots; raw files в SQL не добавлялись.

Previous source и новый reader прошли по **6 chains / 18 physical calls**, exact frozen payloads /
remote receipts и duplicate calls0, без render. Production acceptance этим не заменяется;
unknown groups, missing root parents и oversized legacy edits остаются отдельной работой.
Full gate выполняется на frozen code; corpus text replay для delivery-only change не требуется.

J127 [PR CI](https://github.com/sobolevbel/lexinform/actions/runs/40640458709) success:
**5265 passed**, strict mypy **496**, docs/web green. Feature branch/draft PR only;
main/deployment по-прежнему требуют ранее запрошенного разрешения.

Full gate: **5301 passed**, failures/errors/skips0; final source freeze сохранён. Migrations,
row readers и прочий repository/posting/protocol AST вне scoped methods exact. Остальные
158 runtime files byte-exact. Дополнительный reader actual main `176c328` прошёл **6 chains /
18 calls**, exact pending/payload/parent/receipt и duplicate0; uv.lock byte-exact. Это source +
current dependencies на fake transport, не полный binary/deployment rollback test.
[Acceptance asset](../assets/audits/2026-10-28-legacy-reply-parts/validation.json) пинует source,
faults/ownership/CAS cases, native/default/compatibility evidence и gates. Strict docs/build/
link и diff checks green. Backend и natural production acceptance не расширялись.


<a id="j129"></a>
### J129. Capture a missing legacy reply parent from its current owner's sent card

Статус: **готово**, baseline `89f8858199ad429c4d43ef9cf00a15874d9fab8b`.
Legacy failed reply без сохранённого parent теперь фиксирует адрес только из SENT-card того же
publication owner/channel. Writer повторно проверяет receipt внутри atomic freeze. Уже заданный
parent не меняется. Missing/unconfirmed/foreign/mismatched receipt не допускает API и не расходует
attempt; это не отказ по содержанию закона. Исторический parent не реконструируется.

Lookup использует текущие term/number/channel публикации: после government rollover immutable
Bill snapshot остаётся в прежнем term, а карточка и текущий owner уже переехали. RCL scenario
проверяет новый адрес без перезаписи сохранённого Bill. Короткие и multipart replies используют
тот же J127/J128 freeze/checkpoint protocol; новые поля/schema/backend не вводятся.

**250 focused / 5324 full passed**, failures/errors/skips0, strict mypy **500**, Ruff/format green.
Wrong term/number/channel, pending/unknown/failed/absent card, invalid/mismatched message ID и
retarget уже сохранённого parent отвергаются. Remote pre-API checks подтверждают новый known
parent; source failure воспроизведён на prior source без setup errors.

**168 default native profiles**: requests/messages/all28 tables exact, timing differences0,
SQL/gzip delta0, source doctors errors0. **12 missing-parent profiles**: actual old callback
parent совпадает с новым stored parent; canonical texts/model requests/paid results/root
IDs/receipts/attempts/logical outcomes сохранены. Long before-input принимал только fake transport;
успешная старая Telegram-доставка не утверждается. Fixture SQL delta **919–24392 bytes**,
gzip **93–1335**: сюда входят HTML и новые physical parts, не только integer parent.

Шесть final writer SQL snapshots **побайтно совпадают с J128**. Поэтому prior/new/main reader
acceptance J128 переиспользуется по exact SHA; новая natural/reader acceptance не приписывается.
Остальные159 runtime files unchanged; AST repository/migrations/readers/posting вне scoped
methods и pure legacy policy вне reply eligibility exact. Final source freeze сохранён.
[Acceptance asset](../assets/audits/2026-10-28-legacy-reply-parent/validation.json) пинует sources,
reproduction, owner guards, profiles, exact snapshot reuse и gates. Corpus text replay не нужен:
extraction/selection/normalization/provider inputs не менялись. Strict docs/build/link и diff green.

J128 [PR CI](https://github.com/sobolevbel/lexinform/actions/runs/40643892565) success:
**5301 passed**, strict mypy **498**, docs/web green. Feature branch/draft PR only;
main/deployment по-прежнему требуют ранее запрошенного разрешения.

<a id="release-pr11-pr10"></a>
### Release: PR11 and the artifact-upload dependency update

После повторного явного разрешения пользователя на merge/push/deployment PR11 слит в main:
`9a10d8e2150333f6b4b89c2ff70ef0741b443930`.
[Main CI](https://github.com/sobolevbel/lexinform/actions/runs/40655317241) и
[relay deployment](https://github.com/sobolevbel/lexinform/actions/runs/40656383295) success;
deploy log подтверждает переход `176c32821042 → 9a10d8e21503`. Предыдущие записи о pending
разрешении описывают прежнее состояние и больше не блокируют выпуск.

PR10 проверен и слит: единственное изменение — `actions/upload-artifact@v4 → @v7`.
Официальный action contract сохраняет используемые name/path и ZIP-default; GHES и opt-in
`archive:false` здесь не используются. Actual PR CI загрузил `corpus-check-3.14`, artifact
12681194156, 22723 bytes. Merge `88dc3ea9c1afded812db76a3abb3dc4cbdd8a476`:
[main CI](https://github.com/sobolevbel/lexinform/actions/runs/40656642750) и
[deploy](https://github.com/sobolevbel/lexinform/actions/runs/40657676516) success.
Новый live bot run или Telegram delivery этим не утверждаются.

<a id="j130"></a>
### J130. Freeze failed legacy notices before retry

Статус: **готово**, baseline `88dc3ea9c1afded812db76a3abb3dc4cbdd8a476`.
Шесть kinds — act publication, consultation results, in-force и три deadline reminders —
фиксируют будущий literal HTML и все physical parts перед повторным API. Closed eligibility:
FAILED, literal legacy/direct, positive saved parent, complete required semantic context,
без существующей membership/absorption/revision/participation override. Сохранённые день,
parent, flags/context и attempts не переписываются. Новая очередь/schema не добавляется.

Общая CAS/raw-JSON реализация используется также status reply/edit writers с прежними
ограничениями. Multipart использует J128 ownership/leaf membership и existing checkpoint
protocol. `legacy_snapshot` описывает будущую попытку; текст прошлой попытки не реконструируется.
Attention-v1, отсутствующий parent/context, legacy sittings/digest и oversized status edits
остаются явно за границей этого этапа.

**281 focused passed**, failures/errors/skips0; strict mypy **502**, Ruff/format green.
Remote checks покрывают все шесть notices × три attention modes × attempts0/1, multipart,
pre/post root/leaf failure, restore, formatter change, definite failed child, UNKNOWN + audited
confirmation и atomic writer refusal. Prior source воспроизводит отсутствие freeze без setup errors.

**168 default native profiles**: requests/messages/all28 tables exact, timing differences0,
SQL/gzip delta0, source doctors errors0. **18 native legacy notice profiles** сохраняют outgoing
text/parent/day/root identity/receipt/attempts/model requests/paid results. Fixture SQL increment
949–1445 bytes, gzip293–468. **18 synthetic oversized chains** прочитаны prior/new readers:
по54 physical calls, exact frozen payload/remote receipt/parent, duplicate0, без render.
Corpus text replay не нужен: extraction/selection/normalization/provider inputs не менялись.
Full gate: **5422 passed**, failures/errors/skips0. Final source freeze сохранён.
[Acceptance asset](../assets/audits/2026-10-28-legacy-notice-freeze/validation.json) пинует
sources/gates/profiles и исключения; остальные158 runtime files byte-exact.
С обновлёнными PR9 dependencies повторно прошли281 focused, mypy502, Ruff/format.

PR9 после review обновлён до текущего main и слит:
`c45f3f339a9902ab99aad7b91cfb861b81445f03`.
[Fresh head CI](https://github.com/sobolevbel/lexinform/actions/runs/40657065517) success,
включая PostgreSQL web tests. Изолированная локальная проверка:5324 tests (пять ошибок setup
Git-archive прошли после восстановления metadata), localHTTP21, mypy500, strict docs/web checks;
168 native profiles exact, SQL/gzip delta0. SDK request/collection probes не нашли изменений
HTTP bodies, PDF blocks, JSONL или parsed batch results. Anthropic меняет API version/auth header;
наши requests совместимы. Это не live provider acceptance; платных вызовов не было.

<a id="j131"></a>
### J131. Recover interrupted relay deployments without a false success

Статус: **готово; deployment проверен**. Production incident после J130: deploy40659234925 был запущен CI для c45f3f3,
но reset'нул checkout до1f4331a; `hatchling.build.build_editable` получил SIGTERM15. Sync завершился
ошибкой до restart. Причина сигнала неизвестна; OOM не доказан. Следующий deploy40659847080
вернул success/already-at, пропустив sync/restart из-за равных checkout HEAD. Это не runtime acceptance.
Прямые read-only SSH probes не соединились; никаких диагностических команд на VPS не исполнялось.

Новый script отделяет checkout от подтверждённой установки: `.git/lexinform-relay-deployed`
записывается atomic после sync/restart/45s-health/unit setup, а pending marker сохраняется при
частичном сбое. Оба файла содержат только deployment metadata, не данные бота и не входят в SQL.
Повтор с тем же HEAD снова устанавливает и перезапускает сервис, если нет proof или есть pending.
Failed same-SHA unit repair также не может использовать прежний success. Ограничен параллелизм
uv downloads/builds/installs до1; это уменьшает разрешённую конкуренцию, не доказывает причину SIGTERM.
Stale CI commit пропускается до reset/unit writes: следующий main ждёт собственного CI.
Failure notification больше не утверждает старый код при неизвестном исходе restart.

**16 focused shell tests passed**, real temporary Git remote + fake uv/systemctl/sleep:
SIGTERM/exit sync, same-HEAD retry, restart/health/timer/reload faults, same-SHA repair, current
verified skip, inactive service, все3units и stale CI. Старый false-green воспроизведён тем же
harness без сетевых/service вызовов. Full gate: **5438 passed**, failures/errors/skips0,
strict mypy503, Ruff/format/docs green; frozen inputs unchanged.
[Acceptance asset](../assets/audits/2026-10-28-relay-deploy-recovery/validation.json).
Runtime Python/schema/provider inputs unchanged.

Выпущено `b1d22cbc7afc33bef6f4618b70d2919e0bcfb5bb`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40663368034) success;
[auto deploy](https://github.com/sobolevbel/lexinform/actions/runs/40664268463) подтвердил переход
1f4331a → b1d22cb и45s после restart. Он исполнял прежний script, поэтому отдельно проверен
[новый script](https://github.com/sobolevbel/lexinform/actions/runs/40664625566): same-HEAD sync/restart,
45s health, timer setup и весь job success. [Следующий запуск](https://github.com/sobolevbel/lexinform/actions/runs/40664953943)
дал корректный already-at без restart. Marker write/pending clear подтверждены завершённым
путём нового frozen script и следующим независимым запуском, не прямым SSH-чтением файлов.
B126 перенесён в fixed archive. Дополнительный bot run/paid provider calls не запускались.

<a id="j132"></a>
### J132. Persist bounded runtime extraction provenance

Статус: **готово**, все локальные gates и cross-environment corpus qualification пройдены.
Baseline `b1d22cbc7afc33bef6f4618b70d2919e0bcfb5bb`.
Production TextLoader использует optional physical extraction port с прежней pure package policy;
custom legacy extractors сохраняют совместимый путь. Новый immutable ExtractionResult хранит
известные input/output/physical hashes, component/source-file identities, parser dependencies,
options, format/view/counts и ограничения. Это metadata summary, не полный execution fingerprint.

SQL56 добавляет extraction_results и selection_extractions. Запись идёт на calling thread вместе
с source observation, с проверкой exact outer-body ownership и first-recorded idempotence.
Prefilter, primary, reviews и generic secondary используют общий capture. Historical/direct
bypasses, zero-part failures без selection ref и полная fact-to-block history остаются открытыми.
Максимум16 selected summaries/16KiB JSON; omissions/unknown явные. Body/block/raw archive нет,
completeness не утверждается. Source bytes и provider scan bytes имеют отдельные контракты.

5465 full tests passed, failures/errors/skips0; mypy509, Ruff/format green. Первые шесть failures
были неверно помеченными schema54/55 fixtures поверх новой schema56. Общий test helper теперь
строит настоящую историческую schema из append-only migrations, сохраняет IDs/high-water marks
и проверяет FK; corruption fixtures сохраняют текущую версию. Runtime migration не ослаблялась.

168 native profiles: requests/messages/all28 прежних таблиц exact, timing differences0.
Две новые таблицы пусты для legacy fake port; bootstrap SQL+617 bytes, gzip+74–103.
Actual pinned state4f19d9b (не новый live run): все28 таблиц exact, SQL+617/gzip+119,
SQLite+20480, integrity/FK valid. Старый schema55 reader и новый reader сохранили все30 таблиц
scenario snapshot с настоящим DOCX adapter и fake providers с непустыми extraction rows и schema56, без API effects.

5365 raw/3.753GB/42920 prepared views прошли: весь legacy profile exact, errors/input/code drift0.
20 meaningful raw derivatives проверяют dates/amounts/negation/scope/references; 15 известных
statutory digests меняются, ODT unknown остаётся unknown. Raw/performance measurements выполнены
в shared Pydantic2.13 environment; отдельно freshly extracted4021 unique bodies и проверены все5365paths/input/text/selection/result identities
в deployed Pydantic2.14 environment, errors/changes0. Transformations не менялись; свежие native/full gates
и pilot на13 raw roots всех7formats используют deployed dependencies. Старые measurements не переименовываются.

На исторической raw population metadata median2732B/p953997/max5831. Три отдельных size fixtures:
обычный Word SQL+3148/gzip+1023; 3000-block Word SQL+3157/gzip+1022; ZIP41 с cap16 SQL+12082/gzip+2702.
Повторная запись даёт0. Counterfactual каталог всех5365paths/4021 unique bodies: SQL+14.01MB,
gzip+1.86MB, SQLite+18.42MB; это только artifact/extraction rows, без source/selection links и
без прогноза production demand. Повтор через365дней —0, first times/hash exact. Raw bytes не хранятся.

Local extraction-only performance: 52 fresh processes/104 calls, 13 roots; cold sum root medians
54.786→55.220s, warm54.828→55.201s. Mixed ZIP365→519ms, поэтому overhead не считается нулевым.
Это historical shared-env/local-cache measurements, не универсальная production latency граница.

DA3.5 compatibility и DA3.6 scan identity/window contracts приняты в текущем storage profile.
После provider receipt локальные scan items хранят metadata/checksums; SQL-only replay не обещается.
Completed byte archive отложен по J123. DA8.4 закрыт конечным [dependency audit](../reviews/2026-10-28-retention-dependencies.md):
все guards сохранены, текущие30таблиц учтены, cleanup AST exact. Ни DA3, ни DA8 целиком не закрыты.
План:74 tasks,22 done,6 explicit N/A,46 applicable partial/open.
[Acceptance asset](../assets/audits/2026-10-28-runtime-extraction/validation.json) пинует final source freeze,
все gates, оба окружения, scope reuse, corpus/input hashes, actual state migration и budget exclusions.

J132 выпущен `0f5453e9ea447157023e8cefb24b45170291e40b`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40674247669) success;
[auto deploy](https://github.com/sobolevbel/lexinform/actions/runs/40675419813) подтвердил
переход b1d22cb → 0f5453e, restart,45s health и timer setup.
[Release proof](../assets/audits/2026-10-28-runtime-extraction/release.json) содержит только
allowlisted metadata/filtered lines. Это systemd acceptance, не Telegram functional probe.

<a id="j133"></a>
### J133. Freeze future legacy sitting batches before retry

Статус: **готово; CI/deploy проверены**, все gates и compatibility acceptance пройдены.
Baseline `0f5453e9ea447157023e8cefb24b45170291e40b`.
Qualified legacy AGENDA/AGENDA_CANCELLED batch с хотя бы одним FAILED member теперь сохраняет
точные будущие HTML и ordered membership до pending/checkpoint/API. Допустимы single,
all-FAILED и mixed FAILED/QUEUED0, без rendered/group/continuation/digest/revision ownership.
Saved semantic snapshots, card links, attempts и IDs не переписываются. Это future intent;
предыдущие HTML/состав несохранённой группы не восстанавливаются задним числом.

Repository проверяет весь batch до записи, channel/identity/raw JSON/status/attempt CAS и
отсутствие digest ownership; late CAS failure откатывает все строки. Unknown raw JSON поля
сохраняются. FAILED получает legacy_snapshot; новый QUEUED member сохраняет прежний origin.
Группа отправляется top-level через существующий prepared reader; card parent остаётся ссылкой
в строке. После runner loss UNKNOWN не отправляется автоматически; audited retry/confirm
применяется к сохранённой группе целиком. Существующие modern/frozen producers не меняются.

5553 full tests passed, failures/errors/skips0; 199 focused, strict mypy511, Ruff/format green.
На exact baseline один новый scenario падает на отсутствии prepared-send без setup errors.
Fault scenarios покрывают checkpoint до/после API, fresh remote restore, clock/formatter drift,
audited group resolution и отказ writer. Unit controls проверяют stale members, channels,
ownership, CAS rollback, opaque fields и UTF16 size bound.
168 native profiles: requests/messages/all30 прежних таблиц exact, timing differences0,
SQL/gzip delta0. Source/selection/extraction/provider input не меняются; raw replay не требуется.

Attempted attention-v1 без frozen text, oversized legacy singleton, legacy digest и остальные
неквалифицированные shapes остаются отдельными ветками. Весь DA1 не закрывается; общего
multipart sitting producer этот этап не добавляет. SQL schema56, backend и raw retention прежние.

На9 native sitting profiles schema56→56 API HTML/parent/IDs/owners/attempts/day/model inputs,
paid results и logical outcomes exact; все30 таблиц exact после исключения только трёх
новых future fields. Pre-send и receipt snapshots также проверены. Прирост на fixtures:
SQL+490–1744 bytes, gzip+149–186; это цена сохранения будущего сообщения, не production forecast.
Дополнительные c45/schema55 profiles сохранены как supplemental, их DDL delta не входит в эти числа.

Старый0f reader и current reader восстановили непустые schema56 dumps byte-exact и прошли
27 cases каждый: settled no resend, UNKNOWN audited retry/confirm ×3shapes ×3modes.
По9 fake API calls на reader; никакого повторного render/model/network. Publication/resolution/
checkpoint rows и все30таблиц exact; одно измеренное commands phase время0.5→0.4s сохранено
как duration difference, не скрыто как semantic parity. Миграции AST exact,160 остальных runtime
files byte-exact. [Acceptance asset](../assets/audits/2026-10-28-legacy-sitting-freeze/validation.json)
пинует source freeze, текущие dependencies, XML gates и complete local qualification.

J133 выпущен `7287059ab1392374359fdb07ff6fedb8e7878cec`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40681234966) и
[auto deploy](https://github.com/sobolevbel/lexinform/actions/runs/40682309244) success.
Подтверждены actual0f5453e→7287059, restart/45s health и timer setup;
[release proof](../assets/audits/2026-10-28-legacy-sitting-freeze/release.json) сохраняет
allowlisted metadata, без raw logs/secrets или независимого Telegram probe.

<a id="j134"></a>
### J134. Separate loose-file composition and record candidate normative sets

Статус: **готово; CI/deploy проверены**, full/corpus/native/compatibility gates пройдены. Baseline `7287059ab1392374359fdb07ff6fedb8e7878cec`.
Pure document_composition собирает уже прочитанные snapshots: required continuations,
normative annex order/insertion, self-amendment evidence, optional support и fallback outcomes.
TextLoader оставляет ordered IO, cache reuse и различные required/optional exception contracts.
LoadedFile имеет одно определение в models; прежний service import совместим. Direct review
по-прежнему не получает дополнительную evidence finalization. Official link conflict блокирует
скачивание до network call. Physical adapters/candidate/version/admission policy не менялись.

Schema57 добавляет compact candidate-normative-set-v1 manifest и selection→edition reference.
Identity включает ordered normative roles, exact leaf/text SHA и ranges; полные parts/facts/quotes
остаются в прежнем immutable selection. Это source choice до input fitting, без memo/paid identity
либо заявления о юридической редакции. Один manifest ≤1KiB, без member array/body archive.
Unknown/ambiguous/waiting/unsupported и неоднозначный core остаются без inferred Edition;
self-amendment не становится consolidated bill. Caller transaction сохраняет связи, first time
и повторную immutable identity; doctor проверяет columns/parents/membership без repair.

490 focused tests passed, strict mypy518, Ruff/format green. 168 native profiles: requests,
messages, edits/replies и все30 прежних таблиц exact, timing differences0. Новые manifest/link
views8/8; doctors/integrity/FK valid. На тех же snapshots SQL+492–1024B/gzip+62–241B.
Копия state4f19d9b: old56/current57 сохранили все прежние таблицы и462 Bill models;
новые таблицы пусты, backfill нет. SQL+492B/gzip+96B/SQLite+24576B. Original unchanged,
restore/dump byte-exact и FK/integrity valid; это прежний pinned state, не новый live run.
Старый56 и current57 отдельно сохранили непустой32-table Edition fixture через restore/dump/
prune byte-exact. Наличие совместимого reader не разрешает откат к старому production dump.

На size fixtures manifest182–185B для1/2/2001members; row+link SQL+532–535/gzip+174–294B,
пустая schema+492SQL/+62gzip. Body/part metadata не дублируются; цена ссылки на новый selection
остаётся отдельной от manifest reuse. Это fixture measurements, не production demand forecast.

Полные persisted dispositions/provenance и доказанные relations representation/supersession/
proposal/adoption остаются открытыми DA4.1/DA4.4/DA4.7. Нормативная эквивалентность разных bytes
не выводится из совпадения текста, даты, названия или источника. Backend/raw retention не меняются.

5610 full tests passed, failures/errors/skips0, frozen569 Python files unchanged. Independent
corpus gate:825RCL/4276stage cases, descriptor/text/selection/read/download/cache traces exact;
6468cache hits,85controlled IO/conflict cases exact.1383 missing cached inputs остаются общими
явными gaps; ID-based stubs не выдаются за raw identities. Sejm11786list rows/11784descriptors
и29saved details использованы только для metadata width inventory, не natural text acceptance.

113 historical physical cache records относятся к J132 raw population:6known cores/107none;
49records outsidepopulation исключены.18support-exclusion и18blocked-state probes passed.
20raw meaningful derivatives:15known Edition IDs меняются,5ODTunknown остаютсяunknown.
Дополнительно реальные RCL12390754/stage2 и12403302/stage4:9local raw bodies/1.36MB,
2/3normative members; old/new text113439/270133chars, metadata/IO/metrics exact. Оба выбора
осталисьunverified; юридическая полнота/принятие не выводятся. Physical parser evidence J132
переиспользуется по8byte-exact modules; весь прежний raw cohort заново не извлекался.

Observed max loose width5, alternatives9. IO-free warm measurements75samples/implementation:
wide325495chars median10.004→9.957ms, fallback2.096→2.085ms. Это локальное измерение с перекрытием
процессов, не обещание для произвольной ширины;10missing fallback views обозначены явно.
[Acceptance asset](../assets/audits/2026-10-28-document-composition/validation.json) пинует
полные local manifests, scope reuse, реальные multi-member inputs и migration/size gates.
DA4.2 принят; DA4.1/DA4.4/DA4.7 остаются частичными. План:74tasks,23done,6explicitN/A,
45applicable partial/open. Организация дальнейшей работы закреплена в §1.7 плана:
разработка следующего пакета и независимая приёмка параллельны, full gate один на final tree.

J134 выпущен `563b3e805db04301ae18f5d5e9555d51a9639b19`:
[CI](https://github.com/sobolevbel/lexinform/actions/runs/40683990897) и
[auto deploy](https://github.com/sobolevbel/lexinform/actions/runs/40685055464) success.
Actual7287059→563b3e8, restart/45s health и timer enabled подтверждены в
[filtered proof](../assets/audits/2026-10-28-document-composition/release.json).
Это systemd acceptance, без отдельного Telegram probe или production processing.

<a id="j135"></a>
### J135. Complete future preparation of retryable reader roots

Статус: **выпущен и проверен**. Baseline `563b3e805db04301ae18f5d5e9555d51a9639b19`.
Пакет объединяет совместимые direct-root, legacy reader digest, sitting adoption и guard для
`/preview`; один итоговый full gate идёт на собранном frozen tree. Перед читательским API effect
новые/старые карточки, joint replies, status replies/edits, шесть notices и sitting roundups
получают точный сохранённый HTML, физические части и parent/edit target. Из `Poster` удалены
неиспользуемые callback-ветви прямой отправки. Остаточные legacy QUEUED/FAILED сохраняют IDs,
attempts, raw semantic JSON и frozen origin; UNKNOWN не возвращаются к автоматической отправке.
Неизвестный владелец физической части теперь даёт явную ошибку до `pending`/checkpoint/API;
valid current и legacy card revisions сохраняют оба checkpoint.
Подготовленный direct-root с чужой sitting-group разметкой также отвергается до API:
обычный notice не превращается в roundup из-за некорректного сохранённого поля.
Сохранённый `update_group` для status update остаётся допустимым: его общий текст и checkpoint
не проходят через sitting-group запрет.

Legacy digest с сохранённым HTML теперь отправляет именно сохранённое тело. Пустой прежний
intent получает текущий layout один раз; длинный digest до первого API вызова делится на
сохранённые части, ownership/held release остаётся за последней частью. Большой sitting singleton
также получает frozen chain с реальными receipt parents. Неизвестное владение остаётся
неизвестным. Для `attention=on` старой группы используется актуальный сохранённый snapshot;
top-level roundup остаётся top-level. Некорректная форма доставки отвергается до API effect.

`/preview` в реальном Telegram разрешает только отдельно проверенный тестовый chat:
`@username` сначала разрешается в числовой ID, совпадение с reader channel, неразрешимое имя и
ошибка проверки блокируют отправку. Команда по-прежнему не создаёт publication row. Этот guard
не меняет dry-run/fake paths.

До последних guards 463 focused tests прошли; отдельно прошли55 checkpoint,240 direct-root
и149 update-group focused tests, 25 local HTTP tests на финальном коде, strict mypy528,
Ruff/format green. Три старых scenario expectations и три unit expectations адаптированы;
159 repair tests и отдельные16 sitting unit tests прошли. Последняя правка затронула только
ожидание top-level сообщения; runtime/configuration byte-exact с повторными native/effect/
compatibility proofs. Предыдущие fail-fast/interrupted попытки full gate не являются
приёмкой. Весь unit-набор (2692 tests) отдельно прошёл на финальном дереве. 168 native profiles
сохраняют все32 прежних таблицы, requests/messages/edits и
outcomes; SQL/gzip delta0 на этих неизменённых профилях. Пять отдельных digest fixtures
измерили прирост frozen payload: +9–4239B SQL и +8–263B gzip; повторная отправка не создаёт
дополнительных частей или ownership. Независимый observer сравнил32 reader-effect cases:
26 exact,6 intentional changes (длинные части и saved digest),3 malformed cases с нулём
эффектов до/после. Все новые physical parts ≤4096 UTF-16 units. MockTransport/fakes не доказывают
функциональную доставку в Telegram; paid/provider и production calls не выполнялись.

Schema57, parser, selection, extraction, model input, raw retention и state backend неизменны.
Corpus raw replay для reader-only изменений не требуется; source freeze и тесты old/new читателя
покрывают соответствующие границы. На12 подготовленных schema57 dumps в каждом из трёх
читателей (current/563/0f) сохранены все32 таблицы: pending/UNKNOWN дали0 API calls, failed
дали8 точных old/new bodies/parents/receipts на runtime, repeat0. Это ограниченная совместимость
сохранённых планов, не разрешение на downgrade; откат к старому коду снимет guard `/preview`.
QA и ограничения записаны в локальном `out/corpus/j135/` и в связанном ниже asset.
Статусы DA1 установлены по конечному аудиту reader entrypoints.

5857 full tests прошли без failures/errors/skips на frozen581 source/test/config files.
Все прочие tracked runtime files неизменны; миграции1–57 exact, schema57. Строгий
[acceptance asset](../assets/audits/2026-10-28-reader-delivery/validation.json) фиксирует
XML и hashes, ранее прерванные/failed full attempts как не-приёмку, scoped reuse после
test-only ремонта, независимые native/effect/compatibility результаты и исключения.
DA1.2/DA1.4/DA1.6 приняты локально для нынешней reader surface после конечного callgraph
аудита и fail-closed checkpoint-owner guard. DA1.8 maintenance lock и DA1.9 hosted latency
остаются открытыми. План:74tasks,26done,6explicitN/A,42applicable partial/open.
Коммит `1bd7d7eebf9f260678320e79c1c84c80eb01ff52` прошёл [push CI](https://github.com/sobolevbel/lexinform/actions/runs/40706288523)
и [автоматический deploy](https://github.com/sobolevbel/lexinform/actions/runs/40707955367):
проверен переход `563b3e805db0` → `1bd7d7eebf9f`, 45-секундный health check и активный
timer. [Отфильтрованный release proof](../assets/audits/2026-10-28-reader-delivery/release.json)
содержит точные SHA и job outcomes; systemd proof не является проверкой доставки в Telegram.

<a id="j136"></a>
### J136. Add the legacy-result reader bridge and checkpoint observability

Статус: **локальная приёмка завершена; CI/deploy ожидаются**. Baseline `1bd7d7eebf9f260678320e79c1c84c80eb01ff52`.
Schema58 добавляет четыре пустые таблицы для exact legacy values, их мест сохранения,
once-only import marker и optional общего raw `analysis` body. Миграция не делает inventory,
backfill или переписывание нынешних JSON. Импорт вызывается только явно, хранит неизвестную
original computation provenance как unknown. Reader восстанавливает exact whole value/SHA;
фиксированный `analysis-editorial-v1` upcast служит только текущей typed projection, не меняет
архив. Никакой current writer пока не создаёт ссылок, поэтому старый dump и обычные записи
читаются прежним путём. Активация уплотнения — отдельный этап после deployed reader proof и
нового измерения на копии state с реальными quiet/selection writes и ≤5% приростом gzip.

State checkpoint пишет один ограниченный INFO record с outcome, phase, monotonic durations и
известным объёмом SQL/manifest без SQL-текста, адресов или секретов. Это инструмент для
измерения DA1.9 на естественных hosted runs; локальные замеры не подменяют их. Отдельный
read-only `tools/verify_release.py` сверяет точный main SHA, push CI и автоматический
workflow_run deploy, затем только отфильтрованные признаки перехода SHA, 45-секундного health
и timer. Неполное доказательство остаётся `pending`/`unproven`; systemd не доказывает
функциональную доставку Telegram. В составе пакета нет SSH, rerun или production processing.

Пакет собран поверх J135 без изменения миграций1–57; один import-hunk SQLite adapter
разрешён с сохранением его новых delivery imports. Первый full gate выявил старое ожидание
schema57 в тесте v56 restore; оно исправлено на `SCHEMA_VERSION`. Независимый review до выпуска
нашёл две ошибки: разные values могли занять одно физическое место, а восстановление не
проверяло целостность import manifest/FK. Schema58 теперь уникализирует место без `value_id`,
restore и idempotent import проверяют сохранённые значения, ссылки и manifest; пустой import
не закрывает once-only marker. Исправление и corruption tests прошли158 focused tests,
strict mypy532, Ruff; предыдущие60 независимых focused cases относятся к прежнему freeze.
Проверены все15 интегрированных paths и четыре ожидаемых integrity/test изменения поверх него;
лишних J136 изменений поверх J135 контекста нет. 168 native profiles: все32 прежних таблицы,
requests/reader messages/model inputs exact, четыре v58 таблицы пусты; SQL+1223B,
gzip+222–263B. Финальный source/config freeze586 files неизменен.

На pinned J116 state-copy исходный schema57 dump20,317,340B SQL/4,133,245B gzip;
A-only миграция20,318,590B SQL/4,133,583B gzip (+338B), прежние таблицы exact.
Явный импорт проверен только на временных копиях:323 original values/SHA,267 real typed
serialization/selection roundtrips,667 сохранённых columns,462 Bill readers и148 memos.
Дополнительный counterfactual с optional shared analysis body и quiet/selection writes даёт
4,293,183B gzip (+3.86955%); он не разрешает activation B на свежем state. Наблюдавшийся
пиковый размер временного каталога49,098,224B, steady SQLite22,528,000B; это не upper bound.
Прежний J135 reader и финальный J136 восстановили и выгрузили один schema58 A-only dump
byte-exact: все32 прежние таблицы/2785 строк и четыре пустые новые таблицы сохранены;
462 Bill views,148 memos, pending batches и cost rankings совпали. Это не разрешает rollback
после будущих B references. Первую failed попытку полного gate сохранили отдельно как
не-приёмку. Финальный gate:5940 tests без failures/errors/skips,158 focused,25 local HTTP,
strict mypy532, Ruff, format520 unchanged, docs-check и `git diff --check` прошли на одном
freeze586. Никаких paid/provider, Telegram, SSH, production pipeline или state-branch mutations.
[Acceptance asset](../assets/audits/2026-10-28-legacy-reader-bridge/validation.json) связывает
hashes source, local QA, historical copy, native parity и predecessor compatibility.
DA6.5 и DA1.9 остаются частичными: production import/compact writer и естественные hosted
checkpoint latency пока не включены.
