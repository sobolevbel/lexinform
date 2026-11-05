# Immutable amendment input reader A

One additive migration67 introduces a dedicated immutable canonical text-question table. Optional input refs are omitted when None. Actual question text is retained without a new byte admission cap; capture/preparation/correction/Poster remain unchanged and capture is OFF. No raw-file/image archive or external backend is chosen.

Strict shared storage/read/list/intent/item/result/restore/doctor checks preserve exact IDs, request binding and known parents. Original source receipts remain original lineage; a new request cannot impersonate an old receipt. Derived lineage validates the original parent fully in one bounded level. Root availability resolves only its original frozen amendment result; a later refetched revision never fills the root's missing input. Findings hash damaged keys and do not expose question bodies. Known refs in table-less old schemas are diagnosed; ordinary old reference-free schemas stay compatible. Atomic restore was already safe and remains so.

[Independent V2 review](peer-review.json) is CLEAR. Four review findings have meaningful controls;30newunits/0newscenarios and the existing migration test extension pass. Root also closed one new test connection explicitly, without changing runtime pins or test IDs.

[Root integration](integration-proof.json):8884tests/314.48s, strict mypy766/Ruff/format835 and all835source/config pins pass. The11runtime candidate pins are exact; the test-only closing delta is recorded separately.

[Fresh migration proof](migration-proof.json) uses state6c93feca: schema66→67 preserves all48old tables/rowids and JSON of509Bills; new table empty, exact double restore, original source unchanged. [Seven diagnostic profiles](doctor-comparison.json) differ only by validated schema/file SHA. [Storage](storage-proof.json):+246SQL/+91gzip bytes and+8192SQLite file bytes after double restore, zero body rows. These schema-only measurements are not a production-body forecast.

The [native-byte baseline](question-baseline-boundary.json) has14text/8scan paths with controlled prior/proposal fields and120kchar budget. A [1.5M-budget subset](production-budget-boundary.json) has no exact prior-owner matches and no real proposal/recommendation coverage. Neither establishes a universal body bound; the arbitrary128KiB proposal was rejected. Writer activation, real comparison-field qualification, pending/history pins and populated SQL/gzip growth remain future work. No DA7 checkbox closes here. Publication/release follows the root docs gate.

[Reproduction recipe](recipe.md) and [immutable source manifest](source-manifest.json) pin the local-copy comparison; raw dumps, Bill bodies and questions are not published.

Actualdc7d2f27 [CI and relay release](release.json) verified after publication; no workflow rerun.
