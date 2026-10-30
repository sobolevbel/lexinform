DA6.3 ещё не закрыт: ссылки на вычисления уже есть, но secondary memo сохраняет весь ответ; `joint_json` и три текущих review-поля Bill тоже остаются полными. Name хранит только выбранное имя/основание с binding, supplements Bill — только номера; amendments живут в событиях, не в отдельном поле Bill.

Memo очищается после 180 дней лишь при выполнении защитных условий; это не гарантированный срок. Обычные отправленные delivery payload очищаются после 90 дней с исключениями. Amendment revisions, status changes и editorial snapshots остаются историей. Их замороженный контекст нельзя удалить как обычный cache. Review context не содержится целиком в schema3 paid result.

Приоритет один: reader-first формат и затем prospective compaction только same-owner supplement memo. В проверенной копии два таких memo занимают 4934 B; фактическую экономию ещё надо доказать. Gate: точный recursive JSON/field-presence round-trip, неизменные paid/request/accounting, положительная UTF-8/SQL/gzip экономия, restore/doctor/owner-transfer и rollback. Fresh collect должен сохранить parent перед memo в той же транзакции; иначе first-write останется inline. Foreign/unknown и невыгодные случаи остаются inline, без backfill.

Это небольшой независимый шаг; основные 470606 B текущего consultation context он не устранит. Универсальный bodyRef и изменение frozen rendering сюда не входят.
