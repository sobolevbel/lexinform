---
paths:
  - "src/lexinform/agenda.py"
  - "src/lexinform/attention.py"
  - "src/lexinform/usefulness.py"
  - "src/lexinform/delivery_schedule.py"
  - "src/lexinform/telegram_text.py"
  - "src/lexinform/publication_titles.py"
  - "src/lexinform/i18n.py"
  - "src/lexinform/models/delivery.py"
  - "src/lexinform/models/events.py"
  - "src/lexinform/models/attention.py"
  - "src/lexinform/models/editorial.py"
  - "src/lexinform/models/participation.py"
  - "src/lexinform/services/posting.py"
  - "src/lexinform/services/publishing.py"
  - "src/lexinform/services/publications.py"
  - "src/lexinform/services/joint.py"
  - "src/lexinform/services/amendments.py"
  - "src/lexinform/services/delivery*.py"
  - "src/lexinform/services/message_parts.py"
  - "src/lexinform/services/analysis.py"
  - "src/lexinform/services/tracking/**"
  - "src/lexinform/adapters/publisher_base.py"
  - "src/lexinform/adapters/telegram*.py"
  - "tests/scenario/test_*agenda*.py"
  - "tests/scenario/test_*attention*.py"
  - "tests/scenario/test_*delivery*.py"
  - "tests/scenario/test_joint*.py"
  - "tests/scenario/test_supplements.py"
  - "tests/scenario/test_publication*.py"
  - "tests/scenario/test_routine_updates.py"
  - "tests/scenario/test_update_roundups.py"
  - "tests/scenario/test_message*.py"
---

# Sittings, messages and delivery

## Sittings

