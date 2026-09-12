# Roadmap

Planned on 2026-09-07 after the first production runs. Purpose of the project, restated by the
owner: catch bills that may affect foreigners **early**, follow their whole legislative life, and
give readers a chance to **act in time** (public consultations, hearings, opinions to committees).

## Status (2026-09-12)

The blocks below are in the order they were built; "Still open" closes them. See the commit
history for the detail.

Done on 2026-09-07, the day of the plan:

- **Bills before they get a print number** (`RPW/…` entries from `/bills`): discovered, analysed
  from the official description, published with the public consultation dates; linked to the print
  when it is assigned (same Telegram thread), withdrawal announced. Not in the original plan; it
  surfaced when a consultation-stage bill (RPW/29075/2026) was missing from the channel.
- **Feature 3**: voting totals + per-club breakdown, Senate/President outcomes, committee names.
- **Feature 1**: keyword search inside the print PDF (`reprefilter` CLI, shared text cache).
- **Feature 2**: Dz.U. publication notice + entry-into-force reminder (schema v4).
- Action signals, cheap tier: consultation dates on cards, committee referral with name and hint,
  public hearing label.
- Authors of deputies' bills: signatories parsed from the print's cover letter, resolved to clubs
  via `/MP` (schema v5). Publish threshold raised to importance 3.

Done on 2026-09-09 (schema v7):

- **Medium tier of action signals**: committee sitting agendas (`/committees/{code}/sittings`, one
  request per committee a followed bill was referred to) and the agenda of every Sejm sitting that
  is not over (`/proceedings` + `/proceedings/{n}`) are matched against the followed bills by
  "druk nr N" in the agenda text (`lexinform.agenda`). A new (bill, sitting) pair is one reply
  (`agenda` publication, keyed by `ref` = `ASW/136/2026-09-17` or `sejm/65/2026-09-15`; a
  rescheduled sitting is a new ref and a new post). The upcoming items are stored on the bill
  (`agenda_json`) so cards and updates can date the next step.
- **"What comes next" and "what you can do now"** on cards, updates and consultation posts:
  `models.next_phase` derives the phase from the top-level stages, the submission and the act
  (first reading in committee → committee work → second reading → third reading → Senate (30
  days) → President (21 days) → publication → entry into force); the formatter adds the scheduled
  sitting and the concrete action (consultation form until the deadline, opinion to the committee
  before its sitting, public hearing).
- **Consultation form link**: `agent.xsp?symbol=KONSULTOWANY_PROJEKT&NrProjektu=<RPW number>`
  (browser only; the API carries no link). **Consultation results**: when `/bills` flips
  `consultationResults`, one reply links the same page. Discovery no longer refreshes known
  `/bills` rows (tracking does, comparing new with stored).

Done on 2026-09-09/10 (schema v8):

- **Government bills before the Sejm (RCL).** 541 of the 1279 entries in `/bills` of the 10th
  term are government bills and none of them has a Sejm consultation: the government consults at
  the RCL stage, months before the print. `legislacja.rcl.gov.pl` has no API or RSS, so
  `adapters/rcl_html.py` scrapes it (BeautifulSoup): the list of bills sorted by modification date
  (`/lista?typeId=2&sKey=modifiedDate&sOrder=desc&pSize=100&pNumber=N`), the project page
  (timeline of up to 14 stages with states, metadata, hasła, działy, the `RM-…` number once the
  bill went to the Sejm) and the stage catalogs (`/projekt/{id}/katalog/{stageId}`: folders
  "Projekt", "Pisma kierujące…", "Stanowiska zgłoszone…", "Odniesienie się wnioskodawcy…" with
  their files). Projects are `bills` rows `RCL/{id}` with the project in `rcl_json`; the card
  analyses projekt + uzasadnienie + OSR (PDF, Word, ZIP, and legacy `.doc` since 2026-09-09
  through `adapters/doc_text.py`, a piece-table parser over `olefile` measured on 144 real RCL
  `.doc` files against macOS `textutil`: the text matches everywhere it is text, and what the two
  differ on is the field codes — HYPERLINK targets, PAGE — which only `textutil` prints), names the
  ministry, the wykaz number, the consultation deadline and e-mail read out of the letter
  (`rcl_letters.py`: "w terminie N dni od dnia otrzymania", counted from the letter date or its
  publication on RCL) and the RCL comment form; updates follow the stages, a consultation that
  opened later, published opinions, new text versions (re-analysed) and the hand-over to the
  Sejm. A druk whose `rclNum` names a followed project (looked up by the stored RM number or via
  `getIdFromLegislacja?number=…`, which redirects to the project) inherits the card. Decided with
  the owner: every relevant government project is followed, not only those with an open
  consultation; the deadline is parsed deterministically, no LLM. Request budget: one page per
  new project, catalogs only for candidates, one catalog for a title miss, changed catalogs only
  when tracking; six projects at a time (a page takes ~10 s).

