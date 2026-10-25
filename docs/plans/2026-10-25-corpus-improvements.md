# Улучшение корпуса и исследовательских инструментов

Открыт 25 октября 2026. База `c8f1d83`. Автоматические C5b/C6b/C7b проверены и закоммичены
локально 25–26 октября; 26 октября добавлены форма и проверяемая запись человеческих ответов.
Открыто только само человеческое прочтение C6c. Продолжение и evidence ниже.
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
- [x] C5b. Свежий supplementary catalog и адресные RPW/supplement downloads по required cases.
  Листинги не подменяют новые process trees или оригиналы; массовой докачки пока нет.
- [x] C6a. Очередь relation/span annotation и локальный просмотр источников/decision diff.
  Oracle/basis/read scope явные; граф инициатив удерживает связанные редакции в одном split.
  Machine labels и уже использованный holdout остаются regression, новая независимая разметка
  требует реального прочтения исходников; заполнение этой очереди отдельно ниже.
- [x] C6b. Физический разбор 170 still-empty originals; результаты и previews ниже.
- [x] C6b. Подготовить отдельный pending human holdout из ранее не использованных инициатив.
  Три оригинала зарезервированы до извлечения; новый human oracle ещё не создан.
- [x] C6c0. Форма ручной проверки и валидируемая запись ответов поверх sealed queues:
  страницы оригинала, точные цитаты, закрытые словари, конфликты reviewers; agent reviews
  не становятся labels. Формы для holdout и 170 empty originals собраны.
- [ ] C6c. Человеческое прочтение: обязательность приложений, спорные связи, подтверждение
  независимости инициатив и evidence нового holdout. Machine labels не закрывают эту часть.
- [x] C7a. Подключить 13 существующих suites к общему запуску, включая process/Senate;
  проверить missing-input и отрицательные контроли, сохранить старые imports/CLI.
- [x] C7b. Общий physical-health inventory остальных families и cross-run extraction cache
  по raw/options/version identity; результаты продолжения ниже.

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

### Хранение и приоритет после первой поставки

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

Приоритетом после первой поставки были C6b, затем C5b по конкретным недостающим cases.
Их продолжение ниже. OCR только там, где просмотр оригинала подтверждает потребность;
платные модели и массовый backfill не запущены.
Переиспользовать общие HTTP/extraction/artifact helpers в остальных legacy collectors стоит
постепенно с их resume keys и corpus comparisons, а не переписывать весь scripts каталог сразу.

### C005b. Supplementary catalog и обязательные источники

`corpus_sejm_sources` строит supplementary catalog из pinned свежего prints listing, хранит diff
и явные причины required downloads. `catalogue`, `download`, `install` и publication сохраняют
команду повтора, provenance и SHA; запись каждого запроса и данные остаются в sealed runs.
HTTPS Orka добавлен в общий allowlist с прежними ограничениями redirects, времени и размера.

`c005b-catalogue`: 2479 rows / 2466 unique IDs; против прежнего supplementary listing —
73 added / 3 removed / 1 changed ID. Это смена листинга, не доказательство удаления документа.
В свежем bills listing 70 RPW отсутствовали в прежнем acquisition index; массовой докачки нет.
Supplementary catalog опубликован дополнительно в `sejm/snapshots/2026-10-25/term10`,
исторический каталог не заменён.

`c005b-required-sources-network` скачал три required RPW (31529, 31544, 32084 за 2026 год),
два полных supplements (2553-s, 3039-005) и три отдельных raw-only источника для human holdout.
У 934-002 два conflicting attachment names: `934-002.pdf` отвечает 404, `934-008.pdf` сохранён;
весь required case остаётся unavailable, частичная установка не выполнена. Первая попытка
без сетевого доступа сохранена отдельно как unavailable, не подменяет успешный public-source run.

`c005b-install-required` установил пять полных cases, сохранив прежний RPW index и все его rows.
`c005b-repeat-install` дал пять `already_installed`. RPW/31529 имеет 21 страницу без текста;
2553-s — 10 страниц и 706 извлечённых символов. Наличие оригинала не закрывает scan/completeness.
Новые human sources никогда не проходили extraction/install и лежат только в collection run.

После `scripts/index.py --sejm-snapshot 2026-10-25`: 3368 → 3371 entities, добавлены только
три RPW. Из прежних entities изменились только supplementary lists druki 2553/3039;
удалений нет. Прежние INDEX.json/INDEX.md сохранены по SHA в `out/corpus/input-snapshots/`.
Diff — `out/corpus/c005b-index-diff.json`; прежний `scripts/verify.py` снова даёт corpus intact.

### C006b. Разбор пустых selections и новая человеческая выборка

