"""Join prospective text receipts to parents from the actual captured read only."""

from lexinform.input_receipts import (
    amendment_input_receipt,
    primary_input_receipt,
    supplement_input_receipt,
)
from lexinform.models import (
    AmendmentInputReceipt,
    AmendmentsQuestion,
    AnalysisQuestion,
    ArtifactIdentity,
    BatchIntent,
    BatchItemMeta,
    BatchRequest,
    DigestItemMeta,
    DocumentPart,
    DocumentSelection,
    ExtractionResult,
    PrimaryInputReceipt,
    SupplementInputReceipt,
    SupplementQuestion,
)
from lexinform.models.input_receipts import (
    MAX_INPUT_RECEIPT_SEGMENTS,
    LooseRclInputPart,
    LooseRclInputReceipt,
    input_map_sha256,
    primary_context_sha256,
)
from lexinform.prepared_input import prepared_text_input
from lexinform.services.document_history import DocumentSourceRead, SourceReadCapture
from lexinform.text_projection import TextProjection


def _body(part: DocumentPart, source: SourceReadCapture | None) -> ArtifactIdentity | None:
    if source is None or not part.sha256:
        return None
    matched = None
    for candidate in source.artifacts:
        if candidate.sha256 != part.sha256:
            continue
        if matched is not None and matched.byte_size != candidate.byte_size:
            return None
        matched = candidate
    return matched


def _extraction(
    part: DocumentPart, body: ArtifactIdentity | None, source: SourceReadCapture | None
) -> ExtractionResult | None:
    if source is None or body is None or not part.text_sha256:
        return None
    matched = None
    for candidate in source.extractions:
        if (
            candidate.artifact_sha256 != body.sha256
            or candidate.output_text_sha256 != part.text_sha256
            or candidate.output_chars != len(part.text)
            or candidate.physical_part_count != 1
            or candidate.selected_part_count != 1
            or candidate.omitted_selected_parts != 0
            or len(candidate.selected_parts) != 1
        ):
            continue
        summary = candidate.selected_parts[0]
        if (
            summary.physical_index != 0
            or summary.sha256 != body.sha256
            or summary.text_sha256 != part.text_sha256
            or summary.text_chars != len(part.text)
        ):
            continue
        if matched is not None and matched != candidate:
            return None
        matched = candidate
    return matched


def capture_primary_input_receipt(
    request: BatchRequest,
    selection: DocumentSelection,
    projection: TextProjection | None,
    source: SourceReadCapture | None,
) -> PrimaryInputReceipt | None:
    if (
        not isinstance(request.question, AnalysisQuestion)
        or request.call_kind not in ("analysis", "reanalysis")
        or request.question.ctx.scan is not None
        or request.question.ctx.text_source == "scan"
        or selection.scan_input is not None
        or len(selection.parts) != 1
        or selection.parts[0].members
        or any(span.stage == "analysis" for span in selection.coverage)
    ):
        return None
    part = selection.parts[0]
    body = _body(part, source)
    return primary_input_receipt(
        request,
        selection,
        projection=projection,
        known_selection=source.selection if isinstance(source, DocumentSourceRead) else None,
        body=body,
        extraction=_extraction(part, body, source),
    )


