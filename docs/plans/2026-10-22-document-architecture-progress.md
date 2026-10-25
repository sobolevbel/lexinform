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
