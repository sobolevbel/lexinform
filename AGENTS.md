# lexinform — instructions for coding agents

Daily bot: finds Polish bills that affect foreigners (Sejm API; legislacja.rcl.gov.pl for
government projects still with the ministries; the wykaz prac legislacyjnych RM on gov.pl for those
only announced), scores them 1–5 with an LLM, posts Russian cards to a Telegram channel and follows
each bill until the act is in force. The point is *timely action* — consultations, committee
referrals, hearings, deadlines — not a chronicle.

## Where things are written

- **This file is the one instruction source for every agent.** `CLAUDE.md` only imports it
  (`@AGENTS.md`) and adds Claude Code specifics, so Claude and Codex read the same rules. Keep it
  short: a rule that matters in every session goes here; a rule about one area goes into that area's
  file below; history, measurements and how something was found go into commit messages, `docs/` or
  `docs/BUGS.md`. One fact lives in one place; elsewhere, point to it.
- **Detailed invariants live in `.claude/rules/`, one file per area.** Claude Code loads a file
  automatically when it reads or edits a matching path; any other agent reads the file for the
  area before changing code there:

  | Rules file | Area |
  | --- | --- |
  | `road.md` | stages, phases, "what comes next", term, card refresh, first-sight baseline, re-analysis |
  | `messages.md` | sittings, replies, held changes, joint prints, filed documents, delivery |
  | `documents.md` | scans, cover letters, which file is the bill, text digests, cost guard |
  | `sources.md` | RPW/RCL/wykaz rows, linking, RCL watcher, consultation letters, unthreaded bills |
  | `operations.md` | runs, operator commands, digest, batches, document reviews |
  | `sejm-api.md` | verified facts about the Sejm API, orka, ELI, the Senate site, signatories |
  | `database.md` | migrations and how state travels |

- Docs read on demand: `docs/legislative-process.md` (road RCL → Sejm → Senate → President → Dz.U.,
  deadlines, public windows, API stage vocabulary), `docs/text-selection.md` (what of a document
  reaches the model), `docs/rcl-scraping.md` (RCL markup and files, wykaz CSV, probes),
  `docs/llm-cost.md`, `docs/reference/database.md` (schema, migration history),
  `docs/operator-commands.md`, `docs/roadmap.md` (open work), `docs/BUGS.md` (defects; read
  "Classes we have already had" before hunting a new one), `CONTRIBUTING.md` (setup, tests, where a
  change goes), `README.md` (product). Documentation work: `docs/index.md`, `docs/authoring.md`.

## Working rules

- Python 3.14, `uv`. Before a commit: `uv run pytest -q && uv run mypy && uv run ruff check src
  tests tools`, then `uv run ruff format src tests tools`. mypy is strict over `src` and `tests`: no `type:
  ignore`, no local imports, tests fully typed. An `assert` that narrows a type states an invariant
  and who guarantees it (`assert x is not None, "…"`); a value that can really be None is handled.
  Tests marked `local_http` are outside the default gate; `make test-local-http` only when a change
  needs CLI coverage through the local HTTP server.
- **IMPORTANT — COMMENTS AND DOCSTRINGS ARE ONE LINE. ALWAYS. IF YOU ARE WRITING A THIRD LINE, STOP
  AND CUT IT.** Say the measured number, the API quirk, the deadline — NEVER how it was discovered,
  what went wrong before, or which bill it was found on (that goes in the commit message). The long
  docstrings still in the repo are NOT the model to copy. A comment exists only where code cannot
  speak: an outside fact, an invariant a later edit would silently break, or why the obvious way was
  not taken. Never a restatement of the next line, a divider, a label over a group, or a docstring
  written as a comment at the top of a body.
- **Every measured claim can be re-run** against the corpus: an 11 GB checkout beside this one as
  `lexinform-corpus`, its own git repo for scripts, checks and index (downloaded data untracked);
  measurement paths in the rules are relative to it. It holds all 980 prints of term 10 with text,
  825 RCL projects with every reached stage and 9,221 files, the whole wykaz, 827 orka submissions,
  and the stage trees of all 5,533 processes of terms 8–10 (term 10 re-read 3 Oct 2026 under
  `sejm/snapshots/`). Its `README.md` says how fresh each
  source is; start at `INDEX.json` (druk, RCL id, `wpl:UC104`, RPW → files, labels, each other;
  `cases` are ready-made sets such as `print.scan`, `document.unknown`). Older numbers rest on a 45-print sample.
