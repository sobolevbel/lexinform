"""Read known card-history rows without turning missing physical evidence into confirmation."""

import sqlite3
from datetime import datetime
from typing import Literal

from lexinform.adapters.computation_history import read_result_application
from lexinform.adapters.content_applications import verify_content_applications
from lexinform.adapters.content_source_refs import ContentSourceRows
from lexinform.adapters.input_receipts import validate_primary_receipt_parents
from lexinform.content_versions import (
    card_confirmation_matches,
    card_physical_matches,
    card_plan_matches,
    confirmed_content_version,
    content_delivery_mismatch,
    content_snapshot_refs,
)
from lexinform.json_storage import read_json
from lexinform.models import (
    AnalysisRecord,
    BatchItemMeta,
    Bill,
    ContentSnapshot,
    ContentVersion,
    DeliveryPlan,
    PrimaryInputReceipt,
    Publication,
    PublicationKind,
    PublicationStatus,
)
from lexinform.models.content import CardPrimaryEvidence, card_bill_snapshot
from lexinform.models.primary_projections import PRIMARY_USAGE_FIELDS
from lexinform.primary_projections import primary_record_matches


def _query(
    connection: sqlite3.Connection, sql: str, parameters: tuple[object, ...] = ()
) -> sqlite3.Cursor:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    return cursor.execute(sql, parameters)


class ContentVersionRowError(ValueError):
    def __init__(
        self, code: Literal["invalid_content_version", "content_version_columns_mismatch"]
    ) -> None:
        self.code = code
        super().__init__(code)


def read_content_version(row: sqlite3.Row) -> ContentVersion:
    try:
        value = ContentVersion.model_validate(read_json(row["version_json"]))
    except (ValueError, TypeError, RecursionError) as exc:
        raise ContentVersionRowError("invalid_content_version") from exc
    columns = (
        "content_id",
        "root_publication_id",
        "previous_version_id",
        "bill_snapshot_id",
        "change_snapshot_id",
        "plan_snapshot_id",
    )
    if (
        value.content_id,
        value.root_publication_id,
        value.previous_version_id,
        value.bill_snapshot_id,
        value.change_snapshot_id,
        value.plan_snapshot_id,
    ) != tuple(row[k] for k in columns):
        raise ContentVersionRowError("content_version_columns_mismatch")
    return value


def read_content_publication(row: sqlite3.Row) -> Publication:
    return Publication.model_validate(
        dict(row)
        | {
            "document_message_ids": read_json(row["document_message_ids"] or "[]"),
            "delivery": DeliveryPlan.model_validate(read_json(row["delivery_json"]))
            if row["delivery_json"] is not None
            else None,
        }
    )


