"""Durable typed requests and results for homogeneous provider batches."""

import datetime as dt
import hashlib
from typing import Annotated, Literal, Self

from pydantic import AliasChoices, BaseModel, Field, model_validator

from lexinform.models.analysis import (
    Amendments,
    AmendmentsContext,
    Analysis,
    BillContext,
    BillName,
    DocumentDigest,
    JointComparison,
    JointContext,
    NameContext,
    SupplementContext,
)
from lexinform.models.bill import LocatedText
from lexinform.models.computation_inputs import ComputationInputBinding
from lexinform.models.documents import DocumentSelection
from lexinform.models.enums import SourceKind, TextSource
from lexinform.models.input_receipts import (
    PrimaryInputReceipt,
    input_text_sha256,
    primary_context_sha256,
)
from lexinform.models.observations import DeliveryPlan
from lexinform.models.report import CallKind
from lexinform.models.reviews import DocumentReview, ReviewContext
from lexinform.models.scan_input import ScanInputManifest
from lexinform.models.senate import SenateResolutionIdentity

BatchProvider = Literal["anthropic", "openai"]
BatchStatus = Literal["submitted", "ended", "failed"]


def provider_of(model: str) -> BatchProvider:
    """Who serves `model`: a `claude-` name is Anthropic's, every other name OpenAI's."""
    return "anthropic" if model.startswith("claude-") else "openai"


class AnalysisQuestion(BaseModel):
    kind: Literal["analysis", "reanalysis", "explanation"]
    ctx: BillContext


class AmendmentsQuestion(BaseModel):
    kind: Literal["amendments"] = "amendments"
    ctx: AmendmentsContext


class SupplementQuestion(BaseModel):
    kind: Literal["supplement"] = "supplement"
    ctx: SupplementContext


class JointQuestion(BaseModel):
    kind: Literal["joint"] = "joint"
    ctx: JointContext


class DocumentReviewQuestion(BaseModel):
    kind: Literal["consultation_review", "commission_review", "act_application"]
    ctx: ReviewContext


class NameQuestion(BaseModel):
    kind: Literal["name"] = "name"
    ctx: NameContext


BatchQuestion = Annotated[
    AnalysisQuestion
    | AmendmentsQuestion
    | SupplementQuestion
    | JointQuestion
    | DocumentReviewQuestion
    | NameQuestion,
    Field(discriminator="kind"),
]
BatchAnswer = Analysis | Amendments | DocumentDigest | JointComparison | DocumentReview | BillName
BatchKind = Literal[
    "analysis",
    "reanalysis",
    "explanation",
    "amendments",
    "supplement",
    "joint",
    "consultation_review",
    "commission_review",
    "act_application",
    "name",
]


def batch_output_model(kind: CallKind) -> type[BatchAnswer]:
    if kind in ("analysis", "reanalysis", "explanation"):
        return Analysis
    if kind == "amendments":
        return Amendments
    if kind == "supplement":
        return DocumentDigest
    if kind == "joint":
        return JointComparison
    if kind == "name":
        return BillName
    if kind in ("consultation_review", "commission_review", "act_application"):
        return DocumentReview
    raise ValueError(f"Unsupported batch kind: {kind}")


class BatchRequest(BaseModel):
    custom_id: str
    term: int
    number: str
    question: BatchQuestion
    payload_json: str | None = None
    payload_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", exclude_if=lambda value: value is None
    )
    model: str = ""
    prompt_version: str = ""
    estimated_cost_usd: float = 0.0

    @model_validator(mode="before")
    @classmethod
    def legacy_question(cls, value: object) -> object:
        if isinstance(value, dict) and "question" not in value:
            return {**value, "question": {"kind": value["call_kind"], "ctx": value["ctx"]}}
        return value

    @model_validator(mode="after")
    def frozen_payload(self) -> Self:
        return self.verify_payload()

    def verify_payload(self) -> Self:
        if self.payload_sha256 is not None and (
            self.payload_json is None
            or hashlib.sha256(self.payload_json.encode()).hexdigest() != self.payload_sha256
        ):
            raise ValueError("Frozen batch payload SHA-256 does not match its serialized request")
        return self

    def freeze(
        self, *, payload_json: str, model: str, prompt_version: str, estimated_cost_usd: float
    ) -> Self:
        if self.payload_json is not None:
            raise ValueError("Batch request is already frozen")
        return self.model_copy(
            update={
                "payload_json": payload_json,
                "payload_sha256": hashlib.sha256(payload_json.encode()).hexdigest(),
                "model": model,
                "prompt_version": prompt_version,
                "estimated_cost_usd": estimated_cost_usd,
            }
        )

    @property
    def call_kind(self) -> BatchKind:
        return self.question.kind

    @property
    def ctx(
        self,
    ) -> (
        BillContext
        | AmendmentsContext
        | SupplementContext
        | JointContext
        | ReviewContext
        | NameContext
    ):
        return self.question.ctx


