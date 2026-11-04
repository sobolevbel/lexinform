"""Validate known paid references on typed status-change supplements without owner inference."""

import sqlite3
from collections.abc import Iterable, Iterator

from lexinform.adapters.secondary_dependencies import (
    SecondaryDependencyError,
    validate_secondary_dependency,
)
from lexinform.json_storage import read_json
from lexinform.models.analysis import SupplementRecord


class SupplementDependencyError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def validate_supplement_dependencies(
    connection: sqlite3.Connection, records: Iterable[SupplementRecord]
) -> None:
    for record in records:
        try:
            validate_secondary_dependency(connection, record, "supplement")
        except SecondaryDependencyError as error:
            code = (
                "invalid_supplement_projection"
                if error.code == "invalid_projection"
                else "supplement_" + error.code
            )
            raise SupplementDependencyError(code) from None


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
