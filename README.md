# lexinform

**lexinform** is a small daily bot that watches bills submitted to the Polish Sejm, picks out the ones
that matter to foreigners living in Poland, asks an LLM to score and summarise them, and posts the
result to a Telegram channel with a link to the bill's PDF. It then keeps an eye on each published
bill and posts an update whenever the legislative process moves (committee, readings, Senate, ...).

- Source of truth: the official Sejm REST API (`api.sejm.gov.pl`), no scraping.
- Importance score 1–5, where **5 = changes to legalization of stay** (ustawa o cudzoziemcach,
  residence permits, visas, citizenship, international protection).
- Summaries in Russian by default (Polish statute names are kept in the original); English labels
  are built in, other languages are a small dictionary away.
- Runs once a day as a GitHub Actions workflow with zero infrastructure, or as a Docker container.
- Python 3.12+, SQLite, [`anthropic`](https://github.com/anthropics/anthropic-sdk-python) SDK, `httpx2`, `pydantic`.

## What a post looks like

```
📜 Новый законопроект — druk nr 3039
Poselski projekt ustawy o zmianie ustawy o udzielaniu cudzoziemcom ochrony na terytorium RP

🔴 Важность: ●●●●● 5/5 — изменения в легализации пребывания
🏷 Категория: легализация пребывания

📝 О чём проект
…2–3 предложения простым языком…

🔑 Ключевые изменения
• …
• …

💡 Что это значит на практике: …
👥 Кого касается: …
📅 Вступление в силу: через 14 дней после публикации
🏛 Стадия: Skierowano do I czytania w komisjach (2026-09-03)
✍️ Инициатор: депутатский   📄 Дата druku: 2026-08-03

🔗 Ход процесса в Сейме | PDF

#важность5 #легализация #druk3039 #Sejm10
```

The card links to the bill's PDF on the Sejm API (no file attachments). When a bill moves to a new stage, an
"Обновление — druk nr 3039" message is posted **as a reply to the original card**: the new stages,
the current summary, and, when the text of the bill itself changed (committee report with
amendments, text after the 3rd reading, updated print), a fresh LLM analysis with a
"Что изменилось с прошлого раза" section.

Optionally, a second, technical channel receives a report after every run (counts, tokens, errors,
captured warnings) so the main channel stays clean.

## How it works

```
Sejm API ──► discover (modifiedSince) ──► keyword prefilter ──► PDF text ──► LLM (structured output)
                                                                                  │
      Telegram ◄── publish new bills (once per bill) ◄── SQLite ◄─────────────────┘
                ◄── status updates (stage fingerprint diff) ◄── track published bills
```

1. **Discover.** `GET /sejm/term10/processes?documentType=projekt ustawy&modifiedSince=…` lists
   bills changed since the last run that completed discovery (with one day of overlap). In
   addition, `GET /sejm/term10/bills?dateOfReceiptFrom=…` lists bills that were **submitted but have
   no print (druk) number yet** (`RPW/…` numbers). This is the earliest public trace of a bill and
   the stage where public consultations run, so such bills get a card too, analysed from the
   official title and description (their PDF sits behind the Sejm website's bot protection). When
   the print number is assigned, the print inherits the card: the RPW entry is marked `linked`, the
   full text is analysed against the earlier analysis, and the thread continues with an update
   "print number assigned" under the same message. Withdrawn RPW entries get a final update.
2. **Prefilter.** Polish word stems (`cudzoziem*`, `obywatelstw*`, `zezwoleni* na pobyt`, `Kart* Polaka`,
   `Straż* Graniczn*`, …) with word boundaries decide which bills deserve an LLM call. The filter is
   deliberately over-inclusive; the LLM makes the final relevance call. Bills whose title and
   description say nothing (`o zmianie niektórych ustaw…`) get a second chance: their print PDF is
   downloaded and scanned with the same patterns, and the bill goes on to analysis when at least
   two different patterns occur or the patterns occur three times in total (bandwidth, not tokens).
   Such cards carry the note "found by scanning the bill text".
3. **Analyse.** The bill PDF is downloaded from the API, text is extracted (`pypdf`), cut to a
   character budget that keeps the start of the act and of the justification, and sent to Claude with
   a structured-output schema (`relevant`, `score`, `category`, `summary`, `key_changes`,
   `affected_groups`, `practical_impact`, `effective_date`, `confidence`).
   For deputies' and committee bills the cover letter of the print is parsed for the signatories
   and the representative, and names are matched against `/MP` to show which clubs stand behind
   the bill ("подписали: Lewica 21 · представитель: Daria Gosek-Popiołek, Lewica").
4. **Publish.** Relevant bills with `score >= LEXINFORM_MIN_SCORE` are posted, highest score first,
   at most `LEXINFORM_MAX_PUBLISH_PER_RUN` per run. A publication row is written *before* sending, so
   a crash can never produce a duplicate post.
5. **Track.** For every published bill the stage tree is re-fetched and hashed; a changed hash
   produces exactly one update message listing the new stages. Votes show the totals and how each
   club voted (fetched from `/votings/{sitting}/{number}`), Senate and President stages are rendered
   as outcomes ("Senate introduced amendments", "vetoed by the President"), committee referrals show
   the committee name with a hint that opinions can be sent to it. If a newer text of the bill
   exists (committee report with the full text, text after the 3rd reading, or a print changed after
   the analysis), the bill is re-analysed with the previous analysis as context and the update also
   lists what changed. Bills that did not change are never sent to the LLM again. Updates whose
   Telegram post failed are retried on later runs.
6. **Publication and entry into force.** Once the process carries an ELI address, the act is
   fetched from the Sejm's ELI API (`/eli/acts/DU/{year}/{pos}`) and a reply "Опубликован в
   Dziennik Ustaw" gives the journal position, the publication date and the entry-into-force date
   with links to ISAP and the act's PDF. On the entry-into-force day (Warsaw time) a second reply
   "С сегодняшнего дня действует" repeats the summary. Bills passed by the Sejm are followed until
   their act is published (up to `LEXINFORM_TRACK_PASSED_MAX_DAYS`), because the Senate, the
   President and publication take weeks. Limitation: ELI exposes a single entry-into-force date, so
   staged provisions are only covered by the note in the message.
7. **Report.** If `LEXINFORM_TELEGRAM_LOG_CHANNEL_ID` is set, a run report with counters, token
   usage, errors and captured warnings is posted there. The main channel only ever gets bill posts.

## Quick start (local)

```bash
git clone https://github.com/sobolevbel/lexinform && cd lexinform
uv sync                                   # installs Python 3.12 + dependencies into .venv
cp .env.example .env                      # fill in ANTHROPIC_API_KEY and the Telegram values

uv run lexinform show 3039                # what the API knows about a bill (no keys needed)
uv run lexinform scan --since 2026-08-01  # discovery + prefilter only, prints candidates
uv run lexinform analyze 3039             # one LLM analysis, stored in the local SQLite db
uv run lexinform preview 3039             # render the Telegram card (add --to <chat id> to send it)
uv run lexinform run --dry-run            # full daily run, nothing posted, DB changes rolled back
uv run lexinform run                      # the real thing
```

The first real run publishes nothing from the past: only bills modified after the watermark (by
default, the last 24 hours) are considered. Use `--since YYYY-MM-DD` to start from an earlier date;
combine with `--no-publish` to seed the database without flooding the channel.

## Deploy with GitHub Actions (recommended)

The repository ships a scheduled workflow (`.github/workflows/daily.yml`) that runs the bot every day
and keeps its state in a dedicated `state` branch as a plain-text SQL dump. No servers, no cost.

1. **Create the bot.** Talk to [@BotFather](https://t.me/BotFather), `/newbot`, copy the token.
2. **Create the channel** and add the bot as an administrator with permission to post.
   The channel id is `@your_channel` for public channels or `-100…` for private ones
   (forward a channel post to [@userinfobot](https://t.me/userinfobot) to see it).
3. **Get an Anthropic API key** at <https://console.anthropic.com>.
4. **Add three repository secrets** in *Settings → Secrets and variables → Actions*:
   `ANTHROPIC_API_KEY`, `LEXINFORM_TELEGRAM_BOT_TOKEN`, `LEXINFORM_TELEGRAM_CHANNEL_ID`.
   Optionally add `LEXINFORM_TELEGRAM_LOG_CHANNEL_ID` for a private technical channel (the bot must
   be an admin there too).
   Secrets are encrypted, masked in logs, and not exposed to pull requests from forks.
5. **Run it once by hand.** *Actions → Daily run → Run workflow* with `dry_run` checked. Check the
   log, then run again without `dry_run`. From then on it fires every day at 05:00 UTC.

The live state of this deployment is in the [`state`](https://github.com/sobolevbel/lexinform/tree/state) branch.

How the state branch works: the workflow checks out `state` into a worktree, restores the SQLite
database from `state/lexinform.sql`, runs the bot, dumps the database back and pushes the branch with
the workflow's own `GITHUB_TOKEN` (`permissions: contents: write`). Nothing else is needed; the daily
commit also keeps the repository "active" so GitHub does not pause the schedule. Do not enable branch
protection on `state`.

Other knobs live in the workflow `env` block and in the [configuration](#configuration) table. GitHub
may delay scheduled workflows by minutes to an hour under load; if you need exact timing, use Docker.

## Deploy with Docker

Images are published to GHCR on every `v*` tag.

```bash
docker run --rm --env-file .env -v lexinform-data:/data ghcr.io/sobolevbel/lexinform:latest run
```

Schedule it with cron or a systemd timer, e.g. `0 5 * * * docker run --rm --env-file /etc/lexinform.env -v lexinform-data:/data ghcr.io/sobolevbel/lexinform:latest run`.
The database lives in the `lexinform-data` volume.

## Configuration

All settings are environment variables (or a `.env` file). `ANTHROPIC_API_KEY` is read by the SDK.

| Variable | Default | Meaning |
|---|---|---|
| `LEXINFORM_TELEGRAM_BOT_TOKEN` | — | Bot token from @BotFather (required to post) |
| `LEXINFORM_TELEGRAM_CHANNEL_ID` | — | `@channel` or `-100…` (required to post) |
| `LEXINFORM_TELEGRAM_LOG_CHANNEL_ID` | — | Optional technical channel for run reports and warnings |
| `LEXINFORM_TERM` | `10` | Sejm term (kadencja) |
| `LEXINFORM_DB_PATH` | `lexinform.db` | SQLite file |
| `LEXINFORM_LLM_MODEL` | `claude-opus-5` | Any Claude model id |
| `LEXINFORM_LLM_EFFORT` | `medium` | `low` … `max` |
| `LEXINFORM_OUTPUT_LANGUAGE` | `ru` | Language of summaries and labels (`ru`, `en`; add more in `i18n.py`) |
| `LEXINFORM_MIN_SCORE` | `3` | Minimum importance to publish (2 = indirectly affects foreigners is not posted) |
| `LEXINFORM_MAX_PUBLISH_PER_RUN` | `10` | Flood protection |
| `LEXINFORM_MAX_ANALYZE_PER_RUN` | `40` | Cap on LLM calls per run |
| `LEXINFORM_TEXT_BUDGET_CHARS` | `1500000` | Safety cap on bill text sent to the LLM (~750k tokens of Polish); real prints are sent in full |
| `LEXINFORM_MAX_PDF_DOWNLOAD_MB` | `25` | Bigger PDFs are analysed from metadata only |
| `LEXINFORM_FIRST_RUN_LOOKBACK_DAYS` | `1` | Watermark for the very first run |
| `LEXINFORM_TRACK_CLOSED_GRACE_DAYS` | `90` | Keep tracking closed bills this long (Dz.U. publication follows 30–40 days after the Sejm vote) |
| `LEXINFORM_TRACK_PASSED_MAX_DAYS` | `180` | Keep following passed bills whose act is not published yet |
| `LEXINFORM_IN_FORCE_REMINDERS` | `true` | Post a reminder on the day the act enters into force |
| `LEXINFORM_MAX_PUBLISH_ATTEMPTS` | `3` | Retry a failed Telegram post on later runs at most this many times |
| `LEXINFORM_TEXT_PREFILTER_ENABLED` | `true` | Scan the print PDF when the title/description miss the keywords |
| `LEXINFORM_TEXT_PREFILTER_MIN_DISTINCT` / `_MIN_OCCURRENCES` | `2` / `3` | Text hits needed to send a bill to analysis |
| `LEXINFORM_TEXT_PREFILTER_MAX_PER_RUN` | `20` | Cap on PDFs scanned per run |
| `LEXINFORM_PRE_PRINT_ENABLED` | `true` | Also watch `/bills` for submitted bills without a print number (consultation stage) |
| `LEXINFORM_VOTING_CLUB_BREAKDOWN` | `true` | Fetch per-MP votes to show how each club voted |
| `LEXINFORM_LOG_LEVEL` / `LEXINFORM_LOG_JSON` | `INFO` / `false` | Logging |

## Scoring rubric

| Score | Meaning | Examples |
|---|---|---|
| 5 | Legalization of stay | ustawa o cudzoziemcach, zezwolenia na pobyt, wizy, obywatelstwo, ochrona międzynarodowa |
| 4 | Work and residence-adjacent rights | zezwolenia na pracę, ustawa o pomocy obywatelom Ukrainy, repatriacja, Karta Polaka |
| 3 | Social sphere | świadczenia, NFZ, education, PESEL, banking, housing |
| 2 | Indirect impact | border management, tax residency, bilateral agreements |
| 1 | Marginal mention | foreigners appear only in passing |

The rubric lives in `src/lexinform/adapters/llm_prompts.py`; `PROMPT_VERSION` is stored with every
analysis so prompt changes are traceable.

## CLI

| Command | Purpose |
|---|---|
| `lexinform run [--since D] [--dry-run] [--no-publish] [--no-track] [--max-publish N] [--max-analyze N] [--min-score N]` | The daily job |
| `lexinform scan [--since D]` | Discovery + prefilter, prints candidates |
| `lexinform reprefilter [--limit N] [--include-text-skipped]` | Scan the PDFs of bills the title prefilter skipped; passes become candidates for the next `run` |
| `lexinform analyze NUMBER [--force] [--json]` | Analyse one bill |
| `lexinform preview NUMBER [--to CHAT]` | Render (or send to a test chat) the card |
| `lexinform track [--dry-run]` | Only the status-tracking phase |
| `lexinform show NUMBER` | Stages and attachments from the API, local status (`RPW/…` numbers show the submission) |
| `lexinform db init / dump FILE / restore FILE [--missing-ok]` | Database maintenance |

## Failure behaviour

The bot never crashes on an outage. Each phase (discovery, analysis, publishing, tracking) is
isolated: when the Sejm API, the LLM API or Telegram is unavailable, the phase stops with a clear
message such as `analysis: LLM API unavailable: RateLimitError: 429`, the remaining phases still
run, and the run report with all errors goes to the technical channel (if configured). Outages do
not consume the per-bill retry budget; a problem with one bill (unparsable PDF, model refusal) is
retried on the next run up to 3 times without blocking the others. A publication interrupted
between "sent" and "recorded" is flagged as `unknown` and never re-sent automatically.

The process exits with code `1` when a run had errors so cron and GitHub Actions show it red; the
daily workflow still saves the database state in that case, otherwise the next run would post
duplicates.

## Project layout

```
src/lexinform/
  models.py        domain models + stage fingerprint/diff (pure)
  ports.py         Protocols the services depend on
  keywords.py      keyword prefilter
  i18n.py          labels per language
  settings.py      pydantic-settings
  adapters/        sejm_api, pdf_text, llm_anthropic (+ llm_prompts), telegram (+ telegram_format),
                   sqlite_repo, console (dry-run publisher)
  services/        discovery, analysis, publishing, tracking, pipeline
  container.py     composition root
  cli.py           typer commands
tests/             unit tests on fakes + recorded API fixtures; `-m integration` hits the live API
```

Services only know the `ports.py` interfaces, so swapping the LLM provider, the database, or the
messenger means writing one adapter. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. Data comes from the public Sejm API; summaries are machine-generated and are not legal advice.
