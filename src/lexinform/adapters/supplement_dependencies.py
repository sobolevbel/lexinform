"""Validate known paid references on typed status-change supplements without owner inference."""

import sqlite3
from collections.abc import Iterable, Iterator

from lexinform.json_storage import read_json
from lexinform.models.analysis import SupplementRecord
from lexinform.models.computations import ComputationResult


class SupplementDependencyError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def validate_supplement_dependencies(
    connection: sqlite3.Connection, records: Iterable[SupplementRecord]
) -> None:
    for record in records:
        if record.computation_id is None:
            continue
        try:
            projected = SupplementRecord.model_validate_json(record.model_dump_json())
        except ValueError, TypeError:
            raise SupplementDependencyError("invalid_supplement_projection") from None
        cursor = connection.cursor()
        cursor.row_factory = sqlite3.Row
        try:
            row = cursor.execute(
                "SELECT * FROM computation_results WHERE result_id=?", (projected.computation_id,)
            ).fetchone()
        finally:
            cursor.close()
        if row is None:
            raise SupplementDependencyError("supplement_result_unavailable")
        try:
            result = ComputationResult.model_validate(read_json(row["result_json"]))
        except ValueError, TypeError:
            raise SupplementDependencyError("supplement_result_invalid") from None
        if (
            result.result_id,
            result.provider,
            result.batch_id,
            result.custom_id,
            result.term,
            result.number,
        ) != tuple(
            row[key] for key in ("result_id", "provider", "batch_id", "custom_id", "term", "number")
        ) or result.result_id != projected.computation_id:
            raise SupplementDependencyError("supplement_result_columns_mismatch")
        if result.validation != "validated":
            raise SupplementDependencyError("supplement_result_failed")
        if result.purpose != "supplement" or not isinstance(result.record, SupplementRecord):
            raise SupplementDependencyError("supplement_result_purpose_mismatch")
        if (
            projected.request_sha256 is not None
            and result.request_sha256 is not None
            and projected.request_sha256 != result.request_sha256
        ):
            raise SupplementDependencyError("supplement_request_mismatch")


def supplement_dependency_issues(connection: sqlite3.Connection) -> Iterator[tuple[str, str]]:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(status_changes)")}
    if not {"id", "supplements_json"} <= columns:
        return
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        for row in cursor.execute(
            "SELECT id, supplements_json FROM status_changes "
            "WHERE supplements_json IS NOT NULL ORDER BY id"
        ):
            try:
                values = read_json(row["supplements_json"])
            except ValueError, TypeError, RecursionError:
                continue
            if not isinstance(values, list):
                continue
            for index, value in enumerate(values):
                if not isinstance(value, dict) or value.get("computation_id") is None:
                    continue
                ref = f"status_changes/{row['id']}/supplements_json/{index}"
                try:
                    record = SupplementRecord.model_validate(value)
                except ValueError, TypeError:
                    yield "invalid_supplement_projection", ref
                    continue
                try:
                    validate_supplement_dependencies(connection, (record,))
                except SupplementDependencyError as error:
                    yield error.code, ref
    finally:
        cursor.close()


def validate_status_supplement_dependencies(connection: sqlite3.Connection) -> None:
    issue = next(supplement_dependency_issues(connection), None)
    if issue is not None:
        raise SupplementDependencyError(issue[0])