def _verify_card_primary_evidence(
    connection: sqlite3.Connection,
    reference: CardPrimaryEvidence,
    version: ContentVersion,
    bill: Bill,
    root: Publication | None,
) -> None:
    projection_row = _query(
        connection,
        "SELECT * FROM result_applications WHERE id=?",
        (reference.projection_application_id,),
    ).fetchone()
    if projection_row is None:
        raise ValueError("card_primary_projection_missing")
    projection = read_result_application(projection_row)
    if (
        (projection.term, projection.number) != (bill.term, bill.number)
        or projection.phase != "projection"
        or projection.target is not None
        or projection.reason != "batch_result"
        or projection.projected_generation != bill.analysis_generation
        or root is not None
        and (root.term, root.number) != (bill.term, bill.number)
    ):
        raise ValueError("card_primary_projection_scope")
    reader = ContentSourceRows(connection)
    paid = reader.get_computation_result(projection.result_id)
    if (
        paid is None
        or not isinstance(paid.record, AnalysisRecord)
        or paid.validation != "validated"
        or paid.purpose not in ("analysis", "reanalysis")
        or (paid.term, paid.number, paid.generation)
        != (projection.term, projection.number, projection.generation)
        or paid.request_sha256 is None
        or paid.input_binding is None
        or (paid.input_binding.term, paid.input_binding.number) != (paid.term, paid.number)
    ):
        raise ValueError("card_primary_paid_scope")
    if root is not None and root.delivery is not None:
        rendered_bill = Bill.model_validate(read_json(root.delivery.bill_json))
        record = rendered_bill.analysis
        if (
            record is None
            or not primary_record_matches(record, paid)
            or record.model_dump(exclude=set(PRIMARY_USAGE_FIELDS))
            != paid.record.model_dump(exclude=set(PRIMARY_USAGE_FIELDS))
        ):
            raise ValueError("card_primary_render_result_mismatch")
    item = reader.get_consumed_primary_input(
        paid.provider, paid.batch_id, paid.custom_id, bill.analysis_generation
    )
    if item is None or item.consumed_at is None or not isinstance(item.meta, BatchItemMeta):
        raise ValueError("card_primary_item_missing")
    meta = item.meta
    receipt = meta.prepared_input_receipt
    if (
        (item.term, item.number, item.call_kind) != (paid.term, paid.number, paid.purpose)
        or meta.input_binding != paid.input_binding
        or meta.generation != paid.generation
        or meta.queued_at is None
        or not isinstance(receipt, PrimaryInputReceipt)
        or receipt.source_selection_id is None
        or receipt.artifact_sha256 is None
        or receipt.extraction_id is None
        or not meta.source_url
        or receipt.request_sha256 != paid.request_sha256
        or meta.request_sha256 != paid.request_sha256
        or meta.input_chars != receipt.text_chars
        or paid.record.input_chars != receipt.text_chars
    ):
        raise ValueError("card_primary_item_scope")
    source = reader.get_source_observation(reference.source_observation_id)
    selection = reader.get_document_selection(receipt.source_selection_id)
    if (
        source is None
        or (source.term, source.number, source.purpose) != (paid.term, paid.number, "analysis")
        or source.selection_id != receipt.source_selection_id
        or (source.document.kind, source.document.url) != (meta.source_kind, meta.source_url)
        or selection is None
        or len(selection.selection.parts) != 1
        or selection.selection.parts[0].url != meta.source_url
        or selection.selection.parts != meta.selection.without_text().parts
    ):
        raise ValueError("card_primary_source_scope")
    validate_primary_receipt_parents(connection, receipt)
    collections = _query(
        connection,
        "SELECT * FROM result_applications WHERE result_id=? AND term=? AND number=?"
        " AND generation=? AND phase='collection' LIMIT 2",
        (paid.result_id, paid.term, paid.number, paid.generation),
    ).fetchall()
    if len(collections) != 1:
        raise ValueError("card_primary_collection_missing")
    collection = read_result_application(collections[0])
    if collection.target is not None or (collection.outcome, collection.reason) != (
        "ready",
        "current_generation",
    ):
        raise ValueError("card_primary_collection_scope")
    batch = _query(
        connection,
        "SELECT submitted_at FROM llm_batches WHERE batch_id=? AND provider=?",
        (paid.batch_id, paid.provider),
    ).fetchone()
    if batch is None or not batch["submitted_at"]:
        raise ValueError("card_primary_batch_missing")
    chronology = (
        source.recorded_at,
        meta.queued_at,
        datetime.fromisoformat(batch["submitted_at"]),
        paid.collected_at,
        collection.created_at,
        projection.created_at,
        *((root.created_at,) if root is not None else ()),
        version.recorded_at,
        *((root.sent_at,) if root is not None and root.sent_at is not None else ()),
    )
    if (
        any(value.utcoffset() is None for value in (*chronology, item.consumed_at))
        or chronology != tuple(sorted(chronology))
        or not paid.collected_at <= item.consumed_at <= projection.created_at
    ):
        raise ValueError("card_primary_chronology")


