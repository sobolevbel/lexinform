# lexinform

[![CI](https://github.com/sobolevbel/lexinform/actions/workflows/ci.yml/badge.svg)](https://github.com/sobolevbel/lexinform/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A daily bot that watches bills in the Polish Sejm, picks out the ones that matter to foreigners
living in Poland, scores and summarises them with an LLM, and posts the result to a Telegram channel.
It then follows each bill through the whole legislative process, from public consultation to
publication in Dziennik Ustaw, so readers learn about changes while they can still act on them.

- Sources: the official Sejm REST API (`api.sejm.gov.pl`) and its ELI API, legislacja.rcl.gov.pl
  (government projects before the Sejm), which has no API and is parsed from HTML, and the wykaz
  prac legislacyjnych RM on gov.pl (bills the government has only announced), which comes as one
  CSV.
- Importance 1–5, where **5 = legalization of stay** (ustawa o cudzoziemcach, residence permits,
  visas, citizenship, international protection).
- Posts in Russian (Polish statute names kept in the original); English labels built in.
- Runs twice a day on weekdays and once at midday on the weekend in GitHub Actions; state is a
  SQLite dump in the `state` branch. The bot itself needs no server; only the optional relay that
  carries operator commands does.
- Python 3.12+, `uv`, `pydantic`, `anthropic` SDK, `httpx2`, `beautifulsoup4`, `pypdf`, `typer`,
  SQLite.

## What readers get

A card per relevant bill, and replies under that card as the bill moves. Bills the Sejm
considers jointly (a deputies' and a government print on the same subject, one committee report
for both) share one card: the later print is announced as a short "alternative bill" reply under
it and the group is followed there.

```
📜 Новый законопроект — druk nr 3039
Poselski projekt ustawy o zmianie ustawy o udzielaniu cudzoziemcom ochrony na terytorium RP

🔴 Важность: ●●●●● 5/5 — изменения в легализации пребывания
🏷 Категория: легализация пребывания

📝 О чём проект
…

🔑 Ключевые изменения
• …

💡 Что это значит на практике: …
👥 Кого касается: …
📅 Вступление в силу: …
🗣 Общественные консультации: 05.08.2026 — 04.09.2026 · форма для мнений на сайте Сейма

🗺 Путь: Сейм ✓ → комиссии ● → II и III чтение → Сенат → Президент → Dz.U. → в силе
⏭ Что дальше: I чтение в комиссии — Komisja Administracji i Spraw Wewnętrznych (ASW) · 17.09.2026, 09:00
👉 Что можно сделать сейчас: направить мнение через страницу проекта на сайте Сейма до 04.09.2026; направить мнение в комиссию — Komisja … (ASW) до заседания 17.09.2026

🏛 Стадия: направлен в комиссию Komisja Administracji i Spraw Wewnętrznych (ASW) (03.09.2026)
✍️ Инициатор: депутатский (подписали: Lewica 21 · представитель: Daria Gosek-Popiołek, Lewica)
📄 Дата druku: 03.08.2026

🔗 Ход процесса в Сейме | PDF
#kadencja10druk3039 #важность5 #легализация #консультации #каденция10
```

Every card and update carries three lines that answer the reader's real questions: **the path**
(RCL → Sejm → committees → readings → Senate → President → Dz.U. → in force, with the current
step marked), **what comes next** (the next step of the process, dated when a committee or Sejm
sitting with the bill on its agenda is already scheduled, otherwise with the usual duration) and
**what you can do now** (send an opinion through the Sejm's consultation form until the deadline,
write to the committee before its sitting, apply for a public hearing, send an opinion to the
Senate committee; when there is nothing to do, the line says so and names the next window).

Replies cover: a reminder three days before a public consultation closes, the notice that the
opinions received were published, a committee sitting or a Sejm sitting whose agenda names the
bill (date, time, room, agenda item, live stream), a reminder before applications to a public
hearing close, new stages (committee referral with the committee's name, the committee's report
with its proposal, votes with the per-club breakdown, Senate position, President's signature or
veto), a fresh analysis with "what changed" when the bill's text changes, a summary of what the
Senate's or the second reading's amendments change (read from the Senate's resolution print or
the committee's report on them), "published in Dziennik Ustaw" with the entry-into-force date,
and a reminder on the day the act enters into force.

Every update is named after its event ("Сейм принял закон", "Направлен в комиссии", "Сенат внёс
поправки"), lists the new stages in the reader's language and repeats one sentence of the
summary (the whole text only when the analysis changed). Stages that only frame an event
("Skierowano do I czytania", "Praca w komisjach", the first reading itself, the final
"Uchwalono") get no post of their own: they are held and listed with the next substantive
update. The hand-over to the President is not one of them — it starts the 21 days of art. 122,
which is the reader's last window, and the Senate's 30 days and the President's 21 each get a
reminder of their own as they run out. A closure that arrives together with the act in Dziennik Ustaw is told
by the publication notice alone.

Bills that have no print (druk) number yet (`RPW/…`, the consultation stage) are covered too, from
their official description; when the print number is assigned the thread continues under the same
card. Government bills are caught earlier still, on legislacja.rcl.gov.pl (Rządowy Proces
Legislacyjny), where the ministry consults them months before the Sejm: the card names the
deadline and the e-mail from the consultation letter, the RCL comment form, and follows the project
through the committees of the Council of Ministers until the druk appears and takes over the
thread. Once linked, the replies carry both tags (`#RCL_UC104 #kadencja10druk3055`, or the
`#RPW_…` one) and the card is edited in place to carry the druk's tag, so a search for either
finds the whole thread. Earliest of all is the government's own register of planned legislation
(wykaz prac legislacyjnych RM): an entry there is an intention, months before any text — UD408
(o zmianie ustawy o cudzoziemcach) was entered on 2026-05-12 and reached RCL 55 days later. Its
card says plainly that there is no draft yet and names the one thing the law allows at that
stage: anyone, a private individual included, may file a zgłoszenie zainteresowania pracami nad
projektem with the ministry (art. 7 of the lobbying act), which is also the ticket to the Sejm's
public hearing. When the project appears on RCL it takes over that thread, and when the
government drops a project — which only this register records — the thread is told so. A second, technical channel can receive a report after every run (counters, tokens,
errors).

## How it works

```
/processes + /bills + RCL + wykaz ─► keyword prefilter (title, then text) ─► LLM ─► SQLite
Telegram ◄── cards (once per bill) ◄── publish ◄──┘        └─► track: stage diff, votes, ELI act
```

1. **Discover** bills modified since the last run (`/processes`, with one day of overlap), bills
   submitted without a print number (`/bills`), government projects modified on RCL (the HTML
   list sorted by modification date; one project page per new project, its stage catalogs only
   for candidates) and entries published in the wykaz prac RM (one CSV with the whole register;
   entries older than the watermark are counted in the report and left alone). A bill met for
   the first time when its road is already over — the act is in Dziennik Ustaw, the bill was
   rejected or withdrawn, the project or the plan was dropped — is recorded and left there: a
   card invites action, and there is none. The Sejm's `closureDate` is not that point (it is
   set at the third reading, with the Senate and the President still ahead), so the stage tree
   decides.
2. **Prefilter** by Polish word stems on title and description (for RCL: title, hasła and
   działy); misses get their text scanned with the same patterns (accepted on two distinct topics
   or three hits).
3. **Analyse** the bill with Claude through a structured-output schema (relevance, score,
   category, summary, key changes, affected groups, practical impact, effective date). The print
   is trimmed first: the bill, its justification and the core of the regulatory impact assessment
   go in; consultation reports, EU compliance tables and draft regulations (55–80% of a government
   print) do not. Long prints first pass a cheap triage on excerpts around the keyword hits; a
   confident "not about foreigners" ends there. Signatories of deputies' bills are parsed from
   the print's cover letter and matched against `/MP`.
4. **Publish** relevant bills with `score >= LEXINFORM_MIN_SCORE`. A publication row is written
   before sending, so a crash can never duplicate a post; failed posts are retried on later runs.
5. **Track** published bills: only those the API lists as modified since the watermark (plus a
   full pass every Monday); the stage tree is fingerprinted, every change produces exactly one
   reply; a newer text triggers a re-analysis with the previous one as context; an ELI address
   triggers the Dziennik Ustaw notice and, later, the entry-into-force reminder. Every followed
   bill is also matched against the agendas of its committees' sittings and of the current Sejm
   sitting (`/committees/{code}/sittings`, `/proceedings/{n}`): a new (bill, sitting) pair is one
   reply, and the dates feed the "what comes next" line. Followed RCL projects are re-read when
   the list says they changed: a reached stage, a consultation that opened, published opinions, a
   new text version (re-analysed) and the hand-over to the Sejm each make one reply; the druk
   whose `rclNum` names a followed project inherits its card.
6. **Report** to the technical channel when `LEXINFORM_TELEGRAM_LOG_CHANNEL_ID` is set.
7. **Obey** the operator: commands posted in that channel (`/analyze 3039`, `/show`, `/skip`,
   `/republish`, `/help`; a bill by any number or link) are answered under the command a few
   minutes later. A small relay on an always-on server files them into the git branch `inbox`
   and starts the commands phase on GitHub. See [`docs/operator-commands.md`](docs/operator-commands.md).

The Sejm term (kadencja) is read from the API on every run (`/sejm/term`), so a new Sejm is
picked up without any change of configuration. Discovery works in the current term; everything
else covers the earlier terms too, because acts of the old Sejm still reach Dziennik Ustaw and
enter into force months later. On the first run of a new term the bot posts one last update
under every followed bill the old Sejm never finished with (zasada dyskontynuacji: the bill
lapsed and must be submitted again; a citizens' bill is taken over by the new Sejm), stops
following them, and carries the RCL projects still waiting for their druk over to the new term.

Each phase is isolated: an outage of the Sejm API, the LLM or Telegram stops that phase with a
clear error in the report, the others still run, and no per-bill retry budget is consumed. The
process exits with code 1 on errors so the workflow shows red, but the state is saved regardless.
Within a phase the network calls (PDF downloads, process lookups, model calls) run a few at a
time; decisions and database writes stay sequential, so the outcome never depends on timing.

## Quick start

```bash
git clone https://github.com/sobolevbel/lexinform && cd lexinform
uv sync
cp .env.example .env            # ANTHROPIC_API_KEY, LEXINFORM_TELEGRAM_BOT_TOKEN, LEXINFORM_TELEGRAM_CHANNEL_ID

uv run lexinform show 3039                # what the API knows about a bill (no keys needed)
uv run lexinform scan --since 2026-08-01  # discovery + prefilters, prints candidates
uv run lexinform analyze 3039             # one LLM analysis into the local SQLite db
uv run lexinform preview 3039             # render the card (--to <chat id> sends it)
uv run lexinform run --dry-run            # full run, nothing posted, DB rolled back
uv run lexinform run
```

The first run publishes nothing from the past: only bills modified after the watermark (default:
last 24 hours). `--since YYYY-MM-DD` starts earlier; `--no-publish` seeds the database silently.
`--dry-run` still calls the model; add `--max-analyze 0` to try the pipeline without paying.

## Deploy with GitHub Actions

`.github/workflows/daily.yml` runs the bot on weekdays at 05:23 and 16:23 UTC (07:23 and 18:23
Warsaw in summer) and on the weekend at 10:23 UTC (12:23 Warsaw); GitHub starts scheduled runs
3–4.5 hours late in this repository. It keeps its state in the `state` branch as a plain-text SQL dump.

1. Create a bot with [@BotFather](https://t.me/BotFather); create the channel and add the bot as an
   administrator (channel id: `@name` or `-100…`).
2. Add repository secrets: `ANTHROPIC_API_KEY`, `LEXINFORM_TELEGRAM_BOT_TOKEN`,
   `LEXINFORM_TELEGRAM_CHANNEL_ID`, optionally `LEXINFORM_TELEGRAM_LOG_CHANNEL_ID` and
   `LEXINFORM_RCL_PROXY_URL`.
3. *Actions → Daily run → Run workflow* with `dry_run` checked, then once more without it.

The workflow checks out `state` into a worktree, restores the database, runs the bot, dumps the
database back and pushes with its own `GITHUB_TOKEN`. Schema migrations run on restore. Do not
protect the `state` branch. GitHub may delay scheduled runs by up to an hour.

RCL drops connections from GitHub-hosted runners (US addresses), so without
`LEXINFORM_RCL_PROXY_URL` — an HTTP forward proxy on an EU host — the RCL discovery phase fails
on every scheduled run while the rest of the bot works:
[`docs/rcl-proxy.md`](docs/rcl-proxy.md).

Operator commands from the technical channel need the `inbox` branch and a relay process on a
server that is always on (`lexinform listen`):
[`docs/operator-commands.md`](docs/operator-commands.md).

## Configuration

Environment variables or `.env`. `ANTHROPIC_API_KEY` is read by the SDK.

| Variable | Default | Meaning |
|---|---|---|
| `LEXINFORM_TELEGRAM_BOT_TOKEN` / `_CHANNEL_ID` | — | Required to post |
| `LEXINFORM_TELEGRAM_LOG_CHANNEL_ID` | — | Technical channel for run reports and operator commands |
| `LEXINFORM_INBOX_DIR` | — | Directory of `{update_id}.json` command files (the `inbox` branch checked out by the workflow); empty = no commands phase |
| `LEXINFORM_GITHUB_REPO` / `_GITHUB_TOKEN` / `_INBOX_BRANCH` | — / — / `inbox` | The relay's side (`lexinform listen`): where to file commands (fine-grained token, Contents read/write) |
| `LEXINFORM_LISTEN_TIMEOUT_SECONDS` | `50` | How long one `getUpdates` call of the relay waits for a post |
| `LEXINFORM_TERM` | — | Sejm term; empty = the current one from `/sejm/term` (a new kadencja is picked up by itself), a number pins an older term |
| `LEXINFORM_DB_PATH` | `lexinform.db` | SQLite file |
| `LEXINFORM_LLM_MODEL` / `_LLM_EFFORT` | `claude-opus-5` / `medium` | Model and effort |
| `LEXINFORM_LLM_TRIAGE_MODEL` | `claude-sonnet-5` | Model for the cheap first pass on excerpts (`""` disables it) |
| `LEXINFORM_TRIAGE_MIN_CHARS` / `_TRIAGE_MIN_CONFIDENCE` | `20000` / `0.8` | Texts shorter than this skip the triage; confidence a rejection needs |
| `LEXINFORM_OUTPUT_LANGUAGE` | `ru` | `ru` or `en` (add more in `i18n.py`) |
| `LEXINFORM_MIN_SCORE` | `3` | Minimum importance to publish |
| `LEXINFORM_MAX_PUBLISH_PER_RUN` / `_MAX_ANALYZE_PER_RUN` | `10` / `40` | Flood and cost caps |
| `LEXINFORM_MAX_ANALYSIS_ATTEMPTS` | `3` | Attempts per bill before the analysis is given up on (an outage costs none) |
| `LEXINFORM_TEXT_BUDGET_CHARS` | `1500000` | Safety cap on text sent to the LLM (prints go in full) |
| `LEXINFORM_MAX_ANALYSIS_COST_USD` / `_MAX_RUN_COST_USD` | `2.0` / `15.0` | Cost guard rails (0 disables): a first analysis estimated above the per-bill limit is skipped (`skipped_cost`, revive with `reset`); the analysis phase stops for the run at the per-run limit |
| `LEXINFORM_MAX_PDF_DOWNLOAD_MB` | `200` | Safety valve for memory; bigger files are analysed from metadata |
| `LEXINFORM_SEJM_CONCURRENCY` / `_LLM_CONCURRENCY` | `4` / `2` | Parallel PDF downloads and process lookups / bills analysed at once |
| `LEXINFORM_TEXT_PREFILTER_ENABLED` | `true` | Scan the PDF when the title says nothing |
| `LEXINFORM_TEXT_PREFILTER_MIN_DISTINCT` / `_MIN_OCCURRENCES` | `2` / `3` | Text-hit threshold |
| `LEXINFORM_TEXT_PREFILTER_MAX_PER_RUN` | `20` | Texts scanned per run (a download and an extraction each) |
| `LEXINFORM_PRE_PRINT_ENABLED` | `true` | Watch `/bills` for bills without a print number |
| `LEXINFORM_VOTING_CLUB_BREAKDOWN` | `true` | Show how each club voted |
| `LEXINFORM_TRACK_CLOSED_GRACE_DAYS` / `_TRACK_PASSED_MAX_DAYS` | `90` / `180` | How long closed / passed-but-unpublished bills are followed |
| `LEXINFORM_TRACK_FULL_WEEKDAY` | `0` (Monday) | Weekday on which every followed bill is checked, not only the changed ones |
| `LEXINFORM_IN_FORCE_REMINDERS` | `true` | Reminder on the entry-into-force day |
| `LEXINFORM_RCL_ENABLED` / `_RCL_CONCURRENCY` | `true` / `6` | Follow government projects on legislacja.rcl.gov.pl before they reach the Sejm; projects read at once (a page takes ~10 s) |
| `LEXINFORM_RCL_PROXY_URL` | — | HTTP forward proxy with an EU address for RCL (`http://user:pass@host:port`); RCL drops connections from GitHub's US runners. See [`docs/rcl-proxy.md`](docs/rcl-proxy.md) |
| `LEXINFORM_CONSULTATION_REMINDERS` / `_CONSULTATION_REMINDER_DAYS` | `true` / `3` | Reminder this many days before a public consultation closes or applications to a public hearing close |
| `LEXINFORM_AGENDA_WATCH` | `true` | Post when a followed bill appears on the agenda of a committee or Sejm sitting |
| `LEXINFORM_MAX_PUBLISH_ATTEMPTS` | `3` | Retries of a failed Telegram post |
| `LEXINFORM_RUNS_RETENTION_DAYS` | `90` | Run records (with their reports) older than this are deleted from the database |
| `LEXINFORM_LOG_LEVEL` / `LEXINFORM_LOG_JSON` | `INFO` / `false` | Logging |

The rest of `src/lexinform/settings.py` is infrastructure that rarely moves: API base URLs and
timeouts, the page size of Sejm listings, the first-run lookback and the re-run overlap in days,
and the model's `max_tokens`.

## Scoring rubric

| Score | Meaning |
|---|---|
| 5 | Legalization of stay: ustawa o cudzoziemcach, residence permits, visas, citizenship, protection |
| 4 | Work and adjacent rights: work permits, aid to Ukrainian citizens, repatriation, Karta Polaka |
| 3 | Social sphere: benefits, healthcare, education, PESEL, banking, housing |
| 2 | Indirect impact: border management, tax residency, bilateral agreements |
| 1 | Marginal mention |

The rubric is in `src/lexinform/adapters/llm_prompts.py`; `PROMPT_VERSION` is stored with every analysis.

## CLI

| Command | Purpose |
|---|---|
| `lexinform run [--since D] [--dry-run] [--no-publish] [--no-track] [--no-rcl] [--full-track] [--max-publish N] [--max-analyze N] [--min-score N]` | The daily job |
| `lexinform scan [--since D]` | Discovery + prefilters, prints candidates |
| `lexinform reprefilter [--limit N] [--include-text-skipped]` | Scan the texts of bills the title prefilter skipped (print PDFs; RCL projects are read again) |
| `lexinform analyze NUMBER [--force] [--json]` | Analyse one bill |
| `lexinform preview NUMBER [--to CHAT]` | Render or send the card |
| `lexinform track [--dry-run]` | Only the tracking phase |
| `lexinform commands [--dry-run]` | Answer the operator commands waiting in `LEXINFORM_INBOX_DIR` (what the relay's `repository_dispatch` runs) |
| `lexinform listen [--once] [--dry-run]` | The relay: file the technical channel's commands into the `inbox` branch (runs on a server) |
| `lexinform show NUMBER` | API data and local status (`RPW/…` and `RCL/…` numbers show the submission or the project) |
| `lexinform republish NUMBER [-y]` | Post a bill's card again after a failed or lost post |
| `lexinform reset NUMBER [--to STATUS] [-y]` | Put a bill back into a status with a clean retry budget |
| `lexinform runs [--days N]` | The recorded runs of the last days: counters, errors, tokens, cost |
| `lexinform cost [--days N] [--top N]` | LLM spend per model and per run, the dearest run and analyses |
| `lexinform db init / dump FILE / restore FILE [--missing-ok]` | Database maintenance |

## Code layout

```
src/lexinform/
  models/, ports.py          domain models (enums, sejm, rcl, analysis, bill, events, commands,
                             report), the Protocols every service depends on
  keywords.py, authors.py    keyword prefilter, cover-letter parsing
  rcl_letters.py             deadline and e-mail out of an RCL consultation letter
  sections.py, pricing.py    print structure (trimming, excerpts), model list prices
  agenda.py, concurrency.py  prints named in a sitting agenda; fan_out: parallel network steps,
                             sequential writes
  i18n.py, settings.py       labels per language, pydantic-settings
  adapters/                  sejm_api (+ ELI), rcl_html (scraper), pdf_text, doc_text,
                             document_text (Word, format sniffing), llm_anthropic
                             (+ llm_prompts), publisher_base + telegram (+ telegram_format) and
                             console, sqlite_repo, inbox_files and github_inbox (the command
                             inbox and the relay's writer)
  services/                  terms, discovery, rcl_discovery (+ rcl_projects), sources (where a
                             bill's text comes from), documents (loader), text_prefilter,
                             analysis, signatories, publishing, lookup, commands, listener (the
                             relay), pipeline, tracking/ (service, stages, pre_print, rcl,
                             linking, acts, consultations, hearings, agenda, rollover, posting)
  container.py, cli.py       composition root, typer commands
tests/                       fakes.py (ports in memory), harness.py (the real Container over the fakes),
                             unit/ on fakes + recorded API fixtures; `-m integration` hits the live API
```

Services depend only on `ports.py`, so swapping the LLM, the database or the messenger means one
adapter.

## Documentation

| Document | What is in it |
|---|---|
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Setup, the check before every commit, tests, migrations, where a change goes |
| [`docs/legislative-process.md`](docs/legislative-process.md) | How a Polish law is made: stages, legal deadlines, the public's windows, the API's stage vocabulary (`docs/legislative-process.html` is a one-page Russian version for readers) |
| [`docs/database.html`](docs/database.html) | The database on one page: tables, relations, indexes and the migration ledger (Russian, open it in a browser) |
| [`docs/operator-commands.md`](docs/operator-commands.md) | The technical channel's commands, how one travels to a run, the relay's setup |
| [`docs/rcl-proxy.md`](docs/rcl-proxy.md) | Why RCL needs an EU egress and how the proxy is built |
| [`docs/roadmap.md`](docs/roadmap.md) | What is done, what is still open, and the API facts verified with curl |

## License

MIT. Data comes from the public Sejm API; summaries are machine-generated and are not legal advice.