Done on 2026-09-09/10 (schema v9, v10, v12):

- **The Sejm term switches by itself.** `services/terms.py` reads the term flagged `current` in
  `/sejm/term` (the newest term in the database when the API is down), `LEXINFORM_TERM` only pins
  an older one. Discovery works in the current term; the repository listings are not scoped to a
  term, so acts of the previous kadencja still get their Dz.U. and in-force posts. The first run
  of a new term runs `services/tracking/rollover.py` before discovery: one "lapsed" update under
  every published, unfinished Sejm bill of the old term (a citizens' bill is taken over instead),
  `discontinued_at` on the unfinished rows (v9), and the RCL projects still waiting for their
  druk moved to the new term, keeping the wykaz number for the tag of the print that continues
  the thread (v10). Idempotent, repeats every run.
- **Prints considered jointly share one card** (v12), after druki 1929/1933 got two nearly
  identical cards: `ProcessSummary.prints_considered_jointly` names the group, the first
  candidate gets the card (the government's print goes first within a run: its text is the one
  the committee works on) and every later print becomes a short `joint_bill` reply under it —
  title, applicant, date, links, both tags, no analysis of its own. The reply settles the bill
  like a card would, and because it is not a `new_bill` row every tracker ignores it: the group's
  events come from the card's process. `/republish` forgets both rows and lets the normal path
  decide again.

Done on 2026-09-10, after a review of the code base (no schema change):

- Bugs against the invariants: a Sejm outage between the pending row and the post no longer
  loses the card; a Telegram outage no longer counts as an attempt of the post; a new text that
  cannot be read keeps the previous analysis; the stage fingerprint is saved after the change
  row; a failed "opinions published" notice is retried; held stages are released against the
  message just sent; message assembly names its head and tail blocks and cuts whole lines as a
  last resort; `run --no-publish` records skipped rows for the tracking announcements; the
  formatter dates from the run's clock in Warsaw time; `daily.yml` re-dumps on the fetched tip
  instead of rebasing, tolerates a missing dump only on the run that creates the branch and
  alerts the log channel when the job fails around the run.
- Keywords: citizens named by citizenship (EU, third countries: druk 1812 was invisible), work
  permits, seasonal work, employer declarations, recognised qualifications, foreign students, the
  bare genitive "wiz"; `nierezydent` and border crossing as weak patterns; a weak title hit alone
  goes to the text stage. Measured on the 1500 processes of term 10; PESEL, mObywatel, NFZ, 800+,
  prawo jazdy and Kodeks wyborczy deliberately stay out (4–14 unrelated titles each).
- The text prefilter records why it skipped a bill (`last_error`); the report counts unreadable
  texts and shows cache reads; `lexinform runs` / `cost`; cost guard rails per bill and per run.
- Refactoring: one `Publisher` implementation (`adapters/publisher_base.py`), formatter helpers
  and one stage translation, domain predicates in `models/`, the container typed on the ports and
  the test harness building the real container.

Done on 2026-09-10 (schema v11), after a reader's-eye review of the update posts:

- Updates are named after their event (header from `models.update_event`), list the new stages in
  the reader's language with the committee's proposal, and repeat one sentence of the summary
  unless the analysis changed. Frame stages ("Skierowano", "Praca w komisjach", the first reading,
  "Uchwalono") are held and told with the next substantive update; a closure that comes with the
  act is left to the Dziennik Ustaw notice. The hand-over to the President is *not* a frame stage
  (`models/events.py`): it starts the 21 days of art. 122.
- Amendments are read: the Senate's resolution print (`SenatePosition.printNumber`) and the
  committee reports whose proposal is about poprawki ("-A", the report on the Senate's position)
  get a model summary (`Amendments`: what changes, whether it touches foreigners), stored on the
  status change and rendered as "Что меняют поправки Сената".
- Public hearings: the application deadline (10 days before) on the stage line and in "what you
  can do now"; a reminder reply before applications close. Senate and President deadlines as dates.
- Cost: a re-analysis needs a changed text. The analysed text is hashed (`text_sha256`); a
  republished RCL file or a print re-dated by an attachment is recognised and not sent to the
  model. The text after the 3rd reading is skipped when the Sejm adopted the committee's text as
  it was (no 2nd-reading amendments, no minority motions). Measured on the state dump (Sept
  2026): the single biggest analysis was a government project of 338k chars, 171k input tokens
  (~$0.85), and every RCL stage republishes that package; without the hash each republication
  would cost the same again.

Done on 2026-09-11 (schema v13):

- **Operator commands from the technical channel.** `/analyze BILL [force] [publish]`, `/show`,
  `/skip`, `/republish`, `/help`; a bill by druk/RPW/RCL/wykaz/RM number or a link to
  sejm.gov.pl, api.sejm.gov.pl or legislacja.rcl.gov.pl. The bot is a batch job, so a relay
  (`lexinform listen`, on the owner's mikrus VPS: 384 MB, enough for a getUpdates loop and not
  for pypdf) files each command as a JSON file into the git branch `inbox` through the GitHub
  Contents API and sends a `repository_dispatch`, which starts `daily.yml` within seconds (only
  cron is delayed here; a push of the orphan inbox branch would start nothing); the run answers
  under the command (~3–5 min). Decided with the owner: commands from channel
  admins only; a manual `/analyze` publishes under the daily rule (relevant and score ≥
  `min_score`), `publish` overrides; the whole bot does not move to the VPS (memory), so the
  state branch stays the only database writer. `docs/operator-commands.md`.

Done on 2026-09-12 (schema v14):

- A command whose answer never reached the channel (the channel was down, the job died after the
  post) is **answered again, not executed again**: `commands.executed_at` marks the side effects
  as done before the reply is posted, so a file that comes back cannot produce a second card.
  `/analyze` was already idempotent by construction; `/republish` was not.
- An RCL project a command names may already be in the Sejm: `BillLookup` resolves it to its druk
  through `find_process_by_rcl_num`, links the rows and answers about the print, so a project
  whose act is in force can no longer get a card promising a druk number.
- The database on one page, drawn and explained: `docs/database.html` (tables, relations,
  indexes, the migration ledger). The source of truth stays `MIGRATIONS` in `sqlite_repo.py`.

Done on 2026-09-12 (schema v15), a fourth source:

- The wykaz prac legislacyjnych i programowych RM (KPRM, gov.pl) as one CSV: `WPL/UD408` rows,
  analysed from the register's own `Cele`/`Istota` with no text to read, carded as an intention
  and linked forward to the RCL project that continues them. Measured lead time on UD408
  (o zmianie ustawy o cudzoziemcach): entered 2026-05-12, on RCL 2026-07-06 — 55 days.
- Only `Projekty ustaw` are followed, only entries published since the watermark are stored (the
  state dump is 822 KB; the 775 bill entries with their paragraphs would add ~2.3 MB per run),
  and the rest is reported as backlog.
- GitHub-hosted runners reach www.gov.pl directly (probed 2026-09-12 from Azure eastus2: page
  0.57 s, CSV 3.1 s / 2.8 MB gzipped), unlike RCL, so the source needs no proxy.
  `LEXINFORM_WYKAZ_PROXY_URL` and `.github/workflows/wykaz-probe.yml` stay for the day that
  changes.

Done on 2026-09-12 (no schema change):

- **A bill found when its road is already over gets no card and no analysis** (every source).
  `models.is_over(bill, today)` is the test: the act in Dziennik Ustaw, a rejection or a
  withdrawal, an RCL project closed without reaching the Sejm, a plan realised or taken off the
  wykaz, a lapsed term. `closureDate` is not that test — the Sejm sets it at the third reading
  and 84 of the 938 term-10 bills are closed and `passed` with no act yet (druk 2799: closed
  2026-09-04, the Senate's 30 days only starting) — so discovery reads the stage tree once for a
  bill it meets for the first time with a closure date, and a bill the Sejm has merely passed
  still gets its card: the Senate and the President are the reader's last windows. Skipped rows
  are stored as `skipped_closed` (`reset --to analysis_pending` revives them); bills already
  followed keep their card and their updates to the end.
- **An urgent bill is told in urgent terms.** A bill the government declared *pilny* (art. 123;
  `models.is_urgent`) gets its own wording in "what comes next": `Labels.urgent_step_labels` and
  `urgent_durations` replace the normal steps and the constitutional deadlines shrink to 14 days
  for the Senate and 7 for the President, so a card never promises weeks where the Sejm measured
  days.

Product review (2026-09-12), against the live channel. The findings and what each one cost a
reader are in the commit messages; the shape of the fixes:

- **Phase derivation told the truth about the states it actually meets.** A second reading that
  sent the bill back to committee ("skierowano ponownie", "niedokończone II czytanie") no longer
  announces the third reading with nothing to do; a Senate rejection is not amendments; a plan
  the Council of Ministers adopted is not waiting for RCL; the card's "stage" line skips the
  nodes that arrive beside the process and were rendering as raw Polish.
- **Nothing time-dependent is printed once it has passed** (`Phase.since`, `stalled_days`,
  `PHASE_PATIENCE`, `DEADLINE_GRACE_DAYS`), and a **live card is re-rendered each run** and
  edited in place when it has drifted (`tracking/cards.py`, `publications.rendered_sha256`, v16).
  A finished bill's card says how the road ended instead of losing its last three lines.
- **The end of the road is followed to the end**: a veto or a referral to the Tribunal no longer
  ages out 180 days after a `closureDate` set at the third reading, a closure is suppressed only
  when the Dziennik Ustaw notice really went out, and a withdrawal, a sustained veto and a
  rejection are told apart instead of all reading "Сейм отклонил проект".
- **A run that cannot post does not consume what it saw** (`--no-publish` holds), and a source
  being unreachable (`/bills`, `/proceedings`, ELI) stops its own part of the phase, not the
  reminders and notices the rest of the run owes.
- **Sittings**: a called-off sitting is not announced (`CommitteeSitting.status` was parsed and
  never read), a moved one corrects the earlier post, a sitting already over is not posted, and a
  hearing announced with less than ten days' notice is told rather than skipped.
- **One event, one post for a group of jointly considered prints**; the "alternative bill" reply
  shows where the group stands.
- **Tags**: every reply carries importance, category and topic, the event vocabulary covers what
  a reader searches for, and the tag line is in one alphabet.

Done on 2026-09-12 (schema v18):

- **The documents filed to a print are read, not only listed.** `PrintInfo.additional_prints` was
  parsed from the start and looked at by `lexinform show` alone. Now `supplement_kind` picks out
  the three kinds worth a word — the government's position on someone else's bill, the OSR the
  Marshal asks the applicant for, an opinion that raised something — and each new one is digested
  against the bill's current analysis by one model call and told as one reply under the card. The
  arrival of the government's position used to be a bare "Обновление" (the `GovermentPosition`
  stage has no name of its own), and a late OSR was a wasted download: it re-dated the print, the
  main PDF hashed the same, and the assessment itself was never read.

Knowingly not modelled, and cheap to add if a case turns up: the seven days the President has to
sign after the Sejm overrides a veto (art. 122 ust. 5 — the card would still say 21), the budget
act's own terms (20 days for the Senate, 7 for the President, art. 223/224) and a constitutional
amendment's 60 days for the Senate (art. 235). Only the `urgencyStatus` split is read, and the
budget and the constitution are not what this channel follows.

Still open:

- A retroactive merge for druki 1929/1933, carded before the joint-print rule existed: both
  threads stay, though they no longer duplicate each other's sittings.
- `Analysis.confidence` is asked for and read by nothing; either show it or stop asking.

- RCL leftovers: consultations of draft regulations (rozporządzenia, `typeId=10`); the
  zgłoszenie zainteresowania is offered on the card but the declarations already filed
  ("zgłoszenia lobbingowe") are not read.
- Wykaz prac RM leftovers: the rozporządzenia (`RD`) and programme documents (`ID`, e.g. the
  migration strategy) it also lists, the ministers' own registers on their gov.pl pages (the CSV
  link is not at the same URL pattern there), and the backlog — an entry rewritten into relevance
  long after publication stays invisible, because `Data publikacji` does not move on an edit;
  `lexinform scan --since …` is the way in.
- Ukrainian-language channel; weekly digest; static site from the state dump.
- Committee e-mail addresses in "what you can do now" (the Sejm API has none; the committee page
  is linked instead) and the Senate committee that received the act (the Senate API is not
  used; the card links the Senate's listing of the laws the Sejm has passed).

The sections below are the original plan, kept for the rationale and the verified API facts. They
are not updated as the code moves on: where a name or a CLI flag below differs from the code, the
code is right.

---

Verified API facts used below (curl, 2026-09-07):

- `GET /sejm/term` (2026-09-09): one item per term with `num`, `from`, `to` (absent for the
  running term), `current` (true for exactly one) and `prints.count/lastChanged`. This is what
  `services/terms.py` reads instead of `LEXINFORM_TERM`; the end of a term is handled by
  `services/tracking/rollover.py` (decided 2026-09-09: lapsed bills get one last update and are
  dropped from tracking, passed bills and RCL projects are followed on).
- `GET /sejm/term10/processes/{n}` carries `address` ("WDU20260001099"), `displayAddress`
  ("Dz.U. 2026 poz. 1099"), `ELI` ("DU/2026/1099") and `links[]` (rel `isap`, `eli`, `eli-api`).
  For druk 2699 `closureDate` = 2026-07-17 (3rd reading) and promulgation = 2026-08-18: 32 days
  later. The tracking grace period was therefore raised from 30 to 90 days.
- Post-Sejm stage types: `SenatePosition` (`position`: "nie wniósł poprawek" / "wniósł poprawki"),
  `SenatePositionConsideration` (`decision`), `ToPresident`, `PresidentSignature`, `Veto`,
  `PresidentToTribunal`, `End`. Vetoed bills still have `passed=true` and `ELI=null`, so `passed`
  means "adopted by parliament", not "in force"; `ELI`/`address` is the publication signal.
- `GET /eli/acts/DU/2026/1099`: `ELI`, `displayAddress`, `title`, `status` ("obowiązujący"),
  `inForce` (IN_FORCE|NOT_IN_FORCE), `entryIntoForce` (2026-11-19), `promulgation` (2026-08-18,
  the Dz.U. date), `announcementDate` (2026-07-17: the date of the act, despite the name),
  `texts[]`, `prints[]`. Unknown act: 404. `/text.pdf` works. Exactly one `entryIntoForce`.
- `Voting` stage (child of the 3rd-reading `SejmReading`) embeds `voting`: `yes`, `no`, `abstain`,
  `notParticipating`, `totalVoted`, `present`, `majorityType`, `majorityVotes`, `sitting`,
  `votingNumber`, `date`, `topic`, `links[rel=pdf]`. `GET /votings/{sitting}/{number}` adds
  `votes[]` (`MP`, `club`, `vote` YES/NO/ABSTAIN/ABSENT); no per-club aggregation server-side.
- Volume: 93 new bills between 2026-06-01 and 2026-09-07 (max 6 per day), so a text prefilter
  means 1–6 PDF downloads per day.
- `tests/fixtures/sejm/process_1962.json` already contains a `Voting` stage with results and a
  `SenatePosition` stage.

---

## 1. PDF-text prefilter (second stage)

**Behaviour.** Bills whose title/description miss the keywords are no longer dropped at once: the
main print PDF is scanned with the same `KEYWORD_PATTERNS`, and strong hits send the bill to
analysis. Hits are stored with a prefix (`title:cudzoziemcy`, `text:cudzoziemcy`); the run report
gets `text prefilter: checked N · hits M`. No new Telegram message.

**Status flow.** `DISCOVERED` → title hit → `ANALYSIS_PENDING`; title miss → new
`TEXT_PREFILTER_PENDING` → text hit → `ANALYSIS_PENDING`; miss / no PDF / oversize / unreadable →
new `SKIPPED_TEXT_PREFILTER` (weak hits still stored so the threshold can be tuned later from the
state branch). Legacy `SKIPPED_PREFILTER` keeps meaning "title-only skip, text never checked".

**Threshold** (pure function in `keywords.py`): `match_counts(text) -> dict[str, int]`; accept when
`distinct >= 2` **or** `sum(counts) >= 3`. One "cudzoziemiec" in a 200-page tax bill is noise.

**Design.** No migration. New `services/text_prefilter.py::TextPrefilterService` with
`run(*, limit)`; a `text_prefilter` phase between discovery and analysis;
`ServiceUnavailableError` aborts the phase leaving bills pending, any other per-bill error →
`SKIPPED_TEXT_PREFILTER` + warning. No text caching in the DB (the dump lives in git); the double
download (prefilter + analysis) is avoided by a run-scoped cache in `TextLoader`, not by a column.
(As built: `TextPrefilterService(repo, texts, loader, prefilter, *, min_distinct,
min_occurrences, workers)` — the download limit belongs to the loader, and the source of a bill's
text to `TextSources`.)

**Settings.** `text_prefilter_enabled=True`, `text_prefilter_min_distinct=2`,
`text_prefilter_min_occurrences=3`, `text_prefilter_max_per_run=20`.

**CLI.** `lexinform reprefilter [--limit N] [--include-text-skipped]` runs the text stage over
previously skipped bills (for an RCL project it reads the newest text again: a skipped project
keeps no documents); candidates are analysed by the next `run` (use `run --no-publish` after a big
backfill). `scan` also runs the text stage.

**Tests.** `match_counts` + threshold table; real `print_3039.pdf` → accepted; service on fakes
(hit, weak hit, no PDF, oversize, Sejm down leaves pending, extractor error → skipped); pipeline
test with a "VAT" title and foreigner-heavy text; `enabled=False` reproduces old behaviour.

**Steps (~0.5–1 day).** statuses + pure functions → service + tests → pipeline phase + report line
→ settings/container → CLI → docs.

---

## 2. Publication in Dziennik Ustaw and entry into force

**Behaviour.** Two new replies to the original card:

```
📢 Опубликован в Dziennik Ustaw — druk nr 2699

Ustawa z dnia 17 lipca 2026 r. o zmianie ustawy o nabywaniu nieruchomości przez cudzoziemców …

📰 Dz.U. 2026 poz. 1099 (опубликован 18.08.2026)
📅 Вступает в силу: 19.11.2026
ℹ️ Отдельные положения могут вступать в силу в другие сроки — см. текст закона.

🔗 Ход процесса в Сейме | ISAP | Текст закона (PDF)

#опубликован #druk2699 #Sejm10
```

```
⚖️ С сегодняшнего дня действует — druk nr 2699

Ustawa z dnia 17 lipca 2026 r. …

📰 Dz.U. 2026 poz. 1099 · вступил в силу 19.11.2026

📝 Суть закона
…current summary…

💡 Что это значит на практике: …

🔗 ISAP | Текст закона (PDF)

#вступилвсилу #druk2699 #Sejm10
```

If the act is discovered already in force, the first message says "уже действует с DD.MM.YYYY" and
the reminder is recorded as `skipped`.

**Data model.** `ProcessDetail` + `display_address`, `isap_url`. New frozen `ActInfo` (`eli`,
`display_address`, `title`, `act_date`, `promulgation_date`, `entry_into_force`, `in_force`,
`status`, `text_pdf_url`, `isap_url`, `fetched_at`); `Bill.act: ActInfo | None`.
`PublicationKind` + `ACT_PUBLISHED`, `IN_FORCE`. Migration v4 (v3 went to the RPW rows, which
were built first):

```sql
ALTER TABLE bills ADD COLUMN act_json TEXT;
ALTER TABLE bills ADD COLUMN entry_into_force TEXT;
CREATE INDEX ix_bills_entry_into_force ON bills(entry_into_force);
CREATE UNIQUE INDEX ux_pub_once_per_kind ON publications(term, number, kind, channel_id)
    WHERE kind IN ('act_published', 'in_force');
```

**Ports/adapters.** New `EliGateway(Protocol)`: `get_act(eli) -> ActInfo | None` (None on 404,
`SejmApiUnavailableError` on transport/5xx), implemented on `SejmApiClient` (same host, same retry
loop). `Publisher` + `publish_act_published(bill, reply_to)`, `publish_in_force(bill, reply_to)`;
formatter + console publisher mirror them. Repo: `save_act`, `list_due_in_force(term, channel,
today)`, relaxed `list_tracked`.

**Service.** In `StatusTrackingService._detect`: when `detail.eli` is set and `bill.act` is None,
fetch the act, save it, post the publication notice through the pending-row protocol. New
`remind_in_force(term, *, publish)` after `check_updates`: bills with `entry_into_force <= today`
(today in Europe/Warsaw) and no `in_force` publication get the reminder. `list_tracked` keeps
passed bills without an act up to `track_passed_max_days` (180).

**Limitation.** ELI exposes one `entryIntoForce`; staged provisions are not modelled, hence the
fixed note in both messages.

**Steps (~1.5–2 days).** models + parsing + fixtures (`process_2699.json`,
`eli_act_DU_2026_1099.json`) → `EliGateway` + tests → migration + repo methods → labels +
formatter + publisher + fakes → tracking + pipeline wiring + report counters → settings → README.
(Built without a CLI command of its own: `lexinform show NUMBER` prints the act, `track` runs the
phase.)

---

## 3. Voting results and Senate/President outcomes in updates

**Behaviour.** Stage lines in the existing update become richer:

```
🧭 Новые стадии
• 2026-07-17: III czytanie na posiedzeniu Sejmu — uchwalono
  🗳 Голосование: 239 за, 1 против, 199 воздержались · не голосовали: 21 · протокол (PDF)
• 2026-08-06: Stanowisko Senatu — Сенат внёс поправки (druk 2994)
• 2026-08-13: ✍️ Президент подписал закон
• 2026-07-17: ⛔ Президент наложил вето (druk 2863)
```

Per-club breakdown ("за: KO 152, PSL-TD 31 · против: PiS 178") behind `voting_club_breakdown`,
which was switched on the same day.

**Data model.** `Stage.voting: VotingSummary | None` (`yes`, `no`, `abstain`, `not_participating`,
`total_voted`, `majority_type`, `sitting`, `voting_number`, `date`, `pdf_url`, `topic`). `Stage.position`
already exists (2026-09-07). `_stage_key` is **not** changed, so stored fingerprints stay valid
and no spurious updates fire. No migration.

**Formatter.** `_stage_line` dispatches by `stage_type`: `Voting` → totals + PDF link;
`SenatePosition` → label from `position`; `PresidentSignature`, `Veto`, `ToPresident`,
`PresidentToTribunal` → `stage_type_labels` in `i18n`.

**Steps (~0.5 day; +0.5 for clubs).** models + parse + fingerprint test → labels + formatter →
(optional) `get_voting`, `aggregate_clubs`, enrichment in `_detect` behind `voting_club_breakdown`.

---

## Action signals (add-on, same rendering path)

Verified endpoints (curl, 2026-09-07):

- `GET /sejm/term10/bills?print=3039` → `publicConsultation: true`,
  `publicConsultationStartDate: 2026-08-05`, `publicConsultationEndDate: 2026-09-04`,
  `consultationResults`, plus `applicantType`, `status`, `euRelated`.
- `GET /sejm/term10/committees/{code}` → `name`, `nameGenitive`, `scope`;
  `/committees/{code}/sittings` → `date`, `startDateTime`, `room`, `status`, `agenda` (HTML that
  literally contains "druk nr 2846").
- `GET /sejm/term10/proceedings` → sittings with `dates[]`, `number` (0 for planned ones), `title`;
  future planned sittings are included.
- `PublicHearing` stage carries only `stageName`, `date`, `children`.

Cheap tier (~1 day on top of the stage rendering in feature 3):

1. **Public consultation on the card** from `/bills?print=N` (one request per analysed bill):
   `🗣 Общественные консультации: до 04.09.2026 — мнение можно направить через страницу druku`,
   plus a reminder 3 days before the deadline through the same idempotent status-change mechanism.
2. **Committee referral with the committee name** (`Referral` + `committee_code`, names cached per
   run): `📮 Направлен в комиссию: Komisja Administracji i Spraw Wewnętrznych (ASW) — мнения
   организаций можно направить в комиссию`, with a link to the committee page.
3. **Public hearing** (`PublicHearing` stage): `📢 Публичные слушания (wysłuchanie publiczne):
   12.10.2026 — можно подать заявку на участие`. Whether the stage appears ahead of the date must
   be observed live.

Medium tier (later): scan `/committees/{code}/sittings` for future items whose `agenda` mentions
`druk nr N` → `🗓 Комиссия ASW рассмотрит проект 15.09.2026, 10:00 (sala 412)`; scan the agenda of
the next `/proceedings` entry → `🗓 Проект в повестке заседания Сейма 7–9 октября`. One request per
committee/proceeding per run; needs HTML-to-text and a dedupe key per (bill, sitting).

## Order

Feature 3 first (small, no schema change, introduces `stage_type_labels` reused by 2), then 1
(independent, raises recall), then 2 (largest, the only migration), then the cheap tier of action
signals. Never edit earlier entries of `MIGRATIONS`; `restore()` migrates old dumps.

## Decisions taken with defaults (change if needed)

| Question | Default |
|---|---|
| Text threshold | distinct ≥ 2 or occurrences ≥ 3 |
| New statuses vs reuse | new `text_prefilter_pending` / `skipped_text_prefilter` |
| Cache PDF text between prefilter and analysis | run-scoped in `TextLoader`, not in the DB |
| `reprefilter` results published by next `run` | yes; document `run --no-publish` for backfills |
| `EliGateway` implementation | same `SejmApiClient` class, separate Protocol |
| Publication notice merged with a stage update | no, separate message |
| Club breakdown | `voting_club_breakdown`, on since 2026-09-07 |
| "Today" for reminders | Europe/Warsaw |
| Tracking cap for passed bills without an act | 180 days |
| In-force reminder repeats summary | yes |
| Re-fetch ELI metadata | only while `entry_into_force` is null |
