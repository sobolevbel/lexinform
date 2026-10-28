"""Walk the finite saved-result catalogue without interpreting answer content."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import cast

from pydantic import JsonValue

from lexinform.models.legacy_results import LEGACY_ANSWER_FIELDS, legacy_json


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
    "content_snapshots": SavedTable(("snapshot_id",), ("payload_json",)),
}
BILL_SNAPSHOT_FIELDS = frozenset(
    {"bill_json", "change_json", "input_bill_json", "source_bill_json"}
)


@dataclass(frozen=True)
class SavedColumn:
    ref: str
    raw: object


@dataclass(frozen=True)
class SavedRecord:
    ref: str
    value: dict[str, JsonValue]


@dataclass(frozen=True)
class SavedRecordIssue:
    code: str
    ref: str


def has_answer_payload(value: Mapping[str, object]) -> bool:
    return any(value.get(key) is not None for key in LEGACY_ANSWER_FIELDS)


def is_saved_answer(value: Mapping[str, object]) -> bool:
    return {"model", "prompt_version", "created_at"} <= value.keys() and bool(
        LEGACY_ANSWER_FIELDS.intersection(value)
    )


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
        yield SavedRecord(ref, cast(dict[str, JsonValue], value))
        for key, child in value.items():
            child_ref = f"{ref}/{key.replace('~', '~0').replace('/', '~1')}"
            if key in encoded_fields and isinstance(child, str) and child:
                try:
                    child = legacy_json(child)
                except ValueError, RecursionError:
                    yield SavedRecordIssue("invalid_json", child_ref)
                    continue
            if isinstance(child, (dict, list)):
                yield from saved_records(
                    child, child_ref, encoded_fields=encoded_fields, depth=depth + 1
                )
