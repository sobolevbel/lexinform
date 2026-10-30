"""Bounded prospective text-input maps identify frozen requests without retaining text."""

import hashlib
import json
from collections.abc import Iterable, Iterator
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.models.analysis import BillContext
from lexinform.models.prepared_input import PreparedTextPart, PreparedTextSegment

MAX_INPUT_RECEIPT_BYTES = 8192
MAX_INPUT_RECEIPT_SEGMENTS = 32
MAX_INPUT_RECEIPT_INTEGER = 2**63 - 1


def input_text_sha256(text: str) -> str:
    digest = hashlib.sha256()
    for start in range(0, len(text), 4096):
        digest.update(text[start : start + 4096].encode())
    return digest.hexdigest()


def input_json_chunks(value: object, *, ensure_ascii: bool = True) -> Iterator[str]:
    if isinstance(value, str):
        yield '"'
        for start in range(0, len(value), 1024):
            yield json.dumps(value[start : start + 1024], ensure_ascii=ensure_ascii)[1:-1]
        yield '"'
    elif isinstance(value, BaseModel):
        fields = type(value).model_fields
        yield "{"
        separator = ""
        for key in sorted(fields):
            info = fields[key]
            item = getattr(value, key)
            if info.exclude or info.exclude_if is not None and info.exclude_if(item):
                continue
            yield separator
            yield from input_json_chunks(key, ensure_ascii=ensure_ascii)
            yield ":"
            yield from input_json_chunks(item, ensure_ascii=ensure_ascii)
            separator = ","
        yield "}"
    elif isinstance(value, dict):
        yield "{"
        separator = ""
        for key in sorted(value):
            if not isinstance(key, str):
                raise ValueError("Context JSON requires string keys")
            yield separator
            yield from input_json_chunks(key, ensure_ascii=ensure_ascii)
            yield ":"
            yield from input_json_chunks(value[key], ensure_ascii=ensure_ascii)
            separator = ","
        yield "}"
    elif isinstance(value, (list, tuple)):
        yield "["
        separator = ""
        for item in value:
            yield separator
            yield from input_json_chunks(item, ensure_ascii=ensure_ascii)
            separator = ","
        yield "]"
    elif value is None or isinstance(value, (bool, int)):
        yield json.dumps(value)
    else:
        raise ValueError("Unsupported frozen context JSON value")


def primary_context_sha256(ctx: BillContext) -> str:
    if ctx.scan is not None:
        raise ValueError("Text receipts cannot describe scan contexts")
    context = ctx.model_dump(mode="json", exclude={"text", "previous_key_changes"})
    context.update(text=ctx.text, previous_key_changes=ctx.previous_key_changes)
    digest = hashlib.sha256()
    for chunk in input_json_chunks(context):
        digest.update(chunk.encode("ascii"))
    return digest.hexdigest()


def input_map_sha256(segments: Iterable[PreparedTextSegment]) -> str:
    digest = hashlib.sha256(b"[")
    separator = b""
    for segment in segments:
        digest.update(separator)
        digest.update(
            json.dumps(
                segment.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode()
        )
        separator = b","
    digest.update(b"]")
    return digest.hexdigest()


class PrimaryInputReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    schema_version: Literal[1] = 1
    profile: Literal["primary-single-part-text-v1"] = "primary-single-part-text-v1"
    coordinates: Literal["legacy_part_text_v1"] = "legacy_part_text_v1"
    context_encoding: Literal["bill-context-json-v1"] = "bill-context-json-v1"
    map_encoding: Literal["prepared-text-segments-json-v1"] = "prepared-text-segments-json-v1"
    physical_origins: Literal["unmapped"] = "unmapped"
    block_origins: Literal["unmapped"] = "unmapped"
    exclusions: Literal["unknown"] = "unknown"
    input_completeness: Literal["not_asserted"] = "not_asserted"
    context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$", max_length=64)
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$", max_length=64)
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$", max_length=64)
    text_chars: int = Field(ge=0, le=MAX_INPUT_RECEIPT_INTEGER, strict=True)
    source_part: PreparedTextPart
    source_selection_id: str | None = Field(
        default=None,
        pattern=r"^selection-v1:[0-9a-f]{64}$",
        max_length=80,
        exclude_if=lambda value: value is None,
    )
    artifact_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        max_length=64,
        exclude_if=lambda value: value is None,
    )
    extraction_id: str | None = Field(
        default=None,
        pattern=r"^extraction-v1:[0-9a-f]{64}$",
        max_length=80,
        exclude_if=lambda value: value is None,
    )
    retention: Literal["complete", "prefix", "unknown"]
    reason: Literal["segment_limit", "byte_limit", "projection_unavailable"] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    segment_count: int | None = Field(
        default=None,
        ge=0,
        le=MAX_INPUT_RECEIPT_INTEGER,
        strict=True,
        exclude_if=lambda value: value is None,
    )
    full_map_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        max_length=64,
        exclude_if=lambda value: value is None,
    )
    segments: tuple[PreparedTextSegment, ...] = Field(
        default=(),
        max_length=MAX_INPUT_RECEIPT_SEGMENTS,
    )

    @model_validator(mode="before")
    @classmethod
    def bounded_prefix(cls, value: object) -> object:
        if isinstance(value, dict):
            segments = value.get("segments", ())
            if isinstance(segments, (list, tuple)) and len(segments) > MAX_INPUT_RECEIPT_SEGMENTS:
                raise ValueError("Input receipt segment prefix exceeds its limit")
        return value

    @model_validator(mode="after")
    def coherent_map(self) -> Self:
        if self.source_part.chars > MAX_INPUT_RECEIPT_INTEGER:
            raise ValueError("Input source character count exceeds the profile")
        if self.extraction_id is not None and self.artifact_sha256 is None:
            raise ValueError("An extraction reference requires its known input body")
        if self.retention == "unknown":
            if (
                self.reason != "projection_unavailable"
                or self.segments
                or self.segment_count is not None
                or self.full_map_sha256 is not None
            ):
                raise ValueError("Unavailable maps cannot claim observed segments or totals")
        else:
            if self.segment_count is None or self.full_map_sha256 is None:
                raise ValueError("Observed maps require actual full counts and hash")
            offset = 0
            for segment in self.segments:
                if segment.start != offset or segment.end > self.text_chars:
                    raise ValueError("Retained segments must be an ordered emitted prefix")
                offset = segment.end
                if segment.origin == "unknown":
                    raise ValueError("An observed projection cannot invent an unknown segment")
                if segment.origin == "source" and (
                    segment.part_index != 0
                    or segment.source_end is None
                    or segment.source_end > self.source_part.chars
                ):
                    raise ValueError("A source range must reference this single part")
            if self.retention == "complete":
                if (
                    self.reason is not None
                    or self.segment_count != len(self.segments)
                    or offset != self.text_chars
                    or self.full_map_sha256 != input_map_sha256(self.segments)
                ):
                    raise ValueError("Complete retention requires the exact whole observed map")
            elif self.reason not in ("segment_limit", "byte_limit") or self.segment_count <= len(
                self.segments
            ):
                raise ValueError("A retained prefix requires honest omitted segments")
        if len(self.model_dump_json().encode()) > MAX_INPUT_RECEIPT_BYTES:
            raise ValueError("Input receipt exceeds its serialized byte limit")
        return self
