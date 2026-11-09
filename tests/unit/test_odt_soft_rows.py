from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256

import pytest
from pydantic import ValidationError

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models.artifacts import ArtifactMetadata
from lexinform.models.extractions import (
    ExtractedPartSummary,
    ExtractionResult,
    odt_soft_row_parent_sha256,
)
from lexinform.models.odt_tables import (
    MAX_ODT_TABLE_BYTES,
    OdtMergedCell,
    OdtMergedTable,
    OdtSoftRow,
    OdtSoftRowBreak,
    OdtSoftRows,
    OdtTableMerges,
)
from tests.unit.test_odt_blocks import odt_document
from tests.unit.test_physical_extraction import extractor


def source() -> tuple[ExtractionResult, OdtSoftRows]:
    raw = odt_document(
        "<text:p>" + "USTAWA Art. 1. Cudzoziemiec. " * 20 + "</text:p>"
        "<table:table><table:table-row><table:table-cell table:number-columns-spanned='2'>"
        "<text:p>Termin<text:soft-page-break/> podatku</text:p></table:table-cell>"
        "<table:covered-table-cell/></table:table-row>"
        "<table:table-row><table:table-cell><text:p>Stawka</text:p></table:table-cell>"
        "<table:table-cell><text:p>200 zł</text:p></table:table-cell></table:table-row></table:table>"
    )
    observed = extractor().extract_document_with_provenance(raw)
    (part,) = observed.result.selected_parts
    witness = part.block_copy_witness
    assert witness is not None and part.odt_table_merges is not None, (
        "the normal ODT path retains the plain row and table geometry"
    )
    row_text = "Termin\x0c podatku\t"
    start = observed.document.text.index(row_text)
    soft = OdtSoftRows(
        parent_sha256=odt_soft_row_parent_sha256(witness),
        rows=(
            OdtSoftRow(
                path=(1, 0),
                span=(start, start + len(row_text)),
                row_sha256=sha256(row_text.encode()).hexdigest(),
                breaks=(OdtSoftRowBreak(path=(0, 0, 0), offset=6),),
            ),
        ),
    )
    return observed.result, soft


def test_soft_rows_roundtrip_alongside_unchanged_native_evidence() -> None:
    baseline, soft = source()
    (part,) = baseline.selected_parts
    candidate = ExtractionResult.model_validate(
        baseline.model_copy(
            update={"selected_parts": (part.model_copy(update={"odt_soft_rows": soft}),)}
        )
    )
    assert candidate.result_id != baseline.result_id
    assert candidate.selected_parts[0].model_copy(update={"odt_soft_rows": None}) == part
    now = datetime(2026, 11, 9, tzinfo=UTC)
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_artifact_metadata(
            ArtifactMetadata(sha256=baseline.artifact_sha256, byte_size=120, recorded_at=now)
        )
        for extraction in (baseline, candidate):
            repo.save_extraction_result(extraction, recorded_at=now)
        dump = repo.dump()
        repo.restore(dump)
        assert repo.dump() == dump
        assert repo.get_extraction_result(candidate.result_id) == candidate
        assert repo.get_extraction_result(baseline.result_id) == baseline
        assert dump.count('"offset":6') == 1
        with pytest.raises(ValueError, match="invalid stored extraction result"):
            repo.restore(dump.replace('"offset":6', '"offset":99999'))
        assert repo.dump() == dump


@pytest.mark.parametrize("case", ["foreign_parent", "out_of_part", "already_retained"])
def test_soft_rows_reject_foreign_or_duplicate_native_evidence(case: str) -> None:
    original, soft = source()
    (part,) = original.selected_parts
    (row,) = soft.rows
    expected = "exact native text and XML parent"
    if case == "foreign_parent":
        soft = soft.model_copy(update={"parent_sha256": "0" * 64})
    elif case == "out_of_part":
        row = row.model_copy(update={"span": (row.span[0], part.text_chars + 1)})
        soft = soft.model_copy(update={"rows": (row,)})
    else:
        row = row.model_copy(update={"path": (1, 1)})
        soft = soft.model_copy(update={"rows": (row,)})
        expected = "cannot replace existing block copies"
    with pytest.raises(ValidationError, match=expected):
        ExtractedPartSummary.model_validate(part.model_copy(update={"odt_soft_rows": soft}))


def test_soft_rows_share_the_existing_geometry_byte_budget() -> None:
    baseline, soft = source()
    (part,) = baseline.selected_parts
    merges = part.odt_table_merges
    assert merges is not None, "the control has retained native table geometry"
    table = OdtMergedTable(
        path=(1,),
        merges=tuple(
            OdtMergedCell(
                anchor=(1, row, 0),
                position=(row, 0),
                shape=(1, 2),
                covered=((1, row, 1),),
            )
            for row in range(50)
        ),
    )
    retained = OdtTableMerges.model_validate(merges.model_copy(update={"tables": (table,)}))
    assert (
        len(retained.model_dump_json().encode()) + len(soft.model_dump_json().encode())
        > MAX_ODT_TABLE_BYTES
    )
    with pytest.raises(ValidationError, match="aggregate byte limit"):
        ExtractionResult.model_validate(
            baseline.model_copy(
                update={
                    "selected_parts": (
                        part.model_copy(
                            update={
                                "odt_table_merges": retained,
                                "odt_soft_rows": soft,
                            }
                        ),
                    ),
                }
            )
        )
