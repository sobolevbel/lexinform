# J245: reader A для review memo как result index

5 ноября 2026. Reader проверен на root; publication/release J245 pending. Writer OFF, DA6.3 остаётся OPEN; уже принятые DA6.4/DA10.4 и счётчики плана46/6/22 не меняются.

Version1 body-free marker связывает exact result ID, frozen request SHA и hash immutable schema3 record. Hash определяется canonical JSON сохранённых typed fields с exclude_unset=True: будущие unset defaults не добавляются. Будущий writer обязан сначала перечитать SAVED parent, затем вычислять hash. DTO ограничен4096UTF8 bytes; source_key/source_fingerprint по256characters, canonical_dates до64. Разрешены только baseline_day/source_key/checked_at/source_fingerprint/canonical_dates и paid-or-zero usage. Неизвестные projection fields/versions и неподдерживаемая сериализация часов отвергаются.

Shared adapter проверяет intrinsic layout/parent/SQL identity на load, scratch restore и doctor. Он не восстанавливает caller context из текущего Bill и не утверждает applicability. Pure resolver получает actual caller ReviewContext и проверяет original-term ordered input/schema3/binding/purpose/request/record. Exact-ID ограничение J243 не позволяет подставить другой compatible government result вместо указанного pointer. Current Bill full review/context остаётся прежним. Known missing/corrupt pointer — ERROR, а не cache miss с новым prepare.

Finite scanner распознаёт только actual top-level reserved key, включая escaped key. Unicode, body string и nested key сами по себе не создают профиль. Strict known-marker JSON/hash/size/parent/context/authority checks сохранены. Known BLOB marker получает bounded invalid_review_memo на load/doctor/atomic restore; старый nonmarker сохраняет прежний error owner. Шесть unit-first failures стали зелёными; восемь добавленных cases,0removed. Deep-контроль теперь содержит глубокое VALUE под настоящим root marker.

Итого360focused tests,44newunits/0newscenarios; strictmypy8/Ruff/format8 pass. Три technical purpose carriers не включают production act_application. Exact final peer review CLEAR/findings[], без независимого запуска тестов. AST подтверждает неизменные collect/_remember/paid retention, все src SQL mutation literals и migrations; прежний matcher перенесён в pure module без изменения AST. Memo-before-retain порядок намеренно сохранён до writer B.

Pinned DB b9f86131…fc485:508Bill reads,163memos/972078stored value bytes. Три legacy review memo занимают492635B, canonical contexts471999B; все без computation_id/parent, contexts совпадают с current Bill. Natural eligible compaction0. OFF/ON full probe, memo bytes/default doctor/dump/double restore и исходная DB exact. Экономия не заявляется; backfill, retention/cleanup/schema/storage changes отсутствуют.

[Qualification](qualification.json), [source pins](source-pins.json), [peer review](peer-review.json), [pinned proof](pinned-proof.json), [scanner test IDs](scanner-test-id-delta.json), [metadata artifacts](artifacts.json).

Root [state comparison](root-state-comparison.json) confirms exact semantic reports and dump/memo hashes. The physical SQLite SHA also differs between repeated identical OFF runs after rebuilding the database; this sole field is excluded from cross-run equality. Original source SHA is unchanged. Root additionally re-exports the two public model DTOs, with no service/adapter changes.

Writer qualification must cover memo-key collisions: URL changes, document order and raw whitespace can preserve the existing memo key while changing exact retained input. Strict reference resolution alone cannot reproduce the older stored context; automatic writer remains OFF pending a compatible design.

Root92focused and8776full tests/300.66s/92%coverage pass; mypy756/Ruff/format825green, all825 source/config pins exact.44newunits/0newscenarios/0removed. [Integration proof](integration-proof.json).