- **Validate every extraction, trimming, normalisation, fingerprint or text-comparison change on the
  real corpus before committing**: replay prior matches, inspect changed decisions, inject meaningful
  changes (dates, amounts, negations, scope, references). Similarity alone never proves
  immateriality. Record coverage and exclusions; fixtures alone do not replace this.
- Commit after each finished part; never push unless asked; no `Co-Authored-By`. A push of `main`
  deploys everything: the bot (every `daily.yml` run checks out `main`) and, after green CI, the
  relay on the VPS (`deploy-relay.yml` → `deploy/update.sh` over SSH).
- Code, comments and **commit messages are English**. Reader messages are Russian (labels in
  `i18n.py`, RU + EN); Polish law titles and abbreviations stay Polish.
- `.env` holds real secrets, untracked: never read or print values; keep `.env.example` in step.
  `tests/conftest.py` blanks every credential for every test (autouse) because `Settings()` reads
  `.env`; a CLI test's `env` sets the token and log channel to "" as well.
- Prod state = SQLite dump in the `state` branch, written by `.github/workflows/daily.yml` (cron
  weekdays 01:23 and 13:23 UTC, weekend 06:23 UTC; GitHub starts schedules ~4–6 h late, so runs
  land ~08:00–10:00 and ~18:00–21:00 Warsaw, midday on weekends). Real data: `git show
  origin/state:lexinform.sql > /tmp/s.sql`, `LEXINFORM_DB_PATH=/tmp/t.db uv run lexinform db init
  && … db restore /tmp/s.sql`, then `lexinform run --dry-run --since YYYY-MM-DD` (real triage
  calls; every other model call prepared as a batch request and never submitted; DB rolled back).

## Architecture

`models/` (pydantic + pure helpers; `enums`, `sejm`, `rcl`, `wykaz`, `analysis`, `bill` (the
aggregate, `ConsultationWindow`, `process_stages`/`veto_stood`), `phases` (`Phase`, `next_phase`,
`is_over`, deadlines, patience), `events`, `evidence`, `observations`, `planning`, `report`; all
re-exported from `lexinform.models`) → `ports.py` (Protocols) → `adapters/` (Sejm API, ELI, orka,
RCL scraper `rcl_html`, register CSV `wykaz_csv`, Senate site `senat_html`, PDF/Word text
`document_text`, LLM providers, `publisher_base` (the `Publisher` port rendered once; Telegram and
console only deliver), SQLite, `inbox_files`, `github_inbox`) → `services/` (commands, digest,
lookup, listener (the relay), discovery, rcl_discovery + rcl_projects, wykaz_discovery, sources
(`TextSources` → `SejmTextSource`, `RclTextSource`, `SubmissionTextSource`, `MetadataOnlySource`),
documents (`TextLoader`), text_prefilter, analysis, signatories, publishing, posting, `tracking/`
(service, pre_print, rcl, wykaz, linking, acts, senate, consultations, agenda, cards, deadlines,
hearings, rollover, stages), pipeline) → `container.py` (manual wiring, typed on ports, each
service built once) → `cli.py` (typer).

- Services import only ports, models and pure modules (`keywords`, `sections`, `law_digest`,
  `agenda`, `authors`, `rcl_letters`, `concurrency`, …), never adapters. Generic services
  (analysis, prefilter, formatter, `next_phase`) never branch on the source: they read
  `Bill.has_process`, `Bill.consultation`, `Bill.rcl`, `Bill.wykaz` and the `TextSource` port.
- Network work runs in parallel only through `concurrency.fan_out`; outcomes are consumed and every
  repository write happens on the calling thread in input order. `workers=4` must equal `workers=1`.
- Services take time from the `Clock`, never `datetime.now()`.
- Tests: `tests/unit/` one module; `tests/scenario/` the pipeline over fakes (anything calling
  `World()`); `tests/integration/` live systems. Fakes are in `tests/fakes.py`; `tests/harness.py::World` builds the real
  `Container` from `Settings` naming fake hosts (injection points `llm`, `extractor`,
  `publisher_override`, `notifier_override`); arrange with `add_bill`/`add_rcl_project`/
  `set_stages`/`touch`, act with `run`, assert on the report, publisher records and
  `bill`/`publication`. HTTP adapters use `httpx2.MockTransport`. The fake Sejm gateway answers per
  term like the API. `FakePublisher` **is** `RenderingPublisher` with delivery overridden, so
  `publisher.texts(kind)` is what the channel would show. Arrange-act-assert; never touch private
  attributes.

