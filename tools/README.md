# Offline measurements

## Shared corpus code

`corpus_runtime` owns read-only/network guards and code provenance; `corpus_artifacts` owns
artifact hashing, credential-file rejection and exclusive JSON publication. `corpus_rcl`
owns historical stage construction and cached-text adapters. Replays import these modules
directly; old entry points retain compatible re-exports. Domain selection stays in lexinform.
Low-level helpers do not load test suites or World just to calculate a hash.

## Run registry and decision diffs

Run from the lexinform checkout. Each ID creates a new directory; existing runs are never reused.

```bash
corpus_path=/path/to/lexinform-corpus
run_root="$PWD/out/corpus/runs"
uv run --offline --frozen python -m tools.corpus_runs run \
  --root "$run_root" --id new-health --suite health --corpus "$corpus_path" -- --check-text
uv run --offline --frozen python -m tools.corpus_runs verify "$run_root/new-health"
uv run --offline --frozen python -m tools.corpus_runs register \
  --root "$run_root" --id old-health --suite health --artifact /path/to/old-health.json
uv run --offline --frozen python -m tools.corpus_runs diff \
  "$run_root/old-health" "$run_root/new-health" --output "$PWD/out/corpus/new-health-diff.json"
```

The directory contains started command/options, a snapshot of Python sources and `uv.lock`, the
child log, results, recursive artifact hashes and a sealed manifest. `verify` detects absent or
changed artifacts. Registration preserves an old report's producer provenance; it does not claim
that current code generated it. Diffs separate observations, input identities and coverage.
`recorded` means completed observation, `passed` means declared assertions passed, and `not_run`
means an incomplete requested check. An empty population never becomes a completed run.

Offline suites: `health`, `ci`, `external`, `pdf`, `word`, `input`, `cost`, `amendments`,
`reextract`, `annotations`, `empty-review`, `physical`, `holdout`, `review-form`, `review`,
`candidates`, `process`, `senate`, `scan`. Extra arguments follow `--`;
each child module's `--help` lists its required pins/options. `external` uses the existing
corpus-check profiles; the default `ci` suite requires a manifest corpus such as
`tests/fixtures/corpus_check`. Process/Senate wrappers preserve old observation semantics and
record missing inputs. Senate's default supplement is
`sejm/supplements/2026-10-16-senate-resolutions` inside the declared corpus.
Candidate results describe available cached views; consult `missing_inputs` and
`complete_population` before interpreting them as coverage. Raw extraction, scan completeness
and legal correctness still require their own checks. The registry supplies no independent oracle.

`out/` survives temporary-directory cleanup but is ignored by Git and is not a backup. Keep the
whole important run directory, its referenced inputs and original reports when copying elsewhere.
Storage backend and automatic retention are intentionally undecided.

## Request provenance doctor

```bash
uv run --offline --frozen python -m tools.request_doctor \
  --database /path/to/checkpointed-copy.db --output "$PWD/out/doctor/report.json"
```

Use an isolated SQLite copy with its connections closed. The reader opens it with
`mode=ro&immutable=1` and refuses a nonempty WAL/rollback journal so it cannot silently omit
uncheckpointed data. It writes only a new report; existing reports and the database cannot be
overwritten. Input SHA and WAL/journal state are checked before and after reading, including
a writer starting after preflight. No migrations, repair, provider
calls, submissions or `.env` reads are performed.

The JSON lists recorded input SHA groups, batch/result references, retained intent checksums
and findings. It covers current/ready bill results, reviews, supplements, status changes,
memo and frozen delivery snapshots. Result counts are references, not unique paid calls.
Malformed JSON/SHA, payload checksum mismatch and an orphaned batch item are errors. A recorded
result without a matching batch receipt is a warning. Repeated identical input and unknown
legacy provenance do not establish duplicate billing, corruption or complete historical reading.
An observed legacy payload hash stays separate from its absent recorded identity.

Exit codes: `0` recorded inventory, `1` detected errors, `2` incomplete/no population or invalid
arguments/input. `recorded` is not a full-state health verdict. Blob coverage, source selection,
generation ownership and immutable result/application history remain outside this first doctor.
Payload and legal text are not copied into the report.

## Unfinished-work doctor

```bash
uv run --offline --frozen python -m tools.work_doctor \
  --database /path/to/checkpointed-copy.db --output "$PWD/out/doctor/work.json"
```

Both doctors use `tools.sqlite_snapshot` for the same immutable reader, journal refusal and
before/after checksum check. The work report lists document/review owners, recorded times,
attempts, frozen-input availability, linked publication states and owner memo counts. It detects
missing bill/change dependencies, conflicting owners/schedules, invalid saved JSON and duplicate
review identities. Missing legacy snapshots or publication links are warnings; keys and inputs
are never reconstructed. Empty legacy review ownership remains visible because it can still
protect memos under the current retention rules.

The report contains no legal context or provider payload. It does not refresh sources, evaluate
current ELI applicability, repair state or submit work. Linked publications do not prove the
configured channel or revision eligibility. Owner memo counts overlap and cannot be added or
used as a deletion list. Exit codes match the request doctor; no saved owner rows means `not_run`.

