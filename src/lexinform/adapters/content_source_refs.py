"""Read and validate scoped editorial source parents on the supplied SQLite snapshot."""

import sqlite3
from datetime import datetime

from lexinform.content_sources import verify_primary_content_source
from lexinform.models import (
    BatchProvider,
    BatchResult,
    Bill,
    ComputationResult,
    ContentSnapshot,
    ContentVersion,
    DocumentSelectionRecord,
    LlmBatchItem,
    ResultApplication,
    SourceObservation,
    StatusChange,
    amendments_stage,
)
from lexinform.models.batch import batch_item_meta


def _row_to_llm_batch_item(row: sqlite3.Row) -> LlmBatchItem:
    return LlmBatchItem(
        batch_id=row["batch_id"],
        custom_id=row["custom_id"],
        call_kind=row["call_kind"],
        term=row["term"],
        number=row["number"],
        meta=batch_item_meta(row["call_kind"], row["meta_json"]),
        consumed_at=datetime.fromisoformat(row["consumed_at"]) if row["consumed_at"] else None,
        result=BatchResult.model_validate_json(row["result_json"]) if row["result_json"] else None,
    )


class ContentSourceRows:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def _query(self, sql: str, parameters: tuple[object, ...] = ()) -> sqlite3.Cursor:
        cursor = self._connection.cursor()
        cursor.row_factory = sqlite3.Row
        return cursor.execute(sql, parameters)

    def get_content_snapshot(self, snapshot_id: str) -> ContentSnapshot | None:
        row = self._query(
            "SELECT * FROM content_snapshots WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()
        if row is None:
            return None
        snapshot = ContentSnapshot(kind=row["kind"], payload_json=row["payload_json"])
        if snapshot.snapshot_id != row["snapshot_id"]:
            raise ValueError("content snapshot columns differ from the recorded identity")
        return snapshot

    def get_computation_result(self, result_id: str) -> ComputationResult | None:
        row = self._query(
            "SELECT result_json FROM computation_results WHERE result_id = ?", (result_id,)
        ).fetchone()
        return ComputationResult.model_validate_json(row[0]) if row else None

    def get_consumed_primary_input(
        self, provider: BatchProvider, batch_id: str, custom_id: str, generation: int
    ) -> LlmBatchItem | None:
        row = self._query(
            "SELECT i.*, b.provider AS batch_provider, r.result_id AS paid_id,"
            " r.provider AS paid_provider, r.batch_id AS paid_batch, r.custom_id AS paid_custom,"
            " r.term AS paid_term, r.number AS paid_number, r.result_json AS paid_json,"
            " a.application_json AS projection_json"
            " FROM llm_batch_items i JOIN llm_batches b ON b.batch_id=i.batch_id"
            " LEFT JOIN computation_results r ON r.provider=? AND r.batch_id=i.batch_id"
            " AND r.custom_id=i.custom_id LEFT JOIN result_applications a"
            " ON a.result_id=r.result_id AND a.term=r.term AND a.number=r.number"
            " AND a.generation=? AND a.phase='projection' WHERE i.batch_id=? AND i.custom_id=?"
            " AND i.consumed_at IS NOT NULL",
            (provider, generation, batch_id, custom_id),
        ).fetchone()
        if row is None:
            return None
        if row["batch_provider"] != provider or row["paid_json"] is None:
            raise ValueError("consumed item and original provider/computation disagree")
        result = ComputationResult.model_validate_json(row["paid_json"])
        if (
            result.result_id,
            result.provider,
            result.batch_id,
            result.custom_id,
            result.term,
            result.number,
        ) != tuple(
            row[key]
            for key in (
                "paid_id",
                "paid_provider",
                "paid_batch",
                "paid_custom",
                "paid_term",
                "paid_number",
            )
        ):
            raise ValueError("retained computation columns disagree with its original identity")
        if row["projection_json"] is None:
            return None
        application = ResultApplication.model_validate_json(row["projection_json"])
        if (
            application.result_id,
            application.term,
            application.number,
            application.generation,
            application.phase,
            application.target,
        ) != (result.result_id, result.term, result.number, generation, "projection", None):
            raise ValueError("frozen primary target differs from its projection receipt")
        return _row_to_llm_batch_item(row)

    def get_document_selection(self, selection_id: str) -> DocumentSelectionRecord | None:
        row = self._query(
            "SELECT * FROM document_selections WHERE selection_id=?", (selection_id,)
        ).fetchone()
        if row is None:
            return None
        record = DocumentSelectionRecord.model_validate_json(row["record_json"])
        expected = (
            record.selection_id,
            record.selection.question,
            record.selection.policy_version,
            record.selection.state,
        )
        if expected != tuple(
            row[key] for key in ("selection_id", "question", "policy_version", "state")
        ):
            raise ValueError("document selection columns differ from the recorded identity")
        return record

    def get_source_observation(self, observation_id: str) -> SourceObservation | None:
        row = self._query(
            "SELECT * FROM source_observations WHERE observation_id=?", (observation_id,)
        ).fetchone()
        return self._source_observation(row) if row else None

    def source_observations_for_selection(
        self, term: int, number: str, selection_id: str
    ) -> list[SourceObservation]:
        return [
            self._source_observation(row)
            for row in self._query(
                "SELECT * FROM source_observations INDEXED BY ix_source_observations_selection"
                " WHERE selection_id=? AND term=?"
                " AND number=? AND purpose='analysis' ORDER BY rowid LIMIT 2",
                (selection_id, term, number),
            )
        ]

    @staticmethod
    def _source_observation(row: sqlite3.Row) -> SourceObservation:
        observation = SourceObservation.model_validate_json(row["observation_json"])
        expected = (
            observation.observation_id,
            observation.term,
            observation.number,
            observation.purpose,
            observation.selection_id,
            observation.document.url,
            observation.document.kind,
        )
        columns = (
            "observation_id",
            "term",
            "number",
            "purpose",
            "selection_id",
            "source_url",
            "source_kind",
        )
        if expected != tuple(row[key] for key in columns):
            raise ValueError("source observation columns differ from the recorded identity")
        return observation


def verify_content_source(connection: sqlite3.Connection, version: ContentVersion) -> None:
    if version.source_scope is None:
        return
    reader = ContentSourceRows(connection)
    snapshot = reader.get_content_snapshot(version.bill_snapshot_id)
    if snapshot is None or snapshot.kind != "bill":
        raise ValueError("scoped content has no frozen bill")
    bill = Bill.model_validate_json(snapshot.payload_json)
    change_snapshot = reader.get_content_snapshot(version.change_snapshot_id)
    if change_snapshot is None or change_snapshot.kind != "change":
        raise ValueError("scoped content has no frozen change")
    change = StatusChange.model_validate_json(change_snapshot.payload_json)
    plan = reader.get_content_snapshot(version.plan_snapshot_id)
    if plan is None or plan.kind != "plan":
        raise ValueError("scoped content source context has no semantic plan")
    if (
        not bill.has_process
        or (change.term, change.number) != (bill.term, bill.number)
        or amendments_stage(change.new_stages) is None
    ):
        raise ValueError("scoped content is outside its frozen primary context")
    root = reader._query(
        "SELECT term,number,kind FROM publications WHERE id=?", (version.root_publication_id,)
    ).fetchone()
    if root is None or (root["term"], root["number"], root["kind"]) != (
        bill.term,
        bill.number,
        "status_update",
    ):
        raise ValueError("scoped content differs from its original physical owner")
    if version.source_observation_ids:
        result = verify_primary_content_source(
            reader,
            bill_snapshot_id=version.bill_snapshot_id,
            change_snapshot_id=version.change_snapshot_id,
            observation_id=version.source_observation_ids[0],
        )
        if (
            result.state != "known"
            or result.source_observation_ids != version.source_observation_ids
        ):
            raise ValueError("scoped content source parent is invalid: " + result.reason)


def validate_content_sources(connection: sqlite3.Connection) -> None:
    if (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='content_versions'"
        ).fetchone()
        is None
    ):
        return
    rows = connection.execute(
        "SELECT content_id,version_json FROM content_versions"
        " WHERE CASE WHEN json_valid(version_json)"
        " THEN json_extract(version_json,'$.source_scope') IS NOT NULL ELSE 0 END"
    )
    for row in rows:
        version = ContentVersion.model_validate_json(row[1])
        if version.content_id != row[0]:
            raise ValueError("scoped content columns differ from its recorded identity")
        verify_content_source(connection, version)
