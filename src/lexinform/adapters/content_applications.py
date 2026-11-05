"""Resolve explicitly profiled editorial references against retained projection receipts."""

import sqlite3

from lexinform.adapters.computation_history import read_computation_result, read_result_application
from lexinform.adapters.content_source_refs import ContentSourceRows
from lexinform.adapters.secondary_dependencies import (
    SecondaryPurpose,
    validate_secondary_dependency,
)
from lexinform.content_versions import content_delivery_mismatch, content_snapshot_refs
from lexinform.json_storage import read_json
from lexinform.models import (
    AmendmentsRecord,
    ContentVersion,
    DeliveryPlan,
    ResultApplication,
    ResultApplicationTarget,
    StatusChange,
    amendments_stage,
)


class ContentApplicationError(ValueError):
    pass


def _rows(
    connection: sqlite3.Connection, sql: str, parameters: tuple[object, ...] = ()
) -> list[sqlite3.Row]:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        return cursor.execute(sql, parameters).fetchall()
    finally:
        cursor.close()


def _application(row: sqlite3.Row) -> ResultApplication:
    application = read_result_application(row)
    if (
        application.phase != "projection"
        or application.generation is not None
        or application.target is None
        or application.target.kind not in ("status_change", "publication")
        or not application.target.ref.isascii()
        or not application.target.ref.isdecimal()
        or str(int(application.target.ref)) != application.target.ref
        or not 0 < int(application.target.ref) < 2**63
    ):
        raise ContentApplicationError("content_application_columns_or_target_invalid")
    return application


def _result(connection: sqlite3.Connection, application: ResultApplication) -> str:
    rows = _rows(
        connection, "SELECT * FROM computation_results WHERE result_id=?", (application.result_id,)
    )
    if len(rows) != 1:
        raise ContentApplicationError("content_application_result_unavailable")
    result = read_computation_result(rows[0])
    allowed = (
        ("amendments", "supplement")
        if application.target and application.target.kind == "status_change"
        else ("amendments",)
    )
    if result.validation != "validated" or result.purpose not in allowed:
        raise ContentApplicationError("content_application_result_invalid")
    return result.purpose


def find_projection_application_ids(
    connection: sqlite3.Connection, result_id: str, target: ResultApplicationTarget
) -> tuple[int, ...]:
    try:
        found: list[int] = []
        rows = _rows(
            connection,
            "SELECT * FROM result_applications WHERE result_id=? AND generation IS NULL"
            " AND phase='projection' AND json_extract(application_json, '$.target.kind')=?"
            " AND json_extract(application_json, '$.target.ref')=? ORDER BY id LIMIT 33",
            (result_id, target.kind, target.ref),
        )
        if len(rows) > 32:
            raise ContentApplicationError("content_application_lookup_ambiguous")
        for row in rows:
            application = _application(row)
            if application.result_id != result_id or application.target != target:
                raise ContentApplicationError("content_application_lookup_mismatch")
            _result(connection, application)
            found.append(row["id"])
        return tuple(found)
    except ValueError, TypeError, RecursionError, sqlite3.Error, KeyError, IndexError:
        raise ContentApplicationError("content_application_reference_invalid") from None


def verify_content_applications(
    connection: sqlite3.Connection,
    version: ContentVersion,
    *,
    publication_id: int | None = None,
    retained: bool = True,
) -> None:
    if version.application_ref_profile is None:
        return
    try:
        _verify(connection, version, publication_id=publication_id, retained=retained)
    except ValueError, TypeError, RecursionError, sqlite3.Error, KeyError, IndexError:
        raise ContentApplicationError("content_application_reference_invalid") from None


