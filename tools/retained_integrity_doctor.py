"""Inspect retained native payloads and known references without repair or historical inference."""

import argparse
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from lexinform.adapters.computation_history import read_computation_result
from lexinform.adapters.legacy_history import (
    read_legacy_result,
    validate_legacy_projections,
)
from lexinform.json_storage import read_json
from lexinform.legacy_results import legacy_analysis_body, read_legacy_json
from lexinform.models import (
    AgendaItem,
    AmendmentsContext,
    AnalysisRecord,
    BatchResult,
    Bill,
    DeliveryPlan,
    DocumentWork,
    Editorial,
    Mnemonic,
    NameRecord,
    Phase,
    PrintInfo,
    ProcessDetail,
    PublicationKind,
    ReviewWork,
    Stage,
    StatusChange,
    TextDocument,
)
from lexinform.models.revision_work import decode_document_work
from lexinform.name_binding import mnemonic_matches_result
from tools.corpus_artifacts import write_json
from tools.sqlite_snapshot import open_snapshot, snapshot_files

_LEGACY = {
    "legacy_analysis_bodies": {"body_sha256", "body_json"},
    "legacy_result_values": {"value_id", "envelope_json", "analysis_body_sha256"},
    "legacy_result_imports": {"import_id", "manifest_sha256", "imported_at", "source_sha256"},
    "legacy_result_occurrences": {
        "value_id",
        "import_id",
        "source_table",
        "source_key_json",
        "source_column",
        "source_path",
        "container_term",
        "container_number",
        "purpose",
    },
}
_BODY = {
    "publications": {"id", "kind", "delivery_json"},
    "document_work": {"work_json"},
    "bills": {"review_work_json"},
    "llm_batch_items": {"result_json", "custom_id"},
}
_DELIVERY_BODIES: dict[str, type[BaseModel]] = {
    "bill_json": Bill,
    "change_json": StatusChange,
    "print_json": PrintInfo,
    "primary_json": Bill,
    "item_json": AgendaItem,
    "moved_from_json": AgendaItem,
    "phase_json": Phase,
    "hearing_json": Stage,
}
_DOCUMENT_BODIES: dict[str, type[BaseModel]] = {
    "bill_json": Bill,
    "detail_json": ProcessDetail,
    "stage_json": Stage,
    "document_json": TextDocument,
    "context_json": AmendmentsContext,
}
_BAD = (ValueError, TypeError, RecursionError, sqlite3.Error, IndexError, KeyError)


def _inventory(
    db: sqlite3.Connection, table: str, required: set[str]
) -> tuple[list[sqlite3.Row] | None, list[str]]:
    cursor = db.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        missing = sorted(
            required - {row["name"] for row in cursor.execute(f"PRAGMA table_info({table})")}
        )
        return (
            (None, missing)
            if missing
            else (
                cursor.execute(
                    f"SELECT rowid AS audit_id,* FROM {table} ORDER BY rowid"
                ).fetchall(),
                [],
            )
        )
    except sqlite3.Error:
        return None, sorted(required)
    finally:
        cursor.close()


def _issue(findings: list[dict[str, str]], code: str, ref: str, *, warning: bool = False) -> None:
    findings.append({"code": code, "ref": ref, "level": "warning" if warning else "error"})


def _section(
    counts: Counter[str], keys: tuple[str, ...], missing: dict[str, list[str]]
) -> dict[str, Any]:
    return {
        "status": "unavailable" if missing else "available",
        "missing_columns": missing,
        **{key: None if missing else counts[key] for key in keys},
    }


