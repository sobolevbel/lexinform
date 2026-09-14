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
- **Every measured claim below can be re-run**: the documents are in `../lexinform-corpus` (not a
  git repo, 11 GB) — all 938 bill prints of term 10 with their text, 825 RCL projects with every
  reached stage and 9,208 of their files, the whole wykaz register, 826 orka submissions, and the
  stage tree of all 5,533 processes of terms 8–10. Start at its `INDEX.json`: it maps a druk
  number, an RCL id, a wykaz number (`wpl:UC104`) or an RPW number to the files, the labels and
  each other, and its `cases` are the ready-made sets (`print.scan`, `document.unknown`, …). A
  sample of size 45 is what most of the older numbers here rest on; the corpus is now the term.
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
`services/` (commands (the operator's `/analyze`, `/show`, `/preview`, `/refresh`, `/skip`,
`/unskip`, `/republish`, `/forget`, `/find`, `/status`), lookup (one
bill by number or reference, fetched and prefiltered on first sight; the CLI and the commands
share it), listener (the relay on the VPS), discovery,
rcl_discovery + rcl_projects, wykaz_discovery, sources (`TextSources` routes a bill to
`SejmTextSource`, `RclTextSource`, `SubmissionTextSource` (the RPW file on orka) or
`MetadataOnlySource`), documents (`TextLoader`, downloads routed by host),
text_prefilter, analysis (the bill, the triage, the amendments and the documents filed to a
print), signatories, publishing, `tracking/` (service, pre_print, rcl, wykaz,
linking, acts, consultations, agenda, posting, stages), pipeline) → `container.py` (manual wiring) →
`cli.py` (typer). Services import only ports, models and the pure modules (`keywords`, `sections`
incl. `TextBudget`, `agenda`, `authors`, `rcl_letters`, `concurrency`), never adapters; the
generic services (analysis, text prefilter, formatter, `next_phase`) never branch on the source:
they read `Bill.has_process`, `Bill.consultation`, `Bill.rcl`, `Bill.wykaz` and the
`TextSource` port. `tests/unit/` is one
module under test, `tests/scenario/` the whole pipeline over the fakes (a test that calls
`World()` belongs there, whatever it is about), `tests/integration/` the live systems. Tests
use fakes in `tests/fakes.py` and the `World` harness in `tests/harness.py`, which builds the real
`Container` from a `Settings` naming the fake hosts, so the wiring under test is the daily run's
(`container.py` is typed on the ports and builds each service once; `llm`, `extractor`,
`publisher_override`, `notifier_override` are the injection points) (arrange with
`add_bill`/`add_rcl_project`/`set_stages`/`touch`, act with `run`, assert on the report, the
publisher's records and `bill`/`publication`); HTTP adapters use `httpx2.MockTransport`. The fake
Sejm gateway answers per term like the API (a process, print or `/bills` entry exists only under
its own term) and records the term in `calls`. `FakePublisher` **is** `RenderingPublisher`: it
overrides only delivery, so every message a scenario test records was rendered by the production
formatter (shared with the container, dated from the test's clock) and `publisher.texts(kind)`
is what the channel would show — a bill has to carry the act or the consultation window a reply
is about, or the render raises the way it would in production. Tests follow arrange-act-assert and never touch
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
  **The print that only replies is never sent to the model.** The reply carries the card's
  verdict, its tags and its next step and none of its own, so the analysis of such a print is paid
  for and shown to nobody: druk 1933 cost 305,132 input tokens ($1.53, a seventh of everything the
  project had spent) for one. `services/joint.py::primary_of` is the question, asked by
  `PublishingService` and a phase earlier by `AnalysisService`, which answers it with
  `SKIPPED_JOINT` instead of a call. `list_publish_candidates` therefore lists these rows without
  an analysis; a `skipped_joint` row whose card has gone before the reply went out has no verdict
  to carry and goes back to `analysis_pending` (`/unskip` and `reset` do it by hand). An
  operator's `/analyze` does not pass through the skip — asking explicitly is a wish to have it —
  and `/preview` renders the reply rather than refusing it for want of an analysis.
- **`/bills` rows are refreshed by tracking only.** Discovery saves a submission for new bills;
  the reconciler (`tracking/pre_print.py`) re-reads `/bills` for pending RPW entries and for bills
  awaiting consultation results and compares new with stored (print assigned, withdrawn,
  `consultationResults` flipped). If discovery overwrote the row first, the flip would be lost.
  An entry the listing has simply *stopped* showing is a different fact, and the row is what tells
  them apart (`events._listing_says_withdrawn`): the reconciler reads the end of such an entry off
  its age (`_long_gone`, a year), and an entry can wait months in the Marszałek's "freezer", so
  «Проект отозван» stated a decision the applicant may never have taken. It is headed «Проект
  больше не отслеживается» and says the Sejm published no decision.
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
  `Bill.last_stage` is that top-level stage and never a child of it: children are the paperwork
  that followed the decision, so the newest node of a *flattened* tree is one of them — druk
  2842's card named the referral under the `Veto` node («направлен в комиссию ENM») and the word
  "вето" appeared nowhere on it. `last_stage_detail` adds the child back when it says something
  the parent does not (which committee, how the Sejm voted); a referral to
  `PLENARY_COMMITTEE_CODE` is not one of those.
- **The veto has a road of its own, and the Sejm's vote on it is a stage.** `Veto` carries the
  referral to the committee as its child, "Praca w komisjach nad wnioskiem Prezydenta" is a
  `CommitteeWork` indistinguishable by type from the work after the first reading (only
  `ANSWERED_IN_COMMITTEE`, read backwards over the tree, tells the two apart), and the vote is
  `PresidentMotionConsideration`. Its `decision` decides everything: "nie uchwalona ponownie"
  ends the road and renames the trailing `End`, "uchwalono ponownie" restores "Uchwalono" and
  leaves the motion as the last stage that says anything — phase `president_after_veto`, seven
  days from its date (art. 122 ust. 5, `PRESIDENT_DAYS_AFTER_VETO`). Before 2026-09-13 the type
  was unknown to `_phase_after`, which fell through to `if any(SenatePosition in top)` and put an
  overridden veto back at «Сейм рассматривает поправки Сената». There is no such fallback now:
  an unrecognised last stage gives no phase, and `_ended_line` only says «закон не принят» when
  the listing says `passed=false`.
- **"What comes next" is derived, not stored.** `models.next_phase(bill, today)` reads the
  top-level stages, submission and act; the formatter dates it from `bill.agenda` (upcoming
  sittings, refreshed every run for every followed bill, not only the changed ones) or from the
  constitutional deadline (`Phase.deadline`: Senate 30 days from the 3rd reading, President 21
  from receiving the act; 14/7 for urgent bills). A bill the government declared *pilny*
  (`models.is_urgent`, art. 123) is told in its own words throughout: `Labels.urgent_step_labels`
  and `urgent_durations` override the normal entries, so the card never promises a reader weeks
  where the Sejm measured days. **No date is printed once it has passed**: a deadline past
  `DEADLINE_GRACE_DAYS` says what its running out *meant* (`Labels.deadline_passed_labels`, and
  `urgent_deadline_passed_labels` first for a bill the Sejm measured in seven days rather than
  twenty-one) and the step drops the wording that promised it (`next_step_labels`' `_overdue`
  variant); a step that
  outlived `PHASE_PATIENCE` says how long it has been standing (`models.stalled_days`,
  `Phase.since` — which for an RCL project falls back to the stage's last modification, RCL
  leaving "rozpoczęcie" empty) — but **never a step that carries a date of its own**: a vacatio
  legis of a year is common, and «вступление в силу 01.07.2027 · без движения уже 7 мес.» said of
  a law that is published, final and dated inverts the message. And a sitting only dates a phase
  whose venue it matches: a
  committee's 08:30 slot is not a third reading, and a phase in `COMMITTEE_PHASES`/`SITTING_PHASES`
  takes a sitting only from that venue while a phase in neither (the Senate, the President, Dz.U.,
  a vacatio legis) takes none at all — the fallback to "the next item on the calendar" printed
  «подпись Президента · заседание Сейма № 65» over the constitutional deadline. The same rule governs the action line: a hearing whose application deadline has gone
  is not offered, and «до заседания» is dropped on the day of the sitting. A constitutional
  deadline is shown as the deadline of the body that is under it, never as a window the reader
  has: «решение до 04.10.2026», and the reminder (`tracking/deadlines.py`, one reply per bill and
  phase, v17, `decision_reminder_days` = 7) says the date is counted from the third reading and
  so runs a few days early — **but only where it is** (`Phase.deadline_exact`): `ToPresident` is
  the hand-over itself, so art. 122's twenty-one days run from a date the API gives, and
  apologising for an exact date teaches the reader to discount it. The Senate action carries no
  date at all — art. 121 gives the Senate thirty days and its committee takes the act long
  before they are out.
- **A term the Constitution makes zawity is a step, not a note.** Art. 121 ust. 2: thirty days
  gone with no uchwała from the Senate and the act counts as adopted in the Sejm's wording, and
  the Senate can neither extend nor suspend them — so `next_phase` moves the bill on
  (`_after_senate_silence` → `president_after_senate_silence`, no deadline of its own: the
  President's twenty-one days run from a receipt the API does not date, and a guess stacked on a
  guess is not worth a reminder). Annotating the Senate step instead made one line say both
  things: «рассмотрение в Сенате (до 30 дней) · 30 дней Сената истекли: закон считается принятым
  без поправок». This is the one place the bot names a step the Sejm has not published, so the
  wording says so, and it unwinds on the next run if the Senate did act and the listing was
  behind. Two bills get no Senate deadline from us at all (`_senate_days`,
  `_SENATE_SPECIAL_TERM`): the budget, where art. 223 gives twenty days, and a constitutional
  amendment, where art. 235 gives sixty — neither is in reach of this channel's keywords, but a
  date computed at thirty would be wrong and the silence rule would then fire too early. A step
  whose term is out never keeps a wording that promises it: `next_step_labels` takes an
  `_overdue` variant (and the urgent wording of art. 123, which names the shortened term, is
  dropped for it, `urgent_mode` still saying which mode the bill was in), and the expiry note
  itself comes from `urgent_deadline_passed_labels` first — it used to quote «21 день» to a bill
  the Sejm had measured in seven.
- **A live card is kept true; a finished one says so once and is then left alone.** Everything
  the card says about
  "now" is derived from the day it was rendered, so `tracking/cards.py::CardRefresher` re-renders
  the card of every followed bill each run and edits it in place when the text has drifted. The
  digest of what was last sent (`publications.rendered_sha256`, v16) makes a quiet run free: a
  pure render per bill, no request — **and drift is the only test there is**. The refresher used
  to stop at `next_phase(...) is None`, which fires exactly one run before the card would say the
  road had ended, so a bill the Sejm rejected kept a card reading «дальше: III чтение» and
  «можно сделать: написать в комиссию» for the life of the thread (product review, 2026-09-14).
  It now renders that last state too and the digest holds it there; `is_over` was never the test
  either — an act in Dziennik Ustaw with months of vacatio legis is still live, and freezing the
  card there left it saying «дальше: публикация в Dz.U.» for ever. `list_tracked` has to
  agree, or the refresher never sees the bill: the grace window runs from `closure_date`, which
  the Sejm sets at the third reading, and 2699's ended 35 days before its act applied, so a
  published act is followed until `entry_into_force`, whatever its age. The one ending
  `list_tracked` cannot reach is the lapsed term — it drops a row the moment `discontinued_at` is
  set — so `tracking/rollover.py` re-renders those cards itself (`Poster.rerender_card`, the
  method the linkers use to re-tag a thread that gained a number). The card calls itself
  finished only when the ending line can also say *how* (`_ended_line`): `next_phase` gives up on
  an unrecognised stage tree too, and "процесс завершён" over nothing is a guess. A veto the Sejm
  could not override **is** such a *how* and was missing from it: the API leaves `passed` true on
  a law a veto killed, so the closure branch never fired and the card kept the header «Новый
  законопроект» with no step lines at all, the word «вето» appearing only in the «Стадия» line.
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
- **A file keywords cannot search is not a file to drop.** The text prefilter scans the print
  of a title miss; when the file has no text layer but has pages, the bill goes to
  `analysis_pending` unsearched (`TextPrefilterResult.scans`, told apart in the run report from
  `unreadable`, which is now only a file with no pages either). Refusing it there was refusing
  the bill at both ends at once: the prints that arrive as scanned paper are the deputies'
  bills, and those are the ones whose titles say "o zmianie niektórych ustaw". The per-bill cost
  guard is what bounds the decision — at 1,600 tokens a page it stops a scan at ~250 pages — and
  the skip reasons say which threshold was missed, because "weak hits only" was printed over
  every kind of miss, a single strong pattern included. **And the prefilter asks the question the
  analysis asks**, not a cheaper one: `TextLoader` calls a file textless only under
  `MIN_TEXT_CHARS`, so a print whose text layer is the letter that hands it to the Marshal (700
  to 1,200 characters) arrived looking like a document, was searched for keywords a transmittal
  note never contains, and was skipped for good — **91 of the 938 prints of term 10**, every one
  with pages the model could have read. `carries_the_document` is now what both stages ask. The
  price of that is worth knowing, and it has been measured: the scans of the term are **317
  documents and 7,763 pages**, $62 on Opus, against the ~$44 the rest of it costs — and **a scan
  bypasses the triage**, because `_triage_verdict` wants `len(text) >= triage_min_chars` and a
  scan's `text` used to be the letter and that is what the gate measured, so the most expensive
  documents the project reads went straight to the most expensive model. **`_worth_triaging` now
  says yes to a scan whatever its text**, and the scan is shown `triage_scan_pages` (8) opening
  pages rather than all of them — cut from the bytes already downloaded
  (`TextLoader.first_pages`), so the file is never fetched twice. That is what makes the pass
  cheap on exactly the documents that are dear: the cost of the cheap call stops depending on how
  thick the paper is — **$0.013 for any scan**, eleven pages or three hundred and sixty-two — and
  it breaks even at a **7%** rejection rate. Asked of 18 real scans through this path on Haiku it
  rejected **18 of 18 correctly** at a mean confidence of 0.93: animal protection, drink-driving,
  hunting law, four commemorative resolutions. The window takes the scans' bill from $62 to $27 at
  the conservative 62% rate and to $16 at the lower bound the sample supports. What the sample
  cannot show is a false rejection, since it met no relevant scan; what guards against one is
  structural — the prompt says how many pages of how many are attached and asks for lower
  confidence rather than a guess, and an unsure verdict passes the bill on, so only a *confident*
  wrong "no" loses one.
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
  `has_news` gates the *Sejm* loop only: `RclWatcher` posts every change it records, because
  `rcl_fingerprint` already decides what counts (folder uploads alone do not). What a change with
  no new stage needs is a name, not a gate — `StatusChange.consultation_opened` gives one to the
  consultation that opens under a stage the timeline already shows as reached, which is the one
  moment a reader of a government project can act on and went out headed «Обновление».
  Every reply carries the topic tags: `_tag_line` does it for the kinds that have one, and the
  status update and the joint reply, which build their tag line by hand, must add
  `_topic_tags` themselves — they were the two that did not.
- **What the Sejm publishes is largely scanned paper, and a covering letter is not the document
  it transmits.** Measured with the project's own extractor (term 10, 12 Sept 2026): of 66
  documents filed to prints **none** carries readable text — 55 have no text layer at all and 11
  hold the Prime Minister's letter and nothing else (700–820 characters naming the bill and
  saying who will present the position, never what it is); of 39 prints, 11 are scans and druk
  604 is its letter and the signatures under it. "None" held for those 66 and not for the
  population: over 570 filings (14 Sept 2026) 521 have no text layer and 39 more are a covering
  letter, but **ten carry a document** — `1319-001`, `1528-004`, `3033-001`, `2883-005` are the
  OSR asked of a deputies' bill, 56k–80k characters over 19–25 pages, and `439-s`, `494-s`,
  `1676-s` the government's position. Those are the two kinds the channel is told about, so
  `digest_supplement` sometimes gets real text instead of paying per page. Those 800 characters passed `MIN_TEXT_CHARS`
  and read like a document, so `sections.carries_the_document` cuts the letter (at the page break
  or the heading after it, `without_cover_letter`) and asks whether anything is left — and then
  asks the same of the paper it came from: under 4,000 characters, a text thinner than 300
  characters a page is a photograph of pages and not their text (30 prints drawn at random from
  term 10 on 2026-09-12: the scan among them runs 139 characters a page, the thinnest real
  document 477, the median ~2,200). **and it is judged at any length**: there used to be a
  4,000-character ceiling above which density was not measured at all, on the reasoning that an
  appendix-heavy print extracts to little and is still text. Term 10 says otherwise — what runs
  long and thin is the OCR layer of a scan — and the ceiling let **30 prints** through as `text`:
  druk 703 is 155 pages with text on three (6,865 characters, diacritics gone:
  "norki amerykanskiej"), druk 204 is 268 pages with ten, druk 348 is 362 with sixteen, each read
  by the model as that fragment with nothing on the card to say so. They are scans now
  (`SCANNED_TEXT_CEILING` bounds nothing and is kept only for the record). Two of them (druki 204
  and 348) are more pages than `LEXINFORM_MAX_ANALYSIS_COST_USD` buys and are refused rather than
  half-read, which is the price of the rule and was decided with that in view.
  **`MIN_CHARS_PER_PAGE` is a proxy and the prints crowd right against it** — 306.2 characters a
  page for the thinnest kept (druk 1625) and 299.9 for the densest rejected (druk 205) — so the
  threshold was checked at that seam by a second signal that does not depend on it: walking the
  page's `/XObject` tree recursively, 205 carries a full-page raster on 6 of its first 10 pages
  and 1625 on none. The same check confirms druk 386 (278.6, and its OCR text reads like clean
  Polish) is really a scan, which reading the text cannot tell you. It does **not** catch druk
  348, 362 pages with text on 16 and no raster at all — vector or a font pypdf will not decode —
  so density stays the broader net and structure is the confirmation, not the replacement. The
  corpus holds both cases if this is ever worth building properly (`pdf_scan_classification_en.md`
  there sets out how; PyMuPDF is AGPL, which is its own decision). The gate
  sits in `AnalysisService._load_text`, the one place every document the model reads passes
  through, so the rule is one and not four. A file with no text but with pages goes to the model
  as pages instead (`ScannedDocument`, `text_source="scan"`, the API's document block — ~1,600
  tokens and $0.008 a page, and `pricing.estimate_scan_cost` guards by that, which is also what
  prices it before the call: asking the tokenizer would mean uploading the file to learn a
  number we can multiply out); what is kept of it
  is the file's `sha256`, standing where a text's digest would. A file with neither text nor
  pages (a Word file whose letter we could not cut off, an archive) keeps its text after all:
  there is nothing to fall back to, and a document of that length is not a covering letter.
  The extracted text comes back
  either way, because the letter is the one page of a scanned print that has a text layer and the
  card's club breakdown is parsed from it. Of the pages, only the letter's is dropped
  (`sections.scan_page_window`: exactly one page in all 15 government prints measured, and only
  when `sections.has_cover_letter` finds the transmittal formula — *any* text is not that
  finding, and a page dropped on a running head would be a page of the bill). Dropping it is
  not truncation: `ScannedDocument.truncated` counts only pages of the document itself that the
  model was not shown (`cover_letter_pages`), because the card's «неполный текст» and the
  prompt's instruction to lower confidence were otherwise on every scanned print there is. **Nothing else of a scan is trimmed, and the reason is
  measured.** A filed OSR is not the government's 13-point form — druk 1273's is the Sejm's own
  expertise (BEOS), sections I–XI, substantive from the first page, with the count of affected
  foreigners on page 10 of 30 — so a page budget cuts into the substance. A page map by a cheap
  model was built and removed on 2026-09-12: it labels the pages well (Haiku over the PDF, 30
  pages, $0.048), but it keeps 25 of those 30, so the document costs $0.245 mapped against $0.236
  whole. Mapping pays only where the appendices dominate, and those prints — the government's —
  carry a text layer and are trimmed by `trim_print` already; what reaches us as a scan is an old
  deputies' bill of a few dozen pages that is substance throughout.
- **A document filed to a print is told once, and the bill is what remembers.** The print's
  `additional_prints` say what has been filed; `models.supplement_kind` keeps the government's
  position, the OSR and an opinion with remarks and drops the housekeeping; `bills.supplements_json`
  (v18) is the list the channel has been told about, written last by `_remember_supplements` and
  only when the print was actually read. None means never recorded, and the first sight seeds it
  silently, the way the stage fingerprint is seeded — otherwise every followed bill would announce
  its whole history of filings after the deploy. Each new document is digested against the current
  analysis by one model call (`AnalysisService.digest_supplement`) *after* the change row exists,
  so a change an earlier run recorded never pays twice, and the numbers go into `change_key`: a
  document that arrives between two stages moves nothing else, and without them the row would
  collide with the previous change and be dropped. A digest that could not be made (a scan, a
  refusal, or a text over `LEXINFORM_MAX_ANALYSIS_COST_USD` — the OSR of druk 1273 is a 2.7 MB
  PDF, and somebody's opinion of a bill is not worth any price) still leaves the document named
  and linked (`SupplementRecord.digest is None`) —
  the run records it as told either way, so dropping it would lose it. The reply is named after
  the newest document when the stages do not name it (`models.supplement_event`): the government's
  position has a stage but no name of its own, and «Обновление» over the government's verdict
  was telling the reader nothing.
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
  there sends the rows to `/processes`): skip `get_process`/`get_print` for them. They are not
  textless, though: `TextSources` routes an RPW row to `SubmissionTextSource` (its file on orka),
  an RCL row to its documents, and only a register entry — a bill that has not been written yet —
  to the metadata; when the print appears,
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
  verdict instead. The print copies the entry's status, except a prefilter skip
  (`skipped_prefilter` or `skipped_text_prefilter`), which sends it to `text_prefilter_pending`:
  the entry's own text may have been missed by the keywords, but the file may equally have been a
  scan or refused by the WAF, and the reason cannot be read off the status — the print's PDF
  comes from the API, is the text the Sejm works from, and scanning it costs a download and no
  tokens. The RPW reconciler finds the print in `/bills`; for RCL, Sejm discovery notices a
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
  `/republish` is not: the marks are what keep a second card away. `/forget` is `/republish`
  without the post, for a card deleted from the channel by hand: the `sent` row is what every
  tracker joins on, so left alone it keeps the bill followed and the refresher keeps editing a
  message that is not there (Telegram's `message to edit not found` is a warning, not an error,
  so it repeats for ever), and sending the card again is the wrong answer when it was deleted on
  purpose. Both card kinds go and nothing is posted, so what the bill gets next is the publishing
  rule's decision — a fresh card if it is still a candidate, nothing if it is silenced — and
  running it twice changes nothing. The commands that only read
  (`/show`, `/preview`, `/find`, `/status`) never post and never spend: `/preview` renders the
  card into the technical channel alone, so the wording can be read before `/republish` sends
  it. `/refresh` is the tracking phase for one bill (`StatusTrackingService.check_bill`) —
  the Sejm does not wait for 05:23 UTC — and leaves the reminders to the scheduled run, which
  asks them of the whole channel; it is idempotent the way tracking is (the change row is
  unique). `/unskip` is the way back from `/skip` and from every other skip: a clean budget of
  attempts and `analysis_pending`, with a skipped RCL row's documents re-read *before* the
  status is cleared, or an unreachable RCL would leave the bill queued to be analysed on its
  metadata alone.
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
`SCHEMA_VERSION = len(MIGRATIONS)` (v18 as of Sept 2026). `migrate()` reads `user_version` and
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
President's 21 are each told once), v18 (Sept 2026) the documents filed to a print after its
submission — `bills.supplements_json` (which of them the channel has been told about) and
`status_changes.supplements_json` (the digests one update carried).

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
  `status`, `print`, `consultationResults`. Their text is a PDF on orka.sejm.gov.pl, at an
  address built by convention (`models.submission_pdf_url`, `LEXINFORM_ORKA_BASE_URL`), and it
  **is** downloadable, which the project denied until 2026-09-12: Imperva there refuses a
  `User-Agent` that names a bot (`curl/8.x`) and serves a browser, as long as the client follows
  the 302 and keeps the cookies it sets (`visid_incap_*`, `incap_ses_*`) — `adapters/orka.py`
  does both and is the one client for this host. Verified from a GitHub runner too
  (`.github/workflows/orka-probe.yml`), where the refusal arrives as **HTTP 200 text/html**, not
  403, so the body decides and not the status. A failure of this host is a per-bill problem on
  purpose (`OrkaUnreachableError`): it is WAF-guarded and address-judged, and everything else the
  analysis reads is api.sejm.gov.pl. Every outgoing client says the same thing about itself
  (`adapters/browser_identity.py`, Chrome 140 on Windows; measured: the WAFs score the kind of
  client, not the version), `Accept` aside, which each client sets for what it asks for.
  The API carries no link to the opinion form; the Sejm page is
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
- **The `/prints` listing is not authoritative about attachments; the print's own detail is.**
  For druki 599, 1768 and 2821 of term 10 (14 Sept 2026) every file the listing names answers
  404, while the detail names one that downloads (`599.pdf` is gone, `599-s.pdf` is there). Three
  of 3282 is nothing until a run builds its download URL from the listing and loses the print
  whole; the only way to notice is to ask the detail when the listing's files all fail.
  The listing does carry `additionalPrints` for all 847 prints that have any, so the whole
  catalogue of 2339 filings costs one request.
- `additionalPrints` in a print's detail are the documents filed to it after its submission, each
  a print of its own (`1273-001`, `1273-s`) with `title`, `documentDate`, `deliveryDate` and its
  own PDF, served from api.sejm.gov.pl like any attachment (no WAF in front, unlike the RPW
  PDFs on orka). Term 10, 12 Sept 2026: 2339 of them over 3282 prints — 289 "ocena skutków regulacji",
  82 "Stanowisko Rządu", 1732 opinions (563 saying "nie zgłoszono uwag" in the title itself),
  42 amendments tabled at the second reading, the rest housekeeping (a changed representative of
  the applicants, an extra list of signatures, an errata). Among prints that have any, the median
  is one opinion with remarks and the maximum sixteen. An autopoprawka is never one of them: it
  gets a print number of its own. Only the government's position is also a stage
  (`GovermentPosition`, with the document's title on it); the OSR and the opinions appear nowhere
  in the process tree, which is why the bill has to remember which of them it has been told.
- `rclNum` and `rclLink` exist only in a process's **detail**, never in the `/processes` or
  `/bills` listing (verified 2026-09-11): finding the print an RCL project became means reading
  details one by one, so `find_process_by_rcl_num` narrows by the hand-over date and caps the
  number of lookups. The other direction is one request (`getIdFromLegislacja`) — **and that
  endpoint counts them**: about 90 in a row and it answers 403 with a 292-byte body until left
  alone. A run asks it once per bill and never meets this, but a sweep must pause (1.5 s was
  enough for all 476 rclNums of term 10, of which 471 resolve and 5 answer 200 because they are
  genuinely not on RCL). A probe that keeps only the `Location` header cannot tell those apart:
  record the status beside it, or "throttled" reads as "not on RCL".
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
- **Much of it is scanned paper, and the text layer is a trap.** Sampled 12 Sept 2026 with the
  project's extractor: 66 documents filed to prints (0 with readable text, 55 empty, 11 holding
  only the covering letter) and 39 prints (27 with text, 11 scans, 1 cover-only). The model reads
  such a file as a PDF document block — 32 MB of request (so ≤ 24 MB of file, base64 being a
  third larger; druk 2865 is 40 MB and does not fit) and 600 pages, ~1,600 tokens a page measured
  with `count_tokens` on druk 1273 (10 pages 16,157 tokens, 30 pages 47,268, one page 1,622).
- **What `trim_print` meets in a print, measured over 45 of term 10 with a text layer**
  (13 Sept 2026). Reading the sections by `document_kind` rather than by a handful of headings
  takes another **15%** off what the model is sent across the whole sample (7.65M characters kept
  → 6.48M), and the bill, its uzasadnienie and "Art. 1." survive in every one of them: druk 1479
  keeps 35% of what it did, druk 1963 48%, druk 810 63%. The one print that keeps *more* is druk
  2670, whose OSR was being thrown away whole. The OSR is cut at point 6 in 16 of the first 25
  measured. Three of those 45
  carry **"DEKLAROWANE SKUTKI REGULACJI (DSR)"** instead of the OSR form — the deputies' version,
  unknown to the pattern until now, 15 pages of 60 in druk 2673 — and one heads its OSR
  "Tytuł projektu". Three was the sample: over all 938 bill prints of term 10 (14 Sept 2026,
  `page_index.json.gz` in the corpus) **180** open a page with the DSR — a form every fifth print
  carries, not a curiosity. **And the form was being dropped on 14 of them**: it is itself an
  attachment to a resolution of the Presidium of the Sejm, so it opens "Załącznik / do uchwały
  nr 51 / Prezydium Sejmu" and names itself only on the line after, while `_ANNEX_RE` matches the
  first two lines (`\s+` spans the break). `page_kind` therefore looks for the DSR heading past
  the two-line window — the one heading it does, because widening the window is what druk 810
  page 40 forbids — and the fix moved exactly 21 pages of 84,422 (14 `annex`, 7 `unknown`), costs
  +0.25% of the text sent over the whole term, and kept 40,758 characters of druk 1963 that used
  to go. In the whole corpus every "Załącznik do uchwały" is this masthead and nothing else.
  The DSR is recognised as the OSR section but **not cut**, on purpose: it has
  no fixed thirteen points and so no "point 6" to cut at, and its own headings are where its
  substance is ("Podmioty, na które wpływa projekt", "Wpływ projektu na wskazane podmioty" in
  druk 3035) — which is the part of an OSR this channel reads it for. Cutting at a guessed
  heading would take that and leave the rest. One (druk 1764) prints its club's name and site as the first line of all
  thirty pages, which stood in front of every section heading the trimmer looks for, so a page's
  running head and its number are stripped before it is classified
  (`sections.strip_page_furniture`). A bare "Załącznik" is deliberately *not* a section start:
  druk 2673 carries one on page 25 of 60 as a schedule of its own bill, and cutting there would
  take the rest of the bill with it; only "Załącznik do uchwały/rozporządzenia/raportu" is one.
- **A section heading opens a page; one found inside a page is prose that wrapped that way.**
  `_section_start` reads `document_kind` over the first **two non-empty lines** of the
  furniture-stripped page, not over its first 600 characters — `document_kind` searches rather
  than anchors, so anywhere in 600 characters means anywhere at all. Druk 810 page 40 is the bill
  ("Art. 156q. 1. Prezes Urzędu … w części A") and wraps so that its third line begins
  "załącznika do rozporządzenia nr 2019/947/UE": **119,420 characters of the bill were dropped as
  an appendix** and the model never saw them. Druk 545 page 10 lost its OSR to "Zgodnie z art. 5
  ustawy … o działalności lobbingowej" at character 298, druk 1638 the same way. One line is too
  few (the corpus then keeps 620k characters of appendices that name themselves on the second
  line, "Projekt" over "R O Z P O R Z Ą D Z E N I E"), three already reaches druk 810's wrap.
  **What stands above the heading is furniture, and widening the window is the wrong way to
  reach past it.** A ministry stamps a draft with its date and the committee it is going to —
  "Projekt z dnia 9 lipca 2026 r." over "Etap: materiał informacyjny na SKRM" — and two such
  lines put the heading on the third, out of the window, which `_page_opening` was narrowed to
  two lines precisely so as not to reach. Measured over `openings.json` (13 Sept 2026) the
  narrowing moved 12 of 147 documents to `unknown`, nine of them by the stamp and five of those
  the draft regulations of the ETIAS package — and for a regulation the cost is not a
  mislabelling: `_section_start` latches on `REGULATIONS` and drops everything after it, so a
  block read as `unknown` never latches and each draft's own OSR form (`Nazwa projektu`, a kind
  that is kept) re-opens the run and goes to the model. `strip_page_furniture` takes the stamp
  off the top the way it takes a running head, and the window lands on the heading;
  `page_kind` is the whole reading in one place. The two openings that stay `unknown` are a
  letterhead and a signature block, which say what they are in their body and not at their top —
  which is what the window is for.
