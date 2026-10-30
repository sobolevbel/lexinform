"""Validate both document-work consumers and indexed frozen parents without executing them."""

import sqlite3
from collections.abc import Iterator
from datetime import datetime

from lexinform.models import (
    AmendmentsRecord,
    Bill,
    ComputationResult,
    ContentSnapshot,
    ContentVersion,
    DeliveryPlan,
    DocumentWork,
    ResultApplication,
    ResultApplicationTarget,
    StatusChange,
)
from lexinform.models.content import editorial_bill_snapshot
from lexinform.models.revision_work import AmendmentRevisionApplicationWork, decode_document_work


class DocumentWorkRowError(ValueError):
    pass


def _rows(
    connection: sqlite3.Connection, sql: str, parameters: tuple[object, ...] = ()
) -> Iterator[sqlite3.Row]:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        yield from cursor.execute(sql, parameters)
    finally:
        cursor.close()


def _one(
    connection: sqlite3.Connection, sql: str, parameters: tuple[object, ...], code: str
) -> sqlite3.Row:
    row = next(_rows(connection, sql, parameters), None)
    if row is None:
        raise DocumentWorkRowError(code)
    return row


def _snapshot(connection: sqlite3.Connection, identity: str, kind: str) -> ContentSnapshot:
    row = _one(
        connection,
        "SELECT * FROM content_snapshots WHERE snapshot_id=?",
        (identity,),
        "revision_application_snapshot_missing",
    )
    snapshot = ContentSnapshot(kind=row["kind"], payload_json=row["payload_json"])
    if snapshot.kind != kind or snapshot.snapshot_id != identity:
        raise DocumentWorkRowError("revision_application_snapshot_mismatch")
    return snapshot


