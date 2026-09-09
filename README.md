# lexinform

A daily bot that watches bills in the Polish Sejm, picks out the ones that matter to foreigners
living in Poland, scores and summarises them with an LLM, and posts the result to a Telegram channel.
It then follows each bill through the whole legislative process, from public consultation to
publication in Dziennik Ustaw, so readers learn about changes while they can still act on them.

- Source: the official Sejm REST API (`api.sejm.gov.pl`) and its ELI API. No scraping.
- Importance 1–5, where **5 = legalization of stay** (ustawa o cudzoziemcach, residence permits,
  visas, citizenship, international protection).
- Posts in Russian (Polish statute names kept in the original); English labels built in.
- Runs three times a day in GitHub Actions; state is a SQLite dump in the `state` branch. Zero infra.
- Python 3.12+, `uv`, `pydantic`, `anthropic` SDK, `httpx2`, `pypdf`, SQLite.

## What readers get

A card per relevant bill, and replies under that card as the bill moves:

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
✍️ Инициатор: депутатский (подписали: Lewica 21 · представитель: Daria Gosek-Popiołek, Lewica)   📄 Дата druku: 03.08.2026

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
bill (date, time, room, agenda item, live stream), new stages (committee referral with the
committee's name, readings, votes with the per-club breakdown, Senate position, President's
signature or veto), a fresh analysis with "what changed" when the bill's text changes, "published
in Dziennik Ustaw" with the entry-into-force date, and a reminder on the day the act enters into
force.

Bills that have no print (druk) number yet (`RPW/…`, the consultation stage) are covered too, from
their official description; when the print number is assigned the thread continues under the same
card. Government bills are caught even earlier, on legislacja.rcl.gov.pl (Rządowy Proces
Legislacyjny), where the ministry consults them months before the Sejm: the card names the
deadline and the e-mail from the consultation letter, the RCL comment form, and follows the project
through the committees of the Council of Ministers until the druk appears and takes over the
thread. A second, technical channel can receive a report after every run (counters, tokens,
errors).

## How it works

```
/processes + /bills + RCL ─► keyword prefilter (title, then text) ─► LLM (structured output) ─► SQLite
Telegram ◄── cards (once per bill) ◄── publish ◄──┘        └─► track: stage diff, votes, ELI act
```

1. **Discover** bills modified since the last run (`/processes`, with one day of overlap), bills
   submitted without a print number (`/bills`) and government projects modified on RCL (the HTML
   list sorted by modification date; one project page per new project, its stage catalogs only
   for candidates).
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

## Deploy with GitHub Actions

`.github/workflows/daily.yml` runs the bot at 04:23, 10:23 and 16:23 UTC (odd minutes: GitHub
delays full-hour crons by hours) and keeps its state in the `state` branch as a plain-text SQL dump.

