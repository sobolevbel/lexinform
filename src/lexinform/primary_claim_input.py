"""Offline span checks describe frozen primary context text, never semantic or paid support."""

import re
from dataclasses import dataclass, field
from typing import Literal

from lexinform.models.analysis import AnalysisRecord
from lexinform.models.batch import AnalysisQuestion, BatchIntent, BatchItemMeta
from lexinform.models.document_history import DocumentSelectionRecord
from lexinform.models.input_receipts import (
    ArchiveInputPart,
    LooseRclInputReceipt,
    PrimaryInputReceipt,
    RclArchiveInputReceipt,
    RclArchiveSelfAmendmentInputReceipt,
    RclPairedArchiveInputReceipt,
    SejmSelfAmendmentInputReceipt,
)
from lexinform.models.reviews import ReviewDocument
from lexinform.primary_claims import (
    ClaimEvidenceCheck,
    KeyChangeEvidence,
    validate_key_change_evidence,
)

ClaimInputStatus = Literal["retained", "omitted", "unknown"]
ClaimInputReason = Literal[
    "evidence_unresolved",
    "intent_missing",
    "intent_invalid",
    "primary_intent_required",
    "receipt_missing",
    "request_binding_mismatch",
    "source_binding_unavailable",
    "source_binding_mismatch",
    "map_unknown",
    "map_prefix",
    "source_map_mismatch",
    "input_positions_ambiguous",
    "retained_source_span",
    "no_continuous_source_mapping",
]


type MultipartCitationReceipt = (
    LooseRclInputReceipt
    | RclArchiveInputReceipt
    | RclArchiveSelfAmendmentInputReceipt
    | SejmSelfAmendmentInputReceipt
)


def multipart_claim_part_role(
    receipt: MultipartCitationReceipt, index: int
) -> Literal["bill", "normative_annex", "self_amendment"] | None:
    if index == 0:
        return "bill"
    if (
        isinstance(receipt, LooseRclInputReceipt)
        and 0 < index < len(receipt.source_parts)
        and receipt.source_parts[index].role == "normative_annex"
    ):
        return "normative_annex"
    if isinstance(receipt, SejmSelfAmendmentInputReceipt) and index == 1:
        return "self_amendment"
    if (
        isinstance(receipt, RclArchiveSelfAmendmentInputReceipt)
        and index == len(receipt.source_parts) - 1
    ):
        return "self_amendment"
    return None


@dataclass(frozen=True)
class ClaimInputCheck:
    """Input spans use Python string offsets in the supplied frozen primary context."""

    evidence: ClaimEvidenceCheck
    status: ClaimInputStatus
    reason: ClaimInputReason
    input_span: tuple[int, int] | None = None
    semantic: Literal["semantic_not_assessed"] = field(default="semantic_not_assessed", init=False)
    block_origins: Literal["unmapped"] = field(default="unmapped", init=False)
    physical_origins: Literal["unmapped"] = field(default="unmapped", init=False)

    def __post_init__(self) -> None:
        if len(self.reason) > 64:
            raise ValueError("Input-check reasons must fit 64 characters")
        if (self.status == "retained") != (self.input_span is not None):
            raise ValueError("Only retained source text has an input span")
        if self.status != "unknown" and self.evidence.observed_text_span is None:
            raise ValueError("A retention decision requires a unique literal source span")
        if (
            self.status == "retained"
            and self.reason != "retained_source_span"
            or self.status == "omitted"
            and self.reason != "no_continuous_source_mapping"
            or self.status == "unknown"
            and self.reason in ("retained_source_span", "no_continuous_source_mapping")
        ):
            raise ValueError("Input-check reason must describe its retention status")
        if self.input_span is not None:
            start, end = self.input_span
            if type(start) is not int or type(end) is not int or start < 0 or end <= start:
                raise ValueError("Input spans require nonempty nonnegative integer coordinates")
            source_span = self.evidence.observed_text_span
            if source_span is not None and end - start != source_span[1] - source_span[0]:
                raise ValueError("A retained input span must match its literal source length")


def validate_key_change_input(
    record: AnalysisRecord,
    evidence: KeyChangeEvidence | None,
    documents: tuple[ReviewDocument, ...],
    *,
    intent: BatchIntent | None,
    source_selection: DocumentSelectionRecord | None = None,
) -> ClaimInputCheck:
    return _validate_key_change_input(
        record, evidence, documents, intent=intent, source_selection=source_selection
    )


