"""Inspect a retained direct primary card's source, paid computation and recorded send."""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

from pydantic import TypeAdapter

from lexinform.adapters.computation_history import read_result_application
from lexinform.adapters.content_history import read_expired_card_primary_cycle
from lexinform.adapters.content_source_refs import ContentSourceRows, validate_projected_application
from lexinform.adapters.document_source_rows import DocumentSourceRows
from lexinform.adapters.input_receipts import (
    retained_pdf_page_ranges,
    validate_input_snapshots,
    validate_secondary_receipt_parents,
)
from lexinform.models import (
    AmendmentsRecord,
    BatchItemMeta,
    BatchResult,
    Bill,
    ComputationResult,
    DeliveryPlan,
    DigestItemMeta,
    PrimaryInputReceipt,
    ResultApplication,
    ResultApplicationTarget,
    StatusChange,
    SupplementRecord,
    TokenUsage,
)
from lexinform.models.batch import batch_item_meta
from lexinform.models.primary_projections import PRIMARY_USAGE_FIELDS
from lexinform.primary_projections import primary_record_matches
from tools.corpus_artifacts import write_json
from tools.sqlite_snapshot import open_snapshot

_SUPPLEMENTS = TypeAdapter(list[SupplementRecord])


class CycleNotObservedError(ValueError):
    pass


def _application(
    db: sqlite3.Connection, paid: ComputationResult, phase: str
) -> tuple[int, ResultApplication]:
    rows = db.execute(
        "SELECT * FROM result_applications WHERE result_id=? AND term=? AND number=?"
        " AND generation=? AND phase=? LIMIT 2",
        (paid.result_id, paid.term, paid.number, paid.generation, phase),
    ).fetchall()
    if len(rows) != 1:
        raise CycleNotObservedError("unique_" + phase + "_application_unavailable")
    row = rows[0]
    value = read_result_application(row)
    if value.target is not None:
        raise ValueError("application columns differ from the retained primary receipt")
    return row["id"], value


def _sent_plan(row: sqlite3.Row) -> DeliveryPlan:
    if (
        row["status"] != "sent"
        or type(row["message_id"]) is not int
        or row["message_id"] <= 0
        or not row["channel_id"]
        or not row["sent_at"]
    ):
        raise CycleNotObservedError("recorded_send_unavailable")
    if row["delivery_json"] is None:
        raise CycleNotObservedError("frozen_plan_unavailable")
    return DeliveryPlan.model_validate_json(row["delivery_json"])


def _outside_direct_original(plan: DeliveryPlan) -> bool:
    return bool(
        plan.destination != "direct"
        or plan.publication_ids
        or plan.continuation_ids
        or plan.continuation_root is not None
        or plan.continuation_parent is not None
        or plan.continuation_index != 1
        or plan.absorbed_by is not None
        or plan.update_group
        or plan.sitting_group
        or plan.digest_parts
        or plan.digest_ref
        or plan.digest_event_ids
        or plan.revision_of is not None
        or plan.edit_message_id is not None
        or plan.primary_json
    )


def _direct_plan(row: sqlite3.Row) -> DeliveryPlan:
    if row["kind"] != "new_bill":
        raise CycleNotObservedError("publication_outside_primary_card_scope")
    plan = _sent_plan(row)
    if _outside_direct_original(plan) or plan.reply_to is not None:
        raise CycleNotObservedError("publication_outside_direct_original_scope")
    if not plan.bill_json or not plan.rendered_text:
        raise CycleNotObservedError("frozen_render_context_unavailable")
    return plan


