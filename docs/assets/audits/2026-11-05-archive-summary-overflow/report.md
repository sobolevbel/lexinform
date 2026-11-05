# Archive summary overflow

If an archive summary pushes a complete outer trace past the existing byte limit, the writer retains the summary and the outer universe digest. The outer request and decision rows are omitted, with their count recorded. A known archive stream keeps its original count and digest. Unavailable archive evidence stays unknown.

The regression controls cover both summary forms and check that the original trace remains intact. The required-part writer tests also exercise combined archive and required-part summaries. The paired corpus runs compare the raw and cached cohorts from the previous stage.

Run `uv run --frozen pytest -q tests/unit/test_archive_disposition_builder.py tests/unit/test_archive_disposition_writer.py tests/unit/test_required_part_writer.py` for the focused gate. Use the pinned raw command and cached observer from the earlier qualifications for the corpus comparisons. [Raw replay recipe](../2026-11-05-archive-disposition-writer/report.md). [Cached replay recipe](../2026-11-05-required-part-exclusion-writer/reproduce.md).

[Qualification](qualification.json).

[CI and relay deployment verified](release.json).
