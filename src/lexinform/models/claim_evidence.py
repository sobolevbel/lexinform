"""Claim addresses and citations retained alongside a primary result."""

import hashlib
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.json_storage import canonical_json, read_json
from lexinform.models.analysis import Analysis
from lexinform.models.block_input import PreparedBlockCopy
from lexinform.models.physical_inventory import Position, Sha256
from lexinform.models.reviews import EvidenceCitation

PrimaryClaimField = Literal[
    "summary", "key_changes", "affected_groups", "practical_impact", "effective_date"
]


class PrimaryClaimCitation(BaseModel):
    """Bind a citation to one exact claim without asserting that it supports the claim."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: PrimaryClaimField
    index: int | None = Field(
        default=None, ge=0, strict=True, exclude_if=lambda value: value is None
    )
    claim_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    citation: EvidenceCitation

    @model_validator(mode="after")
    def validate_address(self) -> Self:
        if (self.index is not None) != (self.field in ("key_changes", "affected_groups")):
            raise ValueError("Only repeated claim fields require an index")
        for value, limit in (
            (self.citation.document_id, 256),
            (self.citation.locator, 256),
            (self.citation.quote, 1024),
        ):
            if not value.strip() or len(value) > limit:
                raise ValueError("Citation values must be nonblank and bounded")
        return self

    def require_claim(self, answer: Analysis) -> None:
        text: str | None
        if self.field in ("key_changes", "affected_groups"):
            values = answer.key_changes if self.field == "key_changes" else answer.affected_groups
            index = self.index
            if index is None or index >= len(values):
                raise ValueError("Citation claim index is outside the retained answer")
            text = values[index]
        elif self.field == "summary":
            text = answer.summary
        elif self.field == "practical_impact":
            text = answer.practical_impact
        else:
            text = answer.effective_date
        if (
            not text
            or not text.strip()
            or hashlib.sha256(text.encode()).hexdigest() != self.claim_text_sha256
        ):
            raise ValueError("Citation must refer to the exact retained claim")


MAX_PRIMARY_CLAIM_RECEIPT_BYTES = 8192


class PrimaryClaimTechnicalBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    prepared_copy: PreparedBlockCopy
    block_id: str = Field(min_length=1, max_length=256)
    block_text_sha256: Sha256
    block_span: tuple[Position, Position]


class PrimaryClaimTechnicalReceipt(BaseModel):
    """Bind one literal citation to a saved request without assessing claim support."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    profile: Literal["primary-claim-technical-v1"] = "primary-claim-technical-v1"
    result_id: str = Field(pattern=r"^result-v1:[0-9a-f]{64}$")
    request_sha256: Sha256
    submitted_text_sha256: Sha256
    submitted_span: tuple[Position, Position]
    evidence: PrimaryClaimCitation
    input_checkpoint_sha256: Sha256 | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    block: PrimaryClaimTechnicalBlock | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def bounded_exact_spans(self) -> Self:
        left, right = self.submitted_span
        if (
            self.evidence.source_text_sha256 != self.submitted_text_sha256
            or self.evidence.citation.document_id != "submitted-context"
            or right - left != len(self.evidence.citation.quote)
        ):
            raise ValueError("claim receipt requires one exact submitted text range")
        block = self.block
        if block is not None and (
            not block.prepared_copy.start <= left < right <= block.prepared_copy.end
            or block.block_span[1] - block.block_span[0] != right - left
        ):
            raise ValueError("claim receipt range escapes its copied block")
        if len(self.model_dump_json().encode()) > MAX_PRIMARY_CLAIM_RECEIPT_BYTES:
            raise ValueError("claim receipt exceeds its UTF-8 byte limit")
        return self

    @classmethod
    def read(cls, raw: str) -> Self:
        if len(raw.encode()) > MAX_PRIMARY_CLAIM_RECEIPT_BYTES:
            raise ValueError("claim receipt exceeds its UTF-8 byte limit")
        return cls.model_validate(read_json(raw))

    @property
    def receipt_id(self) -> str:
        encoded = canonical_json(self.model_dump(mode="json")).encode()
        return "primary-claim-technical-v1:" + hashlib.sha256(encoded).hexdigest()


class PrimaryComparisonCitation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    result_id: str = Field(pattern=r"^result-v1:[0-9a-f]{64}$")
    request_sha256: Sha256
    submitted_text_sha256: Sha256
    submitted_span: tuple[Position, Position]
    citation: EvidenceCitation
    input_checkpoint_sha256: Sha256 | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def exact_span(self) -> Self:
        for value, limit in ((self.citation.locator, 256), (self.citation.quote, 1024)):
            if not value.strip() or len(value) > limit:
                raise ValueError("Comparison citation values must be nonblank and bounded")
        left, right = self.submitted_span
        if right - left != len(self.citation.quote):
            raise ValueError("Comparison citation requires an exact submitted text range")
        return self


PRIMARY_COMPARISON_RECEIPT_PROFILE: Final = "primary-comparison-technical-v1"


class PrimaryComparisonTechnicalReceipt(BaseModel):
    """Bind one saved comparison claim to literal ranges in two exact requests."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    profile: Literal["primary-comparison-technical-v1"] = PRIMARY_COMPARISON_RECEIPT_PROFILE
    field: Literal["changes_since_previous"]
    index: Position
    claim_text_sha256: Sha256
    before: PrimaryComparisonCitation
    after: PrimaryComparisonCitation

    @model_validator(mode="after")
    def distinct_scopes(self) -> Self:
        if (
            self.before.citation.document_id != "previous-submitted-context"
            or self.after.citation.document_id != "submitted-context"
            or self.before.result_id == self.after.result_id
            or self.before.request_sha256 == self.after.request_sha256
            or self.before.submitted_text_sha256 == self.after.submitted_text_sha256
        ):
            raise ValueError("Comparison requires distinct before and after request scopes")
        if len(self.model_dump_json().encode()) > MAX_PRIMARY_CLAIM_RECEIPT_BYTES:
            raise ValueError("Comparison receipt exceeds its UTF-8 byte limit")
        return self

    def require_claim(self, answer: Analysis) -> None:
        changes = answer.changes_since_previous
        if (
            self.index >= len(changes)
            or not changes[self.index].strip()
            or hashlib.sha256(changes[self.index].encode()).hexdigest() != self.claim_text_sha256
        ):
            raise ValueError("Comparison receipt requires the exact retained change claim")

    @classmethod
    def read(cls, raw: str) -> Self:
        if len(raw.encode()) > MAX_PRIMARY_CLAIM_RECEIPT_BYTES:
            raise ValueError("Comparison receipt exceeds its UTF-8 byte limit")
        return cls.model_validate(read_json(raw))

    @property
    def receipt_id(self) -> str:
        encoded = canonical_json(self.model_dump(mode="json")).encode()
        return PRIMARY_COMPARISON_RECEIPT_PROFILE + ":" + hashlib.sha256(encoded).hexdigest()