1. Create a bot with [@BotFather](https://t.me/BotFather); create the channel and add the bot as an
   administrator (channel id: `@name` or `-100…`).
2. Add repository secrets: `ANTHROPIC_API_KEY`, `LEXINFORM_TELEGRAM_BOT_TOKEN`,
   `LEXINFORM_TELEGRAM_CHANNEL_ID`, optionally `LEXINFORM_TELEGRAM_LOG_CHANNEL_ID`.
3. *Actions → Daily run → Run workflow* with `dry_run` checked, then once more without it.

The workflow checks out `state` into a worktree, restores the database, runs the bot, dumps the
database back and pushes with its own `GITHUB_TOKEN`. Schema migrations run on restore. Do not
protect the `state` branch. GitHub may delay scheduled runs by up to an hour.

## Configuration

Environment variables or `.env`. `ANTHROPIC_API_KEY` is read by the SDK.

| Variable | Default | Meaning |
|---|---|---|
| `LEXINFORM_TELEGRAM_BOT_TOKEN` / `_CHANNEL_ID` | — | Required to post |
| `LEXINFORM_TELEGRAM_LOG_CHANNEL_ID` | — | Technical channel for run reports |
| `LEXINFORM_TERM` | — | Sejm term; empty = the current one from `/sejm/term` (a new kadencja is picked up by itself), a number pins an older term |
| `LEXINFORM_DB_PATH` | `lexinform.db` | SQLite file |
| `LEXINFORM_LLM_MODEL` / `_LLM_EFFORT` | `claude-opus-5` / `medium` | Model and effort |
| `LEXINFORM_LLM_TRIAGE_MODEL` | `claude-sonnet-5` | Model for the cheap first pass on excerpts (`""` disables it) |
| `LEXINFORM_TRIAGE_MIN_CHARS` / `_TRIAGE_MIN_CONFIDENCE` | `20000` / `0.8` | Texts shorter than this skip the triage; confidence a rejection needs |
| `LEXINFORM_OUTPUT_LANGUAGE` | `ru` | `ru` or `en` (add more in `i18n.py`) |
| `LEXINFORM_MIN_SCORE` | `3` | Minimum importance to publish |
| `LEXINFORM_MAX_PUBLISH_PER_RUN` / `_MAX_ANALYZE_PER_RUN` | `10` / `40` | Flood and cost caps |
| `LEXINFORM_TEXT_BUDGET_CHARS` | `1500000` | Safety cap on text sent to the LLM (prints go in full) |
| `LEXINFORM_MAX_PDF_DOWNLOAD_MB` | `25` | Bigger PDFs are analysed from metadata |
| `LEXINFORM_SEJM_CONCURRENCY` / `_LLM_CONCURRENCY` | `4` / `2` | Parallel PDF downloads and process lookups / bills analysed at once |
| `LEXINFORM_TEXT_PREFILTER_ENABLED` | `true` | Scan the PDF when the title says nothing |
| `LEXINFORM_TEXT_PREFILTER_MIN_DISTINCT` / `_MIN_OCCURRENCES` | `2` / `3` | Text-hit threshold |
| `LEXINFORM_PRE_PRINT_ENABLED` | `true` | Watch `/bills` for bills without a print number |
| `LEXINFORM_VOTING_CLUB_BREAKDOWN` | `true` | Show how each club voted |
| `LEXINFORM_TRACK_CLOSED_GRACE_DAYS` / `_TRACK_PASSED_MAX_DAYS` | `90` / `180` | How long closed / passed-but-unpublished bills are followed |
| `LEXINFORM_TRACK_FULL_WEEKDAY` | `0` (Monday) | Weekday on which every followed bill is checked, not only the changed ones |
| `LEXINFORM_IN_FORCE_REMINDERS` | `true` | Reminder on the entry-into-force day |
| `LEXINFORM_RCL_ENABLED` / `_RCL_CONCURRENCY` | `true` / `6` | Follow government projects on legislacja.rcl.gov.pl before they reach the Sejm; projects read at once (a page takes ~10 s) |
| `LEXINFORM_RCL_PROXY_URL` | — | HTTP forward proxy with an EU address for RCL (`http://user:pass@host:port`); RCL drops connections from GitHub's US runners. See `docs/rcl-proxy.md` |
| `LEXINFORM_CONSULTATION_REMINDERS` / `_CONSULTATION_REMINDER_DAYS` | `true` / `3` | Reminder this many days before a public consultation closes |
| `LEXINFORM_AGENDA_WATCH` | `true` | Post when a followed bill appears on the agenda of a committee or Sejm sitting |
| `LEXINFORM_MAX_PUBLISH_ATTEMPTS` | `3` | Retries of a failed Telegram post |
| `LEXINFORM_LOG_LEVEL` / `LEXINFORM_LOG_JSON` | `INFO` / `false` | Logging |

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
| `lexinform run [--since D] [--dry-run] [--no-publish] [--no-track] [--max-publish N] [--max-analyze N] [--min-score N]` | The daily job |
| `lexinform scan [--since D]` | Discovery + prefilters, prints candidates |
| `lexinform reprefilter [--limit N] [--include-text-skipped]` | Scan PDFs of bills the title prefilter skipped |
| `lexinform analyze NUMBER [--force] [--json]` | Analyse one bill |
| `lexinform preview NUMBER [--to CHAT]` | Render or send the card |
| `lexinform track [--dry-run]` | Only the tracking phase |
| `lexinform show NUMBER` | API data and local status (`RPW/…` numbers show the submission) |
| `lexinform republish NUMBER [-y]` | Post a bill's card again after a failed or lost post |
| `lexinform reset NUMBER [--to STATUS] [-y]` | Put a bill back into a status with a clean retry budget |
| `lexinform db init / dump FILE / restore FILE [--missing-ok]` | Database maintenance |

## Code layout

```
src/lexinform/
  models/, ports.py          domain models (enums, sejm, rcl, analysis, bill, report), Protocols
  keywords.py, authors.py    keyword prefilter, cover-letter parsing
  rcl_letters.py             deadline and e-mail out of an RCL consultation letter
  sections.py, pricing.py    print structure (trimming, excerpts), model list prices
  concurrency.py             fan_out: parallel network steps, sequential writes
  i18n.py, settings.py       labels per language, pydantic-settings
  adapters/                  sejm_api (+ ELI), rcl_html (scraper), pdf_text, document_text (Word,
                             format sniffing), llm_anthropic (+ llm_prompts), telegram
                             (+ telegram_format), sqlite_repo, console
  services/                  discovery, rcl_discovery (+ rcl_projects), sources (where a bill's
                             text comes from), documents (loader), text_prefilter, analysis,
                             signatories, publishing, pipeline, tracking/ (stages, pre-print and
                             RCL links, rcl watcher, acts, reminders, agenda, posting)
  container.py, cli.py       composition root, typer commands
tests/                       fakes.py (ports in memory), harness.py (the pipeline on fakes),
                             unit/ on fakes + recorded API fixtures; `-m integration` hits the live API
```

Services depend only on `ports.py`, so swapping the LLM, the database or the messenger means one
adapter. Roadmap and verified API facts: `docs/roadmap.md`. How a Polish law is made, with the
deadlines, the public's windows and the API stage vocabulary: `docs/legislative-process.md`
(a Russian one-page version for readers: `docs/legislative-process.html`, open it in a browser).
Contributing: `CONTRIBUTING.md`.

## License

MIT. Data comes from the public Sejm API; summaries are machine-generated and are not legal advice.
