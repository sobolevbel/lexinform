# lexinform — notes for Claude Code

Daily bot: finds Polish bills that affect foreigners (Sejm API; legislacja.rcl.gov.pl for
government projects still with the ministries; the wykaz prac legislacyjnych RM on gov.pl for the
ones only announced), scores them 1–5 with an LLM, posts Russian cards to a Telegram channel and
follows each bill until the act is in force. The point is not a chronicle but *timely action*:
consultations, committee referrals, hearings, deadlines.

This file is what every session reads: working rules, architecture, invariants, schema, API facts
and the decisions already taken. Four references are **not** loaded with it and are read when the
modules they describe are touched — `docs/legislative-process.md` (the whole road RCL → Sejm →
Senate → President → Dz.U., its deadlines, the public's windows, the API stage vocabulary),
`docs/text-selection.md` (what of a document reaches the model: `sections.py`, `document_text.py`,
scans), `docs/rcl-scraping.md` (RCL's markup and files, the wykaz CSV, the probes),
`docs/llm-cost.md` (prices, guard rails, what a term costs). Roadmap and still-open items in
`docs/roadmap.md`; everything else in `README.md`.

## Working rules

- Python 3.12, `uv`. `uv run pytest -q && uv run mypy && uv run ruff check src tests` and `uv run
  ruff format src tests` — all clean before a commit. mypy is strict over `src` and `tests`: no
  `type: ignore`, no local imports, tests fully typed.
- A comment earns its place only where the code cannot speak: a fact from outside the repo (an API
  quirk, a legal deadline, a measured number), an invariant a later edit would silently break, or
  why the obvious way was not taken. Never a restatement of the line below it, a divider (`# ----
  helpers`) or a label over a group of fields or methods — names and docstrings do that work. What
  needs a paragraph is a function with a good name and a docstring, not a block with a comment over
  it; a comment that opens a function body and says what the function does is a docstring written
  in the wrong place.
- Developer guide (setup, tests, migrations, where a change goes): `CONTRIBUTING.md`.
- **Every measured claim below can be re-run** against the corpus — a separate 11 GB checkout, not
  a git repo, beside this one as `lexinform-corpus`; the paths below are relative to it. It holds
  all 938 bill prints of term 10 with their text, 825 RCL projects with every reached stage and 9,208
  of their files, the whole wykaz register, 826 orka submissions, and the stage tree of all 5,533
  processes of terms 8–10. Start at `INDEX.json`: it maps a druk number, an RCL id, a wykaz number
  (`wpl:UC104`) or an RPW number to the files, the labels and each other, and its `cases` are the
  ready-made sets (`print.scan`, `document.unknown`, …). A sample of size 45 is what most of the
  older numbers here rest on; the corpus is now the term.
- Commit after each finished part. Do not push unless asked. No `Co-Authored-By` trailers. A push
  of `main` deploys everything: the bot (every `daily.yml` run checks out `main`) and, after a
  green CI, the relay on the VPS (`deploy-relay.yml` → `deploy/update.sh` over SSH).
- **Commit messages are English**, subject and body alike — the code, the comments and every
  document in the repository are English, and the history is read beside them. Messages to
  readers are the exception and are Russian, which is what `i18n.py` is for.
- `.env` holds real secrets and is untracked; never print values. `.env.example` mirrors keys.
  `Settings()` reads it, so a test that builds the real container would reach the real Telegram or
  model: `tests/conftest.py` blanks every credential for every test (autouse). Keep it that way; a
  CLI test's `env` sets the token and the log channel to "" explicitly as well.
- Messages to readers are Russian (labels in `i18n.py`, RU + EN); Polish law titles stay Polish.
- Prod state = SQLite dump in the `state` branch, written by `.github/workflows/daily.yml`
  (weekdays 05:23 and 16:23 UTC = 07:23 and 18:23 Warsaw in summer, weekend 10:23 UTC; GitHub
  starts every schedule 3–4.5 h late here, whatever the minute). To test against real data: `git
  show origin/state:lexinform.sql > /tmp/s.sql`, `LEXINFORM_DB_PATH=/tmp/t.db uv run lexinform db
  init && … db restore /tmp/s.sql`, then `lexinform run --dry-run --since YYYY-MM-DD` (real LLM
  calls, DB rolled back, prints to stdout).

## Architecture in one breath

`models/` (pydantic + pure helpers; `enums`, `sejm`, `rcl`, `wykaz`, `analysis`, `bill` (the
aggregate, `ConsultationWindow`, `process_stages`/`veto_stood`, the two bookkeeping rows),
`phases` (the road: `Phase`, `next_phase`, `is_over`, the deadlines and the patience table),
`events`, `report`, all re-exported from `lexinform.models`)
→ `ports.py` (Protocols) → `adapters/` (Sejm API, ELI, RCL scraper `rcl_html`, the register CSV
`wykaz_csv`, PDF, Word + format sniffing `document_text`, Anthropic, `publisher_base` (the
`Publisher` port rendered once; Telegram and the console only deliver), Telegram (incl.
`get_updates` and the command replier), SQLite, `inbox_files` (the command inbox as a directory),
`github_inbox` (the relay's writer)) → `services/` (commands (the operator's `/analyze`, `/show`,
`/preview`, `/refresh`, `/skip`, `/unskip`, `/republish`, `/forget`, `/find`, `/status`), lookup
(one bill by number or reference, fetched and prefiltered on first sight; the CLI and the commands
share it), listener (the relay on the VPS), discovery, rcl_discovery + rcl_projects,
wykaz_discovery, sources (`TextSources` routes a bill to `SejmTextSource`, `RclTextSource`,
`SubmissionTextSource` (the RPW file on orka) or `MetadataOnlySource`), documents (`TextLoader`,
downloads routed by host), text_prefilter, analysis (the bill, the triage, the amendments and the
documents filed to a print), signatories, publishing, `tracking/` (service, pre_print, rcl, wykaz,
linking, acts, consultations, agenda, posting, stages), pipeline) → `container.py` (manual wiring)
→ `cli.py` (typer).

Services import only ports, models and the pure modules (`keywords`, `sections` incl. `TextBudget`,
`agenda`, `authors`, `rcl_letters`, `concurrency`), never adapters; the generic services (analysis,
text prefilter, formatter, `next_phase`) never branch on the source: they read `Bill.has_process`,
`Bill.consultation`, `Bill.rcl`, `Bill.wykaz` and the `TextSource` port.

`tests/unit/` is one module under test, `tests/scenario/` the whole pipeline over the fakes (a test
that calls `World()` belongs there, whatever it is about), `tests/integration/` the live systems.
Fakes are in `tests/fakes.py`; the `World` harness in `tests/harness.py` builds the real
`Container` from a `Settings` naming the fake hosts, so the wiring under test is the daily run's
(`container.py` is typed on the ports and builds each service once; `llm`, `extractor`,
`publisher_override`, `notifier_override` are the injection points) — arrange with
`add_bill`/`add_rcl_project`/`set_stages`/`touch`, act with `run`, assert on the report, the
publisher's records and `bill`/`publication`. HTTP adapters use `httpx2.MockTransport`. The fake
Sejm gateway answers per term like the API (a process, print or `/bills` entry exists only under
its own term) and records the term in `calls`. `FakePublisher` **is** `RenderingPublisher`: it
overrides only delivery, so every message a scenario test records was rendered by the production
formatter (shared with the container, dated from the test's clock) and `publisher.texts(kind)` is
what the channel would show — a bill has to carry the act or the consultation window a reply is
about, or the render raises the way it would in production. Tests follow arrange-act-assert and
never touch private attributes.

Invariants worth keeping:

- **The term comes from the API, the old term is drained, not dropped.** `LEXINFORM_TERM` is empty
  by default: `services/terms.py::TermResolver` takes the term flagged `current` in `/sejm/term`
  (newest term in the DB when the API is down; nothing known → the run stops with the reason).
  Discovery runs in the current term only; the repository listings (`list_by_status`,
  `list_tracked`, the due queries, `find_rcl`, …) are not scoped to a term: every `Bill` carries
  its own, and the trackers group by `bill.term` where an API path needs one (`/bills` in the
  reconciler, sittings in the agenda watcher), so acts of the old term still get their Dz.U. and
  in-force posts. Only the end-of-term methods take a term. The first run in a new term
  (`tracking/rollover.py`, its own phase *before* discovery) posts one "lapsed" update under every
  published, unfinished Sejm bill of the old term and sets `discontinued_at` on all unfinished rows
  (every listing filters on it); passed bills stay followed; RCL rows still waiting for their druk
  move to the new term (`bills`, `publications`, `status_changes`, `summary_json.term`), because
  the druk appears in the new Sejm and RCL discovery/joins look the project up by its term-less id.
  Idempotent, repeats every run. Citizens' bills get a different wording (they are taken over, and
  return as a new druk with a new card).
- **An announced sitting is taken back when it goes.** The agenda post is the most time-critical
  thing the channel sends — what a reader plans a day around, and what the card's "what comes next"
  is dated from — and a sitting that left `PLANNED`, or an agenda the bill dropped out of, used to
  vanish from `bill.agenda` in silence. `AgendaWatcher._retract_gone` posts one `agenda_cancelled`
  reply per announced sitting that is gone (v19, unique per bill/channel/`ref` — the announcement's
  own key). Three things it is not: a sitting that merely **moved** keeps its `sitting_key` and is
  told as a new agenda post saying what it moved from; a sitting that has **started or passed** is
  never retracted (`_already_happened`, the same test that stops it being announced); and a listing
  that **failed** is not an absence (`_Listings.kept` puts those items back, for a Sejm sitting
  whose `/proceedings/{n}` was refused as well as for a committee's). The reader is told *which*
  fact it is — the sitting is off, or it meets without this bill (`_Listings.announced`) — and only
  if the announcement was actually `sent`. The tag is the announcement's, so one search finds both.
- **The agenda does not always name the druk, one meeting is listed many times, and the hour moves
  without the day.** Three blind spots, measured over the 4,387 committee and 75 Sejm sittings of
  term 10 (coverage audit, 14 Sept 2026). **Past the third reading the agenda names the derived
  print**: "Rozpatrzenie uchwały Senatu w sprawie ustawy o zmianie ustawy o cudzoziemcach (druk nr
  1935)" is druk 1630, "o wniosku Prezydenta o ponowne rozpatrzenie (druk nr 2378)" is druk 643 —
  211 committee items and 160 plenary ones, which is the whole Senate and veto stretch, the
  reader's last windows. `_items_for` therefore also matches `derived_print_numbers(bill.stages)`
  (the committee's report, the Senate's resolution, the President's motion — all prints of this
  bill's own process and nobody else's: of the 991 in term 10 not one is a process number in its
  own right), and adds no request, the numbers being in stages already read. **A joint sitting is
  listed under every committee in it** (938 sittings, 546 pairs), each with a `num` of its own, so
  a bill referred to two was announced twice — 328 (bill, day) pairs, druk 2699 among them (ASW +
  SPC, 2026-07-02) and druk 347 three times. `jointWith` is parsed and
  `CommitteeSitting.meeting_key` (day, hour, the sorted codes) collapses the group;
  `Poster.told_jointly` does not help, it keys on joint *prints*. **The hour and the room move on
  their own**: of the 886 sittings whose `comments` record a change, 204 say "zmiana godziny", 97
  "zmiana sali" and 61 both — and the `ref` was the day, so the run wrote the new hour to
  `bill.agenda` and said nothing while the standing post named the old one. The `ref`'s last
  segment now carries day, hour and room (`_when_and_where`); `sitting_key` is the `ref` without
  it, so the sitting is still the same sitting and is not retracted, and `moved_from` is the item
  as last announced rather than a bare date — «перенесено с 17.09.2026» over an unchanged date told
  the reader nothing. A sitting announced under an old day-only `ref` keeps it (`_keep_told_ref`),
  or the first run after the change would announce every standing sitting again; that rule goes
  once no dump carries a day-only agenda ref.
- **A sitting the committee called conditionally is not a date, and the room is not always one the
  reader may enter.** A committee's `notes` is free prose and 181 of the 226 that carry it only
  record the procedure the sitting was called under (art. 152 ust. 2), but **21 sittings of term
  10 say the sitting, or some of its points, happens only if the Sejm refers something to the
  committee first** — and four of those were still ahead on 15 Sept 2026. The channel announced
  every one of them the way it announces a settled date, which is the one thing an agenda post
  exists to get right. `agenda.sitting_condition` reads the note into a kind
  (`second_reading_amendments`, `first_reading_referral`, `senate_amendments`, `referral`, and
  `other` for the subcommittee waiting to be created — those five cover all 21) and the points it
  covers, because **eleven of the notes name their points** ("Pkt III aktualny…", "Pkt. II-IV
  aktualne…") and on two of them the point carries none of our prints (FPB/86, SPC/52): hedging
  those would be exactly as wrong as the fact the others were stated as. `condition_covers` reads
  the point off the item's own numeral where the committee numbers them and off its position where
  it does not — both shapes occur. The condition is **not** part of the `ref`: a condition lifted
  before the day would otherwise read as a sitting that moved and `_retract_gone` would take back
  a meeting that is still on; the card re-renders every run and self-heals, only the standing
  announcement keeps the hedge it was sent with, and the card's step line marks the day «условно»
  rather than promising it. The same note is the **only place the API publishes an address for
  applying to a przesłuchanie** (4 sittings, all of the Rzecznik Finansowy hearing of Nov 2025) and
  its prose is the consultation letter's, so `rcl_letters.parse_letter` reads it and no second
  parser of Polish dates exists. `closed` (279 sittings) is a sitting the public may not enter
  while the post names its room, and it is now said.
- **Pending-before-send.** Every Telegram post gets a `publications` row (`pending`) first, unique
  per kind/bill/channel (agenda posts and their retractions: per kind/bill/channel/`ref`, one per
  sitting); failed posts are retried up to `max_publish_attempts`; `pending` left by a crash
  becomes `unknown` and is never auto-resent. The "due" queries (`list_due_in_force`,
  `list_due_consultations`) must keep listing a bill whose post `failed`, or the retry never
  happens (`Poster.posted` decides).
- **Jointly considered prints share one thread, and the reply says what the print adds to it.**
  `ProcessSummary.prints_considered_jointly` names the other prints on the same subject (one
  committee report for all of them; their stages coincide from the joint referral on).
  `PublishingService` gives the group one card: a candidate whose partner already has a `sent`
  card that is still followed (not discontinued, not withdrawn/rejected) gets a `joint_bill`
  reply under that card instead (`Publisher.publish_joint_bill`, `MessageFormatter.joint_bill`),
  recorded pending-before-send, unique per bill/channel (v12), and it settles the bill like a
  card would (`list_publish_candidates` excludes both kinds). Within one run the government's
  print goes first (`government_first`): its text is usually the one the committee works on. The
  reply's bill has no `new_bill` row, so every tracker (they all join on a `sent` card) ignores
  it: the group's events come from the card's process, the card's analysis is redone when the
  joint text appears. `republish` forgets both rows and lets the normal path decide again. **The
  print that replies is read like any other and the reply says how it differs** — title,
  applicant and links told a reader meeting «Альтернативный проект того же закона» nothing about
  whether it was the same bill in other words or a different answer to the same question, which
  is the only thing a second print on one subject is news for. It is judged on its own text like
  any other bill, **but `min_score` is the bar for a card and not for a reply**: the bar decides
  whether a bill is worth a message in the feed of every reader, while a reply goes into a thread
  its readers have chosen, and by the time it is asked for the print has been read and judged —
  dropping the answer under the bar would mean paying for a reading and throwing it away (druk
  1929's card scores exactly 3, so its group sits on the threshold in production). The publisher
  asks for those two sets separately (`list_publish_candidates` with the bar,
  `list_joint_reply_candidates` without it) and `primary_of` is still the whole decision, so a
  print with no card to hang under never gets a card of its own below the bar. The difference
  itself is a
  **second, cheap call on the descriptions and not on the texts**
  (`AnalysisService.compare_joint`, `JointContext` → `JointComparison`, `bills.joint_json` v20,
  `compared_with` re-asking it when the group gains a print). Measured over term 10: 53 prints in
  21 groups, 18 past the prefilter, five groups with more than one candidate — **$4.21** for the
  term's non-card candidates against ≈$44 for the term, and about a cent a comparison. The
  comparison is asked in `PublishingService._compared`, a moment before the reply is rendered and
  before its publication row exists, because the common case is a group arriving in one run,
  where at analysis time no print of it has been read yet; it is an embellishment of the reply
  and never stops it, so even a model outage (everywhere else the end of a phase) is caught and
  the reply goes out as it was before comparisons existed. The verdict, importance and category
  stay the card's — one thread, one score. **Neither relevance gate decides an alternative
  bill**, for one reason asked at two moments: the card has already answered their question, of a
  bill on the same subject before the same committee, and all they can still do is take that bill
  out of the channel silently. The triage is not asked at all
  (`AnalysisService._joint_card_exists`, the publisher's own `primary_of` a phase earlier), and a
  print a prefilter had already skipped is put back in the queue
  (`joint.revive_prefilter_skips`, run at the head of the analysis phase so the print is read in
  the same run; `list_skipped_with_joint_prints` finds it because `/processes` carries
  `printsConsideredJointly` in the **listing** — 117 of the 1,665 rows of term 10 — so a row whose
  text was never read still knows its group). Order does not matter: the question is asked again
  every run, whichever came first, the card or the print. Measured: eight groups of term 10 have
  a print the prefilter drops beside one it keeps, **in all eight the same bill by another
  applicant** (druk 1426 the government's Kodeks pracy against the deputies' 1404, druk 316 the
  President's asystencja osobista beside 1929 and 1933, druk 2530 the same rynek kryptoaktywów as
  2529), and reading all of them costs **$0.71 for the term**.
  `BillStatus.SKIPPED_JOINT` is gone with the rule that set it (v21 turns a row of an older dump
  that carries the word into `analysis_pending`), and `/preview` renders the reply without the
  difference block when none is stored — a read-only command spends nothing, and the note says
  so.
- **`/bills` rows are refreshed by tracking only.** Discovery saves a submission for new bills; the
  reconciler (`tracking/pre_print.py`) re-reads `/bills` for pending RPW entries and for bills
  awaiting consultation results and compares new with stored (print assigned, withdrawn,
  `consultationResults` flipped). If discovery overwrote the row first, the flip would be lost. An
  entry the listing has simply *stopped* showing is a different fact, and the row is what tells
  them apart (`events._listing_says_withdrawn`): the reconciler reads the end of such an entry off
  its age (`_long_gone`, a year), and an entry can wait months in the Marszałek's "freezer", so
  «Проект отозван» stated a decision the applicant may never have taken. It is headed «Проект
  больше не отслеживается» and says the Sejm published no decision.
- **The last stage is not the last node.** `models.process_stages` is what `next_phase` and
  `Bill.last_stage` read: top-level stages minus `ASIDE_STAGE_TYPES` (`GovermentPosition`,
  `Opinion` — they arrive beside the process) **and minus a trailing `End`**. The Sejm appends
  "Uchwalono" at the third reading and keeps it last while the Senate, the President and Dz.U. are
  all still ahead (druk 2799, read 2026-09-12: III czytanie "uchwalono" 2026-09-04, `End` already
  there, no Senate stage, no act). Taking it for the current step collapsed the whole Senate →
  President segment into `publication` — «Сенат ✓ → Президент ✓» over the reader's last two
  windows, with the `SenatePosition`, `ToPresident`, `Veto` and `PresidentToTribunal` branches dead
  code. The `End` of a bill a veto killed ("nie uchwalona ponownie",
  `models.end_names_veto_sustained`) stays and ends the road — that narrow question, asked of the
  node itself, is what `process_stages` and the stage line use, and it is not the same question as
  `veto_stood`. `Bill.last_stage` is that top-level stage and never a child of it: children are the
  paperwork that followed the decision, so the newest node of a *flattened* tree is one of them —
  druk 2842's card named the referral under the `Veto` node («направлен в комиссию ENM») and the
  word "вето" appeared nowhere on it. `last_stage_detail` adds the child back when it says
  something the parent does not (which committee, how the Sejm voted); a referral to
  `PLENARY_COMMITTEE_CODE` is not one of those.
- **The veto has a road of its own, and the Sejm's vote on it is a stage.** `Veto` carries the
  referral to the committee as its child, "Praca w komisjach nad wnioskiem Prezydenta" is a
  `CommitteeWork` indistinguishable by type from the work after the first reading (only
  `ANSWERED_IN_COMMITTEE`, read backwards over the tree, tells the two apart), and the vote is
  `PresidentMotionConsideration`. Its `decision` decides everything: "nie uchwalona ponownie" ends
  the road and renames the trailing `End`, "uchwalono ponownie" restores "Uchwalono" and leaves the
  motion as the last stage that says anything — phase `president_after_veto`, seven days from its
  date (art. 122 ust. 5, `PRESIDENT_DAYS_AFTER_VETO`). Before 2026-09-13 the type was unknown to
  `_phase_after`, which fell through to `if any(SenatePosition in top)` and put an overridden veto
  back at «Сейм рассматривает поправки Сената»; there is no such fallback now — an unrecognised
  last stage gives no phase, and `_ended_line` only says «закон не принят» when the listing says
  `passed=false`. **That vote is the only witness, because the rename does not always happen.** Of
  the fifteen processes of terms 8–10 whose motion decided "nie uchwalona ponownie", **eight keep
  `End` = "Uchwalono" and `passed` = true** — druki 410, 643, 865, 935, 1109, 1110, 1131 and 1600
  of term 10, seven closed 2026-03-27 — so the API says of a law the veto killed exactly what it
  says of one that lived. Reading the rename alone, `_ended_line` found no branch that held (no
  act, no `discontinued_at`, no veto, `passed` not false) and returned **nothing**: the card ended
  without a sentence saying the bill was over and the digest froze it there, while
  `_closure_event_of`, seeing `change.passed`, headed the closing post «Сейм принял закон».
  `models.veto_stood` therefore asks the `PresidentMotionConsideration` as well as the `End` — the
  question `_phase_after_veto_vote` was already asking a line away.
- **"What comes next" is derived, not stored.** `models.next_phase(bill, today)` reads the
  top-level stages, submission and act; the formatter dates it from `bill.agenda` (upcoming
  sittings, refreshed every run for every followed bill, not only the changed ones) or from the
  constitutional deadline (`Phase.deadline`: Senate 30 days from the 3rd reading, President 21 from
  receiving the act; 14/7 for urgent bills). A bill the government declared *pilny*
  (`models.is_urgent`, art. 123) is told in its own words throughout (`Labels.urgent_step_labels`,
  `urgent_durations` override the normal entries), so the card never promises weeks where the Sejm
  measured days. **No date is printed once it has passed**: a deadline past `DEADLINE_GRACE_DAYS`
  says what its running out *meant* (`Labels.deadline_passed_labels`, and
  `urgent_deadline_passed_labels` first for a bill measured in seven days rather than twenty-one)
  and the step drops the wording that promised it (`next_step_labels`' `_overdue` variant); a step
  that outlived `PHASE_PATIENCE` says how long it has been standing (`models.stalled_days`,
  `Phase.since` — for an RCL project the stage's last modification, RCL leaving "rozpoczęcie"
  empty) — but **never a step that carries a date of its own**: a vacatio legis of a year is
  common, and «вступление в силу 01.07.2027 · без движения уже 7 мес.» inverts the message. The
  quarter the government names for adopting a project is such a date and was losing to the stalled
  note (UD338, carded 2026-09-14: announced in November 2025, adoption planned for the quarter then
  running, and the card said «без движения уже 9 мес.» in place of the date the reader could plan
  around); `_when` asks `_planned_adoption` before `_stalled_for`, and once the quarter has gone it
  says nothing and the silence is the news again. A
  sitting only dates a phase whose venue it matches (a committee's 08:30 slot is not a third
  reading): a phase in `COMMITTEE_PHASES`/`SITTING_PHASES` takes a sitting only from that venue and
  a phase in neither (the Senate, the President, Dz.U., a vacatio legis) takes none at all — the
  fallback to "the next item on the calendar" printed «подпись Президента · заседание Сейма № 65»
  over the constitutional deadline. Same for the action line: a hearing whose application deadline
  has gone is not offered, and «до заседания» is dropped on the day of the sitting. A
  constitutional deadline is shown as the deadline of the body under it, never as a window the
  reader has («решение до 04.10.2026»), and the reminder (`tracking/deadlines.py`, one reply per
  bill and phase, v17, `decision_reminder_days` = 7) says the date is counted from the third
  reading and so runs early — **but only where it is** (`Phase.deadline_exact`): `ToPresident` is
  the hand-over itself, so art. 122's twenty-one days run from a date the API gives, and
  apologising for an exact date teaches the reader to discount it. The Senate action carries no
  date at all — art. 121 gives thirty days and its committee takes the act long before they end.
- **The Senate rejects an act in the API's words, not the textbook's.** `SenatePosition.position`
  takes exactly four values over terms 8–10: "nie wniósł poprawek" (1 364), "wniósł poprawki"
  (722), "wniósł poprawkę" (27) and **"wnosi o odrzucenie ustawy"** (93). "odrzucił ustawę", which
  `docs/legislative-process.md` gave until 2026-09-14, appears **nowhere** — and neither wording
  contains the `"odrzuci"` both `_phase_after_senate` and `_senate_event` tested for, because
  "odrzuce-nie" does not. So every Senate rejection came out as `senate_amendments` and its post
  under the bare "senate" event: «Сенат внёс поправки» over a resolution that kills the law unless
  the Sejm throws it out by an absolute majority (art. 121 ust. 3). All 93 are of term 9 — the Sejm
  and the Senate of term 10 share a majority — so the branch is dead today and wakes with the next
  configuration, the way the veto road already has. `models.senate_moved_rejection` ("odrzuc" and
  not "popraw") is the one test both ask, `_phase_after_senate` also for the committee working on
  the Senate's position. The Sejm's own answer is read as well: "odrzucono uchwałę Senatu" (85) is
  the override and goes on to the President, "przyjęto uchwałę Senatu" (druk 2898 of term 9, `End`
  = "odrzucono na wniosek Senatu") is the rejection standing and ends the road —
  `SenatePositionConsideration` used to give `president` whatever it decided.
- **A reading the Sejm broke off decided nothing, and the Tribunal's ruling is an answer.** A
  `SejmReading` whose `decision` is "niedokończone … czytanie" is adjourned, not decided: druk 2985
  of term 8 stood at "nie dokończone III czytanie" with its process open, and taking that for a
  decision gave no phase, so `is_over` was true — no card for such a bill, and a followed one
  frozen where it stood. The Sejm has spelled the word both ways (term 10 "niedokończone", 36
  decisions; terms 8–9 "nie dokończone", 58), so `models.reading_decision` normalises the space for
  every rule that reads a decision — `second_reading_sent_back` knew only the current spelling.
  `ConstitutionalTribunalRuling` ("Wyrok Trybunału Konstytucyjnego", with a `verdict` and an M.P.
  address) was in neither the phase map, the event map nor the labels: three nodes in the corpus,
  all term 8, while ten bills of term 10 sit at `PresidentToTribunal` waiting for one. It ends the
  road and says so. Replayed over all 5,533 processes of terms 8–10, every `next_phase is None` is
  now accounted for by a closure the listing states or a veto that stood; before this, eight were
  not.
- **A term the Constitution makes zawity is a step, not a note.** Art. 121 ust. 2: thirty days gone
  with no uchwała from the Senate and the act counts as adopted in the Sejm's wording, and the
  Senate can neither extend nor suspend them — so `next_phase` moves the bill on
  (`_after_senate_silence` → `president_after_senate_silence`, no deadline of its own: the
  President's twenty-one days run from a receipt the API does not date, and a guess stacked on a
  guess is not worth a reminder). Annotating the Senate step instead made one line say both things:
  «рассмотрение в Сенате (до 30 дней) · 30 дней Сената истекли: закон считается принятым без
  поправок». This is the one place the bot names a step the Sejm has not published, so the wording
  says so, and it unwinds on the next run if the Senate did act and the listing was behind. Two
  bills get no Senate deadline from us at all (`_senate_days`, `_SENATE_SPECIAL_TERM`): the budget,
  where art. 223 gives twenty days, and a constitutional amendment, where art. 235 gives sixty —
  neither is in reach of this channel's keywords, but a date computed at thirty would be wrong and
  the silence rule would then fire too early. A step whose term is out never keeps a wording that
  promises it: `next_step_labels` takes an `_overdue` variant (and the urgent wording of art. 123,
  which names the shortened term, is dropped for it, `urgent_mode` still saying which mode the bill
  was in), and the expiry note comes from `urgent_deadline_passed_labels` first — it used to quote
  «21 день» to a bill the Sejm had measured in seven.
- **A live card is kept true; a finished one says so once and is then left alone.** Everything the
  card says about "now" is derived from the day it was rendered, so
  `tracking/cards.py::CardRefresher` re-renders the card of every followed bill each run and edits
  it in place when the text has drifted. The digest of what was last sent
  (`publications.rendered_sha256`, v16) makes a quiet run free: a pure render per bill, no request
  — **and drift is the only test there is**. The refresher used to stop at `next_phase(...) is
  None`, which fires exactly one run before the card would say the road had ended, so a bill the
  Sejm rejected kept «дальше: III чтение» and «написать в комиссию» for the life of the thread
  (product review, 2026-09-14); it now renders that last state too and the digest holds it there.
  `is_over` was never the test either — an act in Dziennik Ustaw with months of vacatio legis is
  still live, and freezing the card there left it saying «дальше: публикация в Dz.U.» for ever.
  `list_tracked` has to agree, or the refresher never sees the bill: the grace window runs from
  `closure_date`, which the Sejm sets at the third reading, and 2699's ended 35 days before its act
  applied, so a published act is followed until `entry_into_force`, whatever its age. The one
  ending `list_tracked` cannot reach is the lapsed term — it drops a row the moment
  `discontinued_at` is set — so `tracking/rollover.py` re-renders those cards itself
  (`Poster.rerender_card`, the method the linkers use to re-tag a thread that gained a number). The
  card calls itself finished only when the ending line can also say *how* (`_ended_line`):
  `next_phase` gives up on an unrecognised stage tree too, and "процесс завершён" over nothing is a
  guess. A veto the Sejm could not override **is** such a *how* and was missing from it: the API
  leaves `passed` true on a law a veto killed, so the closure branch never fired and the card kept
  the header «Новый законопроект» with no step lines at all, the word «вето» appearing only in the
  «Стадия» line.
- **A bill whose road ended before we saw it gets neither an analysis nor a card**, a card being an
  invitation to act. `models.is_over(bill, today)` decides for every source: over means the act
  *applies*, the bill was rejected or withdrawn, the RCL project was closed without reaching the
  Sejm, the plan was realised or taken off the wykaz, or the term lapsed — `next_phase` finding
  nothing ahead, with a Sejm bill whose stages were never read counting as unknown, not over.
  `closureDate` alone is *not* the end: the Sejm sets it at the third reading with the Senate, the
  President and Dz.U. still ahead (druk 2799: closed 2026-09-04, `passed`, no act), so discovery
  reads the stages once for a bill it first sees with a closure date (`_ended_before_first_sight`)
  and stores the skip as `skipped_closed` (`reset --to analysis_pending` revives it). The ELI
  address is not the end either: an act published with a long vacatio legis (druk 2699: Dz.U.
  2026-08-18, in force 2026-11-19) is the one stretch where a reader has a fixed date to prepare
  for, so discovery reads the act too (one request, the row keeps it — the publishing gate asks the
  same question a phase later and without it would drop the card). An ELI the API has not indexed
  yet still counts as the end. RCL discovery decides from the timeline, before the catalogs, and
  the wykaz from the entry's status. Bills already followed are untouched: they keep their card and
  their updates to the end. The last gate is `PublishingService.publish_new`, for a bill analysed
  while it was still running.
- **A file that is not there yet is not a verdict.** The Sejm lists a process before the print's
  file is attached to it: druk 3094 was judged at 10:11 UTC on 15 Sept 2026 and its PDF appeared
  at 10:16, by which time the row read `skipped_text_prefilter`, "no document to read" — and a
  text skip is revisited only when the *title* changes (`discovery._ingest`) or by hand
  (`reprefilter --include-text-skipped`, `/unskip`). So a print with no document leaves the row
  `text_prefilter_pending` and is counted as `unanswered`, exactly like the WAF's refusal on orka:
  the fact is about the day, not about the bill. Only a source that cannot have a file later (an
  RCL project whose "Projekt" folder holds nothing readable) still gets the skip.
- **A file keywords cannot search is not a file to drop.** The text prefilter scans the print of a
  title miss; when the file has no text layer but has pages, the bill goes to `analysis_pending`
  unsearched (`TextPrefilterResult.scans`, told apart in the run report from `unreadable`, which is
  now only a file with no pages either). Refusing it there refused the bill at both ends at once:
  the prints that arrive as scanned paper are the deputies' bills, and those are the ones whose
  titles say "o zmianie niektórych ustaw". The per-bill cost guard bounds the decision — at 1,600
  tokens a page it stops a scan at ~250 pages — and the skip reasons say which threshold was
  missed, because "weak hits only" was printed over every kind of miss, a single strong pattern
  included. **And the prefilter asks the question the analysis asks**, not a cheaper one:
  `TextLoader` calls a file textless only under `MIN_TEXT_CHARS`, so a print whose text layer is
  the letter that hands it to the Marshal (700–1,200 characters) arrived looking like a document,
  was searched for keywords a transmittal note never contains, and was skipped for good — **91 of
  the 938 prints of term 10**, every one with pages the model could have read.
  `carries_the_document` is now what both stages ask. The price is measured: the scans of the term
  are **317 documents and 7,763 pages**, $62 on Opus against the ~$44 the rest costs — and **a scan
  bypassed the triage**, because `_triage_verdict` wants `len(text) >= triage_min_chars` and a
  scan's `text` used to be the letter, so the dearest documents went straight to the dearest model.
  **`_worth_triaging` now says yes to a scan whatever its text**, and the scan is shown
  `triage_scan_pages` (8) opening pages, cut from the bytes already downloaded
  (`TextLoader.first_pages`), so the file is never fetched twice and the cheap call stops depending
  on how thick the paper is — **$0.013 for any scan** on Haiku, eleven pages or three hundred and
  sixty-two, breaking even at a **7%** rejection rate. Production triages on `llm_triage_model`,
  which is Sonnet 5 at twice Haiku's price, and eight pages are not always the 1,600 tokens
  measured on druk 1273: druki 2007 and 2010 (197 pages each, 15 Sept 2026) cost 21.9k and 21.6k
  input tokens, **$0.044 a scan**. Three times the Haiku figure and still insurance rather than
  expense — the reading it replaced was ~530k tokens on Opus, which only the per-bill guard kept
  under $2. Asked of 18 real scans through this path on Haiku it
  rejected **18 of 18 correctly** at a mean confidence of 0.93 (animal protection, drink-driving,
  hunting law, four commemorative resolutions), taking the scans' bill from $62 to $27 at the
  conservative 62% rate and to $16 at the lower bound the sample supports. What the sample cannot
  show is a false rejection, since it met no relevant scan; the guard is structural — the prompt
  says how many pages of how many are attached and asks for lower confidence rather than a guess,
  and an unsure verdict passes the bill on, so only a *confident* wrong "no" loses one.
- **The Sejm does not publish the road in order, and a post that fills in the past is held.** Druk
  1929 was read twice on 15 Sept 2026: at 11:32 the tree had gained "Praca w komisjach po II
  czytaniu" with the committee's report on the amendments, and at 12:29 it gained the II czytanie
  that sent the bill there — a node standing *before* the work already told. `update_event` names
  a post after its own newest stage and nothing asked whether that stage was newer than the last
  one told, so the second post announced the cause of the first and **seven of its eleven lines
  were the first post again**. `models.fills_in_the_past(known, found)` compares the two readings
  and `_post_news` holds such a change instead of sending it, which lists its stages before the
  next post rather than dropping them — the row is what stops them being detected again. Tree
  order is the road's order and not a guess: over the 938 bill processes of term 10 **no** road
  stage stands before one with an earlier date, while **521** of them have a day carrying several
  road stages, so which node a run sees first on such a day is chance — the shape at hand
  (`SejmReading` then `CommitteeWork` on one date) occurs 122 times. Replayed in date order over
  all **6,260** transitions of the term the rule holds **nothing**; replayed with each such day
  split the way two runs would split it, it catches all **131**, most often
  `SenatePositionConsideration` arriving behind `ToPresident` (67) — the reader's last windows.
  Only the road is read (`process_stages`), because **196 of the 241** `GovermentPosition` and
  `Opinion` nodes of the term stand before a road stage dated later, and counting those would
  hold back the government's position, which is the one filing this channel does tell.
- **Not every stage is a post.** `models/events.py`: `is_substantive` separates the events a reader
  cares about (referral, committee report, vote, Senate, President, hearing, a decided reading)
  from the frame nodes (`Start`, `ReadingReferral`, `Reading`, `CommitteeWork`, `End`, `Opinion`
  — `ToPresident` is *not* one of them, it starts the 21 days of art. 122); `has_news` decides
  whether a detected change is posted now. **Both nodes that arrive beside the process are named
  or held explicitly**, and neither was: replaying every one of the 6,260 stage transitions of
  term 10 (cold-places audit, `checks/16_updates.py`), 222 went out under the
  one header that says nothing. `Opinion` is `SERVICE_STAGE_TYPES` now — `process_stages` already
  drops it as beside the road and the decision of 2026-09-12 says filed opinions are not this
  channel's genre, so it is held and listed with the next update that has something to say (149
  transitions, 192 prints). `GovermentPosition` is the other way round: it is told, so its stage
  type names the post in `_EVENT_BY_STAGE_TYPE` under the key the digest uses. `supplement_event`
  alone was not enough, because the stage arrives whether or not the document behind it could be
  read, and a scan, a refusal or a text over the per-bill limit all leave the digest empty (73
  transitions). A change of frame nodes only
  is *held*: its `status_changes` row exists (dedupe) with a `skipped` `status_update` publication,
  and `Poster.status_update` prepends the held stages to the next post and marks their rows `sent`
  with its message id. **Recording and telling are one pair of methods**, because a change that is
  written down and then neither told nor held is lost for good — the row is what stops it being
  detected again: `Poster.record_change` writes the row and hands the change back with its id,
  `Poster.tell` posts it or holds it and answers `Told.SENT`/`HELD`/`FAILED`, and there is no
  fourth outcome. Every watcher goes through them; the one exception is
  `StatusTrackingService._retry_failed`, which re-sends a post that failed and has nothing to
  decide. A closure detected in the same run as the act's ELI is held too: the
  Dziennik Ustaw notice tells it. `update_event` names the header after the newest stage
  (`Labels.update_headers`); the closure line is dropped when the header already says it.
  Amendments (Senate resolution print, a committee report whose proposal is about poprawki) are
  summarised by a third model call (`AnalysisService.summarize_amendments`) after the change row
  exists and stored on it (`amendments_json`); a failure degrades to the bare event. `has_news`
  gates the *Sejm* loop only: `RclWatcher` posts every change it records, because `rcl_fingerprint`
  already decides what counts (folder uploads alone do not). What a change with no new stage needs
  is a name, not a gate — `StatusChange.consultation_opened` gives one to the consultation that
  opens under a stage the timeline already shows as reached, the one moment a reader of a
  government project can act on, which went out headed «Обновление». Every reply carries the topic
  tags: `_tag_line` does it for the kinds that have one, and the status update and the joint reply,
  which build their tag line by hand, must add `_topic_tags` themselves — they were the two that
  did not.
- **What the Sejm publishes is largely scanned paper, and a covering letter is not the document it
  transmits.** `sections.carries_the_document` is what both the prefilter and the analysis ask: it
  cuts the transmittal letter (`without_cover_letter`) and then judges the paper by density — under
  300 characters a page is a photograph of pages, not their text, at any length. A file with no
  text but with pages goes to the model as pages (`ScannedDocument`, `text_source="scan"`, ~1,600
  tokens and $0.008 a page, guarded and priced by `pricing.estimate_scan_cost`), keeping the file's
  `sha256` where a text's digest would stand; a file with neither keeps its text, there being
  nothing to fall back to. Only the letter's page is dropped (`sections.scan_page_window`, and only
  when `sections.has_cover_letter` finds the transmittal formula), and that is not truncation —
  `ScannedDocument.truncated` counts only pages of the document itself (`cover_letter_pages`).
  Nothing else of a scan is trimmed. The gate sits in `AnalysisService._load_text`, the one place
  every document the model reads passes through, so the rule is one and not four. The measurements
  it rests on — the 66 filed documents and 39 prints of 12 Sept 2026, the ten filings that do carry
  a document, the 30 prints the old 4,000-character ceiling let through as `text` (druki 703, 204,
  348), `MIN_CHARS_PER_PAGE` at the seam (306.2 for druk 1625 against 299.9 for druk 205, checked
  against the `/XObject` tree), and why a page map was built and removed — are in
  `docs/text-selection.md`.
- **A document filed to a print is told once, and the bill is what remembers.** The print's
  `additional_prints` say what has been filed; `models.supplement_kind` keeps the government's
  position, the OSR and an opinion with remarks and drops the housekeeping;
  `bills.supplements_json` (v18) is the list the channel has been told about, written last by
  `_remember_supplements` and only when the print was actually read. None means never recorded, and
  the first sight seeds it silently, the way the stage fingerprint is seeded — otherwise every
  followed bill would announce its whole history of filings after the deploy. Each new document is
  digested against the current analysis by one model call (`AnalysisService.digest_supplement`)
  *after* the change row exists, so a change an earlier run recorded never pays twice, and the
  numbers go into `change_key`: a document that arrives between two stages moves nothing else, and
  without them the row would collide with the previous change and be dropped. A digest that could
  not be made (a scan, a refusal, or a text over `LEXINFORM_MAX_ANALYSIS_COST_USD` — the OSR of
  druk 1273 is a 2.7 MB PDF, and somebody's opinion of a bill is not worth any price) still leaves
  the document named and linked (`SupplementRecord.digest is None`); the run records it as told
  either way, so dropping it would lose it. The reply is named after the newest document when the
  stages do not name it (`models.supplement_event`): the government's position has a stage but no
  name of its own, and «Обновление» over the government's verdict told the reader nothing.
- **A committee report is the bill only when it attaches one.** `Stage.carries_bill_text` asked
  whether the `proposal` mentions a *projekt*, which takes in "odrzucić projekt ustawy" (23 bill
  reports of term 10) and "uchwalić projekt ustawy bez poprawek" (95) beside the 528 that really
  are the text. `latest_text_document` then handed the report's PDF to `SejmTextSource` as a new
  bill text, so a bill the committee had moved to **throw out** was re-analysed on the two-page
  motion to throw it out — paid for, and the card's verdict, score and summary overwritten by a
  reading of the recommendation, with «текст обновился» under it, while the event on the same card
  correctly said `committee_rejects`. Only "załączony projekt" attaches one. What follows the
  committee is then read from the report's print number instead (`Stage.is_additional_report` —
  over term 10 every one of the 292 "Praca w komisjach po II czytaniu" stages carries an "-A"
  report and none of the 645 after a first reading does), because a report proposing rejection or
  no amendments still goes to the second reading, and keying that on `carries_bill_text` would have
  sent it to the third.
- **A re-analysis needs a new text, not a new URL.** `AnalysisRecord.text_sha256` is the digest of
  the normalised text the model saw (`services/analysis.py::text_digest`: page numbers and
  whitespace ignored). `reanalyze_bill` returns None and only repoints `source_url` /
  `source_checked_at` when the new document hashes alike (a file republished on RCL under every
  stage, a print re-dated by an attachment such as stanowisko rządu). `SejmTextSource.newer`
  compares the print's `changeDate` with `source_checked_at`, and does not even download the text
  after the 3rd reading when `models.third_reading_kept_the_text` holds (2nd reading went straight
  to the 3rd, no "-A" report, `minorityMotions == 0` on the report): the Sejm adopted the analysed
  text verbatim. Unknown facts (motions not parsed, no 2nd reading) mean "may differ" and the text
  is read.
- **Stage fingerprint** (`_stage_key`) drives updates. Fields added to `Stage` for rendering
  (`voting`, `position`, `committee_name`, `proposal`) must stay *out* of the key, or every tracked
  bill posts a spurious update after deploy.
- **Migrations are append-only.** `dump()` writes `PRAGMA user_version`; `restore()` migrates old
  dumps. Test every migration against a v1 dump (see `test_sqlite_repo.py`).
- **Outages never burn per-bill attempts.** `ServiceUnavailableError` subclasses abort a phase and
  go to the log channel; everything else is a per-bill failure (3 attempts). Unknown model id and
  bad request parameters are fatal, "prompt too long" is per-bill.
- Pre-print bills (`RPW/…`), RCL projects (`RCL/{id}`) and wykaz entries (`WPL/UD408`) have no Sejm
  process (`has_process` tests one tuple of prefixes, `NON_SEJM_PREFIXES`; a prefix missing there
  sends the rows to `/processes`): skip `get_process`/`get_print` for them. They are not textless,
  though: `TextSources` routes an RPW row to `SubmissionTextSource` (its file on orka), an RCL row
  to its documents, and only a register entry — a bill that has not been written yet — to the
  metadata; when the print appears, it inherits the card (`tracking/linking.py::Linker`: `new_bill`
  row aliased with the same `message_id`). **A bill fetched on request is linked in whichever
  direction it is named**, and the druk is what comes back: it is the bill with the text.
  `BillLookup._fetch` splits per kind — an RCL project asks `find_process_by_rcl_num` (the term's
  listing walked once) for the print its `rm_number` names; an RPW entry already carries `print` in
  its `/bills` row; a druk asks `/bills?print=N` (one request, the applicant and consultation dates
  come with it) for the entry it continues, and its own `rclNum` for the project
  (`discovery.NOT_FOLLOWED`: a skipped or already linked row has no thread, so its druk takes the
  normal path, and RCL being unreachable leaves the print standing on its own). An entry whose act
  is already in force must never get a card promising a druk number, and a druk must not get a
  second card next to the entry's: a print linked outside tracking inherits the entry's card in
  `PublishingService` (`_inherited_card`, the same alias `Linker` makes, re-rendered with both
  tags). A command posts no card at all for a bill `models.is_over` calls finished, answering with
  the verdict instead. The print copies the entry's status, except a prefilter skip
  (`skipped_prefilter` or `skipped_text_prefilter`), which sends it to `text_prefilter_pending`:
  the entry's own text may have been missed by the keywords, but the file may equally have been a
  scan or refused by the WAF, and the reason cannot be read off the status — while the print's PDF
  comes from the API, is the text the Sejm works from, and costs a download and no tokens to scan.
  The RPW reconciler finds the print in `/bills`; for RCL, Sejm discovery notices a druk whose
  `rclNum` names a followed project (stored RM number, else `getIdFromLegislacja?number=…`), stores
  the druk number on the RCL row and the RCL watcher links.
- **RCL rows are refreshed by the RCL watcher only.** RCL discovery reads a project once (timeline
  + catalogs for a candidate, one catalog for a title miss) and afterwards only bumps `change_date`
  from the list; `RclWatcher` re-reads the timeline and the catalogs whose "Data ostatniej
  modyfikacji" moved **or that were never read** (`RclStage.catalog_read`, set in
  `parse_stage_catalog` alone — 1,098 of the 4,307 reached stages of the corpus have no folders
  after being read whole, so emptiness cannot say whether anybody opened the page), and
  `rcl_fingerprint` (stages reached, folders that got their first files,
  the newest text, status, hand-over) decides whether there is an update. Folder uploads alone are
  not news; published opinions are a separate `consultation_results` reply. A page takes ~10 s:
  never add a request per project without a reason. A project the prefilter skipped keeps only its
  skeleton (`RclProject.without_documents()`, applied by `set_status` on a skipped status): the
  documents are most of the row and are never read again; `lexinform reset … --to analysis_pending`
  re-reads them. Run records older than `LEXINFORM_RUNS_RETENTION_DAYS` (90) are deleted at the
  start of a run.
- **The stage a project ends on does not carry its text.** `with_text` reads the newest reached
  stage's catalog on the assumption that every stage republishes the current text in its own
  "Projekt" folder. True of the stages that *work* on a project, false of the one it ends on:
  "Skierowanie projektu ustawy do Sejmu" carries the covering letter to the Marshal and no
  "Projekt" folder at all. With `TEXT_STAGE_ATTEMPTS` at 1 the reader stopped there, found
  nothing, and the text prefilter wrote the **verdict** "no document to read" — which for an RCL
  row is final (the invariant above allows it only for a project that cannot have a file later).
  One backfill closed **nine** projects that way (production run 69, 15 Sept 2026): RCL/12400301,
  RCL/12410100 and RCL/12405302 were probed afterwards and all three have a readable bill two
  catalogs down, in "Rada Ministrów". Measured over the 796 projects of the corpus that carry a
  readable "Projekt" folder anywhere, the newest reached stage has it for **209 (26%)**, the next
  brings it to **99.25%** and a third to 99.75%; `TEXT_STAGE_ATTEMPTS` is therefore **3**, and the
  loop stops at the first catalog that has the text, so the extra pages are paid only where the
  answer used to be wrong. The 12 rows the state carries with that reason are to be revived by
  hand (`lexinform reset … --to analysis_pending`, or `/unskip`).
- **A project taken by its text is read for the text alone, so its letter is fetched in a phase of
  its own.** `_deepen` gives a title hit `complete()` (every reached catalog and the consultation
  letter) and a title miss `with_text()` (one catalog, the newest text) — and nothing opens the
  letter afterwards, because the watcher only refreshes a project the *listing* shows as changed
  and a project can stand untouched for months. All three cards of 15 Sept 2026 went out that way:
  no deadline, no address for remarks, no consultation reminder, `_results_due` blind to published
  stanowiska, the phase `rcl_opinions` instead of `rcl_consultation` — and, because
  `_rcl_actions` reads `window is None` as "this project never had a consultation", the comment
  form and the zgłoszenie were offered with the emphasis of an open window over consultations that
  had closed on 13 June (UD387), 19 July (UPRO10) and 2 February (UD337);
  `action_rcl_window_closed` is unreachable while the window is unknown.
  `RclDiscoveryService.read_consultations` (phase "rcl consultations", between the text prefilter
  and the analysis) reads the one catalog that carries the letter
  (`RclProjectReader.with_consultation`) for every RCL row waiting to be analysed and missing a
  window: one page against a reading that costs a dollar, and the queue drains itself. **Reading
  the letter late is not an event**: `consultation_opened` is news only while the window is open
  (`models.consultation_open`), and `_results_due` says nothing when the stored window was None,
  or the run that repairs an old row would announce «Открылись публичные консультации» over a
  door that shut in June and the stanowiska of a consultation nobody here was watching.
- **The bill can be one file with its uzasadnienie, or an appendix to the letter.** `text_role`
  tests "uzasad" before anything else and drops what calls itself a `załącznik`, so "Projekt
  ustawy+uzasadnienie+OSR_podpisane przez DP.pdf" is read as the uzasadnienie and "Załącznik nr 1
  Projekt ustawy — Prawo własności przemysłowej UC81" as an appendix — and the project is then left
  with **no bill at all**, which the text prefilter records as "no document to read" and the row is
  closed for good (RCL/12409801, 15 Sept 2026: a Prawo o ruchu drogowym project whose only text is
  one combined .docx). Measured over the 824 projects of the corpus whose newest "Projekt" folder
  holds readable files: **nine** end with no bill, six of them one combined file and three the bill
  as a numbered appendix. `_bill_of_last_resort` is reached only where the answer would otherwise
  be nothing — so it cannot change what any other project is read from — and it still refuses what
  `text_role` refuses (a tabela, a pismo, an autopoprawka); a combined file is stored as the bill
  and dropped from the extras, or the analysis would pay for the same text twice.
- **The wykaz is the earliest source and the thinnest: an intention, not a bill.** The whole
  register arrives as one CSV per run (`adapters/wykaz_csv.py`; the id in the URL is read from the
  page, columns are matched by prefix because their statutory wording gets repunctuated), so the
  prefilter sees all of it, and **every entry is judged every run** — the gates are a keyword match
  and two indexed lookups, so the watermark decides only what the report counts as news. Until
  2026-09-15 it decided what was judged, and what it held back was counted: «53 older entries
  match, not followed» in every report. Replayed over the whole register with the watermark pushed
  back to 2015, those 53 ingest **nothing** (34 realised or withdrawn, 3 the plans in the channel,
  7 followed as their projects) — but 12 named a live project on RCL that nothing was taking, and
  reading them found **seven candidates**, zawód pielęgniarki (14 keyword families) and zawody
  lekarza among them. So a plan whose project is out hands that project to RCL discovery
  (`WykazDiscoveryResult.on_rcl` → `RclDiscoveryService.discover(from_register=…)` →
  `_take`), which is also the only way in for a project the listing does not show as changed —
  it is walked by modification date, and this bot's watermark starts on 2026-09-09. A rejected
  entry is still not stored: the 775 bill entries with their paragraphs would add ~2.3 MB to a
  state dump that is 822 KB. Only `Projekty ustaw` are
  followed; a plan already realised or withdrawn on first sight never gets a card (there is no
  action left to invite), and neither does one whose project is already on RCL. The card says there
  is no text yet, and the one action it offers is the art. 7 zgłoszenie zainteresowania — which
  anyone may file, and which is the ticket to the Sejm's wysłuchanie publiczne (art. 8 ust. 2). The
  card says **how**, because «подать zgłoszenie» is not an instruction: the form from the
  ministry's BIP page under "Działalność lobbingowa", carrying the name, the address, the interest
  and the legal solution sought (art. 7 ust. 4), sent to the ministry that puts the bill before the
  Council of Ministers (ust. 2), published in BIP with the project's papers, a private address
  excepted (ust. 3). Checked against the consolidated text of 13 July 2026 (Dz.U. 2026 poz. 936):
  **Dz.U. 2026 poz. 160 repealed art. 3–4 on 2026-08-28**, so the wykaz prac legislacyjnych now
  rests on art. 8a–8b ustawy o Radzie Ministrów and the regulation carrying the official form
  (Dz.U. 2011/1080) lapsed with the delegation — no replacement is in ELI, and the ministries go on
  publishing the old form, which is why the card names the BIP page and not a Dz.U. number. The
  register is also the only source that says the government **dropped** a project (`Status
  realizacji`, `Informacja o rezygnacji` — the columns are still in the CSV; the duty behind them
  moved with art. 3): that is posted, a slipped quarter or a rewritten "istota" is only stored. `Planowane przyjęcie przez RM` is free text and half of it
  carries the adoption note: only the quarter is ever rendered.
- **A plan's project is stamped, not ingested.** RCL discovery has the wykaz number on the list
  page: when it names a followed `WPL/` row it writes `rcl_project_id` on that row and skips the
  project. `tracking/wykaz.py::WykazLinker` then creates the `RCL/` row, hands the card over (alias
  with the same `message_id`) and **re-analyses from the documents** — the plan was judged on an
  announcement, and that judgement must not decide the fate of the row that has the text. Ingesting
  the project in discovery instead would post a second card: `publishing`'s inheritance keys on a
  link that does not exist until the linker runs. **The listing is the only place that join is
  published, so every row of it is written down, followed or not** (v22 `rcl_wykaz_numbers`,
  filled by `RclDiscoveryService._remember_number` at no extra request, and by
  `lexinform index-rcl-numbers --since` for the years before this bot — listing pages only, no
  timeline, no catalog, no tokens; the daily workflow takes it as `index_rcl_since`). Two rules
  read it. A plan whose project is **already out gets no card**: the card says "there is no text
  yet" and nothing ever takes that back, because the linker only sees a project the listing shows
  as *changed* — and a project can sit untouched for a year (UD344's since January 2026). And a
  followed plan **claims a project the listing no longer shows**
  (`WykazWatcher._stamp_projects_already_listed`), which is how a project published before the
  plan was followed reaches its thread at all. **The number alone does not identify the project:
  the register reuses its numbers** — UD368 named a Centralny Port Komunikacyjny project in 2018
  and the Karta Polaka plan in 2026, and 61 of the 659 numbers RCL has listed since 2025 carry
  more than one project — so a plan takes only a project created on or after the day it was
  announced, and a row whose creation date the listing did not give matches nothing. Measured on
  the register of 14 Sept 2026: of the 56 entries the keywords accept, 34 are already realised or
  withdrawn, 7 are projects this bot follows, **12 have a project on RCL it does not follow** (the
  ones this rule stops) and 3 are still plans — UD368, UD338 and UD431, which are the three in the
  channel. The 12 are an RCL question, not a wykaz one: they are projects with a text that the
  bot never walked.
- **RCL markup is parsed, not matched.** `adapters/rcl_html.py` uses CSS selectors; a missing
  detail (date, folder, link) is tolerated, a missing structural element (timeline, table with rows
  announced, every stage label) raises `RclPageError`, which the run report shows. The WAF's
  "Request Rejected" page (HTTP 200) is `RclUnavailableError`.
- **`/run` is the one command a run does not execute, because it is the run.** The relay asks
  GitHub for a `workflow_dispatch` of `daily.yml` on `main` with the inputs the operator named
  (`since`, `dry`, `reprefilter`, `index_rcl_since` — `models.RUN_INPUTS`), answers «▶️ run
  started …» with a link and files nothing: an inbox file is a thing *for* a run, and there is no
  run yet. The values are checked in `_parse_run` and not by the workflow, which reads `since` as
  free text and would answer a typo hours later with a job that did the wrong thing. That endpoint
  needs a token with **Actions: read and write** (filing a command needs only Contents), and the
  403/404 it answers otherwise is reported as the missing scope, because the operator cannot tell
  the two apart. A `/run` that somehow reaches a run through the inbox is answered, not executed.
- **Operator commands are recorded before they run, and the relay confirms only what is filed.**
  The technical channel's commands (`docs/operator-commands.md`) reach a run as `{update_id}.json`
  files in the `inbox` branch (checked out by `daily.yml`, `LEXINFORM_INBOX_DIR`); the relay's
  event runs `lexinform commands` (the commands phase alone), every scheduled run does the phase
  first. `CommandService` inserts the `commands` row (v13, keyed by the Telegram update id) before
  executing, marks it executed (v14 `executed_at`) as soon as the side effects are done, answers
  under the command's message (`OperatorReplier`), marks it handled and deletes the file. A file
  read again is measured against the row: handled → only deleted; recorded but not handled (the
  channel was down, or the job died) → the command is **not** run a second time, it is answered
  with what the row knows — the recorded outcome when it was executed, otherwise a note that a run
  started it and did not finish, which the operator answers by sending the command again.
  `/analyze` is idempotent by construction (an analysed bill is not sent to the model again),
  `/republish` is not: the marks are what keep a second card away. `/forget` is `/republish`
  without the post, for a card deleted from the channel by hand: the `sent` row is what every
  tracker joins on, so left alone it keeps the bill followed and the refresher keeps editing a
  message that is not there (Telegram's `message to edit not found` is a warning, not an error, so
  it repeats for ever), while sending the card again is the wrong answer when it was deleted on
  purpose. Both card kinds go and nothing is posted, so what the bill gets next is the publishing
  rule's decision — a fresh card if it is still a candidate, nothing if it is silenced — and
  running it twice changes nothing. The commands that only read (`/show`, `/preview`, `/find`,
  `/status`) never post and never spend: `/preview` renders the card into the technical channel
  alone, so the wording can be read before `/republish` sends it. `/refresh` is the tracking phase
  for one bill (`StatusTrackingService.check_bill`) — the Sejm does not wait for 05:23 UTC — and
  leaves the reminders to the scheduled run, which asks them of the whole channel; idempotent the
  way tracking is (the change row is unique). `/unskip` is the way back from `/skip` and from every
  other skip: a clean budget of attempts and `analysis_pending`, with a skipped RCL row's documents
  re-read *before* the status is cleared, or an unreachable RCL would leave the bill queued to be
  analysed on its metadata alone. An outage of a source system *or of the channel* ends the phase
  and leaves the file, with the counters of the commands already answered intact. The relay
  (`lexinform listen`, one `getUpdates` consumer per bot, never a webhook) files a command through
  the GitHub Contents API, answers "queued" and only then moves the offset; a dry run (`--dry-run`)
  confirms nothing. The run is started by a `repository_dispatch` the writer sends after the file
  (a push of the inbox branch would start nothing: GitHub reads a push event's workflow from the
  pushed branch, which has none); a lost event costs nothing, the next scheduled run drains the
  inbox. The publish rule of a manual `/analyze` is the daily run's (relevant and score ≥
  `min_score`; `publish` overrides, `force` bypasses the prefilter, a previous analysis and the
  per-bill cost guard). Replies are English (the operator's channel), rendered by
  `MessageFormatter.command_reply`.
- **Parallelism only around the network.** `concurrency.fan_out` runs one network step (download,
  process lookup, model call) for many items; that step never touches the repository. Outcomes are
  consumed in the calling thread, in input order, and that is where every DB write happens.
  Services default to `workers=1` (tests); `workers=4` must give identical results.

## Database versioning and migrations

The schema version is SQLite's `PRAGMA user_version`; the source of truth is the `MIGRATIONS` tuple
in `adapters/sqlite_repo.py`. Script at index `i` brings the database to version `i + 1`;
`SCHEMA_VERSION = len(MIGRATIONS)` (v22 as of Sept 2026). `migrate()` reads `user_version` and runs
every later script inside its own transaction, stamping the new version at the end, so a failed
script leaves the database at the previous version.

What each version added (Sept 2026 throughout): v8 `rcl_json`; v9 `bills.discontinued_at` and
`status_changes.discontinued` (end of a Sejm term); v10 `bills.linked_wykaz_number` (the print
continuing an RCL thread keeps the wykaz number for the tag); v11 the unique index of hearing
reminders (per bill, channel and hearing date) and `status_changes.amendments_json` (the model's
summary of the Senate's or a committee's amendments); v12 the unique index of `joint_bill` replies
(per bill and channel); v13 the `commands` table (operator commands by Telegram update id); v14
`commands.executed_at` (a command whose answer never arrived is answered again, not executed
again); v15 `bills.wykaz_json` (`WPL/UD408` rows of the wykaz prac legislacyjnych RM); v16
`publications.rendered_sha256` (the card as last rendered, so a run can tell a drifted card from a
true one without asking Telegram); v17 the unique index of the constitutional-deadline reminders
(per bill, channel and phase: the Senate's 30 days and the President's 21 are each told once); v18
the documents filed to a print after its submission — `bills.supplements_json` (which of them the
channel has been told about) and `status_changes.supplements_json` (the digests one update
carried); v19 the unique index of sitting retractions (per bill, channel and `ref`, so an announced
sitting that is called off is taken back once); v20 `bills.joint_json` (how a print differs from
the others considered jointly with it, as the reply under their card says it — stored so a retry,
a `/preview` and a `/republish` do not pay for the comparison again); v21 the end of the
`skipped_joint` status — the rule that set it is gone, so a row of an older dump that carries the
word goes back to `analysis_pending` rather than failing to load; v22 `rcl_wykaz_numbers` (which
RCL project carries which number of the wykaz prac RM, for every row of the listing and not only
for the projects this bot follows, with the project's creation date because the register reuses
its numbers).

How state travels: the daily workflow runs `db init` (fresh schema at the current version) → `db
restore state/lexinform.sql` → `run` → `db dump`. `dump()` is `iterdump()` plus a trailing `PRAGMA
user_version = N;`. `restore()` drops all tables, replays the dump with foreign keys off, sets
`user_version` from that trailing line (1 when a legacy dump has none) and calls `migrate()`. So a
dump written by an older release is upgraded on the first run of the new one; nothing manual.

Adding a migration:

1. Append one string to `MIGRATIONS`; never edit or reorder earlier entries (deployed dumps carry
   their version). Plain SQL only: `ALTER TABLE … ADD COLUMN` (nullable or with a default), new
   tables, indexes, `UPDATE` backfills. SQLite cannot change a column's type or constraints: for
   that, create the new table, `INSERT … SELECT`, drop and rename.
2. Extend the model and `_row_to_bill` / mapping code. JSON columns (`summary_json`,
   `analysis_json`, `stages_json`, …) are pydantic dumps: new fields need defaults so old rows
   still load; renaming a JSON field is a data migration (SQL `json_set` or a one-off Python step).
3. Extend `test_restore_of_a_v1_dump_applies_every_later_migration` in
   `tests/unit/test_sqlite_repo.py` (it restores a hand-built v1 dump and asserts the new columns)
   and, before pushing, run the real dump through it: `git show origin/state:lexinform.sql`, `db
   init` + `db restore`, then `sqlite3 file "PRAGMA user_version"` and `lexinform show 2699`.

There is no downgrade. To roll back, revert the code and restore the previous dump from the `state`
branch history; the state branch is the backup.

## Sejm API lessons (verified live, Sept 2026)

- `/sejm/term` lists every term: `num`, `from`, `to` (absent for the running one), `current` (true
  for exactly one), `prints.count/lastChanged`. Term 10 started 2023-11-13; the flag is the signal
  for the switch, print numbers restart at 1 in the new term.
- `/processes` only lists bills that already have a print number. Bills at the consultation stage
  live in `/bills` (`RPW/…`), with `publicConsultationStart/EndDate`, `applicantType`, `status`,
  `print`, `consultationResults`. Their text is a PDF on orka.sejm.gov.pl at an address built by
  convention (`models.submission_pdf_url`, `LEXINFORM_ORKA_BASE_URL`), and it **is** downloadable,
  which the project denied until 2026-09-12: Imperva there refuses a `User-Agent` that names a bot
  (`curl/8.x`) and serves a browser, as long as the client follows the 302 and keeps the cookies it
  sets (`visid_incap_*`, `incap_ses_*`) — `adapters/orka.py` does both and is the one client for
  this host. Verified from a GitHub runner too (`.github/workflows/orka-probe.yml`), where the
  refusal arrives as **HTTP 200 text/html**, not 403, so the body decides and not the status. A
  failure of this host is a per-bill problem on purpose (`OrkaUnreachableError`): it is WAF-guarded
  and address-judged, and everything else the analysis reads is api.sejm.gov.pl. **What this host refuses is a
  request that is not shaped like a browser's, and the header order is part of the shape.** On
  2026-09-14 every orka request of four production runs was answered **403** while curl from the
  same runners was served the file, and the measurement took a matrix of cold runners to get
  right, because *the first client to be served vouches for the address and everything from it is
  served afterwards* — which is why a probe that ran curl first showed 44 successes out of 44 and
  proved nothing. Ruled out, each on its own cold address: the address pool (ten runners, ten
  Azure addresses, all refused), the protocol (HTTP/2 refused too), the cookies and the 302 (the
  403 comes first, there is nothing to follow), the browser hint headers (`Sec-Fetch-*`,
  `sec-ch-ua`), the cipher list, and the TLS hello itself — **a raw socket on Python's own `ssl`
  sending curl's bytes was let through** (302, the cookie step). What decided was that httpx
  writes its own defaults first, so the request said `Accept-Encoding` and `Connection` before
  `User-Agent`, which no Chrome does. `BROWSER_HEADERS` is therefore an **ordered** mapping in
  Chrome's own order (`Connection`, `Upgrade-Insecure-Requests`, `User-Agent`, `Accept`,
  `Accept-Encoding`, `Accept-Language`) and httpx keeps what it is given — 3 cold addresses, 3
  files out of 3 — while `browser_headers(accept=…)` replaces that one header in place, because
  moving it would undo the shape (`test_the_request_is_shaped_like_a_browser_and_not_like_a_client_library`
  is what stops a later edit from reordering it). Two guards stand behind that: the client
  **retries** a refusal, a WAF decision being momentary as well as structural (only a 404 is about
  the bill — the address is built by convention and can be wrong), and the error carries the WAF's
  own identifiers (`_waf_marks`: Imperva's incident id, the F5's support id, `x-iinfo`), because a
  403 with nothing to quote costs a session to diagnose. And **no phase turns a refusal into a
  verdict**: the text prefilter leaves the bill `text_prefilter_pending`, and the analysis leaves
  it `analysis_pending` without spending one of its three attempts (`AnalysisResult.unanswered`,
  `report.analysis_unanswered`) — RPW/30695/2026 was analysed on its `/bills` description on
  2026-09-14 and closed as `analyzed`, `text_source: metadata_only`, which nothing ever revisits:
  no `text_sha256` to compare, no `newer` on `SubmissionTextSource`, and the reconciler only
  re-reads `/bills`. An analysis of the metadata must never be what a bill with a text ends up
  with — the rule `_prepare` already applied to a re-analysis, now applied to the first one.
  The EU proxy is **not** the answer to this host (`403 Filtered` until 2026-09-14, when
  orka.sejm.gov.pl was added to the tinyproxy allowlist on the VPS): a CONNECT tunnel carries our
  request unchanged, and the VPS's own address was refused exactly like a runner's. Every outgoing
  client says the same thing about itself (`adapters/browser_identity.py`, Chrome 140 on Windows;
  measured: the WAFs score the kind of client, not the version), `Accept` aside, which each client
  sets for what it asks for. The API carries no link to the opinion form; the Sejm page is
  `www.sejm.gov.pl/Sejm10.nsf/agent.xsp?symbol=KONSULTOWANY_PROJEKT&NrProjektu=RPW/29075/2026`
  (browser only: www.sejm.gov.pl answers curl and fetchers with an F5 captcha). That page only
  *links* the form: the opinion is a survey (ankieta) at `opiniowanie.sejm.gov.pl/RPW-29075-2026` —
  the RPW number with dashes (verified 2026-09-13; the pattern holds for every RPW tried).
  `models.consultation_survey_url` builds it, and the card and the reminder link it directly rather
  than the page it hangs on. Sending the survey means signing in (it redirects to
  `logowanie.sejm.gov.pl`), but Profil Zaufany is one of the ways in and the readers use it:
  decided 2026-09-13 that the messages say nothing about it. Only deputies', president's, Senate,
  committee and citizens' bills have Sejm consultations; government bills (541 of 1279 in term 10)
  were consulted on RCL before submission and never have them.
- Sittings: `/committees/{code}/sittings` items have `num`, `date`, `startDateTime`, `room`,
  `status` (PLANNED|FINISHED), `agenda` (HTML `<div class="agenda-indent-N">` lines, prints as
  "druk nr 3035" / "druki nr 3010 i 3055"), `video[].playerLink`, `jointWith`. `/proceedings` lists
  sittings (`number` 0 for planned ones without agenda); `/proceedings/{n}` adds `agenda` (HTML
  `<li>` items with `PrzebiegProc.xsp?nr=` links that are not reliable: match the text). Measured
  over all 4,387 committee sittings of term 10 (14 Sept 2026): `status` is only **PLANNED or
  FINISHED** — a cancelled sitting vanishes from the listing rather than being marked, which is why
  "gone from the listing" is the right test for a retraction. `jointWith` is on 938 of them, and a
  joint sitting is listed under **every** committee in it with a `num` of its own. `comments`
  records the change that happened ("Nastąpiła zmiana godziny/sali/porządku posiedzenia", 886
  sittings) and is read through the `ref`; `notes` (226) and `closed` (279) are read by
  `agenda.sitting_condition` / `hearing_application` (see the invariant below).
  `/proceedings` of the current sitting also carries `schedule`, the approximate hour of each
  agenda point **per day**, the only way to say which day of a four-day sitting a bill is taken on;
  nothing reads it.
- **The agenda names the print that is before the house, which after the third reading is not the
  bill** (211 committee items and 160 plenary ones of term 10 — see the calendar invariant).
  Matching is by text and not by the `PrzebiegProc` link, and `-A` is stripped, so an additional
  report counts as its base print. Otherwise the numbers are reliable — 981 of the 1,150 plenary
  items naming a projekt give one, and the single miss (`sejm/64`) is the API printing "druki nr
  i". **The Sejm names the print last, so a long title pushes it past the clip**: of the 4,040
  agenda items of term 10 that name a print, 111 are over `ITEM_MAX_CHARS` and 65 were quoted to
  the reader with the number gone — and the quoted item is the whole content of a sitting post.
  `items_mentioning` keeps a window around the reference beside the head where that happens.
- `modifiedSince`/`changeDate` are naive **Europe/Warsaw** times; `Z` is rejected. `sort_by`,
  `passed` filters are ignored; paginate by `offset` until an empty page. `documentType` needs the
  Polish display string ("projekt ustawy"), the enum `BILL` does not filter.
- `passed=true` means adopted by parliament, not in force (vetoed bills too). Publication is
  signalled by `ELI`/`displayAddress`; details from `/eli/acts/DU/{year}/{pos}`: `promulgation` =
  Dz.U. date, `entryIntoForce`, `announcementDate` = date in the act's title. Publication follows
  the Sejm vote by ~30–40 days.
- `closureDate` is the **Sejm's** closure, set at the third reading (and on a rejection or a
  withdrawal), not the end of the road: the term-10 listing (2026-09-12) has 938 bills, 498 closed
  with an act, 84 closed and `passed` with no act (druk 2799 closed 2026-09-04, the Senate's 30
  days only starting; the older ones are vetoes, a referral to the Tribunal, bills the President
  never signed) and 47 closed with `passed=false` (odrzucono/wycofano). The listing carries
  `closureDate` and `passed`; an open process never has the date. `End` ("Uchwalono") is appended
  at the third reading and stays last, so it says nothing about how far the bill got — read the
  stages before it.
- **For a few prints the API answers `/prints/{n}` with somebody else's document, and the file it
  offers is not the bill.** Read the other way round until 2026-09-15: the listing's files for
  druki 599, 1768 and 2821 of term 10 all answer 404 while the detail names one that downloads,
  which was taken to mean the detail is authoritative about attachments. It is not — it is a
  *different record*. `GET /prints/2821` returns `title` "Do druku nr 2821 - opinia NBP (nie
  zgłoszono uwag)" with `attachments` `["2821-004.pdf"]`, and 599 and 1768 return "Stanowisko
  Rządu do druku nr N." with `{n}-s.pdf`; the bill's own PDF is genuinely gone from the server.
  So `PrintInfo.main_pdf`, which falls back to the first PDF when `{number}.pdf` is absent, handed
  druk 2821 to the model as a **one-page "no remarks" scan from the National Bank**, and the
  triage judged and closed the row on it (production, run 69, 15 Sept 2026). The fallback itself
  is needed — of the nine prints of term 10 whose detail carries PDFs but no `{number}.pdf`, six
  publish their text under a name of their own (the budget bills' "125-ustawa i załączniki do
  ustawy.pdf", a print amended before the first reading as "2872 (z autopoprawką).pdf") — so what
  is refused is a file *named after a filing*: `{n}-<digits>.pdf` (an `additionalPrints` entry) or
  `{n}-s.pdf` (the government's position). Over all 938 prints of the term that shape occurs
  **exactly three times, on exactly those three prints, always as the only file**
  (`models.sejm._names_a_filing`). Refused, the print reports no text and the bill stays
  `text_prefilter_pending` — a broken record is about the day, like the WAF's refusal, not about
  the bill. The listing does carry `additionalPrints` for all 847 prints that have any, so the
  whole catalogue of 2339 filings still costs one request.
- `additionalPrints` in a print's detail are the documents filed to it after its submission, each a
  print of its own (`1273-001`, `1273-s`) with `title`, `documentDate`, `deliveryDate` and its own
  PDF, served from api.sejm.gov.pl like any attachment (no WAF, unlike the RPW PDFs on orka). Term
  10, 12 Sept 2026: 2339 of them over 3282 prints — 289 "ocena skutków regulacji", 82 "Stanowisko
  Rządu", 1732 opinions (563 saying "nie zgłoszono uwag" in the title itself), 42 amendments tabled
  at the second reading, the rest housekeeping (a changed representative of the applicants, an
  extra list of signatures, an errata). Among prints that have any, the median is one opinion with
  remarks and the maximum sixteen. An autopoprawka is never one of them: it gets a print number of
  its own. Only the government's position is also a stage (`GovermentPosition`, with the document's
  title on it); the OSR and the opinions appear nowhere in the process tree, which is why the bill
  has to remember which of them it has been told.
- `rclNum` and `rclLink` exist only in a process's **detail**, never in the `/processes` or
  `/bills` listing (verified 2026-09-11): finding the print an RCL project became means reading
  details one by one, so `find_process_by_rcl_num` narrows by the hand-over date and caps the
  number of lookups. The other direction is one request (`getIdFromLegislacja`) — **and that
  endpoint counts them**: about 90 in a row and it answers 403 with a 292-byte body until left
  alone. A run asks it once per bill and never meets this, but a sweep must pause (1.5 s was enough
  for all 476 rclNums of term 10, of which 471 resolve and 5 answer 200 because they are genuinely
  not on RCL). A probe that keeps only the `Location` header cannot tell those apart: record the
  status beside it, or "throttled" reads as "not on RCL".
- Committee reports with print `…-A` (proposal "przyjąć poprawki") are amendment tables, not bill
  text; only `proposal` "załączony projekt …" carries it — "odrzucić projekt ustawy" and "uchwalić
  projekt ustawy bez poprawek" name a projekt and attach none. `SenatePosition` reports via
  `position`, not `decision`. `UE` enum is NO|ADAPTATION|ENFORCEMENT.
- **Some stage types are not where the document says.** Over the 5,533 stage trees of terms 8–10:
  `PublicHearing` is **always a child of `CommitteeWork`**, never top-level (15 nodes), which is
  why `events.open_hearing` and `hearings_due` walk `flatten_stages` — and why `PublicHearing` in
  `_phase_after` is unreachable. `ConstitutionalTribunalRuling` exists (3 nodes, term 8) and was in
  no list. 995 top-level nodes carry **no `stageType` at all**, all of them "Rozpatrywanie na forum
  Sejmu" — but only on `wniosek`, `lista kandydatów`, `informacja` and `zawiadomienie` documents
  and on **no** `projekt ustawy`, so this bot never meets one. Whether the `PublicHearing` node
  appears *before* the hearing, which is what `HearingReminder` needs to be worth anything, cannot
  be settled from a snapshot: 47 committee agendas of term 10 mention "wysłuchanie" against 9 such
  nodes, so the hearing is announced mostly through the sittings listing. One observation in
  production would settle it.
- **An autopoprawka is a print of the bill's own number, and nothing else points at it.** The
  applicant amending its own bill before the first reading files `72-A`, `128-A`, `128-B`,
  `128-BA`, `128-C` — 29 in term 10, and `{druk}-A` is never anything else. It is **not** in the
  base print's `additionalPrints` (1 of 29, and that one is the withdrawal of an autopoprawka), the
  base print's detail does not name it (`processPrint` only points the other way), and the base
  print's own PDF does not change — so `SejmTextSource.newer`, `_remember_supplements` and the
  stage tree are all blind to it and the card keeps describing a text the applicant has rewritten.
  `/bills` is the one place it shows: `submissionType: BILL_AMENDMENT` (49 rows), carrying the
  **base** druk in `print`. Two consequences, neither handled yet: discovery is right to skip these
  rows (`not sub.is_bill`, or they would open a second card), and `find_submission` takes the first
  `/bills` row matching a print, which for such a bill can be the autopoprawka and not the bill.
- `Voting` stage embeds totals; per-club breakdown needs `/votings/{sitting}/{n}` (per-MP votes).
  Signatories are not in the API: parse the print's cover letter and match against `/MP`.
- **The cover letter names the representative in the accusative, and the extractor breaks names
  in two ways.** Measured over the 367 deputies' and committee prints of term 10 (cold-places
  audit, 14 Sept 2026, `checks/10_authors.py`). The formula is «Do
  reprezentowania wnioskodawców … **upoważniamy pana posła Krzysztofa Gadowskiego**» — the
  honorific between the verb and "posła", the name declined, sometimes two or three people («Pawła
  Śliza i Michała Gramatykę», the card names the first) and sometimes a committee's nominative
  («została upoważniona posłanka Wanda Nowicka») or a colon («panie posłanki: Anitę…»). Asking
  only for the nominative found the representative on **33 of the 334 prints whose letter names
  one**. Of the two ways a name arrives broken, one is the `\f` of the previous audit — a
  signature wrapped at the foot of a page reads as "(-)  Barbara\n\n\fOliwiecka", and the
  blank-line rule cut it to "Barbara" (18 bare first names over the term) — and the other is
  pypdf's spacing inside a surname ("Osma lak", "Siekiersk i"), which `MpDirectory` answers with a
  second pass on the name with its spaces gone: over 499 members no two squashed names collide,
  and it carries 130 of the 152 signatures that did not resolve. The term now ends with **3
  unresolved signatures of 11,259** and a representative on all 334. What no rule reaches: the 11
  prints whose text layer is empty (a scan — `signatories` is given `text` only, while the
  analysis reads the pages) and the 22 whose covering letter is in the PDF but not in its text.
- Polish text is ~2 characters per token for Claude; the 1M context takes any print whole.
- `HEAD` on a print attachment returns no `Content-Length` and takes 5–15 s on a file the server
  has not rendered yet (the following `GET` is fast). Never probe sizes: stream the `GET` and stop
  at the limit (`download(url, max_bytes=…)`).
- pypdf needs `pypdf[fonts]` (fontTools) for CFF fonts, otherwise it logs a warning per font per
  page; its logger is capped at ERROR. Extraction is CPU-bound (~1 s per 100 pages).
- **The text layer is a trap, and what of a document reaches the model is a set of measured rules**
  — `trim_print`, `page_kind`/`_section_start`, the page break that Python's line anchors do not
  see, where a document's opening is, where the OSR is cut. All of it, with the prints each rule
  was derived from, is in `docs/text-selection.md`. The API fact behind it: the model reads a scan
  as a PDF document block — 32 MB of request (so ≤ 24 MB of file, base64 being a third larger; druk
  2865 is 40 MB and does not fit) and 600 pages, ~1,600 tokens a page measured with `count_tokens`
  on druk 1273 (10 pages 16,157 tokens, 30 pages 47,268, one page 1,622).

## RCL and the wykaz prac RM

How those two sources are read — RCL's markup and its stage catalogs, the file formats in a
"Projekt" folder and the legacy DOC parser, how a package is unpacked and which member is the bill,
how `document_kind` tells an OSR from a tabela zgodności, the consultation letters, the register
CSV and its columns, and the probe results (RCL is unreachable from GitHub runners, gov.pl is not)
— is in `docs/rcl-scraping.md`. The invariants that govern *when* they run are above.

## LLM cost model

Opus 5 is $5/M input, output ~1% of the bill; a text ≥ `triage_min_chars` is triaged on
`llm_triage_model` first, and a whole term of term-10 candidates costs ≈$44 because the triage
rejects 62% of them. The guard rails are `LEXINFORM_MAX_ANALYSIS_COST_USD` ($2 per first analysis)
and `LEXINFORM_MAX_RUN_COST_USD` ($15 per run); **a text over the per-bill limit is cut down to it,
not refused**, and **every refusal turns on `first`, so a re-analysis is never refused**. Every
model call is written down (`RunReport.llm_calls`, `AnalysisService._charge`). The arithmetic, the
measurements and what was tried and rejected are in `docs/llm-cost.md`.

## Product decisions already taken

Default model `claude-opus-5`, `min_score` 3, text prefilter threshold 2 distinct patterns or 3
hits (weak patterns such as Straż Graniczna, "legalizacja" or "nierezydent" never decide alone: in
a text they count next to a strong pattern, in a title they send the bill to the text stage, not to
the model; everyone's registers and benefits (PESEL, mObywatel, NFZ, 800+, prawo jazdy, Kodeks
wyborczy) are deliberately no patterns — measured on the 1500 processes of term 10 each would cost
4–14 full analyses of unrelated bills, while a bill changing them for foreigners names the
foreigners and the text stage catches it), triage of texts ≥ 20k chars on `claude-sonnet-5` (all of
Haiku 4.5 / Sonnet 5 / Opus 5 judged the four test bills correctly; Haiku ignored the output
language, Sonnet costs ~1 cent per bill), club breakdown on, Dz.U. notice as a separate reply,
in-force reminder repeats the summary. The owner does **not** want a "probability of passing"
estimate. A new `PROMPT_VERSION` does **not** re-analyse or re-post bills already in the channel
(2026-09-08): old cards keep the analysis they were published with, only new texts trigger a
re-analysis. Every card and update carries "what comes next" (dated by scheduled sittings) and
"what you can do now" (2026-09-09); a rescheduled sitting is announced again as a new post.
Abbreviations (MSWiA, UdSC, ZUS, PESEL) stay Polish in the analysis, never МВД.

