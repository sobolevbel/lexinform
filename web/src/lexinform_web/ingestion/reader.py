import json
import sqlite3

from lexinform.models import Bill, Publication, StatusChange
from lexinform.models.batch import BatchIntent, BatchRequest, LlmBatchItem, batch_item_meta
from lexinform_web.ingestion.contract import ImportDocumentV1, SnapshotOrigin
from lexinform_web.ingestion.projection import project_state
from lexinform_web.ingestion.restore import RestoredSnapshot, SnapshotError


def read_document(
    snapshot: RestoredSnapshot, *, source_commit: str, public_channel: str | None = None
) -> ImportDocumentV1:
    """Read only contract inputs; delivery payloads and operator data never enter the projection."""
    cursor = snapshot.connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        origin = SnapshotOrigin.model_validate(
            {
                "source_commit": source_commit,
                "dump_sha256": snapshot.dump_sha256,
                "source_schema": snapshot.source_schema,
                "normalized_schema": snapshot.normalized_schema,
            }
        )
        bills = []
        for row in cursor.execute("SELECT * FROM bills ORDER BY term, number"):
            values = {
                name: row[name]
                for name in (
                    "status",
                    "linked_number",
                    "linked_wykaz_number",
                    "awaiting_batch_since",
                    "first_seen_at",
                    "last_checked_at",
                    "discontinued_at",
                    "observed_closure_date",
                )
            }
            for name in (
                "summary",
                "stages",
                "analysis",
                "observed_process",
                "submission",
                "act",
                "agenda",
                "rcl",
                "wykaz",
                "ready_analysis",
            ):
                raw = row[f"{name}_json"]
                if raw is not None:
                    values[name] = json.loads(raw)
            bill = Bill.model_validate(values)
            if (bill.term, bill.number) != (row["term"], row["number"]):
                raise ValueError("bill identity disagrees with its row")
            bills.append(bill)

        changes = []
        for row in cursor.execute("SELECT * FROM status_changes ORDER BY id"):
            values = dict(row)
            for name in ("new_stages", "amendments", "supplements"):
                raw = values.pop(f"{name}_json")
                if raw is not None:
                    values[name] = json.loads(raw)
            changes.append(StatusChange.model_validate(values))

        publications = [
            Publication.model_validate(dict(row))
            for row in cursor.execute(
                "SELECT term, number, kind, status, channel_id, message_id, created_at "
                "FROM publications WHERE channel_id = ? AND status = 'sent' "
                "AND kind != 'digest' AND message_id IS NOT NULL ORDER BY id",
                (public_channel,),
            )
        ]
        intents = []
        for row in cursor.execute("SELECT * FROM llm_batch_intents ORDER BY custom_id"):
            request = BatchRequest.model_validate_json(row["request_json"])
            if request.custom_id != row["custom_id"]:
                raise ValueError("batch intent identity disagrees with its row")
            intents.append(
                BatchIntent.model_validate(
                    {
                        "request": request,
                        "meta": batch_item_meta(request.call_kind, row["meta_json"]),
                        "provider": row["provider"],
                        "state": row["state"],
                        "created_at": row["created_at"],
                    }
                )
            )
        items = []
        for row in cursor.execute(
            "SELECT * FROM llm_batch_items WHERE consumed_at IS NULL ORDER BY batch_id, custom_id"
        ):
            values = dict(row)
            values["meta"] = batch_item_meta(row["call_kind"], row["meta_json"])
            values["result"] = json.loads(row["result_json"]) if row["result_json"] else None
            items.append(LlmBatchItem.model_validate(values))
        return project_state(
            origin=origin,
            bills=bills,
            changes=changes,
            publications=publications,
            public_channel=public_channel,
            intents=intents,
            batch_items=items,
        )
    except ValueError, TypeError, KeyError, IndexError, sqlite3.Error:
        raise SnapshotError("snapshot rows failed import contract validation") from None
    finally:
        cursor.close()
