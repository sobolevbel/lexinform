"""Immutable amendment questions preserve actual text input without a byte-admission policy."""

import hashlib
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.json_storage import canonical_json, read_json
from lexinform.metadata_urls import metadata_url_decision
from lexinform.models.analysis import AmendmentsContext, ScannedDocument
from lexinform.models.computation_inputs import ComputationInputBinding
from lexinform.models.input_receipts import AmendmentInputReceipt
from lexinform.models.scan_input import MAX_SCAN_BYTES, MAX_SCAN_PAGES, ScanInputManifest

MAX_AMENDMENT_SCAN_WITNESS_BYTES = 4 * 1024


class AmendmentInputSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: Literal["amendments-text-question-v1"] = "amendments-text-question-v1"
    question_json: str

    @property
    def context(self) -> AmendmentsContext:
        value = read_json(self.question_json)
        if (
            not isinstance(value, dict)
            or set(value) != {"kind", "ctx"}
            or value["kind"] != "amendments"
        ):
            raise ValueError("invalid_amendment_input")
        context = AmendmentsContext.model_validate(value["ctx"])
        if context.scan is not None or not isinstance(value["ctx"], dict):
            raise ValueError("invalid_amendment_input")
        if canonical_json(context.model_dump(mode="json", exclude_unset=True)) != canonical_json(
            value["ctx"]
        ):
            raise ValueError("invalid_amendment_input")
        return context

    @property
    def question_sha256(self) -> str:
        return hashlib.sha256(self.question_json.encode()).hexdigest()

    @property
    def snapshot_id(self) -> str:
        return (
            "amendment-input-v1:"
            + hashlib.sha256((self.profile + "\n" + self.question_json).encode()).hexdigest()
        )

    @model_validator(mode="after")
    def typed_canonical_text(self) -> Self:
        try:
            if canonical_json(read_json(self.question_json)) != self.question_json:
                raise ValueError("invalid_amendment_input")
            _ = self.context
        except ValueError, TypeError, RecursionError:
            raise ValueError("invalid_amendment_input") from None
        return self


class AmendmentScanInputWitness(BaseModel):
    """Retain the exact full-scan question context without retaining the PDF bytes."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    version: Literal[1, 2] = 1
    source_url: str = Field(max_length=MAX_AMENDMENT_SCAN_WITNESS_BYTES)
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    question_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    context_json: str = Field(max_length=MAX_AMENDMENT_SCAN_WITNESS_BYTES)
    scan_input: ScanInputManifest

    @property
    def context_without_scan(self) -> AmendmentsContext:
        value = read_json(self.context_json)
        if not isinstance(value, dict) or "scan" in value:
            raise ValueError("invalid_amendment_scan_input")
        context = AmendmentsContext.model_validate(value)
        if (
            context.text != ""
            or context.truncated
            or not self._matches_source(context)
            or canonical_json(context.model_dump(mode="json", exclude={"scan"}))
            != self.context_json
        ):
            raise ValueError("invalid_amendment_scan_input")
        return context

    def _matches_source(self, context: AmendmentsContext) -> bool:
        source = context.source_check
        if self.version == 1:
            return context.source_kind == "committee_amendments" and source is None
        return (
            context.source_kind == "senate_amendments"
            and source is not None
            and source.scan
            and not source.word_eof
            and bool(source.expected_subjects)
            and all(subject.strip() for subject in source.expected_subjects)
            and source.subject == source.expected_subjects[0]
            and source.pages == self.scan_input.source_pages
            and source.sha256 == self.scan_input.source_sha256
        )

    @model_validator(mode="after")
    def full_original_pdf(self) -> Self:
        manifest = self.scan_input
        if (
            not metadata_url_decision(self.source_url).admitted
            or manifest.transformations
            or manifest.first_page != 1
            or manifest.page_count != manifest.source_pages
            or manifest.input_sha256 != manifest.source_sha256
            or manifest.input_bytes != manifest.source_bytes
            or manifest.input_bytes > MAX_SCAN_BYTES
            or manifest.page_count > MAX_SCAN_PAGES
            or len(self.model_dump_json().encode()) > MAX_AMENDMENT_SCAN_WITNESS_BYTES
        ):
            raise ValueError("invalid_amendment_scan_input")
        _ = self.context_without_scan
        return self

    def restore_context(self, scan: ScannedDocument) -> AmendmentsContext:
        if (
            scan.scan_input != self.scan_input
            or scan.media_type != "application/pdf"
            or scan.cover_letter_pages != 0
        ):
            raise ValueError("invalid_amendment_scan_input")
        context = self.context_without_scan.model_copy(update={"scan": scan.verify_exact_input()})
        question = canonical_json({"kind": "amendments", "ctx": context.model_dump(mode="json")})
        if hashlib.sha256(question.encode()).hexdigest() != self.question_sha256:
            raise ValueError("invalid_amendment_scan_input")
        return context


class AmendmentInputRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str = Field(pattern=r"^amendment-input-v1:[0-9a-f]{64}$")
    question_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    lineage_receipt: AmendmentInputReceipt | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    lineage_result_id: str | None = Field(
        default=None, pattern=r"^result-v1:[0-9a-f]{64}$", exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def actual_lineage(self) -> Self:
        if self.lineage_result_id is not None and self.lineage_receipt is None:
            raise ValueError("invalid_amendment_input")
        if (
            self.lineage_receipt is not None
            and self.lineage_receipt.request_sha256 != self.request_sha256
            and self.lineage_result_id is None
        ):
            raise ValueError("invalid_amendment_input")
        return self

    def require_carrier(
        self, purpose: str, request_sha256: str | None, binding: ComputationInputBinding | None
    ) -> None:
        if purpose != "amendments" or request_sha256 != self.request_sha256 or binding is None:
            raise ValueError("invalid_amendment_input")


class AmendmentInputAvailability(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: Literal["available", "requires_exact_scan", "unavailable"]
    reason: (
        Literal[
            "root_history_unavailable", "root_amendment_unavailable", "legacy_input_unavailable"
        ]
        | None
    ) = None
    root_content_id: str | None = None
    result_id: str | None = None
    snapshot: AmendmentInputSnapshot | None = None
    reference: AmendmentInputRef | None = None
    scan_witness: AmendmentScanInputWitness | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
