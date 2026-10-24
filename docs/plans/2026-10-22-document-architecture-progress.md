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
Implementation commit: подготовлен к фиксации.

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
