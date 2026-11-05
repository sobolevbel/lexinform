# Complete finite correlation ID contract

Structured DEBUG events now carry the existing extraction ID and the known parents of actual
queued/submitted requests. The queued event uses the frozen request SHA when it is available.
A shared adapter helper omits missing or opaque receipt parents and masks unrelated outer fields.
Native events cover artifact metadata, extraction, selection-extraction binding, applicability
association, content version and resolved publication creation.

The collision audit also corrects misleading identities. An ignored intent insert emits no new
request or parent trace. Publication upserts expose the resolved row ID and updated status while
withholding an unverified supplied owner. Status updates use the already loaded physical owner
and emit no success-shaped event for an absent row. Immutable identity events follow existing
saved-row equality checks. Every event describes a local validated write; a subsequent rollback
can still occur, and events do not attest durable checkpoint, provider acceptance or delivery.

The complete repository inventory classifies 98 direct write/schema/restore methods, 37 dynamic
SQL call sites and three delegated wrappers. Fifteen native boundaries cover the required core
identity classes. Auxiliary catalog, omission, physical, edition and snapshot IDs remain in typed
SQL. This is the stated finite DA10.1 correlation contract. Request SHA and existing composite
application fields provide real identities; no synthetic PreparedInput or application row ID is
introduced. Broader source/input history and natural lifecycle acceptance remain separate work.

Root reproduces all 305 SQL call ASTs unchanged. The repository business AST remains unchanged
after explicitly checked normalization of one cursor-result capture and two logging-only guards.
No text, selection, provider request, schema, stored report or retention shape changes. The
[inventory recipe](inventory-recipe.md) accepts base checkout, candidate checkout and output JSON paths;
use commit7b196312 as the base and this stage's pinned source for the candidate.

Twenty-one new unit cases cover a public Senate source-to-request-to-result-to-content-to-publication
chain with IDs checked against actual SQL, conflicting duplicates, other-owner upsert, absent
rows, opaque metadata, rollback and cancellation. Existing workers1/4 and context isolation tests
remain part of focused qualification. No scenario is added.

[Qualification](qualification.json), [full boundary inventory](inventory.md),
[reproduced machine inventory](write-inventory.json), [state proof](state-proof.json),
[source pins](source-pins.json), [independent review](peer-review.json).

Previous release: [exact CI and relay verification](previous-release.json).

The first full gate caught a filter defect: explicit unknown IDs were removed before context
merge and could be replaced by unrelated outer IDs. The filter now retains bounded known-key
None tombstones internally, freezing the first observed context across multiple handlers. Output
formatters remain unchanged and omit unavailable values. Nine direct cases and the actual handler
fixture cover masking and later-context isolation. Root sequentially runs logging setup before
41 focused cases, including existing run-context scenarios; all pass. The privacy assertion is
scoped to new architectural events and leaves historical loader INFO logging unchanged.

[Filter correction proof](filter-proof.json), [correction review](filter-review.json).

Final full gate: 8375 tests in 279.676 seconds, 92% coverage, strict mypy over731 files,
Ruff and formatting green. All800 source/config pins remain exact. Fresh state620f092 retains
every table row and rowid, full canonical dump, default doctor and double restore. DA10.1 is
complete for its stated known-ID contract; broader lifecycle/history tasks remain open.