def _inspect(db: sqlite3.Connection, publication_id: int) -> dict[str, object]:
    row = db.execute("SELECT * FROM publications WHERE id=?", (publication_id,)).fetchone()
    if row is None:
        raise CycleNotObservedError("publication_unavailable")
    plan = _direct_plan(row)
    aliases = db.execute(
        "SELECT count(*) FROM publications WHERE kind='new_bill' AND channel_id=? AND message_id=?",
        (row["channel_id"], row["message_id"]),
    ).fetchone()[0]
    if aliases != 1:
        raise CycleNotObservedError("physical_root_ambiguous")
    bill = Bill.model_validate_json(plan.bill_json)
    if (bill.term, bill.number) != (row["term"], row["number"]):
        raise ValueError("frozen bill differs from publication owner")
    record = bill.analysis
    if record is None or record.computation_id is None:
        raise CycleNotObservedError("frozen_paid_reference_unavailable")
    reader = ContentSourceRows(db)
    paid = reader.get_computation_result(record.computation_id)
    if paid is None:
        raise CycleNotObservedError("paid_result_unavailable")
    if (paid.term, paid.number) != (bill.term, bill.number):
        raise CycleNotObservedError("outside_same_owner_scope")
    if not primary_record_matches(record, paid) or paid.record is None:
        raise ValueError("frozen analysis differs from the paid record")
    if record.model_dump(exclude=set(PRIMARY_USAGE_FIELDS)) != paid.record.model_dump(
        exclude=set(PRIMARY_USAGE_FIELDS)
    ):
        raise CycleNotObservedError("frozen_analysis_is_not_original_paid_record")
    if paid.input_binding is None or paid.request_sha256 is None:
        raise CycleNotObservedError("paid_input_binding_unavailable")
    if (paid.input_binding.term, paid.input_binding.number) != (paid.term, paid.number):
        raise CycleNotObservedError("outside_same_owner_input_scope")
    item = reader.get_consumed_primary_input(
        paid.provider, paid.batch_id, paid.custom_id, bill.analysis_generation
    )
    if item is None or item.consumed_at is None:
        raise CycleNotObservedError("consumed_projection_unavailable")
    if (item.term, item.number, item.call_kind) != (paid.term, paid.number, paid.purpose):
        raise ValueError("consumed item differs from its paid input owner")
    meta = item.meta
    if not isinstance(meta, BatchItemMeta) or meta.prepared_input_receipt is None:
        raise CycleNotObservedError("prepared_receipt_unavailable")
    receipt = meta.prepared_input_receipt
    if not isinstance(receipt, PrimaryInputReceipt):
        raise CycleNotObservedError("receipt_outside_single_part_scope")
    if receipt.source_selection_id is None:
        raise CycleNotObservedError("receipt_source_selection_unavailable")
    if (
        meta.input_binding != paid.input_binding
        or meta.generation != paid.generation
        or meta.request_sha256 != paid.request_sha256
        or receipt.request_sha256 != paid.request_sha256
        or meta.input_chars != receipt.text_chars
        or record.input_chars != receipt.text_chars
    ):
        raise ValueError("consumed item differs from its exact paid request")
    validate_input_snapshots(db)
    sources = reader.source_observations_for_selection(
        bill.term, bill.number, receipt.source_selection_id
    )
    if len(sources) != 1:
        raise CycleNotObservedError("unique_source_observation_unavailable")
    source = sources[0]
    if (source.document.kind, source.document.url) != (meta.source_kind, meta.source_url):
        raise CycleNotObservedError("frozen_source_context_unavailable")
    collection_id, collection = _application(db, paid, "collection")
    projection_id, projection = _application(db, paid, "projection")
    validate_projected_application(db, projection)
    generation = projection.projected_generation
    if (
        collection.outcome != "ready"
        or collection.reason != "current_generation"
        or projection.reason != "batch_result"
        or projection.outcome != "applied"
        or (projection.generation if generation is None else generation) != bill.analysis_generation
    ):
        raise CycleNotObservedError("fresh_primary_application_unavailable")
    queued = meta.queued_at
    batch = db.execute(
        "SELECT submitted_at FROM llm_batches WHERE batch_id=? AND provider=?",
        (paid.batch_id, paid.provider),
    ).fetchone()
    if queued is None or batch is None or batch["submitted_at"] is None:
        raise CycleNotObservedError("recorded_batch_chronology_unavailable")
    sent_at = datetime.fromisoformat(row["sent_at"])
    dates = (
        source.recorded_at,
        queued,
        datetime.fromisoformat(batch["submitted_at"]),
        paid.collected_at,
        collection.created_at,
        projection.created_at,
        datetime.fromisoformat(row["created_at"]),
        sent_at,
    )
    if any(date.utcoffset() is None for date in (*dates, item.consumed_at)):
        raise CycleNotObservedError("aware_batch_chronology_unavailable")
    if (
        dates != tuple(sorted(dates))
        or not paid.collected_at <= item.consumed_at <= projection.created_at
    ):
        raise ValueError("recorded source, computation and delivery chronology disagree")
    ranges = retained_pdf_page_ranges(db, receipt)
    return {
        **({"normalized_pdf_page_ranges": ranges} if ranges else {}),
        "source_kind": source.document.kind,
        "source_observation_id": source.observation_id,
        "source_selection_id": receipt.source_selection_id,
        "artifact_sha256": receipt.artifact_sha256,
        "extraction_id": receipt.extraction_id,
        "purpose": paid.purpose,
        "result_id": paid.result_id,
        "request_sha256": paid.request_sha256,
        "collection_application_id": collection_id,
        "projection_application_id": projection_id,
        "recorded_sent_at": sent_at.isoformat(),
        "frozen_render_sha256": hashlib.sha256(plan.rendered_text.encode()).hexdigest(),
    }


