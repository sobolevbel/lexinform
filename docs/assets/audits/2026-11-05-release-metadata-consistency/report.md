# Release metadata consistency

5 November 2026. Qualified isolated candidate; exact independent review is CLEAR.
Root full gate passes; actual release verification follows publication. This changes only the read-only release observer;
no bot, workflow, deployment action or state-writing code changes.

The saved db73 check-2 has a completed/success deploy workflow while its selected job is still
in_progress with null conclusion. The verifier compared the null conclusion to success before
checking incomplete metadata and returned failed/deploy_not_successful. The historical report
is preserved unchanged; it is not retroactively presented as successful deployment proof.

Both CI and deploy now classify completed-workflow/null or nonterminal job metadata as pending,
reason metadata_pending. Missing workflow conclusion, empty jobs and a jobs-list transport-read
failure likewise remain pending. Malformed/incomplete listings remain unproven; partial, wrong
or duplicate expected-job sets cannot verify. Explicit non-success workflow/job conclusions for
completed workflows retain failed classification. A pending return does not read relay logs.

Verified still needs exact target/current/final main SHA, expected completed-success job names,
valid CI/deploy chronology, exactly one ordered commit transition, the 45-second restart proof
and enabled batch-poll timer. The [AST proof](invariants.json) preserves transport, parser/listing,
selector, relay-proof and CLI functions exactly. Existing optional wait remains bounded to one
repeat; no workflow rerun or new polling mechanism is introduced.

[Qualification](qualification.json):73 focused tests,31 new units,0 scenarios; strict mypy2,
Ruff2 and format2 pass. Thirteen selected regression checks fail before correction and pass
in the final suite. Controls cover both workflow phases, unavailable/null/nonterminal metadata,
failed/cancelled outcomes, malformed listings and eventual success only with the original proof.
[Independent review](peer-review.json) verifies both exact source hashes and patch without
rerunning tests or accessing the network. [Source pins](source-pins.json) identify this candidate.

[Observed replay](observed-proof.json) uses the exact [compressed historical report](observed-check-2.json.gz),
SHA1ad4dc597336a0f6b32a155c8e8bd1f030949a6cc4f02aecef6e134022f1a326. Baseline reproduces
all report fields; candidate changes only failed→pending and deploy_not_successful→metadata_pending.
Other fields, read order and source bytes remain exact, with zero relay-log reads, network calls
or workflow reruns. API selector/listing envelopes wrap already filtered metadata; raw GitHub API
responses are not claimed. This offline replay does not verify the remote release: a later
consistent actual observation must still meet every original proof requirement.

[Artifact hashes](artifacts.json) identify compressed/expanded bytes and local qualification logs.
No relay log, provider body, database or source tree is copied. No architecture checkbox closes.

Root73focused and8732full tests/297.433s/92%coverage pass; mypy752/Ruff/format821green, all821
source/config pins exact.31newunits/0newscenarios/0removed. Actual saved report replay against
archived committed baseline changes only the pending status/reason, preserving all other
fields, read sequence, input SHA and zero log/network/rerun calls. [Root proof](integration-proof.json),
[root observed replay](root-observed-proof.json), [case delta](case-delta.json).
Previous2bba2c79 [CI and relay](previous-release.json) actually verified.

Actual15670786 [CI and relay release](release.json) verified after publication; no workflow rerun.
