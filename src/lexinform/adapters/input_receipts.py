"""Validate retained frozen requests and prospective input receipts without executing work."""

import hashlib
import json
import sqlite3
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime

from lexinform.adapters.candidate_trace_history import (
    read_candidate_disposition_trace,
    validate_sejm_proposal_link,
)
from lexinform.adapters.computation_history import read_computation_result
from lexinform.adapters.document_source_rows import DocumentSourceRows
from lexinform.adapters.extraction_history import read_extraction_result
from lexinform.adapters.llm_prompts import build_supplement_prompt
from lexinform.errors import PreparedInputParentError
from lexinform.input_receipts import (
    archive_member_matches,
    ordinary_rcl_archive_document,
    rcl_archive_self_amendment_url,
    selected_archive_version_peer,
)
from lexinform.json_storage import canonical_json, read_json
from lexinform.models import (
    SUPPLEMENT_SOURCES,
    AmendmentInputReceipt,
    AmendmentsQuestion,
    AnalysisQuestion,
    ArtifactMetadata,
    BatchIntent,
    BatchItemMeta,
    BatchProvider,
    BatchRequest,
    ComputationResult,
    DigestItemMeta,
    DocumentPart,
    DocumentSelectionRecord,
    ExtractionResult,
    LlmBatchItem,
    PrimaryInputReceipt,
    SupplementInputReceipt,
    SupplementQuestion,
    SupplementRecord,
)
from lexinform.models.batch import FrozenRequestManifest, batch_item_meta, validate_batch_item_meta
from lexinform.models.input_receipts import (
    ArchiveInputPart,
    LooseMultipartInputReceipt,
    LooseRclInputPart,
    LooseRclInputReceipt,
    MultipartTextInputReceipt,
    RclArchiveInputReceipt,
    RclArchiveSelfAmendmentInputReceipt,
    SejmSelfAmendmentInputReceipt,
)
from lexinform.models.scan_input import MAX_SCAN_BYTES, MAX_SCAN_PAGES
from lexinform.models.sejm import TextDocument
from lexinform.models.supplement_inputs import (
    MAX_SUPPLEMENT_SCAN_WITNESS_BYTES,
    SupplementScanInputWitness,
)

_QUESTION_TEXT_PROFILE = "question-text-v1"
_PROVIDER_PAYLOAD_PROFILE = "question-text-json-v1"
_SCAN_PAYLOAD_PROFILE = "question-scan-base64-v1"
_SCAN_PLACEHOLDERS = ("lexinform:scan-data-a", "lexinform:scan-data-b")
_MAX_SCAN_PAYLOAD_SHELL_CHARS = 1024 * 1024
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
    prefix, suffix = value.get("prefix"), value.get("suffix")
    if (
        set(value) != {"profile", "prefix", "suffix"}
        or not isinstance(prefix, str)
        or not isinstance(suffix, str)
        or not isinstance(question, dict)
        or not isinstance(request.get("payload_sha256"), str)
        or not isinstance(context := question.get("ctx"), dict)
    ):
        raise ValueError("invalid_provider_payload_reference")
    kind = question.get("kind")
    if value["profile"] == _PROVIDER_PAYLOAD_PROFILE and kind in ("analysis", "reanalysis"):
        if isinstance(text := context.get("text"), str) and text:
            return prefix + _escaped_question_text(text) + suffix
    elif value["profile"] == _SCAN_PAYLOAD_PROFILE:
        return _scan_provider_payload(request, kind, context, prefix, suffix)
    raise ValueError("invalid_provider_payload_reference")


def _scan_provider_payload(
    request: dict[str, object],
    kind: object,
    context: dict[str, object],
    prefix: str,
    suffix: str,
) -> str:
    scan = context.get("scan")
    primary = kind in ("analysis", "reanalysis") and context.get("text_source") == "scan"
    committee = (
        kind == "amendments"
        and context.get("source_kind") == "committee_amendments"
        and context.get("source_check") is None
        and "text_source" not in context
    )
    if (
        context.get("text") != ""
        or not isinstance(scan, dict)
        or scan.get("media_type") != "application/pdf"
        or not isinstance(manifest := scan.get("scan_input"), dict)
        or not isinstance(data := scan.get("data"), str)
        or not _scan_data_fits(data, scan.get("pages"))
    ):
        raise ValueError("invalid_scan_provider_payload_reference")
    supplement = kind == "supplement" and _supplement_scan_payload_matches(
        context, request.get("number"), scan, manifest
    )
    if not (
        primary
        or committee
        or supplement
        or _senate_scan_payload_matches(kind, context, scan, manifest)
    ):
        raise ValueError("invalid_scan_provider_payload_reference")
    _validate_scan_payload_shell(request.get("custom_id"), request.get("model"), prefix, suffix)
    return prefix + data + suffix


