"""Read saved result columns and walk their recorded JSON without interpreting answers."""

import json
import sqlite3
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SavedTable:
    keys: tuple[str, ...]
    columns: tuple[str, ...]


RESULT_TABLES = {
    "bills": SavedTable(
        ("term", "number"),
        (
            "analysis_json",
            "ready_analysis_json",
            "joint_json",
            "supplements_json",
            "consultation_review_json",
            "commission_review_json",
            "act_application_review_json",
        ),
    ),
    "status_changes": SavedTable(("id",), ("amendments_json", "supplements_json")),
    "publications": SavedTable(("id",), ("delivery_json",)),
    "analysis_memo": SavedTable(("key",), ("record_json",)),
}
CONTEXT_RESULT_TABLES = {
    **RESULT_TABLES,
    "bills": SavedTable(
        RESULT_TABLES["bills"].keys, (*RESULT_TABLES["bills"].columns, "review_work_json")
    ),
    "document_work": SavedTable(("key",), ("work_json",)),
    "llm_batch_items": SavedTable(("batch_id", "custom_id"), ("meta_json",)),
    "llm_batch_intents": SavedTable(("custom_id",), ("meta_json",)),
}
BILL_SNAPSHOT_FIELDS = frozenset(
    {"bill_json", "change_json", "input_bill_json", "source_bill_json"}
)
_ANSWERS = {"analysis", "amendments", "digest", "comparison", "proposal", "review"}


@dataclass(frozen=True)
class SavedColumn:
    ref: str
    raw: object


@dataclass(frozen=True)
class SavedRecord:
    ref: str
    value: dict[str, Any]


@dataclass(frozen=True)
class SavedRecordIssue:
    code: str
    ref: str


def has_answer_payload(value: Mapping[str, object]) -> bool:
    return any(value.get(key) is not None for key in _ANSWERS)


def is_saved_answer(value: Mapping[str, object]) -> bool:
    return {"model", "prompt_version", "created_at"} <= value.keys() and bool(
        _ANSWERS.intersection(value)
    )


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


def saved_records(
    value: object,
    ref: str,
    *,
    encoded_fields: frozenset[str] = frozenset({"bill_json", "change_json"}),
    depth: int = 0,
) -> Iterator[SavedRecord | SavedRecordIssue]:
    if depth > 50:
        yield SavedRecordIssue("record_nesting_limit", ref)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            yield from saved_records(
                child, f"{ref}/{index}", encoded_fields=encoded_fields, depth=depth + 1
            )
    elif isinstance(value, dict):
        yield SavedRecord(ref, value)
        for key, child in value.items():
            child_ref = f"{ref}/{key}"
            if key in encoded_fields and isinstance(child, str) and child:
                try:
                    child = json.loads(child)
                except ValueError, RecursionError:
                    yield SavedRecordIssue("invalid_json", child_ref)
                    continue
            if isinstance(child, (dict, list)):
                yield from saved_records(
                    child, child_ref, encoded_fields=encoded_fields, depth=depth + 1
                )