def _secondary_suffix(
    db: sqlite3.Connection,
    publication_id: int,
    application_id: int,
    *,
    include_request_prefix: bool = False,
) -> dict[str, object]:
    row = db.execute("SELECT * FROM publications WHERE id=?", (publication_id,)).fetchone()
    if row is None:
        raise CycleNotObservedError("publication_unavailable")
    if row["kind"] != "status_update":
        raise CycleNotObservedError("publication_outside_secondary_status_scope")
    plan = _sent_plan(row)
    if (
        _outside_direct_original(plan)
        or json.loads(row["document_message_ids"]) != []
        or db.execute(
            "SELECT 1 FROM digest_event_links WHERE source_publication_id=?", (publication_id,)
        ).fetchone()
        or db.execute(
            "SELECT 1 FROM delivery_resolutions WHERE publication_id=?", (publication_id,)
        ).fetchone()
    ):
        raise CycleNotObservedError("publication_outside_direct_status_scope")
    if (
        not plan.rendered_text
        or not plan.change_json
        or not plan.bill_json
        or row["status_change_id"] is None
    ):
        raise CycleNotObservedError("frozen_change_unavailable")
    if (
        db.execute(
            "SELECT count(*) FROM publications WHERE channel_id=? AND message_id=?",
            (row["channel_id"], row["message_id"]),
        ).fetchone()[0]
        != 1
    ):
        raise CycleNotObservedError("physical_root_ambiguous")

    change = StatusChange.model_validate_json(plan.change_json)
    owner = (row["term"], row["number"])
    bill = Bill.model_validate_json(plan.bill_json)
    if (bill.term, bill.number) != owner:
        raise ValueError("frozen bill differs from its publication")
    if change.id != row["status_change_id"] or (change.term, change.number) != owner:
        raise ValueError("frozen change differs from its publication")
    stored = db.execute("SELECT * FROM status_changes WHERE id=?", (change.id,)).fetchone()
    if stored is None:
        raise CycleNotObservedError("stored_change_unavailable")
    if (stored["term"], stored["number"], stored["new_fingerprint"]) != (
        change.term,
        change.number,
        change.new_fingerprint,
    ):
        raise ValueError("stored change differs from the frozen event")

    application_row = db.execute(
        "SELECT * FROM result_applications WHERE id=?", (application_id,)
    ).fetchone()
    if application_row is None:
        raise CycleNotObservedError("typed_application_unavailable")
    application = read_result_application(application_row)
    if (
        application.target != ResultApplicationTarget(kind="status_change", ref=str(change.id))
        or (application.term, application.number) != owner
        or application.phase != "projection"
        or application.outcome != "applied"
        or application.reason != "retained_result"
    ):
        raise CycleNotObservedError("application_outside_exact_status_target")
    paid = ContentSourceRows(db).get_computation_result(application.result_id)
    if paid is None:
        raise CycleNotObservedError("paid_result_unavailable")
    if (paid.term, paid.number) != owner:
        raise ValueError("paid result differs from the application owner")
    frozen, persisted = _secondary_records(change, stored, paid)
    if len(frozen) != 1 or len(persisted) != 1:
        raise CycleNotObservedError("unique_secondary_record_unavailable")
    if frozen[0] != persisted[0]:
        raise ValueError("frozen secondary record differs from the stored projection")
    usage = {
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    }
    if paid.record is None or frozen[0].model_dump(exclude=usage) != paid.record.model_dump(
        exclude=usage
    ):
        raise ValueError("frozen secondary record differs from the paid record")
    sent_at = datetime.fromisoformat(row["sent_at"])
    dates = (
        paid.collected_at,
        application.created_at,
        datetime.fromisoformat(row["created_at"]),
        sent_at,
    )
    if any(date.utcoffset() is None for date in dates) or dates != tuple(sorted(dates)):
        raise ValueError("recorded secondary chronology disagrees")
    suffix = {
        "purpose": paid.purpose,
        "result_id": paid.result_id,
        "application_id": application_id,
        "status_change_id": change.id,
        "recorded_sent_at": sent_at.isoformat(),
        "frozen_render_sha256": hashlib.sha256(plan.rendered_text.encode()).hexdigest(),
    }
    if include_request_prefix:
        try:
            suffix["request_prefix"] = _secondary_request_prefix(
                db, paid, application.created_at, dates[2], sent_at
            )
        except CycleNotObservedError as exc:
            suffix["request_prefix"] = {"status": "not_observed", "reason": str(exc)}
        except ValueError, TypeError, RecursionError, KeyError, IndexError, sqlite3.Error:
            suffix["request_prefix"] = {"status": "invalid", "reason": "invalid_snapshot_evidence"}
    return suffix


