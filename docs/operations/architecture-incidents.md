# Остановка при нарушении документного контракта

Статус: актуальная инструкция; исходники проверены 4 ноября 2026, production drill не выполнялся.

Эта инструкция применяется, когда обнаружено одно из семи нарушений DA10.8 ниже.
Сначала прекратите работу затронутого consumer, сохраните доказательства, затем исправляйте
причину и повторяйте затронутый replay. Обнаружение неверной редакции, даты или решения
может быть результатом проверки человеком; универсального автоматического legal detector нет.

## Как остановить

Общий поддерживаемый writer — job `run` workflow `daily.yml`; scheduled runs, inbox,
batch collection и delivery drain используют его `concurrency.group: lexinform-daily`.
Concurrency сериализует работу, но сама по себе не ставит её на паузу.
См. [обслуживание canonical state](state-maintenance.md).

Если подходящей scoped-паузы нет, используйте остановку общего writer:

1. В GitHub **Actions → Daily run → … → Disable workflow** запретите новые запуски.
2. Проверьте running и queued запуски этого workflow; отмените оставшиеся через **Cancel workflow**.
   Запишите URL/ID и дождитесь их завершения. Один остановленный run не блокирует следующий.
3. Проверьте итог каждого запуска и последний подтверждённый SHA ветки `state`.
   Финальный checkpoint имеет `always()` и может выполняться после ошибки; cancellation
   не доказывает ни успешное сохранение, ни отсутствие внешнего действия.
4. Пока общий writer остановлен, не запускайте его локально или manual dispatch и не подавайте
   новые операторские команды. Inbox и уже отправленные provider batches сохраните.

Relay может продолжать принимать команды в inbox, а provider — выполнять уже принятый batch.
Остановка workflow не отменяет эти действия и не отзывает Telegram request в полёте.
Проверяйте отдельно reader channel и provider batch ID; отсутствие локального подтверждения
не означает отсутствие эффекта. Сохранённые pending/unknown/submitting identities остаются.

