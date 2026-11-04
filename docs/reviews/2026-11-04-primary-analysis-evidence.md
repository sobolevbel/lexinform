# Primary analysis evidence scope

Status: offline contract; live answers and reader rendering unchanged · 4 November 2026.

DA9 starts with these bounded claim classes. A quote's presence, its source identity and
its actual support for a conclusion are separate results.

| Claim | Required source | Acceptance boundary |
| --- | --- | --- |
| `key_changes[i]` | Proposed normative text | Identifies a proposal; cannot establish adoption or entry into force. |
| `affected_groups[i]` | Normative provisions defining subjects, conditions and exclusions | General justification alone cannot establish legal scope. |
| `effective_date` | Normative commencement provision | Bind trigger, condition and scope; consultation and procedural dates are separate. |
| `changes_since_previous[i]` | Both normative editions | Require distinct before/after source refs; previous editorial summaries are insufficient. |
| Adoption or entry-into-force assertion | Decision, promulgated act or other authoritative outcome source | Distinguish proposal, committee recommendation, chamber decision and final act. |

The first implementation checks only `key_changes[i]` mechanically. It uses an offline
envelope with the field index, exact claim/source text hashes and existing `EvidenceCitation`.
It accepts an explicitly supplied `ReviewDocument` with role `project_text`. Assigning that
role in a test does not certify that the real document is a proposal.

The existing review citation matcher remains the common owner of quote/locator presence.
Primary checks additionally bind the exact claim text, exact extracted source text and a
unique document identity. Ambiguous matching is explicit. Literal offsets, where available,
are Python string offsets in the supplied text, not physical blocks or paid-input coordinates.
Incomplete supplied sources remain partial even when a real quote is found.

`ReviewDocument.sha256` hashes extracted text, `normalized_sha256` is a versioned comparison
identity, and `file_sha256` identifies raw file bytes. They are not interchangeable. A
`PrimaryInputReceipt` does not retain text and currently declares block/physical origins
unmapped; its hashes alone cannot certify quote or block positions.

Every mechanical result keeps `semantic_not_assessed`. A true but irrelevant quote can
pass presence checks and still fail to support the conclusion. Missing legacy evidence
and unavailable sources remain visible exclusions; neither triggers re-analysis, hides
an existing card, or becomes a negative legal verdict.

The offline envelope is separate from `Analysis`, whose schema is also the live provider
output contract. This stage changes no provider schema, prompt, `AnalysisRecord` storage,
paid input, reader payload or production feature. Later persistence, semantic annotations
and any paid A/B require their own concrete qualification. Existing answers without
citations are not independent labels, and synthetic controls are not human gold data.

DA9.1 selects the claim classes. DA9.2–9.5 retain their separate implementation and semantic
acceptance gates; the first mechanical validator does not complete them.

[Implementation plan](../plans/2026-10-22-document-architecture.md#da9).
