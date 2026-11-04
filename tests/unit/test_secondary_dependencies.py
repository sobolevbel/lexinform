import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal

import pytest
from tools.computation_doctor import audit

from lexinform.adapters.secondary_dependencies import SecondaryDependencyError
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    AmendmentsRecord,
    ComputationResult,
    DocumentReviewRecord,
    JointRecord,
    ProcessSummary,
    ReviewInputReference,
    ReviewResultRecord,
    StatusChange,
)
from tests.computations import NOW, result_record, retained_result

Purpose = Literal[
    "amendments", "joint", "consultation_review", "commission_review", "act_application"
]
PURPOSES: tuple[Purpose, ...] = (
    "amendments",
    "joint",
    "consultation_review",
    "commission_review",
    "act_application",
)
type Record = AmendmentsRecord | JointRecord | DocumentReviewRecord
REVIEW_COLUMNS = {
    "consultation_review": "consultation_review_json",
    "commission_review": "commission_review_json",
    "act_application": "act_application_review_json",
}


def record_for(purpose: Purpose) -> tuple[ComputationResult, Record]:
    result = retained_result(purpose)
    record = result.record
    assert isinstance(record, (AmendmentsRecord, JointRecord, DocumentReviewRecord)), (
        "the fixture creates the requested secondary type"
    )
    return result, record


def save(repo: SqliteBillRepository, record: Record, change_id: int) -> None:
    if isinstance(record, AmendmentsRecord):
        repo.save_status_change_amendments(change_id, record)
    elif isinstance(record, JointRecord):
        repo.save_joint_comparison(10, "3039", record)
    else:
        repo.save_document_review(10, "3039", record)


def load(repo: SqliteBillRepository, purpose: Purpose, change_id: int) -> Record | None:
    if purpose == "amendments":
        change = repo.get_status_change(change_id)
        assert change is not None, "the fixture inserted one status change"
        return change.amendments
    bill = repo.get(10, "3039")
    assert bill is not None, "the fixture inserted this bill"
    return {
        "joint": bill.joint,
        "consultation_review": bill.consultation_review,
        "commission_review": bill.commission_review,
        "act_application": bill.act_application_review,
    }[purpose]


def setup(
    tmp_path: Path, process_3039: ProcessSummary, purpose: Purpose
) -> tuple[Path, ComputationResult, Record, int]:
    path = tmp_path / "secondary.db"
    result, record = record_for(purpose)
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.upsert_summary(process_3039, now=NOW)
        repo.save_computation_result(result)
        change_id = repo.add_status_change(
            StatusChange(
                term=10,
                number="3039",
                old_fingerprint=None,
                new_fingerprint="secondary",
                new_stages=[],
                detected_at=NOW,
            )
        )
        assert change_id is not None, "the first unique change is inserted"
        save(repo, record, change_id)
    return path, result, record, change_id


@pytest.mark.parametrize("purpose", PURPOSES)
def test_public_save_get_and_restore_accept_known_typed_result(
    purpose: Purpose, tmp_path: Path, process_3039: ProcessSummary
) -> None:
    path, result, record, change_id = setup(tmp_path, process_3039, purpose)
    with closing(SqliteBillRepository(path)) as repo:
        assert load(repo, purpose, change_id) == record
        dump = repo.dump()
        repo.restore(dump)
        assert load(repo, purpose, change_id) == record and repo.dump() == dump
        assert repo.get_computation_result(result.result_id) == result
    before = path.read_bytes()
    assert not audit(path)["findings"] and path.read_bytes() == before


