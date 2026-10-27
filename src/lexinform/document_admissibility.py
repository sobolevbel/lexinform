"""Classify document reads per operation without changing source selection or loading bytes."""

from typing import Literal

from lexinform.commission_reviews import commission_text_matches
from lexinform.document_fingerprints import text_digest
from lexinform.models.documents import DocumentSelection
from lexinform.models.reviews import DocumentRole as ReviewRole
from lexinform.sections import APPENDIX_KINDS, carries_the_document, document_kind

ADMISSION_POLICY_VERSION = "bill-read-v1"
REVIEW_ADMISSION_POLICY_VERSION = "review-read-v1"

BlockedSelection = Literal["waiting", "ambiguous", "unsupported"]
PrefilterAdmission = BlockedSelection | Literal["text", "no_text", "no_document"]
AnalysisAdmission = BlockedSelection | Literal["text", "scan", "metadata_only"]
ReviewAdmission = Literal["digest_mismatch", "role_mismatch", "partial", "complete"]


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


def review_admission(
    selection: DocumentSelection,
    text: str,
    *,
    pages: int,
    min_chars: int,
    oversize: bool,
    role: ReviewRole,
    expected_digest: str | None = None,
) -> ReviewAdmission:
    if expected_digest is not None and text_digest(text) != expected_digest:
        return "digest_mismatch"
    if not commission_text_matches(role, text):
        return "role_mismatch"
    if (
        bool(text.strip())
        and not oversize
        and carries_the_document(text, min_chars=min_chars, pages=pages)
        and not selection.rejected
        and selection.state not in ("ambiguous", "unsupported")
    ):
        return "complete"
    return "partial"
