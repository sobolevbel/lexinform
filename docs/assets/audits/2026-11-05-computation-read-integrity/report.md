# Strict public computation-row reads

5 November 2026. Isolated candidate qualified; independent exact-pin review is CLEAR.
Root integration, pinned-state replay, full gate and release remain root-owned.

Public get/list previously selected only result_json and accepted valid paid JSON despite a
mismatching indexed result_id, provider, batch_id, custom_id, term or number. Both now select the
existing indexed columns and use the existing read_computation_result decoder. No new validator,
identity rule, schema, paid-result mutation or current-owner inference is introduced. Missing
lookup remains None/empty; matching foreign-owner, legacy and compact review forms stay valid.

Owner predicate, rowid order and purpose-filter SQL are unchanged, including existing malformed
JSON json_extract behavior. Doctor and shared decoder bytes stay exact. Doctor deliberately
keeps parsed mismatching rows in inventory after its finding; this stage does not replace that
behavior with a strict-read-and-continue refactor. Existing bounded ValueError-subclass codes
computation_columns_mismatch and invalid_computation_result are reused.

[Qualification](qualification.json):12 unit-first column/API controls fail before correction;
all171 focused tests pass with20 new units/0 scenarios, strict mypy3/Ruff3/format3 green. Controls
cover each indexed field through get/list, invalid/duplicate/deep JSON, SQL-filter behavior and
one positive fixture reusing all ten existing purpose records, legacy/foreign and compact forms.
Existing reader, dependencies, J245 memo and J243 government tests pass. [Independent review](peer-review.json)
checks the frozen patch/three physical pins without rerunning tests or accessing the network.

[Six damaged-column doctor reports](doctor-parity.json) are byte-exact before/after; fixture DB
bytes remain unchanged by reads. [Change proof](change-proof.json) records untouched decoder and
doctor hashes plus preserved query semantics. [Source pins](source-pins.json) identify the exact
three files. Root separately owns the pinned13-get/owner-list/default-doctor/dump comparison,
source freeze/full gate and actual release proof; those are not claimed complete here.

No workflow, provider/network call, corpus extraction, production processing or state-writing
policy changes. No plan checkbox closes from this bounded DA10.2 reader-integrity change.

Root82focused and8796full tests pass; mypy757/Ruff/format826green, all826source/config pins exact. [Root state proof](root-state-proof.json):13gets/13ownerlists, entire default doctor including physical DB SHA and dump exact on independently fresh copies. Publication/release follows the green docs gate.
