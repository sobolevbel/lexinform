from contextlib import closing
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models.artifacts import ArtifactMetadata
from lexinform.models.extractions import ExtractedPartSummary, ExtractionResult
from lexinform.models.odt_tables import OdtMergedCell, OdtMergedTable, OdtTableMerges
from tests.unit.test_odt_blocks import odt_document
from tests.unit.test_odt_note_status import result, summary
from tests.unit.test_physical_extraction import extractor


def table_merges() -> OdtTableMerges:
    merge = OdtMergedCell(
        anchor=(0, 0, 0),
        position=(0, 0),
        shape=(2, 2),
        covered=((0, 0, 1), (0, 1, 0), (0, 1, 0)),
    )
    return OdtTableMerges(
        input_sha256="a" * 64,
        body_manifest_sha256="d" * 64,
        verified_tables=2,
        unresolved_tables=1,
        tables=(OdtMergedTable(path=(0,), merges=(merge,)),),
    )


def test_old_and_new_extraction_results_restore_with_exact_identity() -> None:
    legacy = result(summary())
    retained = result(
        summary().model_copy(update={"odt_table_merges": table_merges(), "block_count": 6})
    )
    assert "odt_table_merges" not in legacy.model_dump_json()
    now = datetime(2026, 11, 8, tzinfo=UTC)
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_artifact_metadata(
            ArtifactMetadata(sha256=legacy.artifact_sha256, byte_size=120, recorded_at=now)
        )
        for extraction in (legacy, retained):
            repo.save_extraction_result(extraction, recorded_at=now)
        sql = repo.dump()
    with closing(SqliteBillRepository(":memory:")) as restored:
        restored.restore(sql)
        assert restored.dump() == sql
        for extraction in (legacy, retained):
            assert restored.get_extraction_result(extraction.result_id) == extraction


@pytest.mark.parametrize("field", ["sha256", "manifest_sha256", "coordinates"])
def test_table_metadata_requires_its_actual_odt_part(field: str) -> None:
    part = summary().model_copy(update={"odt_table_merges": table_merges()})
    value = "unknown" if field == "coordinates" else "e" * 64
    with pytest.raises(ValidationError, match="actual ODT input and body manifest"):
        ExtractedPartSummary.model_validate(part.model_copy(update={field: value}))


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"shape": (1, 3)}, "one physical path"),
        ({"anchor": (2, 0, 0)}, "in its table"),
        ({"anchor": (0, 0, 1)}, "both an anchor"),
    ],
)
def test_invalid_retained_merge_fails_at_extraction_boundary(
    updates: dict[str, object], reason: str
) -> None:
    data = table_merges()
    (table,) = data.tables
    (merge,) = table.merges
    invalid = data.model_copy(
        update={
            "tables": (table.model_copy(update={"merges": (merge.model_copy(update=updates),)}),)
        }
    )
    part = summary().model_copy(update={"odt_table_merges": invalid})
    with pytest.raises(ValidationError, match=reason):
        ExtractionResult.model_validate(
            result(summary()).model_copy(update={"selected_parts": (part,)})
        )


def test_retained_tables_share_one_byte_budget_across_selected_parts() -> None:
    data = table_merges()
    table = OdtMergedTable(
        path=(0,),
        merges=tuple(
            OdtMergedCell(
                anchor=(0, row, 0), position=(row, 0), shape=(1, 2), covered=((0, row, 1),)
            )
            for row in range(36)
        ),
    )
    retained = OdtTableMerges.model_validate(data.model_copy(update={"tables": (table,)}))
    first = summary().model_copy(update={"odt_table_merges": retained, "block_count": 72})
    second = first.model_copy(update={"physical_index": 1})

    with pytest.raises(ValidationError, match="aggregate byte limit"):
        ExtractionResult.model_validate(
            result(first).model_copy(
                update={
                    "selected_parts": (first, second),
                    "physical_part_count": 2,
                    "selected_part_count": 2,
                }
            )
        )


def test_native_extraction_retains_merge_coordinates_without_changing_selection() -> None:
    raw = odt_document(
        "<text:p>" + "USTAWA Art. 1. Cudzoziemiec. " * 20 + "</text:p>"
        "<table:table><table:table-row><table:table-cell table:number-columns-spanned='3'>"
        "<text:p>Termin</text:p></table:table-cell>"
        "<table:covered-table-cell table:number-columns-repeated='2'/>"
        "</table:table-row></table:table>"
    )
    reader = extractor()

    observed = reader.extract_document_with_provenance(raw)

    assert observed.document == reader.extract_document(raw)
    (part,) = observed.result.selected_parts
    metadata = part.odt_table_merges
    assert metadata is not None, "the selected native ODT has one verified merge"
    assert metadata.verified_tables == 1 and metadata.unresolved_tables == 0
    (table,) = metadata.tables
    assert table.path == (1,)
    assert table.merges == (
        OdtMergedCell(
            anchor=(1, 0, 0),
            position=(0, 0),
            shape=(1, 3),
            covered=((1, 0, 1), (1, 0, 1)),
        ),
    )