def _secondary_request_prefix(
    db: sqlite3.Connection,
    paid: ComputationResult,
    applied: datetime,
    publication_created: datetime,
    sent: datetime,
) -> dict[str, object]:
    if paid.request_sha256 is None or paid.input_binding is None:
        raise CycleNotObservedError("original_secondary_request_binding_unavailable")
    row = db.execute(
        "SELECT i.*, b.provider, b.call_kind AS batch_kind, b.submitted_at"
        ", b.status AS batch_status"
        " FROM llm_batch_items i JOIN llm_batches b USING (batch_id)"
        " WHERE i.batch_id=? AND i.custom_id=?",
        (paid.batch_id, paid.custom_id),
    ).fetchone()
    if row is None or not row["consumed_at"]:
        raise CycleNotObservedError("original_consumed_secondary_item_unavailable")
    if (
        paid.validation != "validated"
        or row["batch_status"] != "ended"
        or (row["provider"], row["term"], row["number"], row["call_kind"], row["batch_kind"])
        != (paid.provider, paid.term, paid.number, paid.purpose, paid.purpose)
        or (paid.input_binding.term, paid.input_binding.number) != (paid.term, paid.number)
    ):
        raise ValueError("Submitted secondary item differs from its exact paid owner")
    meta = batch_item_meta(paid.purpose, row["meta_json"])
    if not isinstance(meta, DigestItemMeta):
        raise ValueError("Expected the original submitted secondary metadata")
    if meta.request_sha256 is None or meta.input_binding is None:
        raise CycleNotObservedError("submitted_secondary_request_binding_unavailable")
    if (meta.request_sha256, meta.input_binding) != (paid.request_sha256, paid.input_binding):
        raise ValueError("Submitted secondary item differs from its exact paid request")
    if meta.queued_at is None or not row["submitted_at"]:
        raise CycleNotObservedError("original_secondary_batch_chronology_unavailable")
    submitted = datetime.fromisoformat(row["submitted_at"])
    consumed = datetime.fromisoformat(row["consumed_at"])
    dates = (
        meta.queued_at,
        submitted,
        paid.collected_at,
        consumed,
        applied,
        publication_created,
        sent,
    )
    if any(date.utcoffset() is None for date in dates) or dates != tuple(sorted(dates)):
        raise ValueError("Original secondary request and delivery chronology disagree")
    collection = db.execute(
        "SELECT 1 FROM result_applications WHERE result_id=? AND phase='collection'",
        (paid.result_id,),
    ).fetchone()
    receipt, source = _secondary_prepared_source(db, paid, meta, submitted)
    manifest = meta.frozen_request_manifest
    if manifest is not None and manifest.provider != paid.provider:
        raise ValueError("Frozen request manifest differs from the paid provider")
    answer = _retained_provider_answer(paid, row["result_json"])
    return {
        "status": "recorded",
        "reason": "exact_submitted_secondary_request_identity",
        "request_sha256": paid.request_sha256,
        "queued_at": meta.queued_at.isoformat(),
        "submitted_at": submitted.isoformat(),
        "collected_at": paid.collected_at.isoformat(),
        "consumed_at": consumed.isoformat(),
        "original_collection_application": "not_retained"
        if collection is None
        else "outside_scope",
        "prepared_input_receipt": receipt,
        "original_request_payload": "not_verified",
        "source_selection_binding": source,
        "original_amendment_question": _retained_amendment_question(paid, meta),
        "retained_provider_request_manifest": (
            "verified_retained_manifest" if manifest is not None else "not_retained"
        ),
        "retained_provider_answer": answer,
        "retained_collection_checkpoint": (
            "matching_retained_checkpoint"
            if paid.collected_at == consumed and answer == "exact_retained_answer"
            else "not_verified"
        ),
    }


