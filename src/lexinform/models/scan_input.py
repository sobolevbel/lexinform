"""Exact PDF input identity and its contiguous physical page window without storing bytes."""

import hashlib
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

ScanTransform = Literal["cover_letter", "triage"]


class ScanInputManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_bytes: int = Field(ge=1)
    source_pages: int = Field(ge=1)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_bytes: int = Field(ge=1)
    first_page: int = Field(ge=1)
    page_count: int = Field(ge=1)
    transformations: tuple[ScanTransform, ...] = ()

    @model_validator(mode="after")
    def physical_window(self) -> Self:
        if self.first_page + self.page_count - 1 > self.source_pages:
            raise ValueError("Scan input window exceeds the original PDF")
        return self

    def opening(self, data: bytes, count: int) -> ScanInputManifest:
        if not 0 < count <= self.page_count:
            raise ValueError("Triage must stay within the current PDF input")
        return ScanInputManifest(
            source_sha256=self.source_sha256,
            source_bytes=self.source_bytes,
            source_pages=self.source_pages,
            input_sha256=hashlib.sha256(data).hexdigest(),
            input_bytes=len(data),
            first_page=self.first_page,
            page_count=count,
            transformations=(*self.transformations, "triage"),
        )
