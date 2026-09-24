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
| Batch payloads | 90 | Remove source text, source snapshot and answer bodies only after consumption, token accounting and provider cleanup are all older than the cutoff; the owning bill must be settled, without a ready analysis or deferred tracking. Keep item/batch rows and token/model/error metadata. |
| Analysis memo | 180 | Delete entries not used since the cutoff only when ownership is known and the bill is settled. Pending analysis, ready results, deferred tracking, unsent/uncertain publications, queued/submitting intents and unconsumed/unaccounted batch items protect that bill's memos. |

Memo writes and reuse on the calling thread refresh `last_used_at` without replacing the original
paid result. Migration v33 adds nullable ownership and last-use metadata. An old hash alone cannot
reliably identify its bill: legacy entries stay protected until a subsequent save/reuse supplies
that information. There is no guessed ownership or expiration date.

Settings: `LEXINFORM_RUNS_RETENTION_DAYS`, `LEXINFORM_HISTORY_PAYLOAD_RETENTION_DAYS`, and
`LEXINFORM_ANALYSIS_MEMO_RETENTION_DAYS`. Minimum: seven days. Reducing a cutoff takes effect on
the next run. An expired, unneeded cache entry may incur a new LLM call if its old context is
explicitly revisited later; expiration itself never initiates analysis.

Bill identities, legislative evidence, publication history, command IDs, batch accounting and
unknown operations are not deleted on a timer. Thus the payload history is bounded for completed
work, while the lightweight ledger and genuinely pending work can continue growing. This is not
a hard cap on the entire database. Old Git commits remain available; pruning the current snapshot
does not rewrite repository history.