def _senate_scan_payload_matches(
    kind: object,
    context: dict[str, object],
    scan: dict[str, object],
    manifest: dict[str, object],
) -> bool:
    source = context.get("source_check")
    if (
        kind != "amendments"
        or context.get("source_kind") != "senate_amendments"
        or context.get("truncated") is not False
        or "text_source" in context
        or not isinstance(source, dict)
        or source.get("scan") is not True
        or source.get("word_eof", False) is not False
    ):
        return False
    subjects = source.get("expected_subjects")
    return (
        isinstance(subjects, (list, tuple))
        and bool(subjects)
        and all(isinstance(subject, str) and subject.strip() for subject in subjects)
        and source.get("subject") == subjects[0]
        and type(source.get("pages")) is int
        and source.get("pages") == scan.get("pages")
        and source.get("sha256") == scan.get("sha256")
        and _full_scan_payload_matches(scan, manifest)
    )


def _supplement_scan_payload_matches(
    context: dict[str, object],
    number: object,
    scan: dict[str, object],
    manifest: dict[str, object],
) -> bool:
    source_kind = context.get("source_kind")
    title = context.get("document_title")
    return (
        isinstance(source_kind, str)
        and source_kind in SUPPLEMENT_SOURCES
        and isinstance(number, str)
        and bool(number)
        and context.get("number") == number
        and isinstance(title, str)
        and bool(title.strip())
        and context.get("truncated") is False
        and "text_source" not in context
        and "source_check" not in context
        and _full_scan_payload_matches(scan, manifest)
    )


def _full_scan_payload_matches(scan: dict[str, object], manifest: dict[str, object]) -> bool:
    pages = scan.get("pages")
    size = manifest.get("input_bytes")
    return (
        type(pages) is int
        and all(
            type(value) is int and value == pages
            for value in (
                scan.get("of_pages"),
                manifest.get("source_pages"),
                manifest.get("page_count"),
            )
        )
        and type(scan.get("cover_letter_pages", 0)) is int
        and scan.get("cover_letter_pages", 0) == 0
        and type(manifest.get("first_page")) is int
        and manifest.get("first_page") == 1
        and manifest.get("transformations", ()) in ((), [])
        and type(size) is int
        and 0 < size <= MAX_SCAN_BYTES
        and type(manifest.get("source_bytes")) is int
        and manifest.get("source_bytes") == size
        and scan.get("sha256") == manifest.get("source_sha256") == manifest.get("input_sha256")
    )


def _scan_data_fits(data: str, pages: object) -> bool:
    return (
        type(pages) is int
        and 0 < pages <= MAX_SCAN_PAGES
        and 0 < len(data) <= 4 * ((MAX_SCAN_BYTES + 2) // 3)
    )


def _validate_scan_payload_shell(
    custom_id: object, model: object, prefix: str, suffix: str
) -> None:
    if len(prefix) + len(suffix) > _MAX_SCAN_PAYLOAD_SHELL_CHARS:
        raise ValueError("invalid_scan_provider_payload_reference")
    # Two markers distinguish the reconstructed slot from pre-existing JSON strings.
    for marker in _SCAN_PLACEHOLDERS:
        shell = read_json(prefix + marker + suffix)
        if (
            not isinstance(shell, dict)
            or shell.get("custom_id") != custom_id
            or not _scan_payload_slot(shell, model, marker)
        ):
            raise ValueError("invalid_scan_provider_payload_slot")


def _scan_payload_slot(shell: Mapping[str, object], model: object, marker: str) -> bool:
    if not isinstance(model, str) or not model:
        return False
    match shell:
        case {
            "method": "POST",
            "url": "/v1/responses",
            "body": {
                "model": saved_model,
                "input": [
                    _,
                    {"role": "user", "content": [{"type": "input_file", "file_data": slot}, *_]},
                ],
            },
        } if set(shell) == {"custom_id", "method", "url", "body"}:
            return bool(saved_model == model and slot == f"data:application/pdf;base64,{marker}")
        case {
            "params": {
                "model": saved_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "document",
                                "source": {
                                    "type": "base64",
                                    "media_type": "application/pdf",
                                    "data": slot,
                                },
                            },
                            *_,
                        ],
                    }
                ],
            }
        } if set(shell) == {"custom_id", "params"}:
            return bool(saved_model == model and slot == marker)
        case _:
            return False