- **RCL** (2026-09-09): every relevant government project is followed, not only those with an open
  consultation; the consultation deadline and e-mail are parsed from the letter deterministically,
  no LLM; RCL cards tell readers to write in Polish and quote the wykaz number.
- **Prints considered jointly** (2026-09-10, after druki 1929/1933 got two near-identical cards):
  one card per group, the later prints are short "alternative bill" replies under it, the
  government's print is preferred for the card. **Revised 2026-09-14**: the print that replies is
  analysed like any other and its reply says how it differs from the prints already in the thread
  — «по сути то же самое» included, which is the commoner and equally useful answer. The
  comparison is made of the channel's own descriptions of the group and not of their texts (about
  a cent a reply; the term's extra reading is $4.21 against ≈$44). The reply still carries no
  verdict of its own: one thread, one score.
- **The wykaz prac RM** (2026-09-12): planned bills get a card of their own, headed "План
  правительства" and saying above everything else that there is no text yet; the RCL project
  inherits that card when it appears (one thread from the plan to Dz.U.); only `Projekty ustaw` are
  followed; the government dropping a project is posted.
- **Operator commands** (2026-09-11): from the technical channel, any admin of it; delivered by a
  relay on the owner's mikrus VPS (384 MB: enough for a getUpdates loop, not for the bot itself)
  into the `inbox` branch, executed by GitHub Actions so the state branch stays the only database
  writer; a manual `/analyze` publishes under the daily rule unless told `publish`.
