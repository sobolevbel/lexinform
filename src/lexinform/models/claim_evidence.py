"""Claim addresses and citations retained alongside a primary result."""

import hashlib
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.models.analysis import Analysis
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
