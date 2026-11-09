"""Bounded extraction metadata identifies observations without retaining text or block bodies."""

import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.models.block_input import BlockCopyWitness
from lexinform.models.doc_notes import MAX_DOC_NOTE_SUMMARY_BYTES, DocNoteSummary
from lexinform.models.documents import (
    ExtractedDocument,
    ExtractedParts,
    PdfPageText,
    PdfTextExtraction,
)
from lexinform.models.odt_notes import (
    MAX_ODT_NOTE_EVENTS,
    Count,
    OdtEmittedSpan,
    OdtNoteCall,
    OdtNoteEntry,
    OdtNoteOrigins,
    Path,
)
from lexinform.models.odt_notes import _width as odt_note_metadata_width
from lexinform.models.odt_tables import MAX_ODT_TABLE_BYTES, OdtTableMerges
from lexinform.models.physical_inventory import Position, Sha256
from lexinform.models.word_field_ranges import MAX_WORD_FIELD_BYTES, WordFieldPrefix
from lexinform.models.word_grid_merges import (
    MAX_WORD_GRID_MERGE_BYTES,
    WordHorizontalGridSpans,
    WordVerticalGridMerges,
)

MAX_EXTRACTION_METADATA_BYTES = 16_384
MAX_EXTRACTION_PART_SUMMARIES = 16
MAX_WORD_VIEW_STATUS_BYTES = 512
MAX_ODT_NOTE_STATUS_BYTES = 1024
MAX_ODT_NOTE_PREFIX_BYTES = 4096
MAX_PDF_PAGE_MAP_BYTES = 4096


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


class WordInlineMarkers(BaseModel):
    """Counts of field and revision nodes actually visited by the legacy walk."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    fields: int = Field(strict=True, ge=0, le=2**32 - 1)
    revisions: int = Field(strict=True, ge=0, le=2**32 - 1)
    removal_text_nodes: int | None = Field(
        default=None, strict=True, gt=0, le=2**32 - 1, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def observed_wrapper(self) -> Self:
        if self.removal_text_nodes is not None and self.revisions == 0:
            raise ValueError("Removal text requires an observed revision wrapper")
        return self


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
    observed_textbox_nodes: int | None = Field(
        default=None, strict=True, ge=0, le=2**63 - 1, exclude_if=lambda value: value is None
    )
    observed_inline: WordInlineMarkers | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def bounded_status(self) -> Self:
        if len(self.model_dump_json().encode()) > MAX_WORD_VIEW_STATUS_BYTES:
            raise ValueError("Word view status exceeds its serialized byte limit")
        return self


class OdtNoteBlockBound(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    path: Path
    kind: Literal["paragraph", "heading"]
    chars: Count


class OdtNotePrefix(BaseModel):
    """Stored event coverage and hash describe this prefix, independently of captured status."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    version: Literal["odt-note-prefix-v1"] = "odt-note-prefix-v1"
    physical_index: Count
    origins: OdtNoteOrigins
    blocks: tuple[OdtNoteBlockBound, ...] = Field(max_length=MAX_ODT_NOTE_EVENTS)

    @model_validator(mode="before")
    @classmethod
    def preflight(cls, value: object) -> object:
        if _odt_prefix_bytes(value) > MAX_ODT_NOTE_PREFIX_BYTES:
            raise ValueError("stored ODT note prefix exceeds its 4096-byte profile")
        return value

    @model_validator(mode="after")
    def block_witnesses(self) -> Self:
        if not isinstance(self.origins, OdtNoteOrigins):
            raise ValueError("stored ODT origins require their typed model")
        OdtNoteOrigins.model_validate(self.origins)
        for block in self.blocks:
            if not isinstance(block, OdtNoteBlockBound):
                raise ValueError("stored ODT block bounds require their typed model")
            OdtNoteBlockBound.model_validate(block)
        paths = [block.path for block in self.blocks]
        if paths != sorted(set(paths)):
            raise ValueError("stored ODT block witnesses must be unique and ordered")
        records = (*self.origins.entries, *self.origins.calls)
        if set(paths) != {record.block_path for record in records}:
            raise ValueError("stored ODT witnesses must describe exactly their referenced blocks")
        bounds = {block.path: block.chars for block in self.blocks}
        if any(
            record.emitted is not None and record.emitted.end > bounds[record.block_path]
            for record in records
        ):
            raise ValueError("stored ODT spans exceed their original block witness")
        return self


