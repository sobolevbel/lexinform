"""Join prospective primary receipts to parents from the actual captured read only."""

from lexinform.input_receipts import primary_input_receipt
from lexinform.models import (
    AnalysisQuestion,
    ArtifactIdentity,
    BatchIntent,
    BatchItemMeta,
    BatchRequest,
    DocumentPart,
    DocumentSelection,
    ExtractionResult,
    PrimaryInputReceipt,
)
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


def enrich_primary_input_intent(original: BatchIntent, receipt: PrimaryInputReceipt) -> BatchIntent:
    if not isinstance(original.meta, BatchItemMeta):
        raise ValueError("Primary receipt enrichment requires primary metadata")
    return BatchIntent.model_validate(
        {
            "request": original.request,
            "meta": original.meta.model_copy(update={"prepared_input_receipt": receipt}),
            "provider": original.provider,
            "state": original.state,
            "created_at": original.created_at,
        }
    )
