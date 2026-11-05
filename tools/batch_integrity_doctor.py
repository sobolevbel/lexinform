"""Inspect duplicate application identities and saved active primary generations without action."""

import argparse
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from lexinform.adapters.computation_history import read_result_application
from lexinform.json_storage import read_json
from lexinform.models import BatchIntent, BatchRequest, LlmBatchItem
from lexinform.models.batch import batch_item_meta
from tools.corpus_artifacts import write_json
from tools.sqlite_snapshot import open_snapshot

_APPLICATION = {"id", "result_id", "term", "number", "generation", "phase", "application_json"}
_INTENT = {"custom_id", "provider", "request_json", "meta_json", "state", "created_at"}
_ITEM = {"batch_id", "custom_id", "call_kind", "term", "number", "meta_json", "consumed_at"}
_OWNER = {"term", "number", "analysis_generation"}
_WORK_COUNTS = (
    "rows",
    "primary_rows",
    "not_applicable_rows",
    "invalid_rows",
    "generation_unknown_rows",
    "owner_unknown_rows",
    "assessed_rows",
    "same_generation_rows",
    "stale_generation_rows",
)
_APP_COUNTS = ("rows", "valid_rows", "invalid_rows", "duplicate_groups", "duplicate_rows")


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _rows(connection: sqlite3.Connection, sql: str) -> list[sqlite3.Row]:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        return cursor.execute(sql).fetchall()
    finally:
        cursor.close()


def _issue(findings: list[dict[str, str]], code: str, ref: str, *, warning: bool = False) -> None:
    findings.append({"code": code, "ref": ref, "level": "warning" if warning else "error"})


def _unavailable(columns: set[str], keys: tuple[str, ...]) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "missing_columns": sorted(columns),
        **{key: None for key in keys},
    }


def _applications(connection: sqlite3.Connection, findings: list[dict[str, str]]) -> dict[str, Any]:
    missing = _APPLICATION - _columns(connection, "result_applications")
    if missing:
        return _unavailable(missing, _APP_COUNTS)
    counts: Counter[str] = Counter()
    identities: dict[tuple[object, ...], list[str]] = {}
    for row in _rows(
        connection, "SELECT rowid AS audit_id,* FROM result_applications ORDER BY rowid"
    ):
        ref = f"result_applications/row-{row['audit_id']}"
        counts["rows"] += 1
        try:
            application = read_result_application(row)
        except ValueError, TypeError, RecursionError, KeyError, IndexError:
            counts["invalid_rows"] += 1
            _issue(findings, "invalid_application_identity", ref)
            continue
        target = application.target
        key: tuple[object, ...] = (
            application.result_id,
            application.term,
            application.number,
            application.generation,
            application.phase,
        )
        if application.generation is None:
            key += (target.kind if target else None, target.ref if target else None)
        identities.setdefault(key, []).append(ref)
        counts["valid_rows"] += 1
    for refs in identities.values():
        if len(refs) > 1:
            counts["duplicate_groups"] += 1
            counts["duplicate_rows"] += len(refs)
            for ref in refs:
                _issue(findings, "duplicate_application_identity", ref)
    return {
        "status": "available",
        "missing_columns": [],
        **{key: counts[key] for key in _APP_COUNTS},
    }


def _explicit_generation(raw: str) -> int | None:
    value = read_json(raw)
    generation = value.get("generation") if isinstance(value, dict) else None
    return generation if type(generation) is int and generation >= 0 else None


def _intent(row: sqlite3.Row) -> tuple[str, int, str, int | None]:
    request = BatchRequest.model_validate(read_json(row["request_json"]))
    meta = batch_item_meta(request.call_kind, row["meta_json"], submitted=False)
    intent = BatchIntent(
        request=request,
        meta=meta,
        provider=row["provider"],
        state=row["state"],
        created_at=row["created_at"],
    )
    if intent.request.custom_id != row["custom_id"]:
        raise ValueError("intent_identity_mismatch")
    return request.call_kind, request.term, request.number, _explicit_generation(row["meta_json"])


def _item(row: sqlite3.Row) -> tuple[str, int, str, int | None]:
    meta = batch_item_meta(row["call_kind"], row["meta_json"])
    item = LlmBatchItem(
        batch_id=row["batch_id"],
        custom_id=row["custom_id"],
        call_kind=row["call_kind"],
        term=row["term"],
        number=row["number"],
        meta=meta,
    )
    if any(
        getattr(item, key) != row[key]
        for key in ("batch_id", "custom_id", "call_kind", "term", "number")
    ):
        raise ValueError("item_identity_mismatch")
    return item.call_kind, item.term, item.number, _explicit_generation(row["meta_json"])


