# Working on lexinform

Everything a developer needs: setup, the daily commands, how the tests are built, how to try a
change against real data, how to change the schema, and where each kind of change goes.
Product and domain background lives in `README.md`, `docs/roadmap.md` and
`docs/legislative-process.md`; the rules Claude Code follows are in `CLAUDE.md`.

## Setup

```bash
git clone https://github.com/sobolevbel/lexinform && cd lexinform
uv sync                       # Python 3.12+, all dependencies incl. the dev group
uv run pre-commit install     # ruff, mypy and `uv lock --check` on every commit
cp .env.example .env          # keys are only needed for `analyze`, `preview --to` and `run`
```

`.env` is untracked and holds real secrets: never paste its values anywhere. `ANTHROPIC_API_KEY`
is read without a prefix (the SDK wants it that way); everything else is `LEXINFORM_*`.

## The check before every commit

```bash
uv run ruff format src tests && uv run ruff check src tests
uv run mypy                   # strict, over src and tests
uv run pytest                 # unit tests, a few seconds, offline
```

All three must be clean. CI (`.github/workflows/ci.yml`) runs the same on Python 3.12 and 3.13.
Commit after each finished part; do not push unless asked.

Comments carry what the code cannot: a fact from outside the repo (an API quirk, a legal deadline,
a measured number), an invariant a later edit would silently break, or why the obvious way was not
taken. Not a restatement of the next line, not a divider, not a label over a group of fields or
methods. What needs a paragraph becomes a function with a good name and a docstring.

## Running the bot locally

```bash
uv run lexinform show 3039                 # what the API and the local DB know (no keys needed)
uv run lexinform scan --since 2026-08-01   # discovery + both prefilters, prints candidates
uv run lexinform analyze 3039 --force      # one LLM call, result stored locally
uv run lexinform preview 3039              # render the card; --to <chat id> sends it
uv run lexinform run --dry-run             # the full run: messages to stdout, DB rolled back
uv run lexinform run                       # for real, into the DB at LEXINFORM_DB_PATH
```

`run --dry-run` still calls the model. To try the pipeline without paying, add
`--max-analyze 0`; to check every followed bill instead of only the changed ones, `--full-track`.

### Against production data

Production state is a SQL dump in the `state` branch, written by `.github/workflows/daily.yml`
after every run (weekdays 05:23 and 16:23 UTC, weekend 10:23 UTC). To reproduce a run on it:

```bash
git fetch origin state
git show origin/state:lexinform.sql > /tmp/state.sql
export LEXINFORM_DB_PATH=/tmp/state.db
uv run lexinform db init && uv run lexinform db restore /tmp/state.sql
uv run lexinform show 2699                       # sanity check: migrated, data readable
uv run lexinform show RCL/12414100               # an RCL project: stages, letter deadline, texts
uv run lexinform run --dry-run --since 2026-09-08 --max-analyze 0 --full-track
```

An RCL page takes ~10 s to render. A run reads one page per new project (plus its catalogs for a
candidate, one catalog for a title miss) and only the changed catalogs of followed projects, six
at a time: a normal run spends seconds on RCL, a backlog of a few days about two minutes. Pass
`--no-rcl` to leave RCL out of a run.

The dump is upgraded to the current schema on restore, so this is also how a migration is tested
against real rows before it ships.

## How the code is organised

```
models/  → ports.py → adapters/ → services/ → container.py → cli.py
```

- `models/`: pydantic models and pure helpers (`next_phase`, `stage_fingerprint`,
  `latest_text_document`, URL builders). No I/O.
