"""Validate retained frozen requests and prospective input receipts without executing work."""

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

from lexinform.adapters.candidate_trace_history import (
    read_candidate_disposition_trace,
    validate_sejm_proposal_link,
)
from lexinform.adapters.computation_history import read_computation_result
from lexinform.adapters.document_source_rows import DocumentSourceRows
from lexinform.adapters.extraction_history import read_extraction_result
from lexinform.errors import PreparedInputParentError
from lexinform.json_storage import read_json
from lexinform.models import (
    AmendmentInputReceipt,
    AnalysisQuestion,
    ArtifactMetadata,
    BatchIntent,
    BatchItemMeta,
    BatchRequest,
    DigestItemMeta,
    DocumentSelectionRecord,
    ExtractionResult,
    LlmBatchItem,
    PrimaryInputReceipt,
    SupplementInputReceipt,
)
from lexinform.models.batch import batch_item_meta, validate_batch_item_meta
from lexinform.models.input_receipts import (
    LooseMultipartInputReceipt,
    LooseRclInputReceipt,
    SejmSelfAmendmentInputReceipt,
)

_QUESTION_TEXT_PROFILE = "question-text-v1"
_PROVIDER_PAYLOAD_PROFILE = "question-text-json-v1"
_MIN_SHARED_QUESTION_BYTES = 32 * 1024


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
        value = read_json(raw)
    except ValueError, TypeError, RecursionError:
        return None
    return {key: item for key, item in value.items()} if isinstance(value, dict) else None


def parse_batch_request(raw: object) -> BatchRequest:
    try:
        decoded = read_json(raw) if isinstance(raw, str) else raw
        if isinstance(decoded, dict) and isinstance(payload := decoded.get("payload_json"), dict):
            decoded = {**decoded, "payload_json": _provider_payload(decoded, payload)}
        return BatchRequest.model_validate(decoded)
    except ValueError, TypeError, RecursionError:
        raise ValueError("invalid_input_request_json") from None


def _provider_payload(request: dict[str, object], value: dict[str, object]) -> str:
    question = request.get("question")
    context = question.get("ctx") if isinstance(question, dict) else None
    prefix, suffix = value.get("prefix"), value.get("suffix")
    text = context.get("text") if isinstance(context, dict) else None
    if (
        set(value) != {"profile", "prefix", "suffix"}
        or value["profile"] != _PROVIDER_PAYLOAD_PROFILE
        or not isinstance(prefix, str)
        or not isinstance(suffix, str)
        or not isinstance(text, str)
        or not text
        or not isinstance(question, dict)
        or question.get("kind") not in ("analysis", "reanalysis")
        or not isinstance(request.get("payload_sha256"), str)
    ):
        raise ValueError("invalid_provider_payload_reference")
    return prefix + json.dumps(text, ensure_ascii=False)[1:-1] + suffix


def intent_metadata(
    request: BatchRequest, raw: str | dict[str, object]
) -> BatchItemMeta | DigestItemMeta:
    decoded = read_json(raw) if isinstance(raw, str) else raw
    if isinstance(decoded, dict) and isinstance(reference := decoded.get("text"), dict):
        question = request.question
        if (
            not isinstance(question, AnalysisQuestion)
            or request.call_kind not in ("analysis", "reanalysis")
            or request.payload_sha256 is None
            or not question.ctx.text
            or reference
            != {
                "profile": _QUESTION_TEXT_PROFILE,
                "sha256": hashlib.sha256(question.ctx.text.encode()).hexdigest(),
            }
        ):
            raise ValueError("invalid_intent_question_text_reference")
        request.verify_payload()
        decoded = {**decoded, "text": question.ctx.text}
    return validate_batch_item_meta(request.call_kind, decoded, submitted=False)


