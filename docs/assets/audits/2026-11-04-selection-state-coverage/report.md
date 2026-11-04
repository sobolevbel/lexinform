# Recorded selection-state coverage

The optional source-history doctor view counts one persisted source-observation row.
Its denominator includes every row, including invalid observations and missing, invalid
or mismatched selection parents. Shared immutable selections count once for each linked
observation. No latest-row heuristic or attempt history is inferred.

Known states remain selected, waiting, ambiguous, unsupported and unverified. Unknown
invalid observation, missing selection, invalid selection and identity mismatch are separate.
Only observations and parents accepted by the shared typed SQLite readers contribute to
purpose/document-kind groups. Unknown rows remain unattributed. Group fractions explicitly
name the global observation denominator; group totals are not their fraction denominators.

Available empty tables yield zero counts and undefined fractions. Missing required tables
or columns yield unavailable and unknown totals, counts and fractions. In the optional
mode, malformed source-history columns add a bounded finding and skip only the affected
legacy audit loop; ordinary doctor behavior and output remain unchanged. The supported
legacy decoder is retained; historical identities that fail its guards remain unknown.

This measures recorded observations, not bills, HTTP attempts, runs, extraction completeness,
missing-required-part rates or current blocking states. It does not classify reasons from
free text. Unrecorded events, hosted checkpoint latency and natural daily growth stay unknown.
No database/runtime writer, migration, clocks or storage backend changes.

## Pinned state qualification

On state105de577 (4 November2026,11:33:29Z),42observations refer to36unique selections.
The weighted view has12selected,29unverified,1waiting,0unknown,8purpose/kind groups.
The ordinary doctor remains exactly equal with0errors; the database hash is unchanged.
This is an explicit historical pin, not a claim about the latest production state.

## Recheck

```sh
PYTHONPATH=src:. python tools/source_history_doctor.py \
  --database /path/to/read-only-state.db --output /new/report.json --selection-coverage
```

Omit the flag for the existing report. The tool reads a snapshot and reports no source
bodies or URLs in the new view. [Qualification](qualification.json),
[pinned proof](pinned-state-proof.json), [review](peer-review.json).
