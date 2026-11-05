# Compatible compact-card history reader

Card history can represent a genuinely absent change event. Nullable card versions require the
explicit `continuation-v1` context profile; no fake StatusChange is introduced. Existing amendment
JSON, IDs, snapshot references and confirmation rules remain compatible. The single appended
schema66 migration permits the nullable reference while preserving every old row and rowid.

The shared reader checks typed SQL/JSON identities, declared snapshot and predecessor references,
FKs, the actual frozen physical plan, canonical single-part target and previous binding. Unbound,
malformed, uncertain, multipart and inherited operations suppress a stale current claim. Numeric
string target IDs accepted by typed plans are included conservatively. Historical government
rehome preserves old frozen context; the reader does not compare it with today's Bill owner.
Legacy cards without captured history remain unknown.

Compact context uses the exact existing continuation projection and editorial work stripping,
shared through a pure module and the existing service re-export. The profile fixes field/default
and projection semantics for future compatible readers. It preserves commission_review as before.
The compact snapshot is verified against the actual frozen plan. Integrity, render/physical
history and full-context availability remain distinct: default semantic restoration refuses this
compact profile, even if Bill defaults could construct an object. Cleanup can preserve valid
history without pretending that full original analysis is available for a future correction.

This avoids requiring a second full Bill snapshot: the largest qualified natural RCL example is
2,362,572 bytes, while its canonical continuation context is 2,621 bytes. This is a measured sample,
not a universal bound. The exact original rendered payload remains available to a future writer.
Reader activation itself records no card versions, snapshots or bindings; the writer remains OFF.

The byte-frozen public driver was recorded before projection changes. Root repeats fourteen
fixtures from three deterministic actual Bills, with controlled mnemonic/unsupported markers,
fixed Clock and fake transport. All physical operations, rendered text, source Bill, request and
result rows, IO, repeated polls and restore decisions match the baseline. Two rehome repeats are
explicitly inapplicable; twelve other repeats preserve the dump exactly. Model calls/results are
zero. The committed driver is in default mypy coverage. No legal gold or natural paid cycle is
claimed. After replay, only three test-contract files changed; runtime pins remain exact.

On the fresh620f092 state copy, all48 table rowid/row hashes and old versions/snapshots/bindings
remain exact; declared FKs and double restore pass. The default computation doctor is unchanged
apart from schema/database headers, and history findings stay identical across migration. Reader
migration changes the canonical dump by -5 SQL bytes/+7 gzip bytes, with zero new content rows.
Production state is not modified. This does not forecast writer growth.

The initial full gate found three outdated test contracts: two pre-change report hashes included
schema65, and one scenario attempted to restore intentionally corrupt history. The two tests keep
the original semantic checksum with an explicitly normalized schema65 header and separately assert
the actual current schema. The scenario now verifies atomic restore rejection, unchanged UNKNOWN
delivery and safe optional-reader failure through the public port. No restore validation is weakened.

[Qualification](qualification.json), [migration proof](migration-proof.json),
[corpus proof](corpus-proof.json), [frozen OFF baseline](off.json.gz),
[root pre-reader replay](root-before.json.gz), [paired replay](paired.json.gz),
[explicit test-only pin changes](replay-test-pins.json), [test contract proof](test-contract-proof.json),
[source pins](source-pins.json), [independent review](peer-review.json).

Reproduce with `tools/replay_card_history.py` and the pinned schema65 canonical state SQL
(SHA04b69acd02f46522f32ff02cd13a91590d44aacc4083f2c6f82ce05e4995a453). Decompress `off.json.gz`
into a separate temporary JSON before passing it through `--baseline`; the driver accepts raw JSON.

Previous release: [exact CI and relay verification](previous-release.json).

[Root logging overlay proof](logging-overlay-proof.json) confirms that the newer verified correlation implementation is preserved.

Final full gate: 8413 tests in 308.878 seconds, 92% coverage, strict mypy over736files,
Ruff and formatting green. All805 source/config pins remain exact. [Case-set comparison](new-cases.json)
confirms38newunit cases, zero new scenarios and zero removed logical cases. The unknown-schema
boundary parameter advances from66 to67. State620f092 was re-fetched before publication.
DA7 remains partial; 74total/44complete/6explicitN/A/24applicableopen.

Actual reader release: [exact CI and relay verification](release.json), commit6fa7c906.
