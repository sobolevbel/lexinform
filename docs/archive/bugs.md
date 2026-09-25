# Архив дефектов

Статус: история расследований до 24 сентября 2026. Текущие задачи — в [реестре](../BUGS.md).
Идентификаторы сохранены; закрытые записи не означают повторную проверку production сегодня.

## B50

Исправлен 24.09.2026, commit `5b11966`. `awaiting_batch_since` обходит не только watermark,
но и ограничение возраста в `list_tracked`; связанные и discontinued строки исключаются.
Регрессия: `test_waiting_supplement_survives_the_tracking_age_cutoff`.

## Fixed

### VPS batch poller repeatedly dispatched for synthetic batches (24 Sept 2026, P3)

The provider-wide Anthropic listing contained three completed smoke-test batches from
`docs/reviews/2026-09-24-batch-predeploy.md`, while production `llm_batches` was empty.
Seventeen consecutive `batch-ready` Actions runs from 02:50 to 08:26 UTC collected nothing;
each also ran tracking. The poller incorrectly treated every undeleted remote batch as work
owned by this installation. It now reads the pushed SQL state into memory and retrieves only
batches with unconsumed items, by their saved provider and ID. Collected and unrelated batches
cannot dispatch; old OpenAI completions and terminal failures remain eligible for recovery.
Regressions: `test_poll_batches_ignores_provider_batches_absent_from_persisted_state`,
`test_poller_stops_waking_runner_after_collection_is_persisted`, and the state-read failure
and original-provider cases in `tests/unit/test_cli.py`.

- **44 (P2, 2026-09-23)**: `amendments_stage` treated a committee recommendation to adopt without amendments as an amendment document. Exclude `bez poprawek`; regression: `test_amendments_stage_is_the_senate_print_or_a_report_on_amendments`.

### Исправленные дефекты B1–B43

Каждая запись содержит описание дефекта, затронутый модуль, проверку и исправление.

#### B1 · P1

the alias publication it created never copied `sent_at` from the original, unlike `publishing.py`'s and `wykaz.py`'s own linking paths — the digest's `sent_at`-ranged week query never returned it, so a druk that inherited its card this way was invisible to the digest for the rest of its life. Live in production: druk 2172, inherited from RCL/12405609

**Модуль:** `services/tracking/linking.py::_inherit_card`

**Проверка:** `tests/scenario/test_tracking_pre_print.py::test_the_inherited_card_keeps_the_original_sent_at_for_the_digest` — fails on the reverted file, passes on HEAD

**Исправление:** `sent_at=card.sent_at` copied onto both the constructor and `mark_publication`, matching the other two paths

#### B2 · P1