def _text_only_blocks(blocks: object, block_type: str) -> bool:
    return isinstance(blocks, list) and all(
        isinstance(block, dict)
        and block.get("type") == block_type
        and isinstance(block.get("text"), str)
        for block in blocks
    )


def _has_only_one_document(payload: Mapping[str, object]) -> bool:
    match payload:
        case {
            "body": {
                "input": [
                    {"role": "system", "content": system},
                    {"content": [dict() as attachment, *prompt]},
                ]
            }
        }:
            return (
                set(attachment) <= {"type", "filename", "file_data"}
                and (isinstance(system, str) or _text_only_blocks(system, "input_text"))
                and _text_only_blocks(prompt, "input_text")
            )
        case {
            "params": {
                "system": system,
                "messages": [{"content": [{"source": dict() as source}, *prompt]}],
            }
        }:
            return (
                set(source) == {"type", "media_type", "data"}
                and (isinstance(system, str) or _text_only_blocks(system, "text"))
                and _text_only_blocks(prompt, "text")
            )
        case _:
            return False


def validate_scan_attachment(intent: BatchIntent) -> None:
    if intent.recorded_scan_input is not None:
        intent.require_scan_binding()
        validate_provider_scan_attachment(intent.request, intent.provider, allow_opaque=True)


def validate_provider_scan_attachment(
    request: BatchRequest, provider: BatchProvider, *, allow_opaque: bool = False
) -> None:
    question = request.question
    if (
        not isinstance(question, (AnalysisQuestion, AmendmentsQuestion, SupplementQuestion))
        or (scan := question.ctx.scan) is None
        or scan.scan_input is None
    ):
        return
    payload = read_json(request.payload_json) if request.payload_json is not None else None
    if not isinstance(payload, dict) or not ({"method", "url", "body", "params"} & payload.keys()):
        if allow_opaque:
            return
        raise ValueError("Frozen provider request has no verifiable scan attachment")
    if (
        payload.get("custom_id") != request.custom_id
        or ("params" in payload) != (provider == "anthropic")
        or scan.media_type != "application/pdf"
        or not _scan_payload_slot(payload, request.model, scan.data)
    ):
        raise ValueError("Frozen provider attachment differs from the recorded scan")
    if not allow_opaque:
        if not _has_only_one_document(payload):
            raise ValueError("Frozen provider scan request contains unsupported input blocks")
        scan.verify_exact_input()
    request.verify_payload()


def _escaped_question_text(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)[1:-1]


def _shared_scan_payload(
    request: BatchRequest,
    question: AnalysisQuestion | AmendmentsQuestion | SupplementQuestion,
    payload: str,
) -> dict[str, str] | None:
    if isinstance(question, AnalysisQuestion):
        if question.kind not in ("analysis", "reanalysis") or question.ctx.text_source != "scan":
            return None
    elif isinstance(question, AmendmentsQuestion) and (
        question.ctx.source_kind not in ("committee_amendments", "senate_amendments")
        or (
            question.ctx.source_kind == "committee_amendments"
            and question.ctx.source_check is not None
        )
    ):
        return None
    context = question.ctx
    scan = context.scan
    if (
        context.text
        or scan is None
        or scan.media_type != "application/pdf"
        or scan.scan_input is None
        or not isinstance(scan.data, str)
        or len(scan.data) < _MIN_SHARED_QUESTION_BYTES
        or not _scan_data_fits(scan.data, scan.pages)
        or len(payload) - len(scan.data) > _MAX_SCAN_PAYLOAD_SHELL_CHARS
    ):
        return None
    if (
        isinstance(question, AmendmentsQuestion)
        and question.ctx.source_kind == "senate_amendments"
        and not _senate_scan_payload_matches(
            question.kind,
            question.ctx.model_dump(mode="json", exclude={"scan"}),
            scan.model_dump(mode="json", exclude={"data", "scan_input"}),
            scan.scan_input.model_dump(mode="json"),
        )
    ):
        return None
    if isinstance(question, SupplementQuestion) and not _supplement_scan_payload_matches(
        context.model_dump(mode="json", exclude={"scan"}),
        request.number,
        scan.model_dump(mode="json", exclude={"data", "scan_input"}),
        scan.scan_input.model_dump(mode="json"),
    ):
        return None
    start = payload.find(scan.data)
    if start < 0 or payload.find(scan.data, start + 1) >= 0:
        return None
    prefix, suffix = payload[:start], payload[start + len(scan.data) :]
    try:
        _validate_scan_payload_shell(request.custom_id, request.model, prefix, suffix)
        scan.verify_exact_input()
    except ValueError, TypeError, RecursionError:
        return None
    request.verify_payload()
    return {"profile": _SCAN_PAYLOAD_PROFILE, "prefix": prefix, "suffix": suffix}


