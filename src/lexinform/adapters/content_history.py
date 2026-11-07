"""Read known card-history rows without turning missing physical evidence into confirmation."""

import hashlib
import sqlite3
from datetime import datetime
from typing import Literal

from lexinform.adapters.computation_history import read_result_application
from lexinform.adapters.content_applications import verify_content_applications
from lexinform.adapters.content_source_refs import ContentSourceRows
from lexinform.adapters.digest_content import verify_digest_content
from lexinform.adapters.input_receipts import (
    validate_multipart_item,
    validate_primary_receipt_parents,
)
from lexinform.adapters.status_changes import read_status_change
from lexinform.content_versions import (
    card_confirmation_matches,
    card_physical_matches,
    card_plan_matches,
    confirmed_content_version,
    content_delivery_mismatch,
    content_snapshot_refs,
    direct_stage_plan_matches,
    grouped_stage_context,
    grouped_stage_revision_plan,
    grouped_stage_revision_ref,
)
from lexinform.errors import CardPrimaryEvidenceError
from lexinform.grouped_update_preview import preview_grouped_update
from lexinform.json_storage import read_json
from lexinform.models import (
    AgendaItem,
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
    StatusChange,
)
from lexinform.models.commands import (
    GroupedEditDeliveryResolution,
    IncomingCommand,
    read_delivery_resolution,
)
from lexinform.models.content import (
    MAX_DIRECT_STAGE_CONTENT_BYTES,
    MAX_GROUPED_STAGE_CONTENT_BYTES,
    CardPrimaryEvidence,
    card_bill_snapshot,
)
from lexinform.models.events import amendments_stage
from lexinform.models.input_receipts import (
    LooseRclInputReceipt,
    RclArchiveInputReceipt,
    SejmSelfAmendmentInputReceipt,
)
from lexinform.models.primary_projections import PRIMARY_USAGE_FIELDS
from lexinform.models.sitting_content import (
    MAX_SITTING_HISTORY_BYTES,
    SittingContentPlan,
    sitting_content_context,
)
from lexinform.primary_projections import primary_record_matches
from lexinform.sitting_delivery import sitting_root_plan


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


def card_primary_evidence_candidate(
    connection: sqlite3.Connection, bill: Bill
) -> CardPrimaryEvidence | None:
    record = bill.analysis
    if record is None or record.computation_id is None or record.request_sha256 is None:
        return None
    rows = _query(
        connection,
        """
        WITH item AS (
            SELECT r.result_id,r.term,r.number,
              CASE WHEN json_valid(r.result_json)
                THEN json_extract(r.result_json,'$.generation') END AS generation,
              CASE WHEN json_valid(i.meta_json) THEN i.meta_json ELSE '{}' END AS meta
            FROM computation_results r JOIN llm_batch_items i
              ON i.batch_id=r.batch_id AND i.custom_id=r.custom_id
            WHERE r.result_id=? AND r.term=? AND r.number=? AND i.consumed_at IS NOT NULL
        )
        SELECT a.id,o.observation_id
        FROM item JOIN result_applications a INDEXED BY ux_result_application_generation
          ON a.result_id=item.result_id AND a.term=item.term AND a.number=item.number
          AND a.generation=item.generation
        JOIN source_observations o INDEXED BY ix_source_observations_selection
          ON o.term=item.term AND o.number=item.number
          AND o.selection_id=json_extract(item.meta,'$.prepared_input_receipt.source_selection_id')
        WHERE a.phase='projection' AND a.generation IS NOT NULL
          AND json_extract(a.application_json,'$.projected_generation')=?
          AND json_extract(a.application_json,'$.reason')='batch_result'
          AND json_extract(item.meta,'$.request_sha256')=?
          AND (
            json_extract(item.meta,'$.prepared_input_receipt.profile')='primary-single-part-text-v1'
            OR (
              json_extract(item.meta,'$.prepared_input_receipt.profile') IN (
                'primary-sejm-self-amendment-text-v1',
                'primary-loose-rcl-annex-text-v1',
                'primary-loose-rcl-mixed-text-v1',
                'primary-rcl-archive-package-text-v1'
              )
              AND o.observation_id=json_extract(
                item.meta,'$.prepared_input_receipt.source_observation_id'
              )
            )
          )
          AND o.purpose='analysis' AND o.source_kind=json_extract(item.meta,'$.source_kind')
          AND o.source_url=json_extract(item.meta,'$.source_url')
        LIMIT 2
        """,
        (
            record.computation_id,
            bill.term,
            bill.number,
            bill.analysis_generation,
            record.request_sha256,
        ),
    ).fetchall()
    if len(rows) != 1:
        return None
    try:
        return CardPrimaryEvidence(
            projection_application_id=rows[0]["id"], source_observation_id=rows[0]["observation_id"]
        )
    except ValueError:
        return None


