# Улучшение корпуса и исследовательских инструментов

Открыт 25 октября 2026. База `c8f1d83`. Статус: первая поставка проверена локально;
оставшиеся C5b/C6b/C7b перечислены явно.
Основание — [обзор корпуса](../reviews/2026-10-25-corpus-workflow.md).

Цель: воспроизводимые измерения, общие безопасные примитивы scripts, восстановление отсутствующих
материалов и адресуемая разметка без перезаписи исторических оригиналов и benchmark inputs.
Backend хранения не выбирается. Production state, Telegram и платные модели не используются.

## Checklist

- [x] C1. Общий код guards/provenance/artifacts и исторических RCL cases; старые imports/CLI
  совместимы. Сверить прежний health report и regression slices на неизменном корпусе.
- [x] C2. Постоянный run manifest: команды, версии/опции, status, входы/SHA, артефакты/SHA,
  exclusions, обнаружение missing artifacts. Единый diff решений поверх существующих suites;
  отсутствие обязательных inputs не считается успехом.
- [x] C3. Переизвлечение 118 empty ZIP и 59 PDF в отдельную generation, исходный кеш сохранён.
  Сохранить raw/extraction hashes, limits/ошибки/роли; recovered text не объявлять юридически полным.
- [x] C4. Адресная докачка семи отсутствующих RCL originals и повтор трёх consultation refusals.
  Проверить формат/размер/HTML, no overwrite, provenance и повторные решения. Ошибки источника
  сохраняются как недоступность; отсутствие после отказа не заполняется выдуманными данными.
- [x] C5a. Датированный snapshot Sejm prints/processes/bills и diff к прошлому снимку.
  Новый индекс явно указывает свежий bills listing и прежние cached prints/process trees.
- [ ] C5b. Свежий supplementary catalog и адресные RPW/supplement downloads по required cases.
  Листинги не подменяют новые process trees или оригиналы; массовой докачки пока нет.
- [x] C6a. Очередь relation/span annotation и локальный просмотр источников/decision diff.
  Oracle/basis/read scope явные; граф инициатив удерживает связанные редакции в одном split.
  Machine labels и уже использованный holdout остаются regression, новая независимая разметка
  требует реального прочтения исходников; заполнение этой очереди отдельно ниже.
- [ ] C6b. Разобрать 170 still-empty originals, необходимые приложения и спорные связи;
  подготовить отдельный новый human holdout из ранее не использованных инициатив.
- [x] C7a. Подключить 13 существующих suites к общему запуску, включая process/Senate;
  проверить missing-input и отрицательные контроли, сохранить старые imports/CLI.
- [ ] C7b. Общий physical-health inventory остальных families и cross-run extraction cache
  по raw/options/version identity. В поставке есть raw inventory, профильные replays и дедупликация
  внутри поколения; это не полная физическая проверка всех файлов и не общий persistent cache.

## Правила приёмки

Finished parts коммитятся отдельно. Общие helpers не меняют selection/text policies.
Для corpus runs фиксируются исходные SHA и проверяются неизменные входы/код. Сохранённый JSON
не заменяется. Производные поколения и source observations отделены от original download paths.
Новые labels имеют evidence и происхождение; неизвестная роль или связь сохраняет unknown.
При изменении extraction/trim/comparison нужен полноценный профильный corpus gate.

## Журнал

### C001. Общие примитивы исследовательских scripts

Статус: принят локально, commit `48bfe02`. `corpus_runtime`, `corpus_artifacts`, `corpus_rcl` объединяют guards,
code provenance, pinned artifacts, cache adapters и построение исторических stage cases.
Девять прежних tools используют общие функции; прежние imports/CLI сохранены через re-exports.
Низкоуровневые helpers не импортируют pytest/World/suites, что проверено отдельным subprocess.
Health больше не принимает document-like filename из version basis за URL; fake HTTP hosts
по-прежнему допустимы, реальная сеть этим изменением не включается.

