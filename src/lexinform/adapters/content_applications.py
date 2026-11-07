"""Resolve explicitly profiled editorial references against retained projection receipts."""

import sqlite3
from datetime import datetime

from lexinform.adapters.computation_history import read_computation_result, read_result_application
from lexinform.adapters.content_source_refs import ContentSourceRows
from lexinform.adapters.input_receipts import validate_secondary_receipt_parents
from lexinform.adapters.secondary_dependencies import (
    SecondaryPurpose,
    validate_secondary_dependency,
)
from lexinform.content_versions import content_delivery_mismatch, content_snapshot_refs
from lexinform.errors import SupplementContentEvidenceError
from lexinform.json_storage import read_json
from lexinform.models import (
    AmendmentsRecord,
    Bill,
    ComputationResult,
    ContentVersion,
    DeliveryPlan,
    DigestItemMeta,
    ResultApplication,
    ResultApplicationTarget,
    StatusChange,
    SupplementInputReceipt,
    SupplementRecord,
    amendments_stage,
)
from lexinform.models.batch import batch_item_meta
from lexinform.models.content import SupplementContentEvidence
from lexinform.models.original_delivery_content import OriginalDeliveryPlan


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


def _result(connection: sqlite3.Connection, application: ResultApplication) -> ComputationResult:
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
    return result


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
        error = (
            SupplementContentEvidenceError
            if version.supplement_evidence is not None
            else ContentApplicationError
        )
        raise error("content_application_reference_invalid") from None


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
    supplement = version.supplement_evidence
    if (
        change.id is None
        or change.id <= 0
        or change.id != version.status_change_id
        or (
            version.reason == "original"
            and supplement is None
            and amendments_stage(change.new_stages) is None
        )
    ):
        raise ValueError("content_application_change_mismatch")
    if supplement is not None:
        bill_snapshot = snapshots.get(version.bill_context_id)
        if bill_snapshot is None or bill_snapshot.kind != "bill":
            raise ValueError("supplement_content_bill_unavailable")
        bill = Bill.model_validate(read_json(bill_snapshot.payload_json))
        if (
            (bill.term, bill.number) != (change.term, change.number)
            or change.amendments is not None
            or len(change.supplements) != 1
            or amendments_stage(change.new_stages) is not None
        ):
            raise ValueError("supplement_content_change_scope_invalid")
    saved_plan = snapshots.get(version.plan_snapshot_id)
    if saved_plan is None or saved_plan.kind != "plan":
        raise ValueError("content_application_plan_unavailable")
    direct = version.direct_stage_evidence
    multipart = direct is not None and direct.multipart == "direct-stage-application-parts-v1"
    physical_plan = (
        OriginalDeliveryPlan.model_validate_json(saved_plan.payload_json) if multipart else None
    )
    semantic = (
        physical_plan.root
        if physical_plan is not None
        else DeliveryPlan.model_validate(read_json(saved_plan.payload_json))
    )
    if (
        semantic.destination != "direct"
        or semantic.update_group
        or semantic.sitting_group
        or (semantic.continuation_ids and not multipart)
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
    if supplement is not None and (
        semantic.publication_ids
        or semantic.continuation_root is not None
        or semantic.continuation_index != 1
        or semantic.edit_message_id is not None
        or semantic.primary_json
    ):
        raise ValueError("supplement_content_plan_scope_invalid")
    projections = ([change.amendments] if change.amendments else []) + (
        [] if version.reason == "amendments_revision" else list(change.supplements)
    )
    targets: set[int] = set()
    for identity in version.application_ids:
        rows = _rows(connection, "SELECT * FROM result_applications WHERE id=?", (identity,))
        if len(rows) != 1:
            raise ValueError("content_application_unavailable")
        application = _application(rows[0])
        result = _result(connection, application)
        purpose = result.purpose
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
        if supplement is not None:
            _verify_supplement(connection, reader, supplement, change, application, result)
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
    allowed = set(physical_plan.bindings) if physical_plan is not None else {physical_id}
    if any(row["publication_id"] not in allowed for row in bindings):
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
    if supplement is not None and (
        (physical["term"], physical["number"]) != (change.term, change.number)
        or _rows(
            connection,
            "SELECT 1 FROM digest_event_links WHERE source_publication_id=?",
            (physical_id,),
        )
    ):
        raise ValueError("supplement_content_publication_owner_invalid")
    if physical["delivery_json"] and not multipart:
        plan = DeliveryPlan.model_validate(read_json(physical["delivery_json"]))
        if (
            version.reason == "amendments_revision"
            and plan.revision_of != version.root_publication_id
        ) or content_delivery_mismatch(
            version, {key: value for key, value in snapshots.items() if value is not None}, plan
        ):
            raise ValueError("content_application_frozen_target_mismatch")
    elif not physical["delivery_json"] and not retained:
        raise ValueError("content_application_physical_unavailable")


def _verify_supplement(
    connection: sqlite3.Connection,
    reader: ContentSourceRows,
    evidence: SupplementContentEvidence,
    change: StatusChange,
    application: ResultApplication,
    result: ComputationResult,
) -> None:
    selection_id = _supplement_selection(connection, change, application, result)
    observation = reader.get_source_observation(evidence.source_observation_id)
    projected = change.supplements[0]
    if observation is None or (
        observation.term,
        observation.number,
        observation.purpose,
        observation.selection_id,
        observation.document.kind,
        observation.document.url,
    ) != (
        change.term,
        change.number,
        "analysis",
        selection_id,
        projected.source_kind,
        projected.source_url,
    ):
        raise ValueError("supplement_content_observation_invalid")


def _supplement_selection(
    connection: sqlite3.Connection,
    change: StatusChange,
    application: ResultApplication,
    result: ComputationResult,
) -> str:
    projected = change.supplements[0]
    owner = (change.term, change.number)
    paid = result.record
    if (
        application.outcome != "applied"
        or application.reason != "retained_result"
        or (application.term, application.number) != owner
        or (result.term, result.number) != owner
        or not isinstance(paid, SupplementRecord)
        or not paid.number
        or paid.scan_input is not None
        or paid.digest is None
        or projected.digest != paid.digest
        or result.request_sha256 is None
        or projected.request_sha256 != result.request_sha256
        or (projected.number, projected.source_kind, projected.source_url)
        != (paid.number, paid.source_kind, paid.source_url)
    ):
        raise ValueError("supplement_content_result_invalid")
    rows = _rows(
        connection,
        "SELECT i.*, b.provider FROM llm_batch_items i JOIN llm_batches b USING (batch_id)"
        " WHERE i.batch_id=? AND i.custom_id=?",
        (result.batch_id, result.custom_id),
    )
    if len(rows) != 1:
        raise ValueError("supplement_content_item_unavailable")
    item = rows[0]
    if (
        (item["term"], item["number"]) != owner
        or item["provider"] != result.provider
        or item["call_kind"] != "supplement"
        or item["consumed_at"] is None
        or datetime.fromisoformat(item["consumed_at"]).utcoffset() is None
    ):
        raise ValueError("supplement_content_item_invalid")
    meta = batch_item_meta("supplement", item["meta_json"])
    if not isinstance(meta, DigestItemMeta):
        raise ValueError("supplement_content_item_metadata_invalid")
    receipt = meta.prepared_input_receipt
    if (
        not isinstance(receipt, SupplementInputReceipt)
        or meta.scan_input is not None
        or receipt.retention != "complete"
        or receipt.source_selection_id is None
        or receipt.artifact_sha256 is None
        or receipt.extraction_id is None
        or receipt.request_sha256 != result.request_sha256
        or meta.request_sha256 != result.request_sha256
        or (meta.document_number, meta.source_kind, meta.source_url)
        != (paid.number, paid.source_kind, paid.source_url)
    ):
        raise ValueError("supplement_content_receipt_invalid")
    validate_secondary_receipt_parents(connection, receipt)
    return receipt.source_selection_id


def find_supplement_content_evidence(
    connection: sqlite3.Connection, change: StatusChange
) -> tuple[int, SupplementContentEvidence] | None:
    if change.id is None or len(change.supplements) != 1:
        return None
    projected = change.supplements[0]
    if projected.computation_id is None:
        return None
    target = ResultApplicationTarget(kind="status_change", ref=str(change.id))
    try:
        ids = find_projection_application_ids(connection, projected.computation_id, target)
        if len(ids) != 1:
            return None
        (identity,) = ids
        (row,) = _rows(connection, "SELECT * FROM result_applications WHERE id=?", (identity,))
        application = _application(row)
        result = _result(connection, application)
        selection_id = _supplement_selection(connection, change, application, result)
        observations = ContentSourceRows(connection).source_observations_for_selection(
            change.term, change.number, selection_id
        )
        if len(observations) != 1:
            return None
        (source,) = observations
        if (source.document.kind, source.document.url) != (
            projected.source_kind,
            projected.source_url,
        ):
            return None
        return identity, SupplementContentEvidence(source_observation_id=source.observation_id)
    except ValueError, TypeError, RecursionError, sqlite3.Error, KeyError, IndexError:
        return None
