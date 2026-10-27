"""Immutable batch results and their collection/projection decisions."""

import datetime as dt
import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.models.analysis import (
    AmendmentsRecord,
    AnalysisRecord,
    JointRecord,
    NameRecord,
    SupplementRecord,
    TokenUsage,
)
from lexinform.models.batch import BatchKind, BatchProvider
from lexinform.models.enums import BillStatus
from lexinform.models.reviews import (
    DocumentReview,
    DocumentReviewRecord,
    DocumentRole,
    ReviewContext,
    ReviewForum,
    ReviewPurpose,
)


class ReviewSourceReference(BaseModel):
    """Source identities and reading coverage, without another copy of the source text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    identity: str
    url: str
    role: DocumentRole
    sha256: str
    normalized_sha256: str
    complete: bool
    file_sha256: str | None = None
    author: str = ""
    draft_date: dt.date | None = None
    body: str = ""
    forum: ReviewForum = "unknown"


class ReviewInputReference(BaseModel):
    """Recorded input metadata cannot replace the full context used to validate citations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(pattern=r"^document-review:[0-9a-f]{64}$")
    term: int
    number: str
    title: str
    purpose: ReviewPurpose
    documents: tuple[ReviewSourceReference, ...]
    act_eli: str | None = None
    promulgation_date: dt.date | None = None
    canonical_date: dt.date | None = None
    comparison_sha256: str | None = None

    @classmethod
    def from_context(cls, context: ReviewContext, *, term: int) -> Self:
        return cls(
            key=context.memo_key(term),
            term=term,
            number=context.number,
            title=context.title,
            purpose=context.purpose,
            documents=tuple(
                ReviewSourceReference.model_validate(document, from_attributes=True)
                for document in context.documents
            ),
            act_eli=context.act_eli,
            promulgation_date=context.promulgation_date,
            canonical_date=context.canonical_date,
            comparison_sha256=context.comparison_sha256,
        )


class ReviewResultRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    review: DocumentReview
    model: str
    prompt_version: str
    created_at: dt.datetime
    computation_id: str = Field(pattern=r"^result-v1:[0-9a-f]{64}$")
    request_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", exclude_if=lambda value: value is None
    )
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None

    @classmethod
    def from_record(cls, record: DocumentReviewRecord) -> Self:
        if record.computation_id is None:
            raise ValueError("a compact paid review requires a known computation ID")
        return cls(
            review=record.review,
            model=record.model,
            prompt_version=record.prompt_version,
            created_at=record.created_at,
            computation_id=record.computation_id,
            request_sha256=record.request_sha256,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cache_read_input_tokens=record.cache_read_input_tokens,
            cache_creation_input_tokens=record.cache_creation_input_tokens,
        )


ComputationRecord = (
    AnalysisRecord
    | AmendmentsRecord
    | SupplementRecord
    | JointRecord
    | NameRecord
    | DocumentReviewRecord
    | ReviewResultRecord
)
_RECORD_TYPES: dict[BatchKind, type[BaseModel] | tuple[type[BaseModel], ...]] = {
    "analysis": AnalysisRecord,
    "reanalysis": AnalysisRecord,
    "explanation": AnalysisRecord,
    "amendments": AmendmentsRecord,
    "supplement": SupplementRecord,
    "joint": JointRecord,
    "name": NameRecord,
    "consultation_review": (DocumentReviewRecord, ReviewResultRecord),
    "commission_review": (DocumentReviewRecord, ReviewResultRecord),
    "act_application": (DocumentReviewRecord, ReviewResultRecord),
}

CollectionReason = Literal[
    "current_generation",
    "generation_changed",
    "inactive_status",
    "bill_missing",
    "no_validated_result",
]
ProjectionReason = Literal["batch_result", "memo_reuse", "retained_result"]
ApplicationReason = CollectionReason | ProjectionReason


