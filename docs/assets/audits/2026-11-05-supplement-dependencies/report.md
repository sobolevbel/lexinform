# Typed supplement result dependency validation

A modern supplement in `status_changes.supplements_json` could point at an existing validated
computation of another purpose. The generic reference inventory checked existence and request
hashes, but did not check the result's purpose. The same saved reference could therefore appear
healthy while naming an incompatible paid result.

One shared adapter validator now checks this typed edge for public add/save/get/list, scratch
restore and the computation doctor. A nonnull computation ID must resolve to matching SQL/JSON
identity columns, a validated `supplement` computation carrying `SupplementRecord`, and a
compatible request hash when both sides know it. The doctor reports bounded issue codes with
the exact status-change row and supplement index. Restore rejects a bad dependency before
replacing the destination database; public saves validate before legacy-preservation writes.

The validator does not infer unknown IDs or checksums, compare the current owner with the
original paid owner, or require identical display fields or answer dictionaries. Delivery and
reuse can legitimately change those fields. Legacy records without computation IDs remain
unbound. `bills.supplements_json` is a seen-number ledger and is deliberately outside this
contract. Memo, publication snapshots and other secondary record types retain their existing
checks. This stage does not claim complete DA10.2 or source-family lifecycle acceptance.

Nine new unit cases cover missing, invalid, failed, wrong-purpose, request-mismatched and
column-mismatched dependencies, public roundtrip, atomic save/restore rejection, and valid
foreign-owner/missing-hash behavior. No scenario tests are added. Root focused tests include
existing repository and computation-reader tests; the full gate checks existing lifecycles.

Root independently compared historical state `105de577` and freshly fetched state `7b2084b1`.
Both contain 12 typed supplement records and one modern result reference. Public reads remain
exact; the entire computation-doctor report matches its pre-change baseline, with zero errors
or warnings. Double canonical dump/restore is exact; both original database files remain
unchanged. No schema, serialized payload, extraction, selection, provider or delivery change
is introduced. The isolated restores perform no production processing or external sends.

[Qualification](qualification.json), [state proofs](state-proofs.json),
[source pins](source-pins.json), [independent review](peer-review.json).

The first full gate exposed four existing modern-reference fixture cases using nonexistent
result IDs. The fixture now retains real typed secondary computations with original foreign
paid ownership before exercising inline writes. Original parity assertions stay intact; no
production check was relaxed. The final unchanged-source full gate supersedes that run.