no dedupe by `message_id` when a project got its druk the same week its card was sent — the original row and the inherited-card row (same Telegram message, per #1's three aliasing paths) rendered as two entries under two names. Fixing #1 made this reachable on the very next digest instead of masking it, so the two had to move together

**Модуль:** `services/digest.py` (`build`/`_entry`)

**Проверка:** `tests/scenario/test_digest.py::test_an_inherited_card_appears_once_in_the_digest_not_under_two_numbers` — fails on the reverted file (`['RPW/29075/2026', '3100']`), passes on HEAD (`['3100']`)

**Исправление:** `_add_or_replace` dedupes `cards`/`updates` by `message_id`; on a collision the later post wins, since its bill row is the one still current

#### B3 · P1

`record_change()`'s `None` on a `change_key` collision was read as "nothing to do" by all seven callers while the fingerprint and `save_rcl`/`save_wykaz` had already been written outside the transaction, so a genuine change could be lost for good on a collision

**Модуль:** `services/tracking/posting.py`, `rcl.py`, `wykaz.py`

**Проверка:** `test_process_plans.py`, `test_delivery_checkpoints.py` (30 tests)

**Исправление:** `823e9d8`: the fingerprint/stage save and `record_change` now commit inside one `self._repo.atomic()` block, so `None` only ever means "an earlier attempt already recorded and delivered this exact change"

#### B6 · P1

`_stage_key` excludes `position`, so a `SenatePosition` whose position text arrived later moved no fingerprint and was never told

**Модуль:** `models/sejm.py`, `models/evidence.py`, `models/planning.py`

**Проверка:** `test_process_plans.py::test_senate_position_arriving_late_is_news_even_with_the_same_stage_identity` — fails reverted to `3048222` (0 updates instead of 1), passes on HEAD

**Исправление:** `_stage_key` itself is unchanged; `decision_changes`/`decision_fingerprint` (`evidence.py`) fold a Senate-outcome change into `plan.new_stages`/`decision_changed` in parallel, bypassing `fills_in_the_past` and moving `change_key` even when the stage identity does not

#### B7 · P0

`_phase_after` fell through to `return None` for `Referral`, `Voting`, `CommitteeReport` and a non-trailing `End`; `next_phase is None` read as "road over" everywhere else in the codebase, so the bill got no card and a followed one froze

**Модуль:** `models/phases.py`

**Проверка:** `tests/unit/test_phases.py::test_an_unrecognised_last_stage_reads_as_unknown_not_over` — fails reverted to `3048222` (`None`), passes on HEAD (`decision_unknown`)

**Исправление:** fallthrough now returns `Phase(key="decision_unknown")`, the same "unknown must not read as closed" rule incident 2111 forced elsewhere; measured over all 5,533 stage trees of terms 8-10 the exact scenario never actually occurred (0 instances, `End` only ever appears top-level as a legitimate veto-sustained close) — the fix is general and closes any future/unmodelled `stage_type` too, not only the four named

#### B8 · P0

absence from the register read as a withdrawal, guarded only against a wholly empty CSV; a response cut off mid-download parsed cleanly for the bytes it had, so the missing rows were never unreadable, they were just never in the text. Measured on the real 1,453-row register truncated to 60% of its bytes: 915 rows still parse, and 143 of the 296 live open bill-kind entries would be fabricated as `Wycofany` (e.g. UD464)

**Модуль:** `adapters/wykaz_csv.py::parse_register`, `services/tracking/wykaz.py::_removed`

**Проверка:** `tests/unit/test_wykaz_csv.py::test_a_response_cut_off_mid_download_is_an_error_not_a_smaller_register` — fails on the reverted file (no error raised), passes on HEAD

**Исправление:** a complete download always ends its last row cleanly; `parse_register` now refuses one that does not, the same `WykazPageError` it already raises for an empty or column-less response

#### B9 · P1

every unfinished row was discontinued whether or not its end-of-term announcement (`_announce`) succeeded, so a bill whose announcement threw was discontinued, unannounced and outside `CardRefresher`'s reach (`list_tracked` filters on `discontinued_at`)

**Модуль:** `services/tracking/rollover.py`

**Проверка:** code reading against `823e9d8`/`b010b93`

**Исправление:** discontinuation and the announcement's delivery plan now commit in one `self._repo.atomic()` block — a failed announcement rolls the discontinuation back with it, there is no half-finished state left to freeze on

#### B11 · P2

no entry for `Voting`, so a change whose only new node was a vote posted under the bare «Обновление» — measured against all 6,260 term-10 stage transitions, never once reproduced: `Voting` is always a child of a `SejmReading` or `PresidentMotionConsideration` that also moved and names the post more specifically. Fixed as a last resort rather than a priority, so the parent's more specific answer still wins when both are present

**Модуль:** `models/events.py::_EVENT_BY_STAGE_TYPE`/`_newest_stage_event`

**Проверка:** `tests/unit/test_format_updates.py::test_a_voting_node_with_no_decided_parent_is_not_a_bare_update` — fails on the reverted files, passes on HEAD; `test_update_header_names_the_event_and_the_closure_line_is_not_repeated` (pre-existing) still passes, confirming the parent still wins when both are new

**Исправление:** `"Voting": "voting"` added to the stage-type table; `_newest_stage_event` now tries every non-`Voting` stage first and falls back to `Voting`'s own answer only when nothing else in the change names anything

#### B12 · P2

`has_news`/`fills_in_the_past` gate the Sejm path alone; RCL, wykaz, pre-print, rollover and linking post every `StatusChange` unconditionally. Confirmed harmless for all five, not just untested: wykaz/pre-print/rollover/linking each only ever fire on an edge-triggered, at-most-once state flip, and RCL — the one source with a real stage-like sequence — turned out structurally immune too

**Модуль:** `services/tracking/service.py:422` (Sejm only)

**Проверка:** measured on all 831 corpus RCL projects: 557 have ≥2 stages reached the same day (the same-run-multi-stage situation `fills_in_the_past` guards against for the Sejm), and in every one of the 831 the reached-stage order is monotonic by catalog position — `RclStage` has no parent/child nesting (unlike the Sejm `Stage` tree) and is always read in a fixed schema order, so the API-ordering ambiguity `fills_in_the_past` exists for cannot occur here

**Исправление:** no code change: investigated and closed, the guard was never missing anything reachable

#### B14 · P2

the `not bill.stages` fallback to `_pre_print_phase` fired for *any* numbered druk whose stages were never saved, not only a true pre-print one — discovery never calls `save_stages` for a row skipped at the text prefilter. Measured on the production dump: 60 of 129 numbered term-10 bills are in exactly this state, all `skipped_text_prefilter`, and every one answered `Phase(key="pre_print")` — «ждём номер druku» for a bill that already has one, through `/show`/`/preview`

**Модуль:** `models/phases.py::_phase_of`

**Проверка:** `tests/unit/test_models.py::test_a_numbered_druk_with_unread_stages_is_not_told_it_has_no_number` — fails on the reverted file (`pre_print`), passes on HEAD

**Исправление:** `is_pre_print` already decides this correctly on its own, by number; the stages-based fallback is gone and a numbered bill with unread stages now falls to `_sejm_phase`, which already answers `first_reading` for an empty tree

#### B16 · P2

a reply was prepared and sent under a card that was only `queued`/`failed` (never `sent`), landing rootless in the channel — `card()` returns any `NEW_BILL` row regardless of status, and unlike the already-guarded `linking.py::Linker.link`, these two built the `StatusChange`/reply unconditionally; every other caller of a reply already checked `card.status is SENT` first

**Модуль:** `services/tracking/wykaz.py::WykazLinker.link`, `services/tracking/pre_print.py::_announce_withdrawal`

**Проверка:** `test_wykaz_link.py::test_a_reply_waits_for_a_still_unsent_plan_card_instead_of_replying_to_nothing`, `test_tracking_pre_print.py::test_a_withdrawal_waits_for_a_still_unsent_card_instead_of_replying_to_nothing` — both fail reverted, pass on HEAD

**Исправление:** both now gate on `card is not None and card.status is SENT`, mirroring `linking.py`; unsent, the wykaz row falls to the normal publish path (`_NO_CARD_YET`) and the withdrawal is retried whole on the next `/bills` read, so nothing is lost, only delayed to the run whose card finally sends

#### B17 · P3

swallowed `ServiceUnavailableError` while every other `except Exception` in the chain re-raises it first, so an outage while fetching a print's PDF was recorded as a per-bill failure and the card went out with no document links instead of aborting the phase

**Модуль:** `services/publishing.py::_safe_print`

**Проверка:** `tests/scenario/test_outages.py::test_sejm_api_down_fetching_the_print_stops_publishing_instead_of_posting_without_a_pdf` — fails on the reverted file, passes on HEAD

**Исправление:** `except ServiceUnavailableError: raise` added before the catch-all, matching every sibling method

#### B18 · P2

`impact_assessment` had its own header and icon but no searchable tag, unlike `government_position`; fixing `event_keys` alone (`tests/unit/test_events.py`) was not enough, because the tag line filters on `if key in lb.event_tags` and drops an unlabelled key in silence rather than crashing — the same bug one table over, only visible by rendering a real update

**Модуль:** `models/events.py::event_keys`, `i18n.py::event_tags`

**Проверка:** `tests/unit/test_events.py::test_an_impact_assessment_gets_the_same_searchable_tag_as_the_government_position`; `tests/unit/test_format_updates.py::test_impact_assessment_tag_actually_renders` — the second fails on the reverted `i18n.py` alone, passes on HEAD

**Исправление:** `event_keys` appends the key on an `impact_assessment` supplement; RU/EN `event_tags` both gained the label

#### B19 · P3

`tribunal_ruled` had no entry while `tribunal` (the referral) had ⚖️, so the ruling fell back to the generic 🔄

**Модуль:** `adapters/telegram_format.py::EVENT_ICON`

**Проверка:** `tests/unit/test_format_updates.py::test_tribunal_ruling_gets_its_own_icon_not_the_generic_update_one` — fails on the reverted file, passes on HEAD

**Исправление:** `"tribunal_ruled": "⚖️"` added beside `"tribunal"`

#### B20 · P0

a passed bill with no act left `list_tracked` 180 days after `closure_date`, which the Sejm sets at the third reading — druk 210 of term 9 was dropped two days before the Sejm overrode the Senate, so the override, the hand-over, the signature and the act were never told and the card stayed at «Сенат отклонил закон» over a law in force

**Модуль:** `adapters/sqlite_repo.py` `list_tracked`

**Проверка:** `checks/31_tracker.py`: the only bill of 3,266 across terms 8–10 to lose a stage; 1 of 2,380 bills with a closure date

**Исправление:** the wait ends with the act and nothing else, so the passed-with-no-act arm takes `pending_decision_max_days` and `track_passed_max_days` is gone; measured cost, one extra bill of term 10

#### B21 · P1

the reminder read the bills as `check_updates` loaded them, before this run's own stage loop stored the `PublicHearing` node, so a hearing announced today was told twelve hours late — on a window art. 70b measures in ten days

**Модуль:** `services/tracking/hearings.py`

**Проверка:** `checks/32_idempotence.py`: 9 bills of the 830 of term 10 were told only by a second run on the same data

**Исправление:** each row is read again, the idiom `DeadlineReminder` already used for the same reason

#### B22 · P1

a substantive change detected by a run with publishing off was held and released only by a later post, so a bill that went quiet afterwards never told it — 11 of 830 term-10 bills lost their referral to the first reading this way (`checks/32_idempotence.py`, 15 Sept 2026)

**Модуль:** `services/tracking/service.py`

**Проверка:** `test_process_plans.py::test_queued_news_is_delivered_without_waiting_for_another_event`; `checks/32_idempotence.py` rerun on HEAD, 21 Sept 2026, over 3,036 processes of terms 8-10: **0** processes now lose a post this way (was 11 of 830)

**Исправление:** not the patch the row proposed (a hold that remembers why, or a fingerprint that does not move without publishing) — a third path: `publish=False` now queues a durable `DeliveryPlan` (`Told.QUEUED`) instead of an editorial hold, drained by `list_due_deliveries` on any later `publish=True` run regardless of whether the bill produces another legislative event first

#### B23 · P0

a pending veto was called an adopted act; correcting the headline alone left the false claim in the body

**Модуль:** `models/events.py`, `adapters/telegram_format.py`

**Проверка:** run 79, 2111, change 10 with zero new stages; `test_veto_evidence.py`

**Исправление:** shared explicit veto outcome; no adoption claim without the corresponding evidence

#### B24 · P1

an old closure became news on the first tracking pass

**Модуль:** `services/tracking/service.py`

**Проверка:** `test_late_discovery.py`, including a same-day initial closure and delayed tracking

**Исправление:** v24 persists the observed closure separately from discovery metadata; analysis/linking seed it, tracking advances it

#### B25 · P2

a late-discovered bill was analysed from the original print, then immediately from its adopted text

**Модуль:** `services/sources.py`

**Проверка:** real 2111 snapshot, workers 1/4, one initial run and an identical repeat

**Исправление:** initial selection uses `latest_text_document`, just as tracking does

#### B26 · P0

a motion with no/unknown decision meant “veto overridden” and started the signature deadline

**Модуль:** `models/events.py`, `models/phases.py`

**Проверка:** `test_veto_evidence.py`

**Исправление:** pending is a separate `VetoOutcome`, shared by event and next-step logic

#### B27 · P0

a committee's recommendation to reject was evidence of a Sejm rejection

**Модуль:** `models/events.py`

**Проверка:** `test_veto_evidence.py`

**Исправление:** only a reading decision proves rejection; otherwise the closure reason stays unspecified

#### B28 · P2

any `closure_detected` removed next-step guidance, including III reading with the Senate/President still ahead

**Модуль:** `adapters/telegram_format.py`

**Проверка:** `test_veto_evidence.py`, `test_format_updates.py`

**Исправление:** check whether a next phase exists before suppressing it

#### B29 · P3

itemized call costs omitted cache reads/writes although the run total included them

**Модуль:** `services/cost.py`, `models/report.py`

**Проверка:** `test_cost_ledger.py`

**Исправление:** persist both cache counters on each call; old records default to zero

#### B30 · P1

A submission failure stranded bills in `batch_pending` with no durable job

**Модуль:** batch submission

**Проверка:** `test_submit_failure_keeps_work_retryable`, `test_uncertain_submission_waits_for_explicit_reconciliation`

**Исправление:** Persist requests before the remote call; automatically retry definitive rejections; keep ambiguous acceptance visible for provider reconciliation without duplicate submission

#### B31 · P3

Saving the old analysis cleared an in-flight batch marker and caused repeat submissions

**Модуль:** RCL tracking, SQLite analysis save

**Проверка:** `test_rcl_reanalysis_is_not_submitted_twice_while_in_flight`

**Исправление:** Re-saving identical analysis preserves `batch_pending`

#### B32 · P1

A late answer undid an operator skip and published a card

**Модуль:** batch collection

**Проверка:** `test_collect_respects_an_operator_skip`

**Исправление:** Consume only while the bill still awaits that batch; superseded answers do not change its status

#### B33 · P3

Positive triage was charged again after batch collection

**Модуль:** analysis queue

**Проверка:** `test_collect_does_not_pay_for_triage_again`

**Исправление:** Persist the triage memo when enqueuing

#### B34 · P3

Queued work bypassed the run budget

**Модуль:** batch cost guard

**Проверка:** `test_batch_submission_obeys_the_run_budget`

**Исправление:** Reserve a conservative estimate at the selected batch model's price before queuing; stop the phase at the limit

#### B35 · P1

A completed answer could wait for the next source change despite already being ready

**Модуль:** batch reanalysis and tracker

**Проверка:** `test_collected_reanalysis_is_applied_even_after_discovery_watermark_moves`

**Исправление:** Persist `reanalysis_ready` and include it in tracking regardless of discovery watermark

#### B36 · P3

Batch mode produced no full analysis or preview

**Модуль:** dry run

**Проверка:** `test_dry_run_still_previews_a_new_analysis_without_submitting_a_batch`

**Исправление:** Run analysis synchronously inside the rollback transaction

#### B37 · P1

A failed batch with unconsumed items disappeared after an interrupted write

**Модуль:** batch repository and collection

**Проверка:** `test_failed_batch_remains_collectible_after_interrupted_item_consumption`

**Исправление:** Keep unfinished batches visible; register and consume their items atomically

#### B38 · P1

A provider switch polled old IDs through the new backend

**Модуль:** provider routing

**Проверка:** `test_switching_provider_does_not_poll_old_ids_on_the_new_provider`

**Исправление:** Resolve each open batch by its saved provider; wait when that backend is unavailable

#### B39 · P1

Error-file items and successful results in expired/cancelled batches were discarded

**Модуль:** OpenAI batch adapter

**Проверка:** `test_openai_returns_failed_items_from_the_error_file`, `test_openai_terminal_batch_with_results_is_collectible`

**Исправление:** Read both files and collect terminal partial results; reconcile missing items

#### B40 · P1

One invalid structured answer aborted later items

**Модуль:** batch result adapters

**Проверка:** `test_openai_malformed_analysis_does_not_hide_the_next_item`, `test_anthropic_truncated_analysis_does_not_hide_the_next_item`

**Исправление:** Return an item error on schema validation failure

#### B41 · P3

Collected tokens were absent from totals, billed at sync price, and labelled with the collecting model/prompt

**Модуль:** batch accounting

**Проверка:** `test_collected_tokens_are_in_the_run_totals`, `test_batch_result_keeps_the_model_that_answered`

**Исправление:** Build report totals from itemized calls; mark batch usage for half-price accounting and retain submit-time prompt version

#### B42 · P1

Government bills moved while their batch item still named the previous term

**Модуль:** term rollover

**Проверка:** `test_government_batch_survives_a_term_rollover`

**Исправление:** Defer moving a government row until its submitted analysis has been applied under the original memo key

#### B43 · P1

Collection bypassed configured publication threshold and caps

**Модуль:** collect CLI

**Проверка:** `test_collect_cli_respects_configured_min_score`

**Исправление:** Pass the settings into `RunOptions`

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

See [the incident review](../incident-2111.md) for the production evidence, exact spending,
architectural diagnosis, migration and the staged redesign. The false historical reply was deleted
by hand before the code fix shipped — a code fix does not edit old Telegram replies, so this was
never going to be automatic, and by the time of the fix it no longer needed doing.


## Расследованные кандидаты без подтверждённого дефекта

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
points. All fourteen candidates, B30–B43, are fixed and listed in the entries above; none is open.
Offline reproduction does not establish production incidence. Details and limitations:
[review report](../reviews/2026-09-23-batch-architecture.md).

`P1`/`P3` retain the reader-impact definitions above. In particular, duplicated paid LLM work and
a bypassed spending guard are P3 here, although they deserve early engineering attention.
The two `test_batch_review_candidates.py` modules started as strict `xfail` probes and are now
ordinary regression tests: no `xfail` marker is left.

The review's list of what was not yet done was closed on 23 Sept as well: a bill the reader must
act on within `llm_batch_sync_within_days` is analysed synchronously; `collect-batches` and
`track` answer the inbox and the workflow runs `commands` after `scan`, `reprefilter` and
`index-rcl-numbers`, so no dispatch that replaces a pending inbox run leaves a command waiting;
`/status` names the open batches, the queued and uncertain intents and any `batch_pending` bill
held by nothing; the hand-written OpenAI schemas are tested against their pydantic models.
Payload splitting and the poller's view of uncollected batches were already done (`df63daa`,
`0aa596e`).

