"""Plan one finite legacy import and only the approved direct projection replacements."""

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from pydantic import JsonValue

from lexinform.models.legacy_results import (
    LEGACY_REFERENCE_FIELDS,
    LegacyResultImport,
    LegacyResultOccurrence,
    LegacyResultValue,
    canonical_legacy_json,
    has_legacy_marker,
    legacy_json,
)
from lexinform.saved_records import (
    BILL_SNAPSHOT_FIELDS,
    CONTEXT_RESULT_TABLES,
    SavedRecordIssue,
    has_answer_payload,
    is_saved_answer,
    saved_records,
)

_SHA256 = re.compile(r"[0-9a-f]{64}")
_BILL_RECORDS = frozenset(
    {
        "analysis_json",
        "joint_json",
        "consultation_review_json",
        "commission_review_json",
        "act_application_review_json",
    }
)


@dataclass(frozen=True)
class LegacySnapshotColumn:
    table: str
    key_json: str
    column: str
    raw_json: str
    container_term: int | None = None
    container_number: str | None = None


@dataclass(frozen=True)
class LegacyProjectionReplacement:
    original: LegacySnapshotColumn
    replacement_json: str


@dataclass(frozen=True)
class LegacySharedAnalysis:
    value_id: str
    envelope_json: str
    body_sha256: str
    body_json: str


@dataclass(frozen=True)
class LegacyMaintenancePlan:
    snapshot_sha256: str
    imported_at: datetime
    share_analysis_bodies: bool
    imported: LegacyResultImport | None = None
    replacements: tuple[LegacyProjectionReplacement, ...] = ()
    shared: tuple[LegacySharedAnalysis, ...] = ()
    existing_manifest_sha256: str | None = None
    surveyed_columns: int = 0
    modern_occurrences: int = 0


@dataclass(frozen=True)
class LegacyMaintenanceResult:
    applied: bool
    manifest_sha256: str | None
    baseline_sql_bytes: int | None = None
    baseline_gzip_bytes: int | None = None
    compact_sql_bytes: int | None = None
    compact_gzip_bytes: int | None = None


def _purpose(column: LegacySnapshotColumn, path: str) -> str | None:
    if column.table == "bills":
        if column.column == "ready_analysis_json":
            return "analysis" if path == "/record" else None
        if path:
            return None
        return {
            "analysis_json": "analysis",
            "joint_json": "joint",
            "consultation_review_json": "consultation_review",
            "commission_review_json": "commission_review",
            "act_application_review_json": "act_application",
        }.get(column.column)
    if column.table == "status_changes":
        if column.column == "amendments_json" and not path:
            return "amendments"
        if column.column == "supplements_json" and re.fullmatch(r"/[0-9]+", path):
            return "supplement"
    return None


def _direct_projection(value: JsonValue) -> JsonValue:
    if (
        not isinstance(value, dict)
        or not is_saved_answer(value)
        or not has_answer_payload(value)
        or value.get("computation_id") is not None
    ):
        return value
    original = LegacyResultValue(value_json=canonical_legacy_json(value))
    return {"legacy_result_ref": original.value_id}


def _compact(column: LegacySnapshotColumn, value: JsonValue) -> JsonValue:
    if column.table == "bills":
        if column.column in _BILL_RECORDS:
            return _direct_projection(value)
        if column.column == "ready_analysis_json" and isinstance(value, dict) and "record" in value:
            return {**value, "record": _direct_projection(value["record"])}
    if column.table == "status_changes":
        if column.column == "amendments_json":
            return _direct_projection(value)
        if column.column == "supplements_json" and isinstance(value, list):
            return [_direct_projection(child) for child in value]
    if column.table == "analysis_memo" and column.column == "record_json":
        return _direct_projection(value)
    return value