- **A page break is a line break, and Python's anchors do not know it.** `^` and `$` in
  MULTILINE turn on `\n` alone; the pages are joined with `\f` and the extractor emits a page
  from its first glyph, so a print whose justification opens a page reads `…\fUZASADNIENIE\n`
  with no newline in front of the heading. Every line-anchored pattern in `sections` was blind to
  it. Measured over term 10 (14 Sept 2026): **683 of the 819 prints with a text layer hide at
  least one heading behind the form feed, and in 279 `_JUSTIFICATION_RE` finds no justification
  anywhere**. That is the one search `excerpts` makes over the joined text, so those 279 went to
  the triage — and to the cut a text over the per-bill limit is reduced to — as the head of the
  bill and not one line of the reasons for it. `_BOL`/`_EOL` are the rule in one place; teaching
  both anchors about the page break recovered 744,693 characters, and closed the same hole for
  525 documents of RCL, where DOCX is worse still because `document_text` collapses `\n\f\n` to
  `\f` on purpose.
- **The opening of a document is a page, and the first page that speaks wins.** `HEAD_CHARS` is a
  budget spent page by page, not a window over the joined text: a bill's first page on RCL runs
  550-1,150 characters, so a flat 1,200 read on into the second page where the uzasadnienie
  begins, and `_KINDS` tries `justification` before `bill`. Over the 9,208 documents of the
  corpus that cost **66 bills**, every one headed USTAWA on its own first page — and in
  `_pick_parts` such a member fills the justification role and the archive is left with no bill
  at all. A page that says nothing hands the budget on: 26 documents open on a ministry's stamp
  or a title sheet and name themselves on the page after.