def _odt_prefix_bytes(value: object) -> int:
    if isinstance(value, dict):
        if len(value) > 4:
            raise ValueError("stored ODT prefix contains too many fields")
        value = {"version": "odt-note-prefix-v1", **value}
        origins = value.get("origins")
        if isinstance(origins, dict):
            if len(origins) > len(OdtNoteOrigins.model_fields):
                raise ValueError("stored ODT origins contain too many fields")
            origins = dict(origins)
            for name, field in OdtNoteOrigins.model_fields.items():
                if name not in origins and not field.is_required():
                    origins[name] = field.get_default()
            value["origins"] = origins
    size = odt_note_metadata_width(value)
    if isinstance(value, OdtNotePrefix):
        origins = value.origins
        if not isinstance(origins, OdtNoteOrigins) or any(
            not isinstance(block, OdtNoteBlockBound) for block in value.blocks
        ):
            raise ValueError("constructed ODT prefix requires typed origins and block bounds")
        if any(not isinstance(entry, OdtNoteEntry) for entry in origins.entries) or any(
            not isinstance(call, OdtNoteCall) for call in origins.calls
        ):
            raise ValueError("constructed ODT prefix requires typed event records")
        if any(
            record.emitted is not None and not isinstance(record.emitted, OdtEmittedSpan)
            for record in (*origins.entries, *origins.calls)
        ):
            raise ValueError("constructed ODT prefix requires typed emitted ranges")
    return size


class OdtNoteStatus(BaseModel):
    """Counts and hash describe original capture, independently of any stored origin prefix."""

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
    contents: Literal["not_retained", "prefix_retained"] = "not_retained"
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


class PdfPageRange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    page: int = Field(strict=True, gt=0, le=2048)
    start: Position
    end: Position
    normalized_sha256: Sha256

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if self.end <= self.start:
            raise ValueError("PDF page ranges must be nonempty")
        return self


