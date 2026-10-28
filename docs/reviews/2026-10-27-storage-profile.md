# Хранение: измеренный текущий профиль

Статус: измерение J094 · 27 октября 2026. Runtime baseline:
[`83eadcf`](https://github.com/sobolevbel/lexinform/commit/83eadcf87c71fb3d5aa26f6378aebddb3e51bcaf).
State pin: `e3e262b841805ee549f1d3dcdcbba2a77d1f6ee6`, 27.10.2026 16:51:55 UTC.
Это изолированная копия, не утверждение о текущем live state.

## Что сохраняется сейчас

Текущий режим — SQLite с метаданными, результатами, проекциями и обязательными pending/frozen
payload. Постоянный архив всех оригиналов не включён. Незавершённый request сохраняет нужные
ему bytes по существующим правилам; SHA без сохранённых bytes не обещает replay. Решение
о расширении состава хранения, S3 или другом backend остаётся отложенным пользователем.

На pin: SQL **19 751 140 bytes**, deterministic gzip **4 007 469**, SQLite **20 758 528**.
В snapshot **458 bills / 378 runs**, один новый immutable computation и два application receipts.
Параметры retention из кода/документации: runs/sent payload — 90 дней, settled memo — 180;
pending/unknown, ready/unconsumed и связанные dependencies защищены. Конфигурация production
не извлекалась из secrets; правила и ограничения описаны в [retention](../state-retention.md).

## Исследовательский корпус и сохранённые ссылки

| Available raw family | Файлы | Bytes | Unique bytes внутри family |
| --- | ---: | ---: | ---: |
| RCL | 10418 | 5085878840 | 4602991139 |
| Sejm prints | 2630 | 3166113469 | 3166113469 |
| Sejm filings | 572 | 717139091 | 715325160 |
| Self-amendments | 66 | 40124793 | 40124793 |
| RPW | 830 | 2584024275 | 2584024275 |
| Catalogued Senate originals | 1825 | 479946679 | 479946679 |

Всего **16341 paths / 12 073 227 147 bytes**; **14191 unique contents / 11 587 474 897 bytes**.
Дубли — **2150 files / 485 752 250 bytes**. Family unique totals нельзя складывать как global
unique: есть cross-family aliases. Senate берётся из сохранённого catalog через существующий
`senate_sources`; research files/другие snapshots не приписываются original inventory.
Все 1825 catalogued originals доступны. Suffix не доказывает валидность, роль или полноту текста.

Новый read-only `measure_referenced_files` использует общие `saved_records`, `SqliteSnapshot`
и artifact/code guards. Он нашёл **437 occurrences / 44 unique physical SHA** в selection,
scan/review metadata, включая копии и observed candidates. С inventory совпали **13 SHA /
9 585 013 bytes**; **31 SHA** не найден. Для ZIP учитывается записанный outer container,
а не сумма выбранных members. URL, text/law digests и legacy scan fingerprints не превращаются
в raw identity. У **5 saved scan occurrences** нет точного source/input manifest.

Это нижняя граница доступности записанных физических ссылок, а не полный журнал потребления
и не размер нужного production archive. Исторический дневной поток скачиваний из этих данных
не восстанавливается. Для 31 SHA нужна адресная сверка provenance/URL; новый ответ по прежнему
URL не заменяет старые bytes без совпадения SHA. Массовая докачка из этой цифры не следует.

Exact-value inventory содержит **415 saved answer occurrences / 325 unique values**:
3 434 138 occurrence bytes против 2 512 584 unique bytes; canonical unique array gzip — **616 106**.
У 3 occurrences есть известный result ref, у 412 он отсутствует; paid origin не реконструируется.
Это измерение существующих JSON values, не разрешение удалить старые поля/контексты.

## Snapshot, Git history и checkpoint — разные затраты

С 13 октября на first-parent state history — **261 commits / 261 distinct SQL blobs**.
SQL между первым и последним snapshot вырос на **4 492 790 bytes**; внутри периода менялся
от **14 513 298** до **46 427 458 bytes**. После 23 октября end-of-day SQL уменьшился с
37 505 996 до 14 523 442 bytes. Поэтому линейный прогноз из двух концов здесь непригоден.

Для этого диапазона локальная текущая раскладка Git objects занимает **1 790 413 580 bytes**.
Отдельная временная non-thin упаковка тех же **811 objects**: pack **38 411 619**, index **23 780**,
41,69 секунды. Исходный object store не перепаковывался, временный pack удалён. Это две
измеренные локальные раскладки, не размер GitHub storage, не сетевой трафик и не самостоятельный
backup всей истории. Parent commits вне диапазона исключены; внешние base dependencies старой
раскладки отдельно не измерялись.

На копии текущего SQL через изолированный filesystem Git remote: restore **0,269 s**, dump
**0,105 s**, changed checkpoint **0,995–1,203 s**, unchanged **0,241 s**. Восемь checkpoints
создали семь commits; три синтетические пары report mutations добавили **5178 SQL / 175 gzip bytes**.
После локальной GC в этих временных репозиториях objects уменьшились с 36,75 МБ до 4,05 МБ.
GitHub/auth/runner latency этим не измерялись; pre-send/receipt boundaries не объединялись.

## Проверка на реальном сохранённом цикле

В pin есть metadata-only анализ **WPL/UD491**: immutable result, collection `ready`, projection
`applied`, тот же computation ID в Bill и frozen snapshot `new_bill`. В БД записаны `sent`
и message ID. Известны request checksum/input binding; completed request bytes уже не retained.
Содержимое Telegram независимо не запрашивалось. Этот цикл подтверждает данный участок истории,
а не file extraction, legal accuracy или остальные source families/purposes.

Computation/request doctors: **0 errors / 0 warnings**. Work doctor: **3 warnings**
`document_publication_unavailable` для старых `amendments/133`, `/139`, `/140`; они совпали
с предыдущим pin 9e836a9. Эти work rows не удалялись и остаются в dependency audit.

## Следующие действия

Продолжать совместимые source observations/extraction manifests, result refs и content versions
на SQLite, сохраняя неизвестные legacy связи. Дополнительная докачка нужна адресно после
разбора 31 физических SHA. Для будущего raw-профиля сначала нужен наблюдаемый поток чтений:
bytes/unique SHA, фактически использованные материалы, gaps и зависимости backup; затем бюджет
и сроки. Сценарии 30/90/365 дней пока не являются принятой raw retention policy.

Blob store/dual-store/remote backend/archive modes и их backup/GC условны при таком профиле.
Они не включены этим измерением. Обязательные payload текущей SQLite продолжают защищаться;
для уменьшения повторного JSON нужны совместимые refs и переход consumers, а не ранняя очистка.
Полные checks, hashes, scope и reproduction — в [J094](../plans/2026-10-22-document-architecture-progress.md#j094).


## Наблюдение будущих чтений

[J097](../plans/2026-10-22-document-architecture-progress.md#j097) добавляет optional
`RunReport.document_reads` для новых pipeline runs. Shared TextLoader считает downloader attempts,
завершённые response bodies и их bytes по host, over-limit/failed attempts и read-cache hits.
Отдельно сохраняются количество и bytes distinct completed bodies внутри одного run: SHA нужны
только временному счётчику, в report нет URLs, file bodies или individual hashes. Derived scan
windows не считаются новым скачиванием; самостоятельное повторное чтение PDF считается.

Это bytes, возвращённые downloader, без HTTP/TLS overhead и без неизвестной части прерванных
или отказанных ответов. Успешный download не доказывает пригодность документа для extraction
или анализа. Direct gateway downloads вне TextLoader и отдельные CLI invocations вне pipeline
не включены. Сумма run-distinct bytes не является distinct archive size за несколько дней.
Старые reports остаются unknown; новые quiet runs имеют наблюдаемый ноль. Counter reset не
очищает cache и не меняет selection, budget, retry или provider requests.

Метрики попадают в existing `runs.report_json` перед finish-run checkpoint и живут по retention
runs, без новой SQL schema, raw archive или backend. Dry run возвращает измерение и откатывает
DB. Исторический raw demand остаётся неизвестным, а production daily profile можно будет
построить после естественных runs; вручную production ради измерения не запускался.


## Metadata capture после J100

[J100](../plans/2026-10-22-document-architecture-progress.md#j100) сохраняет individual SHA/size
в `artifacts`, отдельно от aggregate run report. Это метаданные известного полного body, без body,
locator или гарантии retention. Source refs связывают точный outer body с immutable part slot;
member SHA не подставляется вместо container. Известные SHA/size остаются в RAM до закрытия loader,
в том числе при reset счётчиков/dry-run/cache reuse. First recorded_at относится к записи metadata,
а не к первому скачиванию файла в истории. Повтор не меняет первое время и не копирует содержимое.

Text prefilter сохраняет metadata/refs с решением и status; прочие известные bodies — при обычном
pipeline finish-run checkpoint. Failed/over-limit download не получает guessed bytes; completed
нечитаемый body может иметь physical metadata без нормативного verdict. Capture не объявлен
crash-complete до final checkpoint; standalone analysis/review CLI и остальные observation
consumers остаются открыты. Архив raw/backend не выбирается, старые размеры не backfilled.

## Обновление диагностики J101

Read-only tools различают per-run demand и каталог известных complete-body identities.
Каталог показывает global unique bytes и UTC день первой записи, без обещания retained files
или реконструкции даты скачивания. В exact natural state `dc97f63814eaa967287e7f8c834798d86b88888e`
(schema 52, без миграции) artifact tables ещё отсутствуют: это unavailable, не ноль.
Один новый run report содержит измеренный нулевой поток; 379 старых reports остаются unknown,
одна run row ещё без готового report. Это наблюдение quiet run, не оценка обычной нагрузки.
Source history содержит две selections/observations без ошибок. Подробности и acceptance —
[J101](../plans/2026-10-22-document-architecture-progress.md#j101).


## URL/body receipts и их проверка

[J106–J108](../plans/2026-10-22-document-architecture-progress.md#j106) добавляют отдельные
metadata receipts requested URL / complete-body SHA / size. Caller source transactions и
finish-run сохраняют известные complete responses, в том числе до отказа parser. Failed/over-limit
ответы не получают receipt. Это не locator retained bytes и не effective redirect URL;
first recorded_at не заменяет дату download или источника. Standalone reads вне этих caller
boundaries и crash до записи остаются за границами capture.

`tools.source_history_doctor` проверяет immutable identity, parent artifact и размер;
`tools.document_read_profile` выводит отдельный `artifact_read_receipts` inventory: число receipts,
unique requested URLs, unique bodies/bytes и UTC день первой записи. Aliases не удваивают bytes.
Отсутствующая старая таблица отличается от known-empty. Ошибочные rows исключаются из оценок,
source URLs/body не выводятся; обе команды только читают input snapshot. Исторический поток
скачиваний из receipt inventory не реконструируется.


## Естественное восстановление schema 55 · 28 октября

[J116](../plans/2026-10-22-document-architecture-progress.md#j116) проверяет новый state pin
`4f19d9bf7b8035f2239dece6098c4b06942f9414`, 28.10.2026 00:43:55 UTC:
**20316258 SQL bytes / schema55 / 462 Bill reads**, isolated restore/dump exact. Естественный
run383 был delivery drain: `document_reads=null`, без model calls/errors. Это подтверждает
сохранение/чтение новой схемы, но не измеряет файловый поток.

Все пять doctors: errors0. Прежние work warnings `document_publication_unavailable` остаются3;
пустая editorial history не подменяется реконструкцией. В saved reports измерен только один
прежний pipeline run; unmeasured history не считается нулём. Архив/retention/backend решение
не меняется. [Evidence](../assets/audits/2026-10-28-natural-schema-55/validation.json).


## Применимость задач DA2 · J123, 28 октября

Текущий профиль выше оставляет пять целых задач неприменимыми: DA2.2 blob store, DA2.6
протокол двух stores, DA2.7 выбор backend, DA2.8 archive modes и DA2.9 blob manifests/backup/GC.
Это явные conditional exclusions, без заявления о реализации и без нового решения о backend.
Если профиль будет расширен, эти задачи открываются перед соответствующим producer.

В DA2.4 условна запись raw bodies; attempt/error metadata остаётся применимым. DA2.1/DA2.3/
DA2.5 — измерения и совместимые source/artifact/evidence metadata — продолжаются. Сохранность
SQL, frozen pending inputs, результаты и зависимости retention остаются в DA5/DA6/DA8/DA10.
Исключение blob store не разрешает удалять защищённые payload либо выдавать URL/SHA за архив.
Дальнейшие изменения состояния по-прежнему требуют измерения dump/history growth.


## Дополнительный естественный срез 28 октября

Pin `e15b53d07ac1d20fccce9d5d931d3c89d880e4ca` проверен на отдельных копиях:
SQL 20 378 124 B, gzip 9 / mtime 0 — 4 112 280 B, SQLite 21 663 744 B; схема 59.
Все 38 таблиц и идентификаторы сохранились; повторный dump совпал побайтно. Production state не изменялся.

Из 389 строк доступны 388 сохранённых отчётов: 4 имеют v1 read metrics, 384 остаются неизмеренными,
1 строка без payload. Два ненулевых естественных отчёта run/collect содержат 21 completed
response / 15 050 702 B и 12 cache hits. CLI read reports на этом pin не наблюдались.
Это ограниченное окно записанных отчётов, не полный суточный поток или точное время requests.
Known catalogue bodies не означают сохранённые raw bytes. Backend/retention expansion остаются
отложенными; production overrides не читались, prune не запускался.
[Проверяемые агрегаты и SHA](../assets/audits/2026-10-28-current-storage/validation.json).


<a id="current-profile-assessment"></a>

## Итоговая оценка текущего профиля · 28 октября

Принимается текущий SQLite-профиль метаданных/решений и обязательных pending/frozen payloads.
Расширение raw retention и выбор backend остаются отложенными. Это конечная оценка DA2.1
по существующим pins; она не требует восстанавливать неизвестные исторические скачивания.
[Evidence и reproduction](../assets/audits/2026-10-28-storage-assessment/validation.json)
связывают J094 inventory/state schema51, свежий state e15b53d0/schema59 и код 9a37/schema60.
Исторический inventory повторно не скачивался и не хешировался целиком; проверены его pinned
SHA и исходные measurement artifacts. Disposable DBs удалены; source/inputs не изменены.

### Источники и границы форматных страт

| Source inventory | Paths / bytes | ZIP suffix paths / bytes | Word suffix paths / bytes | Scans |
| --- | ---: | ---: | ---: | --- |
| Sejm prints/filings/self-amendments/RPW | 4098 / 6 507 401 628 | 0 / 0 | 1678 / 176 829 637 | Unknown |
| RCL | 10 418 / 5 085 878 840 | 1803 / 2 893 623 292 | 3447 / 529 242 240 | Unknown |
| Catalogued Senate originals | 1825 / 479 946 679 | 0 / 0 | 1009 / 46 680 563 | Unknown |
| ELI | Вне raw cohort | Unknown | Unknown | Unknown |

Word здесь — только suffix `.doc/.docx/.docm/.xml`, без подтверждения формата или legal role.
PDF suffix counts: 2419 Sejm / 5168 RCL / 816 Senate; PDF не доказывает scan. Есть один Sejm ODT.
ELI JSON, research/derived/cache copies и uncatalogued Senate snapshots исключены. Нулевой
ZIP count относится только к этому inventory, а не к потреблению источника вообще.
Сохранённые extraction summaries содержат 15 Word / 6 PDF / 1 DOC selected parts; это другой
знаменатель, который не заполняет неизвестные scan/source cells. Полные stock/reference totals
и unmatched SHA приведены выше; для scan gaps используется corrected J094 qualified artifact.

В свежем state 477 Bill rows: current analysis flags 77 relevant / 93 irrelevant / 307 absent.
Есть 10 selection decisions, 49 observed-part и 22 selected-part entries с возможным overlap.
173 SQL sent publications относятся к 49 owners; независимая проверка Telegram не выполнялась.
Это разные множества. Исторические связи raw → exact paid input → relevant → publication
не восстанавливаются из этих чисел. Natural window содержит 21 completions / 15 050 702 B:
Sejm API 7 / 1 220 178 B, RCL 14 / 13 830 524 B; 12 cache hits. Within-run duplicate bytes 0,
cross-run union unknown. Start-date buckets 27/28 октября неполны: часть reports unmeasured;
они не являются полными сутками притока raw.

### Четыре профиля на известных данных

| Профиль | Измеренная основа | Неизвестное расширение |
| --- | --- | --- |
| Принятые metadata/decisions + обязательные payloads | Свежий SQL 20 378 124 B / gzip9 4 112 280 B / SQLite 21 663 744 B | Дополнительного raw store нет |
| Тексты и exact inputs | Уже retained JSON: delivery 9 448 739 B, review work 3 922 226 B, memo 944 003 B, batch meta 233 718 B | Дополнительные уникальные extracts/inputs для candidate/used/relevant/published множеств |
| Выборочные оригиналы | Доступная addressable research subset: 13 SHA / 9 585 013 B | Policy-selected union, его связь с публикациями/важностью и сроками |
| Полный raw по политике | Известны transfers в наблюдаемом окне и research stock, приведённые выше | Полный live unique inflow, derivatives и union с backup pins |

JSON columns пересекаются по смыслу; их сумма не является incremental archive bytes.
Corpus stock, run-distinct counters и число публикаций не дают стоимости raw retention
30/90/365 дней. First-recorded dates не заменяют download/source dates. Для невыбранных
профилей отсутствующие cells остаются unknown; решение о backend повторно не запрашивается.

### Retention sensitivities на имеющихся строках

На копиях state existing guarded pruning проверен на момент commit e15b53d0. После миграции
в schema60 исходные 38 таблиц/values точны; добавлена только пустая letter-use таблица.
Baseline до cleanup: 20 378 958 SQL / 4 112 546 gzip9 B. Политика production не менялась.

| Возраст reports / sent payload / memo, дней | Runs removed | Sent payloads cleared | Memos removed | Остаток SQL / gzip9 B |
| --- | ---: | ---: | ---: | ---: |
| Без age expiry | 0 | 0 | 0 | 20 377 395 / 4 111 659 |
| 30 / 30 / 30 | 169 | 10 | 23 | 19 852 496 / 4 056 246 |
| 90 / 90 / 90 | 0 | 0 | 0 | 20 377 395 / 4 111 659 |
| 365 / 365 / 365 | 0 | 0 | 0 | 20 377 395 / 4 111 659 |
| Code defaults 90 / 90 / 180 | 0 | 0 | 0 | 20 377 395 / 4 111 659 |

В каждом варианте отдельно очищен один eligible consumed/accounted/provider-forgotten batch
payload независимо от возраста. Идентичности publications, unsent payloads, work/accounting,
source/artifact rows и discovery watermark точны; FK/integrity passed. SQLite без VACUUM остаётся
21 676 032 B. Pending/unknown, ready/unconsumed и dependency guards сохранены; queued payloads
удерживают 3 008 025 B. Production overrides из secrets не читались. Уже expired данные эти
сценарии не восстанавливают и будущий рост не прогнозируют.

### Backup, checkpoint и временное сосуществование

Исторические state/pack/restore/checkpoint измерения и ограничения local Git приведены выше.
Они не доказывают размер самостоятельного backup всей истории или GitHub latency. Для свежего
pin local-only restore/dump: 0,331/0,102 s; 4 checkpoints / 3 commits, synthetic +1726 SQL / +102 gzip B;
changed checkpoints 0,929–1,218 s, unchanged 0,228 s. Повторный checkpoint здесь не запускался.

Inactive J140 reference/archive coexistence: 19 975 346 → 20 516 240 SQL B,
4 049 075 → 4 170 910 gzip9 B; worst qualified quiet/selection growth 3,7363% относительно
исходного baseline, sampled two-copy SQLite/WAL peak 93 461 088 B. Это историческая
counterfactual qualification, без активации и общего допуска на любой dual write. J142
385-slot diagnostics coexistence даёт +1963–135 317 gzip B в зависимости от failure scenario.
Каждая следующая migration требует собственного peak/steady gate. Raw backup pin lifetimes
не выбраны; они не реконструируются из времени записи metadata.

Текущий профиль измерен с явными strata, альтернативами, retention sensitivities и пределами
экстраполяции. Дополнительные raw-гарантии остаются вне принятого профиля. Неизвестный
исторический поток не объявляется нулём и не удерживает эту конечную оценку бессрочно открытой.
