# Offline census of saved computations

On a local copy of the checkpoint, the computation doctor can now count saved application
receipts by phase and reason. It separates invalid rows and splits projections by whether
their result has a recorded input binding. This counts persisted applications. It does not
measure lookup attempts, the cache hit rate or provider charges.

The primary claim audit inventories four explicit fields in canonical primary computation
results. Its output uses immutable result IDs, field positions and SHA-256 hashes. It records
legacy citation absence separately from semantic assessment. It excludes duplicate carriers
and pre-ledger answers. The audit supplies a denominator for future independent review.

```sh
uv run python -m tools.computation_doctor --database snapshot.db --output receipts.json --projection-receipts
uv run python -m tools.audit_saved_primary_claims --database snapshot.db --output claims.json
uv run python -m tools.benchmark_state_checkpoint --dump snapshot.sql --output checkpoint.json --iterations 3
```

On the pinned state copy, the previous doctor report was unchanged after removing the opt-in
census. The 28 saved applications include 12 collection records, 9 projections from a batch
result and 7 from a retained result. The 12 validated primary answers had 117 nonblank claim
slots. Two claim audits emitted identical bytes and the input SQLite file was unchanged.

The existing local checkpoint benchmark replayed eight checkpoints with synthetic report
mutations. The initial dump was 5,276,152 bytes in gzip. The measured SQL growth was 5,178
bytes and gzip growth was 162 bytes. Hosted latency and production growth remain unmeasured.
[Qualification and scope](qualification.json).

[Exact CI and relay verification](release.json).