class PdfPageMap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    profile: Literal["pdf-normalized-page-ranges-v1"] = "pdf-normalized-page-ranges-v1"
    pages: tuple[PdfPageRange, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def bounded_ranges(self) -> Self:
        previous_page = previous_end = 0
        for row in self.pages:
            if row.page <= previous_page or row.start < previous_end:
                raise ValueError("PDF page ranges must be ordered and disjoint")
            previous_page, previous_end = row.page, row.end
        if len(self.model_dump_json().encode()) > MAX_PDF_PAGE_MAP_BYTES:
            raise ValueError("PDF page map exceeds its byte limit")
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
    block_copy_witness: BlockCopyWitness | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    pdf_page_map: PdfPageMap | None = Field(default=None, exclude_if=lambda value: value is None)

    word_view_status: WordViewStatus | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    word_field_prefix: WordFieldPrefix | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    word_grid_merges: WordVerticalGridMerges | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    word_horizontal_spans: WordHorizontalGridSpans | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    odt_note_status: OdtNoteStatus | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    odt_note_prefix: OdtNotePrefix | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    odt_table_merges: OdtTableMerges | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    doc_notes: DocNoteSummary | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def status_requires_known_manifest(self) -> Self:
        witness = self.block_copy_witness
        if witness is not None:
            BlockCopyWitness.model_validate(witness)
        if witness is not None:
            if (
                witness.part_sha256,
                witness.manifest_sha256,
                witness.text_sha256,
                witness.text_chars,
            ) != (self.sha256, self.manifest_sha256, self.text_sha256, self.text_chars):
                raise ValueError("block copy witness differs from its native extracted part")
            if (
                self.block_count is None
                or len({row.block_id for row in witness.runs}) > self.block_count
            ):
                raise ValueError("block copy witness requires known native block coverage")
            coordinates = (
                "word-blocks-v1"
                if witness.profile == "word-plain-xml-copies-v1"
                else witness.coordinates
            )
            view = (
                "legacy-word-body-v1"
                if witness.profile == "word-plain-xml-copies-v1"
                else witness.text_view
            )
            formats = {"word-blocks-v1": "word", "pdf-pages-v1": "pdf", "odt-blocks-v1": "odt"}
            if (self.format, self.coordinates, self.text_view) != (
                formats.get(coordinates),
                coordinates,
                view,
            ):
                raise ValueError("block copy witness requires the matching known native extraction")
            if (
                self.format == "word"
                and extraction_metadata_sha256(
                    (witness.body_manifest_sha256, witness.notes_manifest_sha256)
                )
                != self.manifest_sha256
            ):
                raise ValueError("Word block copy witness differs from its body and note manifests")
            if self.format == "pdf" and any(
                int(row.block_id.split(":")[1]) > self.block_count for row in witness.runs
            ):
                raise ValueError("PDF block copy page is outside the retained native page count")
        if self.pdf_page_map is not None:
            page_map = PdfPageMap.model_validate(self.pdf_page_map)
            last = page_map.pages[-1]
            if (
                (self.format, self.coordinates, self.text_view)
                != ("pdf", "pdf-pages-v1", "pdf-whitespace-v1")
                or self.manifest_sha256 is None
                or self.block_count is None
                or last.page > self.block_count
                or last.end > self.text_chars
            ):
                raise ValueError("PDF page map requires matching retained physical page bounds")
        if self.doc_notes is not None:
            if not isinstance(self.doc_notes, DocNoteSummary):
                raise ValueError("Constructed DOC summary requires its typed note source")
            notes = DocNoteSummary.model_validate(self.doc_notes)
            if (self.format, self.coordinates, self.text_view) != (
                "doc",
                "unknown",
                "unlocated",
            ) or (notes.input_sha256, notes.text_sha256) != (self.sha256, self.text_sha256):
                raise ValueError("DOC note summary differs from its actual source projection")
            if notes.projections is not None and any(
                item.reference_offset is not None
                and item.reference_offset > self.text_chars
                or item.body is not None
                and item.body.end > self.text_chars
                for item in notes.projections
            ):
                raise ValueError("DOC note output coordinates exceed the extracted part")
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
        if self.word_field_prefix is not None:
            if not isinstance(self.word_field_prefix, WordFieldPrefix):
                raise ValueError("Constructed Word field summary requires its typed prefix")
            fields = WordFieldPrefix.model_validate(self.word_field_prefix)
            if (
                (self.format, self.coordinates, self.text_view)
                != ("word", "word-blocks-v1", "legacy-word-body-v1")
                or (fields.input_sha256, fields.manifest_sha256)
                != (self.sha256, self.manifest_sha256)
                or self.block_count is None
                or len(fields.paragraphs) > self.block_count
            ):
                raise ValueError("Word field prefix requires its actual input and Word manifest")
        vertical = self.word_grid_merges
        horizontal = self.word_horizontal_spans
        grid_cells = 0
        for grids in (vertical, horizontal):
            if grids is None:
                continue
            type(grids).model_validate(grids)
            grid_cells += (
                sum(len(merge.cells) for table in grids.tables for merge in table.merges)
                if isinstance(grids, WordVerticalGridMerges)
                else sum(len(table.spans) for table in grids.tables)
            )
            if (
                (self.format, self.coordinates, self.text_view)
                != ("word", "word-blocks-v1", "legacy-word-body-v1")
                or grids.input_sha256 != self.sha256
                or extraction_metadata_sha256(
                    (grids.body_manifest_sha256, grids.notes_manifest_sha256)
                )
                != self.manifest_sha256
                or self.block_count is None
                or grid_cells > self.block_count
            ):
                raise ValueError("Word grids require their actual input and body manifest")
        if (
            vertical is not None
            and horizontal is not None
            and {table.path for table in vertical.tables}
            & {table.path for table in horizontal.tables}
        ):
            raise ValueError("Word horizontal-only tables cannot include retained vertical merges")
        if self.odt_table_merges is not None:
            tables = OdtTableMerges.model_validate(self.odt_table_merges)
            if (
                self.format != "odt"
                or self.coordinates != "odt-blocks-v1"
                or self.text_view != "legacy-odt-body-v1"
                or self.block_count is None
                or (tables.input_sha256, tables.body_manifest_sha256)
                != (self.sha256, self.manifest_sha256)
            ):
                raise ValueError(
                    "ODT table merges require their actual ODT input and body manifest"
                )
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
        if self.odt_note_prefix is not None:
            if not isinstance(self.odt_note_prefix, OdtNotePrefix):
                raise ValueError("constructed ODT summary requires its typed stored prefix")
            if _odt_prefix_bytes(self.odt_note_prefix) > MAX_ODT_NOTE_PREFIX_BYTES:
                raise ValueError("stored ODT note prefix exceeds its 4096-byte profile")
            prefix = OdtNotePrefix.model_validate(self.odt_note_prefix)
            status = self.odt_note_status
            if status is None or status.contents != "prefix_retained":
                raise ValueError("stored ODT prefix requires an explicit captured content status")
            stored = prefix.origins
            if (
                prefix.physical_index != self.physical_index
                or stored.input_sha256 != self.sha256
                or stored.body_manifest_sha256 != self.manifest_sha256
                or stored.walk_state != status.known_walk
                or (stored.note_count, stored.call_count)
                != (status.observed_notes, status.observed_calls)
                or len(stored.entries) > status.retained_notes
                or len(stored.calls) > status.retained_calls
            ):
                raise ValueError(
                    "stored ODT prefix differs from its actual part or captured counts"
                )
            if (len(stored.entries), len(stored.calls)) == (
                status.retained_notes,
                status.retained_calls,
            ) and stored.retained_manifest_sha256 != status.retained_manifest_sha256:
                raise ValueError("untruncated stored ODT origins must match the captured hash")
        elif self.odt_note_status is not None and self.odt_note_status.contents != "not_retained":
            raise ValueError("retained ODT content status requires its stored prefix")
        return self


def _odt_prefix_budget(parts: object) -> None:
    if not isinstance(parts, (tuple, list)):
        return
    if len(parts) > MAX_EXTRACTION_PART_SUMMARIES:
        raise ValueError("extraction summaries exceed their existing part limit")
    size = 0
    for part in parts:
        prefix = (
            part.odt_note_prefix
            if isinstance(part, ExtractedPartSummary)
            else part.get("odt_note_prefix")
            if isinstance(part, dict)
            else None
        )
        if prefix is not None:
            size += _odt_prefix_bytes(prefix)
            if size > MAX_ODT_NOTE_PREFIX_BYTES:
                raise ValueError("stored ODT origin prefixes exceed the aggregate 4096-byte cap")


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

    @model_validator(mode="before")
    @classmethod
    def stored_odt_prefix_budget(cls, value: object) -> object:
        parts = (
            value.selected_parts
            if isinstance(value, cls)
            else value.get("selected_parts", ())
            if isinstance(value, dict)
            else ()
        )
        _odt_prefix_budget(parts)
        return value

    @model_validator(mode="after")
    def bounded_summary(self) -> Self:
        if len(self.selected_parts) > MAX_EXTRACTION_PART_SUMMARIES:
            raise ValueError("extraction summaries exceed their existing part limit")
        _odt_prefix_budget(self.selected_parts)
        for part in self.selected_parts:
            if part.block_copy_witness is not None:
                ExtractedPartSummary.model_validate(part.model_dump())
        if (
            sum(
                len(part.block_copy_witness.model_dump_json().encode())
                for part in self.selected_parts
                if part.block_copy_witness is not None
            )
            > 4096
        ):
            raise ValueError("Stored block copy witnesses exceed their aggregate byte limit")
        page_map_bytes = 0
        for part in self.selected_parts:
            if part.pdf_page_map is not None:
                ExtractedPartSummary.model_validate(part.model_dump())
                page_map_bytes += len(part.pdf_page_map.model_dump_json().encode())
        if page_map_bytes > MAX_PDF_PAGE_MAP_BYTES:
            raise ValueError("Stored PDF page maps exceed the aggregate byte limit")
        word_field_bytes = 0
        for part in self.selected_parts:
            if part.word_field_prefix is not None:
                ExtractedPartSummary.model_validate(part)
                word_field_bytes += len(part.word_field_prefix.model_dump_json().encode())
        if word_field_bytes > MAX_WORD_FIELD_BYTES:
            raise ValueError("Stored Word field prefixes exceed the aggregate byte limit")
        for part in self.selected_parts:
            if part.doc_notes is not None:
                if not isinstance(part.doc_notes, DocNoteSummary):
                    raise ValueError("Constructed DOC summary requires its typed note source")
                ExtractedPartSummary.model_validate(part.model_dump(mode="python"))
        if (
            sum(
                len(part.doc_notes.model_dump_json().encode())
                for part in self.selected_parts
                if part.doc_notes is not None
            )
            > MAX_DOC_NOTE_SUMMARY_BYTES
        ):
            raise ValueError("Stored DOC notes exceed the aggregate 4096-byte cap")
        for part in self.selected_parts:
            if (
                part.odt_note_status is not None
                or part.odt_note_prefix is not None
                or part.doc_notes is not None
            ):
                ExtractedPartSummary.model_validate(part)
        word_grid_bytes = 0
        for part in self.selected_parts:
            vertical = part.word_grid_merges
            horizontal = part.word_horizontal_spans
            if vertical is not None or horizontal is not None:
                if (vertical is not None and not isinstance(vertical, WordVerticalGridMerges)) or (
                    horizontal is not None and not isinstance(horizontal, WordHorizontalGridSpans)
                ):
                    raise ValueError("Constructed Word summary requires typed grid metadata")
                ExtractedPartSummary.model_validate(part)
                word_grid_bytes += sum(
                    len(grids.model_dump_json().encode())
                    for grids in (vertical, horizontal)
                    if grids is not None
                )
        if word_grid_bytes > MAX_WORD_GRID_MERGE_BYTES:
            raise ValueError("Stored Word grids exceed the aggregate byte limit")
        table_bytes = 0
        for part in self.selected_parts:
            if part.odt_table_merges is not None:
                if not isinstance(part.odt_table_merges, OdtTableMerges):
                    raise ValueError("Constructed ODT summary requires typed table metadata")
                ExtractedPartSummary.model_validate(part)
                table_bytes += len(part.odt_table_merges.model_dump_json().encode())
        if table_bytes > MAX_ODT_TABLE_BYTES:
            raise ValueError("Stored ODT tables exceed the aggregate byte limit")
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


MAX_ROOT_PDF_FIRST_PAGE_CHARS = 4096


def root_pdf_extraction(physical: ExtractedParts) -> PdfTextExtraction | None:
    if len(physical.parts) != 1 or len(physical.pdf_pages) != 1:
        return None
    (part,) = physical.parts
    (pdf,) = physical.pdf_pages
    if (
        part.members
        or part.sha256 != physical.input_sha256
        or part.pages != pdf.extraction.page_count
    ):
        return None
    return pdf.extraction if pdf.extraction.error_type is None else None


def root_pdf_first_page(pdf: PdfTextExtraction | None) -> PdfPageText | None:
    if pdf is None or not pdf.pages:
        return None
    first = pdf.pages[0]
    if first.error_type is not None or len(first.text) > MAX_ROOT_PDF_FIRST_PAGE_CHARS:
        return None
    return first


class DocumentExtraction(BaseModel):
    model_config = ConfigDict(frozen=True)

    document: ExtractedDocument
    result: ExtractionResult
    first_pdf_page: PdfPageText | None = Field(default=None, exclude=True, repr=False)
    complete_pdf_pages: int | None = Field(default=None, exclude=True)


def extraction_metadata_sha256(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