def _verify_card_primary_evidence(
    connection: sqlite3.Connection,
    reference: CardPrimaryEvidence,
    version: ContentVersion,
    bill: Bill,
    root: Publication | None,
) -> dict[str, object]:
    projection_row = _query(
        connection,
        "SELECT * FROM result_applications WHERE id=?",
        (reference.projection_application_id,),
    ).fetchone()
    if projection_row is None:
        raise CardPrimaryEvidenceError("card_primary_projection_missing")
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
        raise CardPrimaryEvidenceError("card_primary_projection_scope")
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
        raise CardPrimaryEvidenceError("card_primary_paid_scope")
    if root is not None and root.delivery is not None:
        rendered_bill = Bill.model_validate(read_json(root.delivery.bill_json))
        record = rendered_bill.analysis
        if (
            record is None
            or not primary_record_matches(record, paid)
            or record.model_dump(exclude=set(PRIMARY_USAGE_FIELDS))
            != paid.record.model_dump(exclude=set(PRIMARY_USAGE_FIELDS))
        ):
            raise CardPrimaryEvidenceError("card_primary_render_result_mismatch")
    item = reader.get_consumed_primary_input(
        paid.provider, paid.batch_id, paid.custom_id, bill.analysis_generation
    )
    if item is None or item.consumed_at is None or not isinstance(item.meta, BatchItemMeta):
        raise CardPrimaryEvidenceError("card_primary_item_missing")
    meta = item.meta
    receipt = meta.prepared_input_receipt
    if (
        (item.term, item.number, item.call_kind) != (paid.term, paid.number, paid.purpose)
        or meta.input_binding != paid.input_binding
        or meta.generation != paid.generation
        or meta.queued_at is None
        or not isinstance(
            receipt,
            (
                PrimaryInputReceipt,
                LooseRclInputReceipt,
                RclArchiveInputReceipt,
                SejmSelfAmendmentInputReceipt,
            ),
        )
        or receipt.source_selection_id is None
        or not meta.source_url
        or receipt.request_sha256 != paid.request_sha256
        or meta.request_sha256 != paid.request_sha256
        or meta.input_chars != receipt.text_chars
        or paid.record.input_chars != receipt.text_chars
    ):
        raise CardPrimaryEvidenceError("card_primary_item_scope")
    source = reader.get_source_observation(reference.source_observation_id)
    selection = reader.get_document_selection(receipt.source_selection_id)
    if (
        source is None
        or (source.term, source.number, source.purpose) != (paid.term, paid.number, "analysis")
        or source.selection_id != receipt.source_selection_id
        or (source.document.kind, source.document.url) != (meta.source_kind, meta.source_url)
        or selection is None
        or not selection.selection.parts
        or isinstance(receipt, PrimaryInputReceipt)
        and len(selection.selection.parts) != 1
        or selection.selection.parts[0].url != meta.source_url
        or selection.selection.parts != meta.selection.without_text().parts
    ):
        raise CardPrimaryEvidenceError("card_primary_source_scope")
    physical: dict[str, object]
    if isinstance(receipt, PrimaryInputReceipt):
        if receipt.artifact_sha256 is None or receipt.extraction_id is None:
            raise CardPrimaryEvidenceError("card_primary_item_scope")
        validate_primary_receipt_parents(connection, receipt)
        physical = {
            "artifact_sha256": receipt.artifact_sha256,
            "extraction_id": receipt.extraction_id,
        }
    else:
        if receipt.source_observation_id != reference.source_observation_id:
            raise CardPrimaryEvidenceError("card_primary_source_scope")
        validate_multipart_item(connection, item)
        if isinstance(receipt, RclArchiveInputReceipt):
            physical = {
                "artifact_sha256": selection.selection.parts[0].source_sha256,
                "extraction_id": receipt.extraction_id,
                "source_parts": [
                    {"role": recorded.role, "physical_index": recorded.physical_index}
                    for recorded in receipt.source_parts
                ],
            }
        else:
            physical = {
                "source_parts": [
                    {
                        "role": part.role,
                        "artifact_sha256": part.source_sha256,
                        "extraction_id": recorded.extraction_id,
                    }
                    for part, recorded in zip(
                        selection.selection.parts, receipt.source_parts, strict=True
                    )
                ],
            }
            if isinstance(receipt, SejmSelfAmendmentInputReceipt):
                physical["proposal_trace_id"] = receipt.proposal_trace_id
    collections = _query(
        connection,
        "SELECT * FROM result_applications WHERE result_id=? AND term=? AND number=?"
        " AND generation=? AND phase='collection' LIMIT 2",
        (paid.result_id, paid.term, paid.number, paid.generation),
    ).fetchall()
    if len(collections) != 1:
        raise CardPrimaryEvidenceError("card_primary_collection_missing")
    collection = read_result_application(collections[0])
    if collection.target is not None or (collection.outcome, collection.reason) != (
        "ready",
        "current_generation",
    ):
        raise CardPrimaryEvidenceError("card_primary_collection_scope")
    batch = _query(
        connection,
        "SELECT submitted_at FROM llm_batches WHERE batch_id=? AND provider=?",
        (paid.batch_id, paid.provider),
    ).fetchone()
    if batch is None or not batch["submitted_at"]:
        raise CardPrimaryEvidenceError("card_primary_batch_missing")
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
        raise CardPrimaryEvidenceError("card_primary_chronology")
    return {
        "source_kind": source.document.kind,
        "source_observation_id": source.observation_id,
        "source_selection_id": receipt.source_selection_id,
        **physical,
        "purpose": paid.purpose,
        "result_id": paid.result_id,
        "request_sha256": paid.request_sha256,
        "collection_application_id": collections[0]["id"],
        "projection_application_id": reference.projection_application_id,
    }


