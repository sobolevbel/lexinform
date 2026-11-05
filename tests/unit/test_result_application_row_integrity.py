import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from lexinform.adapters.computation_history import ComputationRowError
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import ResultApplication
from tests.computations import NOW, retained_result
from tests.unit.test_result_application_targets import receipt

COLUMNS: tuple[tuple[str, int | str], ...] = (
    ("result_id", "result-v1:" + "b" * 64),
    ("term", 11),
    ("number", "other-owner"),
    ("generation", 1),
    ("phase", "projection"),
)


def primary_application(result_id: str) -> ResultApplication:
    return ResultApplication(
        result_id=result_id,
        term=10,
        number="3039",
        generation=0,
        phase="collection",
        outcome="ready",
        reason="current_generation",
        observed_generation=0,
        observed_status=None,
        created_at=NOW,
    )


@pytest.mark.parametrize(("column", "value"), COLUMNS)
def test_public_application_list_rejects_indexed_identity_corruption(
    tmp_path: Path, column: str, value: int | str
) -> None:
    path = tmp_path / "state.db"
    result = retained_result("analysis")
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_computation_result(result)
        repo.save_result_application(primary_application(result.result_id))
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(f"UPDATE result_applications SET {column}=?", (value,))
        connection.commit()
        term, number = connection.execute("SELECT term,number FROM result_applications").fetchone()
    with closing(SqliteBillRepository(path)) as repo:
        before = path.read_bytes()
        with pytest.raises(ComputationRowError, match="^application_columns_mismatch$"):
            repo.list_result_applications(term, number)
        assert path.read_bytes() == before


def test_existing_positive_ids_nullable_generation_and_opaque_targets_keep_order(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    primary, secondary = retained_result("analysis"), retained_result("supplement")
    original = primary_application(primary.result_id)
    opaque = receipt(secondary.result_id, "future_projection", "opaque-reference-v1")
    assert original.projected_generation is None and opaque.generation is None
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        for result in (primary, secondary):
            repo.save_computation_result(result)
        for application in (original, opaque):
            repo.save_result_application(application)
        before = repo.dump()
        assert repo.list_result_applications(10, "3039") == [original, opaque]
        assert repo.list_result_applications(11, "3039") == []
        assert repo.dump() == before
        repo.restore(before)
        assert repo.list_result_applications(10, "3039") == [original, opaque]
    with closing(sqlite3.connect(path)) as connection:
        ids = [
            row[0]
            for row in connection.execute("SELECT id FROM result_applications ORDER BY rowid")
        ]
    assert ids == [1, 2], (
        "the public writer and restore preserve actual positive IDs in insertion order"
    )