class BatchResult(BaseModel):
    """One line of a collected batch: what the model said and what it cost, or why there is
    nothing — never more than that. `input_chars`/`source_url`/`revision`/… are not the
    provider's to know; `AnalysisService.collect_batches` attaches them from `BatchItemMeta`."""

    custom_id: str
    answer: BatchAnswer | None = Field(
        default=None, validation_alias=AliasChoices("answer", "analysis")
    )
    model: str | None = None
    prompt_version: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    error: str | None = None
    context_exceeded: bool = False

    @property
    def analysis(self) -> Analysis | None:
        return self.answer if isinstance(self.answer, Analysis) else None


class LlmBatch(BaseModel):
    """A submission's own bookkeeping row (`llm_batches`)."""

    batch_id: str
    provider: BatchProvider
    call_kind: CallKind
    submitted_at: dt.datetime
    status: BatchStatus
    polled_at: dt.datetime | None = None
    completed_at: dt.datetime | None = None
    request_count: int
    estimated_cost_usd: float
    forgotten_at: dt.datetime | None = None


class PendingBatch(BaseModel):
    batch_id: str = Field(min_length=1)
    provider: BatchProvider


class PendingBatches(BaseModel):
    version: Literal[1]
    batches: list[PendingBatch]


class BatchItemMeta(BaseModel):
    """What was already known about a request when it was queued — the provenance `_prepare`
    would otherwise attach to the `AnalysisRecord` right after calling the model, and that the
    model's answer alone can never supply. All of it is derivable from the `BillContext` that was
    sent except `source_url` and `revision`, which is why only those two are not."""

    input_chars: int
    overflow: bool = False
    truncated: bool
    text_source: TextSource
    source_kind: SourceKind
    source_url: str | None
    revision: int
    text_sha256: str | None
    selection: DocumentSelection = DocumentSelection()
    law_sha256: str | None = None
    prompt_version: str = ""
    request_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", exclude_if=lambda value: value is None
    )
    generation: int = 0
    memo_key: str | None = None
    located: LocatedText | None = None
    text: str = ""
    input_binding: ComputationInputBinding | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    prepared_input_receipt: PrimaryInputReceipt | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def frozen_binding(self) -> Self:
        if self.input_binding is not None and self.input_binding.reuse_key != self.memo_key:
            raise ValueError("input binding must keep its frozen memo key")
        receipt = self.prepared_input_receipt
        if (
            receipt is not None
            and self.request_sha256 is not None
            and receipt.request_sha256 != self.request_sha256
        ):
            raise ValueError("Input receipt must identify the frozen batch request")
        return self


class BatchJob(BaseModel):
    state: Literal["queued", "submitting", "open", "failed"]
    since: dt.datetime
    submissions: int = 0


class AmendmentRevisionTarget(BaseModel):
    publication_id: int
    channel_id: str
    source_url: str
    delivery: DeliveryPlan