@pytest.mark.parametrize("purpose", PURPOSES)
@pytest.mark.parametrize("fault", ["missing", "failed", "purpose", "request", "columns"])
def test_known_bad_edges_reject_reads_and_restore_atomically(
    purpose: Purpose, fault: str, tmp_path: Path, process_3039: ProcessSummary
) -> None:
    path, result, record, change_id = setup(tmp_path, process_3039, purpose)
    with closing(SqliteBillRepository(path)) as repo:
        original = repo.dump()
    with closing(sqlite3.connect(path)) as connection, connection:
        if fault == "missing":
            connection.execute("DELETE FROM computation_results")
        elif fault == "columns":
            connection.execute("UPDATE computation_results SET number='different'")
        elif fault == "failed":
            value = result.model_dump(mode="json")
            value.update(validation="error", record=None, error="provider failure")
            connection.execute("UPDATE computation_results SET result_json=?", (json.dumps(value),))
        else:
            changed = record.model_copy(update={"request_sha256": "b" * 64})
            if fault == "purpose":
                other = retained_result("supplement")
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
                changed = record.model_copy(update={"computation_id": other.result_id})
            table, column = (
                ("status_changes", "amendments_json")
                if purpose == "amendments"
                else ("bills", "joint_json" if purpose == "joint" else REVIEW_COLUMNS[purpose])
            )
            connection.execute(f"UPDATE {table} SET {column}=?", (changed.model_dump_json(),))
    before = path.read_bytes()
    report = audit(path)
    assert any(row["code"].startswith(purpose + "_") for row in report["findings"])
    assert path.read_bytes() == before
    with closing(SqliteBillRepository(path)) as repo:
        damaged = repo.dump()
        with pytest.raises(SecondaryDependencyError):
            load(repo, purpose, change_id)
        repo.restore(original)
        with pytest.raises(SecondaryDependencyError):
            repo.restore(damaged)
        assert repo.dump() == original


@pytest.mark.parametrize("purpose", PURPOSES)
def test_unbound_legacy_and_foreign_paid_owner_zero_reuse_remain_valid(
    purpose: Purpose, tmp_path: Path, process_3039: ProcessSummary
) -> None:
    path, result, record, change_id = setup(tmp_path, process_3039, purpose)
    legacy = result_record(purpose)
    assert isinstance(legacy, (AmendmentsRecord, JointRecord, DocumentReviewRecord)), (
        "the old fixture has its typed record"
    )
    with closing(SqliteBillRepository(path)) as repo:
        save(repo, legacy, change_id)
        assert load(repo, purpose, change_id) == legacy
    with closing(sqlite3.connect(path)) as connection, connection:
        foreign = result.model_copy(update={"term": 9, "number": "paid-original"})
        connection.execute(
            "UPDATE computation_results SET term=?,number=?,result_json=?",
            (foreign.term, foreign.number, foreign.model_dump_json()),
        )
    reused = record.model_copy(
        update={
            "request_sha256": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
    )
    with closing(SqliteBillRepository(path)) as repo:
        save(repo, reused, change_id)
        assert load(repo, purpose, change_id) == reused
        repo.restore(repo.dump())


@pytest.mark.parametrize("purpose", PURPOSES[2:])
def test_schema3_compact_review_retains_full_projection_without_context_equality(
    purpose: Purpose, tmp_path: Path, process_3039: ProcessSummary
) -> None:
    path, result, record, change_id = setup(tmp_path, process_3039, purpose)
    assert isinstance(record, DocumentReviewRecord), "review purposes use a contextful projection"
    compact = ComputationResult.model_validate_json(
        result.model_copy(
            update={
                "schema_version": 3,
                "record": ReviewResultRecord.from_record(record),
                "review_input": ReviewInputReference.from_context(record.context, term=10),
            }
        ).model_dump_json()
    )
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "UPDATE computation_results SET result_json=?", (compact.model_dump_json(),)
        )
    projected = record.model_copy(
        update={
            "context": record.context.model_copy(update={"title": "Later display title"}),
            "input_tokens": 0,
        }
    )
    with closing(SqliteBillRepository(path)) as repo:
        save(repo, projected, change_id)
        assert load(repo, purpose, change_id) == projected
        repo.restore(repo.dump())


