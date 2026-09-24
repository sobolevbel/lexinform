"""Durable typed requests and results for homogeneous provider batches."""

import datetime as dt
from typing import Annotated, Literal

from pydantic import AliasChoices, BaseModel, Field, model_validator

from lexinform.models.analysis import (
    Amendments,
    AmendmentsContext,
    Analysis,
    BillContext,
    DocumentDigest,
    JointComparison,
    JointContext,
    SupplementContext,
)
from lexinform.models.bill import LocatedText
from lexinform.models.enums import SourceKind, TextSource
from lexinform.models.report import CallKind

BatchProvider = Literal["anthropic", "openai"]
BatchStatus = Literal["submitted", "ended", "failed"]


class AnalysisQuestion(BaseModel):
    kind: Literal["analysis", "reanalysis"]
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


BatchQuestion = Annotated[
    AnalysisQuestion | AmendmentsQuestion | SupplementQuestion | JointQuestion,
    Field(discriminator="kind"),
]
BatchAnswer = Analysis | Amendments | DocumentDigest | JointComparison
BatchKind = Literal["analysis", "reanalysis", "amendments", "supplement", "joint"]


def batch_output_model(kind: CallKind) -> type[BatchAnswer]:
    if kind in ("analysis", "reanalysis"):
        return Analysis
    if kind == "amendments":
        return Amendments
    if kind == "supplement":
        return DocumentDigest
    if kind == "joint":
        return JointComparison
    raise ValueError(f"Unsupported batch kind: {kind}")


class BatchRequest(BaseModel):
    custom_id: str
    term: int
    number: str
    question: BatchQuestion
    payload_json: str | None = None
    model: str = ""
    prompt_version: str = ""
    estimated_cost_usd: float = 0.0

    @model_validator(mode="before")
    @classmethod
    def legacy_question(cls, value: object) -> object:
        if isinstance(value, dict) and "question" not in value:
            return {**value, "question": {"kind": value["call_kind"], "ctx": value["ctx"]}}
        return value

    @property
    def call_kind(self) -> BatchKind:
        return self.question.kind

    @property
    def ctx(self) -> BillContext | AmendmentsContext | SupplementContext | JointContext:
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
    truncated: bool
    text_source: TextSource
    source_kind: SourceKind
    source_url: str | None
    revision: int
    text_sha256: str | None
    prompt_version: str = ""
    generation: int = 0
    memo_key: str | None = None
    located: LocatedText | None = None
    text: str = ""


class BatchJob(BaseModel):
    state: Literal["queued", "submitting", "open", "failed"]
    since: dt.datetime


class DigestItemMeta(BaseModel):
    queued_at: dt.datetime | None = None
    memo_key: str
    prompt_version: str = ""
    source_kind: SourceKind = "print"
    title: str = ""
    compared_with: list[str] = Field(default_factory=list)


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


class BatchIntent(BaseModel):
    request: BatchRequest
    meta: BatchItemMeta | DigestItemMeta
    provider: BatchProvider
    state: Literal["queued", "submitting"] = "queued"
    created_at: dt.datetime
