import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from tools.computation_doctor import audit

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.supplement_dependencies import SupplementDependencyError
from lexinform.models import ComputationResult, ProcessSummary, StatusChange, SupplementRecord
from tests.computations import NOW, result_record, retained_result


def change(record: SupplementRecord, *, number: str = "3039") -> StatusChange:
    return StatusChange(
        term=10,
        number=number,
        old_fingerprint=None,
        new_fingerprint="supplement",
        new_stages=[],
        supplements=[record],
        detected_at=NOW,
    )


def database(tmp_path: Path, process_3039: ProcessSummary) -> tuple[Path, ComputationResult, int]:
    path = tmp_path / "state.db"
    result = retained_result("supplement")
    assert isinstance(result.record, SupplementRecord), (
        "the fixture retains a normalized supplement"
    )
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.upsert_summary(process_3039, now=NOW)
        repo.save_computation_result(result)
        identity = repo.add_status_change(change(result.record))
        assert identity is not None, "the first unique status change is inserted"
    return path, result, identity


def test_public_add_save_get_list_and_restore_preserve_typed_modern_and_unknown_legacy_records(
    tmp_path: Path, process_3039: ProcessSummary
) -> None:
    path, result, identity = database(tmp_path, process_3039)
    assert isinstance(result.record, SupplementRecord), "the result is a typed supplement"
    legacy = result_record("supplement")
    assert isinstance(legacy, SupplementRecord), "the legacy fixture has no computation reference"
    with closing(SqliteBillRepository(path)) as repo:
        repo.save_status_change_supplements(identity, [legacy, result.record])
        saved = repo.get_status_change(identity)
        assert saved is not None, "the unique inserted change is retained"
        assert saved.supplements == [legacy, result.record]
        assert repo.list_status_changes(10, "3039") == [saved]
        dump = repo.dump()
        repo.restore(dump)
        assert repo.dump() == dump and repo.get_status_change(identity) == saved
        assert repo.get_computation_result(result.result_id) == result
    before = path.read_bytes()
    assert not audit(path)["findings"]
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "fault,code",
    [
        ("missing", "supplement_result_unavailable"),
        ("invalid_result", "supplement_result_invalid"),
        ("failed", "supplement_result_failed"),
        ("purpose", "supplement_result_purpose_mismatch"),
        ("request", "supplement_request_mismatch"),
        ("columns", "supplement_result_columns_mismatch"),
    ],
)
def test_corrupt_known_dependencies_fail_public_reads_and_atomic_restore(
    tmp_path: Path, process_3039: ProcessSummary, fault: str, code: str
) -> None:
    path, result, identity = database(tmp_path, process_3039)
    with closing(SqliteBillRepository(path)) as repo:
        original = repo.dump()
    with closing(sqlite3.connect(path)) as connection, connection:
        if fault == "missing":
            connection.execute("DELETE FROM computation_results")
        elif fault == "invalid_result":
            connection.execute("UPDATE computation_results SET result_json='bad'")
        elif fault == "failed":
            value = result.model_dump(mode="json")
            value.update(validation="error", record=None, error="provider failure")
            connection.execute("UPDATE computation_results SET result_json=?", (json.dumps(value),))
        elif fault == "purpose":
            other = retained_result("amendments")
            connection.execute("DELETE FROM computation_results")
            connection.execute(
                "INSERT INTO computation_results VALUES(?,?,?,?,?,?,?)",
                (
                    other.result_id,
                    other.provider,
                    other.batch_id,
                    other.custom_id,
                    other.term,
                    other.number,
                    other.model_dump_json(),
                ),
            )
            value = json.loads(
                connection.execute("SELECT supplements_json FROM status_changes").fetchone()[0]
            )
            value[0]["computation_id"] = other.result_id
            connection.execute("UPDATE status_changes SET supplements_json=?", (json.dumps(value),))
        elif fault == "request":
            connection.execute(
                "UPDATE status_changes SET supplements_json=json_set(supplements_json,'$[0].request_sha256',?)",
                ("b" * 64,),
            )
        else:
            connection.execute("UPDATE computation_results SET number='another'")
    before = path.read_bytes()
    report = audit(path)
    assert code in {item["code"] for item in report["findings"]}
    assert path.read_bytes() == before
    with closing(SqliteBillRepository(path)) as repo:
        damaged = repo.dump()
        with pytest.raises(SupplementDependencyError) as error:
            repo.get_status_change(identity)
        assert error.value.code == code
        with pytest.raises(SupplementDependencyError):
            repo.list_status_changes(10, "3039")
        repo.restore(original)
        with pytest.raises(SupplementDependencyError):
            repo.restore(damaged)
        assert repo.dump() == original


def test_public_add_and_save_reject_dangling_reference_without_writes(
    tmp_path: Path, process_3039: ProcessSummary
) -> None:
    path, _, identity = database(tmp_path, process_3039)
    missing = retained_result("supplement").record
    assert isinstance(missing, SupplementRecord), "the fixture creates a modern supplement"
    missing = missing.model_copy(update={"computation_id": "result-v1:" + "f" * 64})
    with closing(SqliteBillRepository(path)) as repo:
        before = repo.dump()
        with pytest.raises(SupplementDependencyError):
            repo.save_status_change_supplements(identity, [missing])
        with pytest.raises(SupplementDependencyError):
            repo.add_status_change(change(missing).model_copy(update={"new_fingerprint": "new"}))
        assert repo.dump() == before


def test_original_paid_owner_and_missing_request_hash_are_not_reconstructed(
    tmp_path: Path, process_3039: ProcessSummary
) -> None:
    path = tmp_path / "foreign.db"
    result = retained_result("supplement").model_copy(
        update={"term": 9, "number": "original-owner"}
    )
    assert isinstance(result.record, SupplementRecord), (
        "the paid result remains its own typed record"
    )
    projected = result.record.model_copy(update={"request_sha256": None, "input_tokens": 0})
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.upsert_summary(process_3039, now=NOW)
        repo.save_computation_result(result)
        identity = repo.add_status_change(change(projected))
        assert identity is not None, (
            "a verified typed reference does not infer current-owner equality"
        )
        saved = repo.get_status_change(identity)
        assert saved is not None, "the inserted status change is retained"
        assert saved.supplements == [projected]
        repo.restore(repo.dump())
