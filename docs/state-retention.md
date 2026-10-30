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

The [2026-10-27 storage profile](reviews/2026-10-27-storage-profile.md) measures a pinned copy,
recorded physical refs and Git checkpoints; it does not enable a raw archive or change cutoffs.
The [finite dependency audit](reviews/2026-10-28-retention-dependencies.md) records the decision
to keep every existing guard through schema 56; it does not declare legacy work exhausted.

Schema v58 is the legacy-reader bridge. Its four legacy tables start empty; migration, restore,
normal runs and cleanup do not inventory or import existing answers. The explicit finite import
API records exact canonical whole-record values and their observed locations once. It preserves
unknown fields, absent/null distinctions and placeholders without assigning provider/input/prompt
provenance, original timestamps or a paid computation identity. Import time is supplied explicitly;
container ownership is separate from unknown original computation ownership. Legacy history has
no automatic retention cutoff and is independent of operational source-row deletion.

The reader also accepts one fixed optional storage representation: an exact `analysis` member
in `legacy_analysis_bodies`, with the remaining record envelope and a nullable body FK in
`legacy_result_values`. It reconstructs the original whole canonical value and verifies both SHAs;
the stored envelope is a representation, not the original raw JSON. No other members or recursive
contexts are factored out. Present/absent/null/unknown fields survive, and archive materialization
does not upcast. The import API writes inline whole values; the explicit maintenance API can
share only repeated exact analysis bodies within that same finite import.

The bridge accepts direct `legacy_result_ref` projections with bounded current metadata overrides
and explicit removals. The immutable original remains unchanged, including its original usage.
Missing/corrupt refs, duplicate keys in reference-bearing JSON and answer/origin overrides fail
closed; ordinary inline JSON keeps its existing reader behavior. Embedded JSON strings and
content-addressed snapshots are not rewritten. No producer creates first references or compacts
existing inline records automatically. Activation requires the bridge's CI/deploy proof and a
measured copy-state gzip increase of at most 5%,
including actual writer serialization, quiet checks and selection updates. The explicit
`analysis-editorial-v1` current-schema upcast may add only absent `mnemonic_candidate`,
`mnemonic_fallback`, `mnemonic_basis` as empty strings and `mnemonic_language` as `ru`. These fixed
defaults are a current projection, not historical answer facts; present values stay exact. Every
other answer difference takes the ordinary inline path. Timestamp-only counterfactual
measurements do not establish the activation budget.

