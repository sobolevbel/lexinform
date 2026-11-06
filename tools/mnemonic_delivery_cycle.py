"""Audit a paid mnemonic carried by a retained direct card."""

import argparse
import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

from lexinform.adapters.computation_history import read_result_application
from lexinform.adapters.content_source_refs import ContentSourceRows
from lexinform.models import (
    AnalysisRecord,
    BatchItemMeta,
    BatchResult,
    Bill,
    ResultApplicationTarget,
    TokenUsage,
)
from lexinform.models.batch import batch_item_meta
from lexinform.name_binding import mnemonic_matches_result
from tools.corpus_artifacts import write_json
from tools.primary_delivery_cycle import CycleNotObservedError, _direct_plan
from tools.sqlite_snapshot import open_snapshot


def _inspect(db: sqlite3.Connection, publication_id: int) -> dict[str, object]:
    row = db.execute("SELECT * FROM publications WHERE id=?", (publication_id,)).fetchone()
    if row is None:
        raise CycleNotObservedError("publication_unavailable")
    plan = _direct_plan(row)
    bill = Bill.model_validate_json(plan.bill_json)
    owner = (row["term"], row["number"])
    if (bill.term, bill.number) != owner:
        raise ValueError("Frozen bill differs from its publication owner")
    mnemonic = bill.editorial.mnemonic
    if mnemonic is None or mnemonic.computation_id is None:
        raise CycleNotObservedError("frozen_mnemonic_binding_unavailable")
    paid = ContentSourceRows(db).get_computation_result(mnemonic.computation_id)
    if paid is None:
        raise CycleNotObservedError("paid_mnemonic_unavailable")
    record = paid.record
    binding = paid.input_binding
    if paid.purpose not in ("analysis", "reanalysis") or not isinstance(record, AnalysisRecord):
        raise CycleNotObservedError("outside_analysis_mnemonic_scope")
    if paid.request_sha256 is None or binding is None:
        raise CycleNotObservedError("paid_input_binding_unavailable")
    if (
        (paid.term, paid.number) != owner
        or (binding.term, binding.number) != owner
        or not mnemonic_matches_result(mnemonic, paid)
    ):
        raise ValueError("Frozen mnemonic differs from its exact paid owner or result")

    applications = db.execute(
        "SELECT * FROM result_applications WHERE result_id=? AND term=? AND number=?"
        " AND phase='projection' AND json_extract(application_json,'$.target.kind')='editorial'"
        " AND json_extract(application_json,'$.target.ref')='mnemonic' LIMIT 2",
        (paid.result_id, *owner),
    ).fetchall()
    if len(applications) != 1:
        raise CycleNotObservedError("unique_mnemonic_application_unavailable")
    application = read_result_application(applications[0])
    if (
        application.target != ResultApplicationTarget(kind="editorial", ref="mnemonic")
        or application.outcome != "applied"
        or application.reason != "retained_result"
    ):
        raise ValueError("Mnemonic application differs from its retained projection")

    item = db.execute(
        "SELECT i.*, b.provider, b.call_kind AS batch_kind, b.submitted_at,"
        " b.status AS batch_status FROM llm_batch_items i JOIN llm_batches b USING (batch_id)"
        " WHERE i.batch_id=? AND i.custom_id=?",
        (paid.batch_id, paid.custom_id),
    ).fetchone()
    if item is None or item["result_json"] is None or item["consumed_at"] is None:
        raise CycleNotObservedError("consumed_mnemonic_item_unavailable")
    if (item["term"], item["number"]) != owner or (
        item["provider"],
        item["call_kind"],
        item["batch_kind"],
        item["batch_status"],
    ) != (paid.provider, paid.purpose, paid.purpose, "ended"):
        raise ValueError("Mnemonic batch differs from its exact paid owner")
    meta = batch_item_meta(paid.purpose, item["meta_json"])
    if not isinstance(meta, BatchItemMeta):
        raise ValueError("Mnemonic batch lacks typed primary metadata")
    if meta.request_sha256 is None or meta.input_binding is None:
        raise CycleNotObservedError("submitted_mnemonic_binding_unavailable")
    if (meta.request_sha256, meta.input_binding, meta.generation) != (
        paid.request_sha256,
        binding,
        paid.generation,
    ):
        raise ValueError("Mnemonic batch differs from its exact paid request")
    answer = BatchResult.model_validate_json(item["result_json"])
    usage = TokenUsage(
        batch_input=answer.input_tokens or 0,
        batch_output=answer.output_tokens or 0,
        batch_cache_read=answer.cache_read_input_tokens or 0,
        batch_cache_creation=answer.cache_creation_input_tokens or 0,
    )
    if (
        answer.custom_id != paid.custom_id
        or (answer.answer is not None and answer.answer != record.analysis)
        or answer.model != paid.model
        or answer.error is not None
        or answer.context_exceeded
        or usage != paid.usage
    ):
        raise ValueError("Retained mnemonic answer differs from the paid result")

    if meta.queued_at is None or item["submitted_at"] is None:
        raise CycleNotObservedError("mnemonic_batch_chronology_unavailable")
    sent_at = datetime.fromisoformat(row["sent_at"])
    consumed = datetime.fromisoformat(item["consumed_at"])
    dates = (
        meta.queued_at,
        datetime.fromisoformat(item["submitted_at"]),
        consumed,
        mnemonic.selected_at,
        application.created_at,
        datetime.fromisoformat(row["created_at"]),
        sent_at,
    )
    if (
        any(date.utcoffset() is None for date in dates)
        or dates != tuple(sorted(dates))
        or consumed != paid.collected_at
    ):
        raise ValueError("Recorded mnemonic collection, selection and delivery chronology disagree")
    aliases = db.execute(
        "SELECT count(*) FROM publications WHERE kind='new_bill' AND channel_id=? AND message_id=?",
        (row["channel_id"], row["message_id"]),
    ).fetchone()[0]
    if aliases != 1:
        raise CycleNotObservedError("physical_root_ambiguous")

    return {
        "application_id": applications[0]["id"],
        "purpose": paid.purpose,
        "retained_provider_answer": "not_retained"
        if answer.answer is None
        else "exact_retained_answer",
        "result_id": paid.result_id,
        "request_sha256": paid.request_sha256,
        "recorded_sent_at": sent_at.isoformat(),
        "frozen_render_sha256": hashlib.sha256(plan.rendered_text.encode()).hexdigest(),
    }


def audit(database: Path, publication_id: int) -> dict[str, object]:
    with open_snapshot(database) as snapshot:
        try:
            cycle = _inspect(snapshot.connection, publication_id)
            status, reason = "recorded", "exact_retained_mnemonic_chain"
        except CycleNotObservedError as exc:
            cycle, status, reason = None, "not_observed", str(exc)
        except ValueError, TypeError, KeyError, IndexError, RecursionError, sqlite3.Error:
            cycle, status, reason = None, "invalid", "invalid_snapshot_evidence"
        if not snapshot.unchanged():
            raise ValueError("Database changed during mnemonic delivery audit")
    return {
        "version": 1,
        "scope": "Saved request identity, frozen mnemonic projection and recorded send.",
        "database_sha256": snapshot.sha256,
        "publication_id": publication_id,
        "status": status,
        "reason": reason,
        "cycle": cycle,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--publication-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    database = args.database.resolve()
    if output in (
        database,
        Path(str(database) + "-wal"),
        Path(str(database) + "-shm"),
        Path(str(database) + "-journal"),
    ):
        parser.error("Output must be separate from the SQLite snapshot and its sidecars")
    write_json(output, audit(database, args.publication_id))


if __name__ == "__main__":
    main()