def _legacy(db: sqlite3.Connection, findings: list[dict[str, str]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    missing = {}
    for table, required in _LEGACY.items():
        rows, columns = _inventory(db, table, required)
        if columns:
            missing[table] = columns
        if rows is None:
            continue
        counts[table.removeprefix("legacy_") + "_rows"] = len(rows)
        for row in rows:
            try:
                if table == "legacy_analysis_bodies":
                    legacy_analysis_body(row["body_json"], row["body_sha256"])
                elif table == "legacy_result_values":
                    read_legacy_result(db, row["value_id"])
            except _BAD:
                counts["body_value_invalid_rows"] += 1
                _issue(findings, "invalid_legacy_archive_row", f"{table}/row-{row['audit_id']}")
    if not missing:
        try:
            validate_legacy_projections(db)
        except _BAD:
            counts["archive_graph_errors"] += 1
            _issue(findings, "invalid_legacy_archive_graph", "legacy_archive")
    counts["value_rows"] = counts["result_values_rows"]
    return _section(
        counts,
        (
            "analysis_bodies_rows",
            "value_rows",
            "result_imports_rows",
            "result_occurrences_rows",
            "body_value_invalid_rows",
            "archive_graph_errors",
        ),
        missing,
    )


def _hydrated(db: sqlite3.Connection, raw: str) -> str:
    return read_legacy_json(raw, lambda identifier: read_legacy_result(db, identifier))


def _body(
    db: sqlite3.Connection,
    raw: object,
    model: type[BaseModel],
    ref: str,
    counts: Counter[str],
    findings: list[dict[str, str]],
    *,
    native: bool = True,
    required: bool = False,
) -> None:
    counts["slots"] += 1
    if raw is None or raw == "":
        if native and required:
            counts["invalid_rows"] += 1
            _issue(findings, "missing_retained_body", ref)
        else:
            counts["absent_rows"] += 1
        return
    try:
        if not isinstance(raw, str):
            raise ValueError("invalid_body_storage")
        decoded = read_json(_hydrated(db, raw))
    except _BAD:
        counts["invalid_rows"] += 1
        _issue(findings, "invalid_retained_body", ref)
        return
    try:
        model.model_validate(decoded)
    except _BAD:
        if native:
            counts["invalid_rows"] += 1
            _issue(findings, "invalid_retained_body", ref)
        else:
            counts["unknown_rows"] += 1
            _issue(findings, "legacy_body_unavailable", ref, warning=True)
    else:
        counts["valid_rows"] += 1


def _publications(
    db: sqlite3.Connection,
    row: sqlite3.Row,
    counts: Counter[str],
    parents: Counter[str],
    children: Counter[str],
    publication_ids: set[int],
    findings: list[dict[str, str]],
) -> None:
    ref = f"publications/row-{row['audit_id']}"
    parents["publication_rows"] += 1
    children["publication_rows"] += 1
    if row["delivery_json"] is None:
        parents["unavailable_rows"] += 1
        children["unavailable_rows"] += 1
        counts["slots"] += 1
        counts["absent_rows"] += 1
        return
    try:
        raw = read_json(_hydrated(db, row["delivery_json"]))
    except _BAD:
        counts["slots"] += 1
        counts["invalid_rows"] += 1
        _issue(findings, "invalid_retained_body", ref)
        parents["unavailable_rows"] += 1
        children["unavailable_rows"] += 1
        return
    native = isinstance(raw, dict) and raw.get("policy_version") == "attention-v1"
    try:
        plan = DeliveryPlan.model_validate(raw)
        kind = PublicationKind(row["kind"])
    except _BAD:
        counts["slots"] += 1
        counts["invalid_rows" if native else "unknown_rows"] += 1
        _issue(
            findings,
            "invalid_retained_body" if native else "delivery_profile_unavailable",
            ref,
            warning=not native,
        )
        parents["unavailable_rows"] += 1
        children["unavailable_rows"] += 1
        return
    for field, model in _DELIVERY_BODIES.items():
        _body(
            db,
            getattr(plan, field),
            model,
            ref + "/" + field,
            counts,
            findings,
            native=native,
            required=field == "bill_json" and kind != PublicationKind.DIGEST,
        )
    for index, item in enumerate(plan.update_group):
        for field, model in (("bill_json", Bill), ("change_json", StatusChange)):
            _body(
                db,
                getattr(item, field),
                model,
                f"{ref}/update_group/{index}/{field}",
                counts,
                findings,
                native=native,
                required=True,
            )
    if plan.continuation_parent is None:
        parents["no_parent_rows"] += 1
    if plan.continuation_parent is not None:
        parents["rows"] += 1
        present = plan.continuation_parent in publication_ids
        parents["present_rows" if present else "missing_rows"] += 1
        if not present:
            _issue(findings, "continuation_parent_unavailable", ref, warning=True)
    if not plan.continuation_ids:
        children["no_children_rows"] += 1
    for index, identity in enumerate(plan.continuation_ids):
        children["rows"] += 1
        present = identity in publication_ids
        children["present_rows" if present else "missing_rows"] += 1
        if not present:
            _issue(
                findings,
                "continuation_child_unavailable",
                f"{ref}/continuation_ids/{index}",
                warning=True,
            )


def _bodies(
    db: sqlite3.Connection, findings: list[dict[str, str]]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    counts: Counter[str] = Counter()
    parents: Counter[str] = Counter()
    children: Counter[str] = Counter()
    missing = {}
    for table, required in _BODY.items():
        rows, columns = _inventory(db, table, required)
        if columns:
            missing[table] = columns
        publication_ids = {row["id"] for row in rows or []} if table == "publications" else set()
        for row in rows or []:
            ref = f"{table}/row-{row['audit_id']}"
            try:
                if table == "publications":
                    _publications(db, row, counts, parents, children, publication_ids, findings)
                elif table == "document_work":
                    work = decode_document_work(_hydrated(db, row["work_json"]))
                    if isinstance(work, DocumentWork):
                        for field, model in _DOCUMENT_BODIES.items():
                            _body(
                                db,
                                getattr(work, field),
                                model,
                                ref + "/" + field,
                                counts,
                                findings,
                                required=field in ("bill_json", "detail_json", "stage_json"),
                            )
                elif table == "bills" and row["review_work_json"] is not None:
                    value = read_json(_hydrated(db, row["review_work_json"]))
                    if not isinstance(value, list):
                        raise ValueError("invalid_review_owner")
                    for index, item in enumerate(value):
                        work_review = ReviewWork.model_validate(item)
                        _body(
                            db,
                            work_review.input_bill_json,
                            Bill,
                            f"{ref}/review/{index}/input_bill_json",
                            counts,
                            findings,
                        )
                elif table == "llm_batch_items":
                    _body(
                        db, row["result_json"], BatchResult, ref + "/result_json", counts, findings
                    )
            except _BAD:
                counts["slots"] += 1
                counts["invalid_rows"] += 1
                _issue(findings, "invalid_retained_body", ref)
    return (
        _section(
            counts, ("slots", "valid_rows", "invalid_rows", "unknown_rows", "absent_rows"), missing
        ),
        _section(
            parents,
            (
                "publication_rows",
                "rows",
                "present_rows",
                "missing_rows",
                "no_parent_rows",
                "unavailable_rows",
            ),
            {"publications": missing["publications"]} if "publications" in missing else {},
        ),
        _section(
            children,
            (
                "publication_rows",
                "rows",
                "present_rows",
                "missing_rows",
                "no_children_rows",
                "unavailable_rows",
            ),
            {"publications": missing["publications"]} if "publications" in missing else {},
        ),
    )


def _mnemonic_status(
    name: Mnemonic | None, by_id: dict[str, sqlite3.Row]
) -> Literal["unbound_rows", "unknown_rows", "valid_rows"]:
    if name is None or name.computation_id is None:
        return "unbound_rows"
    parent = by_id.get(name.computation_id)
    if parent is None:
        raise ValueError("missing_selected_name_result")
    result = read_computation_result(parent)
    if (
        result.validation != "validated"
        or result.purpose not in ("analysis", "reanalysis", "name")
        or not isinstance(result.record, (AnalysisRecord, NameRecord))
    ):
        raise ValueError("selected_name_source_invalid")
    if name.request_sha256 is None or result.request_sha256 is None:
        return "unknown_rows"
    if not mnemonic_matches_result(name, result):
        raise ValueError("selected_name_result_mismatch")
    return "valid_rows"


def _editorial(db: sqlite3.Connection, findings: list[dict[str, str]]) -> dict[str, Any]:
    rows, columns = _inventory(db, "bills", {"editorial_json"})
    results, result_columns = _inventory(
        db,
        "computation_results",
        {"result_id", "provider", "batch_id", "custom_id", "term", "number", "result_json"},
    )
    missing = {
        table: cols
        for table, cols in (("bills", columns), ("computation_results", result_columns))
        if cols
    }
    counts: Counter[str] = Counter()
    by_id = {row["result_id"]: row for row in results or []}
    for row in rows or []:
        counts["rows"] += 1
        if row["editorial_json"] is None or row["editorial_json"] == "":
            counts["unbound_rows"] += 1
            continue
        ref = f"bills/row-{row['audit_id']}/editorial"
        try:
            editorial = Editorial.model_validate(read_json(row["editorial_json"]))
        except _BAD:
            counts["invalid_rows"] += 1
            _issue(findings, "invalid_editorial_paid_ref", ref)
            continue
        counts["previous_rows"] += len(editorial.previous)
        scopes: list[tuple[str, str, Mnemonic | None]] = [("", "mnemonic", editorial.mnemonic)]
        scopes.extend(
            ("previous_", f"previous/{index}", name)
            for index, name in enumerate(editorial.previous)
        )
        for prefix, position, name in scopes:
            if name is not None and name.computation_id is not None:
                counts[prefix + "known_rows"] += 1
                if result_columns:
                    continue
            try:
                counts[prefix + _mnemonic_status(name, by_id)] += 1
            except _BAD:
                counts[prefix + "invalid_rows"] += 1
                _issue(findings, "invalid_editorial_paid_ref", ref + "/" + position)
    return _section(
        counts,
        (
            "rows",
            "known_rows",
            "unbound_rows",
            "valid_rows",
            "invalid_rows",
            "unknown_rows",
            "previous_rows",
            "previous_known_rows",
            "previous_unbound_rows",
            "previous_valid_rows",
            "previous_invalid_rows",
            "previous_unknown_rows",
        ),
        missing,
    )


def audit(database: Path) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    with open_snapshot(database) as snapshot:
        db = snapshot.connection
        legacy = _legacy(db, findings)
        bodies, parents, children = _bodies(db, findings)
        editorial = _editorial(db, findings)
        sections = {
            "legacy_archive": legacy,
            "saved_bodies": bodies,
            "editorial_refs": editorial,
            "continuation_parents": parents,
            "continuation_children": children,
        }
        schema = db.execute("PRAGMA user_version").fetchone()[0]
        if not snapshot.unchanged():
            _issue(findings, "database_changed_during_audit", "database")
    findings.sort(key=lambda row: (row["level"], row["code"], row["ref"]))
    errors = sum(row["level"] == "error" for row in findings)
    warnings = sum(row["level"] == "warning" for row in findings)
    return {
        "version": 1,
        "profile": "retained-state-integrity-v1",
        "status": "failed"
        if errors
        else "partial"
        if any(s["status"] == "unavailable" for s in sections.values())
        else "recorded",
        "database_sha256": snapshot.sha256,
        "schema_version": schema,
        "summary": {"errors": errors, "warnings": warnings},
        "sections": sections,
        "findings": findings,
        "limitations": [
            "No repair, resend, provider/network call, owner inference or source verdict.",
            "Raw originals are outside retained application-body integrity.",
            "Legacy/cleaned bodies stay unavailable; validity does not prove original context.",
            "Parents use local Publication IDs; absence never proves send eligibility.",
            "Listed child edges use local Publication IDs; cleaned plans leave membership unknown.",
        ],
    }


def run(database: Path, output: Path) -> int:
    if output.resolve() in snapshot_files(database):
        raise ValueError("Report must be separate from database")
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
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.error(str(exc))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