def capture_loose_rcl_input_receipt(
    request: BatchRequest,
    selection: DocumentSelection,
    projection: TextProjection | None,
    source: SourceReadCapture | None,
) -> LooseRclInputReceipt | None:
    if (
        not isinstance(request.question, AnalysisQuestion)
        or request.call_kind not in ("analysis", "reanalysis")
        or request.question.ctx.source_kind != "rcl"
        or request.question.ctx.scan is not None
        or request.question.ctx.text_source == "scan"
        or request.payload_sha256 is None
        or projection is None
        or len(projection.segments) > MAX_INPUT_RECEIPT_SEGMENTS
        or not isinstance(source, DocumentSourceRead)
        or source.document.kind != "rcl"
        or not 2 <= len(selection.parts) <= 5
        or source.document.url != selection.parts[0].url
        or selection.scan_input is not None
        or any(span.stage == "analysis" for span in selection.coverage)
    ):
        return None
    ctx = request.question.ctx
    if projection.text != ctx.text:
        return None
    parts: list[LooseRclInputPart] = []
    for part in selection.parts:
        if (
            part.members
            or not part.text_sha256
            or part.text_start != 0
            or part.text_end != len(part.text)
        ):
            return None
        body = _body(part, source)
        extraction = _extraction(part, body, source)
        if extraction is None:
            return None
        parts.append(
            LooseRclInputPart(
                role=part.role,
                chars=len(part.text),
                text_sha256=part.text_sha256,
                extraction_id=extraction.result_id,
            )
        )
    request.verify_payload()
    prepared = prepared_text_input(selection, ctx.text, projection=projection)
    if (
        prepared.source_selection_id != source.selection.selection_id
        or len(prepared.segments) > MAX_INPUT_RECEIPT_SEGMENTS
    ):
        return None
    return LooseRclInputReceipt(
        source_selection_id=prepared.source_selection_id,
        source_parts=tuple(parts),
        context_sha256=primary_context_sha256(ctx),
        request_sha256=request.payload_sha256,
        text_sha256=prepared.text_sha256,
        text_chars=prepared.text_chars,
        full_map_sha256=input_map_sha256(prepared.segments),
        segments=prepared.segments,
    )


def enrich_primary_input_intent(
    original: BatchIntent, receipt: PrimaryInputReceipt | LooseRclInputReceipt
) -> BatchIntent:
    if not isinstance(original.meta, BatchItemMeta):
        raise ValueError("Primary receipt enrichment requires primary metadata")
    return _enrich_input_intent(original, receipt)


def _enrich_input_intent(
    original: BatchIntent,
    receipt: PrimaryInputReceipt
    | LooseRclInputReceipt
    | SupplementInputReceipt
    | AmendmentInputReceipt,
) -> BatchIntent:
    return BatchIntent.model_validate(
        {
            "request": original.request,
            "meta": original.meta.model_copy(update={"prepared_input_receipt": receipt}),
            "provider": original.provider,
            "state": original.state,
            "created_at": original.created_at,
        }
    )


def capture_secondary_input_receipt(
    request: BatchRequest,
    selection: DocumentSelection,
    projection: TextProjection | None,
    source: SourceReadCapture | None,
) -> SupplementInputReceipt | AmendmentInputReceipt | None:
    if (
        not isinstance(request.question, (SupplementQuestion, AmendmentsQuestion))
        or request.question.ctx.scan is not None
        or selection.scan_input is not None
        or not isinstance(source, DocumentSourceRead)
        or projection is None
        or len(selection.parts) != 1
        or selection.parts[0].members
        or any(span.stage == "analysis" for span in selection.coverage)
    ):
        return None
    part = selection.parts[0]
    body = _body(part, source)
    extraction = _extraction(part, body, source)
    if body is None or extraction is None:
        return None
    builder = (
        amendment_input_receipt
        if isinstance(request.question, AmendmentsQuestion)
        else supplement_input_receipt
    )
    return builder(
        request,
        selection,
        projection=projection,
        known_selection=source.selection,
        body=body,
        extraction=extraction,
    )


def capture_supplement_input_receipt(
    request: BatchRequest,
    selection: DocumentSelection,
    projection: TextProjection | None,
    source: SourceReadCapture | None,
) -> SupplementInputReceipt | None:
    if not isinstance(request.question, SupplementQuestion):
        return None
    receipt = capture_secondary_input_receipt(request, selection, projection, source)
    return receipt if isinstance(receipt, SupplementInputReceipt) else None


def enrich_secondary_input_intent(
    original: BatchIntent, receipt: SupplementInputReceipt | AmendmentInputReceipt
) -> BatchIntent:
    if not isinstance(original.meta, DigestItemMeta):
        raise ValueError("Secondary receipt enrichment requires secondary metadata")
    return _enrich_input_intent(original, receipt)


def enrich_supplement_input_intent(
    original: BatchIntent, receipt: SupplementInputReceipt
) -> BatchIntent:
    if not isinstance(original.meta, DigestItemMeta):
        raise ValueError("Supplement receipt enrichment requires secondary metadata")
    return _enrich_input_intent(original, receipt)