- **After the OSR, no page is the bill or its uzasadnienie again.** A print runs letter, bill,
  uzasadnienie, OSR, appendices, in that order and once each. Every table of submitted comments
  labels each row "Uzasadnienie", so a page of one read as the bill's own justification and
  re-opened the kept run: druk 1424 sent **270,989** characters of a consultation table to Opus
  that way and druk 1677 **317,546**. The rule is tied to the OSR and not to the first dropped
  section because druk 810's uzasadnienie stands *before* its OSR, behind an appendix wrongly
  detected in front of it, and a blunter rule would lose it. Druk 1677 page 98 is why both rules
  are needed: it genuinely opens "Uzasadnienie", so no window saves it.
- **The OSR is cut at point 5, not point 6.** Point 5 ("Informacje na temat zakresu, czasu trwania
  i podsumowanie wyników konsultacji") is the roll of organisations the draft was sent to — 5,136
  characters in druk 1677, 10,187 in druk 1479 — and names nobody the bill affects; point 4
  ("Podmioty, na które oddziałuje projekt"), the one count of the affected a print gives, survives
  in all 26 prints of the corpus that have a point 5. Point 6 stays as the fallback. ~5k a print,
  ~2–3k an RCL package.
- **Net over the 45 prints (13 Sept 2026): 6,437,943 characters kept → 5,929,867.** The sum is not
  the point: it is ~588k of appendices out and ~157k of real bill text back in. The pages these
  rules were measured on are checked in as `tests/fixtures/sejm/page_starts.json` — **21 rows**,
  the individual pages each rule was derived from, not a page corpus. The page corpus is
  `../lexinform-corpus/sejm/term10/page_index.json.gz`: 84,422 pages of 914 prints, one row each
  with the kind `page_kind` gives it, built by the same call the fixture is (druk 810's pages 40,
  113, 182 and 388 come out identical). Asked of it (14 Sept 2026), **no print of term 10 now has
  an appendix detected before its uzasadnienie** — the druk 810 failure is closed across the term
  and not only on the print it was found on.

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
  2026): PDF 40%, DOCX/DOCM 43%, ZIP 7% (the whole package in one archive), legacy DOC 6%
  (`adapters/doc_text.py`, an [MS-DOC] piece-table parser over `olefile`
  reading the document, its footnotes and its endnotes, and the one paragraph property that tells
  the end of a table row from the end of a cell — Word writes both as 0x07, so without it a
  tabela zgodności arrives as one line of tabs),
  ODT rare; XLSX/MSG/XADES/RTF are tables of comments, e-mails, signatures and reports, not bill
  texts. Display names often lack the extension; the URL carries it. Unknown or damaged files
  fall back to metadata-only analysis. RCL's OSR is a separate Word form starting with "Nazwa
  projektu"; point numbers are list formatting, so `sections._OSR_CUT_RE` accepts the heading
  without "6.".