def _verify_card_content(
    connection: sqlite3.Connection, value: ContentVersion, *, retained: bool
) -> tuple[int | None, dict[str, object] | None]:
    if (
        value.change_snapshot_id is not None
        or value.digest_profile is not None
        or value.sitting_profile is not None
    ):
        return None, None
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
    context = snapshots[value.bill_context_id]
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
    primary = None
    if value.card_primary_evidence is not None:
        if semantic.reply_to is not None or semantic.update_group or semantic.sitting_group:
            raise CardPrimaryEvidenceError("card_primary_plan_scope")
        try:
            primary = _verify_card_primary_evidence(
                connection, value.card_primary_evidence, value, bill, root
            )
        except CardPrimaryEvidenceError:
            raise
        except ValueError as exc:
            raise CardPrimaryEvidenceError("invalid_card_primary_evidence") from exc
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
            return None, primary
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
        return None, primary
    if len(matches) != 1:
        raise ValueError("card_content_physical_scope")
    if retained and missing:
        return None, primary
    return matches[0], primary


def verify_direct_stage_content(connection: sqlite3.Connection, version: ContentVersion) -> None:
    evidence = version.direct_stage_evidence
    if evidence is None:
        return
    root_row = _query(
        connection, "SELECT * FROM publications WHERE id=?", (version.root_publication_id,)
    ).fetchone()
    anchor_row = _query(
        connection, "SELECT * FROM publications WHERE id=?", (evidence.anchor_publication_id,)
    ).fetchone()
    event = _query(
        connection, "SELECT * FROM status_changes WHERE id=?", (version.status_change_id,)
    ).fetchone()
    if root_row is None or anchor_row is None or event is None:
        raise ValueError("direct_stage_parent_missing")
    root, anchor = read_content_publication(root_row), read_content_publication(anchor_row)
    if (
        root.kind is not PublicationKind.STATUS_UPDATE
        or root.status_change_id != version.status_change_id
        or anchor.kind is not PublicationKind.NEW_BILL
        or anchor.status is not PublicationStatus.SENT
        or anchor.message_id is None
        or anchor.message_id <= 0
        or anchor.sent_at is None
        or anchor.sent_at > root.created_at
        or (anchor.term, anchor.number, anchor.channel_id)
        != (root.term, root.number, root.channel_id)
        or _query(
            connection,
            "SELECT 1 FROM digest_event_links WHERE source_publication_id=?",
            (root.id,),
        ).fetchone()
        is not None
    ):
        raise ValueError("direct_stage_publication_scope")
    bindings = _query(
        connection,
        "SELECT publication_id FROM publication_content WHERE content_id=? LIMIT 2",
        (version.content_id,),
    ).fetchall()
    if bindings and [binding[0] for binding in bindings] != [root.id]:
        raise ValueError("direct_stage_binding_scope")
    reader = ContentSourceRows(connection)
    snapshots: dict[str, ContentSnapshot] = {}
    for kind, identity in content_snapshot_refs(version):
        snapshot = reader.get_content_snapshot(identity)
        if snapshot is None or snapshot.kind != kind:
            raise ValueError("direct_stage_snapshot_missing")
        snapshots[identity] = snapshot
    if (
        sum(len(s.payload_json.encode()) for s in snapshots.values())
        + len((version.rendered_text or "").encode())
        > MAX_DIRECT_STAGE_CONTENT_BYTES
    ):
        raise ValueError("direct_stage_content_bound")
    bill = snapshots[version.bill_context_id]
    owner = Bill.model_validate_json(bill.payload_json)
    if card_bill_snapshot(bill.payload_json) != bill or (owner.term, owner.number) != (
        root.term,
        root.number,
    ):
        raise ValueError("direct_stage_bill_scope")
    if version.change_snapshot_id is None:
        raise ValueError("direct_stage_snapshot_missing")
    change = StatusChange.model_validate_json(snapshots[version.change_snapshot_id].payload_json)
    # Amendments and supplements can be appended after the original status text is frozen.
    current = read_status_change(event).model_copy(update={"amendments": None, "supplements": []})
    if (
        change != current
        or (change.term, change.number) != (root.term, root.number)
        or len(change.new_stages) != 1
        or amendments_stage(change.new_stages) is not None
    ):
        raise ValueError("direct_stage_event_scope")
    plan = DeliveryPlan.model_validate_json(snapshots[version.plan_snapshot_id].payload_json)
    if plan.reply_to != anchor.message_id or not direct_stage_plan_matches(plan):
        raise ValueError("direct_stage_plan_scope")
    if root.delivery is not None:
        if content_delivery_mismatch(version, snapshots, root.delivery) is not None:
            raise ValueError("direct_stage_frozen_delivery_mismatch")
    elif (
        root.status is not PublicationStatus.SENT
        or root.message_id is None
        or root.message_id <= 0
        or root.sent_at is None
        or not bindings
    ):
        raise ValueError("direct_stage_expired_delivery_scope")


