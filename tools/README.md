# Offline measurements

## RCL corpus health and repair queue

```bash
mkdir -p out/corpus
uv run --offline --frozen python -m tools.corpus_health \
  --corpus /path/to/lexinform-corpus \
  --output out/corpus/new-rcl-health.json --check-text
```

The report inspects every indexed RCL original/cache and references selected from historical
self-amendment candidates. It distinguishes missing originals from missing/broken derived text,
empty text and indexed availability. Actions carry public URLs, candidate fields and stage IDs;
they are recommendations, never executed repairs. `--check-text` validates bounded gzip/UTF-8
reads; without it, existing caches are explicitly unchecked. Raw bytes are not parsed or hashed.
Code and read-input hashes are recorded, changes during inspection are rejected, and network,
corpus writes and `.env` reads are blocked. Existing outputs and symlink inputs are refused.
This scope excludes other uncollected catalog entries, Sejm/RPW/Senate and legal correctness.
Keep reports in a durable project directory rather than `/tmp`; `out/` remains Git-ignored.

Provider token-count probes and the full test gate also need the `tiktoken` vocabulary cache.
`uv --offline` controls dependency resolution, not a library's first vocabulary download.
Warm a persistent local cache once with network access, then set the same path for offline runs:

```bash
TIKTOKEN_CACHE_DIR="$PWD/.cache/tiktoken" uv run --frozen python -c \
  'import tiktoken; print(tiktoken.encoding_for_model("gpt-5.1").name)'
TIKTOKEN_CACHE_DIR="$PWD/.cache/tiktoken" uv run --offline --frozen pytest -q
```

The loader verifies its expected vocabulary SHA. This is a public data download, not a model
call; `.cache/` is ignored and no setting or secret is added to `.env`.

`measure_delivery_state.py` is a development-only benchmark requiring `tests.harness`,
the development dependencies and the checkpointed single-card revision producer.
From the repository root, after installing dependencies:

```bash
uv run --offline python -m tools.measure_delivery_state --edits 10 --quiet-runs 3 --output /path/to/new-result.json
```

It creates one synthetic production-profile World over fakes and temporary local Git remotes.
It records code/fixture hashes, whole SQLite/dump/gzip sizes, reachable Git history and local
timings through confirmed single-card edits, quiet runs and 91-day retention. An identical
no-edit control and failed/unknown forks check retention and fresh-clone recovery. External
sockets and non-file Git transports are blocked; credentials and `.env` are not used.
Output must be new. Local timings and this bounded sample do not predict production growth
or GitHub network latency. Temporary databases and repositories are removed after the run.
The failed/unknown forks deliberately log simulated delivery failures; a successful run ends
with the measurement path, and the JSON records the retention checks.

## Available corpus files

```bash
uv run --offline python -m tools.measure_corpus_files --corpus /path/to/lexinform-corpus --output /path/to/new-inventory.json
```

This read-only inventory hashes files in five explicit corpus roots: RCL documents, Sejm
term-10 prints, filings, self-amendments and RPW. It records each path/hash/size, totals by
family and extension, global exact-byte deduplication and cross-family aliases. ZIP files
remain physical containers; no members are extracted. Duplicate totals across families
cannot be added to obtain the global total. Missing roots are explicit; no available files
is an error. Symlink inputs and existing outputs are refused; output must be outside the corpus.

An inventory of available files does not establish production consumption, freshness, legal
role, file validity or scan/text coverage. Research derivatives, cached text, the old archive,
Senate HTML and ELI metadata are outside this raw-file measurement. Retention, raw compression,
backup replication, daily growth and backend cost need separate measurements. No archive is
created, no storage policy changes, and no network, model or production database is used.
