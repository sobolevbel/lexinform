# Bug registry

What has been wrong with this bot, what is wrong with it now, and — the part that earns the file —
**which kinds of mistake it keeps making**. Read the last section before hunting: almost every
defect found so far was the second or third instance of a shape already in the history.

A row is `open` until a test fails on the old code and passes on the new one. `candidate` means
the code reads wrong but nothing has yet been replayed over the corpus to say how often it bites:
a candidate is a lead, not a finding, and the first suspicion goes to the probe, not to the code.

**Rank** is what it costs the reader, never how hard it is to fix:

| rank | meaning |
|---|---|
| P0 | the channel states something false, or a bill is lost with no way back |
| P1 | a piece of news is lost or told twice; recoverable by hand |
| P2 | the reader is told the truth in a way that misleads or says nothing |
| P3 | the operator, the report or the log is wrong; no reader sees it |

## Open

Rechecked in full against HEAD, the production dump and the corpus on 21 Sept 2026. Four earlier
candidates (old #5, #10, #13, #15) did not survive the recheck and are dropped below the table,
not carried forward as rows — none of them was ever a live defect, so there is nothing to mark
Fixed either. Six rows moved to Fixed with a regression test each; the rest keep their number but
several descriptions are rewritten where the code moved under them.

| # | rank | state | module | what is wrong | found |
|---|---|---|---|---|---|
| 1 | P1 | measured | `services/tracking/linking.py::_inherit_card` | the alias publication it creates never copies `sent_at` from the original the way `publishing.py`'s and `wykaz.py`'s own linking paths both do, so the digest's `sent_at`-ranged week query never returns it — a druk that inherited its card this way is invisible to the digest for the rest of its life. Live in production (state dump, 21 Sept 2026): druk 2172, inherited from RCL/12405609, carries `sent_at=NULL` on its `new_bill` row while the original has a real timestamp | 2026-09-16, confirmed 2026-09-21 |
| 2 | P1 | candidate | `services/digest.py` (`build`/`_entry`) | no dedupe by `message_id` or `bill.linked_number` when a project gets its druk the same week its card was sent — both the original row and the inherited-card row land in the same week's range and render as two entries under two names. No live example yet: the one production case where a card was already `sent` before linking is exactly druk 2172 (#1), whose `NULL sent_at` happens to keep it out of every week's range and mask this bug for that row | 2026-09-16 |
| 4 | P1 | candidate | `services/tracking/posting.py::posted` | `posted()` treats `PENDING` and `UNKNOWN` as "already posted" and neither is retried automatically — this is now a documented trade-off ("never blindly resend an ambiguous delivery", `docs/process-plans.md`), not a silent one: a crashed run's stale `PENDING` rows are swept to `UNKNOWN` at the start of the next run and named in `report.errors` (`pipeline.py::_report_stale_publications`), with `/republish`/`/analyze` as the operator's manual recovery path. The original claim's `--no-publish` half is gone: `tell()` under `publish=False` now queues (`Told.QUEUED`, see #22 in Fixed), which `posted()` does retry | 2026-09-16, revised 2026-09-21 |
| 8 | P0 | measured | `services/tracking/wykaz.py::_removed` | reads absence from the register as a withdrawal, guarded only against a wholly empty CSV (`adapters/wykaz_csv.py`'s `parse_register` drops unparseable rows silently, one row at a time, no count check against the expected total). Measured on the real 1,453-row register (21 Sept 2026) truncated to 60% of its bytes — a plain connection cut, no malformed content: it still parses without error, 915 of 1,453 rows survive, and **143 of the 296 live open bill-kind entries vanish and are persisted as `Wycofany`** (e.g. UD464, a real, still-planned track-law project), confirmed end-to-end through the production `_removed` path | 2026-09-16, measured 2026-09-21 |
| 11 | P2 | candidate | `models/events.py` (`_EVENT_BY_STAGE_TYPE`) | still has no `Voting` entry, so a change whose only new node is a vote would post under the bare «Обновление» — true of the code, but measured against all 6,260 stage transitions of term 10 it has never once happened: `Voting` is always a child of the `SejmReading` that decided it, and the reading's own `decision` is part of `_stage_key`, so the parent moves and names the post first every time. (The 149 transitions that did post as bare «Обновление» are all a single `Opinion` node, an already-accepted case, not this one.) Latent, not reproduced in twelve years of real data — worth the one-line fix on its own merit, not urgency | 2026-09-16, measured 2026-09-21 |
| 12 | P2 | candidate | `services/tracking/service.py:422` (Sejm only) | `has_news`/`fills_in_the_past` gate the Sejm path alone; RCL, wykaz, pre-print, rollover and linking do post every `StatusChange` they record unconditionally — true, but for four of those five sources this is provably harmless and not just untested: wykaz, pre-print and linking each only ever fire on an edge-triggered, at-most-once state flip (`SourceOutcome` change, a term rollover, a one-time print↔predecessor link), so there is no same-run multi-stage reordering for a guard to catch. **RCL is the one real exception** — it still carries a genuine stage tree (`diff_stages` in `tracking/rcl.py`) and could in principle hit the same same-day reordering `fills_in_the_past` exists for, the way 521 of 938 term-10 Sejm processes do; unmeasured on the RCL side, the one part of this row still worth a corpus check before deciding whether to extend the guard | 2026-09-16, narrowed 2026-09-21 |
| 14 | P0 | measured | `models/phases.py::_phase_of` | the `not bill.stages` fallback to `_pre_print_phase` fires for *any* numbered druk whose stages were never saved, not only a true pre-print — discovery never calls `save_stages` for a row skipped at the text prefilter, so it stays `stages=()` indefinitely. Measured live on the production dump (21 Sept 2026): **60 of 129** numbered term-10 bills have empty `stages_json`, all `skipped_text_prefilter`, and `next_phase` on every one of them answers `Phase(key="pre_print")` — «ждём номер druku» for a bill that already has one. Reachable via `/show`/`/preview` only: such rows are never publish candidates, so no reader of the channel sees it, which is why the rank stays below P0's own bar despite the state — raised to `measured` and the exposure corrected from "a numbered druk" (rare-sounding) to "roughly half of all not-yet-read numbered bills" | 2026-09-16, measured 2026-09-21 |
| 16 | P2 | candidate | `services/tracking/posting.py` (`prepare`/`prepare_message`/`_send`, three call sites) | a reply falls back to a top-level post when `card.message_id` is unavailable, so a thread reply can appear in the channel as a rootless message — still true, and wider than "the bill has no card": `card()` returns *any* `NEW_BILL` publication regardless of status, so a card that exists but is only `QUEUED` or `FAILED` (not yet `sent`) has `message_id is None` too and triggers the same fallback | 2026-09-16, widened 2026-09-21 |
| 17 | P3 | candidate | `services/publishing.py::_safe_print` (now `:478-483`) | swallows `ServiceUnavailableError` while every other `except Exception` in the chain re-raises it first — an outage is still recorded as a per-bill failure | 2026-09-16, line confirmed 2026-09-21 |
| 18 | P3 | candidate | `models/events.py::supplement_event`/`_EVENT_TAG` | `impact_assessment` still gets a header and an icon but no searchable event tag, while `government_position` gets one via an explicit check in `event_keys` | 2026-09-16, confirmed 2026-09-21 |
| 19 | P3 | candidate | `adapters/telegram_format.py::EVENT_ICON` | `tribunal_ruled` still has no entry (`tribunal` does, ⚖️) and falls back to the generic 🔄 (`EVENT_ICON.get(event, ICON["update"])`) | 2026-09-16, confirmed 2026-09-21 |

Dropped, not carried forward — investigated and did not reproduce, so there is nothing to mark
Fixed: old **#5** (`create_publication`'s UPSERT has no general "never downgrade `sent`" guard, but
its only caller writing `IN_FORCE`/`SKIPPED` — `acts.py`'s `list_due_in_force()` loop — already
excludes any bill with a settled post at the SQL level, `_NO_SETTLED_POST`; not reachable today,
worth a defensive guard on its own merits but not an active defect); old **#10** (a print adopted
with no alias when the predecessor's card was missing/unsent does end up uncarded for exactly one
run, not for good — `_NO_CARD_YET` picks the row up as an ordinary publish candidate on the next
run, confirmed with a full three-run scenario, both tags present on the eventual card); old **#13**
(the literal key `rcl_sejm` never existed in the codebase — the real derivable key `rcl_to_sejm` is
fully covered in all four label tables, and the other path that could in principle emit
`f"rcl_{group}"` can never do so with `group=="sejm"` because the `sent_to_sejm` branch always
intercepts first — checked against all 825 corpus RCL projects, zero anomalies); old **#15**
(`_DERIVED_PRINT_STAGES` omitting `PresidentMotionConsideration` changes nothing — checked all 15
real nodes of terms 8-10 against the raw API JSON, not one carries a `printNumber` to derive from,
and 0 of 4,462 committee/plenary sittings would have matched through this branch either way).

