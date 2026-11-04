# Shared candidate-trace reader and optional doctor

One public typed SQLite row reader now owns candidate trace JSON/model validation and
trace ID/coverage/count agreement. Repository get/list and scratch restore share it;
restore retains the existing source-candidate link foreign-key check. Errors are bounded
codes without the stored payload. No identity, policy, table or writer changes.

The source-history doctor adds optional `--candidate-traces`. It reports distinct trace
rows and link rows separately, with explicit availability per required table/column set.
A shared trace linked to two observations remains one trace and two links. Missing legacy
schema is unavailable; malformed present/current-schema metadata is a bounded finding.
Missing parent schema leaves link validation unknown. Known invalid trace/source parents
produce sanitized row references. Source parents use the shared JSON/SQL observation reader.

This proves saved model/column identity and referential presence, not current Bill
ownership, equality of the requested trace document with today's URL, legal role, paid
input or raw-body availability. Default output and selection-coverage output stay exact.
Only opt-in malformed source-history loops are skipped safely; other audits remain active.

## Qualification

125 focused units include the new nested-A corruption control: a valid archive section is
saved publicly, its observed-row count is corrupted in SQL, get/list/restore reject it,
restore remains atomic and the doctor reports invalid_candidate_trace without body/URLs.
Existing candidate/source/selection units also pass. No World scenario is added.

On fixed state105de577 (4 November2026,11:33:29Z),all8trace rows and8links validate with no
findings. Ordinary doctor output and the optional42-observation selection view remain
exact. The real dump also restores twice with exact canonical bytes and public trace reads.
Input DB hash is unchanged. This is a historical pin, not latest-state acceptance.

## Recheck

```sh
PYTHONPATH=src:. python tools/source_history_doctor.py \
  --database /path/to/read-only-state.db --output /new/report.json --candidate-traces
```

Combine with `--selection-coverage` when needed; omit both flags for the existing output.
[Qualification](qualification.json), [pinned proof](pinned-state-proof.json),
[review](peer-review.json). This bounded check does not complete all DA10.2 provenance.

[Actual CI and relay release](release.json).
