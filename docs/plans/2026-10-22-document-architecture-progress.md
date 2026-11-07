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

Статус: **выпущен и проверен**. Baseline `1bd7d7eebf9f260678320e79c1c84c80eb01ff52`.
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
Коммит `d73accc0bfbda91eff80c4745529fd6465490b16` прошёл [push CI](https://github.com/sobolevbel/lexinform/actions/runs/40713067276)
и [автоматический deploy](https://github.com/sobolevbel/lexinform/actions/runs/40714475047):
точный переход `1bd7d7eebf9f` → `d73accc0bfbd`, 45-секундный health check и активный timer.
[Отфильтрованный release proof](../assets/audits/2026-10-28-legacy-reader-bridge/release.json)
не содержит raw workflow logs. Systemd proof не подтверждает отправку в Telegram.

<a id="j137"></a>
### J137. Record bounded shadow dispositions of outer document candidates

Статус: **выпущен и проверен**. Baseline `d73accc0bfbda91eff80c4745529fd6465490b16`.
Schema59 добавляет immutable content-addressed trace и отдельную many-to-many связь с
source observation, не меняя identity самого observation/selection. `TextLoader.select_document`
пишет нетривиальные ordered решения по внешним URL, original typed request, известные full-body SHA и
выбранное selection state. Trace передаётся в caller-owned transaction prefilter/analysis/review;
внутренний `_load_text` без source observation остаётся явным исключением. Значение `selected`
для URL не объявляет весь комплект юридически допустимым: обязательное приложение может ждать.
Текст, extracted blocks и raw bytes не добавляются. Модельный вход, admission, порядок IO и
сам выбранный документ не меняются.

Каждый JSON ≤4 KiB; при переполнении хранится `omitted_over_limit`, число не записанных решений
и SHA полного исходного запроса вместо мнимо полного усечённого списка. После ранней остановки
fallback остальные альтернативы — `unattempted`. Textless scan при сравнении версий —
`unclassified`, а не `non_bill`. Ошибка только построения необязательной trace не блокирует
прежний выбор; сохранённые trace ID/JSON/FK проверяются в scratch до замены базы при restore.
Миграции1–58 остаются побайтно прежними. Внутренние ZIP-member dispositions и доказательство
официальной relation пока вне этой стадии; DA4.1 остаётся частичной.

Размер определяет policy capture: обычный успешный singleton с `selected`/`unverified` и
единственным исходом `selected` уже представлен source observation и не получает отдельную
trace. Все альтернативы, recovered failures/exclusions, blocked states и overflow сохраняются.
Контрфактическая полная запись4071 исторических RCL решений дала+895508B gzip;
lossless parent-reference codec сохранял покрытие, но экономил только165946B gzip и усложнял
reader. Более узкий anomaly-only gate терял653 реальных multi-candidate сравнений.
Выбран простой gate без повторных singleton rows; финальный actual-writer proof сохраняет
1744 решения и исключает2327 обычных singleton successes:1400 unique traces/links,
+2617914B SQL/+465424B gzip на том же наборе parent observations. Это абсолютный
исторический counterfactual, не прогноз роста production; backfill не выполняется, повторы
не добавляют rows. Первый full gate остановлен намеренно до смены capture policy и не принят.

На corpus825 RCL projects/4276 stage cases выбранные descriptors, text/selection hashes,
outcomes и read/download order exact с J136;1383 missing cached inputs остались прежними,
0 errors. Из4071 completed candidate decisions1698 complete traces,46 explicit overflow,
2327 routine singleton suppressed; максимум compact JSON4051B. Cached downloads используют
ID stubs и не доказывают raw identity. Два реальных multipart RCL cases сохраняют exact input
и все9 selected part SHA соответствуют local raw body;85 IO branches,14 prepared model-text
strata и60 meaningful date/amount/negation/scope/reference mutations exact.

Четыре empty/old55/populated57/populated58 состояния мигрируют с прежними rows exact:
SQL+675B, gzip+129–214B. Отдельный pristine J136 reader восстанавливает populated schema59
dump byte-exact; все36 прежних таблиц, обе новые таблицы и15 reader views совпадают.
UNKNOWN и очередь не изменены. Это синтетическая проверка совместимости, не production replay.
Повтор записи и restore actual-writer corpus dump byte-exact.

Финальная приёмка:5956 full tests без failures/errors/skips,162 focused tests,25 local HTTP,
strict mypy534, Ruff, format522 unchanged, docs-check и `git diff --check` прошли на одном
source/config freeze588. Прерывание первого full gate ради storage policy не является
приёмкой. [Acceptance asset](../assets/audits/2026-10-28-candidate-dispositions/validation.json)
связывает исходники, corpus/storage и predecessor proofs, XML и явные exclusions. Paid/provider,
Telegram, SSH, production pipeline и state-branch mutations не выполнялись. План остаётся
74tasks/26done/6explicitN/A/42applicable partial-open: DA4.1 этим полностью не закрыта.
Коммит `14e45dcc3b51ffc87a4cb36a8fee9b356fa8d333` прошёл [push CI](https://github.com/sobolevbel/lexinform/actions/runs/40722364265)
и [автоматический deploy](https://github.com/sobolevbel/lexinform/actions/runs/40724193750):
точный переход `d73accc0bfbd` → `14e45dcc3b51`, 45-секундный health check и активный timer.
[Отфильтрованный release proof](../assets/audits/2026-10-28-candidate-dispositions/release.json)
не является проверкой функциональной доставки в Telegram.

<a id="j138"></a>
### J138. Preserve existing legacy projections through ordinary result writes

Статус: **выпущен и проверен**. Baseline `14e45dcc3b51ffc87a4cb36a8fee9b356fa8d333`.
Шесть repository save APIs читают прежний physical JSON отдельным raw cursor и сохраняют
только уже существующие refs на тех же structural paths. Общая pure policy проверяет exact
immutable base; supported quiet/selection metadata остаётся в bounded overrides, новый ответ
или modern computation ref получает обычный inline JSON. Вложенный ready record и списки
supplements сохраняют текущие generation/clear/reorder semantics. Никакой writer не создаёт
первую ссылку и не добавляет archive/import rows. Все59 миграций прежние.

Cost ranking разрешает input tokens projection до SQL filter/order/limit и сохраняет NULL,
zero, override/removal, ties и negative-limit semantics. Допустимая очистка supplements остаётся
SQL NULL; найденный независимым review пропуск проверки corrupt prior ref при empty clear
исправлен до полной приёмки. Первоначальный patch подготовлен в изолированной рабочей копии
параллельно full/CI J137, затем интегрирован без конфликтов. Integration focused377,
strict mypy535 и Ruff прошли; independent review20 targeted tests завершён без оставшихся findings.

Свежий state pin `5c3d43b0c5370d250da03221cea0d4a758837398`, SQL SHA
`82cc62062a5e27244cfbc6a0dd4c7094c55adb7bdea8be221b81ec11d0f06633`:
19,973,448B SQL/4,048,618B gzip(level9,mtime0). Изолированный A-only baseline schema59:
462 Bills/38 tables,19,975,346B SQL/4,049,075B gzip, integrity/FK pass; новые history tables пусты.
Это copy qualification; production processing, import/activation и state writes не выполнялись.
Native168 profiles из pristine J137 archive и candidate сохраняют все38 raw tables и SQL SHA
exact, SQL/gzip delta0, requests/model inputs/public payloads/ordered outcomes exact; даже
phase timing differences отсутствуют. Fresh ordinary-writer proof использует две disposable
копии с одним QA-only import и сравнивает seed/same/ready-metadata/quiet/selection/repeat/
changed-answer: во всех фазах462 Bills/185 status changes/148 memos и38 hydrated tables exact,
включая generation/editorial/usage/applications/publications и ranking limits0/1/5/25/1000.
Archive280 values/300 occurrences/1import остаётся exact, shared bodies0. Пять natural immutable
model upcasts корректно уходят inline;12 ready refs выдерживают отдельное metadata update,
а после штатного analysis-clear их12 recreated records остаются inline без первого ref producer.

Selection/repeat control22,076,878B SQL/4,544,740B gzip против projected20,798,847B SQL/
4,227,401B gzip(level9,mtime0). Последнее — максимальный measured projected gzip по фазам,
+4.4041% к A-only baseline с учётом QA fixtures/import; это не activation approval.
Наблюдавшийся SQLite+WAL peak49,842,608B control/47,557,568B projected не является upper bound.
Natural commission-review row отсутствует; supplement lists в pin одиночные; оба scope
дополнительно покрыты unit fixtures. Source/config freeze589 и исходный pin неизменны,
network/paid calls0. Финальный gate:6033 full tests без failures/errors/skips,377 focused,
strict mypy535, Ruff, format523 unchanged, docs-check и `git diff --check` прошли на frozen коде.
Добавлено77 unit cases; новых сценарных случаев нет.
Finite writer audit отдельно подтвердил границу same-location/path: ready→primary, linking,
memo→new projection/event, supplement reorder и review-envelope refresh вне fixed allowlist
могут развернуть ref в inline. Government rehome/SQL work move сохраняют physical bytes,
reset/retention намеренно очищают operational work. Других SQL JSON selectors по table-specific
eligible result columns не найдено. Эти transfer gaps остаются последующими этапами; B activation
J138 не выполняет и не объявляет разрешённой.
[Acceptance asset](../assets/audits/2026-10-28-legacy-projection-writes/validation.json) фиксирует
hashes исходников/первоначального patch, review repair, full/focused XML, native168 и все
fresh-state phases с coverage/exclusions. План остаётся74tasks/26done/6explicitN/A/42partial-open.
Коммит `97f99a5f9461702d27f7cbca8cc4851b6ff2a3fb` прошёл [push CI](https://github.com/sobolevbel/lexinform/actions/runs/40727685682)
и [автоматический deploy](https://github.com/sobolevbel/lexinform/actions/runs/40729427565):
точный переход `14e45dcc3b51` → `97f99a5f9461`, 45-секундный health check и активный timer.
[Отфильтрованный release proof](../assets/audits/2026-10-28-legacy-projection-writes/release.json)
не подтверждает функциональную доставку Telegram.

<a id="j139"></a>
### J139. Move payload matrices to focused Poster contracts

Статус: **выпущен и проверен в составе d7d0ab30**. Baseline `97f99a5f9461702d27f7cbca8cc4851b6ff2a3fb`.
Три expensive payload/policy/attempt/mode matrices сохраняют все69 сочетаний в прямых
контрактах публичного `Poster`: реальная in-memory SQLite, FixedClock, FakePublisher,
checkpoint dump и независимый restore перед publisher callback. В сценарном слое остаются
15 real-Git представителей:4 status,6 notice,5 review с mode wiring off/shadow/on. Все136
noncandidate scenarios и fault/UNKNOWN/recovery cases сохранены; bodies сценарных функций
не меняются, сокращаются только decorators. Runtime/config/dependencies/AGENTS неизменны.

Изолированный targeted набор401→416 cases:140.540s→100.352s, observed saving40.188s/28.6%.
Это локальный замер выбранного набора, не гарантированное ускорение каждого full gate.
Git candidate testcase time55.514s→13.876s, полный direct contract matrix2.072s.
Все7 meaningful mutation probes отвергнуты: date/parent/policy/attempts, missing pre-checkpoint
в unit и retained notice/review Git paths. Временные runtime mutations восстановлены byte-exact.
Independent review подтвердил coverage и отсутствие actionable findings.

Патч собран на pristine J137 параллельно full J138, затем применён поверх J138 без конфликтов.
Integration focused84 passed, strict mypy536 и Ruff green; runtime diff с J138 пуст.
Source/config freeze590 закреплён. Рост общего числа cases
на15 обусловлен отдельными wiring representatives;54 дорогих World/Git сочетания заменены
быстрыми service contracts без потери исходной матрицы. Состав проверок перед commit не ослаблен.

Финальный full gate6048 tests без failures/errors/skips, focused84, strict mypy536,
Ruff, format524 unchanged, docs-check и diff check прошли. XML collection comparison сохраняет
все6033 прежних cases кроме54 перенесённых scenario combinations и добавляет69 unit cases;
два прежних ZIP-byte parameter IDs нормализованы по формату из-за timestamp при сборке ZIP,
их исходник неизменен. Full wall в этом запуске863.058s против предыдущих911.262s;
это observed run comparison, не контролируемое обещание полного ускорения.

Checklist audit закрывает DA1.9 по его local copy-state критерию и DA5.5 в текущем storage profile.
Свежий неизменённый benchmark CLI на pinned schema59 dump:restore0.293909s/dump0.087813s,
6 checkpoints/5 commits; initial1.194395s, unchanged0.220196s, changed0.980573–1.085685s.
SQL19,975,346→19,978,798B/gzip4,049,075→4,049,239B; local/remote objects после GC
4,090,761/4,091,151B, GC1.657850s. Network blocked, input и175 source/tool hashes exact.
Hosted latency/auth/runner overhead/natural growth не измерены и остаются DA10.3; granularity
checkpoint не меняется. Clone здесь не повторялся, отдельное прежнее доказательство — J036.
DA5.5 сверено по5 validation assets и3 pinned finite scan proofs J132:PDF/scan source hashes
и load_scan/first_pages/exact_input AST exact с56107503. Нового raw replay нет; completed-byte
archive исключён текущим профилем, SQL-only replay не заявляется.

[Acceptance asset](../assets/audits/2026-10-28-delivery-test-contracts/validation.json) связывает
source/test hashes, before/after/mutation XML, independent coverage review, свежий copy benchmark
и scan closure audit. План:74tasks/28done/6explicitN/A/40applicable partial-open.
Отдельный ed99339a superseded параллельным merge a1664fbd до подтверждения deploy. CI общей
версии40733209869 обнаружил coverage-shard/Git-dirty race; его исправление и интегрированный
gate записаны в J140. Историческая локальная приёмка J139 не является deploy proof.
Integrated release d7d0ab30 подтверждён отдельно: CI40735937209 и deploy40736634167 green,
фактический relay transition97f99a5f9461→d7d0ab30e800, health после45s и timer enabled.
[Filtered proof](../assets/audits/2026-10-28-delivery-test-contracts/release-integrated.json)
подтверждает составной релиз, не отдельный superseded ed99339a.

<a id="j140"></a>
### J140. Plan and atomically apply a finite legacy import

Статус: **выпущен и проверен**.
Baseline `d7d0ab30e8004d1ea6a6b75e06064e25c74fea5f` включает J139, параллельную оптимизацию
тестового окружения и coverage fix. Патч подготовлен отдельно на `97f99a5f`, перенесён без изменения пяти
owned source/test/tool файлов. Первый full на ed99339a остановлен после появления новой
общей базы; его частичный результат не считается приёмкой.

Чистый planner связывает конечный inventory с SHA полного raw dump, включая WAL-visible
состояние и schema version. Repository apply заново строит план и проверяет physical originals;
одна outer transaction включает import, sharing повторяющихся exact analysis bodies, строго
ограниченные direct replacements, archive/FK/reader checks, dump/restore parity и gzip budget.
При расхождении или росте свыше5% откатывается весь этап. Existing marker проверяется без
нового answer inventory; пустая выборка marker не расходует. Общий finite saved-record catalogue
и walker перенесены из tools в pure module; SQL-reader facade tools сохраняет совместимость.

Сокращение допускается только для whole analysis/joint/review records, record в ready envelope,
amendments, отдельных supplement records и whole memo records. Work/delivery/request envelopes,
encoded evidence и content-addressed snapshots остаются observation-only. Original canonical
values, unknown/absent/null fields и provenance сохраняются; original ownership не выводится
из container owner. Modern computation records пропускаются. Схема и59 миграций не меняются.

Изолированный replay pinned schema59 copy:667 columns,323 exact values/411 occurrences,
14 modern occurrences skipped,282 compacted columns,62 shared bodies для135 whole-value
variants. Baseline SQL19,975,346/gzip4,049,075B; initial compact SQL20,516,240/gzip4,170,910B.
После actual typed quiet/selection/reuse/memo writes максимум gzip4,200,361B (+3.73631%);
повтор quiet writes byte-exact, archive counts323/411/1/62 неизменны. All34 prior-table logical
views,462 Bills,185 changes и полный cost ranking совпадают с control. Проверены267 typed
roundtrips,170 с уже принятой fixed editorial upcast; архив не upcast'ится.

Проверка памяти в свежем one-copy процессе macOS: observed peak RSS863,387,648B; это не bound.
Typed views проверяются потоковыми framed digests, но inventory/dumps/restore copies остаются
в памяти. Этот maintenance рассчитан на canonical Actions writer; relay host не квалифицирован.
Natural ready/commission-review rows отсутствуют в snapshot; их структуру проверяют focused
fixtures. Source/pins изолированного replay неизменны. Independent review: actionable findings нет.

Нового CLI, port, pipeline/workflow entrypoint, automatic import/backfill или production activation
нет. DA6.5 остаётся partial: locked writer operation, exact deployed-writer proof и свежая
snapshot-specific qualification quiet/selection/transfer ещё нужны. Cross-location transfers,
supplement reorder и неподдерживаемые review-envelope updates сохраняют inline fallback;
новой политики retention или raw/backend storage этот этап не выбирает.

Integrated acceptance заново прогнал baseline и candidate всех168 native cases на общей
frozen среде: прежний baseline не переиспользован после изменения harness/conftest. Все38 таблиц,
SQL SHA/bytes/gzip, provider requests, model inputs, publication payloads и command outcomes
совпали; даже разрешённых phase-timing differences нет. Primary copy replay повторил все
storage/view результаты. Свежий one-copy RSS862,437,376B; two-copy sampled SQLite/WAL/SHM
peak93,461,088B. Оба значения observed, без обещания верхней границы.

CI a1664fbd упал только на source-manifest equality при xdist+coverage: `.coverage.*` shards
не были ignored и меняли `working_tree_dirty`, хотя source/fixture hashes не менялись.
Изолированный real-Git probe воспроизвёл false→true→false; одна ignore pattern исправляет
это на false→false→false. Manifest guard не ослаблен. Три isolated regression probes: ignored shard
create/update/delete и обнаружение реальных source/fixture edits. Red1fail/2pass, green3pass,
green с coverage+xdist2 также3pass; initial integrated benchmark module8pass.
Перед push найден готовый parallel d7d0ab30 с тем же ignore fix и своим Git regression test.
Он принят целиком; три локальные probes оставлены evidence-only, duplicate tests не добавлены.

Финальный full поверх canonical d7d0ab30 с coverage options CI:6097 tests,0fail/error/skip,
coverage92%, wall209.759s/4 workers. Предшествующие qualified runs:6099tests/305.683s с тремя
локальными probes вместо одного parallel regression и6096tests/199.017s без coverage.
Это разные составы/режимы на общем host, не controlled speed comparison. Focused233 legacy+6benchmark,
strict mypy545, Ruff всего src/tests/tools, format599 unchanged, docs-check и diff check прошли.
601 source/config hashes frozen; новых scenario cases нет, добавлены47 maintenance unit cases.
Canonical coverage regression унаследован из d7d0ab30;59 migrations неизменны.

[Acceptance asset](../assets/audits/2026-10-28-legacy-maintenance/validation.json) связывает
patch/source freezes, полные/целевые XML, baseline/candidate native и copy-state evidence,
memory probe, independent review и controlled CI regression. План остаётся
74tasks/28done/6explicitN/A/40applicable partial-open; inert import не выдаётся за activation.
Release ef8d9901 подтверждён CI40737834323 и deploy40738549866: фактический relay transition
d7d0ab30e800→ef8d9901643a, health после45s и timer enabled.
[Filtered proof](../assets/audits/2026-10-28-legacy-maintenance/release.json) не включает raw logs.

<a id="j141"></a>
### J141. Run pinned corpus profiles through the shared registry

Статус: **выпущен и проверен**.
Baseline `ef8d9901643a81afc636f19e0554ee880e246851`.
Opt-in `make corpus-check CORPUS_SUITE=release` сводит существующие offline replays в фиксированную
матрицу16 jobs/4 profiles. Strict plan закрепляет INDEX, input artifacts, обязательные режимы
и полный ожидаемый набор observation IDs. Registry children, code snapshots, manifests и semantic
diffs сохраняются рядом с summary; перезапись результатов и подмена controlled flags запрещены.
Baseline получает `recorded`; compare требует intact reference summary/child manifests и те же
plan/profiles/inputs/populations. Недостающие данные дают `not_run/2`, corruption/drift/changed
observations — `failed/1` с приоритетом над неполнотой. Collectors и human-review команды остаются
за пределами этой regression matrix. Child suites сохраняют свои network/model guards.

Independent review выявил два P2 до integration: initial absent raw у scan/PDF/Word/ODT ошибочно
становился failed, а reference pin проверялся только перед replay. Исправления различают initial
absence, corruption и input drift без чтения exception strings; reference summary и child manifest
pins повторно проверяются перед compare и в конце. Legacy process/Senate not_run с описательным
error остаётся not_run. Повторные disposable repros подтвердили оба исправления; новых findings нет.
Delivery использует existing external suite, чтобы все profiles работали с одним corpus root.

Integrated106 focused tests, full6147 с coverage92% и4 workers, strict mypy546 и Ruff прошли;
format600 unchanged. Добавлены50 unit cases, новых scenario cases нет. Исходники приложения,
SQLite/schema, зависимости, production settings и workflow неизменны. Прежний default manifest
`make corpus-check` прошёл. Common tools README ссылается на canonical matrix/plan reference.

Real corpus INDEX2030ab1d и documents catalog2909d71c закреплены. Delivery plan выполнил
baseline recorded → compare passed с тем же observation ID и всеми3 synthetic recovery invariants.
Это проверка orchestration/delivery contracts. Comparison plan перечисляет1028 реальных пар
(468RCL/560rpw-without-rule),1698 text paths и1029 observations с mutation summary; metadata
enumeration сохраняет прежнее исключение rpw. Actual baseline recorded/qualified:1029 observations,
2169 прочитанных inputs, missing/drift0. Выполнены6594 controlled mutation observations;
full text_digest различил все. Experimental digest/guard misses сохранены в исходном отчёте
как диагностические наблюдения: citation/layout cases требуют отдельной классификации, это
не production accuracy verdict. Повтор полного same-code replay не запускался: application и
child comparator/mutation algorithms byte-exact с baseline, compare path проверен real delivery
и отдельными subprocess/ODT contracts. Полный comparison baseline подготовлен для будущих изменений.

Full extraction/selection readiness отдельно открыта:114-PDF manifest привязан к старому INDEX,
семь указанных исторических references/inventories отсутствуют;1383 известных RCL cache gaps
сохраняются. ODT/scan fragments имеют доступные declared raw paths, без новой raw/extraction
приёмки. Полный corpus release, independent labels и production acceptance этим не объявляются.
Порядок запуска и plan schema — в
[tools reference](https://github.com/sobolevbel/lexinform/blob/main/tools/README.md#pinned-release-checks).
Full wall248.469s на общем host; timings не являются controlled benchmark.
Executable Python/config/Makefile/tests оставались frozen. Во время gate сокращён только
tools README до ссылки на canonical plan matrix; отдельный doc delta закреплён.
DA0.5 закрыта по tooling contract; план74tasks/29done/6explicitN/A/39applicable partial-open.
[Acceptance asset](../assets/audits/2026-10-28-corpus-release/validation.json) связывает
reviewed patch, full/targeted XML, sealed child manifests, plans, preflight и input/code hashes.
Исходные подробные результаты и code snapshots остаются в immutable sidecars; JSON asset хранит
агрегаты и refs, без копии corpus bodies или повторной полной input map.
Release943ce897 подтверждён CI40743085289 и deploy40743661189: фактический relay transition
ef8d9901643a→943ce8971a68, health после45s и timer enabled.
[Filtered proof](../assets/audits/2026-10-28-corpus-release/release.json) сохранён отдельно.

<a id="j142"></a>
### J142. Retain bounded pipeline download diagnostics

Статус: **реализовано, CI и обновление реле подтверждены**.
Baseline `943ce8971a6861ef0824b5730f8f42132dcb3fd9`.
Optional versioned RunReport field измеряет TextLoader download invocations. Cache hits,
invalid/unknown routing и known pre-send budget rejection различаются; adapter-internal HTTP
retries не объявляются отдельными wire attempts. Старые document_reads v1 classes/JSON exact.
Shared typed error details сохраняют прежние exception classes/messages/chains и retry/outage
budgets; services не ветвятся по конкретному adapter. Outage не расходует bill attempts.

Фиксированные caps:8 grouped failure examples и2048 compact UTF-8 bytes. Exact outcome totals
и omitted failure counts сохраняются при deterministic eviction/size trimming. В examples есть
только URL SHA, configured safe host, fixed reason/outcome, known status/limit и count.
Raw URLs, messages, bodies, partial-body SHA и выдуманных timestamps нет. Полные artifact read
receipts и selection/model inputs не меняются. Workers обновляют locked memory, pipeline caller
сохраняет snapshot в прежнем finally/finish_run transaction. Empty/unmeasured field omitted.
Новой схемы/таблицы, backfill, retention policy, raw/backend store или settings нет.

Independent review: actionable findings0. Integrated190 focused и25 local HTTP checks прошли.
Full6186 tests/0fail-error-skip с coverage92%, wall214.542s/4workers; mypy549, Ruff,
format603 unchanged. Из39 новых cases33 unit проверяют accounting/cap/privacy/error contracts,
6 scenario — pipeline caller finalization, reset/workers и dry-run rollback.

Paired51 download/error cases на трёх adapters:2 indexed corpus PDFs/6 real-body deliveries
и status/WAF/transport/stream/limit/cache/routing/budget/parser fixtures. V1 JSON, text/selection/
complete receipts, exception/message/cause hashes и ordered requests совпадают с baseline.
Native168 проверяет38 таблиц: только validated diagnostics и phase_seconds допускаются к
normalization; остальные reports/SQL bytes, metrics, model/provider inputs, messages, targets,
attempts/order exact.180 SQL reports/64 profile fields проверены; max payload283B, суммарный
delta fixture dumps56,880SQL/19,250gzipB, max164gzipB/case. Это fixture measurements, не daily forecast.

Cap stress300 failures/100 groups: normal host8 retained groups/24 failures и276 omitted,
1576B;253-character host4/12 и288 omitted,1912B. На385 copied non-null report slots hypothetical
completed-only: +121,660SQL/+1,963gzipB; one unique failure/slot: +183,260/+19,542B;
eight failures/slot: +617,155/+135,317B. Это counterfactual без backfill, не global size bound.
Старые385 reports читаются с unknown/omitted новым полем; null slot остаётся null.

Scope audit оставляет DA2.4 partial: production scan/reprefilter обходят pipeline.run(), как и
standalone analyze/explain/amendments и RCL lookup/show с чтением consultation letters.
Здесь failed/limited observations ещё могут остаться только в памяти/logs; subsequent commands
process их не восстанавливает. Pipeline inbox команды покрыты. Direct consultation-results HTML
catalog не расширяет этот work item до общего HTTP ledger. Нужен отдельный общий lifecycle для
остальных file consumers; фиктивные Bill owners и новые per-download rows не добавлены.
[Acceptance asset](../assets/audits/2026-10-28-download-diagnostics/validation.json) связывает
source freeze605 files, full/targeted/HTTP checks, paired/native replay и storage/stress evidence.
Raw/report details остаются в исходных QA artifacts; asset содержит агрегаты и SHA refs.
План74tasks/29done/6explicitN/A/39applicable partial-open: scope gap отмечен в DA2.4.

J142 release: commit `6bb31bbb34260404b6a0a419f738601d31f66332`, CI40749351407 и deploy40749846821;
реле перешло с `943ce8971a68` на `6bb31bbb3426`, health после45s и timer подтверждены.
Последующая интеграция mutation-аудита `27b8181355e3f111916ef244308851bbe83deb2c` также прошла
CI40750311611/deploy40751010858 с переходом с6bb31bbb, health45s и timer.
[Историческое подтверждение](../assets/audits/2026-10-28-download-diagnostics/release.json)
и [интегрированный выпуск](../assets/audits/2026-10-28-download-diagnostics/release-integrated.json)
проверены отдельно; проверка старого SHA не выдаётся за состояние текущего main.


### J143. Bound resumable corpus collection and restore the PDF replay generation

Статус: **реализовано, CI и обновление реле подтверждены**. Baseline `27b8181355e3f111916ef244308851bbe83deb2c`.
Отдельный staging pilot читает pinned plan максимум на 20 официальных RCL originals:
serial/no retries, 5 MiB на response, 128 MiB на charged application bodies + retained originals +
24 MiB reserved metadata. Это не wire/TLS accounting и не production archive/backend.
Active corpus, INDEX и caches не меняются. Installation остаётся отдельной операцией.

Resume сверяет exact sealed inventory, unique retained inodes и reservations до HTTP;
unknown request сохраняет полный резерв и не повторяется. Intent, code и directory chain
fsynced до запроса. Strict HTTPS ports ограничены 443; query provenance принадлежит final URL.
Один deterministic final bundle на pilot связывает все dispositions и проверенные hardlinks;
повтор verified bundle read-only, partial output не заменяется, copy fallback отсутствует.
Pilot lifecycle отделён от collect/install; общий create-only helper сохраняет прежний контракт.

Независимое review закрыло budget/resume, port provenance, directory durability и повторную
finalization; дополнительный stale redirect-query hash исправлен отдельным точечным тестом.
Механический перенос сохранил AST 34 production definitions; позднее изменился только fetch.
Crash/power-loss injection не проводился; проверены fsync ordering и отказ до HTTP при ошибке.

PDF generation сохранена отдельно от исторической: 114 raw SHA exact, 330 346 602 B;
исторический INDEX найден byte-exact в corpus commit 408819d01f03339097c720db4aa13bfb1c5f0e90.
При новом INDEX все case records/exclusions сохранены, у трёх дел добавились только связи.
Новые library/adapter recordings совпали: 114 observations, 10 670 pages, 0 errors/changes,
inputs/code unchanged. 210 meaningful mutation probes на 42 files обнаружены; 72 files без
применимого Art.1/law-digest probe не объявлены покрытыми этими мутациями. Это machine
page oracle и regression population, не независимая юридическая разметка.

Кодовая приёмка завершилась до network pilot; установка в активный корпус не выполнялась. План: 74 tasks / 29 done / 6 explicit N/A /
39 applicable partial-open; восстановленная PDF generation не означает полный extraction gate.

Финальный gate: 6290 tests / 0 failures-errors-skips, 214.092s, coverage92%; mypy553,
Ruff/format и docs-check passed. Все612 source/config hashes неизменны. Новые63 cases — unit;
сценарный набор не расширялся. Focused72 и independent review закрыты.
[Acceptance asset](../assets/audits/2026-10-28-corpus-gap-pilot/validation.json) связывает
финальную версию, проверки и обе PDF recordings.

J143 commit `1ff8e8def142d6ebf76bd5534d067efccae80b04`: CI 40755799635 и deploy 40756617831
прошли; переход реле с 27b818, health 45s и timer подтверждены
[release proof](../assets/audits/2026-10-28-corpus-gap-pilot/release.json).
После выпуска bounded pilot получил 20/20 originals, HTTP 200, 8 894 020 B; unknown/retries 0.
Offline final bundle не добавил body bytes; 20 файлов извлекаются непустыми, 2 173 312 characters.
Selection остаётся unverified; legal labels не добавлялись. Active INDEX/documents index exact,
install не выполнялся. [Pilot evidence](../assets/audits/2026-10-28-corpus-gap-pilot/pilot.json).

### J144. Read CLI document observations without counting them as executions

Статус: **реализовано, CI и обновление реле подтверждены**. Только reader-first этап DA2.4.
`cli_reads` требует finite `cli_read_operation`; другие modes отвергают это поле. Старый JSON
его не получает. Списки прогонов показывают loader observations и unmeasured execution/model
accounting. Cost totals/denominators/dearest, status health, month/week figures и weekly
completeness исключают этот scope; аудит чтения файлов выделяет его отдельно.

Producers, caller lifecycle, Container, repository, pipeline, schema и retention не изменены.
Show/read-only lookup сохраняют прежний контракт. Preview не получает нового report owner;
его существующий lookup может зарегистрировать неизвестное дело до ответа «not analysed».
Утверждение о полностью SQL-read-only preview было неточным; S1 не меняет этот путь. После будущего включения записи минимальная
rollback-версия должна поддерживать этот reader; старый 6bb отвергает новый enum.
385 исторических report serializations и historical profile hash exact; проверка повторена
на интегрированной версии. Новые synthetic rows проходят dump/restore и сохраняют прежние
watermark/prune semantics. Технический formatter не меняет editorial content versions.

Full: 6315 tests / 0 failures-errors-skips, 220.975s, coverage 92%; focused 310, local HTTP 25 / 11.26s,
mypy 554, Ruff/format passed. Добавлено 23 unit cases и 2 boundary scenarios для command routing
и weekly completeness. Independent review не нашло actionable findings.
[Acceptance asset](../assets/audits/2026-10-28-cli-readers/validation.json).
DA2.4 остаётся partial до отдельного caller-owned capture; план: 74 / 29 done / 6 N/A / 39 open.

J144 reader commit `8a76a8e4f8a021f6b195c082bf901e09cd660914`: точные CI/deploy и переход
реле с1ff8e8de, health45s/timer подтверждены
[release proof](../assets/audits/2026-10-28-cli-readers/release.json).
Это минимальная совместимая версия для будущих CLI report producers.


### J145. Centralize fixed native replay and qualify baseline reuse

Статус: **runtime replay завершён; CI lint отказал, исправление в J145a**.
Шесть существующих observer bodies перенесены byte-exact в tools; состав остаётся 168 cases.
Один runner владеет запуском, immutable source/artifact manifests, environment/import/read
closure и fail-closed reuse. Он проверяет SQL/FK/integrity и derived profile values, включая
runs hashes. Guard запрещает сеть, запись вне output, исполнение subprocess и чтение `.env`;
source/fixture/runner aliases отклоняются до чтения. Positive reuse проверяет запрошенный
before snapshot и actual environment; AST similarity или старый ad-hoc report не заменяют proof.
Cross-run comparator ещё не включён в этот API; accepted raw comparisons сохранены отдельно.

Independent review исправил descriptor-relative write escape и source-preflight alias read;
root review также закрыл manifest-entry read до validation. Пробы использовали безопасные
внешние fixtures, без credential files. Final 97 focused checks и 6384 full tests прошли:
0 failures/errors/skips, 225.401s, coverage 92%; mypy 555, Ruff/format passed. Новые 69 unit cases
проверяют guards/manifests/reuse; default scenario suite не расширен.

Fresh 168 на интегрированной версии: 348 raw SQL/profile comparisons exact против primary
до entry fix и agent baseline до J144. Normalization не потребовалась; provider inputs,
messages/order и все SQL bytes exact. SQL total 7 574 128 B, gzip 9 / mtime 0 total 1 377 404 B;
изменений объёма 0. Same-tree reuse witness passed; source/config freeze exact.
[Acceptance asset](../assets/audits/2026-10-28-native-runner/validation.json).
План: 74 tasks / 29 done / 6 N/A / 39 applicable partial-open; это автоматизация приёмки существующего
контракта DA0.5, без заявления о завершении natural production coverage.


### J145a. Match the full lint gate and separate its CI job

Статус: **реализовано, CI и обновление реле подтверждены**. CI 40760522668 остановил J145
на 57 lint errors в observer modules: локальный статический gate проверял только выбранные
новые tools, CI — весь каталог. Реле осталось на успешном reader 8a76; ложного deploy proof нет.
Все шесть observers приведены к общим правилам, без suppressions. Два loop closures получили
явный default binding; один постоянный getattr заменён эквивалентным attribute access.
Остальная логика и 168 case identities сохранены; новые source hashes задекларированы явно.

По запросу пользователя lint вынесен в отдельную CI job. Она ставит только frozen Ruff из
новой lint dependency group; dev включает ту же группу, 105 остальных lock records exact.
Test job зависит от lint и затем проверяет types, pytest и corpus. Docs/web остаются параллельны.
Release verifier поддерживает точные старый 3 / новый 4 profiles и требует успеха всех observed jobs;
дубликаты/неизвестные jobs не принимаются. AGENTS, CONTRIBUTING и check skill теперь включают
весь tools, как существующий make check и CI.

Full: 6388 tests / 0 failures-errors-skips, 223.121s, coverage 92%; mypy 555, полный Ruff/format 621 passed,
focused 108. В чистом lint environment установлен только Ruff 0.17.2. Fresh 168: 174 raw SQL/profile
artifacts exact относительно J145, без normalization; same-tree reuse passed. Independent
review не нашло замечаний к job dependency/lock/verifier; 47 readonly checks passed.
[Incident/fix evidence](../assets/audits/2026-10-28-native-runner/ci-lint.json).


<a id="j146"></a>

### J146. Capture standalone CLI document observations at the caller boundary

Статус: **реализовано, CI и обновление реле подтверждены**.
Девять mutating roots регистрируют настоящий runs.id до lookup/file work: scan, reprefilter,
analyze, explain, amendments, republish, forget, reset и observe. Clock/context и finally
принадлежат вызывающей команде; после workers и до close одна короткая transaction сохраняет
complete-body metadata/receipts, IO report и существующий prune. Outer rollback сохраняется.
Ошибка finalization или log handler не заменяет исходный результат, Exception/BaseException
или Typer exit. Report измеряет только loader IO, без выдуманного бизнес-статуса/LLM accounting.

Поддерживающий reader 8a76 уже реально развёрнут. Producers не создают новых provider/notifier/
checkpoint calls и не меняют lookup/selection/model inputs. Inbox/pipeline не получают второй
owner. Show/read-only lookup не оборачиваются; preview сохраняет прежнее поведение, в том
числе enrollment неизвестного дела, и не получает нового IO report.

Independent review подтвердил 6 source hashes и 49 CLI ASTs после удаления 9 wrappers.
11 paired World callers: schema/user_version и 418 whole-table comparisons проверены;
изменились только 9 validated CLI reports, 4 append-only artifacts и 4 receipts. Старые строки
не переписаны/не удалены; остальные 401 comparisons exact, show/preview raw SQL exact.
Report envelope 1649–2052 B, max fixture delta 2596 SQL / 830 gzip B: это синтетические отдельные
DB measurements, не natural daily forecast/global bound. Backfill нет; runs retention прежний.

Final make check после CI/lint исправления: 6417 tests / 0 failures-errors-skips, 217.328s, coverage 92%;
mypy 558, Ruff/format 624 по всем src/tests/tools. Focused 68, local HTTP 25 / 14.92s. Новые 13 unit
cases проверяют общий helper; 16 caller scenarios покрывают реальные границы 9 roots и ошибки.
[Acceptance asset](../assets/audits/2026-10-28-cli-read-capture/validation.json).
DA2.4 partial: show/read-only lookup/preview и direct catalog HTTP не объявлены полным ledger.
DA2.3/2.5 ещё требуют source-use relations для RCL letters, historical rereads и Senate candidates;
повторный finalizer для уже покрытых попыток не нужен. План: 74 / 29 done / 6 N/A / 39 open.

J145a commit `842a91e6f80c2e9b5b52bc5b73fa068fdaca9612` прошёл все четыре CI jobs,
включая новый lint, и deploy. Реле перешло с8a76 на842a; health45s/timer подтверждены
[release proof](../assets/audits/2026-10-28-native-runner/ci-lint-release.json).
J145 включён в этот исправленный выпуск; e7 не объявляется отдельно развёрнутым.

J146 `f503e952e62cb78ffeee1d9bd01f7ec0c7de2b6c`: все четыре CI jobs и deploy прошли,
реле перешло с 842a на f503; health 45s/timer подтверждены
[release proof](../assets/audits/2026-10-28-cli-read-capture/release.json).


<a id="j147"></a>

### J147. Preserve current source evidence from historical rereads

Статус: **реализовано; docs failure исправлен, CI и обновление реле подтверждены**.
Historical previous-law/change-review reads сохраняют returned source metadata через прежний
caller-owned transaction с реальным Bill owner. Это текущее наблюдение, отдельно от неизменного
предыдущего paid input. Успешные и завершённые blocked selections сохраняются; неизвестный
источник до завершения selection не придумывается. First-analysis workers не пишут в SQL.
Один typed helper сохраняет прежний best-effort/fatal outage порядок; DB errors проходят наружу.
Поздняя wrapped scan-outage cause chain сохранена после independent review, direct chains прежние.

139 focused и 56 paired fixture cases прошли. Real-corpus proof: 76 paired public callers,
40 cached-view cases из 10 J141 pairs, 24 meaningful controls, 12 physical extractor cases
(ZIP→Word, PDF→Word, Word→Word). 20 raw originals / 12 001 182 B имеют отдельные SHA pins;
6 совпали с историческим J124, 14 закреплены заново. Cached/synthetic identities не выдаются
за original raw. Из 1028 prior pairs 588 eligible; 440 caps/role exclusions и 578 eligible unselected
явно сохранены. Контроли deadline/date/amount/negation/scope/reference дошли до change review;
это проверка управляемых различий, не независимая юридическая разметка.

Decisions/IO/errors/model contexts/requests/computations exact. Schema/FK/integrity и whole-SQL
проверки прошли; старые строки не изменены. Различаются только append-only source graphs,
в том числе 12 extraction_results / 12 links в независимых cases. Предыдущие paid refs остаются
неизменными/unknown. Новых запросов, моделей, backend, schema или capture policy нет.
Full make check: 6431 tests / 0 failures-errors-skips, 218.531s, coverage 92%; mypy 559,
Ruff/format 625 по всем src/tests/tools, source freeze 627 exact.
[Acceptance asset](../assets/audits/2026-10-28-historical-rereads/validation.json).

Дополнительный [естественный срез хранения](../reviews/2026-10-27-storage-profile.md#дополнительный-естественный-срез-28-октября)
проверен только на отдельных копиях; DA2.1 остаётся partial из-за unmeasured history и неполного
окна/source coverage. RCL letter use и Senate candidate inspections ещё открыты в DA2.3/2.5;
план: 74 / 29 done / 6 N/A / 39 applicable partial-open не объявляется выполненным по этому одному seam.

На 76 независимых simulated DB pairs aggregate source-metadata delta составил
286 584 SQL / 43 259 gzip B, max 13 151 SQL / 1855 gzip B на fixture. Это отдельные
копии, не natural daily forecast и не общий предел dump. Повторный расчёт побайтно
воспроизвёл агрегаты; source bodies/input texts не добавляются в production history.


J147 commit `7516ed2a3539181f64ed233a80f1f5724c943dbd`: lint, test и web прошли,
но CI 40773710544 остановился на шести ссылках к отсутствующим коротким anchors J146/J147.
Добавлены явные anchors, без изменения runtime или ослабления strict docs gate;
реле осталось на f503 до успешного следующего выпуска.
[Failure evidence](../assets/audits/2026-10-28-historical-rereads/ci-docs-failure.json).


J147 с исправлением anchors выпущен в `61e5fab54695174c81501075fe9e0ab5c2bca171`: все четыре
CI jobs и deploy прошли. Реле перешло с f503 на 61e5; health 45s/timer подтверждены
[release proof](../assets/audits/2026-10-28-historical-rereads/release.json).


<a id="j148"></a>

### J148. Read bounded RCL letter-use history before producer activation

Статус: **локальная приёмка reader завершена; CI/deploy ожидаются**.
Одна append-only migration 60 добавляет RCL-specific metadata history с официальным
project/stage/letter owner, без выдуманного Bill/term. Запись описывает parse и in-memory merge,
а не сохранённое application, актуальность deadline или delivery. Optional exact body/read/
extraction refs проверяются одним decoder в get/iterator/doctor и scratch restore до изменения
целевой DB. Полная запись воспроизводит semantic SHA; oversized metadata заменяется явным
omission envelope, который не притворяется полным и не может перепроверить отсутствующие поля.
Читатель ограничивает full JSON 4096 B / omitted 2048 B; shared parent metadata считается отдельно.

Collector/writer inert: RclProjectReader, Container, parser, pipeline и CLI finalizers его не
вызывают. Не меняются запросы, paid inputs, model/parser versions, extraction/selection,
старые RCL/Bill JSON или политика хранения raw. Следующий producer разрешён только после
подтверждённого развёртывания этого читателя. Повторный факт сохраняет first recorded_at
и не создаёт строку на каждый poll; старые origins остаются unknown.

M0 измерил 437 corpus-derived cached-input counterfactual contexts, а не естественный спрос
или сохранённые applications. У всех body origins unknown; max actual JSON 2915 B.
Эта synthetic population добавила 1 216 657 SQL / 145 800 gzip B. Stress full/omission
с known refs составили 3163/1735 B; 2719 B shared extraction fixture вне record cap.
Через 365 дней повтор добавил 0 rows/SQL/gzip bytes. Actual f503 reader сохранил populated
schema60 SQL byte-exact, но это opaque preservation, без semantic validation новой таблицы.

На disposable копии настоящего state e15b53d0 прежние 38 таблиц/schema/rows точны.
Пустая новая таблица добавила 834 SQL / 266 gzip B; schema60 dump/restore byte-exact,
FK/integrity/doctor passed. Production processing не запускался. Independent review
не нашло замечаний; отдельные пробы подтвердили одинаковый reject повреждённых parents
в get/doctor/restore и отсутствие изменений целевого dump после отказа restore.

DA2.3/DA2.5 остаются partial до producer и Senate inspection; план остаётся
74 / 29 done / 6 N/A / 39 applicable partial-open.


Final make check: 6471 tests / 0 failures-errors-skips, 216.87s, coverage 92%; mypy 563,
Ruff/format 629 по всем src/tests/tools, source/config freeze 631 exact. Новые 40 unit
cases не расширяют scenario suite. Первый full выявил одно устаревшее ожидание current schema59;
оно и аналогичная новая проверка теперь используют SCHEMA_VERSION, затем весь gate повторён.

Native replay: все 168 cases прошли; прежние строки/schema, reports, messages и provider
inputs exact. Отличается только проверенная пустая таблица/indexes/schema60: +834 SQL B/case,
+149–240 gzip B/case. После replay изменились ровно два test assertions, runtime/fixtures/
observer/runner exact; same-tree reuse через эту границу не заявляется.
[Acceptance asset](../assets/audits/2026-10-28-rcl-letter-reader/validation.json).


J148 reader commit `9a3729a829c2bb11f2fc20862c12c769095e3407`: все четыре CI jobs
и deploy 40777416543 прошли; реле перешло с 61e5 на 9a37, health 45s/timer подтверждены
[release proof](../assets/audits/2026-10-28-rcl-letter-reader/release.json).
S1 reader prerequisite для RCL producer выполнен.


<a id="j149"></a>

### J149. Accept the measured current storage profile and bounded download contract

Статус: **documentation-only приёмка завершена; strict docs/link/build и diff gate прошли**.
Независимый аудит сопоставил буквальные критерии DA2.1/DA2.4 с текущим metadata-only профилем.
Global wire ledger, восстановление отсутствующей истории, crash-proof capture и новый SQL owner
для show/read-only lookup/preview не требуются этими критериями. Гарантии не расширены:
unknown при потере процесса и existing preview enrollment сохранены. DA2.4 закрыт по уже
развёрнутым J142/J146: bounded errors/omissions, complete-body identity, caller finalization,
rollback/workers и старые report/exception/IO contracts имеют проверенные proofs.

DA2.1 закрыт конечной [оценкой текущего профиля](../reviews/2026-10-27-storage-profile.md#current-profile-assessment):
pinned historical inventory по source/suffix, explicit unknown scan/ELI/paid/publication joins,
четыре storage alternatives и actual guarded retention sensitivities на свежей копии.
Измерения stock, observed transfer window, SQL snapshots и Git history не смешиваются.
Для 30-дневного scratch сценария удалены 169 runs/10 sent payloads/23 memos; mandatory work,
unsent identities/payloads/accounting и discovery watermark точны. 90/365/default age effects
нулевые на этих retained rows; каждый вариант отдельно очищает один уже eligible batch payload.
Production retention, backend и raw profile не менялись.

Inputs/source SHA проверены before/after, все изменения DB только в удалённых scratch copies.
Новых fetch, Settings/.env, CLI/pipeline, provider/paid или production calls нет. Исторические
checkpoint/pack/dual-write numbers не выдаются за новые benchmarks или разрешение на activation.
[Acceptance asset](../assets/audits/2026-10-28-storage-assessment/validation.json).
Runtime/tests/tools/config byte-exact с J148; повтор full pytest для изменения текста не требуется.
План теперь: **74 / 31 done / 6 explicit N/A / 37 applicable partial-open**.


J149 commit `db34f493ab17dfe699ea08e75aa9bd631f47b40b`: все четыре CI jobs и deploy прошли;
реле перешло с 9a37 на db34, health 45s/timer подтверждены
[release proof](../assets/audits/2026-10-28-storage-assessment/release.json).


<a id="j150"></a>

### J150. Capture actual RCL consultation-letter source use

Статус: **локальная приёмка завершена; CI/deploy ожидаются**.
Поддерживающий S1 reader 9a37 уже реально развёрнут. RclProjectReader сохраняет факты
достигнутых read/parse/date/merge этапов, точную parser input identity и actual before/result/after
пяти letter-derived fields. Один прежний read/parse/merge, без дополнительных запросов
или LETTER_PARSER bump. Это source use/in-memory merge, не saved application/current deadline
или delivery verdict. Legacy/cached origins не реконструируются.

Container разделяет один clock-free collector между readers. Generic pipeline/CLI owners
получают один opaque persistence hook и не импортируют RCL. Worker callback не пишет SQL;
после workers существующая caller transaction пишет exact parents/extraction/use перед report.
New-hook failure проходит через bounded generic error с class-only handling, сохраняя
original business result/exception/cause/cancellation и прежнюю atomic rollback политику.
Show/read-only lookup/preview не получают owner; unknown preview enrollment прежний.

Unsafe descriptor URL в новом RCL envelope представлен hash/explicit omission; unsafe read
object/ref туда не переносится. Exact body/extraction могут сохраняться отдельно. Existing
generic receipt behavior не меняется; это не обещание redaction всей исторической БД.
Schema, public RCL/Bill JSON, parser/selection/admission и model/provider/CLI flow неизменны.
Raw/backend/retention не расширяются. Full/omitted use JSON ограничен 4096/2048 B; shared
extraction JSON имеет существующий предел 16 384 B и считается отдельно. Общий dump зависит
от уникальных фактов; повтор/поллинг не создаёт версию только из-за нового Clock time.

118 focused и independent review прошли без actionable findings. Корпус: cached и actual-raw
lanes по 2031 paired public-reader contexts, суммарно 4062; business/window/errors/ordered IO
и extractor options exact. Полная популяция 947 descriptor slots сохранена: 404 available,
543 missing excluded, без fake failures/downloads. В каждой lane явно отмечены 876 partial
case entries; seeded/available-only contexts counterfactual, не natural demand/application.
437 use facts в каждой lane: cached origins unknown; raw имеет 403 body/extraction parents
и 404 URL receipts, все body SHA перепроверены по originals. 433 exact input mappings и
4 no-text outcomes различаются. Max persisted use 2910 B cached / 3151 B raw; 1 104 959 B
shared extraction JSON не включены в record cap. Raw-only graph добавил 2 787 907 SQL /
374 012 gzip B, из них parents 1 373 493 / 162 418 B; это corpus-sized synthetic population,
не daily forecast. Повтор +365 дней: 0 dump growth.

Native 168: 144 SQL artifacts byte-exact; только 24 handoff cases добавили по одному
validated use, без новых parents. Все прежние rows/reports/messages/provider inputs exact;
per-case delta 2525 SQL / 700–742 gzip B, aggregate независимых fixtures 60 600 / 17 204 B.

Final make check: 6500 tests / 0 failures-errors-skips, 228.98s, coverage 93%; mypy 565,
Ruff/format 631 по всем src/tests/tools, source/config freeze 633 exact. Новые 19 unit cases
и 10 boundary scenarios; 25 local HTTP tests прошли за 3.15s.
[Acceptance asset](../assets/audits/2026-10-28-rcl-letter-capture/validation.json).
DA2.3/DA2.5 partial до Senate candidate inspection; план 74 / 31 done / 6 N/A / 37 open.


J150 commit `2a6e599bb4df20925165b24d8c811dd4cc1db903`: CI 40781447926 и deploy 40782126787
прошли; фактический relay transition db34 → 2a6e, health 45s/timer подтверждены
[release proof](../assets/audits/2026-10-28-rcl-letter-capture/release.json).


<a id="j151"></a>

### J151. Read Senate inspection history and bind retained RCL omissions

Статус: **локальная приёмка завершена; CI/deploy ожидаются**, 29 октября.
Одна append-only migration 61 добавляет Senate inspection и body-junction tables.
Факт хранит actual Bill owner, официальную relation к resolution print, ordered attachment
universe, достигнутые inspection/skip/reason/group/result fields. Descriptor index/hash — locator,
не выдуманный официальный ID. Каждый known complete body сохраняет SHA/size и junction;
неизвестные source/read/extraction IDs остаются null. Shared get/doctor/scratch restore
проверяют record/SQL identity, manifest и реальные parents до изменения target DB.

8 KiB full / 2 KiB whole omission включают timestamp. Immutable identity связывает opaque
full semantic SHA, весь retained summary и ordered link count/hash, исключая Clock time.
Пропущенное полное metadata не становится доступным для semantic revalidation. Capture version
описывает формат; source code hashes описывают обработку. Модель проверяет структуру и refs,
не повторяет business policy resolver. Collector/writer инертны; Container/resolver/CLI/pipeline
не создают Senate inspection records. Предыдущие source/body/paid/output contracts прежние.

Independent review нашло и закрыло три проблемы до публикации: worker completion order влиял
на snapshot/SQL; omitted manifest можно было согласованно заменить под прежним ID; validators
повторяли текущие selector rules. Stable-ID snapshot, retained identity envelope и структурная
валидация устранили их. Root также закрыл более узкую numbering grammar относительно public str
и rsplit/PurePosixPath drift на dotfiles; nullable refs не заполняются guesses.

Sena corpus-derived cohort: 759 context records / 724 resolution prints, 929 known originals,
974 mandatory body links. Actual serializer: все 759 complete, max 6641 B с timestamp; synthetic
ref-capacity max 6955 B. Unknown persisted parent IDs не выдаются за existing refs.
Record+junction delta 3 726 372 SQL / 590 156 gzip B, artifact parents отдельно
124 538 / 41 115 B; owner rows seeded только в counterfactual fixture. Это не natural demand
или общий предел БД. Отдельный real-extractor synthetic graph: full 3094 B / omission 1237 B,
2723 B shared extraction metadata вне cap. Old9a сохранил 761-record/976-link SQL opaque
byte-exact; original IDs/parents и +365-day repeat проверены, без assertion old semantic validation.

Аналогичное persisted-integrity ограничение есть у прежних RCL omitted-v1: current SQL/parent
coherence проверяется, но opaque full semantic SHA не связывает отдельно retained refs.
Совместимый reader сохраняет эти bytes/IDs и помечает их legacy-unbound; испорченных production
records этим не установлено. Инертная of_bound factory требует исходный complete payload;
v2 связывает весь retained payload/state с opaque semantic SHA. Полные v1 факты/JSON неизменны,
текущий runtime по-прежнему вызывает прежний of. Нет backfill или отдельной migration для RCL.

RCL proof: прежняя factory AST exact; corpus-derived/fixture population 439 rows dump byte-exact,
438 full JSON exact и один synthetic legacy omission явно unbound. V2 fixture: coordinated
parent/body tamper отклонён get/doctor/scratch restore без изменения target, повтор не растёт.
Тот же SQL length; gzip +47 B в одной fixture, без daily/global forecast. Старый2a reader
отклоняет v2 до изменения target: reader deployment обязателен до writer switch, rollback после
активации сохраняет v2-capable reader. Senate opaque proof выше не распространяется на RCL v2.

На disposable копии real state e15b53d0 все 38 старых tables/schema/rows точны; schema59→61
добавляет только три пустые metadata tables, +2584 SQL / +691 gzip B. FK/integrity/doctor
и dump/restore exact прошли, processing production не запускался. Native 168: только empty
schema61 tables/indexes/pragma и проверенные SQL-derived metrics отличаются; все прежние
rows/reports/messages/provider inputs и 24 существующих v1 RCL IDs byte-exact.

Final make check: 6571 tests / 0 failures-errors-skips, 256.36s, coverage 92%; mypy 570,
Ruff/format 636 по src/tests/tools, source/config freeze 638 exact. Добавлен 71 unit case,
новых scenario нет; существующий invalid-schema parameter автоматически сдвинулся с61 на62.
227 Senate focused и 61 RCL focused passed; независимые reviews/integrated delta checks
не оставили actionable findings. Full gate включает source merge с J150; отдельные cap/older-reader
proofs имеют свои точные source pins.
[Acceptance asset](../assets/audits/2026-10-29-source-history-readers/validation.json).
Новые writers включаются после реального CI/deploy этого reader. План: 74 / 31 done / 6 N/A / 37 open.


J151 commit `8e24c472c46ade0d6adafa0d9a8650b5536a3b2c`: все четыре CI jobs 40789404058
и deploy 40790038690 прошли; actual relay transition 2a6e → 8e24, health 45s/timer подтверждены
[release proof](../assets/audits/2026-10-29-source-history-readers/release.json).


<a id="j152"></a>

### J152. Bind newly captured RCL omissions after compatible reader deployment

Статус: **локальная приёмка завершена; CI/deploy ожидаются**, 29 октября.
Runtime producer переключён с `of` на `of_bound` после подтверждённого reader deployment 8e24.
Новые omitted facts связывают весь retained payload с opaque semantic SHA; полные v1 facts/JSON
и legacy factory прежние. Один public-reader unit case проверяет long descriptor, прежнее окно,
точных родителей, cache hit, сохранение первого времени и отсутствие роста при повторе +365 дней.
Нет backfill, schema/caps/retention/backend изменений; rollback сохраняет v2-capable reader.

Независимый review без findings. Component replay на frozen J150 cached/raw metadata:
437 + 437 complete facts сохранили точные v1 IDs/JSON, quiet SQL growth 0. По 437 synthetic
oversized-descriptor controls в каждой lane: payload/semantic SHA прежние, retained v2 binding
проверено, max timestamped record 1641 B < 2 KiB. 874 retained-output и 437 known physical-size
tampers отклонены. Это controls, не natural omission frequency. SQL length delta 0; gzip delta
15 664 / 15 891 B для двух независимых 437-control populations, не прогноз роста production.
Parser/extractor/loader повторно не запускались: меняется только factory dispatch. Native168
заново не прогонялся; прежний replay J151 не выдаётся за same-code proof нового writer.

Full make check: 6572 tests / 0 failures-errors-skips, 224.86s, coverage 92%; mypy 570,
Ruff/format 636 по src/tests/tools; все 638 frozen source/config files точны. Один новый unit
case, новых scenario нет. Source/input DB hashes независимой проверки неизменны.
[Acceptance asset](../assets/audits/2026-10-29-rcl-bound-omission/validation.json).
DA2.3/DA2.5 остаются partial до Senate capture; план 74 / 31 done / 6 N/A / 37 open.


J152 commit `bb31e9baad8c67c762d4fb3c69cb0fe0116adbe9`: все четыре CI jobs 40793196294
и deploy 40793806579 прошли; relay 8e24 → bb31, health 45s/timer подтверждены
[release proof](../assets/audits/2026-10-29-rcl-bound-omission/release.json).


<a id="j153"></a>

### J153. Read non-outage Senate aborts before producer activation

Статус: **локальная приёмка завершена; CI/deploy ожидаются; producer не активирован**, 29 октября.
При review следующего producer обнаружено: active pre-body KeyboardInterrupt описывался как
source_unavailable, а оставшиеся descriptors — after_outage. Отмена не доказывает outage.
Public facade/finalizer control с двумя descriptors подтвердил это до релиза; original exception
object/cause оставались прежними. Producer отдельно исправляется и получит следующий этап J154.

Совместимый reader добавляет `after_failure` только для unattempted filter facts; completed
inspection не может использовать skip reason. Три Literal/allow-forbid additions, без migration,
default/ID изменений или нового capture. После actual reader deploy producer сможет сохранять
точный non-outage abort; старый8e не заявляется reader для full facts с новым reason.

Два новых unit cases проверяют complete/omitted cancellation history, get/doctor/scratch restore,
первое время, quiet repeat +365 дней и отказ от inspected-after_failure. Независимый review без
findings; оба новых cases отдельно прошли у reviewer. На сохранённой counterfactual популяции
775 facts каждый record JSON и весь SQL остались byte-exact, doctor без findings. Свежая extraction
не выполнялась; отмены не выдаются за natural observed corpus demand.
[Acceptance asset](../assets/audits/2026-10-29-senate-abort-reader/validation.json).
План 74 / 31 done / 6 N/A / 37 open; Senate producer остаётся открытым.


J153 final make check: 6574 tests / 0 failures-errors-skips, 250.92s, coverage 92%; mypy 570,
Ruff/format 636 по src/tests/tools, 638 frozen source/config files exact. Два новых unit cases,
новых scenario нет. Strict docs/link/anchor/resource/build checks прошли.


J153 commit `de97a8002d7910da5a52c172b1897ae68a5964cc`: все четыре CI jobs 40794521795
и deploy 40795040317 прошли; relay bb31 → de97, health 45s/timer подтверждены
[release proof](../assets/audits/2026-10-29-senate-abort-reader/release.json).


<a id="j154"></a>

### J154. Capture actual Senate candidate inspection at caller boundaries

Статус: **локальная приёмка завершена; commit/CI/deploy ожидаются**, 29 октября.
Min-reader de97 уже реально развёрнут. Tracking, delayed document retry и amendments request
передают actual Bill owner. Resolver наблюдает существующие ветки один раз: ordered descriptors,
reached inspection/reasons/groups/outcome и known complete body/read/extraction refs. Scope до
resolver completion; последующее text/scan verification остаётся J105, legal admission не выводится.

SourceObservationIdentity отделяет pure ID от настоящего caller timestamp, сохраняя прежние
SourceObservation field order/JSON/IDs. Дубликаты selection объединяют совместимые actual parents;
каждый descriptor сохраняет собственный junction. Caller резервирует owner input order до fan_out,
явный close/join завершает workers до persistence. Завершённые unconsumed worker facts сохраняются;
новый hook не меняет прежний result/exception/cause. Fixed two-writer composition не вводит registry
или source branch в generic owners. После rollback отсутствующий owner проверяется SELECT1;
ложное enrollment и deserialization mutable Bill ради presence не добавляются.

Review до релиза исправило две ошибки: unsafe URL из hash-only envelope мог добавляться через
новый generic source/selection graph; отмена описывалась как source outage. Новый producer сохраняет
для unsafe URL exact body/URL-free extraction без новых read/source/selection refs, не придумывая
safe URL. Inherited loader-wide CLI/J105 receipt behavior прежний; его prospective privacy fix
остаётся отдельной DA2.5 задачей. `read_error`/`after_failure` отличаются от подтверждённого outage;
completed-body inspection_error сохраняет фактический physical graph. Reader de97 включён раньше.

190 focused, independent revised review и восемь независимых public cancellation/privacy controls
прошли. Ordinary 775-row SQL revision1→2 exact. Корпус: 759 cached contexts / 929 originals,
62 395 521 B, 3708 data pins; отдельно 16 true-raw contexts (8 DOCX/8 PDF) и 112 meaningful mutations.
775 records / 992 links / 18 known extraction junctions; cached max record 6879 B с timestamp,
0 omissions в этой популяции, synthetic full/omitted controls отдельно. Все 775 repeat +365 дней
и get/doctor/restore exact. 14 paired public cases сохраняют IO/errors, model/batch views,
messages/reports и прежние SQL rows; workers1/4 exact. Это counterfactual cohort, не natural demand.
Общий populated SQL 7 105 723 B / gzip 1 305 173 B; shared parents учитываются вне 8/2 KiB record caps.

Native168 against de97: 166 SQL artifacts byte-exact; только revision-1/revision-4 добавили
по 2 facts + 2 links, без parents. Все прежние rows/schema/reports/paid inputs/messages и
24 RCL v1 records exact. Per changed fixture +7609 SQL / +1473 gzip9-mtime0 B; direct повтор
не растёт. Нормализованы только проверенные SQL-derived metrics и два новых table digests.

Final make check на pinned export: 6605 tests / 0 failures-errors-skips, 262.96s, coverage 92%;
mypy 572, Ruff/format 638 по src/tests/tools, 640 source/config pins exact. Новые 11 unit cases
и 20 boundary scenarios; прежние cases не удалены. Local HTTP 25 passed / 3.63s.
Export сохраняет primary source guards параллельной physical M0; перед commit обязателен
copy-back только owned files и byte-exact check всего проверенного source/config closure.
[Acceptance asset](../assets/audits/2026-10-29-senate-inspection-capture/validation.json).
DA2.3 closure ожидает actual producer release; DA2.5 остаётся partial до shared metadata privacy fix.
План пока 74 / 31 done / 6 N/A / 37 open.


J154 copy-back выполнен после завершения physical M0 public phase: все 5365 paths проверены,
13 source pins сохранялись до конца. В primary перенесены только 19 owned patch paths;
все 640 source/config files совпали с прошедшим gate export побайтно. De97 reader/model и два
reader controls, J152 RCL dispatch сохранены. Полный gate повторно для тех же bytes не запускался.


J154 commit `c73d7fa799a098e32e91c1e07a5f825fbaedd12f`: все четыре CI jobs 40797178693
и deploy 40797789854 прошли; relay de97 → c73, health 45s/timer подтверждены
[release proof](../assets/audits/2026-10-29-senate-inspection-capture/release.json).
DA2.3 закрыт конечной проверкой metadata/source SQL structure, ownership/uniqueness/parent refs,
активных caller boundaries и quiet repeats — [acceptance](../assets/audits/2026-10-29-source-history-acceptance/validation.json).
DA2.5 privacy остаётся partial, natural source-family/purpose production-cycle acceptance в
DA10.6/10.7 этим не объявлена. План теперь **74 / 32 done / 6 explicit N/A / 36 applicable open**.


<a id="j155"></a>

### J155. Read-only observed and source latency report

Статус: **локальная приёмка завершена; CI/deploy ожидаются**, 29 октября. Base c73, DA10.4 partial.
Read-only work doctor с явным `--observed-at` добавляет versioned `latencies` рядом с J122
work ages; без clock отчёт byte-exact с c73. `tools/observed_latency.py` читает только
сохранённые часы: `bills.first_seen_at` или `detected_at` события → `ComputationResult.collected_at`,
projection `ResultApplication.created_at` и `publications.sent_at`. Wykaz `Data publikacji` →
обнаружение только для собственного plan row (минута, Europe/Warsaw); RCL/druk, унаследовавшие
план, не получают чужую дату. Read completion недоступно: recorded_at, run и finalization
clocks не подставляются. Runtime, schema, Settings, CLI и production не менялись; tool не пишет DB.

Неизвестное остаётся unknown с причиной, без clamp: missing/naive/invalid/future/reversed clocks,
stale/future generation, identity mismatch, cross-owner result, application before collection,
missing owner/event anchor, unconfirmed или unbound destination, manual confirmation (resolved_at
не время send), shared/reused message без explicit membership, legacy plan. Shared send засчитан
только по explicit sitting/update group или digest_event_links с тем же channel/message/sent_at
и проверенным frozen owner. Недоступные tables/columns отделены от known-empty. Rows смешивают
records и per-owner markers, поэтому не являются denominator успеха.

Root review revision 2 закрыл три пункта. Прежний J122 scenario test падал на новом opt-in поле:
focused selection агента его не включала, теперь тест требует оба раздела и отсутствие маркера.
Reanalysis и amendments мерились от первого обнаружения карточки (26 и 30 дней на реальном state):
trigger time не сохранён, поэтому они `trigger_event_unbound`; application rows получили purpose.
`digest_parts` — отдельные физические сообщения, не shared membership; ветка была недостижима
(frozen owner digest всегда invalid), вывод не изменился.

Свежий state `a5a3f68e` (schema 61, SQL 20 382 485 B) на disposable restore: 2091 rows.
Read 0/477 known; collected 4/477 и applied 4/477 (по 2 trigger_event_unbound); delivery 52/183
(41 от события, 11 новых карточек; 50 own + 2 update group); source 6/477, все Wykaz plans.
Default и прежние opt-in sections exact с c73 tool, DB SHA до/после неизменен. Большинство
unknown deliveries — legacy rows и edit revisions: правка карточки через message_part с тем же
message_id делает исходную карточку shared_or_reused. Это консервативная потеря измерения, не
ложное значение. Dump не растёт: нет migration и записи; отчёт 964 503 B JSON только offline.

71 focused (latency, J122 ages, work doctor scenarios). Full make check на итоговых bytes:
6633 tests / 0 failures-errors-skips, 254.65s; mypy 573, Ruff/format 640 по src/tests/tools.
Новые 28 unit cases и одно усиленное J122 scenario; coverage в этом прогоне не собирался.
[Acceptance asset](../assets/audits/2026-10-29-observed-latency/validation.json).
Actual loader-read completion producer и trigger time later purposes остаются DA10.4.
План **74 / 32 done / 6 explicit N/A / 36 applicable open**.


J155 commit `6aa24d387b3a325e1a828d49dea5fc151836f450`: все четыре CI jobs 40823453475
и deploy 40823808285 прошли; relay c73 → 6aa2, health 45s/timer подтверждены
[release proof](../assets/audits/2026-10-29-observed-latency/release.json).
Proof commit `7920290a`: CI 40824207289 и deploy 40824738573 прошли.


<a id="j156"></a>

### J156. Fix confirmed review findings in models and pure modules

Статус: **локальная приёмка завершена; CI/deploy ожидаются**, 29 октября. Base `7920290a`.
Облачное ревью части 2 diff `49ee2d0a..main` (models и чистые top-level modules, 54 файла)
дало шесть находок; все подтверждены по коду. Исправлены пять, общий safe-URL guard Senate/RCL
(nit 4) отложен: это рефакторинг моделей сохраняемых records. Статусы плана не меняются.

`RunReport.is_empty` считал `document_reads` и `document_download_diagnostics` результатом
execution run: с выкладки 28 октября любой pipeline run непустой, `/runs` и `lexinform runs`
перестали скрывать холостые прогоны. Оба поля теперь пропускаются, как `phase_seconds`; ветка
CLI reads прежняя. На 389 runs свежего state вердикт совпал с вердиктом без этих полей.

Тихая перепроверка неизменного текста (`_unchanged`) перестраивала analysis-stage coverage
из свежего view без map. При budget cut точные paid ranges заменялись на `(0,0)` и «input
coverage unknown», после cost fitting — на полный диапазон, будто модель видела весь текст.
`paid_input_selection` сохраняет paid ranges, `normative_text_omitted` и state, когда каждая
selected part имеет ту же role и непустой text SHA; иначе прежний fallback. Совет ревью передать
`loaded.projection` отклонён: он описывает budgeted, а не cost-fitted input. Backfill нет.

TextFormatter ставит correlation fields на строку сообщения, а не после traceback. Три ручных
списка download outcomes выводятся из `DownloadFailureOutcome`; список причин в
`download_failure` — намеренное подмножество и не менялся. Docstring `PrintInfo.main_pdf`
сокращён до одной строки, история уже в rules sejm-api.

Корпус: 969 PDF друков term 10, 685 с normative part (261 без неё, 23 без текста); реальные
extractor, trim и TextBudget, cost fitting имитирован excerpts 600/2400 как в replay_input_coverage.
Вынос хвоста `selection_for_input` exact с HEAD в 3808/3808 сравнений. Старый `_unchanged`:
без cut equal 685; budget 24000 unknown 475 / equal 210; budget 1000 unknown 685; после fitting
overstated 685 (без budget), 210 overstated + 475 unknown (24000), unknown 383 (1000). Новый
во всех строках равен paid по ranges/state/limitations, fallback 0. Prod state: 21 из 170
records имеют analysis ranges с hashed parts; 149 legacy идут прежним путём; записей, уже
испорченных «unknown», нет. Schema, flags, provider input и решения отбора не менялись.

Падали до исправления: 2 scenario coverage cases, 3 для пустых runs, 1 для traceback; ещё
5 unit cases на границы reuse и guard на outcomes, прежние списки были полны. Full make check:
6645 tests / 0 failures-errors-skips, 256.55s; mypy 573, Ruff/format 640 по src/tests/tools.
[Acceptance asset](../assets/audits/2026-10-29-review-part2-fixes/validation.json).
План **74 / 32 done / 6 explicit N/A / 36 applicable open**.


J156 commit `fa09b3f2780e5d606ab758578bc588f1a83464fc`: все четыре CI jobs 40830180647
и deploy 40830715885 прошли; relay 7920 → fa09, health 45s/timer подтверждены
[release proof](../assets/audits/2026-10-29-review-part2-fixes/release.json).


<a id="j157"></a>

### J157. Add inert metadata URL policy and bounded omission reader

Статус: **выпущено; CI и actual relay deploy подтверждены**, 30 октября. Base `e6e8d033`.
Reader-first этап DA2.5: чистая prospective policy допускает исходные bytes URL без переписывания;
query требует известного official host/path/key/value contract. Userinfo, control characters
(включая encoded U+2028/U+2029), fragment и непроверенные query contracts дают явную причину.
Исторические RCL/Senate validators, source IDs, IO и model inputs не изменены; runtime wiring нет.

Optional `metadata_url_omissions` отсутствует в прежнем JSON. Диагностика ограничена 2 KiB,
восемью reason counters и восемью детерминированными частичными hash-only примерами с точным
остатком observations. Невалидный UTF-8 не получает выдуманный SHA. Collector хранит ограниченное
число identities; overflow отклоняется до изменения counters. Ни новых таблиц, ни роста dump
при выключенном producer. Run retention прежний; restore сохраняет opaque report JSON,
а typed reader проверяет accounting/cap. Старый typed reader может отбросить новое поле.

Корпусная qualification сохранённого policy freeze: 841 metadata files, 68 202 unique URL values;
68 188 admitted, 14 вне проверенного query/identifier contract без вердикта о валидности upstream.
4907 official query URLs и 35 fixture URLs сохраняют исходные bytes. Старые 775 populated records
и absent-field report JSON byte-exact; новый diagnostic проходит get/restore/quiet repeat.
Stress envelope 1918 B. На review интеграции исправлен telemetry-only `is_empty`: diagnostic не
делает обычный холостой execution непустым, CLI reads сохраняет отдельную семантику.
94 focused tests и strict mypy/Ruff прошли у reviewer; root проверил policy/model/collector.
[Acceptance asset](../assets/audits/2026-10-30-metadata-url-reader/validation.json).
DA2.5 остаётся partial до отдельного producer; план 74 / 32 done / 6 N/A / 36 open.


J157 final make check: 6751 passed / 278.76s, coverage 92%; strict mypy 578 files,
Ruff/format 645 по src/tests/tools. Все 645 pinned Python/config files неизменны после gate.
Strict docs/link/anchor/resource checks прошли. Никакие runtime capture paths не включены.


<a id="j158"></a>

### J158. Capture admitted loader receipts without losing complete body metadata

Статус: **выпущено; CI и actual relay deploy подтверждены**, 30 октября. Base `3a05deb4`.
Узкий producer использует J157 reader: TextLoader сохраняет known complete body до проверки
URL и создания read identity. Unsafe новый receipt опускается; exact body и URL-free extraction
сохраняются независимо. Одна omission observation соответствует реальному complete response,
а не distinct URL/file; cache hit не добавляет observation. Failed/incomplete response не
получает выдуманный body SHA. Parser exception остаётся исходным объектом.

Bounded diagnostic сохраняется существующими Pipeline.finally и CLI reporter через download
monitor; workers меняют только память, caller сохраняет report/body. Новый Container, protocol
или migration не нужны. 89 focused tests прошли: 8 новых unit cases и 2 worker cases;
strict mypy/Ruff зелёные. Root проверил четыре source paths и file-origin lookup.
Source/selection graphs ещё могут содержать unsafe URLs: DA2.5 остаётся partial. Historical
rows/validators, selection и provider payload не переписываются. Для некодируемого UTF-8 URL
устранена incidental ошибка metadata read_id.encode; transport/parser errors остаются прежними.
Policy bytes точны J157, его 841-file qualification переиспользована; extraction/normalization
не менялись, новая extraction корпуса не заявляется.
[Acceptance asset](../assets/audits/2026-10-30-loader-url-receipts/validation.json).
План 74 / 32 done / 6 N/A / 36 open.


J157 commit `3a05deb4e6a2b524c9dc35a74ed6ea69231ce1e2`: все четыре CI jobs 41026256239
и deploy 41026665779 прошли; actual relay e6e8 → 3a05, health 45s/timer подтверждены
[release proof](../assets/audits/2026-10-30-metadata-url-reader/release.json). Reader развёрнут до J158 producer.


Первый J158 full gate нашёл один несовместимый Senate fixture helper: он индексировал
`source.reads[0]` даже для deliberately unsafe token URL. Helper теперь допускает фактическое
отсутствие receipt; прежние leak assertions сохранены и усилены проверками no receipt/known body.
70 focused Senate/loader cases прошли; полный gate повторяется на итоговых test bytes.


J158 final make check: 6761 passed / 236.08s, coverage 92%; strict mypy 579 files,
Ruff/format 646 по src/tests/tools. Все 646 source/config pins exact. Independent review
body/error/cache/lock/caller boundaries без findings. Strict docs checks прошли.


<a id="j159"></a>

### J159. Add inert bounded physical inventory reader

Статус: **выпущено; CI и actual relay deploy подтверждены**, 30 октября. Base `93ccd839`.
Append-only schema62 добавляет physical metadata/captures и структурные views. Closed typed records:
headers, graph, container execution, root order и shared recipe с code/dependency/options refs.
Root всегда actual artifact; extraction association nullable, без фиктивного результата.
Repository/get/doctor/shared scratch restore проверяют IDs, ownership, parent refs, cycles,
первое recording time и caps; walker/selection policy не перезапускаются. Runtime producer нет.

Лимиты целого UTF-8 envelope: metadata32 KiB/capture1 KiB/root distinct union512 KiB с recipe.
Partial records связывают retained fields и omission counters; неизвестный full SHA остаётся unknown.
100 focused tests прошли. Independent review выявило TEXT artifact size вместо числа: get/doctor/
restore теперь возвращают typed diagnostic, target restore сохранён. EXPLAIN и замеры выявили
отсутствующий self-FK child index: добавлены header и четыре capture FK indexes. Запись54 198
records теперь16.59s; unindexed попытка прервана спустя более6min, успешного baseline benchmark нет.

Captured M0 без raw reads/extraction:54 197 прежних payload IDs exact +1 actual QA observer recipe,
4017 captures с явным counterfactual extraction=None. Get/repeat+365d/restore twice exact.
Max envelopes H26563/G11426/unit14096/order23233/recipe3404/capture510 B; root union452164 B.
Whole scratch SQL95 443 611 B/gzip21 578 326 B включает parents, это не production forecast.
Final parent guard отдельно проверен doctor на всей популяции, DB SHA unchanged.
Exact predecessor3a05 store против62 на pinned schema59: все41 прежних таблиц/3003rows/87 schema
objects exact, только две пустые feature tables и indexes/views/version; delta+7019 SQL/+1479 gzip B.
Store между3a05 и93ccd839 не менялся. Doctor, migration repeat и двойной restore exact.
[Acceptance assets](../assets/audits/2026-10-30-inert-metadata-foundations/validation.json).
DA3 physical producer остаётся открытым; raw backend не выбран.

<a id="j160"></a>

### J160. Add inert pure source graph admission

Статус: **выпущено; CI и actual relay deploy подтверждены**, 30 октября.
Pure helper проверяет original requested/resolved TextDocument URL fields и alternatives,
selected/observed part URLs и evidence source URLs до graph IDs. Empty physical URL остаётся
unknown; empty document URL rejected. Explicit opaque_failure_prose flag исключает graph,
не сканируя и не сохраняя prose. Result содержит bounded hash-only omissions J157, counts —
field occurrences. Нет model/schema/ID изменений, registry или runtime callers.
19 новых unit cases;113 focused вместе с policy/collector прошли. Retained-field cohort:
974 observations/929 selections, все известные URL admitted, SQL unchanged. Original requested
universe и opaque-prose history недоступны: их не реконструировали. Root и independent reviewer
проверили integration без findings. J159/J160 объединены только для одного exact полного gate;
ни physical producer, ни graph gating этим выпуском не включаются. DA2.5 остаётся partial.
План74 /32done /6N/A /36open.


J158 commit `93ccd839a61ceb4319ed3298e4c1e384ce7ee732`: все четыре CI jobs 41027748582
и deploy 41028185083 прошли; relay3a05 →93cc, health45s/timer подтверждены
[release proof](../assets/audits/2026-10-30-loader-url-receipts/release.json).

Первый совместный J159/J160 full gate:6800 passed и один старый assert ровноschema61.
Он теперь допускает append-only successors; отдельный physical test сохраняет hash первых61
миграций. Все прежние Senate SQL caps assertions сохранены.102 focused прошли; full повторяется.


J159/J160 final make check: 6801 passed / 236.70s, coverage92%; strict mypy584 files,
Ruff/format651 по src/tests/tools; все 651 source/config pins exact.40 новых unit cases,
новых scenarios нет. Strict docs checks прошли. Producer обоих компонентов остаётся выключенным.


J159/J160 commit `0472ccf9506a93d078f189576a8a4d6470d2577f`: все четыре CI jobs 41028972342
и deploy 41029645296 прошли; relay93cc →0472, health45s/timer подтверждены
[release proof](../assets/audits/2026-10-30-inert-metadata-foundations/release.json). Physical producer
и generic graph gating не активированы этим выпуском.


<a id="j161"></a>

### J161. Guard generic source graphs and retain physical results independently

Статус: **выпущено; CI и actual relay deploy подтверждены**, 30 октября.
Base `0472ccf9`. Original requested и resolved documents проверяются до trace и selection/source IDs.
Metadata-only opaque flag выставляется для произвольного exception message в composition; исходные
strings/errors и business selection не меняются. Admitted graph сохраняет прежние identities;
отклонённый возвращает typed PhysicalSourceRead без graph/edition/trace/junction refs. Prefilter
явно сохраняет прежний original catalog context, не заменяя его resolved document в safe graph.

117 focused прошли, включая6 новых capture units и6 public scenarios. Пинованный cached term10:
938 обычных +24 optional-failure +24 fallback-failure =986 business outcomes byte/hash exact,
все938 ordinary и24 class-only support graph payloads/IDs exact;24 arbitrary-prose fallback graphs
omitted.940 data pins,
source/options pins unchanged.42 prints вне snapshot исключены. Это cached text, без новой raw
extraction или выдуманных body origins.8 paired World cases: provider/batch/messages/calls exact,
workers1/4 business/diagnostics exact; safe SQL exact, остальные различия только graph/ref tables.
Get/doctor/restore/+365repeat сохраняют actual physical recovery без graph refs.

Cross-review обнаружило существовавшую потерю extraction history у blocked composition без sha/
parts. Existing artifact observer теперь отдаёт known URL-free results, отфильтрованные по known
body IDs; pipeline/CLI callers сохраняют их после parent artifacts в той же транзакции. Нет URL
maps, per-owner inference, нового extraction или raw bytes.34 focused прошли, включая3 CLI units
и2 pipeline workers cases; rollback failed write сохраняет старый state. Actual DOCX+required
emptyZIP control:2 bodies/2 results/no graph. Прежде World сохранял1 result, теперь2; delta+2882 SQL/
+295 gzip B. Все остальные tables/report/results/IDs exact после удаления phase_seconds;
get/restore/+365repeat exact. Known-cache inventory не выдаётся за новое activity/read timing.
Root и independent cross-review findings закрыты. Specialized RCL/Senate capture, paid/business
payload URLs и arbitrary prose/path privacy не переопределяются; DA2.5 остаётся partial.
[Acceptance assets](../assets/audits/2026-10-30-physical-evidence-preservation/validation.json).

<a id="j162"></a>

### J162. Add inert bounded header capture foundation

Статус: **выпущено; CI и actual relay deploy подтверждены**, 30 октября.
Два новых файла, без изменений extractor/component sources или runtime hooks. Online builder
сохраняет bounded prefix и streaming canonical full hash только при завершённом наблюдении;
прерывание/append guard failure оставляет unknown full hash и явную incomplete metadata.
Per-call ContextVar scope снимается при BaseException, finalizer не подменяет исходную ошибку.
Independent review воспроизвело revival через copied context на том же thread после exit:
active owner теперь сбрасывается до reset/finalization; normal/failure copied-context controls
проходят.18 focused, strict mypy/Ruff зелёные. Saved M0:13108 headers/251742 rows, все payloads/IDs
exact,0 omissions,max26563 B,9.01s,input unchanged. Tiny lifetime delta не меняет builder/streaming
AST, поэтому этот proof переиспользован. Никакого raw read/walker/SQL/caller activation нет.

<a id="j163"></a>

### J163. Preserve first time when repeating exact-cap physical metadata

Статус: **выпущено; CI и actual relay deploy подтверждены**, 30 октября.
Root воспроизвёл edge: valid32768B envelope с whole-second first time сохранялся, но repeat с
microsecond time падал до lookup — новый timestamp удлинял JSON. Writer теперь берёт validated
persisted first time через общий structural reader до incoming envelope validation. Immutable
comparison/corruption checks и caller-clock validation остаются; cap/model/schema не меняются.
Старые legitimate envelopes не отвергаются новой более строгой исторической policy.
Red-before-fix control теперь подтверждает exact dump/get/first time при repeat;101 focused и
независимые17 focused прошли. План74 /32done /6N/A /36open.


Первый J161–J163 full gate выявил10 metadata-history ожиданий: первоначальный opaque flag был
слишком широким. Исправлено: URL+fixed/class-only reason сохраняет прежнюю историю, только
произвольный str(exc) создаёт opaque flag. Trace хранит requested URLs/закрытые codes/hash,
без selection rejection prose: его preflight проверяет URLs независимо от этого flag.
Нормальные overflow/rollback/restore assertions сохранены.5000-byte URL теперь явно проверяет
oversized_url diagnostic/no trace/physical retention. Structured-unreadable fixture сохраняет
ВСЕ прежние body/source/no-selection-artifact assertions; настоящий ValueError privacy case
по-прежнему требует omitted graph/body retention.161 focused, включая все4 упавших модуля,
прошли;986 cached business snapshots exact,962 допустимых graphs exact/24 prose omissions.
Independent cross-review закрыт без findings. Полный gate повторяется на revised frozen bytes.


J161–J163 final make check: 6838 passed / 237.66s, coverage92%; strict mypy590 files,
Ruff/format657 по src/tests/tools. Все 657 frozen source/config files exact. Cross-reviews
закрыты; strict docs/link/anchor/resource checks прошли. Specialized URL capture ещё не включён;
header foundation инертна, raw/backend/retention не менялись.


J161–J163 commit `b5619040ba31f565742e921f4b4ae16e7fb59dab`: все четыре CI jobs 41034441032
и deploy 41035137341 прошли; actual relay0472 →b561, health45s/timer подтверждены
[release proof](../assets/audits/2026-10-30-physical-evidence-preservation/release.json).


<a id="j164"></a>

### J164. Apply prospective URL policy at specialized capture boundaries

Статус: **выпущено; CI и actual relay deploy подтверждены**, 30 октября. Base `b5619040`.
RCL letter/before/result/after metadata и Senate descriptor universe проходят preflight до
новых specialized IDs. Encodable shared-policy refusal использует существующий omitted_unsafe
с exact original SHA, пересекая прежний stricter domain admission. Invalid UTF-8 не получает
выдуманный URL SHA: specialized observation пропускается, bounded diagnostic сохраняет причину.
J105 resolution_source_read использует generic admission без новой domain-only host restriction;
configured/fake origins сохраняют прежний contract. Bodies/URL-free extractions независимы.
Historical .safe/.of validators и immutable rows не меняются. Reader fixture строит legacy
DocumentSourceRead напрямую, сохраняя все прежние assertions и actual optional-read guard.

227 focused, включая4 прежних regression modules;13 новых unit cases/0 новых scenarios.
Retained874 RCL +775 Senate facts/4595 URL fields: payloads/IDs/SQL/+365repeat exact. Это replay
сохранённых metadata, без повторного body/parser/resolver.11 paired public cases: consultation
business hashes/windows, provider/batch/messages/IO exact, workers1/4 diagnostics exact. Fresh
ordinary IDs меняются только из-за настоящих components.merge_sha256/resolver_sha256;
оба SHA сверены с actual source bytes. Specialized prototype не закрывает owner-evidence gap.
[Acceptance assets](../assets/audits/2026-10-30-specialized-omission-readers/validation.json).

<a id="j166"></a>

### J166. Add inert owner-bound source omission reader

Статус: **выпущено; CI и actual relay deploy подтверждены**, 30 октября. J165 assembler ещё в работе.
DA2.5 audit выявил: PhysicalSourceRead и partial run examples не сохраняют адресуемую owner/source
связь при omitted graph. Append-only63 добавляет source_metadata_omissions/source_omission_parents.
Closed owners: actual Bill+purpose, official RCL project/nullable stage/letter, Senate actual Bill+
resolution. Real Bill FK, owner columns/indexes, known_available parent scope без fake source refs.
Exact observed source view hash явно canonical-json-ascii-v1: поддерживает escaped Python surrogates,
не выдаётся за raw URL UTF-8/body/wire SHA. Никаких raw URL/prose/credentials в новой записи.
Envelope<=8KiB/32 retained parents; exact actual full parent count/manifest, partial scope явный.
First aware UTC time исключён из identity; exact-cap repeat не меняет первый timestamp/dump.
Typed save/get/doctor/shared scratch restore проверяют refs, indexed/JSON identity и caps.

Cross-review воспроизвело ложную source/body связь через повреждённый SQL bridge. Reader теперь
сверяет actual part_scope/index/sourceSHA и typed extractionID/indexed body/selection membership.
Senate known source требует sameBill+analysis+senate_amendments, без URL/номер inference.
Пустой supplied parent manifest SHA не заменяется retained hash; он отвергается.24 новых unit
cases/114 focused, strict owned checks и9 independent repair controls прошли. Original freezes
сохранены. Literal migration/writer не менялись при repair: schema62 predecessor proof переиспользован;
43 old tables/3003rows/108schemaobjects exact, input unchanged, repeat/restore twice exact, doctorrecorded.
Две новые пустые таблицы: delta+1881SQL/+416gzip9-mtime0 B, без backfill/producer/caller activation.
Этот reader должен реально развернуться до отдельного producer. DA2.5 остаётся partial;
план74 /32done /6N/A /36open.


J164/J166 final make check: 6875 passed / 243.97s, coverage92%; strict mypy594 files,
Ruff/format661 по src/tests/tools. Все 661 source/config pins exact.37 новых unit cases,
новых scenarios нет; strict docs/link/anchor/resource checks прошли. Reader63 producer выключен;
J165 assembler не входит в этот выпуск.


J164/J166 commit `4ec69a8142a6ada818d2ec454784963460ce9b9c`: все четыре CI jobs 41040720502
и deploy 41041424325 прошли; actual relayb561 →4ec6, health45s/timer подтверждены
[release proof](../assets/audits/2026-10-30-specialized-omission-readers/release.json).
Reader63 producer по-прежнему выключен: аудит private explain выявил дополнительный реальный
source-reference owner без Bill enrollment, требующий совместимого reader64 перед полным producer.


<a id="j167"></a>

### J167 — inert reader64 для source reference без Bill enrollment

Private `explain` для Sejm/RPW/RCL получает реальный source reference без регистрации Bill;
это существующий контракт, проверяемый `test_explain`. Reader63 с Bill FK не покрывал этот путь.
Закрытый owner `source_reference` хранит actual term/number/purpose и canonical context key;
отдельные индексируемые SQL columns не создают фиктивный Bill. Старые Bill/Senate FK сохранены,
known source/body relation по-прежнему проверяет actual context, purpose и typed selection bridge.
Append-only64 расширяет две feature tables; literal migrations1–63 неизменны.

На pinned legacy state43 tables/3003rows сохранены; на actual63 predecessor с тремя
counterfactual facts record JSON, IDs, first times, indexed columns и parent links exact.
Get/doctor actual63, upgrade/restore twice и quiet repeat прошли; delta+317SQL/+58gzip9-mtime0 B.
Cross-review нашло dump с declared64 и actual63 layout: теперь get/doctor/restore отвергают
несогласованную схему до замены target; legitimate63 upgrade остаётся рабочим. Original146 focused
и population proof сохранены; revision2 отдельно62 focused и independent repair controls прошли.

Final `make check`:6887 passed/253.089s, coverage92%, mypy594, Ruff/format661;
12 новых unit cases, новых scenarios нет. [Evidence](../assets/audits/2026-10-30-source-reference-reader/validation.json)
и [migration proof](../assets/audits/2026-10-30-source-reference-reader/migration.json).
Producer выключен; actual reader64 deployment обязателен перед его активацией. DA2.5 partial,
план74 /32done /6N/A /36open.


J167 commit `1c145aaf50858de69c0c477068dbcbddc4a45355`: все четыре CI jobs41045309099
прошли; actual relay4ec69a81 →1c145aaf, health45s/timer подтверждены
[release proof](../assets/audits/2026-10-30-source-reference-reader/release.json).
Совместимый reader64 развёрнут; отдельный owner omission producer ещё не выпущен.


<a id="j165"></a>

### J165. Assemble bounded physical roots from actual supplied observations

Pure assembler разделён на inputs, streaming encoding, independent integrity proofs и сборку.
Actual supplied headers/requests/opens/parts/context order дают child-first graph/unit dependencies
и отдельный actual global root order; recipe берётся из actual supplied observation. Ни runtime hooks,
ни SQL/Clock в этом API нет. Контекстам не приписывается порядок по обходу готового графа.

Overflow сохраняет честный минимальный rooted prefix с actual counts; known child suffix
опускается целиком, без fake null child или выдуманного complete hash. До range/model/JSON
expansion проверяются budgets; строки кодируются chunks1024. Independent1MiB logical proof
проверяет distinct addresses, SHA/size, parent binding и global order после metadata eviction.
Это не RSS guarantee. Если integrity proof не помещается, явный capacity gap не утверждает
непроверенные distinct counts. Known complete member size проверяется по retained header даже
без optional readlog; returned_length_differs и omitted unknown size остаются честными.

65 focused/25 assembler cases и strict5file checks прошли; independent11 controls закрыли три
исходных замечания и public typed get/save/doctor/restore-repeat для partial known child/hugeprefix.
2**63−1 universe и2MB name не разворачиваются в память; tracemalloc controls peak<4MB.
Final saved M0:4017roots/20614contexts,54197 payload/IDs и прежние root IDs exact;
0 unknown known-body sizes/0gaps,49.13s, max root union с recipe/capture454215B.
Это replay сохранённых typed observations с их original observer recipe, не свежий raw run
и не current runtime producer recipe. [Evidence](../assets/audits/2026-10-30-physical-assembler/handoff.json).

Дополнительный corruption control выявил pre-existing reader gap: изменение child artifact
byte_size при неизменных complete request/header проходило get/doctor/scratch restore.
Начальный общий gate остановлен до source edits и сохранён как pre-reader-corroboration-qa;
этот запуск не считается зелёным. Narrow reader repair и final gate фиксируются ниже.
Runtime producer ещё не активирован; StageA walk capture и последующая caller wiring отдельны.


J165 reader repair сверяет known catalog byte_size с retained member/header, complete request
и каждым retained nested header span. Отсутствующий historical child catalog остаётся unknown;
никакого backfill/new FK/size inference. `returned_length_differs` не выдаётся за complete member.
3 corruption modes теперь отвергаются get/doctor/scratch restore без замены target; unknown
catalog, omitted declared size и truthful length mismatch проходят.52 focused, strict2file checks
и root patch review прошли; пять assembler source pins неизменны.
[Repair evidence](../assets/audits/2026-10-30-physical-assembler/reader-repair.json).


J165 final `make check`:6917 passed/252.661s, coverage92%; mypy599, Ruff/format666,
все666 source/config pins exact.30 новых unit cases, новых scenarios нет; strict docs checks
прошли. [Final validation](../assets/audits/2026-10-30-physical-assembler/validation.json).
Schema/backend/raw retention не меняются; runtime graph producer ещё открыт.
План74 /32done /6N/A /36open.


J165 commit `e63e82b42696733f6146c296cd9887ba21afafa5`: все четыре CI jobs
и actual relay1c145aaf →e63e82b4, health45s/timer подтверждены
[release proof](../assets/audits/2026-10-30-physical-assembler/release.json).
Root assembly/reader repair развёрнуты; runtime walk/caller producer ещё не активирован.


<a id="j168"></a>

### J168. Record owner-bound omissions through actual invocation scopes

Reader64 реально развёрнут до нового producer. Generic/prefilter/primary/review, private explain,
RCL window/descriptor, Senate inspection/J105 используют actual invocation-scoped transient origins.
Body/read/extraction захватываются до parser failure; shared inventory/URL lookup не выводит
per-owner association. Actual Bill/Senate FK выбирается только при реальном enrollment, иначе
actual source_reference; RCL сохраняет official project и nullable actual stage/letter.
Caller сохраняет actual parents и omission atomically с Clock; workers не пишут SQL.

Exact observed source view получает canonical-json-ascii-v1 SHA, без raw URL/prose/credentials.
Invalid UTF-8 не получает requested-URL SHA. Stable per-field reason counts не считаются по числу
download/cache hit. Полный known_available parent manifest hash/count вычисляется один раз;
retained prefix<=32links/8KiB, source relation остаётся unknown без валидируемого реального ref.
First time/quiet repeats сохраняются. Это available origins, не утверждение полного used-body set.

Все восемь actual fan_out callers резервируют phase/input slots до dispatch и явно join перед
finalization; actual child order не заменяется owner/hash sort. Later read первого owner не
перегруппировывается назад. RCL/Senate workers1/4: actual order[1,1,2,2,3,3,1]. Metadata setup,
update и teardown защищены; failure discard/mask предотвращает false frame и отравление outer
context. Original business success/exception object/cause и единственный вызов сохраняются.
Cross-review закрыл artifact/observation callbacks, provider-first-call, _order/setup, nested mask,
input-exit и tracking None-slot failures;28 independent unit controls прошли на exact17file freeze.

257 focused, strict17file checks прошли.21 paired public cases сохранили business/IO/paid/messages;
986 cached rows/940 input pins exact. Retained874RCL+775Senate/4595URL occurrences сохранили
old payload/IDs/SQL и+365repeat; fresh specialized audit delta только actual merge/resolver hashes.
Private unsafe Sejm/RCL покрыты двумя новыми scenarios, actual private RPW `/explain` — отдельным
public out-probe: source_reference без Bill enrollment/source graph, known parent, business hashes exact.
Actual new fact get/doctor/restore/quiet repeat прошли; isolated one-fact/one-parent delta
+1721SQL/+380gzip B. [Evidence](../assets/audits/2026-10-30-owner-omission-producer/handoff.json).
Полный root gate и окончательный completion audit DA2.5 фиксируются отдельно.

<a id="j170"></a>

### J170. Bound physical activation with a current-state population measurement

Read-only state `412e9635b84c196732c9bec2b38a878fee120ee6`, SQL SHA
`a39d69e7306d97c7307e7b9a263a71a7f5c646be590815d68860ada2064eaae2`,
изолированно восстановлен/migrated64:20 886 979B SQL/4 191 102B gzip9-mtime0.
39 artifact rows/34 distinct extraction bodies, physical tables пусты.7 actual body SHAs совпали
с saved M0 roots; это coverage7/34, не доказательство исторического physical execution.

Counterfactual subset:115 metadata records/7captures/112 known body parents, original saved QA
observer recipe и extraction=None. Недостающие catalog sizes взяты только из actual complete helper
requests; неизвестные sizes не придуманы. Delta+231 863B SQL/+39 994B gzip; quiet repeat zero,
get/restore twice exact.42 other tables и39 preexisting artifact rows exact, меняются только
artifacts/physical_metadata/physical_captures. [Profile](../assets/audits/2026-10-30-physical-population-profile/subset-profile.json).
Новых raw reads/paid calls/production processing нет. Это не production forecast/activation acceptance:
actual runtime recipe, остальные27 bodies и будущая частота ещё не измерены.
Перед StageB активацией нужен final actual-consumer SQL/gzip profile; storage/backend не выбирается.


J168 final `make check`:6947 passed/272.471s, coverage92%, mypy603, Ruff/format670;
все670 source/config pins exact.28 новых unit cases/2 actual caller scenarios; strict docs checks
прошли. [Final validation](../assets/audits/2026-10-30-owner-omission-producer/validation.json).
DA2.5 completion audit ещё отдельно; physical runtime writer пока не активирован.


J168 commit `6582bc9c70b25fb502905a1a360a73306a5aa8a9`: все четыре CI jobs и actual
relaye63e82b4 →6582bc9c, health45s/timer подтверждены
[release proof](../assets/audits/2026-10-30-owner-omission-producer/release.json).
Независимый [DA2.5 audit](../assets/audits/2026-10-30-owner-omission-producer/completion-audit.json)
сохраняет partial: safe single-source parser/download failure до actual selection не сохраняет
owned official descriptor. Это отдельная positive attempt snapshot задача, не unsafe URL cause;
существующий SourceObservation требует настоящий selection. План74 /32done /6N/A /36open.


<a id="j169"></a>

### J169. Observe actual physical walks without changing business extraction

StageA добавляет cooperative scope и bounded pure collector к actual shared walk. Owned ZIP
factory использует прежний parser один раз; actual parsed directory span/hash/comment и row
universe фиксируются до row/map budget. Header data не реконструируются из final text; actual
member/XML/getinfo/open/part append/context events сохраняют duplicate occurrence distinction,
pre-order context slots и отдельный global event order, child-first graph dependencies.
Deflate64 не получает fake ZipFile.open; failed constructor/opaque helpers остаются unavailable.

Fixed1MiB logical transient budget не объявляется RSS guarantee. Partial-input bridge принимает
explicit measured totals только при actual completed walk/counts_complete и capacity-only loss;
existing typed minimum root/header/recipe/order сохраняет omitted counts с unknown full hashes.
Complete-input integrity не ослаблена, child null placeholders не создаются. Unknown counts/legacy,
guard failure и opaque helpers остаются explicit draft/gap. Default capture off; loader/Container/SQL
producer пока не подключены. Schema/raw retention/backend не меняются.

Review закрыл setup/opaque no-throw, nested failed-entry mask (child не портит parent route),
actual walk completion независимо от later selection error/outer catch и minimum header preflight.
Fault-only final repair удаляет pending outcome из actual failed requests: CaptureError placeholder
не становится фактом; request/open/status/global prefixes обрезаются, unknown counts не выдаются
за exact.28 новых unit controls,163 focused и strict5file checks прошли; independent final7controls
закрыты. [Handoff](../assets/audits/2026-10-30-physical-walk-capture/final-handoff.json).

Полный immutable normal cohort относится к revision4-prefix ДО isolated failure/draft repair:
5365 paths verified/4017 unique eligible roots/4 unique >32MB exclusions;4017 packets,
0 partial/gaps/business/physical-ID deltas,1544.68s. После repair adapter bytes и normal event
algorithm не менялись. Current final source отдельно проверен на9 real files/400412B: actual
business extraction IDs/inventory/execution IDs exact; actual new recipe/capture hashes честно
отличаются. Это явно reused normal proof + final-source bounded controls, не full4017 на новых
байтах. [Final controls](../assets/audits/2026-10-30-physical-walk-capture/final-controls.json).

Pre-repair whole-cohort footprint:54198 unique metadata records/76 129 495 envelope B и4017
captures/2 233 452B, без timestamps;48 413 known complete artifact identities. Это не SQL/gzip
и не production forecast. Growth gate J170 обязателен перед StageB activation. Все ignored proof
files/source snapshots сохранены также в primary out/corpus/j169-physical-walk-capture/evidence.


J169 final `make check`:6975 passed/241.580s, coverage92%; mypy605,
Ruff/format672, все672 source/config pins exact.28 новых unit cases, новых scenarios нет.
Strict docs/link/resource checks прошли; [validation](../assets/audits/2026-10-30-physical-walk-capture/validation.json).
StageB остаётся отдельной работой; план74 /32done /6N/A /36open.


<a id="j171"></a>

### J171. Read safe source catalog attempts without inventing a selection

Для реального DA2.5 gap добавлен inert reader65: SourceCatalogSnapshot хранит admitted safe
TextDocument descriptor как typed ordered prefix/full-view canonical ASCII SHA; catalog dedup
не зависит от retry/owner. Identity связывает retention/schema и explicit URL policyv1 с retained
fields/counts, marker не заменяет future compatible versioned reader. Unsafe/opaque metadata
не выдаётся за safe positive snapshot. Envelope8KiB с reserve timestamp; тела/exception prose/
credentials не копируются. Unknown/oversized fields отмечены, не достраиваются.

SourceCatalogAttempt: actual Bill/purpose или source_reference, read_failed/errorclass и явно
unknown selection. Никакого fake empty selection/SourceObservation или RCL-letter owner.
Actual known_available parent manifest ограничен32links/8KiB с full count/hash и unknown source
relations, first time не входит в identity. Три indexed tables связывают snapshot/actualowner/
actualparents; прежний SourceObservation и mandatory selection FK не меняются.

115 focused/26 новых unit cases, strict7file checks прошли. Independent13 public/policy/parent/
corruption/deep-prefix controls закрыты на exact8path revision2. Original markerless revision1
сохранена; marker, unsupported/tampered-policy control и fullview hash once — проверенные repairs.
Actual schema64 populated predecessor и fresh412e:45 old tables/3194rows/IDs/JSON exact,
literal migrations1–64 exact. Get/doctor/upgrade/restore twice/quiet+365days проходят;
empty65DDL+2364SQL/+272gzip B,2 controlled attempts/1 shared catalog+3078SQL/+738gzip B.
[Evidence](../assets/audits/2026-10-30-source-catalog-reader/handoff.json).
Reader-only: caller/Container не активированы. Actual reader65 deployment обязателен перед
следующим producer; DA2.5 остаётся partial. Raw/backend/retention не выбираются.


J169 commit `399a5defa1adbabc3fae8c7c12fb5e3278bb811e`: все четыре CI jobs и actual
relay6582bc9c →399a5def, health45s/timer подтверждены
[release proof](../assets/audits/2026-10-30-physical-walk-capture/release.json).
Default capture off; StageB и actual-consumer growth gate по-прежнему открыты.


J171 final `make check`:7001 passed/245.226s, coverage92%; mypy608,
Ruff/format675, все675 source/config pins exact.26 новых unit cases, новых scenarios нет;
strict docs checks прошли. [Validation](../assets/audits/2026-10-30-source-catalog-reader/validation.json).
Producer не активирован; план74 /32done /6N/A /36open.


J171 commit `6d6905cc5d5134d0edc9db6c605ba52e253f4ea4`: все четыре CI jobs и actual
relay399a5def →6d6905cc, health45s/timer подтверждены
[release proof](../assets/audits/2026-10-30-source-catalog-reader/release.json).
Reader65 развёрнут перед positive safe read-failure producer; его callsite qualification ещё открыта.

J170 дополнен counterfactual subset с actual accepted StageA runtime recipe вместо QA recipe:
7 roots/115metadata/112parents на том же412e state, schema65 перед/после. Delta231272SQL/+39502gzip,
zero+365growth/get/restore twice. [Runtime recipe subset](../assets/audits/2026-10-30-physical-population-profile/runtime-subset-profile.json).
Это по-прежнему7/34 current body coverage и NULL extraction association, не historical provenance,
полный runtime-consumer forecast или закрытие activation gate. Новых raw reads нет.


<a id="j175"></a>

### J175. Complete the ExtractionResult storage acceptance audit

Независимый item-by-item [audit](../assets/audits/2026-10-30-extraction-result-completion/audit.json)
и root code check закрывают DA3.4 в ранее выбранном bounded metadata-only профиле. J132
field/availability/immutable summary contract вместе с J147 historical rereads, J154 specialized
refs и J161 global known-result finalizers закрывает прежние actual caller gaps. Pipeline/CLI
caller transactions сохраняют actual complete-body parents и все known results даже при privacy
rejection/zero selection; новая версия добавляет запись, first time/repeat стабильны. Это reuse
уже квалифицированных доказательств и current-code audit, не новый raw/natural/paid прогон.
DOC/XML explicit unknown, physical execution, fact-to-block origins и raw storage не закрываются
этим пунктом. План74 /33done /6N/A /35open; новая runtime behavior не добавлена.


<a id="j173"></a>

### J173. Preserve actual safe source catalogs when reading fails before selection

Reader65 реально развёрнут до producer. Safe generic scope замораживает actual typed catalog
до IO; actual read failure hook сохраняет первый error class до business wrappers/caught
missing-file conversion. Caller связывает Bill только при enrollment, иначе source_reference.
Unknown selection не подменяется empty/blocked selection; actual capture_source_read подавляет
новую попытку. Native Sejm/RCL compare-version fallback по-прежнему сохраняет настоящий
blocked result/J168 evidence, а не fake pre-return attempt.

Positive catalog и privacy bundles объединяются по actual input slots в общей caller transaction:
actual parents/reads/extractions → snapshot → attempt. Catalog dedup, first time, quiet repeats
и unknown body relations сохраняются; shared inventory не восстанавливает owner associations.
Исходные exception object/cause/единственный business call не меняются, metadata failures
изолированы. No raw retention/backend/backfill/new selection FK.

Initial6path freeze2eb1735f прошёл independent18controls и root был интегрирован для full gate.
Дополнительный fault probe выявил valid safe descriptor с lone surrogate в version_basis:
модель строилась, но caller JSON serialization ломалась. Gate остановлен до source edits
(exact pytest97196; pre-prefix-serialization-repair-qa), этот запуск не считается зелёным.
Existing reader65 partial-prefix contract позволяет сохранить actual full ASCII view SHA,
owner/error/available parents, опустив неподдерживаемый descriptor suffix вплоть до zero prefix.
Producer-only repair и финальные pins/qualification фиксируются ниже; whole-frame discard
этого известного representational case не считается выполнением DA2.5.


J173 revision2: producer-only serializable_catalog обрезает только неподдерживаемый suffix
и пересчитывает existing retention ID; full ASCII view/count/policy/owner/error/actual parents
сохраняются. Root/child surrogate cases проверяют partial prefix, отсутствие false URI reason,
original exception/cause, doctor/restore/quiet repeat. Exact6path freeze8bf09611, full patch
d2b3ddff и repair delta e7fcb3fc independently closed6repaircontrols; root code review прошёл.
117focused/16newunit+4scenario, strict6file checks прошли.14paired public+1010cached/940pins
повторены на actual final source; business/IO/paid/messages/oldtables/IDs exact.12 actual
controlled producer cases get/doctor/restore2/+365quiet:1887–2810SQL/461–748gzip B, не forecast.
[Evidence](../assets/audits/2026-10-30-source-catalog-producer/handoff.json),
[final DA2.5 audit](../assets/audits/2026-10-30-source-catalog-producer/completion-audit.json).
Audit рекомендует закрыть DA2.5 после exact revision2 integration/fresh full gate; natural
production acceptance и physical execution остаются отдельно. Reader8baseline unchanged.


J173 final `make check`:7021 passed/251.508s, coverage92%; mypy611, Ruff/format678,
все678 source/config pins exact.16 новых unit cases/4 actual caller scenarios; strict docs
прошли. [Validation](../assets/audits/2026-10-30-source-catalog-producer/validation.json).
DA2.5 отмечен выполненным по exact revision2 integration/fresh full и независимому requirement
audit; deploy/natural acceptance отдельно. Вместе с J175: план74 /34done /6N/A /34open.


J173 commit `078b4c5c151a7698180e6bce689db5de75ee4732`: все четыре CI jobs и actual
relay `6d6905cc → 078b4c5c`, health 45s/timer подтверждены
[release proof](../assets/audits/2026-10-30-source-catalog-producer/release.json).
DA2.5 delivered; natural family-cycle acceptance остаётся отдельно.


<a id="j176"></a>

## J176 — retained consultation comparison snapshot

DA5.7 закрыт в текущем профиле хранения. Новый consultation review терял уже сохранённый
`comparison_text`: bill snapshot исключает предыдущий review, а `comparison_document` раньше
копировался только для commission. Теперь work хранит реальный comparison document, новый
ключ связан с этим immutable input; advance использует существующую snapshot-ветку без
повторного чтения baseline URL. Старые completed/pending keys совместимы: обновление кода
не инициирует платную повторную проверку и не дополняет старый pending отсутствующим input.
Паузы purposes, схемы и выбранный профиль хранения не меняются.

Три public Container-регрессии проверяют restore/retry, смену последующего review и URL,
изменение отрицания в baseline, force и legacy pending. Все три падают на изолированной
копии прежнего service `078b4c5c`; текущие 107 focused проходят. Независимый review
подтвердил точные два source SHA. На read-only snapshot `412e9635` есть два consultation
record, оба без comparison document: request-only old/new на disposable in-memory repos
даёт одинаковый полный SQL и restore. Это проверка совместимости, не natural demand
для сохранённого baseline и не восстановление исторического текста.

Full gate: 7024 tests, 245.138s, 92% coverage; mypy 612, Ruff/format 679; source freeze
679 paths, включая pyproject/lock, не изменился.
[Qualification](../assets/audits/2026-10-30-consultation-baseline/qualification.json),
[red proof](../assets/audits/2026-10-30-consultation-baseline/red-proof.json),
[actual-state parity](../assets/audits/2026-10-30-consultation-baseline/actual-state-parity.json).
Ни network, ни paid calls, ни processing/sends production при qualification не запускались.


J176 commit `27cb71e95edc5f9adf37a09a4d52389c7c1780d7`: четыре CI jobs и actual relay
`078b4c5c → 27cb71e9`, health 45s/timer подтверждены
[release proof](../assets/audits/2026-10-30-consultation-baseline/release.json).


<a id="j177"></a>

## J177 — bounded native physical caller history

Actual walk J169 подключён к TextLoader, cache, generic/support, RCL consultation, Senate
и CLI callers. Collector переносит immutable observations из workers; SQL и Clock остаются
на caller после join, в реальном input order. Shared neutral ordering объединяет порядок
privacy/catalog и physical outcomes. Parent artifacts/extraction проверяются до capture;
повторный cache использует исходный physical observation без нового walk. Custom extractor
не получает выдуманный physical graph. Observer setup/reset/cleanup/diagnostics не подменяют
бизнес-результат; original exception/cause и уже sealed facts сохраняются.

Пул ограничен 16 MiB/256 logical observations на проход, сохраняет детерминированный ранний
input prefix. Runtime metadata capture принят в этих bounds: raw тела/новый backend/архив
не добавлены, массового backfill нет. J173 catalog revision2/reader65 сохранены: 15 domain
AST exact, standalone delta 14 paths совпал после интеграции и format. Независимый review
закрыл три найденных сбоя и шесть repair controls; 58 focused merge cases прошли.

Final recipe `9cc13e3b…` проверен на девяти pinned raw controls/400412B и actual native
generic/support/RCL/Senate flow: 13 captures, 74 metadata. На schema65 небольшой популяции
дельта +111075 SQL/+18376 gzip. Повтор на disposable восстановленном state `412e9635`:
4198009 → 4217521 gzip (+19512), 20939397 → 21050472 SQL (+111075). Controlled upserts
3039/1929 пересекаются с реальными строками: это явный counterfactual backdrop, не natural
demand или прогноз целого корпуса. Off/on business/source SQL и workers1/4 all-SQL/order
совпадают; cache repeat — нулевой рост, doctor 0 → 0, два restore exact, исходный SQL неизменён.

Full gate: 7038 tests/247.926s/92%; mypy 616, Ruff/format 683; freeze683 с pyproject/lock
не изменился. Прежняя полная StageA4017-root qualification не объявляется replay текущих
новых component/recipe hashes. DA3.1 остаётся partial до отдельной проверки границы policy.
[Qualification](../assets/audits/2026-10-30-physical-callers/qualification.json),
[final handoff](../assets/audits/2026-10-30-physical-callers/final-handoff.json),
[populated backdrop](../assets/audits/2026-10-30-physical-callers/populated-handoff.json).


J177 commit `ac7c57448bc4113d88ea6fc1c2821cd75b3f0323`: четыре CI jobs и actual relay
`27cb71e9 → ac7c5744`, health 45s/timer подтверждены
[release proof](../assets/audits/2026-10-30-physical-callers/release.json).


<a id="j178"></a>

## J178 — bounded Word package structure sidecar

`extract_with_structure` возвращает transient companion из того же XML walk: body/note
paragraph, table row/cell, note call/actual last target, observed support и legacy view.
Default opt-in выключен; старые WordText/Note JSON, identities и parse path не меняются.
Новые структуры не объявляются fact/block/span coordinates текущего reader; carrier,
необойдённые XML parts/объекты и дальнейшая DA3.2/DA3.3 остаются отдельной работой.

Независимый review шести исходных pins/17 controls green; 116 focused и strict mypy/Ruff.
Один baseline/candidate replay 3055 DOCX/DOCM: текст, old JSON/IDs, trim/digest, physical
selection и provider-text exact; два исходных ValueError 657792/657796 unchanged.
11 pinned ZIP дали 74 actual Word leaf bindings, без ошибок; 56 real-source-derived
мутаций дат/сумм/отрицаний/scope/ссылок сохраняют clauses после trim и меняют fingerprints.
3053 manifests: 900 complete, 2153 partial с unknown full-stream hash. Лимит 512KiB,
4096 events; partial сохранили 1171–1206 events, то есть сработал byte/oversized-event
budget, отдельной классификации этих двух причин в исходном summary нет.
169404/1991043 blocks, 1388/5320 calls, 2957405/29198143 support retained/observed:
этот prefix не является полной картой документа.

Root сохранил exact AST Word classes при объединении с J177; component/recipe SHA
изменились честно. Final combined probe: 9 raw/400412B, 13 captures/74 metadata и
четыре прежних реальных Word notes/parts — old hashes/independent XML paths exact,
workers1/4 SQL/order/business, cache+365 zero growth, doctor/restore2 green.
Ни успешная матрица 3055/11, ни прежние hashes не перезапускались/переименовывались.
Driver-only repairs (keyword/field/tuple-list comparison) сохранены отдельно, source findings
не было. Общий gate Word+default-off maintenance: 7077 tests/322.36s/92%, mypy620,
Ruff688; 705 source/config/YAML/rules pins unchanged.
[Qualification](../assets/audits/2026-10-30-word-structure/qualification.json),
[parity](../assets/audits/2026-10-30-word-structure/parity-proof.json),
[combined native](../assets/audits/2026-10-30-word-structure/combined-native.json),
[shared gate](../assets/audits/2026-10-30-word-maintenance-gate/qualification.json).


<a id="j179"></a>

## J179 — exclusive finite state maintenance entrypoint

DA1.8 реализован для поддерживаемых операций. Manual `maintenance_spec` входит в тот же
`lexinform-daily` job lock до любых normal commands/backfill/index/run. Строгий JSON
4096B разрешает только existing finite legacy compaction: expected Git state, неизменённый
tracked SQL, actual DB == scratch restore+migrations, snapshot/entire-plan SHA и фиксированный
UTC import time. Никакого произвольного SQL, заменяющего dump, второго lease/backend или
нового submitter. Только successful applied transaction разрешает общий final checkpoint
SQL+реального polling manifest. Stale/invalid/no-op/dry-run, включая DDL restore delta,
не пишут canonical state/inbox; unrelated relay acknowledgement подавлен.

Default `LEXINFORM_STATE_MAINTENANCE=off` сохранён. DA6.5 продвинут до manual writer и
snapshot-specific qualification; actual production import не запускался и не объявляется
принятым. No automatic maintenance/sends/paid processing при обычном run.

Независимый Root review точных четырёх paths до copied-state; 200 focused/22 новых tests.
Root fresh fetch подтвердил state412e: original schema63, SQL20886677B/a39d69e…, восстановлен
в65. Один local-only Git/DB replay: 326 values/420 occurrences/62 stored shared bodies/1marker,
282 approved projection cells; 44 tables/3194 original rows сохранены с exact hydrated/public
views. Три doctor before/after exact, включая четыре прежних work warnings; новых errors нет.
Фактически pending batches/items 0; nonempty manifest проверен отдельным executable fixture,
не выдаётся за natural case. Shared checkpoint подтвердил local remote commit, restore2 exact,
+365 no-op без authorization/SQL growth. Пять actual-state guards (stale, wrong plan, dry-run,
postapply rollback, foreign DB) оставили DB/remote без изменений и без authorization.

Baseline current65: SQL20889343/gzip4191374 → SQL21436990/gzip4314030:
+547647 SQL/+122656 gzip (+2.9264%), existing105% budget не ослаблен. Darwin measurements:
prepare CLI0.812s/585MiB, apply CLI3.655s/949MiB; full apply+parity phase4.775s/991MiB,
two checkpoints1.482s/479MiB; extra origin restore≈0.22s. Это один20MB snapshot и process
high-water RSS, не incremental scratch RSS/hosted benchmark/global forecast.
Общий final gate с J178: 7077 tests/322.36s/92%, mypy620, Ruff688, 705 frozen inputs exact;
отдельные owned commits используют общий проверенный candidate, не два повторных full run.
[Qualification](../assets/audits/2026-10-30-locked-maintenance/qualification.json),
[current-state proof](../assets/audits/2026-10-30-locked-maintenance/current-state-handoff.json),
[guards](../assets/audits/2026-10-30-locked-maintenance/actual-state-guards.json),
[shared gate](../assets/audits/2026-10-30-word-maintenance-gate/qualification.json).


J178/J179: head `a1dc3b705c5599819d8b4fa9d8c68404715a56bd` включает оба owned commits;
четыре exact CI jobs и actual relay `ac7c5744 → a1dc3b70`, health45s/timer подтверждены
[release proof](../assets/audits/2026-10-30-locked-maintenance/release.json).


<a id="j180"></a>

## J180 — physical PDF coverage independent of legal interpretation

DA3.1 закрыт в согласованном bounded native профиле. Физический `_collect_content` всегда
фиксирует измеренное pages/chunks mismatch, включая blank text. Прежнее исключение для
целого акта с uzasadnienie и прежняя blank projection живут только в pure candidate policy.
Нельзя скрыть sentinel при unknown pages<=0; прочие losses и immutable physical snapshot
не меняются. Legacy `extract_document`/provenance остаются facades поверх physical+policy.
Root проверил reachable physical calls: юридического classifier/selector в обходе нет;
устаревший private `_zip` delegate недостижим из actual ZIP route, unused rank не вызывается.
Observed parent/container/member SHA, ordinal и limits предоставляет существующий companion;
старый root смысл `container_sha256` не переименован в выдуманный immediate parent.

После независимого Root review: 108 focused/strict4; 10 synthetic oldselectedJSON exact.
Один frozen raw pass 1914 unique roots/2878552247B за1509.624s: 114PDF+1568actualnested
PDF/DOC roots+220DOC/12XML conservative superset. 2103 observed-unaffected Word/ODT/ZIP-only
и четыре oversized исключены явно. Четыре actual Deflate64 headers; native depth/unpacking
limits в M0 не наблюдались, их покрывают synthetic actual-branch controls, не real-corpus claim.

Все selected text/JSON/trim/prefilter/provider variants/решения и other losses/order exact.
83 roots получили ожидаемую физическую диагностику: 92 blank parts/66 roots,17 justification
parts/17 roots. Остальные1831 — только actual component/policy version changes. Inventory1914
exact, execution changes ровно83, unexpected0, prefix reductions0; все1914 result/capture IDs
меняются честно. Source/index/master manifest/M0 pins unchanged. Пять семантических мутаций
дат/сумм/отрицаний/scope/ссылок сохраняются после fit и меняют law/provider digests.

Root объединил только import/loss hunks с текущим Word module; qualified physical class AST
exact, Word class AST сохранён. Actual merged standard recipe `ed52197f…`:9raw400412B,
13native captures/74metadata, off/on source/business SQL и workers1/4 order exact; cachequiet,
doctor/restore2 green. Corpus QA subclass recipe не выдаётся за production recipe.
Full7089/287.845s/92%, mypy620/Ruff688,705inputfreeze unchanged;1914 и3055 не повторялись.
[Qualification](../assets/audits/2026-10-30-physical-policy/qualification.json),
[raw summary](../assets/audits/2026-10-30-physical-policy/affected-summary.json),
[coverage](../assets/audits/2026-10-30-physical-policy/affected-coverage.json),
[merged native](../assets/audits/2026-10-30-physical-policy/merged-native.json).


<a id="j181"></a>

## J181 — durable review reuse after operational memo cleanup

Request review и `review_exhausted` теперь находят уже оплаченный schema3/validated result
при том же owner/purpose и exact known binding/request/result IDs, ordered input reference
и фактическом SHA полного caller-provided текста. После A→B и cleanup memo A точный A
переиспользуется без provider preparation, токенов или нового paid result. Context не
восстанавливается из live Bill; original IDs, prompt/version, paid usage/time сохраняются,
consumer получает прежнюю reuse accounting. Disabled/pause/currentBill/memo/force semantics
не меняются; unknown/wrongowner/input/comparison/version не обходят submission limit.

Root нашёл легитимный край в первом reviewed patch: реальный commission producer создаёт
для missing optional source пустые text/оба hashes, completeFalse/fileNone. Rev2 допускает
только этот corroborated sentinel плюс exact retained reference, не выдумывая empty SHA
или completeness. Подделки/другая purpose отказывают. Исходный rev1 сохранён, scope не расширен.
206 focused/30 новых, strict4; независимые8 initial и7 repair controls прошли на точных pins.

На fresh412e memory-copy есть3 saved reviews (2consultation/1act_application), все legacy
unbound; durable candidates0, matches0 до/после cleanup108memos/125deliverypayloads+restore.
Шесть saved text SHA совпадают; все9 paid rows и source SQL byteexact. Natural positive
не объявляется; positive3purpose A→B→cleanup→restore→A и exhausted/negative paths проверены
через public Container/provider fakes без paid/network/Settings. DA6.4 остаётся partial.
Общий gate с J182:7141tests/256.898s/92%, mypy623/Ruff691,708source/config pins unchanged.
[Qualification](../assets/audits/2026-10-30-review-history-reuse/qualification.json),
[saved-context proof](../assets/audits/2026-10-30-review-history-reuse/saved-context-qualification.json),
[shared gate](../assets/audits/2026-10-30-review-carrier-gate/qualification.json).


<a id="j182"></a>

## J182 — native Word structure companion and replay consumer

`WordStructuredParts` содержит прежний `ExtractedParts` и typed part-bound structures;
старый JSON/IDs не расширяется. Native optional capability собирает companion в существующем
`_collect` проходе, один раз на actual OOXML leaf; валидатор связывает ordered unique
physical part index/input/body/note hashes и прежний WordStructuredExtraction. Custom/failure
сохраняют unknown/старый rejection или original exception, без второго чтения/вымышленной
геометрии. Existing replay runner использует native capability вместо временного подкласса.
Нет Container/default/SQL/facts activation, отдельного framework или v2-budget изменения.

104 initial focused и Root static review; перед raw обнаружилось option mismatch: прежние
drivers отключали PDF page manifests, новый opt-in API включал их. Rev2 добавляет только
`include_pdf_pages=True` keyword, runner задаёт False; остальные options совпадают. После
116 focused/strict6 и независимого exact6/9control review выполнен один candidate-only
проход3055Word/11ZIP/56readyvariants против сохранённых baselines, прежние4lanes не повторены.
3053 native bindings и те же2 ValueError;74 actual nested Word bindings; legacy/physicalJSON,
selection/trim/digest/provider hashes exact, independentXMLpaths/roundtrips green.
V1sidecar unchanged:900complete/2153partial,169404/1991043blocks,1388/5320calls;
полная карта/новые fact spans не объявляются, semantic-firstv2 — отдельный этап.

Root Git3way clean с J180: actualadapter AST равен carrierrev2+ровно3 ранее принятым
physical replacements;other5SHA exact. Finalactualstandardrecipe `f9320160…`,9raw400412B,
13captures/74metadata и4Word cases через native companion: workers/IO/sourceSQL,
cache+365/get/doctor/restore2 и oldprovider hashes exact;708pins unchanged.
Общий gate J181/J182:7141tests/256.898s/92%,mypy623/Ruff691. Полный corpus-proof с
per-case hashes сохранён отдельно; trackedindex/summary фиксируют егоSHA и baselinepins.
[Qualification](../assets/audits/2026-10-30-word-structure-carrier/qualification.json),
[corpus summary](../assets/audits/2026-10-30-word-structure-carrier/qualification-summary.json),
[proof index](../assets/audits/2026-10-30-word-structure-carrier/corpus-proof-index.json),
[combined native](../assets/audits/2026-10-30-word-structure-carrier/combined-native.json).


J180 `f25cfc829134cd731c8c69c712b16c7c645b1419`: четыре exact CI jobs и actual relay
`a1dc3b70 → f25cfc82`, health45s/timer подтверждены
[release proof](../assets/audits/2026-10-30-physical-policy/release.json).


J181/J182 head `28cd1acae73596c2f5194285c515057e5c0b1c7f`: четыре exact CI jobs и
actual relay `f25cfc82 → 28cd1aca`, health45s/timer подтверждены
[release proof](../assets/audits/2026-10-30-word-structure-carrier/release.json).

<a id="j183"></a>

## J183 — prospective selected-name result binding

Nullable omitted-unknown computation/request refs живут в existing `editorial_json`,
не в LLM `BillName` schema; миграций/архива нет. Actual primary analysis и paid backfill
связывают только candidate, который действительно выбран. Proposal/same-name select no-op
не меняют provenance; ручной rename, включая same-name, остаётся unbound; history хранит
собственную прежнюю provenance. Pure helper corroborates validated result/record/request,
actual name/basis и primary language, без вывода refs из написания/старых receipts.

Projection receipt коммитится с фактическим selection в caller Clock/atomic seam; adapter
не импортирует services. Rollover/repeated term, оба tracking link paths и lookup link
переносят лишь текущий carried bound name с его actual source editorial receipt.
Original result owner/usage/request не меняются. Повтор идемпотентен; failure rollback
защищает primary/backfill/link/rollover projection. DA6.2/DA6.3 остаются partial до closure
audit остальных scopes и фактического перехода consumers к refs.

128 focused/27new, strict11 и независимый exact11/8control review green; BillName и
MIGRATIONS AST unchanged. State412e metadata replay:483Bills/47Editorial/194publications/
all9paidresults+refs+receipts old/candidate exact;47currentnames remainunknown,0retroactive
receipts. Quiet47editorial resave даёт+0SQL/+0gzip, restore2exact; original20886677B SQL
unchanged. Отдельный counterfactual knownname+2absentowners+transfer: wholecontrol+4639SQL/
+760gzip; transfer-only+1102SQL/+39gzip, quietrepeat0, restore2 и9originalpaidrows exact.
Это не natural bound-name acceptance. Settings/network/providers не использовались.
Full7168tests/257.380s/92%, mypy626/Ruff694,711frozen source/config inputs unchanged.
[Qualification](../assets/audits/2026-10-30-selected-name-binding/qualification.json),
[state proof](../assets/audits/2026-10-30-selected-name-binding/current-state-handoff.json).


J183 source `a757c4d5` получил docs CI failure: journal ссылался на release-proof, который
Root не включил в commit; локальный untracked asset маскировал ошибку. Только этот файл
добавлен в `4fdf19aeb707a0ea45ae908dfeeed95773c92565`. Проверка документации из staged
Git archive прошла; Python-код не менялся после7168 gate. Все четыре exact CI jobs и
actual relay `28cd1aca → 4fdf19ae`, health45s/timer подтверждены
[release proof](../assets/audits/2026-10-30-selected-name-binding/release.json).
[Staged snapshot qualification](../assets/audits/2026-10-30-selected-name-binding/staged-docs-repair.json).
Перед последующими push docs проверяются из staged/committed snapshot, не только working tree.


<a id="j184"></a>

## J184 — semantic-first Word retention within the same byte ceiling

Opt-in `word-semantic-structure-v2` использует прежний walk/collector hooks и реальные
original order/path, independent prefixes blocks/calls/exceptional support, closed ordinary
support counters. V1 contiguous-prefix JSON/IDs/default APIs и Word text/notes не меняются.
Semantic completeness означает только все наблюдённые blocks/calls известного walk;
support aggregated/partial отдельно, full stream SHA отсутствует. SQL/default/facts
activation нет, custom-v1 capabilities не получают неожиданный keyword.

Общий serialized ASCII envelope<=512KiB: header16KiB, blocks352KiB/calls112KiB/
exceptional32KiB плюс independent event reservations с суммой общего cap. Oversized closes
только свой lane, actualcounts/order продолжаются. Независимый review до raw нашёл
onlineallocation gap: oversized strings/path попадали в JSON serializer до refusal. Rev3
выполняет width/count preflight раньше serializer; два исходных finite repro и шесть
остальных bounded controls прошли; rev2evidence сохранено.155focused/strict9green.

Один candidate-only3055Word+11fixedZIP/74bindings+56readyvariants: прежние legacy/physical/
selection/trim/provider hashes exact, те же2ValueError, все56clauses retained. Каждый
фактическийv2envelope<=512KiB. Blocks169404→1347595/1991043 (8.5→67.7%),
calls1388→5320/5320 (26.1→100%);2150/645files improve,0decreased. Все900oldcomplete
сохранили всеblocks/calls;v2semanticcomplete2788/partial265. Supporttradeoff явный:
2995partial/58aggregatedcomplete,5909546ordinary counts aggregated,421634/23288597
exceptional locations retained. Это не полная XML-карта или гарантия роста на иных данных.
Source/input/baseline pins unchanged, старые lanes/wholeZIPpopulation не повторялись.

Root7pins exact, actualmergedstandardrecipe `c348c289…`;9raw400412B/13nativecontexts/
74metadata+4Wordv2 cases: independentXMLpaths, oldhashes,IO/workers/cache365/doctor/restore2
green;712pins unchanged. Full7207tests/258.370s/92%,mypy627/Ruff695. Документация
проверяется из staged Git archive со всеми новыми assets перед commit.
[Qualification](../assets/audits/2026-10-30-word-semantic-retention/qualification.json),
[corpus summary](../assets/audits/2026-10-30-word-semantic-retention/qualification-summary.json),
[comparison index](../assets/audits/2026-10-30-word-semantic-retention/retention-comparison-index.json),
[combined native](../assets/audits/2026-10-30-word-semantic-retention/combined-native.json).

<a id="j185"></a>

## J185 — bounded prepared-input reader before the runtime producer

Optional `BatchItemMeta.prepared_input_receipt` и pure builder связывают actual frozen
context/request с single loose text source и проверенными copied ranges. Full ordered map
SHA/count вычисляются streaming; retained prefix<=32segments и whole envelope<=8192B.
Absent projection остаётся unknown; physical/block origins unmapped, exclusions unknown,
completeness not asserted. Corroborated selection/artifact/extraction refs optional;
отсутствующие legacy parent records не реконструируются. Runtime producer пока отсутствует.

Independent review и64focused/29new units green; old None JSON/payload/IDs/bindings exact.
19distinct actual savedJ125 source/view/maps на4pinned DOC/XML/DOCM/DOCX cache bodies:
map SHA/count/copied spans exact;4missing-map unknown и4forged-span refusals. Controlled
fake provider requests не называются natural runtime coverage. Maxreceipt1009B,
traced builder peak21053B; natural>32maps в малой страте нет, cap proof synthetic отдельно.
Original5J125modules unchanged. Raw extraction/download/paid calls не выполнялись.

Root merged exports сохраняют Wordv2;715source/config pins exact после full gate:
7236tests/258.609s/92%, mypy630/Ruff698. Old reader теряет новый optional
receipt при rewrite: reader deployment должен предшествовать writer; rollback после
producer требует совместимого reader либо сохранённого snapshot. SQL/default producer
не активированы. Wordv2 a655bbaf: exact CI и actual relay transition/restart45/timer verified.

DA3.2 закрыт по literal bounded native-coordinate contract: versioned PDF page/index до
whitespace projection, Word XML paragraph/table/note addresses из actual walk, ODT body
paths и explicit unknown DOC/XML. Сохранение полной XML-карты не обещается; SQL хранит
bounded summary, повторное извлечение требует оригинала. Default Word view coverage и
ODT note/call gaps остаются в DA3.3; future fact→block mapping относится к DA4/DA9.

[Qualification](../assets/audits/2026-10-30-prepared-input-reader/qualification.json),
[cached corpus proof](../assets/audits/2026-10-30-prepared-input-reader/corpus-qualification.json),
[independent closure](../assets/audits/2026-10-30-prepared-input-reader/independent-closure.json),
[coordinate audit](../assets/audits/2026-10-30-prepared-input-reader/coordinate-closure-audit.json),
[Word release](../assets/audits/2026-10-30-word-semantic-retention/release.json).

<a id="j186"></a>

## J186 — compatible compact-primary and bounded Word-status readers

Modern tagged primary refs сохраняют immutable paid record/request/schema/raw-record digest
и ограниченные observation/accounting overrides. Reader валидирует indexed owner/receipts,
current и READY роли отдельно, public get/list/search/equal-save/memo/cost, doctor/restore.
Default callers продолжают писать inline; writer/backfill/DDL/LLM schema changes отсутствуют.
Neutral duplicate-safe finite JSON/canonical helpers переиспользуются legacy и modern paths.

Government move в существующей atomic transaction проверяет selected/moved count и
пишет corroborated target primary receipt до target hydration; mismatch откатывает всё.
211focused/32new и independent7+actual collected-only government READY control green.
Active READY/BATCH states не переносятся government rollover. Transitive READY не
поддержан: первоначальный writer сохраняет все READY inline до отдельной transport/fallback
qualification. Current/memo projection receipts не заменяют collection proof. Frozen
provider/pending/delivery/content snapshots остаются inline; изменённые answer views нельзя
компактировать как исходный paid body.

WordViewStatus reader добавляет optional<=512B status только actual legacy Word summary;
known visited walk и четыре disposition counters не доказывают full XML или потерю
юридических фактов.41focused/30new, maxvalid399B; None JSON/result IDs и16KiB cap exact.
Peer review нашёл typed-parent bypass: negative child count и rawdict после model_copy.
REV3 revalidates child и отвергает nonmodel typed slot; оба repro+6controls green.
Producer/collector/runtime Word changes в этот commit не входят.

Actual412e:483Bill/list,155memo,172cost rows,194publications,9paid results, все physical
tables/doctors exact;0errors, restore2/quiet repeat, SQL20889343B/gzip4191374B unchanged.
Census:14corroborated slots=7current+7memo/7paid primary,232unknown;0actualREADY.
Отдельная manual counterfactual14 сохраняет public values/field presence, все прочие
columns/tables/paid/frozen/editorial data, doctor0, restore2; corrupt/missing parent restore
сохраняет обе DB. SQL−99036B, gzip−23489B (4191374→4167885), projection JSON104010→4974B,
maxref396B. Это format qualification, не runtime/backfill/global savings forecast.
Single local timings5.69/6.09/7.14s не являются performance forecast; raw SQL/source13 exact.

Root combined723source/config pins exact:7298tests/257.466s/92%, mypy638/Ruff706.
J185 exact CI и actual relay verified до следующего reader release. Reader-first barrier
сохраняется: старый reader отвергает новый Word status/ref; rollback требует совместимого
reader либо сохранённого snapshot. Source/raw/provider/paid production processing не запускались.

[Qualification](../assets/audits/2026-10-30-compatible-document-readers/qualification.json),
[actual state](../assets/audits/2026-10-30-compatible-document-readers/actual-state-qualification.json),
[primary peer](../assets/audits/2026-10-30-compatible-document-readers/primary-closure.json),
[Word peer](../assets/audits/2026-10-30-compatible-document-readers/word-independent-closure.json),
[J185 release](../assets/audits/2026-10-30-prepared-input-reader/release.json).

<a id="j187"></a>

## J187 — prospective compact primary projections and memo

CURRENT analysis и root primary memo при обычном сохранении используют общий pure helper:
same paid owner, indexed immutable result/request/schema/raw-record digest, минимальные
observation differences, точный recursive JSON/field-presence round-trip, cap8192 UTF-8
и строго положительная экономия. Ineligible/foreign/READY/secondary/frozen inputs остаются
inline; архивные ответы и старые неизвестные identities не реконструируются. Bulk backfill нет.

Primary collection сохраняет parent и collection receipt до memo внутри прежней atomic
transaction. Ошибка memo SQL откатывает parent/receipt/memo/READY/consumed до accounting.
Memo conflict сохраняет первоначальный ответ; COALESCE owner transfer сначала материализует
его под прежним владельцем. Equal-save generation/status, legacy preservation и paid rows
сохраняют прежний смысл.335focused/31new и независимые8controls прошли.

Первый полный gate выявил11 пропущенных совместимостей до commit. Direct public government
move теперь атомарно материализует CURRENT перед сменой owner; service ledger bridge остаётся
аудированным, новых разрешений reader не получает. Shared reader подключён к observed_latency,
включая query-only native/inline clocks. Doctor одинаково считает known refs, inline и modern
corruption controls сохранены.420focused и117 regression controls green; исходные failed gate
и прежние source/qualification evidence сохранены.

Повторная paired actual412e qualification вызывает одинаковые PUBLIC7current+7memo saves
в olda400 и candidate. Все14 естественно compact; maxref384B. Public/get/list/search/status/
generation/editorial/recursive shapes для483Bill,155memo,172cost rows,194publications и все
9paid/results/applications/frozen tables совпадают. Различаются только current/memo JSON:
SQL20889343→20790020B (−99323), gzip4191374→4167842B (−23532), payload104010→4687B.
Quiet saves, owner/conflict, doctor0, restore2 и corrupt-parent restore preservation green.
PUBLIC move226government rows и patched latency совпадают; observed coverage остаётся partial.
232unknown-ID slots и0actualREADY явно исключены. Это prospective-copy qualification, не
production backfill или общий прогноз экономии.

Root full7336tests/268.481s/92%, mypy640/Ruff708;725source/config pins exact.
Staged Git archive docs gate выполняется до commit. Reader a400 уже прошёл exact CI и actual
relay; rollback с новыми refs требует совместимого reader либо сохранённого snapshot. Word
producer/performance и prepared-input restore guard сюда не включены. DA6 остаётся открытым
по конкретным ownership/lifetime задачам, универсальный bodyRef не добавляется без требования.

[Qualification](../assets/audits/2026-10-30-primary-projection-writer/qualification.json),
[actual prospective state](../assets/audits/2026-10-30-primary-projection-writer/actual-state-qualification.json),
[remaining audit](../assets/audits/2026-10-30-primary-projection-writer/remaining-audit.json),
[reader release](../assets/audits/2026-10-30-compatible-document-readers/release.json).

<a id="j188"></a>

## J188 — prospective input receipts with restore integrity checks

После единственного provider prepare runtime связывает actual frozen request/context с original
selection до fitting и итоговой projection. Только parents из этого captured read могут стать
known refs; ambiguity/unsupported остаются unknown/absent. Caller сохраняет source history в
прежнем отдельном checkpoint, затем optional receipt вместе с intent/status в enqueue atomic.
Original valid intent создаётся до enrichment; обычные metadata/log faults не ломают работу.
KeyboardInterrupt/SystemExit не подавляются: Root finding исправлен до интеграции; exact
object/cause, отсутствие queue/submit/status writes и rollback подтверждены.

Receipt<=8192 UTF-8 bytes/32 retained segments; full ordered map hash/count streaming, copied
spans проверены. Physical/block origins unmapped, exclusions unknown, completeness not asserted.
Новый body archive/storage backend не появляется, дополнительные provider calls отсутствуют.
103focused/23new и Root6 cancellation/failure controls green.19 distinct savedJ125 maps
на4cached sources прошли helper без raw extraction; controlled fake contexts/requests явно
отделены от natural production. Reader был реально deploy до producer.

Finite paired412e copies с19 одинаковыми controlled intents: receipt-only +18253B SQL/+4711B
gzip; original3194rows/483typedBills, paid/publication/editorial/frozen данные неизменны.
Quiet repeat, restore2 и3doctors exact. Это ограниченная counterfactual qualification,
не восстановление request из старого paid answer и не прогноз общего роста.

Public restore preflight обнаружил принятие corrupt map/request и замену target. Новый shared
adapter validator используется scratch restore до изменения target и computation doctor:
known retained payload SHA проверяется также без receipt; receipt-bearing intents проверяют
actual context/request и row identity, items — retained metadata/known SHA без реконструкции
удалённого context. Indexed known parents должны существовать и соответствовать связи.
Legacy unknown остаётся unknown.55focused/14new и независимые8controls green; исходные
request/map corruptions теперь не меняют target/source. Cleanup, missing/wrong parents,
старые snapshots и fresh0278 (483Bills/194pub/9paid/118items/0intents) проходят restore2/doctor.

Root narrow3way merge сохранил current/memo/government/primary restore checks и known-ref
doctor counters. AST подтверждает только _prepare_loaded/_enqueue и repository.restore
изменения существующих методов; parent-first collection J187 byte-semantics сохранены.
Full7373tests/269.006s/92%, mypy645/Ruff713;730source/config pins exact.
J187 exact CI и actual relay verified; staged Git archive docs gate выполняется до commit.
Multipart/block input profiles и полная DA5 lifetime цепочка остаются открытыми.

[Qualification](../assets/audits/2026-10-30-runtime-input-receipts/qualification.json),
[controlled populated state](../assets/audits/2026-10-30-runtime-input-receipts/runtime-positive-qualification.json),
[guard peer](../assets/audits/2026-10-30-runtime-input-receipts/guard-independent-review.json),
[fresh state](../assets/audits/2026-10-30-runtime-input-receipts/guard-fresh0278-qualification.json),
[J187 release](../assets/audits/2026-10-30-primary-projection-writer/release.json).

<a id="j189"></a>

## J189 — bounded default Word view diagnostics

Actual native provenance path собирает четыре disposition counters в прежнем XML walk.
Status-only hooks не создают coordinate events/path/tag inventory/JSON; typed transient
carrier связывает actual input/body/note manifests и physical index. Plain extract_parts,
Word text/notes, coordinate v1/v2, legacy body/note/physical manifests, selection/admission,
DocumentPart.losses и provider bytes прежние. Custom/inherited/instance overrides не обходятся.

Optional WordViewStatus<=512B сообщает только observed legacy visited walk: finished не
означает full XML или отсутствие пропущенных юридических фактов. Fields/changes остаются
legacy-unresolved; отсутствие metadata означает unknown.16KiB outer cap сначала удаляет
optional tail statuses, сохраняя прежний prefix summaries/omission semantics. Cancel repair
пропускает точные KeyboardInterrupt/SystemExit и восстанавливает ContextVar; cleanup сохраняет
active parser error/cause. Обычные metadata faults не меняют бизнес-результат.

Profile-driven bounded namespace classification сокращает лишние QName allocations; cache
только для существующих3Word namespaces, unsupported namespace не размножает untrusted URI.
Global/model cache, unchecked DTO и ослабление crypto/legacy validation не вводились.
Исходные ревизии/profiles/raw evidence сохранены.8 independent final controls green.

Final candidate3055Word/3053valid +те же2DEFAULT blockedempty; fixed11ZIP/74bindings и
56meaningful variants совпадают с savedREV2 status и всеми business records. Старые lanes
не перезапускались; final повтор после classifier code change был отдельным qualified run.
Controlled412e status-off/on при physicalobserver active в обеих ветках: +3471B SQL/+136B
gzip, repeat/cache365zero. Root merged732 на fresh0278:9raw400412B/13contexts74metadata,
workers1/4 ALLSQL/business equal, get/doctor/restore2 и все старые строки сохранены.
Шесть input/container SHA заново созданных synthetic DOCX отличаются от старого probe;
все прочие бизнес-поля и9pinned raw cases точны. Delta явно сохранён без ID normalization.

Диагностика имеет измеренную CPU стоимость. Isolated16 REV2 +25.5%, hash-reuse REV3 +24.2%;
final embedded16 +27.9% к reused isolated baseline получен в другом harness context.
Ускорение и production forecast не заявляются; overhead принят как стоимость нового
ограниченного наблюдения, строгие проверки сохранены. Это не заявление о нулевой
performance regression; дальнейшая оптимизация остаётся возможностью по профилю.

Root full7409tests/266.136s/92%, mypy647/Ruff715;732source/config pins exact.
J188 exact CI/actual relay verified до release. Staged archive docs gate перед commit.
DA3.3 остаётся открыт для ODT notes и иных явно недоказанных structure/view guarantees.

[Qualification](../assets/audits/2026-10-30-word-default-view/qualification.json),
[corpus](../assets/audits/2026-10-30-word-default-view/qualification-summary.json),
[merged native](../assets/audits/2026-10-30-word-default-view/combined-native.json),
[performance context](../assets/audits/2026-10-30-word-default-view/journal-performance.md),
[J188 release](../assets/audits/2026-10-30-runtime-input-receipts/release.json).

<a id="j190"></a>

## J190 — retain Wykaz naming scope across government term rollover

Existing original-owner lookup распространяется с RCL на Bill.is_wykaz. Полный NameContext
и current/legacy memo keys остаются прежними; terms берутся только из actual retained
intents/items/paid results. government_path не используется: printed government Sejm bills
не получают новое разрешение. Schema, request contents, model prompts и paid identities прежние.

Queued request сохраняет original term/provider/key без дополнительного prepare. Collected
answer после cleanup/restore выбирается с явным selected new-owner receipt; исходные paid
result/request/usage неизменны, повтор не создаёт вызов. Смена title/summary/changes/language
не переиспользует старый ответ; numeric Sejm остаётся вне predicate. Из7 новых случаев старый
код проваливал3; после исправления81focused green, независимые6 runtime controls прошли.
Existing RCL tests, memo-key AST, SQL и models byteexact. Worker/provider варианты проверяют
реальные lifecycle seams без платных запросов.

Root narrow3way merge меняет только _name_request_key среди существующих методов. Parent-first
primary collect и input receipt/restore guards сохранены. Full7416tests/267.137s/92%,
mypy648/Ruff716;733source/config pins exact. Word f2d exact CI/relay verified до следующего
release. Staged Git archive docs gate выполняется перед commit.

DA6.2 audit остаётся OPEN: amendment revision создаёт QUEUED target projection+receipt в
collect atomic, а literal plan требует отдельной recoverable application transaction. SENT
publication не меняется, но эта boundary пока не реализована. Disabled act producer не
включается ради checkbox; actual delivery cycles DA10.7 не доказываются fake tests.
§1.4 persistent duplication lifetime — отдельная задача DA6.3, не скрытый универсальный bodyRef.

[Qualification](../assets/audits/2026-10-30-wykaz-name-rehome/qualification.json),
[peer](../assets/audits/2026-10-30-wykaz-name-rehome/independent-closure.json),
[atomic purpose audit](../assets/audits/2026-10-30-wykaz-name-rehome/atomic-purpose-audit.json),
[Word release](../assets/audits/2026-10-30-word-default-view/release.json).


<a id="j191"></a>

## J191 — inert bounded ODT note-origin reader

Добавлен отдельный odt-note-origins-v1 companion: actual physical part/input/XML/body binding,
пути note/citation/body/call, nested ancestry и явные missing/ambiguous/unverified associations.
Separate reference разрешается только по unique actual nonempty ID при полном наблюдаемом
note inventory; embedded note связывается по своему XML path. Bounds:64combined events,
32KiB canonical ASCII envelope,64path depth,63-bit indexes и256-byte strings.
Finished относится к legacy visited walk, а не к полноте XML или юридического документа.

Parser, ports, SQL, прежние OdtTextExtraction/ExtractedParts JSON и body manifests не меняются.
Новых runtime writers/consumers нет; emitted ranges требуют explicit observed-append witness
и actual block bounds, но модель сама не доказывает producer emission. Custom extractor
остаётся unknown. Natural note-rich qualification и producer остаются открыты, DA3.3 не закрыта.
Повтор corpus extraction не выполняется: extraction/normalization/selection/fingerprint/comparison
код untouched; controlled old JSON/body-manifest compatibility проверена отдельно.

Независимое review нашло concrete coherence defect: две lanes были упорядочены отдельно,
а combined invocation sequence могла идти назад по XML paths. Regression на старом candidate
упал; Root добавил global order и same-node entry→embedded-call constraint. Forward reference
к последующему definition остаётся допустимым. Прерванный первый full run не считается gate;
финальная проверка выполняется на исправленном source freeze.

Final gate:7438tests/269.217s/92%,mypy650/Ruff718;735source/config pins exact. Staged archive docs gate перед commit.

[Qualification](../assets/audits/2026-10-30-odt-note-reader/qualification.json),
[integration](../assets/audits/2026-10-30-odt-note-reader/integration.json),
[original peer finding](../assets/audits/2026-10-30-odt-note-reader/independent-review.json),
[peer closure](../assets/audits/2026-10-30-odt-note-reader/peer-closure.json),
[previous Wykaz exact CI/relay](../assets/audits/2026-10-30-wykaz-name-rehome/release.json).


<a id="j192"></a>

## J192 — inert recoverable amendment-application work storage

В existing document_work добавлен closed second typed consumer без новых migrations или
runtime producer/drain. AmendmentRevisionApplicationWork хранит≤8192B metadata и actual
result/request, original publication/channel/message и bill/change/plan snapshot refs.
Frozen identity не перезаписывается, attempts не уменьшаются, complete не открывается повторно.
Senate DocumentWork JSON прежний; отдельный dispatcher не получает revision application rows.
Unknown explicit work kinds отвергаются. Shared validator проверяет SQL/JSON identity/schedule,
actual immutable result и snapshot hashes/kinds/owners; restore выполняет проверку на scratch
до замены target, тот же validator использует work doctor.

Это prerequisite reader, не разделение runtime transactions: DA6.2 остаётся OPEN. Первый
producer будет требовать совместимого reader при rollback. Current SENT target eligibility,
_pending до source/prepare/force, recoverable collect→apply и drain без open batch относятся
к следующему этапу. Никаких фиктивных Senate fields или inferred ContentVersion не добавлено.
Original retained context corroborates refs; после cleanup отсутствие original operational
payload явно unknown. Mandatory referenced snapshots/results сохраняются.

Author181focused/22new и peer8 public controls green. Проверены typed refs/index corruption,
scratch target preservation, immutable identity/lifecycle и exact cancellation propagation.
Отдельные peer probes: contradictory embedded plan запрещён existing semantic snapshot model,
включая SQL bypass; completed SENT revision переживает365d cleanup и restore.
На pinned state0278c2d619d4 public restore/dump byteexact baseline a97a9bde…:
schema65,483Bills/194publications/9paid/118items/0intents/4legacy document_work.
Doctor:0errors и4preexisting document_publication_unavailable warnings, baseline/candidate
коды и summary равны; первая слишком строгая no-findings assertion сохранена как rejected
proof driver, не выдана за продуктовый дефект. Исходный SQL неизменён, processing/paid IO нет.

Final gate:7460tests/263.145s/92%,mypy653/Ruff721;738source/config pins exact. ODT352c3d37 exact CI/relay verified; staged archive docs gate перед commit.

[Qualification](../assets/audits/2026-10-30-revision-application-work-reader/qualification.json),
[integration](../assets/audits/2026-10-30-revision-application-work-reader/integration.json),
[peer](../assets/audits/2026-10-30-revision-application-work-reader/peer-closure.json),
[actual state](../assets/audits/2026-10-30-revision-application-work-reader/actual-state.json),
[merged PRIMARY repeat](../assets/audits/2026-10-30-revision-application-work-reader/merged-actual-state.json),
[consumer inventory](../assets/audits/2026-10-30-revision-application-work-reader/consumer-inventory.json).

[J191 exact CI/relay](../assets/audits/2026-10-30-odt-note-reader/release.json).


<a id="j193"></a>

## J193 — opt-in same-pass ODT note/call capture

Native extract_with_note_origins и physical extract_parts_with_odt_notes наблюдают existing
parse/walk/append operations, не добавляя production XML/ZIP read. Returned legacy values
и default APIs прежние; consumer в runtime/SQL/summary не подключён. Child paths, nearest
paragraph/block, entry→embedded call, before-tail emitted spans, note IDs/classes и unique
exact-ID references связаны с actual input/XML/body/physical index. Prefix bounded до
allocation:64events/32KiB, counters продолжаются; incomplete inventory не доказывает
уникальность или отсутствие target. No substring inference и full-XML/legal coverage promise.

Custom/inherited/instance/class public overrides вызывают actual old boundary и остаются
unknown. Metadata Exception выключает дополнительный capture; cancellation и parser cause
сохраняются. Peer поймал2ошибки до full: model_construct bypassed carrier validation и
сравнение с mutable class method могло обойти override. Required empty carrier теперь
валидируется перед optional enrichment; native method identity captured once. Оба regression
контроля red-before/green-after; core+fifth runner independent review closed.

Author63focused/23new, strict5/Ruff/format green; Root63focused/preflight green.
Один replay pinned J1196natural+9derivatives:17ODTleaves,0businessdelta/0input/index/code drift.
Natural subset содержит4unique note-bearing inputs:4notes/4embedded calls,8independent XML
path checks. Natural notes содержат controls: independent plain-itertext span oracle для них
не заявлен. Synthetic plain/nested cases отдельно проверяют8spans, literal control cases —
unit tests. Rich-note/real separate-ref generalization и независимых legal labels нет.
Runner делает отдельный comparison pass/XML oracle только в qualification, не в production.
Root переиспользует frozen replay при exact extractor/helper/test/tool source; ports merge
сохраняет J192interfaces, повтор300KiB reports остаётся в out, в docs — compact evidence.

DA6.3 residual audit:2same-owner supplement memos всего4934B; отдельный bespoke format ради
этой экономии отложен. Frozen editorial/delivery context нельзя удалить как cache, а schema3
review result не хранит весь comparison input. Это не закрывает DA6.3; следующий refactor
должен оправдывать сложность и использовать existing reference machinery.

Final gate:7483tests/273.802s/92%,mypy655/Ruff723;740source/config pins exact. Staged archive docs gate перед commit.

[Qualification](../assets/audits/2026-10-30-odt-note-producer/qualification.json),
[handoff](../assets/audits/2026-10-30-odt-note-producer/handoff.json),
[peer](../assets/audits/2026-10-30-odt-note-producer/peer-closure.json),
[integration](../assets/audits/2026-10-30-odt-note-producer/integration.json),
[synthetic oracle](../assets/audits/2026-10-30-odt-note-producer/synthetic-oracle.json),
[replay recipe](../assets/audits/2026-10-30-odt-note-producer/recipe.json),
[lifetime audit](../assets/audits/2026-10-30-odt-note-producer/secondary-lifetime-report.md),
[J192 exact CI/relay](../assets/audits/2026-10-30-revision-application-work-reader/release.json).


<a id="j194"></a>

## J194 — separate recoverable amendment-revision application

AmendmentRevisionApplications отделяет immutable paid collect от QUEUED correction projection.
Collect transaction сохраняет normalized result/source URL, memo, original usage, consumed,
durable waiting/blocked work и малый batch→work pointer. Historical Bill/change/plan refs
зафиксированы до prepare; body-free semantic plan и существующие editorial snapshots dedup.
Separate application transaction создаёт QUEUED publication, exact target receipt и completion
вместе. Сбой apply сохраняет paid answer/work, повтор без provider/prepare/charge создаёт
ровно одну projection. Сбой collect по-прежнему откатывает checkpoint. Original SENT event
и payload не переписываются, физическая доставка сохраняет existing uncertainty rules.

Caller-thread drain вызывается до provider polling и после collection checkpoint; отсутствие
open batch/provider и poll outage не препятствуют local recovery. Waiting/blocked lookup
предшествует source/prepare/force. Current primary skip/reset не подменяет historical correction
новым Bill; смена physical target блокирует работу. Новые producers допускают только printed
Bill.has_process с тем же captured owner: literal government mover не выбирает эти rows,
link_bills оставляет STATUS_UPDATE owner прежним. Unsupported legacy сохраняет paid answer,
исходный target и explicit problem; строгий storage validator не ослаблен ради rehome.

Heavy collected target metadata заменяется validated work-key pointer. Scratch restore и
work doctor проверяют actual item/result/work/owner/request association; known dangling pointer
не заменяет target DB. После первого handoff rollback требует work-aware pending/drain runtime
либо quiesced producers/commands и reconciliation; одной parse-совместимости inert reader мало.
Нет нового submitter/scheduler/DDL, adapter imports в service или worker SQL/Clock writes.

Author207focused/strict11/Ruff и peer207+7 independent controls green: workers1/4 calling-thread
parity, четыре item→work corruption controls, checkpoint failure и provider-free recovery.
Matched fake old/new сохраняет paid/request/usage/Bill/original/revision+other rows. Queue peak
+1062SQL/+22gzip; collected−6708SQL/−94gzip;365d cleanup−6708/−87. Work947B, plan924B,
metadata9644→737B; Bill/change snapshots dedup1each, quiet repeat0. Это controlled fake
lifecycle; natural revision-work cycle не выдуман. Fresh state8022b0a59f73:487Bills/196publications/9paid/118items/0intents/4legacywork; все48tables/sqlite_master/restored dump и487typed digests exact baseline f232/candidate. Три doctor дают0errors, work сохраняет4baseline warnings; modern work/handoffs0.

Первый full нашёл3 content-inspection failures (7500passed), release gate не засчитан.
Completed work теперь принимает actual bound/index-checked editorial version при отсутствующем
operational payload: exact frozen Bill/revised change/target и actual receipt проверяются;
missing/unrelated binding отвергается. Owner/kind checks остаются строгими. Corrupt inspector
fixtures сначала доказывают modern restore rejection/target preservation, затем explicit legacy
profile сохраняет прежние inspection assertions. Post-fix fresh-state public restore/487typed
Bills/3doctors повторён и exact baseline.

J193 new-case tally исправлен: collection-only и full7460→7483 подтверждают23, не24;
original author handoff сохранён, correction artifact явно supersedes только count metadata.

Final gate:7506tests/265.873s/92%,mypy657/Ruff725;742source/config pins exact. J193 exact CI/relay verified. Staged archive docs gate перед commit.

[Qualification](../assets/audits/2026-10-30-revision-application-runtime/qualification.json),
[peer](../assets/audits/2026-10-30-revision-application-runtime/peer-closure.json),
[integration](../assets/audits/2026-10-30-revision-application-runtime/integration.json),
[size comparison](../assets/audits/2026-10-30-revision-application-runtime/size-qualification.json),
[actual state](../assets/audits/2026-10-30-revision-application-runtime/actual-state.json),
[post-fix state](../assets/audits/2026-10-30-revision-application-runtime/post-fix-state.json),
[payload fix](../assets/audits/2026-10-30-revision-application-runtime/payload-fix.json),
[fix peer closure](../assets/audits/2026-10-30-revision-application-runtime/fix-peer-closure.json),
[finite DA6.2 audit](../assets/audits/2026-10-30-revision-application-runtime/closure-audit.json),
[J193 exact CI/relay](../assets/audits/2026-10-30-odt-note-producer/release.json),
[test-count correction](../assets/audits/2026-10-30-odt-note-producer/test-count-correction.json).


<a id="j195"></a>

## J195 — inert bounded ODT note-status reader

Optional ExtractedPartSummary.odt_note_status ограничен1024B и отсутствует в прежнем JSON
при None. Диагностика описывает существующий origins-v1 visited walk: observed/retained counts,
coverage и retained manifest SHA, explicit contents=not_retained и XML not_asserted. Input SHA,
body manifest SHA, ODTformat/view/coordinates/blockcount corroborated существующими part fields;
отдельного XML slot нет, его не выдумываем. Hash не обещает восстановление transient paths/spans.

35new collection cases/65focused, strict3/Ruff и peer closed. Peer нашёл outer typed-slot gap:
ExtractionResult.model_copy/model_validate мог пропускать известный invalid ODTstatus при
валидирующемся внутреннем summary. Narrow outer guard перепроверяет только PRESENTODTstatus
после existing16part preflight; before-red2/green2 и validouter control подтверждены.
Нет глобальной revalidation policy или Word status изменения. Absent/NoneJSON/result IDs,
WordViewStatus AST и old real-fixture inputs byteexact; runtime/adapter/SQL/cache writers untouched.
Старый reader отвергает PRESENTfield, поэтому следующий writer ждёт actual reader deployment.

Root source3 exact; J194runtime/restore/ports и J193ODTproducer сохранены byteexact.
Corpusextraction не повторялась: extraction/trimming/normalization/selection/comparison code
не меняется. Writer, default capture,16KiB fitting/cache behavior относятся к следующему этапу.

Final gate:7541tests/274.664s/92%,mypy658/Ruff726;743source/config pins exact. Staged archive docs gate перед commit.

[Qualification](../assets/audits/2026-10-30-odt-note-status-reader/qualification.json),
[peer](../assets/audits/2026-10-30-odt-note-status-reader/peer-closure.json),
[integration](../assets/audits/2026-10-30-odt-note-status-reader/integration.json).


J194 actual release80025409 подтверждён: exact CI, remote main и relay restart/timer proof.
Literal DA6.2 закрыт: единственный remaining revision collect/application exception устранён;
10kinds/unchanged consumer proofs и отдельная durable recovery transaction проверены.
DA6.3 lifetime и DA10.7 natural/disabled-act cycles не выданы за выполненные.
[Release proof](../assets/audits/2026-10-30-revision-application-runtime/release.json).


<a id="j196"></a>

## J196 — bounded default ODT note diagnostics

Native provenance extraction включает существующий same-pass ODT companion и передаёт его
в summarize_extraction. Required old validated ExtractionResult строится первым; optional
Exception-only enrichment добавляет status только для actual selected physical index +input/body
hash. Invalid обязательный baseline, backend/selection/parser errors и cancellation не скрываются.
Custom/inherited/class/instance public overrides остаются actual+unknown. Текст/selection/provider
inputs и legacy physical manifest прежние; фактические codecomponent SHA меняются честно.

Per-status≤1024B и global summary16KiB/16parts: новое ODT diagnostic удаляется первым,
old selected prefix/Word metadata не вытесняются. Persisted hash/count/status помечен
contents=not_retained, XML not_asserted; никакой32KiB origin manifest/blob не копируется.
Новых SQL/backend/cache/service paths нет. Old cache сохраняет unknown, known cache — original
metadata; quiet/365d/dump-restore проверки показывают zeroIO/неизменныйdump, без fresh claims.
Контролируемый FakeBatchBackend.prepare_request сериализует тот же frozen context/payload/hash;
realSDK/naturalprovider proof и production/performance или SQLgrowth estimate не заявлены.

Author96focused/10new/strict4/Ruff и peer10+5 public controls green; actual oneXMLparse/read
проверен отдельно от qualification driver. На finalsource один J1196+9 replay:17ODTleaves,
0oldbusinessdelta/input/index/code drift. Natural defaultselection даёт1status с0notes/calls;
4physical note-bearing companions не выбраны прежней policy. Variants:8statuses с0notes/calls.
Positive selected note1/call1 — controlled native fixture; note-rich/natural selected-note
coverage не выдумана. Maximum observed result3375B, raw reports остаются в out; compact recipe,
cohort pins и review сохранены. Deployment writer требует actual J195reader proof.

Final gate:7551tests/272.653s/92%,mypy659/Ruff727;744source/config pins exact. J195 exact CI/relay verified before writer push. Staged archive docs gate перед commit.

[Qualification](../assets/audits/2026-10-30-odt-note-status-producer/qualification.json),
[peer](../assets/audits/2026-10-30-odt-note-status-producer/peer-closure.json),
[integration](../assets/audits/2026-10-30-odt-note-status-producer/integration.json),
[recipe](../assets/audits/2026-10-30-odt-note-status-producer/recipe.json).

[J195 exact CI/relay](../assets/audits/2026-10-30-odt-note-status-reader/release.json).


<a id="j197"></a>

## J197 — inert retained ODT origin-prefix reader

Optional omitted-None odt_note_prefix сохраняет existing OdtNoteOrigins, physical index и
original block(path/kind/char-length) witnesses. Aggregate4096 ASCII JSON bytes на весь result
внутри existing16KiB, не4096накаждыйpart. До allocations проверяются raw/typed widths и
counts; после iterable conversion cap проверяется снова. Typed child/emitted slots revalidate.

Status hash/counts/coverage относятся к original capture, stored prefix имеет собственный
recomputed manifest hash и coverage. Full captured event count требует совпадения hash;
truncation не увеличивает captured retained counts. prefix_retained iff prefix присутствует.
Global event prefix, entry-before-call, ancestor closure и conservative association сохраняются:
выброшенная definition inventory не разрешает resolved/missing. Emitted ranges ограничены
original block witnesses, не trimmed summary chars. Text/raw/нового store/DDL нет.

Author80focused/23new/strict3 и peer80+3 public controls green. До freeze найдены и исправлены
nested typed-slot и iterator aggregate-cap bypass; старые NoneJSON/result IDs/Word AST exact.
Writer/adapter/parser/cache/SQL не меняются. PRESENTnewpayload несовместим со старым reader:
будущий writer ждёт actual deployment. Этот этап сам ничего не сохраняет и не объявляет
historical origins доступными в прежних данных.

DA3.3 audit: legacy DOC читает text+footnote+endnote stories; refmark removal/reference-table
mapping не дают полной связи с вызовом. Возможная невидимость перемещения вызова для linear
text — code-derived observation, не новый corpus/legal verdict. ODTstatus hash/counts не
восстанавливают graph. Remaining gaps не закрыты переименованием unsupported profile.

Final gate:7574tests/272.025s/92%,mypy660/Ruff728;745source/config pins exact. J196 exact CI/relay verified. Staged archive docs gate перед commit.

[Qualification](../assets/audits/2026-10-30-odt-note-prefix-reader/qualification.json),
[peer](../assets/audits/2026-10-30-odt-note-prefix-reader/peer-closure.json),
[integration](../assets/audits/2026-10-30-odt-note-prefix-reader/integration.json),
[gap audit](../assets/audits/2026-10-30-odt-note-prefix-reader/report.md),
[prefix contract](../assets/audits/2026-10-30-odt-note-prefix-reader/odt-prefix-contract.md),
[J196 exact CI/relay](../assets/audits/2026-10-30-odt-note-status-producer/release.json).


<a id="j198"></a>

## J198 — inert frozen-primary-context source resolver

ContentSourceAudit различает known/unknown/invalid, ограничен1ID/256B и имеет explicit scope
frozen_primary_context/analysis. Два indexed read-only accessor читают exact consumed provider/
batch/custom item с frozen-generation projection receipt и максимум2 observation по owner/
purpose/selection. Нет current-Bill/catalog/URL/SHA lookup, owner-history scan или first-match
при ambiguity. Frozen paid record/request/input binding, prepared receipt, selection и owner
corroborated; amendment-source coverage этим не подтверждается.

Peer воспроизвёл temporal backfill: observation после queue, но до collection ошибочно давала
known. RED→GREEN фикс требует original frozen queue cutoff. В primary BatchItemMeta timestamp
раньше вообще отсутствовал: BatchIntent.created_at не копировался в item. Добавлен только
optional omitted-None typed queued_at READER; from_intent/producer не меняются. Поэтому
CURRENTNATURAL primary chains остаются unknown, положительные stamped fixtures — explicitly
synthetic future metadata. Missing/naive queue unknown; queue после collection invalid;
observation после queue unknown. RawJSON sidechannel/collection/submission/now fallback нет.

Author135focused/44resolver cases/strict5 и peer originalregression+44cases green. У source
истории нет нового writer, renderer, DDL или capture activation; SQL/cancellation errors
propagate. Controlled audit6SELECT/207B, SQL/gzip delta0, cleanup/restore unchanged. OldNone
metadata bytes/payload/receipt parity доказаны отдельно. Known contradictions не выдаются
за отсутствующие данные. DA7.5 остаётся partial: queue capture и content-ref writer впереди.

Root source5exact; J197prefixreader, J196defaultODTcapture и J194applications preserved.
Это metadata reader/audit: corpus extraction/selection/normalization/comparison не меняется,
новых raw/paid/production прогонов нет.

Final gate:7618tests/272.215s/92%,mypy662/Ruff730;747source/config pins exact. Staged archive docs gate перед commit.

[Qualification](../assets/audits/2026-10-30-content-source-resolver/qualification.json),
[peer](../assets/audits/2026-10-30-content-source-resolver/peer-closure.json),
[integration](../assets/audits/2026-10-30-content-source-resolver/integration.json),
[causal seams](../assets/audits/2026-10-30-content-source-resolver/causal-seams.json),
[legacy parity](../assets/audits/2026-10-30-content-source-resolver/legacy-proof.json),
[read-only query proof](../assets/audits/2026-10-30-content-source-resolver/query-proof.json).

[J197 exact CI/relay](../assets/audits/2026-10-30-odt-note-prefix-reader/release.json) verified before next push.


<a id="j199"></a>

## J199 — bounded ODT note-origin prefix writer

После actual deploy J197 включён pure-helper writer: original capture/body corroborated один
раз, затем сохраняется longest global event prefix в пределах aggregate4KiB ASCII и whole
ExtractionResult16KiB. Части посещаются в physical order и остаются на исходных summary slots.
Original paragraph/heading block bounds, ancestor closure, entry-before-call и conservative
association сохраняются. Удалённая definition inventory больше не доказывает resolved/missing.
Status hash/counts относятся к original capture, prefix имеет собственные hash/coverage.
Если полезный prefix не помещается или optional enrichment падает с Exception, прежний
validated status result остаётся; cancellation propagates.

Legacy physical manifest исключает новый prefix. Adapter/parser/SQL/cache/body/selection/
provider input не меняются; старые cached results остаются прежними. Writer не перечитывает
XML и не добавляет raw storage. Required old validation выполнена до optional enrichment.

Author103focused/7new и peer118six-file+4 public controls green; source3 exact. Final replay:
6natural roots/8ODT leaves и9derivatives/9leaves, business/input drift0, INDEX exact. Natural
selected1status/0notes и variants8statuses/0notes не дают положительной natural coverage.
Controlled actual-native1note: result3372→4441B (+1069B), prefix1047ASCII B, oldphysicalhash
exact. Это JSON metadata measurement, не SQL/gzip/performance или production-size claim.
Raw replay reports остаются в out; versioned recipe/pins/compact proof проверены. Stale
handoff.next с pending superseded actual final proofs/peer closure, immutable handoff сохранён.

Final gate:7625tests/267.475s/92%,mypy663/Ruff731;748source/config pins exact. DA3.3 остаётся
partial: legacyDOC reference/story provenance и остальные acceptance gaps не закрыты.
Staged archive docs gate перед commit; exact J198 CI/actual relay verified before next push.

[Qualification](../assets/audits/2026-10-30-odt-note-prefix-producer/qualification.json),
[peer](../assets/audits/2026-10-30-odt-note-prefix-producer/peer-closure.json),
[integration](../assets/audits/2026-10-30-odt-note-prefix-producer/integration.json),
[corpus proof](../assets/audits/2026-10-30-odt-note-prefix-producer/corpus-proof.json),
[replay recipe](../assets/audits/2026-10-30-odt-note-prefix-producer/recipe.json),
[controlled bytes](../assets/audits/2026-10-30-odt-note-prefix-producer/metadata-delta.json).

[J198 exact CI/relay](../assets/audits/2026-10-30-content-source-resolver/release.json) verified.

[J199 exact CI/relay](../assets/audits/2026-10-30-odt-note-prefix-producer/release.json) verified.


<a id="j200"></a>

## J200 — prospective primary queue-witness capture

После actual deploy J198reader только BatchIntent.from_request для новых analysis/reanalysis
копирует supplied aware created_at в BatchItemMeta.queued_at. Conflicting prestamp rejected;
одинаковый stamp допустим. Нет now/submission/collection fallback или переноса при load.
Input binding сохраняется вместе с metadata; request/payload/SHA, receipt/selection IDs,
memo keys и paid identities прежние. Остальной batch-module AST и from_intent неизменны.

Existing pending/consumed None остаются None; старые JSON bytes exact. Retry/restore берут
original metadata без prepare. Newly stamped batch_job.since намеренно показывает original
queue time; legacy operational fallback сохраняется. Actual automatic workers1/4 и /analyze
controls проверяют source finalization перед queue на caller thread. Если supplied timestamp
раньше observation, он не исправляется: resolver возвращает unknown.

145author/145peer focused,11new cases, strict4/Ruff/format green. Definite-rejection retry,
consumed cleanup/restore и immutable paid/receipt проверены. Matched synthetic copies:
SQL+35B на новую metadata row, gzip+7pending/+5submitted. После удаления только нового stamp
все table values совпадают; existing paid/legacy rows не меняются. Это не natural delivery,
fleet growth или source-reference capture claim. Recipe требует isolated baseline checkout,
seven frozen source overlays и preserved external driver с exact hash; missing input fails closed.

Source4 exact поверх J199; остальные source/config pins прежние. Services/Container/Clock и
ContentVersion source writer не меняются. DA7.5 partial: следующий writer требует prospective
admission/scope, durable invalid disposition и sharedparent restore/doctor qualification.
Existing versions/lazy historical parents не получают backfill; primary-context ID не
подтверждает amendment-source coverage. Контракт следующего этапа сохранён, код не включён.

Final gate:7636tests/268.995s/92%,mypy664/Ruff732;749source/config pins exact. J199 exact
CI/actualrelay verified before push. Staged archive docs gate перед commit.

[Qualification](../assets/audits/2026-10-30-primary-queue-witness/qualification.json),
[peer](../assets/audits/2026-10-30-primary-queue-witness/peer-closure.json),
[integration](../assets/audits/2026-10-30-primary-queue-witness/integration.json),
[matched delta](../assets/audits/2026-10-30-primary-queue-witness/delta-proof.json),
[reproduction recipe](../assets/audits/2026-10-30-primary-queue-witness/recipe.json),
[next writer contract](../assets/audits/2026-10-30-primary-queue-witness/next-writer-contract.md).


<a id="j201"></a>

## J201 — scoped editorial source reader and rollback-aware delivery block

Optional ContentVersion/summary source_scope=frozen_primary_context/analysis omitted при None:
старые JSON/content IDs/public render exact. Scoped metadata допускает максимум1canonicalID.
Parent save/get/scratch restore/doctor используют один pure frozen-chain policy и shared SQL
row readers. Stored positive проверяет точный сохранённый observation PK и original queue
cutoff. Admission остаётся отдельной unique-query policy: последующая observation не делает
старую корректную ссылку невалидной. Current Bill/operational text/payload не нужны.
Source scope описывает primary analysis context, не amendment-source coverage.

Compatible reader уже уважает будущий Publication.error=source_context_invalid при writerOFF.
Одна pure membership closure используется сервисами и Repo. Durable root/group preflight до
freeze/pending/API/checkpoint/мутаций блокирует stale caller objects и generic clearing;
ordinary errors/unknown/attempt semantics прежние. SQL upsert/mark/resolve/group/digest paths
сохраняют reserved block. Это необходимо для rollback: одного parse-only reader недостаточно.
Marker producer и scope/source-ID writer не включены; новых таблиц нет.

Независимое ревью до freeze нашло actual RED→GREEN: continuation child пропускал root send;
blocked digest owner мог подтвердить clean members; direct Repo resolve UNKNOWN child мог
изменить child/resolution под blocked root. У sitting-only computed publication_ids уже
закрывал обход; explicit sitting_group union — defense mixed/masked fields, не ещё один RED.
Original findings/corrections сохранены во внешнем review evidence.

199author/199peer focused +3independent public controls green, source15 exact. Synthetic
365-day cleanup/restore и later-observation control сохраняют scoped positive; это выполненный
тест, который дополняет прежний lifetime audit с code-inference365 и tests200/181days.
Fresh isolated replay cached actual8022state: canonical dump/all48tables/schema,487typedBills
и3readonlydoctors exactfrozenf232baseline;4legacy workwarnings прежние, sourceSQL unchanged.
Это legacy compatibility, не naturalpositive/sourcewriter/delivery acceptance.

Final gate:7662tests/274.450s/92%,mypy669/Ruff737;754source/config pins exact. J200 exact
CI/actualrelay verified beforepush. Staged archive docs gate перед commit. DA7.5/DA10.2 partial;
следующий writer требует отдельной prospective admission/validated recovery qualification и
actual reader deployment. No backfill/newstorage/backenddecision/paid/prod processing.

[Qualification](../assets/audits/2026-10-31-scoped-content-source-reader/qualification.json),
[peer](../assets/audits/2026-10-31-scoped-content-source-reader/peer-closure.json),
[integration](../assets/audits/2026-10-31-scoped-content-source-reader/integration.json),
[legacy parity](../assets/audits/2026-10-31-scoped-content-source-reader/legacy-parity.json),
[actual-state proof](../assets/audits/2026-10-31-scoped-content-source-reader/actual-state-proof.json),
[recipe](../assets/audits/2026-10-31-scoped-content-source-reader/recipe.json),
[J200 exact CI/relay](../assets/audits/2026-10-30-primary-queue-witness/release.json).


<a id="j202"></a>

## J202 — explicit post-projection witness reader and accounting equivalence

Actual native World выявил два разных смысла generation: immutable paid/item и receipt
хранят input/pre-apply generation; save_analysis может изменить counter до frozen Bill.
Арифметика +1 или reinterpretation старых receipts не используются. ResultApplication теперь
читает optional omitted-None projected_generation (strict integer>=0), только applied primary
projection без explicit target. Original generation/observed/key/JSON при None прежние.
Producer нового witness выключен; будущий apply должен сохранить реально прочитанный selected
после save в той же transaction, не дополняя старые immutable application keys.

Source lookup проверяет exact projected target при наличии witness, иначе старый exact target.
Column/JSON/retained validated primary checks общие для save/scratch restore/doctor. Original
item.meta.generation==paid.generation остаётся строгим. Indexed result+owner query ограничен2
rows, не сканирует owner history. Эквивалентные applied receipts того же immutable result,
owner и effective target подтверждают одну origin; они не делают storedpositive неоднозначным.
Public saved+bound/get/restore/doctor RED старого8-кандидата и GREEN9 сохранены. Uniqueness
SourceObservation admission — отдельная политика, здесь не ослаблена.

Второй фикс: primary_record_matches уже разрешает paid accounting или четыре нуля reuse;
дополнительная full equality ошибочно возвращала nonfresh_projection. Теперь только четыре
PRIMARY_USAGE_FIELDS исключены из extra equality; source URL/kind/selection/check time/text/
law/revision и прочие поля exact. Native original39 RED→GREEN показывает честный переход к
UNKNOWN consumed_projection_missing; KNOWN без нового post-save witness не заявляется.
Wrong nonzero usage invalid, changed observation unknown; snapshots/paid не редактировались.

126author/126independent isolatedpeer green, source9 exact. Ошибочный reviewer-env запуск
с PRIMARY editable imports сохранён как INVALID, не candidate defect; повторный explicit
candidate-v2 PYTHONPATH/cwd подтверждён. Cachedactual8022 isolated restore/dump/487typedBills/
3doctors exactf232baseline,4oldworkwarnings прежние. Это legacy compatibility; будущие positive
witness fixtures synthetic. Runtime WIP сохранён отдельно и не вошёл в этот stage.

Final gate:7684tests/271.537s/92%,mypy671/Ruff739;756source/config pins exact. J201 exact
CI/actualrelay verified beforepush. Staged archive docs gate перед commit. J203 producer и
runtime push ждут actual deployment этого reader; noDDL/newstorage/backfill/paid/prod runs.

[Qualification](../assets/audits/2026-10-31-projection-witness-reader/qualification.json),
[peer](../assets/audits/2026-10-31-projection-witness-reader/peer-closure.json),
[integration](../assets/audits/2026-10-31-projection-witness-reader/integration.json),
[query plan](../assets/audits/2026-10-31-projection-witness-reader/query-plan.json),
[actual-state proof](../assets/audits/2026-10-31-projection-witness-reader/actual-state-proof.json),
[recipe](../assets/audits/2026-10-31-projection-witness-reader/recipe.json),
[J201 exact CI/relay](../assets/audits/2026-10-31-scoped-content-source-reader/release.json).


<a id="j203"></a>

## J203 — prospective frozen-primary sources and audited known-only recovery

После actual deploy J202 apply_analysis сохраняет реально прочитанный selected.afterSave
projected_generation в projection atomic. Original input/before generation, observed generation
и immutable key прежние. Exact indexed existing-key lookup сохраняет старый None без enrichment;
matching witness idempotent, conflicting witness откатывает apply. Другие helpers не получают
выдуманный post-state. New native World теперь проходит source→queue→paid→post-save witness→
fresh render как KNOWN без изменения paid/snapshots; J202 UNKNOWN не выдаётся за этот результат.

Capture включён только fresh-render QUEUED/0 supported printed single-message без existing
version. Atomic freeze_parts+semantic snapshots+audit+scoped version/binding предотвращает
rendered retry bypass. Known stores1ID, valid in-scope unknown0; rendered/SENT/lazy parents/
unsupported contexts не backfill. Scope относится к primary context, не amendment input.
Invalid откатывает frame/version/binding и сохраняет queued source_context_invalid без attempt;
paid/application work/target receipts остаются. Unexpected SQL/cancellation не маскируются.

Existing /delivery ID retry имеет отдельный known-only recovery: Repo повторно проверяет
stored owner/plan/frozen chain, фиксирует HTML/snapshots/version/binding/resolution audit и
снимает marker LAST в той же atomic. Unknown/invalid/unsupported после invalid не очищают
противоречие. False-after-write исправлен typed rollback внутри outer transaction; rejected/
SQL-failed recovery сохраняет dump. Generic clearing guard остаётся. Recovery после reset
current Bill использует original context; поздняя доставка не удваивает version/audit.

Initial12 source:155author/155independent isolatedpeer green. Paired actualfake113b/new12:
SQL+162B/gzip+82B, body/providerpayload/paid/snapshot IDs/normalizedhistory exact; появился1ID
и actual post1 вместо None. Это controlled lifecycle, не production/fleet growth measurement.
Explicit old-producer fixture проверяет omitted witness; historical provenance не создаётся.

Первый Rootfull:23failed/7673passed/7696total. До commit исправлены реальные compatibility gaps:
secondary/opaque fixture transforms теперь явно удаляют primary-only witness при прежних
assertions; doctor больше не скрывает failed_result_applied под invalid_application и отдельно
отмечает invalid_primary_projection_witness. Historical source chain не требует вечного
physical publication: missing target — прежний warning; known wrong owner/kind всё ещё error.
Новые tests проверяют get/list и wrong-owner/kind. Изолированный whole-dump restore после
ручного удаления root всё ещё отказывается из-за отдельного existing revision-work invariant;
он не ослаблен ради history-doctor теста. Исходный full и три узких fix handoff сохранены.

Postfix86focused green; final full7699tests/270.509s/92%,mypy672/Ruff740;757source/config pins
exact, final17owned source paths. Postfixactual8022 readonly restore/dump/487typedBills/3doctors
exactf232baseline,4oldworkwarnings/sourceSQL unchanged. J202 CI/actualrelay verified beforepush;
staged archive docs gate перед commit. No newmodel/table/backend/raw/paid/prod processing.
DA7.5/other program tasks остаются partial; remaining source families/kinds не объявлены принятыми.

[Qualification](../assets/audits/2026-10-31-content-source-runtime/qualification.json),
[initial independent peer](../assets/audits/2026-10-31-content-source-runtime/peer-closure.json),
[fix integration](../assets/audits/2026-10-31-content-source-runtime/fix-integration.json),
[doctor fix](../assets/audits/2026-10-31-content-source-runtime/doctor-fix.json),
[history fix](../assets/audits/2026-10-31-content-source-runtime/history-fix.json),
[fixture fix](../assets/audits/2026-10-31-content-source-runtime/fixtures-fix.json),
[delta](../assets/audits/2026-10-31-content-source-runtime/delta-proof.json),
[actual-state proof](../assets/audits/2026-10-31-content-source-runtime/actual-state-proof.json),
[recipe](../assets/audits/2026-10-31-content-source-runtime/recipe.json),
[J202 exact CI/relay](../assets/audits/2026-10-31-projection-witness-reader/release.json).


<a id="j204"></a>

## J204 — corpus DOC profile and reproducible next-step handoff

J203 exact CI/actual relay подтверждён. Native source capture/known-only recovery не заменяет
natural delivery/legal acceptance и не закрывает остальные purpose/kind profiles.

Сохранены результаты одного bounded DOC FIB-header pass: J121 manifest417cases включает404
DOC path associations и13XML исключённых здесь. DOC paths имеют246uniqueSHA;245OLE/402paths
все baseC1/effective0112,count183,cswNew5. Strict effective97-only candidates0: такой первый
producer был бы мёртвым на этом корпусе.69unique/117paths объявляют377footnotes,max21/file;
endnote declarations0.2paths разделяют1non-OLE SHA. Старое402 — OLE path observations,
не unique raw inputs. INDEX и404inputSHA проверены до/после, sourcefiles unchanged.

Header pass ограничен counted FIB<=1630B плюс OLE directory/FAT metadata; текст/Table payload
не декодировались. Это declarations/profile coverage, не доказательство CP/link/CFSpec/CR
или юридической применимости. Big metadata reports/driver остаются externalout с hashes;
versioned compactsummary/handoff/specsources и corrected<=120word contract доступны рядом.
Microsoft profile chain2007→2003→2002→2000→97prefix подтверждён primary sources.

Следующий DA3.3 срез: compatible reader для bounded PLCF-declared CP links effective2007
countedFIB97prefix, затем отдельный opt-in same-pass producer с corpus parity. Ref/Txt tables
и исходные16-bitCP units, numbering flags, N+2Txt layout; CFSpec/CHPX и emitted/Python/OLE
mapping явно unknown.69footnote-bearing entries позволяют naturalpositive qualification;
endnote и non-BMP evidence не выдумываются. Default text/selection/fingerprint change требует
отдельной corpus qualification и не объединяется с provenance transfer.

Новые код/парсер/backend/rawarchive не добавлены. Source/config757pins exact J203; повторный
full gate для docs-only не нужен, staged archive docs gate перед commit. Из actual plan
пересчитано74tasks:39complete,6explicitNA,29applicableopen. Цель не выполнена и не сужена.
Все drafts/out/stashes сохранены. При5%remaining завершить ближайший verifiedstage и поставить
goal paused по explicit user reserve rule; backenddecision/human labels не запрашиваются вновь.

[Profile summary](../assets/audits/2026-10-31-doc-profile-handoff/summary.json),
[header handoff](../assets/audits/2026-10-31-doc-profile-handoff/handoff.json),
[report](../assets/audits/2026-10-31-doc-profile-handoff/report.md),
[corrected2007 contract](../assets/audits/2026-10-31-doc-profile-handoff/contract2007.md),
[primary sources](../assets/audits/2026-10-31-doc-profile-handoff/sources.json),
[2007 source delta](../assets/audits/2026-10-31-doc-profile-handoff/sources-delta.json),
[qualification](../assets/audits/2026-10-31-doc-profile-handoff/qualification.json),
[J203 exact CI/relay](../assets/audits/2026-10-31-content-source-runtime/release.json).


<a id="j205"></a>

## J205 — bounded declared DOC note CP reader

4 November 2026. Baseline `901f8ca1`; DA3.3 remains partial. New inert reader validates
the measured effective2007 counted FIB and inherited Ref/Txt PLCFs, retains source CPs
and numbering flags, rejects malformed/over-limit declarations, and marks results
`declared_cp_only`. Default extraction, selection, model inputs and SQL writers stay unchanged.

Offline replay:404 paths/246 unique SHA,245 OLE accepted,one non-OLE SHA/two paths excluded;
69 note-bearing inputs/377 declared footnote links,zero endnotes. All405 raw/INDEX paths
stable. This does not prove raw markers/terminalCR/CFSpec or emitted offsets. The first
note boundary zero is an explicit supported-profile restriction. The replay CLI reuses
shared corpus read/network/write guards and hash/artifact helpers. No data download or dump growth.

Independent review found no blocker. First full gate exposed one unstable RCL identity
test: shared DOCX/ZIP fixtures used wall-clock member times. Fixed only the fixture to
use fixed ZipInfo metadata; original identity assertions retained. A deterministic RED
and controlled clocks two seconds apart demonstrate old differing SHA/new identical SHA.
Final gate:7761 tests/287.98s/92%,mypy674,Ruff743;742 source fingerprints unchanged during
the final gate. Explicit strict mypy also includes the new replay tool. Staged-archive
docs gate precedes commit; exact pushed CI/relay proof is recorded separately afterward.

Plan remains74 total/39 complete/6 explicitNA/29 applicable open. Next: opt-in source-character
qualification through existing parsed pieces, no new text decode/OLE read, CFSpec still unknown.

[Qualification](../assets/audits/2026-11-04-doc-note-reader/qualification.json),
[report and reproduction](../assets/audits/2026-11-04-doc-note-reader/report.md),
[controlled clock proof](../assets/audits/2026-11-04-doc-note-reader/fixture-clock-proof.json).


<a id="j206"></a>

## J206 — opt-in DOC source-character qualification

4 November 2026. Baseline `fa0fb7a7`; DA3.3 remains partial. Actual OLE SHA binds an
immutable result; the streams-only API invents no file identity. One OLE read/one legacy
render reuses CLX/pieces for bounded note checks. Validates raw automatic0x02, terminalCR,
UTF16CP coverage, CLX shape and cbMac; physical FC reuse is allowed. Malformed optional
metadata preserves text; unexpected errors/cancellation propagate. CFSpec remains unknown.

404paths/246SHA:245 OLE qualified,69note-bearing/377footnote links,one non-OLE SHA/two
paths unavailable. All405 raw/INDEX paths unchanged. Pinned old/default/opt-in text identical;
independent raw PLC/code-unit checks confirm377links. Five in-memory natural-note derivatives
change date/amount/negation/scope/reference and all survive unchanged through old/new decoding.
Natural endnotes absent; synthetic endnotes/nonBMP/mixed pieces/custom refs covered.
No note bodies/raw derivatives are retained; no default note writer/new SQL fields/tables.
Existing code fingerprints change normally, historical evidence is not rewritten.

Peer found a replay manifest path bypass into allowed project files; requirecorpus membership
beforeIO plus absolute/relative RED controls fixes it. Unit tests also caught olefile treating
short bytes as filenames: the replay signature check now uses BytesIO. Final92 focused
tests pass;7786 full tests/371.26s/92%,mypy677/Ruff746. Frozen source/config746 pins
unchanged; independent audit driver is versioned under tools and included in the default strict gate.
Two RED/green controls also fix the old production byte-signature boundary: short document
bytes no longer probe a local filename; valid corpus text remains identical. Staged-archive
docs gate precedes commit; exact pushed CI/relay is checked separately afterward.
Plan remains74/39 complete/6 explicitNA/29 applicable open. Next: physical-leaf integration
and bounded retained diagnostics, with reader deployed before any default writer.

[Qualification](../assets/audits/2026-11-04-doc-note-source-checks/qualification.json),
[report and reproduction](../assets/audits/2026-11-04-doc-note-source-checks/report.md),
[independent proof](../assets/audits/2026-11-04-doc-note-source-checks/independent-source-proof.json).


J205 release snapshot while preparing J206: `fa0fb7a7` was pushed; every recorded job step
is successful, but GitHub still reports the job/run in progress and no deploy event.
This is not a verified relay release; no rerun or forced deployment was requested.
J206 is independently compatible with default note storage still disabled; subsequent
verification must name the actual latest pushed SHA.
[Pending release](../assets/audits/2026-11-04-doc-note-reader/release-pending.json),
[observed steps](../assets/audits/2026-11-04-doc-note-reader/observed-job-steps.json).


<a id="j207"></a>

## J207 — DOC physical binding, bounded reader and safe extraction restore

4 November 2026. Baseline `9533c551`; DA3.3 remains partial. Pure models/public exports,
opt-in physical wrapper and summary helper bind native DOC captures to exact leaf SHA,
text SHA and index; custom extractors keep their behavior. Legacy body/emitted coordinates
stay unlocated; CFSpec unknown. Optional None is omitted; old physical manifest unchanged.
Bounds:64 transient sidecars,32 retained links/leaf,4096 aggregate bytes within16384 whole
metadata. Full/prefix hashes differ for a proper prefix; deterministic trimming preserves
all old selected summaries/omission counts. Default note writer remains OFF.

404paths/246SHA replay:245 native captures,69 note-bearing files,377 captured/retainedlinks,
max3200 DOC bytes/6005 whole metadata; physical/selection/text/prior fields exact. All405
source/INDEX paths stable. Independent raw checks confirm377links and5meaningful mutations;
a nested package with duplicate real DOC leaves preserves actual bindings. No natural
endnotes or human legal labels claimed; no raw derivatives/note contents retained.

Future-row SQL tests found scratch restore omitted unreferenced ExtractionResult validation.
Shared row reader now validates JSON/ID/artifact columns in lookup, RCL parents, scratch
restore after migrations, and source doctor. Two known/unavailable cases roundtrip old+new
rows; corrupted hash rejects before target mutation, and doctor reports the bad unlinked
row while counting the valid row. No migration, rewriting or new parent policy.
Cached8022 snapshot: canonical SQL/487typedBills/40legacyextractionrows/three olddoctors
exact; expanded source doctor0errors. This proves old compatibility, not production capture.

Final7809 tests/284.44s/92%,mypy680/Ruff749;749 source/config pins unchanged. Initial green
pre-export gate preserved; final gate includes public model exports. Focused model/SQL/doctor/
replay checks and independent peer review pass. Staged-archive docs precede commit.
Plan remains74/39complete/6explicitNA/29applicableopen. Next default activation is prepared
separately and waits for actual deployed reader; no paid/production processing submitted.

J206 `9533c551` actual CI+relay is now proven: transition901f8ca1→9533c551,45shealth,timer.
Two workflow_run deployments shared the default-branch headSHA: the earlier run actually
referred to fa0/skip and predates exactCI. A frozen independently tested verifier candidate
excludes only such chronologically impossible runs before ambiguity; all relay/main guards
remain. Committed CLI fix plus three regressions are prepared for the next stage.

[Qualification](../assets/audits/2026-11-04-doc-note-physical-reader/qualification.json),
[report/reproduction](../assets/audits/2026-11-04-doc-note-physical-reader/report.md),
[cached state](../assets/audits/2026-11-04-doc-note-physical-reader/cached-state-proof.json),
[J206 release](../assets/audits/2026-11-04-doc-note-source-checks/release.json),
[release selection qualification](../assets/audits/2026-11-04-doc-note-source-checks/release-qualification.json),
[actual deployment metadata](../assets/audits/2026-11-04-doc-note-source-checks/release-selection-evidence.json).


<a id="j208"></a>

## J208 — default bounded DOC metadata and exact release selection

4 November 2026. Actual reader `d1277f57` CI+relay verified before integration. Default
provenance captures native DOC notes in its existing walk and adds the qualified summary
after selection/prior diagnostics. One OLE read; custom/invalid input contracts unchanged.
New notes yield to old summaries within4KiBaggregate/16KiBwhole; no raw/note text store,
new tables, flags, backfill or paid purpose. DA3.3 remains partial for other declared gaps.

Pinned J207 gzip/reference manifest/path+SHA/population checks pass:404paths/246SHA,245native
captures/69note-bearing/377links allretained,3200maxDOC/6005whole bytes;405source/INDEXpaths
unchanged. Four historical semantic digests exact; current default equals bounded opt-in.
Independent377rawlinks/5meaningfulmutations/same natural nested package pass. Full old
component/version JSON equality is not claimed. CFSpec/body/emitted maps/endnotes unknown.

A new single native CFB scenario crosses actual World/Container with fake backend: queued
requests, real local user prompt, memo/customID match; a same-byte alias adds no queue.
Independent control also matches all batch metadata except prepared receipt; its extraction
ref changes intentionally. No real provider call. Versioned growth tool reproduces one
controlled new record:JSON/SQL+757B,gzip+245B,9semanticfields exact; not a fleet estimate.

Fresh origin/state105de577 (4Nov11:33UTC) restored schema65:496typedBills/65extractions/
224publications/122batchitems. DirectSQL vsrepo canonical data/schema exact;4doctors0errors,
4workwarnings codes/refs exactcachedbaseline. Source andDB unchanged afterreads. Allowlisted
flags checked without secrets/changes:paid reviewsfalse,maintenanceoff,attentionon/daily;
dailyworkflow unchanged. This extends DA10.5 current release evidence; future/source-family
acceptance remains separate. No production processing or paid experiment was run.

Release verifier now excludes chronologically impossible deployment runs before ambiguity:
workflow_run headSHA may name defaultbranch instead of upstream CI. Three regressions cover
stale+eligible/inclusivecutoff,2eligible stillambiguous,malformeddatefailclosed. Actual
transition/45shealth/timer/exactCI/finalmain guards remain. No workflow reruns/forced deploy.

Final7827 tests/313.75s/92%,mypy682/Ruff751;751source/config pins exact. Prior7826green preserved
before adding the meaningful native scenario; corpusproduction/replay pins unchanged, only
the extra test changed. Focused checks/peer review and staged-archive docs pass beforecommit.
Plan remains74total/39complete/6explicitNA/29applicableopen. Next: exact writer release proof
and bounded ODT acceptance/current release-gate completion audit; no new reader cycle without
observed material.

[Qualification](../assets/audits/2026-11-04-doc-note-default/qualification.json),
[report and commands](../assets/audits/2026-11-04-doc-note-default/report.md),
[native pipeline](../assets/audits/2026-11-04-doc-note-default/native-pipeline-proof.json),
[growth](../assets/audits/2026-11-04-doc-note-default/growth.json),
[fresh state](../assets/audits/2026-11-04-doc-note-default/fresh-state.json),
[allowlisted flags](../assets/audits/2026-11-04-doc-note-default/allowlisted-flags.json),
[actual reader release](../assets/audits/2026-11-04-doc-note-physical-reader/release.json).


<a id="j209"></a>

## J209 — reproducible ODT context acceptance and current release gate

4 November 2026. Typed `tools.audit_odt_context` reuses the corpus manifest, read-only
guard and existing independent XML/path checks. Original download-root selection and
standalone qualification use the same native format adapters and exact observed bytes.
No download, provider call, body retention, new reader/writer or storage decision.

Pinned six original roots contain eight ODT leaves. Only one ODT leaf is selected in its
original context; zero selected originals contain notes. Four standalone leaves contain
and retain four notes/four embedded calls. Independent XML paths/targets/block bounds
pass; separate references and independent plaintext emitted spans remain unobserved.
Standalone positives therefore do not close DA3.3. Seven corpus/INDEX inputs unchanged.

Independent review found that missing sidecars could silently reduce the checked cohort.
The audit now requires exact physical/sidecar index coverage and a nonempty ODT cohort;
negative controls reject missing companions and empty populations before publishing.
The initial full gate was deliberately interrupted for this review fix; its log remains.

DA10.5 closes for the current release profile: writer1d7e44a5 passed exact CI41880229720
and actual relay41881921373, transitiond127→1d7/45shealth/timer. Post-release state tip
105de577, allowlisted flags and daily workflow remain identical to the fresh isolated
restore qualification. No new production feature or paid processing was enabled.
DA10.6/10.7 remain separate natural-cycle acceptance requirements.

Final7834 tests/289.09s; strict mypy/Ruff/format pass.
753 source/config pins exact; root CLI reproduces the frozen corpus outcome.
Staged-archive docs gate precedes commit.

Plan:74total/40complete/6explicitNA/28applicableopen.

[ODT qualification](../assets/audits/2026-11-04-odt-context-acceptance/qualification.json),
[report/reproduction](../assets/audits/2026-11-04-odt-context-acceptance/report.md),
[current release gate](../assets/audits/2026-11-04-odt-context-acceptance/current-release-acceptance.json),
[actual writer release](../assets/audits/2026-11-04-doc-note-default/release.json).


<a id="j210"></a>

## J210 — saved LLM queue ages in the existing doctor

4 November 2026. The explicit-clock read-only work doctor adds `llm_work_ages` without
changing existing owner groups or the no-clock report. Queued/submitting intents use
creation time; physical unconsumed items use submission time. Exact saved ownership
suppresses overlapping intents; distinct retry batches remain separate physical items.
Schema gaps, orphan providers, unknown statuses and conflicting ownership stay visible.
Missing/naive/future clocks remain unknown. Ended/failed groups report total submitted
age, not time since provider completion or local-phase start. No new writer or scheduler.

Independent review caught a legacy-only request-shape assumption before publication.
Typed BatchRequest decoding now supports current question.kind and legacy call_kind/ctx,
including the exact request customID. Real current/legacy serialization and conflict controls
pass. Initial local full gate was interrupted for this fix; the log is preserved.
The next full gate exposed one additional existing opt-in shape assertion in observed-latency
tests; it now permits the new explicit-clock sibling while retaining default-report and
DB-byte equality. The failed7843pass/1assertion report is preserved before the final gate.

Pinned restored schema65:57batches/122consumeditems/0intents; no active natural phase.
Existing audit equality and unchanged DB SHA prove compatibility/read-only behavior,
while active phase claims rest on fixtures. 53 focused checks and independent review.
No extraction/selection/comparison change, corpus text replay inapplicable.

Final7844 tests/300.34s/92%, strict mypy/Ruff/format pass;
753 source/config pins unchanged. No new scenario; existing doctor assertion extended.
Staged-archive docs gate precedes commit.

J209 f6d64720 actual CI41884765323 and relay41885949974 verified: transition1d7→f6d,
45shealth/timer. Plan remains74/40complete/6explicitNA/28applicableopen; DA10.3 partial.

[Qualification](../assets/audits/2026-11-04-llm-queue-ages/qualification.json),
[report/reproduction](../assets/audits/2026-11-04-llm-queue-ages/report.md),
[isolated state](../assets/audits/2026-11-04-llm-queue-ages/fresh-state-proof.json),
[actual J209 release](../assets/audits/2026-11-04-odt-context-acceptance/release.json).


<a id="j211"></a>

## J211 — compatible retained revision listing before cleanup

4 November 2026. `list_amendment_revisions` can read operationally pruned publications
through their immutable content binding. Version identity/reason/root and physical owner
are checked; indexed/JSON root disagreement cannot silently redirect the listing. Inline
legacy behavior and descending order remain. Binding admission shares its validation helper.

Cleanup stays OFF: the blanket amendments_revision retention exclusion remains byte-for-byte
in effect. Its later writer must wait for this reader's actual CI and relay release.
Existing completed-work lifecycle test now asserts revision-list identity after manual
payload removal and restore, with existing invalid-binding controls. No new scenario.
User preference for unit tests and necessary-only scenarios is recorded in AGENTS.md.

Pinned schema65 state105de577 restored in memory:2originalroots/3legacyrevisions exact
SQL-derived IDs/order; canonical dump021b0a00 unchanged. No natural pruned revision claimed.
No extraction/selection/comparison change; text corpus replay inapplicable.
Final7844 tests/291.09s/92%, strict mypy/Ruff/format pass;
753 source/config pins exact. Staged-archive docs gate precedes commit.
Plan remains74/40complete/6explicitNA/28applicableopen.

[Qualification](../assets/audits/2026-11-04-revision-retention-reader/qualification.json),
[report](../assets/audits/2026-11-04-revision-retention-reader/report.md),
[isolated state](../assets/audits/2026-11-04-revision-retention-reader/fresh-state-proof.json).


<a id="j212"></a>

## J212 — expire duplicate operational payloads of proven completed revisions

4 November 2026. Expired SENT amendment revisions require validated rendered immutable
content/snapshots, one completed application work and its paid result/projection receipt.
After clearing delivery_json, shared work validation checks retained history in the same
transaction. Known corruption aborts all cleanup; missing modern proof stays protected.
Existing uncertainty, recency, work, digest and continuation guards remain. IDs, content,
physical attempts, accounting, work and receipts are retained. No new store or migration.

User unit-first requirement:18unit cases use only typed models/public repo APIs, without
World/service/scenario imports. One cross-service lifecycle scenario checks the actual
fake batch/application/send, prune, history, restore/doctors and next correction. 65focused
checks; runtime and compatible reader patches unchanged during the test reorganization.

Pinned state105de577 restored in memory with a deliberately future cutoff:all3legacy
revisions retain exact rows/payload/listing. Existing ordinary cleanup changes157payloads/
126memos/1batchpayload; mixed SQL25,787,073→13,490,612B is not attributed to the new policy
or claimed as production savings. No natural modern revision cleanup observed. No paid
call, production processing or state maintenance performed.

Final7863 tests/272.09s/92%, strict mypy/Ruff/format pass;
755 source/config pins unchanged. Staged-archive docs gate precedes commit.

Reader62f028fa exact CI41890685475 and actual relay41891779419 verified
before writer publication:transition110→62f/45shealth/timer. Local fake/copy qualification
ran during that CI; no writer publication occurred before the reader proof. DA6.6 and plan counts remain partial/unchanged:
74total/40complete/6explicitNA/28applicableopen.

[Qualification](../assets/audits/2026-11-04-revision-retention-cleanup/qualification.json),
[report](../assets/audits/2026-11-04-revision-retention-cleanup/report.md),
[legacy protection](../assets/audits/2026-11-04-revision-retention-cleanup/fresh-legacy-protection.json).


<a id="j213"></a>

## J213 — bounded offline primary-claim evidence and chosen claim classes

4 November 2026. DA9.1 chooses proposal/scope/commencement/before-after/decision classes
with distinct source requirements. First pure offline validator addresses key_changes[index]
and binds exact claim/source text SHA, document identity/project_text role and existing
EvidenceCitation. Common matcher is extracted once; review wrapper behavior is unchanged.
Analysis/AnalysisRecord, prompts, live provider schemas, reader payload and DB remain unchanged.

Missing legacy/source, changed hash/index/role, duplicate identity and quote ambiguity are
explicit. All normalized overlapping matches counted in constant extra memory; literal
Python offsets only when unique. Partial sources remain partial. Semantic support always
unassessed, block/paid origins unmapped; irrelevant true quote does not prove the claim.
No human labels, paid experiment, source archive or automatic re-analysis.

Bounded versioned corpus CLI pins manifest/old review SHA, checks other review globals by
AST before compiling only the trusted old function, and uses read-only CorpusReads guards.
Root replay:404paths/246SHA,233eligible (232unique/1ambiguous),12noArt/1unreadable;558exact
old/wrapper/helper comparisons.325absent derivatives:date147/amount1/negation77/scope13/
reference87; ineligible cases explicit. Synthetic role/context/claim around natural text
is technical coverage only; no natural primary-citation or legal classification claim.
All405raw/INDEX inputs and source pins unchanged. No bodies/quotes retained in reports.

Root adds full baseline commit identity and output-symlink refusal before IO;68focused
unit tests/strict5filemypy/Ruff pass. Independent core/tool review has no blockers.
No new scenario. DA9.2/9.3 remain partial; DA9.4/9.5 remain open.
Final7925 tests/275.31s/92%, strict mypy/Ruff/format pass;
759 source/config pins exact. Staged-archive docs gate precedes commit.
Plan:74total/41complete/6explicitNA/27applicableopen.

J212 db2e55c0 exact CI41892331310 and relay41893129276 verified, transition62f→db2/
45shealth/timer; no production cleanup or natural savings claimed.

[Scope](../reviews/2026-11-04-primary-analysis-evidence.md),
[qualification](../assets/audits/2026-11-04-primary-claim-evidence/qualification.json),
[corpus summary](../assets/audits/2026-11-04-primary-claim-evidence/corpus-summary.json),
[report/reproduction](../assets/audits/2026-11-04-primary-claim-evidence/report.md),
[actual J212 release](../assets/audits/2026-11-04-revision-retention-cleanup/release.json).


<a id="j214"></a>

## J214 — actionable stop conditions and audited recovery ordering

4 November 2026. DA10.8 now has a canonical operations runbook for all seven conditions:
missing required input; wrong edition/date/decision; orphan pending; inconsistent refs/tip;
checkpoint bypass; duplicate delivery; duplicate accounting. Each maps consumer/actual stop,
evidence, targeted replay and restart criterion. Purpose flags only where already supported;
otherwise quiesce the common writer, including queued jobs. Preserve in-flight pending/
unknown/submitting identities; cancellation does not prove no external effect or checkpoint.

Root review corrected recovery-before-enable ambiguity: prepare decisions while stopped,
qualify safe runnable work/inbox first, then enable the supported writer and audit recovery.
No fictional recovery-only mode, unknown auto-retry or old-dump rollback. If no scoped pause
or qualified guard isolates the consumer, remain stopped. Independent procedural review passes.

45 existing targeted composition/CAS/accounting/restore regressions pass. No new tests.
Same759source/config pins as J213; reuse its7925test/mypy690/Ruff759/92% gate for this doc-only
change. Strict staged docs gate and browser nav/overview/ToC verification precede commit.
No production workflow/flag/state/provider/Telegram action or incident drill performed.
J213 9fca1780 exact CI41895728623 and actual relay41896460287 verified,
transitiondb2→9fc/45shealth/timer.

DA10.8 completion is its specified procedural contract, not universal legal-error detection
or actual production incident acceptance. Plan74total/42complete/6explicitNA/26applicableopen.

[Runbook](../operations/architecture-incidents.md),
[requirement evidence](../assets/audits/2026-11-04-architecture-stop-contract/acceptance.json).


<a id="j215"></a>

## J215 — bounded natural primary writer acceptance

4 November 2026. DA10.6 accepts the actual guarded J187 CURRENT/root-primary-memo writer,
not J094measurement or J106reader. Verified release 26e8d01f plus eight saved modern refs
from four natural post-release bound results prove activation. Cohort selection is a
complete census by deployment time/purpose/binding through pin 105de577, with no score,
publication or compaction-success filter:one analysis/three reanalyses, three RCL/one print.

The original fourteen paired PUBLIC saves on older state412e are actually preserved:
baseline/candidate DB,SQL,public/physical/slot outcomes and exact driver pins were found.
Independent reviewer and root verified qualification hash, public equality/digest and
writer lineage:four AST definitions exact since deployment, both projection modules
match qualification pins. This is separate same-writer comparative evidence; no natural
per-item production counterfactual is invented or paid shadow run performed.

Actual queues:122 items/0 unconsumed, 4 document waiting, 0 doctor errors/4 unchanged warnings;
read-only DB unchanged. One explicit prepared-input receipt, three missing source-selection
bindings; six frozen publications have result refs without independent delivery proof.
READY/foreign/frozen/below-minscore natural cases unobserved/excluded, not accepted.
DA10.7/provenance/semantic gates remain separate. No production or provider action.

Doc-only evidence stage uses unchanged J213 759 source/config gate:7925 tests/mypy690/Ruff759;
strict staged docs gate precedes commit. Plan:74 total/43 complete/6 explicit NA/25 applicable open.
J214 5a06b96b exact CI41898429488 and relay41899542963 verified,
transition9fc→5a0/45shealth/timer.

[Acceptance](../assets/audits/2026-11-04-primary-writer-rollout/acceptance.json),
[report/limits/recheck](../assets/audits/2026-11-04-primary-writer-rollout/report.md),
[exhaustive natural matrix](../assets/audits/2026-11-04-primary-writer-rollout/natural-cohort.json).


<a id="j216"></a>

## J216 — compatible supplement text input receipt reader

4 November2026. A private common model owns unchanged primary fields/maps/8KiB/32segment
limits. Supplement adds explicit profile/context encoding; Digest metadata optional field
omits None. No writer, pure builder, analysis producer, prompt/output schema or migration
changes. Block/physical origins unmapped; unknown/prefix support unchanged.

Actual queued supplement question/frozen payload/context/text are checked without union
fallback. Submitted items require the independently saved request SHA; the sole queued/
submitting reader explicitly retains its actual-frozen-request validation instead. Normal
SQL readers, restore and doctor share guards. Existing primary checksum policy unchanged.
Peer review checked all callsites after root-requested submitted checksum hardening.

78 focused units, fixed old primary JSON hash and literal legacy metadata bytes pass.
Future mapped/unknown receipts, tamper/wrong purpose/scan rejection, prefix bounds and
consumed cleanup/dump/restore covered through public repo APIs; no new scenario.
Pinned state105de577:double restore/canonical dump exact,122typedbatchmetadata/1oldprimary
receipt,4doctors0errors/same4warnings; no new supplement receipts, DB unchanged byreads.

No extraction/selection/normalization algorithm changes; reader-only corpus scope is not
producer acceptance. Writer B must qualify exact source/context/payload/projection on
corpus and wait for actual deployed reader before publication.
Final7951 tests/425.40s/92%, strict mypy/Ruff/format pass;
760 source/config pins exact. Staged-archive docs gate precedes commit.
J215 bb1f3bfa exact CI41900688672 and relay41901855613 verified,
transition5a0→bb1/45shealth/timer.
Plan remains74total/43complete/6explicitNA/25applicableopen.

[Qualification](../assets/audits/2026-11-04-supplement-receipt-reader/qualification.json),
[report](../assets/audits/2026-11-04-supplement-receipt-reader/report.md),
[legacy state](../assets/audits/2026-11-04-supplement-receipt-reader/legacy-state-proof.json).


<a id="j217"></a>

## J217 — bounded prospective supplement input capture

4 November2026. After actual J216 reader deployment, single-loose-text supplement requests
can retain the exact captured source/selection/extraction and emitted-text map. One existing
prepare is followed by ordinary validated intent, optional enrichment and the original save.
Unsupported/ambiguous parents, scans/multipart and uncorroborated changed prepared text remain
unstamped. Metadata failures preserve the request; cancellation and existing preparation errors
propagate. Common builder retains primary serialization/checks. No extra IO or provider calls.

Independent text review found no runtime blockers. Root added the replay tool to strict mypy;
its reused str/list variable was renamed and both corpus sides rerun with the exact same script.
101 focused units;23 new unit cases/no scenarios. Public service/repo APIs cover frozen request,
known parents, failure/cancellation, submit/collect/cleanup/restore and post-prepare text changes.

Root corpus:26paired cases/zero text,context,actualOpenAIpayload,customID,memo,projection changes;
receipts0→26.16natural=8eligibleofficialPDF×2loaderbudgets,10synthetic meaningful controls.
All21corpusinputs stable;3official-title exclusions. Controlled prior analysis, not historical
paid reconstruction. Scans/all-purpose profiles/provider fitting not accepted by this sample.
Fixture growth1245–1608SQLbytes/207–615gzipbytes each; not a production growth forecast.

Full gate:7974 tests/305.254s/92%,mypy693/Ruff/format green;762source/configpins exact.
Strict staged-archive docs gate precedes commit.
J216 a266030d CI41904051778 and relay41905295694 exact verified,45shealth/timer;
reader A or compatible successor remains minimum rollback. No raw archive/backend/migration/
prompt/model-output/reader-payload change. DA5 remains partial;74total/43complete/6NA/25open.

[Qualification](../assets/audits/2026-11-04-supplement-input-writer/qualification.json),
[report and recheck](../assets/audits/2026-11-04-supplement-input-writer/report.md),
[independent review](../assets/audits/2026-11-04-supplement-input-writer/peer-review.json).


<a id="j218"></a>

## J218 — literal claim retention in supplied frozen primary context

4 November2026. A pure offline bridge reruns existing key_changes evidence checks, then
revalidates the supplied primary intent, record/request/context and selected bill part.
Raw file/text/URL/length and actual selected role must agree; a relabelled justification
cannot become normative source evidence. Missing selected fragments are never replaced
by a matching substring in the full extraction. Existing primary_claims output is unchanged.

All source segments round-trip against both supplied selected text and frozen context.
Unique continuous source-backed quote→retained; complete map without that span→omitted
(not all characters absent); prefix, missing bindings, normalized nonliteral spans and
ambiguous positions→unknown. Generated copies/gaps prove no source continuity. Nothing
claims semantic support, submission/payment, result ownership, blocks or physical origins.

41new units/no scenarios;103focused pass. Independent frozen-source review checked role/
owner/request/source-slice guards. Root corpus preserves exact404DOCpaths/246SHAs,405pins,
all old observations/counts,558paritychecks and325meaningful five-class negatives.
Separate11synthetic input controls over one deterministic realDOC:2retained/4omitted/5unknown,
independent slices; no corpus-wide map, legal-role or historical paid-input acceptance.

Full gate:8015tests/294.139s/92%,mypy695/Ruff/formatgreen;764source/configpins exact.
Strict staged-archive docs gate precedes commit.
J217 1c3468e4 CI41907475432/relay41908646627 exact verified,transitiona266→1c34/45shealth/timer.
No runtime consumer/schema/persistence/prompt/reader-payload or backend change.
DA5.4/DA9.3 remainpartial;74total/43complete/6explicitNA/25applicableopen.

[Contract](../reviews/2026-11-04-primary-analysis-evidence.md#frozen-context-retention),
[qualification](../assets/audits/2026-11-04-primary-claim-input/qualification.json),
[report and recheck](../assets/audits/2026-11-04-primary-claim-input/report.md).


<a id="j219"></a>

## J219 — opt-in recorded selection-state coverage

4 November2026. Source-history doctor can report states for every persisted observation
row, not bills/attempts/runs/latest selections. Shared immutable selections count per link.
All rows remain in the denominator; attribution requires shared-reader JSON/SQL identities.
Unverified stays distinct from unknown; corrupt/missing parents remain unattributed.
Group fractions explicitly use the global denominator. Empty available tables have zero
counts/undefined fractions; unavailable tables/columns have unknown totals and fractions.

Only the opt-in path skips a malformed source-history loop with a bounded finding; other
audits and default bytes are preserved. Required-column preflight has one owner. No reasons
are inferred from prose, no unsaved attempt history/clocks or current blockers are invented.
No writer, migration, runtime, raw archive/backend change. Valid legacy decoder behavior
is preserved; historical mismatched identities remain unknown.

21newunits/no scenarios;47focused pass including fixed prior default-output SHA, allstates,
shared selections, missing/corrupt/SQL-mismatched parents, malformed populated schema,
empty/unavailable, direct connection configuration, tool CLI flag and unchanged DB.
Independent frozen-source review found no blockers. Root state105de577:42observations/
36selections;weighted12selected/29unverified/1waiting,0unknown,8groups. Default doctor exact,
0errors;DB unchanged. This fixed historical pin is not today's production-tip claim.

Fullgate:8036tests/303.187s/92%,mypy696/Ruff/formatgreen;765source/configpins exact.
Strict staged-archive docs gate precedes commit.
J218 45ba8314 exact CI41909679495/relay41910813576 verified;45shealth/timer.
DA10.3 remainspartial: reasonclassification/fullcoverage,hostedlatency/naturalgrowth stillopen.
Plan74total/43complete/6explicitNA/25applicableopen.

[Contract/recheck](../assets/audits/2026-11-04-selection-state-coverage/report.md),
[qualification](../assets/audits/2026-11-04-selection-state-coverage/qualification.json),
[pinned proof](../assets/audits/2026-11-04-selection-state-coverage/pinned-state-proof.json).


<a id="j220"></a>

## J220 — compatible archive-member disposition reader

4 November2026. Inactive optional section extends existing bounded candidate traces.
Physical walk observations and policy decisions are distinct stages. Known ordinal paths
require actual root hash; a transient physical slot alone leaves association unknown.
No filenames-as-identity, new capture ID/FK or inferred source/admission relation.
Known root/occurrence/leaf/text facts cannot contradict across stages of one outer attempt.

Complete retention checks canonical whole-row hash; prefix/omitted summaries retain honest
counts/hash, unknown claims neither. Walk completion and unvisited descendants stay separate.
8rows/depth4/1KiBsection/4KiBwholetrace. Complete outer checks attempted phase/index and known
root agreement; omitted outer retains summary only. Legacy None normal/compact JSON, IDs,
factory and singleton remain exact. No extractor/selection/producer/migration changes.

47newunits/no scenarios;61focused pass. Frozen-source independent review clear after knownroot/
crossstage consistency and failed-outer guards. Root fixedstate105:8legacytraces/8links exact
JSON/IDs/columns and publicget/list;2restores preserve canonical021b0a00. Ordinarydoctor exact
0errors;DBunchanged. Doctor parity is not candidate-corruption coverage; shared row reader/
opt-in doctor follows before B. No extraction corpus acceptance claimed for inactive reader.

B1 excludes bare singleton before sourceobs and unaddressed rootpreheader failure; bodyfacts
stay existingphysicalhistory. Unknown bytes do not proveunsupported format. Outerread_failure
admits onlywalk metadata, without claimingpolicyneverran before later summarizerfailure.
Losingouter archives and singleton suppression remain producerqualification requirements.

Fullgate:8083tests/295.360s/92%,mypy697/Ruff/formatgreen;766source/configpins exact.
Strict staged-archive docs gate precedes commit.
J219268a9111 exactCI41912327357/relay41913125377 verified,45shealth/timer.
ReaderAactualdeploy required beforeproducerB; boundedarchivecontract doesnotcompleteDA4.1/4.4.
Plan74total/43complete/6explicitNA/25applicableopen.

[Qualification](../assets/audits/2026-11-04-archive-disposition-reader/qualification.json),
[report/limits](../assets/audits/2026-11-04-archive-disposition-reader/report.md),
[legacy proof](../assets/audits/2026-11-04-archive-disposition-reader/legacy-state-proof.json).


<a id="j221"></a>

## J221 — shared candidate-trace row decoding and optional doctor

4 November2026. One public SQLite row decoder validates candidate model/JSON and traceID/
coverage/count columns for get/list/scratchrestore. Existing restore FK check remains.
Bounded errors hide stored payload. No new table, identity, policy, writer or migration.

Opt-in source-history doctor --candidate-traces distinguishes trace rows and observation
links, unavailable schema from empty, unknown parent checks from corrupt known parents.
Source observations use existing shared SQL/JSON guards; refs are bounded row IDs only.
No currentowner, requested-URL equality, legalrole, paidinput or rawbody claims. Default
output stays exact; malformed source-history loops are skipped only when opt-in, other
audits retained. Existing selection coverage remains independent and unchanged.

17newunits/no scenarios;125focused include prior archive/source/candidate/selection checks.
Root added one reviewed nested-A control: SQL observedrowcount tamper→get/list/restore reject,
restoreatomic,doctor invalid_candidate_trace, noURLs/body,DBunchanged. Frozen runtime hashes
and root unit independently reviewed clear. Root fixedstate105:8traces/8links valid0findings;
ordinarydoctor exact0errors and42-observationselectionview exact;DBhash unchanged.

Fullgate:8100tests/299.549s/92%,mypy699/Ruff/formatgreen;768source/configpins exact.
Strict staged-archive docs gate precedes commit.
J22002b9b098 exactCI41916342426/relay41917088658 verified,45shealth/timer.
DA10.2 remains partial; saved identity/referentialpresence is not full provenance acceptance.
Plan74total/43complete/6explicitNA/25applicableopen.

[Contract/recheck](../assets/audits/2026-11-04-candidate-trace-doctor/report.md),
[qualification](../assets/audits/2026-11-04-candidate-trace-doctor/qualification.json),
[pinned proof](../assets/audits/2026-11-04-candidate-trace-doctor/pinned-state-proof.json).


<a id="j222"></a>

## J222 — frozen public-path producer-OFF archive baseline

4 November2026. Versioned native TextLoader/source/physical replay fixes driver/cohort/
INDEX/options/raw/source pins before writer instrumentation.16ZIP14,377,628bytes/17natural
requests,34returnedoutcomes;4deterministicsynthetic roots/8outcomes (duplicate names,nested/
unknown,sixwrappers,CRC). All42archive sectionsabsent confirmsproducerOFF. Natural16downloads/
54cachehits,synthetic4/12;SAMEloader cachedA afterinterveningB exercised by artifact events.

Core retains selectedtext/document/file/parts/state/rejected,source capture,IO/cacheorder,
nonrecipephysical/extraction metadata and options. Only explicit code-origin IDs/hash fields
are separate; genuine recipes are not called identical. Singleton normalization explicitly
reapplies old suppression; future extra persisted traces are not hidden as unchangedcounts.

Independentreview foundfuture-row verifier gap in v1. Correctedv2 requires actualphysicalleaf
for suppliedwalkslot and policyassociations; positiveleaf/forgedslot/nonleaf units added.
Sixnewunits/no scenarios;reviewclear. Root uses SAMEfinaldriver4eea367f/baselineeefa8dca;
natural/synthetic cores exact,all17corpusinputs stable. v1 superseded, notqualification.

No futureproducer evidence exists in baseline; retainedassociation branches need actualB
qualification.16boundedZIPs exclude1787others;cachedRCLreplay is separate,notaZIPcensus.
No legalgold,paid/provider/network/production/corpuswrites/rawstore/backend change. Member/
expanded/size/cancellation controls and SQLgrowth/dedup remain B requirements; B1exclusions
and unknown origins explicit. Driver adds no runtime instrumentation.

Fullgate:8106tests/286.614s/92%,mypy701/Ruff/formatgreen;770source/configpins exact.
Strict staged-archive docs gate precedes commit.
J221243c9427 exactCI41917902539/relay41918816406 verified,45shealth/timer.
DA4.1/DA4.4 remainpartial;plan74total/43complete/6explicitNA/25applicableopen.

[Qualification](../assets/audits/2026-11-04-archive-replay-baseline/qualification.json),
[report/recheck](../assets/audits/2026-11-04-archive-replay-baseline/report.md),
[versioned cohort](../assets/audits/2026-11-04-archive-replay-baseline/cohort.json).


<a id="j223"></a>

## J223 — compatible no-main-candidate reason reader

4 November2026. Writer branch review found a precise vocabulary gap: annexes excluded on
actual if-not-candidates cannot be labelled family_exclusion when no family was chosen.
Reader adds only no_bill_candidate policy reason, before any producer can emit it.
No writeractivation, selection/trim/policy behavior, schema/default/identity change.

One new public-model section/trace roundtrip unit/no scenarios;62focused pass including
old literal JSON/ID/factory tests. Independent two-file review clear. Root fixedstate105:
8legacyrows JSON/IDs/columns exact,ordinarydoctor exact,candidate doctor8valid0findings,
DBunchanged. This is reader compatibility, not actual runtime branch acceptance.

Before source change root cachedRCLreference fixed4276stagecases/7599readinputs and1383
missinginputs;complete_population=false. Independentworker matched results/counts/inputs/
missing/exclusions. It supports future writer parity, not raw archive/gold coverage.
No extra download/provider/production/rawstore/backend operation.

Fullgate:8107tests/288.615s/92%,mypy701/Ruff/formatgreen;770source/configpins exact.
Strict staged-archive docs gate precedes commit.
J222167945a2 exactCI41920639871/relay41921777283 verified,45shealth/timer.
ReaderA2 actualdeploy gates newliteral publication; runtimeB1 remains separately qualified.
DA4.1/DA4.4 partial;plan74total/43complete/6explicitNA/25applicableopen.

[Qualification](../assets/audits/2026-11-04-no-bill-candidate-reader/qualification.json),
[report/limits](../assets/audits/2026-11-04-no-bill-candidate-reader/report.md),
[legacy proof](../assets/audits/2026-11-04-no-bill-candidate-reader/legacy-proof.json).

<a id="j224"></a>

## J224 — bounded native archive member decision writer

5 November 2026. Default native loader records actual policy and walk reasons against exact
invocation-owned artifact/ordinal/physical-slot facts. Private cache sidecar preserves identity
across cache hits and intervening reads. Public read overrides remain effective. Optional
metadata failures preserve ordinary traces; source errors and cancellation keep their contracts.
No extraction-cache payload/recipe option, selection heuristic or source text change.

Canonical choice-first priority v1 retains main/annex/support, other policy, then walk in
original slot/ordinal order. This fixes an initial rejected qualification whose entire prefix
was walk-only. Full count/hash validates omitted tail; failed roots stay walk-only. Section
1KiB/eight rows/path depth four and whole trace4KiB remain;128KiB canonical bookkeeping budgets
are not heap ceilings. Accumulator releases losing root packets instead of retaining them.

Frozen J222 driver4eea/baselineeefa stay exact. Separate pinned independent Deflate64 ordinal
decoder checks16method9 decode events; no production selection helpers or skipped checks.
Final raw cores natural/synthetic exact:16ZIP/14,377,628bytes,17natural requests twice,
four synthetic roots twice. All78retained rows checked,66policy; natural58policy/10walk,
synthetic8policy/2walk. Unknown/depth and missing-tail coverage remain explicit.

Cached4276stage decisions/counts/7599readinputs/1383missing/exclusions match OFF reference;
complete_population=false. All existing source pins match final freeze; afterward only
qualification decoder/units/mypy target added. No claim of ZIP census or independent legal gold.
Public source-capture storage fixture21traces deduplicates/restores/doctor-validates both modes:
section-only +30,511SQL/+6,142gzip bytes, not production growth forecast. Fixedstate105 legacy
8JSON/IDs remain exact, double restore/default+candidate doctor pass, inputDB unchanged.

Unit-first: guard failures, public-read compatibility, original late ordinal main/excluded
prefix, full canonical priority/hash and independent decoder corruption/cancellation tests.
69newunit cases/no new scenarios. Full gate8176tests/281.12s/92%,mypy711/Ruff/formatgreen;780source/config pins unchanged. Initial gate with one obsolete producer-OFF assertion is superseded.
Independent root/service/priority/decoder review clear. Strict staged-archive docs gate required.
ReaderA2eb5 exactCI41923516152/relay41924265518 verified,45shealth/timer before writer release.
DA4.1/DA4.4 remain partial;74total/43complete/6explicitNA/25applicableopen.

[Report and boundaries](../assets/audits/2026-11-05-archive-disposition-writer/report.md),
[qualification](../assets/audits/2026-11-05-archive-disposition-writer/qualification.json),
[corpus proof](../assets/audits/2026-11-05-archive-disposition-writer/corpus-proof.json).

<a id="j225"></a>

## J225 — typed supplement retained-result dependencies

5 November 2026. Actual gap: generic doctor follows result IDs and request hashes but cannot
prove a typed supplement points at a supplement result. One shared adapter guard now serves
public add/save/get/list, scratch restore and doctor for status_changes.supplements_json.
Known IDs require exact retained SQL/JSON identity, validated supplement purpose/record and
compatible known request SHA. Invalid references fail before state replacement or legacy
preservation writes. bills.supplements_json is a seen-number ledger and stays untouched.

Unknown legacy IDs/checksums, original paid owner and changed delivery/display fields remain
valid. No inferred owner/answer equality, new schema/fields, payload size, selection/extraction,
provider or delivery behavior. Other secondary carriers remain outside this bounded contract.

Nine new unit cases/no new scenarios;128root focused units,mypy713/Ruff/formatgreen.
Independent four-file review clear. Root historical105 and freshly fetched7b208 state each
have12typed records/1modern reference: public values and ENTIREdoctor reports exact,0errors/
0warnings,double canonical dump/restore exact,source DB hashes unchanged. No production
processing, paid calls or writes to state/corpus. Full gate8185tests/278.705s/92%,mypy713/Ruff/formatgreen;782source/config pins exact. First fullgate4fake-IDfixture failures superseded by realretainedresult fixture and finalv2;86legacy/supplement units green. Strict staged docs gate precedes commit.
J2243a7d0667 exactCI41931909058/relay41932899218 verified;45shealth/timer proof linked with writer report.
DA10.2/DA10.7 partial;74total/43complete/6explicitNA/25applicableopen.

[Report](../assets/audits/2026-11-05-supplement-dependencies/report.md),
[qualification](../assets/audits/2026-11-05-supplement-dependencies/qualification.json),
[state proofs](../assets/audits/2026-11-05-supplement-dependencies/state-proofs.json).

<a id="j226"></a>

## J226 — shared typed secondary result dependencies

5 November 2026. Common strict computation-row decoder plus finite secondary validator replaces
supplement's duplicated implementation and protects five more typed carriers: status-change
amendments, Bill joint, consultation/commission/act-application reviews. J225 supplement API/
codes remain. This is not a global computation-reader migration or arbitrary JSON traversal.

Known modern refs require exact retained SQL/JSON identity, validated purpose/type and compatible
known SHA. Review column/review/context purposes agree; schema2 and compact schema3 both work.
Missing IDs return before new checks; legacy, foreign original paid owner, missing hashes,
zero-accounting reuse and display/context differences remain valid. No name/memo/snapshot,
current-owner or answer-equality inference. Public saves precede writes/preservation; rowmapper,
scratchrestore and doctor share checks. No schema/payload/selection/extraction/provider change.

54newunit cases/no newscenarios;140root focused,mypy716/Ruff/formatgreen. Root added deepJSON
corruption to strict duplicate-key check: bounded invalid-result finding, no doctor crash.
Independent frozen6file review and root2file delta review clear. Historical105/fresh7b208
ENTIREdoctor outputs and double canonical restore exact, DB hashes unchanged. Fresh ALL508Bills/
201StatusChanges public serialization hashes exact. Natural modern amendments1; joint/reviews
legacy, not claimed observed modern production cycles. Fullgate8239tests/276.769s/92%,mypy716/Ruff/formatgreen;785source/config pins exact. Strict staged docs gate precedes commit.
J225d94038f8 exactCI41934398481/relay41935143190 verified;45shealth/timer.
DA10.2/DA10.7 remain partial;74total/43complete/6explicitNA/25applicableopen.

[Report](../assets/audits/2026-11-05-secondary-dependencies/report.md),
[qualification](../assets/audits/2026-11-05-secondary-dependencies/qualification.json),
[state proofs](../assets/audits/2026-11-05-secondary-dependencies/state-proofs.json).

<a id="j227"></a>

## J227 — read-only paginated delivery content history

5 November 2026. /delivery ID history [PAGE] exposes intermediate immutable editorial versions
in the existing single-message amendments scope. One version/page with predecessor,date,frozen
snapshot/source/result refs, rendered SHA and physical publication/operator-decision metadata.
Caps3physical rows/latest3decisions each,2rendered canonical refs perkind; omissions explicit.
No body/URL/operator free-text/currentBill/provider/publisher or new stored payload/schema.

Original inspector functions remain AST exact; existing confirmation/integrity policy governs
availability/current content. Unknown physical outcome does not confirm editorial current.
Command branches after target-channel check and works with publish=False; ordinary /delivery
output unchanged. Read-only content inspection retains normal command bookkeeping/reply.

14newunit cases plus ONEpublic-command cleanup/unknown/publishFalse lifecycle: actualwiring
and no content/publication/resolution/batch/send/edit changes beyond units. Root155focused,
mypy718/Ruff/formatgreen. Independent9file review clear. Fresh7b208 copied-state proof1natural
root/version: page1available/page2outofrange,defaultinspection/canonicaldump/DBhash unchanged.
No natural revision-cycle claim; fixture lifecycle evidence distinct. Fullgate8254tests/281.670s/92%,mypy718/Ruff/formatgreen;787source/config pins exact. Strict staged docs precede commit.
J22606499ec2 exactCI41935643004/relay41936270553 verified;45shealth/timer.
DA7.5 partial;74total/43complete/6explicitNA/25applicableopen.

[Report](../assets/audits/2026-11-05-delivery-content-history/report.md),
[qualification](../assets/audits/2026-11-05-delivery-content-history/qualification.json),
[state proof](../assets/audits/2026-11-05-delivery-content-history/state-proof.json).

<a id="j228"></a>

## J228 — intent creation ages and shared saved-clock evaluation

5 November 2026. /status reuses existing queued/submitting lists and one injected Clock to
group intent rows by purpose/state, report known/unknown ages and oldest known owner. Creation
age is explicit; source/provider/current-state clocks are not inferred. Submitting uncertain.
Caps6rendered groups, clipped/escaped refs, omitted counts; emptygroups omitted fromJSON,
quiet status unchanged. No repository/schema/clock-field/provider/submission/retry changes.

Pure saved-clock evaluator is shared with work_doctor, retaining its four unknown codes and
non-string SQL-clock behavior. Root date-limit controls avoid UTC-conversion overflow without
losing elapsed DST-fold semantics.15newunit cases; existing status scenario extended, none
added. Root207focused,mypy721/Ruff/formatgreen; independent9file +root2file review clear.
Fresh7b208 default/explicit-clock doctor reports, quiet status/model JSON byte exact, DBhash
unchanged. This is not hosted/provider/source latency or natural full-cycle acceptance.
Fullgate8269tests/284.056s/92%,mypy721/Ruff/formatgreen;790source/config pins exact. Strict staged docs precede commit.
J227778bbd01 exactCI41936926006/relay41937838598 verified;45shealth/timer.
DA10.3 partial;74total/43complete/6explicitNA/25applicableopen.

[Report](../assets/audits/2026-11-05-status-intent-ages/report.md),
[qualification](../assets/audits/2026-11-05-status-intent-ages/qualification.json),
[state proof](../assets/audits/2026-11-05-status-intent-ages/state-proof.json).

<a id="j229"></a>

## J229 — opt-in supplement frozen delivery diagnostic

5 November 2026. computation_doctor --supplement-deliveries links applied typed supplement→
status-change target→exact frozen change/computation refs→recorded physical state. Shared
strict result/dependency guards and known request hashes, no currentBill/currentstatus contents,
answer equality or timestamp joins. Ordinary report/defaultCLI/exit behavior unchanged.

Direct single-message only. Reviewer caught digest completion's SENT source rows; destination,
digest ownership/parts, absorbed and compound flags now remain unknown/unassessed. StrictJSON
rejects duplicate identities at application/plan/change/batch edges. Root corrected non-SENT
verdict to not_confirmed (UNKNOWN is never no-delivery); exact physicalstatus preserved.
recorded_sent is local DB+messageID evidence, never remote Telegram verification. Consumed
provideritem/inputreceipt are separate; sourceedge not_assessed, no full lifecycle acceptance.

35newunit cases/no newscenarios;98root focused,mypy723/Ruff/formatgreen. Independent4file and
root2file semantic review clear. Fresh7b208 copy proof app16/change192/pub240/message152,
consumed/requestmatched, sourcekindimpact_assessment, inputreceiptunknown. ENTIREdefaultreport/
serializedbytes exact, source/copyDBunchanged. No bodies/URLs/paid/provider/send/storage writes.
Fullgate8304tests/282.877s/92%,mypy723/Ruff/formatgreen;792source/config pins exact. Strict staged docs precede commit.
J22895f98f32 exactCI41938849306/relay41939728819 verified;45shealth/timer.
DA10.2/DA10.7 partial;74total/43complete/6explicitNA/25open.

[Report](../assets/audits/2026-11-05-supplement-deliveries/report.md),
[qualification](../assets/audits/2026-11-05-supplement-deliveries/qualification.json),
[state proof](../assets/audits/2026-11-05-supplement-deliveries/state-proof.json).

<a id="j230"></a>

## J230 — compatible amendment text-input receipt reader A

5 November 2026. Internal AmendmentInputReceipt profile/context encoding reuses existing bounded
text-map builder and parent validator. Complete actual AmendmentsContext is hashed, source
segments cover only ctx.text; previoussummary/keychanges/proposal/recommendation never acquire
normative ranges. Digest metadata, exact question/profile/purpose/request/context hashes and
submitted-item checks recognize the new profile. Ordinary and correction share amendments purpose.

Primary/supplement defaults/IDs unchanged; correction target contract unchanged. Runtime writer
OFF: analysis/loading/correction services and repository/doctor source untouched. Resumed context
without original capture remains unstamped; scans/multipart/unsupported origins remain unmapped.
No provider preparation/calls, raw storage, schema or legacy provenance reconstruction.

24newunit cases/no newscenarios;125root focused,mypy724/Ruff/formatgreen. Independent6file review
clear. Fresh7b208 ENTIREdefaultdoctor, double canonical restore and DBhash exact; queued/submitting
population0/0 explicitly not modern amendment production evidence. The separate11raw originals/
22public preparation OFF baseline belongs to futureB acceptance, not activation in this reader.
Fullgate8328tests/282.881s/92%,mypy724/Ruff/formatgreen;793source/config pins exact. Strict staged docs precede commit.
J22977970851 exactCI41940853777/relay41941627412 verified;45shealth/timer.
DA5 partial;74total/43complete/6explicitNA/25applicableopen.

[Report](../assets/audits/2026-11-05-amendment-input-reader/report.md),
[qualification](../assets/audits/2026-11-05-amendment-input-reader/qualification.json),
[state proof](../assets/audits/2026-11-05-amendment-input-reader/state-proof.json).

<a id="j231"></a>

## J231 — bounded append-only committee-report cohort collection

5 November 2026. Existing term10 metadata417unique report URLs had no originals at checked exact
print/supplement paths (not global byte census). Collector freezes12URL-SHA-sorted sources with
all official owner/stage observations BEFORE GET, shares strictfetch/run/artifact helpers.
8MiB complete-file/64MiB observed-body budgets withchunkreserve,180s source-start/60s fetch/10s
socket limits, sequential0retry/0substitution. Output is new external generation; corpus/INDEX
unchanged. Fresh bytes only, no old historical-byte/legalgold claim or production rawbackend.

Actual11/12PDF2,619,560bytes;1028-A transportfailure preserved. PlanSHAe8b77b5c, manifestbc94e5d8
verified by root, metadata plan reproduces exactly, INDEX2030 unchanged; rootdoesnotrepeatGETs.
6newunit cases/0newscenarios;10focused,mypy726/Ruff/formatgreen. Root compatibilityguard rejects
committee profile in oldfiling installer before rowdecode/newrun/writes. Independent2file+
rootguard/unit review clear; collector included in defaultmypy. No installation/source mutation.

A separate OFFpreparation baseline forfuturewriter covers22paths(14text/8scan); not accepted
as an active producer here. Fullgate8334tests/284.121s/92%,mypy726/Ruff/formatgreen;795source/config pins exact. Strict staged docs precede commit.
J230ccb27d43 exactCI41942728330/relay41943488954 verified;45shealth/timer. DA0.6/DA5partial; plan74total/
43complete/6explicitNA/25applicableopen.

[Report and replay](../assets/audits/2026-11-05-committee-report-cohort/report.md),
[qualification](../assets/audits/2026-11-05-committee-report-cohort/qualification.json),
[collection proof](../assets/audits/2026-11-05-committee-report-cohort/collection-proof.json).

<a id="j232"></a>

## J232 — prospective amendment text-input receipt writer B

5 November 2026. Fresh ordinary preparation retains the actual loaded normative source until its
single request preparation. Corrections enrich the intent in the existing snapshot/target
transaction. Shared secondary hooks preserve supplement interfaces and primary behavior. Context
is fully hashed; only normative text receives source ranges. Recommendation-last cannot replace
its parent. Scans, multipart, resumed context and unsupported Senate paths remain unstamped.
No provider call, schema, raw storage or invented historical source evidence.

Frozen OFF/ON public driver covers 22 paths from 11 freshly acquired originals: unchanged core
request/context/preparation/cost/status decisions, 14 text receipts, eight unmapped scan paths,
one collection failure excluded. Controlled prior context/targets are not legal gold. Actual
metadata-only fixture growth: +1,248 SQL bytes per text receipt, gzip +417 ordinary/+402 correction;
scans zero, duplicate saves exact, both variants restore idempotently. No production forecast.

Nine new unit cases, no new scenarios; 65 root focused. Full gate: 8,343 tests, 92% coverage,
mypy 729, Ruff/format green, all 798 source/config pins exact. Pinned 7b208 copy: 508 bills and
201 changes serialize identically; full default doctor, double restore and DB hash unchanged.
Reader A ccb27d43 verified before publication. J231 069cb6f4 exact CI41944621564 and
relay41945453873 verified. DA5 remains partial; 74 total/43 complete/6 explicit N/A/25 applicable open.

[Report](../assets/audits/2026-11-05-amendment-input-writer/report.md),
[qualification](../assets/audits/2026-11-05-amendment-input-writer/qualification.json),
[corpus proof](../assets/audits/2026-11-05-amendment-input-writer/corpus-proof.json).

<a id="j233"></a>

## J233 — Senate full-text identity input bridge

5 November 2026. Reuse the actual verified Senate loaded file and source capture after unchanged
identity/budget checks. Hydrate only the exact captured selection when raw SHA, text SHA, bounds
and selection identity agree. Shared amendment profile persists prospective receipts. No second
read, source-policy change or offset inference for removed cover prefixes. Scans, prefix variants
and resumed contexts remain unstamped; cancellation and original validation failures propagate.

Root public preparation replay covers all 701 selected text inputs/39,243,213 raw bytes against
the frozen pre-code baseline: request/context/source observation/download order/preparation count
exact. All 636 identity receipt parents checked independently; 65 prefix paths remain unmapped.
The stage replay preserves 1,825 files/816 observations/4,903 mutation probes. Excluded: 23 scans,
two cases without a unique date, 90 non-amendments. Prior context is controlled fixture data;
no provider call, historical paid-intent reconstruction or independent legal gold claim.

Eleven new unit cases/no scenarios; 66 root focused. Full gate: 8,354 tests/289.60s/92%, mypy730,
Ruff/format green; all799 source/config pins exact. Pinned state: all508 bills/201 changes public
serialization, full default doctor, double restore and DB hash exact. J232 f050d68c exact
CI41946923557/relay41947818432 verified, including45s health/timer. DA5 remains partial;
74 total/43 complete/6 explicit N/A/25 applicable open.

[Report](../assets/audits/2026-11-05-senate-input-bridge/report.md),
[qualification](../assets/audits/2026-11-05-senate-input-bridge/qualification.json),
[corpus proof](../assets/audits/2026-11-05-senate-input-bridge/corpus-proof.json).

<a id="j235"></a>

## J235 — complete finite correlation ID contract

5 November 2026. Full repository inventory: 98 write/schema/restore boundaries, 37 dynamic SQL
sites, three delegated wrappers. Fifteen native boundaries cover the stated core ID contract.
Add extraction ID and actual request/receipt parents through one shared adapter helper. Request
SHA comes from the actual frozen input when known. Auxiliary metadata IDs remain typed SQL-only;
no artificial input/application ID, retroactive trace or universal SQL journal is introduced.

Collision controls: ignored intent insert logs no supplied replacement IDs; publication upsert
uses resolved ID/status and masks an unverified supplied owner; missing status rows emit no
success-shaped event. Events are local validated writes, never commit or remote delivery proof.
All305 SQL call ASTs remain exact; repository business AST exact after explicitly checked cursor
capture and two logging-only guards. Portable inventory recipe reproduces the same proof.

Initial full gate caught three NEW test failures: the existing handler filter removed explicit
None before context merge and restored unrelated IDs; a privacy assertion also captured old
loader INFO URLs. Corrected bounded known-key tombstones freeze first context across handlers;
formatters remain exact. Actual filter fixtures, nine direct controls and root41 sequential
cases cover the correction. Twenty-one new unit cases/no scenarios. Final full gate:
8375 tests/279.676s/92%, mypy731, Ruff/format green; all800 source/config pins exact.
Fresh620f092 every table row/rowid, canonical dump, entire default doctor and double restore exact.
J2337b196312 exactCI41948171201/relay41948673010 verified, including45shealth/timer.

DA10.1 accepted for its stated finite known-ID contract. Plan: 74 total/44 complete/6 explicit
N/A/24 applicable open. Source/input history and natural lifecycle acceptance remain open.

[Report](../assets/audits/2026-11-05-correlation-contract/report.md),
[qualification](../assets/audits/2026-11-05-correlation-contract/qualification.json),
[boundary inventory](../assets/audits/2026-11-05-correlation-contract/inventory.md),
[filter proof](../assets/audits/2026-11-05-correlation-contract/filter-proof.json).

<a id="j234"></a>

## J234 — compatible compact-card history reader A3

5 November 2026. Parallel stage published after J235. Exactly one schema66 migration permits
honest absence of a change event, with explicit continuation-v1 compact card context. Prior65
migration ASTs, old IDs/JSON and amendment behavior stay compatible. Shared typed row/snapshot/FK
and physical-scope checks cover repository, scratch restore, doctor and inspection; known typed
corruption is atomically rejected. Unknown/unbound/malformed/multipart/inherited operations and
numeric-string targets cannot leave a stale confirmed current version. Rehome retains old context.

Exact existing continuation projection is shared in a pure module with service re-export;
unfinished work is stripped. Its profile fixes field/default/projection semantics. Integrity,
render/physical history and full context are separate: default semantic restoration refuses compact
context while valid history survives cleanup. No fake StatusChange, new outbox or producer.
Writer OFF. Largest qualified RCL full Bill2,362,572B versus compact2,621B is a sample, not a bound.

Frozen public driverd75f5a precedes changes. Root repeats14 fixtures/3 real Bills: physical,
render, request/result and IO cores plus metadata exact; zero new model requests/results.
Twelve repeats dump-exact; two rehome repeats explicitly inapplicable. Driver in default mypy.
Runtime replay pins remain exact after three test-only fixes; no gold/natural-cycle acceptance.

Fresh620f092 copy, re-fetched before publication: every row/rowid in48tables, old versions,
snapshots/bindings, declared FKs, double restore and doctor parity independently checked.
Reader-only -5SQL/+7gzip bytes, zero new content rows, original DB unchanged. First full gate
caught two schema-header hash assumptions and one corrupt-history-import test. Original semantic
checksums remain pinned with actual-schema assertions; scenario proves atomic rejection/UNKNOWN
preservation and public optional-read failure. No runtime weakening.38newunit cases/0newscenarios/
0removed logical cases; unknown-schema boundary advances66→67. Root83focus+46affected; final
8413tests/308.878s/92%,mypy736,Ruff/formatgreen,all805source/config pins exact.
J2353aa0a1ed CI41952153634/relay41953006717 verified. DA7 partial;74total/44complete/6N/A/24open.

[Report](../assets/audits/2026-11-05-card-history-reader/report.md),
[qualification](../assets/audits/2026-11-05-card-history-reader/qualification.json),
[migration proof](../assets/audits/2026-11-05-card-history-reader/migration-proof.json),
[paired proof](../assets/audits/2026-11-05-card-history-reader/corpus-proof.json).

<a id="j237"></a>

## J237 — prospective compact card history writer B

5 November 2026. Fresh single NEW_BILL root creation/render freeze/history and single card-edit
outbox/history commit atomically before API. Revisions require confirmed bound predecessors and
exact previous physical target. Reuse continuation-v1 snapshots plus shared plan builder; actual
formatter language via read-only publisher capability. Unknown language, legacy-unbound, inherited
and multipart scopes stay unbound, without orphan snapshots or backfill. No full Bill copy,
StatusChange invention, new schema/outbox/backend or delivery/attempt/operator policy change.

First full gate19failed/8405passed uncovered a real supported deletion lifecycle incompatibility:
retained history after forget/republish needs physical availability separated from intrinsic
integrity. Reads/restore/doctor retain exact rows/IDs with unavailable physical evidence/currentNone;
new revisions/save/bind stay strict, live contradictions and snapshot/predecessor FKs still fail.
No synthetic root/tombstone or inferred historical existence. Late independent review adds strict
available-SENT predecessor admission for new lineage, while exact already-bound card retry is a
validated no-op. Intermediate corrected gate8435 passed; six further directed units precede final.

Other failing sitting/retry fixtures rewrote confirmed card receipts. Tests now freeze a distinct
synthetic historical callback through public unattempted-plan replacement, or audited dismissal +
public republish, leaving actual histories immutable. Six corpus failures were a delivery-retry
child cascade; root reran real subprocess qualification in its actual Git checkout. No skipped
checks, fabricated Git SHA, weakened baseline or new World flow.

Root exact frozen public replay14fixtures preserves physical/render/request/result/IO cores;
final metadata equals original writer measurement. Two retained versions per fixture; unsupported
controls follow the valid edit, initial exclusions separately unit-tested. Actual original+edit
metadata +18,336–29,121SQL/+3,018–5,915gzip; largest2.36MBRCL +9,254/+2,401 original-only,
+18,336/+3,018 total. Poll growth0; no cross-version reuse claimed in controls. Compatible receipt
removal preserves every nonreceipt rowid and OFF canonical dump. No production forecast/gold.
All48old-state table rows/rowids, doctor and double restore exact, no source writes/backfill.

Final8441tests/293.335s/92%,mypy738,Ruff/formatgreen, all807source/config pins exact;28newunits,
0newscenarios/removals. Root74focus+66sitting+112lifecycle/corpus+45predecessor. Actual readerA3
6fa7c906 CI41957557486/relay41958372822 verified before B publication. DA7 remains partial;
74total/44complete/6explicitN/A/24applicableopen.

[Report](../assets/audits/2026-11-05-card-history-writer/report.md),
[qualification](../assets/audits/2026-11-05-card-history-writer/qualification.json),
[measured budget](../assets/audits/2026-11-05-card-history-writer/budget.md),
[storage proof](../assets/audits/2026-11-05-card-history-writer/storage-proof.json).

<a id="j236"></a>

## J236 — compatible local timing witness reader A

5 November 2026. Parallel stage published after J237. Optional LocalTimingWitness in batch
metadata/retained results binds actual frozen request SHA, purpose and ORIGINAL input scope,
independent of current item/application owner. Ten purposes; no new enum/schema/source inference.
Old None omits itself from JSON; immutable reuse preserves original witness/None. Event pairs
explicitly distinguish unknown, local discovery/candidate/work request and fresh/cache/mixed
read completion. Read may precede trigger; enclosing known clocks are upper bounds. No source
publication/provider/delivery clock or source-coverage claim. Aware clock normalization/ISO preserve
fold/offset precision; shared clock math handles DST/extreme dates. Unsupported subminute JSON
carrier endpoints reject only new witness, old None remains permissive; actual SystemClockUTC valid.

Strict duplicate/deep JSON reader expansion is explicit across request/meta/result paths, including
timing-only records. Root preserves newer card lifecycle and snapshot identity readers; exact SQL
call ASTs and all repository methods except two read helpers +first restore preflight flag unchanged.
First gate1failed/8543passed caught domain error ordering: only FIRST restore preflight defers
retained-result scan. Intent/item checks keep order, established domain validators own their errors,
and final full scan still rejects standalone malformed/deep/future timing atomically. No parser
or expectederror weakening. Runtime capture/services/loader/Clock remain unchanged/OFF.

Root old-wire proof:79serialized hashes/13result IDs exact across10purposes+3compact review schemas.
Fresh620f092 all48table rows/rowids, full default doctor except schema/DB headers, double restore
and original/copy hashes exact; no retrospective clocks. Same-carrier technical growth queued10
+3,562SQL/+280gzip; submitted10+result10 +7,124/+309; maxwitness362JSONbytes; duplicate saves0,
double restore exact. No production forecast. Root103focus+171affected; final8549tests/
291.896s/92%,mypy739,Ruff/formatgreen, all808pins exact.108newunit cases,0newscenarios/removals.
J237c1a5a04d exactCI41963996232/relay41964819087 verified. DA10.4 remains open pending actual
all-purpose capture/retention/report qualification.74total/44complete/6explicitN/A/24applicableopen.

[Report](../assets/audits/2026-11-05-local-timing-reader/report.md),
[qualification](../assets/audits/2026-11-05-local-timing-reader/qualification.json),
[integration proof](../assets/audits/2026-11-05-local-timing-reader/integration-proof.json),
[storage proof](../assets/audits/2026-11-05-local-timing-reader/storage-proof.json).


<a id="j238"></a>

## J238 — frozen producer-OFF public preparation replay tool

5 November 2026. Tooling stage only: no runtime capture/status/schema/result transfer. Exactly18
planned cases span ten real local batch preparation purposes plus eight controls. Seventeen initial
intents/23 actual prepare_request calls; planned read failure remains0requests, no unexpected case
or provider/client/submit/synchronous model/delivery call. Active review request→advance and
explanation preparation dry-run use existing public APIs. Eight new unit cases,0newscenarios.

Three pinned natural local source bodies total3,826,880bytes; priors/targets/descriptions, blank
scan and disabled act_application context are explicitly technical, not natural/gold acceptance.
Fixed and event clocks do not infer upstream time. Paired core compares request/context/IO/status/
accounting/reuse exactly; only new witness and approved fresh derived nullable metaSHA normalize,
with known-value equality and raw None/hash retained separately. Existing BOTH-None legacy packet
and known-witness packet reuse remain full-byte exact; passed/resumed context reads stay unknown.

OFF/selfcheck precede writer edits. Root exact driver/unit replay passes18cases/all10purposes,
no unexpected failures; runtime src diff empty and810 source/config pins exact. Strict mypy tool
entry included. Root8focus and final8557tests/303.067s XML (303.15s summary)/92%coverage pass;
mypy741/Ruff/format810green, all810 source/config pins exact after gate. Root case delta proves
8newunit/0newscenario/0removed. Reader4f16442 actualCI41967036140/relay41967663979 verified.
Runtime capture/retention/report qualification and
DA10.4 remain open; no checkbox closes.

[Report](../assets/audits/2026-11-05-local-timing-off-baseline/report.md),
[qualification](../assets/audits/2026-11-05-local-timing-off-baseline/qualification.json),
[root integration proof](../assets/audits/2026-11-05-local-timing-off-baseline/integration-proof.json),
[compressed evidence](../assets/audits/2026-11-05-local-timing-off-baseline/artifacts.json).


<a id="j239"></a>

## J239 — explicit amendment application-reference reader A

5 November 2026. Reader stage only: omitted-None `amendment-projections-v1` interprets real
positive SQL `result_applications.id` values for frozen single-message amendment originals and
revisions. Existing unprofiled integers stay opaque and byte-stable; no receipt creation,
retro-enrichment, schema migration or runtime producer change. DA7.1 and DA7.5 remain partial;
prospective capture needs reader deployment followed by J242 writer B.

One shared adapter validates strict application SQL/JSON, retained result identity/purpose,
frozen change computation IDs and known request SHA, exact event/publication target and known
physical plan/binding. It compares no current Bill, answer/title/time or paid/application owner.
Retained deleted physical targets and cleaned payloads remain availability unknown; strict new
save/bind admission is unchanged. Public narrow lookup returns validated actual IDs; paginated
history renders at most two known refs and an omitted count, without presenting opaque integers
as verified SQL references.

27 new unit cases and250 focused cases pass; strict mypy8/Ruff/format green. Tests cover
original/revision/supplement, display differences, foreign/rehome, SQL/result/target/JSON
corruption, duplicates, cleanup/deleted targets, atomic restore and bounded public display.
Existing306 repository SQL call ASTs and all service source bytes remain exact. Existing fresh-b
reader proof preserves all table rowids/data, old JSON/IDs, default doctor, complete dump and
double restore. Baseline/candidate25,816,929SQL bytes and5,208,775gzip bytes are exact:0/0 reader
overhead. Modern chains here are unit fixtures; this is not prospective writer storage evidence.

Root independent review found no issues;94 focused and8584 full tests/297.169s/92%coverage
passed, strict mypy743/Ruff/format812green, all812 source/config pins exact. Root state replay
and306 SQL AST checks pass;27newunits/0newscenarios/0removed cases. Previous f7ee12c0 release
CI41970157905/relay41970627223 actually verified. This reader release verification follows publication.

[Report](../assets/audits/2026-11-05-application-reference-reader/report.md),
[qualification](../assets/audits/2026-11-05-application-reference-reader/qualification.json),
[source pins](../assets/audits/2026-11-05-application-reference-reader/source-pins.json),
[compatibility evidence](../assets/audits/2026-11-05-application-reference-reader/evidence.json).


<a id="j240"></a>

## J240 — prospective local loading and work timing capture

5 November 2026. All ten existing batch purposes receive optional timing only on newly frozen
intents; original discovery/work-request clocks and actual invocation-local fresh/cache/mixed
completion bind the frozen request and original input scope. Worker sidecars aggregate in input
order, without global latest-read lookup, extra reads or packet retention. Source availability,
publication time and complete input/source coverage are not inferred. Saved/passed contexts and
retained descriptions keep read clocks unknown; act_application production remains disabled.

Exact frozen J238 driver/recipe/OFF passes18 cases, all10 purposes and unchanged core:17 initial
packets,23 actual prepare calls,11 known-read cases (8mixed/2fresh/1cache_reuse),6 unknown-read
controls and one explicit read failure with0requests. Client/submit/synchronous-model/delivery
calls remain0. Natural evidence is three raw source files3,826,880bytes; priors/targets/descriptions,
blank scan and typed disabled-act context are technical, not natural legal labels or gold.

Fresh metadata adds only the witness and separately approved nullable request-SHA corroboration.
Legacy queued BOTH-None packets and known-witness packets remain byte-exact on reuse. ALL10
public collection units transfer the original witness on first retention, preserve stored None or
a different original witness on recollection and still reject changed paid results. Fault/cancel,
unsupported clocks, recommendation-last and workers1/4 controls pass. Original queue Clock-call,
loader-call and actual prepare_request AST lists are exact; SQL/models/ports/Container unchanged.

Author344 focused tests (291+53),20 new unit cases, strict mypy719/Ruff/format pass. Independent
exact-five-pin review is CLEAR/findings[]; reviewer did not rerun tests. Actual first-queued
snapshots17 add6,753SQL/1,100gzip witness bytes plus1,428/55 derived-SHA bytes:8,181/1,155 total
independent fixture deltas, not a production growth forecast. Existing fresh-b source/copy,
complete default doctor reports and double restore stay exact; constructor/start_run in both
modes adds0enrichment and makes0provider calls.

First root gate found62 invalid legacy/changed-binding fixture constructions and one optional
callback-stub mismatch. The approved eight-file test-only correction preserves all308 focused
and8604 full case IDs, asserts strict modern scope and prior binding guards separately, and
leaves fake loading unknown. Runtime five pins and reader/parser guards remain unchanged.
Root308 focused and8604 full tests/298.627s/92%coverage pass; mypy745/Ruff/format814green,
all814 source/config pins exact. Final18case pair, actual storage/fresh-state proofs pass again.
Twenty new units/0newscenarios/0removed. Previous4cb430d2 CI41970912310/relay41971451869 actually
verified; this release verification follows publication. J241 operator report integration remains
separate; DA10.4 stays OPEN and no task checkbox closes.

[Report](../assets/audits/2026-11-05-local-timing-writer/report.md),
[qualification](../assets/audits/2026-11-05-local-timing-writer/qualification.json),
[peer review](../assets/audits/2026-11-05-local-timing-writer/peer-review.json),
[compressed paired evidence](../assets/audits/2026-11-05-local-timing-writer/artifacts.json).


<a id="j241"></a>

## J241 — opt-in saved timing and physical-reference report v2

5 November 2026. Explicit work_doctor timing-witness mode adds a version2 latency section;
default, full observed-default and v1 outputs remain exact. Every retained-result and application
SQL row remains in its own denominator, including collection receipts and unattributed malformed
rows; physical edges have a separate application/publication denominator and known-clock counts.
Original input/paid/application owners remain distinct; current Bill/generation, titles, answer
content and timestamp proximity never manufacture a join. The shared strict application-row
decoder preserves existing J239 finite-profile wrapper behavior. No runtime writer changes occur.

All ten purposes are inventoried: nine have supported exact frozen-slot/retained-reference proof;
explanation has a durable command application but no retained physical operator-reply receipt. Group members, bidirectional digest/held
membership, full multipart chains, known-language selected names and profiled cleaned history
reuse existing proof helpers. Recorded operation is local evidence; manual confirmation does not
supply an API-send clock. Read-before-work_requested, DST folds/extremes and explicit unknown
clock/schema/physical states are preserved. Source availability remains unknown. Digest membership
uses source-publication IDs; held changes use event IDs. Revisions bind exact publication targets
with truthful absent SQL status_change_id. Eight focused peer controls reproduce three pre-fix
failures and pass after correction; J239 unknown-SHA compatibility is unchanged. Three additional
upstream-schema controls assert complete own-population and dependent null counts rather than
zero, preserving populated v2 bytes.

168 focused tests pass,57 new units/0new scenarios, strict mypy6/Ruff/format6 green. Pinned620f0923
DB74633d21 remains exact:13 retained rows across four purposes,26 applications, five exact frozen
operation-reference edges and0timing witnesses. Ten-purpose clock/nine-slot positives are typed
technical controls; no historical timing or natural legal labels are invented. Exact final independent correction review is CLEAR/findings[] without a test rerun;
Root154focused and8683full tests/298.118s/92%coverage pass, mypy751/Ruff/format820green, all820
source/config pins exact;57newunits/0newscenarios/0removed and all full case IDs retained.
FinalV5 factual review confirms existing command APPLICATION; only physical operator reply is
unrecorded. Archiveddb73 default/fullobserved/V1 bytes are exact; pinnedV2 changes only this
factual policy label, with all counts/clocks/rows/IDs unchanged. Joined native→publicofflinebatch
retention→actual primary projection→publicFakePublisher recorded NEW_BILL proves known
1200/1500/1800s trigger/read intervals and300s phase intervals. All eight source-parent tables,
original witness/paid request and full closed-copy doctor objects survive double restore.
Separate existing V1 trusted Wykaz source-clock control passes. This is technical capability,
not natural production/legal acceptance; DA10.4 remains for root decision after actual release.
Historical/source-clock unknowns do not alone block this item; DA10.7 cycles remain separate.

[Report](../assets/audits/2026-11-05-observed-timing-report/report.md),
[qualification](../assets/audits/2026-11-05-observed-timing-report/qualification.json),
[source pins](../assets/audits/2026-11-05-observed-timing-report/source-pins.json),
[pinned proof](../assets/audits/2026-11-05-observed-timing-report/pinned-proof.json),
[metadata artifacts](../assets/audits/2026-11-05-observed-timing-report/artifacts.json).


<a id="j242"></a>

## J242 — prospective amendment application-reference writer B

5 November 2026. Fresh single-message amendment original/revision freeze and the existing audited
fresh-unrendered recovery route capture only validated existing result_applications SQL IDs.
Original targets are frozen status-change IDs; revisions target the actual revision publication.
Capture commits with snapshots/version/binding/outbox before API. Missing0refs stays unprofiled
unknown; partial known refs assert no complete inventory. No receipt creation, current-Bill/owner/
answer/title/time inference, enrichment of old history or expansion of recovery authority occurs.
Legacy rendered/unprofiled/opaque reuse remains byte-exact; card and historical fallback paths stay
unchanged. Multipart/digest/group/absorbed operations cannot enter this finite profile.

OFF driver596bd286…559c93b was frozen BEFORE runtime edits, with exact repeated OFF outputs and
source/input pins. Natural evidence is one31,091-byte existing frozen3146 Bill/change/result
envelope (publication231/change189); no new raw extraction or download. Existing parent/original/
correction operations and the separate known-source primary prior for recovery are technical
controls, not natural legal gold. The envelope remains local/untracked; committed evidence holds
only its provenance and before/after SHA, not bodies.

Final same-source ON natural and recovery chains preserve ALL nonhistory table rowids/values,
application rows and snapshots, physical plans/render/outcomes and normalized history/bindings.
Repeated confirmed delivery and double restore stay exact. Measured independent fixture totals:
natural original54SQL/28gzip bytes, original+revision108/29; technical recovery54/20 and108/23.
These deltas are neither additive across phases nor a production-growth forecast. Existing fresh-b
startup/readers/default doctor/dump/old IDs/all-table rows/double restore remain exact and source
DB unchanged. Adapters/models/ports/SQL/application writers are untouched.

Author176 focused tests across11modules,21newunits (20writer+1baseline),0newscenarios; strict
mypy5/Ruff/format green. Unit-first controls cover pre-API capture, original/revision/recovery,
missing/partial/foreign/rehome refs, actual SQL contradiction, post-save fault/cancellation atomicity,
legacy reuse, multipart/digest, cleanup/deleted physical target and restore. Independent five-pin
peer review CLEAR/findings[], no independent test rerun; root static review reports no findings.
Reader prerequisite4cb430d2 actualCI41970912310/relay41971451869 is verified. Root142focused
and57 final affected cases pass; full8626tests/298.190s/92%coverage,mypy748/Ruff/format817green,
all817source/config pins exact. First gate exposed one old opaque wrong-kind fixture that now
acquired known refs. Test-only producer-OFF seeding before first freeze preserves that legacy
path; a new UNIT independently verifies modern get/doctor/atomic-restore rejection without
operational work. No reader/runtime/hash/ID weakening. Final public pair and full existing-state
proof remain exact;22newunits/0newscenarios/0removed; all prior scenario IDs preserved. Previous
a703da5f CI41972960477/relay41973777988 verified; this release verification follows publication.
DA7.1/DA7.5 remain partial outside scope.

[Report](../assets/audits/2026-11-05-application-reference-writer/report.md),
[qualification](../assets/audits/2026-11-05-application-reference-writer/qualification.json),
[peer review](../assets/audits/2026-11-05-application-reference-writer/peer-review.json),
[metadata evidence](../assets/audits/2026-11-05-application-reference-writer/artifacts.json).


<a id="j243"></a>

## J243 — government review reuse through exact carried projection receipts

5 November 2026. A finite receipt-backed bridge recovers exact government review A after the
current review becomes B and memo is cleaned: A → public pruneA before rollover → TermRollover →
paid B → public pruneB/restore → AnalysisService exact caller A. Four unit-first failures become
passing actual public-service lifecycle controls across RCL/WPL consultation/commission; exact
reuse makes zero extra preparations, charges zero repeat tokens and preserves original paid
owner/binding/result bytes. Existing orphan-memo protection remains unchanged; both memos are
not claimed removable after rollover.

Current-owner applied receipt SQL/JSON identity uses the exact J241 shared decoder3885767e…;
original schema3 ordered input, binding term, purpose and request/record identity remain separate.
Actual purpose/review-input:<64hex> is a stored work address, not a reconstructed text hash;
known purpose/document-review:<64hex> must equal the original binding reuse key. Same-owner and
Sejm matching stay strict. Known contradictions reject before prepare; missing/opaque evidence
and changed input do not grant reuse. Current producer TermRollover carries the same government
number, so known foreign-number scope rejects in this bridge only. A real second paid government
result reproduces the pre-fix hole. Different original TERM stays valid; no global receipt-owner
invariant or inferred authority for future cross-number transfer is introduced.

250 focused tests pass,18newunits/0newscenarios, strict mypy5/Ruff/format green. Independent final
five-pin review is CLEAR/findings[], with no independent test rerun. Pinned fresh DB b9f86131…:
257government owners,0eligible scoped receipts; read/dump/double restore/source DB stay exact.
No natural terminal cohort or legal gold is claimed. No schema/retention/backfill/rekey/receipt
writes or provider/network calls occur. Root55focused and8701full tests/294.878s/92%coverage pass,
mypy752/Ruff/format821green; all821source/config pins exact,18newunits/0newscenarios/0removed.
Fresh read/dump/restore and strict AST checks pass. Previousd85feff2 actualCI41980800311/relay41981573411
verified; this release follows publication. DA6.4 is
OPEN pending remaining-requirements audit, with DA6.3/DA6.6 untouched.

[Report](../assets/audits/2026-11-05-government-review-reuse/report.md),
[qualification](../assets/audits/2026-11-05-government-review-reuse/qualification.json),
[peer review](../assets/audits/2026-11-05-government-review-reuse/peer-review.json),
[metadata evidence](../assets/audits/2026-11-05-government-review-reuse/artifacts.json).

DA10.4 completion recorded after exact d85feff2 deployment: [requirement-by-requirement audit](../assets/audits/2026-11-05-government-review-reuse/timing-completion-audit.json). Native input through recorded technical delivery, separate trusted Wykaz control and unchanged V1 meet the item; DA10.7 natural/legal cycles and unrecorded best-effort operator replies are not silently imported as its requirements. Current plan:74tasks/45complete/6explicitN.A./23applicableopen.

<a id="j244"></a>

## J244 — eventual GitHub job metadata stays pending

5 November 2026. The read-only exact-release verifier no longer reports failure solely because a
completed-success workflow's job metadata remains nonterminal/null. Both CI and deploy use
pending/metadata_pending for incomplete or unavailable job metadata. Explicit non-success
completed workflow/job outcomes stay failed; malformed listings and unproven structure remain
unproven. Exact SHA/expected jobs/CI chronology/ordered transition/45-second restart/timer/final
main proof is unchanged. Existing bounded optional wait is retained; no rerun, workflow/deploy,
bot, provider, state write or new polling mechanism is introduced.

Unit-first13 regression checks fail before correction; all73 focused cases pass,31 new units and
0scenarios. Strict mypy2/Ruff/format2 green; independent exact-two-pin review CLEAR/findings[],
no independent tests/network. Offline replay reproduces the full actual db73 check-2 report
SHA1ad4dc59 before correction and changes only failed→pending/deploy_not_successful→metadata_pending
afterward. All other fields/read sequence/input bytes stay exact;0log reads/network/reruns.
This preserves historical evidence and is not remote deployment verification. A later consistent
actual observation still needs the original proof. Root73focused and8732full tests/297.433s/92%coverage
pass, mypy752/Ruff/format821green; all821source/config pins exact.31newunits/0newscenarios/0removed.
Root replay of the real saved snapshot against committed baseline confirms status/reason-only
delta, unchanged read sequence and0network/log/rerun calls. Previous2bba2c79 actualCI41983076567/
relay41983674162 verified; this release verification follows publication;
no DA10 checkbox closes.

[Report](../assets/audits/2026-11-05-release-metadata-consistency/report.md),
[qualification](../assets/audits/2026-11-05-release-metadata-consistency/qualification.json),
[peer review](../assets/audits/2026-11-05-release-metadata-consistency/peer-review.json),
[observed proof](../assets/audits/2026-11-05-release-metadata-consistency/observed-proof.json),
[metadata artifacts](../assets/audits/2026-11-05-release-metadata-consistency/artifacts.json).

DA6.4 accepted after actual2bba2c79 release: [literal requirement/source/test audit](../assets/audits/2026-11-05-release-metadata-consistency/paid-history-completion-audit.json). Memo is an index over immutable paid history, exact known reuse survives cleanup for all ten current purposes, request identity/policy keys differ and quiet source pointers do not rewrite paid results. Unknown legacy/changed or forced questions and undefined future aliases are not silently accepted. DA6.3/6.5/6.6/10.7 remain independent. Current plan:74tasks/46complete/6explicitN.A./22applicableopen.


<a id="j245"></a>

## J245 — reader A for body-free review memo result indexes

5 November 2026. A finite version1 marker pins immutable schema3 result ID/request/saved-record
hash and bounded projection metadata with paid-or-zero usage. Shared load/scratch-restore/doctor
checks establish intrinsic parent and SQL identity; actual caller ReviewContext separately proves
ordered original-term input/binding/purpose/request. Exact-ID J243 authority cannot substitute
another receipted government result. Full current Bill reviews/contexts remain untouched.
The saved-field hash excludes future unset defaults; future writer B must reload SAVED parent
before hashing. Unknown projection fields and unsupported clocks are rejected, not discarded.

Scanner ownership is an actual top-level reserved key, including escaped keys; nested keys/body
strings/Unicode alone do not infer a profile. Strict known-marker corruption checks remain.
Known BLOB markers produce bounded invalid_review_memo on load/doctor/atomic restore; prior
nonmarker duplicate/malformed error ownership stays PrimaryProjectionError/JSONDecodeError.
Six scanner unit-first failures close with eight added controls/0removed; the existing deep-case
ID now supplies a deep value beneath an actual root marker. Final360focused/44newunits/0newscenarios,
strictmypy8/Ruff/format8 pass; final exact-eight-pin peer review CLEAR/findings[], no independent
rerun. Technical act_application carriers do not enable its production purpose.

Pinned b9f86131…fc485 OFF/ON proof is exact across508Bill reads/163memo values, default doctor,
dump/double restore and source DB. Three legacy review memos492635B/context471999B have0retained
parents/natural eligible rows; no storage saving or natural modern-marker cohort is claimed.
Writer remains OFF. Memo-before-retain order, current Bill/context, paid retention, migrations and
all source SQL mutation literals remain unchanged; old inline/null values stay exact. No backfill,
raw/context store, provider/network or retention/cleanup changes occur. Root J245 gate is green:8776tests/300.66s/92%coverage, mypy756/Ruff/format825;
all825source/config pins exact. Root92focused pass; release follows publication. DA6.3 stays OPEN; already accepted DA6.4/DA10.4 and plan46/6/22 remain unchanged.

[Report](../assets/audits/2026-11-05-review-memo-reader/report.md),
[qualification](../assets/audits/2026-11-05-review-memo-reader/qualification.json),
[peer review](../assets/audits/2026-11-05-review-memo-reader/peer-review.json),
[metadata artifacts](../assets/audits/2026-11-05-review-memo-reader/artifacts.json).

Root OFF/ON repeats preserve exact dump/memo hashes and semantic doctor fields. Physical SQLite
file SHA differs also between two identical OFF runs after double restore; only that physical
hash is excluded from cross-run equality. Original source SHA stays unchanged.

<a id="j246"></a>

## J246 — qualify review memo compaction before enabling a writer

5 November 2026. Three technical controls preserve the old coarse memo key while changing
exact input: URL, document order and trailing whitespace. Original descriptors plus caller raw
text recover URL/order contexts; one whitespace variant loses an original body. Native text-v2
normalization is non-injective, including removed numeric lines, so fixed bounded reversible
metadata cannot recover arbitrary original contexts.

The pinned legacy sample has six texts/465475UTF8 bytes, zero already canonical originals and
zero retained computation parents. No natural compaction is claimed. A prospective canonical-only
profile would need explicit versioned rehydration and an original input-reference hash; it is
unimplemented and has no positive natural case here. Writer stays OFF, existing inline reuse and
retention remain unchanged, DA6.3 stays OPEN; plan46/6/22 unchanged.

[Report](../assets/audits/2026-11-05-review-memo-design/report.md),
[evidence](../assets/audits/2026-11-05-review-memo-design/evidence.json).


<a id="j247"></a>

## J247 — strict indexed identities on public computation reads

5 November 2026. Public get/list now use the existing strict computation-row decoder instead of
returning JSON without its indexed result/provider/batch/custom/owner identity. No new policy,
schema or owner inference; legacy/foreign/compact records remain valid. Owner predicate, rowid
order and purpose-filter SQL, including malformed json_extract behavior, stay unchanged. Doctor
and shared decoder source bytes are exact; its mismatching-row inventory behavior is preserved.

Twelve unit-first column/API controls fail before correction;171 focused tests pass,20newunits
and0scenarios, strict mypy3/Ruff/format3green. Six damaged-column doctor reports remain byte-exact
and fixture reads do not change DB bytes. Independent exact-three-pin review CLEAR/findings[],
no independent tests/network/edits. Root82focused/8796full tests, mypy757/Ruff/format826 and all826source/config pins pass.
Pinned13-get/13-owner-list/dump/entire-default-doctor including physical DB SHA are exact on
fresh copies. Actual release follows publication. No pipeline/provider/corpus/storage-policy change;
DA10.2 remains partial and plan46/6/22 unchanged.

[Report](../assets/audits/2026-11-05-computation-read-integrity/report.md),
[qualification](../assets/audits/2026-11-05-computation-read-integrity/qualification.json),
[peer review](../assets/audits/2026-11-05-computation-read-integrity/peer-review.json),
[source pins](../assets/audits/2026-11-05-computation-read-integrity/source-pins.json),
[change proof](../assets/audits/2026-11-05-computation-read-integrity/change-proof.json),
[doctor parity](../assets/audits/2026-11-05-computation-read-integrity/doctor-parity.json).


<a id="j248"></a>

## J248 — strict indexed identities on the public application list

5 November 2026. One list method now uses existing read_result_application instead of JSON-only
history. Indexed result/owner/generation/phase contradictions reject; WHERE/ORDER/parameters and
return shape stay exact. No new target/current-owner policy, schema, writer or abstraction.
Before enabling its existing id>0 guard, pinned raw65/migrated66 each show26 actual IDs1..26,
zero nonpositive/noninteger IDs and identical ordered-ID hash, with source SHAs unchanged.
Public writer/v50 migration preserve normal positive IDs; arbitrary nonpositive legacy IDs are
not promised compatible. The default doctor gains no positive-ID check; no new restore-validator
coverage or doctor completeness is claimed.

Five unit-first controls fail before correction;67 focused tests pass,6newunits/0scenarios,
strict mypy2/Ruff/format2green. Existing nullable/opaque/migration/name/government/primary contracts
pass. Independent exact-two-pin review CLEAR/findings[], no independent tests/network/edits.
J247/candidate replay preserves all26 applications/13 owner lists/public JSON/dump/entire default
doctor/copied physical DB/source SHA. Root56focused/8802full tests/295.45s/92%coverage, mypy758/Ruff/format827 and all827source
pins pass; independent root26-app/13-owner/IDs/dump/doctor/physical-SHA replay exact. Actual
release follows publication. This
bounded DA10.2 change closes no task; plan46/6/22 unchanged.

[Report](../assets/audits/2026-11-05-application-read-integrity/report.md),
[qualification](../assets/audits/2026-11-05-application-read-integrity/qualification.json),
[peer review](../assets/audits/2026-11-05-application-read-integrity/peer-review.json),
[ID contract](../assets/audits/2026-11-05-application-read-integrity/id-contract.json),
[state parity](../assets/audits/2026-11-05-application-read-integrity/state-parity.json),
[source pins](../assets/audits/2026-11-05-application-read-integrity/source-pins.json).

<a id="j249"></a>

## J249 — result-reference acceptance and read-only diagnostics

DA6.3 acceptance reviewed against its full literal requirements and section1.4, not only its
title. All ten supported purposes have compatible refs, collect/consumer checkpoints and
original-owner boundaries; permanent application history stores refs/outcomes without another
full context per reuse. Committed J066/J068 peak/cleaned SQL/gzip/SQLite measurements and actual
retention exit/protection conditions are pinned. Hot/pending/orphan exceptions are explicit.

The metadata recipe validates40source/test/proof pins. All14named tests executed as86passing
cases in the merged8802-test gate; independent requirement review is CLEAR. Actual b5a9d27a
CI41990416151/relay41991034474 verified. Root accepts DA6.3; plan47complete/6N/A/21open. Generic
review memo body compaction stays OFF and is not substituted for this reference-ledger contract.
Legacy import, orphan retention and natural/legal acceptance remain separate items.

[Acceptance](../assets/audits/2026-11-05-result-reference-completion/root-acceptance.json),
[audit](../assets/audits/2026-11-05-result-reference-completion/report.md),
[execution proof](../assets/audits/2026-11-05-result-reference-completion/root-execution-proof.json),
[independent review](../assets/audits/2026-11-05-result-reference-completion/peer-review.json).

The standalone opt-in batch integrity doctor reuses typed row/request models and a checkpointed
read-only snapshot. It checks both actual application key families and explicit-generation
queued/submitting/unconsumed primary work. Known staleness is advisory; consumed/history and
nonprimary scopes are not compared with current Bill generation. Missing schema yields unknown
counts. Deep/invalid payload findings contain bounded refs, never request bodies. No repair,
retry, cancellation, provider call or delivery occurs.

Two unit-first failures close; final55focused/23newunits/0newscenarios and independent V2 review
pass. A root nullable-key question was resolved by existing model/SQL/pre-v50 invariants; one
extra corrupt-layout test locks invalid2/duplicate0, without production-policy change. Existing
five doctor implementations stay byte-exact, and their full default reports compare byte-exact
on pinned b9. The new CLI observes26valid applications/0duplicates and known-empty active queues;
no natural stale/duplicate cohort is claimed. Source DB unchanged.

Root8825full tests/295.12s/92%coverage, mypy760/Ruff/format829 and all829source/config pins pass.
The exact receipt-identity and batch-cardinality coverage gaps remain explicit; DA10.2 stays
partial. Actual J249 release follows publication.

[Diagnostic report](../assets/audits/2026-11-05-batch-integrity-doctor/REPORT.md),
[coverage](../assets/audits/2026-11-05-batch-integrity-doctor/coverage.json),
[integration proof](../assets/audits/2026-11-05-batch-integrity-doctor/integration-proof.json).

<a id="j250"></a>

## J250 — exact paid receipts and retained batch populations

The explicit --batch-receipts profile checks immutable provider/batch/custom tuples and both-known
request checksums; another same-SHA item cannot supply a missing receipt. Known result purpose
must match the actual item kind, independently of SHA availability. Mixed batch headers and
foreign/current owners are not compared. Missing receipts/deficits are advisory, known structural
contradictions/excess/invalid counts are errors, unavailable schema stays unknown. All retained
items count, including consumed/forgotten ones; no automatic repair/retry/provider calls occur.

Four technical false-negative shapes were reproduced before implementation. Final77focused/
29newunits/0scenarios and exact V2 independent review pass. The purpose refinement has two
unit-first negatives and a mixed-batch positive; original V1 package is preserved. Root72focused/
8854full tests/298.13s/92%coverage, mypy762/Ruff/format831 and all831source/config pins pass.

All five older default reports and the deployed v1 profile remain byte-exact on b9; DB source
unchanged. New opt-in observes13exact/SHA-equal results and57equal batches/122items, with no
natural errors/warnings. Actual release follows publication. DA10.2 whole-requirement acceptance
is still pending; plan47complete/6N/A/21open unchanged.

[Report](../assets/audits/2026-11-05-batch-receipt-integrity/REPORT.md),
[integration proof](../assets/audits/2026-11-05-batch-receipt-integrity/integration-proof.json),
[peer review](../assets/audits/2026-11-05-batch-receipt-integrity/peer-review.json).


<a id="j251"></a>

## J251 — immutable amendment input Reader A

Dedicated migration67 adds a canonical typed text-question table and optional request-bound refs.
Shared save/get/restore/doctor checks exact identities/known parents. Availability resolves only
the root-original frozen result; later legacy-refetch revisions cannot backfill it. Original
source receipts remain lineage. Capture, correction consumer and all prior None carriers stay
unchanged; no arbitrary body cap or storage activation is introduced.

Unit-first reproduction and276focused/30newunits/0scenarios, strict mypy11/Ruff/format pass.
Final independent V2 exact-pin review is CLEAR. A fresh schema66 copy preserves all48existing tables and509public Bill reads, adds zero body rows,
and survives exact double restore. Doctor semantics differ only by schema/file SHA; schema-only
+246SQL/+91gzip bytes. Immutable source checksum stays b08843e5…77c6. These are reader proofs,
not a production storage forecast: native question subsets with controlled prior fields establish
no universal body bound. Root8884full tests/314.48s, mypy766/Ruff/format835 and all835source/config pins pass.
Independent root migration preserves48tables/509Bill JSON and exact double restore; seven doctor
profiles differ only by schema/file SHA. New migration-test connection closes explicitly; no
runtime or test-ID delta. Actual release follows publication. DA7 tasks stay open.

[Root report](../assets/audits/2026-11-05-j251-amendment-question-reader/report.md),
[integration](../assets/audits/2026-11-05-j251-amendment-question-reader/integration-proof.json),
[review](../assets/audits/2026-11-05-j251-amendment-question-reader/peer-review.json).

<a id="j252"></a>

## J252 — retained operational bodies, paid names and parent availability

An explicit read-only profile checks recognized shared legacy bodies/refs, native saved bodies
and current paid editorial name refs. Three legacy validators move to a public shared adapter
with old aliases; root composition preserves every other J251 repo definition and all constants.

Strict JSON/recognized-parent failures are errors. Decoded unproven legacy/future layouts that
cannot match current typed models are unavailable/advisory; native admission uses the explicit
raw policy stamp, never defaults. Cleaned/absent/unknown data remain distinct. Name validation
accepts actual analysis/reanalysis/name provenance, manual unbound values and permitted foreign
owners. Local continuation_parent points to publications.id; historical absence is advisory,
never a send/retry verdict. No repair, provider calls or schema/storage-policy changes.

Three retained-body/ref gaps and a parent-availability omission were reproduced before this
feature; the V2 boundary refinement has unit-first red controls. Final212focused/35newunits/
0scenarios and independent exact-pin review CLEAR. Root118focused/8919full tests/310.64s,
mypy769/Ruff/format838 and all838source/config pins pass. Seven prior reports remain byte-exact
on composed schema67; new profile0errors/0warnings and source unchanged.

Actual release and complete DA10.2 named-requirement acceptance follow; plan47/6/21 unchanged.

[Report](../assets/audits/2026-11-05-retained-state-integrity/REPORT.md),
[integration](../assets/audits/2026-11-05-retained-state-integrity/integration-proof.json),
[composition](../assets/audits/2026-11-05-retained-state-integrity/composition-proof.json),
[review](../assets/audits/2026-11-05-retained-state-integrity/peer-review.json).

J252 final V3: current and previous selected names share validation; actual public rename damaged-parent control now reports the bounded previous/0 reference. Author215focused/38newunits/0scenarios, exact-pin independent review CLEAR. Root38dedicated units/8922full/307.25s/mypy769/Ruff838 and all838source/config pins pass; seven prior report profiles exact, immutable source unchanged. DA10.2 remains open: an independently reproduced missing inverse continuation child needs an advisory availability finding. No auto repair or resend.

<a id="j254"></a>

## J254 — inverse continuation-child availability

A retained multipart root explicitly lists local publication child IDs. Deleting only its final
child was a demonstrated availability false negative even when all surviving parent edges existed.
The doctor now records a separate child-edge census and bounded advisory finding per missing slot.
Cleaned/unreadable plans remain unavailable; absent schema produces null counts. Membership checks
also handle oversized parent/child IDs without SQLite binding overflow or raw-ID disclosure.

Five new unit cases, one extended schema test and no new scenarios; author63focused and exact-pin
independent review CLEAR. Root43focused/8927full/300.87s/mypy769/Ruff838 and all838source/config
pins pass. Seven prior reports and every prior retained-state field match; immutable source unchanged.
Fresh state226publications contains2listed child edges, bothpresent;59plans are unavailable.
No runtime delivery, repository schema, auto repair or resend behavior changes.

J252 actual971aba3e CI42016266907/deploy42017158295 verified, including transition/restart45/timer.
Complete finite DA10.2 acceptance and J254 actual release remain separately recorded.

[Report](../assets/audits/2026-11-05-continuation-child-availability/REPORT.md),
[integration](../assets/audits/2026-11-05-continuation-child-availability/integration-proof.json),
[review](../assets/audits/2026-11-05-continuation-child-availability/peer-review.json).

J254 actual f9a4efa9 CI42018529750/deploy42019390877 verified: exact transition/restart45/timer. Root reviewed the complete eight-requirement/sixteen-class matrix and accepts DA10.2 for the current metadata/retained-body profile. Raw originals/backend remain deferred; unknown legacy and legal/natural-cycle boundaries remain explicit. Plan48complete/6N.A./20applicable open (74total). [Acceptance](../assets/audits/2026-11-05-continuation-child-availability/acceptance.json).

<a id="j255"></a>

## J255 — saved selection limitation census

Explicit source-doctor `--selection-limitations` counts only saved typed DocumentLimitation codes,
including missing_own_annex. Each persisted observation remains a denominator unit even if unknown;
each code counts once per row. Codes overlap and fractions need not sum to one. Explicit empty,
legacy absent, invalid/dangling, unavailable schema and known empty populations remain distinct.
No new classifier, runtime blocker, source availability or legal completeness inference is added.

Native public producer controls prove missing-annex evidence and overlapping2/3fractions.
Ten new units/0scenarios, author57focused and exact-pin independent review CLEAR. Root31focused/
8937full/303.56s/mypy769/Ruff838 and all838source/config pins pass. Four prior source-doctor
flag combinations stay byte-exact on both pinned states (50/54observations), sources unchanged.
Natural missing-own-annex positive is not present or claimed. Broader DA10.3 stays open.

The same commit records actual J254 release and root DA10.2 acceptance; plan48complete/6N.A./20open.
J255 actual release follows publication.

[Report](../assets/audits/2026-11-05-selection-limitation-coverage/REPORT.md),
[integration](../assets/audits/2026-11-05-selection-limitation-coverage/integration-proof.json),
[reproduce](../assets/audits/2026-11-05-selection-limitation-coverage/reproduce.md),
[review](../assets/audits/2026-11-05-selection-limitation-coverage/peer-review.json).

<a id="j257"></a>

## J257 — compatible required-part trace reader

Deploy the optional typed required-exclusion reader before any producer: actual outer/required
namespaces, body-free metadata, known hashes and bounded omission summaries use the existing trace
budget. Shared declared-trace SQL JSON rejects duplicate keys and excessive nesting with the
original bounded error. No selection, admission, source witness or producer behavior changes.

Focused units, independent exact-pin review, root full/static/docs gates and pinned historical
read/double-restore/report replay pass. Exact scope/counts/hashes live in the linked evidence.
J255 actual release is now recorded. J257 actual release follows publication; writer B still
requires verified reader deployment and a frozen real-corpus OFF baseline. DA4.1 remains open.

[Report](../assets/audits/2026-11-05-required-part-trace-reader/REPORT.md),
[integration](../assets/audits/2026-11-05-required-part-trace-reader/integration-proof.json),
[review](../assets/audits/2026-11-05-required-part-trace-reader/peer-review.json),
[writer prerequisites](../assets/audits/2026-11-05-required-part-trace-reader/design.md).

<a id="j258"></a>

## J258 — actual required-part exclusion provenance

The existing native non-annex required-file branch now emits bounded typed provenance for its
actual attempted outer/required scope. Body-free returned descriptors and count/digest summaries
survive the existing whole trace bound alongside archive evidence. Selection, observed/rejected
parts, admission, source witnesses/receipts, text/digests, question, policy and IO stay unchanged.
Source exceptions/cancellation propagate; optional metadata failures stay neutral and scope resets.

The deployed reader precedes this producer. Unit-first controls, scoped checks, exact-pin review,
paired cached/raw/technical qualification and root full/static gates pass; numbers, hashes and
coverage live in the linked evidence. Natural target-branch coverage is zero; missing cached
inputs and fixed-owner fake-preparation probes stay explicit. No schema/classifier/backend or
raw traversal expansion. Archive-only near-full-parent summary omission remains a follow-up;
DA4.1 stays open and plan48complete/6N.A./20open is unchanged.

J257 actual release is recorded with this stage. J258 actual release follows publication.
At the user's70%used threshold, finish this stage, record handoff and pause the full goal.

[Report](../assets/audits/2026-11-05-required-part-exclusion-writer/REPORT.md),
[corpus qualification](../assets/audits/2026-11-05-required-part-exclusion-writer/corpus-proof.json),
[integration](../assets/audits/2026-11-05-required-part-exclusion-writer/integration-proof.json),
[review](../assets/audits/2026-11-05-required-part-exclusion-writer/peer-review.json).

<a id="j259"></a>

## J259 — archive summary overflow

Preserve the archive summary when the outer request and archive evidence exceed the trace limit. The outer universe digest and omitted decision count remain available. Known and unknown archive observations retain their respective coverage. DA4.1 remains open.

[Qualification and replay recipes](../assets/audits/2026-11-05-archive-summary-overflow/report.md).

<a id="j260"></a>

## J260 — compatible reader for shared amendment text

Schema v68 reads canonical inline and shared-text amendment questions through the same snapshot
identity. A known shared body is digest checked before reconstruction. The current writer still
uses inline encoding. Pipeline capture remains a separate stage.

[Qualification and replay recipes](../assets/audits/2026-11-05-amendment-shared-text-reader/report.md).

<a id="j261"></a>

## J261 — capture of exact prepared amendment text

The two amendment preparation paths save the returned text-only question and intent in one
transaction. First validated collection retains the reference. Pruning respects live queue,
accounting, delivery and paid-result ownership. Repeated collection and legacy absence are
preserved. Correction source reuse remains a separate step.

The deployed v68 reader preceded capture. Pinned offline replay and 19 controls passed.
Whole-dump size and row parity were checked at 31 points across the collect and cleanup
lifecycle. [Scope, storage measurements and evidence](../assets/audits/2026-11-05-amendment-input-capture/report.md).

<a id="j262"></a>

## J262 — reuse the original amendment question

Known single-message roots use their saved question before source selection. The same prepared
question can retain old source lineage. Legacy roots still fetch their source and later
revisions cannot fill a missing root. Correction and cost guards continue to apply.

[Offline qualification and prior release](../assets/audits/2026-11-05-amendment-correction/report.md).

<a id="j263"></a>

## J263 — offline inventories for retained computations

The computation doctor can count saved application receipts and classify projection reasons
and input binding availability. The primary claim census reports exact field hashes for saved
validated answers and distinguishes missing legacy citations from semantic adjudication.
The default doctor report is preserved. The existing checkpoint benchmark supplies local
compressed size and timing measurements.

DA9.4 and DA10.3 remain open for independent adjudication and hosted measurements.
[Commands, scope and checkpoint results](../assets/audits/2026-11-05-saved-computation-censuses/report.md).

<a id="j264"></a>

## J264 — prepared input ranges for loose RCL annexes

Compose exact main and annex origins through the existing splice, trim and budget fitting.
A matching loose assembly gets per-part ranges. Generated separators have no origin.
Omission and selection decisions keep their existing policy.

The frozen cached/raw cohort preserved all 36 native requests and 12 no-request outcomes.
Thirty saved selections changed only in analysis coverage. Four units and the full local
gate passed. DA5.2 and DA5.4 remain open for other assembly shapes.
[Corpus cohort and parity](../assets/audits/2026-11-05-loose-rcl-annex-projection/report.md).

<a id="j265"></a>

## J265 — evidence from the Word walk, Sejm proposals and group citations

The compatible Word reader distinguishes absent textbox coverage from a recorded zero.
The native writer remains separate. A read-only corpus replay compares bounded OOXML body
inventory with nodes the legacy walk actually visits. The old extraction ledger remains exact.

The Sejm replay proves a narrowly scoped official proposal relation to a selected candidate
edition. The affected-group checker extends literal citation validation while preserving
existing key-change checks. The evidence covers scope and literal bindings.
[Replays and qualification](../assets/audits/2026-11-05-offline-provenance-evidence/report.md).

<a id="j266"></a>

## J266 — count Word textboxes in the native legacy walk

The native collector records a bounded count without expanding another XML branch or
changing the selected text. Existing extraction metadata gets priority at the byte cap.
The 3,055-file corpus comparison and six-result checkpoint round trip passed.
[Parity and storage measurements](../assets/audits/2026-11-05-word-textbox-writer/report.md).

<a id="j267"></a>

## J267 — compatible reader for a blocked normative annex

An optional bounded trace field can describe why the selected loose candidate stopped at
its normative annex. The reader verifies the actual parent slot and blocked state. Old
trace bytes and identities are exact. The writer remains a separate release stage.
[Compatibility and checkpoint parity](../assets/audits/2026-11-05-normative-annex-gap-reader/report.md).

<a id="j268"></a>

## J268 — record the actual blocked normative annex

The selected loose candidate now retains the terminal annex reason and exact URL slot.
Selection behavior and retry semantics are preserved. A trace with insufficient capacity
keeps its prior evidence. The 48-profile corpus replay changed only two diagnostic
records and their trace links.
[Paired evidence and source limits](../assets/audits/2026-11-05-normative-annex-gap-writer/report.md).

<a id="j269"></a>

## J269 — compatible loose RCL input receipts and proposed commencement

A reader verifies the actual request, emitted ranges and exact existing extraction parents
for a bounded loose multipart RCL receipt. The runtime writer remains a separate stage.
The offline claim checker binds a proposed commencement to an exact project-text quote.
The frozen cohort, old receipt formats and the full local gate passed.
[Reader and mechanical checks](../assets/audits/2026-11-05-multipart-rcl-receipt-reader/report.md).

<a id="j270"></a>

## J270 — observed checkpoint costs and an original primary card cycle

Twelve scheduled jobs provide hosted restore and final checkpoint durations. Eight
daily state boundaries provide actual SQL and compressed net growth. Saved read reports
provide cache-reuse counts for their recorded population. The direct-card auditor found
one exact original Sejm analysis-to-SENT chain and four explicit negative controls.
[Scope, pins and commands](../assets/audits/2026-11-05-hosted-state-and-delivery-cycle/report.md).

<a id="j271"></a>

## J271 — write exact loose RCL multipart input receipts

The native primary writer captures the actual returned question and its bounded
part-relative map. The same intent can continue without the receipt if a parent
cannot be verified before insert. Paired raw and cached preparation plus both collection
outcomes passed with unchanged other SQL rows. No new document bodies are stored.
[Replay and storage evidence](../assets/audits/2026-11-05-multipart-rcl-receipt-writer/report.md).

<a id="j272"></a>

## J272 — keep live review work out of new status plans

New status plans reuse the existing publication projection without operational review
work. The frozen route, render and other Bill fields are exact. The original amendment
history page reports the bounded availability of its input without exposing the payload.
[Native replay, dump comparison and history parity](../assets/audits/2026-11-05-editorial-freeze-and-input-history/report.md).

<a id="j273"></a>

## J273 — exact mixed RCL input ranges and main-part citation checks

Verified loose annex and self-amendment assembly retains exact ranges through the
final fit. Eight prepared profiles gained analysis coverage with unchanged omission
decisions. Archive and waiting controls were exact. The current proven DA5.2 routes
are accepted. Unverified deduplication and hybrid archive splices remain outside the
exact-origin scope.

An offline check can bind a key-change quote to the main part of a frozen multipart
RCL receipt. Annexes and unavailable selection parents stay outside that profile.
[Coverage and literal citation evidence](../assets/audits/2026-11-05-mixed-rcl-prepared-input/report.md).

<a id="j274"></a>

## J274 — smaller status plans and coded document deferrals

New status plans omit the saved consultation-review record. The dedicated review
publication keeps its context. Fresh route, rendering and digest parity were verified
on a saved status update. A single-row counterfactual reduced the copied gzip dump
by 109,168 bytes.

The work doctor can group the age of waiting document work by its last coded deferral
reason. Backoff and completion behavior are unchanged, with report parity across
three retained snapshots. [Replay and compatibility](../assets/audits/2026-11-05-purpose-freeze-and-deferral-ages/report.md).

<a id="j275"></a>

## J275 — compatible proposal relation reader and two-edition citation checks

The reader accepts an optional confirmed original Sejm proposal relation with exact
existing selection, edition and artifact parents. The pinned native 2320-A path and
two restores verified the reader. The normal writer remains unchanged.

A separate offline check binds one saved change to literal quotes from two distinct
proposal editions. On the pinned UD325 pair, a duration mutation invalidated only
the edited side. [Qualified scopes](../assets/audits/2026-11-05-proposal-relations-and-citations/report.md).

<a id="j276"></a>

## J276 — mixed RCL receipt reader and exact ODT inline-space verification

The mixed RCL profile admits six ordered parts without widening the previous format.
Four actual raw profiles qualified the reader in queued and submitted state. The
writer remains a separate stage.

The independent ODT verifier now checks eight actual note and embedded-call spans
with bare space and tab elements. Original selected notes and separate references
remain unobserved.
[Reader and corpus evidence](../assets/audits/2026-11-05-mixed-reader-and-odt-inline-spans/report.md).

<a id="j277"></a>

## J277 — prospective writers for confirmed Sejm proposals and mixed RCL receipts

The native Sejm writer preserves the official singleton proposal relation after
validating the observed PDFs and saved source parents. A separate writer records exact
mixed RCL receipts for the previously qualified profile. Both use the pre-existing
selection and preparation decisions, with bounded storage and an unchanged fallback when
evidence is unavailable. [Native replays and size checks](../assets/audits/2026-11-05-native-proposal-and-mixed-writers/report.md).

<a id="j278"></a>

## J278 — independent coverage views and compatible compact card evidence

An opt-in replay report distinguishes physical extraction from the source-selection
decision and actual prepared ranges. It preserves the old output on the tested profiles.

A new compact-card reader verifies optional references to existing source and applied
result evidence. The synthetic example survives the ordinary cleanup paths. The existing
writer and legacy cards are unchanged. The two-snapshot cycle inventory retains the one
proved 3281 cycle and records missing links for the next candidates.
[Coverage and retention qualification](../assets/audits/2026-11-06-coverage-views-and-card-evidence/report.md).

<a id="j279"></a>

## J279 — native compact card lineage and offline committee recommendation

New eligible original direct cards bind their exact primary source and result
before first send. The unchanged plain content remains available on missing evidence.
The synthetic replay confirms the compact reference survives normal expiry.

An offline proof records report 587 as a recommendation for Senate resolution 584
within process 471. A different report from the same process is a negative control.
The proof leaves adoption unasserted.
[Qualification and limits](../assets/audits/2026-11-06-card-lineage-and-committee-recommendation/report.md).

<a id="j280"></a>

## J280 — retained delivery audit modes and compatible required-part diagnostic

The direct-cycle auditor can opt in to compact original history after the full plan
expires. A separate mode checks two exact retained secondary applications through
their frozen changes and recorded sends. The primary default report stays exact.

The required-part diagnostic reader validates a terminal missing-text result against
the exact selected candidate and required URL. Both actual source files read normally.
A deliberately damaged input verified the waiting and restore paths.
[Audit and reader evidence](../assets/audits/2026-11-06-retained-delivery-and-required-part/report.md).

<a id="j281"></a>

## J281 — native required-part wait evidence and official historical reading outcome

The native writer records a bounded diagnostic for the exact required part that
returned no text. The actual readable PDFs were unchanged and a deliberately damaged
derivative verified the writer and restore boundary.

An offline audit separately proves the typed historical third-reading decision and
official same-owner locator for print 2111. Proposal and later proceedings cannot
satisfy this profile. [Qualified scopes](../assets/audits/2026-11-06-required-part-and-sejm-decision/report.md).

<a id="j282"></a>

## J282 — show verified compact primary lineage on original-card history

The explicit history page shows the presence of retained primary lineage on a
confirmed original card. It exposes a status only. The native publication test
checks its absence before send and presence after normal expiry and restore.
[Operator scope and parity](../assets/audits/2026-11-06-operator-card-lineage/report.md).

<a id="j283"></a>

## J283 — retained gap census, offline card references and print representation proof

The source doctor can count saved gap reasons over distinct validated trace rows.
The content doctor exposes exact references for validated compact original lineage.
Both preserve their previous legacy output.

A separate pinned replay confirms the same literal normative core in the PDF and DOCX
of print 1608. The strict control stays unknown. The plan accepts DA5.4, DA7.5 and DA10.3
in their tested profiles, with unavailable evidence and additional source families
listed separately. [Evidence and accepted scope](../assets/audits/2026-11-06-retained-evidence-census/report.md).

<a id="j284"></a>

## J284 — refresh the human handoff and acceptance census

The sealed C006b queue was replayed with the current code. The new form
`current-holdout-form` verified three original PDFs in three initiative groups.
Its manifest SHA-256 is `9ad57597374503d0f74bce5a1fb083c760587957fc7fdea8d924ba4cf638cb6b`. The inspected corpus run store contained no
recorded review submissions. DA0.6 still awaits independent review.

The read-only restore of state `ff49a74a8d5e5b846a8f11d98ca71c2f421930b8`
covered 50 sent original cards. The direct-cycle audit with compact history
enabled recorded only publication 258. The other 49 had missing or out-of-scope
evidence. The same snapshot contained 117 nonblank claims in 12 validated
primary results. Their semantic support remains unassessed. DA9.4 and DA10.7
stay open.

<a id="j285"></a>

## J285 — make representation qualification repeatable

The cohort command enumerates the pinned official listing, records missing
and excluded pairs, and reuses the strict single-print proof. It supports
explicit subsets and reports progress independently of the JSON. The pinned
1608 positive and 1759 boundary control reproduced their prior outcomes.
DA4.7 remains open for persisted relations and other relation types.

The full offline cohort found 612 eligible prints among 3,051 numbered prints.
Eight matched. The strict proof left 552 unproven, 51 exceeded its byte bound
and one lacked a local pair. The final command reproduced all eight positives
and the strict boundary control.
[Counts and replay pins](../assets/audits/2026-11-06-print-representation-cohort/evidence.json).

<a id="j286"></a>

## J286 — validate stored Sejm normative-core representations

The candidate trace reader accepts optional bounded representation metadata.
It checks the original observation and edition, the selected PDF, both
complete-body read receipts and the exact DOCX extraction identity. The
trace records the profile and core digest, with no claim of retained
bodies, complete-document equivalence, or adoption. Existing trace IDs
and the schema stay unchanged.

Fresh scratch observations for all eight pinned positive pairs survived
two restores apiece. Removing the DOCX receipt is rejected in the reader,
restore and doctor paths. The ordinary producer remains unchanged.
[Replay results](../assets/audits/2026-11-06-representation-trace-reader/qualification.json).

The existing state snapshot `ff49a74a` produced byte-identical trace and gap
doctor reports before and after the reader change. The largest measured
increment with all parents already present was 1,820 SQL bytes and 435 gzip
bytes for one trace and its observation link.

<a id="j287"></a>

## J287 — materialize proven representation links in isolated snapshots

The exact single-print replay can export a new standalone SQL snapshot.
It retains the original selection, edition, both read receipts, the DOCX
extraction and the bounded relation from the same verified capture. The
ordinary JSON proof remains identical. No live analysis reads or state
updates were added.

All eight corpus positives produced their previous trace IDs and survived
two restores. The negative 1759 case produced no SQL file, and an existing
output was preserved. DA4.7 remains open for production capture and the
other relation types.
[Snapshot qualification](../assets/audits/2026-11-06-representation-snapshot/qualification.json).

<a id="j288"></a>

## J288 — prepare independent review from an exact retained submission

The claim census can export one local review sheet when the paid result,
batch item and receipt identify the same request and the retained submitted
text matches its hash and length. It displays saved claim slots and the input
for independent review, with no labels or reconstructed comparison text.

The state `ff49a74a` copy provides eight claims for the reanalysis of print
3231. The default inventory remains byte-identical. Changed and expired
text controls are refused. Only body-free qualification is tracked in Git.
DA9.4 remains open pending the independent judgment.
[Qualification](../assets/audits/2026-11-06-saved-primary-claim-review/qualification.json).

<a id="j289"></a>

## J289 — bind a third-reading decision to its adopted text edition

The compatible trace reader validates the explicit third-reading decision,
its same-owner official locator, the selected PDF, the exact body read
and the saved edition. It leaves individual provision lineage, later
proceedings and legal force outside the evidence profile.

The pinned term 10 process snapshot and a separately acquired official
2111 PDF qualified the profile. The trace survived two restores. Second
reading, rejection and a different owner were refused. No default writer
was added, and no historical state was backfilled.
[Qualification and acquisition receipt](../assets/audits/2026-11-06-adopted-text-trace-reader/qualification.json).

Among 980 numeric term 10 bill owners, the existing selector chose 610
third-reading locators. Of these, 579 matched the unique same-owner
dated adoption metadata and 31 had another locator. This count does
not qualify PDF bodies beyond the pinned 2111 case. The older state
copy produced identical trace and gap doctor reports.

<a id="j290"></a>

## J290 — record the adopted Sejm edition during analysis

Analysis records a third-reading adoption trace when fresh same-owner
process metadata identifies a unique dated passed stage and the selected
official PDF has a matching read receipt and a one-part normative edition.
The stage metadata travels in memory. The existing selection, request and
durable batch metadata keep their prior behavior.

The paired offline run used the pinned term 10 process snapshot and the
official PDF for print 2111 from J289. The same analysis ran with a successful
result, a simulated provider failure, an omitted decision, a foreign locator
and a duplicated stage. Each run read one process and one attachment.
The request digest and normalized SQL were identical to baseline in all five
profiles at queue, submission and collection, after two restores per
checkpoint. The qualified cases added one trace and its source link.
The three metadata controls added zero bytes. The two positive profiles
added 1,433 SQL bytes and 334–385 gzip bytes per checkpoint. No model
requests were submitted and no historical rows were backfilled.

This covers one native PDF case and the stated controls. It establishes
the adoption of that whole text at third reading. Representation,
supersession and recommendation coverage remain open under DA4.7.
[Qualification](../assets/audits/2026-11-06-adopted-text-trace-writer/qualification.json).

<a id="j291"></a>

## J291 — share the committee recommendation evidence rules

The application owns the pure rules for pairing an official committee
report with an earlier amending Senate position and checking its first-page
heading. The replay calls those same rules. The result carries the heading
hash and page number; the checks keep the exact cited print and require
a recommendation formula. An empty first page yields no proof.

Against baseline `c42e5269`, the pinned report 587 and resolution 584
produced the same proof and input hashes. Report 849 and resolution 847
matched the metadata profile, but the first page of the retained PDF
provided no extractable text. Both revisions refused that case at the
heading check. The case files pin the source bytes and can be run through
`tools.replay_committee_recommendation` using the existing J231 collection
and the Senate resolution supplement.
[Positive case](../assets/audits/2026-11-06-card-lineage-and-committee-recommendation/recommendation-case.json) and
[empty first page](../assets/audits/2026-11-06-committee-recommendation-contract/empty-first-page-case.json).

Native persistence remains open. The native extraction already visits
the PDF pages. The next writer needs a bounded first-page observation
from that read and exact parents for both documents before it can record
a recommendation. A later adoption decision requires separate evidence.

The native `DocumentTextExtractor.extract_parts` sees one direct root
PDF in each case and reproduces both outcomes without a second parse.
[First-page hashes and character counts](../assets/audits/2026-11-06-committee-recommendation-contract/physical-observations.json).

<a id="j292"></a>

## J292 — carry the root PDF first page through source selection

The structured extractor supplies an ephemeral first page from its existing
physical PDF walk. The loader keeps it with the root file through its cache
and normal selection. A PDF inside an archive, a failed first page, or a page
over 4,096 characters has no view. A readable but empty first page stays
empty even if a later page has text. The page is excluded from serialized
extraction output and does not enter state or the model request.

The existing recommendation replay now consumes that view through
`TextLoader.select_document`. The pinned 587 → 584 proof matches J291
after excluding the code fingerprint fields. The pinned 849 → 847 pair
still fails the heading check. An offline preparation through the normal
amendment path produced the same request hashes, source observations,
artifact links and download sequences for both pairs as J291.
[Positive and empty-page source pins](#j291).

The report view is ready for a native recommendation writer. The writer
still needs to bind the exact report and resolution observations and
verify their existing physical receipts before persisting the relation.

<a id="j293"></a>

## J293 — record the committee recommendation during amendment preparation

The ordinary amendment path records a bounded recommendation trace when
the owner and official process stage match, the resolution is confirmed
by its existing reader, and the report's first page names that exact
resolution and recommends action. The recommendation uses the report's
existing candidate trace and refers to the resolution observation.
Lookups and restore verify both same-owner singleton selections and
their exact artifact read receipts. The proof makes no adoption claim.

The offline native run against `abad53c6` used the two pinned pairs
from J291 and queued a request without submitting it. Both cases retained
the same request hashes, download sequences and SQL after excluding the
new trace and its source link. Report 587 added one trace after two
restores. Removing either artifact read caused restore to refuse the
dump while preserving the valid state. Report 849 added no trace.
In this fixture the 587 trace and source link added 1,654 SQL bytes
and changed the gzip size by 2,495 bytes. The control added zero bytes.
[Qualification](../assets/audits/2026-11-06-committee-recommendation-writer/qualification.json).

This covers the verified mixed-recommendation text profile. Other
recommendation forms, representation and supersession still require
their own qualification under DA4.7.

<a id="j294"></a>

## J294 — qualify supersession of an exact committee draft at third reading

The pinned official process and print metadata identify 2594 as the
last full committee draft before the known third-reading adoption in
process 2111. The report cover names its owner and the attached draft.
Its normative text starts on physical page 5 and runs through page 324,
matching the printed count of 320 pages. The adopted text starts on
page 1 and ends on page 312. Both texts have the same title and their
printed dates match the official stages. Their final commencement clauses
differ literally. The proof concerns the whole text within the Sejm
process at third reading.

A repeated pinned run produced the same hashes. Six mutations of the
extracted inputs refused wrong ownership, a shifted boundary, a missing
page body, a changed printed date, a changed page count and an identical
commencement clause. The original PDF bodies remain in local audit inputs.
[Input pins](../assets/audits/2026-11-06-sejm-supersession/case.json),
[acquisition receipt](../assets/audits/2026-11-06-sejm-supersession/report-receipt.json),
[proof](../assets/audits/2026-11-06-sejm-supersession/proof.json) and
[negative controls](../assets/audits/2026-11-06-sejm-supersession/negative-controls.json).

Run `python -m tools.replay_sejm_supersession` with the pinned corpus
checkout, the local body root, case path and SHA-256, and a new output
path outside both source roots. The ordinary selector still classifies
2594 as unknown. Native edition capture and the durable supersession link
remain open, as do provision-level lineage and later legislative events.

<a id="j295"></a>

## J295 — export the proved supersession with both edition parents

The pinned replay can export a standalone SQL snapshot. The report draft
is addressed separately from the whole committee report by its body
hash, text-view hash and physical pages 5–324. The adopted edition is
the ordinary selected 2111 text, with the same identity as J289.
The adoption trace references the exact report observation and edition.
The reader checks the report range, same-owner observation, selection
artifact, edition membership and complete body read. Existing adoption-only
traces keep their original identity.

Two exports at the same recording timestamp produced byte-identical SQL
and proof, and each export passed two restores. A missing prior edition
link, prior artifact link, prior body read or adopted body read was
rejected without replacing the valid SQLite state.
[Snapshot qualification](../assets/audits/2026-11-06-sejm-supersession/sql-qualification.json).

Use the J294 replay with `--snapshot-output` and a timezone-aware
`--recorded-at` to reproduce the separate snapshot. The normal analysis
selector and runtime writer keep their existing behavior. Native capture
of the report range and native supersession remain open under DA4.7.

<a id="j296"></a>

## J296 — capture a committee draft edition during normal analysis

When the current official committee report is read for analysis, a
separate observation records the exact attached draft range. The proof
requires a same-owner official stage, a cover naming the attached bill,
a readable direct PDF, consecutive printed page numbers and a final
commencement article. The ordinary source selection and request continue
to use their existing input. The additional observation stores the range
and hashes, with the existing artifact read and extraction as parents.

The paired native run used pinned process metadata and four official
reports. It produced an edition for 2594 (pages 5–324, the J295 identity)
and 3107 (pages 2–23). It refused the conflicting term stamp in 583, the
unreadable 1703 and a duplicated 2594 stage. All five cases matched their
baseline request hashes and download sequence, and each passed two
restores. SQL matched after removing the new draft graph and normalizing
the extractor code fingerprints and their derived extraction IDs.
[Qualification and body pins](../assets/audits/2026-11-06-native-committee-drafts/qualification.json).

This covers a printed-page profile. It leaves unqualified reports
unknown. The normal writer still needs to bind the captured predecessor
when it observes the later third-reading adoption.

<a id="j297"></a>

## J297 — link a previously captured committee edition at third reading

The fresh Sejm stage tree identifies the predecessor before adoption.
The writer links it only when the same report URL has one known body
identity and a unique qualified draft observed before the adopted
edition. The existing lookup validates both edition and body parents.
An unavailable or ambiguous predecessor leaves the adoption-only trace.

The paired offline analysis retained drafts from reports 2594 and 3107,
then observed adopted texts 2111 and 3102. Both cases persisted their
supersession trace, and 2111 matched the J295 trace identity. No prior
read, two report bodies at one URL and a missing prior edition link
kept the ordinary adoption-only trace. Each case survived two restores.
All requests and read sequences matched the baseline. SQL matched after
replacing the enriched trace and link with the adoption-only identity.
The two positive fixtures added 446–447 SQL bytes and 60–63 gzip bytes.
[Qualification](../assets/audits/2026-11-06-native-sejm-supersession/qualification.json).

This records whole-text succession in the Sejm process. The runtime
PDF/DOCX representation writer and other evidence profiles under DA4.7
remain open.

<a id="j298"></a>

## J298 — persist the native PDF and DOCX representation proof

The original-print analysis can prove that a DOCX represents the same
literal normative core as the selected PDF. It requires the official
singleton print and exact attachments. It checks the already-read PDF
before attempting one optional DOCX read. The bounds are 2 MiB and 30
readable PDF pages, 2,000 core characters and 256 KiB for the DOCX.
The proof persists only matching cores and their exact read, extraction
and edition parents. Earlier listing-based proofs retain their profile.
The native proof pins the observed print metadata.

The pinned PDF preflight selected 19 of 612 eligible prints. Paired
native runs covered all 19 and reproduced all eight known positive
pairs. The other 11 left no relationship. The controls covered a
DOCX outage, byte-limit refusal, and ambiguous print metadata. The
request and metadata-call identities matched baseline in every run.
All checkpoints passed two restores. Removing the new trace and its
DOCX metadata parents left the same SQL. Positive fixtures added 5,594–5,603
SQL bytes and 1,080–1,202 gzip bytes. No model request was submitted.
[Qualification](../assets/audits/2026-11-06-native-print-representations/qualification.json).

DA4.7 is accepted for its proved profiles. Cases outside their contracts
stay unknown, and the PDF remains the ordinary analysis source.

<a id="j299"></a>

## J299 — distinguish size refusals from returned files without text

Terminal required-part and normative-annex traces identify a download
size refusal at the actual selected candidate and relation position.
A refusal cannot carry a completed-body hash. Loader exceptions still
follow the existing waiting and outage paths.

The paired offline replay used both original PDFs of term 8 print 108
and the pinned loose annex for RCL 12366453. The ordinary byte limit
preserved the readable cases exactly. Lower test limits exercised the
refusal paths using the same bytes. Text hashes, read order, waiting
state and SQL apart from the gap and trace identity matched baseline.
Each snapshot restored twice. No provider request was sent.
[Size refusal evidence](../assets/audits/2026-11-06-size-refusal-traces/qualification.json)
and [Sejm attachment pins](../assets/audits/2026-11-06-retained-delivery-and-required-part/source-pins.json).

DA4.1 and DA4.4 remain open for the remaining decision provenance.

<a id="j300"></a>

## J300 — retain terminal self-amendment gaps on the actual candidate

The native trace records the selected candidate, amendment position
and observed failure when composition returns waiting at a self-amendment.
It distinguishes a returned unreadable body from a download size refusal.
The candidate doctor counts the new reason when a validated trace carries
it. Existing doctor output keeps the prior shape when no such trace exists.

The pinned 2320 and 2320-A originals replayed unchanged against
`afeebc4d`. A damaged derivative and a controlled size refusal added
only the new gap and trace identity. The selected source, read order,
waiting state, selection ID and normalized SQL stayed exact. Both
snapshots passed two restores. An injected missing-file exception
still propagated. No model requests were made.
[Qualification](../assets/audits/2026-11-06-amendment-gap/qualification.json)
and [pinned originals](../assets/audits/2026-11-05-offline-provenance-evidence/case.json).

DA4.1 and DA4.4 remain open for the other decision provenance gaps.

<a id="j301"></a>

## J301 — retain bounded supporting-file exclusion reasons

The trace identifies excluded supporting files by position in the
actual selected candidate. It distinguishes a draft regulation, a
returned file without text, size refusal, known missing or refused
response, pre-send rejection and generic read failure. The existing
trace cap still applies. Within the field, the first eight exclusions
are retained in source order with an omitted count. The doctor reports
only validated retained reasons and the omitted count.

The paired replay used the pinned RCL 12366453 stage and its existing
body pins. The local inputs lacked two optional source bodies.
Separate controls used a typed 404, damaged returned PDFs and a
required-annex size refusal. The selected source, text, read order and
SQL outside the new trace and link stayed exact. Every checkpoint
restored twice. The mandatory-annex control never reached optional
reads and kept the earlier trace unchanged. No model requests were made.
[Qualification](../assets/audits/2026-11-06-supporting-exclusions/qualification.json).

DA4.1 and DA4.4 remain open for the remaining decision provenance.

<a id="j302"></a>

## J302 — distinguish observed read failures for a normative annex

The selected candidate trace reuses the loader's typed download
classification for annex reads. Known missing and refused responses,
pre-send rejection and an unclassified read error stay distinct.
A legacy `no_returned_file` remains readable. The new reasons cannot
carry a completed-body hash. A source outage still propagates.

The pinned RCL 12394200 input has no retained body for annex 703531.
The paired replay used that local absence, controlled 404 and 403
responses and a pre-send budget refusal. It also covered a damaged
returned PDF and a source outage. The selected URL, selection ID, read
order and waiting outcome matched baseline. SQL was exact after
normalizing the refined gap and trace identity. The damaged-PDF and
outage controls were unchanged. The checkpoints restored twice.
[Qualification](../assets/audits/2026-11-06-annex-read-reasons/qualification.json).

The controlled response statuses do not establish the live availability
of this attachment. DA4.1 and DA4.4 remain open for other profiles.

<a id="j303"></a>

## J303 — expire isolated explanation location payloads

Batch cleanup already removed `explanation.located` on eligible rows, but
the row predicate missed a location object when it was the only heavy field.
The predicate now includes that object. Consumption, accounting, provider
cleanup and settled-owner guards still apply. Deferred review and document
work read their own contexts. Consumed primary readers use request bindings,
selection metadata and prepared-input receipts, which cleanup preserves.

The unit regression failed on baseline and covers the three batch lifecycle
guards, usage preservation, a second cleanup and a dump/restore. The offline
comparison restored saved state `13440124b6f38f1124beb9b99f35a7c8a227c08b`
from SQL SHA-256 `baee693eefec358f018cf8265096d9d5acca1eaba2d5f9bfa4afd363166c4bec`.
The saved copy had zero nested location objects. Baseline and candidate native
post-cleanup dumps were byte-identical at 5,270,886 gzip bytes. Adding only a
location object to the copy's one eligible explanation item left the baseline
at 5,270,896 bytes. The candidate removed one additional payload and reproduced
the native dump byte-for-byte. The four post-cleanup dumps restored without
changes and a second cleanup removed zero rows. The control shows reachability
on a compatible saved row. It establishes no natural incidence or production savings.

The same copy retained 85,453 characters of amendment revision delivery plans
in three of nine forgotten and accounted amendments items. The three had no
matching paid result or revision work key. DA6.6 remains open for qualifying
legacy revision retention and other dependency boundaries.

<a id="j304"></a>

## J304 — qualify retention of legacy amendment targets

The three captured plans identified in J303 each match a SENT revision by
the exact `amendments/<original-publication-id>/<batch-custom-id>` ref,
bill owner, channel, message and amendment key. None has a modern paid
result or editorial binding. Each queued plan has the same serialized Bill
as both the original publication and sent revision. The sent revision has a
different frozen change and rendered text. A future cleanup on the saved copy
clears both original publication payloads and keeps the three revision payloads.

On that pinned copy, removing only the three duplicated Bill strings would
save 78,193 bytes of SQL and 14,345 bytes of gzip, about 0.27% of the compressed
dump. All three modified targets fail the existing reader on the required
`bill_json`. This measurement describes an invalid counterfactual. Retain the
legacy targets as recorded. The measured scope does not justify adding a
second target representation, pinning original delivery payloads, or creating
retrospective handoffs. Modern targets continue to use their separate durable
work reference. DA6.6 remains open for other retention dependencies.
[Qualification](../assets/audits/2026-11-06-legacy-revision-retention/qualification.json).
Commit `b38cbdc5` passed CI 42184444571 and relay deployment 42185161482.

<a id="j305"></a>

## J305 — read terminal part failures in safe catalog attempts

The catalog attempt reader accepts an optional terminal read detail for a
required part or self-amendment. It binds the catalog descriptor address,
relation, position and exact URL hash. A missing descriptor, wrong position
or different URL hash makes the attempt invalid. The detail carries a fixed
read reason and leaves selection unknown. It is absent from legacy JSON and
its identity calculation when missing. The SQL schema and read policy stay
the same.

The reader was qualified with explicit test attempts built from the pinned
term 8 druk 108 multipart catalog and term 10 druk 2320 self-amendment catalog
from J299 and J300. Both typed attempts and both legacy forms survived two
restores with identical SQL. Unit controls rejected invalid addresses,
positions and hashes without changing the repository. The pinned state dump
from J303 also restored twice byte-for-byte and had zero catalog attempts.

Commit `a4ecb11a` passed CI 42185343068 and relay deployment 42186307729
before the producer was enabled. Older readers reject the new field once
a writer persists it. DA4.1 and DA4.4 remain open for the remaining profiles.

<a id="j306"></a>

## J306 — record terminal required and self-amendment read failures

Native loose-file composition records the exact catalog slot only when the
same exception escapes the observation scope directly or as the explicit cause
of the primary analysis selection error. Existing generic error class
semantics remain intact if an earlier outer candidate failed. The retained
detail separates 404, refusal, pre-send rejection, source outage and other
read errors. Unsafe or omitted descriptors receive no detail. The exception
object, retry behavior and unknown selection are preserved.

Sixteen paired offline cases used the pinned term 8 druk 108 and term 10
druk 2320 catalogs and body hashes. On druk 108 the same URL appears as
both an outer candidate and a required part. Pre-send rejection and outage
stop the selection path at the outer read and do not produce part evidence.
The direct composition controls reach the required slot and do produce it.
Both readable cases and direct readable control stayed byte-exact. All
sixteen matched read order, selection or escaping exception and SQL after
normalizing the new detail and its attempt identity. Typed controls added
87–95 bytes of gzip per attempt. Every checkpoint restored twice. No network
or model calls were made. [Qualification](../assets/audits/2026-11-06-terminal-part-read/qualification.json).

DA4.1 and DA4.4 remain open for the remaining catalog and decision coverage.

<a id="j307"></a>

## J307 — read the exact prepared Sejm bill and self-amendment map

A bounded primary text receipt identifies the frozen context, request, emitted
ranges and both loose extraction parents. It requires an analysis observation
with its exact retained official proposal trace, selection and edition, all
owned by the same term and print. Existing single-part and RCL receipts keep
their serialized fields. The new profile carries neither source text nor a
storage promise. Citation verification for this profile remains unqualified.

In the pinned native druk 2320/2320-A replay, the offline fixture attached a
receipt to an otherwise unchanged queued request. Its 51,796 characters map
to 39,272 from the bill, 155 generated characters and 12,369 from the
self-amendment. Queue, submitted and collected checkpoints restored twice.
After removing only the new receipt, SQL matched the paired baseline. The
serialized request, source IO and outcome matched as well. Gzip deltas were
561, 594 and 589 bytes. A nonexistent proposal trace rejected the save
atomically. A submitted item with a foreign binding was rejected before the
batch was saved. Both existing RCL receipt profiles serialized byte-for-byte
like the previous code.
[Qualification](../assets/audits/2026-11-06-sejm-self-amendment-input-reader/qualification.json).

Reader stage only. Commit `d39112a9` passed CI 42190382539 and relay
deployment 42191323318 before activation. Older readers cannot accept the
new profile once persisted.

<a id="j308"></a>

## J308 — record the exact native Sejm bill and self-amendment input

Primary preparation writes the J307 map when the fitted request, both
current loose extractions and the same official proposal proof agree.
Proof construction is shared with the source history writer. Persistence
checks the exact observation and trace before saving the queue item. If that
optional evidence is absent, the unchanged frozen request still queues.

The paired offline lifecycle replay used the pinned druk 2320/2320-A
catalog and bodies. The native receipt stayed exact through queue, simulated
submission, accounting and collection for success and a controlled provider
failure. Six controls without sufficient official evidence or a retained
parent queued without this receipt. All 24 checkpoints restored twice and
matched baseline SQL after removing only the new receipt. The request,
source calls and results matched in all eight profiles. The positive cases
added 561 gzip bytes at queue, 594 at submission and 563–589 at collection.
The controls added zero. No model requests were sent.
[Qualification](../assets/audits/2026-11-06-sejm-self-amendment-input-writer/qualification.json).

The native replay covers first analysis. DA5.1 and DA5.6 remain open for
reanalysis without a current proposal witness, other assemblies, exact
physical spans and the request-payload retention boundary.

<a id="j309"></a>

## J309 — read an exact question-text reference in queued metadata

An intent reader can resolve a bounded marker in the primary metadata text
slot against that row's frozen question and payload. It checks the UTF-8
text hash and verifies the frozen payload checksum before yielding hydrated
metadata. Restore and the integrity doctor use the same reader. Submitted
items keep ordinary metadata with the reconstructed text.

Eight offline queue variants from two pinned Sejm source bundles had an
exact context/metadata text duplicate. The controlled representation saved
13,553–64,470 bytes in gzip per isolated queue snapshot. Every compact
checkpoint restored twice and reproduced the original queued intent,
submitting intent and submitted item. A tampered text hash failed reads
and scratch restore. The saved schema 66 queue from J241 restored to the
same SQL and intent as the previous code. The J303 copy had no active batch
intents or unconsumed items, so this evidence does not estimate production
prevalence. [Qualification](../assets/audits/2026-11-06-pending-question-text-reader/qualification.json).

Reader only. Commit `a4c5e3ae` passed CI 42193881546 and relay deployment
42194626444 before activation. The prior runtime rejects the marker in a
primary text slot.

<a id="j310"></a>

## J310 — share identical large question text while an intent is queued

New primary queue rows use the J309 text marker when their frozen question
and metadata contain the same UTF-8 string of at least 32 KiB. Smaller,
different and unfrozen inputs retain their inline representation. Reading a
queued or submitting intent restores the exact original metadata before
building the submitted item. Submitted items keep their self-contained text.

The native paired replay covered eight offline queue, submission and
collection lifecycles from the two pinned Sejm source bundles. All 24
checkpoints restored twice. The queue-to-submitting-to-queue transition kept
the dump exact. Request identity, source reads, outcomes, retained receipts
and normalized SQL matched baseline. The native writer saved 13,554–64,472
bytes of gzip in these isolated queue snapshots. Submitted and collected
checkpoint sizes matched. Unit controls cover the ineligible inputs and
repeat saves. [Qualification](../assets/audits/2026-11-06-pending-question-text-writer/qualification.json).

DA5.1 remains open for other payload copies and source profiles. This stage
covers only one duplicate while its immutable question is present.

<a id="j311"></a>

## J311 — read the original card lineage for a Sejm self-amendment input

The original-card verifier accepts the exact two-part receipt when the
reference names that receipt's own observation. It verifies the official
proposal trace and both extraction parents through the shared batch receipt
validator. The compact read exposes both parts with their roles and physical
identities. The existing single-part profile keeps its output shape.

The pinned druk 2320/2320-A lifecycle used local batch and delivery fakes.
The reader fixture supplied the exact lineage before the original send.
The normal candidate lookup remained inactive. After batch and delivery
payload cleanup and two restores, the compact read kept both part identities.
The request, rendered card, publication and message IDs matched the plain
baseline. An invalid observation reference produced the same plain card.
The fixture added 40 gzip bytes after cleanup.
[Qualification](../assets/audits/2026-11-06-sejm-card-lineage-reader/qualification.json).

Commit `1ae1601f` passed CI 42196958682 and relay deployment 42197931787
before automatic capture was enabled.

<a id="j312"></a>

## J312 — bind an original card to the exact Sejm self-amendment input

The original-card lookup can now select the J308 receipt's exact observation
before first send. Another observation of the same selection is not a candidate.
The existing content verifier validates the reference before persistence.
Single-part matching retains its previous ambiguity rule.

The pinned native 2320/2320-A lifecycle included a second observation of the
same selection and a fake delivery boundary. The exact reference was present
while the publication was pending before the fake send. The request, source
calls, rendered card and publication/message identities matched baseline.
Batch and delivery payloads expired, both restores were byte-exact, and
compact history still exposed the two physical parents. Final SQL matched
after normalizing only the new reference and derived content ID.
[Qualification](../assets/audits/2026-11-06-sejm-card-lineage-writer/qualification.json).

DA7.1 remains open for other editorial profiles.

<a id="j313"></a>

## J313 — read explicit observations in multipart RCL input receipts

Both existing loose RCL profiles can carry an optional exact source
observation. The reader checks the original paid owner, analysis purpose,
selection and source URL before resolving the physical parts. For a
submitted item transferred to a new owner, the observation still belongs
to the original binding. An absent field preserves the previous JSON.
The compact card reader requires an explicit matching observation for RCL.
The runtime producer and automatic card lookup stay inactive.

Three pinned raw queue profiles for RCL 12294859 accepted an explicit
fixture reference, preserved the frozen request and restored twice.
Removing only the field reproduced the original SQL. The archive control
for RCL 12365005 prepared no request. Unit controls cover both RCL receipt
profiles, reject review observations and restore a transferred submitted
item with its original binding.
[Qualification](../assets/audits/2026-11-06-rcl-receipt-observation-reader/qualification.json).

Commit `f03081d7` passed CI 42200261955 and relay deployment 42200900179
before activation.

<a id="j314"></a>

## J314 — record original card lineage for multipart RCL inputs

Preparation records the exact observation identity from the same captured
source as the multipart receipt. The first-card lookup uses this explicit
reference for either RCL profile. Earlier receipts without an observation ID
do not participate in automatic capture.

The paired native replay covered three six-part profiles of RCL 12294859,
the two-part annex profile of RCL 12366453 and the no-request archive
control of RCL 12365005. The writer bound the exact observation before
the fake send. Both parts or all six parts stayed visible in compact
history after batch and delivery cleanup and two byte-exact restores.
Requests, source calls, rendered cards and publication identities matched.
Queue and final SQL matched after normalizing the new receipt and card
references and the derived content ID. The four positive profiles added
38–99 bytes to the compressed post-cleanup dump.
[Qualification](../assets/audits/2026-11-06-rcl-card-lineage-writer/qualification.json).

DA7.1 remains open for other editorial histories.

<a id="j315"></a>

## J315 — read a compact frozen provider payload

The queue reader can reconstruct one frozen provider payload from its
saved prefix and suffix and the JSON-escaped text of the same question.
The original payload checksum validates the reconstructed bytes. The
reader does not rebuild prompts, options or schemas. Restore and the
request, work and batch integrity diagnostics use the same decoder.

The pinned 2320/2320-A offline control used the actual OpenAI and Anthropic
request builders with no provider calls. Anthropic used its offline token
estimate. Both reconstructed payloads matched exactly through two restores
and the transition to submitting. The isolated compact queue dumps saved
14,103 and 14,049 gzip bytes respectively. The unit control includes
Unicode, quotes, backslashes and newlines and rejects a changed prefix
before replacing a restored repository.
[Qualification](../assets/audits/2026-11-06-provider-payload-reader/qualification.json).

Commit `e68d923a` passed CI 42203668477 and relay deployment 42204730124
before activation.

<a id="j316"></a>

## J316 — share the exact question text inside a frozen provider payload

Primary queue writes use the J315 representation when at least 32 KiB of
question text has one exact JSON-escaped occurrence in the frozen payload.
Missing, repeated, overlapping and short matches keep the original inline
format. The existing provider checksum still names the full payload.

The paired 2320/2320-A replay used the actual OpenAI and Anthropic request
builders. Both compact queues restored twice. With different sender
model and language defaults, a local fake SDK received the same OpenAI
JSONL bytes and Anthropic request object. Submitted SQL and queue SQL
normalized for the representation matched the baseline. The isolated
queue dumps saved 14,103 and 14,049 gzip bytes respectively.
[Qualification](../assets/audits/2026-11-06-provider-payload-writer/qualification.json).

DA5.6 remains open for the remaining input profiles and post-submission
request-manifest coverage.

<a id="j317"></a>

## J317 — read a frozen provider request manifest after submission

Submitted item metadata can retain the exact payload checksum and byte
length, model, prompt version and canonical JSON hashes of the message
bundle, output format and request options. The reader binds the manifest
to the item's purpose and request SHA and to the batch provider. Existing
rows omit the optional field. Restore and the request doctor validate
the retained binding without claiming access to expired bodies.

The pinned 2320/2320-A replay used the real OpenAI and Anthropic builders
and fake SDK boundaries. The reader fixture inserted one manifest per
submitted item and restored twice. Cleanup removed the transient text and
retained the manifest with byte-exact restore. The fixtures added 325 and
327 gzip bytes respectively to submitted state. The foreign-provider
control rejected save and restore before changing the target repository.
[Qualification](../assets/audits/2026-11-06-frozen-request-manifest-reader/qualification.json).

Commit `3182bc9c` passed CI 42207149596 and relay deployment 42208235918
before native capture was enabled.

<a id="j318"></a>

## J318 — retain the exact frozen request manifest at submission

The submitted item writer captures the J317 manifest from the matching
submitting intent before removing the queue row. It checks the provider,
owner, purpose, custom ID and request checksum. Both native submission
and audited attachment use this boundary. Missing, queued-only, mismatched
and unrecognized request shapes keep the existing metadata. There is no
legacy reconstruction.

The paired pinned 2320/2320-A replay used the real OpenAI and Anthropic
builders and fake SDKs. The submitted manifests matched independent
structural hashes. After removal of the queue and cleanup of batch text,
the dumps restored byte-for-byte. Normalizing the new manifest alone
reproduced baseline SQL. The isolated post-cleanup snapshots grew by
245 and 249 gzip bytes respectively. Provider payload and request identity
matched baseline. Unit controls cover missing and mismatched ownership.
[Qualification](../assets/audits/2026-11-06-frozen-request-manifest-writer/qualification.json).

DA5.6 remains open for incomplete prepared-input profiles. The retained
hashes support verification of known material after cleanup.

<a id="j319"></a>

## J319 — compatible reader for visited Word field and revision markers

The optional Word view metadata can distinguish visited field and revision node
counts from absent evidence. The reader accepts a pair of strict nonnegative
32-bit counts. The maximum serialized view status is 507 bytes under the existing
512-byte cap. Both view flags still report unresolved semantics. These counts do
not establish which field content is displayed or which revisions are accepted.

Legacy serialization and native output stay exact. The reader stage leaves
the production collector unchanged. The next stage will capture the counters
from the existing walk after this reader is deployed.

The pinned 3,055-file Word cohort reproduced the baseline ledger byte for
byte. The ledger compares text, selection, extraction metadata, textbox
counts, component fingerprints and errors. A separate scratch checkpoint
round trip used explicitly synthetic marker counts on one pinned Word
extraction. Both the legacy and new records survived two restores with
identical SQL, successful integrity checks and no foreign-key errors.
[Qualification](../assets/audits/2026-11-06-word-inline-reader/qualification.json).

<a id="j320"></a>

## J320 — record field and revision nodes visited by the Word walk

The legacy traversal counts exact WordML field markers (`fldChar`,
`instrText`, `delInstrText`, `fldSimple`) and revision wrappers (`ins`,
`del`, `moveFrom`, `moveTo`) reported by the existing walk. The count
is by XML node. Both semantic views remain unresolved. Other revision
types, ignored fallback descendants and foreign namespace lookalikes
are outside the marker scope.

The optional bundle is written when either count is positive and the
metadata fits after existing textbox evidence. Absence remains unknown.
Text, selection and prior metadata matched on 3,055 pinned files after
separating the recorded code fingerprints. The bundle appeared on
562 files, including 18 with observed revision wrappers. Across the
cohort it added 26,077 bytes to serialized extraction results.

Two scratch checkpoints compared six distinct native results, with
identical non-extraction row changes. Both passed two exact SQL round
trips, integrity and foreign-key checks.
[Corpus and checkpoint qualification](../assets/audits/2026-11-06-word-inline-writer/qualification.json).

<a id="j321"></a>

## J321 — preserve the exact Senate resolution suffix after a cover letter

The native amendment preparation binds the verified resolution text to
the same captured full extraction. The existing projection checks exact
suffix equality at the computed offset. The receipt retains the full
source hash and character count, the exact emitted range, and the frozen
request hash. No additional runtime read or receipt format is needed.

All 701 pinned Senate text preparations reproduced the same requests,
contexts, observations, downloads and outcomes. The 636 identity
receipts are exact. Independent re-extraction corroborated the complete
source and emitted spans for 65 new suffix receipts. Scans and resumed
contexts are outside this proof.

All 65 paired queued scratch snapshots passed two exact restores.
Removing the new receipt and its lineage copy reproduced baseline
SQL in each pair. No provider call, historical reconstruction or
storage change occurred.
[Qualification](../assets/audits/2026-11-06-senate-prefix-input/qualification.json).

Commit `f809021d` passed exact CI 42213916930 and relay deployment
42214763362.

<a id="j322"></a>

## J322 — read a bounded source witness on frozen Senate continuation work

An optional work field binds the frozen text and context to the exact
source observation, extraction and suffix start. The reader validates
the historical change owner, source document, physical parent and
bounds on save and restore. Once recorded, the witness cannot be
replaced by a later work update. Legacy rows keep their original bytes.
No schema migration or duplicate document body is required.

The 701 pinned text preparations kept the existing requests, receipts
and observations. A controlled work row with a fixture witness passed
two exact restores in each case. Removing the witness reproduced the
legacy checkpoint. Two separate corpus cases exercised the same
validation before request preparation.
[Corpus and checkpoint qualification](../assets/audits/2026-11-06-senate-work-reader/qualification.json),
[pre-request cases](../assets/audits/2026-11-06-senate-work-reader/pre-request-pins.json).

The runtime capture and resumed-request projection remain a separate
release stage. The reader checks bounds and hashes of retained parents.
For a nonzero suffix, the writer must verify the original slice while
the extracted text is still available.

Commit `d24ffd4c` passed exact CI 42214937144 and relay deployment
42215831398.

<a id="j323"></a>

## J323 — preserve the exact Senate source when late amendments resume

The continuation saves the witness with the original frozen text
before preparing a batch request. On resume, the existing amendment
receipt binds the exact frozen request to the retained parent and the
verified text range. There is no reread of the source. Legacy context
without a witness keeps its existing behavior.

All 701 native preparations restored twice before queuing and again
after queuing with the downloader disabled on resume. The requests
and contexts matched the baseline. All 636 identity and 65 cover
suffix ranges matched independent extraction. Removing the witness
and the two receipt fields reproduced baseline SQL in every pair.

The existing late revision scenario now fails before queuing,
restores the saved continuation, removes the original file and
completes the existing revision path. The simulated paid application
retains the request and exact range. Optional controls reject changed
contexts and missing observation parents without changing the queued
request. Scans remain outside this text witness profile.
[Corpus and checkpoint qualification](../assets/audits/2026-11-06-senate-work-writer/qualification.json).

<a id="j324"></a>

## J324 — read frozen scan payloads without a second inline PDF copy

The queued request reader accepts an optional scan reference for
analysis and reanalysis with a native PDF manifest. It checks the
attachment slot using bounded JSON probes, then reconstructs the exact
original payload and validates its SHA-256. Existing inline and
compact-text forms remain supported. The runtime writer is unchanged.

Two pinned PDFs exercised whole-file and cover-letter page windows
through both provider builders. Each compact fixture restored twice
and produced the identical handoff at the fake SDK boundary. After
normalizing the stored request representation, SQLite dumps matched.
The paired isolated gzip dumps differed by 1.42 MB and 3.07 MB per
request on these two files. This is a fixture measurement for the
later writer, which remains inactive.
[Qualification](../assets/audits/2026-11-06-scan-payload-reader/qualification.json).

An initial probe hit Anthropic's token-count endpoint with a dummy key
and received two confirmed 401 responses. That probe and intermediate
runs using the same SDK helper are excluded. Qualification used a
mock token-count transport, fake SDK handoff and blocked sockets.

Commit `c5f0aaf4` passed exact CI 42217155413 and relay deployment
42218217502.

<a id="j325"></a>

## J325 — store one inline PDF in a qualified frozen primary queue item

The queue writer shares the saved PDF when its native manifest agrees
and its base64 occupies exactly one validated provider attachment slot.
It keeps the original payload checksum and uses the existing inline
format for unqualified requests. The compact text profile is unchanged.

Both provider paths on two pinned scans reproduced the exact request,
two checkpoint restores and identical SQL after submission and queue
removal. The isolated queued gzip dumps were smaller by 1.42 MB for
the full scan and 3.07 MB for the cover-trimmed scan. Submission was
simulated at the SDK boundary with sockets blocked.
[Qualification](../assets/audits/2026-11-06-scan-payload-writer/qualification.json).

<a id="j326"></a>

## J326 — omit full source text from new consultation review deliveries

New consultation review delivery copies retain findings, citations,
links and source identities with empty document text. The stored
review remains complete. Existing delivery plans keep their original
representation.

Both pinned plans produced the same Russian and English previews
and weekly digest output. The paired scratch dumps restored twice.
Omitting five copied document texts saved 470,637 SQL bytes and
121,142 gzip bytes in the paired checkpoint comparison. The focused retry control preserves
the same rendered message after failure and restore.
[Qualification](../assets/audits/2026-11-06-consultation-delivery/qualification.json).

Commit `0777b617` passed exact CI 42219457180 and relay deployment
42220134391.

<a id="j327"></a>

## J327 — read exact source lineage for a direct supplement update

The optional editorial profile binds one standalone supplement
update to its applied result, consumed batch item, complete input
receipt and exact source observation. It checks the frozen owner,
filing and physical publication. Normal retention can clear the
delivery and answer payloads while keeping their verified references.
Existing versions and unqualified history keep their original scope.

Sixteen natural preparations from eight official PDFs and ten
controlled cases kept the original requests, receipts and projections.
Manually attached reader profiles survived cleanup and two exact
restores on every case. The legacy publication 240 doctor report
remained byte-identical. Qualification uses controlled answers and
does not activate an editorial writer.
[Qualification](../assets/audits/2026-11-06-supplement-lineage-reader/qualification.json).

<a id="j328"></a>

## J328 — freeze supplement source lineage with its first direct delivery

A new standalone direct supplement update records editorial history
at its first render when the exact applied result, input receipt and
one unambiguous source observation are available. An absent or
ambiguous reference leaves the ordinary delivery path in place.
Existing frozen plans, grouped updates and digest-owned rows remain
outside this producer.

All 26 paired preparations produced the same request and reader
message through the normal Poster entry point. The writer's history
survived cleanup and two restores. SQL outside the new editorial rows
matched in every case. After cleanup those rows added 1,519–1,539
gzip bytes per isolated case. Qualification used controlled answers
and a fake publisher.
[Qualification](../assets/audits/2026-11-06-supplement-lineage-writer/qualification.json).

Commit `655efee9` passed exact CI 42221996508 and relay deployment
42222713911.

<a id="j329"></a>

## J329 — read a bounded explanation of version comparison

The optional trace field validates the ordered compared and retained
candidate positions against the existing outcomes. It distinguishes a
single survivor, matching normative identities and a conflict. When
textual dates actually removed a candidate, the field records the
dates used in that decision. Existing trace identities are unchanged.

Four pinned official comparisons ran on original files and cached
extractions. Six manually attached explanations survived two exact
restores. Two overflowed the existing 4 KiB bound and kept the old
trace and SQL. The paired cached corpus and legacy doctor kept their
previous observations. The runtime writer is inactive.
[Qualification](../assets/audits/2026-11-06-version-decision-reader/qualification.json).

<a id="j330"></a>

## J330 — retain the reason from the actual loose-file comparison

The native chooser returns the existing selection together with the
already computed date filter and normative identity outcome. Eligible
loose-file traces retain the explanation within the existing 4 KiB
budget. Six pinned raw/cache cases saved it and restored twice. The
two overflow cases kept their exact earlier traces.

All 4,276 cached observations matched. The current paired archive
replay kept core and archive decisions across 16 roots and four
controlled inputs. The changed candidate-policy source fingerprint
updated 20 extraction IDs and their linked capture IDs. Both current
revisions diverged from the older J222 archive baseline, so that
historical comparison is outside this parity proof.
[Qualification](../assets/audits/2026-11-06-version-decision-writer/qualification.json).

Commit `8ec713ea` passed exact CI 42224064296 and relay deployment
42225065657.

<a id="j331"></a>

## J331 — read the exact peer of a successful archive version comparison

The optional reader checks a two-member archive comparison against
the retained selected policy row, physical capture and source
extraction. Slot, ordinal path, body hash and physical text hash
must match the saved execution. The comparison records the peer
that may be absent from a retained prefix, within the existing
whole-trace budget. It does not re-evaluate absent source text.

The real RCL 705897 archive retained one selected main and one
supporting row out of sixteen. An independently derived peer witness
survived two exact restores. Normalizing trace records reproduced
baseline SQL. The isolated witness added 557 SQL bytes and 86 gzip
bytes. Original ZIP and three controlled ZIP observations and the
legacy doctor remained exact. Automatic capture remains inactive.
[Qualification](../assets/audits/2026-11-06-archive-version-reader/qualification.json).

Commit `463f15a7` passed exact CI 42225871346 and relay deployment
42226565157.

<a id="j332"></a>

## J332 — append the proven archive comparison after physical persistence

Successful two-member comparisons capture the peer from the existing
policy decision and exact physical execution. The normal source write
keeps its original trace. The deferred writer adds the optional trace
only after standard physical persistence and requires the original
observation link. A failed optional save rolls back independently.

Native RCL 705897 kept the original physical recording time, archive
prefix and selection. A controlled failure after insert preserved the
baseline dump. Repeat and cached reads added one enriched link in
total. Two restores were exact. The isolated append added 2,619 SQL
bytes and 207 gzip bytes. Archive decisions and physical observations
matched across the paired archive cohort after separating recorded
code fingerprints. Conflict and multiple roots are outside this
successful comparison profile.
[Qualification](../assets/audits/2026-11-06-archive-version-writer/qualification.json).

Commit `310741f3` passed exact CI 42227216532 and relay deployment
42228341265.

<a id="j333"></a>

## J333 — read the exact conflicting archive members

The optional profile identifies an attempted outer archive and two
physical slots. The reader corroborates them against the exact root
read, retained execution and both extraction summaries. A conflicting
nested archive remains separate from the final outer selection.
Legacy rows and the 4 KiB limit stay unchanged.

On RCL 12390551 stage 9, the exact 749446.zip comparison used slots
1 and 3. The older generic archive reasons included slots 0 and 2
as well. Substituting a supporting slot was rejected without a write.
The manually attached profile added 142 bytes to the trace and survived
two exact restores. The legacy doctor output remained byte-identical.
Automatic capture is inactive.
[Qualification](../assets/audits/2026-11-06-archive-conflict-reader/qualification.json).

<a id="j334"></a>

## J334 — append the exact nested archive conflict

The chooser marks the two members it actually compared. The deferred
source writer links one unambiguous two-member archive conflict to
its physical execution after the normal history write. Multiple
eligible conflicting roots leave this optional field absent.

The five pinned official ZIPs for RCL 12390551 stage 9 used the same
download path. The nested 749446 conflict retained slots 1 and 3 with
its original physical recording time. Cached repeat, two restores
and the base dump after an injected optional write failure were exact.
The broad archive pair kept its decisions and physical observations
after accounting for the updated code fingerprint.
All 4,276 cached candidate decisions, 7,599 input hashes and 1,383
missing inputs matched the preceding reader.
[Qualification](../assets/audits/2026-11-06-archive-conflict-writer/qualification.json).

The exact `a01cfa59` release passed CI run 42232312851 and relay
deployment 42233467897. Restart and timer were verified.

<a id="j335"></a>

## J335 — retain the outer comparison of ZIP packages

The outer comparator records the actual compared and retained candidate
positions for a conflict involving archive packages. It uses the existing
version evidence format. When the combined trace would exceed 4 KiB, the
previously supported nested evidence takes priority.

On RCL 12390551 stage 9, the outer conflict compared positions 0 and 1.
The independent inner conflict belongs to outer position 3 and member
slots 1 and 3. The five pinned downloads and cached result were identical.
The new trace added 108 bytes. Two restores and the injected optional write
failure passed. Comparing both revisions with the same interpreter, the
checkpoint differed only in the new field and its trace identity and link.
The sample dump grew by 216 bytes of SQL and 42 bytes of gzip.
The paired J334 raw cohort matched all 16 roots and four synthetic
controls. Cached candidate replay matched 4,276 outcomes and coverage,
including 1,383 unavailable inputs.
[Qualification](../assets/audits/2026-11-06-outer-archive-conflict/qualification.json).

The exact `c222b431` release passed CI run 42235158196 and relay
deployment 42236442549. Restart and timer were verified.

<a id="j336"></a>

## J336 — read compact frozen committee amendment scans

The frozen scan reader accepts the existing PDF reference profile for
committee amendments with an empty text field and a typed scan manifest.
It verifies the provider attachment slot and the original request hash.
The text reference remains scoped to primary analysis. The writer still
stores committee scan requests inline.

The pinned J232 public replay matched all 22 preparation paths, including
14 text paths and eight scan paths from four official PDFs. Both OpenAI
and Anthropic request builders round-tripped the manually packed scans.
The old reader rejected those compact forms. The new reader restored them
twice through SQLite and kept the submitting request byte-exact in the
focused tests. No provider batch was submitted.
[Qualification](../assets/audits/2026-11-06-committee-scan-reader/qualification.json).

The exact `e2993cfe` release passed CI run 42236699461 and relay
deployment 42237789610. Restart and timer were verified.

<a id="j337"></a>

## J337 — store one inline PDF for qualified committee amendments

Frozen committee scan requests store one copy of the PDF and an exact
provider attachment reference. Ambiguous payload slots and requests
without a matching scan manifest stay inline.

The J232 cohort produced eight scan and 14 text paths. All text records
matched, as did the scan context, selection, and request hashes. A full
public OpenAI dump saved 56,645–183,981 SQL bytes and 34,345–127,830 gzip
bytes per scan. Controlled queues for both providers restored twice and
submitted the same request to mock transports.
[Qualification](../assets/audits/2026-11-06-committee-scan-writer/qualification.json).

The exact `f0d6ee46` release passed CI run 42238010102 and relay
deployment 42238720278. Restart and timer were verified.

<a id="j338"></a>

## J338 — validate legacy projections without hydrating modern references

The legacy validator reads raw stored JSON through its own SQLite cursor.
The normal repository reader still checks modern projection ownership
and generation. The previous validator tried to hydrate a modern reference
from a single selected column without the owner fields and rolled back.

On the pinned state copy, the old failure left the dump byte-exact. The
fixed import retained 334 legacy values across 438 occurrences, with 283
replacements. Public views of 516 bills, 206 changes, and 165 memos matched,
as did 12 raw modern references. The second import was a no-op and two
restores were exact. The sample dump grew by 593,042 SQL bytes and 133,498
gzip bytes (2.53%). Production import remains a separate manual operation.
[Qualification](../assets/audits/2026-11-06-legacy-raw-validation/qualification.json).

The exact `f89b607d` release passed CI run 42238915113 and relay
deployment 42240242715. Restart and timer were verified.

<a id="j339"></a>

## J339 — read the original committee scan context without a PDF copy

A validated paid result can carry the original comparison context, source
URL, request and question hashes, and full PDF manifest in a 4 KiB record.
The reader reconstructs the question only from a supplied matching PDF.
It checks the paid result owner, request, manifest and exact question hash.
Submitted-item metadata preserves the same evidence across rewrites and
restores; a queued intent cannot carry it. Old records stay unavailable when the original context was never captured.

All eight J232 scan paths from four official PDFs restored the exact
question through controlled paid records. The retained witness occupied
979–988 bytes. Fourteen text preparations were controls. The pinned legacy
dump and JSON for all 14 retained results matched across revisions. The
runtime writer and correction consumer remain inactive.
[Qualification](../assets/audits/2026-11-06-amendment-scan-context-reader/qualification.json).

The exact `f4f3e2f6` release passed CI run 42240448888 and relay
deployment 42241768429. Restart and timer were verified.

<a id="j340"></a>

## J340 — retain committee scan context from the submitted request

The writer captures the bounded witness from the exact frozen submitting
row when submission is confirmed. A validated paid result keeps it after
operational cleanup. Failed collection keeps only the submitted evidence.
There is no extra copy in the queued request or a historical backfill.

The 24 offline paths included eight successful scans, two scan failures,
and 14 text controls. Requests, normalized SQL, cost and delivery work
matched after excluding the new witness. Cleanup and two restores preserved
the evidence. A successful isolated dump grew by 153–242 gzip bytes.
The submitted copy accounts for 8–23 gzip bytes and remains available for
cross-checking the paid record. The correction consumer stays separate.
[Qualification](../assets/audits/2026-11-06-amendment-scan-context-writer/qualification.json).

The exact `cc30808b` release passed CI run 42241990396 and relay
deployment 42243207926. Restart and timer were verified.

<a id="j341"></a>

## J341 — correct an amendment from the exact original scan question

The public correction command resolves the root content version and its
paid result. For a witnessed committee scan it reads the saved URL once
and reconstructs the original question only on an exact PDF match.
Changed or missing files stop before preparation, reservation or queue
writes. Outages and cancellation propagate through the existing path.

On the official 2467-A PDF, the offline command reused the original
question after cleanup and two restores, then edited the original message
through the test publisher. A repeated force command while queued made
no additional read or request. Changed PDF, missing file, outage and
cancellation made one read attempt each with no new preparation or state
write. All 24 existing J340 lifecycle paths repeated byte-exact.
[Qualification](../assets/audits/2026-11-06-original-scan-correction/qualification.json).

The exact `5ff1d67d` release passed CI run 42243444846 and relay
deployment 42244530663. Restart and timer were verified.

<a id="j342"></a>

## J342 — read prepared input receipts for one RCL archive

The reader verifies the exact RCL source observation and ZIP body read,
the retained extraction, and each selected part's physical slot, member
path, leaf hash and text extent. The ordered segments still describe the
emitted text. Hybrid roots and missing evidence do not qualify.

The pinned 778150.zip preparation produced 259,726 characters, 12
segments, and six selected parts. The manually attached receipt occupied
2,876 bytes and added 553 gzip bytes to the queued sample. It survived
submission, consumption, cleanup and two restores. Swapped slots were
rejected. The legacy dump and two existing loose profiles matched.
The runtime writer remains disabled.
[Qualification](../assets/audits/2026-11-06-rcl-archive-input-reader/qualification.json).

The exact `ee13ecef` release passed CI run 42244750519 and relay
deployment 42245525231. Restart and timer were verified.

<a id="j343"></a>

## J343 — save the actual prepared map from one RCL ZIP

The runtime attaches the one-root receipt to the actual prepared request
when the retained observation, full member texts and extraction slots
match. Cached inputs with missing parents, hybrid roots and failed
receipt validation preserve the ordinary queue.

The 17 paired paths matched request bytes, selection, cost and reads.
Only the raw 778150.zip case retained the receipt. A forced parent
validation failure preserved the plain request. The real offline apply
and cleanup removed the ordinary payload and kept the map through two
restores. The retained record added 2,902 SQL bytes and 570 gzip bytes
after cleanup.
[Qualification](../assets/audits/2026-11-06-rcl-archive-input-writer/qualification.json).

The exact `7cb97701` release passed CI run 42246269671 and relay
deployment 42247165823. Restart and timer were verified.

<a id="j344"></a>

## J344 — count verified corpus splits across sealed runs

The offline census takes explicit manifest pins. It counts exact case
IDs, recorded groups and byte aliases separately, and checks review
records against their pinned queues. Review forms do not add cases.
Evaluation consumption stays unknown without a supported record.

Five real seals yielded 177 diagnostic cases in 105 groups and three
reserved holdout cases in three groups. The 180 distinct case IDs form
165 components when known byte aliases are joined. No verified overlap
or human question labels were present in these runs. Repeated reports
matched byte-for-byte. Independent human review remains open.
[Qualification](../assets/audits/2026-11-06-corpus-split-census/qualification.json).

The exact `27f9c6de` release passed CI run 42247382377 and relay
deployment 42248364412. Restart and timer were verified.

<a id="j345"></a>

## J345 — verify footnote paths in standalone Flat OPC

The opt-in replay checks the call, containing paragraph and definition
against the SHA-pinned original XML package. Paths are relative to the
outer document. It uses the same Word body traversal as the legacy text
and keeps incomplete or unsupported proof outside the result.

The official RCL 12403303 bill and impact assessment confirmed seven and
14 links respectively. All 14 impact-assessment calls were inside table
cells. The paired cohort of 13 XML files preserved text, selected
documents, law fingerprints and physical extraction metadata. The
production route and dump do not carry the proof.
[Qualification](../assets/audits/2026-11-06-flat-opc-notes/qualification.json).

The exact `19fa8779` release passed CI run 42248577611 and relay
deployment 42249445140. Restart and timer were verified.

<a id="j346"></a>

## J346 — verify the submitted request identity of secondary sends

The read-only audit adds the exact batch item identity and ordered
queue, submit, collect and consume times to the existing status-change
projection and send proof. Use `tools.primary_delivery_cycle` with
`--secondary-application-id` and `--include-request-prefix`. Default
reports stay identical.

The pinned ff49a74a state matched publications 231 and 240. The original
prepared input receipt and collection application were absent, and the
request payload and source selection remain unverified. The census of
50 original sends still proves only the full cycle for druk 3281. The
database and repeated reports matched byte-for-byte.
[Qualification](../assets/audits/2026-11-06-secondary-request-prefix/qualification.json).

The exact `cccfeb39` release passed CI run 42250172227 and relay
deployment 42251493855. Restart and timer were verified.

<a id="j347"></a>

## J347 — retain a successful outer ZIP version choice

The outer decision records only the archives the comparator actually
compared when their completed native reads establish the root hashes.
The existing size guard preserves the base trace and nested evidence.

On RCL 12337613 stage 12, positions 0 and 2 were compared and position 2
survived the date filter. The adjacent PDF was outside the comparison.
Selection, text, downloads and physical identities matched. The sample
dump added 139 SQL bytes and 55 gzip bytes. Old and new readers restored
the new trace twice. The raw pair matched 16 roots and four controls.
The broad cached pair matched all 4,276 outcomes and 1,383 missing inputs.
[Qualification](../assets/audits/2026-11-06-outer-archive-choice/qualification.json).

The exact `7a0b1790` release passed CI run 42251735573 and relay
deployment 42253001766. Restart and timer were verified.

<a id="j348"></a>

## J348 — validate a literal citation on one physical PDF page

The offline command `tools.replay_pdf_page_citation` checks an explicit
case pin against the original PDF, page extraction and legacy text.
It requires a unique literal quote entirely inside one normalized page.
Collapsed boundaries, embedded page separators and mismatched pins fail.

The controlled quote in official RCL PDF 587739 matched page 1 at
`[204, 331)`. The proof matched across checkouts; code fingerprints
differed as expected. Geometry, semantics and paid-input retention
are outside the check.
[Case](../assets/audits/2026-11-06-pdf-page-citation/case.json) and
[qualification](../assets/audits/2026-11-06-pdf-page-citation/qualification.json).

The exact `9bd571b1` release passed CI run 42253278845 and relay
deployment 42254325917. Restart and timer were verified.

<a id="j349"></a>

## J349 — verify literal quotes in two multipart prepared inputs

The offline replay validates the frozen queued request and retained
parent records in one pinned SQLite snapshot before locating a unique
quote in the first bill part and exact prepared input.

The native Sejm 2320 preparation retained `[1297, 1411)`, and RCL
778150 retained `[157, 272)`. Missing parents, wrong request identity
and wrong quote rejected both cases. Database bytes stayed exact.
Reports matched across checkouts apart from the code pins. The claims
are controlled; semantic support and provider submission are unassessed.
[Qualification](../assets/audits/2026-11-06-multipart-citation/qualification.json).

The exact `898fd6c1` release passed CI run 42254588640 and relay
deployment 42255754292. Restart and timer were verified.

<a id="j350"></a>

## J350 — read original card lineage for one RCL archive

The existing card evidence identity now accepts a verified one-root
archive receipt and checks its source observation, physical slots and
paid/application parents. The public audit confirms only a recorded
original send. The ordinary candidate writer remains disabled.

In the native 778150.zip case, manually attached evidence survived
cleanup and two exact restores. The public audit reported six exact
source parts after the mock send. Queued and unknown sends remained
unconfirmed. A wrong observation was rejected without a state write.
The legacy audit matched byte-for-byte.
[Qualification](../assets/audits/2026-11-06-rcl-archive-card-reader/qualification.json).

The exact `5dc1a5d1` release passed CI run 42256002182 and relay
deployment 42256857976. Restart and timer were verified.

<a id="j351"></a>

## J351 — attach qualified archive lineage to the original card

The ordinary card writer includes the one-root RCL archive profile in
its existing candidate query. The same reader validates the exact source,
prepared request and paid/application parents before saving the link.

The native 778150.zip case kept the same render, delivery plan and test
message identity. Repeated publishing was idempotent. A missing collection
parent preserved ordinary delivery with no lineage. After cleanup the
sample added 206 SQL bytes and 91 gzip bytes, and both versions of the
reader restored the saved dump twice.
[Qualification](../assets/audits/2026-11-06-rcl-archive-card-writer/qualification.json).

The exact `5d2e7fe5` release passed CI run 42257112369 and relay
deployment 42258416505. Restart and timer were verified.

<a id="j352"></a>

## J352 — read compact direct stage content

The optional profile binds a frozen render and typed status event to a
same-owner sent original card. It preserves exact visible content after
delivery expiry and cannot restore a full historical Bill for corrections.
The amendment command retains its existing handling of direct stage updates.

Four of 52 pinned historical candidates met the exact conditions.
Their renders matched the existing delivery. After cleanup the manually
attached records added 29,828 SQL bytes and 7,117 gzip bytes in total.
Both restores were exact. Wrong event or anchor and unknown delivery
were rejected. The ordinary writer remains disabled.
[Qualification](../assets/audits/2026-11-06-direct-stage-content-reader/qualification.json).

The exact `1c1aedca` release passed CI run 42258643248 and relay
deployment 42259736188. Restart and timer were verified.

<a id="j353"></a>

## J353 — save compact content for qualified direct stage updates

The writer attaches the compact version only on the first freeze of a
qualified direct stage update. The full delivery plan stays available
until ordinary expiry. Unqualified updates still follow ordinary delivery.

Across four pinned replays, both revisions sent the same text, plan and
test message IDs. For two older RCL updates, both used the existing
pre-attempt attention and participation refresh and differed from the
historical render. Legacy frozen, ambiguous anchor, budget and changed
event controls wrote no version. After expiry the four records added
30,231 SQL bytes and 6,978 gzip bytes. Both readers restored the dumps twice.
[Qualification](../assets/audits/2026-11-06-direct-stage-content-writer/qualification.json).

The exact `67689f9e` release passed CI run 42259940058 and relay
deployment 42261263234. Restart and timer were verified.

<a id="j354"></a>

## J354 — read a prepared map from one ZIP and one self-amendment

The reader checks the exact archive and standalone extraction parents
for a saved RCL observation. It requires the recorded self-amendment
URL and complete terminal PDF span. Missing parents, partial spans and
unrelated alternatives remain outside this profile.

In the native 785848.zip and 787193.pdf case, the nine segments covered
90,271 prepared characters. The manually attached receipt added 2,574
SQL bytes and 518 gzip bytes in the queue. The mocked submitted and
consumed item kept the map after cleanup and two restores. The previous
reader rejected the new profile atomically. Automatic capture stays off.
[Qualification](../assets/audits/2026-11-06-hybrid-input-reader/qualification.json).

The exact `998a2de3` release passed CI run 42261453165 and relay
deployment 42262708414. Restart and timer were verified.

<a id="j355"></a>

## J355 — retain the actual prepared map for ZIP and self-amendment

The writer attaches the already fitted map when the archive and the
separate self-amendment have exact retained parents. The ordinary queue
remains available when a parent is missing or validation fails.

All 17 pinned paths preserved requests, selection, downloads, cost and
fallbacks. In the native 785848.zip and 787193.pdf case the nine-segment
map survived mocked submission, collection, application and cleanup.
The cleaned dump added 2,574 SQL bytes and 524 gzip bytes. The prior
compatible reader restored all four lifecycle checkpoints twice.
[Qualification](../assets/audits/2026-11-06-hybrid-input-writer/qualification.json).

The exact `bff4f8c2` release passed CI run 42262951045 and relay
deployment 42264131387. Restart and timer were verified.

<a id="j356"></a>

## J356 — record declared judgments over a pinned saved answer

The offline intake binds the saved result, request, text and claim
hashes before checking literal spans. Reviewer-declared judgments are
separate from mechanical evidence. Unknown and conflict stay explicit.
The blank template supplies the pins and leaves judgment fields empty;
intake rejects the template until a reviewer completes it.

On the authentic 3231 snapshot the synthetic control verified one
literal citation and contributed zero independent semantic labels.
The eight-slot template, prior HTML and repeated report matched.
No saved answer, reader card or provider request was changed.
[Qualification](../assets/audits/2026-11-06-primary-review-intake/qualification.json).

The exact `ede23bbc` release passed CI run 42264350238 and relay
deployment 42265191231. Restart and timer were verified.

<a id="j357"></a>

## J357 — retain the exact mixed outer version comparison

A successful outer comparison can include a standalone file whose
native complete extraction proves the exact bytes and text. Archive
participants keep the existing native root requirement. Unknown and
custom extraction paths preserve the earlier behavior.

On RCL 12388203 stage 4 the ZIP and DOCX were compared, and the ZIP
survived the existing date filter. Both downloads, selected text and
physical identities matched. The trace added 139 SQL and 62 gzip bytes.
The previous reader restored it twice. The current raw pair matched 16
roots and four controls, and all 4,276 cached decisions matched.
The unit control with equal ZIP and DOCX text now checks both compared
positions and the matching-policy-identity outcome.
[Qualification](../assets/audits/2026-11-06-mixed-outer-choice/qualification.json).

The exact `e2d465a2` release passed CI run 42265952087 and relay
deployment 42267188218. Restart and timer were verified.

<a id="j358"></a>

## J358 — read compact frozen Senate scan requests

The compatible reader verifies the Senate source check, full PDF window,
exact scan manifest and provider attachment slot. The ordinary writer
continues to store Senate scan requests inline.

Four pinned term-10 resolutions each passed both provider request
builders and two exact compact restores. The restored frozen requests
matched the original. The preceding reader accepted inline controls and
rejected compact ones without changing state. No provider was called.
[Qualification](../assets/audits/2026-11-06-senate-scan-reader/qualification.json).
Exact release verified for `176f0cfc` via [CI](https://github.com/sobolevbel/lexinform/actions/runs/42267403238) and [relay](https://github.com/sobolevbel/lexinform/actions/runs/42268575508).

<a id="j359"></a>

## J359 — store one PDF in qualified frozen Senate scan requests

The queue writer uses the compatible reader's full-scan and exact
source checks. It keeps one PDF and a verified provider attachment
reference. Other requests retain their existing format.

Four official resolutions passed both offline provider builders,
mock submission and two restores at each checkpoint. Request and
context hashes, metadata and submitted SQL matched. In the single-item
queue samples the writer saved 410,157–1,849,285 SQL bytes and
297,057–1,345,975 gzip bytes. The previous compatible reader restored
the writer's pinned dumps exactly.
[Qualification](../assets/audits/2026-11-06-senate-scan-writer/qualification.json).
Exact release verified for `b462dd8c` via [CI](https://github.com/sobolevbel/lexinform/actions/runs/42268814718) and [relay](https://github.com/sobolevbel/lexinform/actions/runs/42270170658).

<a id="j360"></a>

## J360 — read bounded Office Math source structures

The offline reader inventories formulas in the main OOXML document body.
It records source paths, ordered structure and explicit unsupported or limited
outcomes. Word rendering and formula semantics remain unresolved. Runtime
extraction, selection, request building and state writes continue on their
existing paths.

The pinned corpus slice covers 69 paths and 28 unique bodies, including
12 controls without formulas. It records 117 structured formulas, 49 unsupported
and eight over the formula bounds. Native RCL selection confirmed three primaries
and two unverified controls. Missing companion sources are listed in the
qualification. Changing the divisor from 100% to 200% in one selected source
changed its formula structure and checksum while preserving the legacy text
and law digest.

[Input manifest](../assets/audits/2026-11-06-word-formulas/manifest.json) ·
[Qualification](../assets/audits/2026-11-06-word-formulas/qualification.json).
Exact release verified for `97fa2167` via [CI](https://github.com/sobolevbel/lexinform/actions/runs/42271621589) and [relay](https://github.com/sobolevbel/lexinform/actions/runs/42272638342).

<a id="j361"></a>

## J361 — verify Word formula positions in prepared text

The offline placement check requires one exact retained source span covering
the formula gap and its two neighboring paragraphs. It rejects a changed XML
identity, unsupported formulas, absent anchors, duplicate copies and generated
text that happens to contain the same bytes.

In the selected RCL 12410805/2 source 778990.docm the two formula gaps map
to character offsets 4324 and 4929 in the prepared text. An unsupported
formula in RCL 12387701/4 prevents placement for that input. Removing an
anchor or replacing it with generated text also prevents placement.
The native offline request bytes and estimated cost stayed identical with
and without the placement observer. The positive control counted 5,124
input tokens. The replay uses pinned local source files and tokenizer data.

[Qualification](../assets/audits/2026-11-06-word-formula-placement/qualification.json).
Exact release verified for `0a447de7` via [CI](https://github.com/sobolevbel/lexinform/actions/runs/42273399049) and [relay](https://github.com/sobolevbel/lexinform/actions/runs/42274168418).

<a id="j362"></a>

## J362 — read compact frozen supplemental scan requests

The compatible reader recognizes a complete scanned government position or
impact assessment only when the supplemental context, full scan receipt and
exact provider attachment agree. The writer continues to
store supplemental scan requests inline.

Six pinned term-10 supplements passed both provider builders offline. The old
reader rejected all 12 compact requests. The new reader accepted inline and
compact requests, restored both checkpoints twice and produced identical
request and submitted-state hashes through mock submission.

[Qualification](../assets/audits/2026-11-06-supplement-scan-reader/qualification.json).
Exact release verified for `b97152ff` via [CI](https://github.com/sobolevbel/lexinform/actions/runs/42274392891) and [relay](https://github.com/sobolevbel/lexinform/actions/runs/42275506826).

<a id="j363"></a>

## J363 — read a bounded Word formula companion in queued requests

The compatible reader accepts an optional Word formula companion in a frozen
OpenAI primary request. It validates the source observation, selected Word
artifact, extraction receipt, text anchors and exact provider input. The
ordinary producer still omits the companion.

The pinned RCL 12410805/2 request stayed byte-identical on the ordinary path.
A constructed companion using its two verified formula positions survived
queue restore. The reader refused a different source owner and a changed
formula in the frozen provider input even with a recomputed payload hash.
The prior reader restored the ordinary dump and rejected the enriched
dump atomically.

[Qualification](../assets/audits/2026-11-06-word-formula-companion-reader/qualification.json).
Exact release verified for `b57d31e8` via [CI](https://github.com/sobolevbel/lexinform/actions/runs/42275732868) and [relay](https://github.com/sobolevbel/lexinform/actions/runs/42277033667).

<a id="j364"></a>

## J364 — store one PDF in qualified frozen supplemental scan requests

The queue writer stores one PDF with an exact provider attachment reference
for a full scanned government position or impact assessment. Other requests
use their existing storage representation.

Six pinned official files passed both offline provider builders and mock
submission. The writer and previous compatible reader restored the compact
checkpoints exactly. The single-intent samples saved 408,273–2,285,529 SQL
bytes and 291,860–1,636,748 gzip bytes.

[Qualification](../assets/audits/2026-11-06-supplement-scan-writer/qualification.json).
Exact release verified for `aa79cc2f` via [CI](https://github.com/sobolevbel/lexinform/actions/runs/42277290923) and [relay](https://github.com/sobolevbel/lexinform/actions/runs/42278544765).

<a id="j365"></a>

## J365 — optionally attach verified Word formula structure to primary analysis

The writer uses the already downloaded Word body and admits the companion
only for the matching selected source, exact retained placements and bounded
OpenAI primary context. `LEXINFORM_WORD_FORMULA_INPUTS_ENABLED` defaults to
false. The disabled native path preserved selection, extraction identities,
frozen request and SQL dump byte-for-byte against the previous release.

On RCL 12410805/2 the enabled request used 6,044 input tokens instead of
5,124. The local reservation estimate rose from $0.060342 to $0.062746.
The queued dump increased by 6,124 SQL bytes and 981 gzip bytes. The
submitted dumps had the same size and did not retain the companion tree.
No source was downloaded a second time. Changing the divisor changed the
enriched request identity while preserving the legacy text and law digest.
Eight refusal controls fell back to the ordinary request. Mock submission
and restore used the exact frozen requests.

[Qualification](../assets/audits/2026-11-06-word-formula-companion-writer/qualification.json).
Exact release verified for `d97b8328` via [CI](https://github.com/sobolevbel/lexinform/actions/runs/42278730632) and [relay](https://github.com/sobolevbel/lexinform/actions/runs/42279453151).

<a id="j366"></a>

## J366 — repeat the retained delivery census on a newer state snapshot

A read-only audit of state `533529bf` found one exact retained primary
cycle among 51 sent new-bill rows. The one additional row since J346
shares the physical root of an older government-plan publication and
has no frozen original plan. It adds no direct-original proof.
Both secondary chains still have a verified submitted identity and
send suffix. The original prepared input and collection receipt remain
unavailable. DA10.7 remains open.

[Qualification](../assets/audits/2026-11-06-retained-delivery-census/qualification.json).
Exact release verified for `b2e7809e` via [CI](https://github.com/sobolevbel/lexinform/actions/runs/42279706606) and [relay](https://github.com/sobolevbel/lexinform/actions/runs/42280954430).

<a id="j367"></a>

## J367 — inventory explicit Word n-ary operators offline

The opt-in corpus profile records operator properties and ordered lower,
upper and body operands. It rejects ambiguous property structure and
keeps rendered meaning unresolved. The production parser and frozen
companion stay on their existing profile.

On the J360 manifest the profile recognized 19 more formulas across eight
paths. Text and selection matched for every path. Replacing a sum with a
product changed the recorded structure and checksum while preserving the
legacy text and law digest. Only two entries fit the standalone request
shape and size preflight. Both point to the same file, whose verified
native selection is multipart. Preflight uses synthetic anchors and
establishes no request eligibility.

The previous release candidate also restored the actual J365 enabled
queue twice with the same frozen request and dump bytes.

[Qualification](../assets/audits/2026-11-06-word-nary-source/qualification.json).
Exact release verified for `224ed04c` via [CI](https://github.com/sobolevbel/lexinform/actions/runs/42281161841) and [relay](https://github.com/sobolevbel/lexinform/actions/runs/42282271102).

<a id="j368"></a>

## J368 — detect indirect corpus split overlap

The census follows known byte aliases and recorded groups through the
same connected component when looking for holdout leakage. It still
counts raw-byte components separately. These links are exclusion
evidence and assign no new initiative identities or human labels.

A sealed regression control found one reserved case the previous census
missed through a byte alias followed by a recorded group. An unrelated
reserved case stayed separate. The five real J344 seals returned the
same report byte-for-byte, including 180 cases and three reserved cases.

[Qualification](../assets/audits/2026-11-06-corpus-split-overlap/qualification.json).

<a id="j369"></a>

## J369 — enforce reviewer uniqueness across sealed corpus runs

The census deduplicates exact pinned answer rows. It refuses different
answer pins from the same declared reviewer for one case, matching the
single-run recorder's uniqueness rule. It preserves the input seals and
leaves choosing the authoritative submission to the operator.

A sealed control reproduced the historical acceptance of two submissions
from one reviewer. The corrected census refused them before writing a
report. Two copies of the exact same answer still contributed one review.
The five real J344 seals returned the same report byte-for-byte.
No independent human judgments were added.

[Qualification](../assets/audits/2026-11-06-corpus-review-identity/qualification.json).
Exact release verified for `bc6851aa` via [CI](https://github.com/sobolevbel/lexinform/actions/runs/42283909548) and [relay](https://github.com/sobolevbel/lexinform/actions/runs/42285108700).

<a id="j370"></a>

## J370 — include observed archive members in corpus split checks

The split census compares a standalone original with nonempty archive
member bytes recorded in a sealed empty-review queue. It skips unread
members and does not infer a shared initiative from shared bytes.

The regression control covers a holdout PDF that matches a diagnostic
archive member. On the five J344 seals, the 180 cases now form 163
byte-connected components instead of 165. Eight member occurrences
were re-read from five pinned containers and matched their recorded
checksums. The three reserved cases still have no verified overlap in
these seals. Existing human review counts are unchanged.

[Qualification](../assets/audits/2026-11-06-corpus-archive-alias/qualification.json).
Exact release verified for `8e542eb5` via [CI](https://github.com/sobolevbel/lexinform/actions/runs/42285315584) and [relay](https://github.com/sobolevbel/lexinform/actions/runs/42286521257).

<a id="j371"></a>

## J371 — bind derivative review excerpts to verified raw sources

New annotation queues carry the exact raw path and SHA when a pinned
derivative was selected and its raw input was re-read. The split census
uses this evidence for overlap checks. Cached excerpts without a verified
derivative still omit the binding, and existing sealed queues stay intact.

All 177 annotation excerpts and three reserved originals were reverified
against their historical seals. The historical empty review contains
170 container pins and 355 leaf hash rows. The newly sealed annotation
generation verified all 177 raw sources. Its previous case fields and
excerpts matched the baseline, and the combined census retained 180
cases, 163 byte-connected components and zero observed holdout overlaps.
No human labels or evaluation-use records were added. The historical
holdout archive scan covered size-matching direct members.

[Qualification](../assets/audits/2026-11-06-corpus-annotation-provenance/qualification.json).
Exact release verified for `c4b0cc25` via [CI](https://github.com/sobolevbel/lexinform/actions/runs/42286743655) and [relay](https://github.com/sobolevbel/lexinform/actions/runs/42287542185).

<a id="j372"></a>

## J372 — bind review targets to the verified queue bytes

Form generation and review recording now parse the result bytes only
after checking their hash against the verified seal and rechecking the
manifest pin. A queue changed during that read is rejected before the
caller receives any review target.

A controlled edit after seal verification reproduced historical acceptance
of a changed case title. The new form refused it without writing output.
The four real seals yielded the same targets as before, including three
holdout originals, the 170-case empty queue and both 177-case excerpt
generations. This is an integrity check, and adds no human labels.

[Qualification](../assets/audits/2026-11-06-corpus-review-queue-seal/qualification.json).
Exact release verified for `01f917c8` via [CI](https://github.com/sobolevbel/lexinform/actions/runs/42287761454) and [relay](https://github.com/sobolevbel/lexinform/actions/runs/42288734301).

<a id="j373"></a>

## J373 — verify retained evidence after supplemental scan cleanup

The J364 six-file slice ran through both frozen provider builders,
mock submission, validated collection, accounting, provider-forgotten
state and ordinary payload cleanup. All 12 cases kept their scanned
source manifest, supplement number, source URL and exact request
checksum in the result. Submitted metadata retained the request
manifest and scan identity. Each cleaned dump restored twice exactly.

The cleaned state omits the PDF and frozen provider request. The result
and manifest can identify known bytes, but do not recover the original
prompt context or prove a publication. This qualifies the retained
identity for these scans. A replayable original context and delivery
proof remain separate requirements.

[Qualification](../assets/audits/2026-11-06-supplement-scan-retention/qualification.json).
Exact release verified for `f54b7361` via [CI](https://github.com/sobolevbel/lexinform/actions/runs/42288901512) and [relay](https://github.com/sobolevbel/lexinform/actions/runs/42289846734).

<a id="j374"></a>

## J374 — read the original full Senate scan question without PDF bytes

The amendment witness reader accepts version 2 for a full Senate scan.
It requires the original resolution check to match the manifest and the
submitted item's owner and source. A validated paid result must also
carry corroborating Senate answer evidence. The version 1 committee
profile remains available. Ordinary writes still omit the Senate witness.

Four pinned official PDFs passed both provider request builders.
Each reconstructed question matched the original hash after an exact
PDF reread. The witnesses occupy 1,346–1,546 bytes. Submitted metadata
with the constructed witness survived two exact restores in all eight
cases. The ordinary requests and dumps matched the earlier replay.
The preceding reader refused a version 2 checkpoint and preserved its
legacy state. The correction consumer and automatic witness writer
remain open.

[Qualification](../assets/audits/2026-11-06-senate-scan-context-reader/qualification.json).
Exact release verified for `56dbb3ef` via [CI](https://github.com/sobolevbel/lexinform/actions/runs/42290054935) and [relay](https://github.com/sobolevbel/lexinform/actions/runs/42291286826).

<a id="j375"></a>

## J375 — correct a Senate scan from its retained original question

The correction command accepts a retained Senate scan witness only
for the original publication, source kind, owner and URL. It reads the
PDF once and reconstructs the exact original question with its Senate
source check. The existing committee scan path remains compatible.

The official Senate resolution for druk 377 passed both offline provider
builders. A constructed submitted witness was collected and then used
after cleanup and two restores. The command queued the identical question
and the fake publisher edited the original message. A repeated command
while queued did not fetch or prepare again. Changed PDF, missing file,
outage and cancellation each stopped before preparation or a state write.
The committee v1 replay matched the direct parent byte-for-byte.

[Qualification](../assets/audits/2026-11-06-senate-scan-correction/qualification.json).

<a id="j376"></a>

## J376 — retain the original Senate scan context on confirmed submission

The writer captures the version 2 witness from a qualified frozen
Senate request when the submission is confirmed. A validated result
retains the same witness through cleanup. The queued request keeps its
existing representation and the PDF is not copied into the witness.

Four official PDFs passed both offline builders, mock submission and
collection, cleanup, restore and correction. The ordinary frozen
requests and the constructed reader checkpoints matched exactly.
The witness added 259–267 gzip bytes to an isolated submitted dump.
After cleanup, the item and result witnesses added 295–303 gzip bytes
against the same dumps with only those witness fields removed.

[Qualification](../assets/audits/2026-11-06-senate-scan-context-writer/qualification.json).

<a id="j377"></a>

## J377 — bind secondary scan metadata to the queued question

A queued amendment or supplement with a recorded scan manifest must
carry the same manifest in its frozen question. Save and restore
refuse a mismatch before submission. Legacy queues without the
optional manifest remain readable.

The previous revision accepted the mismatched Senate and government
position controls. The new reader refused them and preserved existing
state on failed restore. Eight Senate and 12 supplement corpus reports
matched their previous versions byte-for-byte. The pinned state copy
restored with both existing queued intents. The J375 rollback reader
also restored the actual J376 submitted and cleaned checkpoints twice
without changing the request or original question identity.

[Qualification](../assets/audits/2026-11-06-secondary-scan-input-binding/qualification.json).

<a id="j378"></a>

## J378 — verify the provider attachment of frozen secondary scans

The queue checks the PDF slot in recognized OpenAI and Anthropic
envelopes against the manifested scan. It validates the request ID,
provider and model as part of the envelope check. Save, restore and
pre-submission reads reject a substituted attachment. Records without the manifest or a frozen payload keep their existing
path. Valid JSON without provider envelope fields does not establish
attachment evidence.

Both builders previously accepted a different attachment with a freshly
computed payload checksum. The new guard rejects those controls and
preserves state on failed restore. Eight Senate and 12 supplemental
scan lifecycle reports match J377 byte-for-byte.

[Qualification](../assets/audits/2026-11-06-secondary-scan-provider-binding/qualification.json).

<a id="j379"></a>

## J379 — bind primary scan selections and frozen provider attachments

Primary intents with a recorded scan manifest must match both their
frozen question and the PDF attached to a recognized provider request.
The shared queue check still covers amendments and supplements. Legacy
primary rows without the manifest remain readable.

Pinned Sejm prints 24 and 25 passed both offline provider builders,
including the whole-file case and the cover-letter removal case.
Their selection manifests, request hashes, checkpoint identities and
mock SDK handoffs matched the direct parent. The previous reader
accepted rehashed substituted primary attachments. The new guard
rejects both provider controls and rejects a mismatched selection. Eight Senate and twelve
supplement scan lifecycle reports matched the direct parent byte-for-byte.

[Qualification](../assets/audits/2026-11-06-primary-scan-provider-binding/qualification.json).

<a id="j380"></a>

## J380 — audit retained scan manifests against consumed batch items

The computation doctor compares retained primary, amendment and supplement scan
manifests with the consumed item's metadata and frozen request receipt. Known
conflicts are errors. Missing evidence remains unverified. The doctor reads
the checkpoint without adding state or restoring removed bytes.

The pinned J376 mock OpenAI run for Senate amendments to print 377 records one
bound scan after cleanup. Changing the item's scan hash fails the check and
removing the item yields one unverified result. The saved state checkpoint has
14 computation rows with no eligible retained scan manifest and gives a
byte-identical report against the parent. Focused unit controls also cover
primary and supplement records and a changed request SHA.

A newer state checkpoint at `9a802a55` also gives byte-identical computation
reports across 19 results. The delivery census still records one complete
direct primary cycle. It now records five secondary suffixes, including three
additional amendment applications with exact submitted request identity. Their
source selection, original request body and collection application remain
unverified or unavailable, so DA10.7 stays open.

[Qualification](../assets/audits/2026-11-06-retained-scan-binding/qualification.json).

<a id="j381"></a>

## J381 — detect scan manifests missing from retained results

The computation doctor checks both sides of the retained scan link. A manifest
on the submitted item with no counterpart in the result now produces a warning
and an unverified count. Records without a manifest on either side remain
outside this census.

The Senate print 377 OpenAI mock checkpoint now reports a warning when the
retained scan and its dependent result witness are removed. The intact
checkpoint and the state snapshot at `9a802a55` produce byte-identical reports.
Unit controls exercise the missing result manifest for primary and supplement
records. The audit does not reconstruct legacy manifests.

[Qualification](../assets/audits/2026-11-06-retained-scan-missing-result/qualification.json).

<a id="j382"></a>

## J382 — verify retained source selection in secondary delivery chains

The request prefix validates retained amendment and supplement input receipts
and their available selection, artifact and extraction parents. Exact source
selection also requires a unique scoped observation agreeing with the paid
record, submitted item and chronology. Missing evidence remains unverified.
The existing suffix and ordinary audit retain their scope.

On state `9a802a55`, three amendment chains now have verified prepared-input
receipts and exact retained source selections. The primary census and the two
older secondary chains are unchanged. Removing the observation makes source
selection unverified. A changed observation URL invalidates the prefix. Removing
the extraction parent remains a snapshot error. Original request bytes and
collection applications remain outside the evidence of these three chains.

[Qualification](../assets/audits/2026-11-06-secondary-cycle-source-binding/qualification.json).

<a id="j383"></a>

## J383 — verify the original amendment question in secondary delivery chains

The retained-cycle request prefix compares the validated paid result's
amendment input reference and scan witness with the consumed item's metadata.
A verified text question is reported separately from provider payload bytes.
A scan witness still requires the exact PDF. Missing legacy evidence remains
explicit, and supplements are outside the amendment question profile.

State `9a802a55` proves the retained question for applications 33, 34 and 35.
All prior report fields are identical. Removing the reference from only the
paid result invalidates the request prefix while leaving the recorded delivery
suffix visible. The provider request body and collection application remain
unverified for these chains.

[Qualification](../assets/audits/2026-11-06-secondary-cycle-amendment-question/qualification.json).

<a id="j384"></a>

## J384 — verify retained provider exchange evidence in secondary chains

The clean CI type check for `1a7b9b1e` found incompatible inferred answer
types in the amendment and supplement branches. The follow-up compares
each typed branch directly and retains the same report output.

The secondary request prefix checks the frozen request manifest against the
recorded provider, purpose and request SHA. The retained parsed answer must
agree with the paid computation's custom ID, answer, model and token usage.
Both checks remain separate from verification of original request bytes or
the presence of a collection application.

State `9a802a55` proves both checks for applications 33, 34 and 35. Neither
piece of evidence was retained for 11 or 16. Prior census fields are unchanged.
Conflicting provider, answer and token usage controls invalidate the request
prefix while preserving the recorded delivery suffix. Removing the retained
answer makes it unavailable.

[Qualification](../assets/audits/2026-11-06-secondary-cycle-provider-answer/qualification.json).

<a id="j385"></a>

## J385 — require the frozen scan attachment at provider handoff

Both real batch adapters validate the manifested scan against its frozen
provider envelope before issuing any SDK call. An unverifiable envelope or a
substituted PDF produces a definitive pre-submission rejection. The queue
reader still permits historical opaque envelopes and requests without a scan
manifest remain outside the new handoff check.

Pinned Sejm prints 24 and 25 exercised whole-file and cover-removal cases
through both adapters. The four legitimate cases kept identical request
hashes, SDK input and submitted checkpoint identities. All eight rehashed
opaque or substituted controls reached the mock SDK on the baseline and were
rejected before the first SDK call on the candidate. Unit controls also cover
secondary scans and a mixed batch containing a valid request before the
invalid one.

The initial full gate caught two changes to the existing checksum-error
contract. The final implementation preserves that exception and classifies
only the new attachment validation failures as definitely unsent. The
unverified local commit was removed from the release queue before push.

[Qualification](../assets/audits/2026-11-06-provider-scan-submit-guard/qualification.json).

<a id="j386"></a>

## J386 — enforce the single scan input at provider handoff

After matching the first attachment, the real adapters require all remaining
message blocks to be text. A second document in a user or system message
makes the request definitively unsent. The historical queue reader preserves
its existing envelope rules.

Pinned prints 24 and 25 exercised both providers. The four valid requests
kept their original hashes, mock SDK input and submitted checkpoints. All
eight controls reached the baseline mock SDK and were rejected before the
first call on the candidate. Unit controls also cover secondary scan requests.

[Qualification](../assets/audits/2026-11-06-provider-single-scan-input/qualification.json).

<a id="j387"></a>

## J387 — verify the scan bytes and attachment source at provider handoff

Before submission, both adapters check the PDF bytes against their scan
manifest and require the provider attachment to use the inline PDF source.
Matching substitutions in the question and envelope fail the manifest check.
An alternate file identifier fails attachment validation. The historical queue
reader keeps its previous behavior.

The four pinned primary corpus cases kept their request hashes, mock SDK input
and submitted checkpoints. All eight rehashed substitution and alternate-source
controls were stopped before the SDK. Eight Senate amendment cases also kept
their complete lifecycle reports byte-identical after passing the real adapter
handoff into a mock SDK. Exact correction and missing or changed PDF controls
retained their prior outcomes.

[Qualification](../assets/audits/2026-11-06-provider-scan-manifest-bytes/qualification.json).

<a id="j388"></a>

## J388 — replay the legacy projection of Word revision text

The offline replay pins Word inputs by SHA and replaces text inside selected
OOXML revision wrappers on copies held in memory. It first verifies that a
no-op repackage preserves the extraction. The report records only hashes,
scope and marker counts.

All 18 files with visited revision markers in J320 passed the no-op control.
The existing projection emitted all 27 markers replacing insertion text and
none of the 29 markers replacing deletion text. No natural move wrappers
carried the profiled text in this cohort. A separate synthetic control emitted
the destination text and omitted the deleted source text. In a second
synthetic control the legacy walk also emitted ordinary `w:t` under removal
wrappers. The profiled natural inputs contained no such nodes. The scope is
`word/document.xml`. Complete Word revision semantics remain unresolved.

[Qualification](../assets/audits/2026-11-06-word-revision-view/qualification.json).

<a id="j389"></a>

## J389 — read the optional Word removal text observation

The compatible reader accepts a bounded positive count of ordinary text nodes
emitted inside removal wrappers. A count requires an observed revision wrapper.
Existing records omit the field and retain their exact serialization. The
reader stage leaves the native producer unchanged.

All 3,055 pinned Word files produced byte-identical replay ledgers. One pinned
extraction with existing revision counters also qualified the new reader on
a synthetic count. Its old and new records each survived two exact SQL
restores and SQLite integrity and foreign-key checks. The synthetic field
added 23 bytes to that extraction record.

[Qualification](../assets/audits/2026-11-06-word-removal-text-reader/qualification.json).

<a id="j390"></a>

## J390 — record ordinary text emitted from Word removal wrappers

The existing inline walk counts nonempty `w:t` nodes it emits under visited
`w:del` or `w:moveFrom` wrappers. The counter is optional and is dropped
before existing metadata when either status or result reaches its size cap.
The legacy projection and unresolved revision semantics remain unchanged.

All 3,055 pinned files retained identical text, selection and observations
apart from code fingerprints. The natural cohort had no positive counter.
A separate mutation inserted one ordinary text node into an existing deletion
wrapper in a pinned file. Both versions emitted the same text. The candidate
added one observation and 23 bytes to the extraction record. Both SQLite
checkpoints passed two exact restores and integrity checks.

[Qualification](../assets/audits/2026-11-06-word-removal-text-writer/qualification.json).

<a id="j391"></a>

## J391 — replay Word field text against the existing projection

The offline Word probe can now classify text by the physical XML sequence
of simple and complex fields. An unbalanced sequence is reported separately
without classifying its text. The revision probe retains its previous output.

All 20 selected Word files passed the no-op control. The legacy projection
emitted all 43 post-separator and 26 simple-field body markers. It emitted
none of the 62 instruction markers. The 553 pinned field-positive bodies
contained no ordinary text before a separator. A separate synthetic control
showed that such ordinary text is emitted by the current extractor. The
probe measures lexical projection and does not evaluate Word fields.

[Qualification](../assets/audits/2026-11-06-word-field-view/qualification.json).

<a id="j392"></a>

## J392 — replay automatic binary DOC reference offsets

The offline DOC probe replaces a qualified automatic note reference code unit
with a width-preserving marker. It records an output character offset only
when the marker appears once and removing it reproduces the exact unmodified
legacy text. Shared physical bytes and cleanup changes remain unverified.

The pinned 404 paths and 246 distinct inputs reproduced the legacy text
reference. The opt-in report established 375 reference offsets. Two other
references changed whitespace cleanup under the probe, so no offset is
recorded for them. Existing report observations are unchanged. The probe
does not validate CFSpec or note body offsets.

[Qualification](../assets/audits/2026-11-06-doc-note-reference-projection/qualification.json).

<a id="j393"></a>

## J393 — track DOC reference coordinates through text cleanup

Clean CI found that the offline replay imported `PAGE_BREAK` through the
DOC adapter rather than its defining module. The replay now imports it
directly. Runtime extraction is unchanged.

The opt-in replay reconstructs the cleaned text with source-character origins.
It requires exact equality with the legacy text before using the map. A
width-preserving mutation locates each reference in the raw decoded stream.
The original marker-only report stays available.

All 375 earlier offsets matched the tracked projection. Two references
that disturbed whitespace cleanup in the marker-only report gained exact
insertion boundaries in the tracked report. All 246 original legacy text
hashes and unrelated observations stayed identical. This check does not
validate CFSpec or infer note-body spans.

[Qualification](../assets/audits/2026-11-06-doc-note-cleanup-projection/qualification.json).

<a id="j394"></a>

## J394 — map binary DOC note bodies into cleaned output

The opt-in replay maps both declared note boundaries through the same exact
legacy text comparison used for reference positions. It reports half-open
output ranges and text hashes, with distinct states for empty output and
unverified boundaries. It retains no note text.

The pinned corpus qualified 376 nonempty note body ranges and one empty
range. The nonempty ranges and hashes matched the exploratory probe. All
377 reference observations and unrelated extraction observations remained
identical. Synthetic controls cover nonempty and empty note output. CFSpec
and runtime retention remain separate.

[Qualification](../assets/audits/2026-11-06-doc-note-body-projection/qualification.json).

<a id="j395"></a>

## J395 — read bounded DOC note output positions

The compatible reader accepts optional reference insertion positions and
note body ranges on retained DOC links. It verifies link membership, source
order, extracted-text bounds and the empty text hash. Historical source
manifest hashes and existing serialized rows stay intact. The native
producer still writes the previous format.

The 404 pinned DOC paths reproduced all existing observations. A scratch
checkpoint used verified coordinates and body hashes from one real corpus
input. The legacy record and SQL were byte-identical across readers. The
new record added 338 bytes and survived two exact restores with integrity
and foreign-key checks. The prior reader rejected the new checkpoint
without changing its state.

[Qualification](../assets/audits/2026-11-06-doc-note-projection-reader/qualification.json).

<a id="j396"></a>

## J396 — capture DOC note output coordinates in the native adapter

The optional native DOC capture maps requested source CPs through the actual
piece decoding and matches the entire legacy text before offering output
positions. Automatic references hidden by field codes are excluded. The
capture reuses the OLE streams already read for text and note declarations.
The existing extraction path leaves output coordinates off.

The 246 distinct pinned inputs retained their legacy text. Native capture
matched all 377 independent reference offsets, 376 nonempty body ranges
and one empty range, with one OLE open for each valid container. The 404
path default physical replay matched every existing observation. The
new capture remains in memory pending physical retention qualification.

[Qualification](../assets/audits/2026-11-06-doc-note-native-projection/qualification.json).

<a id="j397"></a>

## J397 — qualify bounded physical retention of DOC note coordinates

The explicit physical-part capture can carry note coordinates with their text
hash. Existing DOC observations take priority within the per-summary and
aggregate byte caps. Default extraction leaves coordinate capture off.

A replay of 246 distinct pinned DOC inputs retained all 377 previous source
links and 301 verified projections across 69 files. Prior observations and
extracted text matched exactly. Record growth totalled 50,190 bytes, with
a maximum of 1,970 bytes per record. On a scratch checkpoint with the three
largest increments, SQL grew by 5,748 bytes and gzip by 1,664 bytes. Each
checkpoint survived two byte-identical restores with integrity checks.

[Qualification](../assets/audits/2026-11-07-doc-note-physical-projections/qualification.json).

<a id="j398"></a>

## J398 — retain verified DOC coordinates in default extraction

The native provenance path adds coordinates after all existing extraction
observations. A mixed DOC and DOCX archive at the metadata limit preserves
its Word revision observation and DOC source link. Existing compatible
readers accept the optional fields.

The pinned default-writer replay verified all 404 paths. Across 246 unique
inputs the selected documents and earlier metadata matched after excluding
the changed implementation fingerprints and new coordinate fields. All 301
retained projections matched independently observed offsets and body hashes.
Serialized records grew by 50,190 bytes in aggregate and at most 1,970 bytes
per record. One local replay measured 1.48 s before and 2.77 s after in
process CPU across the cohort. A native DOC through the real pipeline with
a fake provider preserved the batch request, prompt, memo key and custom ID.

[Qualification](../assets/audits/2026-11-07-doc-note-default-projections/qualification.json).

<a id="j399"></a>

## J399 — distinguish matching secondary collection checkpoints from applications

The offline delivery census checks whether the exact retained provider
answer and paid computation agree with the bound consumed batch item and
its timestamp. A matching checkpoint records consistency between the
saved rows. The separate collection application and atomicity of the
original writes require their own evidence.

On the same pinned snapshot as J384, applications 33–35 had matching
checkpoints. Rows 11 and 16 remained unverified. All five retained their
existing collection-application status and the census preserved all prior
fields and delivery observations.

[Qualification](../assets/audits/2026-11-07-secondary-collection-checkpoint/qualification.json).

<a id="j400"></a>

## J400 — bound the working memory of binary DOC coordinate cleanup

The cleanup pass tracks source offsets in a compact integer array and
compacts page-break adjustments in place. It keeps the previous text and
coordinates, including which automatic references are visible inside fields.

The 246 unique pinned DOC inputs and all 377 native projections matched.
A deterministic 10,000-string probe also matched exact text, offsets and
visible references. On the same one-million-character synthetic input,
traced peak allocations fell from 138,848,193 to 17,540,741 bytes.
This measures the cleanup helper on that input.

[Qualification](../assets/audits/2026-11-07-doc-note-compact-origins/qualification.json).

<a id="j401"></a>

## J401 — audit analysis-derived mnemonics on retained direct cards

The offline audit binds a direct recorded card's frozen mnemonic to the
validated paid result, exact submitted item and retained editorial
projection. It checks scope, request identity, chronology and matching
provider fields. The report distinguishes a compacted answer from a retained
answer and records SQLite delivery evidence.

On the J384 snapshot the audit linked applications 18 and 23 to publications
258 and 264. The results were analysis and reanalysis respectively. Both
provider answers were compacted. This snapshot has no standalone name result.
Source and prepared-input evidence and live Telegram verification remain
outside this check.

[Qualification](../assets/audits/2026-11-07-mnemonic-delivery-cycle/qualification.json).

<a id="j402"></a>

## J402 — read explicit CFSpec evidence for binary DOC note calls

The bounded binary reader resolves automatic reference CPs through the CLX,
CHPX and piece properties. It distinguishes an explicit on or off value from
absence and unknown. Malformed structures and unresolved operands stay
unknown. The persisted reader accepts optional compact evidence tied to an
existing automatic link and preserves the legacy link digests. Default
extraction emits the previous format.

The typed reader reproduced all 377 explicit on observations from 404 pinned
paths and 246 distinct DOC files. The model qualification preserved the
previous metadata and fit 229 compact observations under the existing caps.
On a scratch checkpoint with the three largest increments, SQL grew by
567 bytes and gzip by 147 bytes. Both forms survived two exact restores
and integrity checks. The previous reader rejected the new checkpoint
without changing its legacy state.

[Qualification](../assets/audits/2026-11-07-doc-note-cfspec-reader/qualification.json).

<a id="j403"></a>

## J403 — retain explicit CFSpec in default DOC extraction

Default native DOC extraction reads explicit CFSpec for already validated
automatic note calls from the same OLE streams. The retained prefix uses
space remaining after earlier DOC, Word and ODT observations and existing
DOC output coordinates. Absent or unresolved properties do not emit evidence.

On 246 pinned unique DOC inputs, 229 observations matched the independently
checked source properties. Selected documents and previous metadata were
identical apart from changed implementation fingerprints. Records grew by
5,195 bytes in total and at most 190 bytes individually. SQL grew by 567 bytes
and gzip by 127 bytes in a scratch checkpoint containing the three largest
increments. Both forms survived two exact restores and integrity checks.
The native DOC handoff through a fake provider preserved the request, prompt,
memo key, custom ID and same-byte deduplication. The J402 reader restored
the writer checkpoint byte-for-byte twice.

[Qualification](../assets/audits/2026-11-07-doc-note-cfspec-writer/qualification.json).

<a id="j404"></a>

## J404 — capture exact Word field text ranges inside legacy paragraphs

The optional Word observer binds actual emitted field text to its XML node
and the exact legacy paragraph. It leaves unbalanced or incomplete complex
field walks unknown and distinguishes unobserved simple-field candidates.
Coordinates apply within the paragraph, including for table cells.

Two independent marker perturbations reproduced unchanged legacy text on
all 20 pinned paths. The native observer matched 69 emitted nodes by path,
group and text hash and bound every range to the actual paragraph slice.
Forty-three nodes were in table paragraphs and 26 were in directly
projected paragraphs. The independent probe confirmed 62 instruction
omissions. Default extraction and persisted formats remain unchanged.

[Qualification](../assets/audits/2026-11-07-word-field-ranges/qualification.json).

<a id="j405"></a>

## J405 — measure memo retention exclusions on saved SQLite state

The offline census separates cleanup blockers from exact references in
pending intent and item keys and memo computation IDs. The focused tests
compare the reported eligibility with the actual repository cleanup on a
scratch database and exercise a known pending reference without an owner.

At the pinned cutoff, the saved snapshot had 13 memo records with neither
a recorded bill owner nor a last-used date. No exact references appeared
in the checked scopes. A counterfactual in-memory comparison measured
40,270 bytes of canonical SQL and 10,176 bytes of compressed SQL attributable
to those rows. The production cleanup policy remains unchanged.

[Qualification](../assets/audits/2026-11-07-memo-retention-census/qualification.json).

<a id="j406"></a>

## J406 — read bounded Word paragraph field witnesses

The extraction reader accepts an optional field prefix bound to the exact
Word input and manifest. Each range belongs to one witnessed paragraph and
has ordered paragraph-local coordinates, a relative XML path and a text
hash. Legacy records retain their serialized bytes. The native writer
continues to emit the previous format.

After deduplicating the 20 pinned Word paths, all 64 ranges from 15 distinct
inputs fit the existing extraction and bounded prefix limits. Three selected
records increased the scratch SQL dump by 6,936 bytes and gzip by 197 bytes.
Both forms survived two exact restores. The previous reader restored the
legacy checkpoint and rejected the enriched checkpoint atomically.

[Qualification](../assets/audits/2026-11-07-word-field-ranges-reader/qualification.json).

<a id="j407"></a>

## J407 — retain Word field paragraph witnesses in default extraction

The native Word path records field coordinates during its existing parse
and fits the prefix after earlier observations. Validation failures in the
optional evidence leave the legacy extraction intact. The offline replay
can verify emitted markers against native paragraph slices.

On 20 pinned paths the writer retained all 69 independently verified
ranges. The 15 distinct inputs account for 64 ranges. The selected text
and prior metadata matched with the writer off. In the scratch checkpoint
containing three records, SQL grew by 6,936 bytes and gzip by 192 bytes.
Both forms survived two byte-identical restores. The fake-provider
handoff preserved the request and same-byte deduplication.

[Qualification](../assets/audits/2026-11-07-word-field-ranges-writer/qualification.json).

<a id="j408"></a>

## J408 — verify preattached manifests against the submitting request

When the submitting intent is still available, batch confirmation compares
all fields of a preattached manifest with the exact frozen request. A
contradiction aborts the transaction and preserves the intent for recovery.
The existing path for a batch without a matching intent remains compatible.

The pinned J318 fake-SDK replay matched the baseline for both providers.
A schema-hash mutation under the same request SHA was rejected by both
providers' confirmation paths, with a byte-identical SQLite dump after the
rollback. Matching preattached manifests were accepted.

[Qualification](../assets/audits/2026-11-07-frozen-manifest-submission/qualification.json).

<a id="j409"></a>

## J409 — project verified Word field ranges into the legacy body

The opt-in projection reconstructs paragraph, cell and row joins from the
actual block layout. It verifies parent text and projects positions through
the same page-break cleanup. Unsupported topology, partial capture and a
range that intersects removed characters remain unknown.

The independent marker replay and native projection matched all 69 ranges
across 20 pinned paths, including the 43 inside table paragraphs. The
replay CLI checks whole-body coordinates separately from paragraph slices.
Default text and stored range coordinates remain unchanged.

[Qualification](../assets/audits/2026-11-07-word-field-global-projection/qualification.json).

<a id="j410"></a>

## J410 — read exact inclusion evidence for loose support documents

A compatible candidate trace reader accepts a bounded prefix of included
loose justification and OSR files. Each position belongs to the selected
outer candidate and binds a unique retained selection part by URL, role,
file SHA and text SHA. Existing traces keep their bytes and identities.

The pinned RCL 12366453 stage 3 replay corroborated both included supports
with unchanged reads and assembly. The staged trace and link added
1,791 bytes of SQL and 239 bytes of gzip alongside the old trace. Two
restores were byte-identical. The previous reader rejected the enriched
checkpoint atomically. The default writer still emits the previous format.

[Qualification](../assets/audits/2026-11-07-admitted-support-reader/qualification.json).

<a id="j411"></a>

## J411 — record exact inclusion of loose supporting documents

The default trace records complete loose justification and OSR positions
from the actual composition and final selection. It preserves earlier trace
evidence and existing limits. The source-history inventory counts retained
admissions by role. The writer skips alternative winners where the caller
persists the original requested source instead of the selected source.

On pinned RCL 12366453 stage 3 it retained two positions with unchanged
four reads, selected source and assembly. The checkpoint survived two
byte-identical restores. Four J301 exclusion controls matched the previous
output. The reader release is the rollback floor for the new records.

[Qualification](../assets/audits/2026-11-07-admitted-support-writer/qualification.json).

<a id="j412"></a>

## J412 — retain loose support on a resolved alternative observation

The selected alternative's exact support positions remain attached to the
resolved analysis observation. Prefilter records the requested document and
keeps its previous trace. The chosen source, text and reads remain unchanged.

In pinned RCL 12396606 stage 4, file 712346 won over requested file 719425 and
included the complete OSR 712347. Both paths retained the same selection,
the prefilter observation matched exactly, and two checkpoint restores
preserved the complete saved state.

[Qualification](../assets/audits/2026-11-07-admitted-support-alternative/qualification.json).

<a id="j413"></a>

## J413 — read two-member grouped stage content history

The compatible reader binds one frozen render to the canonical publication
root and two ordered stage updates. It validates both card anchors, event
snapshots, group membership and the shared physical receipt. Unknown
delivery retains its evidence without becoming current. Existing production
writers and correction routing retain their current behavior.

The fake Telegram lifecycle preserved the render after cleanup, including
asymmetric expiry, and restored the checkpoint byte-for-byte twice. A
reordered frozen group failed restore atomically. The staged shared version
added 10,981 SQL bytes and 1,774 gzip bytes to the scratch checkpoint.
The previous reader rejected that checkpoint without changing legacy state.

[Qualification](../assets/audits/2026-11-07-group-content-reader/qualification.json).

<a id="j414"></a>

## J414 — freeze shared content history before the grouped stage send

The first freeze of two qualified stage-only members writes their immutable
shared version in the same transaction as their frozen delivery plans.
A history write failure rolls back that freeze. Unsupported groups follow
the existing delivery path. Recovery and correction routing are unchanged.

The fake Telegram boundary observed one send and a restored pending
checkpoint containing the exact shared text. Sent and unknown cases kept
their physical evidence, and unknown delivery was not resent. After
cleanup the two SQLite checkpoints matched their respective dumps across
two restores. The content history added 10,981 SQL bytes and 1,774 gzip
bytes in the scratch fixture. The compatible reader also restored the new
checkpoint exactly.

[Qualification](../assets/audits/2026-11-07-group-content-writer/qualification.json).

<a id="j415"></a>

## J415 — inspect shared stage history through either verified member

Read-only content inspection resolves the second member to its canonical
group version only when the stored group evidence explicitly names it.
Both member views show the same history and preserve the shared delivery
confirmation policy. The physical binding remains on the root.

The paired J414 checkpoints showed matching root and member views with
unchanged SQL. Legacy groups without evidence remain unavailable, and
unknown delivery has no current version. A different publication ID
cannot claim the group's history.

[Qualification](../assets/audits/2026-11-07-group-content-inspection/qualification.json).

<a id="j416"></a>

## J416 — preview a single owned fragment of a shared stage update

An opt-in formatter witness records exact UTF-8 offsets and ownership
when rendering a two-member stage update. The pure preview verifies the
original render hash and unchanged topology, then replaces only the
member's entry. Shared blocks, the sibling and separators remain exact.
Regrouping, an unknown owner and a stale original hash are rejected.

The fake Telegram lifecycle confirmed the same rendered output and
preservation of shared bytes on a date mutation. A 112-case render matrix
and four configurations of the saved group matched the previous
formatter. The saved group's historical HTML could not be reproduced
with those configurations, so it supplies parity evidence only. No
historical witness was reconstructed.

[Qualification](../assets/audits/2026-11-07-group-update-preview/qualification.json).

<a id="j417"></a>

## J417 — read bounded exact ownership of shared stage fragments

The compatible reader binds optional fragment ranges to the immutable
render hash, UTF-8 boundaries, canonical topology, ordered member/card/event
identities and the frozen rendering day. Older versions preserve their
serialized form and identity. No production writer emits the field.

The staged fake Telegram checkpoint retained five fragments without
duplicating rendered text. The 332-byte witness added 350 SQL bytes and
153 gzip bytes in the paired checkpoint. Both forms survived two exact
restores after delivery cleanup. The prior reader rejected the extended
checkpoint atomically.

[Qualification](../assets/audits/2026-11-07-group-fragment-reader/qualification.json).

<a id="j418"></a>

## J418 — verify literal evidence from the exact self-amendment segment

The offline citation replay accepts a bounded second source part only for
a verified Sejm self-amendment receipt. It binds the pinned file and
selected text to the actual saved relationship and maps only that part's
source segments. Main-part behavior remains unchanged.

On pinned 2320/2320-A the exact quote at source offsets 2100–2204 maps
to request offsets 41527–41631. Wrong source, missing relation and
generated-boundary controls do not produce an exact result. This is a
literal and coordinate check. The controlled claim has no semantic review.

[Case](../assets/audits/2026-11-07-sejm-amendment-citation/case.json)
[Qualification](../assets/audits/2026-11-07-sejm-amendment-citation/qualification.json).

<a id="j419"></a>

## J419 — retain shared fragment ownership from the original render

The existing publisher render call supplies an optional exact ownership
witness in the first atomic freeze. Changed HTML, an opaque or repeated
render and insufficient space preserve base group history. No correction
or delivery retry behavior changes.

The paired fake Telegram checkpoints retained the same physical rows and
text. The witness was present in the pending checkpoint before the send.
Its addition measured 350 SQL bytes and 153 gzip bytes. Both forms survived
two exact restores, and the compatible reader restored the writer output.
Unknown delivery stayed unconfirmed without a resend.

[Qualification](../assets/audits/2026-11-07-group-fragment-writer/qualification.json).

<a id="j420"></a>

### J420. Точный вход обзора консультаций в замороженном запросе

- `tools/replay_review_input.py` проверяет SHA входного файла и манифест
  запроса, затем сопоставляет декодированный пользовательский блок с
  сохранённым вопросом и проверяет порядок и хеши текстов документов.
- На офлайн-фикстуре J176 сборщики OpenAI и Anthropic дали одинаковый хеш
  декодированного контекста и диапазоны `[0,62)`, `[0,187)`. Отрицательные
  контроли покрывают конфликт текста, порядка, роли и идентичности,
  дополнительный пользовательский блок и повреждённые хеши или JSON.
  [Квалификация](../assets/audits/2026-11-07-review-prepared-input/qualification.json).
- В закреплённом снимке состояния 14 отправленных элементов обзора без
  сохранённого контекста или манифеста и 0 намерений в очереди. Для этого
  снимка доказательство на естественном замороженном запросе недоступно.
  Координаты относятся к строкам декодированного JSON. Формат состояния
  и штатный путь не менялись, обращений к провайдерам не было.

<a id="j421"></a>

### J421. Офлайн-предпросмотр правки группового обновления

- Read-only сервис требует подтверждённый корень, непротиворечивую текущую
  версию, ожидаемые content ID и SHA и сохранённое описание фрагментов.
  Он принимает явно подготовленный кандидат и возвращает HTML и хеши,
  только если изменился фрагмент выбранного участника.
- Юнит-тесты закрывают неподтверждённую историю, неверные ID и хеш,
  отсутствующее или повреждённое описание и изменения за границами
  разрешённого фрагмента. Сценарий проверяет чтение после очистки
  и восстановления SQLite. Дамп до и после предпросмотра совпадает.
- Приёмка ограничена офлайн-предпросмотром. Операторская команда,
  постановка правки в очередь и доставка остаются отдельным этапом.

<a id="j422"></a>

### J422. Совместимое чтение подтверждения входа обзора консультаций

- Необязательное подтверждение связывает отправленный запрос с его владельцем,
  SHA запроса, манифестом и упорядоченными ролями, хешами и длинами полей.
  Тексты в подтверждение не копируются. При наличии контекста проверяется
  соответствие контексту. Отсутствие контекста допустимо после потребления.
- Проверены прежняя форма метаданных, противоречивые записи и сохранение
  свидетельства после штатной очистки и двух точных восстановлений.
  Отчёты офлайн-проверки J420 совпали побайтно у обоих провайдеров.
- Офлайн request doctor отдельно считает подтверждённые и неизвестные записи.
  В закреплённом снимке J420 подтверждённых 0, неизвестных 14.
- Штатная запись сохраняет прежний формат. Выпуск reader и его проверка
  должны предшествовать включению writer. Исторические значения не добавляются.

<a id="j423"></a>

### J423. Запись подтверждения точных полей обзора консультаций

- Переход из отправляемого намерения в batch item сохраняет компактное
  подтверждение только при совпадении фактического запроса и обоих
  контекстов с владельцем. Расхождение оставляет существующий манифест.
  Офлайн-инструмент J420 использует тот же валидатор.
- Офлайн-фикстуры обоих провайдеров прошли постановку в очередь,
  подтверждение отправки, очистку и два точных восстановления.
  Дамп очереди побайтно совпал с базовым. После очистки изменение
  составило 783 байта SQL и 406–407 байт gzip на проверенную запись.
  [Квалификация](../assets/audits/2026-11-07-review-prepared-input-writer/qualification.json).
- Reader J422 побайтно восстановил все четыре дампа обоих провайдеров
  до и после очистки. [Проверка отката](../assets/audits/2026-11-07-review-prepared-input-writer/rollback.json).
- Минимальная версия для отката после первой записи содержит reader J422.
  Публикация J423 ждёт подтверждённой выкладки точного SHA J422.
  Провайдеры не вызывались. Источник физических байтов не утверждается.

<a id="j424"></a>

### J424. Чтение истории групповой правки до включения записи

- Сохранённый слот сверяет цель, владельца, предыдущую версию, неизменный
  состав группы и замену фрагмента одного участника. Только совпадающее
  подтверждение доставки делает новую версию текущей.
- При откате очередь, отправитель и действия оператора оставляют такие
  слоты без изменений. Проверены queued, failed, pending, unknown,
  неверная цель и изменения чужого текста.
- На закреплённом снимке состояния отчёт doctor, четыре версии и дамп
  после восстановления совпали побайтно с J421.
  [Проверка совместимости](../assets/audits/2026-11-07-group-revision-reader/legacy-qualification.json).
- На двух контрольных дампах J419 отчёт и SQL совпали с J421 как
  при наличии описания фрагментов, так и при его отсутствии.
  [Контрольные дампы](../assets/audits/2026-11-07-group-revision-reader/grouped-qualification.json).
- Четыре контрольных состояния слота восстановились побайтно.
  Слот добавил 620 байт gzip в queued и 618 байт в unknown.
  [Размеры и хеши дампов](../assets/audits/2026-11-07-group-revision-reader/seeded-slot-qualification.json).
- Совместная предварительная сборка J420–J424 прошла 49 целевых тестов,
  strict mypy и Ruff. Полный gate выполняется на итоговой базе этапа.
- J421 атомарно отклоняет новый checkpoint с unknown. J424 задаёт
  минимальную версию для отката после будущего включения записи.
  Штатное создание правки и её отправка остаются выключенными.

<a id="j425"></a>

### J425. Атомарное резервирование групповой правки

- Репозиторий берёт право записи SQLite до проверки подтверждённой
  исходной версии и её хеша. Он сверяет одобренный хеш, проверяет
  конфликты на той же физической цели и одним сохранением добавляет
  слот, версию и связь между ними.
- Два SQLite-соединения конкурировали за правки разных участников
  одной группы. Слот получило одно соединение. Проверены устаревшая
  версия, несовпадающее одобрение и откат при частичной записи.
- Reader J424 дважды побайтно восстановил исходный дамп и дампы
  queued и unknown. Повтор резервирования не изменил SQL. Новый слот
  добавил 8184 байта SQL и 620 байт gzip в queued.
  [Квалификация](../assets/audits/2026-11-07-group-revision-reservation/qualification.json).
- Слот остаётся вне штатной отправки. Выпуск после подтверждённой
  выкладки reader J424.

<a id="j426"></a>

### J426. Проверяемая квитанция явной правки Telegram

- Адаптер делает одну попытку по закреплённым числовым chat ID и message ID.
  Успех требует совпадения обоих ID в ответе. Неопределённые ответы
  и потеря транспорта не повторяются автоматически.
- Адаптер не подключён к штатной отправке. Существующий editor
  продолжает работать по прежнему контракту.
- В закреплённом снимке 70 отправленных обновлений используют один
  псевдоним канала. Для будущей отправки требуется проспективно
  сохранять числовую цель из квитанции исходной публикации.
  [Проверка снимка](../assets/audits/2026-11-07-group-strict-telegram-edit/target-qualification.json).

<a id="j427"></a>

### J427. Совместимое чтение физической цели групповой правки

- Миграция 69 добавляет nullable числовой ID чата в подтверждённые
  исходные обновления. Маршрутизация остаётся по `channel_id`.
  Для старых записей цель остаётся неизвестной.
- Читатель проверяет согласованность общей цели участников отдельно
  от идентичности слота правки J424. Схема и чтение выпускаются
  до подключения записи ID из ответа Telegram.
- Проверки восстановления и миграции проведены на закреплённом
  снимке состояния. [Квалификация](../assets/audits/2026-11-07-group-telegram-chat-reader/qualification.json).

<a id="j428"></a>

### J428. Числовой ID чата из ответа исходной отправки

- Telegram-адаптер возвращает необязательный числовой ID чата из
  того же ответа, который подтвердил ID сообщения. Дополнительных
  сетевых запросов нет. При явно заданной числовой цели ответ обязан
  с ней совпадать для выдачи числового ID.
- Отсутствующий, повреждённый или несовпадающий ID чата остаётся
  неизвестным и не меняет подтверждение сообщения. Прежние вызовы
  `send_message` продолжают возвращать только ID сообщения.
- Проверены alias, числовая цель и отрицательные контроли на fake
  Telegram. Запись ID в SQLite остаётся отдельным этапом.

<a id="j429"></a>

### J429. Сохранение подтверждённого обновления после очистки

- Повторная подготовка подтверждённого обновления после удаления
  временного payload сохраняет исходную запись и её квитанцию.
  Новый план доставки для неё не создаётся.
- Шесть целевых проверок покрывают одиночное и групповое обновление,
  отсутствие и наличие числового ID, а также повторную подготовку
  при сохранённом или очищенном payload.
- Этап предшествует записи числовой цели из ответа Telegram.

### Подтверждённые выпуски после исправления DOC replay

Для каждого SHA проверены CI, переход реле, перезапуск через 45 секунд и включённый таймер.

| SHA | CI | Выкладка реле |
| --- | --- | --- |
| `bdd2adffeb6e3e2a2158522403314154f7a6eac8` | [CI 42316528259](https://github.com/sobolevbel/lexinform/actions/runs/42316528259) | [Deploy 42317389413](https://github.com/sobolevbel/lexinform/actions/runs/42317389413) |
| `71c416671d03cb248613b6a74e8fa85eeb41d517` | [CI 42317523093](https://github.com/sobolevbel/lexinform/actions/runs/42317523093) | [Deploy 42318366065](https://github.com/sobolevbel/lexinform/actions/runs/42318366065) |
| `2b7ab9cee2f500a9f805b15262e8ea9f237ce7b2` | [CI 42318499505](https://github.com/sobolevbel/lexinform/actions/runs/42318499505) | [Deploy 42319363396](https://github.com/sobolevbel/lexinform/actions/runs/42319363396) |
| `01cbee2db54132e36567ef91318fc1d52c715175` | [CI 42319485139](https://github.com/sobolevbel/lexinform/actions/runs/42319485139) | [Deploy 42320292760](https://github.com/sobolevbel/lexinform/actions/runs/42320292760) |
| `a84b1697cf8d2cadbedb183d46805515783628f4` | [CI 42320403390](https://github.com/sobolevbel/lexinform/actions/runs/42320403390) | [Deploy 42320916877](https://github.com/sobolevbel/lexinform/actions/runs/42320916877) |
| `76707e018590382b60f04015c1d35799df7738f4` | [CI 42321026661](https://github.com/sobolevbel/lexinform/actions/runs/42321026661) | [Deploy 42321547122](https://github.com/sobolevbel/lexinform/actions/runs/42321547122) |
| `2710d9c3dc4ba2eed8bc1b45527e9c039fcdb999` | [CI 42321691692](https://github.com/sobolevbel/lexinform/actions/runs/42321691692) | [Deploy 42322497476](https://github.com/sobolevbel/lexinform/actions/runs/42322497476) |
| `25351cab6e03e282376d7459b92e9c593beb130d` | [CI 42322628870](https://github.com/sobolevbel/lexinform/actions/runs/42322628870) | [Deploy 42323345863](https://github.com/sobolevbel/lexinform/actions/runs/42323345863) |
| `bfe4a71df6b0ee54aaf7f184be1e77b77d48f387` | [CI 42323485815](https://github.com/sobolevbel/lexinform/actions/runs/42323485815) | [Deploy 42323983761](https://github.com/sobolevbel/lexinform/actions/runs/42323983761) |
| `dea043eb3e6cd931fb57f6f2148a0554d8187fc7` | [CI 42324075745](https://github.com/sobolevbel/lexinform/actions/runs/42324075745) | [Deploy 42324753283](https://github.com/sobolevbel/lexinform/actions/runs/42324753283) |

<a id="j430"></a>

### J430. Запись числовой цели исходного группового сообщения

- Две подтверждённые исходные публикации получают общий числовой ID
  чата в одной транзакции с `SENT`. Без пригодного ID обе квитанции
  сохраняются без числовой цели.
- Потеря подтверждения записи после принятой отправки оставляет
  восстановленную группу в `unknown` без повтора. Проверен один
  `sendMessage` без поиска alias, очистка и два точных восстановления.
  После очистки изменение составляет 20 байт SQL и 18 байт gzip.
  [Квалификация](../assets/audits/2026-11-07-group-telegram-chat-writer/qualification.json).
- J429 дважды побайтно восстановил оба дампа J430 до и после очистки
  и подтвердил общую числовую цель.
  [Проверка отката](../assets/audits/2026-11-07-group-telegram-chat-writer/rollback.json).
- Выпуск зависит от проверенной выкладки J429, минимальной версии
  отката после записи новых квитанций. Групповая правка не отправляется.

<a id="j431"></a>

### J431. Отправка одного явно одобренного группового слота

- Отдельный путь сверяет текущую базу, хеш одобрения и закреплённую
  числовую цель. Слот переходит в pending до checkpoint и обращения
  к редактору. Редактор получает сохранённый HTML.
- Квитанция Telegram должна совпасть с целью. Неопределённый результат
  остаётся unknown и не повторяется. Однозначный отказ остаётся failed. Операторская команда и штатное
  подключение этого пути остаются отдельными этапами.
- J430 дважды побайтно восстановил четыре контрольных checkpoint,
  включая pending, sent и unknown. Штатная очередь не выдаёт слот.
  [Проверка отката](../assets/audits/2026-11-07-group-edit-dispatch/rollback.json).
- Переход из pending меняет только поля квитанции слота. Сохранённый
  HTML и остальные строки не меняются. На контрольном дампе изменение
  gzip составило −5 байт для sent и +36 байт для unknown.
  [Замер](../assets/audits/2026-11-07-group-edit-dispatch/dispatch-size.json).

<a id="j432"></a>

### J432. Проверка хранения и импорта старого состояния

- В закреплённом состоянии `607e9f30` шесть отправленных обновлений
  содержали 7 960 564 байта `review_work`. Три связаны с отправленным
  дайджестом. У всех шести отсутствуют редакционная привязка, физические
  потомки и открытая документная работа по тому же законопроекту.
  [Инвентаризация](../assets/audits/2026-11-07-legacy-update-review-work/owner-census.json).
- Удаление только поля на отдельной копии уменьшило gzip на 1 982 770 байт.
  Изменённый дамп восстановился побайтно. Это замер размера, а не проверка
  эквивалентности всех исторических читателей.
  [Замер](../assets/audits/2026-11-07-legacy-update-review-work/qualification.json).
- Штатная очистка на дату снимка сохранила шесть копий. При сдвиге часов
  на 90 дней удалила все шесть, повторный проход оказался пустым.
  Новые планы уже исключают поле. Оставляем действующий срок хранения.
  [Прогон очистки](../assets/audits/2026-11-07-legacy-update-review-work/retention-replay.json).
- На той же копии проверен существующий импорт DA6.5 на схемах 68 и 69.
  Подготовка не меняла SQL, второй apply не менял состояние, два
  восстановления точны. Сжатый дамп вырос на 133 908 и 133 988 байт
  соответственно. Штатный state не менялся.
  [Схема 68](../assets/audits/2026-11-07-legacy-update-review-work/legacy-import-schema68.json)
  · [Схема 69](../assets/audits/2026-11-07-legacy-update-review-work/legacy-import-schema69.json).

<a id="j433"></a>

### J433. Чтение аудита решения по групповой правке

- Отдельный формат связывает решение оператора с сохранённой командой,
  слотом, предыдущей редакцией, хешем HTML и точной числовой целью.
  Подтверждение требует записанного времени наблюдения.
- Старый читатель скрывает историю при встрече с этим форматом.
  Новый проверяет связь и при чтении, и при восстановлении.
  Запись формата и операторская команда пока не подключены.
- На снимке `607e9f30` SQL после восстановления и отчёт doctor совпали
  с J431. В снимке нет записей аудита доставки, поэтому новая форма
  проверена отдельно на контрольных данных.
  [Сравнение снимка](../assets/audits/2026-11-07-group-edit-resolution-reader/legacy-qualification.json).
- Контрольное старое подтверждение прямого обновления сохраняет тот же
  SQL и тот же JSON аудита в J431 и J433.
  [Контроль legacy](../assets/audits/2026-11-07-group-edit-resolution-reader/legacy-direct.json).
- На совместной сборке с диспетчером и прежним восстановлением
  доставки прошли 43 целевых теста и Ruff.

<a id="j434"></a>

### J434. Атомарная запись ручного решения по групповой правке

- Только отдельная операция для слота unknown сверяет сохранённую
  команду, базу, HTML и числовую цель. Она записывает решение и статус
  в одной транзакции. Повтор команды не меняет запись.
- Подтверждение требует явного наблюдения оператора. Закрытие без
  подтверждения оставляет текущую редакцию неизвестной. Повтор отправки
  и операторский вход остаются отдельными этапами.
- J433 дважды побайтно восстановил оба исхода. На контрольных дампах
  подтверждение добавило 859 байт SQL и 213 байт gzip, закрытие добавило
  825 байт SQL и 219 байт gzip.
  [Квалификация](../assets/audits/2026-11-07-group-edit-resolution-writer/qualification.json).
- Выпуск ждёт подтверждённого J433. При будущем подключении команда
  должна вызывать операцию за пределами прежнего блока atomic.

<a id="j435"></a>

### J435. Проверяемая правка даты направления в комитет

- Кандидат строится для одной прошедшей даты направления в комитет.
  Читатель сверяет текущую версию и хеш, владельца и полный блок стадий
  из неизменяемого снимка. Меняются только дата и координаты фрагмента.
- Полный рендер из компактного снимка не совпал с исходным HTML.
  Блоки стадий совпали у обоих участников. На восстановленном дампе
  J430 четыре из восьми пробных сдвигов допустимы, остальные отклонены.
  Дамп после проверки неизменен.
  [Контроль снимка](../assets/audits/2026-11-07-group-date-candidate/candidate-restore.json).
- Соседние фрагменты, включая ранее подтверждённую правку, сохраняются.
  Штатная команда и запись пока не подключены.

<a id="j436"></a>

### J436. Предпросмотр и одобрение точечной групповой правки

- Предпросмотр возвращает HTML, идентификатор исходной версии,
  хеш кандидата и числовую цель. Одобрение повторно строит кандидат
  и сверяет все значения до атомарного резервирования.
- Диспетчер выполняет checkpoint перед эффектом. При отказе захвата
  уже созданный queued остаётся неактивным. Повторное одобрение
  не продолжает queued, pending или unknown.
- Хеш связывает одобрение с содержимым. Факт просмотра оператором
  из него не следует. Новый формат хранения и операторский вход
  на этом этапе не добавляются.
- Отчёт J421 о сохранении соседних байтов включён в журнал.
  [Контроль предпросмотра](../assets/audits/2026-11-07-group-offline-preview/qualification.json).

<a id="j437"></a>

### J437. Операторский вход для предпросмотра и одобрения

- Команда принимает только типизированные параметры одной правки даты.
  Предпросмотр показывает изменяемую строку и точные данные для одобрения.
  Одобрение требует включённого флага, публикации и рабочего checkpoint.
  Dry run не отправляет правку.
- Независимый офлайн-контроль на восстановленном J430 проверил один вызов
  редактора с одобренным HTML и побайтное восстановление обеих точек.
  [Квалификация](../assets/audits/2026-11-07-group-edit-command/approval-restore.json).
- Тест с временным Git remote подтвердил сохранение команды и pending
  перед вызовом тестового редактора. Прошли 25 CLI-тестов с локальным HTTP.
- Флаг по умолчанию выключен. Вход для ручного подтверждения и закрытия
  неопределённой правки остаётся отдельным этапом.

<a id="j438"></a>

### J438. Операторское решение по неопределённой групповой правке

- Отдельные confirm и dismiss используют сохранённую команду, точную
  редакцию, исходную версию, хеш и числовую цель. Confirm требует
  явно указанного времени наблюдения, dismiss требует причину.
- Обе команды используют выделенную атомарную запись J434 вне
  транзакции прежнего /delivery. Вызова редактора нет.
- Вход выключен по умолчанию. Режим dry run и запуск без
  публикации не меняют сохранённую правку.
- Проверка на временном Git remote воспроизводит сбой сохранения
  решения: из последней версии восстанавливаются UNKNOWN и входная
  команда без аудита решения. Повтор той же записи не меняет
  правку. Проверены полный pytest, строгий mypy, Ruff, локальные
  HTTP-тесты и строгая сборка документации. Исходные документы
  и правила их обработки не менялись.

<a id="j439"></a>

### J439. Чтение исходного вопроса по полному скану дополнительного документа

- Reader принимает ограниченную запись из submitted item. Она связывает
  владельца, дополнительный документ, исходный вопрос и манифест.
  Восстановление требует PDF с точным SHA и полного неизменённого скана.
  После очистки reader также сверяет consumed item с платным результатом.
- Офлайн-проверка на 19-страничном 1000-001 воспроизвела один и тот же SHA
  вопроса для OpenAI и Anthropic после сбора тестового результата,
  штатной очистки и двух точных восстановлений. Подмена владельца,
  источника или PDF отклонялась. Старые записи без свидетельства
  остались неопределёнными.
- Запись в проверке добавила 1147 байт SQL и 243–244 байта gzip.
  Прочий SQL совпал. В этом этапе включено только чтение.
  [Квалификация](../assets/audits/2026-11-07-supplement-scan-input-reader/qualification.json).

<a id="j440"></a>

### J440. Диапазон цитаты в исходном тексте страницы PDF

- Дополнительный офлайн-вход проверяет диапазон и SHA подстроки
  исходного текста экстрактора. При неоднозначной границе
  схлопнутого пробела или переноса проверка отказывает.
- На закреплённом PDF J348 диапазон [204, 331) нормализованной
  страницы соответствует исходному диапазону [206, 334).
  Прежний отчёт J348 воспроизведён без изменений.
- Координаты относятся к строке экстрактора. Смысловая оценка и
  геометрия страницы остаются за пределами этой проверки.
  [Квалификация](../assets/audits/2026-11-07-pdf-raw-span/qualification.json).

<a id="j441"></a>

### J441. Запись исходного вопроса по полному скану дополнительного документа

- После подтверждения batch сохраняется ограниченное свидетельство,
  если замороженные запрос, вложение, текст запроса и метаданные
  совпали. При расхождении остаётся обычный манифест.
- На закреплённом 19-страничном 1000-001 проверены оба провайдера,
  отправка через тестовый транспорт, сбор результата, очистка
  и два побайтных восстановления. Точный вопрос воспроизводится
  при подстановке PDF с исходным SHA.
- Очередь и остальной SQL побайтно совпали. Свидетельство добавило
  1147 байт SQL и 245 байт gzip на запись после очистки. Отдельные
  копии PDF и текста запроса в свидетельство не входят.
- Выпуск следует за подтверждённой выкладкой reader J439.
  [Квалификация](../assets/audits/2026-11-07-supplement-scan-input-writer/qualification.json).

<a id="j442"></a>

### J442. Повтор чтения GitHub при проверке выпуска

- Верификация 4f2e2e02 прервалась с github_read_failed. Повтор
  для того же SHA подтвердил CI, выкладку и состояние main.
- При явном таймауте проверки временный отказ чтения расходует
  общий бюджет ожидания. Следующая попытка проверяет весь SHA
  заново. Невалидная идентичность остаётся терминальной.
- Unit-тесты проверяют восстановление чтения, исчерпание
  дедлайна и немедленный отказ при невалидной идентичности.

<a id="j443"></a>

### J443. Чтение основания включения отдельного нормативного приложения

- Совместимый reader проверяет выбранного кандидата, позицию
  в официальном списке приложений и точную итоговую часть по SHA
  файла и текста.
- На RCL 12403302/4 проверены два нативных DOCX с наблюдённым
  видом unknown. Итоговый статус остался unverified. Контроль
  12394200/2 остался waiting с прежним read_error.
- В проверке новая трасса добавила 2189 байт SQL и 166 байт gzip.
  Два восстановления совпали побайтно. Прежний reader отклонил
  новый формат и сохранил исходное состояние. Writer не включён.
  [Квалификация](../assets/audits/2026-11-07-normative-annex-reader/qualification.json).

<a id="j444"></a>

### J444. Проверка мягкого разрыва страницы в ODT

- Опциональный офлайн-отчёт связывает XML-путь с диапазоном
  символа в извлечённом абзаце и показывает пропущенные узлы.
- В закреплённом 8561788 узел (16, 2) соответствует [172, 173).
  Одиннадцать узлов уровня документа пропущены. В контрольном
  Sejm 1614 единственный узел этого уровня не создаёт символа.
- Перенос узла 8561788 на уровень документа убрал ровно один
  form feed. На шести закреплённых корнях прежние наблюдения
  совпали. Проверка относится к извлечению текста.
  [Квалификация](../assets/audits/2026-11-07-odt-soft-break/qualification.json).

<a id="j445"></a>

### J445. Запись основания включения отдельного нормативного приложения

- Запись сохраняет наблюдённый вид и позицию приложения, если
  после дедупликации оно однозначно соответствует итоговой части.
  Для альтернативы запись требует установленного владельца.
- На закреплённом RCL 12403302/4 совпали итоговая сборка, хеши
  частей и порядок чтения. Два приложения сохранили вид unknown.
  Изменились только трасса и ссылка на неё. Прирост составил
  499 байт SQL и 41 байт gzip. Проверены два точных восстановления.
- Контроль 12394200/2 сохранил прежний отказ и побайтно тот же
  SQL. Выпуск следует за подтверждённой выкладкой reader J443.
  [Квалификация](../assets/audits/2026-11-07-normative-annex-writer/qualification.json).

<a id="j446"></a>

### J446. Проверка исходного абзаца Word для цитаты в составном запросе

- Опциональная офлайн-проверка сначала подтверждает выбранную
  часть и точный запрос, затем сверяет диапазон абзаца с XML.
- На закреплённом DOCM из RCL 778150 диапазон [157, 267)
  совпал с исходными текстовыми узлами абзаца (0, 4).
  Прежняя цитата [157, 272) включает добавленный текст сноски.
  Она проходит прежнюю проверку, но не проверку исходных узлов.
- Прежний отчёт J349 совпал. Точная привязка к XML не оценивает
  смысл цитаты.
  [Квалификация](../assets/audits/2026-11-07-multipart-word-quote/qualification.json).

<a id="j447"></a>

### J447. Чтение карты запроса для ZIP, выбранного среди версий

- Reader сверяет выбранный архив, основную часть и проигравшую
  версию с сохранённым сравнением того же наблюдения. Этап
  охватывает победу запрошенного ZIP над одной альтернативой.
- На закреплённом RCL 12388203/4 локальные сборщики OpenAI и
  Anthropic подтвердили точный блок и шесть сегментов. Для каждого
  провайдера очередь и submitted item восстановились дважды.
  Подмена корня и отсутствие сохранённой связи отклонены.
- В проверке очередь выросла на 2058 байт SQL и 582–584 байта
  gzip. Запись новых свидетельств остаётся отдельным этапом.
  [Квалификация](../assets/audits/2026-11-07-rcl-version-archive-input-reader/qualification.json).

<a id="j448"></a>

### J448. Запись карты запроса для ZIP, выбранного среди версий

- Writer использует сохранённое решение и замороженный запрос.
  При отсутствии решения, наблюдения проигравшего файла или
  совпадения с выбранным корнем карта не добавляется.
- На закреплённом RCL 12388203/4 для обоих провайдеров совпали
  точный блок запроса, шесть сегментов и резерв токенов.
  После очистки текст и ответ удалены, карта осталась.
- Дампы после очистки дважды восстановились на reader J447.
  В очереди прирост составил 566 байт gzip. После очистки
  осталось 283–295 байт gzip на запись. Выпуск следует после J447.
  [Квалификация](../assets/audits/2026-11-07-rcl-version-archive-input-writer/qualification.json).

<a id="j449"></a>

### J449. Чтение компактной трассы решений по архиву

- Reader проверяет коды, словарь SHA-256, порядок строк,
  полный хеш и привязку к выбранному кандидату. Лимит секции
  составляет 1152 байта и 32 строки.
- На закреплённом RCL 12388203/4 сохранены все 11 наблюдённых
  строк вместо двух. Секция заняла 1131 байт, дамп вырос
  на 165 байт SQL и 70 байт gzip. Трасса с пересчитанным ID
  дважды восстановилась побайтно.
- Прежний reader отклонил новый формат и сохранил состояние.
  Штатная запись использует прежний формат до выпуска reader.
  [Квалификация](../assets/audits/2026-11-07-packed-archive-reader/qualification.json).

<a id="j450"></a>

### J450. Подтверждение выпуска после повторной выкладки реле

- Первая попытка f130c5c5 зафиксировала переход с проверенного
  SHA и завершилась ошибкой до подтверждения здоровья.
  Повтор того же workflow подтвердил 45 секунд работы и таймер.
- Проверка связывает попытки одного run только при известном
  предыдущем SHA, точном первом переходе и успешном повторе
  на том же checkout. Неоднозначные и пропущенные шаги отклоняются.
  [Подтверждение](../assets/audits/2026-11-07-release-retry-proof/release.json).

<a id="j451"></a>

### J451. Запись компактной трассы решений по архиву

- Writer сохраняет прежний полный формат, если он помещается.
  Компактный формат используется, если позволяет сохранить все
  наблюдённые строки. При превышении лимитов остаётся прежняя
  запись с явным числом пропусков.
- На RCL 12388203/4 сохранены 11 строк вместо двух. Выбор,
  сборка, загрузки и прочий SQL совпали. Сжатый дамп вырос на 72 байта.
- На закреплённой выборке совпали решения и чтения в 42 парных
  наблюдениях. Сохранены 36 дополнительных строк в 16 наблюдениях.
  Дамп уменьшился на 2407 байт SQL и вырос на 179 байт gzip.
  Проверены два точных восстановления и откат на reader J449.
  [Квалификация](../assets/audits/2026-11-07-packed-archive-writer/qualification.json).
- Первый полный gate выявил шесть устаревших fixture-предположений.
  Тесты теперь изменяют действительное packed-представление, проверяют
  отказ для обоих форматов и сохраняют отдельный случай 68 наблюдений
  с явным prefix. Production validator не менялся; 150 целевых тестов прошли.
  [Разбор](../assets/audits/2026-11-07-packed-archive-writer/gate-test-fixtures.json).

<a id="j452"></a>

### J452. Сравнение сохранённых цепочек на двух снимках состояния

- Read-only census на f8010d33 сравнил изолированные копии
  state 9a802a55 и 4764779e. Результаты совпали, кроме SHA базы.
- Подтверждён один полный сохранённый первичный цикл и пять
  частичных вторичных цепочек. Новый полный цикл не наблюдался.
  [Квалификация](../assets/audits/2026-11-07-da10-fresh-census/qualification.json).

<a id="j453"></a>

### J453. Чтение неизменяемой истории дайджеста

- Отдельный профиль сохраняет порядок частей и событий, точный
  текст и реальный физический ref. Он не требует Bill snapshot.
  Остальные профили сохраняют обязательную связь с законопроектом.
- Append-only миграция допускает nullable Bill FK. На закреплённом
  снимке сохранены прежние строки, content IDs и JSON; проверены
  два точных восстановления. Старые дайджесты остаются неизвестными.
  [Миграция](../assets/audits/2026-11-07-digest-history-reader/legacy-qualification.json).
- Поддержка разных времён создания частей и повторных черновиков
  проверяется отдельно. Штатная запись истории следует после reader.
  [Квалификация](../assets/audits/2026-11-07-digest-history-reader/qualification.json).

<a id="j454"></a>

### J454. Запись истории дайджеста при первой отправке

- Точный дайджест, части и события сохраняются одной транзакцией
  при первом замораживании до отправки. Повтор использует
  сохранённые данные. Исторические пропуски не дополняются.
- Проверены multipart с движущимися часами, повторные черновики,
  unknown, отказ транзакции, checkpoint и штатная очистка.
  Пять контрольных дампов восстановились дважды на reader J453.
- При превышении лимита или невалидном входе отправка сохраняет
  прежнее поведение. Ограниченная диагностика содержит ID и код
  причины. После очистки база не хранит причину пропуска.
  [Квалификация](../assets/audits/2026-11-07-digest-history-writer/qualification.json).
- Первый полный gate выявил три случая с искусственным переводом SENT
  в неопределённое состояние без удаления подтверждённой квитанции.
  Fixtures теперь проходят реальную неопределённую попытку, восстановление
  и проверку запрета повтора. Противоречивая квитанция всё ещё отклоняется.
  [Разбор](../assets/audits/2026-11-07-digest-history-writer/gate-lifecycle-fixtures.json).

<a id="j455"></a>

### J455. Совместимое чтение привязки входа к исходному блоку

- Необязательное свидетельство в метаданных извлечения связывает
  точный скопированный диапазон Word, PDF или ODT с блоком и его хешем.
  Companion входа проверяет сохранённое извлечение, выбранную
  физическую часть, исходный диапазон и текст запроса.
- Старые записи без свидетельства сохраняют прежние байты и
  идентичности. Новые runtime writers не включены. Полнота
  физического покрытия всего входа не утверждается.
- Проверка закреплённого примера J446 проводится на отдельной
  копии, не дописывает происхождение в legacy-историю и не
  обращается к провайдеру. Текст с добавленной сноской не
  становится подтверждённым исходным диапазоном.
  На PDF 587739 подтверждён диапазон [204,305). Расширенный
  диапазон [204,331) пересекает нормализованный пробел.
  [Квалификация](../assets/audits/2026-11-07-block-input-reader/qualification.json).
- Для будущей записи требуется подтверждённая выкладка этого
  reader, отдельная same-pass квалификация и измерение дампа.

<a id="j456"></a>

### J456. Запись исходных диапазонов без повторного чтения файла

- Наблюдатель существующего XML-обхода сохраняет ограниченные
  хеши и счётчики. Готовый witness добавляется после прежних
  метаданных в их общий бюджет. Companion входа связывает только
  целые подтверждённые runs. Ошибка наблюдателя сохраняет прежний путь.
- На 59 закреплённых путях (50 SHA) совпали текст, выбор частей,
  native manifests, потери и ошибки reader-only и writer. По каждой
  стороне было 59 чтений корня, 375 XML-разборов и 470 открытий ZIP.
  Независимый verifier подтвердил 502 runs; 54 не входят в его узкий
  профиль, 33 недоступны его ZIP-декодеру. Они не объявлены проверенными.
- На J446 исходный запрос и резерв стоимости не изменились.
  Подтверждён [157,267), диапазон со сноской [157,272) отклонён.
  Пройдены submit, сбор результата, очистка и два восстановления
  каждого состояния; queued и cleaned восстановлены reader-only J455.
  В этом примере прирост после очистки составил 7884 байта SQL
  и 3264 байт gzip.
  [Квалификация](../assets/audits/2026-11-07-block-input-writer/qualification.json),
  [корпус](../assets/audits/2026-11-07-block-input-writer/corpus-qualification.json).
- Дополнительный прогон на семи закреплённых корнях подтвердил
  прежние тексты, выбор частей, манифесты и число чтений. Два PDF
  и один ODT дали 31 подтверждённый диапазон. Пробелы нормализации
  PDF и неподдержанные узлы ODT остаются без привязки.
  [Квалификация PDF и ODT](../assets/audits/2026-11-07-block-input-writer/native-qualification.json).
- Запись выпускается только после подтверждённой выкладки reader J455.
  Полное физическое покрытие входов и provenance legacy не утверждаются.
  Сырые файлы не сохраняются; решение о backend остаётся отложенным.
- Первый полный gate выявил устаревшее ограничение размера в тесте
  для 3000 блоков. Тест теперь отдельно проверяет рост базовых
  метаданных, лимиты записи о диапазонах и общий бюджет.
  [Результат](../assets/audits/2026-11-07-block-input-writer/gate-metadata-budget.json).

<a id="j457"></a>

### J457. Чтение точных частей дайджеста с ограниченным контекстом

- План связывает каждую часть с её HTML, владельцем и событиями.
  Профиль явно указывает, что исходный семантический контекст
  превышал бюджет хранения. Исправления, которым нужен полный
  контекст, остаются недоступны для таких записей.
- На штатном рендерере заголовок длиной 70 000 символов дал одну
  отправляемую часть в 437 байт. Прежний путь терял её HTML после
  очистки. Контрольный снимок нового формата восстановил точные
  байты и связь с событием. Повреждённые ссылки отклонены атомарно.
- Снимок v1 восстановился с прежним дампом и идентификатором.
  Запись нового профиля требует отдельного этапа после
  подтверждённой выкладки reader.
  [Квалификация](../assets/audits/2026-11-07-digest-rendered-history-reader/qualification.json).

<a id="j458"></a>

### J458. Сохранение частей дайджеста при превышении бюджета контекста

- При переполнении полного семантического снимка writer сохраняет
  точный HTML и события каждой части в транзакции замораживания
  перед отправкой. Для обычного дайджеста сохраняется профиль v1.
- Штатный рендерер дал 437 байт HTML при заголовке в 70 000
  символов. Проверены отправка, очистка и два восстановления
  на J457. Прирост составил 2176 байт SQL и 613 байт gzip после
  очистки. Полный контекст помечен недоступным для исправлений.
- Отказ после вставки снимка откатывает запись до отправки.
  Повтор после подтверждённого сбоя доставки использует
  сохранённый снимок и пропускает уже отправленные части.
  [Квалификация](../assets/audits/2026-11-07-digest-rendered-history-writer/qualification.json).


<a id="j459"></a>

### J459. Проверка выпуска и границы приёмки J453–J458

| Этапы | SHA выпуска | CI | Выкладка |
| --- | --- | --- | --- |
| J453 | `715a3fc4` | [CI 42344737313](https://github.com/sobolevbel/lexinform/actions/runs/42344737313) | [deploy 42345207644](https://github.com/sobolevbel/lexinform/actions/runs/42345207644) |
| J454–J455 | `8c383e8e` | [CI 42345307627](https://github.com/sobolevbel/lexinform/actions/runs/42345307627) | [deploy 42345970585](https://github.com/sobolevbel/lexinform/actions/runs/42345970585) |
| J456–J457 | `1a83f49f` | [CI 42346059220](https://github.com/sobolevbel/lexinform/actions/runs/42346059220) | [deploy 42346501766](https://github.com/sobolevbel/lexinform/actions/runs/42346501766) |
| J458 | `fbbef5e9` | [CI 42346594468](https://github.com/sobolevbel/lexinform/actions/runs/42346594468) | [deploy 42347202510](https://github.com/sobolevbel/lexinform/actions/runs/42347202510) |

- Квитанции фиксируют проверенный SHA, переход реле, перезапуск и
  активный таймер. Пакет J456–J457 дождался reader J455, а J458
  дождался reader J457.
  [Квитанции](../assets/audits/2026-11-07-document-architecture-release/digest-rendered-history-writer.json).
- DA5.1 сохраняет проверенные диапазоны Word, PDF и ODT в пределах
  бюджета. Пробелы нормализации и неподдержанные узлы остаются
  без привязки. DA7.1 сохраняет точный HTML частей дайджеста при
  переполнении бюджета контекста. Полный контекст таких записей
  недоступен для исправлений.
- Наблюдавшийся tip ветки state — `4764779e91d9d8fe5609aba45de02f4a792479be`.
  Перепись J452 для этого снимка подтверждает один полный первичный
  цикл и пять частичных вторичных цепочек. Эти данные предшествуют
  J453–J458 и не подтверждают новые рабочие циклы.
  [Перепись](../assets/audits/2026-11-07-da10-fresh-census/qualification.json).
- Для DA0.6 требуется независимая разметка с обоснованиями и границами
  групп инициатив. Для DA3.3 и DA4.1/4.4 сохраняются открытые вопросы
  приёмки естественных ODT-профилей, полноты и нормативных связей.
  Фактический legacy import, естественное истечение современных
  записей и недостающие циклы DA10.7 требуют отдельных наблюдений.
  Выкладка сама по себе их не подтверждает.

<a id="j460"></a>

### J460. Чтение исходного контекста сенатского скана

- Локальный цикл с закреплённым PDF и контрольным заключением
  воспроизвёл подмену исходного вопроса после очистки. Исправление
  перечитало изменённые PDF и заключение для прежнего сообщения.
- Reader проверяет точный контекст без PDF по хешу в общем пуле
  текста. Для исправления PDF проверяется по исходному манифесту.
  При отсутствии контекста у современного результата с известной
  привязкой исправление останавливается до чтения источников.
- Контрольный снимок v3 восстановил исходный вопрос без чтения
  заключения. Изменённый PDF отклонён. Отсутствующая запись
  контекста отклонена атомарно при восстановлении.
- Запись формата v3 требует отдельного этапа после выкладки reader.
  [Квалификация](../assets/audits/2026-11-07-senate-scan-context-reader/qualification.json).

<a id="j461"></a>

### J461. Запись исходного контекста сенатского скана

- При подтверждении отправки батча контекст без PDF и ссылка на
  него сохраняются атомарно. Действующие варианты v1/v2 сохраняют
  прежнее представление. Запись v3 ждёт подтверждённой выкладки J460.
- Оба офлайн-адаптера прошли очередь, подтверждение отправки, сбор,
  очистку и исправление. Исходный вопрос восстановился без чтения
  заключения при совпадении PDF. Изменённый PDF отклонён.
- Сбой после вставки контекста оставил submitting intent без нового
  тела, элемента или батча. Повтор не отправил запрос. Очистка
  удалила контекст после удаления последней ссылки.
- Два восстановления на J460 сохранили точные дампы. Прирост после
  очистки составил 9417 байт SQL и 378–380 байт gzip.
  [Квалификация](../assets/audits/2026-11-07-senate-scan-context-writer/qualification.json).

<a id="j462"></a>

### J462. Чтение точных диапазонов после обрезки входа

- Reader проверяет, что сохранённые координаты задают полное
  пересечение подтверждённого блока с исходным сегментом входа.
  При наличии замороженного текста он сверяет хеш выходного среза.
- На PDF 587739 при контрольном лимите 1024 обрезка оставила
  диапазон [418,488) из блока [418,492). Он соответствует
  [423,493) первой страницы в извлечённом тексте. Стандартный
  лимит этот документ не обрезает.
- После очистки остаются проверяемые координаты и сохранённый хеш.
  Для независимой проверки байтов произвольного среза нужен
  исходный материал. Прежние записи сохраняют представление.
  Запись пересечений ждёт подтверждённой выкладки reader.
  [Квалификация](../assets/audits/2026-11-07-clipped-block-reader/qualification.json).

<a id="j463"></a>

### J463. Запись точных диапазонов после обрезки входа

- Writer проверяет хеш целого исходного блока и совпадение
  пересечения с выходным срезом при подготовке запроса. При
  нехватке места приоритет сохраняют целые блоки.
- На PDF 587739 с лимитом 1024 запись [418,488) пережила сбор,
  очистку и два восстановления на J462. Запрос и целые строки
  совпали с базой. Прирост составил 372 байта SQL и 78 байт gzip.
  При стандартном лимите дампы совпали побайтно.
- Синтетический Word проверен с одним и четырьмя workers.
  ODT использует общий путь. Отдельного естественного ODT с
  обрезкой в очереди в этой проверке нет.
- Запись нового представления ждёт выкладки J462.
  [Квалификация](../assets/audits/2026-11-07-clipped-block-writer/qualification.json).

<a id="j464"></a>

### J464. Проверка контракта исходного запроса DA5.6

- Проверены точки подготовки analysis/reanalysis, обычных и исправленных
  поправок, дополнительных документов, joint/name, explanation и включённых
  review. Они замораживают подготовленный адаптером запрос до отправки.
  OpenAI и Anthropic отправляют сохранённые байты.
- При подтверждении отправки manifest связывается с исходным intent.
  Он фиксирует SHA запроса, модель, версию prompt и хеши сообщений,
  схемы ответа и параметров. Сбор ответа использует исходную идентичность
  запроса. Актуальный Bill используется отдельно для проверки применимости.
- Typed PreparedInput сохраняется при подтверждённой привязке.
  При неизвестном физическом родителе замороженный запрос фиксирует
  логический вход, а физическая привязка остаётся unknown.
- После очистки manifest проверяет предъявленные исходные байты.
  Полное восстановление истёкшего payload выходит за профиль J123.
  Отсутствующие legacy-сведения остаются unknown.
- Тесты exact payload и manifest подтверждают восстановление до отправки,
  сохранение свидетельства после очистки и отклонение чужого intent.
  Эта проверка закрывает реализацию DA5.6. Приёмка естественных циклов
  остаётся в DA10.7.

<a id="j465"></a>

### J465. Чтение исходного контекста сканов поправок комиссии

- Reader v4 проверяет исходный запрос и контекст комиссии по хешам.
  Принимает полный PDF или подтверждённое удаление первой страницы.
  Сохраняет правила чтения версий 1–3.
- Два синтетических PDF прошли штатное извлечение, отправку через fake,
  сбор, очистку и два восстановления. Исходные запросы совпали.
  Изменённый PDF и отсутствующая связь отклоняются.
- В четырёх закреплённых контролях J340 текстовый слой скана пуст.
  Естественный пример смешанного скана в этой выборке отсутствует.
- Этап читает v4 без включения записи в штатном submit.
  [Квалификация](../assets/audits/2026-11-07-committee-scan-context/qualification.json).

<a id="j466"></a>

### J466. Запись исходного контекста сканов поправок комиссии

- Подтверждённая отправка сохраняет исходный контекст для полного скана
  с разреженным текстовым слоем и скана с доказанным удалением титульной
  страницы. Сырые PDF не сохраняются.
- В двух синтетических контрольных циклах исходный запрос совпал по SHA.
  После очистки и двух восстановлений reader J465 восстановил исходный
  вопрос по проверенному PDF. Изменённый PDF отклоняется.
- Прирост gzip после очистки составил 235 и 241 байт соответственно.
  Writer ждёт подтверждённой выкладки J465.
  [Квалификация](../assets/audits/2026-11-07-committee-scan-context/qualification.json).

<a id="j467"></a>

### J467. Чтение неизменяемой истории заседаний

- Reader связывает исторический план с логическими участниками,
  фактическими публикациями и сохранёнными снимками Bill. Проверяет
  канал, порядок частей и подтверждённые идентификаторы.
- Короткое, групповое и многочастное сообщения восстановлены дважды
  после очистки. Дамп с потерянной привязкой отклоняется атомарно.
  Старые дампы принимаются без создания истории задним числом.
- Штатная запись на этом этапе выключена.
  [Квалификация](../assets/audits/2026-11-07-sitting-history/qualification.json).
- В закреплённом снимке state из 31 отправленной строки 17 относятся
  к 13 группам с сохранённым HTML. Максимум в этих группах составил
  57 442 байта, превышений 128 КиБ нет. Для 14 строк размер неизвестен.
  Связанных физических продолжений в снимке нет.
  [Срез state](../assets/audits/2026-11-07-sitting-history/state-census.json).

<a id="j468"></a>

### J468. Запись неизменяемой истории заседаний

- Первое замораживание доставки атомарно связывает версию, участников,
  физические части и снимки Bill. Совпадающие снимки переиспользуются.
  Ошибка записи откатывает подготовку до отправки.
- Короткое, групповое и многочастное сообщения сохранили точный HTML
  и подтверждённые привязки после очистки и двух восстановлений на J467.
  Прирост gzip составил 1038, 920 и 3408 байт соответственно.
- Два синтетических входа через обычный watcher превысили 128 КиБ
  либо 32 части. Они доставлены с прежними HTML, целями ответов и ID.
  Запись истории пропущена. Частота таких случаев на реальных данных
  не измерена. Writer ждёт подтверждённой выкладки J467.
  [Квалификация](../assets/audits/2026-11-07-sitting-history/qualification.json).

<a id="j469"></a>

### J469. Чтение неизменяемой истории публикации актов

- Reader проверяет точный план сообщения, владельца и связь с
  публикацией без искусственного StatusChange. Старые версии сохраняются.
- Старый reader атомарно отклоняет новый формат. Новый reader
  принимает старый и новый дампы и отклоняет потерянную привязку.
  Штатная запись ждёт отдельного этапа.
  [Квалификация](../assets/audits/2026-11-07-act-history/qualification.json).

<a id="j470"></a>

### J470. Запись неизменяемой истории публикации актов

- Отдельное сообщение о публикации акта сохраняет замороженный план
  и точный HTML в пределах 64 КиБ. Подтверждённые идентификаторы
  и цель ответа совпадают после очистки и двух восстановлений.
- Офлайн проверены неопределённая доставка и подтверждение после
  аудированного повтора с сохранением той же версии.
  Прирост дампа в контрольном цикле составил 8312 байт SQL и 611 байт gzip.
- Многочастная доставка проверена отдельно и остаётся вне этого профиля.
  В закреплённом state один сохранившийся план занял 22 823 байта.
  Размер трёх очищенных планов неизвестен.
  [Квалификация](../assets/audits/2026-11-07-act-history/qualification.json).
