from contextlib import closing
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models.artifacts import ArtifactMetadata
from lexinform.models.extractions import ExtractedPartSummary, ExtractionResult
from lexinform.models.word_grid_merges import (
    MAX_WORD_GRID_MERGE_BYTES,
    WordHorizontalGridSpans,
    WordHorizontalGridTable,
    WordHorizontalSpan,
)
from tests.unit.test_odt_note_status import result
from tests.unit.test_word_vertical_grid_history import vertical_grids, word_summary


def horizontal() -> WordHorizontalGridSpans:
    vertical = vertical_grids()
    return WordHorizontalGridSpans(
        input_sha256=vertical.input_sha256,
        body_manifest_sha256=vertical.body_manifest_sha256,
        notes_manifest_sha256=vertical.notes_manifest_sha256,
        tables=(
            WordHorizontalGridTable(
                path=(0, 278),
                columns=4,
                rows=16,
                spans=(WordHorizontalSpan(row=13, columns=(0, 4), cell=(15, 0)),),
            ),
        ),
    )


def test_horizontal_and_vertical_maps_restore_without_rewriting_vertical_history() -> None:
    part = word_summary().model_copy(update={"word_grid_merges": vertical_grids()})
    baseline = result(part)
    candidate = result(part.model_copy(update={"word_horizontal_spans": horizontal()}))
    assert "word_horizontal_spans" not in baseline.model_dump_json()
    assert baseline.result_id != candidate.result_id
    now = datetime(2026, 11, 9, tzinfo=UTC)
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_artifact_metadata(
            ArtifactMetadata(sha256=baseline.artifact_sha256, byte_size=120, recorded_at=now)
        )
        for extraction in (baseline, candidate):
            repo.save_extraction_result(extraction, recorded_at=now)
        sql = repo.dump()
        repo.restore(sql)
        assert repo.dump() == sql
        for extraction in (baseline, candidate):
            assert repo.get_extraction_result(extraction.result_id) == extraction
        assert sql.count('"cell":[15,0]') == 1
        with pytest.raises(ValueError):
            repo.restore(sql.replace('"cell":[15,0]', '"cell":[15,-1]'))
        assert repo.dump() == sql


@pytest.mark.parametrize(
    ("second", "reason"),
    [
        ((13, (1, 4), (15, 1)), "intervals cannot overlap"),
        ((13, (2, 3), (15, 1)), "covers one column"),
        ((13, (2, 4), (16, 1)), "XML cells require increasing positions"),
        ((14, (2, 4), (14, 1)), "rows require increasing XML positions"),
    ],
)
def test_horizontal_spans_validate_logical_intervals_and_xml_order(
    second: tuple[int, tuple[int, int], tuple[int, int]], reason: str
) -> None:
    row, columns, cell = second
    with pytest.raises(ValidationError, match=reason):
        WordHorizontalGridTable(
            path=(0, 278),
            columns=4,
            rows=16,
            spans=(
                WordHorizontalSpan(row=13, columns=(0, 2), cell=(15, 0)),
                WordHorizontalSpan(row=row, columns=columns, cell=cell),
            ),
        )


def test_horizontal_tables_require_their_own_manifest_and_scope() -> None:
    grids = horizontal()
    (table,) = grids.tables
    part = word_summary().model_copy(update={"word_grid_merges": vertical_grids()})
    for altered, reason in (
        (
            grids.model_copy(update={"body_manifest_sha256": "f" * 64}),
            "actual input and body manifest",
        ),
        (
            grids.model_copy(update={"tables": (table.model_copy(update={"path": (0, 87)}),)}),
            "cannot include retained vertical merges",
        ),
    ):
        with pytest.raises(ValidationError, match=reason):
            ExtractedPartSummary.model_validate(
                part.model_copy(update={"word_horizontal_spans": altered})
            )


def test_horizontal_and_vertical_maps_share_the_original_aggregate_budget() -> None:
    horizontal_grid = horizontal()
    vertical_grid = vertical_grids()
    horizontal_grid = WordHorizontalGridSpans.model_validate(
        horizontal_grid.model_copy(
            update={
                "tables": (
                    WordHorizontalGridTable(
                        path=(0, 278),
                        columns=4,
                        rows=48,
                        spans=tuple(
                            WordHorizontalSpan(row=row, columns=(0, 4), cell=(2**62 + row, 2**62))
                            for row in range(48)
                        ),
                    ),
                )
            }
        )
    )
    assert (
        len(horizontal_grid.model_dump_json().encode())
        + len(vertical_grid.model_dump_json().encode())
        > MAX_WORD_GRID_MERGE_BYTES
    ), "each fixture fits on its own but their aggregate does not"
    part = word_summary().model_copy(
        update={
            "word_grid_merges": vertical_grid,
            "word_horizontal_spans": horizontal_grid,
            "block_count": 100,
        }
    )
    with pytest.raises(ValidationError, match="aggregate byte limit"):
        ExtractionResult.model_validate(
            result(word_summary()).model_copy(update={"selected_parts": (part,)})
        )
