"""Validate retained frozen requests and prospective input receipts without executing work."""

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass

from lexinform.models import (
    ArtifactMetadata,
    BatchIntent,
    BatchItemMeta,
    BatchRequest,
    DocumentSelectionRecord,
    ExtractionResult,
    PrimaryInputReceipt,
)


@dataclass(frozen=True)
class InputSnapshotCheck:
    ref: str
    receipt: bool
    frozen_request: bool
    code: str | None = None


def _rows(
    connection: sqlite3.Connection, sql: str, parameters: tuple[object, ...] = ()
) -> Iterator[sqlite3.Row]:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        yield from cursor.execute(sql, parameters)
    finally:
        cursor.close()


def _one(connection: sqlite3.Connection, sql: str, parameters: tuple[object, ...]) -> sqlite3.Row:
    row = next(_rows(connection, sql, parameters), None)
    if row is None:
        raise ValueError("input_receipt_parent_missing")
    return row


def _object(raw: object) -> dict[str, object] | None:
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _reference(table: str, key: object) -> str:
    try:
        encoded = json.dumps(key, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    except TypeError, ValueError:
        return table + "/unavailable-key"
    return table + "/" + hashlib.sha256(encoded).hexdigest()


def _parents(connection: sqlite3.Connection, receipt: PrimaryInputReceipt) -> None:
    selection = None
    if receipt.source_selection_id is not None:
        row = _one(
            connection,
            "SELECT * FROM document_selections WHERE selection_id=?",
            (receipt.source_selection_id,),
        )
        selection = DocumentSelectionRecord.model_validate_json(row["record_json"])
        if (
            selection.selection_id,
            selection.selection.question,
            selection.selection.policy_version,
            selection.selection.state,
        ) != (row["selection_id"], row["question"], row["policy_version"], row["state"]) or len(
            selection.selection.parts
        ) != 1:
            raise ValueError("input_receipt_selection_mismatch")
        part = selection.selection.parts[0]
        if (
            part.members
            or part.role != receipt.source_part.role
            or part.text_sha256 != receipt.source_part.text_sha256
            or part.text_end is not None
            and part.text_end - part.text_start != receipt.source_part.chars
        ):
            raise ValueError("input_receipt_selection_mismatch")
    if receipt.artifact_sha256 is not None:
        row = _one(connection, "SELECT * FROM artifacts WHERE sha256=?", (receipt.artifact_sha256,))
        ArtifactMetadata(
            sha256=row["sha256"], byte_size=row["byte_size"], recorded_at=row["recorded_at"]
        )
        if selection is not None:
            if selection.selection.parts[0].sha256 != receipt.artifact_sha256:
                raise ValueError("input_receipt_body_mismatch")
            link = _one(
                connection,
                "SELECT artifact_sha256 FROM selection_artifacts"
                " WHERE selection_id=? AND part_scope='parts' AND part_index=0",
                (receipt.source_selection_id,),
            )
            if link["artifact_sha256"] != receipt.artifact_sha256:
                raise ValueError("input_receipt_body_mismatch")
    if receipt.extraction_id is not None:
        row = _one(
            connection,
            "SELECT * FROM extraction_results WHERE extraction_id=?",
            (receipt.extraction_id,),
        )
        extraction = ExtractionResult.model_validate_json(row["result_json"])
        if (
            extraction.result_id != row["extraction_id"]
            or extraction.artifact_sha256 != row["artifact_sha256"]
            or extraction.artifact_sha256 != receipt.artifact_sha256
            or extraction.output_text_sha256 != receipt.source_part.text_sha256
            or extraction.output_chars != receipt.source_part.chars
            or extraction.physical_part_count != 1
            or extraction.selected_part_count != 1
            or extraction.omitted_selected_parts != 0
            or len(extraction.selected_parts) != 1
        ):
            raise ValueError("input_receipt_extraction_mismatch")
        part_summary = extraction.selected_parts[0]
        if (
            part_summary.physical_index != 0
            or part_summary.sha256 != receipt.artifact_sha256
            or part_summary.text_sha256 != receipt.source_part.text_sha256
            or part_summary.text_chars != receipt.source_part.chars
        ):
            raise ValueError("input_receipt_extraction_mismatch")
        if selection is not None:
            _one(
                connection,
                "SELECT 1 FROM selection_extractions WHERE selection_id=? AND extraction_id=?",
                (receipt.source_selection_id, receipt.extraction_id),
            )


def _input_snapshot_checks(connection: sqlite3.Connection) -> Iterator[InputSnapshotCheck]:
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "llm_batch_intents" in tables:
        for row in _rows(connection, "SELECT * FROM llm_batch_intents"):
            meta = _object(row["meta_json"])
            request = _object(row["request_json"])
            has_receipt = meta is not None and meta.get("prepared_input_receipt") is not None
            frozen = request is not None and request.get("payload_sha256") is not None
            if not has_receipt and not frozen:
                continue
            ref = _reference("llm_batch_intents", row["custom_id"])
            try:
                actual = BatchRequest.model_validate_json(row["request_json"])
                if actual.custom_id != row["custom_id"] or row["provider"] not in (
                    "anthropic",
                    "openai",
                ):
                    raise ValueError("input_snapshot_request_columns")
                if has_receipt:
                    intent = BatchIntent.model_validate(
                        {
                            "request": actual,
                            "meta": meta,
                            "provider": row["provider"],
                            "state": row["state"],
                            "created_at": row["created_at"],
                        }
                    )
                    if (
                        not isinstance(intent.meta, BatchItemMeta)
                        or intent.meta.prepared_input_receipt is None
                    ):
                        raise ValueError("input_snapshot_primary_metadata")
                    _parents(connection, intent.meta.prepared_input_receipt)
            except ValueError, TypeError, sqlite3.Error:
                yield InputSnapshotCheck(ref, has_receipt, frozen, "invalid_input_intent_snapshot")
            else:
                yield InputSnapshotCheck(ref, has_receipt, frozen)
    if "llm_batch_items" in tables:
        for row in _rows(connection, "SELECT * FROM llm_batch_items"):
            raw = _object(row["meta_json"])
            if raw is None or raw.get("prepared_input_receipt") is None:
                continue
            ref = _reference("llm_batch_items", (row["batch_id"], row["custom_id"]))
            try:
                if row["call_kind"] not in ("analysis", "reanalysis"):
                    raise ValueError("input_snapshot_primary_metadata")
                item_meta = BatchItemMeta.model_validate(raw)
                if item_meta.prepared_input_receipt is None:
                    raise ValueError("input_snapshot_primary_metadata")
                parent = _one(
                    connection,
                    "SELECT provider FROM llm_batches WHERE batch_id=?",
                    (row["batch_id"],),
                )
                if parent["provider"] not in ("anthropic", "openai"):
                    raise ValueError("input_snapshot_provider")
                _parents(connection, item_meta.prepared_input_receipt)
            except ValueError, TypeError, sqlite3.Error:
                yield InputSnapshotCheck(ref, True, False, "invalid_input_item_snapshot")
            else:
                yield InputSnapshotCheck(ref, True, False)


def input_snapshot_checks(connection: sqlite3.Connection) -> Iterator[InputSnapshotCheck]:
    try:
        yield from _input_snapshot_checks(connection)
    except IndexError, KeyError, sqlite3.Error:
        yield InputSnapshotCheck("input_snapshots", False, False, "invalid_input_snapshot_layout")


def validate_input_snapshots(connection: sqlite3.Connection) -> None:
    for check in input_snapshot_checks(connection):
        if check.code is not None:
            raise ValueError(check.code) from None
