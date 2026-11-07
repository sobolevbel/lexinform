"""Compact decoded review-field evidence with no source text."""

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lexinform.json_storage import canonical_json
from lexinform.models.physical_inventory import Sha256
from lexinform.models.reviews import DocumentRole, ReviewContext, ReviewDocument

MAX_REVIEW_PREPARED_DOCUMENTS = 16


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def review_context_sha256(context: ReviewContext) -> str:
    return _digest(canonical_json(context.model_dump(mode="json")))


class ReviewPreparedDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    identity_sha256: Sha256
    role: DocumentRole
    source_text_sha256: Sha256
    chars: int = Field(ge=0, le=2**31 - 1, strict=True)

    @classmethod
    def from_document(cls, document: ReviewDocument) -> ReviewPreparedDocument:
        checksum = _digest(document.text)
        if document.sha256 != checksum and (document.text or document.sha256 or document.complete):
            raise ValueError("Frozen review document text differs from its recorded hash")
        return cls(
            identity_sha256=_digest(document.identity),
            role=document.role,
            source_text_sha256=checksum,
            chars=len(document.text),
        )


class ReviewPreparedInputWitness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    profile: Literal["consultation-review-provider-text-v1"] = (
        "consultation-review-provider-text-v1"
    )
    term: int = Field(gt=0, strict=True)
    number: str = Field(min_length=1, max_length=160)
    request_sha256: Sha256
    manifest_sha256: Sha256
    context_sha256: Sha256
    documents: tuple[ReviewPreparedDocument, ...] = Field(
        min_length=1, max_length=MAX_REVIEW_PREPARED_DOCUMENTS
    )

    def require_context(self, context: ReviewContext) -> None:
        if (
            context.purpose != "consultation_review"
            or context.number != self.number
            or self.context_sha256 != review_context_sha256(context)
            or len(self.documents) != len(context.documents)
            or self.documents
            != tuple(ReviewPreparedDocument.from_document(doc) for doc in context.documents)
        ):
            raise ValueError("Review prepared input differs from its saved context")