- **Bills found when their road is already over** (2026-09-12): no card and no analysis, whatever
  the source — but only when there is really nothing ahead (the act is out, the bill was rejected
  or withdrawn, the project or the plan was dropped), and a bill the Sejm has merely passed keeps
  its card, because the Senate and the President are the reader's last windows; the last stage is
  read to tell the two apart.
- **Documents filed to a print** (2026-09-12): only the government's position on a bill it did not
  write and the OSR are told, each with what it says — the position because it decides the bill's
  fate, the OSR because it is the only count of who is affected that a non-government bill gets
  (226 of the 285 filed OSRs go to deputies' bills; a government print carries its own, which
  `trim_print` keeps points 1–5 of). Opinions are not: 1732 of the 2339 filings are opinions, 1.4
  per print and 16 at the most, and "another body has written something" is the chronicle this
  channel is not — with scans now readable that is a decision about noise, not content, and one
  line of `supplement_kind` away from being revisited. The housekeeping filings and the amendments
  tabled at the second reading are not told either; the latter reach the reader through the
  committee's report. The document is judged against the bill's analysis, not in place of it: a
  filed document is what somebody makes of the bill, never a new version of it.

**Product review of 2026-09-12**: a live card is edited in place when what it says has drifted and
a finished one is not; the «Важность» line shows the score without the scale's legend, which read
as a statement about the bill; a sitting or a hearing is told once for a group of jointly
considered prints, not once per print; every reply carries the importance, category and topic tags,
so a tag finds the moments to act and not only the card; a source that is unreachable (`/bills`,
`/proceedings`, RCL, the register) stops its own part of the run and nothing else.

