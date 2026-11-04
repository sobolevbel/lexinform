# Literal claims in a supplied frozen context

The bridge links an already checked unique literal source quote to a supplied frozen
primary context. It is pure and offline; no runtime consumer, model schema, stored record,
prompt or reader payload changes. Source quote evidence remains the existing public result.
See the [contract and meanings](../../../reviews/2026-11-04-primary-analysis-evidence.md#frozen-context-retention).

## Corpus evidence

The extended versioned replay preserves the pinned J213 baseline exactly: 404 DOC paths,
246 raw SHAs, 405 input hashes, all source observations/counts, 558 quote parity checks
and 325 meaningful negative controls across dates, amounts, negations, scope and references.
Twelve no-Art sources and one unreadable source retain their explicit exclusions.

A separate deterministic sample uses `documents/files/396900.doc` (73,975 extracted chars).
Eleven synthetic frozen-input controls give two retained, four omitted and five unknown
results. They cover whole/cut text, generated copies/gaps, repeated mappings, prefix maps,
raw-file/request/source-text mismatches. Literal retained slices and source coordinates are
checked independently. All source inputs are unchanged; the guard blocks network and corpus
writes. Reports contain hashes, coordinates and statuses, not source bodies or quotes.

The selection and review roles and frozen requests are controlled synthetic fixtures over
real extracted text. This is one sample of mechanical mapping, not corpus-wide input-map,
legal-role, semantic or paid-history acceptance. No provider is constructed or called.

## Recheck

With the project environment and `PYTHONPATH=src:.`, run:

```sh
python tools/replay_primary_claims.py \
  --corpus /path/to/lexinform-corpus --output /new/report.json
```

The tool checks its pinned DOC manifest, original review matcher source and compressed
J213 parity report. Output must be new and outside the corpus. Use a repository checkout
containing the pinned git history, or provide its `GIT_DIR` when using an isolated archive.

[Qualification](qualification.json), [corpus report](corpus-report.json.gz),
[independent review](peer-review.json).

[Actual CI and relay release](release.json).