- **An archive is never sent as an archive, and what a file is is read from the file, not from
  its name.** A package is a "Projekt" folder in a file: it is unpacked, every member is read and
  asked what it is (`sections.document_kind`), and only the best bill, uzasadnienie and OSR go to
  the model (`document_text._pick_parts`). A nested archive is opened only when the bill is not
  outside it — the three seen were bundles of draft regulations, and the "letter.pdf +
  projekt.zip" shape is what the exception is for. Measured over the packages of seven followed
  projects and 45 prints of term 10 (13 Sept 2026), the name is the thing that lies:
  `projekt.docx`, `uzasadnienie.docx` and `OSR.doc` inside `akty_wykonawcze_ETIAS.ZIP` are draft
  **rozporządzenia**, `opiniaUE.pdf` and `Minister Zdrowia UD439 na SKRM.pdf` are letters,
  `Lista_kontrolna_na_KRMC_-_etias_.DOCX` is a checklist — and every one of them passed as the
  bill until its own opening was read. Archives written on Windows carry cp437 file names
  (`zaêÑcznik nr 2.docx`), which is one more reason no rule may rest on a name alone. The names
  still narrow the candidates before a download, and they stay measured, not guessed: an appendix
  is ruled out *before* the OSR is recognised, or "załącznik do OSR" stands in for it, and no
  pattern may be a word a bill can carry in its own subject — "protokół" of a ratification,
  "raportowanie" of a reporting duty, "opiniowanie" the stage a bill is published for (which is
  why "opinia" is word-bounded). UC104's package: 646k characters → 338k. Run over the "Projekt"
  folders of 80 harvested projects (13 Sept 2026) the name rule finds the bill in all 80, and the
  three it got wrong before are what the last patterns are for: an **autopoprawka** is an
  amendment to the government's own bill and was winning over it (a PDF outranks the package the
  bill comes in), an "opinia RL" and a "materiał uzupełniający" likewise, and one ministry files
  each part as an attachment to its letter and says which is which in a tag —
  `załącznik do pismo 07.08.2026 uzgodnienia [projekt].pdf`. The tag beats the rest of the name,
  being the one part of it that is about the document rather than about its envelope. A file that opens
  as an appendix is refused at the other end too (`AnalysisService._load_text`): describing a
  compliance table would describe the wrong document with every appearance of describing the
  right one. A *letter* is not refused there — every print opens with one.
