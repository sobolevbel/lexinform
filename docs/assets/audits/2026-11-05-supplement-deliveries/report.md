# Opt-in supplement application and frozen delivery links

`computation_doctor --supplement-deliveries` adds a read-only section linking applied supplement
results to an exact status-change target, frozen delivery payload and recorded physical status.
It reuses the shared computation/dependency guards. Result/application SQL identities and known
request hashes must agree; the frozen change ID and computation reference establish the edge.
Current Bill/status-change contents, answer equality and timestamp joins are not used.

The first scope is direct single-message status updates. Grouped, continuation, digest-owned,
absorbed, missing and malformed payloads remain unassessed/unknown. Review caught that digest
completion can mark a source event SENT without making it a standalone physical delivery;
a public repository digest-completion unit now protects this boundary. New evidence JSON is
strict: duplicate keys cannot certify application, plan, frozen change or batch metadata.

`recorded_sent` means positive local SENT plus message-ID evidence, not remote Telegram
verification. Every valid-bound non-SENT state is `not_confirmed`, preserving its exact physical
status; UNKNOWN/PENDING never become a claim of no delivery. A SENT row without message ID stays
unknown. Consumed batch/item evidence and input-receipt availability are separate; source linkage
is explicitly `not_assessed`. No complete DA10.7 lifecycle is inferred.

Thirty-five new unit cases cover frozen IDs/hashes, later current-row changes, retention/missing
schema, invalid indexed identities, compound/digest ownership, uncertainty, strict JSON and
cancellation. No scenario is added. Ordinary doctor output, default CLI and exit semantics stay
unchanged. No bodies, URLs, provider queries, sends, new storage or production processing.

Root independently checks fresh state7b208 and an immutable copy: application16 targets change192;
publication240/message152 records a direct SENT payload containing that exact supplement result.
The provider item is consumed and its known request hash matches. The original input receipt is
unknown and source edge unassessed. Entire default report and serialized bytes remain exact;
both source/copy database hashes are unchanged. Stored source_kind is impact_assessment, not an
inferred government-position role. Broader families, cleanup alternatives and source cycles remain
outside this diagnostic; DA10.2 and DA10.7 stay partial.

[Qualification](qualification.json), [state proof](state-proof.json),
[source pins](source-pins.json), [independent review](peer-review.json).

Previous release: [exact CI and relay verification](previous-release.json).

Actual release: [exact CI and relay verification](release.json), commit77970851.
