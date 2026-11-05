# Read bounded mixed RCL receipts and verify ODT inline spans

The compatible reader recognizes a distinct mixed RCL profile with at most six source
parts. It requires the main part, normative annexes, self-amendments and optional
support in that order. The prior profile retains its five-part cap and original bytes.
Both profiles use the same request, physical parent and emitted-range checks.

Four raw replays from stages 9 and 11 of project 12294859 preserved native request,
I/O and cost. Adding each candidate receipt in isolation then validated its exact
parents and restored the queued and submitted SQL twice. The receipts were 2,506 to
3,067 bytes. The largest gzip increase was 965 bytes. Restoring a receipt with the
wrong profile failed atomically. The runtime writer remains unchanged.
[Mixed reader qualification](mixed-reader.json) and [legacy parity](legacy-reader.json).

The offline ODT audit now verifies the exact emitted spans of notes containing bare
space and tab elements. On the existing six-root cohort it verified four note spans
and their four embedded calls, both in original archives and when reading the same
leaf bytes alone. Earlier report fields were identical on a same-version control.
No selected original leaf contained a note. Separate note references, explicit
space repeats and other unresolved views remain outside this check.
[ODT summary](odt-summary.json) and [pinned inputs](../2026-10-28-odt-replay/cases.json).