def serialize_intent_request(request: BatchRequest) -> str:
    question = request.question
    payload = request.payload_json
    if request.payload_sha256 is not None and payload is not None:
        if (
            isinstance(question, AnalysisQuestion)
            and question.kind in ("analysis", "reanalysis")
            and len(question.ctx.text.encode()) >= _MIN_SHARED_QUESTION_BYTES
        ):
            escaped = _escaped_question_text(question.ctx.text)
            start = payload.find(escaped)
            if start >= 0 and payload.find(escaped, start + 1) < 0:
                request.verify_payload()
                values = request.model_dump(mode="json")
                values["payload_json"] = {
                    "profile": _PROVIDER_PAYLOAD_PROFILE,
                    "prefix": payload[:start],
                    "suffix": payload[start + len(escaped) :],
                }
                return json.dumps(
                    values, ensure_ascii=False, separators=(",", ":"), allow_nan=False
                )
        elif isinstance(question, (AnalysisQuestion, AmendmentsQuestion, SupplementQuestion)) and (
            reference := _shared_scan_payload(request, question, payload)
        ):
            values = request.model_dump(mode="json", exclude={"payload_json"})
            values["payload_json"] = reference
            return json.dumps(values, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return request.model_dump_json()


def capture_frozen_request_manifest(
    request: BatchRequest, provider: BatchProvider
) -> FrozenRequestManifest | None:
    payload = request.payload_json
    checksum = request.payload_sha256
    if payload is None or checksum is None:
        return None
    request.verify_payload()
    envelope = read_json(payload)
    if not isinstance(envelope, dict) or envelope.get("custom_id") != request.custom_id:
        return None
    body = envelope.get("body" if provider == "openai" else "params")
    if not isinstance(body, dict) or body.get("model") != request.model:
        return None
    options = body.copy()
    if provider == "openai":
        messages = options.pop("input", None)
        output_format = options.pop("text", None)
        if (
            envelope.get("method") != "POST"
            or envelope.get("url") != "/v1/responses"
            or not isinstance(messages, list)
            or not isinstance(output_format, dict)
            or not isinstance(output_format.get("format"), dict)
        ):
            return None
    else:
        system = options.pop("system", None)
        user_messages = options.pop("messages", None)
        output_config = options.get("output_config")
        if not isinstance(system, list) or not isinstance(user_messages, list):
            return None
        if not isinstance(output_config, dict):
            return None
        output_options = output_config.copy()
        output_format = output_options.pop("format", None)
        if not isinstance(output_format, dict):
            return None
        messages = {"system": system, "messages": user_messages}
        options["output_config"] = output_options
    return FrozenRequestManifest(
        provider=provider,
        purpose=request.call_kind,
        request_sha256=checksum,
        model=request.model,
        prompt_version=request.prompt_version,
        payload_bytes=len(payload.encode()),
        messages_sha256=hashlib.sha256(canonical_json(messages).encode()).hexdigest(),
        output_format_sha256=hashlib.sha256(canonical_json(output_format).encode()).hexdigest(),
        options_sha256=hashlib.sha256(canonical_json(options).encode()).hexdigest(),
    )


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


def _archive_parents(
    connection: sqlite3.Connection,
    receipt: RclArchiveInputReceipt | RclArchiveSelfAmendmentInputReceipt,
    meta: BatchItemMeta,
    owner: tuple[int, str] | None,
    selection: DocumentSelectionRecord,
) -> None:
    observation = DocumentSourceRows(connection).get_source_observation(
        receipt.source_observation_id
    )
    binding = meta.input_binding
    if (
        observation is None
        or binding is None
        or (observation.term, observation.number) != owner
        or (binding.term, binding.number) != owner
        or observation.purpose != "analysis"
        or observation.selection_id != selection.selection_id
        or (observation.document.kind, observation.document.url) != ("rcl", meta.source_url)
    ):
        raise ValueError("input_receipt_archive_observation_mismatch")
    document = observation.document
    parts = selection.selection.parts
    if isinstance(receipt, RclArchiveInputReceipt):
        if receipt.selected_version_trace_id is None and not ordinary_rcl_archive_document(
            document
        ):
            raise ValueError("input_receipt_archive_scope_mismatch")
        root = _archive_parts_parent(
            connection,
            selection.selection_id,
            receipt.extraction_id,
            document.url,
            parts,
            receipt.source_parts,
        )
        if receipt.selected_version_trace_id is not None:
            _selected_archive_version(connection, receipt, document, selection, root)
        return
    amendment = rcl_archive_self_amendment_url(document)
    if amendment is None or parts[-1].url != amendment:
        raise ValueError("input_receipt_archive_amendment_scope_mismatch")
    root = _archive_parts_parent(
        connection,
        selection.selection_id,
        receipt.extraction_id,
        document.url,
        parts[:-1],
        receipt.archive_parts,
    )
    for index in range(len(parts) - 1):
        _one(
            connection,
            "SELECT 1 FROM selection_artifacts"
            " WHERE selection_id=? AND part_scope='parts' AND part_index=? AND artifact_sha256=?",
            (selection.selection_id, index, root),
        )
    _loose_part_parent(
        connection, selection.selection_id, len(parts) - 1, parts[-1], receipt.amendment_part
    )
    if parts[-1].sha256 == root:
        raise ValueError("input_receipt_archive_amendment_root_mismatch")
    _one(
        connection,
        "SELECT 1 FROM artifact_reads WHERE requested_url=? AND artifact_sha256=?",
        (amendment, parts[-1].sha256),
    )


def _selected_archive_version(
    connection: sqlite3.Connection,
    receipt: RclArchiveInputReceipt,
    document: TextDocument,
    selection: DocumentSelectionRecord,
    root: str,
) -> None:
    trace = read_candidate_disposition_trace(
        _one(
            connection,
            "SELECT t.* FROM source_candidate_traces l"
            " JOIN candidate_disposition_traces t USING(trace_id)"
            " WHERE l.observation_id=? AND l.trace_id=?",
            (receipt.source_observation_id, receipt.selected_version_trace_id),
        )
    )
    peer_sha = selected_archive_version_peer(
        document, selection.selection, trace, root, receipt.source_parts[0].physical_index
    )
    if peer_sha is None:
        raise ValueError("input_receipt_archive_version_mismatch")
    _one(
        connection,
        "SELECT 1 FROM artifact_reads WHERE requested_url=? AND artifact_sha256=?",
        (document.alternatives[0].url, peer_sha),
    )


def _archive_parts_parent(
    connection: sqlite3.Connection,
    selection_id: str,
    extraction_id: str,
    url: str,
    parts: tuple[DocumentPart, ...],
    source_parts: tuple[ArchiveInputPart, ...],
) -> str:
    row = _one(
        connection,
        "SELECT e.* FROM selection_extractions l JOIN extraction_results e USING(extraction_id)"
        " WHERE l.selection_id=? AND l.extraction_id=?",
        (selection_id, extraction_id),
    )
    extraction = read_extraction_result(row)
    root = extraction.artifact_sha256
    _one(
        connection,
        "SELECT 1 FROM artifact_reads WHERE requested_url=? AND artifact_sha256=?",
        (url, root),
    )
    summaries = {part.physical_index: part for part in extraction.selected_parts}
    if (
        extraction.omitted_selected_parts != 0
        or extraction.selected_part_count != len(source_parts)
        or set(summaries) != {part.physical_index for part in source_parts}
    ):
        raise ValueError("input_receipt_archive_extraction_mismatch")
    for part, recorded in zip(parts, source_parts, strict=True):
        summary = summaries[recorded.physical_index]
        if (
            not part.members
            or (part.url, part.source_sha256) != (url, root)
            or (part.role, part.text_sha256, part.text_start, part.text_end)
            != (recorded.role, recorded.text_sha256, 0, recorded.chars)
            or not archive_member_matches(part, summary, chars=recorded.chars)
        ):
            raise ValueError("input_receipt_archive_part_mismatch")
    return root


def _block_parents(
    connection: sqlite3.Connection,
    receipt: PrimaryInputReceipt
    | SupplementInputReceipt
    | AmendmentInputReceipt
    | MultipartTextInputReceipt,
) -> None:
    companion = receipt.block_companion
    if companion is None:
        return
    if receipt.source_selection_id is None:
        raise ValueError("block companion requires its saved source selection")
    selection = _source_selection(connection, receipt.source_selection_id).selection
    for row in companion.rows:
        if row.part_index >= len(selection.parts):
            raise ValueError("block companion part is absent")
        if isinstance(receipt, MultipartTextInputReceipt):
            recorded = receipt.source_parts[row.part_index]
            expected = (
                receipt.extraction_id
                if isinstance(
                    receipt, (RclArchiveInputReceipt, RclArchiveSelfAmendmentInputReceipt)
                )
                and isinstance(recorded, ArchiveInputPart)
                else recorded.extraction_id
                if isinstance(recorded, LooseRclInputPart)
                else None
            )
        else:
            expected = receipt.extraction_id
        if expected != row.extraction_id:
            raise ValueError("block companion differs from its receipt extraction")
        part = selection.parts[row.part_index]
        saved = _one(
            connection,
            "SELECT e.* FROM selection_extractions l JOIN extraction_results e USING(extraction_id)"
            " WHERE l.selection_id=? AND l.extraction_id=?",
            (receipt.source_selection_id, row.extraction_id),
        )
        extraction = read_extraction_result(saved)
        matches = [p for p in extraction.selected_parts if p.physical_index == row.physical_index]
        if len(matches) != 1:
            raise ValueError("block companion physical part is absent")
        summary = matches[0]
        witness = summary.block_copy_witness
        if (
            witness is None
            or witness.witness_id != row.witness_id
            or row.run_index >= len(witness.runs)
            or extraction.artifact_sha256 != part.source_sha256
            or not archive_member_matches(part, summary, chars=summary.text_chars)
        ):
            raise ValueError("block companion extraction parent mismatch")
        run = witness.runs[row.run_index]
        segment = next(s for s in receipt.segments if s.start <= row.start < row.end <= s.end)
        if segment.source_start is None:
            raise ValueError("block companion source range mismatch")
        source_end = segment.source_start + segment.end - segment.start
        start = max(segment.source_start, run.source_start)
        end = min(source_end, run.source_end)
        clipped = (start, end) != (run.source_start, run.source_end)
        if clipped != (row.intersection == "clipped-v1") or (start, end) != (
            segment.source_start + row.start - segment.start,
            segment.source_start + row.end - segment.start,
        ):
            raise ValueError("block companion source range mismatch")
        if not clipped and row.text_sha256 != run.text_sha256:
            raise ValueError("block companion copy hash mismatch")


def _multipart_parents(
    connection: sqlite3.Connection,
    receipt: MultipartTextInputReceipt,
    meta: BatchItemMeta,
    owner: tuple[int, str] | None,
) -> None:
    _block_parents(connection, receipt)
    source = _source_selection(connection, receipt.source_selection_id)
    selection = source.selection
    fitted = meta.selection
    parts = selection.parts
    if (
        selection.scan_input is not None
        or (selection.question, selection.policy_version)
        != (fitted.question, fitted.policy_version)
        or any(span.stage == "analysis" for span in selection.coverage)
        or len(parts) != len(receipt.source_parts)
        or parts != fitted.without_text().parts
    ):
        raise ValueError("input_receipt_selection_mismatch")
    if isinstance(receipt, (RclArchiveInputReceipt, RclArchiveSelfAmendmentInputReceipt)):
        _archive_parents(connection, receipt, meta, owner, source)
        return
    if not isinstance(receipt, LooseMultipartInputReceipt) or len(
        {part.url for part in parts}
    ) != len(parts):
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
        _loose_part_parent(connection, receipt.source_selection_id, index, part, recorded)


def _loose_part_parent(
    connection: sqlite3.Connection,
    selection_id: str,
    index: int,
    part: DocumentPart,
    recorded: LooseRclInputPart,
) -> None:
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
        (selection_id, index),
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
        (selection_id, recorded.extraction_id),
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
    | RclArchiveInputReceipt
    | RclArchiveSelfAmendmentInputReceipt
    | SupplementInputReceipt
    | AmendmentInputReceipt,
    *,
    primary_meta: BatchItemMeta | None = None,
    owner: tuple[int, str] | None = None,
) -> None:
    if isinstance(receipt, MultipartTextInputReceipt):
        if primary_meta is None:
            raise ValueError("Multipart receipt requires its fitted selection")
        _multipart_parents(connection, receipt, primary_meta, owner)
        return
    _block_parents(connection, receipt)
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