The original probes pass after the 23 September fixes. The initial closure was too broad:
follow-up validation also covers runner loss, superseding jobs and missing source documents.
Production incidence remains unmeasured; the review preserves the original reproduction limits.

Follow-up for B30/B32/B35/B37: state is now pushed before batch submission on ephemeral runners;
generation checks prevent an old answer from completing a new job after reset; ready answers keep
their source snapshot and do not require another download; usage is charged after the local
consumption transaction commits. Regression coverage includes `test_batch_checkpoint.py`,
`test_old_result_cannot_complete_a_new_job_after_reset`,
`test_completed_initial_analysis_uses_its_saved_input_without_downloading_again`, and
`test_ready_reanalysis_survives_restart_and_a_missing_document`.

B34/B41 follow-up: queue entries now freeze the provider payload, model, prompt and maximum output.
Reservations include scan input and the output cap; restored open jobs and uncertain intents count
against the next run's budget. Requests that exceed the remaining budget wait without submission.
The selected batch backend counts the input; batches are split at 100 requests / 100 MB.
`test_open_batch_reserves_budget_after_a_restart` covers workers 1/4;
`test_saved_request_keeps_model_prompt_and_output_limit_after_configuration_change` covers both
providers. Unknown model prices require an explicit price-table update before new batch work.

B38/B40/B41 follow-up: a missing remote ID remains recoverable, one unavailable batch does not
block the next, malformed JSONL/envelopes are isolated and reconciled against the manifest.
Truncated/refused answers retain billed usage. v29 stores each answer before application and
acknowledges its cost atomically with the run report; a restart before that report restores the
cost exactly once (`test_collected_cost_survives_restore_before_the_report_is_saved`).
