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


WordRetention = Literal["event-prefix-v1", "semantic-first-v2"]
WordPrefixLimit = Literal["none", "bytes", "events"]
SEMANTIC_BYTE_QUOTAS = {"block": 352 * 1024, "call": 112 * 1024, "support": 32 * 1024}
SEMANTIC_HEADER_BYTES = 16 * 1024
WORD_EXCEPTIONAL_VIEWS = frozenset(
    {
        "ignored_leaf",
        "ignored_fallback",
        "recursed",
        "skipped_block",
        "skipped_missing_id",
        "inline_note",
    }
)


def semantic_event_quotas(max_events: int) -> dict[str, int]:
    support = max(1, max_events // 16)
    calls = max(1, max_events * 7 // 32)
    return {"block": max_events - support - calls, "call": calls, "support": support}


def word_envelope_bytes(value: object) -> int:
    return len(
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    )


class WordSemanticBlock(WordStructureBlock):
    ordinal: Count


class WordSemanticCall(WordStructureCall):
    ordinal: Count


class WordSemanticSupport(WordStructureSupport):
    ordinal: Count


class WordRecognizedSupportCount(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    xml_part: WordXmlPart
    view: WordStructureView
    count: Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]

    @model_validator(mode="after")
    def only_ordinary_recognized_views(self) -> Self:
        if self.view in WORD_EXCEPTIONAL_VIEWS:
            raise ValueError("Exceptional Word support requires its own bounded prefix")
        return self


class WordSemanticStructureExtraction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    version: Literal["word-semantic-structure-v2"] = "word-semantic-structure-v2"
    scope: Literal["legacy-visited-semantic-prefixes"] = "legacy-visited-semantic-prefixes"
    input_sha256: Sha256
    xml_sha256: Sha256
    body_manifest_sha256: Sha256
    notes_manifest_sha256: Sha256
    note_parts: tuple[WordNoteXmlPart, ...]
    blocks: tuple[WordSemanticBlock, ...]
    calls: tuple[WordSemanticCall, ...]
    support: tuple[WordSemanticSupport, ...]
    recognized_support: tuple[WordRecognizedSupportCount, ...]
    block_count: Count
    call_count: Count
    support_count: Count
    exceptional_support_count: Count
    block_limit: WordPrefixLimit
    call_limit: WordPrefixLimit
    support_limit: WordPrefixLimit
    walk_complete: bool = Field(strict=True)
    max_events: Annotated[int, Field(strict=True, ge=3, le=65536)]
    max_bytes: Annotated[int, Field(strict=True, ge=524288, le=524288)] = 524288
    coverage: Literal["semantic_complete", "semantic_partial"]
    support_coverage: Literal["aggregated", "partial"]
    observed_stream_sha256: None = None

    @property
    def limit_reached(self) -> bool:
        return any(
            reason != "none" for reason in (self.block_limit, self.call_limit, self.support_limit)
        )

    @property
    def manifest_sha256(self) -> str:
        return structure_sha256(self.model_dump(mode="json"))

    @model_validator(mode="after")
    def independent_bounded_prefixes(self) -> Self:
        buckets = [(row.xml_part, row.view) for row in self.recognized_support]
        if buckets != sorted(set(buckets)):
            raise ValueError("Recognized Word support buckets require unique canonical order")
        if (
            sum(row.count for row in self.recognized_support) + self.exceptional_support_count
            != self.support_count
        ):
            raise ValueError("Word support totals differ from their actual classifications")
        orders: list[int] = []
        event_quotas = semantic_event_quotas(self.max_events)
        for kind, records, count, reason in (
            ("block", self.blocks, self.block_count, self.block_limit),
            ("call", self.calls, self.call_count, self.call_limit),
            ("support", self.support, self.exceptional_support_count, self.support_limit),
        ):
            if [row.ordinal for row in records] != list(range(len(records))):
                raise ValueError("Semantic Word records require their per-kind observed prefix")
            current = [row.order for row in records]
            if current != sorted(set(current)) or count < len(records):
                raise ValueError("Semantic Word original orders or counts are inconsistent")
            if (reason == "none") != (count == len(records)):
                raise ValueError("Semantic Word omitted suffix requires its actual limit")
            if (
                len(records) > event_quotas[kind]
                or reason == "events"
                and len(records) != event_quotas[kind]
            ):
                raise ValueError("Semantic Word prefix exceeds its reserved event allowance")
            if (
                word_envelope_bytes([row.model_dump(mode="json") for row in records])
                > SEMANTIC_BYTE_QUOTAS[kind]
            ):
                raise ValueError("Semantic Word prefix exceeds its reserved byte allowance")
            orders.extend(current)
        if len(orders) != len(set(orders)) or any(
            order >= self.block_count + self.call_count + self.support_count for order in orders
        ):
            raise ValueError(
                "Semantic Word original event orders overlap or exceed actual observations"
            )
        if any(
            row.support == "recognized" and row.view not in WORD_EXCEPTIONAL_VIEWS
            for row in self.support
        ):
            raise ValueError("Ordinary recognized support belongs in aggregate counters")
        complete = (
            self.walk_complete
            and self.block_count == len(self.blocks)
            and self.call_count == len(self.calls)
        )
        if (self.coverage == "semantic_complete") != complete:
            raise ValueError(
                "Semantic coverage describes only the retained observed blocks and calls"
            )
        if (self.support_coverage == "aggregated") != (
            self.walk_complete and self.exceptional_support_count == len(self.support)
        ):
            raise ValueError("Support coverage differs from its observed exceptional prefix")
        payload = self.model_dump(mode="json")
        header = {
            key: value
            for key, value in payload.items()
            if key not in ("blocks", "calls", "support")
        }
        if (
            word_envelope_bytes(header) > SEMANTIC_HEADER_BYTES
            or word_envelope_bytes(payload) > self.max_bytes
        ):
            raise ValueError("Semantic Word exceeds its complete 512KiB serialized envelope")
        return self


class WordStructuredExtraction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")
    legacy: WordDetailedExtraction
    structure: Annotated[
        WordStructureExtraction | WordSemanticStructureExtraction, Field(discriminator="version")
    ]

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
    extraction: Annotated[
        WordStructureExtraction | WordSemanticStructureExtraction, Field(discriminator="version")
    ]


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
