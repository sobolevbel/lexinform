"""Input-bound binary DOC note coordinates without retained note text."""

import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.models.documents import ExtractedParts

MAX_DOC_NOTE_LINKS = 256
MAX_DOC_NOTE_PARTS = 64
MAX_DOC_RETAINED_LINKS = 32
MAX_DOC_NOTE_SUMMARY_BYTES = 4096
Count = Annotated[int, Field(strict=True, ge=0, lt=0x7FFFFFFF)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Validation = Literal["source_characters_checked_cf_spec_unknown"]


class DocSourceNoteLink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    kind: Literal["footnote", "endnote"]
    reference_cp: Count
    note_start_cp: Count
    note_end_cp: Count
    story_start_cp: Count
    numbering_flag: int = Field(strict=True, ge=0, le=65535)

    @model_validator(mode="after")
    def nonempty_source_range(self) -> Self:
        if self.note_start_cp >= self.note_end_cp:
            raise ValueError("DOC note source range must be nonempty")
        if self.story_start_cp + self.note_end_cp >= 0x7FFFFFFF:
            raise ValueError("DOC note global CP range exceeds its profile")
        return self


class _Source(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    version: Literal["doc-note-source-v1"] = "doc-note-source-v1"
    scope: Literal["source-cp-declarations"] = "source-cp-declarations"
    input_sha256: Sha256
    text_sha256: Sha256
    state: Literal["checked", "unavailable"]
    table_name: Literal["0Table", "1Table"] | None
    main_characters: Count | None
    footnote_characters: Count | None
    endnote_characters: Count | None
    validation: Validation | None
    unavailable_reason: Literal["unsupported_or_invalid", "legacy_unavailable"] | None

    @model_validator(mode="after")
    def source_state_is_explicit(self) -> Self:
        declared = (self.main_characters, self.footnote_characters, self.endnote_characters)
        if self.state == "checked":
            if (
                any(value is None for value in declared)
                or self.table_name is None
                or self.validation is None
                or self.unavailable_reason is not None
            ):
                raise ValueError("Checked DOC note sources require their captured declarations")
        elif any(value is not None for value in declared) or (
            self.table_name is not None
            or self.validation is not None
            or self.unavailable_reason is None
        ):
            raise ValueError("Unavailable DOC notes cannot claim captured declarations")
        return self


class DocNoteCapture(_Source):
    links: tuple[DocSourceNoteLink, ...] = Field(max_length=MAX_DOC_NOTE_LINKS)

    @model_validator(mode="after")
    def declared_links_are_complete(self) -> Self:
        _validate_links(self, self.links, complete=True)
        return self

    @property
    def manifest_sha256(self) -> str:
        return _capture_digest(self, self.links)


class DocNoteOutputRange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    start: Count
    end: Count
    text_sha256: Sha256

    @model_validator(mode="after")
    def ordered_bounds(self) -> Self:
        if self.start > self.end:
            raise ValueError("DOC note output range ends before its start")
        if self.start == self.end and self.text_sha256 != hashlib.sha256(b"").hexdigest():
            raise ValueError("An empty DOC note output range requires the empty text hash")
        return self


class DocNoteProjection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    reference_cp: Count
    reference_offset: Count | None = Field(default=None, exclude_if=lambda value: value is None)
    body: DocNoteOutputRange | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def known_position(self) -> Self:
        if self.reference_offset is None and self.body is None:
            raise ValueError("DOC note projection requires a known reference or body position")
        return self


class DocNoteSummary(_Source):
    observed_links: Count | None
    links: tuple[DocSourceNoteLink, ...] = Field(max_length=MAX_DOC_RETAINED_LINKS)
    captured_manifest_sha256: Sha256
    retained_manifest_sha256: Sha256
    projections: tuple[DocNoteProjection, ...] | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_DOC_RETAINED_LINKS,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def stored_prefix_matches_its_witness(self) -> Self:
        observed = self.observed_links
        if self.state == "unavailable":
            if observed is not None:
                raise ValueError("Unavailable DOC notes have no observed link count")
        elif observed is None or observed > MAX_DOC_NOTE_LINKS or observed < len(self.links):
            raise ValueError("Retained DOC notes exceed their observed link inventory")
        complete = observed == len(self.links) or self.state == "unavailable"
        _validate_links(self, self.links, complete=complete)
        if self.projections is not None:
            positions = {link.reference_cp: index for index, link in enumerate(self.links)}
            indices = [positions.get(item.reference_cp, -1) for item in self.projections]
            if -1 in indices or indices != sorted(set(indices)):
                raise ValueError(
                    "DOC note projections require unique retained links in source order"
                )
        retained = _capture_digest(self, self.links)
        if self.retained_manifest_sha256 != retained:
            raise ValueError("Retained DOC links differ from their stored prefix hash")
        if complete and self.captured_manifest_sha256 != retained:
            raise ValueError("Complete DOC link retention requires the captured manifest hash")
        if not complete and self.captured_manifest_sha256 == retained:
            raise ValueError("A proper DOC prefix cannot equal the full captured manifest hash")
        if len(self.model_dump_json().encode()) > MAX_DOC_NOTE_SUMMARY_BYTES:
            raise ValueError("Stored DOC links exceed their byte profile")
        return self

    @classmethod
    def from_capture(cls, capture: DocNoteCapture, *, retained_links: int) -> Self:
        capture = DocNoteCapture.model_validate(capture)
        if type(retained_links) is not int or not 0 <= retained_links <= min(
            len(capture.links), MAX_DOC_RETAINED_LINKS
        ):
            raise ValueError("DOC retained prefix length exceeds its capture or profile")
        links = capture.links[:retained_links]
        fields = {name: getattr(capture, name) for name in _Source.model_fields}
        fields.update(
            links=links,
            observed_links=len(capture.links) if capture.state == "checked" else None,
            captured_manifest_sha256=capture.manifest_sha256,
            retained_manifest_sha256=_capture_digest(capture, links),
        )
        return cls.model_validate(fields)

    @property
    def coverage(self) -> Literal["complete_declared_links", "prefix", "unknown"]:
        if self.observed_links is None:
            return "unknown"
        return "complete_declared_links" if self.observed_links == len(self.links) else "prefix"


def _capture_digest(value: _Source, links: tuple[DocSourceNoteLink, ...]) -> str:
    fields = {name: getattr(value, name) for name in _Source.model_fields}
    fields["links"] = [link.model_dump(mode="json") for link in links]
    canonical = json.dumps(fields, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _validate_links(
    source: _Source, links: tuple[DocSourceNoteLink, ...], *, complete: bool
) -> None:
    if source.state == "unavailable":
        if links:
            raise ValueError("Unavailable DOC notes cannot retain source links")
        return
    main, footnotes, endnotes = (
        source.main_characters,
        source.footnote_characters,
        source.endnote_characters,
    )
    assert main is not None and footnotes is not None and endnotes is not None, (
        "_Source validates declarations before checked DOC link validation"
    )
    order = [(link.kind == "endnote", link.note_start_cp) for link in links]
    if order != sorted(order) or len({link.reference_cp for link in links}) != len(links):
        raise ValueError("DOC link declarations require unique references in story order")
    for kind, size in (("footnote", footnotes), ("endnote", endnotes)):
        group = [link for link in links if link.kind == kind]
        previous_end = 0
        previous_ref = -1
        bases = {link.story_start_cp for link in group}
        if len(bases) > 1:
            raise ValueError("DOC notes in one story require a single source base")
        for link in group:
            if link.note_start_cp != previous_end or not previous_ref < link.reference_cp < main:
                raise ValueError("DOC note records require contiguous ranges and ordered calls")
            if link.note_end_cp >= size or link.story_start_cp < main + (
                footnotes if kind == "endnote" else 0
            ):
                raise ValueError("DOC note coordinates exceed their captured story")
            if kind == "footnote" and link.story_start_cp != main:
                raise ValueError("DOC footnote story must begin after the main document")
            previous_end, previous_ref = link.note_end_cp, link.reference_cp
        finished = complete or kind == "footnote" and any(link.kind == "endnote" for link in links)
        if finished and group and previous_end != size - 1:
            raise ValueError("Complete DOC note declarations must reach the last note boundary")


class DocPartNoteCapture(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")
    part_index: Count
    extraction: DocNoteCapture


class DocNotedParts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")
    legacy: ExtractedParts
    doc_notes: tuple[DocPartNoteCapture, ...] = Field(max_length=MAX_DOC_NOTE_PARTS)

    @model_validator(mode="after")
    def source_records_bind_to_actual_leaves(self) -> Self:
        indices = [value.part_index for value in self.doc_notes]
        if indices != sorted(set(indices)):
            raise ValueError("DOC notes require unique physical input order")
        unlocated = {value.part_index: value.extraction for value in self.legacy.unlocated_text}
        for value in self.doc_notes:
            index, capture = value.part_index, value.extraction
            if index >= len(self.legacy.parts) or index not in unlocated:
                raise ValueError("DOC notes require an actual unlocated physical leaf")
            part, body = self.legacy.parts[index], unlocated[index]
            if body.route != "doc" or (capture.input_sha256, capture.text_sha256) != (
                part.sha256,
                part.text_sha256,
            ):
                raise ValueError("DOC notes differ from their physical input and legacy text")
        return self