def _active(
    connection: sqlite3.Connection, table: str, findings: list[dict[str, str]]
) -> dict[str, Any]:
    intent = table == "llm_batch_intents"
    missing = (_INTENT if intent else _ITEM) - _columns(connection, table)
    if missing:
        return _unavailable(missing, _WORK_COUNTS)
    owner_missing = _OWNER - _columns(connection, "bills")
    condition = "state IN ('queued','submitting')" if intent else "consumed_at IS NULL"
    counts: Counter[str] = Counter()
    for row in _rows(
        connection, f"SELECT rowid AS audit_id,* FROM {table} WHERE {condition} ORDER BY rowid"
    ):
        ref = f"{table}/row-{row['audit_id']}"
        counts["rows"] += 1
        try:
            kind, term, number, generation = _intent(row) if intent else _item(row)
        except ValueError, TypeError, RecursionError, KeyError, IndexError:
            counts["invalid_rows"] += 1
            _issue(findings, "invalid_active_work", ref)
            continue
        if kind not in ("analysis", "reanalysis"):
            counts["not_applicable_rows"] += 1
            continue
        counts["primary_rows"] += 1
        if generation is None:
            counts["generation_unknown_rows"] += 1
            _issue(findings, "active_primary_generation_unknown", ref, warning=True)
            continue
        if owner_missing:
            counts["owner_unknown_rows"] += 1
            continue
        owners = connection.execute(
            "SELECT analysis_generation FROM bills WHERE term=? AND number=?", (term, number)
        ).fetchall()
        current = owners[0][0] if len(owners) == 1 else None
        if type(current) is not int or current < 0:
            counts["owner_unknown_rows"] += 1
            _issue(findings, "active_primary_owner_unknown", ref, warning=True)
            continue
        counts["assessed_rows"] += 1
        if generation == current:
            counts["same_generation_rows"] += 1
        else:
            counts["stale_generation_rows"] += 1
            _issue(findings, "active_primary_generation_mismatch", ref, warning=True)
    dependent = {"assessed_rows", "same_generation_rows", "stale_generation_rows"}
    return {
        "status": "partial" if owner_missing else "available",
        "missing_columns": [],
        "owner_missing_columns": sorted(owner_missing),
        **{
            key: None if owner_missing and key in dependent else counts[key] for key in _WORK_COUNTS
        },
    }


def audit(database: Path) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    with open_snapshot(database) as snapshot:
        connection = snapshot.connection
        schema = connection.execute("PRAGMA user_version").fetchone()[0]
        sections = {}
        for name, keys in (
            ("applications", _APP_COUNTS),
            ("llm_batch_intents", _WORK_COUNTS),
            ("llm_batch_items", _WORK_COUNTS),
        ):
            try:
                sections[name] = (
                    _applications(connection, findings)
                    if name == "applications"
                    else _active(connection, name, findings)
                )
            except sqlite3.Error:
                sections[name] = _unavailable(set(), keys)
                _issue(findings, "integrity_schema_unavailable", name, warning=True)
        if not snapshot.unchanged():
            _issue(findings, "database_changed_during_audit", "database")
    findings.sort(key=lambda item: (item["level"], item["code"], item["ref"]))
    errors = sum(item["level"] == "error" for item in findings)
    warnings = sum(item["level"] == "warning" for item in findings)
    available = all(section["status"] == "available" for section in sections.values())
    return {
        "version": 1,
        "profile": "saved-batch-application-integrity-v1",
        "status": "failed" if errors else "recorded" if available else "partial",
        "database_sha256": snapshot.sha256,
        "schema_version": schema,
        "summary": {"errors": errors, "warnings": warnings},
        "sections": sections,
        "findings": findings,
        "scope": "Saved application identities and active primary work generations.",
        "limitations": [
            "No repair, cancellation, retry, model/provider call, delivery or remote verification.",
            "Staleness is advisory; resets and late responses may retain old generations.",
            "Historical and consumed rows are not compared with current generations.",
            "Only explicit primary generation is known; legacy defaults stay unknown.",
            "Document/revision work have no generation; work_doctor handles their dependencies.",
            "Original input, work-owner and paid-owner scopes are not inferred through aliases.",
            "Intent/item counts are separate row populations, not unique bills.",
            "Duplicate identities are not proof of duplicate charges or physical delivery.",
            "Raw blobs and source applicability are outside this metadata profile.",
        ],
    }


def run(database: Path, output: Path) -> int:
    if database.resolve() == output.resolve():
        raise ValueError("Report must be separate from the input database")
    report = audit(database)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, report)
    return 1 if report["status"] == "failed" else 2 if report["status"] == "partial" else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        code = run(args.database, args.output)
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.error(str(exc))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