**Product review of 2026-09-13** added the two constitutional deadlines as reminders of their own
and stopped the card freezing while an act's vacatio legis runs; the second review that day settled
how the road *ends* — a veto the Sejm overrode is read from `PresidentMotionConsideration` and gets
the seven days of art. 122 ust. 5 (until then it read as «Сейм рассматривает поправки Сената»), a
veto pending is a committee phase, and a constitutional term that has run out says what running out
meant instead of «срок истёк». It also settled that a step outside the Sejm takes no sitting for
its date, that a committee is named and not coded on the card, that a status update carries the
topic tags like every other reply, that the Dz.U. address belongs on the card and a sentence of the
summary on the Dz.U. notice, and that the analysis never states where the bill stands — the card
does, and only the card is re-rendered (`PROMPT_VERSION` `2026-09-v7`; old cards keep their
analysis).

**Product review of 2026-09-14** read the whole road of all three sources against
`docs/legislative-process.md` and settled four things about how a story *ends* and how a term is
told. **A finished card says so, once**: it settles into «Законопроект: процесс завершён» with the
ending line and no invitation, and the digest holds it there — the refresher used to stop one run
before that, so a rejected bill's card kept «дальше: III чтение» and «написать в комиссию» for the
life of the thread. **The Senate's silence is a step, not a note** (art. 121 ust. 2 makes the
thirty days *zawity*, so the bill moves to the President rather than one line saying both things);
it is the one place the bot names a stage the Sejm has not published, so the wording says so and
the phase carries no deadline. **A date that is exact needs no apology** (`ToPresident` is the
hand-over) and **an urgent bill is told its own term** when one runs out. And two messages stopped
asserting what the data does not carry: «без движения» is never said of a step with a date of its
own (a vacatio legis), and an RPW entry the listing merely stopped showing is «больше не
отслеживается», not «отозван».

