"""Inspect a retained direct primary card's source, paid computation and recorded send."""

import argparse
import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

from lexinform.adapters.computation_history import read_result_application
from lexinform.adapters.content_source_refs import ContentSourceRows, validate_projected_application
from lexinform.adapters.input_receipts import validate_input_snapshots
from lexinform.models import (
    BatchItemMeta,
    Bill,
    ComputationResult,
    DeliveryPlan,
    PrimaryInputReceipt,
    ResultApplication,
)
from lexinform.models.primary_projections import PRIMARY_USAGE_FIELDS
from lexinform.primary_projections import primary_record_matches
from tools.corpus_artifacts import write_json
from tools.sqlite_snapshot import open_snapshot


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


def _direct_plan(row: sqlite3.Row) -> DeliveryPlan:
    if row["kind"] != "new_bill":
        raise CycleNotObservedError("publication_outside_primary_card_scope")
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
    plan = DeliveryPlan.model_validate_json(row["delivery_json"])
    if (
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
        or plan.reply_to is not None
        or plan.edit_message_id is not None
        or plan.primary_json
    ):
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
    return {
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


def audit(database: Path, publication_id: int) -> dict[str, object]:
    if not 0 < publication_id < 2**63:
        raise ValueError("Publication ID must be a positive SQLite integer")
    with open_snapshot(database) as snapshot:
        status, reason, cycle = "recorded", "exact_retained_cycle", None
        try:
            cycle = _inspect(snapshot.connection, publication_id)
        except CycleNotObservedError as exc:
            status, reason = "not_observed", str(exc)
        except ValueError, TypeError, RecursionError, KeyError, IndexError, sqlite3.Error:
            status, reason = "invalid", "invalid_snapshot_evidence"
        if not snapshot.unchanged():
            status, reason, cycle = "invalid", "database_changed_during_audit", None
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--publication-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.resolve() == args.database.resolve():
            raise ValueError("Report must be separate from the input database")
        report = audit(args.database, args.publication_id)
        write_json(args.output, report)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    raise SystemExit(
        0 if report["status"] == "recorded" else 1 if report["status"] == "invalid" else 2
    )


if __name__ == "__main__":
    main()