def verify_grouped_stage_content(
    connection: sqlite3.Connection, version: ContentVersion, *, require_bound: bool = False
) -> int | None:
    evidence = version.grouped_stage_evidence
    if evidence is None:
        return None
    if evidence.revision is not None:
        return _verify_grouped_stage_revision(connection, version, require_bound=require_bound)
    ref = f"updates/{version.root_publication_id}"
    selected = _query(
        connection,
        "SELECT * FROM publications WHERE ref=? AND kind='status_update' LIMIT 3",
        (ref,),
    ).fetchall()
    by_id = {row["id"]: read_content_publication(row) for row in selected}
    ids = tuple(member.publication_id for member in evidence.members)
    if set(by_id) != set(ids):
        raise ValueError("grouped_stage_membership")
    root, second = (by_id[identity] for identity in ids)
    if (
        root.channel_id != second.channel_id
        or (root.term, root.number) == (second.term, second.number)
        or (root.status, root.message_id, root.telegram_chat_id)
        != (second.status, second.message_id, second.telegram_chat_id)
        or root.document_message_ids
        or second.document_message_ids
    ):
        raise ValueError("grouped_stage_receipt")
    if root.status is PublicationStatus.SENT:
        if (
            root.message_id is None
            or root.message_id <= 0
            or root.sent_at is None
            or second.sent_at is None
        ):
            raise ValueError("grouped_stage_receipt")
    elif (
        root.status
        not in (
            PublicationStatus.QUEUED,
            PublicationStatus.PENDING,
            PublicationStatus.FAILED,
            PublicationStatus.UNKNOWN,
        )
        or root.message_id is not None
        or root.sent_at is not None
        or second.sent_at is not None
    ):
        raise ValueError("grouped_stage_receipt")
    bindings = _query(
        connection,
        "SELECT publication_id FROM publication_content WHERE content_id=? LIMIT 2",
        (version.content_id,),
    ).fetchall()
    if bindings and [binding[0] for binding in bindings] != [root.id]:
        raise ValueError("grouped_stage_binding_scope")
    reader = ContentSourceRows(connection)
    snapshots: dict[str, ContentSnapshot] = {}
    for kind, identity in content_snapshot_refs(version):
        snapshot = reader.get_content_snapshot(identity)
        if snapshot is None or snapshot.kind != kind:
            raise ValueError("grouped_stage_snapshot_missing")
        snapshots[identity] = snapshot
    if (
        sum(len(snapshot.payload_json.encode()) for snapshot in snapshots.values())
        + len((version.rendered_text or "").encode())
        > MAX_GROUPED_STAGE_CONTENT_BYTES
    ):
        raise ValueError("grouped_stage_content_bound")
    compact = DeliveryPlan.model_validate_json(snapshots[version.plan_snapshot_id].payload_json)
    if (
        compact != grouped_stage_context(compact)
        or compact.rendered_text
        or compact.reply_to != evidence.members[0].anchor_message_id
        or not direct_stage_plan_matches(compact)
    ):
        raise ValueError("grouped_stage_plan_scope")
    witness = evidence.render_witness
    if witness is not None and (
        witness.today != compact.today
        or tuple((owner.term, owner.number) for owner in witness.members)
        != ((root.term, root.number), (second.term, second.number))
    ):
        raise ValueError("grouped_stage_render_witness_scope")
    plans = (root.delivery, second.delivery)
    first_plan, second_plan = plans
    if None in plans and (root.status is not PublicationStatus.SENT or not bindings):
        raise ValueError("grouped_stage_expired_scope")
    shared = first_plan or second_plan
    if shared is not None and (
        shared.group_text != version.rendered_text
        or shared.today != compact.today
        or tuple(item.publication_id for item in shared.update_group) != ids
        or (
            first_plan is not None
            and content_delivery_mismatch(version, snapshots, first_plan) is not None
        )
        or (
            second_plan is not None
            and (
                second_plan.group_text != shared.group_text
                or second_plan.today != shared.today
                or second_plan.update_group != shared.update_group
            )
        )
    ):
        raise ValueError("grouped_stage_frozen_plan")
    for index, member in enumerate(evidence.members):
        publication = by_id[member.publication_id]
        anchor_row = _query(
            connection, "SELECT * FROM publications WHERE id=?", (member.anchor_publication_id,)
        ).fetchone()
        event = _query(
            connection, "SELECT * FROM status_changes WHERE id=?", (member.status_change_id,)
        ).fetchone()
        if anchor_row is None or event is None:
            raise ValueError("grouped_stage_parent_missing")
        anchor = read_content_publication(anchor_row)
        if (
            publication.status_change_id != member.status_change_id
            or anchor.kind is not PublicationKind.NEW_BILL
            or anchor.status is not PublicationStatus.SENT
            or anchor.message_id != member.anchor_message_id
            or anchor.sent_at is None
            or anchor.sent_at > publication.created_at
            or (anchor.term, anchor.number, anchor.channel_id)
            != (publication.term, publication.number, publication.channel_id)
            or _query(
                connection,
                "SELECT 1 FROM digest_event_links WHERE source_publication_id=?",
                (publication.id,),
            ).fetchone()
            is not None
        ):
            raise ValueError("grouped_stage_publication_scope")
        bill = snapshots[member.bill_snapshot_id]
        owner = Bill.model_validate_json(bill.payload_json)
        change = StatusChange.model_validate_json(snapshots[member.change_snapshot_id].payload_json)
        current = read_status_change(event).model_copy(
            update={"amendments": None, "supplements": []}
        )
        if (
            card_bill_snapshot(bill.payload_json) != bill
            or (owner.term, owner.number) != (publication.term, publication.number)
            or (change.term, change.number, change.id)
            != (publication.term, publication.number, member.status_change_id)
            or change != current
            or not change.new_stages
            or amendments_stage(change.new_stages) is not None
        ):
            raise ValueError("grouped_stage_event_scope")
        if shared is not None:
            saved = shared.update_group[index]
            if (
                saved.card_message_id != member.anchor_message_id
                or hashlib.sha256(saved.bill_json.encode()).hexdigest()
                != member.original_bill_sha256
                or card_bill_snapshot(saved.bill_json) != bill
                or saved.change_json != snapshots[member.change_snapshot_id].payload_json
            ):
                raise ValueError("grouped_stage_frozen_member")
            plan = plans[index]
            if plan is not None and (
                not direct_stage_plan_matches(grouped_stage_context(plan))
                or plan.rendered_text
                or (plan.bill_json, plan.change_json, plan.reply_to)
                != (saved.bill_json, saved.change_json, member.anchor_message_id)
            ):
                raise ValueError("grouped_stage_frozen_member")

    return root.id


