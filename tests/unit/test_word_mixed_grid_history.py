from contextlib import closing
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models.artifacts import ArtifactMetadata
from lexinform.models.extractions import ExtractionResult, word_mixed_grid_parent_sha256
from lexinform.models.word_grid_merges import (
    MAX_WORD_GRID_MERGE_BYTES,
    WordHorizontalSpan,
    WordMixedGridSpans,
    WordMixedGridTable,
    WordVerticalGridMerges,
    WordVerticalGridTable,
    WordVerticalMerge,
)
from tests.unit.test_odt_note_status import result
from tests.unit.test_word_vertical_grid_history import vertical_grids, word_summary


def test_mixed_spans_require_disjoint_xml_cells_and_keep_vertical_history() -> None:
    vertical = vertical_grids()
    mixed = WordMixedGridSpans(
        parent_sha256=word_mixed_grid_parent_sha256(vertical),
        tables=(
            WordMixedGridTable(
                path=(0, 87),
                columns=4,
                rows=4,
                observed_spans=2,
                spans=(WordHorizontalSpan(row=1, columns=(2, 4), cell=(3, 2)),),
            ),
        ),
    )
    part = word_summary().model_copy(update={"word_grid_merges": vertical})
    old = result(part)
    candidate = result(part.model_copy(update={"word_mixed_grid_spans": mixed}))
    assert candidate.result_id != old.result_id
    assert candidate.selected_parts[0].word_grid_merges == vertical
    (table,) = mixed.tables
    (span,) = table.spans
    for altered, reason in (
        (mixed.model_copy(update={"parent_sha256": "f" * 64}), "exact retained vertical parent"),
        (
            mixed.model_copy(
                update={
                    "tables": (
                        table.model_copy(
                            update={
                                "spans": (span.model_copy(update={"columns": (1, 3)}),),
                            }
                        ),
                    )
                }
            ),
            "overlap or disagree",
        ),
    ):
        with pytest.raises(ValidationError, match=reason):
            ExtractionResult.model_validate(
                old.model_copy(
                    update={
                        "selected_parts": (
                            part.model_copy(
                                update={
                                    "word_mixed_grid_spans": altered,
                                }
                            ),
                        )
                    }
                )
            )
    now = datetime(2026, 11, 9, tzinfo=UTC)
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_artifact_metadata(
            ArtifactMetadata(
                sha256=old.artifact_sha256,
                byte_size=120,
                recorded_at=now,
            )
        )
        for value in (old, candidate):
            repo.save_extraction_result(value, recorded_at=now)
        dump = repo.dump()
        repo.restore(dump)
        assert repo.dump() == dump
        assert repo.get_extraction_result(old.result_id) == old
        assert repo.get_extraction_result(candidate.result_id) == candidate


def test_mixed_spans_share_the_existing_grid_budget() -> None:
    size = 2**62
    vertical = WordVerticalGridMerges.model_validate(
        vertical_grids().model_copy(
            update={
                "tables": (
                    WordVerticalGridTable(
                        path=(0, 87),
                        columns=4,
                        rows=70,
                        merges=tuple(
                            WordVerticalMerge(
                                row=row,
                                columns=(0, 1),
                                cells=((size + row, size), (size + row + 1, size)),
                            )
                            for row in range(0, 44, 2)
                        ),
                    ),
                )
            }
        )
    )
    mixed = WordMixedGridSpans(
        parent_sha256=word_mixed_grid_parent_sha256(vertical),
        tables=(
            WordMixedGridTable(
                path=(0, 87),
                columns=4,
                rows=70,
                observed_spans=24,
                spans=tuple(
                    WordHorizontalSpan(row=row, columns=(1, 4), cell=(size + row, size + 1))
                    for row in range(44, 68)
                ),
            ),
        ),
    )
    assert (
        len(vertical.model_dump_json().encode()) + len(mixed.model_dump_json().encode())
        > MAX_WORD_GRID_MERGE_BYTES
    )
    baseline = result(
        word_summary().model_copy(update={"word_grid_merges": vertical, "block_count": 70})
    )
    (part,) = baseline.selected_parts
    with pytest.raises(ValidationError, match="aggregate byte limit"):
        ExtractionResult.model_validate(
            baseline.model_copy(
                update={
                    "selected_parts": (part.model_copy(update={"word_mixed_grid_spans": mixed}),)
                }
            )
        )
