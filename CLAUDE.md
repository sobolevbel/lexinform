# lexinform — notes for Claude Code

Daily bot: finds Polish bills that affect foreigners (Sejm API, legislacja.rcl.gov.pl for
government projects still with the ministries, and the wykaz prac legislacyjnych RM on gov.pl
for the ones the government has only announced), scores them 1–5 with an LLM, posts Russian cards
to a Telegram channel and follows each bill until the act is in force. The point is not a
chronicle but *timely action*: consultations, committee referrals, hearings, deadlines.
Everything else in `README.md`; roadmap and verified API facts in `docs/roadmap.md`; the whole
legislative process (RCL → Sejm → Senate → President → Dz.U.), its deadlines, the public's
windows and the API stage vocabulary in `docs/legislative-process.md`.

## Working rules

- Python 3.12, `uv`. Check with `uv run pytest -q && uv run mypy && uv run ruff check src tests`
  and `uv run ruff format src tests`. All three must be clean before a commit. mypy is strict over
  `src` and `tests`: no `type: ignore`, no local imports, tests fully typed.
- A comment earns its place only where the code cannot speak: a fact from outside the repo (an
  API quirk, a legal deadline, a measured number), an invariant a later edit would silently break,
  or why the obvious way was not taken. Never a restatement of the line below it, a divider
  (`# ---- helpers`) or a label over a group of fields or methods — names and docstrings do that
  work. What needs a paragraph is a function with a good name and a docstring, not a block with a
  comment over it; a comment that opens a function body and says what the function does is a
  docstring written in the wrong place.
- Developer guide (setup, tests, migrations, where a change goes): `CONTRIBUTING.md`.
- Commit after each finished part. Do not push unless asked. No `Co-Authored-By` trailers.
  A push of `main` deploys everything: the bot (every `daily.yml` run checks out `main`) and,
  after a green CI, the relay on the VPS (`deploy-relay.yml` → `deploy/update.sh` over SSH).
- `.env` holds real secrets and is untracked; never print values. `.env.example` mirrors keys.
  `Settings()` reads it, so a test that builds the real container would reach the real Telegram
  or model: `tests/conftest.py` blanks every credential for every test (autouse). Keep it that
  way; a CLI test's `env` sets the token and the log channel to "" explicitly as well.
- Messages to readers are Russian (labels in `i18n.py`, RU + EN); Polish law titles stay Polish.
- Prod state = SQLite dump in the `state` branch, written by `.github/workflows/daily.yml`
  (weekdays 05:23 and 16:23 UTC = 07:23 and 18:23 Warsaw in summer, weekend 10:23 UTC; GitHub
  starts every schedule 3–4.5 h late here, whatever the minute). To test against real data: `git show origin/state:lexinform.sql > /tmp/s.sql`,
  `LEXINFORM_DB_PATH=/tmp/t.db uv run lexinform db init && … db restore /tmp/s.sql`, then
  `lexinform run --dry-run --since YYYY-MM-DD` (real LLM calls, DB rolled back, prints to stdout).

## Architecture in one breath

