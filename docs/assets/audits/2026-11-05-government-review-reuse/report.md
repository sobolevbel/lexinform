# J243: durable reuse government-review после rollover и cleanup

5 ноября 2026. Исторический аудит изолированного кандидата; root fullgate пройден, проверка релиза J243 следует после публикации. DA6.4 остаётся OPEN до проверки всех требований; DA6.3 и DA6.6 не изменены.

Результат A оплачен и применён через публичные DocumentReviews request/collect/apply. После government rollover, замены текущего review на B и штатного удаления memo прежний same-owner поиск больше не находил A. Новый узкий bridge читает actual current-owner projection receipt и точный retained result, сохраняя его исходные owner, binding, request и bytes.

## Доказательство и границы

Четыре controlled native-service случая покрывают consultation/commission × RCL/WPL. Точная последовательность: A → public pruneA при существующем старом owner → TermRollover → paid B → public pruneB/restore → exact caller A через AnalysisService. Все четыре сначала падали на последнем запросе; после исправления reuse не делает новых prepare, возвращает нулевой повторный usage и сохраняет original paid bytes. Это не утверждение, что оба memo штатно удаляются после rollover: действующая защита orphan memo сохранена.

Authority — actual receipt с точными SQL/JSON identity и applied projection, затем result ID, known schema3, исходный binding term, purpose, ordered caller input/reference и request/record identity. Shared decoder J241 имеет SHA `3885767e8a0d2d943341ac9f46d1b4d52ec3dec0161bb0eded209101275e01fb`; он должен присутствовать до интеграции bridge.

Реальный `purpose/review-input:<64hex>` — сохранённый work address, не восстановленный хеш текста. У известного `purpose/document-review:<64hex>` ключ обязан совпасть с original binding.reuse_key. Отсутствующие/opaque адреса не дают authority. Известное противоречие selected receipt отклоняется до prepare; changed input идёт обычным путём. Same-owner matcher и Sejm-policy не расширены.

Только текущий TermRollover переносит review receipts, сохраняя government number. Поэтому известный schema3 result/binding с другим number в этом конечном bridge отклоняется до prepare; настоящий второй paid government result проверяет эту границу. Другой исходный TERM разрешён. Это не global owner-equality invariant для ResultApplication; будущий cross-number transfer требует отдельного явного контракта, legacy/missing scope остаётся unknown.

## Qualification

250 focused tests,18 new unit cases,0new scenarios; strict mypy5, Ruff/format проходят. Отрицательные контроли охватывают indexed/JSON identity, phase/generation, canonical targets, actual foreign-number result, missing/opaque evidence и изменённые text/order/comparison scope. Independent exact-pin review: CLEAR, без независимого повторного запуска тестов.

Pinned fresh DB SHA `b9f86131e34d8a7e9c983d20d690bd2bd062836b0c86a50f69a1a978f27fc485`:257 government owners,0eligible scoped receipts. Read/dump/double restore и исходная DB неизменны. Natural terminal cleanup/reuse cohort и legal gold не заявляются: положительное acceptance здесь — четыре controlled public-service lifecycle случая. Нет schema/retention changes, backfill, rekey, новых receipt writes или model/network calls.

[Qualification](qualification.json), [source pins](source-pins.json), [peer review](peer-review.json), [fresh proof](fresh.json), [function inventory](function-inventory.json), [artifact hashes](artifacts.json).

Root:55 focused и8701 full tests/294.878s/92%coverage; mypy752/Ruff/format821green, все821source/config pins неизменны. Повторный pinned read/dump/double restore и AST-пробы неизменны. [Root proof](integration-proof.json), [case delta](case-delta.json), [root fresh](root-fresh.json). Новые18unit/0scenario/0removed; [предыдущий CI и relay](previous-release.json) подтверждены.

Actual2bba2c79 release and DA6.4 acceptance: [CI/relay](../2026-11-05-release-metadata-consistency/previous-release.json), [literal requirement audit](../2026-11-05-release-metadata-consistency/paid-history-completion-audit.json).
