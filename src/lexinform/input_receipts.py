"""Corroborate bounded text-input metadata against an already frozen request."""

import hashlib
from collections.abc import Iterator

from lexinform.models.analysis import AmendmentsContext, BillContext, SupplementContext
from lexinform.models.artifacts import ArtifactIdentity
from lexinform.models.batch import (
    AmendmentsQuestion,
    AnalysisQuestion,
    BatchRequest,
    SupplementQuestion,
)
from lexinform.models.document_history import DocumentSelectionRecord
from lexinform.models.documents import DocumentPart, DocumentSelection
from lexinform.models.extractions import (
    ExtractedPartSummary,
    ExtractionResult,
    extraction_metadata_sha256,
)
from lexinform.models.input_receipts import (
    MAX_INPUT_RECEIPT_BYTES,
    MAX_INPUT_RECEIPT_INTEGER,
    MAX_INPUT_RECEIPT_SEGMENTS,
    AmendmentInputReceipt,
    PrimaryInputReceipt,
    SupplementInputReceipt,
    amendment_context_sha256,
    input_json_chunks,
    input_map_sha256,
    input_text_sha256,
    primary_context_sha256,
    supplement_context_sha256,
)
from lexinform.models.prepared_input import PreparedTextPart, PreparedTextSegment
from lexinform.models.sejm import TextDocument
from lexinform.text_projection import TextProjection, TextSegment


def ordinary_rcl_archive_document(document: TextDocument) -> bool:
    return document.kind == "rcl" and not (
        document.official_link_conflict
        or document.alternatives
        or document.required_urls
        or document.normative_urls
        or document.amendment_urls
        or document.extra_urls
    )


def rcl_archive_self_amendment_url(document: TextDocument) -> str | None:
    if (
        document.kind != "rcl"
        or document.official_link_conflict
        or document.required_urls
        or document.normative_urls
        or document.extra_urls
        or len(document.amendment_urls) != 1
        or len(document.alternatives) > 1
    ):
        return None
    (amendment,) = document.amendment_urls
    if amendment == document.url:
        return None
    if document.alternatives:
        (alternate,) = document.alternatives
        if alternate.url != amendment or not ordinary_rcl_archive_document(alternate):
            return None
    return amendment


def archive_member_matches(
    part: DocumentPart, summary: ExtractedPartSummary, *, chars: int
) -> bool:
    return (
        summary.sha256,
        summary.text_sha256,
        summary.text_chars,
        summary.member_path_sha256,
    ) == (
        part.sha256,
        part.text_sha256,
        chars,
        extraction_metadata_sha256(part.members),
    )


def _selection_id(
    selection: DocumentSelection, known: DocumentSelectionRecord | None
) -> str | None:
    if known is None:
        return None
    original = known.selection
    for field in DocumentSelection.model_fields:
        left, right = getattr(selection, field), getattr(original, field)
        if field in ("parts", "observed_parts"):
            if len(left) != len(right) or any(
                part.model_copy(update={"text": ""}) != saved
                for part, saved in zip(left, right, strict=True)
            ):
                raise ValueError("Known selection differs from the actual source decision")
        elif left != right:
            raise ValueError("Known selection differs from the actual source decision")
    if not selection.parts[0].text_sha256:
        return None
    digest = hashlib.sha256()
    for chunk in input_json_chunks(known, ensure_ascii=False):
        digest.update(chunk.encode())
    return "selection-v1:" + digest.hexdigest()


def _segments(projection: TextProjection, source: str) -> Iterator[PreparedTextSegment]:
    pending: TextSegment | None = None
    for segment in projection.segments:
        if segment.source_start is not None:
            for offset in range(0, segment.end - segment.start, 4096):
                length = min(4096, segment.end - segment.start - offset)
                if (
                    source[segment.source_start + offset : segment.source_start + offset + length]
                    != projection.text[segment.start + offset : segment.start + offset + length]
                ):
                    raise ValueError("Copied input differs from its declared source range")
        if pending is not None and (
            pending.source_start is None
            and segment.source_start is None
            or pending.source_start is not None
            and segment.source_start is not None
            and pending.source_start + pending.end - pending.start == segment.source_start
        ):
            pending = TextSegment(pending.start, segment.end, pending.source_start)
        else:
            if pending is not None:
                yield _segment(pending)
            pending = segment
    if pending is not None:
        yield _segment(pending)


def _segment(segment: TextSegment) -> PreparedTextSegment:
    if segment.source_start is None:
        return PreparedTextSegment(
            start=segment.start,
            end=segment.end,
            origin="generated",
            system_origin="input_transform",
        )
    return PreparedTextSegment(
        start=segment.start,
        end=segment.end,
        origin="source",
        part_index=0,
        source_start=segment.source_start,
        source_end=segment.source_start + segment.end - segment.start,
    )


def primary_input_receipt(
    request: BatchRequest,
    selection: DocumentSelection,
    *,
    projection: TextProjection | None,
    known_selection: DocumentSelectionRecord | None = None,
    body: ArtifactIdentity | None = None,
    extraction: ExtractionResult | None = None,
) -> PrimaryInputReceipt:
    if not isinstance(request.question, AnalysisQuestion) or request.call_kind not in (
        "analysis",
        "reanalysis",
    ):
        raise ValueError("The receipt profile requires a primary text request")
    return _text_input_receipt(
        request,
        selection,
        ctx=request.question.ctx,
        receipt_model=PrimaryInputReceipt,
        context_sha256=primary_context_sha256(request.question.ctx),
        projection=projection,
        known_selection=known_selection,
        body=body,
        extraction=extraction,
    )


