---
paths:
  - "src/lexinform/models/phases.py"
  - "src/lexinform/models/bill.py"
  - "src/lexinform/models/events.py"
  - "src/lexinform/models/evidence.py"
  - "src/lexinform/models/observations.py"
  - "src/lexinform/models/planning.py"
  - "src/lexinform/models/sejm.py"
  - "src/lexinform/services/terms.py"
  - "src/lexinform/services/discovery.py"
  - "src/lexinform/services/analysis.py"
  - "src/lexinform/services/sources.py"
  - "src/lexinform/services/tracking/**"
  - "src/lexinform/adapters/telegram_format.py"
  - "src/lexinform/law_*.py"
  - "src/lexinform/i18n.py"
  - "tests/scenario/test_tracking*.py"
  - "tests/scenario/test_term_rollover.py"
  - "tests/scenario/test_late_discovery.py"
  - "tests/scenario/test_process_plans.py"
  - "tests/unit/test_phases*.py"
  - "tests/unit/test_events*.py"
---

# The road, "what comes next", baselines and re-analysis

- **The term comes from the API; the old term is drained, not dropped.** `LEXINFORM_TERM` is empty
  by default: `services/terms.py::TermResolver` takes the term flagged `current` in `/sejm/term`
  (newest in the DB when the API is down; nothing known → the run stops with the reason). Discovery
  runs in the current term only; repository listings (`list_by_status`, `list_tracked`, due queries,
  `find_rcl`, …) are not term-scoped — trackers group by `bill.term` where an API path needs one, so
  old-term acts still get Dz.U. and in-force posts. The first run of a new term
  (`tracking/rollover.py`, a phase *before* discovery) posts one "lapsed" update under every
  published unfinished Sejm bill of the old term and sets `discontinued_at` on unfinished rows
  (every listing filters on it); passed bills stay followed; RCL rows awaiting their druk move to the
  new term (`bills`, `publications`, `status_changes`, `summary_json.term`) because RCL joins use the
  term-less id. Idempotent. Citizens' bills get other wording (taken over; they return as a new druk
  with a new card).