def read_grouped_stage_telegram_target(
    connection: sqlite3.Connection, version: ContentVersion
) -> tuple[int, int] | None:
    evidence = version.grouped_stage_evidence
    if evidence is None or evidence.revision is not None:
        return None
    root_id = verify_grouped_stage_content(connection, version, require_bound=True)
    row = _query(
        connection,
        "SELECT p.* FROM publications p JOIN publication_content c ON c.publication_id=p.id"
        " WHERE p.id=? AND c.content_id=?",
        (root_id, version.content_id),
    ).fetchone()
    if row is None:
        return None
    root = read_content_publication(row)
    if root.telegram_chat_id is None or root.message_id is None:
        return None
    return root.telegram_chat_id, root.message_id


def _verify_grouped_stage_revision(
    connection: sqlite3.Connection, version: ContentVersion, *, require_bound: bool
) -> int | None:
    evidence = version.grouped_stage_evidence
    if evidence is None or evidence.revision is None or evidence.render_witness is None:
        raise ValueError("grouped_revision_profile")
    original_row = _query(
        connection,
        "SELECT v.* FROM publication_content p JOIN content_versions v ON p.content_id=v.content_id"
        " WHERE p.publication_id=?",
        (version.root_publication_id,),
    ).fetchone()
    parent_row = _query(
        connection,
        "SELECT * FROM content_versions WHERE content_id=?",
        (version.previous_version_id,),
    ).fetchone()
    root_row = _query(
        connection, "SELECT * FROM publications WHERE id=?", (version.root_publication_id,)
    ).fetchone()
    if original_row is None or parent_row is None or root_row is None:
        raise ValueError("grouped_revision_missing_parent")
    original, previous = read_content_version(original_row), read_content_version(parent_row)
    root = read_content_publication(root_row)
    if (
        original.reason != "original"
        or original.root_publication_id != version.root_publication_id
        or original.grouped_stage_evidence is None
        or original.grouped_stage_evidence.members != evidence.members
        or root.status is not PublicationStatus.SENT
        or root.message_id != evidence.revision.target_message_id
        or previous.root_publication_id != version.root_publication_id
        or previous.grouped_stage_evidence is None
        or previous.grouped_stage_evidence.members != evidence.members
        or previous.grouped_stage_evidence.render_witness is None
        or previous.rendered_text is None
        or version.rendered_text is None
        or previous.language is None
        or version.language is None
        or version.language != previous.language
        or version.formatter_version != previous.formatter_version
        or content_snapshot_refs(version) != content_snapshot_refs(original)
    ):
        raise ValueError("grouped_revision_parent_scope")
    verify_grouped_stage_content(connection, original)
    reader = ContentSourceRows(connection)
    snapshots = {
        identity: reader.get_content_snapshot(identity)
        for _, identity in content_snapshot_refs(version)
    }
    bill = snapshots[version.bill_context_id]
    if bill is None or (
        sum(
            len(snapshot.payload_json.encode())
            for snapshot in snapshots.values()
            if snapshot is not None
        )
        + len(version.rendered_text.encode())
        > MAX_GROUPED_STAGE_CONTENT_BYTES
    ):
        raise ValueError("grouped_revision_content_bound")
    if previous.reason == "original":
        if previous.content_id != original.content_id:
            raise ValueError("grouped_revision_stale_parent")
    else:
        prior_slot = _grouped_revision_slot(connection, previous, root, bill, require_bound=True)
        if prior_slot.status is not PublicationStatus.SENT:
            raise ValueError("grouped_revision_stale_parent")
    baseline = previous.grouped_stage_evidence.render_witness.bind(
        previous.rendered_text, previous.language
    )
    candidate = evidence.render_witness.bind(version.rendered_text, version.language)
    index = (
        0 if evidence.revision.member_publication_id == evidence.members[0].publication_id else 1
    )
    if (
        preview_grouped_update(
            baseline, candidate, owner=baseline.members[index], expected_sha256=baseline.sha256
        )
        != version.rendered_text
    ):
        raise ValueError("grouped_revision_member_patch")
    return _grouped_revision_slot(connection, version, root, bill, require_bound=require_bound).id