def _retained_provider_answer(paid: ComputationResult, raw: str | None) -> str:
    if raw is None:
        return "not_retained"
    result = BatchResult.model_validate_json(raw)
    if result.custom_id != paid.custom_id:
        raise ValueError("Retained provider answer differs from its submitted item")
    if result.answer is None:
        return "not_retained"

    record = paid.record
    if isinstance(record, AmendmentsRecord):
        answer_matches = result.answer == record.amendments
    elif isinstance(record, SupplementRecord):
        answer_matches = result.answer == record.digest
    else:
        raise ValueError("Retained provider answer has no supported paid result")
    usage = TokenUsage(
        batch_input=result.input_tokens or 0,
        batch_output=result.output_tokens or 0,
        batch_cache_read=result.cache_read_input_tokens or 0,
        batch_cache_creation=result.cache_creation_input_tokens or 0,
    )
    if (
        result.error is not None
        or result.context_exceeded
        or not answer_matches
        or result.model != paid.model
        or usage != paid.usage
    ):
        raise ValueError("Retained provider answer differs from the paid result")
    return "exact_retained_answer"


def _retained_amendment_question(paid: ComputationResult, meta: DigestItemMeta) -> str:
    if paid.purpose != "amendments":
        return "outside_scope"
    if (
        paid.amendment_input_ref != meta.amendment_input_ref
        or paid.amendment_scan_input != meta.amendment_scan_input
    ):
        raise ValueError("Submitted amendment question differs from the paid result")
    if paid.amendment_input_ref is None and paid.amendment_scan_input is None:
        return "not_retained"
    if paid.amendment_input_ref is not None:
        return "exact_retained_question"
    return "requires_exact_scan"


def _secondary_prepared_source(
    db: sqlite3.Connection, paid: ComputationResult, meta: DigestItemMeta, submitted: datetime
) -> tuple[str, str]:
    receipt = meta.prepared_input_receipt
    if receipt is None:
        return "not_retained", "not_verified"
    validate_secondary_receipt_parents(db, receipt)
    if (
        receipt.source_selection_id is None
        or receipt.artifact_sha256 is None
        or receipt.extraction_id is None
    ):
        return "validated_retained_receipt", "not_verified"
    observations = DocumentSourceRows(db).source_observations_for_selection(
        paid.term, paid.number, receipt.source_selection_id
    )
    if len(observations) != 1:
        return "validated_retained_receipt", "not_verified"
    observation = observations[0]
    if (
        not isinstance(paid.record, (AmendmentsRecord, SupplementRecord))
        or (observation.document.url, observation.document.kind)
        != (meta.source_url, meta.source_kind)
        or (paid.record.source_url, paid.record.source_kind) != (meta.source_url, meta.source_kind)
        or observation.recorded_at > submitted
    ):
        raise ValueError("Prepared secondary source differs from its retained observation")
    return "validated_retained_receipt", "exact_retained_source_selection"


