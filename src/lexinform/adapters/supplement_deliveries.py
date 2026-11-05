"""Observe frozen supplement delivery links without current-state inference."""

import sqlite3
from collections import Counter
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from lexinform.adapters.computation_history import read_computation_result
from lexinform.adapters.secondary_dependencies import (
    SecondaryDependencyError,
    validate_secondary_dependency,
)
from lexinform.json_storage import read_json
from lexinform.models import (
    ComputationResult,
    DeliveryPlan,
    ResultApplication,
    StatusChange,
    SupplementRecord,
)
from lexinform.models.batch import batch_item_meta

_REQUIRED = {
    "result_applications": {
        "id",
        "result_id",
        "term",
        "number",
        "generation",
        "phase",
        "application_json",
    },
    "computation_results": {
        "result_id",
        "provider",
        "batch_id",
        "custom_id",
        "term",
        "number",
        "result_json",
    },
    "digest_event_links": {"source_publication_id", "owner_publication_id"},
    "publications": {"id", "status_change_id", "kind", "status", "message_id", "delivery_json"},
}


def _rows(
    connection: sqlite3.Connection, sql: str, values: tuple[object, ...] = ()
) -> Iterator[sqlite3.Row]:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        yield from cursor.execute(sql, values)
    finally:
        cursor.close()


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _batch(connection: sqlite3.Connection, result: ComputationResult) -> tuple[dict[str, str], str]:
    evidence = {"status": "unavailable", "request": "unknown"}
    if not {"batch_id", "provider"} <= _columns(connection, "llm_batches") or not {
        "batch_id",
        "custom_id",
        "call_kind",
        "meta_json",
        "consumed_at",
    } <= _columns(connection, "llm_batch_items"):
        return evidence, "unknown"
    row = next(
        _rows(
            connection,
            "SELECT i.call_kind, i.meta_json, i.consumed_at, b.provider FROM llm_batch_items i "
            "JOIN llm_batches b ON b.batch_id=i.batch_id WHERE i.batch_id=? AND i.custom_id=?",
            (result.batch_id, result.custom_id),
        ),
        None,
    )
    if row is None:
        return evidence, "unknown"
    try:
        read_json(row["meta_json"])
        meta = batch_item_meta("supplement", row["meta_json"])
    except ValueError, TypeError, RecursionError:
        return {**evidence, "status": "invalid"}, "unknown"
    if row["provider"] != result.provider or row["call_kind"] != "supplement":
        return {**evidence, "status": "mismatch"}, "unknown"
    if meta.request_sha256 is not None and result.request_sha256 is not None:
        evidence["request"] = (
            "matched" if meta.request_sha256 == result.request_sha256 else "mismatch"
        )
        if evidence["request"] == "mismatch":
            return {**evidence, "status": "mismatch"}, "unknown"
    if row["consumed_at"] is not None:
        try:
            datetime.fromisoformat(row["consumed_at"])
        except ValueError, TypeError:
            return {**evidence, "status": "invalid"}, "unknown"
    evidence["status"] = "consumed" if row["consumed_at"] is not None else "unconsumed"
    return evidence, "present" if meta.prepared_input_receipt is not None else "unknown"


def _physical(
    connection: sqlite3.Connection, row: sqlite3.Row, result: ComputationResult, change_id: int
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "publication_id": row["id"],
        "physical_status": row["status"]
        if row["status"]
        in ("sent", "unknown", "pending", "queued", "failed", "dismissed", "skipped")
        else "unrecognized",
        "delivery": "unknown",
        "reason": "payload_unavailable",
    }
    if base["physical_status"] == "unrecognized":
        return {**base, "reason": "physical_status_invalid"}
    if row["delivery_json"] is None:
        return base
    try:
        plan = DeliveryPlan.model_validate(read_json(row["delivery_json"]))
    except ValueError, TypeError, RecursionError:
        return {**base, "reason": "payload_invalid"}
    digest_owner = next(
        _rows(
            connection,
            "SELECT owner_publication_id FROM digest_event_links WHERE source_publication_id=?",
            (row["id"],),
        ),
        None,
    )
    if row["kind"] != "status_update" or (
        plan.destination != "direct"
        or plan.absorbed_by is not None
        or digest_owner is not None
        or plan.digest_event_ids
        or plan.digest_parts
        or plan.digest_ref
        or plan.update_group
        or plan.sitting_group
        or plan.publication_ids
        or plan.group_text
        or plan.continuation_ids
        or plan.continuation_parent is not None
        or plan.continuation_root is not None
    ):
        return {**base, "reason": "compound_delivery_unassessed"}
    if not plan.change_json:
        return base
    try:
        frozen = StatusChange.model_validate(read_json(plan.change_json))
    except ValueError, TypeError, RecursionError:
        return {**base, "reason": "payload_invalid"}
    if frozen.id != change_id:
        return {**base, "reason": "frozen_change_mismatch"}
    matches = [record for record in frozen.supplements if record.computation_id == result.result_id]
    if not matches:
        return {
            **base,
            "reason": "frozen_reference_mismatch"
            if any(record.computation_id for record in frozen.supplements)
            else "frozen_reference_unavailable",
        }
    try:
        for record in matches:
            validate_secondary_dependency(connection, record, "supplement")
    except SecondaryDependencyError as error:
        return {
            **base,
            "reason": "frozen_request_mismatch"
            if error.code == "request_mismatch"
            else "frozen_dependency_invalid",
        }
    sent = row["status"] == "sent" and type(row["message_id"]) is int and row["message_id"] > 0
    if row["status"] == "sent" and not sent:
        return {**base, "reason": "message_id_unavailable"}
    return {
        **base,
        "delivery": "recorded_sent" if sent else "not_confirmed",
        "reason": "exact_frozen_reference",
        "message_id": row["message_id"] if type(row["message_id"]) is int else None,
        "request": "matched"
        if all(
            record.request_sha256 is not None and result.request_sha256 is not None
            for record in matches
        )
        else "unknown",
        "rendered_payload": "known" if plan.rendered_text else "unavailable",
    }