def validate_secondary_receipt_parents(
    connection: sqlite3.Connection, receipt: SupplementInputReceipt | AmendmentInputReceipt
) -> None:
    _parents(connection, receipt)


def has_word_formula_input(request: BatchRequest) -> bool:
    question = request.question
    return isinstance(question, AnalysisQuestion) and question.ctx.word_formula_input is not None


def validate_word_formula_intent(connection: sqlite3.Connection, intent: BatchIntent) -> None:
    if not has_word_formula_input(intent.request):
        return
    checked = BatchIntent.model_validate_json(intent.model_dump_json())
    request, meta = checked.request, checked.meta
    receipt = meta.prepared_input_receipt
    if (
        not isinstance(request.question, AnalysisQuestion)
        or (companion := request.question.ctx.word_formula_input) is None
        or not isinstance(meta, BatchItemMeta)
        or not isinstance(receipt, PrimaryInputReceipt)
        or receipt.extraction_id is None
    ):
        raise ValueError("Word formula input requires exact durable parents")
    _parents(connection, receipt)
    observation = DocumentSourceRows(connection).get_source_observation(
        companion.source_observation_id
    )
    selection = _source_selection(connection, companion.selection_id).selection
    row = _one(
        connection,
        "SELECT result_json FROM extraction_results WHERE extraction_id=?",
        (receipt.extraction_id,),
    )
    extraction = ExtractionResult.model_validate_json(row["result_json"])
    if (
        observation is None
        or (observation.term, observation.number) != (request.term, request.number)
        or observation.purpose != "analysis"
        or observation.selection_id != companion.selection_id
        or (observation.document.kind, observation.document.url) != ("rcl", meta.source_url)
        or selection.state != "selected"
        or selection.parts[0].url != meta.source_url
        or not any(item.role == "word" for item in extraction.components)
    ):
        raise ValueError("Word formula input requires its selected native Word source")