- **A heading is matched on its whole line, and where the case is data it is respected.**
  `document_kind` reads the first 1,200 characters. `USTAWA` and `ROZPORZĄDZENIE` are matched in
  either case but **anchored at both ends**, and the anchor is what does the work: the
  justification of every act implementing an EU regulation wraps onto a line beginning
  "rozporządzenia 2018/1240", and an unanchored pattern read UC104's uzasadnienie as a draft
  regulation. Over the 168 openings of the fixture, ignoring case moves exactly one document, and
  it moves it right — a draft headed "Rozporządzenie" in title case that had passed for a bill's
  uzasadnienie on its file name alone. `Nazwa projektu dokumentu` is a tabela legislacyjna, one
  word from the OSR's `Nazwa projektu`. A kind we do not know is `unknown`, never an appendix:
  the file name decides then, as it did before, unless the file is longer than
  `LEXINFORM_MAX_PART_CHARS` (300k) — the longest real document measured is 161,678 characters
  and the longest nameless appendix 954,730.
- **A table of provisions is known by its columns, not by its title.** `Tytuł projektu` opens an
  OSR form and `TYTUŁ PROJEKTU` a tabela zgodności, but the case does not settle it and the title
  never did: UD439's OSR opens "Tytuł projektu" and druk 2670's "Tytuł projektu: ustawa o zmianie
  ustawy – Kodeks wyborczy", while druk 1430's derivation table heads itself "Tabelaryczne
  zestawienie przepisów rozporządzenia wykonawczego Komisji (UE) 2023/564" and only says
  "Tytuł projektu:" further down the same page. Matching case-blind threw both OSRs away — 78,281
  characters of UD439's, all of druk 2670's, and those are the documents that count who is
  affected. What separates them is that one is a table: `Tabelaryczne zestawienie przepisów`,
  `Jedn. red.` and `Treść przepisu` are its column headings and appear in no OSR form.