@pytest.mark.parametrize("purpose", PURPOSES)
def test_bad_new_reference_is_rejected_before_preservation_or_update(
    purpose: Purpose, tmp_path: Path, process_3039: ProcessSummary
) -> None:
    path, _, record, change_id = setup(tmp_path, process_3039, purpose)
    missing = record.model_copy(update={"computation_id": "result-v1:" + "f" * 64})
    with closing(SqliteBillRepository(path)) as repo:
        before = repo.dump()
        with pytest.raises(SecondaryDependencyError):
            save(repo, missing, change_id)
        if purpose == "amendments":
            with pytest.raises(SecondaryDependencyError):
                repo.add_status_change(
                    StatusChange(
                        term=10,
                        number="3039",
                        old_fingerprint=None,
                        new_fingerprint="bad-new",
                        new_stages=[],
                        amendments=missing,
                        detected_at=NOW,
                    )
                )
        assert repo.dump() == before


@pytest.mark.parametrize("purpose", PURPOSES[2:])
@pytest.mark.parametrize("fault", ["context", "column"])
def test_review_column_and_projected_review_context_purposes_must_agree(
    purpose: Purpose, fault: str, tmp_path: Path, process_3039: ProcessSummary
) -> None:
    path, _, record, change_id = setup(tmp_path, process_3039, purpose)
    assert isinstance(record, DocumentReviewRecord), "a review carrier supplies both purpose fields"
    other = "commission_review" if purpose != "commission_review" else "consultation_review"
    changed = record.model_copy(
        update={"context": record.context.model_copy(update={"purpose": other})}
    )
    if fault == "column":
        changed = changed.model_copy(
            update={"review": record.review.model_copy(update={"purpose": other})}
        )
    with closing(SqliteBillRepository(path)) as repo:
        before = repo.dump()
        with pytest.raises(SecondaryDependencyError):
            save(repo, changed, change_id)
        assert repo.dump() == before
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            f"UPDATE bills SET {REVIEW_COLUMNS[purpose]}=?", (changed.model_dump_json(),)
        )
    report = audit(path)
    assert any(
        row["code"] == purpose + "_projection_purpose_mismatch" for row in report["findings"]
    )
    with closing(SqliteBillRepository(path)) as repo:
        damaged = repo.dump()
        with pytest.raises(SecondaryDependencyError):
            load(repo, purpose, change_id)
        repo.restore(before)
        with pytest.raises(SecondaryDependencyError):
            repo.restore(damaged)
        assert repo.dump() == before


@pytest.mark.parametrize("fault", ["duplicate", "deep"])
def test_shared_result_decoder_reports_duplicate_and_deep_json_as_invalid(
    tmp_path: Path, process_3039: ProcessSummary, fault: str
) -> None:
    path, _, _, change_id = setup(tmp_path, process_3039, "joint")
    with closing(sqlite3.connect(path)) as connection, connection:
        raw = connection.execute("SELECT result_json FROM computation_results").fetchone()[0]
        invalid = (
            raw.replace('"purpose":"joint"', '"purpose":"name","purpose":"joint"')
            if fault == "duplicate"
            else "[" * 2000 + "0" + "]" * 2000
        )
        assert invalid != raw
        connection.execute("UPDATE computation_results SET result_json=?", (invalid,))
    assert "joint_result_invalid" in {item["code"] for item in audit(path)["findings"]}
    with (
        closing(SqliteBillRepository(path)) as repo,
        pytest.raises(SecondaryDependencyError, match="result_invalid"),
    ):
        load(repo, "joint", change_id)


@pytest.mark.parametrize("purpose", PURPOSES[2:])
def test_legacy_without_reference_keeps_prior_review_purpose_typing(
    purpose: Purpose, tmp_path: Path, process_3039: ProcessSummary
) -> None:
    path, _, _, change_id = setup(tmp_path, process_3039, purpose)
    legacy = result_record(purpose)
    assert isinstance(legacy, DocumentReviewRecord), "the legacy fixture is a contextful review"
    other = "commission_review" if purpose != "commission_review" else "consultation_review"
    legacy = legacy.model_copy(
        update={"context": legacy.context.model_copy(update={"purpose": other})}
    )
    assert legacy.computation_id is None
    with closing(SqliteBillRepository(path)) as repo:
        save(repo, legacy, change_id)
        assert load(repo, purpose, change_id) == legacy
        repo.restore(repo.dump())
