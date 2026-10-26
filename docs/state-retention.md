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
| Primary computation history | — | New `analysis`/`reanalysis` batch collections retain one validated `AnalysisRecord` or failure receipt per provider item, original usage and one collection decision. This history does not expire with the memo or batch body. No input text, raw file, request body or raw provider response is added. |

Schema v49 starts primary history from newly collected items; existing completed results are not
copied or reconstructed. `AnalysisRecord.computation_id` points to that result; previously
completed legacy records and synchronous compatibility records omit it. A legacy pending item
collected now gets a known result ID while its absent original request checksum stays unknown.
The result ID names a provider/batch/custom item,
whereas `request_sha256` names its frozen input. Reuse retains the same result ID and does not
create another paid computation. Current provider identities have the repository's existing
single-account scope; they do not claim cross-account uniqueness.

`result_applications` separates **collection** (ready, superseded or failed) from **projection**
(applied by `AnalysisService._persist` or direct `reanalyze_bill`). A projection receipt commits
with `Bill.analysis`, records the target's generation/status before that write, and distinguishes
the ready batch result from memo reuse. A repeated receipt keeps its original timestamp.
Preparation and quiet source checks do not create projection receipts. Ready does not prove
application or publication, and applied does not prove delivery. Other tracking/linking consumers
and secondary purposes remain in [DA6](plans/2026-10-22-document-architecture.md#da6).
Result, collection decision, memo/ready changes and consumed checkpoint commit together;
run cost acknowledgement retains its existing mechanism.
The history preserves the paid record when a skip/reset makes it inapplicable. Immutable rows
reject replacement, and an exact repeated write is harmless.

This first migration temporarily retains the answer in both history and legacy bill/memo
projections. Removing those copies requires all primary consumers, frozen deliveries and
secondary purposes to use protected result refs; it is not part of this dual-write stage.
The measured sizes and scope are in [J061](plans/2026-10-22-document-architecture-progress.md#j061)
and [J062](plans/2026-10-22-document-architecture-progress.md#j062).

Batch metadata and collected records retain `request_sha256` after payload cleanup. It is copied
from the original frozen request by the shared submission/attachment constructor. An absent
legacy checksum stays unknown; cleanup and reuse never derive it from a fresh context. The hash
does not pin or archive the request body and does not extend memo retention.
The offline [request doctor](https://github.com/sobolevbel/lexinform/blob/main/tools/README.md#request-provenance-doctor) inventories these refs
on a checkpointed copy and reports corruption/unknowns without modifying state or retrying work.

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
explicitly revisited later; expiration itself never initiates analysis.

Bill identities, legislative evidence, publication history, command IDs, batch accounting and
unknown operations are not deleted on a timer. Thus the payload history is bounded for completed
work, while the lightweight ledger and genuinely pending work can continue growing. This is not
a hard cap on the entire database. Old Git commits remain available; pruning the current snapshot
does not rewrite repository history.
