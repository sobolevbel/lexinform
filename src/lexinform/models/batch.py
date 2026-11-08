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

from lexinform.json_storage import canonical_json, read_json
from lexinform.models.amendment_inputs import AmendmentInputRef, AmendmentScanInputWitness
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
    ArchiveInputPart,
    LooseRclInputReceipt,
    MultipartTextInputReceipt,
    PrimaryInputReceipt,
    RclArchiveInputReceipt,
    RclArchiveSelfAmendmentInputReceipt,
    SejmSelfAmendmentInputReceipt,
    SupplementInputReceipt,
    amendment_context_sha256,
    input_text_sha256,
    primary_context_sha256,
    supplement_context_sha256,
)
from lexinform.models.observations import DeliveryPlan
from lexinform.models.physical_inventory import Sha256
from lexinform.models.report import CallKind
from lexinform.models.review_prepared_input import ReviewPreparedInputWitness
from lexinform.models.reviews import DocumentReview, ReviewContext
from lexinform.models.scan_input import ScanInputManifest
from lexinform.models.senate import SenateResolutionIdentity
from lexinform.models.supplement_inputs import SupplementScanInputWitness
from lexinform.saved_clock import saved_clock_age

BatchProvider = Literal["anthropic", "openai"]
BatchStatus = Literal["submitted", "ended", "failed"]


class FrozenRequestManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    profile: Literal["frozen-provider-request-v1"] = "frozen-provider-request-v1"
    provider: BatchProvider
    purpose: CallKind
    request_sha256: Sha256
    model: str = Field(min_length=1, max_length=160)
    prompt_version: str = Field(min_length=1, max_length=160)
    payload_bytes: int = Field(gt=0, le=2**63 - 1, strict=True)
    messages_sha256: Sha256
    output_format_sha256: Sha256
    options_sha256: Sha256

    @property
    def manifest_sha256(self) -> str:
        encoded = canonical_json(self.model_dump(mode="json", exclude_defaults=True))
        return hashlib.sha256(encoded.encode()).hexdigest()

    def require_request(self, purpose: str, request_sha256: str | None) -> None:
        if (self.purpose, self.request_sha256) != (purpose, request_sha256):
            raise ValueError("Frozen request manifest differs from its submitted request")


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


class ReanalysisBaselineReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    result_id: str = Field(pattern=r"^result-v1:[0-9a-f]{64}$")
    context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def from_context(cls, result_id: str, summary: str | None, changes: list[str]) -> Self:
        checksum = hashlib.sha256(canonical_json([summary, [*changes]]).encode()).hexdigest()
        return cls(result_id=result_id, context_sha256=checksum)

    def require_context(self, summary: str | None, changes: list[str]) -> None:
        if self != self.from_context(self.result_id, summary, changes):
            raise ValueError("Reanalysis baseline differs from the previous answer context")


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
    reanalysis_baseline: ReanalysisBaselineReference | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    queued_at: dt.datetime | None = Field(default=None, exclude_if=lambda value: value is None)
    prepared_input_receipt: (
        PrimaryInputReceipt
        | LooseRclInputReceipt
        | SejmSelfAmendmentInputReceipt
        | RclArchiveInputReceipt
        | RclArchiveSelfAmendmentInputReceipt
        | None
    ) = Field(default=None, exclude_if=lambda value: value is None)

    local_timing_witness: LocalTimingWitness | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    frozen_request_manifest: FrozenRequestManifest | None = Field(
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
        if isinstance(receipt, MultipartTextInputReceipt):
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
                if bool(part.members) != isinstance(recorded, ArchiveInputPart) or (
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
    frozen_request_manifest: FrozenRequestManifest | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    review_input_witness: ReviewPreparedInputWitness | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    amendment_input_ref: AmendmentInputRef | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    amendment_scan_input: AmendmentScanInputWitness | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    supplement_scan_input: SupplementScanInputWitness | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    def require_supplement_scan_carrier(self, purpose: str, term: int, number: str) -> None:
        witness = self.supplement_scan_input
        if witness is None:
            return
        context = witness.context_without_scan
        binding, manifest = self.input_binding, self.frozen_request_manifest
        if (
            purpose != "supplement"
            or binding is None
            or (binding.term, binding.number) != (term, number)
            or (witness.term, context.number) != (term, number)
            or witness.document_number != self.document_number
            or witness.source_url != self.source_url
            or context.document_title != self.title
            or context.source_kind != self.source_kind
            or witness.scan_input != self.scan_input
            or witness.request_sha256 != self.request_sha256
            or manifest is None
            or manifest.purpose != "supplement"
            or witness.request_sha256 != manifest.request_sha256
            or witness.manifest_sha256 != manifest.manifest_sha256
        ):
            raise ValueError("invalid_supplement_scan_input")

    def require_review_input_carrier(
        self, purpose: str, term: int, number: str, *, consumed: bool
    ) -> None:
        witness = self.review_input_witness
        if witness is None:
            return
        if (
            purpose != "consultation_review"
            or (term, number) != (witness.term, witness.number)
            or (not consumed and self.review_context is None)
        ):
            raise ValueError("Review prepared input differs from its submitted item")

    def require_amendment_scan_carrier(self, purpose: str, term: int, number: str) -> None:
        witness, binding = self.amendment_scan_input, self.input_binding
        if witness is None:
            return
        context = witness.context_without_scan if not witness.external_context else None
        if (
            purpose != "amendments"
            or binding is None
            or (binding.term, binding.number) != (term, number)
            or (context is not None and context.number != number)
            or witness.request_sha256 != self.request_sha256
            or self.source_kind
            != (context.source_kind if context is not None else witness.source_kind)
            or witness.source_url != self.source_url
            or (
                self.amendment_revision is not None
                and self.amendment_revision.source_url != witness.source_url
            )
            or witness.scan_input != self.scan_input
            or (context is not None and self.amendment_source != context.source_check)
            or (
                context is None
                and (self.amendment_source is not None)
                != (witness.source_kind == "senate_amendments")
            )
            or self.amendment_input_ref is not None
            or self.prepared_input_receipt is not None
        ):
            raise ValueError("invalid_amendment_scan_input")

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
        if self.supplement_scan_input is not None:
            self.require_supplement_scan_carrier(
                "supplement",
                self.supplement_scan_input.term,
                self.supplement_scan_input.context_without_scan.number,
            )
        witness = self.review_input_witness
        if witness is not None:
            manifest, binding = self.frozen_request_manifest, self.input_binding
            if (
                manifest is None
                or binding is None
                or (witness.term, witness.number) != (binding.term, binding.number)
                or witness.request_sha256 != self.request_sha256
                or witness.request_sha256 != manifest.request_sha256
                or manifest.purpose != "consultation_review"
                or witness.manifest_sha256 != manifest.manifest_sha256
            ):
                raise ValueError("Review prepared input differs from its frozen request")
            if self.review_context is not None:
                witness.require_context(self.review_context)
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
    try:
        decoded = read_json(raw)
    except ValueError, TypeError, RecursionError:
        raise ValueError("invalid_batch_metadata_json") from None
    return validate_batch_item_meta(kind, decoded, submitted=submitted)


def validate_batch_item_meta(
    kind: str, decoded: object, *, submitted: bool
) -> BatchItemMeta | DigestItemMeta:
    if (
        isinstance(decoded, dict)
        and "amendment_scan_input" in decoded
        and (not submitted or kind != "amendments")
    ):
        raise ValueError("invalid_amendment_scan_input")
    if (
        isinstance(decoded, dict)
        and "review_input_witness" in decoded
        and (not submitted or kind != "consultation_review")
    ):
        raise ValueError("Review prepared input belongs to submitted consultation reviews")
    if (
        isinstance(decoded, dict)
        and "supplement_scan_input" in decoded
        and (not submitted or kind != "supplement")
    ):
        raise ValueError("Supplement scan input belongs to submitted supplements")
    if isinstance(decoded, dict) and "reanalysis_baseline" in decoded and kind != "reanalysis":
        raise ValueError("Reanalysis baseline requires its actual purpose")
    model = BatchItemMeta if kind in ("analysis", "reanalysis") else DigestItemMeta
    parsed = model.model_validate(decoded)
    manifest = parsed.frozen_request_manifest
    if manifest is not None:
        if not submitted:
            raise ValueError("Frozen request manifests belong to submitted items")
        manifest.require_request(kind, parsed.request_sha256)
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
        and isinstance(parsed.prepared_input_receipt, MultipartTextInputReceipt)
        and parsed.request_sha256 is None
    ):
        raise ValueError("Submitted multipart text receipts require their frozen request checksum")
    if (
        isinstance(parsed, BatchItemMeta)
        and parsed.reanalysis_baseline is not None
        and (parsed.input_binding is None or (submitted and parsed.request_sha256 is None))
    ):
        raise ValueError("Reanalysis baseline requires its bound request")
    timing = parsed.local_timing_witness
    if timing is not None:
        if timing.purpose != kind:
            raise ValueError("Local timing must identify the submitted purpose")
        if submitted:
            timing.require_request(kind, parsed.request_sha256)
    return parsed


def _typed_secondary_input_metadata(value: object) -> object:
    if isinstance(value, dict) and isinstance(value.get("meta"), dict):
        meta = value["meta"]
        if (
            meta.get("amendment_input_ref") is not None
            or "amendment_scan_input" in meta
            or "review_input_witness" in meta
            or "supplement_scan_input" in meta
        ):
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
    def typed_secondary_input_metadata(cls, value: object) -> object:
        return _typed_secondary_input_metadata(value)

    @model_validator(mode="after")
    def actual_local_timing(self) -> Self:
        manifest = self.meta.frozen_request_manifest
        if manifest is not None:
            manifest.require_request(self.call_kind, self.meta.request_sha256)
        if isinstance(self.meta, DigestItemMeta) and self.meta.amendment_input_ref is not None:
            self.meta.amendment_input_ref.require_carrier(
                self.call_kind, self.meta.request_sha256, self.meta.input_binding
            )
        if isinstance(self.meta, DigestItemMeta):
            self.meta.require_amendment_scan_carrier(self.call_kind, self.term, self.number)
            self.meta.require_supplement_scan_carrier(self.call_kind, self.term, self.number)
            self.meta.require_review_input_carrier(
                self.call_kind, self.term, self.number, consumed=self.consumed_at is not None
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
        value = _typed_secondary_input_metadata(value)
        if isinstance(value, dict):
            meta = value.get("meta")
            if isinstance(meta, dict) and "reanalysis_baseline" in meta:
                value = {**value, "meta": BatchItemMeta.model_validate(meta)}
                meta = value["meta"]
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

    @property
    def recorded_scan_input(self) -> ScanInputManifest | None:
        if isinstance(self.meta, DigestItemMeta):
            return self.meta.scan_input
        return self.meta.selection.scan_input

    def require_scan_binding(self) -> None:
        manifest = self.recorded_scan_input
        if manifest is None:
            return
        question = self.request.question
        if isinstance(self.meta, BatchItemMeta):
            if (
                not isinstance(question, AnalysisQuestion)
                or question.ctx.text_source != "scan"
                or question.ctx.scan is None
                or question.ctx.scan.scan_input != manifest
            ):
                raise ValueError("Primary scan selection differs from its frozen question")
        elif (
            not isinstance(question, (AmendmentsQuestion, SupplementQuestion))
            or question.ctx.scan is None
            or question.ctx.scan.scan_input != manifest
        ):
            raise ValueError("Secondary scan metadata differs from its frozen question")

    @model_validator(mode="after")
    def actual_input_receipt(self) -> Self:
        self.require_scan_binding()
        if isinstance(self.meta, DigestItemMeta) and self.meta.amendment_scan_input is not None:
            raise ValueError("invalid_amendment_scan_input")
        if isinstance(self.meta, DigestItemMeta) and self.meta.review_input_witness is not None:
            raise ValueError("Review prepared input belongs to submitted consultation reviews")
        if isinstance(self.meta, DigestItemMeta) and self.meta.supplement_scan_input is not None:
            raise ValueError("Supplement scan input belongs to submitted supplements")
        if self.meta.frozen_request_manifest is not None:
            raise ValueError("Frozen request manifests belong to submitted items")
        if isinstance(self.meta, DigestItemMeta) and self.meta.amendment_input_ref is not None:
            self.meta.amendment_input_ref.require_carrier(
                self.request.call_kind, self.request.payload_sha256, self.meta.input_binding
            )
        if isinstance(self.meta, BatchItemMeta) and (baseline := self.meta.reanalysis_baseline):
            request = self.request
            question = request.question
            binding = self.meta.input_binding
            if (
                not isinstance(question, AnalysisQuestion)
                or question.kind != "reanalysis"
                or request.payload_sha256 is None
                or binding is None
                or (binding.term, binding.number) != (request.term, request.number)
            ):
                raise ValueError("Reanalysis baseline requires its frozen reanalysis question")
            baseline.require_context(
                question.ctx.previous_summary, question.ctx.previous_key_changes
            )
        receipt = self.meta.prepared_input_receipt
        if isinstance(self.request.question, AnalysisQuestion):
            word_ctx = self.request.question.ctx
            if word_ctx.word_formula_input is not None:
                self._validate_word_formula_input(word_ctx, receipt)
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
            if isinstance(receipt, MultipartTextInputReceipt):
                binding = self.meta.input_binding
                if (
                    ctx.source_kind != receipt.source_kind
                    or request.question.ctx.text_source == "scan"
                    or ctx.number != request.number
                    or binding is None
                    or (binding.term, binding.number) != (request.term, request.number)
                ):
                    raise ValueError("Multipart text receipt requires its actual source question")
            if isinstance(receipt, RclArchiveSelfAmendmentInputReceipt):
                final = receipt.segments[-1]
                if input_text_sha256(ctx.text[final.start : final.end]) != (
                    receipt.amendment_part.text_sha256
                ):
                    raise ValueError("Hybrid receipt differs from its frozen amendment text")
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
        if receipt.block_companion is not None and any(
            input_text_sha256(ctx.text[row.start : row.end]) != row.text_sha256
            for row in receipt.block_companion.rows
        ):
            raise ValueError("block companion differs from actual emitted request text")

        return self

    def _validate_word_formula_input(
        self,
        ctx: BillContext,
        receipt: PrimaryInputReceipt
        | MultipartTextInputReceipt
        | SupplementInputReceipt
        | AmendmentInputReceipt
        | None,
    ) -> None:
        companion = ctx.word_formula_input
        if companion is None:
            return
        request, meta = self.request, self.meta
        if (
            request.call_kind != "analysis"
            or self.provider != "openai"
            or ctx.source_kind != "rcl"
            or ctx.text_source != "documents"
            or ctx.scan is not None
            or ctx.truncated
            or ctx.number != request.number
            or not isinstance(meta, BatchItemMeta)
            or meta.source_kind != ctx.source_kind
            or meta.text_source != ctx.text_source
            or meta.input_chars != len(ctx.text)
            or len(meta.selection.parts) != 1
            or meta.selection.parts[0].role != "bill"
            or meta.selection.parts[0].sha256 != companion.artifact_sha256
            or meta.input_binding is None
            or (meta.input_binding.term, meta.input_binding.number)
            != (request.term, request.number)
            or not isinstance(receipt, PrimaryInputReceipt)
            or receipt.retention != "complete"
            or receipt.source_part.role != "bill"
            or receipt.extraction_id is None
            or receipt.source_selection_id != companion.selection_id
            or receipt.artifact_sha256 != companion.artifact_sha256
            or request.payload_json is None
        ):
            raise ValueError("Word formula input requires its exact primary RCL source and request")
        for item in companion.formulas:
            start, end = item.emitted_anchor
            copied = any(
                segment.source_start is not None
                and segment.origin == "source"
                and segment.start <= start < end <= segment.end
                and segment.source_start + start - segment.start == item.source_anchor[0]
                for segment in receipt.segments
            )
            if not copied or ctx.text[item.emitted_gap - 1 : item.emitted_gap + 1] != "\n\n":
                raise ValueError("Word formula anchor differs from the frozen primary copy")
        envelope = read_json(request.payload_json)
        body = envelope.get("body") if isinstance(envelope, dict) else None
        if (
            not isinstance(envelope, dict)
            or envelope.get("custom_id") != request.custom_id
            or envelope.get("method") != "POST"
            or envelope.get("url") != "/v1/responses"
            or not isinstance(body, dict)
            or body.get("model") != request.model
        ):
            raise ValueError("Word formula input requires its frozen OpenAI request")
        messages = body.get("input")
        user = messages[-1] if isinstance(messages, list) and len(messages) == 2 else None
        content = (
            user.get("content") if isinstance(user, dict) and user.get("role") == "user" else None
        )
        blocks = content if isinstance(content, list) else []
        block = blocks[0] if len(blocks) == 1 else None
        prompt = (
            block.get("text")
            if isinstance(block, dict) and block.get("type") == "input_text"
            else None
        )
        if not isinstance(prompt, str) or not prompt.endswith(
            "\n=== TEKST DRUKU ===\n" + ctx.text + companion.prompt_suffix()
        ):
            raise ValueError("Word formula data differs from the frozen provider input")

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