## Computation graph doctor

```bash
uv run --offline --frozen python -m tools.computation_doctor \
  --database /path/to/checkpointed-copy.db --output "$PWD/out/doctor/computations.json"
```

This uses the same checkpointed reader and the saved-record walker shared with request doctor.
It validates normalized computation/application records against their SQL identities and follows
known result refs in current/ready bills, reviews, memos, status changes, frozen deliveries,
document work and batch metadata. Missing results, inconsistent recorded request SHA and applying
a failed result are errors. It preserves the distinction between original input, collection owner
and application target, including term/generation changes and free reuse.

Legacy refs/checksums stay unknown. Application target kinds/refs remain opaque; this report does
not execute or resolve them, compare old receipts to today's generation, or establish legal
applicability, billing or complete state health. It contains identities and links without copying
answers or legal context. Exit codes match the other doctors; absent history tables or no observed
records produce `not_run`.

## Exact scan-input windows

```bash
uv run --offline --frozen python -m tools.replay_scan_inputs \
  --corpus "$corpus_path" --output "$PWD/out/corpus/new-scan-inventory.json" --inventory
uv run --offline --frozen python -m tools.corpus_runs run \
  --root "$run_root" --id new-scan-inputs --suite scan --corpus "$corpus_path" -- \
  --manifest "$PWD/out/corpus/new-scan-inventory.json" --manifest-sha256 SHA
```

Inventory includes saved Sejm PDF attachments, empty cached RCL PDFs and RPW PDFs with raster
pages. Replay probes both cover-letter settings and eight-page triage, preserving the old byte,
page and truncation observations for comparisons. New manifests distinguish source SHA/size
from attached SHA/size and map contiguous input pages to the original PDF. Content/XObject
streams, page boxes and rotation are checked against the declared source window; pixels are not
rendered. Raw-derived probes cover dates, amounts, negations, scope and references.

For before/after comparison, run with the same pinned inventory and `--reference PATH
--reference-sha256 SHA`, or compare two recorded registry runs. Keep an exported baseline's
source archive and its SHA: `loader_sha256` identifies the actually imported loader. Existing
byte/page limits remain explicit outcomes. This does not judge legal selection, OCR quality,
complete attachments or source retention, and makes no network or model calls.

## New derivative generations and annotation queue

```bash
health_path="$run_root/new-health/result.json"
health_sha=$(shasum -a 256 "$health_path" | cut -d ' ' -f 1)
uv run --offline --frozen python -m tools.corpus_runs run \
  --root "$run_root" --id new-empty-generation --suite reextract --corpus "$corpus_path" -- \
  --health "$health_path" --health-sha256 "$health_sha"
```

The checked health inventory pins the cohort, document index and old empty caches. Re-extraction
uses a bounded extractor and writes deduplicated gzip objects under this run's result directory.
Raw and extracted hashes, options, library versions, observed parts and exclusions remain explicit.
Originals, caches and machine labels stay unchanged. After an append-only index update, use
`--index-snapshot /path/to/old-index.json` to replay against the exact pinned index bytes.
New installations save those bytes as `index-before.json` in their installation run.

Add `--cache "$PWD/out/corpus/extraction-cache"` to reuse bounded extraction across runs.
Entries are keyed by original SHA, extractor contract/options, Python/library versions and runtime
source hashes. Every hit verifies identity and result hashes; corrupt entries fail explicitly and
are never replaced. All source URL/role observations remain separate. A cache is optional and stays
outside the corpus; the default reader and historical caches are unchanged.

Pass `--derivatives /path/to/generation/result.json --derivatives-sha256 SHA` to the `candidates`
or `annotations` suite to explicitly read a new generation. The shared reader checks both raw
source identity and derived text identity; the default view continues using original caches.
`annotations` also requires `--health` and `--health-sha256`. It writes a local HTML viewer and a
pending JSON template with source ranges, relation groups and reviewer/basis fields. Its evidence
validator requires literal spans and rejects consumed or agent-reviewed cases as independent
holdout. Creating a queue never creates gold labels; readers must inspect the original where the
excerpt is insufficient. Full relation/span adjudication and a fresh human holdout remain separate work.

The `empty-review` suite takes `--generation PATH --generation-sha256 SHA` and optionally `--previews`.
It pins every still-empty original, inventories bounded archive leaves, records PDF page text hashes
and readable unselected parts, and writes JSON/HTML review material. Previews render only page 1 of
each distinct PDF leaf with Poppler. A textless page asks for original review before OCR; an archive
of readable letters can have an empty bill selection. Machine kinds do not resolve roles, annex
requirements or initiative relations. These diagnostic cases stay consumed regression material.

The `holdout` suite takes `--baseline-index PATH --baseline-sha256 SHA --inventory PATH
--inventory-sha256 SHA --source-run DIR --source-sha256 MANIFEST_SHA`. It reserves newly collected,
unprinted non-government RPW groups, excluding all initiatives in the pinned historical index,
original-byte aliases and size-matching direct ZIP members. It also excludes aliases of newly
collected regression files. Its JSON/HTML contains metadata and original links, without reading
legal text. `holdout_pending` becomes a usable holdout only after a human confirms relations and
records actual read scope/evidence; neither a small purpose-selected cohort nor byte independence
establishes independent legal correctness.