`models/` (pydantic + pure helpers; `enums`, `sejm`, `rcl`, `wykaz`, `analysis`, `bill` (incl.
`next_phase`, `is_over`, `ConsultationWindow`), `report`, all re-exported from
`lexinform.models`) →
`ports.py` (Protocols) → `adapters/` (Sejm API, ELI, RCL scraper `rcl_html`, the register CSV
`wykaz_csv`, PDF, Word +
format sniffing `document_text`, Anthropic, `publisher_base` (the `Publisher` port rendered once;
Telegram and the console only deliver), Telegram (incl. `get_updates` and the command replier),
SQLite, `inbox_files` (the command inbox as a directory), `github_inbox` (the relay's writer)) →
`services/` (commands (the operator's `/analyze`, `/show`, `/skip`, `/republish`), lookup (one
bill by number or reference, fetched and prefiltered on first sight; the CLI and the commands
share it), listener (the relay on the VPS), discovery,
rcl_discovery + rcl_projects, wykaz_discovery, sources (`TextSources` routes a bill to
`SejmTextSource`,
`RclTextSource` or `MetadataOnlySource`), documents (`TextLoader`, downloads routed by host),
text_prefilter, analysis, signatories, publishing, `tracking/` (service, pre_print, rcl, wykaz,
linking, acts, consultations, agenda, posting, stages), pipeline) → `container.py` (manual wiring) →
`cli.py` (typer). Services import only ports, models and the pure modules (`keywords`, `sections`
incl. `TextBudget`, `agenda`, `authors`, `rcl_letters`, `concurrency`), never adapters; the
generic services (analysis, text prefilter, formatter, `next_phase`) never branch on the source:
they read `Bill.has_process`, `Bill.consultation`, `Bill.rcl`, `Bill.wykaz` and the
`TextSource` port. Tests
use fakes in `tests/fakes.py` and the `World` harness in `tests/harness.py`, which builds the real
`Container` from a `Settings` naming the fake hosts, so the wiring under test is the daily run's
(`container.py` is typed on the ports and builds each service once; `llm`, `extractor`,
`publisher_override`, `notifier_override` are the injection points) (arrange with
`add_bill`/`add_rcl_project`/`set_stages`/`touch`, act with `run`, assert on the report, the
publisher's records and `bill`/`publication`); HTTP adapters use `httpx2.MockTransport`. The fake
Sejm gateway answers per term like the API (a process, print or `/bills` entry exists only under
its own term) and records the term in `calls`. Tests follow arrange-act-assert and never touch
private attributes.

Invariants worth keeping:

- **The term comes from the API, the old term is drained, not dropped.** `LEXINFORM_TERM` is
  empty by default: `services/terms.py::TermResolver` takes the term flagged `current` in
  `/sejm/term` (newest term in the DB when the API is down; nothing known → the run stops with
  the reason). Discovery runs in the current term only; the repository listings (`list_by_status`,
  `list_tracked`, the due queries, `find_rcl`, …) are not scoped to a term: every `Bill` carries
  its own, and the trackers group by `bill.term` where an API path needs one (`/bills` in the
  reconciler, sittings in the agenda watcher), so acts of the old term still get their Dz.U. and
  in-force posts. Only the end-of-term methods take a term. The first run in a new term (`tracking/rollover.py`, its own phase *before*
  discovery) posts one "lapsed" update under every published, unfinished Sejm bill of the old
  term and sets `discontinued_at` on all unfinished rows (every listing filters on it); passed
  bills stay followed; RCL rows still waiting for their druk move to the new term (`bills`,
  `publications`, `status_changes`, `summary_json.term`), because the druk appears in the new
  Sejm and RCL discovery/joins look the project up by its term-less id. Idempotent, repeats
  every run. Citizens' bills get a different wording (they are taken over, and return as a new
  druk with a new card).
- **Pending-before-send.** Every Telegram post gets a `publications` row (`pending`) first, unique
  per kind/bill/channel (agenda posts: per kind/bill/channel/`ref`, one per sitting); failed posts
  are retried up to `max_publish_attempts`; `pending` left by a crash becomes `unknown` and is
  never auto-resent. The "due" queries (`list_due_in_force`, `list_due_consultations`) must keep
  listing a bill whose post `failed`, otherwise the retry never happens (`Poster.posted` decides).
- **Jointly considered prints share one thread.** `ProcessSummary.prints_considered_jointly`
  names the other prints on the same subject (one committee report for all of them; their stages
  coincide from the joint referral on). `PublishingService` gives the group one card: a candidate
  whose partner already has a `sent` card that is still followed (not discontinued, not
  withdrawn/rejected) gets a `joint_bill` reply under that card instead (`Publisher.publish_joint_bill`,
  `MessageFormatter.joint_bill`: title, applicant, date, links, both tags; no analysis), recorded
  pending-before-send and unique per bill/channel (v12), and settles the bill like a card would
  (`list_publish_candidates` excludes both kinds). Within one run the government's print goes
  first (`government_first`): its text is usually the one the committee works on. The reply's bill
  has no `new_bill` row, so every tracker (they all join on a `sent` card) ignores it: the group's
  events come from the card's process, the card's analysis is redone when the joint text appears.
  `republish` forgets both rows and lets the normal path decide again.
- **`/bills` rows are refreshed by tracking only.** Discovery saves a submission for new bills;
  the reconciler (`tracking/pre_print.py`) re-reads `/bills` for pending RPW entries and for bills
  awaiting consultation results and compares new with stored (print assigned, withdrawn,
  `consultationResults` flipped). If discovery overwrote the row first, the flip would be lost.
- **The last stage is not the last node.** `models.process_stages` is what `next_phase` and
  `Bill.last_stage` read: top-level stages minus `ASIDE_STAGE_TYPES` (`GovermentPosition`,
  `Opinion` — they arrive beside the process) **and minus a trailing `End`**. The Sejm appends
  "Uchwalono" at the third reading and keeps it last while the Senate, the President and Dz.U.
  are all still ahead (druk 2799, read 2026-09-12: III czytanie "uchwalono" 2026-09-04, `End`
  already there, no Senate stage, no act). Taking it for the current step collapsed the whole
  Senate → President segment into `publication`, so the card marked «Сенат ✓ → Президент ✓» and
  offered «пока ничего» over the reader's last two windows, and the `SenatePosition`,
  `ToPresident`, `Veto` and `PresidentToTribunal` branches were dead code. The `End` of a bill a
  veto killed ("nie uchwalona ponownie", `models.veto_stood`) stays and ends the road.
- **"What comes next" is derived, not stored.** `models.next_phase(bill, today)` reads the
  top-level stages, submission and act; the formatter dates it from `bill.agenda` (upcoming
  sittings, refreshed every run for every followed bill, not only the changed ones) or from the
  constitutional deadline (`Phase.deadline`: Senate 30 days from the 3rd reading, President 21
  from receiving the act; 14/7 for urgent bills). A bill the government declared *pilny*
  (`models.is_urgent`, art. 123) is told in its own words throughout: `Labels.urgent_step_labels`
  and `urgent_durations` override the normal entries, so the card never promises a reader weeks
  where the Sejm measured days. **No date is printed once it has passed**: a deadline past
  `DEADLINE_GRACE_DAYS` says «срок истёк», a step that outlived `PHASE_PATIENCE` says how long it
  has been standing (`models.stalled_days`, `Phase.since`) instead of quoting an average, and a
  sitting only dates a phase whose venue it matches (a committee's 08:30 slot is not a third
  reading). The same rule governs the action line: a hearing whose application deadline has gone
  is not offered, and «до заседания» is dropped on the day of the sitting. A constitutional
  deadline is shown as the deadline of the body that is under it, never as a window the reader
  has: «решение до 04.10.2026», and the reminder (`tracking/deadlines.py`, one reply per bill and
  phase, v17, `decision_reminder_days` = 7) says the date is counted from the third reading and
  so runs a few days early. The Senate action carries no date at all — art. 121 gives the Senate
  thirty days and its committee takes the act long before they are out.
- **A live card is kept true; a finished one is left alone.** Everything the card says about
  "now" is derived from the day it was rendered, so `tracking/cards.py::CardRefresher` re-renders
  the card of every followed bill each run and edits it in place when the text has drifted. The
  digest of what was last sent (`publications.rendered_sha256`, v16) makes a quiet run free: a
  pure render per bill, no request. The refresher stops at `next_phase(...) is None`, **not** at
  `is_over`: an act in Dziennik Ustaw with months of vacatio legis is still live, and freezing
  the card there left it saying «дальше: публикация в Dz.U.» for ever. The card calls itself
  finished only when the ending line can also say *how* (`_ended_line`): `next_phase` gives up on
  an unrecognised stage tree too, and "процесс завершён" over nothing is a guess.
- **A bill whose road ended before we saw it gets neither an analysis nor a card.** A card
  invites action, and there is none left. `models.is_over(bill, today)` decides for every source:
  over means the act *applies*, the bill was rejected or withdrawn, the RCL
  project was closed without reaching the Sejm, the plan was realised or taken off the wykaz, or
  the term lapsed — `next_phase` finding nothing ahead, with a Sejm bill whose stages were never
  read counting as unknown, not over. `closureDate` alone is *not* the end: the Sejm sets it at
  the third reading, with the Senate, the President and Dz.U. still ahead (druk 2799: closed
  2026-09-04, `passed`, no act), so discovery reads the stages once for a bill it sees for the
  first time with a closure date (`_ended_before_first_sight`) and stores the skip as
  `skipped_closed` (`reset --to analysis_pending` revives it). The ELI address is not the end
  either: an act published with a long vacatio legis (druk 2699: Dz.U. 2026-08-18, in force
  2026-11-19) is the one stretch where a reader has a fixed date to prepare for, so discovery
  reads the act too (one request, the row keeps it — the publishing gate asks the same question a
  phase later and without it would drop the card). An ELI the API has not indexed yet still
  counts as the end. RCL discovery decides from the
  timeline, before the catalogs, and the wykaz from the entry's status. Bills already followed
  are untouched by this: they keep their card and their updates to the end. The last gate is
  `PublishingService.publish_new`, for a bill analysed while it was still running.
- **Not every stage is a post.** `models/events.py`: `is_substantive` separates the events a
  reader cares about (referral, committee report, vote, Senate, President, hearing, a decided
  reading) from the frame nodes (`Start`, `ReadingReferral`, `Reading`, `CommitteeWork`,
  `ToPresident`, `End`); `has_news` decides whether a detected change is posted now. A change of
  frame nodes only is *held*: its `status_changes` row exists (dedupe) with a `skipped`
  `status_update` publication, and `Poster.status_update` prepends the held stages to the next
  post and marks their rows `sent` with its message id. A closure detected in the same run as the
  act's ELI is held too: the Dziennik Ustaw notice tells it. `update_event` names the header
  after the newest stage (`Labels.update_headers`); the closure line is dropped when the header
  already says it. Amendments (Senate resolution print, a committee report whose proposal is about
  poprawki) are summarised by a third model call (`AnalysisService.summarize_amendments`) after
  the change row exists and stored on it (`amendments_json`); a failure degrades to the bare event.
- **A re-analysis needs a new text, not a new URL.** `AnalysisRecord.text_sha256` is the digest
  of the normalised text the model saw (`services/analysis.py::text_digest`: page numbers and
  whitespace ignored). `reanalyze_bill` returns None and only repoints `source_url` /
  `source_checked_at` when the new document hashes alike (a file republished on RCL under every
  stage, a print re-dated by an attachment such as stanowisko rządu). `SejmTextSource.newer`
  compares the print's `changeDate` with `source_checked_at`, and does not even download the
  text after the 3rd reading when `models.third_reading_kept_the_text` holds (2nd reading went
  straight to the 3rd, no "-A" report, `minorityMotions == 0` on the report): the Sejm adopted
  the analysed text verbatim. Unknown facts (motions not parsed, no 2nd reading) mean "may
  differ" and the text is read.
- **Stage fingerprint** (`_stage_key`) drives updates. Fields added to `Stage` for rendering
  (`voting`, `position`, `committee_name`, `proposal`) must stay *out* of the key, or every tracked
  bill posts a spurious update after deploy.
- **Migrations are append-only.** `dump()` writes `PRAGMA user_version`; `restore()` migrates old
  dumps. Test every migration against a v1 dump (see `test_sqlite_repo.py`).
- **Outages never burn per-bill attempts.** `ServiceUnavailableError` subclasses abort a phase
  and go to the log channel; everything else is a per-bill failure (3 attempts). Unknown model id
  and bad request parameters are fatal, "prompt too long" is per-bill.
- Pre-print bills (`RPW/…`), RCL projects (`RCL/{id}`) and wykaz entries (`WPL/UD408`) have no
  Sejm process (`has_process` tests one tuple of prefixes, `NON_SEJM_PREFIXES`; a prefix missing
  there sends the rows to `/processes`): skip `get_process`/`get_print` for them; when the print appears,
  the print inherits the card (`tracking/linking.py::Linker`: `new_bill` row aliased with the same
  `message_id`). **A bill fetched on request is linked in whichever direction it is named**, and
  the druk is what comes back: it is the bill with the text. `BillLookup._fetch` splits per kind
  — an RCL project asks `find_process_by_rcl_num` (the term's listing walked once) for the print
  its `rm_number` names; an RPW entry already carries `print` in its `/bills` row; a druk asks
  `/bills?print=N` (one request, the applicant and consultation dates come with it) for the entry
  it continues, and its own `rclNum` for the project (`discovery.NOT_FOLLOWED`: a skipped or
  already linked row has no thread, so its druk takes the normal path, and RCL being unreachable
  leaves the print standing on its own). An entry whose act is already in force must
  never get a card promising a druk number, and a druk must not get a second card next to the
  entry's: a print linked outside tracking inherits the entry's card in `PublishingService`
  (`_inherited_card`, the same alias `Linker` makes, and the card is re-rendered with both tags).
  A command posts no card at all for a bill `models.is_over` calls finished, and answers with the
  verdict instead. The print copies the entry's status, except `skipped_prefilter`: an RPW entry has
  no text to scan, so its print goes to `text_prefilter_pending` instead of inheriting the skip
  (otherwise every non-government bill with a neutral title would bypass the text stage). The RPW
  reconciler finds the print in `/bills`; for RCL, Sejm discovery notices a
  druk whose `rclNum` names a followed project (stored RM number, else
  `getIdFromLegislacja?number=…`), stores the druk number on the RCL row and the RCL watcher links.
- **RCL rows are refreshed by the RCL watcher only.** RCL discovery reads a project once (timeline
  + catalogs for a candidate, one catalog for a title miss) and afterwards only bumps
  `change_date` from the list; `RclWatcher` re-reads the timeline and the catalogs whose "Data
  ostatniej modyfikacji" moved, and `rcl_fingerprint` (stages reached, folders that got their
  first files, the newest text, status, hand-over) decides whether there is an update. Folder
  uploads alone are not news; published opinions are a separate `consultation_results` reply.
  A page takes ~10 s: never add a request per project without a reason. A project the prefilter
  skipped keeps only its skeleton (`RclProject.without_documents()`, applied by
  `set_status` on a skipped status): the documents are most of the row and are never read again;
  `lexinform reset … --to analysis_pending` re-reads them. Run records older than
  `LEXINFORM_RUNS_RETENTION_DAYS` (90) are deleted at the start of a run.
- **The wykaz is the earliest source and the thinnest: an intention, not a bill.** The whole
  register arrives as one CSV per run (`adapters/wykaz_csv.py`; the id in the URL is read from
  the page, columns are matched by prefix because their statutory wording gets repunctuated), so
  the prefilter sees all of it, but only entries published since the watermark are stored:
  `Data publikacji` never moves on an edit, so a rejected entry is never revisited, and storing
  the 775 bill entries with their paragraphs would add ~2.3 MB to a state dump that is 822 KB.
  The rest is `report.wykaz_backlog`, and `scan --since` is the way to take it. Only
  `Projekty ustaw` are followed; a plan already realised or withdrawn on first sight never gets a
  card (there is no action left to invite), and neither does one whose project is already on RCL.
  The card says there is no text yet, and the one action it offers is the art. 7 zgłoszenie
  zainteresowania — which anyone may file, and which is the ticket to the Sejm's wysłuchanie
  publiczne (art. 8 ust. 2). The register is also the only source that says the government
  **dropped** a project (`Status realizacji`, `Informacja o rezygnacji`, art. 3 ust. 3): that is
  posted, a slipped quarter or a rewritten "istota" is only stored. `Planowane przyjęcie przez
  RM` is free text and half of it carries the adoption note: only the quarter is ever rendered.
- **A plan's project is stamped, not ingested.** RCL discovery has the wykaz number on the list
  page: when it names a followed `WPL/` row it writes `rcl_project_id` on that row and skips the
  project. `tracking/wykaz.py::WykazLinker` then creates the `RCL/` row, hands the card over
  (alias with the same `message_id`) and **re-analyses from the documents** — the plan was judged
  on an announcement, and that judgement must not decide the fate of the row that has the text.
  Ingesting the project in discovery instead would post a second card: `publishing`'s inheritance
  keys on a link that does not exist until the linker runs.
- **RCL markup is parsed, not matched.** `adapters/rcl_html.py` uses CSS selectors; a missing
  detail (date, folder, link) is tolerated, a missing structural element (timeline, table with
  rows announced, every stage label) raises `RclPageError`, which the run report shows. The WAF's
  "Request Rejected" page (HTTP 200) is `RclUnavailableError`.
- **Operator commands are recorded before they run, and the relay confirms only what is filed.**
  The technical channel's commands (`docs/operator-commands.md`) reach a run as
  `{update_id}.json` files in the `inbox` branch (checked out by `daily.yml`, `LEXINFORM_INBOX_DIR`);
  the relay's event runs `lexinform commands` (the commands phase alone), every scheduled run
  does the phase first. `CommandService` inserts the `commands` row (v13, keyed by the
  Telegram update id) before executing, marks it executed (v14 `executed_at`) as soon as the
  side effects are done, answers under the command's message (`OperatorReplier`), marks it
  handled and deletes the file. A file read again is measured against the row: handled
  → only deleted; recorded but not handled (the channel was down, or the job itself died)
  → the command is **not** run a second time, it is answered with what the row knows — the
  recorded outcome when it was executed, and otherwise a note that a run started it and did
  not finish, which the operator answers by sending the command again.
  `/analyze` is idempotent by construction (an analysed bill is not sent to the model again),
  `/republish` is not: the marks are what keep a second card away.
  An outage of a source system *or of the channel* ends the phase and leaves the file, with the
  counters of the commands already answered intact. The relay (`lexinform listen`, one `getUpdates`
  consumer per bot, never a webhook) files a command through the GitHub Contents API, answers
  "queued" and only then moves the offset; a dry run (`--dry-run`) confirms nothing. The run is
  started by a `repository_dispatch` the writer sends after the file (a push of the inbox branch
  would start nothing: GitHub reads a push event's workflow from the pushed branch, which has
  none); a lost event costs nothing, the next scheduled run drains the inbox. The publish rule of a manual `/analyze` is the daily run's (relevant and score
  ≥ `min_score`; `publish` overrides, `force` bypasses the prefilter, a previous analysis and
  the per-bill cost guard). Replies are English (the operator's channel), rendered by
  `MessageFormatter.command_reply`.
- **Parallelism only around the network.** `concurrency.fan_out` runs one network step (download,
  process lookup, model call) for many items; that step never touches the repository. Outcomes
  are consumed in the calling thread, in input order, and that is where every DB write happens.
  Services default to `workers=1` (tests); `workers=4` must give identical results.

## Database versioning and migrations

The schema version is SQLite's `PRAGMA user_version`; the source of truth is the `MIGRATIONS`
tuple in `adapters/sqlite_repo.py`. Script at index `i` brings the database to version `i + 1`;
`SCHEMA_VERSION = len(MIGRATIONS)` (v17 as of Sept 2026). `migrate()` reads `user_version` and
runs every later script inside its own transaction, stamping the new version at the end, so a
failed script leaves the database at the previous version. v8 (Sept 2026) added `rcl_json`, v9
`bills.discontinued_at` and `status_changes.discontinued` (end of a Sejm term), v10
`bills.linked_wykaz_number` (the print continuing an RCL thread keeps the wykaz number for the tag),
v11 the unique index of hearing reminders (per bill, channel and hearing date) and
`status_changes.amendments_json` (the model's summary of the Senate's or a committee's amendments),
v12 the unique index of `joint_bill` replies (per bill and channel), v13 (Sept 2026) the
`commands` table (operator commands by Telegram update id), v14 `commands.executed_at` (a
command whose answer never arrived is answered again, not executed again), v15 (Sept 2026)
`bills.wykaz_json` (entries of the wykaz prac legislacyjnych RM, `WPL/UD408` rows), v16
`publications.rendered_sha256` (the card as last rendered, so a run can tell a card that has
drifted from one that is still true without asking Telegram), v17 the unique index of the
constitutional-deadline reminders (per bill, channel and phase: the Senate's 30 days and the
President's 21 are each told once).

How state travels: the daily workflow runs `db init` (fresh schema at the current version) →
`db restore state/lexinform.sql` → `run` → `db dump`. `dump()` is `iterdump()` plus a trailing
`PRAGMA user_version = N;`. `restore()` drops all tables, replays the dump with foreign keys off,
sets `user_version` from that trailing line (1 when a legacy dump has none) and calls `migrate()`.
So a dump written by an older release is upgraded on the first run of the new one; nothing manual.

Adding a migration:

1. Append one string to `MIGRATIONS`; never edit or reorder earlier entries (deployed dumps carry
   their version). Plain SQL only: `ALTER TABLE … ADD COLUMN` (nullable or with a default), new
   tables, indexes, `UPDATE` backfills. SQLite cannot change a column's type or constraints: for
   that, create the new table, `INSERT … SELECT`, drop and rename.
2. Extend the model and `_row_to_bill` / mapping code. JSON columns (`summary_json`,
   `analysis_json`, `stages_json`, …) are pydantic dumps: new fields need defaults so old rows still
   load; renaming a JSON field is a data migration (SQL `json_set` or a one-off Python step).
3. Extend `test_restore_of_a_v1_dump_applies_every_later_migration` in
   `tests/unit/test_sqlite_repo.py` (it restores a hand-built v1 dump and asserts the new columns)
   and, before pushing, run the real dump through it: `git show origin/state:lexinform.sql`,
   `db init` + `db restore`, then `sqlite3 file "PRAGMA user_version"` and `lexinform show 2699`.

There is no downgrade. To roll back, revert the code and restore the previous dump from the
`state` branch history; the state branch is the backup.

## Sejm API lessons (verified live, Sept 2026)

- `/sejm/term` lists every term: `num`, `from`, `to` (absent for the running one), `current`
  (true for exactly one), `prints.count/lastChanged`. Term 10 started 2023-11-13; the flag is the
  signal for the switch, print numbers restart at 1 in the new term.
- `/processes` only lists bills that already have a print number. Bills at the consultation
  stage live in `/bills` (`RPW/…`), with `publicConsultationStart/EndDate`, `applicantType`,
  `status`, `print`, `consultationResults`. Their PDF on orka.sejm.gov.pl is behind Incapsula: not
  downloadable. The API carries no link to the opinion form; the Sejm page is
  `www.sejm.gov.pl/Sejm10.nsf/agent.xsp?symbol=KONSULTOWANY_PROJEKT&NrProjektu=RPW/29075/2026`
  (browser only: www.sejm.gov.pl answers curl and fetchers with an F5 captcha). That page only
  *links* the form: the opinion is a survey (ankieta) at `opiniowanie.sejm.gov.pl/RPW-29075-2026`
  — the RPW number with dashes (verified 2026-09-13; the pattern holds for every RPW tried).
  `models.consultation_survey_url` builds it, and the card and the reminder link it directly
  rather than the page it hangs on. Sending the survey means signing in (it redirects to
  `logowanie.sejm.gov.pl`), but Profil Zaufany is one of the ways in and the readers use it:
  decided 2026-09-13 that the messages say nothing about it. Only deputies',
  president's, Senate, committee and citizens' bills have Sejm consultations; government bills
  (541 of 1279 in term 10) were consulted on RCL before submission and never have them.
- Sittings: `/committees/{code}/sittings` items have `num`, `date`, `startDateTime`, `room`,
  `status` (PLANNED|FINISHED), `agenda` (HTML `<div class="agenda-indent-N">` lines, prints as
  "druk nr 3035" / "druki nr 3010 i 3055"), `video[].playerLink`, `jointWith`. `/proceedings`
  lists sittings (`number` 0 for planned ones without agenda); `/proceedings/{n}` adds `agenda`
  (HTML `<li>` items with `PrzebiegProc.xsp?nr=` links that are not reliable: match the text).
- `modifiedSince`/`changeDate` are naive **Europe/Warsaw** times; `Z` is rejected. `sort_by`,
  `passed` filters are ignored; paginate by `offset` until an empty page. `documentType` needs
  the Polish display string ("projekt ustawy"), the enum `BILL` does not filter.
- `passed=true` means adopted by parliament, not in force (vetoed bills too). Publication is
  signalled by `ELI`/`displayAddress`; details from `/eli/acts/DU/{year}/{pos}`: `promulgation`
  = Dz.U. date, `entryIntoForce`, `announcementDate` = date in the act's title. Publication
  follows the Sejm vote by ~30–40 days.
- `closureDate` is the **Sejm's** closure, set at the third reading (and on a rejection or a
  withdrawal), not the end of the road: the term-10 listing (2026-09-12) has 938 bills, 498
  closed with an act, 84 closed and `passed` with no act (druk 2799 closed 2026-09-04, the
  Senate's 30 days only starting; the older ones are vetoes, a referral to the Tribunal, bills
  the President never signed) and 47 closed with `passed=false` (odrzucono/wycofano). The
  listing carries `closureDate` and `passed`; an open process never has the date. `End`
  ("Uchwalono") is appended at the third reading and stays last, so it says nothing about how
  far the bill got — read the stages before it.
- `rclNum` and `rclLink` exist only in a process's **detail**, never in the `/processes` or
  `/bills` listing (verified 2026-09-11): finding the print an RCL project became means reading
  details one by one, so `find_process_by_rcl_num` narrows by the hand-over date and caps the
  number of lookups. The other direction is one request (`getIdFromLegislacja`).
- Committee reports with print `…-A` (proposal "przyjąć poprawki") are amendment tables, not
  bill text; only `proposal` containing "projekt" carries the text. `SenatePosition` reports via
  `position`, not `decision`. `UE` enum is NO|ADAPTATION|ENFORCEMENT.
- `Voting` stage embeds totals; per-club breakdown needs `/votings/{sitting}/{n}` (per-MP votes).
  Signatories are not in the API: parse the print's cover letter and match against `/MP`.
- Polish text is ~2 characters per token for Claude; the 1M context takes any print whole.
- `HEAD` on a print attachment returns no `Content-Length` and takes 5–15 s on a file the
  server has not rendered yet (the following `GET` is fast). Never probe sizes: stream the `GET`
  and stop at the limit (`download(url, max_bytes=…)`).
- pypdf needs `pypdf[fonts]` (fontTools) for CFF fonts, otherwise it logs a warning per font per
  page; its logger is capped at ERROR. Extraction is CPU-bound (~1 s per 100 pages).

## RCL lessons (verified live, Sept 2026)

- `legislacja.rcl.gov.pl` has no API/RSS; unknown query params (`pSize=all`, `modifiedDateFrom`)
  and blocked clients get HTTP 200 with `<title>Request Rejected</title>`. Plain `curl` works
  from Poland. **GitHub-hosted runners cannot reach it at all** (verified 2026-09-09 with
  `.github/workflows/rcl-probe.yml`): DNS resolves to 157.25.193.140, the TCP SYN to :443 is
  dropped (45 s connect timeout, no SYN-ACK), for every User-Agent and every path, while
  api.sejm.gov.pl answers in 1.7 s from the same runner (Azure northcentralus, US). A network
  level block of the IP range or the country, not the WAF. The first list request is therefore a
  20 s single-attempt probe (`RclClient(probe_timeout=…)`), so a blocked run loses seconds, not
  four minutes. Reaching RCL from CI needs an EU egress (proxy or self-hosted runner).
- List: `/lista?typeId=2&sKey=modifiedDate&sOrder=desc&pSize=100&pNumber=N` (2619 bills;
  `pSize` 10/50/100); wykaz numbers come as `UC164`, `UD424`, `UD 247`, `UDER66`, `UPRO6`.
- Project page: `div.rcl-title`, `div.info` rows (Wnioskodawca, Data utworzenia, Działy, Hasła,
  Status `otwarty`, Numer z wykazu, EU note, Kadencja `X`), timeline `ul.cbp_tmtimeline li[id]`
  with icon classes `cbp_tmicon_notstart` / `cbp_tmicon` (reached) / `cbp_tmicon_active`,
  "Data ostatniej modyfikacji", optional "rozpoczęcie"/"zakończenie" (unreliable). Stage 14 links
  `sejm.gov.pl/…?symbol=RPL&Id=RM-0610-139-26`. A quarter of the projects skip "Konsultacje
  publiczne" (only uzgodnienia + opiniowanie); every stage republishes the text in its own
  "Projekt" folder. ~10 s per page.
- Stage catalog `/projekt/{id}/katalog/{stageId}`: `div.clearbox > ul > li.childdir` folders
  ("Projekt", "Pisma kierujące…", "Stanowiska zgłoszone…", "Odniesienie się wnioskodawcy…"),
  `li.doc > a[href=/docs//…/dokumentN.ext]`. Files in "Projekt" folders (40 projects, Sept
  2026): PDF 40%, DOCX/DOCM 43%, ZIP 7% (the package in one archive, read member by member, bill
  first), legacy DOC 6% (`adapters/doc_text.py`, an [MS-DOC] piece-table parser over `olefile`
  reading the document, its footnotes and its endnotes, and the one paragraph property that tells
  the end of a table row from the end of a cell — Word writes both as 0x07, so without it a
  tabela zgodności arrives as one line of tabs),
  ODT rare; XLSX/MSG/XADES/RTF are tables of comments, e-mails, signatures and reports, not bill
  texts. Display names often lack the extension; the URL carries it. Unknown or damaged files
  fall back to metadata-only analysis. RCL's OSR is a separate Word form starting with "Nazwa
  projektu"; point numbers are list formatting, so `sections._OSR_CUT_RE` accepts the heading
  without "6.".
- Consultation letters give a relative deadline ("w terminie 7/14 dni od dnia otrzymania
  niniejszego pisma", 30 for social partners), often no date (electronic time stamp) and the
  e-mail for comments ("na adres: …"). `rcl_letters.parse_letter` reads them; the deadline counts
  from the letter date or the day the letter appeared on RCL. Every project also has a comment
  form `/projekt/{id}/komentarz` (captcha).
- Join: `/processes` `rclNum="RM-0610-81-26"`, `rclLink=…/getIdFromLegislacja?number=…` →
  302 to `/projekt/{id}`.

## Wykaz prac RM lessons (verified live, 12 Sept 2026)

- The register page `gov.pl/web/premier/wplip-rm` renders client-side; the whole register is one
  file, `gov.pl/register-file/Rejestr_{id}.csv`, and the id is in the page as `registerVue-{id}`
  (20874195 then). 10.5 MB, 2.8 MB gzipped, 1454 rows, `;`-separated with quoted multi-line
  paragraphs. `ETag` and `If-None-Match` work (304, 0 bytes), but there is nowhere to keep the
  tag, so every run downloads it.
- 19 columns; `Rodzaj dokumentu` splits 775 Projekty ustaw / 357 rozporządzeń / 322 inne. Statuses:
  403 empty (in progress), 1018 Zrealizowany, 31 Wycofany, 2 Niezrealizowany. Of the bill entries
  ~19 match the project's keywords in the whole register — 2–5 a year.
- `Data publikacji` is the **first** publication and does not move when an entry is edited; the
  entry's own gov.pl page keeps a version history instead (UD408 is at 2.0, edited 18.08.2026).
- Numbers are all but unique: UC168 is the same project entered twice a day apart, two `Podgląd`
  URLs. The later publication wins.
- Header wording drifts ("o przyczynach i potrzebie **wprowadzenia** rozwiązań" in one export,
  without it in another) and two headers start with "Organ odpowiedzialny", so columns are matched
  by the longest prefix.
- Lead time over RCL, measured: UD408 (o zmianie ustawy o cudzoziemcach, MSWiA) entered the
  register 2026-05-12 and appeared on RCL 2026-07-06 (`/projekt/12412103`) — 55 days. RCL shows
  its number as "UD 408", with a space.
- `www.gov.pl` resolves to one Polish address (185.32.48.49), not a CDN — the shape that turned
  out to be blocked for RCL — but **GitHub-hosted runners reach it fine** (verified 2026-09-12
  with `.github/workflows/wykaz-probe.yml`, Azure eastus2): the page answers in 0.57 s, the CSV
  in 10.1 s uncompressed and 3.1 s / 2.8 MB with `Accept-Encoding: gzip`, which httpx sends by
  default. So no proxy is needed; `LEXINFORM_WYKAZ_PROXY_URL` stays as the escape hatch if that
  ever changes.
- The stage is genuinely actionable: art. 7 ust. 1 of the ustawa o działalności lobbingowej —
  "z chwilą udostępnienia w BIP programów prac legislacyjnych … **każdy** może zgłosić
  zainteresowanie pracami nad projektem", with the organ that prepares it; art. 8 ust. 2 makes
  the filing the ticket to the Sejm's wysłuchanie publiczne. Caveat to check before the card
  names a form: the RM regulation with the official form (Dz.U. 2011/1080) was repealed on
  2026-08-28 by Dz.U. 2026/160, so the mechanics must be read from the consolidated text
  (Dz.U. 2026/936).

## LLM cost model (Sept 2026)

Opus 5 is $5/M input; output is ~1% of the bill. A government print is bill + uzasadnienie + OSR
(13-point form) + appendices (consultation report, tabela zgodności, draft regulations with their
own uzasadnienie/OSR), and the appendices are 55–80% of the text. `sections.trim_print` keeps the
bill, uzasadnienie and OSR points 1–5 (pages are separated by `\f` by the extractor). Long texts
(≥ `triage_min_chars`) first get a triage on `sections.excerpts` (heads + windows around keyword
hits) by `llm_triage_model`; a confident "no" is stored as a non-relevant analysis with
`text_source="excerpts"`. Real numbers: druk 2695 (564k chars, irrelevant) cost $1.45 in full,
would cost ~$0.01 with the triage. The triage call runs without extended thinking (Haiku 4.5
rejects `thinking: adaptive`; a classification does not need it). The system prompts carry
`cache_control`; the analysis prompt alone is ~850 tokens, under the 1024-token minimum of a cache
entry, but the structured-output schema is part of the cached prefix, so the entry is ~2k tokens
and does get read (the state dump of 2026-09-09: 23k cache-read tokens over 11 analyses). The run
report's "cache read" figure and `lexinform cost` show it; `lexinform runs` lists the recorded
runs. Guard rails
(`LEXINFORM_MAX_ANALYSIS_COST_USD`, default $2 per first analysis, estimated from the text length
at 2 chars/token before the call; `LEXINFORM_MAX_RUN_COST_USD`, default $15 per run): a text
over the per-bill limit gets `skipped_cost` with the reason in `last_error` (`lexinform reset
--to analysis_pending` revives it), the analysis phase stops for the run once its spend reaches
the per-run limit (a note in the report, not an error; the rest waits for the next run).
Re-analyses are not estimated: a new version of a text that already passed must not leave the
card behind.

## Product decisions already taken

Default model `claude-opus-5`, `min_score` 3, text prefilter threshold 2 distinct patterns or 3
hits (weak patterns such as Straż Graniczna, "legalizacja" or "nierezydent" never decide alone:
in a text they count next to a strong pattern, in a title they send the bill to the text stage,
not to the model; everyone's registers and benefits (PESEL, mObywatel, NFZ, 800+, prawo jazdy,
Kodeks wyborczy) are deliberately no patterns: measured on the 1500 processes of term 10 each
would cost 4–14 full analyses of unrelated bills, while a bill changing them for foreigners names
the foreigners and the text stage catches it), triage of texts ≥ 20k chars on
`claude-sonnet-5` (all of Haiku 4.5 / Sonnet 5 / Opus 5 judged the four test bills correctly;
Haiku ignored the output language, Sonnet costs ~1 cent per bill), club breakdown on, Dz.U. notice as a separate reply, in-force reminder repeats
the summary. The owner does **not** want a "probability of passing" estimate. A new `PROMPT_VERSION` does
**not** re-analyse or re-post bills already in the channel (decided 2026-09-08): old cards keep the
analysis they were published with, only new texts trigger a re-analysis. Every card and update
carries "what comes next" (dated by scheduled sittings) and "what you can do now" (decided
2026-09-09); a rescheduled sitting is announced again as a new post. RCL (decided 2026-09-09):
every relevant government project is followed, not only those with an open consultation; the
consultation deadline and e-mail are parsed from the letter deterministically, no LLM; RCL cards
tell readers to write in Polish and quote the wykaz number. Prints considered jointly (decided
2026-09-10, after druki 1929/1933 got two near-identical cards): one card per group, the later
prints are short "alternative bill" replies under it, the government's print is preferred for the
card. Abbreviations (MSWiA, UdSC, ZUS, PESEL) stay Polish in the analysis, never МВД. The wykaz prac
RM (decided 2026-09-12): planned bills get a card of their own, headed "План правительства" and
saying above everything else that there is no text yet; the RCL project inherits that card when
it appears (one thread from the plan to Dz.U.); only `Projekty ustaw` are followed; the
government dropping a project is posted. Operator
commands (decided 2026-09-11): from the technical channel, any admin of it; delivered by a relay
on the owner's mikrus VPS (384 MB: enough for a getUpdates loop, not for the bot itself) into
the `inbox` branch, executed by GitHub Actions so the state branch stays the only database
writer; a manual `/analyze` publishes under the daily rule unless told `publish`. Bills found
when their road is already over (decided 2026-09-12): no card and no analysis, whatever the
source — but only when there is really nothing ahead (the act is out, the bill was rejected or
withdrawn, the project or the plan was dropped), and a bill the Sejm has merely passed keeps its
card, because the Senate and the President are the reader's last windows; the last stage is read
to tell the two apart. The product review of 2026-09-13 added the two constitutional deadlines as reminders of their
own and stopped the card freezing while an act's vacatio legis runs. The product review of
2026-09-12 settled the rest: a live card is
edited in place when what it says has drifted and a finished one is not; the «Важность» line
shows the score without the scale's legend, which read as a statement about the bill; a
sitting or a hearing is told once for a group of jointly considered prints, not once per
print; every reply carries the importance, category and topic tags, so a tag finds the
moments to act and not only the card; and a source that is unreachable (`/bills`,
`/proceedings`, RCL, the register) stops its own part of the run and nothing else.
Open items are listed under "Still open" in `docs/roadmap.md`.
