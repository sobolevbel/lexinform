"""Offline quote checks do not establish semantic support or paid-input coordinates."""

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import ClassVar, Literal

from lexinform.models.analysis import AnalysisRecord
from lexinform.models.reviews import EvidenceCitation, ReviewDocument, citation_document_in

ClaimEvidenceStatus = Literal[
    "legacy_missing",
    "claim_index_invalid",
    "claim_unavailable",
    "claim_hash_mismatch",
    "source_unavailable",
    "source_ambiguous",
    "source_role_mismatch",
    "source_hash_mismatch",
    "citation_mismatch",
    "quote_ambiguous",
    "quote_present",
]


def _require_raw_hashes(claim_text_sha256: str, source_text_sha256: str) -> None:
    if any(
        re.fullmatch(r"[0-9a-f]{64}", digest) is None
        for digest in (claim_text_sha256, source_text_sha256)
    ):
        raise ValueError("Claim and source bindings require raw-text SHA-256 digests")


@dataclass(frozen=True)
class _IndexedClaimEvidence:
    _claim_name: ClassVar[str]

    index: int
    claim_text_sha256: str
    source_text_sha256: str
    citation: EvidenceCitation

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise ValueError(f"A {self._claim_name} address requires a nonnegative integer index")
        _require_raw_hashes(self.claim_text_sha256, self.source_text_sha256)


@dataclass(frozen=True)
class KeyChangeEvidence(_IndexedClaimEvidence):
    _claim_name = "key-change"


@dataclass(frozen=True)
class AffectedGroupEvidence(_IndexedClaimEvidence):
    _claim_name = "affected-group"


@dataclass(frozen=True)
class ProposedCommencementEvidence:
    claim_text_sha256: str
    source_text_sha256: str
    citation: EvidenceCitation

    def __post_init__(self) -> None:
        _require_raw_hashes(self.claim_text_sha256, self.source_text_sha256)


@dataclass(frozen=True)
class ClaimEvidenceCheck:
    """Observed spans use Python string offsets in the explicitly supplied extracted text."""

    status: ClaimEvidenceStatus
    source_complete: bool | None = None
    quote_occurrences: int = 0
    observed_text_span: tuple[int, int] | None = None
    semantic: Literal["semantic_not_assessed"] = field(default="semantic_not_assessed", init=False)
    block_origins: Literal["unmapped"] = field(default="unmapped", init=False)
    paid_origins: Literal["unmapped"] = field(default="unmapped", init=False)

    @property
    def coverage(self) -> Literal["complete", "partial", "unknown"]:
        return (
            "unknown"
            if self.source_complete is None
            else "complete"
            if self.source_complete
            else "partial"
        )

    def __post_init__(self) -> None:
        if self.quote_occurrences < 0:
            raise ValueError("Quote counts cannot be negative")
        if self.status == "quote_present" and self.quote_occurrences != 1:
            raise ValueError("A unique quote requires one observed occurrence")
        if self.status == "quote_ambiguous" and self.quote_occurrences < 2:
            raise ValueError("An ambiguous quote requires multiple observed occurrences")
        if self.status not in ("quote_present", "quote_ambiguous") and self.quote_occurrences:
            raise ValueError("Unchecked or mismatched quotes cannot claim observed occurrences")
        if self.quote_occurrences and self.source_complete is None:
            raise ValueError("Observed quotes require declared source coverage")
        if self.observed_text_span is not None:
            start, end = self.observed_text_span
            if self.status != "quote_present" or start < 0 or end <= start:
                raise ValueError("Only a unique literal quote has an observed text span")


def validate_key_change_evidence(
    record: AnalysisRecord,
    evidence: KeyChangeEvidence | None,
    documents: tuple[ReviewDocument, ...],
) -> ClaimEvidenceCheck:
    if evidence is not None and not isinstance(evidence, KeyChangeEvidence):
        raise TypeError("Expected key-change evidence")
    return _validate_indexed_claim(record.analysis.key_changes, evidence, documents)


def validate_affected_group_evidence(
    record: AnalysisRecord,
    evidence: AffectedGroupEvidence | None,
    documents: tuple[ReviewDocument, ...],
) -> ClaimEvidenceCheck:
    if evidence is not None and not isinstance(evidence, AffectedGroupEvidence):
        raise TypeError("Expected affected-group evidence")
    return _validate_indexed_claim(record.analysis.affected_groups, evidence, documents)


def validate_proposed_commencement_evidence(
    record: AnalysisRecord,
    evidence: ProposedCommencementEvidence | None,
    documents: tuple[ReviewDocument, ...],
) -> ClaimEvidenceCheck:
    """Check an exact quote in proposed text for the saved effective-date field."""
    if evidence is None:
        return ClaimEvidenceCheck("legacy_missing")
    if not isinstance(evidence, ProposedCommencementEvidence):
        raise TypeError("Expected proposed-commencement evidence")
    claim = record.analysis.effective_date
    if claim is None or not claim.strip():
        return ClaimEvidenceCheck("claim_unavailable")
    checked = _validate_claim_text(claim, evidence, documents)
    if checked.status == "quote_present" and checked.observed_text_span is None:
        return ClaimEvidenceCheck("citation_mismatch", checked.source_complete)
    return checked


def _validate_indexed_claim(
    claims: Sequence[str],
    evidence: _IndexedClaimEvidence | None,
    documents: tuple[ReviewDocument, ...],
) -> ClaimEvidenceCheck:
    if evidence is None:
        return ClaimEvidenceCheck("legacy_missing")
    if evidence.index >= len(claims):
        return ClaimEvidenceCheck("claim_index_invalid")
    return _validate_claim_text(claims[evidence.index], evidence, documents)


def _validate_claim_text(
    claim: str,
    evidence: _IndexedClaimEvidence | ProposedCommencementEvidence,
    documents: tuple[ReviewDocument, ...],
) -> ClaimEvidenceCheck:
    if hashlib.sha256(claim.encode()).hexdigest() != evidence.claim_text_sha256:
        return ClaimEvidenceCheck("claim_hash_mismatch")
    matches = tuple(doc for doc in documents if doc.identity == evidence.citation.document_id)
    if not matches:
        return ClaimEvidenceCheck("source_unavailable")
    if len(matches) > 1:
        return ClaimEvidenceCheck("source_ambiguous")
    source = matches[0]
    if not source.text.strip():
        return ClaimEvidenceCheck("source_unavailable", source.complete)
    if source.role != "project_text":
        return ClaimEvidenceCheck("source_role_mismatch", source.complete)
    if (
        source.sha256 != evidence.source_text_sha256
        or hashlib.sha256(source.text.encode()).hexdigest() != evidence.source_text_sha256
    ):
        return ClaimEvidenceCheck("source_hash_mismatch", source.complete)
    if citation_document_in(evidence.citation, (source,), {"project_text"}) is None:
        return ClaimEvidenceCheck("citation_mismatch", source.complete)
    pattern = r"\s+".join(re.escape(word) for word in evidence.citation.quote.split())
    count = 0
    first_span: tuple[int, int] | None = None
    for match in re.finditer(f"(?=({pattern}))", source.text):
        count += 1
        if first_span is None:
            first_span = match.span(1)
    if first_span is None:
        return ClaimEvidenceCheck("citation_mismatch", source.complete)
    if count != 1:
        return ClaimEvidenceCheck("quote_ambiguous", source.complete, count)
    start, end = first_span
    span = (start, end) if source.text[start:end] == evidence.citation.quote else None
    return ClaimEvidenceCheck("quote_present", source.complete, 1, span)