def supplement_delivery_links(connection: sqlite3.Connection) -> dict[str, Any]:
    """Recorded-sent means local database evidence, never remote Telegram verification."""
    unavailable = sorted(
        table for table, columns in _REQUIRED.items() if not columns <= _columns(connection, table)
    )
    report: dict[str, Any] = {
        "version": 1,
        "status": "unavailable" if unavailable else "available",
        "unavailable_schema": unavailable,
        "links": [],
        "counts": None,
        "unclassified_applications": 0,
        "scope": "Applied supplement -> frozen semantic payload -> recorded physical status.",
        "exclusions": [
            "recorded_sent is database evidence, not remote Telegram verification.",
            "Source linkage is not assessed; input-receipt availability is separate.",
            "No current Bill/status-change contents, answer equality or timestamp joins.",
            "Compound, absorbed or missing payloads stay unknown; no full DA10.7 claim.",
        ],
    }
    if unavailable:
        return report
    links: list[dict[str, Any]] = []
    for row in _rows(
        connection, "SELECT * FROM result_applications WHERE phase='projection' ORDER BY id"
    ):
        try:
            application = ResultApplication.model_validate(read_json(row["application_json"]))
            if (
                application.result_id,
                application.term,
                application.number,
                application.generation,
                application.phase,
            ) != tuple(row[key] for key in ("result_id", "term", "number", "generation", "phase")):
                raise ValueError("application columns mismatch")
        except ValueError, TypeError, RecursionError:
            report["unclassified_applications"] += 1
            continue
        target = application.target
        if target is None or target.kind != "status_change":
            continue
        result_row = next(
            _rows(
                connection,
                "SELECT * FROM computation_results WHERE result_id=?",
                (application.result_id,),
            ),
            None,
        )
        try:
            if result_row is None:
                raise ValueError("result unavailable")
            result = read_computation_result(result_row)
        except ValueError, TypeError, RecursionError:
            report["unclassified_applications"] += 1
            continue
        if result.purpose != "supplement":
            continue
        base: dict[str, Any] = {
            "application_id": row["id"],
            "computation_id": result.result_id,
            "source_edge": "not_assessed",
            "delivery": "unknown",
        }
        if result.validation != "validated" or not isinstance(result.record, SupplementRecord):
            links.append({**base, "reason": "retained_result_unvalidated"})
            continue
        if (
            not target.ref.isascii()
            or not target.ref.isdigit()
            or len(target.ref) > 19
            or not 0 < int(target.ref) <= 2**63 - 1
            or str(int(target.ref)) != target.ref
        ):
            links.append({**base, "reason": "opaque_target"})
            continue
        base["source_kind"] = result.record.source_kind
        change_id = int(target.ref)
        batch, receipt = _batch(connection, result)
        base.update(change_id=change_id, batch=batch, input_receipt=receipt)
        publications = list(
            _rows(
                connection,
                "SELECT * FROM publications WHERE status_change_id=? ORDER BY id",
                (change_id,),
            )
        )
        if not publications:
            links.append({**base, "reason": "publication_unavailable"})
        for publication in publications:
            links.append({**base, **_physical(connection, publication, result, change_id)})
    report["links"] = links
    report["counts"] = dict(Counter(link["delivery"] for link in links))
    return report