def _verify_card_content(
    connection: sqlite3.Connection, value: ContentVersion, *, retained: bool
) -> int | None:
    if value.change_snapshot_id is not None:
        return None
    root_row = _query(
        connection, "SELECT * FROM publications WHERE id=?", (value.root_publication_id,)
    ).fetchone()
    root = read_content_publication(root_row) if root_row is not None else None
    reader = ContentSourceRows(connection)
    snapshots: dict[str, ContentSnapshot] = {}
    for kind, identity in content_snapshot_refs(value):
        snapshot = reader.get_content_snapshot(identity)
        if snapshot is None or snapshot.kind != kind:
            raise ValueError("card_content_snapshot_missing")
        snapshots[identity] = snapshot
    context = snapshots[value.bill_snapshot_id]
    bill = Bill.model_validate(read_json(context.payload_json))
    if card_bill_snapshot(context.payload_json) != context:
        raise ValueError("card_content_context_profile")
    semantic = DeliveryPlan.model_validate(
        read_json(snapshots[value.plan_snapshot_id].payload_json)
    )
    if semantic.bill_json or semantic.rendered_text or not card_plan_matches(value, semantic):
        raise ValueError("card_content_plan_scope")
    if root is not None and (
        root.kind is not PublicationKind.NEW_BILL
        or root.status_change_id is not None
        or value.previous_version_id is not None
        and semantic.edit_message_id != root.message_id
    ):
        raise ValueError("card_content_root_scope")
    if value.card_primary_evidence is not None:
        if semantic.reply_to is not None or semantic.update_group or semantic.sitting_group:
            raise ValueError("card_primary_plan_scope")
        _verify_card_primary_evidence(connection, value.card_primary_evidence, value, bill, root)
    previous_ids: set[int] = set()
    if value.previous_version_id is not None:
        row = _query(
            connection,
            "SELECT * FROM content_versions WHERE content_id=?",
            (value.previous_version_id,),
        ).fetchone()
        if row is None:
            raise ValueError("card_content_predecessor_missing")
        previous = read_content_version(row)
        if (
            previous.root_publication_id != value.root_publication_id
            or previous.change_snapshot_id is not None
        ):
            raise ValueError("card_content_predecessor_scope")
        previous_ids = {
            r[0]
            for r in _query(
                connection,
                "SELECT publication_id FROM publication_content WHERE content_id=?",
                (value.previous_version_id,),
            )
        }
        if not retained:
            previous_id = verify_card_content(connection, previous, retained=True)
            previous_row = (
                _query(
                    connection, "SELECT * FROM publications WHERE id=?", (previous_id,)
                ).fetchone()
                if previous_id is not None
                else None
            )
            physical = read_content_publication(previous_row) if previous_row is not None else None
            if (
                root is None
                or root.status is not PublicationStatus.SENT
                or root.message_id is None
                or physical is None
                or physical.status is not PublicationStatus.SENT
                or physical.message_id != root.message_id
            ):
                raise ValueError("card_content_predecessor_unconfirmed")
    bindings = _query(
        connection,
        "SELECT publication_id FROM publication_content WHERE content_id=?",
        (value.content_id,),
    ).fetchall()
    missing = False
    for binding in bindings:
        identity = binding[0]
        if (identity == value.root_publication_id) != (value.previous_version_id is None):
            raise ValueError("card_content_binding_scope")
        physical_row = _query(
            connection, "SELECT * FROM publications WHERE id=?", (identity,)
        ).fetchone()
        if physical_row is None:
            missing = True
            continue
        physical = read_content_publication(physical_row)
        if not card_physical_matches(value, physical, root, semantic, previous_ids) or (
            physical.delivery is not None
            and content_delivery_mismatch(value, snapshots, physical.delivery) is not None
        ):
            raise ValueError("card_content_binding_scope")
    if root is None:
        if retained:
            return None
        raise ValueError("card_content_root_missing")
    assert root_row is not None, "the available typed root comes from its physical row"
    rows = (
        [root_row]
        if value.previous_version_id is None
        else card_publication_rows(connection, value.root_publication_id)
    )
    matches: list[int] = []
    for row in rows:
        physical = read_content_publication(row)
        if not card_physical_matches(value, physical, root, semantic, previous_ids):
            continue
        bound = _query(
            connection,
            "SELECT content_id FROM publication_content WHERE publication_id=?",
            (physical.id,),
        ).fetchone()
        if physical.delivery is None:
            if bound is None or bound[0] != value.content_id:
                continue
        elif content_delivery_mismatch(value, snapshots, physical.delivery) is not None:
            continue
        if physical.id is not None:
            matches.append(physical.id)
    if not matches and retained and value.previous_version_id is not None:
        return None
    if len(matches) != 1:
        raise ValueError("card_content_physical_scope")
    if retained and missing:
        return None
    return matches[0]


