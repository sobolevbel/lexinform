# Offline measurements

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
