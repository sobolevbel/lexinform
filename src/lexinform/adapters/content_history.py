"""Read known card-history rows without turning missing physical evidence into confirmation."""

import hashlib
import sqlite3
from datetime import datetime
from typing import Literal, NamedTuple

from lexinform.adapters.computation_history import read_result_application
from lexinform.adapters.content_applications import verify_content_applications
from lexinform.adapters.content_source_refs import ContentSourceRows
from lexinform.adapters.digest_content import verify_digest_content
from lexinform.adapters.input_receipts import (
    project_pdf_page_ranges,
    retained_pdf_page_ranges,
    validate_multipart_item,
)
from lexinform.adapters.joint_content_evidence import joint_comparison_application_id
from lexinform.adapters.primary_scan_history import read_primary_scan_source
from lexinform.adapters.review_content_evidence import direct_review_application_id
from lexinform.adapters.status_changes import read_status_change
from lexinform.bill_context import continuation_snapshot
from lexinform.content_versions import (
    act_published_plan_matches,
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
    ComputationResult,
    ContentSnapshot,
    ContentVersion,
    DeliveryPlan,
    LlmBatchItem,
    PrimaryInputReceipt,
    Publication,
    PublicationKind,
    PublicationStatus,
    SourceObservation,
    StatusChange,
)
from lexinform.models.commands import (
    GroupedEditDeliveryResolution,
    IncomingCommand,
    read_delivery_resolution,
)
from lexinform.models.content import (
    MAX_ACT_PUBLISHED_CONTENT_BYTES,
    MAX_DIRECT_HELD_STAGE_CONTENT_BYTES,
    MAX_DIRECT_STAGE_CONTENT_BYTES,
    MAX_GROUPED_STAGE_CONTENT_BYTES,
    CardPrimaryEvidence,
    card_bill_snapshot,
    grouped_stage_content_budget,
    original_bill_context,
    original_delivery_bill_snapshot,
    unpack_original_bill_snapshot,
    validate_review_notice,
)
from lexinform.models.events import amendments_stage, has_single_amendments_stage
from lexinform.models.input_receipts import (
    LooseRclInputReceipt,
    RclArchiveInputReceipt,
    RclArchiveSelfAmendmentInputReceipt,
    RclPairedArchiveInputReceipt,
    SejmSelfAmendmentInputReceipt,
)
from lexinform.models.multipart_card_history import MAX_MULTIPART_CARD_BYTES, MultipartCardPlan
from lexinform.models.original_delivery_content import (
    MAX_ORIGINAL_DELIVERY_BYTES,
    REVIEW_NOTICE_KINDS,
    OriginalDeliveryPart,
    OriginalDeliveryPlan,
    original_delivery_context,
)
from lexinform.models.primary_projections import PRIMARY_USAGE_FIELDS
from lexinform.models.rcl import RclProject, rcl_number
from lexinform.models.sejm import has_process
from lexinform.models.sitting_content import (
    MAX_SITTING_HISTORY_BYTES,
    SittingContentPlan,
    sitting_content_context,
)
from lexinform.models.wykaz import WykazEntry, wykaz_number
from lexinform.primary_projections import primary_record_matches, primary_record_sha256
from lexinform.sitting_delivery import sitting_root_plan
from lexinform.telegram_text import MESSAGE_LIMIT, utf16_length


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


def _card_scan_evidence_candidate(
    connection: sqlite3.Connection, bill: Bill, result_id: str
) -> CardPrimaryEvidence | None:
    reader = ContentSourceRows(connection)
    paid = reader.get_computation_result(result_id)
    if paid is None or (paid.term, paid.number) != (bill.term, bill.number):
        return None
    item = reader.get_consumed_primary_input(
        paid.provider, paid.batch_id, paid.custom_id, bill.analysis_generation
    )
    if item is None or not isinstance(item.meta, BatchItemMeta):
        return None
    meta = item.meta
    if meta.prepared_input_receipt is not None or meta.text_source != "scan":
        return None
    source, _ = read_primary_scan_source(connection, paid, meta)
    rows = _query(
        connection,
        "SELECT id FROM result_applications WHERE result_id=? AND term=? AND number=?"
        " AND generation=? AND phase='projection'"
        " AND json_extract(application_json,'$.projected_generation')=?"
        " AND json_extract(application_json,'$.reason')='batch_result' LIMIT 2",
        (result_id, paid.term, paid.number, paid.generation, bill.analysis_generation),
    ).fetchall()
    if len(rows) != 1:
        return None
    return CardPrimaryEvidence(
        projection_application_id=rows[0]["id"], source_observation_id=source.observation_id
    )


def card_primary_evidence_candidate(
    connection: sqlite3.Connection, bill: Bill
) -> CardPrimaryEvidence | None:
    record = bill.analysis
    if record is None or record.computation_id is None or record.request_sha256 is None:
        return None
    if record.text_source == "scan" and record.selection.scan_input is not None:
        try:
            return _card_scan_evidence_candidate(connection, bill, record.computation_id)
        except ValueError:
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
                'primary-rcl-archive-package-text-v1',
                'primary-rcl-archive-self-amendment-text-v1',
                'primary-rcl-paired-archive-text-v1'
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