def _grouped_revision_slot(
    connection: sqlite3.Connection,
    version: ContentVersion,
    root: Publication,
    bill: ContentSnapshot,
    *,
    require_bound: bool,
) -> Publication:
    ref = grouped_stage_revision_ref(version)
    plan = grouped_stage_revision_plan(version, bill)
    row = _query(
        connection,
        "SELECT * FROM publications WHERE channel_id=? AND kind='message_part' AND ref=?",
        (root.channel_id, ref),
    ).fetchone()
    if ref is None or plan is None or row is None:
        raise ValueError("grouped_revision_slot_missing")
    slot = read_content_publication(row)
    if (
        (slot.term, slot.number) != (root.term, root.number)
        or slot.status_change_id is not None
        or slot.document_message_ids
        or plan.edit_message_id != root.message_id
    ):
        raise ValueError("grouped_revision_slot_owner")
    bindings = _query(
        connection,
        "SELECT publication_id FROM publication_content WHERE content_id=? LIMIT 2",
        (version.content_id,),
    ).fetchall()
    if (bindings or require_bound) and [binding[0] for binding in bindings] != [slot.id]:
        raise ValueError("grouped_revision_slot_binding")
    if slot.delivery is None:
        if not bindings or slot.status is not PublicationStatus.SENT:
            raise ValueError("grouped_revision_expired_slot")
    elif slot.delivery != plan:
        raise ValueError("grouped_revision_frozen_slot")
    if slot.status is PublicationStatus.SENT:
        if slot.message_id != root.message_id or slot.sent_at is None:
            raise ValueError("grouped_revision_receipt")
    elif (
        slot.status
        not in (
            PublicationStatus.QUEUED,
            PublicationStatus.FAILED,
            PublicationStatus.PENDING,
            PublicationStatus.UNKNOWN,
            PublicationStatus.DISMISSED,
        )
        or slot.message_id is not None
        or slot.sent_at is not None
    ):
        raise ValueError("grouped_revision_receipt")
    _verify_grouped_resolution_audit(connection, slot, version, root, bound=bool(bindings))
    return slot


def _verify_grouped_resolution_audit(
    connection: sqlite3.Connection,
    slot: Publication,
    version: ContentVersion,
    root: Publication,
    *,
    bound: bool,
) -> None:
    rows = _query(
        connection,
        "SELECT command_update_id, resolution_json FROM delivery_resolutions"
        " WHERE publication_id=? LIMIT 2",
        (slot.id,),
    ).fetchall()
    if not rows and slot.status is not PublicationStatus.DISMISSED:
        return
    if len(rows) != 1 or not bound:
        raise ValueError("grouped_resolution_binding")
    row = rows[0]
    audit = read_delivery_resolution(row["resolution_json"])
    if not isinstance(audit, GroupedEditDeliveryResolution):
        raise ValueError("grouped_resolution_profile")
    if (
        (audit.publication_id, audit.term, audit.number, audit.kind, audit.channel_id)
        != (slot.id, slot.term, slot.number, slot.kind, slot.channel_id)
        or audit.content_id != version.content_id
        or audit.base_content_id != version.previous_version_id
        or version.rendered_text is None
        or audit.rendered_sha256 != hashlib.sha256(version.rendered_text.encode()).hexdigest()
        or (audit.telegram_chat_id, audit.target_message_id)
        != (root.telegram_chat_id, root.message_id)
        or audit.status is not slot.status
        or (audit.action == "confirm" and slot.sent_at != audit.resolved_at)
        or (audit.observed_at or audit.resolved_at) < slot.created_at
    ):
        raise ValueError("grouped_resolution_attestation")
    if row["command_update_id"] != audit.command.update_id or not recorded_command_matches(
        connection, audit.command
    ):
        raise ValueError("grouped_resolution_command")


