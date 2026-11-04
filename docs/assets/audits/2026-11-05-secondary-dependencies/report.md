# Shared typed secondary result dependencies

The supplement guard exposed the same dependency gap in five other typed columns: status-change
amendments, joint comparisons, consultation reviews, commission reviews and act-application
reviews. Their modern computation references could name a retained result with an incompatible
purpose. A generic existence/request-hash inventory does not establish that typed relationship.

A shared computation-row decoder checks strict JSON and its six indexed identity columns. One
finite secondary validator then checks result validation, expected purpose/record type and
compatibility between known request hashes. The supplement API and issue codes remain intact
through a thin wrapper. Existing global computation readers retain their prior behavior; this
is a shared decoder for six typed dependency checks, not a global reader migration.

New carriers are exactly `status_changes.amendments_json`, `bills.joint_json`,
`bills.consultation_review_json`, `bills.commission_review_json` and
`bills.act_application_review_json`. Reviews require agreement among the column, projected
review purpose and projected context purpose. Both retained schema-2 `DocumentReviewRecord`
and schema-3 `ReviewResultRecord` are valid. Public saves validate before writes or legacy
preservation; Bill/status-change row readers, scratch restore and doctor share the guard.

Missing computation IDs return before new purpose/type checks. Unknown legacy/request hashes,
original foreign paid owners, zero-accounting reuse, and changed display/context fields remain
valid. No current-owner or answer/context equality is inferred. The bills supplement-number
ledger, names/Mnemonic, memo and publication snapshots are outside this finite contract.
No fields, schema, stored payload size, extraction, selection or source policy change.

Unit-first coverage checks missing, failed, wrong-purpose, request/column mismatches; no-write
save/add and atomic restore; both review representations; wrong review column/context; legacy,
foreign-owner and zero-usage reuse. Root added deeply nested retained JSON to the duplicate-key
control: the strict decoder reports an invalid result instead of crashing the doctor.
No scenario tests are added. Existing legacy writer tests retain real secondary paid results
before exercising modern references; their original parity assertions remain intact.

Historical state105 and freshly fetched state7b208 both preserve the entire doctor report,
double canonical dump/restore and original database bytes. Root additionally compares all508
Bills and201StatusChanges from the fresh snapshot by public serialization: exact before/after.
Natural retained references include one modern amendments record; joint/review rows are legacy.
Constructed modern joint/review tests are not relabelled as completed production cycles.
DA10.2 and DA10.7 remain partial. No provider calls, production processing or external sends.

[Qualification](qualification.json), [state proofs](state-proofs.json),
[public baseline](public-baseline.json), [source pins](source-pins.json),
[independent review](peer-review.json).

Previous supplement release: [exact CI and relay verification](previous-release.json).