def _card_primary_text_source(
    connection: sqlite3.Connection,
    reference: CardPrimaryEvidence,
    paid: ComputationResult,
    item: LlmBatchItem,
    meta: BatchItemMeta,
) -> tuple[SourceObservation, dict[str, object]]:
    record = paid.record
    assert isinstance(record, AnalysisRecord), "Card evidence requires a validated primary result"
    receipt = meta.prepared_input_receipt
    if (
        not isinstance(
            receipt,
            (
                PrimaryInputReceipt,
                LooseRclInputReceipt,
                RclArchiveInputReceipt,
                RclArchiveSelfAmendmentInputReceipt,
                RclPairedArchiveInputReceipt,
                SejmSelfAmendmentInputReceipt,
            ),
        )
        or receipt.source_selection_id is None
        or not meta.source_url
        or receipt.request_sha256 != paid.request_sha256
        or meta.input_chars != receipt.text_chars
        or record.input_chars != receipt.text_chars
    ):
        raise CardPrimaryEvidenceError("card_primary_item_scope")
    reader = ContentSourceRows(connection)
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
        ranges = retained_pdf_page_ranges(connection, receipt)
        physical = {
            "artifact_sha256": receipt.artifact_sha256,
            "extraction_id": receipt.extraction_id,
        }
        if ranges:
            physical["normalized_pdf_page_ranges"] = ranges
    else:
        if receipt.source_observation_id != reference.source_observation_id:
            raise CardPrimaryEvidenceError("card_primary_source_scope")
        parts = validate_multipart_item(connection, item)
        if isinstance(
            receipt,
            (
                RclArchiveInputReceipt,
                RclArchiveSelfAmendmentInputReceipt,
                RclPairedArchiveInputReceipt,
            ),
        ):
            if isinstance(receipt, RclArchiveInputReceipt):
                archive_parts = receipt.source_parts
            elif isinstance(receipt, RclArchiveSelfAmendmentInputReceipt):
                archive_parts = receipt.archive_parts
            else:
                archive_parts = receipt.source_parts[:-1]
            source_parts: list[dict[str, object]] = [
                {"role": recorded.role, "physical_index": recorded.physical_index}
                for recorded in archive_parts
            ]
            if isinstance(receipt, RclArchiveSelfAmendmentInputReceipt):
                source_parts.append(
                    {
                        "role": receipt.amendment_part.role,
                        "artifact_sha256": selection.selection.parts[-1].source_sha256,
                        "extraction_id": receipt.amendment_part.extraction_id,
                    }
                )
            elif isinstance(receipt, RclPairedArchiveInputReceipt):
                amendment = selection.selection.parts[-1]
                source_parts.append(
                    {
                        "role": receipt.amendment_part.role,
                        "artifact_sha256": amendment.source_sha256,
                        "member_sha256": amendment.sha256,
                        "physical_index": receipt.amendment_part.physical_index,
                        "capture_id": receipt.amendment_capture_id,
                    }
                )
            physical = {
                "artifact_sha256": selection.selection.parts[0].source_sha256,
                "extraction_id": receipt.extraction_id,
                "source_parts": source_parts,
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
        if not isinstance(receipt, RclPairedArchiveInputReceipt):
            ranges = project_pdf_page_ranges(receipt.segments, parts)
            if ranges:
                physical["normalized_pdf_page_ranges"] = ranges
    return source, physical


def _verify_card_primary_evidence(
    connection: sqlite3.Connection,
    reference: CardPrimaryEvidence,
    version: ContentVersion,
    bill: Bill,
    root: Publication | None,
    *,
    revision_analysis_sha256: str | None = None,
    projection_after: datetime | None = None,
    compact_revision: bool = False,
    original_owner_verified: bool = False,
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
        and not original_owner_verified
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
    if original_owner_verified or root is not None and root.delivery is not None:
        rendered_bill = bill
        if root is not None and root.delivery is not None:
            rendered_bill = Bill.model_validate(read_json(root.delivery.bill_json))
        record = rendered_bill.analysis
        if (
            record is None
            and not compact_revision
            or record is not None
            and (
                not primary_record_matches(record, paid)
                or record.model_dump(exclude=set(PRIMARY_USAGE_FIELDS))
                != paid.record.model_dump(exclude=set(PRIMARY_USAGE_FIELDS))
            )
        ):
            raise CardPrimaryEvidenceError("card_primary_render_result_mismatch")
    item = reader.get_consumed_primary_input(
        paid.provider, paid.batch_id, paid.custom_id, bill.analysis_generation
    )
    if item is None or item.consumed_at is None or not isinstance(item.meta, BatchItemMeta):
        raise CardPrimaryEvidenceError("card_primary_item_missing")
    meta = item.meta
    if (
        (item.term, item.number, item.call_kind) != (paid.term, paid.number, paid.purpose)
        or meta.input_binding != paid.input_binding
        or meta.generation != paid.generation
        or meta.queued_at is None
        or meta.request_sha256 != paid.request_sha256
    ):
        raise CardPrimaryEvidenceError("card_primary_item_scope")
    if revision_analysis_sha256 is not None and (
        meta.prepared_input_receipt is None
        or revision_analysis_sha256 != primary_record_sha256(paid.record)
    ):
        raise CardPrimaryEvidenceError("card_revision_primary_result_mismatch")
    if (
        meta.prepared_input_receipt is None
        and meta.text_source == "scan"
        and meta.selection.scan_input is not None
    ):
        source, physical = read_primary_scan_source(connection, paid, meta)
        if source.observation_id != reference.source_observation_id:
            raise CardPrimaryEvidenceError("card_primary_source_scope")
    else:
        source, physical = _card_primary_text_source(connection, reference, paid, item, meta)
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
        or projection_after is not None
        and projection.created_at < projection_after
    ):
        raise CardPrimaryEvidenceError("card_primary_chronology")
    return {
        "source_kind": source.document.kind,
        "source_observation_id": source.observation_id,
        "source_selection_id": source.selection_id,
        **physical,
        "purpose": paid.purpose,
        "result_id": paid.result_id,
        "request_sha256": paid.request_sha256,
        "collection_application_id": collections[0]["id"],
        "projection_application_id": reference.projection_application_id,
    }


def _verify_card_content(
    connection: sqlite3.Connection,
    value: ContentVersion,
    *,
    retained: bool,
    bound_physical_id: int | None = None,
) -> tuple[int | None, dict[str, object] | None]:
    if (
        value.change_snapshot_id is not None
        or value.digest_profile is not None
        or value.sitting_profile is not None
        or value.act_published_evidence is not None
        or value.original_delivery_evidence is not None
        or value.multipart_card_revision is not None
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
    if (
        bound_physical_id is None
        and value.card_primary_evidence is not None
        and value.previous_version_id is None
    ):
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
    marked_continuation = False
    current_projection = False
    projection_after = None
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
        reference = value.card_primary_evidence
        if reference is not None and bound_physical_id is None:
            marked_continuation = previous.previous_version_id is not None
            if (
                root is None
                or root.status is not PublicationStatus.SENT
                or root.sent_at is None
                or root.sent_at.utcoffset() is None
                or root.sent_at > value.recorded_at
                or semantic.update_group
                or semantic.sitting_group
            ):
                raise CardPrimaryEvidenceError("card_revision_primary_predecessor")
            current_projection = reference.revision_projection == "current"
            try:
                if current_projection:
                    projection_after = _card_current_projection_parent(
                        connection,
                        reference,
                        value,
                        root,
                        previous,
                        previous_ids,
                        bill.analysis_generation,
                    )
                    marked_continuation = True
                else:
                    original = _card_primary_revision_origin(
                        connection, value, root, previous, previous_ids
                    )
                    inherited = reference.model_copy(update={"revision_analysis_sha256": None})
                    primary = _verify_card_primary_evidence(
                        connection,
                        inherited,
                        original,
                        bill,
                        root,
                        revision_analysis_sha256=reference.revision_analysis_sha256,
                    )
            except CardPrimaryEvidenceError:
                raise
            except ValueError as exc:
                raise CardPrimaryEvidenceError("invalid_card_primary_evidence") from exc
        if (
            not retained
            and not current_projection
            and (reference is None or previous.previous_version_id is None)
        ):
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
        if (
            not card_physical_matches(value, physical, root, semantic, previous_ids)
            or (marked_continuation and not _card_revision_time_matches(value, physical))
            or (
                physical.delivery is not None
                and content_delivery_mismatch(value, snapshots, physical.delivery) is not None
            )
        ):
            raise ValueError("card_content_binding_scope")
    if root is None:
        if retained:
            return None, primary
        raise ValueError("card_content_root_missing")
    assert root_row is not None, "the available typed root comes from its physical row"
    if bound_physical_id is not None:
        if {binding[0] for binding in bindings} != {bound_physical_id}:
            return None, primary
        rows = _query(
            connection, "SELECT * FROM publications WHERE id=?", (bound_physical_id,)
        ).fetchall()
    else:
        rows = (
            [root_row]
            if value.previous_version_id is None
            else card_publication_rows(connection, value.root_publication_id)
        )
    matches: list[Publication] = []
    for row in rows:
        physical = read_content_publication(row)
        if not card_physical_matches(value, physical, root, semantic, previous_ids) or (
            marked_continuation and not _card_revision_time_matches(value, physical)
        ):
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
            matches.append(physical)
    if not matches and retained and value.previous_version_id is not None:
        return None, primary
    if len(matches) != 1:
        raise ValueError("card_content_physical_scope")
    if retained and missing:
        return None, primary
    (physical,) = matches
    reference = value.card_primary_evidence
    if current_projection and reference is not None:
        try:
            primary = _verify_card_primary_evidence(
                connection,
                reference,
                value,
                bill,
                physical,
                revision_analysis_sha256=reference.revision_analysis_sha256,
                projection_after=projection_after,
                compact_revision=True,
            )
        except CardPrimaryEvidenceError:
            raise
        except ValueError as exc:
            raise CardPrimaryEvidenceError("invalid_card_primary_evidence") from exc
    return physical.id, primary


def _card_revision_time_matches(value: ContentVersion, physical: Publication) -> bool:
    return (
        physical.created_at.utcoffset() is not None
        and physical.created_at <= value.recorded_at
        and (
            physical.status is not PublicationStatus.SENT
            or physical.sent_at is not None
            and physical.sent_at.utcoffset() is not None
            and value.recorded_at <= physical.sent_at
        )
    )


def _card_current_projection_parent(
    connection: sqlite3.Connection,
    reference: CardPrimaryEvidence,
    value: ContentVersion,
    root: Publication,
    previous: ContentVersion,
    previous_ids: set[int],
    generation: int,
) -> datetime | None:
    prior = previous.card_primary_evidence
    if root.id is None or root.sent_at is None or len(previous_ids) != 1:
        raise CardPrimaryEvidenceError("card_revision_primary_predecessor")
    (prior_id,) = previous_ids
    row = _query(connection, "SELECT * FROM publications WHERE id=?", (prior_id,)).fetchone()
    if row is None:
        raise CardPrimaryEvidenceError("card_revision_primary_predecessor")
    physical = read_content_publication(row)
    if (
        physical.status is not PublicationStatus.SENT
        or physical.message_id != root.message_id
        or physical.sent_at is None
        or not _card_revision_time_matches(previous, physical)
        or not root.sent_at <= physical.sent_at <= value.recorded_at
        or _verify_card_content(connection, previous, retained=True, bound_physical_id=prior_id)[0]
        != prior_id
    ):
        raise CardPrimaryEvidenceError("card_revision_primary_predecessor")
    if prior is None:
        snapshot = ContentSourceRows(connection).get_content_snapshot(previous.bill_context_id)
        if snapshot is None:
            raise CardPrimaryEvidenceError("card_revision_primary_predecessor")
        previous_bill = Bill.model_validate_json(snapshot.payload_json)
        if not 0 < previous_bill.analysis_generation < generation:
            raise CardPrimaryEvidenceError("card_revision_primary_predecessor")
        return physical.sent_at
    if reference.projection_application_id == prior.projection_application_id:
        if prior.revision_projection != "current" or reference != prior:
            raise CardPrimaryEvidenceError("card_revision_primary_predecessor")
        return None
    previous_projection_row = _query(
        connection,
        "SELECT * FROM result_applications WHERE id=?",
        (prior.projection_application_id,),
    ).fetchone()
    current_projection_row = _query(
        connection,
        "SELECT * FROM result_applications WHERE id=?",
        (reference.projection_application_id,),
    ).fetchone()
    if previous_projection_row is None or current_projection_row is None:
        raise CardPrimaryEvidenceError("card_revision_primary_predecessor")
    previous_projection = read_result_application(previous_projection_row)
    current_projection = read_result_application(current_projection_row)
    if (
        previous_projection.result_id == current_projection.result_id
        or previous_projection.projected_generation is None
        or current_projection.projected_generation is None
        or previous_projection.projected_generation >= current_projection.projected_generation
    ):
        raise CardPrimaryEvidenceError("card_revision_primary_predecessor")
    return physical.sent_at


def _card_primary_revision_origin(
    connection: sqlite3.Connection,
    value: ContentVersion,
    root: Publication,
    previous: ContentVersion,
    previous_ids: set[int],
) -> ContentVersion:
    reference = value.card_primary_evidence
    assert reference is not None, "only a marked revision resolves its original evidence"
    inherited = reference.model_copy(update={"revision_analysis_sha256": None})
    if previous.previous_version_id is None:
        if previous_ids != {root.id} or previous.card_primary_evidence != inherited:
            raise CardPrimaryEvidenceError("card_revision_primary_predecessor")
        return previous
    if root.id is None or len(previous_ids) != 1 or previous.card_primary_evidence != reference:
        raise CardPrimaryEvidenceError("card_revision_primary_predecessor")
    (previous_id,) = previous_ids
    predecessor_row = _query(
        connection, "SELECT * FROM publications WHERE id=?", (previous_id,)
    ).fetchone()
    if predecessor_row is None:
        raise CardPrimaryEvidenceError("card_revision_primary_predecessor")
    predecessor = read_content_publication(predecessor_row)
    if (
        predecessor.status is not PublicationStatus.SENT
        or predecessor.message_id != root.message_id
        or predecessor.sent_at is None
        or root.sent_at is None
        or not _card_revision_time_matches(previous, predecessor)
        or not root.sent_at <= previous.recorded_at
        or predecessor.sent_at > value.recorded_at
        or _verify_card_content(connection, previous, retained=True, bound_physical_id=previous_id)[
            0
        ]
        != previous_id
    ):
        raise CardPrimaryEvidenceError("card_revision_primary_predecessor")
    row = _query(
        connection,
        "SELECT v.* FROM publication_content p JOIN content_versions v USING(content_id)"
        " WHERE p.publication_id=?",
        (root.id,),
    ).fetchone()
    if row is None:
        raise CardPrimaryEvidenceError("card_revision_primary_predecessor")
    original = read_content_version(row)
    if (
        original.root_publication_id != value.root_publication_id
        or original.previous_version_id is not None
        or original.card_primary_evidence != inherited
        or _verify_card_content(connection, original, retained=True, bound_physical_id=root.id)[0]
        != root.id
    ):
        raise CardPrimaryEvidenceError("card_revision_primary_predecessor")
    return original


class StageAnchorReceipt(NamedTuple):
    term: int
    number: str
    channel_id: str
    message_id: int
    sent_at: datetime
    retired: bool = False


def _stage_anchor(
    connection: sqlite3.Connection, identity: int, *, allow_retired: bool = True
) -> StageAnchorReceipt | None:
    live = _query(connection, "SELECT * FROM publications WHERE id=?", (identity,)).fetchone()
    if live is not None:
        anchor = read_content_publication(live)
        if (
            anchor.kind is not PublicationKind.NEW_BILL
            or anchor.status is not PublicationStatus.SENT
            or anchor.message_id is None
            or anchor.message_id <= 0
            or anchor.sent_at is None
        ):
            return None
        return StageAnchorReceipt(
            anchor.term, anchor.number, anchor.channel_id, anchor.message_id, anchor.sent_at
        )
    if not allow_retired:
        return None
    archived = _query(
        connection, "SELECT * FROM retired_stage_anchors WHERE id=?", (identity,)
    ).fetchone()
    if archived is None:
        return None
    sent_at = datetime.fromisoformat(archived["sent_at"])
    if sent_at.tzinfo is None or archived["message_id"] <= 0:
        return None
    return StageAnchorReceipt(
        archived["term"],
        archived["number"],
        archived["channel_id"],
        archived["message_id"],
        sent_at,
        retired=True,
    )


def verify_direct_stage_content(
    connection: sqlite3.Connection, version: ContentVersion, *, require_bound: bool = False
) -> OriginalDeliveryPlan | None:
    evidence = version.direct_stage_evidence
    if evidence is None:
        return None
    root_row = _query(
        connection, "SELECT * FROM publications WHERE id=?", (version.root_publication_id,)
    ).fetchone()
    anchor = _stage_anchor(connection, evidence.anchor_publication_id)
    event = _query(
        connection, "SELECT * FROM status_changes WHERE id=?", (version.status_change_id,)
    ).fetchone()
    if root_row is None or anchor is None or event is None:
        raise ValueError("direct_stage_parent_missing")
    root = read_content_publication(root_row)
    if (
        root.kind is not PublicationKind.STATUS_UPDATE
        or root.status_change_id != version.status_change_id
        or anchor.sent_at > root.created_at
        or (anchor.number, anchor.channel_id) != (root.number, root.channel_id)
        or (anchor.term != root.term and not anchor.retired)
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
        "SELECT publication_id FROM publication_content WHERE content_id=?",
        (version.content_id,),
    ).fetchall()
    if (
        evidence.multipart is None
        and bindings
        and [binding[0] for binding in bindings] != [root.id]
    ):
        raise ValueError("direct_stage_binding_scope")
    reader = ContentSourceRows(connection)
    snapshots: dict[str, ContentSnapshot] = {}
    for kind, identity in content_snapshot_refs(version):
        snapshot = reader.get_content_snapshot(identity)
        if snapshot is None or snapshot.kind != kind:
            raise ValueError("direct_stage_snapshot_missing")
        snapshots[identity] = snapshot
    if sum(len(s.payload_json.encode()) for s in snapshots.values()) + len(
        (version.rendered_text or "").encode()
    ) > (
        MAX_ORIGINAL_DELIVERY_BYTES
        if evidence.multipart is not None
        else MAX_DIRECT_HELD_STAGE_CONTENT_BYTES
        if evidence.held is not None
        else MAX_DIRECT_STAGE_CONTENT_BYTES
    ):
        raise ValueError("direct_stage_content_bound")
    bill = snapshots[version.bill_context_id]
    owner = Bill.model_validate_json(bill.payload_json)
    if (
        card_bill_snapshot(bill.payload_json) != bill
        or not _original_delivery_owner_matches(
            connection, owner, root, compact=evidence.multipart is None
        )
        or not owner.term <= anchor.term <= root.term
    ):
        raise ValueError("direct_stage_bill_scope")
    if version.change_snapshot_id is None:
        raise ValueError("direct_stage_snapshot_missing")
    change = StatusChange.model_validate_json(snapshots[version.change_snapshot_id].payload_json)
    suffix: dict[str, object] = {"amendments": None, "supplements": []}
    current = read_status_change(event).model_copy(update=suffix)
    if evidence.held is not None:
        held_plan = DeliveryPlan.model_validate_json(
            snapshots[version.plan_snapshot_id].payload_json
        )
        current = _direct_held_stage_change(connection, root, current, held_plan)

    application = evidence.multipart == "direct-stage-application-parts-v1"
    amendment = amendments_stage(change.new_stages) is not None
    if application:
        pair = version.supplement_pair_evidence is not None
        scope = (
            change.amendments is None
            and len(change.supplements) == (2 if pair else 1)
            and not amendment
            and len(change.new_stages) <= (2 if pair else 1)
            if version.supplement_evidence is not None or pair
            else change.amendments is not None
            and not change.supplements
            and has_single_amendments_stage(change.new_stages)
        )
    else:
        scope = bool(change.new_stages) and not amendment
    if (
        (current.term, current.number) != (root.term, root.number)
        or (change.term, change.number) != (owner.term, owner.number)
        or (change.model_copy(update=suffix) if application else change)
        != current.model_copy(update={"term": owner.term})
        or not scope
    ):
        raise ValueError("direct_stage_event_scope")
    if evidence.multipart is not None:
        physical = OriginalDeliveryPlan.model_validate_json(
            snapshots[version.plan_snapshot_id].payload_json
        )
        if (
            not owner.has_process
            or len(physical.parts) < 2
            or physical.bindings[0] != root.id
            or (require_bound or bindings)
            and {binding[0] for binding in bindings} != set(physical.bindings)
            or physical.root.reply_to != anchor.message_id
            or not direct_stage_plan_matches(
                physical.root.model_copy(update={"continuation_ids": ()})
            )
            or physical.root.held_change_ids
            or physical.root.with_act
            or physical.root.render_origin is not None
            or any(part.held_change_ids for part in physical.parts)
        ):
            raise ValueError("direct_stage_multipart_scope")
        _verify_direct_stage_parts(connection, version, physical, root, owner, bill, change)
        return physical
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
    return None


def _direct_held_stage_change(
    connection: sqlite3.Connection, root: Publication, current: StatusChange, plan: DeliveryPlan
) -> StatusChange:
    ids = plan.held_change_ids
    if (
        not 1 <= len(ids) <= 16
        or len(set(ids)) != len(ids)
        or current.id in ids
        or not current.new_stages
        or plan.with_act
        or plan.render_origin is not None
        or not direct_stage_plan_matches(plan)
    ):
        raise ValueError("direct_held_stage_plan_scope")
    if root.status is PublicationStatus.SENT and (
        root.message_id is None or root.message_id <= 0 or root.sent_at is None
    ):
        raise ValueError("direct_held_stage_root_receipt")
    dismissed = root.status is PublicationStatus.DISMISSED
    if dismissed:
        _verify_direct_held_dismissal(connection, root)
    stages = []
    for identity in ids:
        event = _query(
            connection, "SELECT * FROM status_changes WHERE id=?", (identity,)
        ).fetchone()
        matches = _query(
            connection,
            "SELECT * FROM publications WHERE status_change_id=? AND channel_id=?"
            " AND kind='status_update' LIMIT 2",
            (identity, root.channel_id),
        ).fetchall()
        if event is None or len(matches) != 1:
            raise ValueError("direct_held_stage_parent_missing")
        held = read_status_change(event)
        physical = read_content_publication(matches[0])
        if (
            (held.term, held.number) != (root.term, root.number)
            or (physical.term, physical.number) != (root.term, root.number)
            or held.detected_at > current.detected_at
            or physical.created_at > root.created_at
            or physical.attempts
            or not held.new_stages
            or _query(
                connection,
                "SELECT 1 FROM digest_event_links WHERE source_publication_id=?",
                (physical.id,),
            ).fetchone()
            is not None
        ):
            raise ValueError("direct_held_stage_parent_scope")
        if root.status is PublicationStatus.SENT:
            if physical.status is not PublicationStatus.SENT or (
                physical.message_id,
                physical.sent_at,
            ) != (root.message_id, root.sent_at):
                raise ValueError("direct_held_stage_release_receipt")
        elif (
            physical.status
            is not (PublicationStatus.DISMISSED if dismissed else PublicationStatus.SKIPPED)
            or physical.message_id is not None
            or physical.sent_at is not None
        ):
            raise ValueError("direct_held_stage_unreleased_scope")
        stages.extend(held.new_stages)
    return current.model_copy(update={"new_stages": stages + current.new_stages})


def _verify_direct_held_dismissal(connection: sqlite3.Connection, root: Publication) -> None:
    rows = _query(
        connection,
        "SELECT command_update_id, resolution_json FROM delivery_resolutions"
        " WHERE publication_id=? AND json_extract(resolution_json,'$.action')='dismiss' LIMIT 2",
        (root.id,),
    ).fetchall()
    if len(rows) != 1 or root.message_id is not None or root.sent_at is not None:
        raise ValueError("direct_held_stage_dismissal")
    row = rows[0]
    audit = read_delivery_resolution(row["resolution_json"])
    if (
        isinstance(audit, GroupedEditDeliveryResolution)
        or (audit.publication_id, audit.term, audit.number, audit.kind, audit.channel_id)
        != (root.id, root.term, root.number, root.kind, root.channel_id)
        or audit.publication_ids != (root.id,)
        or audit.action != "dismiss"
        or audit.message_id is not None
        or audit.resolved_at.utcoffset() is None
        or audit.resolved_at < root.created_at
        or row["command_update_id"] != audit.command.update_id
        or not recorded_command_matches(connection, audit.command)
    ):
        raise ValueError("direct_held_stage_dismissal")


def _verify_direct_stage_parts(
    connection: sqlite3.Connection,
    version: ContentVersion,
    physical: OriginalDeliveryPlan,
    root: Publication,
    owner: Bill,
    bill: ContentSnapshot,
    change: StatusChange,
) -> None:
    evidence = version.direct_stage_evidence
    assert evidence is not None, "direct stage parts have direct evidence"
    parent = root
    message_ids: set[int] = (
        {physical.root.reply_to} if physical.root.reply_to is not None else set()
    )
    for index, part in enumerate(physical.parts, 1):
        row = _query(connection, "SELECT * FROM publications WHERE id=?", (part.id,)).fetchone()
        if row is None:
            raise ValueError("direct_stage_part_missing")
        publication = read_content_publication(row)
        retained = publication.delivery
        sent = (
            publication.status is PublicationStatus.SENT
            and publication.message_id is not None
            and publication.message_id > 0
            and publication.sent_at is not None
        )
        if (
            (publication.term, publication.number, publication.channel_id)
            != (root.term, root.number, root.channel_id)
            or (publication.status is PublicationStatus.SENT and not sent)
            or (retained is None and not sent)
        ):
            raise ValueError("direct_stage_part_scope")
        if sent and publication.message_id is not None:
            if publication.message_id in message_ids:
                raise ValueError("direct_stage_duplicate_message")
            message_ids.add(publication.message_id)
        if index == 1:
            if retained is not None and (
                hashlib.sha256(retained.bill_json.encode()).hexdigest()
                != evidence.original_bill_sha256
                or card_bill_snapshot(retained.bill_json) != bill
                or StatusChange.model_validate_json(retained.change_json) != change
                or retained.rendered_text != part.html
                or original_delivery_context(retained).model_copy(update={"change_json": ""})
                != physical.root
            ):
                raise ValueError("direct_stage_frozen_delivery_mismatch")
        else:
            if publication.status_change_id is not None:
                raise ValueError("direct_stage_part_scope")
            _verify_original_delivery_child(version, part, index, publication, parent, owner)
        parent = publication


def verify_grouped_stage_content(
    connection: sqlite3.Connection, version: ContentVersion, *, require_bound: bool = False
) -> int | None:
    evidence = version.grouped_stage_evidence
    if evidence is None:
        return None
    if evidence.revision is not None:
        return _verify_grouped_stage_revision(connection, version, require_bound=require_bound)
    ref = f"updates/{version.root_publication_id}"
    ids = tuple(member.publication_id for member in evidence.members)
    selected = _query(
        connection,
        "SELECT * FROM publications WHERE ref=? AND kind='status_update' LIMIT ?",
        (ref, len(ids) + 1),
    ).fetchall()
    by_id = {row["id"]: read_content_publication(row) for row in selected}
    if set(by_id) != set(ids):
        raise ValueError("grouped_stage_membership")
    text = version.rendered_text or ""
    if len(ids) > 2 and (not text.strip() or utf16_length(text) > MESSAGE_LIMIT):
        raise ValueError("grouped_stage_physical_html")
    publications = tuple(by_id[identity] for identity in ids)
    root = publications[0]
    if (
        len({(row.term, row.number) for row in publications}) != len(ids)
        or (len(ids) > 2 and any(not has_process(row.number) for row in publications))
        or any(
            (row.channel_id, row.status, row.message_id, row.telegram_chat_id)
            != (root.channel_id, root.status, root.message_id, root.telegram_chat_id)
            or row.document_message_ids
            for row in publications
        )
    ):
        raise ValueError("grouped_stage_receipt")
    if root.status is PublicationStatus.SENT:
        if (
            root.message_id is None
            or root.message_id <= 0
            or any(row.sent_at is None for row in publications)
        ):
            raise ValueError("grouped_stage_receipt")
    elif (
        (
            root.status
            not in (
                PublicationStatus.QUEUED,
                PublicationStatus.PENDING,
                PublicationStatus.FAILED,
                PublicationStatus.UNKNOWN,
            )
            and not (len(ids) > 2 and root.status is PublicationStatus.DISMISSED)
        )
        or root.message_id is not None
        or any(row.sent_at is not None for row in publications)
    ):
        raise ValueError("grouped_stage_receipt")
    bindings = _query(
        connection,
        "SELECT publication_id FROM publication_content WHERE content_id=? LIMIT 2",
        (version.content_id,),
    ).fetchall()
    if (bindings or (len(ids) > 2 and require_bound)) and [binding[0] for binding in bindings] != [
        root.id
    ]:
        raise ValueError("grouped_stage_binding_scope")
    reader = ContentSourceRows(connection)
    snapshots: dict[str, ContentSnapshot] = {}
    for kind, identity in content_snapshot_refs(version):
        snapshot = reader.get_content_snapshot(identity)
        if snapshot is None or snapshot.kind != kind:
            raise ValueError("grouped_stage_snapshot_missing")
        snapshots[identity] = snapshot
    if sum(len(snapshot.payload_json.encode()) for snapshot in snapshots.values()) + len(
        (version.rendered_text or "").encode()
    ) > grouped_stage_content_budget(len(ids)):
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
        != tuple((row.term, row.number) for row in publications)
    ):
        raise ValueError("grouped_stage_render_witness_scope")
    plans = tuple(row.delivery for row in publications)
    first_plan = plans[0]
    if None in plans and (root.status is not PublicationStatus.SENT or not bindings):
        raise ValueError("grouped_stage_expired_scope")
    shared = next((plan for plan in plans if plan is not None), None)
    if shared is not None and (
        shared.group_text != version.rendered_text
        or shared.today != compact.today
        or tuple(item.publication_id for item in shared.update_group) != ids
        or (
            first_plan is not None
            and content_delivery_mismatch(version, snapshots, first_plan) is not None
        )
        or any(
            plan is not None
            and (
                plan.group_text != shared.group_text
                or plan.today != shared.today
                or plan.update_group != shared.update_group
            )
            for plan in plans[1:]
        )
    ):
        raise ValueError("grouped_stage_frozen_plan")
    for index, member in enumerate(evidence.members):
        publication = by_id[member.publication_id]
        anchor = _stage_anchor(connection, member.anchor_publication_id)
        event = _query(
            connection, "SELECT * FROM status_changes WHERE id=?", (member.status_change_id,)
        ).fetchone()
        if anchor is None or event is None:
            raise ValueError("grouped_stage_parent_missing")
        if (
            publication.status_change_id != member.status_change_id
            or anchor.message_id != member.anchor_message_id
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
    if evidence is None or len(evidence.members) != 2 or evidence.revision is not None:
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
        if member.packed_bill:
            bill = unpack_original_bill_snapshot(bill)
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


def verify_act_published_content(
    connection: sqlite3.Connection, version: ContentVersion, *, require_bound: bool = False
) -> None:
    evidence = version.act_published_evidence
    if evidence is None:
        return
    row = _query(
        connection, "SELECT * FROM publications WHERE id=?", (version.root_publication_id,)
    ).fetchone()
    if row is None:
        raise ValueError("act_history_publication_missing")
    publication = read_content_publication(row)
    if (
        publication.kind is not PublicationKind.ACT_PUBLISHED
        or publication.channel_id != evidence.channel_id
        or publication.ref != evidence.ref
        or publication.status_change_id is not None
        or publication.created_at != version.recorded_at
        or (
            publication.status is PublicationStatus.SENT
            and (publication.message_id is None or publication.message_id <= 0)
        )
        or (publication.delivery is None and publication.status is not PublicationStatus.SENT)
    ):
        raise ValueError("act_history_publication_scope")
    if require_bound:
        bindings = _query(
            connection,
            "SELECT publication_id FROM publication_content WHERE content_id=?",
            (version.content_id,),
        ).fetchall()
        if [binding[0] for binding in bindings] != [version.root_publication_id]:
            raise ValueError("act_history_binding_scope")
    reader = ContentSourceRows(connection)
    snapshots: dict[str, ContentSnapshot] = {}
    for kind, identity in content_snapshot_refs(version):
        snapshot = reader.get_content_snapshot(identity)
        if snapshot is None or snapshot.kind != kind:
            raise ValueError("act_history_snapshot_missing")
        snapshots[identity] = snapshot
    html = version.rendered_text or ""
    if (
        not html.strip()
        or utf16_length(html) > MESSAGE_LIMIT
        or sum(len(snapshot.payload_json.encode()) for snapshot in snapshots.values())
        + len(html.encode())
        > MAX_ACT_PUBLISHED_CONTENT_BYTES
    ):
        raise ValueError("act_history_budget")
    bill = Bill.model_validate(read_json(snapshots[version.bill_context_id].payload_json))
    plan = DeliveryPlan.model_validate(read_json(snapshots[version.plan_snapshot_id].payload_json))
    if (
        bill.act is None
        or (bill.term, bill.number) != (publication.term, publication.number)
        or not act_published_plan_matches(plan)
        or (
            publication.delivery is not None
            and (content_delivery_mismatch(version, snapshots, publication.delivery) is not None)
        )
    ):
        raise ValueError("act_history_frozen_plan")


def _original_delivery_owner_matches(
    connection: sqlite3.Connection,
    bill: Bill,
    publication: Publication,
    *,
    retired: bool = False,
    compact: bool = False,
) -> bool:
    if publication.number != bill.number or publication.term < bill.term:
        return False
    if publication.term == bill.term:
        return True
    if retired:
        return (
            bill.rcl is not None
            and bill.rcl.id > 0
            and bill.rcl.print_number is None
            and rcl_number(bill.rcl.id) == bill.number
        ) or (bill.wykaz is not None and wykaz_number(bill.wykaz.number) == bill.number)
    row = _query(
        connection,
        "SELECT rcl_json, wykaz_json FROM bills WHERE term=? AND number=?",
        (publication.term, publication.number),
    ).fetchone()
    if row is None:
        return False
    if row["rcl_json"] is not None and (bill.rcl is not None or (compact and bill.is_rcl)):
        current_rcl = RclProject.model_validate_json(row["rcl_json"])
        return (
            current_rcl.id > 0
            and rcl_number(current_rcl.id) == bill.number
            and (
                compact
                if bill.rcl is None
                else bill.rcl.id == current_rcl.id and bill.rcl.print_number is None
            )
        )
    if row["wykaz_json"] is not None and (bill.wykaz is not None or (compact and bill.is_wykaz)):
        current_wykaz = WykazEntry.model_validate_json(row["wykaz_json"])
        return wykaz_number(current_wykaz.number) == bill.number and (
            compact if bill.wykaz is None else current_wykaz.number == bill.wykaz.number
        )
    return False


def _retired_card_root(connection: sqlite3.Connection, identity: int) -> Publication | None:
    row = _query(
        connection,
        "SELECT r.*, b.content_id AS binding, v.root_publication_id AS root, v.version_json"
        " FROM retired_card_roots r LEFT JOIN publication_content b ON b.publication_id=r.id"
        " LEFT JOIN content_versions v ON v.content_id=r.content_id WHERE r.id=?",
        (identity,),
    ).fetchone()
    if row is None:
        return None
    if row["binding"] != row["content_id"] or row["root"] != identity:
        raise ValueError("retired_card_root_binding")
    version = ContentVersion.model_validate_json(row["version_json"])
    kind = PublicationKind(row["kind"])
    evidence = version.original_delivery_evidence
    if version.reason != "original" or (
        evidence.kind != kind
        if evidence is not None
        else kind is not PublicationKind.NEW_BILL or version.card_context_profile is None
    ):
        raise ValueError("retired_card_root_profile")
    created = datetime.fromisoformat(row["created_at"])
    sent = datetime.fromisoformat(row["sent_at"])
    if created.utcoffset() is None or sent.utcoffset() is None or sent < created:
        raise ValueError("retired_card_root_time")
    return Publication(
        id=identity,
        term=row["term"],
        number=row["number"],
        channel_id=row["channel_id"],
        kind=kind,
        ref=row["ref"],
        created_at=created,
        status=PublicationStatus.SENT,
        sent_at=sent,
        message_id=row["message_id"],
    )


def verify_original_delivery_content(
    connection: sqlite3.Connection, version: ContentVersion, *, require_bound: bool = False
) -> OriginalDeliveryPlan | None:
    evidence = version.original_delivery_evidence
    if evidence is None:
        return None
    reader = ContentSourceRows(connection)
    bill_snapshot = reader.get_content_snapshot(version.bill_context_id)
    snapshot = reader.get_content_snapshot(version.plan_snapshot_id)
    if (
        bill_snapshot is None
        or bill_snapshot.kind != "bill"
        or snapshot is None
        or snapshot.kind != "plan"
        or len(bill_snapshot.payload_json.encode()) + len(snapshot.payload_json.encode())
        > MAX_ORIGINAL_DELIVERY_BYTES
    ):
        raise ValueError("original_delivery_snapshot_scope")
    plan = OriginalDeliveryPlan.model_validate(read_json(snapshot.payload_json))
    plan.validate_original(evidence.kind)
    if plan.bindings[0] != version.root_publication_id:
        raise ValueError("original_delivery_root_scope")
    if require_bound:
        bindings = _query(
            connection,
            "SELECT publication_id FROM publication_content WHERE content_id=?",
            (version.content_id,),
        ).fetchall()
        if {binding[0] for binding in bindings} != set(plan.bindings):
            raise ValueError("original_delivery_binding_scope")
    review_notice = evidence.kind in REVIEW_NOTICE_KINDS
    context = original_bill_context(
        bill_snapshot, packed=bool(evidence.packed_bill), review_notice=review_notice
    )
    bill = Bill.model_validate(read_json(context.payload_json))
    if review_notice:
        review = validate_review_notice(bill, evidence.kind, evidence.ref)
        application_id = evidence.commission_review_application_id or evidence.review_application_id
        if application_id is not None and application_id != direct_review_application_id(
            connection, bill, review, evidence.kind, recorded_at=version.recorded_at
        ):
            raise ValueError("original_delivery_review_application_mismatch")
    if (
        evidence.joint_comparison_application_id is not None
        and evidence.joint_comparison_application_id
        != joint_comparison_application_id(connection, bill, recorded_at=version.recorded_at)
    ):
        raise ValueError("original_delivery_joint_application_mismatch")
    if evidence.kind is PublicationKind.ACT_PUBLISHED and bill.act is None:
        raise ValueError("original_delivery_act_missing")
    primary_context = None
    joint = evidence.joint_primary
    if joint is not None:
        primary_snapshot = reader.get_content_snapshot(joint.bill_snapshot_id)
        primary = _stage_anchor(connection, joint.publication_id, allow_retired=False)
        if primary is None and (
            require_bound
            or _query(
                connection,
                "SELECT 1 FROM publication_content WHERE publication_id=? AND content_id=?",
                (version.root_publication_id, version.content_id),
            ).fetchone()
            is not None
        ):
            primary = _stage_anchor(connection, joint.publication_id)
            if primary is not None and primary.sent_at > version.recorded_at:
                raise ValueError("joint_history_primary_receipt")
        if (
            primary_snapshot is None
            or primary_snapshot.kind != "bill"
            or primary is None
            or len(primary_snapshot.payload_json.encode())
            + len(bill_snapshot.payload_json.encode())
            + len(snapshot.payload_json.encode())
            > MAX_ORIGINAL_DELIVERY_BYTES
        ):
            raise ValueError("joint_history_primary_snapshot")
        primary_context = original_bill_context(primary_snapshot, packed=bool(joint.packed_bill))
        primary_bill = Bill.model_validate(read_json(primary_context.payload_json))
        if (
            primary.message_id != plan.root.reply_to
            or primary.channel_id != evidence.channel_id
            or (primary.term, primary.number) != (primary_bill.term, primary_bill.number)
            or primary.term != bill.term
            or primary.number == bill.number
        ):
            raise ValueError("joint_history_primary_receipt")

    parent: Publication | None = None
    retired_root = False
    for index, part in enumerate(plan.parts, 1):
        row = _query(connection, "SELECT * FROM publications WHERE id=?", (part.id,)).fetchone()
        publication = (
            read_content_publication(row)
            if row is not None
            else _retired_card_root(connection, part.id)
            if parent is None
            and evidence.kind in (PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL)
            else None
        )
        if publication is None:
            raise ValueError("original_delivery_part_missing")
        if parent is None:
            retired_root = row is None
        retained = publication.delivery
        if (
            publication.channel_id != evidence.channel_id
            or (
                not _original_delivery_owner_matches(
                    connection, bill, publication, retired=retired_root
                )
                if parent is None
                else (
                    publication.term < parent.term
                    or not _original_delivery_owner_matches(connection, bill, publication)
                    if retired_root
                    else (publication.term, publication.number) != (parent.term, parent.number)
                )
            )
            or publication.status_change_id is not None
            or (
                publication.status is PublicationStatus.SENT
                and (publication.message_id is None or publication.message_id <= 0)
            )
            or (retained is None and publication.status is not PublicationStatus.SENT)
        ):
            raise ValueError("original_delivery_part_scope")
        if parent is None:
            if (
                publication.kind != evidence.kind
                or publication.ref != evidence.ref
                or publication.created_at != version.recorded_at
                or (
                    retained is not None
                    and (
                        original_delivery_bill_snapshot(
                            retained.bill_json, review_notice=review_notice
                        )
                        != context
                        or retained.rendered_text != part.html
                        or original_delivery_context(retained, joint=joint is not None) != plan.root
                        or (
                            primary_context is not None
                            and (
                                not retained.primary_json
                                or original_delivery_bill_snapshot(retained.primary_json)
                                != primary_context
                            )
                        )
                    )
                )
            ):
                raise ValueError("original_delivery_frozen_root")
            if version.card_primary_evidence is not None:
                try:
                    _verify_card_primary_evidence(
                        connection,
                        version.card_primary_evidence,
                        version,
                        bill,
                        publication,
                        original_owner_verified=True,
                    )
                except CardPrimaryEvidenceError:
                    raise
                except ValueError as exc:
                    raise CardPrimaryEvidenceError("invalid_card_primary_evidence") from exc
        else:
            _verify_original_delivery_child(
                version, part, index, publication, parent, bill, review_notice=review_notice
            )
        parent = publication
    return plan


def _verify_original_delivery_child(
    version: ContentVersion,
    part: OriginalDeliveryPart,
    index: int,
    publication: Publication,
    parent: Publication,
    bill: Bill,
    *,
    review_notice: bool = False,
) -> None:
    retained = publication.delivery
    attempted = publication.status in (
        PublicationStatus.SENT,
        PublicationStatus.PENDING,
        PublicationStatus.UNKNOWN,
        PublicationStatus.FAILED,
    )
    if (
        publication.kind is not PublicationKind.MESSAGE_PART
        or publication.ref != f"message/{version.root_publication_id}/{index}"
        or (
            attempted and (parent.status is not PublicationStatus.SENT or parent.message_id is None)
        )
    ):
        raise ValueError("original_delivery_child_scope")
    if retained is None:
        return
    child_bill = (
        original_delivery_bill_snapshot(retained.bill_json, review_notice=True).payload_json
        if review_notice
        else retained.bill_json
    )
    saved_bill = Bill.model_validate(read_json(child_bill))
    expected_bill = Bill.model_validate_json(continuation_snapshot(bill))
    if (
        ((attempted or retained.reply_to is not None) and retained.reply_to != parent.message_id)
        or saved_bill.model_copy(update={"ready_analysis": None, "review_work": ()})
        != expected_bill
        or retained
        != DeliveryPlan(
            bill_json=retained.bill_json,
            rendered_text=part.html,
            continuation_parent=parent.id,
            continuation_root=version.root_publication_id,
            continuation_index=index,
            policy_version="attention-v1",
            held_change_ids=part.held_change_ids,
            disable_notification=part.disable_notification,
            reply_to=retained.reply_to,
        )
    ):
        raise ValueError("original_delivery_parent_receipt")


def _multipart_card_plan(
    connection: sqlite3.Connection, version: ContentVersion
) -> MultipartCardPlan:
    snapshot = ContentSourceRows(connection).get_content_snapshot(version.plan_snapshot_id)
    if snapshot is None or snapshot.kind != "plan":
        raise ValueError("multipart_card_plan_missing")
    if len(snapshot.payload_json.encode()) > MAX_MULTIPART_CARD_BYTES:
        raise ValueError("multipart_card_budget")
    return MultipartCardPlan.model_validate(read_json(snapshot.payload_json))


def _edition_bindings(connection: sqlite3.Connection, content_id: str) -> set[int]:
    return {
        row[0]
        for row in _query(
            connection,
            "SELECT publication_id FROM publication_content WHERE content_id=?",
            (content_id,),
        ).fetchall()
    }


def _edition_publication(
    connection: sqlite3.Connection, identity: int, *, retired_root: bool = False
) -> Publication:
    row = _query(connection, "SELECT * FROM publications WHERE id=?", (identity,)).fetchone()
    if row is None:
        retired = _retired_card_root(connection, identity) if retired_root else None
        if retired is not None:
            return retired
        raise ValueError("multipart_card_publication_missing")
    return read_content_publication(row)


def verify_multipart_card_revision(
    connection: sqlite3.Connection, version: ContentVersion, *, require_bound: bool = False
) -> MultipartCardPlan | None:
    if version.multipart_card_revision is None:
        return None
    plan = _multipart_card_plan(connection, version)
    owner = _edition_publication(connection, version.root_publication_id, retired_root=True)
    retired = (
        _query(
            connection, "SELECT 1 FROM publications WHERE id=?", (version.root_publication_id,)
        ).fetchone()
        is None
    )
    if (
        owner.kind is not PublicationKind.NEW_BILL
        or owner.status is not PublicationStatus.SENT
        or owner.message_id is None
        or owner.message_id <= 0
        or owner.channel_id != plan.channel_id
        or not plan.ref_prefix.startswith(f"card/{owner.id}/")
        or not plan.ref_prefix.endswith(f"-{plan.predecessor_slots[0]}")
    ):
        raise ValueError("multipart_card_owner_scope")
    previous_row = _query(
        connection,
        "SELECT * FROM content_versions WHERE content_id=?",
        (version.previous_version_id,),
    ).fetchone()
    if previous_row is None:
        raise ValueError("multipart_card_predecessor_missing")
    previous = read_content_version(previous_row)
    if previous.root_publication_id != version.root_publication_id:
        raise ValueError("multipart_card_predecessor_owner")
    if previous.multipart_card_revision is not None:
        expected = _multipart_card_plan(connection, previous).bindings
    elif previous.original_delivery_evidence is not None:
        original = verify_original_delivery_content(connection, previous, require_bound=True)
        if (
            original is None
            or previous.original_delivery_evidence.kind is not PublicationKind.NEW_BILL
        ):
            raise ValueError("multipart_card_predecessor_profile")
        expected = original.bindings
    else:
        identity = verify_card_content(connection, previous, retained=True)
        if (
            identity is None
            and previous.previous_version_id is None
            and _retired_card_root(connection, version.root_publication_id) is not None
        ):
            identity = version.root_publication_id
        if identity is None:
            raise ValueError("multipart_card_predecessor_profile")
        expected = (identity,)
    if expected != plan.predecessor_slots or _edition_bindings(
        connection, previous.content_id
    ) != set(expected):
        raise ValueError("multipart_card_predecessor_binding")
    predecessors = [
        _edition_publication(connection, identity, retired_root=identity == owner.id)
        for identity in expected
    ]
    targets = tuple(row.message_id for row in predecessors)
    if (
        any(row.status is not PublicationStatus.SENT for row in predecessors)
        or targets[0] != owner.message_id
        or None in targets
        or len(set(targets)) != len(targets)
        or targets != tuple(slot.edit_message_id for slot in plan.slots[: len(targets)])
    ):
        raise ValueError("multipart_card_predecessor_receipts")
    reader = ContentSourceRows(connection)
    bill = reader.get_content_snapshot(version.bill_context_id)
    snapshot = reader.get_content_snapshot(version.plan_snapshot_id)
    if (
        bill is None
        or snapshot is None
        or len(bill.payload_json.encode()) + len(snapshot.payload_json.encode())
        > MAX_MULTIPART_CARD_BYTES
    ):
        raise ValueError("multipart_card_semantic_scope")
    context = original_bill_context(
        bill, packed=version.multipart_card_revision == "card-multipart-revision-packed-v1"
    )
    semantic = Bill.model_validate(read_json(context.payload_json))
    if not _original_delivery_owner_matches(connection, semantic, owner, retired=retired):
        raise ValueError("multipart_card_semantic_owner")
    if require_bound and _edition_bindings(connection, version.content_id) != set(plan.bindings):
        raise ValueError("multipart_card_binding_scope")
    compact = card_bill_snapshot(context.payload_json)
    parent = owner
    for index, slot in enumerate(plan.slots, 1):
        row = _edition_publication(connection, slot.id)
        attempted = row.status in (
            PublicationStatus.SENT,
            PublicationStatus.PENDING,
            PublicationStatus.UNKNOWN,
            PublicationStatus.FAILED,
        )
        retained = row.delivery
        if (
            row.kind is not PublicationKind.MESSAGE_PART
            or row.status_change_id is not None
            or row.channel_id != owner.channel_id
            or (
                row.term < owner.term
                or not _original_delivery_owner_matches(connection, semantic, row)
                if retired
                else (row.term, row.number) != (owner.term, owner.number)
            )
            or row.ref != f"{plan.ref_prefix}/{index}"
            or (index == 1 and row.created_at != version.recorded_at)
            or (
                attempted and (parent.status is not PublicationStatus.SENT or not parent.message_id)
            )
            or (
                row.status is PublicationStatus.SENT
                and (
                    row.message_id is None
                    or row.message_id <= 0
                    or (slot.edit_message_id is not None and row.message_id != slot.edit_message_id)
                )
            )
            or (retained is None and row.status is not PublicationStatus.SENT)
        ):
            raise ValueError("multipart_card_slot_scope")
        if retained is not None and (
            card_bill_snapshot(retained.bill_json) != compact
            or (
                (index == 1 or attempted or retained.reply_to is not None)
                and retained.reply_to != parent.message_id
            )
            or retained
            != DeliveryPlan(
                bill_json=retained.bill_json,
                rendered_text=slot.html,
                edit_message_id=slot.edit_message_id,
                today=plan.today,
                revision_of=owner.id if index == 1 else None,
                continuation_parent=parent.id,
                continuation_root=plan.bindings[0] if index > 1 else None,
                continuation_index=index,
                continuation_ids=plan.bindings[1:] if index == 1 else (),
                reply_to=retained.reply_to,
                disable_notification=slot.disable_notification,
            )
        ):
            raise ValueError("multipart_card_frozen_target")
        parent = row
    if version.card_primary_evidence is not None:
        try:
            _verify_multipart_card_primary_evidence(
                connection, version, previous, predecessors, semantic, plan.slots[0].id
            )
        except CardPrimaryEvidenceError:
            raise
        except ValueError as exc:
            raise CardPrimaryEvidenceError("invalid_card_primary_evidence") from exc
    return plan


def _verify_multipart_card_primary_evidence(
    connection: sqlite3.Connection,
    version: ContentVersion,
    previous: ContentVersion,
    predecessors: list[Publication],
    semantic: Bill,
    first_slot: int,
) -> None:
    reference = version.card_primary_evidence
    assert reference is not None, "the multipart verifier only requests explicitly recorded proof"
    record = semantic.analysis
    if record is None or reference.revision_analysis_sha256 != primary_record_sha256(record):
        raise CardPrimaryEvidenceError("card_revision_primary_result_mismatch")
    snapshot = ContentSourceRows(connection).get_content_snapshot(previous.bill_context_id)
    if snapshot is None:
        raise CardPrimaryEvidenceError("card_revision_primary_predecessor")
    if (
        previous.multipart_card_revision is not None
        or previous.original_delivery_evidence is not None
    ):
        original = previous.original_delivery_evidence
        snapshot = original_bill_context(
            snapshot,
            packed=previous.multipart_card_revision == "card-multipart-revision-packed-v1"
            or bool(original is not None and original.packed_bill),
        )
    old_bill = Bill.model_validate_json(snapshot.payload_json)
    if version.recorded_at.utcoffset() is None:
        raise CardPrimaryEvidenceError("card_revision_primary_predecessor")
    timestamps: list[datetime] = []
    for predecessor in predecessors:
        sent_at = predecessor.sent_at
        if (
            (predecessor.term, predecessor.number) != (semantic.term, semantic.number)
            or sent_at is None
            or sent_at.utcoffset() is None
            or sent_at > version.recorded_at
        ):
            raise CardPrimaryEvidenceError("card_revision_primary_predecessor")
        timestamps.append(sent_at)
    if (old_bill.term, old_bill.number) != (semantic.term, semantic.number):
        raise CardPrimaryEvidenceError("card_revision_primary_predecessor")
    prior = previous.card_primary_evidence
    same_paid = (
        prior is not None
        and 0 < old_bill.analysis_generation == semantic.analysis_generation
        and prior.projection_application_id == reference.projection_application_id
        and prior.source_observation_id == reference.source_observation_id
        and prior.revision_analysis_sha256 in (None, reference.revision_analysis_sha256)
    )
    if not same_paid and not 0 < old_bill.analysis_generation < semantic.analysis_generation:
        raise CardPrimaryEvidenceError("card_revision_primary_predecessor")
    _verify_card_primary_evidence(
        connection,
        reference,
        version,
        semantic,
        _edition_publication(connection, first_slot),
        revision_analysis_sha256=reference.revision_analysis_sha256,
        projection_after=None if same_paid else max(timestamps),
        compact_revision=True,
    )


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
            verify_direct_stage_content(connection, value, require_bound=True)
            verify_grouped_stage_content(connection, value, require_bound=True)
            verify_digest_content(connection, value, require_bound=True)
            verify_sitting_content(connection, value, require_bound=True)
            verify_act_published_content(connection, value, require_bound=True)
            verify_original_delivery_content(connection, value, require_bound=True)
            verify_multipart_card_revision(connection, value, require_bound=True)
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
        if "retired_card_roots" in tables:
            for row in _query(connection, "SELECT id FROM retired_card_roots"):
                if _query(
                    connection, "SELECT 1 FROM publications WHERE id=?", (row[0],)
                ).fetchone():
                    raise ValueError("retired_card_root_live_collision")
                _retired_card_root(connection, row[0])
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
    return read_bound_card_primary_cycle(connection, publication_id, require_expired=True)


def read_bound_card_primary_cycle(
    connection: sqlite3.Connection, publication_id: int, *, require_expired: bool = False
) -> dict[str, object] | None:
    """Read an original card's validated evidence and compare any retained delivery plan."""
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
        or require_expired
        and root.delivery is not None
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


def card_refresh_rows(connection: sqlite3.Connection, card_id: int) -> list[sqlite3.Row]:
    root = _query(
        connection,
        "SELECT ref FROM publications WHERE kind = 'message_part' AND ref LIKE ?"
        " ORDER BY id DESC LIMIT 1",
        (f"card/{card_id}/%/1",),
    ).fetchone()
    if root is None:
        card = _query(connection, "SELECT * FROM publications WHERE id=?", (card_id,)).fetchone()
        children = _query(
            connection,
            "SELECT * FROM publications WHERE kind = 'message_part' AND ref LIKE ? ORDER BY id",
            (f"message/{card_id}/%",),
        ).fetchall()
        return ([card] if card is not None else []) + children
    return _query(
        connection,
        "SELECT * FROM publications WHERE kind = 'message_part' AND ref LIKE ? ORDER BY id",
        (f"{root['ref'].rsplit('/', 1)[0]}/%",),
    ).fetchall()