def recorded_command_matches(connection: sqlite3.Connection, command: IncomingCommand) -> bool:
    row = _query(
        connection,
        "SELECT chat_id, message_id, text, received_at FROM commands WHERE update_id=?",
        (command.update_id,),
    ).fetchone()
    return row is not None and tuple(row) == (
        command.chat_id,
        command.message_id,
        command.text,
        command.received_at.isoformat(),
    )


def verify_sitting_content(
    connection: sqlite3.Connection, version: ContentVersion, *, require_bound: bool = False
) -> SittingContentPlan | None:
    if version.sitting_profile is None:
        return None
    reader = ContentSourceRows(connection)
    snapshot = reader.get_content_snapshot(version.plan_snapshot_id)
    if (
        snapshot is None
        or snapshot.kind != "plan"
        or len(snapshot.payload_json.encode()) > MAX_SITTING_HISTORY_BYTES
    ):
        raise ValueError("sitting_history_plan_unavailable")
    plan = SittingContentPlan.model_validate(read_json(snapshot.payload_json))
    if plan.parts[0].id != version.root_publication_id:
        raise ValueError("sitting_history_root_mismatch")
    if require_bound:
        bindings = _query(
            connection,
            "SELECT publication_id FROM publication_content WHERE content_id=?",
            (version.content_id,),
        ).fetchall()
        if {binding[0] for binding in bindings} != set(plan.bindings):
            raise ValueError("sitting_history_binding_mismatch")
    publications = {}
    for identity in plan.bindings:
        row = _query(connection, "SELECT * FROM publications WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise ValueError("sitting_history_publication_missing")
        publication = read_content_publication(row)
        if publication.channel_id != plan.channel_id:
            raise ValueError("sitting_history_channel_mismatch")
        if publication.status is PublicationStatus.SENT and (
            publication.message_id is None or publication.message_id <= 0
        ):
            raise ValueError("sitting_history_receipt_missing")
        if publication.delivery is None and publication.status is not PublicationStatus.SENT:
            raise ValueError("sitting_history_active_plan_missing")
        publications[identity] = publication
    if publications[version.root_publication_id].created_at != version.recorded_at:
        raise ValueError("sitting_history_recorded_at_mismatch")
    bill_sizes: dict[str, int] = {}
    for member in plan.members:
        publication = publications[member.id]
        if (publication.term, publication.number, publication.kind.value, publication.ref) != (
            member.term,
            member.number,
            member.kind,
            member.ref,
        ):
            raise ValueError("sitting_history_member_mismatch")
        bill = reader.get_content_snapshot(member.bill_snapshot_id)
        if bill is None or bill.kind != "bill":
            raise ValueError("sitting_history_bill_missing")
        bill_sizes[bill.snapshot_id] = len(bill.payload_json.encode())
        if (
            len(snapshot.payload_json.encode()) + sum(bill_sizes.values())
            > MAX_SITTING_HISTORY_BYTES
        ):
            raise ValueError("sitting_history_snapshot_budget")
        semantic = Bill.model_validate(read_json(bill.payload_json))
        if (semantic.term, semantic.number) != (member.term, member.number):
            raise ValueError("sitting_history_bill_mismatch")
        context = member.plan.model_copy(
            update={
                "bill_json": bill.payload_json,
                "sitting_group": (),
                "continuation_ids": (),
            }
        )
        sitting_root_plan(
            publication.model_copy(
                update={
                    "status": PublicationStatus.QUEUED,
                    "delivery": context,
                }
            )
        )
        if (
            context.item_json is None
            or AgendaItem.model_validate_json(context.item_json).ref != member.ref
        ):
            raise ValueError("sitting_history_agenda_ref_mismatch")
        retained = publication.delivery
        if retained is not None and (
            retained.bill_json != bill.payload_json
            or sitting_content_context(retained) != member.plan
            or (retained.group_text if member.plan.sitting_group else retained.rendered_text)
            != plan.parts[0].html
        ):
            raise ValueError("sitting_history_member_plan_mismatch")
    root = publications[version.root_publication_id]
    if plan.parts[0].disable_notification != plan.members[0].plan.disable_notification:
        raise ValueError("sitting_history_root_notification_mismatch")
    if len(plan.members) > 1:
        sent = [
            publications[member.id]
            for member in plan.members
            if publications[member.id].status is PublicationStatus.SENT
        ]
        if sent and (
            len(sent) != len(plan.members)
            or root.message_id is None
            or any(member.message_id != root.message_id for member in sent)
        ):
            raise ValueError("sitting_history_group_receipt_mismatch")
    parent = root
    for index, part in enumerate(plan.parts[1:], 2):
        child = publications[part.id]
        retained = child.delivery
        if (
            child.kind is not PublicationKind.MESSAGE_PART
            or (child.term, child.number) != (root.term, root.number)
            or child.ref != f"message/{version.root_publication_id}/{index}"
            or (
                child.status is PublicationStatus.SENT
                and (parent.status is not PublicationStatus.SENT or parent.message_id is None)
            )
            or (
                retained is not None
                and (
                    retained.continuation_root != root.id
                    or retained.continuation_parent != parent.id
                    or retained.continuation_index != index
                    or retained.rendered_text != part.html
                    or retained.disable_notification != part.disable_notification
                    or retained.reply_to not in (None, parent.message_id)
                    or retained.edit_message_id is not None
                )
            )
        ):
            raise ValueError("sitting_history_part_mismatch")
        parent = child
    return plan


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
            verify_direct_stage_content(connection, value)
            verify_grouped_stage_content(connection, value, require_bound=True)
            verify_digest_content(connection, value, require_bound=True)
            verify_sitting_content(connection, value, require_bound=True)
            physical_identity = verify_card_content(connection, value, retained=True)
            if value.change_snapshot_id is None and value.digest_profile is None:
                bindings = _query(
                    connection,
                    "SELECT publication_id FROM publication_content WHERE content_id=?",
                    (value.content_id,),
                ).fetchall()
                if physical_identity is not None and any(
                    binding[0] != physical_identity for binding in bindings
                ):
                    raise ValueError("card_content_binding_mismatch")
        orphan = _query(
            connection,
            "SELECT 1 FROM delivery_resolutions AS d"
            " LEFT JOIN publication_content AS pc ON pc.publication_id=d.publication_id"
            " LEFT JOIN content_versions AS v ON v.content_id=pc.content_id"
            " WHERE CASE WHEN json_valid(d.resolution_json) THEN"
            " json_type(d.resolution_json, '$.profile') IS NOT NULL OR"
            " json_type(d.resolution_json, '$.resolution') IS NOT NULL ELSE 0 END"
            " AND COALESCE(json_extract(v.version_json, '$.reason'), '')!='grouped_stage_revision'"
            " LIMIT 1",
        ).fetchone()
        if orphan is not None:
            raise ValueError("grouped_resolution_orphan")
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
        physical_id, _ = _verify_card_content(connection, value, retained=retained)
        return physical_id
    except RecursionError as exc:
        raise ValueError("invalid_card_content_history") from exc


def read_expired_card_primary_cycle(
    connection: sqlite3.Connection, publication_id: int
) -> dict[str, object] | None:
    """Read a confirmed original using its validated compact evidence and expired plan."""
    if (
        _query(
            connection,
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='publication_content'",
        ).fetchone()
        is None
    ):
        return None
    row = _query(connection, "SELECT * FROM publications WHERE id=?", (publication_id,)).fetchone()
    if row is None:
        return None
    root = read_content_publication(row)
    if (
        root.kind is not PublicationKind.NEW_BILL
        or root.status is not PublicationStatus.SENT
        or root.message_id is None
        or root.message_id <= 0
        or root.sent_at is None
        or not root.channel_id
        or root.delivery is not None
    ):
        return None
    binding = _query(
        connection,
        "SELECT v.* FROM publication_content p LEFT JOIN content_versions v USING(content_id)"
        " WHERE p.publication_id=?",
        (publication_id,),
    ).fetchone()
    if binding is None:
        return None
    if binding["content_id"] is None:
        raise ValueError("compact_card_content_missing")
    version = read_content_version(binding)
    if version.card_primary_evidence is None:
        return None
    if version.root_publication_id != publication_id:
        raise ValueError("compact_card_original_binding_scope")
    aliases = _query(
        connection,
        "SELECT count(*) FROM publications WHERE kind='new_bill' AND channel_id=? AND message_id=?",
        (root.channel_id, root.message_id),
    ).fetchone()[0]
    if aliases != 1:
        return None
    physical_id, primary = _verify_card_content(connection, version, retained=True)
    if physical_id != publication_id or primary is None or not version.rendered_text:
        raise ValueError("compact_card_primary_evidence_unavailable")
    return {
        **primary,
        "content_version_id": version.content_id,
        "recorded_sent_at": root.sent_at.isoformat(),
        "frozen_render_sha256": hashlib.sha256(version.rendered_text.encode()).hexdigest(),
    }


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