def _validate_key_change_input(
    record: AnalysisRecord,
    evidence: KeyChangeEvidence | None,
    documents: tuple[ReviewDocument, ...],
    *,
    intent: BatchIntent | None,
    source_selection: DocumentSelectionRecord | None = None,
    verified_multipart_selection: DocumentSelectionRecord | None = None,
    multipart_part_index: int = 0,
    allow_annex_locator: bool = False,
) -> ClaimInputCheck:
    checked = validate_key_change_evidence(
        record, evidence, documents, allow_annex_locator=allow_annex_locator
    )
    if evidence is None or checked.observed_text_span is None:
        return ClaimInputCheck(checked, "unknown", "evidence_unresolved")
    if intent is None:
        return ClaimInputCheck(checked, "unknown", "intent_missing")
    try:
        frozen = BatchIntent.model_validate_json(intent.model_dump_json())
    except ValueError, TypeError:
        return ClaimInputCheck(checked, "unknown", "intent_invalid")
    question, meta = frozen.request.question, frozen.meta
    if (
        not isinstance(question, AnalysisQuestion)
        or question.kind not in ("analysis", "reanalysis")
        or not isinstance(meta, BatchItemMeta)
        or question.ctx.scan is not None
        or question.ctx.text_source == "scan"
        or meta.text_source == "scan"
        or meta.selection.scan_input is not None
    ):
        return ClaimInputCheck(checked, "unknown", "primary_intent_required")
    receipt = meta.prepared_input_receipt
    if receipt is None:
        return ClaimInputCheck(checked, "unknown", "receipt_missing")
    if isinstance(receipt, RclPairedArchiveInputReceipt):
        return ClaimInputCheck(checked, "unknown", "source_binding_unavailable")
    ctx = question.ctx
    if (
        frozen.request.number != ctx.number
        or meta.input_binding is not None
        and (meta.input_binding.term, meta.input_binding.number)
        != (frozen.request.term, frozen.request.number)
        or record.request_sha256 is None
        or record.request_sha256 != frozen.request.payload_sha256
        or record.input_chars != len(ctx.text)
        or meta.input_chars != len(ctx.text)
        or meta.source_kind != ctx.source_kind
        or record.source_kind != ctx.source_kind
    ):
        return ClaimInputCheck(checked, "unknown", "request_binding_mismatch")
    document = next(doc for doc in documents if doc.identity == evidence.citation.document_id)
    if isinstance(receipt, LooseRclInputReceipt) and verified_multipart_selection is None:
        if multipart_part_index != 0:
            return ClaimInputCheck(checked, "unknown", "source_binding_mismatch")
        binding = _loose_rcl_main_binding(
            document, meta, receipt, record.source_url, source_selection
        )
    elif isinstance(
        receipt,
        (
            LooseRclInputReceipt,
            RclArchiveInputReceipt,
            RclArchiveSelfAmendmentInputReceipt,
            SejmSelfAmendmentInputReceipt,
        ),
    ):
        binding = _bound_multipart_part(
            document,
            meta,
            receipt,
            record.source_url,
            verified_multipart_selection,
            multipart_part_index,
        )
    elif multipart_part_index != 0:
        return ClaimInputCheck(checked, "unknown", "source_binding_mismatch")
    else:
        binding = _source_binding(document, meta, receipt, record.source_url)
    if binding is not None:
        return ClaimInputCheck(checked, "unknown", binding)
    runs: list[tuple[int, int, int, int]] = []
    for segment in receipt.segments:
        if segment.origin != "source" or segment.part_index != multipart_part_index:
            continue
        start, end = segment.source_start, segment.source_end
        assert start is not None and end is not None, (
            "PreparedTextSegment requires source coordinates"
        )
        if document.text[start:end] != ctx.text[segment.start : segment.end]:
            return ClaimInputCheck(checked, "unknown", "source_map_mismatch")
        if runs and runs[-1][1] == start and runs[-1][3] == segment.start:
            first, _, emitted, _ = runs[-1]
            runs[-1] = (first, end, emitted, segment.end)
        else:
            runs.append((start, end, segment.start, segment.end))
    if receipt.retention != "complete":
        return ClaimInputCheck(
            checked, "unknown", "map_prefix" if receipt.retention == "prefix" else "map_unknown"
        )
    source_start, source_end = checked.observed_text_span
    quote = document.text[source_start:source_end]
    first = ctx.text.find(quote)
    if first >= 0 and ctx.text.find(quote, first + 1) >= 0:
        return ClaimInputCheck(checked, "unknown", "input_positions_ambiguous")
    spans = tuple(
        (emitted + source_start - start, emitted + source_end - start)
        for start, end, emitted, _ in runs
        if start <= source_start and end >= source_end
    )
    if len(spans) > 1:
        return ClaimInputCheck(checked, "unknown", "input_positions_ambiguous")
    if spans:
        return ClaimInputCheck(checked, "retained", "retained_source_span", spans[0])
    return ClaimInputCheck(checked, "omitted", "no_continuous_source_mapping")


