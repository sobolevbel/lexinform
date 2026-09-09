# lexinform — notes for Claude Code

Daily bot: finds Polish bills that affect foreigners (Sejm API, and legislacja.rcl.gov.pl for
government projects still with the ministries), scores them 1–5 with an LLM, posts Russian cards
to a Telegram channel and follows each bill until the act is in force. The point is not a
chronicle but *timely action*: consultations, committee referrals, hearings, deadlines.
Everything else in `README.md`; roadmap and verified API facts in `docs/roadmap.md`; the whole
legislative process (RCL → Sejm → Senate → President → Dz.U.), its deadlines, the public's
windows and the API stage vocabulary in `docs/legislative-process.md`.

## Working rules

- Python 3.12, `uv`. Check with `uv run pytest -q && uv run mypy && uv run ruff check src tests`
  and `uv run ruff format src tests`. All three must be clean before a commit. mypy is strict over
  `src` and `tests`: no `type: ignore`, no local imports, tests fully typed.
- Developer guide (setup, tests, migrations, where a change goes): `CONTRIBUTING.md`.
- Commit after each finished part. Do not push unless asked. No `Co-Authored-By` trailers.
- `.env` holds real secrets and is untracked; never print values. `.env.example` mirrors keys.
- Messages to readers are Russian (labels in `i18n.py`, RU + EN); Polish law titles stay Polish.
- Prod state = SQLite dump in the `state` branch, written by `.github/workflows/daily.yml`
  (04:23, 10:23, 16:23 UTC; full-hour crons were delayed by 4+ hours). To test against real data: `git show origin/state:lexinform.sql > /tmp/s.sql`,
  `LEXINFORM_DB_PATH=/tmp/t.db uv run lexinform db init && … db restore /tmp/s.sql`, then
  `lexinform run --dry-run --since YYYY-MM-DD` (real LLM calls, DB rolled back, prints to stdout).

## Architecture in one breath