## Fixed

| # | rank | module | what was wrong | how it was found | fix |
|---|---|---|---|---|---|
| 23 | P0 | `models/events.py`, `adapters/telegram_format.py` | a pending veto was called an adopted act; correcting the headline alone left the false claim in the body | run 79, 2111, change 10 with zero new stages; `test_veto_evidence.py` | shared explicit veto outcome; no adoption claim without the corresponding evidence |
| 24 | P1 | `services/tracking/service.py` | an old closure became news on the first tracking pass | `test_late_discovery.py`, including a same-day initial closure and delayed tracking | v24 persists the observed closure separately from discovery metadata; analysis/linking seed it, tracking advances it |
| 25 | P2 | `services/sources.py` | a late-discovered bill was analysed from the original print, then immediately from its adopted text | real 2111 snapshot, workers 1/4, one initial run and an identical repeat | initial selection uses `latest_text_document`, just as tracking does |
| 26 | P0 | `models/events.py`, `models/phases.py` | a motion with no/unknown decision meant “veto overridden” and started the signature deadline | `test_veto_evidence.py` | pending is a separate `VetoOutcome`, shared by event and next-step logic |
| 27 | P0 | `models/events.py` | a committee's recommendation to reject was evidence of a Sejm rejection | `test_veto_evidence.py` | only a reading decision proves rejection; otherwise the closure reason stays unspecified |
| 28 | P2 | `adapters/telegram_format.py` | any `closure_detected` removed next-step guidance, including III reading with the Senate/President still ahead | `test_veto_evidence.py`, `test_format_updates.py` | check whether a next phase exists before suppressing it |
| 29 | P3 | `services/cost.py`, `models/report.py` | itemized call costs omitted cache reads/writes although the run total included them | `test_cost_ledger.py` | persist both cache counters on each call; old records default to zero |
| 21 | P1 | `services/tracking/hearings.py` | the reminder read the bills as `check_updates` loaded them, before this run's own stage loop stored the `PublicHearing` node, so a hearing announced today was told twelve hours late — on a window art. 70b measures in ten days | `checks/32_idempotence.py`: 9 bills of the 830 of term 10 were told only by a second run on the same data | each row is read again, the idiom `DeadlineReminder` already used for the same reason |
| 20 | P0 | `adapters/sqlite_repo.py` `list_tracked` | a passed bill with no act left `list_tracked` 180 days after `closure_date`, which the Sejm sets at the third reading — druk 210 of term 9 was dropped two days before the Sejm overrode the Senate, so the override, the hand-over, the signature and the act were never told and the card stayed at «Сенат отклонил закон» over a law in force | `checks/31_tracker.py`: the only bill of 3,266 across terms 8–10 to lose a stage; 1 of 2,380 bills with a closure date | the wait ends with the act and nothing else, so the passed-with-no-act arm takes `pending_decision_max_days` and `track_passed_max_days` is gone; measured cost, one extra bill of term 10 |
| 3 | P1 | `services/tracking/posting.py`, `rcl.py`, `wykaz.py` | `record_change()`'s `None` on a `change_key` collision was read as "nothing to do" by all seven callers while the fingerprint and `save_rcl`/`save_wykaz` had already been written outside the transaction, so a genuine change could be lost for good on a collision | `test_process_plans.py`, `test_delivery_checkpoints.py` (30 tests) | `823e9d8`: the fingerprint/stage save and `record_change` now commit inside one `self._repo.atomic()` block, so `None` only ever means "an earlier attempt already recorded and delivered this exact change" |
| 9 | P1 | `services/tracking/rollover.py` | every unfinished row was discontinued whether or not its end-of-term announcement (`_announce`) succeeded, so a bill whose announcement threw was discontinued, unannounced and outside `CardRefresher`'s reach (`list_tracked` filters on `discontinued_at`) | code reading against `823e9d8`/`b010b93` | discontinuation and the announcement's delivery plan now commit in one `self._repo.atomic()` block — a failed announcement rolls the discontinuation back with it, there is no half-finished state left to freeze on |
| 6 | P1 | `models/sejm.py`, `models/evidence.py`, `models/planning.py` | `_stage_key` excludes `position`, so a `SenatePosition` whose position text arrived later moved no fingerprint and was never told | `test_process_plans.py::test_senate_position_arriving_late_is_news_even_with_the_same_stage_identity` — fails reverted to `3048222` (0 updates instead of 1), passes on HEAD | `_stage_key` itself is unchanged; `decision_changes`/`decision_fingerprint` (`evidence.py`) fold a Senate-outcome change into `plan.new_stages`/`decision_changed` in parallel, bypassing `fills_in_the_past` and moving `change_key` even when the stage identity does not |
| 7 | P0 | `models/phases.py` | `_phase_after` fell through to `return None` for `Referral`, `Voting`, `CommitteeReport` and a non-trailing `End`; `next_phase is None` read as "road over" everywhere else in the codebase, so the bill got no card and a followed one froze | `tests/unit/test_phases.py::test_an_unrecognised_last_stage_reads_as_unknown_not_over` — fails reverted to `3048222` (`None`), passes on HEAD (`decision_unknown`) | fallthrough now returns `Phase(key="decision_unknown")`, the same "unknown must not read as closed" rule incident 2111 forced elsewhere; measured over all 5,533 stage trees of terms 8-10 the exact scenario never actually occurred (0 instances, `End` only ever appears top-level as a legitimate veto-sustained close) — the fix is general and closes any future/unmodelled `stage_type` too, not only the four named |
| 22 | P1 | `services/tracking/service.py` | a substantive change detected by a run with publishing off was held and released only by a later post, so a bill that went quiet afterwards never told it — 11 of 830 term-10 bills lost their referral to the first reading this way (`checks/32_idempotence.py`, 15 Sept 2026) | `test_process_plans.py::test_queued_news_is_delivered_without_waiting_for_another_event`; `checks/32_idempotence.py` rerun on HEAD, 21 Sept 2026, over 3,036 processes of terms 8-10: **0** processes now lose a post this way (was 11 of 830) | not the patch the row proposed (a hold that remembers why, or a fingerprint that does not move without publishing) — a third path: `publish=False` now queues a durable `DeliveryPlan` (`Told.QUEUED`) instead of an editorial hold, drained by `list_due_deliveries` on any later `publish=True` run regardless of whether the bill produces another legislative event first |