def _shared_analyses(values: tuple[LegacyResultValue, ...]) -> tuple[LegacySharedAnalysis, ...]:
    candidates: list[LegacySharedAnalysis] = []
    for original in values:
        value = legacy_json(original.value_json)
        assert isinstance(value, dict), "LegacyResultValue validates a saved record object"
        if isinstance(value.get("analysis"), dict):
            envelope = dict(value)
            body = canonical_legacy_json(envelope.pop("analysis"))
            candidates.append(
                LegacySharedAnalysis(
                    original.value_id,
                    canonical_legacy_json(envelope),
                    hashlib.sha256(body.encode()).hexdigest(),
                    body,
                )
            )
    counts = Counter(item.body_sha256 for item in candidates)
    return tuple(item for item in candidates if counts[item.body_sha256] > 1)


def plan_legacy_maintenance(
    columns: tuple[LegacySnapshotColumn, ...],
    *,
    snapshot_sha256: str,
    imported_at: datetime,
    share_analysis_bodies: bool = True,
) -> LegacyMaintenancePlan:
    if _SHA256.fullmatch(snapshot_sha256) is None:
        raise ValueError("a legacy maintenance plan requires its exact normalized raw dump SHA")
    locations = {(item.table, item.key_json, item.column) for item in columns}
    if len(locations) != len(columns):
        raise ValueError("a legacy snapshot cannot repeat a physical column")
    values: dict[str, LegacyResultValue] = {}
    occurrences: list[LegacyResultOccurrence] = []
    replacements: list[LegacyProjectionReplacement] = []
    modern = 0
    for column in columns:
        shape = CONTEXT_RESULT_TABLES.get(column.table)
        if shape is None or column.column not in shape.columns:
            raise ValueError("a legacy snapshot column is outside the finite catalogue")
        key = legacy_json(column.key_json)
        if (
            not isinstance(key, list)
            or len(key) != len(shape.keys)
            or any(not isinstance(part, (str, int)) or isinstance(part, bool) for part in key)
            or canonical_legacy_json(key) != column.key_json
        ):
            raise ValueError("a legacy snapshot requires its exact canonical physical key")
        value = legacy_json(column.raw_json)
        if has_legacy_marker(value):
            raise ValueError("legacy projections require an existing finite import marker")
        for record in saved_records(value, "", encoded_fields=BILL_SNAPSHOT_FIELDS):
            if isinstance(record, SavedRecordIssue):
                raise ValueError(f"corrupt legacy evidence: {record.code}")
            if LEGACY_REFERENCE_FIELDS.intersection(record.value):
                raise ValueError("embedded legacy projections require an existing import marker")
            if not is_saved_answer(record.value):
                continue
            if record.value.get("computation_id") is not None:
                modern += 1
                continue
            original = LegacyResultValue(value_json=canonical_legacy_json(record.value))
            values[original.value_id] = original
            occurrences.append(
                LegacyResultOccurrence(
                    value_id=original.value_id,
                    source_table=column.table,
                    source_key_json=column.key_json,
                    source_column=column.column,
                    source_path=record.ref,
                    container_term=column.container_term,
                    container_number=column.container_number,
                    purpose=_purpose(column, record.ref),
                )
            )
        compact = _compact(column, value)
        if canonical_legacy_json(compact) != canonical_legacy_json(value):
            replacements.append(LegacyProjectionReplacement(column, canonical_legacy_json(compact)))
    imported = (
        LegacyResultImport(
            imported_at=imported_at,
            source_sha256=snapshot_sha256,
            values=tuple(values[key] for key in sorted(values)),
            occurrences=tuple(occurrences),
        )
        if values
        else None
    )
    return LegacyMaintenancePlan(
        snapshot_sha256=snapshot_sha256,
        imported_at=imported_at,
        share_analysis_bodies=share_analysis_bodies,
        imported=imported,
        replacements=tuple(replacements),
        shared=_shared_analyses(imported.values) if imported and share_analysis_bodies else (),
        surveyed_columns=len(columns),
        modern_occurrences=modern,
    )