def validate_content_history(connection: sqlite3.Connection) -> None:
    try:
        tables = {
            row[0]
            for row in _query(connection, "SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not {"content_snapshots", "content_versions", "publication_content"}.intersection(
            tables
        ):
            return
        reader = ContentSourceRows(connection)
        for row in _query(connection, "SELECT snapshot_id FROM content_snapshots"):
            reader.get_content_snapshot(row["snapshot_id"])
        for row in _query(connection, "SELECT * FROM content_versions"):
            value = read_content_version(row)
            for kind, identity in content_snapshot_refs(value):
                snapshot = reader.get_content_snapshot(identity)
                if snapshot is None or snapshot.kind != kind:
                    raise ValueError("content_snapshot_unavailable")
            if value.previous_version_id is not None:
                previous_row = _query(
                    connection,
                    "SELECT * FROM content_versions WHERE content_id=?",
                    (value.previous_version_id,),
                ).fetchone()
                previous = read_content_version(previous_row) if previous_row is not None else None
                if previous is None or previous.root_publication_id != value.root_publication_id:
                    raise ValueError("content_predecessor_unavailable")
            verify_content_applications(connection, value)
            physical_identity = verify_card_content(connection, value, retained=True)
            if value.change_snapshot_id is None:
                bindings = _query(
                    connection,
                    "SELECT publication_id FROM publication_content WHERE content_id=?",
                    (value.content_id,),
                ).fetchall()
                if physical_identity is not None and any(
                    binding[0] != physical_identity for binding in bindings
                ):
                    raise ValueError("card_content_binding_mismatch")
        for table in ("content_snapshots", "content_versions", "publication_content"):
            if _query(connection, f"PRAGMA foreign_key_check({table})").fetchone() is not None:
                raise ValueError("content_history_foreign_key")
    except (ValueError, TypeError, IndexError, sqlite3.Error, RecursionError) as exc:
        raise ValueError("invalid_content_history") from exc


def card_publication_rows(connection: sqlite3.Connection, root_id: int) -> list[sqlite3.Row]:
    root = _query(connection, "SELECT * FROM publications WHERE id=?", (root_id,)).fetchone()
    if root is None:
        return []
    return list(
        _query(
            connection,
            """
        WITH RECURSIVE operations(id) AS (
          SELECT id FROM publications WHERE id=? OR (channel_id=? AND (
            ref LIKE ? OR ref LIKE ? OR CASE WHEN json_valid(delivery_json) THEN
              CAST(json_extract(delivery_json,'$.revision_of') AS INTEGER)=? OR
              CAST(json_extract(delivery_json,'$.edit_message_id') AS INTEGER)=? END))
          UNION
          SELECT p.id FROM publications p JOIN operations o ON p.channel_id=? AND
            CASE WHEN json_valid(p.delivery_json) THEN
              CAST(json_extract(p.delivery_json,'$.continuation_parent') AS INTEGER)=o.id OR
              CAST(json_extract(p.delivery_json,'$.continuation_root') AS INTEGER)=o.id END
        ) SELECT * FROM publications WHERE id IN (SELECT id FROM operations) ORDER BY id
    """,
            (
                root_id,
                root["channel_id"],
                f"card/{root_id}/%",
                f"message/{root_id}/%",
                root_id,
                root["message_id"],
                root["channel_id"],
            ),
        )
    )


def verify_card_content(
    connection: sqlite3.Connection, value: ContentVersion, *, retained: bool = False
) -> int | None:
    try:
        return _verify_card_content(connection, value, retained=retained)
    except RecursionError as exc:
        raise ValueError("invalid_card_content_history") from exc


def card_current_content(connection: sqlite3.Connection, root_id: int) -> str | None:
    from_root = _query(connection, "SELECT * FROM publications WHERE id=?", (root_id,)).fetchone()
    if from_root is None:
        return None
    root = read_content_publication(from_root)
    for row in _query(
        connection, "SELECT * FROM content_versions WHERE root_publication_id=?", (root_id,)
    ):
        value = read_content_version(row)
        if (
            value.change_snapshot_id is None
            and verify_card_content(connection, value, retained=True) is None
        ):
            return None
    bound: dict[int, ContentVersion] = {}
    for row in _query(
        connection,
        "SELECT b.publication_id, v.* FROM publication_content b "
        "JOIN content_versions v ON v.content_id=b.content_id WHERE v.root_publication_id=?",
        (root_id,),
    ):
        bound[row["publication_id"]] = read_content_version(row)
    inventory = [
        read_content_publication(row) for row in card_publication_rows(connection, root_id)
    ]
    if any(item.id not in bound for item in inventory):
        return None
    confirmed = {
        bound[item.id].content_id
        for item in inventory
        if item.id is not None
        and item.status is PublicationStatus.SENT
        and item.message_id == root.message_id
    }
    confirmation = confirmed_content_version(
        {value.content_id: value.previous_version_id for value in bound.values()}, confirmed
    )
    latest = max(inventory, key=lambda p: p.id or 0, default=None)
    aliases = _query(
        connection,
        "SELECT id FROM publications WHERE kind='new_bill' AND status='sent' "
        "AND channel_id=? AND message_id=?",
        (root.channel_id, root.message_id),
    )
    return (
        confirmation.current_id
        if card_confirmation_matches(
            root,
            [latest] if latest else [],
            bound,
            confirmation.current_id,
            alias_count=len(aliases.fetchall()),
        )
        else None
    )