В постоянный registry зарегистрированы прежний health и новый проход: 10418 observations,
все records/actions/counts/input hashes совпали. Diff `out/corpus/c001-health-diff.json`: same.
Raw files, cached text и runtime src этой частью не менялись.
Общий gate рабочего набора: 4078 tests, errors/failures/skips/xfails 0; strict mypy 380 файлов,
Ruff src/tests/tools green. XML: `out/corpus/2026-10-25-corpus-improvements-tests.xml`.
Последующие части и их проверки записаны ниже.

### C002. Реестр, поколения и общий путь чтения

Статус: принят локально, commit `3217e80`.

`corpus_runs` запускает прежние suites, сохраняет started/result/log/code.tar.gz и recursive
artifact manifest с SHA. Нельзя занять существующий ID, подменить output/corpus/suite или preset
вроде `--kind`. Проверка обнаруживает потерянные и изменённые артефакты. Старый JSON можно
зарегистрировать с его исходным provenance, не приписывая ему текущий producer. Diff различает
изменённые observations и inputs. Пустая population не получает completed status.

`corpus_extraction` задаёт общий bounded reader; `corpus_derivatives` сохраняет новое поколение
без замены old caches. `DerivedTexts` проверяет pinned report, raw source SHA и text SHA и
используется и replay, и очередью разметки. Новая generation включается явно; старый view сохранён.
Оригинальный индекс для воспроизведения тоже можно передать явно после append-only installation.

Run `c003-empty-rcl`: 177 originals, **7 recovered / 170 still_empty**, 0 errors/изменённых
inputs/кода. Восстановлены 709230, 711435, 711445, 718474, 719233, 754701, 771111; всего 8
уникальных gzip объектов, включая empty text. В 754701 выбран supporting text о staffing REMIT,
автоматический kind остаётся unknown: восстановление не означает, что найден основной закон.
Просмотрены заголовки/окончания всех семи восстановленных текстов и сохранённые part selections;
это диагностический просмотр, не полная юридическая adjudication. Повтор `c003-replay-preserved-generation`
с точным старым index snapshot дал те же 177 observations.

`c007-candidates-old-view` / `c007-candidates-new-generation`: **4276** stage snapshots,
**11 изменённых observations**, из них 10 изменили state; selected 2273 → 2281.
Просмотрены все изменения и заголовки выбранных файлов. Появление supporting text 754701
позволяет выбрать прежний bill 752694 в двух случаях; его cache не изменялся. Три состояния
остались unverified, несмотря на наличие текста. Полный diff —
`out/corpus/c007-candidates-generation-diff.json`; 1383 missing cached sources остаются явными.
Изменены inputs, поэтому это сравнение поколений данных, а не доказательство неизменного runtime.

`process` wrapper сохранил все **3294** keyed observations и summary прежнего runner:
term 8/9/10 — 1340/1016/938. `senate`: **816** observations, 4903 mutation probes;
отрицательные контроли 10418 RCL + 980 Sejm, matches 0. 23 scans и две неоднозначные даты
остаются explicit outcomes. Внутри summary исторического Senate catalog сохранены старые
source-path descriptions; текущие реально прочитанные paths/SHA находятся в `read_inputs`.
External selection после установки: **20407** observations, **1383** missing caches,
status **not_run**. Общий gate не объявлен пройденным на основании доступной части корпуса.

`c006-review-queue-qualified`: **177 pending cases / 105 connected groups**, HTML и JSON
template, точные диапазоны показанных excerpts. Source aliases и связи инициатив соединяются
транзитивно; совпадение bare wykaz number не создаёт связь. Evidence validator проверяет quote
и source SHA, запрещает agent/consumed cases как independent holdout. Новых gold labels нет.
Для семи восстановленных текстов сохранён отдельный
[диагностический review excerpts](../assets/audits/2026-10-25-corpus/agent-excerpt-labels.json):
reviewer_kind=agent, split=regression, точные quotes/SHA, whole-package role и связи unknown.
Evidence spans проверены валидатором. Это ограниченные наблюдения, не human oracle.

Полный gate неизменного рабочего набора: **4085 tests**, failures/errors/skips/xfails 0,
579,501 s; strict mypy 381 файлов, Ruff src/tests/tools и format check green.
XML `out/corpus/2026-10-25-corpus-improvements-qualified.xml`. Strict docs build и ссылки green.
Python sources совпали со snapshot проверенного CI slice `c002-ci-qualified` (passed).
Все 15 выбранных run manifests проверены; статусы recorded/passed/not_run сохранены.

