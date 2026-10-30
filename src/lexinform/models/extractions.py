"""Bounded extraction metadata identifies observations without retaining text or block bodies."""

import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.models.documents import ExtractedDocument

MAX_EXTRACTION_METADATA_BYTES = 16_384
MAX_EXTRACTION_PART_SUMMARIES = 16
MAX_WORD_VIEW_STATUS_BYTES = 512


class ExtractionComponent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["package", "pdf", "word", "doc", "odt"]
    qualified_name_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_file_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ExtractionDependency(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Literal["python", "pypdf", "olefile", "inflate64"]
    version: str | None = Field(default=None, max_length=64)


class ExtractionOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_member_bytes: int | None = None
    max_part_chars: int | None = None
    archive_depth: int = Field(ge=0)
    archive_bytes: int = Field(ge=0)
    archive_members: int = Field(ge=0)
    xml_bytes: int = Field(ge=0)


class WordViewStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    version: Literal["word-legacy-view-status-v1"] = "word-legacy-view-status-v1"
    scope: Literal["legacy-visited-word-nodes"] = "legacy-visited-word-nodes"
    known_walk: Literal["finished", "interrupted"]
    xml_completeness: Literal["not_asserted"] = "not_asserted"
    fields_view: Literal["legacy_unresolved"] = "legacy_unresolved"
    changes_view: Literal["legacy_unresolved"] = "legacy_unresolved"
    observed_skipped_blocks: int = Field(strict=True, ge=0, le=2**63 - 1)
    observed_unclassified_nodes: int = Field(strict=True, ge=0, le=2**63 - 1)
    observed_legacy_recursion: int = Field(strict=True, ge=0, le=2**63 - 1)
    observed_ignored_fallbacks: int = Field(strict=True, ge=0, le=2**63 - 1)

    @model_validator(mode="after")
    def bounded_status(self) -> Self:
        if len(self.model_dump_json().encode()) > MAX_WORD_VIEW_STATUS_BYTES:
            raise ValueError("Word view status exceeds its serialized byte limit")
        return self


class ExtractedPartSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    physical_index: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    member_path_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    text_chars: int = Field(ge=0)
    format: Literal["pdf", "word", "odt", "doc", "word_xml", "unknown"]
    coordinates: Literal["pdf-pages-v1", "word-blocks-v1", "odt-blocks-v1", "unknown"]
    text_view: Literal[
        "pdf-whitespace-v1", "legacy-word-body-v1", "legacy-odt-body-v1", "unlocated", "unknown"
    ]
    manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    block_count: int | None = Field(default=None, ge=0)
    error_count: int = Field(ge=0)
    limitations: tuple[
        Literal[
            "pdf_text_layer_only",
            "odt_notes_unmapped",
            "tracked_changes_unresolved",
            "table_repetitions_unexpanded",
            "coordinates_unknown",
            "notes_unlocated",
            "tracked_changes_unknown",
            "fields_unlocated",
        ],
        ...,
    ] = Field(max_length=8)
    loss_count: int = Field(ge=0)
    losses_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    word_view_status: WordViewStatus | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def status_requires_word(self) -> Self:
        if self.word_view_status is not None:
            if not isinstance(self.word_view_status, WordViewStatus):
                raise ValueError("Constructed Word summary requires its typed view status")
            WordViewStatus.model_validate(self.word_view_status)
        if self.word_view_status is not None and (
            self.format != "word"
            or self.coordinates != "word-blocks-v1"
            or self.text_view != "legacy-word-body-v1"
            or self.manifest_sha256 is None
            or self.block_count is None
        ):
            raise ValueError("Word view status requires an actual Word legacy manifest summary")
        return self


class ExtractionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    contract: Literal["physical-extraction-summary-v1"] = "physical-extraction-summary-v1"
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    package_policy_version: Literal["package-selection-v1"] = "package-selection-v1"
    package_policy_source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    components: tuple[ExtractionComponent, ...] = Field(min_length=1, max_length=5)
    dependencies: tuple[ExtractionDependency, ...] = Field(max_length=4)
    options: ExtractionOptions
    physical_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_chars: int = Field(ge=0)
    physical_part_count: int = Field(ge=0)
    selected_part_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    rejected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_parts: tuple[ExtractedPartSummary, ...] = Field(
        max_length=MAX_EXTRACTION_PART_SUMMARIES
    )
    omitted_selected_parts: int = Field(ge=0)
    bodies: Literal["not_retained"] = "not_retained"
    physical_coordinates: Literal["manifest_not_retained"] = "manifest_not_retained"
    input_mapping: Literal["unmapped"] = "unmapped"
    completeness: Literal["not_asserted"] = "not_asserted"

    @property
    def result_id(self) -> str:
        return "extraction-v1:" + hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    @model_validator(mode="after")
    def bounded_summary(self) -> Self:
        indices = [part.physical_index for part in self.selected_parts]
        if len(set(indices)) != len(indices) or any(
            index >= self.physical_part_count for index in indices
        ):
            raise ValueError("extraction summaries require distinct existing physical parts")
        if self.selected_part_count > self.physical_part_count:
            raise ValueError("selected parts exceed the physical extraction")
        if len(indices) + self.omitted_selected_parts != self.selected_part_count:
            raise ValueError("extraction summaries and omissions must account for selected parts")
        if len(self.model_dump_json().encode()) > MAX_EXTRACTION_METADATA_BYTES:
            raise ValueError("extraction metadata exceeds its serialized byte limit")
        return self


class SelectionExtractionReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    selection_id: str = Field(pattern=r"^selection-v1:[0-9a-f]{64}$")
    extraction_id: str = Field(pattern=r"^extraction-v1:[0-9a-f]{64}$")
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DocumentExtraction(BaseModel):
    model_config = ConfigDict(frozen=True)

    document: ExtractedDocument
    result: ExtractionResult


def extraction_metadata_sha256(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