## Recording human review

```bash
queue="$run_root/c006b-human-holdout"
queue_sha=$(shasum -a 256 "$queue/manifest.json" | cut -d ' ' -f 1)
uv run --offline --frozen python -m tools.corpus_runs run \
  --root "$run_root" --id new-review-form --suite review-form --corpus "$corpus_path" -- \
  --queue "$queue" --queue-sha256 "$queue_sha"
answers_sha=$(shasum -a 256 /path/to/review-answers.json | cut -d ' ' -f 1)
uv run --offline --frozen python -m tools.corpus_runs run \
  --root "$run_root" --id new-review --suite review --corpus "$corpus_path" -- \
  --queue "$queue" --queue-sha256 "$queue_sha" \
  --answers /path/to/review-answers.json --answers-sha256 "$answers_sha"
```

`review-form` accepts a sealed `holdout`, `empty-review` or `annotations` queue and writes
`result.html`: links to originals, unpacked archive members and verified page-1 previews, page
counts, closed answer vocabularies and quote fields. Open it from disk; the draft stays in the
browser and "Сохранить ответы" downloads the answers JSON. The form reads no legal text.

`review` validates every answers file before writing anything: the queue SHA, a named reviewer,
the declared read pages or text ranges, and at least one quote for each answer other than
`unknown`. A `text_layer` quote must occur on that PDF page or in the Word/ODF text; a `visual`
quote is bound to a declared page but not to a text layer. A text quote without offsets must
occur exactly once inside the declared range. One rejected case rejects the submission.
Only human reviews form labels; agent reviews stay alongside as observations. Disagreeing humans
stay `conflict`. A reserved case becomes `holdout` only when humans agree on
`independent_initiative: yes` and no agent reviewed it; previously consumed queues stay
`regression`. Several `--answers`/`--answers-sha256` pairs record several reviewers in one run.

## Explicit public collection

These entry points allow public source requests only when `--allow-network` is present.
They are not runnable through an offline registry suite. No credentials, models or state DB are used.

```bash
uv run --offline --frozen python -m tools.corpus_collect download \
  --allow-network --corpus "$corpus_path" --root "$run_root" --id new-rcl-gaps \
  --health "$health_path" --health-sha256 "$health_sha"
uv run --offline --frozen python -m tools.corpus_collect install \
  --corpus "$corpus_path" --root "$run_root" --id new-rcl-install \
  --source-run "$run_root/new-rcl-gaps"
uv run --offline --frozen python -m tools.corpus_snapshot download \
  --allow-network --root "$run_root" --id new-sejm-metadata --term 10 \
  --reference "$corpus_path/sejm/snapshots/2026-10-03/term10"
uv run --offline --frozen python -m tools.corpus_snapshot publish \
  --source-run "$run_root/new-sejm-metadata" --corpus "$corpus_path" --name YYYY-MM-DD
```

RCL collection executes the nonempty missing-original plan from a pinned health report. Optional
`--consultations /path/to/download-status.json` retries its explicit failed URLs. Downloads check
HTTPS hosts/redirects, complete responses, byte/time limits and attachment signatures; HTML
refusals stay unavailable. Installation verifies the sealed source run, never replaces existing
bytes, preserves old index records, saves the before-index and uses an advisory lock plus content
check before atomic index publication. Identical repeat installation records `already_installed`.

Sejm collection paginates fresh `prints`, `processes`, `bills` listings and records request
provenance plus added/removed/changed metadata against the chosen reference. Publication creates
one new dated snapshot. Listings do not refresh process detail trees, supplementary catalogs or
RPW attachment bytes. Select further downloads by the cases that need them.

`python -m tools.corpus_sejm_sources catalogue` derives a dated supplementary catalog from pinned
`prints.json` and requires explicit `--rpw ID=purpose` or `--supplement ID=purpose` cases. It also
accepts `--reserve-rpw ID=purpose` for raw-only pending human cohorts. `download --allow-network`
fetches only those required attachments using the common bounded public HTTP reader. `install`
verifies the sealed source run, preserves old RPW records and creates raw/cache/record files only
when absent or byte-identical. Reserved sources are excluded from extraction and installation.
`publish-catalogue` adds `supplements.json` and its provenance to an existing dated Sejm snapshot.
Each operation records its repeat command; HTML refusals stay unavailable. Consult `--help` for
paths and required run IDs. Historical snapshots and download resume keys are preserved.

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

The `physical` suite extends the observation to five raw families, their saved text caches and
catalogued Senate sources. It hashes original bytes, checks bounded PDF/ZIP/OLE/XML containers,
ZIP CRC and gzip/UTF-8 caches, and reports missing recorded acquisitions, limits and unsupported
formats. Parser failures remain separate from demonstrated corruption. PDF rendering, nested ZIP
validation, full OLE/RTF decoding and legal completeness are explicit exclusions. `recorded` means
that the inventory completed, not that every source is valid or every required input is available.

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
