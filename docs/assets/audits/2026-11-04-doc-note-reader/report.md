# Declared DOC note links

Historical qualification · 4 November 2026 · baseline `901f8ca1`.

The new inert reader follows the measured Word 2007 counted FIB profile and reads the
inherited 97 reference/text PLCF pairs. It retains source CP ranges and numbering flags,
with an aggregate limit of 256 links and no silent truncation. It validates signed story
counts, usable CP bounds/order, pair cardinality, and stream ranges. Undefined final
sentinels and unused zero-length offsets have no inferred meaning. The first boundary
must be zero for a nonempty note list: this is an additional supported-profile limit.

The pinned replay covers 404 DOC paths, 246 unique input hashes: 245 OLE inputs accepted,
one non-OLE input unavailable (two paths). There are 69 note-bearing unique inputs,
377 declared footnote links, and no endnote links. All 405 input paths, including INDEX,
were unchanged before/after. The full report contains per-input coordinates and hashes,
no document bodies. The 13 XML manifest entries and unlisted archive leaves are excluded.

`declared_cp_only` does not establish actual reference characters, terminal paragraph
marks, CFSpec, emitted text offsets, legal labels, or completeness of reading. Default
DOC extraction, source selection, model inputs and SQL writers do not call this reader.
The existing DOC extractor has exactly the baseline source hash. No dump growth results
from this stage because nothing is persisted. Physical verification and runtime capture
remain subsequent DA3.3 work.

Reproduce from a checkout with development dependencies and the pinned corpus:

```bash
uv run --frozen python -m tools.replay_doc_notes \
  --corpus /path/to/lexinform-corpus \
  --manifest docs/assets/audits/2026-11-04-doc-note-reader/input-manifest.json \
  --output /path/to/new-report-directory
```

The CLI rejects a changed INDEX/raw SHA, forbids network calls and corpus writes,
records source fingerprints, and refuses to replace an existing output directory.
See the [summary](summary.json), [full report](report.json.gz),
[independent review](review.json), and [source contract](../2026-10-31-doc-profile-handoff/contract2007.md).
