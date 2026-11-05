"""Inventory saved local timing witnesses without inferring source or provider clocks."""

import hashlib
import sqlite3
from collections import Counter
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from lexinform.adapters.computation_history import (
    ComputationRowError,
    read_computation_result,
    read_result_application,
)
from lexinform.models import ComputationResult
from lexinform.models.batch import BatchKind
from lexinform.saved_clock import saved_clock_age
from tools.timing_delivery_links import TimingDeliveryLinks

PURPOSES: tuple[BatchKind, ...] = (
    "analysis",
    "reanalysis",
    "explanation",
    "amendments",
    "supplement",
    "joint",
    "name",
    "consultation_review",
    "commission_review",
    "act_application",
)
_REQUIRED = {
    "computation_results": {
        "result_id",
        "provider",
        "batch_id",
        "custom_id",
        "term",
        "number",
        "result_json",
    },
    "result_applications": {
        "id",
        "result_id",
        "term",
        "number",
        "generation",
        "phase",
        "application_json",
    },
}


class ObservedTimingReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[2] = 2
    observed_at: str
    coverage: Literal["partial"] = "partial"
    missing_schema: tuple[str, ...]
    populations: dict[str, dict[str, int | None]]
    clock_coverage: dict[str, dict[str, int | None]]
    purposes: tuple[dict[str, Any], ...]
    results: tuple[dict[str, Any], ...]
    applications: tuple[dict[str, Any], ...]
    physical_edges: tuple[dict[str, Any], ...]
    limitations: tuple[str, ...] = (
        (
            "Counts are retained SQL rows, application receipts and exact "
            "application/operation edges separately; not bills or historical traffic."
        ),
        (
            "Source availability and provider completion remain unknown; saved collection is "
            "our observation."
        ),
        (
            "Original input scope, retained owner and application owner are distinct; no "
            "current-owner or current-generation inference."
        ),
        (
            "An input-reference operation does not prove answer wording, legal accuracy or "
            "remote Telegram state."
        ),
        (
            "Second-resolution intervals use saved aware clocks; read completion may precede "
            "work_requested."
        ),
        (
            "Manual confirmation is not an API-send clock; absent or cleaned unprofiled "
            "payloads cannot establish a link."
        ),
    )


