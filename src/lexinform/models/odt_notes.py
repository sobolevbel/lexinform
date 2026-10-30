"""Bounded native ODT note origins in the unchanged legacy inline text view."""

import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.models.documents import ExtractedParts, OdtTextExtraction

MAX_ODT_NOTE_EVENTS = 64
MAX_ODT_NOTE_BYTES = 32768
MAX_ODT_NOTE_PATH = 64
MAX_ODT_NOTE_ID_BYTES = 256
Count = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
Path = Annotated[tuple[Count, ...], Field(min_length=1, max_length=MAX_ODT_NOTE_PATH)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Association = Literal["embedded", "resolved", "missing", "ambiguous", "unverified"]


def _width(value: object, depth: int = 0) -> int:
    if depth > 8:
        raise ValueError("ODT note metadata nesting exceeds its profile")
    if value is None:
        return 4
    if isinstance(value, bool):
        return 4 if value else 5
    if isinstance(value, int):
        if value < 0 or value.bit_length() > 63:
            raise ValueError("ODT note integers exceed their profile")
        return len(str(value))
    if isinstance(value, str):
        if len(value) > MAX_ODT_NOTE_ID_BYTES:
            raise ValueError("ODT note strings exceed their profile")
        size = 2
        for char in value:
            number = ord(char)
            if 0xD800 <= number <= 0xDFFF:
                raise ValueError("ODT note strings require Unicode scalar values")
            size += (
                2
                if char in '\\"\b\f\n\r\t'
                else 12
                if number > 0xFFFF
                else 6
                if number < 32 or number > 126
                else 1
            )
        if size > MAX_ODT_NOTE_ID_BYTES:
            raise ValueError("ODT note JSON strings exceed their profile")
        return size
    if isinstance(value, BaseModel):
        if len(type(value).model_fields) > 32:
            raise ValueError("ODT note metadata has too many fields")
        value = {name: getattr(value, name, None) for name in type(value).model_fields}
    if isinstance(value, dict):
        if len(value) > 32:
            raise ValueError("ODT note metadata has too many fields")
        size = 2
        for index, (key, item) in enumerate(value.items()):
            if not isinstance(key, str):
                raise ValueError("ODT note metadata requires named fields")
            size += bool(index) + _width(key, depth + 1) + 1 + _width(item, depth + 1)
            if size > MAX_ODT_NOTE_BYTES:
                raise ValueError("ODT note metadata exceeds its byte profile")
        return size
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_ODT_NOTE_EVENTS:
            raise ValueError("ODT note sequence exceeds its profile")
        size = 2
        for index, item in enumerate(value):
            size += bool(index) + _width(item, depth + 1)
            if size > MAX_ODT_NOTE_BYTES:
                raise ValueError("ODT note metadata exceeds its byte profile")
        return size
    raise ValueError("ODT note metadata contains an unsupported value")


class _Bounded(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    @model_validator(mode="before")
    @classmethod
    def preflight(cls, value: object) -> object:
        if isinstance(value, cls):
            value = {name: getattr(value, name, None) for name in cls.model_fields}
        if not isinstance(value, dict):
            return value
        if any(name not in cls.model_fields for name in value):
            raise ValueError("ODT note metadata contains an unknown field")
        filled = dict(value)
        for name, field in cls.model_fields.items():
            if name not in filled and not field.is_required():
                filled[name] = field.get_default()
        _width(filled)
        return value


def _inside(child: tuple[int, ...], parent: tuple[int, ...], *, equal: bool = False) -> bool:
    return len(child) >= len(parent) + (not equal) and child[: len(parent)] == parent


class OdtEmittedSpan(_Bounded):
    """Offsets are witnessed by producer append operations, never inferred by substring search."""

    start: Count
    end: Count
    evidence: Literal["observed-legacy-appends"]

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.start > self.end:
            raise ValueError("ODT emitted range must be ordered")
        return self


class _Located(_Bounded):
    order: Count
    path: Path
    paragraph_path: Path
    block_path: Path
    emitted: OdtEmittedSpan | None

    @model_validator(mode="after")
    def physical_location(self) -> Self:
        if not _inside(self.path, self.paragraph_path) or not _inside(
            self.paragraph_path, self.block_path, equal=True
        ):
            raise ValueError("ODT note location must follow its actual containing paragraph/block")
        return self


class OdtNoteEntry(_Located):
    identity: str | None
    note_class: str | None
    citation_path: Path | None
    body_path: Path | None
    parent_note_path: Path | None

    @model_validator(mode="after")
    def entry_children(self) -> Self:
        if (
            any(
                path is not None and not _inside(path, self.path)
                for path in (self.citation_path, self.body_path)
            )
            or self.citation_path is not None
            and self.citation_path == self.body_path
        ):
            raise ValueError("ODT citation/body require distinct actual note child paths")
        if self.parent_note_path is not None and not _inside(self.path, self.parent_note_path):
            raise ValueError("ODT nested note requires an actual ancestor entry")
        return self


class OdtNoteCall(_Located):
    kind: Literal["embedded", "reference"]
    identity: str | None
    association: Association
    target_path: Path | None


def odt_note_association(
    kind: Literal["embedded", "reference"],
    path: tuple[int, ...],
    identity: str | None,
    entries: tuple[OdtNoteEntry, ...],
    *,
    inventory_complete: bool,
) -> tuple[Association, tuple[int, ...] | None]:
    if len(entries) > MAX_ODT_NOTE_EVENTS:
        raise ValueError("ODT note inventory exceeds its profile")
    if kind == "embedded":
        target = next((note for note in entries if note.path == path), None)
        if target is not None:
            return "embedded", target.path
        if inventory_complete:
            raise ValueError("ODT embedded call lacks its actual entry")
        return "unverified", None
    if not identity:
        return "unverified", None
    matches = [note for note in entries if identity and note.identity == identity]
    if len(matches) > 1:
        return "ambiguous", None
    if not inventory_complete:
        return "unverified", None
    return ("resolved", matches[0].path) if matches else ("missing", None)


class OdtNoteOrigins(_Bounded):
    """Coverage describes retained observations, never complete XML or legal extraction."""

    version: Literal["odt-note-origins-v1"] = "odt-note-origins-v1"
    scope: Literal["legacy-visited-content.xml"] = "legacy-visited-content.xml"
    xml_part: Literal["content.xml"] = "content.xml"
    completeness: Literal["not_asserted"] = "not_asserted"
    input_sha256: Sha256
    xml_sha256: Sha256
    body_manifest_sha256: Sha256
    walk_state: Literal["finished", "interrupted", "unavailable"]
    note_count: Count
    call_count: Count
    entries: tuple[OdtNoteEntry, ...]
    calls: tuple[OdtNoteCall, ...]
    coverage: Literal["complete", "partial", "unknown"]

    @model_validator(mode="before")
    @classmethod
    def event_budget(cls, value: object) -> object:
        if isinstance(value, cls):
            entries, calls = getattr(value, "entries", None), getattr(value, "calls", None)
        elif isinstance(value, dict):
            entries, calls = value.get("entries", ()), value.get("calls", ())
        else:
            return value
        if (
            isinstance(entries, (list, tuple))
            and isinstance(calls, (list, tuple))
            and len(entries) + len(calls) > MAX_ODT_NOTE_EVENTS
        ):
            raise ValueError("ODT note event prefix exceeds its profile")
        return value

    @model_validator(mode="after")
    def prefix_and_associations(self) -> Self:
        if self.note_count < len(self.entries) or self.call_count < len(self.calls):
            raise ValueError("ODT observed counts cannot be smaller than their prefix")
        finished = self.walk_state == "finished"
        complete = finished and (self.note_count, self.call_count) == (
            len(self.entries),
            len(self.calls),
        )
        expected = (
            "unknown" if self.walk_state == "unavailable" else "complete" if complete else "partial"
        )
        if (
            self.coverage != expected
            or self.walk_state == "unavailable"
            and (self.note_count or self.call_count)
        ):
            raise ValueError("ODT coverage must describe the actual retained/observed walk")
        records = [*self.entries, *self.calls]
        if sorted(item.order for item in records) != list(range(len(records))):
            raise ValueError("ODT retained events must form the original bounded prefix")
        ordered = sorted(records, key=lambda item: item.order)
        ordered_paths = [item.path for item in ordered]
        if ordered_paths != sorted(ordered_paths):
            raise ValueError("ODT combined events must follow their actual XML walk order")
        for left, right in zip(ordered, ordered[1:], strict=False):
            if left.path == right.path and not (
                isinstance(left, OdtNoteEntry)
                and isinstance(right, OdtNoteCall)
                and right.kind == "embedded"
            ):
                raise ValueError("ODT same-node events require entry then embedded invocation")
        for lane in (self.entries, self.calls):
            if [item.order for item in lane] != sorted(item.order for item in lane):
                raise ValueError("ODT note events must retain their original order")
            positions = [item.path for item in lane]
            if positions != sorted(set(positions)):
                raise ValueError("ODT note XML positions must be unique and ordered")
        for note in self.entries:
            ancestors = [entry.path for entry in self.entries if _inside(note.path, entry.path)]
            parent = max(ancestors, key=len) if ancestors else None
            if note.parent_note_path != parent:
                raise ValueError("ODT nested entry must identify its actual nearest note ancestor")
        for call in self.calls:
            if call.kind == "reference" and any(note.path == call.path for note in self.entries):
                raise ValueError("ODT separate reference cannot occupy a note definition node")
            expected_link = odt_note_association(
                call.kind,
                call.path,
                call.identity,
                self.entries,
                inventory_complete=finished and self.note_count == len(self.entries),
            )
            if (call.association, call.target_path) != expected_link:
                raise ValueError("ODT call association requires its actual available unique target")
            if call.kind == "embedded" and call.target_path is not None:
                target = next(note for note in self.entries if note.path == call.target_path)
                if (call.identity, call.paragraph_path, call.block_path) != (
                    target.identity,
                    target.paragraph_path,
                    target.block_path,
                ):
                    raise ValueError("ODT embedded call must identify the same physical note entry")
        _width(self)
        return self

    @property
    def retained_manifest_sha256(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"), ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("ascii")).hexdigest()

    @property
    def full_manifest_sha256(self) -> str | None:
        return self.retained_manifest_sha256 if self.coverage == "complete" else None


class OdtPartNoteOrigins(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")
    part_index: Count
    extraction: OdtNoteOrigins


class OdtNotedParts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")
    legacy: ExtractedParts
    odt_notes: tuple[OdtPartNoteOrigins, ...] = Field(max_length=MAX_ODT_NOTE_EVENTS)

    @model_validator(mode="before")
    @classmethod
    def physical_prefix_limit(cls, value: object) -> object:
        offered = (
            getattr(value, "odt_notes", None)
            if isinstance(value, cls)
            else value.get("odt_notes")
            if isinstance(value, dict)
            else None
        )
        if isinstance(offered, (tuple, list)) and len(offered) > MAX_ODT_NOTE_EVENTS:
            raise ValueError("ODT note physical prefix exceeds its profile")
        legacy = (
            getattr(value, "legacy", None)
            if isinstance(value, cls)
            else value.get("legacy")
            if isinstance(value, dict)
            else None
        )
        if (
            isinstance(legacy, ExtractedParts)
            and ExtractedParts.model_validate(legacy.model_dump(warnings=False)) != legacy
        ):
            raise ValueError("ODT note carrier requires validated typed legacy parts")
        return value

    @model_validator(mode="after")
    def actual_physical_binding(self) -> Self:
        legacy = self.legacy
        indices = [entry.part_index for entry in self.odt_notes]
        if indices != sorted(set(indices)):
            raise ValueError("ODT note parts require unique physical input order")
        needed = set(indices)
        bodies = {
            part.part_index: part.extraction
            for part in legacy.odt_blocks
            if part.part_index in needed
        }
        for part in self.odt_notes:
            value = part.extraction
            body = bodies.get(part.part_index)
            if body is None:
                raise ValueError("ODT note origins require the actual physical ODT body")
            _bind_body(value, body)
        return self


def _bind_body(value: OdtNoteOrigins, body: OdtTextExtraction) -> None:
    if (
        (value.input_sha256, value.xml_sha256, value.body_manifest_sha256)
        != (body.input_sha256, body.xml_sha256, body.manifest_sha256)
        or body.body_state != "read"
        and value.walk_state != "unavailable"
    ):
        raise ValueError("ODT note origins must bind their actual unchanged input/XML/body")
    needed = {record.block_path for record in (*value.entries, *value.calls)}
    blocks = {block.path: block for block in body.blocks if block.path in needed}
    for record in (*value.entries, *value.calls):
        block = blocks.get(record.block_path)
        if block is None or block.kind not in ("paragraph", "heading"):
            raise ValueError("ODT note origin requires its actual legacy paragraph/heading block")
        if record.emitted is not None and record.emitted.end > len(block.text):
            raise ValueError("ODT observed emitted span exceeds its actual source block")
