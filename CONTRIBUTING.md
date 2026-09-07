# Contributing

Thanks for helping! This document covers the development setup and the conventions the code follows.

## Setup

```bash
uv sync                      # Python 3.12 + all dependencies (dev group included)
uv run pre-commit install    # ruff, mypy, uv lock check on every commit
uv run pytest                # unit tests (fast, offline)
uv run pytest -m integration # live Sejm API checks (network)
uv run mypy && uv run ruff check src tests
```

## Design rules

- **Services depend on `ports.py` only.** `BillDiscoveryService`, `AnalysisService`,
  `PublishingService`, `StatusTrackingService` receive adapters through their constructors and never
  import from `adapters/`. Wiring lives in `container.py`.
- **Adapters contain no business rules.** They translate between an external system and the domain
  models in `models.py`.
- **Pure where possible.** `models.py`, `keywords.py`, `telegram_format.py`, `llm_prompts.py` do no I/O
  and are tested exhaustively; everything else is tested against fakes (`tests/fakes.py`) or
  `httpx2.MockTransport`.
- **No new dependencies without a reason.** The HTTP client is `httpx2` because the `anthropic` SDK
  depends on it; do not add `httpx`, `requests`, or a Telegram framework.
- Line length 100, `ruff` for lint/format, `mypy --strict` on `src`.

## Common tasks

### Adding or tuning keywords

Edit `KEYWORD_PATTERNS` in `src/lexinform/keywords.py`, then add the title that motivated the change
to `POSITIVE` (or a false positive that must stay excluded to `NEGATIVE`) in
`tests/unit/test_keywords.py`. Patterns must use word boundaries (`\b`) and Polish stems; the filter
should err on the side of inclusion because the LLM makes the final call.

### Changing the prompt or the rubric

Edit `src/lexinform/adapters/llm_prompts.py` and bump `PROMPT_VERSION`. Calibrate on a few known
bills before merging, e.g.:

```bash
uv run lexinform analyze 3039 --force   # ustawa o udzielaniu cudzoziemcom ochrony -> expect 4-5
uv run lexinform analyze 950  --force   # ustawa o cudzoziemcach -> expect 5
uv run lexinform analyze 2699 --force   # nabywanie nieruchomości przez cudzoziemców -> expect 2-3
uv run lexinform analyze 1962 --force   # Prawo o adwokaturze -> expect relevant=false or 1
```

### Adding a language

Add a `Labels` instance to `src/lexinform/i18n.py` and register it in `LABELS`; add the language name
to `_LANGUAGE_NAMES` in `llm_prompts.py`. Set `LEXINFORM_OUTPUT_LANGUAGE` accordingly.

### Refreshing recorded API fixtures

```bash
cd tests/fixtures/sejm
curl -s 'https://api.sejm.gov.pl/sejm/term10/processes?limit=20&documentType=projekt%20ustawy' -o processes_page.json
curl -s 'https://api.sejm.gov.pl/sejm/term10/processes/3039' -o process_3039.json
curl -s 'https://api.sejm.gov.pl/sejm/term10/processes/1962' -o process_1962.json
curl -s 'https://api.sejm.gov.pl/sejm/term10/prints/3039' -o print_3039.json
curl -s 'https://api.sejm.gov.pl/sejm/term10/prints/3039/3039.pdf' -o print_3039.pdf
```

Keep fixtures small; the tests assert on a handful of stable fields.

### Database schema changes

Append a new SQL script to `MIGRATIONS` in `src/lexinform/adapters/sqlite_repo.py`; never edit an
existing entry. `migrate()` applies missing steps based on `PRAGMA user_version`.

## Pull requests

- One topic per PR, with tests.
- Run `uv run pre-commit run --all-files` before pushing.
- Describe the user-visible effect (a changed message format, a new keyword, a new setting).
