# Допустимость документа зависит от вопроса

Контракт DA4.5/DA4.6, этапы [J089–J091](../plans/2026-10-22-document-architecture-progress.md#j089).
Состояние выбора файла и пригодность для конкретной операции отвечают на разные вопросы.
Отсутствие собственной пометы `Projekt z dnia` не запрещает чтение текста.

## Матрица потребителей

| Вопрос | Чистая политика и владелец вызова | Сохраняемый контракт |
| --- | --- | --- |
| Prefilter | `prefilter_admission`, `TextPrefilterService.load` | Waiting остаётся unanswered; ambiguous/unsupported сохраняют причину. Отсутствующее тело и тонкий текст различаются. Keywords получают только пригодный текст; доступные страницы проходят прежний scan path. |
| Основной анализ | `analysis_admission`, общий `_load_text` | Blocked selection вызывает прежнюю ошибку. Отсутствующее тело/тонкий PDF передаются scan loader. Непустой Word без страниц остаётся текстом. Неподходящий appendix даёт metadata fallback; OSR допускается. |
| Сравнение версий | Тот же `_load_text`, затем прежние `matches`/`diff_laws` | Допуск чтения не доказывает равенство редакций. Baseline должен совпасть с сохранённым fingerprint; материальные изменения проверяются отдельно. Ошибки источника не превращаются в отсутствие изменений. |
| Поправки | Committee path использует `_load_text(trim=False)`; Senate path — существующие pure validators `senate_documents` | Полнота и бюджет проверяются после чтения. Senate требует подтверждённые предмет/дату/исход/полный текст либо согласованный scan manifest. Общая bill policy не заменяет этот контракт. |
| Reviews | `review_admission`, `_read_review_document`, затем purpose-specific `review_context` | Несовпавший expected digest или роль исключает документ. Пригодный, но неполный текст остаётся partial. Commission review дополнительно требует полный документ роли комиссии; consultation review сохраняет прежнюю обработку partial. |

`bill-read-v1` и `review-read-v1` — версии допуска чтения. Они не являются номером юридической
редакции, версии анализа или свидетельством сохранённого оригинала. Полная запись решения
и `DocumentEdition` остаются отдельными задачами DA4.4/DA4.7.

## Граница ответственности

Политики получают immutable selection, текст и известные параметры файла. Они не скачивают
данные, не обращаются к repository, не выбирают модель и не публикуют сообщения. Loader
сохраняет прежнее владение I/O, cache, official-source checks и exceptions. Сервисы используют
решение для прежних counters/errors, scan/metadata fallback и сборки контекста.

Review `complete` сохраняет существующий флаг пригодности входа. Он не доказывает полноту
юридической редакции. В частности, перенос не вводит новое blanket-правило для `waiting`
или `unverified`; действующие условия rejected/oversize/density сохраняются буквально.
Saved review snapshots проходят прежний отдельный путь. Paused review purposes не включаются.

`text_digest` принадлежит чистому `document_fingerprints`: прежние whitespace/decorated-page
правила, prefix `text-v2:` и сохранение голых числовых строк неизменны. Старый import из
`services.analysis` остаётся совместимым alias. Expected digest review проверяет ту же identity,
которую затем записывает `ReviewDocument.normalized_sha256`.

## Приёмка

До подключения review consumer сохранён shadow run с исходными service class methods:
**14691 cached views / 396657 решений**, девять условных ролей × pages 0/1/20.
Каждое решение совпало с прежними ветками; каждый `text-v2` digest — с прежним алгоритмом.
Role/page probes не восстанавливают исторические metadata и не являются legal holdout.

Public `review_context` сравнивается до/после на **864** сочетаниях workers 1/4, двух включённых
purposes, девяти ролей, восьми body/page fixtures и трёх вариантов expected digest.
Сравниваются весь context, complete flags и source calls; подготовка не меняет repository
и не вызывает модель. Дополнительно используются прежние native pipeline profiles,
материальные fingerprint mutations, Senate replay и полный quality gate.
Итоговые результаты и исключения записываются в [J091](../plans/2026-10-22-document-architecture-progress.md#j091).