`corpus_empty_review` читает все still-empty originals из pinned generation, отдельно рассматривает
каждый bounded ZIP member, физические PDF pages и читаемые unselected parts. Role, принадлежность
и обязательность приложений остаются unknown до содержательного evidence. JSON/HTML отделяет
physical/parser errors, unsupported members, readable unselected parts и pages без текстового слоя.
Preview показывает только первую страницу PDF; это не полное прочтение документа.
Перед rendering проверяется SHA фактически раскрытого PDF member: одинаковые имена в ZIP
не могут подменить preview другого документа. Duplicate-name и PDF page-error regressions
добавлены после независимого review; initial full gate заменён повтором на исправленном коде.

`c006b-empty-originals-qualified`: **170 originals / 355 leaves**, 295 inspected и 60 unsupported
members. Actions пересекаются: 108 originals с readable unselected parts, 60 с PDF pages без текста,
19 с unsupported members. Карта содержит 1636 PDF pages, из них 991 без извлечённого текста.
161 distinct PDF preview сохранён, rendering errors 0; inputs/code unchanged. Первая попытка
`c006b-empty-originals` сохранена как failed: во время неё обновился INDEX после C5b.
Повтор выполнен на стабильном индексе; guard не отключался, failed run не переименован в успех.
После проверки SHA перед preview полный повтор `c006b-empty-originals-preview-verified`
сохранил все 170 observations; diff `c006b-preview-sha-validation-diff.json` имеет status=same.
Все три визуально прочитанные страницы byte-identical новому rendering.

Просмотрены страницы 1 originals 158276/158279/162963: видны соответственно письмо, заголовок
проекта и письмо, хотя на этих страницах извлечение пустое. Это подтверждает потребность OCR
этих трёх страниц; остальные страницы и правовая роль всего комплекта не прочитаны.
[Page observations](../assets/audits/2026-10-25-corpus/agent-page-observations.json) сохраняет raw/image
SHA, page coordinates, точные visual transcriptions, reviewer_kind=agent и split=regression.
OCR не запускался. Обязательность упомянутых приложений и спорные связи не выведены из названий.

`corpus_holdout` консервативно исключает все инициативы прежнего INDEX и raw byte aliases:
старый `checks/00_replay.py` читал весь RPW index, поэтому старые orphan RPW не стали новым holdout.
`c006b-human-holdout`: **3 pending cases / 3 groups**, RPW/35346/2026, RPW/35635/2026,
RPW/35783/2026. Все 14511 прежних raw paths сверены по SHA; 6032 ZIP/docx/xlsx/odt containers
проверены на прямые вложения совпадающего размера, unavailable 0. Исключены также hashes новых
regression downloads. Всего 12367 известных consumed raw identities и 13786 indexed entity/doc
identities. Baseline INDEX сохранён до установки, его SHA согласован с pinned raw inventory.

Статус каждого нового case — `holdout_pending`, read_scope=`not_read`, reviewer/oracle=null.
Агент видел только метаданные и hashes, не legal text. HTML содержит ссылки на оригиналы;
человек должен подтвердить независимость и связи, затем записать фактический read scope/evidence.
Вложенные ZIP для alias-проверки не раскрывались рекурсивно; выборка маленькая и адресная,
не случайная оценка всего корпуса. Готового human oracle эта работа не заявляет.

### C007b. Persistent extraction cache и physical health

`ExtractionCache` сохраняет bounded extraction в append-only entries по raw SHA,
extractor/options/contract, версиям Python/libraries и SHA runtime sources. Hashes identity/result
проверяются на каждом попадании, corruption не перезаписывается. `corpus_derivatives --cache`
включает его явно; default generation и старые corpus caches работают по-прежнему.

`c007b-cache-first`: 177 originals, 162 misses / 15 same-byte hits.
`c007b-cache-second`: 177 hits / 0 misses. Оба дали 7 recovered / 170 still_empty.
Observations и входные hashes совпали между прогонами и с прежним pinned C003 generation;
diffs `c007b-cache-diff.json` и `c007b-cache-versus-prior-generation.json` имеют status=same.
Изменений extraction policy нет.

`corpus_physical` проверяет пять raw families, их сохранённые gzip/UTF-8 caches и catalog-defined
Senate originals. Hashes и recorded acquisition gaps отдельны от bounded PDF/ZIP/OLE/XML checks;
parser failure и неподдержанный ZIP codec не выдаются за доказанную порчу bytes. Nested containers,
полное декодирование OLE/RTF, rendering всех PDF и юридическая полнота явно вне этого inventory.