### 20. A passed bill was dropped while its road still ran

Replaying every process of terms 8–10 through the real tracker one day at a time
(`checks/31_tracker.py`) told 21,123 posts over 3,266 bills and lost exactly one bill's worth of
road. Druk 210 of term 9:

```
2020-02-14 SejmReading  III czytanie «uchwalono»   ← closureDate
2020-03-13 SenatePosition
2020-08-14 SenatePositionConsideration  «odrzucono uchwałę Senatu»   ← +182 days
2020-08-14 ToPresident
2020-08-25 PresidentSignature
```

`track_passed_max_days` was 180 days from `closure_date`, so the bill left `list_tracked` on
2020-08-12 and nothing after that was read. The reasoning for the fix was already in the
neighbouring test: "`closure_date` is set at the third reading, long before the President sends the
act on; dropping the bill at 180 days loses the Dziennik Ustaw notice for good" — it had only been
applied to a veto and a referral to the Tribunal.

| | before | after |
|---|---|---|
| bills of terms 8–10 losing a stage | 1 | 0 |
| bills of term 10 polled past day 180 | 42 (all held by the veto clause) | 43 |

### 23–29. Late discovery, false decisions and duplicate cost

See [the incident review](incident-2111.md) for the production evidence, exact spending,
architectural diagnosis, migration and the staged redesign. The false historical reply was deleted
by hand before the code fix shipped — a code fix does not edit old Telegram replies, so this was
never going to be automatic, and by the time of the fix it no longer needed doing.

