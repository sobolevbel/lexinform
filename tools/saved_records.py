"""Read saved result columns using the shared finite catalogue and traversal."""

import sqlite3
from collections.abc import Iterator, Mapping

from lexinform.saved_records import (
    BILL_SNAPSHOT_FIELDS,
    CONTEXT_RESULT_TABLES,
    RESULT_TABLES,
    SavedColumn,
    SavedRecord,
    SavedRecordIssue,
    SavedTable,
    has_answer_payload,
    is_saved_answer,
    saved_records,
)

__all__ = [
    "BILL_SNAPSHOT_FIELDS",
    "CONTEXT_RESULT_TABLES",
    "RESULT_TABLES",
    "SavedColumn",
    "SavedRecord",
    "SavedRecordIssue",
    "SavedTable",
    "has_answer_payload",
    "is_saved_answer",
    "saved_records",
    "saved_columns",
]


def saved_columns(
    connection: sqlite3.Connection,
    tables: set[str],
    *,
    catalogue: Mapping[str, SavedTable] = RESULT_TABLES,
) -> Iterator[SavedColumn]:
    for table, shape in catalogue.items():
        if table not in tables:
            continue
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        selected = [column for column in shape.columns if column in columns]
        if not selected or not set(shape.keys) <= columns:
            continue
        for row in connection.execute(
            f"SELECT {', '.join((*shape.keys, *selected))} FROM {table} "
            f"ORDER BY {', '.join(shape.keys)}"
        ):
            ref = table + "/" + "/".join(str(row[key]) for key in shape.keys)
            for column in selected:
                if row[column] is not None:
                    yield SavedColumn(f"{ref}/{column}", row[column])