`c007b-physical-six-families`: **16341 raw files / 12073227147 bytes**, 16347 keyed observations
с учётом шести отсутствующих RPW originals. 16329 valid containers, 11 bounded exclusions,
один strict PDF parse failure (`documents/files/714802.pdf`, PdfReadError; не вердикт о порче).
Все 1825 Senate originals прошли container checks. Text caches: 13454 readable, 1237 empty,
6 missing. Это число пустых кешей всех шести families, не 170 RCL selections из C006b.
Изменённых входов/кода нет; manifest и все artifacts проверены.

### Gate и локальные коммиты продолжения

Полный offline gate исправленного кода: **4116 tests**, failures/errors/skips/xfails 0,
669,891 s. Strict mypy — 386 файлов; Ruff src/tests/tools и format green. XML:
`out/corpus/2026-10-25-corpus-completion-qualified-tests.xml`. Финальный CI slice
`c007b-ci-preview-verified` passed; Python sources полного gate совпали с его snapshot.
Strict docs build и локальные ссылки green.

Все 14 manifests продолжения проверены, включая сохранённый failed initial empty-review run;
его статус не заменялся. Малый сохраняемый отчёт —
[completion evidence](../assets/audits/2026-10-25-corpus/completion.json).
Исторический external selection `not_run` не объявлен passed по результатам physical inventory.

Локальные коммиты: C5b `198d0da`, C7b `511e199`, C6b `e9536a9`;
индекс/описание отдельного corpus repository — `ec70eca`. Push не выполнялся.
Остался C6c: реальные человеческие evidence и разрешение неизвестных связей/приложений.
Pending human queue доступна в `out/corpus/runs/c006b-human-holdout/result.html`.

### C006c. Форма и запись человеческой проверки

Статус: инструмент принят локально, commit `650ae3f`; человеческих ответов пока нет.

Прежний validator принимал evidence только как span извлечённого текста. У 170 из 177 cases
очереди C6a этот текст пуст, а три holdout originals намеренно не извлекались, поэтому прочитанное
человеком в оригинале некуда было записать. `corpus_review` добавляет offline suites
`review-form` и `review` поверх sealed `holdout`, `empty-review` или `annotations` queue.

Форма даёт ссылки на оригиналы, раскрытые ZIP members, page-1 previews с проверкой SHA
по manifest очереди, число страниц, закрытые словари ответов и поля цитат. Legal text она
не читает. Черновик хранится в браузере, сохранение выгружает answers JSON с SHA manifest очереди.
`review` до записи проверяет весь файл: reviewer и дату, объявленные прочитанные страницы или
диапазоны, хотя бы одну цитату на каждый ответ кроме `unknown`. `text_layer` цитата должна быть
на этой странице PDF или в тексте Word/ODF; `visual` привязана к объявленной странице, но не к
текстовому слою. Цитата без offsets должна встречаться в прочитанной выдержке ровно один раз.
Один отвергнутый case отвергает весь файл. Labels образуют только human reviews; расхождение
людей остаётся `conflict`. `holdout` возможен только при согласном `independent_initiative: yes`
и без agent review; уже потреблённые очереди остаются `regression`.

| Run | Cases | Originals | PDF pages | Manifest SHA |
| --- | ---: | ---: | ---: | --- |
| `c006c-holdout-form` | 3 | 3 | 50 | `62be5a0aeb1cf115a2b7a737f799983dcdce70d0d4f6d95605509baca3ade0b1` |
| `c006c-empty-originals-form` | 170 | 355 | 1636 | `c77fdc48db0a85eb74a94e2f2a1bc6c85012acd737ecae458ddd354b2d699b5c` |

Во второй форме 296 ссылок на раскрытые members и 169 previews; 1636 страниц совпадают с картой
C6b. Обе формы собраны на чистом коде, manifests проверены, изменённых входов нет.
Форма проверена в браузере на реальной empty-review очереди: черновик переживает reload,
export → import → export идентичен, выгрузка принята `review` с итогом 1 reviewed / 169 pending,
все cases `regression`. Это проверка формата в scratch, затем удалённая; ответом reviewer она не
является и в runs не записана. Агент открывал только формы, не holdout originals.

Полный offline gate: **4130 tests**, failures/errors/skips/xfails 0, 697,567 s; strict mypy
387 файлов, Ruff и format green. XML: `out/corpus/2026-10-26-c006c-review-tests.xml`.

Для C6c человек открывает `out/corpus/runs/c006c-holdout-form/result.html` и
`out/corpus/runs/c006c-empty-originals-form/result.html` из файловой системы, читает оригиналы,
сохраняет answers JSON и записывает его suite `review` (команды в `tools/README.md`).
До этого holdout остаётся `holdout_pending`, а роли, связи и приложения — `unknown`.