**Text selection and cost** (2026-09-13, measured on the 45 prints and 10 RCL packages of
the corpus and on the 46 analyses of the state branch, $10.87 spent to date of which the
top five bills are $6.45): the per-bill limit stays **$2** and stops refusing — a text over it is
cut down to it by the keywords and read, because the triage has already said the bill matters; a
print that will only get a `joint_bill` reply was not analysed at all (**reversed 2026-09-14**: it
is read like any other and the reply says how it differs, $4.21 a term); every model call is
written down so a run's spend can be accounted for. Three things were measured and **rejected**, and should
not be revisited without new numbers: a diff-based re-analysis of a new RCL redaction (three
consecutive packages differ by 63–116% of the new text's lines — the redactions are really
rewritten, so a diff is not smaller than the text); article-level selection inside the bill body
(`Art. N` with keyword hits kept: 21% over 15 prints, because the body is the minority of what is
kept, 14–68%, and a provision the keywords do not name would be lost); and changing
`CHARS_PER_TOKEN` (2.0 verified against the real tokenizer, 1.96–2.08 over five documents). RCL
package handling was checked and left alone: on all ten packages `_pick_parts` refused every
appendix by content and kept bill + uzasadnienie + OSR, $0.03–$0.84 a package.

**What a whole term costs, measured on the corpus (14 Sept 2026).** Of the 819 prints of term 10
with a text layer, 268 pass the keywords; their 132.9M characters become 47.5M after `trim_print`
(**35.8%**), which is **$118.83** on Opus. The triage is what the term actually costs: asked of 183
of them on the production path (`AnthropicAnalyzer.triage`, the production prompt and digest, on
Haiku for the measurement, $1.70 spent) it **rejects 62%** and takes $56.72 of $89.99 off that
sample — so **the term is ≈$44, not $118**, and the triage is the cheapest saving in the system by
a wide margin. What is left is not chaff: mapped page by page, the twelve most expensive candidates
are the law and its reasons, prose to the last page, with **zero** non-prose pages among those
kept. The bill is **$45.90 (38.8%)**, the uzasadnienie **$53.53 (45.2%)** — the largest single line
— and OSR points 1–4 **$18.86 (15.9%)**; twenty prints of the 268 carry 30% of the bill. The
per-bill guard binds **4 prints** and saves $3, so it is insurance and not economy. Article-level
selection stays rejected on new numbers: over all 268 candidates the body is a **median 27%** of
what is kept (quartiles 16% and 42%), the 14–68% of the old sample confirmed, not overturned.

