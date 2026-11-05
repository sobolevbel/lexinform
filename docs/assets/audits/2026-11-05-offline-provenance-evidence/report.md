# Offline evidence for Word, Sejm proposals and affected groups

The Word reader accepts an optional bounded count of visited textbox nodes. Old rows
omit the field and retain their serialized bytes and identities. The runtime writer is
a separate stage. An immutable copy of state 13440124 preserved all 88 extraction results,
including 29 Word view statuses. [Legacy extraction parity](legacy-extraction-parity.json).

The OOXML diagnostic compares the raw body inventory with the native legacy walk. Five
positive files and one negative contained 13 nodes in Choice and 13 in Fallback. The
legacy body walk visited 13 and the extracted text and selection matched plain extraction.
The report describes the bounded body walk. [Word evidence](word-proof.json).

The proposal replay requires same-term official parent identity, exact selected PDF roles
and a native candidate edition. Druk 2320-A passed as a proposal targeting the original
2320 edition. Druk 2082-A was rejected because the base PDF did not have the required
selected bill role. Neither case supplies an adoption result. [Positive case](case.json),
[negative case](case-no-edition.json), [recorded relation](proposal-proof.json).

The affected-group validator binds the exact list entry to a literal citation through
the same source/hash/role checks as key changes. A technical answer on the pinned DOC
accepted its unique source quote and rejected a quote with a modified scope. Existing
key-change replay preserved 404 paths, 558 checks, 325 mutations and 11 input maps.
Semantic support, physical origins and paid-input mapping remain unassessed for groups.
[Affected-group evidence](affected-group-proof.json).

The proposal and Word replays emit body-free evidence. Use an output path outside the corpus.

```sh
uv run python -m tools.replay_proposal_edition --corpus ../lexinform-corpus \
  --case docs/assets/audits/2026-11-05-offline-provenance-evidence/case.json --case-sha256 09c1d04aed9bb1ebe1d3d6eb6be49bb0ecbc959bb886233985c4b53f993d3dfc \
  --output proposal.json

uv run python -m tools.replay_word_textboxes --corpus ../lexinform-corpus \
  --catalog docs/assets/audits/2026-10-30-word-structure/catalog.json \
  --catalog-sha256 7bfa79ac7ee2a6981d8106880cad2bde6bc86df0b5da9a966530fcaebbf057f5 \
  --case 418609.docx --case 676870.docx --case 709354.docx --case 722211.docx \
  --case 723373.docx --case 339907.docx --output textboxes.json
```
