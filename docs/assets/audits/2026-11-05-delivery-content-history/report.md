# Read-only editorial history in delivery inspection

`/delivery ID history [PAGE]` exposes retained intermediate editorial versions that the ordinary
original/current inspection did not show. One stored-order version appears per page with its
predecessor, recorded date, frozen bill/event/plan references, rendered checksum, known source
and result references, and associated physical publication outcomes. Links navigate pages and
return to physical `/delivery ID` inspection.

The existing integrity and confirmed-current policy remains unchanged. An uncertain physical
outcome does not become confirmed current content. Missing, corrupt or ambiguous history is
unavailable. The command checks the existing target-channel boundary and can inspect history
with publishing disabled. It does not invoke the current Bill, provider or reader publisher.
Existing command bookkeeping and the operator reply remain normal command behavior.

The page retains at most three physical publications and the latest three operator decisions
per publication; rendering shows two canonical source/result refs of each kind with explicit
omitted counts. It displays metadata, not saved bodies, source URLs or free-text operator
actor/reason fields. No content/delivery/source schema, formatter version, repository contract,
stored payload, recovery decision or publication state changes. Ordinary `/delivery` output
and both original inspector functions remain unchanged.

Fourteen new unit cases cover grammar, intermediate versions, forbidden current-Bill reads,
unknown/current separation, legacy/default behavior, absent/corrupt/ambiguous history and
rendering bounds. One new scenario checks the public command after cleanup with an uncertain
revision and `publish=False`: original/current, versions, publications, resolutions, submissions,
sends and edits remain unchanged. This proves command/container wiring beyond the unit
parser/service/formatter tests; no second scenario is added.

Root independently copied freshly fetched state7b208: one natural root/version gives page1
available and page2 out_of_range. The original inspector functions are AST-identical to the
previous committed code; ordinary inspection remains identical after pagination. The canonical
dump and both source/copy database hashes stay exact. Natural revision cycles are not claimed;
that lifecycle evidence is explicitly fixture-based. Scope remains existing single-message
amendments history; DA7.5 stays partial. No paid calls, source downloads or production processing.

[Qualification](qualification.json), [state proof](state-proof.json),
[source pins](source-pins.json), [independent review](peer-review.json).

Previous release: [exact CI and relay verification](previous-release.json).
