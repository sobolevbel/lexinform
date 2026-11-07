"""Bounded Word paragraph witnesses for retained field output coordinates."""

from typing import Annotated, Literal, NamedTuple, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_WORD_FIELD_BYTES = 4096
MAX_WORD_FIELD_RANGES = 32
Count = Annotated[int, Field(strict=True, ge=0, lt=2**31)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Path = Annotated[tuple[Count, ...], Field(min_length=1, max_length=64)]
type FieldGroup = Literal["simple_body_text", "before_separator", "after_separator"]


class WordFieldSlice(NamedTuple):
    path: Path
    group: FieldGroup
    start: Count
    end: Count
    text_sha256: Sha256


class WordFieldParagraph(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    path: Path
    text_sha256: Sha256
    text_chars: Count
    ranges: tuple[WordFieldSlice, ...] = Field(min_length=1, max_length=MAX_WORD_FIELD_RANGES)

    @model_validator(mode="after")
    def ranges_bind_to_paragraph(self) -> Self:
        paths = [item.path for item in self.ranges]
        if paths != sorted(set(paths)):
            raise ValueError(
                "Word field nodes require distinct paragraph-relative paths in XML order"
            )
        previous_end = 0
        for item in self.ranges:
            if not previous_end <= item.start < item.end <= self.text_chars:
                raise ValueError("Word field ranges must be ordered and within their paragraph")
            previous_end = item.end
        return self


class WordFieldPrefix(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    version: Literal["word-field-paragraph-prefix-v1"] = "word-field-paragraph-prefix-v1"
    input_sha256: Sha256
    manifest_sha256: Sha256
    paragraphs: tuple[WordFieldParagraph, ...] = Field(
        min_length=1, max_length=MAX_WORD_FIELD_RANGES
    )

    @model_validator(mode="after")
    def bounded_prefix(self) -> Self:
        paths = [paragraph.path for paragraph in self.paragraphs]
        if paths != sorted(set(paths)):
            raise ValueError("Word field paragraphs require distinct paths in XML order")
        if sum(len(paragraph.ranges) for paragraph in self.paragraphs) > MAX_WORD_FIELD_RANGES:
            raise ValueError("Word field prefix exceeds its range limit")
        if len(self.model_dump_json().encode()) > MAX_WORD_FIELD_BYTES:
            raise ValueError("Word field prefix exceeds its byte limit")
        return self
