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

Rechecked in full against HEAD, the production dump and the corpus on 21 Sept 2026, then fixed
with a regression test each where a fix was a mechanical, well-scoped change: #1, #2, #8, #11,
#14, #17, #18, #19 moved to Fixed below, each citing the test that fails reverted and passes on
HEAD. #12 was measured and closed without a code change — RCL turned out structurally immune, not
just untested (see Fixed). Four earlier candidates (old #5, #10, #13, #15) did not survive the
recheck and are dropped below the table, never having been a live defect. #16 moved to Fixed on
21 Sept 2026 too, once tracing its actual callers turned "somewhere inside `posting.py`" into two
named, reachable call sites. One row remains open on purpose: it needs a design decision, not a
patch, and forcing one under time pressure risked being wrong in a way a mechanical fix would not
have been. This paragraph describes the 21 Sept audit; the separate batch review below adds new
open candidates on 23 Sept.

| # | rank | state | module | what is wrong | found |
|---|---|---|---|---|---|
| 4 | P1 | candidate | `services/tracking/posting.py::posted` | `posted()` treats `PENDING` and `UNKNOWN` as "already posted" and neither is retried automatically — this is now a documented trade-off ("never blindly resend an ambiguous delivery", `docs/process-plans.md`), not a silent one: a crashed run's stale `PENDING` rows are swept to `UNKNOWN` at the start of the next run and named in `report.errors` (`pipeline.py::_report_stale_publications`), with `/republish`/`/analyze` as the operator's manual recovery path. The original claim's `--no-publish` half is gone: `tell()` under `publish=False` now queues (`Told.QUEUED`, see #22 in Fixed), which `posted()` does retry. What is left open is a genuine choice, not a bug: should a stale `PENDING`/`UNKNOWN` retry itself once the run reports it, or does an operator have to ask? Left as a design decision on 2026-09-21: no code change, but `/status` now names every `pending`/`unknown` post by bill number, kind and age instead of only counting them (`list_stuck_publications`), since `report.errors` only ever says what one run found — `/republish` clears one for a card (`NEW_BILL`/`JOINT_BILL`), and for every other kind there is still no command, only the row now being visible enough to ask for one. | 2026-09-16, revised 2026-09-21 |

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

### Batch architecture review — 23 Sept 2026

Reviewed `1cafa93..3b20456`, including the provider split, batch lifecycle and collect/poll entry
points. **All rows below are open candidates requiring verification, not fixes or claims of
production incidents.** Their mechanisms reproduce offline; incidence in production and the
complete recovery paths still need checking. Details, reproduction commands, limitations and
suggested remedies: [review report](reviews/2026-09-23-batch-architecture.md).

`P1`/`P3` retain the reader-impact definitions above. In particular, duplicated paid LLM work and
a bypassed spending guard are P3 here, although they deserve early engineering attention.
The two `test_batch_review_candidates.py` modules retain expected-behaviour assertions under
strict `xfail`; run with `--runxfail` to see the defects. Remove the marker only with a verified fix.

| # | rank | state | module | candidate and local evidence | found |
|---|---|---|---|---|---|
| 30 | P1 | open / candidate; needs verification | `services/analysis.py::_enqueue`, `submit_queued_batches` | Bills become `batch_pending` before durable submission exists. A rejected submission leaves no batch row and no automatic retry; another run submits nothing. Also inspect remote acceptance followed by a crash before saving the ID. [B30](reviews/2026-09-23-batch-architecture.md#b30). | 2026-09-23 |
| 32 | P1 | open / candidate; needs verification | `services/analysis.py::_consume` | A late answer unconditionally resets the bill's status. Reproduced: `/skip` an in-flight bill, collect its answer, and its card is published anyway. Check forced analyses, linking and other superseding actions too. [B32](reviews/2026-09-23-batch-architecture.md#b32). | 2026-09-23 |
| 35 | P1 | open / candidate; needs verification | `services/analysis.py::collect_batches`, `adapters/sqlite_repo.py::list_tracked` | A collected reanalysis only seeds memo and resets status; it does not schedule application independently of discovery. With a moved watermark, the collected bill is not tracked and retains its old analysis until a full check or new source change. [B35](reviews/2026-09-23-batch-architecture.md#b35). | 2026-09-23 |
| 37 | P1 | open / candidate; needs verification | `services/analysis.py::collect_batches`, `_consume`, `adapters/sqlite_repo.py::save_llm_batch` | Failed status is committed before item consumption; a crash then makes unfinished items invisible to `list_open_llm_batches`. Reproduced with injected failure. Batch registration and successful consumption also lack the atomicity their multi-write transitions need. [B37](reviews/2026-09-23-batch-architecture.md#b37). | 2026-09-23 |
| 38 | P1 | open / candidate; needs verification | `container.py::batch_backend`, `services/analysis.py::collect_batches` | The saved `batch.provider` is ignored. Switching to OpenAI polls old Anthropic IDs through OpenAI; a 404 is classified as failed and the paid work is abandoned. Reproduced with a restarted container and another backend. [B38](reviews/2026-09-23-batch-architecture.md#b38). | 2026-09-23 |
| 39 | P1 | open / candidate; needs verification | `adapters/llm_openai.py::poll`, `fetch_results` | Only `output_file_id` is read; failed items in `error_file_id` remain unconsumed. `expired`/`cancelled` batches are treated as wholly failed even when they hold successful results. Three offline contract probes fail. [B39](reviews/2026-09-23-batch-architecture.md#b39). | 2026-09-23 |
| 40 | P1 | open / candidate; needs verification | both batch adapters' result parsers | A malformed/truncated structured answer raises `ValidationError` through the whole iterator. Later valid items and later batches never reach consumption; the same poison row fails every collection. Reproduced for Anthropic and OpenAI. [B40](reviews/2026-09-23-batch-architecture.md#b40). | 2026-09-23 |
| 42 | P1 | open / candidate; needs verification | `adapters/sqlite_repo.py::move_government_rows`, `services/analysis.py::_consume` | Term rollover moves government bills but not their batch item references. Collection consumes the old-term item while the new-term bill stays `batch_pending`. Reproduced through the repository's public rollover operation. [B42](reviews/2026-09-23-batch-architecture.md#b42). | 2026-09-23 |
| 43 | P1 | open / candidate; needs verification | `cli.py::collect_batches` | The new entry point constructs `RunOptions` without configured `min_score` or run caps. Reproduced: `min_score=5`, completed score-3 answer, `collect-batches` publishes the card. [B43](reviews/2026-09-23-batch-architecture.md#b43). | 2026-09-23 |
| 31 | P3 | open / candidate; needs verification | `services/tracking/rcl.py::_detect`, `adapters/sqlite_repo.py::save_analysis` | Saving the unchanged old analysis resets `batch_pending` to `analyzed`. The next unchanged RCL check files another paid reanalysis while the first is still running. Reproduced; audit the same pattern in linking/wykaz. [B31](reviews/2026-09-23-batch-architecture.md#b31). | 2026-09-23 |
| 33 | P3 | open / candidate; needs verification | `services/analysis.py::analyze_pending`, `_enqueue`, `_persist` | The queued branch bypasses persistence of an accepted triage. Collection re-runs triage before reaching the full-analysis memo; one unchanged document incurs two triage calls. [B33](reviews/2026-09-23-batch-architecture.md#b33). | 2026-09-23 |
| 34 | P3 | open / candidate; needs verification | `services/analysis.py::analyze_pending`, `submit_queued_batches` | Batch requests reserve no run budget; the queued branch also bypasses the exhaustion check. With a negligible positive budget all three candidates are submitted and no stop is reported. Inspect estimate/model routing mismatches as part of the fix. [B34](reviews/2026-09-23-batch-architecture.md#b34). | 2026-09-23 |
| 36 | P3 | open / candidate; needs verification | `services/pipeline.py::_run_phases`, `services/analysis.py::_prepare` | Batch-enabled dry runs queue in memory but deliberately never submit: zero full analyses and no new-card preview. Reproduced through the dry-run container. This no longer tests the documented paid-analysis dry-run path. [B36](reviews/2026-09-23-batch-architecture.md#b36). | 2026-09-23 |
| 41 | P3 | open / candidate; needs verification | batch result parsers, `services/cost.py`, pipeline/report aggregation | Collected calls contain tokens but report totals contain zero; batch discount is absent from the ledger; parsers label answers with the collecting model/prompt version. Token mismatch and wrong model reproduced; discount/provenance propagation traced in code. [B41](reviews/2026-09-23-batch-architecture.md#b41). | 2026-09-23 |

## Fixed

| # | rank | module | what was wrong | how it was found | fix |
|---|---|---|---|---|---|
| 16 | P2 | `services/tracking/wykaz.py::WykazLinker.link`, `services/tracking/pre_print.py::_announce_withdrawal` | a reply was prepared and sent under a card that was only `queued`/`failed` (never `sent`), landing rootless in the channel — `card()` returns any `NEW_BILL` row regardless of status, and unlike the already-guarded `linking.py::Linker.link`, these two built the `StatusChange`/reply unconditionally; every other caller of a reply already checked `card.status is SENT` first | `test_wykaz_link.py::test_a_reply_waits_for_a_still_unsent_plan_card_instead_of_replying_to_nothing`, `test_tracking_pre_print.py::test_a_withdrawal_waits_for_a_still_unsent_card_instead_of_replying_to_nothing` — both fail reverted, pass on HEAD | both now gate on `card is not None and card.status is SENT`, mirroring `linking.py`; unsent, the wykaz row falls to the normal publish path (`_NO_CARD_YET`) and the withdrawal is retried whole on the next `/bills` read, so nothing is lost, only delayed to the run whose card finally sends |
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
| 1 | P1 | `services/tracking/linking.py::_inherit_card` | the alias publication it created never copied `sent_at` from the original, unlike `publishing.py`'s and `wykaz.py`'s own linking paths — the digest's `sent_at`-ranged week query never returned it, so a druk that inherited its card this way was invisible to the digest for the rest of its life. Live in production: druk 2172, inherited from RCL/12405609 | `tests/scenario/test_tracking_pre_print.py::test_the_inherited_card_keeps_the_original_sent_at_for_the_digest` — fails on the reverted file, passes on HEAD | `sent_at=card.sent_at` copied onto both the constructor and `mark_publication`, matching the other two paths |
| 2 | P1 | `services/digest.py` (`build`/`_entry`) | no dedupe by `message_id` when a project got its druk the same week its card was sent — the original row and the inherited-card row (same Telegram message, per #1's three aliasing paths) rendered as two entries under two names. Fixing #1 made this reachable on the very next digest instead of masking it, so the two had to move together | `tests/scenario/test_digest.py::test_an_inherited_card_appears_once_in_the_digest_not_under_two_numbers` — fails on the reverted file (`['RPW/29075/2026', '3100']`), passes on HEAD (`['3100']`) | `_add_or_replace` dedupes `cards`/`updates` by `message_id`; on a collision the later post wins, since its bill row is the one still current |
| 8 | P0 | `adapters/wykaz_csv.py::parse_register`, `services/tracking/wykaz.py::_removed` | absence from the register read as a withdrawal, guarded only against a wholly empty CSV; a response cut off mid-download parsed cleanly for the bytes it had, so the missing rows were never unreadable, they were just never in the text. Measured on the real 1,453-row register truncated to 60% of its bytes: 915 rows still parse, and 143 of the 296 live open bill-kind entries would be fabricated as `Wycofany` (e.g. UD464) | `tests/unit/test_wykaz_csv.py::test_a_response_cut_off_mid_download_is_an_error_not_a_smaller_register` — fails on the reverted file (no error raised), passes on HEAD | a complete download always ends its last row cleanly; `parse_register` now refuses one that does not, the same `WykazPageError` it already raises for an empty or column-less response |
| 14 | P2 | `models/phases.py::_phase_of` | the `not bill.stages` fallback to `_pre_print_phase` fired for *any* numbered druk whose stages were never saved, not only a true pre-print one — discovery never calls `save_stages` for a row skipped at the text prefilter. Measured on the production dump: 60 of 129 numbered term-10 bills are in exactly this state, all `skipped_text_prefilter`, and every one answered `Phase(key="pre_print")` — «ждём номер druku» for a bill that already has one, through `/show`/`/preview` | `tests/unit/test_models.py::test_a_numbered_druk_with_unread_stages_is_not_told_it_has_no_number` — fails on the reverted file (`pre_print`), passes on HEAD | `is_pre_print` already decides this correctly on its own, by number; the stages-based fallback is gone and a numbered bill with unread stages now falls to `_sejm_phase`, which already answers `first_reading` for an empty tree |
| 17 | P3 | `services/publishing.py::_safe_print` | swallowed `ServiceUnavailableError` while every other `except Exception` in the chain re-raises it first, so an outage while fetching a print's PDF was recorded as a per-bill failure and the card went out with no document links instead of aborting the phase | `tests/scenario/test_outages.py::test_sejm_api_down_fetching_the_print_stops_publishing_instead_of_posting_without_a_pdf` — fails on the reverted file, passes on HEAD | `except ServiceUnavailableError: raise` added before the catch-all, matching every sibling method |
| 18 | P2 | `models/events.py::event_keys`, `i18n.py::event_tags` | `impact_assessment` had its own header and icon but no searchable tag, unlike `government_position`; fixing `event_keys` alone (`tests/unit/test_events.py`) was not enough, because the tag line filters on `if key in lb.event_tags` and drops an unlabelled key in silence rather than crashing — the same bug one table over, only visible by rendering a real update | `tests/unit/test_events.py::test_an_impact_assessment_gets_the_same_searchable_tag_as_the_government_position`; `tests/unit/test_format_updates.py::test_impact_assessment_tag_actually_renders` — the second fails on the reverted `i18n.py` alone, passes on HEAD | `event_keys` appends the key on an `impact_assessment` supplement; RU/EN `event_tags` both gained the label |
| 19 | P3 | `adapters/telegram_format.py::EVENT_ICON` | `tribunal_ruled` had no entry while `tribunal` (the referral) had ⚖️, so the ruling fell back to the generic 🔄 | `tests/unit/test_format_updates.py::test_tribunal_ruling_gets_its_own_icon_not_the_generic_update_one` — fails on the reverted file, passes on HEAD | `"tribunal_ruled": "⚖️"` added beside `"tribunal"` |
| 11 | P2 | `models/events.py::_EVENT_BY_STAGE_TYPE`/`_newest_stage_event` | no entry for `Voting`, so a change whose only new node was a vote posted under the bare «Обновление» — measured against all 6,260 term-10 stage transitions, never once reproduced: `Voting` is always a child of a `SejmReading` or `PresidentMotionConsideration` that also moved and names the post more specifically. Fixed as a last resort rather than a priority, so the parent's more specific answer still wins when both are present | `tests/unit/test_format_updates.py::test_a_voting_node_with_no_decided_parent_is_not_a_bare_update` — fails on the reverted files, passes on HEAD; `test_update_header_names_the_event_and_the_closure_line_is_not_repeated` (pre-existing) still passes, confirming the parent still wins when both are new | `"Voting": "voting"` added to the stage-type table; `_newest_stage_event` now tries every non-`Voting` stage first and falls back to `Voting`'s own answer only when nothing else in the change names anything |
| 12 | P2 | `services/tracking/service.py:422` (Sejm only) | `has_news`/`fills_in_the_past` gate the Sejm path alone; RCL, wykaz, pre-print, rollover and linking post every `StatusChange` unconditionally. Confirmed harmless for all five, not just untested: wykaz/pre-print/rollover/linking each only ever fire on an edge-triggered, at-most-once state flip, and RCL — the one source with a real stage-like sequence — turned out structurally immune too | measured on all 831 corpus RCL projects: 557 have ≥2 stages reached the same day (the same-run-multi-stage situation `fills_in_the_past` guards against for the Sejm), and in every one of the 831 the reached-stage order is monotonic by catalog position — `RclStage` has no parent/child nesting (unlike the Sejm `Stage` tree) and is always read in a fixed schema order, so the API-ordering ambiguity `fills_in_the_past` exists for cannot occur here | no code change: investigated and closed, the guard was never missing anything reachable |

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
