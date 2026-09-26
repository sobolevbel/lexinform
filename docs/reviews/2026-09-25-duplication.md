# Аудит дублирования кода

Дата: 25 сентября 2026. Статус: исторический аудит исходников;
исправления выполнены локально 26 сентября 2026, результаты — в
[плане реализации](../plans/2026-09-25-deduplication.md#результат-реализации).
Проверенная версия: `ff04d9f81af2caaeb262f9e00f940f72f325caa7`, рабочее дерево до аудита чистое.

Найдено **8 групп повторений, для которых оправдано сокращение общей логики**.
Главная проблема — несколько реализаций одного правила, а не полностью одинаковые классы.
Самые существенные группы: фиксация результата доставки, наследование карточки,
сборка LLM-результатов и поиск связанного RCL-проекта.
Это технический долг: сам факт повторения не доказывает ошибку в production.
Новые подтверждённые функциональные дефекты этим аудитом не установлены;
поэтому записи `B…` в реестр дефектов не добавлены.
При последующей реализации отдельная регрессия обнаружила повторную отправку
неопределённого дайджеста: [B55](../BUGS.md#b55), исправлен отдельным коммитом.

[План исправлений](../plans/2026-09-25-deduplication.md) задаёт порядок, границы и проверки.

## Метод и охват

- Структурный проход по 232 Python-файлам: 95 в `src`, 25 в `web/src`,
  2 в `tools`, 108 в `tests`, 2 в `web/tests`.
- Сравнение AST функций длиной от 9 строк: 1832 функции, включая тестовые.
  Дополнительно сравнивались последовательности строк, чтобы заметить повторение
  внутри больших методов и строковых шаблонов.
- Ручное чтение найденных пар, их вызовов и существующих тестов: адаптеры LLM/HTTP,
  discovery/lookup, linking, publishing/posting/digest, модели usage, SQLite,
  форматирование сообщений и напоминания.
- Обзор web-моделей и настроек, общей базы publisher, CI и тестовых помощников.
  Миграции, `.pyi`, записанные ответы API, сгенерированные файлы и исторические
  HTML-прототипы не рассматриваются как кандидаты на механическое удаление повторов.

Оценки сходства использовались только для поиска кандидатов. Это не процент
дублирования проекта: короткие обёртки, конструкторы и объявления портов дают
много ложных совпадений. Ручное чтение охватывает кандидатов, а не каждую строку проекта.
Production state и внешние API для этого аудита не требуются и не проверялись.

## Что стоит исправить

Приоритет ниже означает порядок инженерных работ, а не тяжесть подтверждённого бага.
Риск — вероятность изменить поведение при объединении.

| ID | Повторение | Приоритет | Риск изменения |
| --- | --- | --- | --- |
| D1 | Результат отправки и учёт попыток в трёх сервисах | Высокий | Высокий |
| D2 | Наследование Telegram-карточки в трёх местах | Высокий | Средний |
| D3 | Сборка записей LLM и перенос usage | Высокий | Средний |
| D4 | Резерв стоимости batch в двух адаптерах | Средний | Низкий |
| D5 | Чтение потока с ограничением размера | Средний | Низкий |
| D6 | Поиск продолжаемого RCL-проекта | Высокий | Средний |
| D7 | Повтор уже существующего `fetch_print` | Средний | Низкий |
| D8 | HTTP retry-цикл и backoff | Ниже остальных | Высокий |

### D1. Три реализации фиксации результата отправки

Места: [PublishingService._send, 442–467](https://github.com/sobolevbel/lexinform/blob/ff04d9f81af2caaeb262f9e00f940f72f325caa7/src/lexinform/services/publishing.py#L442),
[Poster._send, 426–459](https://github.com/sobolevbel/lexinform/blob/ff04d9f81af2caaeb262f9e00f940f72f325caa7/src/lexinform/services/tracking/posting.py#L426),
[DigestService._send, 319–348](https://github.com/sobolevbel/lexinform/blob/ff04d9f81af2caaeb262f9e00f940f72f325caa7/src/lexinform/services/digest.py#L319).

Везде повторяются вызов отправки, `ServiceUnavailableError → failed` без расхода
попытки с повторным выбрасыванием исключения, прочая ошибка → `failed` с попыткой,
успех → `sent` с ID и временем. При изменении классификации ошибок или учёта
попыток нужно синхронно менять три реализации.

Стоит выделить сервис выполнения уже подготовленной отправки и регистрации её
исхода. Оставить у вызывающих сервисов решение, что и когда отправлять.
Различия существенны: publishing сохраняет `document_message_ids`, Poster
атомарно освобождает только `held_change_ids` плана, digest создаёт свою pending-запись.
Общий код обязан сохранить эти различия, порядок `pending → сеть → результат`,
защиту `unknown` и сохранённый `reply_to`. Сетевой вызов не помещать в транзакцию.

### D2. Три копии наследования карточки

Места: [Linker._inherit_card, 161–181](https://github.com/sobolevbel/lexinform/blob/ff04d9f81af2caaeb262f9e00f940f72f325caa7/src/lexinform/services/tracking/linking.py#L161),
[WykazLinker._inherit_card, 133–150](https://github.com/sobolevbel/lexinform/blob/ff04d9f81af2caaeb262f9e00f940f72f325caa7/src/lexinform/services/tracking/wykaz.py#L133),
[publishing.py, блок 400–414](https://github.com/sobolevbel/lexinform/blob/ff04d9f81af2caaeb262f9e00f940f72f325caa7/src/lexinform/services/publishing.py#L400).

Повторяется создание `NEW_BILL/SENT` для нового номера с `message_id` и `sent_at`
старой карточки, затем `mark_publication`. Это один инвариант продолжения треда,
который сейчас обслуживают три места.

Выделить операцию создания alias-карточки с явными целевыми term/number, каналом,
исходной публикацией и временем; возвращать сохранённую `Publication`.
Не объединять Linker и WykazLinker целиком: различаются загрузка источника,
повторный анализ, наследование статуса, baseline и порядок перерисовки.
Не удалять автоматически второй SQL-вызов: сначала проверить поведение
`create_publication` при существующей записи.

### D3. Сборка LLM-record повторяется между провайдерами и batch

Места: [AnthropicAnalyzer, 106–303](https://github.com/sobolevbel/lexinform/blob/ff04d9f81af2caaeb262f9e00f940f72f325caa7/src/lexinform/adapters/llm_anthropic.py#L106),
[OpenAiAnalyzer, 186–311](https://github.com/sobolevbel/lexinform/blob/ff04d9f81af2caaeb262f9e00f940f72f325caa7/src/lexinform/adapters/llm_openai.py#L186),
[AnalysisService._consume_digest/_consume_current, 727–841](https://github.com/sobolevbel/lexinform/blob/ff04d9f81af2caaeb262f9e00f940f72f325caa7/src/lexinform/services/analysis.py#L727).

Четыре вида результата в двух адаптерах повторяют перенос model, prompt_version,
времени, usage и полей контекста. Batch восстанавливает те же виды записей из
сохранённых метаданных. В Anthropic дополнительно повторяется чтение четырёх
счётчиков usage и проверка типа structured output.

Выделить типизированные чистые фабрики записей и небольшое представление usage.
Входы фабрик — ответ, метаданные вызова и уже нормализованные токены; источник
метаданных остаётся у вызывающего кода. Провайдерские SDK, промпты, retry и parsing
не переносить в универсальный базовый Analyzer.

Особенно важно сохранить: OpenAI `_usage_of` уже вычитает cached из total input;
повторно вычитать нельзя. Batch должен брать версию промпта из сохранённого
запроса, не из текущей константы. Persistence, generation-check и стоимость
batch остаются в сервисе; фабрика ничего не пишет в БД.

### D4. Две копии оценки batch-резерва

Места: [llm_anthropic.py, 333–346](https://github.com/sobolevbel/lexinform/blob/ff04d9f81af2caaeb262f9e00f940f72f325caa7/src/lexinform/adapters/llm_anthropic.py#L333),
[llm_openai.py, 377–390](https://github.com/sobolevbel/lexinform/blob/ff04d9f81af2caaeb262f9e00f940f72f325caa7/src/lexinform/adapters/llm_openai.py#L377).

Одинаковы `max(tokens or 0, len(prompt) + len(system) + 2_000)`, добавление
стоимости страниц скана и вызов `batch_reservation`. Изменение запаса или правила
для сканов требует двух правок, хотя сама политика провайдеронезависима.

Вынести только расчёт резерва в `pricing.py` с явными аргументами.
Сериализация payload и получение tokenizer count остаются в адаптерах.
Уже подготовленный `payload_json` должен по-прежнему возвращаться без пересчёта.

### D5. Три копии ограниченного чтения HTTP-потока

Места: [SejmApiClient.download, 252–265](https://github.com/sobolevbel/lexinform/blob/ff04d9f81af2caaeb262f9e00f940f72f325caa7/src/lexinform/adapters/sejm_api.py#L252),
[RclClient.download, 139–155](https://github.com/sobolevbel/lexinform/blob/ff04d9f81af2caaeb262f9e00f940f72f325caa7/src/lexinform/adapters/rcl_html.py#L139),
[OrkaClient._body, 103–112](https://github.com/sobolevbel/lexinform/blob/ff04d9f81af2caaeb262f9e00f940f72f325caa7/src/lexinform/adapters/orka.py#L103).

Во всех трёх — chunks, счётчик байтов, проверка `received > max_bytes`,
`AttachmentTooLargeError` и join. Это хороший небольшой первый рефакторинг.
Вынести helper чтения итератора байтов. Владение response и `finally: close()`
оставить явным у адаптера; проверки WAF у RCL и Orka тоже сохранить отдельно.
Не смешивать эту работу с переделкой retry.

### D6. Discovery и lookup независимо ищут RCL-предшественника

Места: [DiscoveryService._rcl_project_of, 114–134](https://github.com/sobolevbel/lexinform/blob/ff04d9f81af2caaeb262f9e00f940f72f325caa7/src/lexinform/services/discovery.py#L114),
[BillLookup._project_of, 273–293](https://github.com/sobolevbel/lexinform/blob/ff04d9f81af2caaeb262f9e00f940f72f325caa7/src/lexinform/services/lookup.py#L273).

Одинаковая цепочка: RM number → локальная запись → resolve_project_id →
find_rcl → исключение NOT_FOLLOWED. Уже есть расхождение: discovery дополнительно
проверяет `bill.rcl is not None`, lookup — нет; также различается логирование outage.
Это наблюдаемая разница проверок, но достижимый пользовательский сбой не доказан.
Уточнение при реализации: вызывающий метод `BillLookup._fetch` тоже проверял
`project.rcl`, поэтому перенос этой проверки в resolver не меняет результат lookup.

Выделить resolver в сервисном слое с общим поиском и явным контрактом результата.
До объединения зафиксировать поведение для отсутствующей RCL-модели и пропущенных
статусов. Сохранить best-effort поиск: недоступность RCL не должна мешать загрузить
сам Sejm print. Не смешивать поиск предшественника с изменением связей в БД.

### D7. `_safe_print` повторяет готовый helper

Места: [PublishingService._safe_print, 504–512](https://github.com/sobolevbel/lexinform/blob/ff04d9f81af2caaeb262f9e00f940f72f325caa7/src/lexinform/services/publishing.py#L504),
[sources.fetch_print, 30–38](https://github.com/sobolevbel/lexinform/blob/ff04d9f81af2caaeb262f9e00f940f72f325caa7/src/lexinform/services/sources.py#L30).

Совпадает контракт: get_print, outage пробрасывается, прочая ошибка логируется
и даёт None. Различается текст предупреждения. `fetch_print` уже используется
в tracking, linking, cards и SejmTextSource; новую абстракцию вводить не нужно.
Перевести publishing на него, сохранив проверку `has_process` там, где она есть.
Поведение при outage особенно важно: в `test_outages.py` уже описана регрессия
старого `_safe_print`, который поглощал outage.

### D8. Retry-циклы HTTP похожи, но объединять их нужно ограниченно

Места: [sejm_api.py, 273–316](https://github.com/sobolevbel/lexinform/blob/ff04d9f81af2caaeb262f9e00f940f72f325caa7/src/lexinform/adapters/sejm_api.py#L273),
[rcl_html.py, 169–226](https://github.com/sobolevbel/lexinform/blob/ff04d9f81af2caaeb262f9e00f940f72f325caa7/src/lexinform/adapters/rcl_html.py#L169),
[senat_html.py, 205–237](https://github.com/sobolevbel/lexinform/blob/ff04d9f81af2caaeb262f9e00f940f72f325caa7/src/lexinform/adapters/senat_html.py#L205),
[wykaz_csv.py, 218–246](https://github.com/sobolevbel/lexinform/blob/ff04d9f81af2caaeb262f9e00f940f72f325caa7/src/lexinform/adapters/wykaz_csv.py#L218).

Повторяется счётчик попыток, retry transport/429/5xx, backoff и сборка ошибки.
Стоит сократить механизм повторных попыток после стабилизации контрактных тестов.
Однако единый «умный HTTP-клиент» с множеством флагов будет хуже текущего кода.

RCL имеет короткий probe и память о недоступности; Sejm и RCL отличают локальные
ошибки от outage; Senate допускает отсутствие страницы; Orka отдельно распознаёт
challenge. Оставить классификацию ответов и времени ожидания у адаптеров.
Если небольшой исполнитель retry не получается без сложного callback API,
ограничиться общей политикой backoff и контрактными тестами, а циклы сохранить.

## Повторы, которые сейчас не стоит устранять

| Область | Решение и причина |
| --- | --- |
| Claude/GPT templates в `llm_prompts.py:23–241` | Намеренно независимые шаблоны после калибровки. Отличаются правила gate и языка; это прямо объяснено в исходнике. Не сводить их к одному промпту ради размера файла. Общие фрагменты возможны только с побайтовым сохранением конечных строк и сохранением независимой настройки. |
| JSON schemas OpenAI и Pydantic | Дублирование схем осознанно; есть `test_the_hand_written_schema_matches_the_pydantic_model`. Новый универсальный converter ради четырёх небольших схем сейчас не оправдан. |
| Поля usage в пяти Record-моделях | Сходны, но модели имеют разные обязательные поля и defaults. D3 сокращает сборку данных без миграции JSON и иерархии наследования моделей. |
| DeadlineReminder/HearingReminder/ConsultationReminder | Похожий обход, но разные окна, наличие карточки, ref и подавление совместных публикаций. Конструкторы не основание для общего базового класса. |
| `list_*` в SQLite | Обвязка похожа, SQL-предикаты различаются по смыслу. Generic query builder скроет бизнес-условия; deployed migrations неизменяемы. |
| RenderingPublisher, порты, HybridAnalyzer | Методы уже делегируют в общие реализации; типизированный явный интерфейс полезнее динамической таблицы методов. |
| Telegram formatter | Есть сходные блоки сообщений, но `_assemble`, `_steps_block`, `_links`, `_tags` уже общие. Объединение целых карточек и напоминаний ухудшит ясность. |
| Тесты | `World`, fakes и builders уже общие; сценарии failure/restart проверяют разные инварианты. Повтор arrange не основание удалять регрессии. Реальный gateway и fake намеренно независимы и проверены общим контрактом. |
| Web | В просмотренных моделях и настройках полезных кандидатов не найдено. Раздельные local/test/production settings и миграции оправданы. |

## Ограничения вывода

Аудит подтверждает наличие перечисленных повторов и различий в исходниках.
Он не доказывает, что предложенное объединение уже безопасно: для этого нужны
проверки из плана. Не оценивались частота production-ошибок, экономия токенов
или процент сокращения строк. Удаление повторов само по себе не уменьшит число
LLM-запросов и не должно менять поведение продукта.

## Проверка материалов аудита

`make docs-check` прошёл: строгая сборка, локальные ссылки, якоря, ресурсы и
состав портала. Визуальная проверка в браузере не выполнялась.
Полный офлайн pytest прошёл без assertion failures; 21 setup error в CLI был
вызван запретом sandbox на bind `127.0.0.1`. Повтор всего `test_cli.py` с доступом
к локальному серверу прошёл. Mypy и Ruff прошли, formatter оставил 203 файла
без изменений; `git diff --check` чистый. Исполняемый код и тесты не менялись.