def supplement_input_receipt(
    request: BatchRequest,
    selection: DocumentSelection,
    *,
    projection: TextProjection | None,
    known_selection: DocumentSelectionRecord | None = None,
    body: ArtifactIdentity | None = None,
    extraction: ExtractionResult | None = None,
) -> SupplementInputReceipt:
    if not isinstance(request.question, SupplementQuestion):
        raise ValueError("The receipt profile requires a supplement text request")
    return _text_input_receipt(
        request,
        selection,
        ctx=request.question.ctx,
        receipt_model=SupplementInputReceipt,
        context_sha256=supplement_context_sha256(request.question.ctx),
        projection=projection,
        known_selection=known_selection,
        body=body,
        extraction=extraction,
    )


def amendment_input_receipt(
    request: BatchRequest,
    selection: DocumentSelection,
    *,
    projection: TextProjection | None,
    known_selection: DocumentSelectionRecord | None = None,
    body: ArtifactIdentity | None = None,
    extraction: ExtractionResult | None = None,
) -> AmendmentInputReceipt:
    if not isinstance(request.question, AmendmentsQuestion):
        raise ValueError("The receipt profile requires an amendments text request")
    return _text_input_receipt(
        request,
        selection,
        ctx=request.question.ctx,
        receipt_model=AmendmentInputReceipt,
        context_sha256=amendment_context_sha256(request.question.ctx),
        projection=projection,
        known_selection=known_selection,
        body=body,
        extraction=extraction,
    )


def _text_input_receipt[
    Receipt: (PrimaryInputReceipt, SupplementInputReceipt, AmendmentInputReceipt)
](
    request: BatchRequest,
    selection: DocumentSelection,
    *,
    ctx: BillContext | SupplementContext | AmendmentsContext,
    receipt_model: type[Receipt],
    context_sha256: str,
    projection: TextProjection | None,
    known_selection: DocumentSelectionRecord | None,
    body: ArtifactIdentity | None,
    extraction: ExtractionResult | None,
) -> Receipt:
    if (
        ctx.scan is not None
        or selection.scan_input is not None
        or len(selection.parts) != 1
        or selection.parts[0].members
        or any(span.stage == "analysis" for span in selection.coverage)
    ):
        raise ValueError("The receipt profile requires one loose source part before input fitting")
    if (
        request.payload_json is None
        or request.payload_sha256 is None
        or input_text_sha256(request.payload_json) != request.payload_sha256
    ):
        raise ValueError("The receipt requires the exact frozen serialized request")
    part = selection.parts[0]
    source_sha = input_text_sha256(part.text)
    if part.text_sha256 and part.text_sha256 != source_sha:
        raise ValueError("Source text differs from its declared hash")
    if body is not None and body.sha256 != part.sha256:
        raise ValueError("Known body differs from the actual loose source part")
    if extraction is not None and (
        body is None
        or extraction.artifact_sha256 != body.sha256
        or extraction.output_text_sha256 != source_sha
        or extraction.output_chars != len(part.text)
        or extraction.physical_part_count != 1
        or extraction.selected_part_count != 1
        or extraction.omitted_selected_parts != 0
        or len(extraction.selected_parts) != 1
        or extraction.selected_parts[0].physical_index != 0
        or extraction.selected_parts[0].sha256 != part.sha256
        or extraction.selected_parts[0].text_sha256 != source_sha
        or extraction.selected_parts[0].text_chars != len(part.text)
    ):
        raise ValueError("Known extraction does not corroborate this whole loose source text")
    data: dict[str, object] = {
        "context_sha256": context_sha256,
        "request_sha256": request.payload_sha256,
        "text_sha256": input_text_sha256(ctx.text),
        "text_chars": len(ctx.text),
        "source_part": PreparedTextPart(
            text_sha256=source_sha, chars=len(part.text), role=part.role
        ),
        "source_selection_id": _selection_id(selection, known_selection),
        "artifact_sha256": body.sha256 if body is not None else None,
        "extraction_id": extraction.result_id if extraction is not None else None,
    }
    if projection is None:
        return receipt_model.model_validate(
            {**data, "retention": "unknown", "reason": "projection_unavailable"}
        )
    if (
        projection.text != ctx.text
        or projection.source_sha256 != source_sha
        or projection.source_length != len(part.text)
    ):
        raise ValueError("Projection does not identify the actual source and frozen context text")
    prefix: list[PreparedTextSegment] = []
    count = 0
    reason: str | None = None
    envelope = receipt_model.model_validate(
        {**data, "retention": "unknown", "reason": "projection_unavailable"}
    )
    remaining = MAX_INPUT_RECEIPT_BYTES - len(envelope.model_dump_json().encode()) - 256

    def observed() -> Iterator[PreparedTextSegment]:
        nonlocal count, remaining, reason
        for segment in _segments(projection, part.text):
            count += 1
            if count > MAX_INPUT_RECEIPT_INTEGER:
                raise ValueError("Observed segment count exceeds the receipt profile")
            if reason is None:
                size = len(segment.model_dump_json().encode()) + 1
                if len(prefix) == MAX_INPUT_RECEIPT_SEGMENTS:
                    reason = "segment_limit"
                elif size > remaining:
                    reason = "byte_limit"
                else:
                    prefix.append(segment)
                    remaining -= size
            yield segment

    map_sha = input_map_sha256(observed())
    return receipt_model.model_validate(
        {
            **data,
            "retention": "complete" if reason is None else "prefix",
            "reason": reason,
            "segment_count": count,
            "full_map_sha256": map_sha,
            "segments": tuple(prefix),
        }
    )
