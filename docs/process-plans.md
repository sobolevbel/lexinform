# Process observations and delivery plans

Implemented after [incident 2111](incident-2111.md). Source watchers separate source metadata,
processed observations, analysis work and delivery. Sejm, RCL, RPW, wykaz, linking, agendas and
term rollover commit an observation and its delivery plan in one SQLite savepoint.

## Evidence

`models/evidence.py` interprets presidential and Senate decisions. `DecisionEvidence` retains
the source stage and distinguishes absent evidence, a pending decision, an unrecognised or
missing decision, and a known result. Event headers and next-step calculations share this
interpretation. Unknown Senate decisions cannot imply amendments, acceptance or a presidential
signing deadline; the Russian message explicitly says that the decision is unavailable.

`is_over` requires positive terminal evidence. Neither an unknown next step nor an ELI address
without entry-into-force information ends the process. Discovery no longer permanently
discards an act whose ELI record has not arrived yet.

Senate positions can arrive after their stage. Semantic decision changes are compared
separately from stage fingerprints: rendering enrichment cannot create news, but a newly known
Senate decision can. Ordinary stage identities and deployed fingerprints remain compatible.

## Observation and planning

`ObservedProcess` is immutable: processed stages, closure, decision flag, analysed document
and revision, and known supplements. Discovery updates source metadata without moving this
baseline. Analysis seeds it; tracking advances it with delivery work; linking seeds a successor.

`models/planning.py::plan_bill` is pure. Old observation and source snapshot produce a
`BillPlan`: stage/decision differences, closure detection and the document that needs checking.
First sight seeds an observation without replaying history. `BillPlan.should_publish` owns the
news/backfill distinction, including delayed decisions. It can run offline without a model or
publisher.

The Sejm tracker performs network work first, including analysis and enrichment. One SQLite
savepoint then stores metadata, stages, observation, status change and queued delivery or
editorial hold. Failure rolls the whole checkpoint back. Savepoints nest inside the dry-run
transaction, so dry-run rollback remains intact.

Analysis results are saved before the checkpoint. If a later write fails, comparison with the
old observation detects the analysed revision on the next run and still creates its event.

## Delivery

Publications store an immutable `DeliveryPlan`. Status updates serialize the bill and change,
including analysis and document provenance and the exact IDs of included held changes. Cards,
agendas, cancellations and reminders store the bill plus the event-specific facts used to render
them. The payload belongs to one channel. A retry cannot use newer facts, move to another thread,
change a deadline date or absorb later held stages.

- `queued`: durable work, no send started; the next publishing run drains it.
- `pending`: sending may have started; stale pending becomes `unknown`, never retried.
- `failed`: explicit failure, retried within the existing attempt budget.
- `skipped`: editorial hold, included in the next substantive update.
- `sent`: delivery and release of its exact held-change IDs commit together.

Disabled delivery queues detected updates, agendas and reminders, which no longer wait for
another legislative event to be sent. Non-substantive and historical additions remain held.
The existing silent seeding of **new cards** under `run --no-publish` remains unchanged.

RCL, RPW and wykaz changes build their delivery plans in the transaction that advances the
stored source snapshot. Linking a predecessor to its RCL project or druk commits the successor,
card alias, analysis and update plan together. Term rollover commits discontinuation and all its
update plans before any Telegram call. Model work remains outside these transactions; its memo is
stored first, so a checkpoint retry does not pay for the same analysis again.

## Analysis memoization

`analysis_memo` stores structured results, never document bodies. Keys include bill identity,
purpose, normalized text and context, including the previous summary used for a comparison.
A URL change alone cannot create a paid job. Amendment and supplement results survive failure
before the checkpoint. Reused results charge no new tokens; original provenance remains in
the memo. Forced full analysis bypasses memoization. Workers only read the run's memo snapshot;
repository writes remain on the calling thread.

A scanned input is memoized by the file's digest and the page window sent (`pages`,
`of_pages`), and a triage verdict under a key of its own, stored before a batch request is
queued. Filed-document scans use the same digest and page-window memoization. A crash after a synchronous call
answers but before its result is stored can still repeat a paid call; SQLite cannot atomically
commit a remote call. A batch answer is stored before it is applied (v29), so collecting it
again never pays twice.

## Migration and compatibility

Schema v25 adds `bills.observed_process_json`, `publications.delivery_json`, persisted
`status_changes.consultation_opened`, and `analysis_memo`. Previous migrations are unchanged.
Old bills adapt stored stages and the v24 closure baseline until their first new checkpoint;
deployment does not replay history.

Old failed publications have no historical payload. Their first retry captures available
facts once; overwritten metadata cannot reconstruct exact historical facts. Old skipped rows
retain their meaning.

## Verification

The local release gate passed: 1,212 tests, 9 live integration tests deselected, strict mypy,
ruff check and formatting.

`test_process_plans.py` covers restored queues, immutable retry facts, checkpoint failure after
paid analysis, unknown states, late Senate decisions, memoized amendments and ambiguous
deliveries. `test_delivery_checkpoints.py` injects failures into RCL, RPW, wykaz, linking, term
rollover and agenda checkpoints, and restores the database before retrying cards, reminders and
agendas. `test_late_discovery.py` exercises every stage of the incident sequence, the full
recorded 2111 snapshot, document selection and call counts, with workers 1 and 4. Legacy dump
tests cover existing versions.

A fresh production dump restored locally to v25; 2111 retained its 2026-05-29 closure baseline.
Offline shadow comparison of all 60 stored Sejm processes with known baselines produced no
new stages, closures or text changes from unchanged input. This checks migration compatibility,
not independent correctness of every historical decision. No LLM calls, live process replay
or production publications were performed for this check.