def _verify(
    connection: sqlite3.Connection,
    version: ContentVersion,
    *,
    publication_id: int | None,
    retained: bool,
) -> None:
    reader = ContentSourceRows(connection)
    snapshots = {
        identity: reader.get_content_snapshot(identity)
        for _, identity in content_snapshot_refs(version)
    }
    change_snapshot = snapshots.get(version.change_snapshot_id or "")
    if change_snapshot is None or change_snapshot.kind != "change":
        raise ValueError("content_application_change_unavailable")
    change = StatusChange.model_validate(read_json(change_snapshot.payload_json))
    if (
        change.id is None
        or change.id <= 0
        or change.id != version.status_change_id
        or (version.reason == "original" and amendments_stage(change.new_stages) is None)
    ):
        raise ValueError("content_application_change_mismatch")
    saved_plan = snapshots.get(version.plan_snapshot_id)
    if saved_plan is None or saved_plan.kind != "plan":
        raise ValueError("content_application_plan_unavailable")
    semantic = DeliveryPlan.model_validate(read_json(saved_plan.payload_json))
    if (
        semantic.destination != "direct"
        or semantic.update_group
        or semantic.sitting_group
        or semantic.continuation_ids
        or semantic.continuation_parent is not None
        or semantic.absorbed_by is not None
        or semantic.digest_event_ids
        or semantic.digest_parts
        or semantic.digest_ref
        or (version.reason == "original" and semantic.revision_of is not None)
        or (
            version.reason == "amendments_revision"
            and semantic.revision_of != version.root_publication_id
        )
    ):
        raise ValueError("content_application_plan_scope_invalid")
    projections = ([change.amendments] if change.amendments else []) + (
        [] if version.reason == "amendments_revision" else list(change.supplements)
    )
    targets: set[int] = set()
    for identity in version.application_ids:
        rows = _rows(connection, "SELECT * FROM result_applications WHERE id=?", (identity,))
        if len(rows) != 1:
            raise ValueError("content_application_unavailable")
        application = _application(rows[0])
        purpose = _result(connection, application)
        target = application.target
        assert target is not None, "the application decoder requires an explicit target"
        if application.result_id not in version.computation_ids:
            raise ValueError("content_application_result_not_frozen")
        matching = [
            record for record in projections if record.computation_id == application.result_id
        ]
        if not matching:
            raise ValueError("content_application_projection_not_frozen")
        for record in matching:
            expected: SecondaryPurpose = (
                "amendments" if isinstance(record, AmendmentsRecord) else "supplement"
            )
            if purpose != expected:
                raise ValueError("content_application_purpose_mismatch")
            validate_secondary_dependency(connection, record, expected)
        if version.reason == "original":
            if target.kind != "status_change" or target.ref != str(change.id):
                raise ValueError("content_application_target_mismatch")
            targets.add(version.root_publication_id)
        elif target.kind != "publication":
            raise ValueError("content_application_target_mismatch")
        else:
            targets.add(int(target.ref))
    if len(targets) != 1:
        raise ValueError("content_application_target_ambiguous")
    physical_id = next(iter(targets))
    if publication_id is not None and publication_id != physical_id:
        raise ValueError("content_application_binding_mismatch")
    bindings = _rows(
        connection,
        "SELECT publication_id FROM publication_content WHERE content_id=?",
        (version.content_id,),
    )
    if any(row["publication_id"] != physical_id for row in bindings):
        raise ValueError("content_application_binding_mismatch")
    rows = _rows(connection, "SELECT * FROM publications WHERE id=?", (physical_id,))
    if not rows:
        if retained:
            return
        raise ValueError("content_application_physical_unavailable")
    physical = rows[0]
    expected_kind = "status_update" if version.reason == "original" else "amendments_revision"
    if physical["kind"] != expected_kind or (
        version.reason == "original" and physical["status_change_id"] != change.id
    ):
        raise ValueError("content_application_physical_target_mismatch")
    if physical["delivery_json"]:
        plan = DeliveryPlan.model_validate(read_json(physical["delivery_json"]))
        if (
            version.reason == "amendments_revision"
            and plan.revision_of != version.root_publication_id
        ) or content_delivery_mismatch(
            version, {key: value for key, value in snapshots.items() if value is not None}, plan
        ):
            raise ValueError("content_application_frozen_target_mismatch")
    elif not retained:
        raise ValueError("content_application_physical_unavailable")