class DigestItemMeta(BaseModel):
    queued_at: dt.datetime | None = None
    memo_key: str
    delivery_key: str = ""
    prompt_version: str = ""
    request_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", exclude_if=lambda value: value is None
    )
    source_kind: SourceKind = "print"
    source_url: str | None = Field(default=None, exclude_if=lambda value: value is None)
    document_number: str | None = Field(default=None, exclude_if=lambda value: value is None)
    title: str = ""
    compared_with: list[str] = Field(default_factory=list)
    review_context: ReviewContext | None = None
    amendment_revision: AmendmentRevisionTarget | None = None
    revision_application_work_key: str | None = Field(
        default=None,
        pattern=r"^amendment-revision-application-v1:[0-9a-f]{64}$",
        exclude_if=lambda value: value is None,
    )
    revision_application_problem: str | None = Field(
        default=None, min_length=1, max_length=256, exclude_if=lambda value: value is None
    )
    amendment_source: SenateResolutionIdentity | None = None
    scan_input: ScanInputManifest | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    explanation: BatchItemMeta | None = None
    input_binding: ComputationInputBinding | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def frozen_binding(self) -> Self:
        if self.input_binding is not None and self.input_binding.reuse_key != self.memo_key:
            raise ValueError("input binding must keep its frozen memo key")
        return self


def batch_item_meta(kind: str, raw: str) -> BatchItemMeta | DigestItemMeta:
    model = BatchItemMeta if kind in ("analysis", "reanalysis") else DigestItemMeta
    return model.model_validate_json(raw)


class LlmBatchItem(BaseModel):
    """Which bill one `custom_id` of a batch answers for, and whether it has been written back."""

    batch_id: str
    custom_id: str
    call_kind: CallKind
    term: int
    number: str
    meta: BatchItemMeta | DigestItemMeta
    consumed_at: dt.datetime | None = None
    result: BatchResult | None = None

    @classmethod
    def from_intent(cls, batch_id: str, intent: BatchIntent) -> Self:
        return cls(
            batch_id=batch_id,
            custom_id=intent.request.custom_id,
            call_kind=intent.request.call_kind,
            term=intent.request.term,
            number=intent.request.number,
            meta=intent.meta.model_copy(update={"request_sha256": intent.request.payload_sha256}),
        )


class BatchIntent(BaseModel):
    request: BatchRequest
    meta: BatchItemMeta | DigestItemMeta
    provider: BatchProvider
    state: Literal["queued", "submitting"] = "queued"
    created_at: dt.datetime

    @model_validator(mode="before")
    @classmethod
    def receipt_meta_cannot_fall_back(cls, value: object) -> object:
        if isinstance(value, dict):
            meta = value.get("meta")
            if (
                isinstance(meta, dict)
                and meta.get("prepared_input_receipt") is not None
                or isinstance(meta, BatchItemMeta)
                and meta.prepared_input_receipt is not None
            ):
                BatchItemMeta.model_validate(meta)
        return value

    @model_validator(mode="after")
    def actual_input_receipt(self) -> Self:
        if not isinstance(self.meta, BatchItemMeta) or self.meta.prepared_input_receipt is None:
            return self
        receipt = self.meta.prepared_input_receipt
        request = self.request
        if not isinstance(request.question, AnalysisQuestion) or request.call_kind not in (
            "analysis",
            "reanalysis",
        ):
            raise ValueError("Primary receipts require their actual primary question")
        ctx = request.question.ctx
        if (
            request.payload_json is None
            or receipt.request_sha256 != request.payload_sha256
            or input_text_sha256(request.payload_json) != receipt.request_sha256
            or receipt.context_sha256 != primary_context_sha256(ctx)
            or receipt.text_sha256 != input_text_sha256(ctx.text)
            or receipt.text_chars != len(ctx.text)
        ):
            raise ValueError("Input receipt differs from the frozen request and context")
        return self

    @classmethod
    def from_request(
        cls,
        *,
        request: BatchRequest,
        meta: BatchItemMeta | DigestItemMeta,
        provider: BatchProvider,
        created_at: dt.datetime,
    ) -> Self:
        if meta.input_binding is not None:
            raise ValueError("a new intent cannot rebind an existing input")
        key = meta.memo_key
        if not key:
            raise ValueError("a new intent requires its recorded question key")
        question = request.question
        number = (
            question.ctx.subject.number
            if isinstance(question, JointQuestion)
            else question.ctx.number
        )
        if request.number != number:
            raise ValueError("a new request must belong to its question subject")
        binding = ComputationInputBinding(term=request.term, number=number, reuse_key=key)
        return cls(
            request=request,
            meta=meta.model_copy(update={"input_binding": binding}),
            provider=provider,
            created_at=created_at,
        )