def rows(connection: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        return cursor.execute(query).fetchall()
    finally:
        cursor.close()


def columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def opaque(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def scope(term: int, number: str) -> dict[str, Any]:
    return {"term": term, "number_sha256": opaque(number)}


def interval(start: object, end: object, observed: datetime) -> dict[str, Any]:
    for label, value in (("start", start), ("end", end)):
        age = saved_clock_age(value, observed)
        if age.unknown:
            return {"seconds": None, "reason": f"{label}_{age.unknown}"}
    ended = end if isinstance(end, datetime) else datetime.fromisoformat(str(end))
    elapsed = saved_clock_age(start, ended)
    return {"seconds": elapsed.seconds, "reason": "end_before_start" if elapsed.unknown else None}


def _clocks(result: ComputationResult, end: object, observed: datetime) -> dict[str, Any]:
    witness = result.local_timing_witness
    return {
        "trigger_to_endpoint": interval(witness.triggered_at if witness else None, end, observed),
        "read_to_endpoint": interval(witness.read_completed_at if witness else None, end, observed),
    }


def observed_timing(connection: sqlite3.Connection, observed_at: datetime) -> ObservedTimingReport:
    if observed_at.utcoffset() is None:
        raise ValueError("Observed time must include a timezone")
    missing = [
        table for table, required in _REQUIRED.items() if not required <= columns(connection, table)
    ]
    retained: dict[str, ComputationResult] = {}
    results: list[dict[str, Any]] = []
    applications: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    counts: dict[str, Counter[str]] = {kind: Counter() for kind in PURPOSES}
    if "computation_results" not in missing:
        for row in rows(connection, "SELECT * FROM computation_results ORDER BY result_id"):
            item: dict[str, Any] = {
                "ref": f"result-ref:{opaque(str(row['result_id']))}",
                "purpose": None,
            }
            try:
                result = read_computation_result(row)
            except ComputationRowError as error:
                results.append({**item, "reason": error.code})
                continue
            retained[result.result_id] = result
            witness = result.local_timing_witness
            binding = result.input_binding
            clocks = _clocks(result, result.collected_at, observed_at)
            counts[result.purpose]["results"] += 1
            counts[result.purpose]["witnesses"] += witness is not None
            results.append(
                {
                    **item,
                    "purpose": result.purpose,
                    "result_id": result.result_id,
                    "retained_owner": scope(result.term, result.number),
                    "input_scope": {
                        **scope(binding.term, binding.number),
                        "reuse_key_sha256": opaque(binding.reuse_key),
                    }
                    if binding
                    else None,
                    "witness": "present" if witness else "unavailable",
                    "trigger_kind": witness.trigger_kind if witness else None,
                    "read_kind": witness.read_kind if witness else None,
                    "validation": result.validation,
                    "collected_at": result.collected_at.isoformat(),
                    "triggered_at": witness.triggered_at.isoformat()
                    if witness and witness.triggered_at
                    else None,
                    "read_completed_at": witness.read_completed_at.isoformat()
                    if witness and witness.read_completed_at
                    else None,
                    "source_availability": "unknown",
                    "trigger_to_collection": clocks["trigger_to_endpoint"],
                    "read_to_collection": clocks["read_to_endpoint"],
                }
            )
    physical = TimingDeliveryLinks(connection, observed_at)
    if "result_applications" not in missing:
        for row in rows(connection, "SELECT * FROM result_applications ORDER BY id"):
            item = {
                "ref": f"application-row:{row['id']}"
                if type(row["id"]) is int
                else "application-row:invalid",
                "purpose": None,
            }
            try:
                application = read_result_application(row)
            except ComputationRowError as error:
                applications.append({**item, "reason": error.code})
                continue
            applied_result = retained.get(application.result_id)
            if applied_result is None:
                applications.append(
                    {**item, "phase": application.phase, "reason": "result_unavailable_or_invalid"}
                )
                continue
            result = applied_result
            counts[result.purpose]["applications"] += 1
            counts[result.purpose][application.phase] += 1
            record: dict[str, Any] = {
                **item,
                "purpose": result.purpose,
                "phase": application.phase,
                "result_id": result.result_id,
                "owner": scope(application.term, application.number),
                "created_at": application.created_at.isoformat(),
                "target_kind": application.target.kind
                if application.target
                and application.target.kind
                in (
                    "status_change",
                    "publication",
                    "bill",
                    "editorial",
                    "document_review",
                    "command",
                )
                else "opaque"
                if application.target
                else None,
                "target_ref_sha256": opaque(application.target.ref) if application.target else None,
                "collection_to_application": interval(
                    result.collected_at, application.created_at, observed_at
                ),
                **_clocks(result, application.created_at, observed_at),
            }
            if application.phase == "collection":
                record["physical"] = {"status": "not_applicable", "reason": "collection_receipt"}
            else:
                links, reason = physical.for_application(row["id"], application, result)
                record["physical"] = {
                    "status": "linked" if links else "unknown",
                    "reason": reason,
                    "edges": len(links),
                }
                counts[result.purpose]["linked_projections"] += bool(links)
                for link in links:
                    counts[result.purpose]["recorded_operations"] += (
                        link["status"] == "recorded_operation"
                    )
                    endpoint = link.pop("endpoint")
                    edge = {
                        **link,
                        "application_id": row["id"],
                        "purpose": result.purpose,
                        "operation_at": endpoint
                        if link["status"] == "recorded_operation"
                        else None,
                        "application_to_operation": interval(
                            application.created_at, endpoint, observed_at
                        ),
                        **_clocks(result, endpoint, observed_at),
                    }
                    edges.append(edge)
                counts[result.purpose]["physical_edges"] += len(links)
            applications.append(record)
    populations = {}
    for label, data, table in (
        ("results", results, "computation_results"),
        ("applications", applications, "result_applications"),
    ):
        attributed = sum(item["purpose"] is not None for item in data)
        populations[label] = {
            "rows": None if table in missing else len(data),
            "attributed": attributed if table not in missing else None,
            "unattributed": len(data) - attributed if table not in missing else None,
        }
    applications_available = not missing
    physical_available = physical.available and applications_available
    populations["physical_edges"] = {"rows": len(edges) if physical_available else None}
    coverage: dict[str, dict[str, int | None]] = {}
    for population, data, metrics in (
        ("results", results, ("trigger_to_collection", "read_to_collection")),
        (
            "applications",
            applications,
            ("collection_to_application", "trigger_to_endpoint", "read_to_endpoint"),
        ),
        (
            "physical_edges",
            edges,
            ("application_to_operation", "trigger_to_endpoint", "read_to_endpoint"),
        ),
    ):
        denominator = populations[population]["rows"]
        for metric in metrics:
            known = sum(
                isinstance(item.get(metric), dict) and item[metric]["seconds"] is not None
                for item in data
            )
            coverage[f"{population}/{metric}"] = {
                "denominator_rows": denominator,
                "known_rows": known if denominator is not None else None,
                "unknown_rows": denominator - known if denominator is not None else None,
            }
    return ObservedTimingReport(
        observed_at=observed_at.isoformat(),
        missing_schema=tuple(sorted(set(missing + physical.missing))),
        populations=populations,
        clock_coverage=coverage,
        results=tuple(results),
        applications=tuple(applications),
        physical_edges=tuple(edges),
        purposes=tuple(
            {
                "purpose": kind,
                "results": counts[kind]["results"]
                if "computation_results" not in missing
                else None,
                "witnesses": counts[kind]["witnesses"]
                if "computation_results" not in missing
                else None,
                "applications": counts[kind]["applications"] if applications_available else None,
                "collection_receipts": counts[kind]["collection"]
                if applications_available
                else None,
                "projection_receipts": counts[kind]["projection"]
                if applications_available
                else None,
                "physical_edges": counts[kind]["physical_edges"] if physical_available else None,
                "projection_rows_with_input_reference": counts[kind]["linked_projections"]
                if physical_available
                else None,
                "recorded_operation_edges": counts[kind]["recorded_operations"]
                if physical_available
                else None,
                "physical_evidence": physical.policy(kind),
            }
            for kind in PURPOSES
        ),
    )