Существующая scoped-пауза есть у consultation reviews:
`LEXINFORM_CONSULTATION_REVIEWS_ENABLED=false` удерживает новую работу и queued deliveries;
уже submitted batches всё равно собираются и учитываются. Это настройка следующего процесса,
не команда прерывания текущего run. Она применима, только если изолирована причина и проверена
фактическая конфигурация исполнителя; иначе остановите общий writer.
`act_application` уже отключён отдельно. Контракт и границы —
[разбор документов](../operator-commands.md#разбор-документов-и-физические-продолжения).
Аналогичная purpose-пауза `LEXINFORM_COMMISSION_REVIEWS_ENABLED=false` проверяется перед
новой commission-review работой и её queued delivery; collection submitted batches продолжается.
Используйте её только для изолированного commission review с проверенной конфигурацией.
Для остальных перечисленных здесь consumers используйте общую остановку.

## Условие, доказательства и повторная проверка

Во всех строках «общая остановка» означает процедуру выше, включая queued jobs.
Снимок доказательств берите после остановки; сохраните также доступные данные прерванного run.
Не меняйте исходную копию во время диагностики.

| Условие | Consumer и остановка | Что сохранить сверх общего пакета | Replay и критерий возобновления |
| --- | --- | --- | --- |
| Потеря обязательной части | Loader/selection и потребляющий analysis/review; общая остановка, либо проверенная consultation-пауза для изолированного review. | Descriptor/catalog, required/optional роли, доступные exact bytes/хеши, extraction/selection/input IDs и missing reason. Отсутствующий raw обозначить отсутствующим. | Повторить затронутый selection/composition corpus replay и unit regressions: без required part результат blocked; после восстановления выбрана нужная полная композиция. Проверить все изменившиеся решения и смысловые мутации. |
| Неверная редакция, дата или решение | Source/edition resolver, analysis/review или sender неверного content; общая остановка, либо применимая consultation-пауза. | Официальный источник и его дата, сопоставленные редакции, frozen comparison/input/content и фактически отправленный текст. Отделить предложение от принятого решения. | Повторить затронутый source/selection/review replay; проверить исправленный пример и прежние решения. Человек подтверждает нужную редакцию/дату/решение по источнику; retry сохраняет исходные факты, а исправление использует поддерживаемую correction operation. |
| Orphaned pending work | Владелец work/batch/projection; общая остановка, consultation-пауза только для изолированной соответствующей работы. | Work/owner/dependency IDs, generation, intent/custom/provider batch IDs, pending snapshot и результаты work/computation doctors. | На изолированной копии проверить ownership/dependencies и восстановление pending после dump/restore. У каждого затронутого work один реальный owner и доказанный дальнейший путь; unknown legacy не считать исправленным. |
| Несогласованные refs или remote tip | Metadata reader/writer, checkpoint writer; общая остановка. | Exact code/state/local prepared/remote SHAs, SQL и manifest, dangling/corrupt refs, checkpoint error и локальный rejected commit, если доступен. | Повторить профильный read-only doctor и unit CAS/restore regressions. Исправленные refs согласованы; writer исходит из актуального подтверждённого snapshot, чужой tip по-прежнему отвергается. Не force-push поверх чужой истории. |
| Обход checkpoint | Batch submit/collect или physical sender; общая остановка. | Intent/receipt, frozen payload/membership, prepared/remote SHA, границу внешнего вызова и checkpoint logs. | Повторить затронутый checkpoint/crash replay: до подтверждения intent нет внешнего вызова; ошибка receipt/checkpoint останавливает следующие effects. Уже начатый uncertain effect отдельно сверить до возобновления. |
| Повтор доставки | Physical sender/recovery; общая остановка. | Publication/physical-part/group IDs, frozen HTML и parent, attempt/confirmation/audit, все обнаруженные Telegram message IDs. | Повторить recovery/crash/restore replay для этого вида доставки. Повтор не создаёт второй эффект; unknown требует аудированного решения для конкретной physical part. Сверить канал и разрешить uncertain исходы до drain. |
| Двойное usage accounting | Batch collector и cost accounting; общая остановка. | Provider batch/custom ID, исходный usage, retained result/collection, accounted/consumed отметки и затронутые run reports. | Повторить collection/restore/accounting regressions: один provider item учитывается один раз, следующий run не начисляет его снова; memo reuse имеет нулевой новый usage. Сверить затронутые reports с исходными items, сохранив оригинальный paid usage. |

## Общий пакет доказательств

Запишите condition, время обнаружения, bill key/purpose, affected consumer, code SHA,
workflow URL/ID, исходный и итоговый state SHA, фактические allowlisted flags.
Сохраните checkpointed SQL вместе с polling manifest, доступные exact artifact objects,
структурированные IDs/logs, error и old/candidate outcomes. Не переносите секреты из `.env`.
Для refs без сохранённых bytes укажите реальную границу профиля: новый download не заменяет
исторический artifact. Инструменты и требования к копии —
[state retention](../state-retention.md) и
[каталог offline doctors/replay](https://github.com/sobolevbel/lexinform/blob/main/tools/README.md).

Doctors только сообщают факты. Не превращайте их output в список удалений или resubmit.
Отдельно зафиксируйте проверенный Telegram/provider outcome; успешный job или projection receipt
не доказывает физическую доставку. Не восстанавливайте старый dump поверх последующих
LLM/Telegram действий: [rollback contract](../plans/2026-10-22-document-architecture.md#103-переключения-и-rollback).

## Исправление и возобновление

1. На изолированной копии воспроизведите конкретное нарушение; закрепите regression,
   предпочтительно unit, если не нужна durable граница нескольких сервисов.
2. Исправьте причину, сохранив pending/unknown identities, original usage и immutable snapshots.
   Нужное изменение state выполняется только поддерживаемым отдельным maintenance contract;
   обычный replay не получает production write access.
3. Повторите затронутый replay из таблицы, совместимость dump/restore и необходимый quality gate.
   Для extraction/selection/comparison обязателен реальный corpus, включая changed decisions
   и meaningful mutations. Зафиксируйте coverage/exclusions; not observed не означает success.
4. Сверьте актуальный remote tip, сохранённые queues и внешние effects. Не передавайте attempted
   work старому sender; не используйте synchronous paid fallback как способ обойти ожидание.
5. Пока writer остановлен, подготовьте решения по uncertain effects без исполнения команд.
   Для unknown допустимы только аудированные операции
   [delivery recovery](../operator-commands.md#recovering-an-uncertain-delivery): confirm найденного сообщения,
   retry после проверки отсутствия либо dismiss с причиной. Для submitting batch после
   provider-проверки подготовьте применимую
   [batch recovery/attach операцию](../operator-commands.md#the-batch-poller-optional).
6. До включения проверьте весь runnable saved work и накопленный inbox: ни один готовый payload
   или команда не должен повторить incident. Unknown остаётся held без автоматического retry.
   Если recovery требует writer, а существующая scoped-пауза или квалифицированный исправленный
   guard не изолирует затронутый consumer, сохраните общую остановку до проверки безопасного пути.
   Само наличие подготовленного решения не разрешает включить небезопасный sender.
7. Владелец incident фиксирует evidence, исправление, replay, подготовленные решения и remaining
   unknowns. После выполнения критериев строки и проверки безопасного пути включите в GitHub
   **Enable workflow**. Затем исполняйте согласованные recovery-команды через поддерживаемого
   writer и проверяйте их outcomes, точный checkpoint и физические исходы первого run.
   Это обычный workflow с его действующей маршрутизацией, не изолированный recovery-only режим.

Процедура не подтверждает приёмку ещё не наблюдавшейся source family/purpose и не требует
снимать scoped-паузу review, если его source/model QA ещё не выполнен.
