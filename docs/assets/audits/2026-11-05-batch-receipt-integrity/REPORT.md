# Exact paid receipts and batch populations

J250 extends the existing read-only batch-integrity tool with an explicit optional profile:

```sh
uv run --offline --frozen python -m tools.batch_integrity_doctor --database COPY.db --output out/batch-receipts.json --batch-receipts
```

Without the flag, the deployed `saved-batch-application-integrity-v1` object remains unchanged. The optional `saved-batch-receipt-integrity-v1` profile adds two row populations. Paid receipts join strict retained computation identities through the exact provider, batch ID and custom ID; an unrelated item with the same SHA cannot satisfy the edge. Both-known SHA, provider or actual typed item-purpose disagreement is an error. The batch header kind is not compared: mixed batches remain valid. An absent receipt is advisory, while an unknown historical SHA stays unknown. Neither current consumer ownership nor source applicability is inferred.

Batch populations compare each validated stored request count with all retained item rows, including consumed/accounted/forgotten rows. Deficits are advisory; excess rows and invalid counts are errors. Malformed rows remain in observed denominators. Missing schema produces explicit unavailable/null assessments, not an empty population. No missing historical count, receipt or owner is reconstructed. The tool never repairs, cancels, retries or calls a provider.

Technical public-repository fixtures reproduce the four prior diagnostic blind spots: a missing original receipt masked by another same-SHA item; an exact-tuple known-SHA conflict; partial item population; and an empty retained population with a positive expected count. Positive controls cover all ten purposes with consumed/forgotten receipts and different current item owners, plus legacy absent hashes and unavailable schemas. These are technical integrity controls, not corpus gold or production incidents.

The first freeze was preserved; V2 adds two unit-first purpose-conflict negatives (known/unknown SHA) and a genuine mixed two-item batch positive. The author qualification passed 77 focused cases, including 29 new unit cases and no scenarios, plus scoped strict mypy/Ruff/format. The pinned historical source `620f09234a4959278811885b3e757593ae260bdd` retained 13 exact receipts with matching known hashes and 57 matching batch counts across 122 item rows. Its database hash and the entire default report were unchanged. Source pins and compact proof are adjacent. Root owns the full gate, release and DA10.2 acceptance; this packet changes no checkbox and makes no raw-blob retention claim.

Root72focused and8854full tests/298.13s/92%coverage pass; strict mypy762/Ruff/format831green and all831source/config pins exact. [Integration proof](integration-proof.json): five older default reports and deployed v1 byte-exact; source DB unchanged;13exact/SHA-equal receipts,57equal batches/122retained items. Publication/release follows docs verification.
