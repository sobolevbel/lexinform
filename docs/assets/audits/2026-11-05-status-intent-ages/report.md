# Intent creation ages in operator status

`/status` now groups existing queued/submitting intent rows by purpose and state. Each group
shows count, known/unknown ages and the oldest known owner reference. Age starts at the saved
intent `created_at`; it does not measure entry into the current state, provider completion or
source availability. Submitting remains explicitly uncertain. Counts are intent rows, not
unique bills or paid calls; unknown clocks stay unknown.

One pure saved-clock evaluator serves live grouping and the offline work doctor. It preserves
existing missing/invalid/naive/future reason codes and measures elapsed time across DST folds.
Root additionally avoids UTC-conversion overflow for supported boundary dates by subtracting
wall-clock timedeltas and their offsets. The doctor still treats non-string SQL clocks as
missing, preserving its existing behavior. One injected Clock observation supplies the status
snapshot; no new repository queries, fields, schema or provider calls are introduced.

Rendering caps output at six groups, clips and HTML-escapes owner references, and reports
omitted groups. Empty derived groups are excluded from serialization: quiet status text and
model JSON remain exact. Existing batch-intent operator guidance stays intact; this feature
never submits or retries work.

Fifteen new unit cases cover clock reasons, offsets/DST/date limits, grouping denominators,
oldest references, unknown-only groups, rendering bounds and quiet compatibility. The existing
status scenario is extended for public persisted queued/submitting rows and injected time;
no scenario case is added. Root focused checks cover work ages, formatting, command routing
and the previous delivery-history feature.

On fresh state7b208, root independently verifies byte-exact work-doctor default and explicit-
clock reports, quiet status/model JSON and unchanged database SHA. This is a derived metadata
view, not new natural lifecycle or hosted-latency evidence. DA10.3 remains partial.

[Qualification](qualification.json), [state proof](state-proof.json),
[source pins](source-pins.json), [independent review](peer-review.json).

Previous release: [exact CI and relay verification](previous-release.json).

Actual release: [exact CI and relay verification](release.json), commit95f98f32.