def validate_revision_application_work(
    connection: sqlite3.Connection, work: AmendmentRevisionApplicationWork
) -> None:
    row = _one(
        connection,
        "SELECT * FROM computation_results WHERE result_id=?",
        (work.result_id,),
        "revision_application_result_missing",
    )
    result = ComputationResult.model_validate_json(row["result_json"])
    if (
        result.result_id,
        result.provider,
        result.batch_id,
        result.custom_id,
        result.term,
        result.number,
    ) != tuple(
        row[name] for name in ("result_id", "provider", "batch_id", "custom_id", "term", "number")
    ) or (
        result.schema_version != 2
        or result.purpose != "amendments"
        or result.validation != "validated"
        or not isinstance(result.record, AmendmentsRecord)
        or (result.term, result.number, result.custom_id, result.request_sha256)
        != (work.term, work.number, work.request_key, work.request_sha256)
        or result.record.source_url != work.source_url
        or result.input_binding is not None
        and (result.input_binding.term, result.input_binding.number, result.input_binding.reuse_key)
        != (work.term, work.number, work.request_key)
    ):
        raise DocumentWorkRowError("revision_application_result_mismatch")
    original = _one(
        connection,
        "SELECT * FROM publications WHERE id=?",
        (work.original_publication_id,),
        "revision_application_original_missing",
    )
    if (original["term"], original["number"], original["channel_id"]) != (
        work.term,
        work.number,
        work.channel_id,
    ):
        raise DocumentWorkRowError("revision_application_original_owner_mismatch")
    if original["kind"] != "status_update":
        raise DocumentWorkRowError("revision_application_original_owner_mismatch")
    binding = next(
        _rows(
            connection,
            "SELECT content_id FROM publication_content WHERE publication_id=?",
            (work.original_publication_id,),
        ),
        None,
    )
    if binding is not None:
        version_row = _one(
            connection,
            "SELECT * FROM content_versions WHERE content_id=?",
            (binding["content_id"],),
            "revision_application_content_missing",
        )
        version = ContentVersion.model_validate_json(version_row["version_json"])
        if (
            version.content_id != binding["content_id"]
            or (
                version.root_publication_id,
                version.previous_version_id,
                version.bill_snapshot_id,
                version.change_snapshot_id,
                version.plan_snapshot_id,
            )
            != tuple(
                version_row[name]
                for name in (
                    "root_publication_id",
                    "previous_version_id",
                    "bill_snapshot_id",
                    "change_snapshot_id",
                    "plan_snapshot_id",
                )
            )
            or version.root_publication_id != work.original_publication_id
            or version.bill_snapshot_id != work.bill_snapshot_id
            or version.change_snapshot_id != work.change_snapshot_id
        ):
            raise DocumentWorkRowError("revision_application_content_mismatch")
    elif original["delivery_json"] is not None:
        saved = DeliveryPlan.model_validate_json(original["delivery_json"])
        if (
            editorial_bill_snapshot(saved.bill_json).snapshot_id != work.bill_snapshot_id
            or ContentSnapshot(kind="change", payload_json=saved.change_json).snapshot_id
            != work.change_snapshot_id
        ):
            raise DocumentWorkRowError("revision_application_content_mismatch")
    bill = Bill.model_validate_json(
        _snapshot(connection, work.bill_snapshot_id, "bill").payload_json
    )
    change = StatusChange.model_validate_json(
        _snapshot(connection, work.change_snapshot_id, "change").payload_json
    )
    plan = DeliveryPlan.model_validate_json(
        _snapshot(connection, work.plan_snapshot_id, "plan").payload_json
    )
    if (
        (bill.term, bill.number) != (work.term, work.number)
        or (change.term, change.number) != (work.term, work.number)
        or change.id is not None
        and change.id != original["status_change_id"]
        or plan.revision_of != work.original_publication_id
        or plan.edit_message_id != work.message_id
    ):
        raise DocumentWorkRowError("revision_application_snapshot_owner_mismatch")
    if work.revision_publication_id is not None:
        revision = _one(
            connection,
            "SELECT * FROM publications WHERE id=?",
            (work.revision_publication_id,),
            "revision_application_publication_missing",
        )
        if (
            revision["term"],
            revision["number"],
            revision["channel_id"],
            revision["kind"],
            revision["ref"],
        ) != (
            work.term,
            work.number,
            work.channel_id,
            "amendments_revision",
            f"amendments/{work.original_publication_id}/{work.request_key}",
        ):
            raise DocumentWorkRowError("revision_application_publication_mismatch")
        if revision["delivery_json"] is None:
            raise DocumentWorkRowError("revision_application_publication_mismatch")
        delivered = DeliveryPlan.model_validate_json(revision["delivery_json"])
        if (
            delivered.revision_of != work.original_publication_id
            or delivered.edit_message_id != work.message_id
        ):
            raise DocumentWorkRowError("revision_application_publication_mismatch")
        receipt = _one(
            connection,
            "SELECT application_json FROM result_applications WHERE result_id=? AND term=?"
            " AND number=? AND phase='projection' AND generation IS NULL"
            " AND json_extract(application_json, '$.target.kind')='publication'"
            " AND json_extract(application_json, '$.target.ref')=?",
            (work.result_id, work.term, work.number, str(work.revision_publication_id)),
            "revision_application_receipt_missing",
        )
        application = ResultApplication.model_validate_json(receipt["application_json"])
        if (application.result_id, application.term, application.number, application.target) != (
            work.result_id,
            work.term,
            work.number,
            ResultApplicationTarget(kind="publication", ref=str(work.revision_publication_id)),
        ):
            raise DocumentWorkRowError("revision_application_receipt_mismatch")


def read_document_work(
    connection: sqlite3.Connection, row: sqlite3.Row
) -> DocumentWork | AmendmentRevisionApplicationWork:
    try:
        work = decode_document_work(row["work_json"])
    except ValueError as exc:
        raise DocumentWorkRowError("invalid_document_work") from exc
    if (work.key, work.term, work.number, work.state) != tuple(
        row[name] for name in ("key", "term", "number", "state")
    ):
        raise DocumentWorkRowError("document_owner_mismatch")
    try:
        scheduled = datetime.fromisoformat(row["next_at"])
    except ValueError as exc:
        raise DocumentWorkRowError("invalid_document_schedule") from exc
    if scheduled != work.next_at:
        raise DocumentWorkRowError("document_schedule_mismatch")
    if isinstance(work, AmendmentRevisionApplicationWork):
        try:
            validate_revision_application_work(connection, work)
        except DocumentWorkRowError:
            raise
        except ValueError as exc:
            raise DocumentWorkRowError("invalid_revision_application_parent") from exc
    return work


def validate_document_work(connection: sqlite3.Connection) -> None:
    for row in _rows(connection, "SELECT * FROM document_work ORDER BY key"):
        read_document_work(connection, row)
