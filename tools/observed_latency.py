"""Read saved observation clocks without inferring source availability or read completion."""

import datetime as dt
import sqlite3
from collections import Counter
from contextlib import suppress
from typing import Literal

from pydantic import BaseModel, ConfigDict

from lexinform.models import (
    AnalysisRecord,
    Bill,
    ComputationResult,
    DeliveryPlan,
    ResultApplication,
    StatusChange,
)
from lexinform.models.batch import BatchKind
from lexinform.models.commands import DeliveryResolution
from lexinform.models.wykaz import WykazEntry

Metric = Literal[
    "read_completed", "analysis_collected", "analysis_applied", "delivery", "source_discovery"
]


class LatencyRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metric: Metric
    ref: str
    term: int
    number: str
    start_field: str
    end_field: str
    start: dt.datetime | None = None
    end: dt.datetime | None = None
    seconds: float | None = None
    reason: str | None = None
    generation: int | None = None
    purpose: BatchKind | None = None
    projection_match: bool = False
    delivery_class: str | None = None
    source_precision: Literal["minute"] | None = None
    source_timezone: Literal["Europe/Warsaw"] | None = None
    source_endpoint_reason: Literal["source_event_binding_unavailable"] | None = (
        "source_event_binding_unavailable"
    )


class LatencyGroup(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metric: Metric
    rows: int
    known: int
    unknown: dict[str, int]


class ObservedLatencyReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    observed_at: dt.datetime
    coverage: Literal["partial"] = "partial"
    missing_schema: tuple[str, ...]
    groups: tuple[LatencyGroup, ...]
    rows: tuple[LatencyRow, ...]
    limitations: tuple[str, ...] = (
        "Recorded owner/event clocks only; no current applicability or legal chronology verdict.",
        "Read completion is unavailable; recorded_at and run clocks are not read endpoints.",
        "Collection is our observation, not provider completion; application is not delivery.",
        "Official availability is unknown except an exactly bound Wykaz announcement.",
        "Rows mix retained records with explicit per-owner coverage markers; "
        "they are not distinct bills, a success-rate denominator or full historical traffic.",
        "Source-to-analysis/application/delivery bindings are unavailable; "
        "announcement dates alone do not bind later inputs.",
        "Only primary analysis is timed from discovery; later purposes lack a saved trigger time.",
    )


def _rows(connection: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    return cursor.execute(query).fetchall()


def _time(value: object, observed: dt.datetime) -> tuple[dt.datetime | None, str | None]:
    if value is None or value == "":
        return None, "missing_timestamp"
    try:
        stamp = value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None, "invalid_timestamp"
    if stamp.utcoffset() is None:
        return None, "timezone_unknown"
    if stamp > observed:
        return stamp, "timestamp_after_observation"
    return stamp, None


def _projection_matches(bill: sqlite3.Row, result: ComputationResult) -> bool:
    columns = set(bill.keys())
    if "analysis_json" not in columns or not bill["analysis_json"]:
        return False
    try:
        record = AnalysisRecord.model_validate_json(bill["analysis_json"])
    except ValueError:
        return False
    return (
        record.computation_id == result.result_id and record.request_sha256 == result.request_sha256
    )


def _delivery_owner_error(identity: int, owner: tuple[int, str], plan: DeliveryPlan) -> str | None:
    snapshot = plan.bill_json
    if plan.update_group and plan.publication_ids[0] != identity:
        members = [item for item in plan.update_group if item.publication_id == identity]
        if len(members) != 1:
            return "invalid_delivery_owner"
        snapshot = members[0].bill_json
    try:
        frozen = Bill.model_validate_json(snapshot)
    except ValueError:
        return "invalid_delivery_owner"
    return "delivery_owner_mismatch" if (frozen.term, frozen.number) != owner else None


def _span(
    metric: Metric,
    ref: str,
    term: int,
    number: str,
    start: object,
    end: object,
    observed: dt.datetime,
    *,
    start_field: str = "bills.first_seen_at",
    end_field: str,
    reason: str | None = None,
    generation: int | None = None,
    purpose: BatchKind | None = None,
    projection_match: bool = False,
    delivery_class: str | None = None,
    source: bool = False,
) -> LatencyRow:
    began, start_error = _time(start, observed)
    ended, end_error = _time(end, observed)
    failure = reason or start_error or end_error
    if failure is None and began is not None and ended is not None and ended < began:
        failure = "reversed_timestamps"
    seconds = (ended - began).total_seconds() if failure is None and began and ended else None
    return LatencyRow(
        metric=metric,
        ref=ref,
        term=term,
        number=number,
        start_field=start_field,
        end_field=end_field,
        start=began,
        end=ended,
        seconds=seconds,
        reason=failure,
        generation=generation,
        purpose=purpose,
        projection_match=projection_match,
        delivery_class=delivery_class,
        source_precision="minute" if source else None,
        source_timezone="Europe/Warsaw" if source else None,
        source_endpoint_reason=None
        if metric == "source_discovery"
        else "source_event_binding_unavailable",
    )


class _Inventory:
    def __init__(self, connection: sqlite3.Connection, observed: dt.datetime) -> None:
        self.connection = connection
        self.observed = observed
        self.output: list[LatencyRow] = []
        self.missing: set[str] = set()
        self.bills: dict[tuple[int, str], sqlite3.Row] = {}
        self.results: dict[str, ComputationResult] = {}
        self.results_available = False
        self.analysis_available = False
        self.generation_available = False

    def available(self, table: str, columns: set[str]) -> bool:
        actual = {row["name"] for row in _rows(self.connection, f'PRAGMA table_info("{table}")')}
        absent = columns - actual
        self.missing.update(table + "." + name for name in absent)
        return not absent

    def append(
        self,
        metric: Metric,
        ref: str,
        owner: tuple[int, str],
        end: object,
        *,
        end_field: str,
        reason: str | None = None,
        generation: int | None = None,
        purpose: BatchKind | None = None,
        projection_match: bool = False,
    ) -> None:
        bill = self.bills.get(owner)
        self.output.append(
            _span(
                metric,
                ref,
                *owner,
                bill["first_seen_at"] if bill else None,
                end,
                self.observed,
                end_field=end_field,
                reason=reason or ("missing_owner" if bill is None else None),
                generation=generation,
                purpose=purpose,
                projection_match=projection_match,
            )
        )

    def owners(self) -> None:
        base_available = self.available("bills", {"term", "number", "first_seen_at"})
        self.analysis_available = self.available("bills", {"analysis_json"})
        self.generation_available = self.available("bills", {"analysis_generation"})
        wykaz_available = self.available("bills", {"wykaz_json"})
        if not base_available:
            return
        for bill in _rows(self.connection, "SELECT * FROM bills ORDER BY term,number"):
            owner = (bill["term"], bill["number"])
            self.bills[owner] = bill
            self.append(
                "read_completed",
                f"bills/{owner[0]}/{owner[1]}",
                owner,
                None,
                end_field="unavailable",
                reason="read_completion_unavailable",
            )
            entry = None
            with suppress(ValueError):
                entry = (
                    WykazEntry.model_validate_json(bill["wykaz_json"])
                    if wykaz_available and bill["wykaz_json"]
                    else None
                )
            bound = entry is not None and entry.bill_number == owner[1]
            stamp = entry.published_at if bound and entry is not None else None
            reason = (
                "source_schema_unavailable"
                if not wykaz_available
                else None
                if bound
                else "official_availability_unknown"
            )
            if stamp is not None and (stamp.second or stamp.microsecond):
                reason = "source_precision_mismatch"
            self.output.append(
                _span(
                    "source_discovery",
                    f"bills/{owner[0]}/{owner[1]}",
                    *owner,
                    stamp,
                    bill["first_seen_at"],
                    self.observed,
                    start_field="wykaz.Data publikacji",
                    end_field="bills.first_seen_at",
                    reason=reason,
                    source=bound,
                )
            )

    def collected(self) -> None:
        seen: set[tuple[int, str]] = set()
        results_available = self.available(
            "computation_results",
            {"result_id", "provider", "batch_id", "custom_id", "term", "number", "result_json"},
        )
        self.results_available = results_available
        if results_available:
            for row in _rows(
                self.connection, "SELECT * FROM computation_results ORDER BY result_id"
            ):
                owner = (row["term"], row["number"])
                seen.add(owner)
                reason = None
                result = None
                try:
                    result = ComputationResult.model_validate_json(row["result_json"])
                except ValueError:
                    reason = "invalid_result"
                if result is not None:
                    expected = (
                        result.result_id,
                        result.provider,
                        result.batch_id,
                        result.custom_id,
                        result.term,
                        result.number,
                    )
                    if expected != tuple(
                        row[key]
                        for key in (
                            "result_id",
                            "provider",
                            "batch_id",
                            "custom_id",
                            "term",
                            "number",
                        )
                    ):
                        reason = "result_identity_mismatch"
                    else:
                        self.results[result.result_id] = result
                        if result.validation != "validated":
                            reason = "unvalidated_result"
                bill = self.bills.get(owner)
                projection = False
                if bill is not None and result is not None:
                    projection = _projection_matches(bill, result)
                    if result.generation is not None and not self.generation_available:
                        reason = reason or "generation_schema_unavailable"
                    if (
                        result.generation is not None
                        and self.generation_available
                        and result.generation > bill["analysis_generation"]
                    ):
                        reason = reason or "future_generation"
                    if (
                        result.generation is not None
                        and self.generation_available
                        and result.generation != bill["analysis_generation"]
                        and not projection
                    ):
                        reason = reason or "stale_generation"
                    if result.purpose != "analysis":
                        reason = reason or "trigger_event_unbound"
                self.append(
                    "analysis_collected",
                    "computation_results/" + str(row["result_id"]),
                    owner,
                    result.collected_at if result else None,
                    end_field="ComputationResult.collected_at",
                    reason=reason,
                    generation=result.generation if result else None,
                    purpose=result.purpose if result else None,
                    projection_match=projection and reason is None,
                )
        for owner, bill in self.bills.items():
            if owner not in seen:
                self.append(
                    "analysis_collected",
                    f"bills/{owner[0]}/{owner[1]}",
                    owner,
                    None,
                    end_field="unavailable",
                    reason=(
                        "result_schema_unavailable"
                        if not results_available
                        else "projection_schema_unavailable"
                        if not self.analysis_available
                        else "legacy_result_unbound"
                        if bill["analysis_json"]
                        else "missing_result"
                    ),
                )

    def applied(self) -> None:
        seen: set[tuple[int, str]] = set()
        applications_available = self.available(
            "result_applications",
            {"id", "result_id", "term", "number", "generation", "phase", "application_json"},
        )
        if applications_available:
            for row in _rows(
                self.connection,
                "SELECT * FROM result_applications WHERE phase='projection' ORDER BY id",
            ):
                owner = (row["term"], row["number"])
                seen.add(owner)
                app = None
                result: ComputationResult | None = None
                reason = None
                try:
                    app = ResultApplication.model_validate_json(row["application_json"])
                except ValueError:
                    reason = "invalid_application"
                if app is not None:
                    if (app.result_id, app.term, app.number, app.generation, app.phase) != tuple(
                        row[key] for key in ("result_id", "term", "number", "generation", "phase")
                    ):
                        reason = "application_identity_mismatch"
                    result = self.results.get(app.result_id)
                    if result is None:
                        reason = reason or (
                            "missing_valid_result"
                            if self.results_available
                            else "result_schema_unavailable"
                        )
                    elif (result.term, result.number) != owner:
                        reason = reason or "cross_owner_result"
                    elif result.validation != "validated":
                        reason = reason or "unvalidated_result"
                    elif app.generation is not None and app.generation != result.generation:
                        reason = reason or "application_result_generation_mismatch"
                    if result is not None:
                        collected, error = _time(result.collected_at, self.observed)
                        applied, app_error = _time(app.created_at, self.observed)
                        if (
                            collected is not None
                            and applied is not None
                            and error is None
                            and app_error is None
                            and applied < collected
                        ):
                            reason = reason or "application_before_collection"
                    bill = self.bills.get(owner)
                    if app.generation is not None and not self.generation_available:
                        reason = reason or "generation_schema_unavailable"
                    if (
                        app.generation is not None
                        and bill is not None
                        and self.generation_available
                        and app.generation != bill["analysis_generation"]
                        and (result is None or not _projection_matches(bill, result))
                    ):
                        reason = reason or "stale_generation"
                    if bill is not None and result is not None and result.purpose != "analysis":
                        reason = reason or "trigger_event_unbound"
                self.append(
                    "analysis_applied",
                    "result_applications/" + str(row["id"]),
                    owner,
                    app.created_at if app else None,
                    end_field="ResultApplication.created_at",
                    reason=reason,
                    generation=app.generation if app else None,
                    purpose=result.purpose if result is not None else None,
                )
        for owner in self.bills:
            if owner not in seen:
                self.append(
                    "analysis_applied",
                    f"bills/{owner[0]}/{owner[1]}",
                    owner,
                    None,
                    end_field="unavailable",
                    reason="missing_application"
                    if applications_available
                    else "application_schema_unavailable",
                )

    def deliveries(self) -> None:
        columns = {
            "id",
            "term",
            "number",
            "kind",
            "status",
            "channel_id",
            "message_id",
            "status_change_id",
            "sent_at",
            "delivery_json",
        }
        if not self.available("publications", columns):
            return
        publications = {
            row["id"]: row
            for row in _rows(self.connection, "SELECT * FROM publications ORDER BY id")
        }
        changes = {}
        if self.available("status_changes", {"id", "term", "number", "detected_at"}):
            changes = {
                row["id"]: row for row in _rows(self.connection, "SELECT * FROM status_changes")
            }
        manual: dict[int, DeliveryResolution | None] = {}
        resolutions_available = self.available(
            "delivery_resolutions", {"id", "publication_id", "resolution_json"}
        )
        if resolutions_available:
            for row in _rows(
                self.connection,
                "SELECT publication_id,resolution_json FROM delivery_resolutions ORDER BY id",
            ):
                try:
                    resolution = DeliveryResolution.model_validate_json(row["resolution_json"])
                except ValueError:
                    manual[row["publication_id"]] = None
                    continue
                if resolution.publication_id != row["publication_id"]:
                    manual[row["publication_id"]] = None
                elif resolution.action == "confirm":
                    parent = publications.get(resolution.publication_id)
                    if parent is None or (
                        resolution.term,
                        resolution.number,
                        resolution.kind.value,
                        resolution.channel_id,
                    ) != tuple(parent[key] for key in ("term", "number", "kind", "channel_id")):
                        manual[resolution.publication_id] = None
                        continue
                    for identity in resolution.publication_ids or (resolution.publication_id,):
                        manual[identity] = resolution
        memberships: dict[int, set[int]] = {}
        if self.available("digest_event_links", {"source_publication_id", "owner_publication_id"}):
            for row in _rows(self.connection, "SELECT * FROM digest_event_links"):
                memberships[row["source_publication_id"]] = {row["owner_publication_id"]}
        plans: dict[int, DeliveryPlan] = {}
        invalid_plans: set[int] = set()
        owner_errors: dict[int, str] = {}
        for identity, pub in publications.items():
            if pub["delivery_json"]:
                try:
                    plan = DeliveryPlan.model_validate_json(pub["delivery_json"])
                except ValueError:
                    invalid_plans.add(identity)
                    continue
                plans[identity] = plan
                owner_error = _delivery_owner_error(identity, (pub["term"], pub["number"]), plan)
                if owner_error is not None:
                    owner_errors[identity] = owner_error
                for member in plan.update_group:
                    target = publications.get(member.publication_id)
                    if target is None:
                        owner_errors[identity] = "invalid_delivery_member_snapshot"
                        continue
                    try:
                        member_bill = Bill.model_validate_json(member.bill_json)
                        member_change = StatusChange.model_validate_json(member.change_json)
                    except ValueError:
                        owner_errors[identity] = "invalid_delivery_member_snapshot"
                        continue
                    if (
                        (member_bill.term, member_bill.number) != (target["term"], target["number"])
                        or (member_change.term, member_change.number)
                        != (target["term"], target["number"])
                        or member_change.id is None
                        or member_change.id != target["status_change_id"]
                    ):
                        owner_errors[identity] = "invalid_delivery_member_snapshot"
                for member_id in plan.publication_ids:
                    memberships.setdefault(member_id, set()).add(identity)
        messages = Counter(
            (pub["channel_id"], pub["message_id"])
            for pub in publications.values()
            if pub["message_id"] is not None
        )
        for identity, pub in publications.items():
            owner = (pub["term"], pub["number"])
            bill = self.bills.get(owner)
            start = bill["first_seen_at"] if bill is not None else None
            start_field = "bills.first_seen_at"
            reason = "missing_owner" if bill is None else None
            if pub["status_change_id"] is not None:
                change = changes.get(pub["status_change_id"])
                if change is None or (change["term"], change["number"]) != owner:
                    reason = "event_owner_mismatch"
                    start = None
                else:
                    start = change["detected_at"]
                    start_field = "status_changes.detected_at"
            elif pub["kind"] != "new_bill":
                start = None
                reason = "missing_event_anchor"
            delivery_class = "recorded_confirmation"
            end = pub["sent_at"]
            end_field = "publications.sent_at"
            if pub["status"] != "sent" or pub["message_id"] is None:
                reason = reason or "delivery_unconfirmed"
                delivery_class = "unconfirmed"
            elif pub["channel_id"] is None or pub["channel_id"] == "":
                reason = reason or "destination_unbound"
                delivery_class = "destination_unbound"
            elif identity in owner_errors:
                reason = reason or owner_errors[identity]
            elif identity in invalid_plans:
                reason = reason or "invalid_delivery_plan"
            elif identity in manual:
                confirmation = manual[identity]
                delivery_class = "manual_confirmation"
                reason = reason or "manual_confirmation_not_send_time"
                end_field = "DeliveryResolution.resolved_at"
                end = confirmation.resolved_at if confirmation is not None else None
                if (
                    confirmation is None
                    or confirmation.channel_id != pub["channel_id"]
                    or confirmation.message_id != pub["message_id"]
                ):
                    reason = "invalid_manual_membership"
            elif not resolutions_available:
                reason = reason or "manual_history_unavailable"
            elif identity in memberships:
                delivery_class = "recorded_shared_membership"
                parents = [publications.get(parent) for parent in memberships[identity]]
                if any(
                    parent is None
                    or parent["id"] in owner_errors
                    or parent["channel_id"] != pub["channel_id"]
                    or parent["status"] != "sent"
                    or parent["message_id"] != pub["message_id"]
                    or parent["sent_at"] != pub["sent_at"]
                    for parent in parents
                ):
                    reason = reason or "invalid_delivery_membership"
            elif messages[(pub["channel_id"], pub["message_id"])] > 1:
                delivery_class = "shared_or_reused_message"
                reason = reason or "shared_message_without_membership"
            elif identity not in plans:
                delivery_class = "legacy_confirmation"
                reason = reason or "legacy_delivery_unbound"
            self.output.append(
                _span(
                    "delivery",
                    f"publications/{identity}",
                    *owner,
                    start,
                    end,
                    self.observed,
                    start_field=start_field,
                    end_field=end_field,
                    reason=reason,
                    delivery_class=delivery_class,
                )
            )


def observed_latency(
    connection: sqlite3.Connection, observed_at: dt.datetime
) -> ObservedLatencyReport:
    if observed_at.utcoffset() is None:
        raise ValueError("Observed time must include a timezone")
    inventory = _Inventory(connection, observed_at)
    inventory.owners()
    inventory.collected()
    inventory.applied()
    inventory.deliveries()
    metrics: tuple[Metric, ...] = (
        "read_completed",
        "analysis_collected",
        "analysis_applied",
        "delivery",
        "source_discovery",
    )
    groups = []
    for metric in metrics:
        items = [row for row in inventory.output if row.metric == metric]
        groups.append(
            LatencyGroup(
                metric=metric,
                rows=len(items),
                known=sum(row.seconds is not None for row in items),
                unknown=dict(
                    sorted(Counter(row.reason for row in items if row.reason is not None).items())
                ),
            )
        )
    return ObservedLatencyReport(
        observed_at=observed_at.astimezone(dt.UTC),
        missing_schema=tuple(sorted(inventory.missing)),
        groups=tuple(groups),
        rows=tuple(inventory.output),
    )
