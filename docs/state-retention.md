# State size and retention

`lexinform.sql` is the authoritative snapshot. `pending-batches.json` contains only a format
version and the IDs/providers of batches with unconsumed items. `db dump` writes both files;
batch checkpoints commit both before reporting persistence. A missing or invalid manifest stops
the poller without dispatch; the next scheduled workflow creates the manifest when it dumps.

Bill summaries, Sejm stages, RCL snapshots and observed processes use compact JSON. Only fields
whose explicit default is `None` omit their null value. Required nulls, unknown fields, false,
zero and empty collections survive. Restoring an older SQL dump compacts these snapshots too;
this changes storage, not stage fingerprints or reader-visible values.

Cleanup runs at the start of pipeline phases using the injected clock. In dry runs it belongs
to the transaction that rolls back. All cutoffs are configurable in days:

| Data | Default | Rule |
|---|---:|---|
| Run reports | 90 | Delete old runs, retaining the latest successful discovery watermark even after a long pause. |
| Sent delivery payloads | 90 | Clear `delivery_json` only for confirmed `sent` rows with an old `sent_at`; keep row, IDs, status and deduplication keys. |
| Batch payloads | — | Remove source text, located text, review documents and answer bodies on the first run after consumption, token accounting and provider cleanup; the owning bill must be settled, without a ready analysis or deferred tracking. Nothing reads them after collection: the answer is in the bill and the memo, the located text in `ready_analysis_json`. Keep item/batch rows, `queued_at`, and token/model/error metadata. |
| Analysis memo | 180 | Delete entries not used since the cutoff only when ownership is known and the bill is settled. Pending analysis, ready results, deferred tracking, unsent/uncertain publications, queued/submitting intents and unconsumed/unaccounted batch items protect that bill's memos. |
| Computation history | — | New primary collections retain one validated `AnalysisRecord` or failure receipt and collection decision; secondary collections retain normalized results or failures, with separate compact input refs for reviews. Original usage survives memo and batch-body cleanup. No full source context, raw file, request body or raw provider response is added. |

