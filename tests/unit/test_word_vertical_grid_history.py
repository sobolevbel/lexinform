from contextlib import closing
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models.artifacts import ArtifactMetadata
from lexinform.models.extractions import (
    ExtractedPartSummary,
    ExtractionResult,
    extraction_metadata_sha256,
)
from lexinform.models.word_grid_merges import (
    WordVerticalGridMerges,
    WordVerticalGridTable,
    WordVerticalMerge,
)
from tests.unit.test_odt_note_status import result, summary


def word_summary() -> ExtractedPartSummary:
    return summary().model_copy(
        update={
            "format": "word",
            "coordinates": "word-blocks-v1",
            "text_view": "legacy-word-body-v1",
            "limitations": (),
            "manifest_sha256": extraction_metadata_sha256(("d" * 64, "b" * 64)),
            "block_count": 12,
        }
    )


def vertical_grids() -> WordVerticalGridMerges:
    return WordVerticalGridMerges(
        input_sha256="a" * 64,
        body_manifest_sha256="d" * 64,
        notes_manifest_sha256="b" * 64,
        verified_tables=2,
        unresolved_tables=1,
        tables=(
            WordVerticalGridTable(
                path=(0, 87),
                columns=4,
                rows=4,
                merges=(WordVerticalMerge(row=1, columns=(0, 2), cells=((3, 1), (4, 1))),),
            ),
        ),
    )


def test_legacy_and_word_grid_extractions_restore_with_exact_identities() -> None:
    legacy = result(word_summary())
    retained = result(word_summary().model_copy(update={"word_grid_merges": vertical_grids()}))
    assert "word_grid_merges" not in legacy.model_dump_json()
    assert legacy.result_id != retained.result_id
    now = datetime(2026, 11, 9, tzinfo=UTC)
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


@pytest.mark.parametrize(
    "updates",
    [
        {"sha256": "e" * 64},
        {"manifest_sha256": "e" * 64},
        {"format": "odt"},
        {"coordinates": "unknown"},
        {"text_view": "unknown"},
        {"block_count": None},
        {"block_count": 1},
    ],
)
def test_word_grid_requires_its_actual_word_leaf(updates: dict[str, object]) -> None:
    part = word_summary().model_copy(update={"word_grid_merges": vertical_grids(), **updates})
    with pytest.raises(ValidationError, match="actual input and body manifest"):
        ExtractedPartSummary.model_validate(part)


@pytest.mark.parametrize("field", ["body_manifest_sha256", "notes_manifest_sha256"])
def test_word_grid_rejects_a_different_composite_manifest_component(field: str) -> None:
    wrong = vertical_grids().model_copy(update={field: "e" * 64})
    with pytest.raises(ValidationError, match="actual input and body manifest"):
        ExtractedPartSummary.model_validate(
            word_summary().model_copy(update={"word_grid_merges": wrong})
        )


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"columns": (0, 5)}, "interval escapes"),
        ({"columns": (2, 2)}, "interval escapes"),
        ({"row": 3}, "interval escapes"),
        ({"cells": ((3, 1), (3, 2))}, "increasing XML"),
        ({"cells": ((4, 1), (3, 1))}, "increasing XML"),
        ({"cells": ((3, 1), (3, 1))}, "distinct XML cells"),
    ],
)
def test_malformed_grid_evidence_is_rejected_at_the_extraction_boundary(
    updates: dict[str, object], reason: str
) -> None:
    grids = vertical_grids()
    (table,) = grids.tables
    (merge,) = table.merges
    bad = table.model_copy(update={"merges": (merge.model_copy(update=updates),)})
    part = word_summary().model_copy(
        update={"word_grid_merges": grids.model_copy(update={"tables": (bad,)})}
    )
    with pytest.raises(ValidationError, match=reason):
        ExtractionResult.model_validate(
            result(word_summary()).model_copy(update={"selected_parts": (part,)})
        )


@pytest.mark.parametrize(
    ("second", "reason"),
    [
        (((0, 3), ((4, 2), (5, 2))), "cannot overlap"),
        (((2, 3), ((5, 2), (6, 2))), "XML row differs"),
        (((2, 3), ((4, 0), (5, 0))), "columns require increasing XML"),
    ],
)
def test_multiple_chains_require_consistent_physical_order(
    second: tuple[tuple[int, int], tuple[tuple[int, int], ...]], reason: str
) -> None:
    (table,) = vertical_grids().tables
    columns, cells = second
    with pytest.raises(ValidationError, match=reason):
        WordVerticalGridTable(
            path=table.path,
            columns=table.columns,
            rows=table.rows,
            merges=(*table.merges, WordVerticalMerge(row=2, columns=columns, cells=cells)),
        )


def test_word_grids_share_one_byte_budget_across_parts() -> None:
    grid = WordVerticalGridMerges(
        input_sha256="a" * 64,
        body_manifest_sha256="d" * 64,
        notes_manifest_sha256="b" * 64,
        verified_tables=1,
        unresolved_tables=0,
        tables=(
            WordVerticalGridTable(
                path=(0, 9),
                columns=1,
                rows=44,
                merges=tuple(
                    WordVerticalMerge(
                        row=i,
                        columns=(0, 1),
                        cells=((2**62 + i, 2**62), (2**62 + i + 1, 2**62)),
                    )
                    for i in range(0, 44, 2)
                ),
            ),
        ),
    )
    part = word_summary().model_copy(update={"word_grid_merges": grid, "block_count": 44})
    second = part.model_copy(update={"physical_index": 1})
    with pytest.raises(ValidationError, match="aggregate byte limit"):
        ExtractionResult.model_validate(
            result(part).model_copy(
                update={
                    "selected_parts": (part, second),
                    "physical_part_count": 2,
                    "selected_part_count": 2,
                }
            )
        )