def _secondary_records(
    change: StatusChange, stored: sqlite3.Row, paid: ComputationResult
) -> tuple[list[AmendmentsRecord | SupplementRecord], list[AmendmentsRecord | SupplementRecord]]:
    if paid.purpose == "amendments":
        if change.amendments is None or stored["amendments_json"] is None:
            return [], []
        persisted = AmendmentsRecord.model_validate_json(stored["amendments_json"])
        return (
            [change.amendments] if change.amendments.computation_id == paid.result_id else [],
            [persisted] if persisted.computation_id == paid.result_id else [],
        )
    if paid.purpose == "supplement":
        persisted_supplements = _SUPPLEMENTS.validate_json(stored["supplements_json"] or "[]")
        return (
            [record for record in change.supplements if record.computation_id == paid.result_id],
            [record for record in persisted_supplements if record.computation_id == paid.result_id],
        )
    raise CycleNotObservedError("result_outside_secondary_status_scope")


def _inspect_card(
    db: sqlite3.Connection, publication_id: int, *, include_compact: bool
) -> tuple[str, dict[str, object]]:
    try:
        return "exact_retained_cycle", _inspect(db, publication_id)
    except CycleNotObservedError as exc:
        if include_compact and str(exc) == "frozen_plan_unavailable":
            compact = read_expired_card_primary_cycle(db, publication_id)
            if compact is not None:
                return "exact_retained_compact_cycle", compact
        raise


def _observe(
    db: sqlite3.Connection,
    publication_id: int,
    *,
    secondary_application_id: int | None = None,
    include_compact: bool = False,
    include_request_prefix: bool = False,
) -> tuple[str, str, dict[str, object] | None]:
    cycle: dict[str, object] | None = None
    status, reason = "recorded", "exact_retained_cycle"
    try:
        if secondary_application_id is None:
            reason, cycle = _inspect_card(db, publication_id, include_compact=include_compact)
        else:
            reason = "exact_retained_secondary_suffix"
            cycle = _secondary_suffix(
                db,
                publication_id,
                secondary_application_id,
                include_request_prefix=include_request_prefix,
            )
    except CycleNotObservedError as exc:
        status, reason = "not_observed", str(exc)
    except ValueError, TypeError, RecursionError, KeyError, IndexError, sqlite3.Error:
        status, reason = "invalid", "invalid_snapshot_evidence"
    return status, reason, cycle


def audit(
    database: Path,
    publication_id: int,
    *,
    secondary_application_id: int | None = None,
    include_compact: bool = False,
    include_request_prefix: bool = False,
) -> dict[str, object]:
    if not 0 < publication_id < 2**63:
        raise ValueError("Publication ID must be a positive SQLite integer")
    if secondary_application_id is not None and not 0 < secondary_application_id < 2**63:
        raise ValueError("Application ID must be a positive SQLite integer")
    if include_request_prefix and secondary_application_id is None:
        raise ValueError("Request identity requires a secondary application")
    if include_compact and secondary_application_id is not None:
        raise ValueError("Compact and secondary evidence modes are mutually exclusive")
    with open_snapshot(database) as snapshot:
        status, reason, cycle = _observe(
            snapshot.connection,
            publication_id,
            secondary_application_id=secondary_application_id,
            include_compact=include_compact,
            include_request_prefix=include_request_prefix,
        )
        if not snapshot.unchanged():
            status, reason, cycle = "invalid", "database_changed_during_audit", None
    if secondary_application_id is not None:
        return {
            "version": 1,
            "status": status,
            "reason": reason,
            "database_sha256": snapshot.sha256,
            "publication_id": publication_id,
            "requested_application_id": secondary_application_id,
            "scope": "One retained secondary status-change projection and recorded direct send.",
            "exclusions": [
                "Source observation and selection",
                "Current Telegram content",
                "live verification",
                "source-family acceptance",
            ],
            "suffix": cycle,
        }
    return {
        "version": 1,
        "status": status,
        "reason": reason,
        "database_sha256": snapshot.sha256,
        "publication_id": publication_id,
        "scope": "One historical database-recorded direct card send.",
        "exclusions": ["Current Telegram content", "live verification", "source-family acceptance"],
        "cycle": cycle,
    }


