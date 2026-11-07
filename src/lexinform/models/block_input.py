"""Bounded exact copied runs bind prepared text to retained native block witnesses."""

import hashlib
import re
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.models.physical_inventory import Position, Sha256

MAX_BLOCK_INPUT_BYTES = 4096
MAX_BLOCK_INPUT_ROWS = 32


_PATH = r"(?:0|[1-9][0-9]*)(?:/(?:0|[1-9][0-9]*)){0,63}"
_BLOCK_IDS = {
    "word-xml-plain-run-copy-v1": rf"word-xml:word/document\.xml:{_PATH}",
    "word-blocks-v1": rf"word-xml:word/document\.xml:{_PATH}",
    "pdf-pages-v1": r"pdf-page:[1-9][0-9]*:0",
    "odt-blocks-v1": rf"odt-xml:content\.xml:body:{_PATH}",
}
_NATIVE_VIEWS = {
    "word-blocks-v1": "legacy-word-body-v1",
    "pdf-pages-v1": "pdf-whitespace-v1",
    "odt-blocks-v1": "legacy-odt-body-v1",
}


class BlockCopyRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    source_start: Position
    source_end: Position
    block_id: str = Field(max_length=256)
    block_start: Position
    block_end: Position
    block_text_sha256: Sha256
    block_chars: Position
    text_sha256: Sha256

    @model_validator(mode="after")
    def exact_copy(self) -> Self:
        if (
            not 0 <= self.block_start < self.block_end <= self.block_chars
            or self.source_end - self.source_start != self.block_end - self.block_start
        ):
            raise ValueError("block copy requires equal nonempty source and block ranges")
        if (
            self.block_start == 0
            and self.block_end == self.block_chars
            and (self.text_sha256 != self.block_text_sha256)
        ):
            raise ValueError("whole block copy must retain the exact block text hash")
        if not any(re.fullmatch(pattern, self.block_id) for pattern in _BLOCK_IDS.values()):
            raise ValueError("block copy requires a canonical known native block ID")
        return self


class BlockCopyWitness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    profile: Literal["word-plain-xml-copies-v1", "native-block-copies-v1"] = (
        "word-plain-xml-copies-v1"
    )
    coordinates: Literal[
        "word-xml-plain-run-copy-v1", "word-blocks-v1", "pdf-pages-v1", "odt-blocks-v1"
    ] = "word-xml-plain-run-copy-v1"
    manifest_sha256: Sha256
    body_manifest_sha256: Sha256 | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    notes_manifest_sha256: Sha256 | None
    xml_sha256: Sha256 | None = Field(default=None, exclude_if=lambda value: value is None)
    part_sha256: Sha256
    text_sha256: Sha256
    text_chars: Position
    runs: tuple[BlockCopyRun, ...] = Field(min_length=1, max_length=MAX_BLOCK_INPUT_ROWS)
    text_view: Literal["legacy-word-body-v1", "pdf-whitespace-v1", "legacy-odt-body-v1"] | None = (
        Field(default=None, exclude_if=lambda value: value is None)
    )

    @model_validator(mode="after")
    def bounded_runs(self) -> Self:
        if self.profile == "word-plain-xml-copies-v1":
            if self.coordinates != "word-xml-plain-run-copy-v1" or self.text_view is not None:
                raise ValueError("plain Word copies require the literal XML coordinate profile")
        elif _NATIVE_VIEWS.get(self.coordinates) != self.text_view or self.text_view is None:
            raise ValueError("native block copies require the exact extracted part text view")
        if self.coordinates in ("word-xml-plain-run-copy-v1", "word-blocks-v1"):
            if self.body_manifest_sha256 is None or self.xml_sha256 is None:
                raise ValueError("Word block copies require their original body and XML identities")
        elif (
            self.body_manifest_sha256 is not None
            or self.notes_manifest_sha256 is not None
            or self.coordinates == "pdf-pages-v1"
            and self.xml_sha256 is not None
        ):
            raise ValueError("native block copies cannot claim unrelated Word or XML parents")
        end = 0
        blocks: dict[str, tuple[int, str]] = {}
        block_ends: dict[str, int] = {}
        for row in self.runs:
            if re.fullmatch(_BLOCK_IDS[self.coordinates], row.block_id) is None:
                raise ValueError("block identity differs from the witness coordinate system")
            identity = (row.block_chars, row.block_text_sha256)
            if blocks.setdefault(row.block_id, identity) != identity:
                raise ValueError("repeated block IDs require the same text hash and length")
            if row.block_start < block_ends.get(row.block_id, 0):
                raise ValueError("native copies within a block must retain their original order")
            block_ends[row.block_id] = row.block_end
            if not end <= row.source_start < row.source_end <= self.text_chars:
                raise ValueError("block copies require ordered disjoint in-part source ranges")
            if (
                row.source_start == 0
                and row.source_end == self.text_chars
                and (row.text_sha256 != self.text_sha256)
            ):
                raise ValueError("whole part copy must retain the exact extracted text hash")
            end = row.source_end
        if len(self.model_dump_json().encode()) > MAX_BLOCK_INPUT_BYTES:
            raise ValueError("block copy witness exceeds its byte limit")
        return self

    @property
    def witness_id(self) -> str:
        return "block-copy-v1:" + hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class PreparedBlockCopy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    start: Position
    end: Position
    part_index: Position
    extraction_id: str = Field(pattern=r"^extraction-v1:[0-9a-f]{64}$")
    physical_index: Position
    witness_id: str = Field(pattern=r"^block-copy-v1:[0-9a-f]{64}$")
    run_index: Position
    text_sha256: Sha256

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if self.start >= self.end:
            raise ValueError("prepared block copies must be nonempty")
        return self


class PreparedBlockCompanion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    profile: Literal["prepared-block-copies-v1"] = "prepared-block-copies-v1"
    retention: Literal["complete", "prefix"]
    observed_rows: Annotated[int, Field(strict=True, ge=1, lt=2**63)]
    rows_sha256: Sha256
    rows: tuple[PreparedBlockCopy, ...] = Field(min_length=1, max_length=MAX_BLOCK_INPUT_ROWS)

    @model_validator(mode="after")
    def bounded_prefix(self) -> Self:
        end = 0
        for row in self.rows:
            if row.start < end:
                raise ValueError("prepared block copies must be ordered and disjoint")
            end = row.end
        if self.retention == "complete":
            if (
                self.observed_rows != len(self.rows)
                or self.rows_sha256 != self.canonical_rows_sha256
            ):
                raise ValueError("complete block companion requires its full canonical rows")
        elif self.observed_rows <= len(self.rows):
            raise ValueError("block companion prefix must declare omitted observed rows")
        if len(self.model_dump_json().encode()) > MAX_BLOCK_INPUT_BYTES:
            raise ValueError("block companion exceeds its byte limit")
        return self

    @property
    def canonical_rows_sha256(self) -> str:
        encoded = "[" + ",".join(row.model_dump_json() for row in self.rows) + "]"
        return hashlib.sha256(encoded.encode()).hexdigest()