## Invariants (index — details in the rules file named)

- Term from `/sejm/term`; the old term is drained, not dropped (`road.md`).
- "What comes next" is derived from stages, agenda and statutory deadlines, never stored; no passed
  date is printed (`road.md`).
- First sight seeds a baseline and never replays history; a decision needs positive evidence;
  missing decisions and unknown next steps never prove completion (`road.md`).
- Stage fingerprint excludes rendering-only `Stage` fields (`road.md`).
- A re-analysis needs a new normalised text, not a new URL, and is news only when the model names a
  change (`road.md`).
- Pending-before-send; stale `pending` becomes `unknown`, never auto-resent; `unknown` is resolved
  only by an audited operator decision (`messages.md`).
- Recording and telling a change are one pair; frame-only and routine steps are held or go to the
  digest (`messages.md`).
- Jointly considered prints share one card; later prints are `joint_bill` replies (`messages.md`).
- A missing or refused file is never a verdict; outages never burn per-bill attempts (`documents.md`,
  `operations.md`).
- `/bills` rows are refreshed by the pre-print tracker only; RCL rows by the RCL watcher only
  (`sources.md`).
- Every model call except triage and change review is a batch, written down before it is sent
  (`operations.md`).
- Operator commands are recorded before they run and never re-run (`operations.md`).
- Editorial versions survive operational payload cleanup; corrections use original context (`operations.md`).
- Migrations are append-only (`database.md`).
- Projection receipts commit with their target state (`operations.md`).

## Product decisions

- Analysis, reviews, amendments, supplement, joint and names on `gpt-6.1-sol` (`medium`, its own
  calibrated prompt), batch provider `openai`, one model up to its 922k-token input (the overflow
  setting names the same model). Its prompt cache is explicit, one breakpoint on the system message:
  left implicit, every one-off document is written to the cache at 1.25x input. Triage of texts ≥ 20k
  chars on `claude-sonnet-5` (synchronous, ~1 cent a bill). Scans counted at the analysis model's
  tokens a page (Sol ~3,110). Urgency and waiting never buy a synchronous call.
- `min_score` 3. Text prefilter: 2 distinct patterns or 3 hits; weak patterns (Straż Graniczna,
  "legalizacja", "nierezydent") never decide alone; everyone's registers and benefits (PESEL,
  mObywatel, NFZ, 800+, prawo jazdy, Kodeks wyborczy) are deliberately not patterns. The keyword
  stage is over-inclusive on purpose: a false hit costs a call, a miss loses a bill.
- Cost guards: `LEXINFORM_MAX_ANALYSIS_COST_USD` ($2 per first analysis; a longer text is cut down,
  not refused; a re-analysis is never refused) and `LEXINFORM_MAX_RUN_COST_USD` ($15 a run); a term
  costs ≈$44 because the triage rejects 62%. Skipped-cost rows revive with `lexinform reset --to
  analysis_pending`. Details and rejected ideas: `docs/llm-cost.md`.
- No "probability of passing" estimate. A new `PROMPT_VERSION` never re-analyses or re-posts
  existing cards; only a materially new text does. The analysis never states where the bill stands
  — the card does. Abbreviations (MSWiA, UdSC, ZUS, PESEL) stay Polish, never МВД.
- Every card and update carries "what comes next" and "what you can do now", and every call to
  action names an address. RCL cards ask readers to write in Polish and cite the wykaz number.
  Committees are named, not coded; «Важность» shows the score without the scale legend; the Dz.U.
  notice is a separate reply with one summary sentence; a sitting or hearing is told once per joint
  group.
- Every relevant RCL project is followed, not only open consultations; deadline and e-mail are
  parsed from the letter deterministically. Wykaz plans get a «План правительства» card saying there
  is no text yet; the RCL project and druk inherit it (one thread from plan to Dz.U.).
- A bill found already over gets no card and no analysis; a bill merely passed by the Sejm keeps its
  card. Of filed documents only the government's position on a bill it did not write and the OSR are
  told; opinions are not.
- Operator commands come from any admin of the technical channel through a relay on the owner's
  mikrus VPS (384 MB: a `getUpdates` loop, not the bot) into the `inbox` branch and are executed by
  GitHub Actions, so the state branch stays the only DB writer; every CLI subcommand is a command.
- Weekly digest: one post (Sunday, Warsaw), drafted with a publish button, never sent unread, costs
  nothing; a quiet week is still told.
