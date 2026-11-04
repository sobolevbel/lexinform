# Offline primary claim evidence

This stage selects the bounded claim classes in the [scope contract](../../../reviews/2026-11-04-primary-analysis-evidence.md).
The first pure validator checks only a supplied `key_changes[index]` envelope with exact
claim/source text hashes and the existing `EvidenceCitation`. It does not modify live
model schemas, prompts, stored analysis records, reader rendering or database state.

The shared review quote/locator matcher is extracted without changing its policy. The
primary wrapper additionally checks source identity/role/hash and address/hash of the
claim. Missing evidence/source, duplicate identities, mismatched bindings and ambiguous
quotes remain explicit. All overlapping whitespace-normalized occurrences are counted
with constant extra memory. Literal Python string offsets are reported only for a unique
literal match; block and paid-input origins remain unmapped.

A true but irrelevant quote is still `semantic_not_assessed`. Supplied incomplete sources
remain partial. These mechanical results cannot establish legal scope, a commencement
date, an old/new normative change, adoption or actual support for the conclusion.

## Reproduce the bounded corpus check

```sh
uv run --frozen python tools/replay_primary_claims.py \
  --corpus /path/to/lexinform-corpus \
  --output /path/to/new-primary-claim-report.json
```

The tool pins the existing manifest and INDEX, all raw hashes, and the old review module
from commit `62f028fa11acce3aa2654b794428d74a9ca08bb7`. Only its exact fixed-hash function
is compiled after AST equality verifies every other review module node. Corpus inputs
are read-only, network access is blocked, code/input drift fails, and output is create-only.
An output symlink is rejected before resolving it or reading inputs.

The pinned DOC cohort covers 404 paths and 246 unique source hashes. Of these, 233 have
an eligible Art. excerpt; 12 lack a suitable locator and one is unreadable. The frozen old
matcher, current wrapper and common helper agree in 558 comparisons: 233 positive
(232 unique, one ambiguous) and 325 absent quote derivatives. Derivative coverage is date147,
amount1, negation77, scope13 and reference87; ineligible cases are reported individually.
All 405 raw/INDEX input hashes remain unchanged. Amount coverage is one mechanical case,
not broad legal or financial acceptance.

Role, context and claim bindings around these natural texts are synthetic technical
controls. They are not natural primary-model citations, legal source classifications,
independent annotations, entailment labels or completed production cycles. Reports retain
counts, hashes and provenance only, without quotes or source bodies.

[Qualification](qualification.json), [corpus summary](corpus-summary.json),
[pinned corpus report](corpus-report.json.gz), [independent review](peer-review.json).