Schema v49 starts primary history from newly collected items; existing completed results are not
copied or reconstructed. `AnalysisRecord.computation_id` points to that result; previously
completed legacy records and synchronous compatibility records omit it. A legacy pending item
collected now gets a known result ID while its absent original request checksum stays unknown.
The result ID names a provider/batch/custom item,
whereas `request_sha256` names its frozen input. Reuse retains the same result ID and does not
create another paid computation. Current provider identities have the repository's existing
single-account scope; they do not claim cross-account uniqueness.
The result reader also accepts schema v2 for secondary purposes, with no primary generation.
It validates the purpose-specific record and matching request/result refs; review output and
context must agree on the purpose. Nullable computation refs on secondary records are omitted
from legacy JSON. The reader compatibility stage is
[J065](plans/2026-10-22-document-architecture-progress.md#j065).
The secondary writer in [J066](plans/2026-10-22-document-architecture-progress.md#j066)
adds amendments, supplement, joint, name and explanation results to the existing memo/consumed
transaction. Reuse carries the original computation ID with zero new tokens; it does not add a
paid result. New amendments/supplement metadata pins the known original URL and document number;
absent legacy values remain unknown in history. Revision delivery work shares the transaction.
Secondary collection does not change primary status/generation or claim an application receipt.
The [J067](plans/2026-10-22-document-architecture-progress.md#j067) reader accepts compact
review schema v3: `review_input` retains source references independently of a validated answer,
so failed results can preserve known input metadata too. A missing legacy context remains
unknown. Full source text and projection-only fields are absent from the compact paid record.
The [J068](plans/2026-10-22-document-architecture-progress.md#j068) writer retains these review
results with memo/consumed checkpoint, using the original frozen metadata. Detailed memo/current
records keep their consumer format. Existing review submit/pause settings remain in force;
collecting an already submitted result never confirms application or delivery. Rollback of
this writer uses the J067 reader. Primary v1 and other secondary v2 retain their serialization.
No completed legacy secondary results are imported. J066 shipped on schema v49 with the J065
reader as its bridge; subsequent review history requires the J067 reader.
Computation validation describes the parsed output schema; it does not certify source
completeness or legal correctness, which have separate selection and DA9 checks.

`result_applications` separates **collection** (ready, superseded or failed) from **projection**.
Schema v50 adds explicit target namespace/ref for projections outside primary generations.
The target is stored once in JSON, with partial unique indexes and a CHECK preventing NULL
identity duplicates. Primary receipts keep their original JSON and generation; target producers
are enabled separately. Unknown namespaces remain readable metadata and never execute work.
See [J069](plans/2026-10-22-document-architecture-progress.md#j069) for the migration and
pre-producer rollback bridge; v49 is not a rollback reader for future explicit-target rows.
New status changes retain a `status_change` receipt for each paid amendments/supplement result
actually stored in the event. The event's persisted ID addresses the target; late amendments
record only their own application. Event, receipt and enclosing observation/delivery work share
one transaction. Repeated fingerprints and legacy records without a result ref add no receipts;
old supplements are not backfilled by a later amendment. This is application evidence, not
content-version history or delivery confirmation. The minimum rollback reader is J069.
Other active secondary consumers retain short receipts through `record_secondary_application`:

| Target namespace | Reference | Projection checkpoint |
|---|---|---|
| `document_review` | Purpose and original source key; memo key when legacy source key is absent | Review record, delivery work and removal of review work. Two source scopes can share one paid result. |
| `bill` | `joint` | Saved joint comparison. |
| `editorial` | `mnemonic` | Selected backfill name, after validation. |
| `publication` | New publication ID | Manual amendment revision, retained result and consumed checkpoint. |
| `command` | Incoming update ID | Executed `/explain` command and its known result. Technical reply delivery has its existing separate boundary. |

The shared writer records metadata inside each consumer's transaction. It does not submit work,
choose sources or supply primary generations. Bare memo/API/CLI reads have no persisted projection
target and add no receipt. Legacy records without a result ref remain untraced; paused act reviews
stay paused. Revision receipts follow their retained result within the same transaction, satisfying
the existing foreign key. No source text or paid answer is duplicated per application.
RCL name backfill locates its original question across actual stored work-owner terms. It compares
the complete name-context key before using a memo, pending intent/job or retained result. A paid
retry can belong to the new term while retaining the original question key; history lookup keeps
both identities. Memo cleanup does not force another name request, and legacy memo refs remain
unknown. This scope rule applies to RCL project IDs; Sejm terms and reused wykaz codes keep their
existing boundaries. The original owner ledger must outlive work depending on it. See
[J072](plans/2026-10-22-document-architecture-progress.md#j072).
Names also accept the older empty-text digest tag: `NameContext` has no source text, so the key
still preserves every name input. This exception does not permit legacy text-analysis reuse.
All service writes of primary analysis use `services.analysis_applications.apply_analysis`:
the primary service, RCL watcher, wykaz handover and RPW/RCL-to-print linker share one command.
A projection receipt commits with `Bill.analysis` and records the actual target row's
generation/status before the write. Primary commands distinguish ready batch result from memo
reuse; tracking/linking records a changed computation ref as `retained_result`, without claiming
another paid call. An unchanged ref during quiet tracking adds no receipt. A repeated receipt
keeps its original timestamp. Nested savepoints retain the enclosing observation/delivery
transaction's rollback boundary.
Sejm tracking prepares reanalysis before its checkpoint and applies it with the observed process
and delivery work. Waiting secondary work leaves the current analysis and ready result intact.
The reanalysis counter advances after that commit; paid usage remains in the existing call ledger.
Preparation and quiet source checks do not create projection receipts. Ready does not prove
application or publication, and applied does not prove delivery. Direct identity relocation
at term rollover and the remaining applicability history stay in
[DA6](plans/2026-10-22-document-architecture.md#da6).
Schema v51 adds a reader for compact `result_applicability` observations. The source association
contains the known paid ref, owner scope, URL/kind, recorded fingerprints and first recording time.
It neither copies the answer/input nor certifies archived bytes or complete input coverage.
Repeated saves retain the first time without a row per poll. Unknown legacy refs are not backfilled.
A paid scan manifest remains original input provenance and is excluded from the current selection
metadata fingerprint. Primary `apply_analysis` writes the association in the existing projection
transaction, including an unchanged paid ref at a new source URL. Collection and preparation do
not write it. A failure rolls back the association and projection together; the paid result stays
retryable. The observation itself is the source association, without a duplicate application receipt.
Reuse, delivery and cost policy remain unchanged. The additive table survives a J082 restore/dump
unchanged, including future-populated fixtures; rollback keeps schema 51 rather than downgrading it.
See [J083](plans/2026-10-22-document-architecture-progress.md#j083) and
[J084](plans/2026-10-22-document-architecture-progress.md#j084).
Result, collection decision, memo/ready changes and consumed checkpoint commit together;
run cost acknowledgement retains its existing mechanism.
Primary handoff to an assigned druk moves only unconsumed `analysis`/`reanalysis` items,
their intents and ready analysis. Secondary work keeps its original subject and frozen context;
historical results are not retargeted. Collection coordinates alone do not identify the
original input of a primary job that was moved before collection. See
[J073](plans/2026-10-22-document-architecture-progress.md#j073).
The nullable `input_binding` reader separates the original request's term/number and logical
reuse key from collection coordinates and exact request SHA. Missing legacy metadata stays
unknown and is omitted from JSON. Metadata keeps the frozen memo key; review bindings match
recorded input references and subject identities. A historical policy key is never recomputed
from a stored context with today's rules. The collector preserves already known bindings without
deriving them from a current bill or mutable item owner. The
[J076](plans/2026-10-22-document-architecture-progress.md#j076) writer uses
`BatchIntent.from_request` in the five new-request producers to freeze original term/number/key
before repository IO. It checks the question subject and rejects rebinding an existing input;
plain construction/restore keeps absent legacy metadata unknown. Provider payloads and retry
engines are unchanged. Populated bindings require the
[J075](plans/2026-10-22-document-architecture-progress.md#j075) reader/collector bridge for rollback.
The binding does not archive source bytes or certify a full historical PreparedInput.
The history preserves the paid record when a skip/reset makes it inapplicable. Immutable rows
reject replacement, and an exact repeated write is harmless.

This first migration temporarily retains the answer in both history and legacy bill/memo
projections. Removing those copies requires all primary consumers, frozen deliveries and
secondary purposes to use protected result refs; it is not part of this dual-write stage.
When a secondary memo is absent, amendments/supplement/joint/name can recover a validated paid
record from history by the same subject, purpose, exact request key and record type. Reuse
rebuilds the operational memo with the original result/request refs and charges no new tokens.
Existing memos take precedence, including legacy records without those refs. This does not
broaden context equivalence or merge Sejm terms. See
[J074](plans/2026-10-22-document-architecture-progress.md#j074).
An explanation with a recorded input binding can recover its exact paid answer after memo
cleanup. The caller reads only explanation history and passes a snapshot into preparation;
parallel prepare workers never read the repository. The same subject, purpose, recorded logical
key and record type must match. Existing memos take precedence; an unbound legacy explanation
does not acquire a guessed key. Reuse restores the operational memo without another paid request
or mutation of the original record. See
[J077](plans/2026-10-22-document-architecture-progress.md#j077).
Primary analysis and reanalysis also use caller-owned, purpose-filtered history snapshots.
An exact known binding can supply the paid answer after memo cleanup or a generation reset.
The original collection receipt remains immutable; applying the answer records the current
generation through the existing projection checkpoint. Preparation alone creates no projection
receipt. Ready/pending shortcuts and the explicit force cache bypass remain unchanged. Unknown
legacy bindings are not inferred from generation-dependent request IDs. See
[J078](plans/2026-10-22-document-architecture-progress.md#j078).
The measured sizes and scope are in [J061](plans/2026-10-22-document-architecture-progress.md#j061)
and [J062](plans/2026-10-22-document-architecture-progress.md#j062), with tracking/linking in
[J063](plans/2026-10-22-document-architecture-progress.md#j063).

Batch metadata and collected records retain `request_sha256` after payload cleanup. It is copied
from the original frozen request by the shared submission/attachment constructor. An absent
legacy checksum stays unknown; cleanup and reuse never derive it from a fresh context. The hash
does not pin or archive the request body and does not extend memo retention.
The offline [request doctor](https://github.com/sobolevbel/lexinform/blob/main/tools/README.md#request-provenance-doctor) inventories these refs
on a checkpointed copy and reports corruption/unknowns without modifying state or retrying work.
The [unfinished-work doctor](https://github.com/sobolevbel/lexinform/blob/main/tools/README.md#unfinished-work-doctor)
uses the same snapshot reader to inventory document/review owners and saved dependencies.
Its overlapping memo counts describe existing guards; they do not authorize deletion or establish
that a historical publication can be edited. See
[J080](plans/2026-10-22-document-architecture-progress.md#j080).
The [computation graph doctor](https://github.com/sobolevbel/lexinform/blob/main/tools/README.md#computation-graph-doctor)
checks saved result/application identities and known result refs across projections. Original
input, collection owner and application target retain their separate meanings; unknown legacy
refs remain unknown. It shares recorded JSON traversal with request doctor. See
[J081](plans/2026-10-22-document-architecture-progress.md#j081).
Known physical application targets have a row-presence inventory; missing rows/tables are
warnings. Logical targets show owner presence without comparing today's slot contents with a
historical receipt. An absent historical owner remains an observation, and unsupported reference
formats stay opaque. See [J082](plans/2026-10-22-document-architecture-progress.md#j082).

Schema-51 applicability observations also get SQL/JSON identity and retained-result checks;
failed or absent results cannot support a valid association. Legacy schema 50 may omit the table;
a missing schema-51 table is reported as damage. Historical associations do not require the
current Bill or revalidate its present source. Source metadata is not echoed in the report.
See [J085](plans/2026-10-22-document-architecture-progress.md#j085).

A document review whose batch failed at every allowed submission keeps only its keys and a
limitation (`ReviewWork.abandoned`): its documents go, and the same source is not asked again.
A review the provider refused for its length keeps its documents for the overflow model.

Memo writes and reuse on the calling thread refresh `last_used_at` without replacing the original
paid result. Migration v33 adds nullable ownership and last-use metadata. An old hash alone cannot
reliably identify its bill: legacy entries stay protected until a subsequent save/reuse supplies
that information. There is no guessed ownership or expiration date.

Settings: `LEXINFORM_RUNS_RETENTION_DAYS`, `LEXINFORM_HISTORY_PAYLOAD_RETENTION_DAYS` (sent delivery
payloads only), and
`LEXINFORM_ANALYSIS_MEMO_RETENTION_DAYS`. Minimum: seven days. Reducing a cutoff takes effect on
the next run. An expired, unneeded cache entry may incur a new LLM call if its old context is
explicitly revisited and its consumer cannot recover an exact answer from protected history;
expiration itself never initiates analysis.

Bill identities, legislative evidence, publication history, command IDs, batch accounting and
unknown operations are not deleted on a timer. Thus the payload history is bounded for completed
work, while the lightweight ledger and genuinely pending work can continue growing. This is not
a hard cap on the entire database. Old Git commits remain available; pruning the current snapshot
does not rewrite repository history.
