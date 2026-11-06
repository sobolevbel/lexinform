"""Read and validate the existing immutable legacy archive without new historical inference."""

import sqlite3
from contextlib import closing

from lexinform.legacy_results import (
    LEGACY_PROJECTION_COLUMNS,
    legacy_analysis_body,
    materialize_legacy_value,
    read_legacy_json,
)
from lexinform.models import LegacyResultImport, LegacyResultOccurrence, LegacyResultValue


def read_legacy_result(connection: sqlite3.Connection, value_id: str) -> LegacyResultValue | None:
    row = connection.execute(
        "SELECT envelope_json, analysis_body_sha256 FROM legacy_result_values WHERE value_id = ?",
        (value_id,),
    ).fetchone()
    if row is None:
        return None
    body = None
    if row[1] is not None:
        body = connection.execute(
            "SELECT body_json FROM legacy_analysis_bodies WHERE body_sha256 = ?", (row[1],)
        ).fetchone()
    value = materialize_legacy_value(
        row[0], body_sha256=row[1], body_json=body[0] if body else None
    )
    if value.value_id != value_id:
        raise ValueError("immutable legacy value checksum mismatch")
    return value


def validate_legacy_import(connection: sqlite3.Connection) -> None:
    for body_sha, raw in connection.execute(
        "SELECT body_sha256, body_json FROM legacy_analysis_bodies"
    ):
        legacy_analysis_body(raw, body_sha)
    for table in ("legacy_result_values", "legacy_result_occurrences"):
        if connection.execute(f"PRAGMA foreign_key_check({table})").fetchone() is not None:
            raise ValueError("legacy result import has a broken reference")
    markers = connection.execute(
        "SELECT manifest_sha256, imported_at, source_sha256 FROM legacy_result_imports"
    ).fetchall()
    if len(markers) > 1:
        raise ValueError("legacy result import has more than one marker")
    values: list[LegacyResultValue] = []
    for (value_id,) in connection.execute("SELECT value_id FROM legacy_result_values"):
        value = read_legacy_result(connection, value_id)
        assert value is not None, "the selected legacy result row exists on this connection"
        values.append(value)
    occurrences = tuple(
        LegacyResultOccurrence.model_validate(
            {
                "value_id": row[0],
                "source_table": row[1],
                "source_key_json": row[2],
                "source_column": row[3],
                "source_path": row[4],
                "container_term": row[5],
                "container_number": row[6],
                "purpose": row[7],
            }
        )
        for row in connection.execute(
            "SELECT value_id, source_table, source_key_json, source_column, source_path,"
            " container_term, container_number, purpose FROM legacy_result_occurrences"
        )
    )
    if not markers:
        if (
            values
            or occurrences
            or connection.execute("SELECT 1 FROM legacy_analysis_bodies LIMIT 1").fetchone()
        ):
            raise ValueError("legacy result rows require an import marker")
        return
    manifest_sha, imported_at, source_sha = markers[0]
    imported = LegacyResultImport(
        imported_at=imported_at,
        source_sha256=source_sha,
        values=tuple(values),
        occurrences=occurrences,
    )
    if manifest_sha != imported.manifest_sha256:
        raise ValueError("legacy result import manifest checksum mismatch")


def validate_legacy_projections(connection: sqlite3.Connection) -> None:
    def read(value_id: str) -> LegacyResultValue | None:
        return read_legacy_result(connection, value_id)

    validate_legacy_import(connection)
    with closing(connection.cursor()) as cursor:
        cursor.row_factory = None
        for table in (
            "bills",
            "status_changes",
            "publications",
            "analysis_memo",
            "document_work",
            "llm_batch_items",
            "llm_batch_intents",
        ):
            columns = {
                row[1] for row in cursor.execute(f"PRAGMA table_info({table})")
            } & LEGACY_PROJECTION_COLUMNS
            for column in columns:
                for (raw,) in cursor.execute(
                    f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL"
                ):
                    read_legacy_json(raw, read)
