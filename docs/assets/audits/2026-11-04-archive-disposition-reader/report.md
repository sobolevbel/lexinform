# Compatible archive-disposition reader

This inactive reader adds an optional archive-member section to the existing candidate
trace. Old None values disappear from normal and compact JSON; old IDs, factories and
routine-singleton behavior stay exact. No extractor, selection policy, producer, schema,
source identity or physical inventory changes. The writer is still off.

Walk rows record existing physical HeaderDisposition statuses with outcome observed.
Policy rows name actual selection branches. They are separate stages, not legal-admission
verdicts. Member occurrence uses known root/ordinal paths or an explicitly available
transient physical slot. A slot alone leaves association unknown; filenames are not identity.
No capture ID, foreign key or physical corroboration is invented. Known facts cannot
contradict one another across rows or stages of the same outer attempt.

Retention covers observed rows, independently of walk completion and unvisited descendants.
Complete retention checks the canonical whole-row hash. Prefix/omitted summaries preserve
counts/hash without reconstructing missing rows; unknown coverage claims neither counts
nor hash. Maximum8rows/pathdepth4/1KiBsection; whole trace remains4KiB. Complete outer
traces check attempted phase/index and known root agreement; omitted outers retain summaries.

## Compatibility evidence

Root61focused units include47new cases and14existing candidate-trace cases. On fixed state
105de577 (4 November2026,11:33:29Z),all8traces/8links keep exact IDs, columns and serialized
JSON. Public get/list and two restore cycles preserve canonical dump021b0a00. Ordinary
doctor output is exact with0errors; input DB unchanged. This is a historical pin.

Doctor parity is not archive-corruption validation: the current doctor does not inspect
candidate traces. A separately qualified shared row reader/opt-in doctor follows before B.
No extraction corpus acceptance is claimed for an inactive reader; producer B must qualify
actual raw and cached archive selection/IO parity separately and wait for deployed A.

## Producer boundaries still open

B1 excludes a bare singleton failure before source observation and cannot address a root
parse failure before any header/leaf as a member. Known root bytes remain separate existing
physical history; use honest unknown/omitted metadata instead of an invented member.
Missing contexts leave association unknown. Unknown bytes are not proof of unsupported
format. Under outer read_failure only walk metadata is admitted; this conservative scope
does not assert that policy never ran before a later summarizer failure. Producer B must
also preserve losing outer archives and avoid routine-singleton suppression.

[Qualification](qualification.json), [legacy proof](legacy-state-proof.json),
[independent review](peer-review.json). DA4.1/DA4.4 remain partial.

[Actual reader CI and relay release](release.json).
