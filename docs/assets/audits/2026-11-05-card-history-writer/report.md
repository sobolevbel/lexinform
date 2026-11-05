# Prospective compact card history writer

Fresh single NEW_BILL roots now retain a compact continuation-v1 snapshot, semantic plan, exact
render and content binding atomically with their frozen outbox operation before any API call.
Single card edits retain a revision only from a known confirmed bound predecessor. Existing
physical kinds, completion, retries, attempts and operator decisions stay unchanged. Actual
formatter language comes through a read-only formatter/publisher capability; RU/EN are known,
unsupported language stays unbound. No text inference or fake StatusChange is introduced.

Capture is explicitly prospective. Legacy unbound, inherited, unsupported and multipart scopes
remain unknown and create no orphan snapshots. Default recorder calls do not backfill cards.
Repeated polls preserve existing IDs and dumps. Original and edit contexts share the existing
canonical projection rather than copying a full Bill or source document.

The full gate exposed a genuine lifecycle incompatibility: supported forget/republish removes a
physical publication while immutable history remains. Retained reads, restore and doctor now
separate physical availability from intrinsic snapshot/profile/predecessor integrity. Missing
physical evidence is unavailable, current stays None, and doctor reports a warning. Surviving
contradictory targets or bindings remain errors. There is deliberately no publication FK/tombstone,
so missing IDs do not prove historical existence. No root object is fabricated or history deleted.
New revision save/bind remains strict and requires an available confirmed physical predecessor. An exact
already-bound card retry reuses its validated binding as a no-op instead of requesting new admission.

Other failed cases came from fixtures rewriting a confirmed card message ID while its retained
edit still targeted the old ID. Sitting tests now freeze a differing synthetic historical callback
through the public unattempted-plan API before render/attempts; actual card receipts remain immutable.
The recovery fixture uses audited dismissal and public republish. Existing HTML, parent, checkpoint
and attempt assertions remain. Six corpus failures were the resulting delivery-retry cascade;
root reruns the real subprocess qualification in its actual Git checkout without weakening pins.

All14 byte-frozen public fixture cores match OFF: physical operations, rendering, requests/results
and IO. Unsupported rows in that driver follow a valid edit; separate units cover unsupported
conditions before capture. The original3natural fixtures' whole canonical dump measurements show
18,336–29,121 additional SQL bytes and3,018–5,915 gzip bytes for original+edit. Largest2.36MB RCL
adds9,254SQL/2,401gzip for original alone,18,336SQL/3,018gzip for both. No full Bill copy is added.
Repeated polls add zero. The controls have zero cross-version snapshot reuse; no future savings
or universal bound is assumed. Receipt removal preserves every other table rowid and reproduces
OFF canonical dumps exactly. Final runtime replay core/metadata and SQL/gzip match that measurement.

On the pinned old state, all48 table rows/rowids, default computation doctor and double restore
stay exact; source/copy hashes are unchanged and no legacy history is backfilled. No model call,
corpus write, raw archive or storage backend is added. These are controlled public-operation
fixtures using actual Bills, not legal gold or natural paid-cycle acceptance.

Final full gate: 8441 tests in 293.335 seconds,92%coverage, strictmypy738,
Ruff/format green, all807source/config pins exact. Case-set comparison adds28units, no scenarios
or removed cases. Reader6fa7c906 was actually CI/relay verified before B publication. DA7 remains
partial; plan74total/44complete/6explicitN/A/24applicableopen.

[Qualification](qualification.json), [paired replay](paired.json.gz), [corpus proof](corpus-proof.json),
[measured budget](budget.md), [storage proof](storage-proof.json), [state proof](state-proof.json),
[new cases](new-cases.json), [source pins](source-pins.json), [base review](peer-review.json),
[lifecycle review](lifecycle-review.json), [strict predecessor review](predecessor-review.json).

Previous reader release: [exact CI and relay verification](previous-release.json).

Actual card writer release: [exact CI and relay verification](release.json), commitc1a5a04d.
