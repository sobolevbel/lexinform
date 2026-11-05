import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal

import pytest
from tools.computation_doctor import audit

from lexinform.adapters.computation_history import ComputationRowError
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    ComputationResult,
    DocumentReviewRecord,
    ReviewInputReference,
    ReviewResultRecord,
)
from tests.computations import KINDS, retained_result

Reader = Literal["get", "list"]
COLUMNS: tuple[tuple[str, int | str], ...] = (
    ("result_id", "result-v1:" + "b" * 64),
    ("provider", "anthropic"),
    ("batch_id", "different-batch"),
    ("custom_id", "different-item"),
    ("term", 11),
    ("number", "different-owner"),
)


def database(path: Path) -> ComputationResult:
    result = retained_result("analysis")
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_computation_result(result)
    return result


def read(repo: SqliteBillRepository, reader: Reader, identity: str, term: int, number: str) -> None:
    if reader == "get":
        repo.get_computation_result(identity)
    else:
        repo.list_computation_results(term, number)


@pytest.mark.parametrize("reader", ["get", "list"])
@pytest.mark.parametrize(("column", "value"), COLUMNS)
def test_public_reads_reject_indexed_identity_corruption(
    tmp_path: Path, reader: Reader, column: str, value: int | str
) -> None:
    path = tmp_path / "state.db"
    database(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(f"UPDATE computation_results SET {column}=?", (value,))
        connection.commit()
        identity, term, number = connection.execute(
            "SELECT result_id,term,number FROM computation_results"
        ).fetchone()
    doctor = audit(path)
    assert any(item["code"] == "computation_columns_mismatch" for item in doctor["findings"])
    assert doctor["summary"]["result_validated"] == 1
    with closing(SqliteBillRepository(path)) as repo:
        before = path.read_bytes()
        with pytest.raises(ComputationRowError, match="^computation_columns_mismatch$"):
            read(repo, reader, identity, term, number)
        assert path.read_bytes() == before
    assert audit(path) == doctor


@pytest.mark.parametrize("reader", ["get", "list"])
@pytest.mark.parametrize("fault", ["invalid", "duplicate", "deep"])
def test_public_reads_use_existing_bounded_json_errors(
    tmp_path: Path, reader: Reader, fault: str
) -> None:
    path = tmp_path / "state.db"
    result = database(path)
    damaged = (
        "{"
        if fault == "invalid"
        else '{"purpose":"analysis","purpose":"analysis"}'
        if fault == "duplicate"
        else "[" * 2000 + "0" + "]" * 2000
    )
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE computation_results SET result_json=?", (damaged,))
        connection.commit()
    doctor = audit(path)
    assert any(item["code"] == "invalid_computation_result" for item in doctor["findings"])
    with closing(SqliteBillRepository(path)) as repo:
        before = path.read_bytes()
        with pytest.raises(ComputationRowError, match="^invalid_computation_result$"):
            read(repo, reader, result.result_id, result.term, result.number)
        assert path.read_bytes() == before
    assert audit(path) == doctor


def test_purpose_filter_keeps_its_sql_json_extract_failure(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    database(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE computation_results SET result_json=?", ("{",))
        connection.commit()
    with (
        closing(SqliteBillRepository(path)) as repo,
        pytest.raises(sqlite3.OperationalError, match="malformed JSON"),
    ):
        repo.list_computation_results(10, "3039", purpose="analysis")


def test_valid_reads_keep_all_existing_purposes_order_and_foreign_legacy_forms(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    results = [retained_result(kind) for kind in KINDS]
    legacy = results[-1]
    record = legacy.record
    assert isinstance(record, DocumentReviewRecord), (
        "the final existing purpose fixture is a full legacy review"
    )
    compact = ComputationResult.model_validate(
        {
            **legacy.model_dump(),
            "schema_version": 3,
            "record": ReviewResultRecord.from_record(record),
            "review_input": ReviewInputReference.from_context(record.context, term=legacy.term),
        }
    )
    results[-1] = compact
    foreign = retained_result("joint").model_copy(update={"term": 9, "number": "paid-owner"})
    results[KINDS.index("joint")] = foreign
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        for result in results:
            repo.save_computation_result(result)
        before = repo.dump()
        for result in results:
            assert repo.get_computation_result(result.result_id) == result
            assert repo.list_computation_results(
                result.term, result.number, purpose=result.purpose
            ) == [result]
        assert repo.list_computation_results(10, "3039") == [
            item for item in results if item is not foreign
        ]
        assert repo.list_computation_results(11, "3039") == []
        assert repo.get_computation_result("result-v1:" + "f" * 64) is None
        assert repo.dump() == before
