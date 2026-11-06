"""Durable typed requests and results for homogeneous provider batches."""

import datetime as dt
import hashlib
import re
from typing import Annotated, Literal, Self

from pydantic import (
    AliasChoices,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from lexinform.json_storage import read_json
from lexinform.models.amendment_inputs import AmendmentInputRef
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
    AmendmentInputReceipt,
    LooseMultipartInputReceipt,
    LooseRclInputReceipt,
    PrimaryInputReceipt,
    SejmSelfAmendmentInputReceipt,
    SupplementInputReceipt,
    amendment_context_sha256,
    input_text_sha256,
    primary_context_sha256,
    supplement_context_sha256,
)
from lexinform.models.observations import DeliveryPlan
from lexinform.models.report import CallKind
from lexinform.models.reviews import DocumentReview, ReviewContext
from lexinform.models.scan_input import ScanInputManifest
from lexinform.models.senate import SenateResolutionIdentity
from lexinform.saved_clock import saved_clock_age

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


class LocalTimingWitness(BaseModel):
    """Local event clocks bind a frozen input without claiming source or remote completion."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    schema_version: Literal[1] = 1
    purpose: BatchKind
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_scope: ComputationInputBinding
    trigger_kind: (
        Literal["bill_discovered", "document_candidate_observed", "work_requested"] | None
    ) = Field(default=None, exclude_if=lambda value: value is None)
    triggered_at: AwareDatetime | None = Field(default=None, exclude_if=lambda value: value is None)
    read_kind: Literal["fresh", "cache_reuse", "mixed"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    read_completed_at: AwareDatetime | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @field_validator("triggered_at", "read_completed_at", mode="before")
    @classmethod
    def explicit_clock(cls, value: object) -> object:
        if isinstance(value, str):
            if len(value) > 64 or re.search(r"[.,]\d{7,}", value):
                raise ValueError("Local timing exceeds explicit datetime clock precision")
            value = dt.datetime.fromisoformat(value)
        if isinstance(value, dt.datetime):
            offset = value.utcoffset()
            return value if offset is None else value.replace(tzinfo=dt.timezone(offset), fold=0)
        if value is None:
            return None
        raise ValueError("Local timing requires an explicit aware timestamp")

    @field_serializer("triggered_at", "read_completed_at", when_used="json")
    def lossless_clock(self, value: dt.datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    @model_validator(mode="after")
    def known_event_pairs(self) -> Self:
        if (self.trigger_kind is None) != (self.triggered_at is None):
            raise ValueError("A local trigger requires both its event kind and clock")
        if (self.read_kind is None) != (self.read_completed_at is None):
            raise ValueError("A local read completion requires both its kind and clock")
        if self.trigger_kind == "bill_discovered" and self.purpose != "analysis":
            raise ValueError("Bill discovery is a trigger only for initial analysis")
        if len(self.model_dump_json().encode()) > 1024:
            raise ValueError("Local timing witness exceeds its 1 KiB metadata cap")
        return self

    def require_scope(self, binding: ComputationInputBinding | None) -> None:
        if binding is None or self.input_scope != binding:
            raise ValueError("Local timing must retain its exact original input scope")

    def require_request(self, purpose: str, request_sha256: str | None) -> None:
        if (
            self.purpose != purpose
            or request_sha256 is None
            or self.request_sha256 != request_sha256
        ):
            raise ValueError("Local timing must identify the actual purpose and frozen request")

    def require_not_after(self, endpoint: dt.datetime, *, json_endpoint: bool = False) -> None:
        offset = endpoint.utcoffset()
        if offset is None:
            raise ValueError("A local timing endpoint requires an aware clock")
        if json_endpoint and offset % dt.timedelta(minutes=1):
            raise ValueError("unsupported_timing_endpoint_offset")
        if any(
            stamp is not None and saved_clock_age(stamp, endpoint).unknown is not None
            for stamp in (self.triggered_at, self.read_completed_at)
        ):
            raise ValueError("Local timing cannot follow its enclosing recorded endpoint")


def _timing_meta(
    witness: LocalTimingWitness | None,
    binding: ComputationInputBinding | None,
    request_sha256: str | None,
    queued_at: dt.datetime | None,
    *,
    primary: bool,
) -> None:
    if witness is None:
        return
    witness.require_scope(binding)
    if (witness.purpose in ("analysis", "reanalysis")) != primary:
        raise ValueError("Local timing must use its actual primary or secondary metadata")
    witness.require_request(witness.purpose, request_sha256)
    if queued_at is not None:
        witness.require_not_after(queued_at, json_endpoint=True)


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
    queued_at: dt.datetime | None = Field(default=None, exclude_if=lambda value: value is None)
    prepared_input_receipt: (
        PrimaryInputReceipt | LooseRclInputReceipt | SejmSelfAmendmentInputReceipt | None
    ) = Field(default=None, exclude_if=lambda value: value is None)

    local_timing_witness: LocalTimingWitness | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def frozen_binding(self) -> Self:
        _timing_meta(
            self.local_timing_witness,
            self.input_binding,
            self.request_sha256,
            self.queued_at,
            primary=True,
        )
        if self.input_binding is not None and self.input_binding.reuse_key != self.memo_key:
            raise ValueError("input binding must keep its frozen memo key")
        receipt = self.prepared_input_receipt
        if (
            receipt is not None
            and self.request_sha256 is not None
            and receipt.request_sha256 != self.request_sha256
        ):
            raise ValueError("Input receipt must identify the frozen batch request")
        if isinstance(receipt, LooseMultipartInputReceipt):
            if (
                self.source_kind != receipt.source_kind
                or self.input_binding is None
                or self.text_source == "scan"
                or self.selection.scan_input is not None
                or self.input_chars != receipt.text_chars
                or len(self.selection.parts) != len(receipt.source_parts)
                or self.source_url != self.selection.parts[0].url
            ):
                raise ValueError("Multipart text receipt differs from its selected text input")
            for part, recorded in zip(self.selection.parts, receipt.source_parts, strict=True):
                if part.members or (
                    part.role,
                    part.text_sha256,
                    part.text_start,
                    part.text_end,
                ) != (recorded.role, recorded.text_sha256, 0, recorded.chars):
                    raise ValueError("Multipart text receipt differs from its selected part")
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

    prepared_input_receipt: SupplementInputReceipt | AmendmentInputReceipt | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    local_timing_witness: LocalTimingWitness | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    amendment_input_ref: AmendmentInputRef | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def frozen_binding(self) -> Self:
        _timing_meta(
            self.local_timing_witness,
            self.input_binding,
            self.request_sha256,
            self.queued_at,
            primary=False,
        )
        if self.input_binding is not None and self.input_binding.reuse_key != self.memo_key:
            raise ValueError("input binding must keep its frozen memo key")
        if self.amendment_input_ref is not None:
            self.amendment_input_ref.require_carrier(
                "amendments", self.request_sha256, self.input_binding
            )
        receipt = self.prepared_input_receipt
        if receipt is not None:
            if self.scan_input is not None:
                raise ValueError("Secondary text receipts cannot describe scan inputs")
            if self.request_sha256 is not None and receipt.request_sha256 != self.request_sha256:
                raise ValueError("Input receipt must identify the frozen batch request")
        return self


def batch_item_meta(
    kind: str, raw: str, *, submitted: bool = True
) -> BatchItemMeta | DigestItemMeta:
    model = BatchItemMeta if kind in ("analysis", "reanalysis") else DigestItemMeta
    try:
        decoded = read_json(raw)
    except ValueError, TypeError, RecursionError:
        raise ValueError("invalid_batch_metadata_json") from None
    parsed = model.model_validate(decoded)
    if isinstance(parsed, DigestItemMeta) and parsed.amendment_input_ref is not None:
        parsed.amendment_input_ref.require_carrier(
            kind, parsed.request_sha256, parsed.input_binding
        )
    if isinstance(parsed, DigestItemMeta) and parsed.prepared_input_receipt is not None:
        expected = (
            "amendments"
            if isinstance(parsed.prepared_input_receipt, AmendmentInputReceipt)
            else "supplement"
        )
        if kind != expected:
            raise ValueError("Text receipts require their actual secondary purpose")
        if submitted and parsed.request_sha256 is None:
            raise ValueError("Submitted text receipts require their frozen request checksum")
    if (
        submitted
        and isinstance(parsed.prepared_input_receipt, LooseMultipartInputReceipt)
        and parsed.request_sha256 is None
    ):
        raise ValueError("Submitted multipart text receipts require their frozen request checksum")
    timing = parsed.local_timing_witness
    if timing is not None:
        if timing.purpose != kind:
            raise ValueError("Local timing must identify the submitted purpose")
        if submitted:
            timing.require_request(kind, parsed.request_sha256)
    return parsed


def _amendment_input_metadata(value: object) -> object:
    if isinstance(value, dict) and isinstance(value.get("meta"), dict):
        meta = value["meta"]
        if meta.get("amendment_input_ref") is not None:
            return {**value, "meta": DigestItemMeta.model_validate(meta)}
    return value


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

    @model_validator(mode="before")
    @classmethod
    def typed_amendment_metadata(cls, value: object) -> object:
        return _amendment_input_metadata(value)

    @model_validator(mode="after")
    def actual_local_timing(self) -> Self:
        if isinstance(self.meta, DigestItemMeta) and self.meta.amendment_input_ref is not None:
            self.meta.amendment_input_ref.require_carrier(
                self.call_kind, self.meta.request_sha256, self.meta.input_binding
            )
        timing = self.meta.local_timing_witness
        if timing is not None:
            timing.require_scope(self.meta.input_binding)
            timing.require_request(self.call_kind, self.meta.request_sha256)
        return self

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
        value = _amendment_input_metadata(value)
        if isinstance(value, dict):
            meta = value.get("meta")
            timing = (
                meta.get("local_timing_witness")
                if isinstance(meta, dict)
                else (
                    meta.local_timing_witness
                    if isinstance(meta, (BatchItemMeta, DigestItemMeta))
                    else None
                )
            )
            if timing is not None:
                purpose = (
                    timing.get("purpose")
                    if isinstance(timing, dict)
                    else (timing.purpose if isinstance(timing, LocalTimingWitness) else None)
                )
                if isinstance(meta, DigestItemMeta) or purpose not in ("analysis", "reanalysis"):
                    DigestItemMeta.model_validate(meta)
                else:
                    BatchItemMeta.model_validate(meta)
            receipt = (
                meta.get("prepared_input_receipt")
                if isinstance(meta, dict)
                else (
                    meta.prepared_input_receipt
                    if isinstance(meta, (BatchItemMeta, DigestItemMeta))
                    else None
                )
            )
            if receipt is not None:
                if (
                    isinstance(meta, DigestItemMeta)
                    or isinstance(receipt, (SupplementInputReceipt, AmendmentInputReceipt))
                    or (
                        isinstance(receipt, dict)
                        and receipt.get("profile")
                        in ("supplement-single-part-text-v1", "amendment-single-part-text-v1")
                    )
                ):
                    DigestItemMeta.model_validate(meta)
                else:
                    BatchItemMeta.model_validate(meta)
        return value

    @model_validator(mode="after")
    def actual_input_receipt(self) -> Self:
        if isinstance(self.meta, DigestItemMeta) and self.meta.amendment_input_ref is not None:
            self.meta.amendment_input_ref.require_carrier(
                self.request.call_kind, self.request.payload_sha256, self.meta.input_binding
            )
        receipt = self.meta.prepared_input_receipt
        if receipt is None:
            return self
        request = self.request
        if isinstance(self.meta, BatchItemMeta):
            if not isinstance(request.question, AnalysisQuestion) or request.call_kind not in (
                "analysis",
                "reanalysis",
            ):
                raise ValueError("Primary receipts require their actual primary question")
            ctx: BillContext | SupplementContext | AmendmentsContext = request.question.ctx
            if isinstance(receipt, LooseMultipartInputReceipt):
                binding = self.meta.input_binding
                if (
                    ctx.source_kind != receipt.source_kind
                    or request.question.ctx.text_source == "scan"
                    or ctx.number != request.number
                    or binding is None
                    or (binding.term, binding.number) != (request.term, request.number)
                ):
                    raise ValueError("Multipart text receipt requires its actual source question")
            context_sha256 = primary_context_sha256(request.question.ctx)
        elif isinstance(receipt, AmendmentInputReceipt):
            if not isinstance(request.question, AmendmentsQuestion):
                raise ValueError("Amendment receipts require their actual amendments question")
            ctx = request.question.ctx
            context_sha256 = amendment_context_sha256(ctx)
        else:
            if not isinstance(request.question, SupplementQuestion):
                raise ValueError("Supplement receipts require their actual supplement question")
            ctx = request.question.ctx
            context_sha256 = supplement_context_sha256(ctx)
        if (
            request.payload_json is None
            or receipt.request_sha256 != request.payload_sha256
            or input_text_sha256(request.payload_json) != receipt.request_sha256
            or receipt.context_sha256 != context_sha256
            or receipt.text_sha256 != input_text_sha256(ctx.text)
            or receipt.text_chars != len(ctx.text)
        ):
            raise ValueError("Input receipt differs from the frozen request and context")
        return self

    @model_validator(mode="after")
    def actual_local_timing(self) -> Self:
        timing = self.meta.local_timing_witness
        if timing is not None:
            if (self.request.call_kind in ("analysis", "reanalysis")) != isinstance(
                self.meta, BatchItemMeta
            ):
                raise ValueError("Local timing requires the actual request metadata shape")
            timing.require_scope(self.meta.input_binding)
            timing.require_request(self.request.call_kind, self.meta.request_sha256)
            timing.require_request(self.request.call_kind, self.request.payload_sha256)
            if (
                self.request.payload_json is None
                or input_text_sha256(self.request.payload_json) != timing.request_sha256
            ):
                raise ValueError("Local timing requires the exact frozen serialized request")
            timing.require_not_after(self.created_at, json_endpoint=True)
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
        frozen_meta = meta.model_copy(update={"input_binding": binding})
        if isinstance(meta, BatchItemMeta) and request.call_kind in ("analysis", "reanalysis"):
            if created_at.utcoffset() is None:
                raise ValueError("a new primary intent requires an aware queue time")
            if meta.queued_at is not None and meta.queued_at != created_at:
                raise ValueError("a primary queue stamp must match its original intent time")
            frozen_meta = meta.model_copy(
                update={"input_binding": binding, "queued_at": created_at}
            )
        return cls(
            request=request,
            meta=frozen_meta,
            provider=provider,
            created_at=created_at,
        )
