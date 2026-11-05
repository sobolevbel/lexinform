# Saved selection limitation coverage

The explicit `--selection-limitations` source-doctor flag reports coded limitations already saved in typed document selections. Each persisted source observation contributes one denominator unit, including invalid/dangling/unknown rows. Each code counts once per observation; shared selections remain separate observations. Labels overlap, so their fractions need not sum to one.

Explicit empty limitations mean no recorded limitations, not completeness. An absent legacy field is unknown. Missing schema has null counts; a known empty population has zero counts and undefined fractions. The report contains finite codes and counts, not document bodies, URLs or current blocker/legal/source conclusions. Existing output remains unchanged without the flag.

A unit-first native `bill_selection` case records a referenced absent annex. Two observations sharing that selection and one damaged observation prove two overlapping codes each at2/3. Ten new unit cases cover duplicate codes, invalid/missing selections/observations, legacy absence, missing schema, empty population and CLI/default compatibility; no scenarios were added.

Author57focused and scoped static checks pass; exact three-file independent review is CLEAR. Both pinned source states preserve all four prior report flag combinations byte-exact. Their50/54observations have explicit limitation fields; nonzero saved codes are support applicability, missing evidence and unknown annex linkage. They contain no natural missing-own-annex positive; no such natural acceptance is claimed.

[Qualification](qualification.json), [pins](source-pins.json), [review](peer-review.json), [pinned replay](pinned-proof.json). Root integration/full gate and actual release follow. This finite slice leaves broader DA10.3 open.

Root31focused/8937full/303.56s/92%coverage, mypy769/Ruff838 and all838source/config pins pass. [Integration](integration-proof.json): all four prior report flag combinations exact on both pinned50/54observation states, sources unchanged. Actual release follows publication.

Actual c12929f5 [CI and relay release](release.json) verified: CI42021041904/deploy42021914325, exact transition/restart45/timer. No workflow rerun.