## Classes we have already had

Each line is a shape, not an incident. When a probe finds one instance, look for the others: every
entry below was found at least twice.

- **A fallback picked the wrong file.** `main_pdf` fell back to the first PDF when `{number}.pdf`
  was absent, so druk 2821 was judged on a one-page "no remarks" scan from the NBP (`9439b06`).
  `consultation()` looked only in "Pisma kierujące", so a project filing its letter elsewhere was
  carded with no deadline and no address (`a87386d`).
- **A constant table missed a member.** `rcl_opinions` was the one of four RCL groups with no
  `PHASE_PATIENCE` entry — and it is the fallback, so the commonest government step used the
  "nobody measured this" default (`1d6f1ac`); `rcl_consultation` was missing too (`4e1f17d`).
- **A key collided, or an index was never taken.** The digest row carried the current kadencja
  while its unique index carries no term, so a term moving between two drafts of one week hit a
  bare `assert`; and `ix_pub_sent_at` was never used because SQLite preferred `ix_pub_status`
  (`5e94db9`).
- **A guard asked a question the data could not answer.** `_has_drafts_channel` guarded on a value
  that falls back to a placeholder and is never empty, so with no technical channel the first
  Sunday would have sent every reader the unapproved draft and its publish button (`5e94db9`).
- **A watermark used as a relevance gate.** `Data publikacji` never moves on an edit, so an entry
  rewritten into relevance stayed behind the watermark for ever and 12 live projects were
  invisible to both sources (`ef77725`, `3caac74`).
