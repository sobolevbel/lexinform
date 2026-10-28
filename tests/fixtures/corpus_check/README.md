# Offline corpus regression slice

Run from the repository root, with a new output file outside this fixture directory:

```sh
make corpus-check CORPUS=tests/fixtures/corpus_check CORPUS_OUTPUT=/tmp/corpus-check-new.json
```

Profiles are `extraction,selection,comparison,delivery`; choose with `CORPUS_PROFILES`.
The input manifest pins every source SHA, official identity and relation, coverage, initiative,
dataset split, expectation and its basis. Missing manifests, profiles or inputs report `not_run`
and return a nonzero exit status. Results are never replaced. A consumed holdout is recorded as
regression in the result; promote it in a later reviewed input-manifest change before reuse.
Machine baselines are labeled non-independent and cannot constitute a holdout.
Results include the code HEAD, dirty-state flag and hashes of Python sources and `uv.lock`,
so an uncommitted execution is distinguishable from the code at HEAD.

`rcl_779502.docx` is the original official RCL document, SHA-256
`1cd93dcd5a30a4f4655fb69372c18f36e3222a5697f35305f2ad5b5a9f6f54e8`, copied from
`lexinform-corpus/documents/files/779502.docx`; source:
<https://legislacja.rcl.gov.pl/docs//2/12410906/13207357/13207358/dokument779502.docx>.
The URL is provenance, never fetched by this runner.

`rcl_12410906.json` and `print_8_2864.json` retain the existing reviewed fixtures from
`tests/fixtures/document_selection/`. The RCL fixture retains only stage 4 from source
`corpus/12410906.json`, original SHA-256
`6db5d23a0e1c9169ab9d4cd045ccfaf245701c67199ddfa660a165417beafa5c`.
They are official-source cached metadata rather than freshly parsed complete source pages.
`synthetic_law.txt` and the delivery JSON files are self-authored fixtures and are not Polish law.

All cases are regressions. No holdout accuracy or full external-corpus coverage is claimed.
The manifest's exclusions are copied into every result. Comparison reuses the existing
`probe_law_comparison.decide` and `probe_law_mutations.mutations` replay functions. Delivery
reuses three fixed offline scenarios with fake providers and an in-memory repository.
Network connections are blocked while cases execute; no corpus script or model client is run.
The full external corpus is a separate release gate with explicit observational baselines:

```sh
make corpus-check CORPUS=../lexinform-corpus CORPUS_OUTPUT=/tmp/external-baseline-new.json \
  CORPUS_SUITE=external CORPUS_MODE=baseline
```

External baseline success is `recorded`, not a statement of legal correctness. Review its
observations before accepting it as a regression reference. A subsequent comparison requires
that complete baseline and its SHA-256, obtained independently with `shasum -a 256`:

```sh
make corpus-check CORPUS=../lexinform-corpus CORPUS_OUTPUT=/tmp/external-compare-new.json \
  CORPUS_SUITE=external CORPUS_MODE=compare \
  CORPUS_REFERENCE=/tmp/external-baseline-new.json CORPUS_REFERENCE_SHA256=<sha256>
```

Use the same profiles for both runs. Missing inputs or empty requested populations return
`not_run` with exit 2; errors or changed inputs/decisions return a failure. Neither a missing
text cache nor an interrupted run is a complete baseline. The runner records first/final input
hashes and refuses a baseline if inputs change during replay. It never fetches missing files.

The external suite combines raw Word/nested-ZIP extraction, cached RCL candidate selection,
recorded transition comparisons, controlled mutations and the fixed fake-delivery slice.
Standalone DOC/PDF, a complete ZIP inventory, independent legal labels, and separate Senate,
process-evidence and policy inventories remain outside its coverage. Transitions of kind `rpw`
are excluded by the existing comparison replay; `rpw-without-rule` transitions are retained.
All external observations are machine baselines in the regression split. The input manifest
suite above remains the independently packaged CI slice.

The opt-in release suite runs a finite set of existing registry commands on one external corpus:

| Profile | Required job IDs |
| --- | --- |
| extraction | external-extraction, pdf, word, odt, scan, input, cost, amendments |
| selection | external-selection, candidates, admissibility, review-admissibility, process, senate |
| comparison | external-comparison |
| delivery | external-delivery |

The external jobs fix their named profile and baseline mode. PDF uses the adapter page reader;
Word uses `notes-parts`, ODT uses `adapter`, input uses `compare`, cost/amendments use mapped
inputs. The remaining suite IDs match their job IDs. Online collectors, human-review workflows
and maintenance commands are outside this matrix. Delivery runs the three existing synthetic
recovery invariants through the external suite, so it uses the same corpus root as other jobs.

Supply a reviewed JSON plan and its SHA explicitly:

```sh
make corpus-check CORPUS=/absolute/corpus CORPUS_OUTPUT=/tmp/release-new.json \
  CORPUS_SUITE=release CORPUS_PROFILES=delivery CORPUS_MODE=baseline \
  CORPUS_PLAN=/absolute/release-plan.json CORPUS_PLAN_SHA256=<sha256>
```

The strict version-1 plan has `index_sha256` and `jobs`. Each job has `id`, `suite`, `args`,
nonempty `pins` (`path`, `sha256`), positive `expected_observations` and `expected_ids_sha256`.
Use absolute nonsymlink pin paths. Every artifact argument (`--cases`, `--catalog`, `--pdf-cases`,
`--inventory`, `--manifest`, `--reference`, `--derivatives`) needs a matching pin; existing
SHA arguments must equal it. Required file/options remain those of the underlying replay.
Paths, suite, profile and fixed modes cannot be overridden or abbreviated.

Expected counts and IDs come from an explicitly accepted regression population. The IDs hash is
SHA-256 of UTF-8 `json.dumps(sorted(observation_ids), separators=(",", ":"))`.
For `external-delivery`, the observation ID is `delivery`, containing all three invariants.
The plan records source/reference artifacts, not independently verified legal labels.

Outputs are a new summary JSON and a new adjacent `<output-name>.runs/` directory of immutable
registry children. Keep both together. Baseline success is `recorded`; compare additionally
requires `CORPUS_REFERENCE` and `CORPUS_REFERENCE_SHA256`, the same plan and profiles, intact
child manifests and unchanged semantic observations/input populations. Only then is it `passed`.
Missing jobs, artifacts or complete populations are `not_run` (exit 2). Corruption, changed
inputs/code or replay differences fail (exit 1), taking priority over missing populations.
No missing corpus files are fetched. Existing raw/cache/format exclusions remain applicable;
this runner does not replace independent labels, paid evaluations or production acceptance.