- **Sittings go out as one roundup per run, never as replies under cards** (2026-10-01).
  `Poster.queue_sitting` → `flush_sittings` → `Publisher.publish_sittings`
  (`MessageFormatter.sittings`): Sejm first, then committees, each line linked to its card. The
  per-sitting `agenda`/`agenda_cancelled` rows remain the bookkeeping (dedupe, retraction, frozen
  `DeliveryPlan`) and share the roundup's `message_id`; a failed roundup leaves every line `failed`
  and `_retry_failed` requeues them. The card does not absorb a same-run update or sitting (it shows
  neither an RPW withdrawal nor a sitting outside its phase's venue).
- **An announced sitting is taken back when it goes.** `AgendaWatcher._retract_gone` posts one
  `agenda_cancelled` per announced sitting that left `PLANNED` or dropped this bill (v19, unique per
  bill/channel/`ref`). Not a retraction: a sitting that **moved** (same `sitting_key`; told as a new
  post saying what it moved from), one that **started or passed** (`_already_happened`), a listing
  that **failed** (`_Listings.kept` restores items, Sejm `/proceedings/{n}` and committees alike).
  The line says which fact — sitting off, or meets without this bill (`_Listings.announced`) — and
  only if the announcement was `sent`; a still-queued one is dropped (`Poster.drop_sitting`).
  Committees renumber planned sittings (ASW 138 → 135, 1 Oct 2026): `_keep_renumbered` keeps the
  told `sitting_key`.
- **Agenda matching has three blind spots** (term 10: 4,387 committee and 75 Sejm sittings).
  (1) Past the third reading the agenda names the **derived print** (Senate resolution, President's
  motion; 211 committee + 160 plenary items — the reader's last windows): `_items_for` also matches
  `derived_print_numbers(bill.stages)` (of 991 such prints none is a process number itself), no extra
  request. (2) **A joint sitting is listed under every committee** with its own `num` (938 sittings):
  `jointWith` is parsed and `CommitteeSitting.meeting_key` (day, hour, sorted codes) collapses it
  (`Poster.told_jointly` keys on joint *prints* and does not help). (3) **Hour and room move on their
  own** (886 sittings record a change: 204 hour, 97 room, 61 both): the `ref`'s last segment carries
  day, hour and room (`_when_and_where`), `sitting_key` is the `ref` without it, `moved_from` is the
  item as last announced. A sitting told under an old day-only `ref` keeps it (`_keep_told_ref`)
  until no dump carries one.
- **A conditionally called sitting is not a date.** 21 sittings of term 10 happen only if the Sejm
  refers something first. `agenda.sitting_condition` reads committee `notes` into a kind
  (`second_reading_amendments`, `first_reading_referral`, `senate_amendments`, `referral`, `other`)
  and the points it covers (eleven notes name points, e.g. "Pkt III aktualny…"; on two the point
  carries none of our prints); `condition_covers` reads a point by its numeral or by position. The
  condition is **not** in the `ref` (lifting it must not look like a move); the card marks the day
  «условно» and self-heals, the sent announcement keeps its hedge. `notes` is also the only place the
  API gives an address for a przesłuchanie, parsed by `rcl_letters.parse_letter` (no second
  Polish-date parser). `closed` (279 sittings, read by `hearing_application`) is said: the public may not enter.

## Replies and updates

- **Pending-before-send.** Every Telegram post gets a `publications` row (`pending`) first, unique
  per kind/bill/channel (agenda posts and retractions: also per `ref`). Failed posts retry up to
  `max_publish_attempts`; a crash-left `pending` becomes `unknown` and is never auto-resent. Due
  queries (`list_due_in_force`, `list_due_consultations`) must keep listing a bill whose post
  `failed` (`Poster.posted` decides). `unknown` is resolved only by an audited operator decision:
  retry the saved payload and parent, never reuse publication ids, keep `dismissed` distinct from
  `sent` and editorial holds.
- **A reply leaves to the card what the card says** (2026-10-02). Status update and consultation,
  hearing and deadline reminders open with `_reply_header` (label + number, no title), no importance
  badge, no «Суть проекта»; tags still carry importance and category. A re-analysis repeats the whole
  summary. Dz.U. and in-force notices keep the title and one summary sentence. **An update and the
  act in one run are one message**: `_post_news` sends the update `with_act`
  (`DeliveryPlan.with_act`, frozen by `_detect`; `replace_unsent_update_delivery` may rewrite it only
  while `queued` with no attempt); the act row is written `sent` with the update's id after the send
  (`Poster._act_told_in`), so a failed update leaves no act row and its retry carries the act.
- **Every reply carries the topic tags.** `_tag_line` does it; the status update and the joint
  reply build theirs by hand and must add `_topic_tags`.
- **Not every stage is a post.** `models/events.py`: `is_substantive` separates reader events
  (referral, report, vote, Senate, President, hearing, a decided reading) from frame nodes (`Start`,
  `ReadingReferral`, `Reading`, `CommitteeWork`, `End`, `Opinion`; `ToPresident` is *not* frame — it
  starts art. 122's 21 days); `has_news` decides whether a change is posted now. `Opinion` is in
  `SERVICE_STAGE_TYPES` (held; filed opinions are not this channel's genre, 2026-09-12);
  `GovermentPosition` is told and names the post via `_EVENT_BY_STAGE_TYPE` under the digest's key
  (the stage arrives even when its document could not be read, so `supplement_event` alone was not
  enough). A frame-only change is *held*: its `status_changes` row exists with a `skipped`
  `status_update` publication, and `Poster.status_update` prepends held stages to the next post and
  marks them `sent`. **Recording and telling are one pair**: `Poster.record_change` writes the row
  and returns it with its id, `Poster.tell` answers `Told.SENT`/`HELD`/`FAILED` — no fourth outcome.
  Every watcher uses them; the only exception is `StatusTrackingService._retry_failed`. A closure
  detected in the run of the act's ELI is held (the Dz.U. notice tells it). Headers: `update_event` →
  `Labels.update_headers`; the closure line is dropped when the header says it. `has_news` gates the
  *Sejm* loop only; `RclWatcher` posts every change it records (`rcl_fingerprint` decides).
  `StatusChange.consultation_opened` names a consultation opening under an already-reached RCL stage.
- **Queued delivery is not an editorial hold.** A retry sends the saved per-channel payload
  (`publications.delivery_json`, frozen at send time) and releases only its included held-change
  ids; an old event is never rendered against a newer bill. Before the first attention delivery
  attempt, participation evidence is refreshed apart from stages and analysis; missing windows hold
  reminders without attempts; frozen payloads and attempted groups are immutable.
- **A routine step is not a message of its own** (2026-10-22): with `reader_attention=on`, an update
  rated `routine` goes `digest_only` (`usefulness.digest_reason`,
  `routine_step_without_reader_action`) — an RCL stage, hand-over to the Sejm, a committee report with
  no new text, hand-over to the President. The card is still re-rendered and the digest lists it.
  Committee referral, RCL consultation stage, the President's signature and anything urgent stay
  posts. (30 updates sent by 21 Oct 2026: 9 would have gone to the digest.)
- **A card is one message** (2026-10-29): over 4,096 characters, `MessageFormatter.new_bill` shortens
  the model's fields in `CARD_FIELD_CAPS` steps (groups, bullets, sentences; the entry-into-force
  rule before its list of exceptions) and never splits; a card that fits is rendered unchanged.
- **Multipart deliveries** freeze every physical part before the first attempt, wait for the saved
  parent and keep parent payloads while a continuation is unfinished (`message_part`,
  `DeliveryPlan.continuation_*`; see `docs/reference/database.md`).
- **Amendments** (Senate resolution print; a committee report proposing poprawki) are summarised by
  `AnalysisService.summarize_amendments` after the change row exists, stored as `amendments_json`;
  failure degrades to the bare event. A report on the Senate's position is only votes ("pkt 1 –
  przyjąć"), so `amendments.amendments_source` reads the Senate's resolution with the report beside it
  (`AmendmentsContext.recommendation`) and gives no summary when the resolution cannot be confirmed
  (`senate_position_reported`).

## Joint prints and filed documents

- **Jointly considered prints share one thread.** `ProcessSummary.prints_considered_jointly`;
  `PublishingService` gives the group one card: a candidate whose partner has a followed `sent` card
  gets a `joint_bill` reply instead (`Publisher.publish_joint_bill`, `MessageFormatter.joint_bill`;
  unique per bill/channel, v12), settling it like a card (`list_publish_candidates` excludes both
  kinds). Within a run the government's print goes first (`government_first`). The reply's bill has
  no `new_bill` row, so trackers ignore it; the group's events come from the card's process.
  `republish` forgets both rows. The replying print is analysed on its own text and **`min_score`
  gates cards, not replies** (`list_publish_candidates` with the bar, `list_joint_reply_candidates`
  without; `primary_of` decides, so a print with no card to hang under never gets its own below the
  bar). The difference is a **cheap second call on the descriptions, not the texts**
  (`AnalysisService.compare_joint`, `JointContext` → `JointComparison`, `bills.joint_json` v20,
  re-asked via `compared_with` when the group grows; ~1 cent each, $4.21 for term 10's non-card
  candidates vs ≈$44 for the term), asked in `PublishingService._compared` just before rendering. It
  delays the reply until its batch answers (a manual republish too) but never stops it: a comparison
  that failed twice, or a model outage, is dropped and the reply goes out without it; waiting creates
  no publication and uses no attempt. Verdict, importance and category stay the card's. **No
  relevance gate decides an alternative bill**: the triage is not asked
  (`AnalysisService._joint_card_exists`) and a prefilter skip is revived
  (`joint.revive_prefilter_skips`, head of the analysis phase; found via
  `list_skipped_with_joint_prints`, since the `/processes` listing carries `printsConsideredJointly`
  — 117 of 1,665 rows of term 10). Eight term-10 groups had a dropped print beside a kept one, all the
  same bill by another applicant; reading them costs $0.71 a term. `/preview` renders the reply
  without the difference when none is stored. (`SKIPPED_JOINT` is gone; v21 maps it to
  `analysis_pending`.)
- **A document filed to a print is told once; the bill remembers.** `additional_prints` list
  filings; `models.supplement_kind` keeps the government's position, the OSR and an opinion with
  remarks, drops housekeeping. `bills.supplements_json` (v18) is what the channel was told, written
  last by `_remember_supplements` only when the print was read; None = never recorded, and first
  sight seeds it silently. Each new document is digested against the current analysis
  (`AnalysisService.digest_supplement`) after the change row exists, and its numbers go into
  `change_key` (else it collides with the previous change). A digest that could not be made (scan,
  refusal, over `LEXINFORM_MAX_ANALYSIS_COST_USD`) still names and links the document
  (`SupplementRecord.digest is None`). The reply is named by `models.supplement_event` when stages do
  not name it. A waiting digest leaves stages, seen supplements and delivery work uncommitted: mark
  the bill waiting as soon as a digest is queued (also when a later document fails) and clear it
  atomically with the completed observation; `/refresh` waits like a run.