def capture_submitted_supplement_scan_input(
    request: BatchRequest,
    manifest: FrozenRequestManifest,
    item: LlmBatchItem,
    queued: BatchItemMeta | DigestItemMeta,
) -> SupplementScanInputWitness | None:
    question, meta = request.question, item.meta
    if (
        not isinstance(question, SupplementQuestion)
        or not isinstance(meta, DigestItemMeta)
        or not isinstance(queued, DigestItemMeta)
        or meta
        != queued.model_copy(
            update={
                "request_sha256": request.payload_sha256,
                "frozen_request_manifest": meta.frozen_request_manifest,
                "supplement_scan_input": meta.supplement_scan_input,
            }
        )
        or meta.source_url is None
        or meta.document_number is None
    ):
        return None
    scan = question.ctx.scan
    if scan is None or scan.scan_input is None:
        return None
    context_json = canonical_json(question.ctx.model_dump(mode="json", exclude={"scan"}))
    if len(context_json.encode()) > MAX_SUPPLEMENT_SCAN_WITNESS_BYTES:
        return None
    validate_provider_scan_attachment(request, manifest.provider)
    payload = request.payload_json
    if payload is None:
        return None
    match read_json(payload):
        case {
            "body": {"input": [_, {"content": [_, {"type": "input_text", "text": str(prompt)}]}]}
        }:
            pass
        case {"params": {"messages": [{"content": [_, {"type": "text", "text": str(prompt)}]}]}}:
            pass
        case _:
            return None
    if prompt != build_supplement_prompt(question.ctx):
        return None
    witness = SupplementScanInputWitness(
        term=request.term,
        document_number=meta.document_number,
        source_url=meta.source_url,
        request_sha256=manifest.request_sha256,
        manifest_sha256=manifest.manifest_sha256,
        question_sha256=hashlib.sha256(
            canonical_json(question.model_dump(mode="json")).encode()
        ).hexdigest(),
        context_json=context_json,
        scan_input=scan.scan_input,
    )
    candidate = meta.model_copy(
        update={"frozen_request_manifest": manifest, "supplement_scan_input": witness}
    )
    candidate.require_supplement_scan_carrier(item.call_kind, item.term, item.number)
    return witness