Two of the audit's three questions were settled on 14 Sept 2026. **The uzasadnienie is not
thinned**: it is the largest single line of the bill ($53.53 of $118.83) and it restates the act
article by article, but it is also where the card gets "what this is for", and there is no
measurement of what cutting it would lose. **`azyl` keeps matching "azyle dla zwierząt"**: over the
term the pattern decides the outcome for exactly two prints — druk 1861 (the Centralny Azyl dla
Zwierząt bill, a false positive worth $0.14) and druk 1268 ("osoby ubiegające się o azyl" among
vulnerable groups, a true one) — and both narrowings were measured and are worse than the disease.
Proximity to "cudzoziem|uchod|ochron" zeroes `azyl` on druk 1812, "o czasowym zakazie wjazdu
obywateli", a bill squarely on topic; excluding the named institution cuts 365 hits to 50 and
**changes no outcome at all**, because 50 still clears a threshold of 3. The keyword stage is
over-inclusive on purpose — a false hit costs one call, a miss loses a bill for good — and druk
1861 is rejected by the triage for about two cents anyway.

Open items are listed in `docs/roadmap.md`. The audit of 14 Sept 2026 over the
whole corpus closed six defects and left nothing of its own open. Its record is
`checks/`: `FINDINGS.md` for the findings with their numbers, `README.md` for
how it was run, which of the methods paid and what to do next time.

A second audit the same day asked a different question — whether every event of the legislative
process is detected, read, stored and told — and answered it the same way, by replaying the
production code over the corpus rather than reading it: `next_phase`, `process_stages` and
`veto_stood` over all 5,533 stage trees of terms 8–10, and `agenda.print_numbers` over the 4,387
committee sittings and 75 Sejm sittings of term 10, each result checked against the bill's own
tree. It found three errors and three uncovered cases, all fixed above and each with the corpus
example it was found on. It left five things open, because each needed a decision rather than a
fix; the committee sitting's `notes` and `closed` were taken on 15 Sept 2026 (the invariant
above), and **four remain**: the autopoprawka (detection is one `/bills` query, but an
autopoprawka amends the bill without replacing it, so re-analysing on its PDF would repeat the
`carries_bill_text` mistake — the
supplement path is probably right); the `Opinion`
stage, which is substantive and so posts «Обновление» although the decision of 2026-09-12 says
filed opinions are not this channel's genre — the same fact arriving through a different door; the
plenary `schedule`, which would name the day within a four-day sitting; and the measurement that
would tell whether a `PublicHearing` node ever appears before its hearing.