- **What the rule was measured on is checked in.** `tests/fixtures/rcl/openings.json` holds the
  opening of 147 real documents, with the kind each must be recognised as, and one
  table test runs `document_kind` over the lot. A new case is one row. It is a regression set and
  not the corpus: `../lexinform-corpus/candidates/openings-candidate.json` holds all 21,071
  openings collected, labelled by the current code — which is why they are copied in by hand and
  read first, never generated into the repo.
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
bill, uzasadnienie and OSR points 1–4 (pages are separated by `\f` by the extractor, and the kept
pages are rejoined with it: the page is the unit every rule in `sections` works in). Long texts
(≥ `triage_min_chars`) first get a triage on `sections.excerpts` (heads + windows around keyword
hits) by `llm_triage_model`; a confident "no" is stored as a non-relevant analysis with
`text_source="excerpts"`. Real numbers: druk 2695 (564k chars, irrelevant) cost $1.45 in full,
would cost ~$0.01 with the triage. The triage call runs without extended thinking (Haiku 4.5
rejects `thinking: adaptive`; a classification does not need it). The system prompts carry
`cache_control`; the analysis prompt alone is ~850 tokens, under the 1024-token minimum of a cache
entry, but the structured-output schema is part of the cached prefix, so the entry is ~2k tokens
and does get read (the state dump of 2026-09-09: 23k cache-read tokens over 11 analyses). The run
report's "cache read" figure and `lexinform cost` show it; `lexinform runs` lists the recorded
runs. **Every model call is written down** (`RunReport.llm_calls`: bill, kind — analysis,
reanalysis, triage, amendments, supplement — model and tokens, recorded by `AnalysisService.
_charge`, which every phase that asks the model goes through). One figure per run could not be
accounted for afterwards: the run of 2026-09-13 billed 316,767 input tokens with nothing to say
which call made them, and the report now names the three costliest. Guard rails
(`LEXINFORM_MAX_ANALYSIS_COST_USD`, default $2 per first analysis, estimated from the text length
at 2 chars/token before the call — verified against the real tokenizer on 13 Sept 2026, five
documents, 1.96–2.08; `LEXINFORM_MAX_RUN_COST_USD`, default $15 per run): **a text over the
per-bill limit is cut down to it, not refused.** The triage has already said the bill matters,
and `skipped_cost` left the reader with nothing and the operator with a `reset` to run by hand.
`_fit_to_budget` counts the real tokens, scales the characters by the overshoot the tokenizer
measured (so it lands inside the limit whatever the text tokenizes at, one re-count to confirm)
and rebuilds the text with `sections.excerpts` over the keyword hits — head of the bill, head of
the uzasadnienie, a window around every hit — marking it `truncated`. Under `_FIT_MIN_CHARS`
(20k) there is no document left and `skipped_cost` stands. A **scan** is still refused rather
than cut (its pages are substance from the first to the last), and for a re-analysis not even
that: a bill whose new text we decline to read must not keep a card describing the old one.
**Every refusal turns on `first`, and a re-analysis is never one** — not the scan, not a text
with nothing left to cut, and not an excerpt that counts over the limit the whole document was
scaled to fit (the live case: `excerpts` keeps keyword-dense provisions, which tokenize worse
than the ratio the whole text measured). `reanalyze_bill` is called from the tracking loop,
whose per-bill `except` has nowhere to put a refusal: the bill would fail on the same text every
run, with no `skipped_cost` row to `reset` and no `/unskip` to undo.
`TextBudget` is the outer cap only, and cuts the same keyword-aware way; the per-bill limit is
what binds. The cap gives the whole cap: each head takes a **quarter** of it, because
`excerpts` keeps both heads whatever it is asked for — at a half each they filled the budget
before the first keyword window was measured, so no hit was ever kept and a text with no
"Uzasadnienie" heading came back half the length allowed. What the windows leave unspent goes
back to the head (`excerpts(fill_head=True)`, which only a cap asks for: the triage digest is
paid for by the character, and a text whose keywords are few is one the cheap model should read
less of, not more). The analysis phase stops for the run once its spend reaches
the per-run limit (a note in the report, not an error; the rest waits for the next run).
Re-analyses get the per-bill guard too, in the same cut-to-fit shape — they had none until
2026-09-13, and the most expensive single call the project has made is one (316,767 tokens,
$1.58, an RCL package re-read). They count against the **run's** budget as well, and are held back
once it is reached (`AnalysisService.start_run` / `stopped`, a note in the report) — until
2026-09-13 the per-run limit bounded the analysis phase alone, and the tracking phase's
re-analyses, amendment summaries and supplement digests spent on top of it with nothing watching:
one re-analysis of the ETIAS package cost $1.63 in a run that consulted no limit at all. A held
text is not written down, so the next run offers the same document again; what it costs is that
the stage update of that run goes out without its «текст обновился» note, and the card catches up
when the refresher re-renders it.

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
own and stopped the card freezing while an act's vacatio legis runs; the second review that day
settled how the road *ends* — a veto the Sejm overrode is read from
`PresidentMotionConsideration` and gets the seven days of art. 122 ust. 5 (until then an
overridden veto read as «Сейм рассматривает поправки Сената»), a veto pending is a committee
phase, and a constitutional term that has run out says what running out meant instead of «срок
истёк». It also settled that a step outside the Sejm takes no sitting for its date, that a
committee is named and not coded on the card, that a status update carries the topic tags like
every other reply, that the Dz.U. address belongs on the card and a sentence of the summary on
the Dz.U. notice, and that the analysis never states where the bill stands — the card does, and
only the card is re-rendered (`PROMPT_VERSION` `2026-09-v7`; old cards keep their analysis).
The product review of 2026-09-12 settled the rest: a live card is
edited in place when what it says has drifted and a finished one is not; the «Важность» line
shows the score without the scale's legend, which read as a statement about the bill; a
sitting or a hearing is told once for a group of jointly considered prints, not once per
print; every reply carries the importance, category and topic tags, so a tag finds the
moments to act and not only the card; and a source that is unreachable (`/bills`,
`/proceedings`, RCL, the register) stops its own part of the run and nothing else.
Documents filed to a print (decided 2026-09-12): only the government's position on a bill it did
not write and the OSR are told, each with what it says — the position because it decides the
bill's fate, the OSR because it is the only count of who is affected that a non-government bill
gets (226 of the 285 filed OSRs go to deputies' bills; a government print carries its own, which
`trim_print` keeps points 1–5 of). Opinions are not: 1732 of the 2339 filings are opinions, 1.4
per print and 16 at the most, and "another body has written something" is the chronicle this
channel is not — with scans now readable that is a decision about noise, not about content, and
one line of `supplement_kind` away from being revisited. The housekeeping filings and the
amendments tabled at the second reading are not told either; the latter reach the reader through
the committee's report. The document is judged against the bill's analysis, not in place of it: a
filed document is what somebody makes of the bill, never a new version of it.
Text selection and cost (decided 2026-09-13, measured on the 45 prints and 10 RCL packages of
`../lexinform-corpus` and on the 46 analyses of the state branch, $10.87 spent to date of which
the top five bills are $6.45): the per-bill limit stays **$2** and stops refusing — a text over it
is cut down to it by the keywords and read, because the triage has already said the bill matters;
a print that will only get a `joint_bill` reply is not analysed at all; every model call is
written down so a run's spend can be accounted for. Three things were measured and **rejected**,
and should not be revisited without new numbers: a diff-based re-analysis of a new RCL redaction
(three consecutive packages differ by 63–116% of the new text's lines — the redactions are really
rewritten, so a diff is not smaller than the text); article-level selection inside the bill body
(`Art. N` with keyword hits kept: 21% over 15 prints, because the body is the minority of what is
kept, 14–68%, and a provision the keywords do not name would be lost); and changing
`CHARS_PER_TOKEN` (2.0 verified against the real tokenizer, 1.96–2.08 over five documents).
RCL package handling was checked and left alone: on all ten packages `_pick_parts` refused every
appendix by content and kept bill + uzasadnienie + OSR, $0.03–$0.84 a package.