- **"What comes next" is derived, not stored.** `models.next_phase(bill, today)` reads top-level
  stages, submission and act; the formatter dates it from `bill.agenda` (refreshed every run for
  every followed bill) or the constitutional deadline (`Phase.deadline`: Senate 30 days from the 3rd
  reading, President 21 from receipt; 14/7 for urgent). A *pilny* bill (`models.is_urgent`, art. 123)
  uses its own wording (`Labels.urgent_step_labels`, `urgent_durations`). **No passed date is
  printed**: past `DEADLINE_GRACE_DAYS` the line says what expiry *meant*
  (`Labels.deadline_passed_labels`, `urgent_deadline_passed_labels` first for urgent bills) and the
  step takes the `_overdue` variant of `next_step_labels` (urgent art. 123 wording dropped, `urgent_mode` kept). A step past
  `PHASE_PATIENCE` says how long it has stood (`models.stalled_days`, `Phase.since`; for RCL the
  stage's last modification, RCL leaving "rozpoczęcie" empty) — **but never a step with a date of its
  own** (a vacatio legis; the wykaz's planned quarter: `_when` asks `_planned_adoption` before
  `_stalled_for`, silent once the quarter is gone). A sitting dates only a phase whose venue it
  matches (`COMMITTEE_PHASES`/`SITTING_PHASES`; Senate, President, Dz.U., vacatio take none). A
  hearing past its application deadline is not offered; «до заседания» is dropped on the sitting's
  day. A constitutional deadline is the body's, never the reader's window («решение до …»). The
  reminder (`tracking/deadlines.py`, one per bill and phase, v17, `decision_reminder_days` = 7) says
  the date is counted from the third reading and runs early **only where it is**
  (`Phase.deadline_exact`; `ToPresident` is the hand-over itself). The Senate action carries no date
  (its committee acts long before the 30 days end).
- **The last stage is not the last node.** `models.process_stages` (read by `next_phase` and
  `Bill.last_stage`) = top-level stages minus `ASIDE_STAGE_TYPES` (`GovermentPosition`, `Opinion`)
  **and minus a trailing `End`**: the Sejm appends "Uchwalono" at the third reading and keeps it last
  while Senate, President and Dz.U. are ahead (druk 2799). The `End` of a sustained veto ("nie
  uchwalona ponownie", `models.end_names_veto_sustained`) stays and ends the road. `Bill.last_stage`
  is a top-level stage, never a child (children are post-decision paperwork); `last_stage_detail`
  adds a child that says more (which committee, how the Sejm voted), except a referral to
  `PLENARY_COMMITTEE_CODE`.
- **The veto has its own road; the Sejm's vote on it is a stage.** `Veto` carries the committee
  referral as its child; "Praca w komisjach nad wnioskiem Prezydenta" is a `CommitteeWork` told apart
  only by `ANSWERED_IN_COMMITTEE` read backwards; the vote is `PresidentMotionConsideration`. Its
  `decision`: "nie uchwalona ponownie" ends the road; "uchwalono ponownie" → phase
  `president_after_veto`, seven days (art. 122 ust. 5, `PRESIDENT_DAYS_AFTER_VETO`). An unrecognised
  last stage gives no phase (no fallback); `_ended_line` says «закон не принят» only when the listing
  says `passed=false`. **That vote is the only witness**: of 15 sustained vetoes in terms 8–10, eight
  keep `End` = "Uchwalono" and `passed` = true (druki 410, 643, 865, 935, 1109, 1110, 1131, 1600 of
  term 10), so `models.veto_stood` asks `PresidentMotionConsideration` as well as `End`. Use the
  shared `VetoOutcome` evidence for veto headers, process completion and deadlines.
- **The Senate rejects in the API's words.** `SenatePosition.position` has four values over terms
  8–10: "nie wniósł poprawek" (1,364), "wniósł poprawki" (722), "wniósł poprawkę" (27), **"wnosi o
  odrzucenie ustawy"** (93, all term 9). "odrzucił ustawę" never occurs, and "odrzuce-nie" does not
  contain "odrzuci": `models.senate_moved_rejection` ("odrzuc" and not "popraw") is the one test,
  also for the committee working on the position. Sejm's answer: "odrzucono uchwałę Senatu" (85) =
  override, on to the President; "przyjęto uchwałę Senatu" = rejection stands, road ends. Committee
  proposals are not Sejm decisions; Senate decision evidence is compared apart from rendering-only
  stage fields.
- **A broken-off reading decided nothing; the Tribunal's ruling is an answer.** "niedokończone …
  czytanie" is adjourned (term 10 joined, 36; terms 8–9 "nie dokończone", 58):
  `models.reading_decision` normalises the space for every rule. `ConstitutionalTribunalRuling` (3
  nodes, term 8; ten bills of term 10 at `PresidentToTribunal`) ends the road. Over all 5,533
  processes every `next_phase is None` is a stated closure or a veto that stood.
- **A zawity term is a step.** Art. 121 ust. 2: 30 days without a Senate uchwała and the act counts
  as adopted, so `next_phase` moves on (`_after_senate_silence` → `president_after_senate_silence`,
  no deadline — the President's receipt is undated). The only step the bot names unpublished; the
  wording says so and it unwinds if the Senate did act. No Senate deadline for the budget (art. 223,
  20 days) or a constitutional amendment (art. 235, 60 days): `_senate_days`, `_SENATE_SPECIAL_TERM`
  (the 2027 budget, druk 3150, does pass the keywords).
- **A live card is kept true; a finished one says so once.** `tracking/cards.py::CardRefresher`
  re-renders every followed card each run and edits it when the text drifted
  (`publications.rendered_sha256`, v16: a quiet run is a pure render, no request); **drift is the
  only test** — it also renders the final state (a rejected bill must not keep «дальше: III
  чтение»), and `is_over` is not the test (an act in vacatio legis is live). `list_tracked` must
  agree: a published act is followed until `entry_into_force` whatever its age (the grace window runs
  from `closure_date`), and **a passed law keeps the three-year window a veto gets** (druk 210 of term
  9 was answered 182 days after closure). The lapsed term is outside `list_tracked`, so
  `tracking/rollover.py` re-renders those cards (`Poster.rerender_card`, also used by linkers to
  re-tag a thread). The card calls itself finished only when `_ended_line` can say *how* — including
  a veto that stood.
- **A bill whose road ended before we saw it gets no analysis and no card.** `models.is_over(bill,
  today)` for every source: the act *applies*, rejected/withdrawn, RCL project closed without the
  Sejm, plan realised or removed, term lapsed — `next_phase` finding nothing ahead; a Sejm bill
  whose stages were never read is unknown, not over. `closureDate` alone is not the end (set at the
  third reading): discovery reads stages once for a bill first seen with one
  (`_ended_before_first_sight`) and stores `skipped_closed` (`reset --to analysis_pending` revives).
  An ELI is not the end either (druk 2699: Dz.U. 2026-08-18, in force 2026-11-19): discovery reads
  the act too (the publishing gate asks again later); an ELI not yet indexed counts as the end. RCL
  decides from the timeline before the catalogs (`RclProject.history_on_arrival`: a past-term
  project unmoved since this term began; RCL re-dates old listing rows, B64–B65); the wykaz from the
  entry's status. Followed bills are untouched. Last gate: `PublishingService.publish_new`.
- **The Sejm publishes the road out of order; a post that fills in the past is held.**
  `models.fills_in_the_past(known, found)`: `_post_news` holds a change whose new stage stands before
  one already told, listing it before the next post. Tree order is the road's order (no road stage of
  term 10 precedes one with an earlier date; 521 days carry several stages; split-day replay catches
  all 131 cases, most often `SenatePositionConsideration` behind `ToPresident`). Only
  `process_stages` is compared — 196 of 241 `GovermentPosition`/`Opinion` nodes precede later road
  stages.
- **Stage fingerprint** (`_stage_key`) drives updates. Rendering fields on `Stage` (`voting`,
  `position`, `committee_name`, `proposal`) stay *out* of it, or every bill posts after deploy.
- **First sight seeds a baseline and never replays history; a decision needs positive evidence**
  (incident 2111, `docs/incident-2111.md`; design record `docs/process-plans.md`).
  `models/evidence.py::DecisionEvidence` (`DecisionState`: absent/pending/unknown/known) turns every
  ambiguous field into an explicit state — `VetoOutcome`/`SenateOutcome`/`ReadingOutcome`/
  `TribunalOutcome` for the Sejm road, `SourceOutcome` for RPW/RCL/wykaz (`submission_evidence`,
  `rcl_evidence`, `wykaz_evidence`, used by `is_over`/`is_withdrawn` and the trackers);
  `terminal_stage` needs positive terminal evidence; `next_phase=None` does not mean closed; unknown
  decisions are not successful decisions. `models/observations.py::ObservedProcess` is the immutable
  baseline (stages, closure date, `passed`, analysed document, digest, revision, `seen_supplements`)
  discovery may extend but never rewrite; Sejm checkpoints commit it together with the status-change
  delivery work, with network calls outside that transaction. `models/planning.py::plan_bill` diffs
  old vs new, and **first sight (`previous is None`) yields `new_stages=()`,
  `closure_detected=False`, `decision_changed=False`**. `BillPlan.should_publish` adds
  `decision_changed` (from `decision_changes`: Senate-position outcomes, outside `_stage_key`) beside `fills_in_the_past`.
  Wired into `tracking/service.py`, `tracking/rcl.py`, `tracking/linking.py`; `services/analysis.py`
  calls `save_observed_process` at first analysis. **Discovery never overwrites
  `observed_closure_date`** (v24): analysis and linking seed it, tracking advances it after recording
  the change; a closure in the initial snapshot is not news. `tests/scenario/test_late_discovery.py`
  replays the incident cold at every stage.
- **A re-analysis needs a new text, not a new URL.** `AnalysisRecord.text_sha256` digests the
  normalised text the model saw (`services/analysis.py::text_digest`; page numbers, whitespace
  ignored); on a same-hash document `reanalyze_bill` returns None and only repoints `source_url`/`source_checked_at`.
  `SejmTextSource.newer` compares `changeDate` with `source_checked_at` and skips the download after
  the 3rd reading only when `models.third_reading_kept_the_text` proves it (2nd reading straight to
  3rd, no "-A" report, `minorityMotions == 0`); unknowns mean "may differ". **The act is compared,
  not the file** (`law_digest.py`, `AnalysisRecord.law_sha256`): body from "Art. 1." to the
  uzasadnienie/OSR (`sections.law_body`), dropping only what `law_technical.py` proves technical
  (footnote or Dz.U. citation, dash-framed page number, signed RCL stamp, list marks), letters and
  digits lowercased with **every number its own token, in order** (B80–B81). Digests are versioned
  (`law-v2:`, `text-v2:`; an older hash never certifies a new text); only strict equality counts; the
  change review gets both bodies whole. **A differing digest is not yet a reading** (2026-10-01, B71;
  corpus `checks/92_transitions.py`, `out/92_review_calibration.json`): a druk printed from the
  `BILL` submission the analysis read is that text by rule (`_same_submission`; all 524 RPW→druk
  pairs of term 10 identical). Otherwise `law_diff.py` diffs the file the *record* names: layout noise
  keeps the analysis free; a change ≤ `change_review_max_ratio` (0.15) of words goes to the triage
  model with the card's description (`AnalysisService._changes_settled`, synchronous, ~$0.012) and
  only a confident (`triage_min_confidence`) "not material" keeps it; else it is read (468 RCL→druk
  pairs: 107 free, 304 reviewed, 57 read). Older records get a digest from their own file
  (`_previous_law`) if it still hashes as `text_sha256`. **A scan has no digest**: only the same file
  `sha256` is the same text. **A re-analysis is news only when the model names a change**
  (`Analysis.names_changes`, also in `plan_bill`, B61), except a wykaz plan's first text (B62);
  otherwise stored and counted, not posted (`docs/incident-3141.md`,
  `docs/reviews/2026-09-28-incident-3141-review.md`).
