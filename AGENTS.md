# lexinform — instructions for Codex

`lexinform` monitors Polish legislation that materially affects foreigners. It discovers Sejm bills and RCL government projects, scores them with an LLM, publishes Russian Telegram cards, and follows relevant matters through entry into force. Prioritise timely, actionable events (consultations, hearings, referrals and deadlines), not a legislative chronicle.

Read `README.md` for product context, `CONTRIBUTING.md` before changing code, and:

- `docs/roadmap.md` for verified API facts and open work;
- `docs/legislative-process.md` for stages, legal deadlines and public participation windows;
- `docs/operator-commands.md` for the technical channel's commands and the relay that carries them;
- `docs/database.html` for the schema, its indexes and the migration ledger on one page.

## Working rules

- Python 3.12; use `uv`.
- Before committing, run `uv run pytest -q && uv run mypy && uv run ruff check src tests`, then `uv run ruff format src tests`. Mypy is strict: no `type: ignore`, no local imports, and fully typed tests.
- Commit each completed, coherent part. Do not push unless asked; never add `Co-Authored-By` trailers.
- `.env` contains real secrets: do not read or print it. Keep `.env.example` aligned when adding settings.
- Reader-facing wording is Russian (labels live in `i18n.py`); retain Polish legal titles and abbreviations unchanged.
- Production state is the SQLite dump on the `state` branch, maintained by `.github/workflows/daily.yml`. Real-data dry runs must use a copy of that dump and `lexinform run --dry-run`; they still make real LLM calls.

## Architecture and tests

Dependencies flow only in this direction:

`models/` → `ports.py` → `adapters/` / pure helpers → `services/` → `container.py` → `cli.py`.

- `models` are Pydantic models and pure business helpers; re-export public types from `lexinform.models`.
- Services depend on ports, models and pure modules, never concrete adapters. Generic services must not branch by source; use `Bill.has_process`, `Bill.consultation`, `Bill.rcl` and the `TextSource` port.
- `container.py` is the real manual wiring. Keep it type-safe and build each service once.
- Test through `tests/harness.py::World` and fakes in `tests/fakes.py`: arrange with public helpers, run the pipeline, assert reports/publications/models. Do not access private state. HTTP adapters use `httpx2.MockTransport`.
- Parallelise network-only work with `concurrency.fan_out`; make repository writes in input order on the calling thread. `workers=4` must behave identically to `workers=1`.

## Invariants to preserve

- Resolve the current Sejm term from `/sejm/term`. Discover only in that term, but keep tracking previous-term bills until resolved. Rollover marks unfinished Sejm work as lapsed; passed acts remain followed; RCL projects awaiting a print move to the new term.
- Publishing is pending-before-send. Create a unique pending publication before delivery; retry `failed`, never automatically resend stale `pending` (they become `unknown`).
- Prints considered jointly receive one main card; later related prints are `joint_bill` replies. Prefer the government print as the initial card.
- Discovery must not overwrite existing `/bills` rows. Only the pre-print tracker refreshes them, so changes such as a print assignment or consultation result remain detectable.
- Derive “what comes next” from stages, agenda and statutory deadlines; never persist it as independent state.
- Not every stage merits a message. Frame-only transitions are held and included with the next substantive event. Keep rendering-only `Stage` fields out of the stage fingerprint.
- Reanalyse only after normalized document text changes, not merely after a URL or attachment date changes. After a third reading, avoid a download only when the known conditions prove the adopted text is unchanged.
- RPW and RCL entries have no Sejm process. When linked to a print, preserve their thread/card; a skipped RPW entry's print must still enter text prefilter.
- RCL discovery reads a project once; the RCL watcher owns later full refreshes. Folder uploads alone are not news. Parse RCL HTML structurally with CSS selectors; treat its HTTP-200 “Request Rejected” page as an outage.
- Service outages abort the phase and do not consume per-bill attempts. Other bill-level failures retry up to the configured limit.

## Database state and migrations

- SQLite schema version is `PRAGMA user_version`; `MIGRATIONS` in `adapters/sqlite_repo.py` is append-only. Never edit or reorder deployed migrations.
- Add a migration by appending plain SQL, update models and row mappings with backward-compatible defaults, and extend the legacy-dump migration test in `tests/unit/test_sqlite_repo.py`.
- `dump()` records the schema version; `restore()` replays then migrates old dumps. There is no downgrade: roll back code and restore a previous `state`-branch dump.

## Important integration facts

- `/processes` covers only printed bills; pre-print consultation entries live in `/bills`. API timestamps are naive `Europe/Warsaw` times, not `Z` timestamps. Paginate until empty; do not trust ignored API filters.
- A bill being passed is not the same as in force. Use ELI/Dz.U. data for promulgation and entry into force.
- Never probe attachments with `HEAD`; stream a bounded `GET`. `…-A` committee prints containing amendment proposals are not bill text.
- RCL has no API. Its project pages are slow, and GitHub-hosted runners may be network-blocked; the initial list probe must stay short and single-attempt.
- Large text goes through deterministic trimming and triage before full analysis. Cost limits are safeguards: skipped-cost rows are revivable with `lexinform reset --to analysis_pending`.

## Product defaults

- Default model: `claude-opus-5`; minimum publication score: 3. Triage texts at least 20k chars with `claude-sonnet-5`.
- Do not add a likelihood-of-passage estimate.
- Prompt-version changes alone must not reanalyse or repost an existing card. A materially new text may.
- Every card/update includes what happens next and what readers can do now. RCL cards ask readers to write in Polish and cite the wykaz number.
