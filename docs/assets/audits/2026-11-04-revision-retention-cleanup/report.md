# Completed amendment revision payload cleanup

Expired confirmed revisions can release their duplicate operational `delivery_json` only
with positive modern evidence: bound rendered content version, frozen snapshots, one
completed application work item and its validated paid-result/projection receipt. The
same binding validator and document-work reader own these checks. Work is revalidated
through retained history after clearing the payload, inside the transaction.

Publication IDs, attempts, message IDs, editorial versions, snapshots, results, receipts
and completed work remain. Uncertain states, recent revisions, missing modern proof and
existing work/digest/continuation dependencies protect the payload. Known corruption
rejects the cleanup atomically, including earlier updates in that cleanup transaction.

The compatible retained-list reader must be actually deployed before publishing this
writer. Switching off a producer cannot repair an old reader that ignores pruned rows.
The previous blanket revision exclusion remains in the reader-only rollback release.

Eighteen unit cases build a minimal state solely through typed models and public
repository methods. They do not use World, services or a scenario factory. One necessary
scenario checks real fake batch/application/delivery, cleanup, retained history, dump/restore,
doctors, repeated collection and the next supported correction. No extra per-status
pipeline scenarios or frozen production fixture are introduced.

```sh
uv run --frozen pytest -q tests/unit/test_revision_retention.py \
  tests/scenario/test_revision_retention.py
```

Pinned state105de577 is restored in memory for a legacy protection control. A deliberately
future cutoff exercises existing retention:157ordinarypayloads/126memos/1batchpayload
compact, while all three legacy revision rows, payloads and listing remain identical.
The mixed SQL reduction25,787,073→13,490,612B is not a saving attributable to this new
revision policy and is not a production cleanup. No natural modern revision cleanup was
observed in this snapshot. Missing modern work is preserved, not backfilled or guessed.

No schema, extraction/selection/comparison behavior, backend or paid purpose changes.
DA6.6 remains partial for its other ownership/retention obligations.

[Qualification](qualification.json), [legacy protection](fresh-legacy-protection.json),
[independent runtime review](peer-review.json).

[Actual compatible reader release](../2026-11-04-revision-retention-reader/release.json)
was verified before this writer was published.
