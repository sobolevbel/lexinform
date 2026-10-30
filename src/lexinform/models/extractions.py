"""Bounded extraction metadata identifies observations without retaining text or block bodies."""

import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.models.documents import ExtractedDocument
from lexinform.models.odt_notes import MAX_ODT_NOTE_EVENTS

MAX_EXTRACTION_METADATA_BYTES = 16_384
MAX_EXTRACTION_PART_SUMMARIES = 16
MAX_WORD_VIEW_STATUS_BYTES = 512
MAX_ODT_NOTE_STATUS_BYTES = 1024


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


class OdtNoteStatus(BaseModel):
    """Origin hashes retain no coordinates and prove no full XML coverage."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    version: Literal["odt-note-status-v1"] = "odt-note-status-v1"
    origins_version: Literal["odt-note-origins-v1"] = "odt-note-origins-v1"
    scope: Literal["legacy-visited-content.xml"] = "legacy-visited-content.xml"
    known_walk: Literal["finished", "interrupted", "unavailable"]
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    body_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retained_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_notes: int = Field(strict=True, ge=0, le=2**63 - 1)
    observed_calls: int = Field(strict=True, ge=0, le=2**63 - 1)
    retained_notes: int = Field(strict=True, ge=0, le=2**63 - 1)
    retained_calls: int = Field(strict=True, ge=0, le=2**63 - 1)
    coverage: Literal["complete", "partial", "unknown"]
    contents: Literal["not_retained"] = "not_retained"
    xml_completeness: Literal["not_asserted"] = "not_asserted"

    @model_validator(mode="after")
    def observed_prefix(self) -> Self:
        if (
            self.retained_notes > self.observed_notes
            or self.retained_calls > self.observed_calls
            or self.retained_notes + self.retained_calls > MAX_ODT_NOTE_EVENTS
        ):
            raise ValueError("ODT note status requires the bounded actual retained prefix")
        complete = self.known_walk == "finished" and (self.observed_notes, self.observed_calls) == (
            self.retained_notes,
            self.retained_calls,
        )
        expected = (
            "unknown" if self.known_walk == "unavailable" else "complete" if complete else "partial"
        )
        if (
            self.coverage != expected
            or self.known_walk == "unavailable"
            and (self.observed_notes or self.observed_calls)
        ):
            raise ValueError("ODT note status coverage must describe only its observed walk")
        if len(self.model_dump_json().encode()) > MAX_ODT_NOTE_STATUS_BYTES:
            raise ValueError("ODT note status exceeds its 1024-byte metadata limit")
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

    odt_note_status: OdtNoteStatus | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def status_requires_known_manifest(self) -> Self:
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
        if self.odt_note_status is not None:
            if not isinstance(self.odt_note_status, OdtNoteStatus):
                raise ValueError("Constructed ODT summary requires its typed note status")
            value = OdtNoteStatus.model_validate(self.odt_note_status)
            if (
                self.format != "odt"
                or self.coordinates != "odt-blocks-v1"
                or self.text_view != "legacy-odt-body-v1"
                or self.block_count is None
                or self.manifest_sha256 != value.body_manifest_sha256
                or self.sha256 != value.input_sha256
            ):
                raise ValueError(
                    "ODT note status requires its actual input and legacy body manifest"
                )
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
        if len(self.selected_parts) > MAX_EXTRACTION_PART_SUMMARIES:
            raise ValueError("extraction summaries exceed their existing part limit")
        for part in self.selected_parts:
            if part.odt_note_status is not None:
                ExtractedPartSummary.model_validate(part)
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
