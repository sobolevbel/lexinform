"""Versioned XML note origins and their calls in the legacy Word body view."""

import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

NotePartName = Literal["word/footnotes.xml", "word/endnotes.xml"]


class WordNoteXmlPart(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: NotePartName
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: Literal["read", "unsupported", "refused"]


class WordNote(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    xml_part: NotePartName
    path: tuple[int, ...] = Field(min_length=1)
    identity: str
    text: str

    @property
    def note_id(self) -> str:
        return f"word-note:{self.xml_part}:{'/'.join(str(index) for index in self.path)}"

    @property
    def text_sha256(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()

    @model_validator(mode="after")
    def path_is_nonnegative(self) -> Self:
        if any(index < 0 for index in self.path):
            raise ValueError("Word note path must be nonnegative")
        return self


class WordNoteCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    paragraph_path: tuple[int, ...] = Field(min_length=1)
    path: tuple[int, ...] = Field(min_length=1)
    kind: Literal["footnote", "endnote"]
    identity: str
    state: Literal["expanded", "empty", "missing", "unverified"]
    target: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def path_follows_its_paragraph(self) -> Self:
        if (
            any(index < 0 for index in (*self.path, *self.paragraph_path))
            or len(self.path) <= len(self.paragraph_path)
            or self.path[: len(self.paragraph_path)] != self.paragraph_path
        ):
            raise ValueError("Word note call must be inside its paragraph")
        return self


class WordNoteExtraction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["word-notes-v1"] = "word-notes-v1"
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    body_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    parts: tuple[WordNoteXmlPart, ...] = ()
    notes: tuple[WordNote, ...] = ()
    calls: tuple[WordNoteCall, ...] = ()
    losses: tuple[str, ...] = ()

    @property
    def manifest_sha256(self) -> str:
        data = json.dumps(
            self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(data.encode()).hexdigest()

    @model_validator(mode="after")
    def note_origins_and_calls_agree(self) -> Self:
        parts = {part.name: part for part in self.parts}
        if len(parts) != len(self.parts):
            raise ValueError("Word note XML parts must be unique")
        positions = [(note.xml_part, note.path) for note in self.notes]
        if len(set(positions)) != len(positions):
            raise ValueError("Word note XML positions must be unique")
        for note in self.notes:
            if note.xml_part not in parts or parts[note.xml_part].state != "read":
                raise ValueError("Word notes require a supported XML part")
        order = {part.name: index for index, part in enumerate(self.parts)}
        ordered = [(order[note.xml_part], note.path) for note in self.notes]
        if ordered != sorted(ordered):
            raise ValueError("Word notes must retain their XML part order")
        targets = {(note.xml_part, note.identity): index for index, note in enumerate(self.notes)}
        paths = [call.path for call in self.calls]
        if paths != sorted(paths) or len(set(paths)) != len(paths):
            raise ValueError("Word note calls require unique XML positions in order")
        for call in self.calls:
            if call.target is None:
                if call.state not in ("missing", "unverified"):
                    raise ValueError("An expanded Word note call requires its source")
                continue
            if call.target >= len(self.notes):
                raise ValueError("Word note call refers to an absent note")
            note = self.notes[call.target]
            expected = "word/footnotes.xml" if call.kind == "footnote" else "word/endnotes.xml"
            if (
                note.xml_part != expected
                or note.identity != call.identity
                or targets[(note.xml_part, note.identity)] != call.target
            ):
                raise ValueError("Word note call refers to a different source")
            if call.state != ("expanded" if note.text else "empty"):
                raise ValueError("Word note state differs from its legacy text")
        return self