def census(database: Path) -> dict[str, object]:
    primary: Counter[str] = Counter()
    secondary = []
    with open_snapshot(database) as snapshot:
        db = snapshot.connection
        for (publication_id,) in db.execute(
            "SELECT id FROM publications WHERE kind='new_bill' AND status='sent'"
        ):
            _, reason, _ = _observe(db, publication_id, include_compact=True)
            primary[reason] += 1
        for publication_id, application_id in db.execute(
            "SELECT p.id, a.id FROM result_applications a JOIN publications p"
            " ON CAST(p.status_change_id AS TEXT)=json_extract(a.application_json,'$.target.ref')"
            " WHERE p.status='sent' AND a.phase='projection'"
            " AND json_extract(a.application_json,'$.target.kind')='status_change'"
            " ORDER BY p.id,a.id"
        ):
            status, reason, suffix = _observe(
                db,
                publication_id,
                secondary_application_id=application_id,
                include_request_prefix=True,
            )
            prefix = suffix.get("request_prefix") if suffix is not None else None
            secondary.append(
                {
                    "publication_id": publication_id,
                    "application_id": application_id,
                    "status": status,
                    "reason": reason,
                    "prefix": {
                        key: prefix[key]
                        for key in (
                            "status",
                            "reason",
                            "original_collection_application",
                            "prepared_input_receipt",
                            "original_request_payload",
                            "source_selection_binding",
                            "original_amendment_question",
                            "retained_provider_request_manifest",
                            "retained_provider_answer",
                            "retained_collection_checkpoint",
                        )
                        if key in prefix
                    }
                    if isinstance(prefix, dict)
                    else {},
                }
            )
        if not snapshot.unchanged():
            raise ValueError("Database changed during delivery census")
    return {
        "version": 1,
        "status": "recorded",
        "scope": "Sent new-bill rows and sent status changes with retained projections.",
        "database_sha256": snapshot.sha256,
        "primary_census": dict(primary),
        "secondary_census": secondary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--publication-id", type=int)
    target.add_argument("--census", action="store_true", help="survey a checkpointed snapshot")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--secondary-application-id", type=int)
    mode.add_argument(
        "--include-compact",
        action="store_true",
        help="allow validated compact original history after the frozen plan expires",
    )
    parser.add_argument(
        "--include-request-prefix",
        action="store_true",
        help="verify retained submitted request identity for an exact secondary status send",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        database = args.database.resolve()
        protected = {database}
        if args.census and (
            args.include_request_prefix
            or args.include_compact
            or args.secondary_application_id is not None
        ):
            raise ValueError("Census uses its own primary and secondary evidence modes")
        if args.include_request_prefix or args.census:
            protected.update(
                Path(str(database) + suffix) for suffix in ("-wal", "-shm", "-journal")
            )
        if args.output.resolve() in protected:
            raise ValueError("Report must be separate from the input database")
        report = (
            census(args.database)
            if args.census
            else audit(
                args.database,
                args.publication_id,
                secondary_application_id=args.secondary_application_id,
                include_compact=args.include_compact,
                include_request_prefix=args.include_request_prefix,
            )
        )
        write_json(args.output, report)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.error(str(exc))
    status = report["status"]
    suffix = report.get("suffix")
    if args.include_request_prefix and isinstance(suffix, dict):
        status = suffix["request_prefix"]["status"]
    raise SystemExit(0 if status == "recorded" else 1 if status == "invalid" else 2)


if __name__ == "__main__":
    main()
