"""Observed downloader bodies exclude wire overhead, incomplete responses and raw retention."""

from dataclasses import dataclass
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.models.candidate_trace import CandidateDispositionTrace
from lexinform.models.documents import DocumentSelection


class HostDocumentReads(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    attempts: int = Field(default=0, ge=0)
    completed: int = Field(default=0, ge=0)
    body_bytes: int = Field(default=0, ge=0)
    over_limit: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def accounted_attempts(self) -> Self:
        if self.completed + self.over_limit + self.failed > self.attempts:
            raise ValueError("document outcomes exceed observed download attempts")
        return self


class DocumentReadMetrics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    cache_hits: int = Field(default=0, ge=0)
    distinct_bodies: int = Field(default=0, ge=0)
    distinct_body_bytes: int = Field(default=0, ge=0)
    hosts: dict[str, HostDocumentReads] = Field(default_factory=dict)

    @property
    def completed(self) -> int:
        return sum(host.completed for host in self.hosts.values())

    @property
    def body_bytes(self) -> int:
        return sum(host.body_bytes for host in self.hosts.values())

    @property
    def duplicate_body_bytes(self) -> int:
        return self.body_bytes - self.distinct_body_bytes

    @model_validator(mode="after")
    def known_completed_bytes(self) -> Self:
        if self.distinct_bodies > self.completed or self.distinct_body_bytes > self.body_bytes:
            raise ValueError("distinct bodies exceed the observed completed responses")
        return self


@dataclass(frozen=True)
class LoadedFile:
    """A downloaded file's existing text, scan and physical-selection snapshot."""

    text: str | None
    pages: int = 0
    oversize: bool = False
    sha256: str | None = None
    selection: DocumentSelection = DocumentSelection()
    candidate_trace: CandidateDispositionTrace | None = None

    @property
    def is_scan(self) -> bool:
        return self.text is None and self.pages > 0