- **The last item taken for the representative one.** `with_text` read only the newest reached
  stage's catalog, and the stage a project ends on carries the covering letter and no bill — nine
  projects were closed with "no document to read" (`dfd08b3`).
- **A "not yet" recorded as a verdict.** Druk 3094 was judged five minutes before its PDF
  appeared, and a text skip is reopened only by a title change (`c7e4f6d`).
- **A whole record dropped by a naming assumption.** A folder naming bill and uzasadnienie in one
  file, or filing the bill as "Załącznik nr 1", yielded no bill text at all (`c7e4f6d`).
- **An emptiness standing in for "unread".** `refresh` kept any stage whose modification date had
  not moved, so a project read through one catalog left every other catalog closed for the row's
  life; 1,098 of 4,307 reached stages legitimately have no folders (`0bb1c0b`).
- **The same thing announced twice.** A plenary sitting named twice in one line (`5e94db9`); druk
  1929's second post of the day announced the cause of the first, seven of its eleven lines a
  repeat (`db8bd85`).
- **A conditional stated as a fact.** A committee's `notes` was read by nothing, and 21 sittings
  of term 10 happen only if the Sejm refers something first (`b306266`).
- **A success value indistinguishable from a failure.** `DigestResult.note` is empty on success,
  so `note or "posted"` reported a send that never happened (`5e94db9`).
- **A promise broken by a second path.** `/skip` promises no re-analysis; `reprefilter` re-scanned
  the silenced rows anyway and put two bills back in the paid queue (`5cc639d`).
- **A flag nothing in production could ask for.** `reprefilter --include-text-skipped` was never
  passed by `daily.yml`, so the skip that most needs revisiting never was (`ebd44a4`).
- **An exception raised above the guard meant to protect it.** The guard was around
  `notify_backfill`; the throw was one line earlier, and 36 minutes of scanning were lost
  (`1420f50`).
- **A parallel phase reimplemented sequentially.** The backfill had its own loop where the phase it
  mirrors fans out — 110 bills in 1791 s, five downloads overlapping (`7fd6686`).
- **A cap that silently dropped data.** Week 37 carried 14 cards; the digest showed ten and said
  nothing about the four (`5e94db9`).
- **An off-by-one week.** `current_ref` assumed the digest day ends the ISO week, so a Monday
  digest would have drafted the week that had just begun (`5e94db9`).
- **A cost measured on the wrong model or sample.** $0.013 was Haiku on eight pages; production
  triages on Sonnet and a 197-page print costs $0.044 (`b4c94e2`).
- **A page break that is not a line break.** `^`/`$` are blind to `\f`: 279 prints reached the
  model with no uzasadnienie (audit 1, defect 1), and the same blindness cut 18 signatures to a
  bare first name (audit 2, defect 2).
- **A doc invariant stated backwards.** CLAUDE.md said the print's detail is authoritative about
  attachments; the observation was right and the conclusion inverted (`9439b06`).

## Where the probes live

The replays that find these are in the corpus, not here:
`~/projects/lexinform-corpus/checks/`, run from this checkout as
`uv run python ../lexinform-corpus/checks/NN_name.py`. `checks/README.md` holds the method,
`FINDINGS.md` and `COLD.md` the two audits of 14 Sept 2026. The registry is the repository's copy
of what they concluded, because the corpus is not a git repository and does not travel with the
code.
