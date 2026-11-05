# Saved batch/application integrity diagnostics

J249 adds a separate read-only `saved-batch-application-integrity-v1` profile:

```sh
uv run --offline --frozen python -m tools.batch_integrity_doctor --database COPY.db --output out/batch-integrity.json
```

The report detects duplicate complete typed application identities and warns about explicitly saved active primary generations that differ from their current stored owner generation. Only queued/submitting primary intents and unconsumed primary items enter that comparison. Consumed rows, historical applications and secondary purposes are not compared to current generations. Missing generation or owner evidence remains unknown; unavailable schemas produce null assessment counts. Findings contain bounded row references, not source or model bodies. The command performs no repair, retry or submission.

Application identity follows the existing generation/phase or explicit target contract. Distinct phases, generations and targets remain valid. A generation-null, target-null row is invalid under both the typed application model and migration 50 CHECK; earlier schemas required a generation. The V2 increment adds a damaged-layout control with two such rows: both are invalid, neither is classified as a duplicate. It changes no production logic.

Unit-first qualification recorded two initial failures. The final focused run passed 55 cases, including 23 new unit cases and no new scenarios. Scoped strict mypy, Ruff and format checks passed. V1 independent review was followed by a nullable-identity question; V2 was independently reviewed CLEAR after the contract/control resolved it. Reviewers did not run tests or edit code. Exact source/full-patch/increment pins are retained here. Root owns the full gate and immutable-state default-report comparison.

This finite profile does not complete DA10.2 by itself. Existing orphan detection covers an item without its batch, while missing-receipt detection groups by request SHA. Exact original provider/batch/custom-ID receipt matching and batch expected-count population checks require separate qualification. Raw blob storage is deferred; metadata archive checks do not prove full traversal or durable raw-body availability. `coverage.json` maps all named requirements to existing commands/tests and states these limits. No plan checkbox is changed by this packet.

Root55focused and8825full tests/295.12s/92%coverage pass; strict mypy760/Ruff/format829green, all829source/config pins exact. [Integration proof](integration-proof.json): all five default doctor objects remain byte-exact, pinned b9 source unchanged. New CLI observes26valid application rows/0duplicates and known-empty active queues; technical controls prove anomaly detection, no natural stale case is claimed. Release follows publication.