def serialize_intent_metadata(intent: BatchIntent) -> str:
    meta, request = intent.meta, intent.request
    question = request.question
    if (
        isinstance(meta, BatchItemMeta)
        and isinstance(question, AnalysisQuestion)
        and request.call_kind in ("analysis", "reanalysis")
        and request.payload_sha256 is not None
        and meta.text == question.ctx.text
    ):
        text = meta.text.encode()
        if len(text) >= _MIN_SHARED_QUESTION_BYTES:
            request.verify_payload()
            values = meta.model_dump(mode="json")
            values["text"] = {
                "profile": _QUESTION_TEXT_PROFILE,
                "sha256": hashlib.sha256(text).hexdigest(),
            }
            return json.dumps(values, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return meta.model_dump_json()


def _reference(table: str, key: object) -> str:
    try:
        encoded = json.dumps(key, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    except TypeError, ValueError:
        return table + "/unavailable-key"
    return table + "/" + hashlib.sha256(encoded).hexdigest()


def _source_selection(connection: sqlite3.Connection, identity: str) -> DocumentSelectionRecord:
    row = _one(
        connection,
        "SELECT * FROM document_selections WHERE selection_id=?",
        (identity,),
    )
    selection = DocumentSelectionRecord.model_validate_json(row["record_json"])
    if (
        selection.selection_id,
        selection.selection.question,
        selection.selection.policy_version,
        selection.selection.state,
    ) != (row["selection_id"], row["question"], row["policy_version"], row["state"]):
        raise ValueError("input_receipt_selection_mismatch")
    return selection


def _multipart_parents(
    connection: sqlite3.Connection,
    receipt: LooseMultipartInputReceipt,
    meta: BatchItemMeta,
    owner: tuple[int, str] | None,
) -> None:
    selection = _source_selection(connection, receipt.source_selection_id).selection
    fitted = meta.selection
    parts = selection.parts
    if (
        selection.scan_input is not None
        or (selection.question, selection.policy_version)
        != (fitted.question, fitted.policy_version)
        or any(span.stage == "analysis" for span in selection.coverage)
        or len(parts) != len(receipt.source_parts)
        or len({part.url for part in parts}) != len(parts)
        or parts != fitted.without_text().parts
    ):
        raise ValueError("input_receipt_selection_mismatch")
    if isinstance(receipt, LooseRclInputReceipt) and receipt.source_observation_id is not None:
        observation = DocumentSourceRows(connection).get_source_observation(
            receipt.source_observation_id
        )
        binding = meta.input_binding
        if (
            observation is None
            or binding is None
            or (observation.term, observation.number) != (binding.term, binding.number)
            or observation.purpose != "analysis"
            or observation.selection_id != receipt.source_selection_id
            or (observation.document.kind, observation.document.url) != ("rcl", meta.source_url)
        ):
            raise ValueError("input_receipt_observation_mismatch")
    if isinstance(receipt, SejmSelfAmendmentInputReceipt):
        binding = meta.input_binding
        if binding is None or (binding.term, binding.number) != owner:
            raise ValueError("input_receipt_proposal_owner_mismatch")
        row = _one(
            connection,
            "SELECT t.* FROM source_candidate_traces l"
            " JOIN candidate_disposition_traces t USING(trace_id)"
            " JOIN source_observations o USING(observation_id)"
            " WHERE l.observation_id=? AND l.trace_id=? AND o.purpose='analysis'",
            (receipt.source_observation_id, receipt.proposal_trace_id),
        )
        trace = read_candidate_disposition_trace(row)
        proposal = trace.sejm_original_proposal
        if (
            proposal is None
            or proposal.selection_id != receipt.source_selection_id
            or (proposal.term, proposal.parent_print) != owner
        ):
            raise ValueError("input_receipt_proposal_mismatch")
        validate_sejm_proposal_link(connection, trace, receipt.source_observation_id)
    for index, (part, recorded) in enumerate(zip(parts, receipt.source_parts, strict=True)):
        if (
            part.members
            or not part.url
            or (part.role, part.text_sha256, part.text_start, part.text_end)
            != (recorded.role, recorded.text_sha256, 0, recorded.chars)
        ):
            raise ValueError("input_receipt_selection_mismatch")
        link = _one(
            connection,
            "SELECT a.* FROM selection_artifacts l JOIN artifacts a ON a.sha256=l.artifact_sha256"
            " WHERE l.selection_id=? AND l.part_scope='parts' AND l.part_index=?",
            (receipt.source_selection_id, index),
        )
        artifact = ArtifactMetadata(
            sha256=link["sha256"], byte_size=link["byte_size"], recorded_at=link["recorded_at"]
        )
        if artifact.sha256 != part.sha256:
            raise ValueError("input_receipt_body_mismatch")
        row = _one(
            connection,
            "SELECT e.* FROM selection_extractions l JOIN extraction_results e USING(extraction_id)"
            " WHERE l.selection_id=? AND l.extraction_id=?",
            (receipt.source_selection_id, recorded.extraction_id),
        )
        extraction = read_extraction_result(row)
        if (
            extraction.artifact_sha256 != artifact.sha256
            or extraction.output_text_sha256 != recorded.text_sha256
            or extraction.output_chars != recorded.chars
            or extraction.physical_part_count != 1
            or extraction.selected_part_count != 1
            or extraction.omitted_selected_parts != 0
            or len(extraction.selected_parts) != 1
        ):
            raise ValueError("input_receipt_extraction_mismatch")
        summary = extraction.selected_parts[0]
        if (
            summary.physical_index != 0
            or summary.sha256 != artifact.sha256
            or summary.text_sha256 != recorded.text_sha256
            or summary.text_chars != recorded.chars
        ):
            raise ValueError("input_receipt_extraction_mismatch")


def _parents(
    connection: sqlite3.Connection,
    receipt: PrimaryInputReceipt
    | LooseRclInputReceipt
    | SejmSelfAmendmentInputReceipt
    | SupplementInputReceipt
    | AmendmentInputReceipt,
    *,
    primary_meta: BatchItemMeta | None = None,
    owner: tuple[int, str] | None = None,
) -> None:
    if isinstance(receipt, LooseMultipartInputReceipt):
        if primary_meta is None:
            raise ValueError("Multipart receipt requires its fitted selection")
        _multipart_parents(connection, receipt, primary_meta, owner)
        return
    selection = None
    if receipt.source_selection_id is not None:
        selection = _source_selection(connection, receipt.source_selection_id)
        if len(selection.selection.parts) != 1:
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


def validate_primary_receipt_parents(
    connection: sqlite3.Connection, receipt: PrimaryInputReceipt
) -> None:
    _parents(connection, receipt)


def validate_multipart_intent(connection: sqlite3.Connection, intent: BatchIntent) -> None:
    if not isinstance(intent.meta.prepared_input_receipt, LooseMultipartInputReceipt):
        return
    validated = BatchIntent.model_validate_json(intent.model_dump_json())
    meta = validated.meta
    if not isinstance(meta, BatchItemMeta) or not isinstance(
        meta.prepared_input_receipt, LooseMultipartInputReceipt
    ):
        raise ValueError("Multipart receipt requires primary metadata")
    try:
        _multipart_parents(
            connection,
            meta.prepared_input_receipt,
            meta,
            (validated.request.term, validated.request.number),
        )
    except ValueError as exc:
        raise PreparedInputParentError(str(exc)) from None


def validate_multipart_item(connection: sqlite3.Connection, item: LlmBatchItem) -> None:
    if not isinstance(item.meta.prepared_input_receipt, LooseMultipartInputReceipt):
        return
    meta = batch_item_meta(item.call_kind, item.meta.model_dump_json())
    if not isinstance(meta, BatchItemMeta) or not isinstance(
        meta.prepared_input_receipt, LooseMultipartInputReceipt
    ):
        raise ValueError("Multipart receipt requires primary metadata")
    _multipart_parents(connection, meta.prepared_input_receipt, meta, (item.term, item.number))


def _input_snapshot_checks(
    connection: sqlite3.Connection, *, include_results: bool
) -> Iterator[InputSnapshotCheck]:
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "llm_batch_intents" in tables:
        for row in _rows(connection, "SELECT * FROM llm_batch_intents"):
            meta = _object(row["meta_json"])
            request = _object(row["request_json"])
            if meta is None or request is None:
                yield InputSnapshotCheck(
                    _reference("llm_batch_intents", row["custom_id"]),
                    False,
                    False,
                    "invalid_input_intent_snapshot",
                )
                continue
            has_receipt = meta is not None and meta.get("prepared_input_receipt") is not None
            has_timing = meta is not None and meta.get("local_timing_witness") is not None
            frozen = request is not None and request.get("payload_sha256") is not None
            has_text_reference = isinstance(meta.get("text"), dict)
            has_provider_reference = isinstance(request.get("payload_json"), dict)
            if not (
                has_receipt or has_timing or frozen or has_text_reference or has_provider_reference
            ):
                continue
            ref = _reference("llm_batch_intents", row["custom_id"])
            try:
                actual = parse_batch_request(request)
                if actual.custom_id != row["custom_id"] or row["provider"] not in (
                    "anthropic",
                    "openai",
                ):
                    raise ValueError("input_snapshot_request_columns")
                if has_receipt or has_timing or has_text_reference:
                    intent = BatchIntent.model_validate(
                        {
                            "request": actual,
                            "meta": intent_metadata(actual, meta) if has_text_reference else meta,
                            "provider": row["provider"],
                            "state": row["state"],
                            "created_at": row["created_at"],
                        }
                    )
                    if has_receipt:
                        if intent.meta.prepared_input_receipt is None:
                            raise ValueError("input_snapshot_receipt_metadata")
                        _parents(
                            connection,
                            intent.meta.prepared_input_receipt,
                            primary_meta=intent.meta
                            if isinstance(intent.meta, BatchItemMeta)
                            else None,
                            owner=(actual.term, actual.number),
                        )
                    if has_timing and intent.meta.local_timing_witness is None:
                        raise ValueError("input_snapshot_timing_metadata")
            except ValueError, TypeError, RecursionError, sqlite3.Error:
                yield InputSnapshotCheck(ref, has_receipt, frozen, "invalid_input_intent_snapshot")
            else:
                yield InputSnapshotCheck(ref, has_receipt, frozen)
    if "llm_batch_items" in tables:
        for row in _rows(connection, "SELECT * FROM llm_batch_items"):
            raw = _object(row["meta_json"])
            if raw is None:
                yield InputSnapshotCheck(
                    _reference("llm_batch_items", (row["batch_id"], row["custom_id"])),
                    False,
                    False,
                    "invalid_input_item_snapshot",
                )
                continue
            has_receipt = raw is not None and raw.get("prepared_input_receipt") is not None
            has_timing = raw is not None and raw.get("local_timing_witness") is not None
            if not has_receipt and not has_timing:
                continue
            ref = _reference("llm_batch_items", (row["batch_id"], row["custom_id"]))
            try:
                item_meta = batch_item_meta(row["call_kind"], row["meta_json"])
                if has_receipt and item_meta.prepared_input_receipt is None:
                    raise ValueError("input_snapshot_receipt_metadata")
                parent = _one(
                    connection,
                    "SELECT provider, submitted_at FROM llm_batches WHERE batch_id=?"
                    if has_timing
                    else "SELECT provider FROM llm_batches WHERE batch_id=?",
                    (row["batch_id"],),
                )
                if parent["provider"] not in ("anthropic", "openai"):
                    raise ValueError("input_snapshot_provider")
                if item_meta.prepared_input_receipt is not None:
                    _parents(
                        connection,
                        item_meta.prepared_input_receipt,
                        primary_meta=item_meta if isinstance(item_meta, BatchItemMeta) else None,
                        owner=(row["term"], row["number"]),
                    )
                if has_timing:
                    timing = item_meta.local_timing_witness
                    if timing is None:
                        raise ValueError("input_snapshot_timing_metadata")
                    timing.require_not_after(datetime.fromisoformat(parent["submitted_at"]))
            except ValueError, TypeError, RecursionError, sqlite3.Error:
                yield InputSnapshotCheck(ref, has_receipt, False, "invalid_input_item_snapshot")
            else:
                yield InputSnapshotCheck(ref, has_receipt, False)

    if include_results and "computation_results" in tables:
        for row in _rows(connection, "SELECT * FROM computation_results"):
            raw = _object(row["result_json"])
            if raw is not None and raw.get("local_timing_witness") is None:
                continue
            ref = _reference("computation_results", row["result_id"])
            try:
                result = read_computation_result(row)
                if result.local_timing_witness is None:
                    raise ValueError("input_snapshot_timing_metadata")
            except ValueError, TypeError, RecursionError, sqlite3.Error:
                yield InputSnapshotCheck(ref, False, False, "invalid_input_result_snapshot")
            else:
                yield InputSnapshotCheck(ref, False, False)


def input_snapshot_checks(
    connection: sqlite3.Connection, *, include_results: bool = True
) -> Iterator[InputSnapshotCheck]:
    try:
        yield from _input_snapshot_checks(connection, include_results=include_results)
    except IndexError, KeyError, sqlite3.Error:
        yield InputSnapshotCheck("input_snapshots", False, False, "invalid_input_snapshot_layout")


def validate_input_snapshots(
    connection: sqlite3.Connection, *, include_results: bool = True
) -> None:
    for check in input_snapshot_checks(connection, include_results=include_results):
        if check.code is not None:
            raise ValueError(check.code) from None
