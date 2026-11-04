# Supplement text receipt reader

This reader-only stage adds a compatible bounded supplement text receipt beside the
existing primary receipt. Common fields, map validation and 8 KiB/32-segment limits have
one private owner; primary field order, defaults and fixed historical JSON bytes remain
unchanged. The new profile and context encoding are explicit. Scan inputs are refused.
Block and physical origins stay unmapped; prefix and unknown maps remain honest.

Digest metadata adds an omitted-None optional field. A receipt-bearing queued intent
must match its actual supplement question, exact frozen request, whole frozen context
and emitted text. Union parsing cannot reinterpret a malformed receipt as legacy data.
Submitted items additionally require the saved request checksum already copied by
`LlmBatchItem.from_intent`; it must match the receipt. Queued/submitting metadata can
lack that duplicate hash because its actual frozen request supplies the independent
check. The sole queued-reader exemption is explicit; actual item reads, restore and
doctor keep the stricter submitted contract. Existing primary policy is unchanged.

Future valid known/unavailable parents, tampered contexts/maps/parents, wrong purpose,
missing/mismatched submitted checksum and consumed cleanup are covered through typed
models and public SQLite APIs. No World scenario is introduced.

Pinned legacy state105de577 restores twice with exact canonical dump021b0a00. All122
existing batch metadata rows read successfully, including the prior primary receipt;
four doctors report0errors and retain the same4workwarnings. The copied database is
unchanged by reads. No natural supplement receipt or production capture is claimed.

No builder, analysis producer, model output schema, prompt, migration, raw archive or
backend changes. Runtime still emits no supplement receipts. This is the compatible
rollback reader required before a separately qualified prospective writer is published.
No extraction/selection/text-transform/fingerprint algorithm changes; primary context
hash implementation and serialized receipt are preserved. Writer corpus acceptance
remains required before activation.

[Qualification](qualification.json), [legacy state](legacy-state-proof.json),
[independent review](peer-review.json).
