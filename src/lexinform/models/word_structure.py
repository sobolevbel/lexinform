"""Opt-in Word package structure describes the observed legacy walk without page or span claims."""

import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.models.documents import ExtractedParts, WordDetailedExtraction
from lexinform.models.word_notes import WordNote, WordNoteCall, WordNoteXmlPart

WordXmlPart = Literal["word/document.xml", "word/footnotes.xml", "word/endnotes.xml"]
WordStructureView = Literal[
    "structural",
    "text",
    "tab",
    "line_break",
    "page_break",
    "note_reference",
    "inline_note",
    "recursed",
    "ignored_leaf",
    "ignored_fallback",
    "skipped_block",
    "note_entry",
    "skipped_missing_id",
    "part_root",
]
Count = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


def structure_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("ascii")
    ).hexdigest()


class _Location(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")
    order: Count
    xml_part: WordXmlPart
    path: tuple[Count, ...]
    note_id: str | None = None

    @model_validator(mode="after")
    def note_location(self) -> Self:
        if self.note_id is not None:
            expected = f"word-note:{self.xml_part}:{self.path[0]}" if self.path else None
            if self.xml_part == "word/document.xml" or self.note_id != expected:
                raise ValueError("Word structure note must bind its actual entry path")
        return self


class WordStructureBlock(_Location):
    kind: Literal["paragraph", "table_row", "table_cell"]
    text_sha256: Sha256
    text_characters: Count


class WordStructureSupport(_Location):
    tag: str = Field(min_length=1, max_length=4096)
    child_count: Count
    support: Literal["recognized", "legacy_recursion", "unclassified"]
    view: WordStructureView


class WordStructureCall(_Location):
    call: WordNoteCall
    legacy_view: Literal["expanded", "missing_marker"]

    @model_validator(mode="after")
    def call_location(self) -> Self:
        if self.path != self.call.path:
            raise ValueError("Word structure call differs from its actual locator")
        return self


class WordStructureExtraction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")
    version: Literal["word-structure-v1"] = "word-structure-v1"
    scope: Literal["legacy-visited-package-nodes"] = "legacy-visited-package-nodes"
    input_sha256: Sha256
    xml_sha256: Sha256
    body_manifest_sha256: Sha256
    notes_manifest_sha256: Sha256
    note_parts: tuple[WordNoteXmlPart, ...]
    blocks: tuple[WordStructureBlock, ...]
    calls: tuple[WordStructureCall, ...]
    support: tuple[WordStructureSupport, ...]
    block_count: Count
    call_count: Count
    support_count: Count
    walk_complete: bool = Field(strict=True)
    limit_reached: bool = Field(strict=True)
    max_events: Annotated[int, Field(strict=True, ge=1, le=65536)]
    max_bytes: Annotated[int, Field(strict=True, ge=4096, le=1048576)]
    coverage: Literal["complete", "partial"]
    observed_stream_sha256: Sha256 | None

    @property
    def manifest_sha256(self) -> str:
        return structure_sha256(self.model_dump(mode="json"))

    def stream(self) -> list[dict[str, object]]:
        entries = [
            {"type": kind, **entry.model_dump(mode="json")}
            for kind, records in (
                ("block", self.blocks),
                ("call", self.calls),
                ("support", self.support),
            )
            for entry in records
        ]
        return sorted(entries, key=lambda entry: int(str(entry["order"])))

    @model_validator(mode="after")
    def retained_prefix(self) -> Self:
        records = self.stream()
        if [entry["order"] for entry in records] != list(range(len(records))):
            raise ValueError("Word structure retains a unique observed event prefix")
        if any(
            actual < retained
            for actual, retained in (
                (self.block_count, len(self.blocks)),
                (self.call_count, len(self.calls)),
                (self.support_count, len(self.support)),
            )
        ):
            raise ValueError("Word structure retained counts exceed actual observations")
        observed = self.block_count + self.call_count + self.support_count
        if self.limit_reached != (observed > len(records)):
            raise ValueError("Word structure omissions require their actual retention limit")
        complete = self.walk_complete and not self.limit_reached
        if (self.coverage == "complete") != complete:
            raise ValueError("Word structure coverage differs from its actual walk")
        if complete and (self.block_count, self.call_count, self.support_count) != (
            len(self.blocks),
            len(self.calls),
            len(self.support),
        ):
            raise ValueError("Complete Word structure cannot omit observed records")
        if self.observed_stream_sha256 != (structure_sha256(records) if complete else None):
            raise ValueError("Word structure full stream hash requires a complete retained walk")
        if (
            len(records) > self.max_events
            or len(
                json.dumps(
                    self.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":")
                ).encode("ascii")
            )
            > self.max_bytes
        ):
            raise ValueError("Word structure exceeds its complete bounded envelope")
        return self