def _source_binding(
    document: ReviewDocument,
    meta: BatchItemMeta,
    receipt: PrimaryInputReceipt,
    record_url: str | None,
) -> ClaimInputReason | None:
    if len(meta.selection.parts) != 1 or meta.selection.parts[0].members:
        return "source_binding_unavailable"
    part = meta.selection.parts[0]
    chars = (
        part.text_end - part.text_start
        if part.text_end is not None
        else len(part.text)
        if part.text
        else None
    )
    if (
        not document.file_sha256
        or not receipt.artifact_sha256
        or not document.url
        or not meta.source_url
        or not part.url
        or not part.sha256
        or not part.text_sha256
        or chars is None
    ):
        return "source_binding_unavailable"
    if (
        document.file_sha256 != receipt.artifact_sha256
        or part.sha256 != receipt.artifact_sha256
        or document.url != part.url
        or meta.source_url != part.url
        or record_url is not None
        and record_url != part.url
        or document.sha256 != receipt.source_part.text_sha256
        or part.text_sha256 != document.sha256
        or part.role != "bill"
        or receipt.source_part.role != part.role
        or receipt.source_part.chars != len(document.text)
        or chars != len(document.text)
        or part.text_start < 0
        or part.text
        and part.text != document.text
    ):
        return "source_binding_mismatch"
    return None


def _loose_rcl_main_binding(
    document: ReviewDocument,
    meta: BatchItemMeta,
    receipt: LooseRclInputReceipt,
    record_url: str | None,
    source_selection: DocumentSelectionRecord | None,
) -> ClaimInputReason | None:
    if source_selection is None or not document.url or not document.file_sha256 or not record_url:
        return "source_binding_unavailable"
    source = source_selection.selection
    if (
        source_selection.selection_id != receipt.source_selection_id
        or (source.question, source.policy_version)
        != (meta.selection.question, meta.selection.policy_version)
        or any(span.stage == "analysis" for span in source.coverage)
        or source.parts != meta.selection.without_text().parts
        or len({part.url for part in source.parts}) != len(source.parts)
    ):
        return "source_binding_mismatch"
    part = source.parts[0]
    recorded = receipt.source_parts[0]
    if (
        part.role != "bill"
        or not re.fullmatch(r"[0-9a-f]{64}", part.sha256)
        or part.sha256 != document.file_sha256
        or part.url != document.url
        or record_url != part.url
        or recorded.text_sha256 != document.sha256
        or recorded.chars != len(document.text)
    ):
        return "source_binding_mismatch"
    return None


def _bound_multipart_part(
    document: ReviewDocument,
    meta: BatchItemMeta,
    receipt: MultipartCitationReceipt,
    record_url: str | None,
    selection: DocumentSelectionRecord | None,
    part_index: int,
) -> ClaimInputReason | None:
    if selection is None or not document.file_sha256 or not document.url or not record_url:
        return "source_binding_unavailable"
    role = multipart_claim_part_role(receipt, part_index)
    if (
        role is None
        or selection.selection_id != receipt.source_selection_id
        or part_index >= len(selection.selection.parts)
        or selection.selection.parts != meta.selection.without_text().parts
    ):
        return "source_binding_mismatch"
    main = selection.selection.parts[0]
    part, saved = selection.selection.parts[part_index], receipt.source_parts[part_index]
    if (
        part.role != role
        or saved.role != role
        or part.sha256 != document.file_sha256
        or part.url != document.url
        or main.url != record_url
        or main.url != meta.source_url
        or bool(part.members) != isinstance(saved, ArchiveInputPart)
        or part.text_sha256 != document.sha256
        or saved.text_sha256 != document.sha256
        or part.text_start != 0
        or part.text_end != len(document.text)
        or saved.chars != len(document.text)
    ):
        return "source_binding_mismatch"
    return None
