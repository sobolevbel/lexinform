---
paths:
  - "src/lexinform/cli.py"
  - "src/lexinform/container.py"
  - "src/lexinform/settings.py"
  - "src/lexinform/concurrency.py"
  - "src/lexinform/commission_reviews.py"
  - "src/lexinform/models/commands.py"
  - "src/lexinform/models/digest.py"
  - "src/lexinform/models/batch.py"
  - "src/lexinform/models/reviews.py"
  - "src/lexinform/models/delivery_alert.py"
  - "src/lexinform/models/report.py"
  - "src/lexinform/services/pipeline.py"
  - "src/lexinform/services/commands.py"
  - "src/lexinform/services/listener.py"
  - "src/lexinform/services/digest.py"
  - "src/lexinform/services/document_reviews.py"
  - "src/lexinform/services/senate_documents.py"
  - "src/lexinform/services/amendments.py"
  - "src/lexinform/services/delivery_notifier.py"
  - "src/lexinform/services/cost.py"
  - "src/lexinform/adapters/llm_*.py"
  - "src/lexinform/adapters/batch_checkpoint.py"
  - "src/lexinform/adapters/github_inbox.py"
  - "src/lexinform/adapters/inbox_files.py"
  - ".github/workflows/**"
  - "deploy/**"
  - "tests/scenario/test_commands.py"
  - "tests/scenario/test_*batch*.py"
  - "tests/scenario/test_digest*.py"
  - "tests/scenario/test_*review*.py"
  - "tests/scenario/test_outages.py"
  - "tests/scenario/test_pipeline.py"
---

# Runs, operator commands, digest, batches

Command reference and the relay: `docs/operator-commands.md`.

- **Outages never burn per-bill attempts.** `ServiceUnavailableError` subclasses abort the phase and
  go to the log channel; anything else is a per-bill failure (3 attempts). Unknown model id and bad
  request parameters are fatal; "prompt too long" is per-bill. An unreachable source stops only its
  own part of the run.
- **Parallelism only around the network.** `concurrency.fan_out` runs one network step for many
  items and never touches the repository; outcomes are consumed on the calling thread in input
  order, where every DB write happens. `workers=1` default (tests); `workers=4` must be identical.
- **Every CLI command is a channel command**, except the machinery: `listen` (the relay),
  `commands` (the phase answering the inbox), `db init|dump|restore`. `models.OPTIONS` is the whole
  grammar (per option: bare flag or `key=value`, validation, what it becomes); options are checked
  where written (never by the workflow, which reads `since` as free text), unknown ones are answered,
  and `key=value` is an option only where the command knows the key (a Sejm link stays a link).
  **`models.DISPATCHED`** — `/run`, `/scan`, `/track`, `/reprefilter`, `/index-rcl-numbers` — are
  started by the relay as a `workflow_dispatch` of `daily.yml` on `main` with `command` (a `type:
  choice` kept in step with the enum by a test) and `options` (checked values passed through the
  environment, never interpolated). `dry_run` stays its own input (it also keeps the state branch
  unpushed). Needs a token with **Actions: read and write** (filing needs only Contents); 403/404 is
  reported as the missing scope. Reaching a run through the inbox, these are answered, not executed.
  `/run reprefilter=N` ≠ `/reprefilter limit=N` (backfill then a whole run; the concurrency group
  keeps one pending run). `/runs`, `/cost`, `/status` spend nothing; `/reset BILL to=STATUS`
  generalises `/unskip`.
