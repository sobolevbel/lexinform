"""Classify bill reads per operation without changing source selection or loading bytes."""

from typing import Literal

from lexinform.models.documents import DocumentSelection
from lexinform.sections import APPENDIX_KINDS, carries_the_document, document_kind

ADMISSION_POLICY_VERSION = "bill-read-v1"

BlockedSelection = Literal["waiting", "ambiguous", "unsupported"]
PrefilterAdmission = BlockedSelection | Literal["text", "no_text", "no_document"]
AnalysisAdmission = BlockedSelection | Literal["text", "scan", "metadata_only"]


def _blocked(selection: DocumentSelection) -> BlockedSelection | None:
    match selection.state:
        case "waiting":
            return "waiting"
        case "ambiguous":
            return "ambiguous"
        case "unsupported":
            return "unsupported"
    return None


def prefilter_admission(
    selection: DocumentSelection, text: str | None, *, pages: int, min_chars: int
) -> PrefilterAdmission:
    blocked = _blocked(selection)
    if blocked is not None:
        return blocked
    if text is None:
        return "no_text"
    if not carries_the_document(text, min_chars=min_chars, pages=pages):
        return "no_document"
    return "text"


def analysis_admission(
    selection: DocumentSelection, text: str | None, *, pages: int, min_chars: int
) -> AnalysisAdmission:
    blocked = _blocked(selection)
    if blocked is not None:
        return blocked
    if text is None:
        return "scan"
    if not carries_the_document(text, min_chars=min_chars, pages=pages) and (pages > 0 or not text):
        return "scan"
    if document_kind(text) in APPENDIX_KINDS:
        return "metadata_only"
    return "text"