def computation_id(provider: BatchProvider, batch_id: str, custom_id: str) -> str:
    identity = json.dumps([provider, batch_id, custom_id], separators=(",", ":"))
    return "result-v1:" + hashlib.sha256(identity.encode()).hexdigest()


class ComputationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1, 2, 3] = 1
    provider: BatchProvider
    batch_id: str
    custom_id: str
    purpose: BatchKind
    term: int
    number: str
    generation: int | None
    collected_at: dt.datetime
    review_input: ReviewInputReference | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    request_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", exclude_if=lambda value: value is None
    )
    validation: Literal["validated", "error"]
    record: ComputationRecord | None
    model: str | None
    usage: TokenUsage
    error: str | None = None
    context_exceeded: bool = False

    @property
    def result_id(self) -> str:
        return computation_id(self.provider, self.batch_id, self.custom_id)

    @model_validator(mode="after")
    def validated_record(self) -> Self:
        primary = self.purpose in ("analysis", "reanalysis")
        if not primary and self.schema_version == 1:
            raise ValueError("secondary results require schema version 2")
        if isinstance(self.record, ReviewResultRecord) and self.schema_version != 3:
            raise ValueError("compact reviews require schema version 3")
        if self.schema_version == 3 and (
            self.purpose not in ("consultation_review", "commission_review", "act_application")
            or self.record is not None
            and not isinstance(self.record, ReviewResultRecord)
        ):
            raise ValueError("schema version 3 is reserved for compact review results")
        if self.review_input is not None:
            if self.schema_version != 3:
                raise ValueError("compact review inputs require schema version 3")
            if self.review_input.purpose != self.purpose:
                raise ValueError("the retained review input must match its purpose")
            if (self.review_input.term, self.review_input.number) != (self.term, self.number):
                raise ValueError("the retained review input must match its original owner")
        if primary == (self.generation is None):
            raise ValueError("generation is required only for primary results")
        if (self.record is not None) != (self.validation == "validated"):
            raise ValueError("validation must describe the retained record")
        if self.record is not None and not isinstance(self.record, _RECORD_TYPES[self.purpose]):
            raise ValueError("the retained record must match its purpose")
        if isinstance(self.record, DocumentReviewRecord) and (
            self.record.review.purpose != self.purpose
            or self.record.context.purpose != self.purpose
        ):
            raise ValueError("the retained review and context must match its purpose")
        if isinstance(self.record, ReviewResultRecord):
            if self.record.review.purpose != self.purpose:
                raise ValueError("the retained compact review must match its purpose")
            if self.review_input is None:
                raise ValueError("a validated compact review requires its original input reference")
        if isinstance(self.record, SupplementRecord) and self.record.digest is None:
            raise ValueError("a validated supplement result must contain its digest")
        if self.record is not None and self.record.computation_id != self.result_id:
            raise ValueError("the retained record must refer to its computation")
        if self.record is not None and self.record.request_sha256 != self.request_sha256:
            raise ValueError("the retained record must refer to its original request")
        return self


class ResultApplicationTarget(BaseModel):
    """Opaque target identities are recorded, never interpreted to execute work."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    ref: str = Field(min_length=1)


class ResultApplication(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    result_id: str
    term: int
    number: str
    generation: int | None
    target: ResultApplicationTarget | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    phase: Literal["collection", "projection"] = "collection"
    outcome: Literal["ready", "superseded", "failed", "applied"]
    reason: ApplicationReason
    observed_generation: int | None
    observed_status: BillStatus | None
    created_at: dt.datetime

    @model_validator(mode="after")
    def application_phase(self) -> Self:
        if self.target is None:
            if self.generation is None:
                raise ValueError("a primary receipt requires its original generation")
        elif self.generation is not None or self.phase != "projection":
            raise ValueError("an explicit target requires projection without a primary generation")
        projection = self.phase == "projection"
        if projection != (self.outcome == "applied"):
            raise ValueError("only a projection receipt confirms application")
        if projection != (self.reason in ("batch_result", "memo_reuse", "retained_result")):
            raise ValueError("the application reason must describe its phase")
        return self
