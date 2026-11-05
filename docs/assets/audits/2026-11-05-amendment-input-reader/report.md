# Compatible amendment input receipt reader

The internal reader accepts `AmendmentInputReceipt`, profile `amendment-single-part-text-v1`
with `amendments-context-json-v1` encoding. It reuses the bounded text-map builder and common
parent validator rather than copying primary/supplement mapping logic. The complete actual
AmendmentsContext contributes to its checksum; source segments describe only `ctx.text`.
Previous summary, key changes, proposal and recommendation are not normative source ranges.

DigestItemMeta, BatchIntent and submitted-item readers check the exact profile/question/purpose,
request/context/text hashes, range constraints and known recorded parents. Ordinary preparation
and corrections share the amendments purpose; correction targets retain their existing contract.
Primary and supplement formats, defaults and identities remain unchanged.

The runtime writer is OFF. Analysis/loading/correction services, repository and doctor source
are unchanged in this stage. Resumed contexts without original capture remain unstamped;
scans, multipart and unsupported origins are not newly mapped. No paid-input/source history is
reconstructed, and no new provider preparation, schema or raw storage is introduced. A later
writer requires verified reader deployment and a frozen public preparation baseline first.

Twenty-four new unit cases cover complete-context changes, profile/purpose/request/context
mismatches, scan/range rejection, known parents, correction-target roundtrip, submitted metadata,
atomic restore rejection and doctor findings. Existing primary/supplement/capture cases remain
part of focused qualification; no scenario is added.

On fresh state7b208, root independently verifies the entire default doctor report, unchanged
source database SHA and double canonical dump/restore. There are zero queued/submitting intents:
modern amendment compatibility is controlled fixture evidence, not a production population claim.
The separate new raw committee-report generation and producer-OFF preparation baseline qualify
future writer work; they are not acceptance of an active producer here. DA5 remains partial.

[Qualification](qualification.json), [state proof](state-proof.json),
[source pins](source-pins.json), [independent review](peer-review.json).

Previous release: [exact CI and relay verification](previous-release.json).

Actual reader release: [exact CI and relay verification](release.json), commitccb27d43.