- **Operator commands are recorded before they run; the relay confirms only what is filed.**
  Commands reach a run as `{update_id}.json` in the `inbox` branch (`LEXINFORM_INBOX_DIR`); the
  relay's event runs `lexinform commands`, every scheduled run does that phase first.
  `CommandService` inserts the `commands` row (v13) before executing, sets `executed_at` (v14) when
  side effects are done, answers (`OperatorReplier`), marks handled, deletes the file. A re-read file:
  handled → deleted; recorded not handled → **not re-run**, answered with what the row knows (the
  outcome, or that a run started it and did not finish). `/analyze` is idempotent; `/republish` is
  not (the marks keep a second card away). `/forget` = `/republish` without the post (for a card
  deleted by hand; Telegram's `message to edit not found` is only a warning). `/show`, `/preview`,
  `/find`, `/status` never post or spend; `/preview` renders into the technical channel. `/refresh` =
  tracking for one bill (`StatusTrackingService.check_bill`), leaving reminders to the scheduled run.
  `/unskip` returns any skip to `analysis_pending` with a clean attempt budget, re-reading a skipped
  RCL row's documents *before* clearing the status. A source or channel outage ends the phase,
  leaving the file. The relay (`lexinform listen`, one `getUpdates` consumer, never a webhook) files
  via the GitHub Contents API, answers "queued", then moves the offset; `--dry-run` confirms nothing.
  The run is started by `repository_dispatch` (a push of `inbox` starts nothing — that branch has no
  workflow); a lost event is drained by the next run. Manual `/analyze` follows the daily publish rule
  (`publish` overrides; `force` bypasses prefilter, previous analysis and cost guard). Replies are
  English (`MessageFormatter.command_reply`).
- **Operator delivery-alert cursors** advance only after a successful notification, survive
  retention and never change delivery identities or resolve `unknown`.
- **The weekly digest is never sent unread.** `services/digest.py`, its own phase after tracking;
  `Outgoing.bill` is None; its row uses `DIGEST_NUMBER` and **`DIGEST_TERM` = 0** (`create_publication`
  looks rows up by term, `ux_pub_digest` has none), keyed on the ISO week (v23); `Digest.ref` (`2026-W38`) is the week everywhere. The run
  only **drafts** it into the technical channel on the digest weekday **in Warsaw time**
  (`DigestService.today`); `/digest publish ref=…` posts the reader copy, and the draft's inline
  button files exactly that command (`command_for_callback`; acknowledged by `answerCallbackQuery`,
  `CommandAcknowledger.pressed`). **No technical channel → no digest**, asked of
  `telegram_log_channel_id` itself (`drafts_channel_id` falls back to the readers'). The week is
  rebuilt from the DB at publish time (a second press changes nothing); no model calls. Content: the
  week's `sent` posts (`list_publications_between`) plus what can still be acted on (past reminders
  and agenda posts are left out). One message (2026-10-22): ⏰ open windows, 📌 one line per bill
  (short name, step, meaning; decisions first), 🗓 sittings, a line of what the bot read/took/follows,
  a second line for the previous month and the support ask only in a month's first digest. Routine
  steps are owned and confirmed by the digest but not shown; sections capped (`DIGEST_MAIN_ROWS`, …)
  and shrunk to fit 4,096 characters. Names are today's; a card carded before names existed gets a
  `name` batch on `llm_joint_model` (`TrackingService._name`, `name_cards`) and is edited. The week is
  the last ISO week that **ended** (`current_ref`). An update is named by the change it was **written
  for** (`get_status_change`, `update_event`). The first digest of a month
  (`is_first_digest_of_month`, by the week's Sunday) counts rows *taken in*, never a phase's `seen`.
  Digest-only events stay queued until their digest part is confirmed; reserve `digest_event_links`
  before sending; retries and recovery act on the physical part, preserve its payload and membership,
  release only its held changes.
- **A batch request is written down before it is sent, its answer before it is applied.** Every
  model call except the triage and the change review goes to its model's batch API at half price
  (`require_batch`): analysis, re-analysis, amendments, digests, joint comparisons, card names —
  manual `/analyze`, `force`, `/refresh`, `/republish` included. Neither urgency nor waiting buys a
  synchronous call; `llm_batch_enabled`, `_kinds`, `_provider`, `_sync_within_days`,
  `_max_wait_hours` are legacy. A failed secondary batch is filed once more
  (`SECONDARY_BATCH_SUBMISSIONS`) unless the provider refused its context, then the post goes without
  it. Secondary requests use the memo key as custom ID (`batch_job` reads the newest submission) and
  pass through `analysis_memo` without changing bill status. The request is frozen in
  `llm_batch_intents` (payload, model, prompt, output cap) with a cost reservation, and the state
  branch is pushed before the provider is called (`GitBatchCheckpoint`). A `submitting` intent is
  never resubmitted automatically: the operator uses `attach-batch-intents` or
  `recover-batch-intent`. The bill waits as `batch_pending`; an answer applies only while
  `analysis_generation` matches (`/skip`, `/reset`, a newer text supersede it — paid and memoized,
  never published). Each batch is polled through the provider it was filed with; one failing item
  never hides the next; a collected re-analysis waits as `reanalysis_ready`. `awaiting_batch_since`
  (v32) keeps deferred observations eligible across discovery watermarks (queued time counts toward
  the deadline) and is cleared with the observation checkpoint. The VPS poller only hurries
  collection (`repository_dispatch: batch-ready`). A dry run submits nothing (only the triage is
  asked). `/status` names open batches and any `batch_pending` bill no batch holds.
- **Primary computation history** retains immutable validated `analysis`/`reanalysis` results,
  original usage and collection decisions with the consumed checkpoint (v49); past results are not backfilled.
  Only a projection receipt confirms primary-service application; neither phase confirms publication.
  Result IDs name provider items,
  request hashes name inputs; memo reuse never creates a new paid computation. See `docs/state-retention.md`.
  Services apply primary analysis only through `services.analysis_applications.apply_analysis`;
  its receipt uses the actual target generation and shares the caller's transaction. Quiet tracking
  of the same result adds no receipt; linking reuses its original computation ID.
- **Document reviews** use separate secondary batch purposes and exact source citations.
  Participant positions never prove acceptance, project analyses never prove final-act
  applicability; commission reviews across RCL, Sejm and Senate keep proposals, recommendations and
  decisions apart — minority motions never prove a committee recommendation, committee reports never
  prove a chamber decision. The first Sejm committee-material catalog seeds already-seen print ids
  without historical review work. Confirmed government outcomes need a complete response paragraph
  or row; incorporation needs dated before/after citations; pending contexts and the original
  comparison snapshot are immutable. Preserve pending review work, comparison snapshots and
  canonical history for the same ELI.
- **Amendment revisions** use the original message's saved bill, change and date (never rebuilt from
  the current bill): queue the batch with its immutable edit target, then a separate
  `amendments_revision` delivery; the original event and publication stay as audit history; repeated
  commands never supersede a pending batch or an attempted/uncertain revision delivery.