class WordStructuredExtraction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")
    legacy: WordDetailedExtraction
    structure: WordStructureExtraction

    @model_validator(mode="after")
    def actual_legacy_bindings(self) -> Self:
        legacy, value = self.legacy, self.structure
        if (
            value.input_sha256,
            value.xml_sha256,
            value.body_manifest_sha256,
            value.notes_manifest_sha256,
            value.note_parts,
        ) != (
            legacy.body.input_sha256,
            legacy.body.xml_sha256,
            legacy.body.manifest_sha256,
            legacy.notes.manifest_sha256,
            legacy.notes.parts,
        ):
            raise ValueError("Word structure must bind its actual unchanged legacy manifests")
        needed = {
            (record.xml_part, record.path[0])
            for record in (*value.blocks, *value.calls, *value.support)
            if record.path
        }
        notes: dict[tuple[WordXmlPart, int], WordNote] = {
            (note.xml_part, note.path[0]): note
            for note in legacy.notes.notes
            if (note.xml_part, note.path[0]) in needed
        }
        parts = {part.name for part in legacy.notes.parts}
        for record in (*value.blocks, *value.calls, *value.support):
            if record.xml_part != "word/document.xml" and record.xml_part not in parts:
                raise ValueError("Word structure refers to an absent XML part")
            note = notes.get((record.xml_part, record.path[0])) if record.path else None
            if record.note_id != (note.note_id if note is not None else None):
                raise ValueError("Word structure note binding differs from its actual entry")
        for reference in value.calls:
            call = reference.call
            if call.target is None and call.state not in ("missing", "unverified"):
                raise ValueError("Word structure expanded reference requires its actual target")
            if call.target is not None:
                if call.target >= len(legacy.notes.notes):
                    raise ValueError("Word structure call target is unavailable")
                note = legacy.notes.notes[call.target]
                expected = "word/footnotes.xml" if call.kind == "footnote" else "word/endnotes.xml"
                if note.xml_part != expected or note.identity != call.identity:
                    raise ValueError("Word structure call target belongs to another note")
                if call.state != ("expanded" if note.text else "empty"):
                    raise ValueError("Word structure target state differs from its actual text")
        return self


class WordPartStructureExtraction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    part_index: Count
    extraction: WordStructureExtraction


class WordStructuredParts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    legacy: ExtractedParts
    word_structure: tuple[WordPartStructureExtraction, ...] = ()

    @model_validator(mode="after")
    def structures_refer_to_actual_legacy_parts(self) -> Self:
        legacy = ExtractedParts.model_validate(self.legacy.model_dump())
        bodies = {record.part_index: record.extraction for record in legacy.word_blocks}
        notes = {record.part_index: record.extraction for record in legacy.word_notes}
        previous = -1
        for record in self.word_structure:
            index = record.part_index
            if index <= previous or index not in bodies or index not in notes:
                raise ValueError(
                    "Word structure requires unique ordered physical body and note links"
                )
            WordStructuredExtraction(
                legacy=WordDetailedExtraction(body=bodies[index], notes=notes[index]),
                structure=record.extraction,
            )
            previous = index
        return self
