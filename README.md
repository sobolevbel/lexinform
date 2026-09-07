# lexinform

**lexinform** is a small daily bot that watches bills submitted to the Polish Sejm, picks out the ones
that matter to foreigners living in Poland, asks an LLM to score and summarise them, and posts the
result to a Telegram channel together with the bill's PDF. It then keeps an eye on each published
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

🔗 Ход процесса в Сейме | PDF druku

#важность5 #легализация #druk3039 #Sejm10
```

The PDF of the print is attached as a reply to the card. When a bill moves to a new stage, an
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
   bills changed since the last successful run (with one day of overlap).
2. **Prefilter.** Polish word stems (`cudzoziem*`, `obywatelstw*`, `zezwoleni* na pobyt`, `Kart* Polaka`,
   `Straż* Graniczn*`, …) with word boundaries decide which bills deserve an LLM call. The filter is
   deliberately over-inclusive; the LLM makes the final relevance call.
3. **Analyse.** The bill PDF is downloaded from the API, text is extracted (`pypdf`), cut to a
   character budget that keeps the start of the act and of the justification, and sent to Claude with
   a structured-output schema (`relevant`, `score`, `category`, `summary`, `key_changes`,
   `affected_groups`, `practical_impact`, `effective_date`, `confidence`).
4. **Publish.** Relevant bills with `score >= LEXINFORM_MIN_SCORE` are posted, highest score first,
   at most `LEXINFORM_MAX_PUBLISH_PER_RUN` per run. A publication row is written *before* sending, so
   a crash can never produce a duplicate post.
5. **Track.** For every published bill the stage tree is re-fetched and hashed; a changed hash
   produces exactly one update message listing the new stages. If a newer text of the bill exists
   (committee report, text after the 3rd reading, or a print changed after the analysis), the bill
   is re-analysed with the previous analysis as context and the update also lists what changed.
   Bills that did not change are never sent to the LLM again.
6. **Report.** If `LEXINFORM_TELEGRAM_LOG_CHANNEL_ID` is set, a run report with counters, token
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
| `LEXINFORM_MIN_SCORE` | `2` | Minimum importance to publish |
| `LEXINFORM_MAX_PUBLISH_PER_RUN` | `10` | Flood protection |
| `LEXINFORM_MAX_ANALYZE_PER_RUN` | `40` | Cap on LLM calls per run |
| `LEXINFORM_TEXT_BUDGET_CHARS` | `80000` | Max characters of bill text sent to the LLM |
| `LEXINFORM_MAX_PDF_DOWNLOAD_MB` | `25` | Bigger PDFs are analysed from metadata only |
| `LEXINFORM_FIRST_RUN_LOOKBACK_DAYS` | `1` | Watermark for the very first run |
| `LEXINFORM_TRACK_CLOSED_GRACE_DAYS` | `30` | Keep tracking closed bills this long |
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
| `lexinform analyze NUMBER [--force] [--json]` | Analyse one bill |
| `lexinform preview NUMBER [--to CHAT]` | Render (or send to a test chat) the card |
| `lexinform track [--dry-run]` | Only the status-tracking phase |
| `lexinform show NUMBER` | Stages and attachments from the API, local status |
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