- `ports.py`: the Protocols services depend on (`SejmGateway`, `RclGateway`, `ProjectResolver`,
  `EliGateway`, `LlmAnalyzer`, `Publisher`, `BillRepository`, `Clock`, `TextExtractor`,
  `Downloader`, `TextSource`, `AuthorsResolver`, `RunNotifier`, `CommandInbox`,
  `OperatorReplier`; the relay's `UpdatesSource`, `InboxWriter`, `CommandAcknowledger`).
- `adapters/`: one implementation per external system: `sejm_api` (also the ELI API),
  `rcl_html` (the legislacja.rcl.gov.pl scraper, BeautifulSoup), `pdf_text`, `document_text`
  (Word files, format sniffing), `llm_anthropic` + `llm_prompts`, `telegram` +
  `telegram_format`, `sqlite_repo`, `console` (the dry-run publisher and replier),
  `inbox_files` (the command inbox as a directory), `github_inbox` (the relay's writer).
- `services/`: the phases (`commands`, `discovery`, `rcl_discovery` + `rcl_projects`,
  `text_prefilter`, `analysis`, `publishing`, `pipeline`), `terms` (the current kadencja from
  `/sejm/term`), `lookup` (one bill by number or reference, fetched on first sight; the CLI and
  the commands share it), `listener` (the relay), the seams between sources and the generic
  services (`sources`: `SejmTextSource`, `RclTextSource`, `MetadataOnlySource` behind
  `TextSources`; `documents`: `TextLoader` routing downloads by host; `signatories`), and
  `tracking/` (`service` loop, `pre_print`, `rcl`, `linking`, `acts`, `consultations`,
  `hearings`, `agenda`, `rollover`, `posting`, `stages`). Services import ports, models and the
  pure modules (`keywords`, `sections`, `agenda`, `authors`, `rcl_letters`, `concurrency`),
  never adapters.
- `container.py` wires adapters into services from `Settings`; `cli.py` is typer.

Rules that keep this honest:

- **Adapters hold no business rules.** They translate between an external format and the models.
- **Network in parallel, decisions in order.** `concurrency.fan_out` runs one network step for
  many items; outcomes are consumed in the calling thread, where every DB write happens. Every
  service takes `workers` and must give identical results with 1 and 4.
- **Outages are phase-fatal, everything else is per-bill.** Raise a `ServiceUnavailableError`
  subclass when the whole system is down; anything else costs the bill one attempt.
- **Pending before send.** Every Telegram post has a `publications` row first (see `Poster`).
- **Time comes from the `Clock`.** No `datetime.now()` in services; tests use `FixedClock`.
- **A source stays behind its seam.** The analysis and the text prefilter ask a `TextSource`
  where a bill's text is; the formatter and `next_phase` read `Bill.consultation` and `Bill.rcl`,
  never `submission.*` for consultation dates. Skip logic uses `Bill.has_process` (false for
  `RPW/` and `RCL/` rows); `is_pre_print` means the RPW entry only, `is_rcl` the RCL project.

## Tests

```bash
uv run pytest                          # unit (default: -m 'not integration and not llm')
uv run pytest -m integration           # live Sejm API, network
uv run pytest tests/unit/test_tracking.py -k closure -vv
uv run pytest --cov --cov-report=term-missing
```

Layout:

- `tests/fakes.py`: in-memory fakes for the ports. They record what was asked of them and can
  fail on demand: `gateway.outages.add("get_process")` (API down), `publisher.fail_on={"3039"}`
  (one post rejected), `publisher.outage_on={...}` (Telegram down),
  `FakeTextExtractor(error=...)`, `FakeLlm(script={"3039": RuntimeError()})`.
- `tests/harness.py`: `World`, the production `Container` built over the fakes and an in-memory
  SQLite (a `Settings` names the fake hosts; `llm`, `extractor`, `publisher_override` and
  `notifier_override` are the container's injection points), plus builders
  (`summary`, `detail`, `submission`, `act`, stage tuples). A scenario test arranges the Sejm and
  RCL (`w.add_bill`, `w.add_rcl_project`, `w.set_stages`, `w.touch`, `w.publish_act`,
  `w.command`, `w.gateway.submissions`), acts (`w.run(...)`) and asserts on the `RunReport`, the
  publisher's records and the database (`w.bill`, `w.publication`, `w.card_id`).
- `tests/fixtures/sejm/`: recorded API responses. Refresh with `curl` when the API changes
  (commands below) and keep them small.
- Adapter tests use `httpx2.MockTransport` (`test_sejm_api`, `test_telegram_client`) or a stub
  client (`test_llm_anthropic`); no network in the unit suite.

Conventions:

- Arrange, act, assert, separated by blank lines. One behaviour per test; a sequence of runs is
  fine when the sequence is the behaviour ("posted once, not again"). Take intermediate
  snapshots (`posted = list(w.publisher.updates)`) when a later act would hide an earlier state.
- Assert on observable results (report counters, posts, rows), not on private attributes.
  If a test needs a private, expose the thing properly instead.
- Fully typed (`mypy` runs over `tests/`); no `type: ignore`. Imports at the top of the file.
- Fixture bills: `process_3039` (deputies' bill in committee), `process_1962` (Senate
  amendments, adopted), `process_950` (plenary first reading), `print_3039` and its real PDF.

Refreshing fixtures:

```bash
cd tests/fixtures/sejm
curl -s 'https://api.sejm.gov.pl/sejm/term10/processes?limit=20&documentType=projekt%20ustawy' -o processes_page.json
curl -s 'https://api.sejm.gov.pl/sejm/term10/processes/3039' -o process_3039.json
curl -s 'https://api.sejm.gov.pl/sejm/term10/prints/3039' -o print_3039.json
curl -s 'https://api.sejm.gov.pl/sejm/term10/committees/ASW/sittings' | python3 -c 'import json,sys; d=json.load(sys.stdin); json.dump([s for s in d if s["num"] in (133,135,136)], open("committee_sittings_ASW.json","w"), ensure_ascii=False, indent=1)'
curl -s 'https://api.sejm.gov.pl/sejm/term10/proceedings/65' -o proceeding_65.json
```

RCL pages (`tests/fixtures/rcl/`) are saved HTML with the `<script>` blocks removed; the list is
trimmed to three rows and the pager. The letters are the extracted text of two real pisma:

```bash
cd tests/fixtures/rcl
curl -s 'https://legislacja.rcl.gov.pl/projekt/12414100' | sed -E 's#<script.*</script>##g' > projekt_12414100.html
curl -s 'https://legislacja.rcl.gov.pl/projekt/12414100/katalog/13223895' > katalog_13223895.html
curl -s 'https://legislacja.rcl.gov.pl/projekt/12414050' > projekt_12414050.html   # stage 14, RM number
```

## Database schema and migrations

The tables, their columns, every index and the whole migration ledger, drawn and explained on one
page: `docs/database.html` (Russian, open it in a browser). The source of truth stays the code.

The schema version is SQLite's `PRAGMA user_version`; the source of truth is the `MIGRATIONS`
tuple in `adapters/sqlite_repo.py` (script `i` brings the database to version `i + 1`,
`SCHEMA_VERSION = len(MIGRATIONS)`). `migrate()` runs every later script in its own transaction.
`dump()` appends `PRAGMA user_version = N;`; `restore()` replays a dump, sets the version it
carries (1 for a legacy dump without the line) and migrates. So the daily workflow
(`db init` → `db restore` → `run` → `db dump`) upgrades old state automatically.

To add a migration:

1. Append one SQL string to `MIGRATIONS`. Never edit or reorder earlier entries. Only additive
   SQL: `ALTER TABLE … ADD COLUMN` (nullable or with a default), new tables and indexes, `UPDATE`
   backfills. SQLite cannot alter a column's type: create a new table, `INSERT … SELECT`, drop,
   rename.
2. Extend the model and `_row_to_bill` / `_row_to_publication`. JSON columns (`summary_json`,
   `analysis_json`, `stages_json`, `submission_json`, `act_json`, `agenda_json`) are pydantic
   dumps: new fields need defaults, removed fields are simply ignored on load, a renamed field
   needs a data migration (`json_set` or a one-off Python step).
3. Extend `test_restore_of_a_v1_dump_applies_every_later_migration` in `test_sqlite_repo.py`
   and run the real dump through the new code (see "Against production data").

There is no downgrade: to roll back, revert the code and restore the previous dump from the
history of the `state` branch.

## Where a change goes

| I want to… | Touch |
|---|---|
| Add or tune a keyword | `keywords.py` (`KEYWORD_PATTERNS`, `WEAK_PATTERNS`), then a title in `POSITIVE`/`NEGATIVE` in `test_keywords.py`. Patterns are Polish stems with `\b`; err on inclusion, the model decides. |
| Change the prompt or the rubric | `adapters/llm_prompts.py`, bump `PROMPT_VERSION`. Calibrate: `analyze 3039 --force` (expect 4–5), `950` (5), `2699` (2–3), `1962` (not relevant). A new version does not re-analyse published bills. |
| Change a message | `adapters/telegram_format.py` for structure, `i18n.py` for words (RU and EN, `test_i18n` checks parity). Keep HTML to `<b> <i> <a> <code> <pre>` and escape everything from outside. |
| Add a language | A `Labels` instance in `i18n.py`, registered in `LABELS`; the language name in `_LANGUAGE_NAMES` of `llm_prompts.py`. |
| Add a kind of reply | `PublicationKind` + a unique index in a new migration; `Publisher` port + Telegram, console and fake publishers; a `Poster` method; a formatter method; a watcher in `services/tracking/` wired in `service.py`; counters in `TrackingResult`, `RunReport`, `pipeline._track` and the run report line. |
| React to a new Sejm stage type | `stage_type_labels` in `i18n.py` for the label; `models/bill.py::next_phase` if it changes what comes next; keep `_stage_key` in `models/sejm.py` unchanged unless every followed bill should post an update. |
| Add a setting | `settings.py` (with the `LEXINFORM_` prefix), `container.py`, the table in `README.md`, `.env.example` if users should see it. |
| Change what happens at the end of a Sejm term | `services/tracking/rollover.py` (what lapses, what moves), `sqlite_repo.py::list_unfinished_published` / `discontinue_unfinished` / `move_government_rows`, the wording in `i18n.py` (`process_discontinued`, `process_carried_over`), `test_term_rollover.py`. The term itself comes from `services/terms.py`. |
| Add an API endpoint | `SejmGateway` port, `adapters/sejm_api.py` (+ parser), `tests/fakes.py`, a fixture and a test in `test_sejm_api.py`. |
| Read something new from an RCL page | `adapters/rcl_html.py` (a parser per page; CSS selectors, no regexes on markup; raise `RclPageError` when a structural element is missing, tolerate missing details), the model in `models/rcl.py`, a saved page in `tests/fixtures/rcl/`, a test in `test_rcl_html.py`. |
| Change what an RCL event posts | `services/tracking/rcl.py` (detection), `models/rcl.py::rcl_fingerprint` (what counts as a change), `telegram_format.py` + `i18n.py` (words), `test_tracking_rcl.py` and `test_telegram_format_rcl.py`. |
| Add an operator command | `models/commands.py` (`CommandName`, `parse_command`, an `OutcomeStatus` if the answer is a new kind), `services/commands.py` (`_execute` and a method), the reply in `telegram_format.py::command_reply` and `COMMAND_HELP`, a scenario in `test_commands.py`, the list in `docs/operator-commands.md`. A command reads the database through `BillLookup.find_ref` and the sources through `load_ref`; posts go through `PublishingService.publish_bill` (pending row first). |
| Add a text format | `adapters/document_text.py` (`DocumentTextExtractor` picks by magic bytes; PDF, .docx/.docm, .odt, zip packages and legacy .doc via `doc_text.py` exist), a test in `test_document_text.py`; `models/rcl.py::READABLE_EXTENSIONS` and `_format_rank` if RCL publishes it. |

## Deploy and operations

- `.github/workflows/daily.yml` runs the bot twice a day on weekdays, once on weekend days, and pushes the dump to `state`.
  Secrets: `ANTHROPIC_API_KEY`, `LEXINFORM_TELEGRAM_BOT_TOKEN`, `LEXINFORM_TELEGRAM_CHANNEL_ID`,
  optionally `LEXINFORM_TELEGRAM_LOG_CHANNEL_ID` and `LEXINFORM_RCL_PROXY_URL` — without the
  proxy the RCL phase fails on every scheduled run, because RCL drops connections from
  GitHub-hosted runners (`docs/rcl-proxy.md`). Do not protect the `state` branch.
- `.github/workflows/integration.yml` runs the live API checks weekly and pings the log channel
  when the API changed under us.
- Operator commands: `lexinform republish NUMBER` (post a card again), `lexinform reset NUMBER
  --to analysis_pending` (re-analyse), `--to skipped_prefilter` (silence a false positive); the
  same from the technical channel (`/analyze`, `/show`, `/skip`, `/republish`) through the relay
  on the VPS and the `inbox` branch: `docs/operator-commands.md` (setup, the systemd unit in
  `deploy/`, the rules: one `getUpdates` consumer per bot, test with `--dry-run`).
- Deploying: the bot needs none (`daily.yml` checks out `main` on every run). The relay on the
  VPS is updated by `.github/workflows/deploy-relay.yml` after every green CI on `main`
  (`deploy/update.sh` over SSH with a key bound to that script; secret `MIKRUS_SSH_KEY`).
- The run report in the log channel lists counters, token cost, analysed-but-not-published
  bills and captured warnings; a red run means a phase failed, the state is saved regardless.

## Pull requests

One topic per PR, with tests; run `uv run pre-commit run --all-files` before pushing; describe
the user-visible effect (a changed message, a new keyword, a new setting).
