"""Retained context for an exact full-scan supplement question."""

import hashlib
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.json_storage import canonical_json, read_json
from lexinform.metadata_urls import metadata_url_decision
from lexinform.models.analysis import ScannedDocument, SupplementContext
from lexinform.models.enums import SUPPLEMENT_SOURCES
from lexinform.models.physical_inventory import Sha256
from lexinform.models.scan_input import MAX_SCAN_BYTES, MAX_SCAN_PAGES, ScanInputManifest

MAX_SUPPLEMENT_SCAN_WITNESS_BYTES = 4 * 1024


class SupplementScanInputWitness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    profile: Literal["supplement-full-scan-question-v1"] = "supplement-full-scan-question-v1"
    term: int = Field(gt=0, strict=True)
    document_number: str = Field(min_length=1, max_length=160)
    source_url: str = Field(min_length=1, max_length=MAX_SUPPLEMENT_SCAN_WITNESS_BYTES)
    request_sha256: Sha256
    manifest_sha256: Sha256
    question_sha256: Sha256
    context_json: str = Field(max_length=MAX_SUPPLEMENT_SCAN_WITNESS_BYTES)
    scan_input: ScanInputManifest

    @property
    def context_without_scan(self) -> SupplementContext:
        value = read_json(self.context_json)
        if not isinstance(value, dict) or "scan" in value:
            raise ValueError("invalid_supplement_scan_input")
        context = SupplementContext.model_validate(value)
        if (
            context.text != ""
            or context.truncated
            or not context.number.strip()
            or not context.document_title.strip()
            or context.source_kind not in SUPPLEMENT_SOURCES
            or canonical_json(context.model_dump(mode="json", exclude={"scan"}))
            != self.context_json
        ):
            raise ValueError("invalid_supplement_scan_input")
        return context

    @model_validator(mode="after")
    def full_original_pdf(self) -> Self:
        manifest = self.scan_input
        if (
            not self.document_number.strip()
            or not metadata_url_decision(self.source_url).admitted
            or manifest.transformations
            or manifest.first_page != 1
            or manifest.page_count != manifest.source_pages
            or manifest.input_sha256 != manifest.source_sha256
            or manifest.input_bytes != manifest.source_bytes
            or manifest.input_bytes > MAX_SCAN_BYTES
            or manifest.page_count > MAX_SCAN_PAGES
            or len(self.model_dump_json().encode()) > MAX_SUPPLEMENT_SCAN_WITNESS_BYTES
        ):
            raise ValueError("invalid_supplement_scan_input")
        _ = self.context_without_scan
        return self

    def restore_context(self, scan: ScannedDocument) -> SupplementContext:
        if (
            scan.scan_input != self.scan_input
            or scan.media_type != "application/pdf"
            or scan.cover_letter_pages != 0
        ):
            raise ValueError("invalid_supplement_scan_input")
        context = self.context_without_scan.model_copy(update={"scan": scan.verify_exact_input()})
        question = canonical_json({"kind": "supplement", "ctx": context.model_dump(mode="json")})
        if hashlib.sha256(question.encode()).hexdigest() != self.question_sha256:
            raise ValueError("invalid_supplement_scan_input")
        return context