**What a whole term costs, measured on the corpus (14 Sept 2026).** Of the 819 prints of term 10
with a text layer, 268 pass the keywords; their 132.9M characters become 47.5M after `trim_print`
(**35.8%**), which is **$118.83** on Opus. The triage is what the term actually costs: asked of
183 of them on the production path (`AnthropicAnalyzer.triage`, the production prompt and digest,
on Haiku for the measurement, $1.70 spent) it **rejects 62%** and takes $56.72 of $89.99 off that
sample — so **the term is ≈$44, not $118**, and the triage is the cheapest saving in the system by
a wide margin. What is left is not chaff: mapped page by page, the twelve most expensive
candidates are the law and its reasons, prose to the last page, with **zero** non-prose pages
among those kept. The bill is **$45.90 (38.8%)**, the uzasadnienie **$53.53 (45.2%)** — the
largest single line — and OSR points 1-4 **$18.86 (15.9%)**; twenty prints of the 268 carry 30% of
the bill. The per-bill guard binds **4 prints** and saves $3, so it is insurance and not economy.
Article-level selection stays rejected on new numbers: over all 268 candidates the body is a
**median 27%** of what is kept (quartiles 16% and 42%), which is the 14–68% of the old sample
confirmed, not overturned.

Two of the audit's three questions were settled on 14 Sept 2026. **The uzasadnienie is not
thinned**: it is the largest single line of the bill ($53.53 of $118.83) and it restates the act
article by article, but it is also where the card gets "what this is for", and there is no
measurement of what cutting it would lose. **`azyl` keeps matching "azyle dla zwierząt"**: over
the term the pattern decides the outcome for exactly two prints — druk 1861 (the Centralny Azyl
dla Zwierząt bill, a false positive worth $0.14) and druk 1268 ("osoby ubiegające się o azyl"
among vulnerable groups, a true one) — and both narrowings were measured and are worse than the
disease. Proximity to "cudzoziem|uchod|ochron" zeroes `azyl` on druk 1812, "o czasowym zakazie
wjazdu obywateli", which is a bill squarely on topic; excluding the named institution cuts 365
hits to 50 and **changes no outcome at all**, because 50 still clears a threshold of 3. The
keyword stage is over-inclusive on purpose — a false hit costs one call, a miss loses a bill for
good — and druk 1861 is rejected by the triage for about two cents anyway.

Open items are listed under "Still open" in `docs/roadmap.md`. The audit of 14 Sept 2026 over
the whole corpus closed six defects and left nothing of its own open. Its record is
`../lexinform-corpus/checks/`: `FINDINGS.md` for the findings with their numbers, `README.md`
for how it was run, which of the methods paid and what to do next time.
