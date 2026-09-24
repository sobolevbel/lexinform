# Incident 2111: false adoption, duplicate analysis and the reliability boundary

Статус: исторический разбор инцидента 16.09.2026.
Действующая архитектура — [process-plans.md](process-plans.md), дефекты — [в реестре](BUGS.md).

Run 79, 16 September 2026, 19:35:36–19:39:18 UTC. Evidence: state commit `30bc17e`,
[Actions run 35141456953](https://github.com/sobolevbel/lexinform/actions/runs/35141456953),
running code `2b972cf`. Investigation used restored copies of state and offline fakes;
no production state was edited and no model calls were made.

## What readers received

For term 10, druk 2111: card 53, agenda reply 54, status reply 55. This was one card and two
replies, not three cards. The agenda was a separate real event; reply 55 falsely said the
Sejm had adopted the act. Its persisted change contains **zero new stages**,
`closure_detected=true` and `content_changed=true`.

The process was adopted at III reading on 29 May, vetoed on 17 July, and the committee filed
its recommendation on 16 September. There was no `PresidentMotionConsideration` stage in
the incident snapshot. The complete normalized snapshot is retained in
`tests/fixtures/sejm/process_2111_incident.json`.

## Why it happened

1. `SejmTextSource.locate` selected the original print, while `newer` selected the adopted
   text. Discovery at a late stage therefore started with obsolete material, published it,
   and immediately paid to supersede it in tracking.
2. Initial analysis seeded the stage fingerprint but not an observed closure baseline.
   Tracking treated any unannounced `closureDate` as a new event. Discovery also overwrote
   the summary before tracking, so comparing two summaries could not fix the problem.
3. The event classifier inferred adoption from `passed and not veto_stood`. The negative
   predicate includes both an overridden veto and a veto not yet voted on.
4. The formatter independently interpreted the same flags: it could print adoption in the
   body even after the header was corrected, and treated every closure as the end of the
   whole process, removing the next step and participation window.

## What the money bought

These are the recorded per-call prices under this checkout's price table, excluding cache
charges (the old per-call records did not retain them):

| Call | Input tokens | USD |
|---|---:|---:|
| 2111, original print analysis | 393,898 | 1.989765 |
| 2111, adopted text reanalysis | 348,525 | 1.769725 |
| 2110, separate bill analysis | 47,073 | 0.259015 |
| Three triage calls combined | 32,569 | 0.070968 |
| Cache reads/writes, run total | 2,496 / 4,992 | 0.032448 |
| Total | | **4.121921** |

The original 2111 PDF is 348 pages: extraction produced 800,842 characters, trimming removed
40,768 characters of OSR sections and sent 760,102 characters. The adopted text contained
664,717 characters and was sent whole. At the configured $5 per million input tokens,
348,525 input tokens alone cost $1.742625. This is mostly input cost, not reasoning output.

Triage on both 2110 and 2111 returned `affects_foreigners=false` but confidence 0.75, below
the configured 0.8 rejection threshold. Both correctly proceeded as uncertain cases; lowering
that threshold to save this bill's cost would risk suppressing a relevant project.

The $2 guard limits estimated **input per analysis**, not total spending per project across
phases, and the run limit is $15. Neither limit should have stopped these two calls. The cache
covered the small prompt prefix, not the hundreds of thousands of document tokens.

Selecting the current text initially removes the obsolete $1.99 call. A necessary complete
analysis of this unusually long adopted text still costs about $1.77 at this run's token
volume. The new run's exact bill cannot be established without another paid model run.
The per-call ledger now retains cache usage so future itemized costs reconcile with totals.

## Changes made

- Initial analysis and tracking choose the current text with the same `latest_text_document`
  rule. No block on all same-run reanalysis: a real new revision arriving mid-run must remain
  detectable.
- Schema v24 adds `observed_closure_date`, owned by analysis/linking/tracking. Discovery cannot
  move it. Existing stage snapshots are backfilled so deploying this fix creates no old closure
  announcements. This historical observation cannot be derived from the overwritten summary.
- `VetoOutcome` distinguishes pending, overridden and sustained; missing evidence cannot start
  the seven-day signature deadline. Header and deadline logic share the interpretation.
- Committee rejection recommendations no longer prove a Sejm rejection.
- Closure wording cannot contradict a pending veto, and an ongoing process keeps its next step.

## What is fundamentally wrong

The dependency direction and ports are useful. The weakness is that there is no single domain
decision between external observations and side effects. The same facts are interpreted in
`events.py`, `phases.py`, source selection, tracking and formatting. `None`/`False` frequently
collapse “unknown”, “not yet”, “not applicable” and “finished” into one value.

`Bill` also combines the latest source metadata, the last processed observation, analysis and
publication state. Different phases own different pieces without one atomic boundary. The
closure defect is one instance; the held-publication issue #22 is another boundary to repair.

Tests can assert that a change was recorded or that a reply was sent without independently
checking the truth of its headline, document provenance and permitted next step. The corpus
tracker replay starts processes near their beginning, so it never covered the late-discovery
sequence that failed here. A corpus whose labels are produced by the implementation is a
regression sample, not an independent oracle.

## Proposed next architecture

The first implementation, verification and compatibility boundaries are now described in
[Process observations and delivery plans](process-plans.md). The proposal below records the
direction; it does not claim that every source watcher has already been migrated.

1. **Normalize evidence once.** Interpret source snapshots into explicit process facts:
   known decision / pending decision / unavailable evidence, with the source stage attached.
   A completed process must require positive terminal evidence; `next_phase=None` must not
   double as “unknown, therefore closed”. Migrate one domain slice at a time, starting with
   presidential/Senate decisions and document identity.
2. **Keep observations separate.** One immutable `ObservedProcess` represents what was
   processed. Discovery writes candidates and fresh source data without mutating that baseline.
   A pure comparison of old and new observations produces typed events. Initial observation
   produces an introduction, not a replay of the entire history.
3. **Plan before spending or posting.** Produce one `BillPlan` containing selected document
   revision, required analysis, evidence-backed events and intended messages. All messages use
   the same observation and analysis revision. Memoize analysis by bill identity, normalized
   content and analysis purpose; URL changes alone never make a paid job.
4. **Commit the checkpoint and delivery work together.** In one SQLite transaction persist
   the processed observation and a durable publication plan. Keep pending-before-send and
   never blindly resend ambiguous pending/unknown deliveries. Distinguish “held because no
   news” from “waiting because delivery disabled”; retries must retain the message's original
   factual snapshot, rather than rendering an old event against a later `Bill`.
5. **Make independent expectations the release gate.** Store real incident snapshots and
   expected events, allowed headlines, document URLs and call counts. Exercise cold discovery
   at every stage, missing decisions that arrive later, source outages, failed delivery,
   retries, unchanged input twice, and workers 1/4. Assert visible Russian text as well as
   counters. During migration compare old/new plans offline; then use a shadow planner with
   no LLM calls or publications before making it authoritative.

This is an incremental domain refactor, not a new framework or a simultaneous rewrite of all
watchers. Each replacement needs an explicit compatibility boundary and a replayable case.
For cheaper long-document analysis, independently evaluate article-level selection plus
cross-reference coverage on this corpus before lowering the input cap or changing models.

## Validation and remaining limits

The final local gate passed: 1,172 tests (9 live integration tests deselected), strict mypy,
ruff check and ruff format. CLI tests used only their localhost stub server.

Regression cases fail on the old code, including the complete 2111 snapshot; they cover both
worker counts, delayed initial tracking, a real later veto vote, unknown decisions, misleading
committee proposals, closure rendering and cache-cost accounting. Legacy and v23 dumps migrate
to v24; a fresh production dump restores and reads 2111 with baseline 2026-05-29.

The corpus tracker replay covers 3,266 bills in terms 8–10: no lost stages, phase crashes or
counter mismatches. It still flags 116 repeated stage identities, the same count as the earlier
stored audit; that probe drops parent context from identities, so this is not evidence of 116
duplicate Telegram messages. One process remains cardless in that probe. These existing flags
are not silently marked fixed, and the other candidates in BUGS.md remain open.

Production has no pending/failed/unknown publications, exhausted analysis retries or relevant
score-qualified analyses without sent cards at this snapshot. The incorrect message 55 is
already sent and **is not repaired by a code deployment**. A maintainer must edit/remove it or
publish a correction explicitly stating that the veto vote had not happened. `/refresh 2111`
updates the main card and process; it does not repair historical reply 55. The CLI has no
historical-reply correction command, so recommending `/republish` would just create more noise.