def read_retained_supplement_scan_input(
    connection: sqlite3.Connection, result: ComputationResult
) -> SupplementScanInputWitness | None:
    record = result.record
    if result.purpose != "supplement" or not isinstance(record, SupplementRecord):
        return None
    row = next(
        _rows(
            connection,
            "SELECT i.*, b.provider, b.call_kind AS batch_kind FROM llm_batch_items i"
            " JOIN llm_batches b USING (batch_id) WHERE i.batch_id=? AND i.custom_id=?",
            (result.batch_id, result.custom_id),
        ),
        None,
    )
    if row is None:
        return None
    meta = batch_item_meta(row["call_kind"], row["meta_json"])
    if not isinstance(meta, DigestItemMeta) or meta.supplement_scan_input is None:
        return None
    witness = meta.supplement_scan_input
    meta.require_supplement_scan_carrier(row["call_kind"], row["term"], row["number"])
    manifest = meta.frozen_request_manifest
    if (
        row["consumed_at"] is None
        or row["batch_kind"] != "supplement"
        or (row["provider"], row["term"], row["number"])
        != (result.provider, result.term, result.number)
        or manifest is None
        or manifest.provider != result.provider
        or meta.input_binding != result.input_binding
        or witness.request_sha256 != result.request_sha256
        or (record.number, record.title, record.source_kind, record.source_url, record.scan_input)
        != (meta.document_number, meta.title, meta.source_kind, meta.source_url, meta.scan_input)
    ):
        raise ValueError("invalid_supplement_scan_input")
    return witness


