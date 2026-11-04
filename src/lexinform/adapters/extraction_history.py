"""Read immutable extraction metadata consistently during lookup, restore and diagnosis."""

import sqlite3

from lexinform.models.extractions import ExtractionResult

EXTRACTION_SCHEMA_VERSION = 56


class ExtractionResultRowError(ValueError):
    code = "invalid_extraction_result"


def read_extraction_result(row: sqlite3.Row) -> ExtractionResult:
    try:
        result = ExtractionResult.model_validate_json(row["result_json"])
    except ValueError, TypeError:
        raise ExtractionResultRowError("invalid stored extraction result") from None
    if (result.result_id, result.artifact_sha256) != (
        row["extraction_id"],
        row["artifact_sha256"],
    ):
        raise ExtractionResultRowError("extraction columns differ from its immutable result")
    return result


def validate_extraction_results(connection: sqlite3.Connection) -> None:
    present = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='extraction_results'"
    ).fetchone()
    if present is None:
        if connection.execute("PRAGMA user_version").fetchone()[0] >= EXTRACTION_SCHEMA_VERSION:
            raise ValueError("extraction result schema table is missing")
        return
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        for row in cursor.execute("SELECT * FROM extraction_results"):
            read_extraction_result(row)
    finally:
        cursor.close()
