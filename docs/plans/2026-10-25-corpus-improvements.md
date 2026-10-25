# Улучшение корпуса и исследовательских инструментов

Открыт 25 октября 2026. База `c8f1d83`. Статус: реализация.
Основание — [обзор корпуса](../reviews/2026-10-25-corpus-workflow.md).

Цель: воспроизводимые измерения, общие безопасные примитивы scripts, восстановление отсутствующих
материалов и адресуемая разметка без перезаписи исторических оригиналов и benchmark inputs.
Backend хранения не выбирается. Production state, Telegram и платные модели не используются.

## Checklist

- [x] C1. Общий код guards/provenance/artifacts и исторических RCL cases; старые imports/CLI
  совместимы. Сверить прежний health report и regression slices на неизменном корпусе.
- [ ] C2. Постоянный run manifest: команды, версии/опции, status, входы/SHA, артефакты/SHA,
  exclusions, обнаружение missing artifacts. Единый diff решений поверх существующих suites;
  отсутствие обязательных inputs не считается успехом.
- [ ] C3. Переизвлечение 118 empty ZIP и 59 PDF в отдельную generation, исходный кеш сохранён.
  Сохранить raw/extraction hashes, limits/ошибки/роли; recovered text не объявлять юридически полным.
- [ ] C4. Адресная докачка семи отсутствующих RCL originals и повтор трёх consultation refusals.
  Проверить формат/размер/HTML, no overwrite, provenance и повторные решения. Ошибки источника
  сохраняются как недоступность; отсутствие после отказа не заполняется выдуманными данными.
- [ ] C5. Датированный snapshot Sejm metadata/RPW/supplements, вычисленный diff к прошлому снимку.
  Докачка по required cases, без массового переноса каталогов или стирания старых listings.
- [ ] C6. Очередь relation/span annotation и локальный просмотр источников/decision diff.
  Oracle/basis/read scope явные; граф инициатив удерживает связанные редакции в одном split.
  Machine labels и уже использованный holdout остаются regression, новая независимая разметка
  требует реального прочтения исходников.
- [ ] C7. Подключение suites, preflight и health других families к общему запуску; дорогие
  extraction кешируются только по точным raw/options/version identities.

## Правила приёмки

Finished parts коммитятся отдельно. Общие helpers не меняют selection/text policies.
Для corpus runs фиксируются исходные SHA и проверяются неизменные входы/код. Сохранённый JSON
не заменяется. Производные поколения и source observations отделены от original download paths.
Новые labels имеют evidence и происхождение; неизвестная роль или связь сохраняет unknown.
При изменении extraction/trim/comparison нужен полноценный профильный corpus gate.

## Журнал

### C001. Общие примитивы исследовательских scripts

Статус: принят локально. `corpus_runtime`, `corpus_artifacts`, `corpus_rcl` объединяют guards,
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
Статусы остальных частей обновляются после их отдельных implementation commits.