The inert maintenance planner binds its finite inventory and approved direct replacements to
the exact WAL-visible raw dump SHA. Apply recomputes the plan and checks physical originals in
one transaction, validates immutable values and hydrated readers, then verifies dump/restore
parity and the 5% gzip budget. Any refusal rolls everything back. Existing import markers permit
only a validated no-op; empty inventories leave the marker absent. Encoded snapshots and work,
delivery and request envelopes are inventoried without rewrites. No CLI, pipeline or workflow
activates this API. The canonical locked job and fresh writer/size qualification remain the
activation boundary; the copy probe is not sized for the relay host. See
[J140](plans/2026-10-22-document-architecture-progress.md#j140).

Ordinary analysis, ready-analysis, joint, review and status-answer saves preserve a valid existing
physical reference at its saved structural path. Quiet checks and supported selection metadata
updates change only the existing bounded overrides; changed answers and modern computation refs
use inline JSON. These writes add no import or archive rows. Empty supplement clearing validates
the previous refs before retaining its existing SQL NULL representation. Cost ranking resolves
saved input tokens before filtering and ordering, including explicit null, zero and removals.
Transfers into new locations, reordered lists and review-envelope changes outside the fixed
override allowlist can still become inline; immutable archive values remain retained. Preserving
source refs across those transfers needs a separate contract and qualification.

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
application or publication, and applied does not prove delivery.
Government-row rollover records a known primary result's new projection and unchanged source
association in the same transaction as the move. The paid result, original input owner and old
receipts stay under their original identities; no analysis is re-applied to update status or
generation. Legacy without a paid ID gets no invented receipt. This records the retained view,
not a fresh source read or a new legal-applicability check. See
[J115](plans/2026-10-22-document-architecture-progress.md#j115); secondary relocation and the
remaining applicability history stay in [DA6](plans/2026-10-22-document-architecture.md#da6).
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

The [legacy inventory](https://github.com/sobolevbel/lexinform/blob/main/tools/README.md#legacy-history-inventory)
uses the same saved-record catalogue and reports exact duplicate JSON values and their sizes.
It preserves unknown fields without adding current model defaults; its report contains no answer
bodies. Batch receipts and memo keys do not establish a missing paid result identity, and this
read-only inventory does not import or remove data. See
[J086](plans/2026-10-22-document-architecture-progress.md#j086).

A document review whose batch failed at every allowed submission clears `context` and
`input_bill_json` and keeps its keys and limitation (`ReviewWork.abandoned`); an existing
`comparison_document` snapshot remains. The same source is not asked again.
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

Schema 52 introduces deduplicated document-selection metadata and owner/catalog observations.
This stage provides the reader and atomic repository contract only; production tables start empty.
History excludes extracted bodies and later provider scan inputs, survives operational memo cleanup,
and does not imply raw retention or an archive backend. A repeated check preserves the first
recording time and exact dump; historical paid results acquire no inferred observation. See
[J095](plans/2026-10-22-document-architecture-progress.md#j095).

The text prefilter records new completed selection decisions and their official document relations
before storing the status outcome, in the same transaction on the calling thread. A keyword miss,
scan, waiting or unsupported selection can retain metadata without a paid analysis. A failed source
fetch is not promoted to a completed decision. Repeated checks keep the original recording time;
no legacy backfill, extra download, model call or provider-input retention is introduced. Other
consumers and standalone artifact metadata remain open. See
[J096](plans/2026-10-22-document-architecture-progress.md#j096).

New pipeline run reports can retain aggregate `document_reads` observations under the existing
run retention. They store host counts, completed-response bytes and per-run distinct-body totals,
without URLs, file bodies or individual hashes. Old reports remain unmeasured; failed/over-limit
bytes and cross-run distinct storage cannot be reconstructed from these counters. See
[J097](plans/2026-10-22-document-architecture-progress.md#j097) and the
[storage profile](reviews/2026-10-27-storage-profile.md#наблюдение-будущих-чтений).

Pipeline reports may also retain versioned `document_download_diagnostics`, measured per
TextLoader download invocation. Cache hits, unknown routing and known pre-send rejection remain
separate; adapter-internal HTTP retries are unmeasured. Exact outcome/omission counts accompany
at most eight deterministic grouped failure examples. The compact UTF-8 object is capped at
2 KiB, plus its report-field envelope. Examples contain only URL SHA, configured safe host,
fixed reasons, known status/limit and count; bodies, raw URLs, messages and partial-body SHA are
absent. Workers update locked memory; the pipeline caller snapshots it in the existing final
report transaction. Empty/unmeasured reports omit the field, with no legacy backfill or new rows.
The existing run retention applies. Standalone scan/reprefilter, direct CLI and RCL lookup
consumers still need their own durable finalization; this is not a complete HTTP ledger. See
[J142](plans/2026-10-22-document-architecture-progress.md#j142).

J157 добавляет reader поля `metadata_url_omissions` и collector без Clock. J158 заполняет
его для omitted requested-URL receipts общего загрузчика через pipeline/CLI finalizers. Отсутствующая metadata сохраняет прежние public/stored JSON bytes.
Диагностика v1 считает omission observations: точные суммы по фиксированным причинам, не более
восьми детерминированных частичных hash-only примеров и точный остаток observations. Компактный
UTF-8 объект ограничен 2 KiB плюс envelope поля отчёта; URL и exception prose отсутствуют.
Не кодируемый в UTF-8 URL не имеет SHA и явно отмечается `invalid_unicode`. Действует run retention.

Будущая shared URL policy сохраняет исходные допущенные bytes, включая credential-free URL без
query на configured/fake origins. Query требует известного из кода official host/path/key/value
contract; непроверенный query/identifier contract явно опускается без upstream/legal вердикта.
J158 подключает policy к loader receipts, J161 — к generic primary/review/prefilter graph factories.
Она не ищет secrets в произвольном тексте и не переопределяет историческую валидацию RCL/Senate envelopes. Их более строгий query admission
остаётся прежним. Будущий specialized producer применяет пересечение policies до новых IDs, независимо
сохраняет complete body и URL-free extraction, передаёт omission source/selection refs в prepared
metadata без dangling links. Для opaque failure prose нужен явный flag в месте создания, без
изменения actual IO, parser/model input или rejection messages. Reader optional поля должен быть
развёрнут до producer: старый typed reader может отбросить незнакомое поле, хотя dump/restore
сохраняет его opaque stored JSON.

Schema 53 adds complete-body SHA/size/first-recording metadata and normalized selection-part refs.
The reader stage creates empty tables, without production capture or legacy backfill. A metadata
row is neither retained bytes nor a GC pin; the chosen storage profile and any required pending
payload remain separate. Repeated metadata keeps the first UTC recording time; historical file
sizes/dates are never inferred from text hashes or aggregate download counters. See
[J099](plans/2026-10-22-document-architecture-progress.md#j099).

The shared loader now exposes known complete-body identities for metadata capture. Text prefilter
records matching outer-body refs with its source/status transaction; pipeline finalization records
other known bodies with the run checkpoint. Known identities survive counter resets and warm-cache
reuse, including after a dry-run rollback; body bytes do not. First recording time is not the
original download time. New rows neither retain raw bytes nor pin them for GC; standalone consumers
outside these capture paths remain outside scope. See
[J100](plans/2026-10-22-document-architecture-progress.md#j100).


Primary analysis now records completed source decisions independently of the later fitted input,
paid result or delivery. First analysis, re-analysis and explanation use the same caller-thread
recording boundary, including standalone calls and paid-work failures. A crash before preparation returns can still lose this source observation. Source outages create no
completed decision; batch-ready reuse without a new read creates no new observation. Existing
source metadata and first recording times stay immutable. Review/secondary readers remain outside
this producer's scope. No raw retention, extra request or inferred link to an old paid input is
introduced. See [J102](plans/2026-10-22-document-architecture-progress.md#j102).


Review preparation now records the original file-selection metadata through the shared
`DocumentSourceRead` helper on the calling thread. A refused role/digest can still retain its
observed source; this metadata does not assert successful review admission. Reused historical
ReviewDocument snapshots create no new source observation, and unknown file policies stay unknown.
Source metadata remains independent of paid input/result identity and raw retention. See
[J103](plans/2026-10-22-document-architecture-progress.md#j103).


Generic secondary readers now record completed source metadata before committee amendments,
recommendation and supplement inputs are used. Input-budget refusal and a bare supplement do not
remove the completed source decision. An extractor exception before selection completion creates
no inferred decision. The Senate-specific resolution path and historical comparison workers are
unchanged. See [J104](plans/2026-10-22-document-architecture-progress.md#j104).


Senate-specific source observations record actual complete-body identity separately from the
expected resolution descriptor. Their question/policy identify a physical observation, with
`unverified` state and unknown normative role; an observed reader-text hash does not establish
extraction or normative coverage. Changed-body refusals can retain the actual physical metadata.
Valid full scans retain their original source SHA independently of provider derivatives, without
claiming visual verification. Subject/date/fullness checks and worker historical comparisons are
unchanged. See [J105](plans/2026-10-22-document-architecture-progress.md#j105).


Schema 54 adds an immutable requested-URL/complete-body read receipt, with the first caller recording
time preserved across retries and exact restores. The parent artifact and byte size must match.
This reader-first stage starts empty and infers no old downloads from selected URLs or text hashes.
A receipt is neither a retained-file locator nor a reliable original download/publication date;
no bodies, source owners or paid-input lineage are added. The producer remains disabled. See
[J106](plans/2026-10-22-document-architecture-progress.md#j106).


The receipt producer records matching selected-source reads in the source transaction, and the
remaining known complete responses in pipeline finalization. This includes parser failures before
a source choice without inventing an accepted document. Known identities survive warm caches,
metric resets and dry-run rollback; first durable recording remains distinct from first download.
Failed/over-limit fetches produce no complete receipt. Crash before the relevant recording boundary
and unrelated standalone reads are still outside scope. See
[J107](plans/2026-10-22-document-architecture-progress.md#j107).


## Ограниченные extraction metadata

Schema 56 сохраняет один `ExtractionResult` для известного outer input и версии metadata:
SHA физического manifest, прежней package output string, параметры limits, component file hashes,
доступные dependency versions и сводки максимум 16 выбранных физических частей. JSON ограничен
16 KiB в модели и SQL; omitted count явно показывает недостающие сводки. Тысячи PDF pages или
Word blocks не создают SQL rows на каждый block. Member names, block text, raw и error prose
в эту запись не входят. Source selection связывается с результатом в своей caller transaction;
неизменившийся результат и повторная проверка не дублируются.

Detailed physical manifests существуют только в памяти текущего извлечения. `not_retained`,
`manifest_not_retained`, `input_mapping=unmapped` и `completeness=not_asserted` не обещают
исторический replay, physical citations, нормативную полноту или чтение скана моделью.
DOC/отдельный XML и custom adapters без physical origins сохраняют explicit unknown; старые
adapters без нового optional port не получают выдуманный результат. Перечисленные component
hashes относятся к исходному файлу класса, не ко всему транзитивному коду или environment;
отсутствующая file identity/version остаётся unknown. Package policy имеет отдельную версию/hash,
а последующая source selection и provider fitting остаются самостоятельными решениями.

Primary, prefilter, review и generic secondary source transactions сохраняют доступные records.
Senate special path получает их только если общий loader действительно выполнил такое извлечение.
Exception до результата, zero-part refusal без physical source ref и unrelated direct downloads
не становятся сохранённым ExtractionResult. Эти metadata переживают operational cache cleanup;
данные из старых URL и legacy state не backfilled. Raw/backend/retention decision не меняется.

## Candidate Edition metadata (v57)

`document_editions` сохраняет один manifest выбранного candidate normative set, ограниченный
1 KiB в модели и SQL. Он содержит hash/count membership, contract и первое время записи;
метаданные частей остаются в существующем immutable selection. Каждая связь
`selection_editions` проверяет exact parent и membership. Повторные наблюдения и URL aliases
с тем же известным core используют прежний manifest; строки на каждую часть нет.

FK parents и ссылки переживают operational cleanup. Правила очистки, body retention и backend
не меняются. Неизвестные scans/legacy identities и unresolved source sets не получают
предполагаемую Edition. Этот адрес не доказывает юридическую полноту, допустимость для вопроса,
эквивалентность representations, supersession или paid-input identity. Полная provenance
решения остаётся частичной; manifest не заполняет пропущенные candidate dispositions.

## Senate candidate inspection reader (v61)

`senate_inspections` сохраняет известного Bill owner, официальную связь с resolution print,
реальные поля стадии, аргументы вызова, упорядоченные descriptors вложений, причины inspection/
skip, equivalence groups и итог выбора/отказа/ошибки. Индексы и hashes descriptors — локальные
locators; отсутствующие в API Stage/Attachment IDs не придумываются. Physical source facts
остаются unverified, отдельно от legal admission, прежнего paid input и сохранённого применения.
Resolver наблюдает существующие ветки один раз: producer вызывается tracking, отложенным
document work и amendments request с настоящим Bill owner. Сбор clock-free; finish hook пишет
metadata на вызывающем потоке после явного join workers, включая завершённые непрочитанные outcomes.
Повторные descriptor links сохраняются, общий physical selection graph записывается один раз.
SourceObservationIdentity не требует времени; SourceObservation получает настоящий aware timestamp
при записи, сохраняя прежние IDs/JSON. После rollback отсутствующий owner не создаётся; finish
проверяет только существование identity row. Позднее text/scan verification остаётся отдельным J105.

Полная запись вместе с первым aware UTC timestamp ограничена 8 KiB UTF-8. Переполнение сохраняет
whole omission ≤ 2 KiB с semantic/universe/context/decision hashes, counts, итогом/chosen identity
hashes и immutable manifest физических связей. Отсутствующее полное metadata явно недоступно
для semantic revalidation; поля не обрезаются. Immutable ID связывает opaque full semantic SHA,
весь retained summary, count и hash links; timestamp в identity не входит. Capture version
описывает metadata contract, а не legal admission policy; reader проверяет структуру и ссылки,
не повторяет resolver policy. Безопасные официальные URL сохраняют значение
и hash точного оригинала; userinfo/query/fragment и небезопасные URL в новых envelopes имеют
только hash/omission. Текст исключений и извлечённые bodies не копируются.

Каждый известный complete candidate body сохраняет точный artifact parent и descriptor-index
link, включая omitted records. Source/selection/read/extraction IDs nullable, пока реально
неизвестны или опущены для unsafe URL; retained parents проверяются по owner, actual outer SHA/size
и точному URL hash.
Известные source observations требуют своего physical selection/artifact/extraction graph.
Reader-text SHA не доказывает равенство extraction output или provider input. Обработка URL/body
в существующих J105 parents прежняя; новые envelopes не редактируют и не заменяют старые parents.
При unsafe requested URL этот producer сохраняет exact body/extraction parents без новых
read/source/selection refs. Общие loader-wide receipts и последующий J105 сохраняют свой точный
requested URL по прежнему контракту. Безопасный URL вместо исходного не придумывается.
Min-reader de97 с `after_failure` развёрнут до producer; новый reason допустим только у unattempted
filter fact. Непрочитанные descriptors получают `after_failure` после отмены/обычной ошибки; `after_outage`
сохраняется для фактического ServiceUnavailableError/nonmissing OrkaUnreachableError.

Повторный факт сохраняет первое время; вновь известные parent metadata меняют immutable identity.
Run/history pruning сохраняет записи и shared parents. JSON caps не включают shared artifact/read/
extraction/source metadata и junction rows; их размеры измеряются отдельно. Schema60 reader
сохраняет populated opaque SQL без понимания новых semantics. Get, doctor и pre-destructive
scratch restore используют общую SQL/JSON/parent validation, включая count/hash связей,
известного owner и orphan checks.

## RCL consultation-letter use reader (v60)

`rcl_letter_uses` хранит неизменяемое metadata разбора письма и merge окна в памяти, отдельно от
`RclConsultation`/Bill JSON. Это наблюдение source-use, а не receipt сохранённого применения,
актуального дедлайна или отправки. RCL reader наблюдает существующие read/parse/merge без повторного
IO; один collector разделяется между reader instances контейнера. Pipeline и девять standalone
CLI owners сохраняют его через один opaque history hook внутри существующей транзакции: exact
body/read/extraction parents перед uses, затем report/prune или batch-cost acknowledgement.
SQL остаётся на вызывающем потоке; callback не получает Clock/repository. Counter reset не забывает
известные факты, dry-run откатывает SQL. Show/read-only lookup и preview не получают нового owner;
неизвестный preview сохраняет существующее enrollment поведение. Container.close ничего не flush'ит.

Новая telemetry construction/observer failure не меняет результат или исходное исключение.
History hook failure откатывает общую owner-транзакцию, следует её прежней report policy и пишет
только ограниченное имя класса; исходная business exception сохраняется при ошибке logging.
Для unsafe URL новый RCL envelope не переносит ArtifactReadIdentity/requested_url: read ref остаётся
unknown, но известные body/extraction refs сохраняются. Existing generic receipts не удаляются и
не переписываются; это не гарантия URL-redaction всей БД или изменение общего receipt behavior.

Официальные project/stage/letter ID принадлежат RCL; неизвестный stage ID остаётся `None`,
stage number не подставляется вместо ID. Известные complete body/read/extraction refs проверяются
по существующим parent metadata. Кэшированный текст без проверенной связи с body остаётся unknown.
Hash точного UTF-8 parser input отделён от extraction output; равенство заявляется только явно.

Полная запись вместе с первым aware UTC `recorded_at` ограничена 4 KiB UTF-8. При превышении общих
или полевых лимитов сохраняется omission ≤ 2 KiB с исходным semantic SHA, официальными ключами,
известными refs, input/output/merge hashes и причинами; отсутствующее полное metadata не считается
перепроверенным. URL с userinfo/query/fragment и небезопасные URL сохраняют только hash/omission.
Текст, body, каталог, exception message и платный input сюда не копируются; shared parent metadata
не входит в лимит одной записи и учитывается отдельно.

Совместимый reader принимает identities v1 и v2. У полного v1 semantic SHA перепроверяет retained
metadata. У omitted v1 отсутствующий full payload не позволяет связать retained refs с исходным
ID; doctor помечает такие rows как legacy-unbound, сохраняя bytes/IDs и прежнюю parent-coherence
проверку. Это ограничение persisted integrity, не свидетельство ошибочного production record.
V2 identity связывает opaque full semantic SHA, весь retained payload и coverage, без timestamp;
coordinated замена допустимых refs под прежним ID отклоняется до изменения restore target.

Factory `of_bound` требует исходный complete payload: legacy omission не upcast'ится. Полные
факты сохраняют старый v1 ID/JSON. Runtime producer вызывает `of_bound`: только новые omitted
факты получают v2; legacy factory `of` продолжает создавать v1. Нет backfill, новой migration
или увеличения 4/2 KiB caps. V2-capable reader 8e24 реально развёрнут до writer activation.
Старый 2a6e reader отклоняет v2 на scratch preflight; после writer activation rollback сохраняет
этот совместимый reader. Проверенный schema60 opaque roundtrip Senate records относится к
snapshot с прежними RCL identities и не обещает чтения будущих RCL v2 старым кодом.

Одинаковый факт сохраняет первую запись и время; изменившиеся input/origin/components/merge context
дают другой ID. Run/history pruning не удаляет эти metadata и родителей. Число различных фактов
зависит от работы; постоянный общий размер БД/collector не обещан. Reader-first релиз проверяется
до подключения producer; S2 может откатываться к этому reader без изменения публичного RCL JSON.

## Outer candidate disposition trace (v59)

`candidate_disposition_traces` сохраняет теневую метаисторию нетривиального выбора внешних URL:
исходный typed запрос, порядок кандидатов, известный SHA полного прочитанного body, исход и
причину каждой попытки. Обычный успешный одиночный кандидат с selection `selected`/`unverified`
уже описан в source observation и не получает повторную trace. Альтернативы, восстановленные
отказы, переполнение и заблокированные состояния сохраняются. `source_candidate_traces`
связывает trace с source observation отдельно от
`selection_id`; разные отвергнутые альтернативы могут привести к одному наблюдению.
Обе записи делят транзакцию observation. Пустые новые таблицы не заполняют старые дела.

JSON ограничен 4 KiB. Если полный запрос и решения не помещаются, запись имеет явное
`omitted_over_limit`, число пропущенных dispositions и SHA полного исходного запроса без
частичного списка. Состояние выбранной selection хранится отдельно от факта выбора URL:
выбранный внешний файл может всё ещё ждать обязательное приложение. Поздние альтернативы
после остановки fallback помечены `unattempted`, без притворного чтения. Ошибка построения
только этой необязательной телеметрии не меняет прежний выбор; сохранённая запись и её FK
проверяются при restore. Тексты, bytes и extracted blocks здесь не хранятся.

Этап не фиксирует каждое решение по внутреннему члену ZIP и не выводит официальные связи
по имени файла. Он не меняет policy, admission, model input, стоимость или raw retention.

## Editorial history после J109

Inert revision-application storage accepts a second typed owner in `document_work` without a
runtime producer, collector handoff or drain. Its metadata is limited to 8192 UTF-8 bytes and
references an immutable paid amendments result and real bill/change/plan snapshots. Waiting and
blocked work retain those dependencies; current retention deletes neither these work rows nor
results/content snapshots. Completed rows keep their frozen identity and actual publication receipt.
The shared restore/doctor validator checks indexed identities, known parents and frozen owners.
Retained editorial/inline context corroborates refs; absent original payload remains explicitly
unknown and is not recreated. Edit eligibility remains a later application check. Senate keeps its JSON
and dispatcher. The first new-format row requires a compatible reader for rollback: older readers
may restore its SQL but cannot consume that work. Reader deployment alone creates no such rows.

The prospective producer admits only printed Sejm corrections and their exact historical owner.
Snapshot values are frozen before request preparation and persisted with the intent; identical
bill/change snapshots deduplicate existing editorial history. Collection normalizes the original
source URL before retaining the paid record, then commits result/memo/usage/consumed and small
application work together. Its consumed metadata replaces the full revision target with a work-key
reference checked by restore and doctor. Unsupported legacy targets keep their paid result and
original metadata with an explicit application problem; they do not acquire transferable work.
Local application drains before polling and after collection, including without an open batch or
provider. Its separate transaction creates QUEUED publication, target receipt and completion;
delivery remains later. Changed physical targets block without rebasing, and primary skip/reset
does not rewrite the historical correction. Original SENT identities remain unchanged.
After the first handoff, runtime rollback must retain work-aware pending/drain behavior or stop
producers and commands until outstanding work is reconciled; v1 read compatibility alone is insufficient.

`content_snapshots`, `content_versions` и `publication_content` не являются operational cache
и не очищаются вместе с sent delivery JSON через 90 дней или settled memo через 180.
Совпадающий frozen Bill хранится один раз и переиспользуется revision; change и plan имеют
собственные identities. На переходе прежний delivery payload ещё сохраняется по своим правилам,
поэтому пиковый размер выше устойчивой истории; измерения публикуются в
[J109](plans/2026-10-22-document-architecture-progress.md#j109).

История относится к новым одиночным amendments status updates/revisions и к доступному original snapshot
при явном `/amendments`. Retention не заполняет пропуски legacy, не обращается к сети и не
заменяет прошлый Bill сегодняшним. Сохранённая version не подтверждает редактирование и не
снимает pending/unknown barrier. Grouped/multipart/digest correction contract не расширен.
Raw archive/backend/retention policy оригиналов этим не выбираются.


Editorial Bill view не копирует `review_work`/`ready_analysis` с их незавершёнными входами.
Они остаются у operational владельцев и под прежними retention guards. Это ограничение истории,
не удаление из Bill, pending request или original delivery. Остальные status updates пока не
попадают в этот producer; расширение требует отдельной проверки состава/объёма snapshot.


Новые amendments targets используют сохранённый editorial view вместо повторного включения
старых unfinished work в transport metadata. Это не очистка original delivery или pending owner;
изменяется только новый target. Сериализованный provider request и исходная content identity
сохраняются. Измерение synthetic legacy case и exclusions —
[J112](plans/2026-10-22-document-architecture-progress.md#j112).


## Совместимость отчётов о чтении файлов из CLI

Приложение читает `runs.mode=cli_reads` с обязательным `cli_read_operation`:
`scan`, `reprefilter`, `analyze`, `explain`, `amendments`, `republish`, `forget`, `reset`
или `observe`. Эти девять CLI-команд регистрируют отчёт перед поиском дела и чтением файлов. Исторический JSON
не получает новое необязательное поле. Сохраняются существующие правила хранения, выгрузки, восстановления и очистки SQL;
такие строки не передвигают границу последнего успешного полного поиска новых дел.

CLI-отчёт описывает наблюдения общего загрузчика. Число обработанных дел, публикаций и расходы моделей
не измеряются: нули в полях по умолчанию не являются наблюдениями.
Расходы, состояние сервиса и показатели дайджеста исключают эти отчёты; списки прогонов показывают операцию
и измеренные счётчики загрузчика. Аудит чтения файлов выделяет CLI в отдельный необязательный раздел.
SQL-read-only show и читающие lookup API не получают записей. Поведение preview сохраняется:
неизвестное дело может попасть в БД через прежний lookup, но отдельного владельца диагностики у preview нет.
Прямые обращения к источникам и прочие несохранённые вызовы остаются вне наблюдения;
отсутствующие исторические метрики не подменяются нулями.

Вызывающая команда снимает счётчики загрузчика в `finally`, до закрытия Container и после завершения
сетевых workers. Полные body identities и URL/body receipts, отчёт и существующая очистка runs
сохраняются в одной короткой транзакции. Внешняя транзакция по-прежнему владеет rollback.
У CLI нет выдуманного Bill или Telegram command ID: корреляция использует настоящий `runs.id`.
Статус бизнес-операции не выводится из исхода чтения. Ошибка сохранения диагностики пишет только класс
ошибки в log и не заменяет исходную ошибку, код выхода или результат команды.
Прерывание до `finally` может оставить незавершённую строку; устойчивый удалённый checkpoint остаётся
обязанностью существующего daily job. Отчёт не добавляет notifier, provider calls или dispatch.

Поддержка чтения из `8a76a8e4` должна быть развёрнута до включения записи. После записи `cli_reads` она становится
минимальной версией для отката: старый `RunReport` отвергает новый режим. Откат к более старой версии
требует отдельного плана совместимого состояния; удаление истории не является автоматическим
шагом отката. Отключение записи сохраняет поддержку чтения и существующие строки.

J158 проверяет prospective URL policy перед новым loader receipt; complete body metadata
сохраняется независимо до этой проверки. Cache reuse не считается новой omission observation.
Source/selection graph privacy остаётся отдельным этапом; historical rows не переписываются.

## Физический инвентарь: совместимый reader (v62)

`physical_metadata` и `physical_captures` добавляют только чтение и явный repository API;
extractor, TextLoader, Container и вызывающие команды пока не записывают эти строки.
Это описание наблюдённых физических фактов, отдельно от candidate policy, admission,
оплаченного input и применения результата. `scope=observed` не подтверждает полный обход архива.

Закрытые записи сохраняют header universe, непосредственные parent/ordinal links, известные SHA
полных payload и порядок чтений. Original/runtime names и actual read ordinal остаются разными
полями. Непрочитанные тела и недоступные заголовки не получают выдуманных identities. Extras/comments
представлены длиной и SHA, без opaque bytes. Нет source text, raw store или backend promise.

Каждый capture принадлежит настоящему `artifacts.sha256`, с nullable actual extraction association.
Recipe хранится отдельно и остаётся доступным без extraction: реализации, source-file hashes,
Python/dependency versions и эффективные options/limits. Неизвестные legacy recipes не достраиваются.
SQL views раскрывают все сохранённые metadata refs для ownership/reachability проверок; getters,
save, doctor и scratch restore используют общий structural reader без повторного walker/classifier.

Лимиты относятся к UTF-8 JSON envelope вместе с recording timestamp: 32 KiB на metadata,
1 KiB на capture, 512 KiB на distinct transitive union capture вместе с recipe. SQL/index/WAL
занимают отдельное место. Partial identity связывает retained fields, owner, counters и известный
opaque full-observation SHA; неизвестный SHA остаётся unknown, partial не считается полным архивом.
Повторная запись сохраняет первое время; timestamps не входят в IDs. Автоматической очистки нет.

Старые ExtractionResult JSON/IDs не меняются. Будущий producer должен записывать настоящие новые
component hashes и явно проверенные audit-ID deltas, сохраняя business text/selection/model-input/IO.
Активация producer требует развёрнутого reader, populated predecessor restore и отдельной проверки
caller-owned failure finalization: публичный метод не возвращает sidecar после исключения.

J161 отделяет known physical results от admitted source graph: существующие pipeline/CLI finalizers
сохраняют все known-body-owned URL-free extraction results, включая blocked compositions. Known
warm-cache inventory остаётся known inventory, не новой download activity. Paid/business payloads
и historical rows не переписываются; specialized RCL/Senate graph admission — отдельный этап.
J162 добавляет только инертный header builder/scope, без extractor hooks. J163 сохраняет validated
первое время при exact-cap повторе; допустимый32768B старый envelope не получает новый timestamp.

Schema63 ввела совместимый reader owner-bound source omissions: hash-only evidence,
known-available physical parent manifest, explicit unknown source relation и immutable first time.
Backfill и новой очистки нет; это metadata history без обещания raw retention.
Подробный контракт: [Owner-bound omissions](reference/database.md#owner-bound-omissions-v63).

Reader64 сохраняет omission evidence незарегистрированного official source reference без создания
Bill: separate actual context/key и known-available parents; прежние records/retention неизменны.
После подтверждённого reader64 deploy J168 включает producer через actual invocation scopes:
ordinary/private explain, RCL letters/windows и Senate inspections. Body origins приходят из
конкретного вызова, не shared inventory; caller пишет parents/fact atomically с Clock и actual
input order. Scope failures не заменяют business exceptions и не публикуют неполные captures.
Available parent set не доказывает полный used-body set. Quiet repeat не увеличивает dump.
Контракт: [Official source owners](reference/database.md#official-source-reference-owners-v64).


J169 добавляет optional cooperative physical walk capture: actual ZIP/header/read/open/part
events и bounded partial-input bridge без новой схемы. Scope failures не меняют extraction
result/exception; pending telemetry не выдаётся за failed read. Default capture off, calling loader/
cache/SQL wiring отдельно. До активации нужны actual-consumer populated SQL/gzip и repeat gates;
per-root512KiB cap не ограничивает общий рост дампа. Raw/backend/retention этим не выбираются.


J173 сохраняет safe official descriptor перед IO и actual failed read до построения selection:
shared SourceCatalogSnapshot, actual owner/purpose/errorclass, unknown selection и known-available
parents. Caller atomically объединяет positive/privacy facts в input order; исходные ошибки и
успешные flows не меняются. Если descriptor не сериализуется в UTF-8, сохраняются actual full
ASCII view SHA/count/policy и честный сериализуемый prefix, вплоть до zero descriptors; новая
retention identity/partial coverage не выдаёт это за unsafe URL. Snapshot/attempt roundtrip
проверяется до публикации. Старый mandatory selection FK, raw/backend и retention неизменны.
