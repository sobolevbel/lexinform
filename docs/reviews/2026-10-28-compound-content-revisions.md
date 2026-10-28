# Исправления shared, multipart и digest сообщений

Статус: архитектурный контракт DA7.4 · 28 октября 2026. Реализация новых типов исправлений
не включена; текущие ограничения `/amendments` сохраняются. Исходная реализация:
[J109–J112](../plans/2026-10-22-document-architecture-progress.md#j109).

## Единица исправления

Исправляется сохранённая редакция физического сообщения либо набора его частей. Bill и событие
остаются источниками содержания; они не становятся владельцем общего edit target. У группы
один coordination key, даже если команды пришли для разных законопроектов. Original content,
новая редакция и physical attempts имеют отдельные identities.

| Случай | Владелец | Обязательный frozen baseline | Допустимость |
| --- | --- | --- | --- |
| Одиночный update | Original publication ID | Bill/change/date, content version, target message | Уже поддерживается |
| Shared update | Physical shared message / ordered membership | Все historical member snapshots, их порядок и применённые corrections | До появления полного baseline — отказ |
| Multipart | Logical edition + ordered physical slots | Все старые parts, targets/parents и payloads | Число старых targets должно быть известно |
| Digest | Physical digest part + frozen event links | Approved rendered part, полный membership и source content refs | Нужен новый просмотр изменённой части оператором |

Недостающий historical HTML, member snapshot или edit target не восстанавливается из
сегодняшнего Bill, нового formatter или нового недельного digest. Официальный файл не доказывает,
каким было отправленное сообщение. Missing baseline — `unsupported`, не повод для paid backfill.

## Content и доставка

Новая редакция ссылается на expected previous content ID и общие immutable snapshots.
Она содержит полный итоговый logical content, renderer/split identity и ordered part map.
Каждый physical slot хранит exact text/hash и действие: edit существующего target либо append
нового continuation. Retry использует сохранённый slot; root message, parent и membership не
пересобираются. Unknown блокирует новые редакции этого physical owner.

Telegram не даёт атомарного изменения нескольких сообщений. После первого успешного edit
и до подтверждения всех slots читателю уже может быть видна смешанная редакция. Поэтому view
должен различать `confirmed`, `mixed`, `unknown` и `unsupported`. Он не показывает всю старую
или новую редакцию как подтверждённую, пока receipts не описывают каждый physical slot.
`dismissed` не означает завершённую новую редакцию.

`current` нового logical content меняется только после всех требуемых receipts/аудированных
confirm. Частичные receipts сохраняются отдельно и видны оператору. Удаление лишних старых
messages в этот контракт не входит: до отдельного решения о reader copy и recovery исправление,
которому нужно уменьшить число старых parts, получает `unsupported`. Прежний безопасный
single-original → long revision path сохраняет свой существующий контракт и не выдаётся за
поддержку произвольного multipart original.

## Shared updates и конкуренция

Исправление одного member использует остальные members из текущей подтверждённой frozen
редакции общей публикации. Оно сохраняет их события, даты и уже подтверждённые corrections.
Два исправления разных bills не могут владеть одним physical message одновременно.

До queue/save применяется compare-and-set expected previous content ID. Если за время batch
другая correction завершилась, полученный paid result сохраняется. Он может примениться только
через проверяемый patch к тому же member в новой полной baseline, сохраняя изменения остальных
members. При изменённом membership, отсутствующем source либо недоказуемом patch применение
останавливается; paid result не теряется, повтор оплаты автоматически не назначается.

Это координация существующих publication/batch/result owners, не новый универсальный scheduler.
Prepared request и comparison фиксируются до provider call; content edition и physical slots —
до первого reader API effect. Network остаётся вне SQL-транзакции, checkpoints — прежние CAS.

## Digest corrections

Исправление опубликованного digest part создаёт preview новой полной части и явный diff.
Оператор подтверждает exact content hash и expected previous version. Устаревшая кнопка не
публикует новую редакцию после смены baseline. Остальные parts недели и membership не меняются
как побочный эффект исправления одного bill.

Confirmed digest event links не освобождаются и не подтверждаются второй раз из-за edit.
Изменение состава digest, перемещение события между parts и замена уже утверждённой части
текущей реконструкцией недели в этот scope не входят. Для них нужен отдельный editorial contract.

## Recovery и проверка

| Сбой | Обязательное наблюдаемое состояние |
| --- | --- |
| До pre-effect checkpoint | API не вызван; новый content не confirmed |
| После checkpoint до API | Pending становится unknown; автоматического повтора нет |
| Первый edit принят, следующий не начат | Сохранён receipt первого slot; logical view mixed |
| Ответ edit потерян | Точный target/text сохранён; slot unknown, следующий не идёт |
| Добавленный continuation не подтверждён | Новая logical edition не confirmed; parent не отправляется снова |
| Два batch results к одному shared owner | Expected-base CAS не даёт затереть чужую correction |
| Confirm/dismiss части | Меняется только известный physical subject; dismissed не завершает edition |
| Очищен operational payload | История читается по immutable bindings; missing snapshots явно видны |

Приёмка требует restore только из последнего remote snapshot, потери локального runner,
workers1/4, независимых corrections двух members, stale preview approval и отсутствующего
baseline после 90/180 дней. Показания UI `mixed`/`unknown` проверяются отдельно от SQL identity.
Reader text, provider input и source provenance остальных members должны остаться exact.
Нужны whole-dump SQL/gzip измерения и natural production cycle затронутого family/purpose;
fixture-only gate не доказывает независимую legal accuracy или физическое содержимое Telegram.

## Самостоятельные выпуски

1. Добавить reader и диагностику logical edition/part receipts, включая mixed state; producer off.
2. Сохранять достаточные baseline/part bindings для новых shared/multipart сообщений без смены sender.
3. Перенести один fixed-membership shared correction через existing owner и expected-base CAS.
4. Отдельно принять multipart original; уменьшение числа parts пока остаётся unsupported.
5. Включить digest correction только с immutable preview/approval и неизменным membership.

Перед каждым producer выпуском rollback-reader обязан сохранить новые pending/unknown и part
receipts. Выключение флага не разрешает отправить их старым sender. Пока конкретный выпуск не
прошёл эти gates, прежний отказ остаётся продуктовым поведением; общая content schema сама по
себе не означает поддержку нового типа исправлений.