def validate_multipart_intent(connection: sqlite3.Connection, intent: BatchIntent) -> None:
    if not isinstance(intent.meta.prepared_input_receipt, MultipartTextInputReceipt):
        return
    validated = BatchIntent.model_validate_json(intent.model_dump_json())
    meta = validated.meta
    if not isinstance(meta, BatchItemMeta) or not isinstance(
        meta.prepared_input_receipt, MultipartTextInputReceipt
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
    if not isinstance(item.meta.prepared_input_receipt, MultipartTextInputReceipt):
        return
    meta = batch_item_meta(item.call_kind, item.meta.model_dump_json())
    if not isinstance(meta, BatchItemMeta) or not isinstance(
        meta.prepared_input_receipt, MultipartTextInputReceipt
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
            has_manifest = meta.get("frozen_request_manifest") is not None
            has_submitted_witness = (
                "review_input_witness" in meta or "supplement_scan_input" in meta
            )
            question = request.get("question")
            ctx = question.get("ctx") if isinstance(question, dict) else None
            has_formula = isinstance(ctx, dict) and ctx.get("word_formula_input") is not None
            selection = meta.get("selection")
            has_scan_manifest = meta.get("scan_input") is not None or (
                isinstance(selection, dict) and selection.get("scan_input") is not None
            )
            if not (
                has_receipt
                or has_timing
                or frozen
                or has_text_reference
                or has_provider_reference
                or has_manifest
                or has_submitted_witness
                or has_formula
                or has_scan_manifest
            ):
                continue
            ref = _reference("llm_batch_intents", row["custom_id"])
            try:
                if "supplement_scan_input" in meta:
                    raise ValueError("Supplement scan input belongs to submitted supplements")
                actual = parse_batch_request(request)
                if actual.custom_id != row["custom_id"] or row["provider"] not in (
                    "anthropic",
                    "openai",
                ):
                    raise ValueError("input_snapshot_request_columns")
                if (
                    has_receipt
                    or has_timing
                    or has_text_reference
                    or has_manifest
                    or has_submitted_witness
                    or has_word_formula_input(actual)
                    or has_scan_manifest
                ):
                    intent = BatchIntent.model_validate(
                        {
                            "request": actual,
                            "meta": intent_metadata(actual, meta) if has_text_reference else meta,
                            "provider": row["provider"],
                            "state": row["state"],
                            "created_at": row["created_at"],
                        }
                    )
                    if has_scan_manifest:
                        validate_scan_attachment(intent)
                    if has_word_formula_input(actual):
                        validate_word_formula_intent(connection, intent)
                    elif has_receipt:
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
            has_manifest = raw.get("frozen_request_manifest") is not None
            if not (
                has_receipt
                or has_timing
                or has_manifest
                or "review_input_witness" in raw
                or "supplement_scan_input" in raw
            ):
                continue
            ref = _reference("llm_batch_items", (row["batch_id"], row["custom_id"]))
            try:
                item_meta = batch_item_meta(row["call_kind"], row["meta_json"])
                if isinstance(item_meta, DigestItemMeta):
                    item_meta.require_supplement_scan_carrier(
                        row["call_kind"], row["term"], row["number"]
                    )
                    item_meta.require_review_input_carrier(
                        row["call_kind"],
                        row["term"],
                        row["number"],
                        consumed=row["consumed_at"] is not None,
                    )
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
                if (
                    item_meta.frozen_request_manifest is not None
                    and item_meta.frozen_request_manifest.provider != parent["provider"]
                ):
                    raise ValueError("input_snapshot_manifest_provider")
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
