"""Check finite typed secondary projections without inferring owners or comparing answer content."""

import sqlite3
from collections.abc import Iterator
from typing import Literal

from lexinform.adapters.computation_history import ComputationRowError, read_computation_result
from lexinform.json_storage import read_json
from lexinform.models.analysis import AmendmentsRecord, JointRecord, SupplementRecord
from lexinform.models.computations import ReviewResultRecord
from lexinform.models.reviews import DocumentReviewRecord

type SecondaryPurpose = Literal[
    "supplement",
    "amendments",
    "joint",
    "consultation_review",
    "commission_review",
    "act_application",
]
type ProjectedSecondaryRecord = (
    SupplementRecord | AmendmentsRecord | JointRecord | DocumentReviewRecord
)
_PROJECTED: dict[SecondaryPurpose, type[ProjectedSecondaryRecord]] = {
    "supplement": SupplementRecord,
    "amendments": AmendmentsRecord,
    "joint": JointRecord,
    "consultation_review": DocumentReviewRecord,
    "commission_review": DocumentReviewRecord,
    "act_application": DocumentReviewRecord,
}
_CARRIERS: tuple[tuple[str, str, SecondaryPurpose, type[ProjectedSecondaryRecord]], ...] = (
    ("status_changes", "amendments_json", "amendments", AmendmentsRecord),
    ("bills", "joint_json", "joint", JointRecord),
    ("bills", "consultation_review_json", "consultation_review", DocumentReviewRecord),
    ("bills", "commission_review_json", "commission_review", DocumentReviewRecord),
    ("bills", "act_application_review_json", "act_application", DocumentReviewRecord),
)


class SecondaryDependencyError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def validate_secondary_dependency(
    connection: sqlite3.Connection, record: ProjectedSecondaryRecord, purpose: SecondaryPurpose
) -> None:
    if record.computation_id is None:
        return
    try:
        if not isinstance(record, _PROJECTED[purpose]):
            raise SecondaryDependencyError("invalid_projection")
        projected = type(record).model_validate_json(record.model_dump_json())
    except ValueError, TypeError:
        raise SecondaryDependencyError("invalid_projection") from None
    if isinstance(projected, DocumentReviewRecord) and (
        projected.review.purpose != purpose or projected.context.purpose != purpose
    ):
        raise SecondaryDependencyError("projection_purpose_mismatch")
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        row = cursor.execute(
            "SELECT * FROM computation_results WHERE result_id=?", (projected.computation_id,)
        ).fetchone()
    finally:
        cursor.close()
    if row is None:
        raise SecondaryDependencyError("result_unavailable")
    try:
        result = read_computation_result(row)
    except ComputationRowError as error:
        raise SecondaryDependencyError(
            "result_columns_mismatch"
            if error.code == "computation_columns_mismatch"
            else "result_invalid"
        ) from None
    if result.result_id != projected.computation_id:
        raise SecondaryDependencyError("result_columns_mismatch")
    if result.validation != "validated":
        raise SecondaryDependencyError("result_failed")
    expected = (
        (DocumentReviewRecord, ReviewResultRecord)
        if isinstance(projected, DocumentReviewRecord)
        else (_PROJECTED[purpose],)
    )
    if result.purpose != purpose or not isinstance(result.record, expected):
        raise SecondaryDependencyError("result_purpose_mismatch")
    if (
        projected.request_sha256 is not None
        and result.request_sha256 is not None
        and projected.request_sha256 != result.request_sha256
    ):
        raise SecondaryDependencyError("request_mismatch")


def secondary_dependency_issues(connection: sqlite3.Connection) -> Iterator[tuple[str, str]]:
    for table, column, purpose, model in _CARRIERS:
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        keys = ("id",) if table == "status_changes" else ("term", "number")
        if not {column, *keys} <= existing:
            continue
        cursor = connection.cursor()
        cursor.row_factory = sqlite3.Row
        try:
            for row in cursor.execute(
                f"SELECT {', '.join((*keys, column))} FROM {table} "
                f"WHERE {column} IS NOT NULL ORDER BY {', '.join(keys)}"
            ):
                try:
                    value = read_json(row[column])
                except ValueError, TypeError, RecursionError:
                    continue
                if not isinstance(value, dict) or value.get("computation_id") is None:
                    continue
                ref = "/".join((table, *(str(row[key]) for key in keys), column))
                try:
                    record = model.model_validate(value)
                except ValueError, TypeError:
                    yield f"{purpose}_invalid_projection", ref
                    continue
                try:
                    validate_secondary_dependency(connection, record, purpose)
                except SecondaryDependencyError as error:
                    yield f"{purpose}_{error.code}", ref
        finally:
            cursor.close()


def validate_secondary_dependencies(connection: sqlite3.Connection) -> None:
    issue = next(secondary_dependency_issues(connection), None)
    if issue is not None:
        raise SecondaryDependencyError(issue[0])
