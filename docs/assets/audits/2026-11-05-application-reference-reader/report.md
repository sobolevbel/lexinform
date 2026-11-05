# Amendment application-reference reader A

5 November 2026. Status: reader integration and full local gate passed.
DA7.1/DA7.5 remain partial; future J242 writer B must follow actual reader deployment.

The optional `amendment-projections-v1` profile reserves real persisted positive application IDs.
Unprofiled legacy integers remain opaque and retain their old JSON/content identities. Known
references use the display namespace `result-application-v1:<SQL id>`; history renders two at
most, then counts additional/opaque refs. No reader writes an application or enriches history.

The common validator joins each exact SQL receipt and strict JSON to its retained validated
result, the frozen change record and matching known request hash. Original targets are saved
status-change IDs; revision targets are actual publication IDs. Existing physical plan, render
and binding must agree. Current Bill, paid/application owners, answer/title/time equality and
upstream dates are not evidence. Retained missing physical targets affect availability, while
new save/bind remains strict. Receipt integrity alone never confirms Telegram delivery.

27 new unit cases and 250 focused cases pass, with strict mypy on eight files, Ruff and formatting.
The 306 existing repository SQL-call ASTs and all service sources are unchanged. Reader-only
pinned fresh-b checks preserve every table rowid/data, existing JSON/IDs, doctor, dump and double
restore. SQL25,816,929bytes and deterministic gzip5,208,775bytes match exactly:0/0 overhead.
This proves compatibility on that existing database; modern profiled chains are unit fixtures,
not natural prospective writer acceptance. No network or full gate ran in the author workspace.

Root independent review found no issues;94 focused and8584 full tests pass in297.169seconds
with92%coverage, strict mypy743 and Ruff/format812. All812 frozen source/config hashes remain
exact;27newunit cases,0newscenarios and0removed cases. Root repeats the full existing-state
reader proof and independently checks306 unchanged SQL call ASTs and unchanged services.
Reader release verification follows publication. [Previous release](previous-release.json)
confirms f7ee12c0 CI41970157905 and relay41970627223.

[Qualification](qualification.json), [pins](source-pins.json), [AST proof](ast-proof.json),
[reader evidence](evidence.json), [integration status](integration-proof.json).