### C003. Адресная докачка и свежие листинги

Статус: данные применены и проверены; maintenance tools проходят тот же полный gate.

`corpus_http` объединяет allowlisted HTTPS, redirects, complete response, byte/time limits и
public response provenance. `corpus_collect` разделяет download/install: source run проверяется
по manifest, originals/cache создаются только при отсутствии или точном совпадении bytes,
старые index rows сохраняются. Advisory lock и content check защищают atomic index publish.
Новые installation runs сохраняют `index-before.json` для обратного воспроизведения.

`c004-rcl-gaps`: скачаны **7/7** missing originals, всего **550134 raw bytes**:
300535, 300536, 300677, 301219, 412632, 418667, 418672. Все семь кешей непустые.
787416, 787418, 787427 по-прежнему HTTP 200 HTML; они остались unavailable, без fake originals.
`c004-install-new-rcl`: 10411 прежних index records сохранены, добавлены семь новых.
Повтор `c004-repeat-install-qualified` получил семь already_installed без изменений index/raw/cache.
Первый install предшествовал добавлению автоматического index-before snapshot. Его точные
прежние bytes восстановлены из unchanged records и проверены по прежнему SHA
`cef6187dcfc72f522819a2ff39d4a7965ef2d984a883db626bd7eb90544bd19f`;
сохранены отдельно в `out/corpus/input-snapshots/` с provenance, sealed run не редактировался.

`c005-sejm-metadata` сравнивает 25 октября с 3 октября:

| Listing | Rows now | Added IDs | Changed IDs | Removed IDs |
| --- | ---: | ---: | ---: | ---: |
| prints | 3501 | 130 | 36 | 0 |
| processes | 1761 | 43 | 102 | 0 |
| bills | 1352 | 28 | 20 | 0 |

Снимок опубликован в corpus `sejm/snapshots/2026-10-25/term10` без замены старого.
Requests, hashes, response dates и pagination сохранены. Листинги читаются последовательно;
атомарный snapshot сервера не заявляется. Новых process detail trees, supplementary catalogs
или RPW attachments эта операция не даёт.

Corpus `scripts/index.py --sejm-snapshot 2026-10-25` использует новый bills listing, добавляет
metadata input hashes/source roots и `document.cached_empty`. Перегенерирован INDEX:
3368 entities, 10418 RCL docs, 833 RPW records. Ни одна entity не удалена; изменились только
rpw links у 42 entities и document lists у двух RCL projects. Сверены все 3032 input hashes.
Это обновление указателя, не новая правовая разметка и не обновление всех исторических данных.

### Хранение и следующий приоритет

Рабочие результаты находятся в постоянном ignored `out/corpus/runs`, а не `/tmp`.
Для ранних запусков до automatic code snapshots сохранён общий точный архив исходников
`out/corpus/source-snapshots/fd651588d892493255f0a67840148524525533dd548defac36f07e87a2d7e4ad.tar.gz`.
У следующих запусков свой code snapshot входит в manifest. Малый сохраняемый отчёт:
[implementation evidence](../assets/audits/2026-10-25-corpus/implementation.json).
Локальный durable каталог не является off-device backup; remote/retention policy пока не выбраны.

После обновления пройдён прежний `scripts/verify.py`: corpus intact. Отдельная raw опись
захешировала **14511** физических файлов пяти families, **11585076070 bytes**;
exact-byte duplicates — **485752250 bytes** (около 4,2%). Это физические downloaded containers,
не число законопроектов, не прогноз production storage и не проверка правовой полноты.
Raw inventory SHA и totals сохранены в implementation evidence. Originals не дедуплицировались,
download paths сохранены; изменения не добавляют документы или эти отчёты в SQLite dump.

Следующая полезная работа — C6b, затем C5b по конкретным недостающим cases. OCR только там,
где просмотр оригинала подтверждает потребность; платные модели и массовый backfill не запущены.
Переиспользовать общие HTTP/extraction/artifact helpers в остальных legacy collectors стоит
постепенно с их resume keys и corpus comparisons, а не переписывать весь scripts каталог сразу.
