"""Text-only input identities describe legacy string coordinates, not physical citations."""

import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.models.documents import DocumentRole


class PreparedTextPart(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    chars: int = Field(ge=0)
    role: DocumentRole


class PreparedTextSegment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(ge=0)
    origin: Literal["source", "generated", "unknown"]
    part_index: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    source_start: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    source_end: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    system_origin: Literal["input_transform", "part_separator"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def origin_fields_agree(self) -> Self:
        if self.end <= self.start:
            raise ValueError("Prepared text segments must be nonempty")
        source_fields = (self.part_index, self.source_start, self.source_end)
        if self.origin == "source":
            if any(value is None for value in source_fields) or self.system_origin is not None:
                raise ValueError("Source text requires its part and source range")
            assert self.source_start is not None and self.source_end is not None, (
                "the source-field check requires both coordinates"
            )
            if self.source_end - self.source_start != self.end - self.start:
                raise ValueError("Copied source and emitted text must have equal lengths")
        elif any(value is not None for value in source_fields):
            raise ValueError("Generated or unknown text cannot claim a source range")
        if (self.origin == "generated") != (self.system_origin is not None):
            raise ValueError("Only generated text names a system origin")
        return self


class PreparedTextInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    scope: Literal["document_text"] = "document_text"
    coordinates: Literal["legacy_part_text_v1"] = "legacy_part_text_v1"
    physical_origins: Literal["unmapped"] = "unmapped"
    source_selection_id: str = Field(pattern=r"^selection-v1:[a-f0-9]{64}$")
    source_text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_chars: int = Field(ge=0)
    separator: str = Field(default="\f", max_length=16)
    text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    text_chars: int = Field(ge=0)
    parts: tuple[PreparedTextPart, ...] = ()
    segments: tuple[PreparedTextSegment, ...] = ()

    @model_validator(mode="after")
    def ranges_partition_the_input(self) -> Self:
        expected = sum(part.chars for part in self.parts)
        expected += max(0, len(self.parts) - 1) * len(self.separator)
        if self.source_chars != expected:
            raise ValueError("Prepared source length differs from its ordered part assembly")
        offset = 0
        for segment in self.segments:
            if segment.start != offset:
                raise ValueError("Prepared segments must partition the emitted text")
            offset = segment.end
            if segment.origin == "source":
                index, end = segment.part_index, segment.source_end
                assert index is not None and end is not None, (
                    "PreparedTextSegment validates source coordinates"
                )
                if index >= len(self.parts) or end > self.parts[index].chars:
                    raise ValueError("Prepared source range escapes its referenced part")
        if offset != self.text_chars:
            raise ValueError("Prepared segments do not cover the emitted text")
        return self

    @property
    def mapping(self) -> Literal["exact", "unknown"]:
        return "unknown" if any(part.origin == "unknown" for part in self.segments) else "exact"

    @property
    def input_id(self) -> str:
        encoded = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return "prepared-text-v1:" + hashlib.sha256(encoded.encode()).hexdigest()