`models/` (pydantic + pure helpers; `enums`, `sejm`, `rcl`, `analysis`, `bill` (incl.
`next_phase`, `ConsultationWindow`), `report`, all re-exported from `lexinform.models`) →
`ports.py` (Protocols) → `adapters/` (Sejm API, ELI, RCL scraper `rcl_html`, PDF, Word +
format sniffing `document_text`, Anthropic, Telegram, SQLite) → `services/` (discovery,
rcl_discovery + rcl_projects, sources (`TextSources` routes a bill to `SejmTextSource`,
`RclTextSource` or `MetadataOnlySource`), documents (`TextLoader`, downloads routed by host),
text_prefilter, analysis, signatories, publishing, `tracking/` (service, pre_print, rcl, linking,
acts, consultations, agenda, posting, stages), pipeline) → `container.py` (manual wiring) →
`cli.py` (typer). Services import only ports, models and the pure modules (`keywords`, `sections`
incl. `TextBudget`, `agenda`, `authors`, `rcl_letters`, `concurrency`), never adapters; the
generic services (analysis, text prefilter, formatter, `next_phase`) never branch on the source:
they read `Bill.has_process`, `Bill.consultation`, `Bill.rcl` and the `TextSource` port. Tests
use fakes in `tests/fakes.py` and the `World` harness in `tests/harness.py` (arrange with
`add_bill`/`add_rcl_project`/`set_stages`/`touch`, act with `run`, assert on the report, the
publisher's records and `bill`/`publication`); HTTP adapters use `httpx2.MockTransport`. Tests
follow arrange-act-assert and never touch private attributes.

Invariants worth keeping:

- **The term comes from the API, the old term is drained, not dropped.** `LEXINFORM_TERM` is
  empty by default: `services/terms.py::TermResolver` takes the term flagged `current` in
  `/sejm/term` (newest term in the DB when the API is down; nothing known → the run stops with
  the reason). Discovery runs in the current term only; the queues and tracking loop over every
  term in `bills` (`repo.known_terms()`), so acts of the old term still get their Dz.U. and
  in-force posts. The first run in a new term (`tracking/rollover.py`, its own phase *before*
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
- **`/bills` rows are refreshed by tracking only.** Discovery saves a submission for new bills;
  the reconciler (`tracking/pre_print.py`) re-reads `/bills` for pending RPW entries and for bills
  awaiting consultation results and compares new with stored (print assigned, withdrawn,
  `consultationResults` flipped). If discovery overwrote the row first, the flip would be lost.
- **"What comes next" is derived, not stored.** `models.next_phase(bill, today)` reads the
  top-level stages, submission and act; the formatter dates it from `bill.agenda` (upcoming
  sittings, refreshed every run for every followed bill, not only the changed ones).
- **Stage fingerprint** (`_stage_key`) drives updates. Fields added to `Stage` for rendering
  (`voting`, `position`, `committee_name`, `proposal`) must stay *out* of the key, or every tracked
  bill posts a spurious update after deploy.
- **Migrations are append-only.** `dump()` writes `PRAGMA user_version`; `restore()` migrates old
  dumps. Test every migration against a v1 dump (see `test_sqlite_repo.py`).
- **Outages never burn per-bill attempts.** `ServiceUnavailableError` subclasses abort a phase
  and go to the log channel; everything else is a per-bill failure (3 attempts). Unknown model id
  and bad request parameters are fatal, "prompt too long" is per-bill.
- Pre-print bills (`RPW/…`) and RCL projects (`RCL/{id}`) have no Sejm process
  (`Bill.has_process` is false): skip `get_process`/`get_print` for them; when the print appears,
  the print inherits the card (`tracking/linking.py::Linker`: `new_bill` row aliased with the same
  `message_id`). The RPW reconciler finds the print in `/bills`; for RCL, Sejm discovery notices a
  druk whose `rclNum` names a followed project (stored RM number, else
  `getIdFromLegislacja?number=…`), stores the druk number on the RCL row and the RCL watcher links.
- **RCL rows are refreshed by the RCL watcher only.** RCL discovery reads a project once (timeline
  + catalogs for a candidate, one catalog for a title miss) and afterwards only bumps
  `change_date` from the list; `RclWatcher` re-reads the timeline and the catalogs whose "Data
  ostatniej modyfikacji" moved, and `rcl_fingerprint` (stages reached, folders that got their
  first files, the newest text, status, hand-over) decides whether there is an update. Folder
  uploads alone are not news; published opinions are a separate `consultation_results` reply.
  A page takes ~10 s: never add a request per project without a reason.
- **RCL markup is parsed, not matched.** `adapters/rcl_html.py` uses CSS selectors; a missing
  detail (date, folder, link) is tolerated, a missing structural element (timeline, table with
  rows announced, every stage label) raises `RclPageError`, which the run report shows. The WAF's
  "Request Rejected" page (HTTP 200) is `RclUnavailableError`.
- **Parallelism only around the network.** `concurrency.fan_out` runs one network step (download,
  process lookup, model call) for many items; that step never touches the repository. Outcomes
  are consumed in the calling thread, in input order, and that is where every DB write happens.
  Services default to `workers=1` (tests); `workers=4` must give identical results.

## Database versioning and migrations

The schema version is SQLite's `PRAGMA user_version`; the source of truth is the `MIGRATIONS`
tuple in `adapters/sqlite_repo.py`. Script at index `i` brings the database to version `i + 1`;
`SCHEMA_VERSION = len(MIGRATIONS)` (v9 as of Sept 2026). `migrate()` reads `user_version` and
runs every later script inside its own transaction, stamping the new version at the end, so a
failed script leaves the database at the previous version. v8 (Sept 2026) added `rcl_json`, v9
`bills.discontinued_at` and `status_changes.discontinued` (end of a Sejm term).

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
3. Extend `test_restore_of_a_previous_schema_dump_applies_missing_migrations` in
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
  (browser only: www.sejm.gov.pl answers curl and fetchers with an F5 captcha). Only deputies',
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
  and blocked clients get HTTP 200 with `<title>Request Rejected</title>`. Plain `curl` works.
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
  `li.doc > a[href=/docs//…/dokumentN.ext]`. Files: DOCX/DOCM ~55%, PDF ~35%, legacy DOC ~10%
  (unreadable → metadata-only analysis). RCL's OSR is a separate Word form starting with "Nazwa
  projektu"; point numbers are list formatting, so `sections._OSR_CUT_RE` accepts the heading
  without "6.".
- Consultation letters give a relative deadline ("w terminie 7/14 dni od dnia otrzymania
  niniejszego pisma", 30 for social partners), often no date (electronic time stamp) and the
  e-mail for comments ("na adres: …"). `rcl_letters.parse_letter` reads them; the deadline counts
  from the letter date or the day the letter appeared on RCL. Every project also has a comment
  form `/projekt/{id}/komentarz` (captcha).
- Join: `/processes` `rclNum="RM-0610-81-26"`, `rclLink=…/getIdFromLegislacja?number=…` →
  302 to `/projekt/{id}`.

## LLM cost model (Sept 2026)

Opus 5 is $5/M input; output is ~1% of the bill. A government print is bill + uzasadnienie + OSR
(13-point form) + appendices (consultation report, tabela zgodności, draft regulations with their
own uzasadnienie/OSR), and the appendices are 55–80% of the text. `sections.trim_print` keeps the
bill, uzasadnienie and OSR points 1–5 (pages are separated by `\f` by the extractor). Long texts
(≥ `triage_min_chars`) first get a triage on `sections.excerpts` (heads + windows around keyword
hits) by `llm_triage_model`; a confident "no" is stored as a non-relevant analysis with
`text_source="excerpts"`. Real numbers: druk 2695 (564k chars, irrelevant) cost $1.45 in full,
would cost ~$0.01 with the triage. The triage call runs without extended thinking (Haiku 4.5
rejects `thinking: adaptive`; a classification does not need it).

## Product decisions already taken

Default model `claude-opus-5`, `min_score` 3, text prefilter threshold 2 distinct patterns or 3
hits (weak patterns such as Straż Graniczna never decide alone), triage of texts ≥ 20k chars on
`claude-sonnet-5` (all of Haiku 4.5 / Sonnet 5 / Opus 5 judged the four test bills correctly;
Haiku ignored the output language, Sonnet costs ~1 cent per bill), club breakdown on, Dz.U. notice as a separate reply, in-force reminder repeats
the summary. The owner does **not** want a "probability of passing" estimate. A new `PROMPT_VERSION` does
**not** re-analyse or re-post bills already in the channel (decided 2026-09-08): old cards keep the
analysis they were published with, only new texts trigger a re-analysis. Every card and update
carries "what comes next" (dated by scheduled sittings) and "what you can do now" (decided
2026-09-09); a rescheduled sitting is announced again as a new post. RCL (decided 2026-09-09):
every relevant government project is followed, not only those with an open consultation; the
consultation deadline and e-mail are parsed from the letter deterministically, no LLM; RCL cards
tell readers to write in Polish and quote the wykaz number. Open items are listed under "Still
open" in `docs/roadmap.md`.
