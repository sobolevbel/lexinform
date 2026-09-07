# lexinform — notes for Claude Code

Daily bot: finds Polish Sejm bills that affect foreigners, scores them 1–5 with an LLM, posts
Russian cards to a Telegram channel and follows each bill until the act is in force. The point is
not a chronicle but *timely action*: consultations, committee referrals, hearings, deadlines.
Everything else in `README.md`; roadmap and verified API facts in `docs/roadmap.md`.

## Working rules

- Python 3.12, `uv`. Check with `uv run pytest -q && uv run mypy src && uv run ruff check src tests`
  and `uv run ruff format src tests`. All three must be clean before a commit.
- Commit after each finished part. Do not push unless asked. No `Co-Authored-By` trailers.
- `.env` holds real secrets and is untracked; never print values. `.env.example` mirrors keys.
- Messages to readers are Russian (labels in `i18n.py`, RU + EN); Polish law titles stay Polish.
- Prod state = SQLite dump in the `state` branch, written by `.github/workflows/daily.yml`
  (05:00 UTC). To test against real data: `git show origin/state:lexinform.sql > /tmp/s.sql`,
  `LEXINFORM_DB_PATH=/tmp/t.db uv run lexinform db init && … db restore /tmp/s.sql`, then
  `lexinform run --dry-run --since YYYY-MM-DD` (real LLM calls, DB rolled back, prints to stdout).

## Architecture in one breath

`models.py` (pydantic, pure helpers) → `ports.py` (Protocols) → `adapters/` (Sejm API, ELI, PDF,
Anthropic, Telegram, SQLite) → `services/` (discovery, text_prefilter, analysis, publishing,
tracking, pipeline) → `container.py` (manual wiring) → `cli.py` (typer). Services import only
ports/models (plus `TextBudget`, `PdfTextLoader`). Tests use fakes in `tests/fakes.py` and the
`World` harness in `tests/unit/test_pipeline.py`; HTTP adapters use `httpx2.MockTransport`.

Invariants worth keeping:

- **Pending-before-send.** Every Telegram post gets a `publications` row (`pending`) first, unique
  per kind/bill/channel; failed posts are retried up to `max_publish_attempts`; `pending` left by a
  crash becomes `unknown` and is never auto-resent.
- **Stage fingerprint** (`_stage_key`) drives updates. Fields added to `Stage` for rendering
  (`voting`, `position`, `committee_name`, `proposal`) must stay *out* of the key, or every tracked
  bill posts a spurious update after deploy.
- **Migrations are append-only.** `dump()` writes `PRAGMA user_version`; `restore()` migrates old
  dumps. Test every migration against a v1 dump (see `test_sqlite_repo.py`).
- **Outages never burn per-bill attempts.** `ServiceUnavailableError` subclasses abort a phase
  and go to the log channel; everything else is a per-bill failure (3 attempts). Unknown model id
  and bad request parameters are fatal, "prompt too long" is per-bill.
- Pre-print bills (`RPW/…`) have no process: skip `get_process`/`get_print` for them; when the
  print appears, the print inherits the card (`new_bill` row aliased with the same `message_id`).

## Sejm API lessons (verified live, Sept 2026)

- `/processes` only lists bills that already have a print number. Bills at the consultation
  stage live in `/bills` (`RPW/…`), with `publicConsultationStart/EndDate`, `applicantType`,
  `status`, `print`. Their PDF on orka.sejm.gov.pl is behind Incapsula: not downloadable.
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

## Product decisions already taken

Default model `claude-opus-5`, full text sent, `min_score` 3, text prefilter threshold 2 distinct
patterns or 3 hits, club breakdown on, Dz.U. notice as a separate reply, in-force reminder repeats
the summary. The owner does **not** want a "probability of passing" estimate. Open items are
listed under "Still open" in `docs/roadmap.md`.
