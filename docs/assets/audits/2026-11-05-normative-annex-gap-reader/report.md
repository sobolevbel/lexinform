# Compatible reader for blocked normative annexes

A candidate trace can optionally describe one terminal gap in the chosen loose candidate.
The reason distinguishes a missing loader return, a returned file without text and a
returned text classified as a non-annex appendix. The reader validates the chosen phase,
the candidate and annex indexes, URL admission and the matching blocked state. The model
carries only an optional file hash and observed kind alongside the indexes.

Older traces omit the field and keep their serialized bytes and IDs. The existing trace
limit still applies. The runtime writer remains a separate stage and can omit the new
field when the existing trace has no retained parent or spare capacity.

Two restored checkpoints preserved all 18 stored legacy traces and their links, including
an archive section. The old serialized trace JSON, normalized SQL and doctor outputs
matched, and each copy passed a double restore. [Checkpoint parity](legacy-parity.json).

[Exact CI and relay verification](release.json).
