"""Observed downloader bodies exclude wire overhead, incomplete responses and raw retention."""

from dataclasses import dataclass
from typing import Annotated, Literal, Self

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


DownloadFailureOutcome = Literal[
    "missing",
    "over_limit",
    "source_unavailable",
    "refused",
    "error",
    "pre_send_rejected",
    "unknown_host",
    "invalid_url",
]
DownloadDiagnosticReason = Literal[
    "http_status",
    "transport_error",
    "rejected_response",
    "failure_budget",
    "byte_limit",
    "unconfigured_host",
    "invalid_url",
    "other_error",
]
DownloadDiagnosticCount = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
MAX_DOWNLOAD_DIAGNOSTIC_BYTES = 2048
MAX_DOWNLOAD_FAILURE_GROUPS = 8


class DocumentDownloadFailureGroup(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    requested_url_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    host: str | None = Field(
        default=None,
        max_length=253,
        pattern=r"^[A-Za-z0-9.:-]+$",
        exclude_if=lambda value: value is None,
    )
    outcome: DownloadFailureOutcome
    reason: DownloadDiagnosticReason
    status_code: int | None = Field(
        default=None, strict=True, ge=100, le=599, exclude_if=lambda value: value is None
    )
    limit_bytes: DownloadDiagnosticCount | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    count: DownloadDiagnosticCount = Field(ge=1)


class DocumentDownloadDiagnostics(BaseModel):
    """Loader invocations include routing failures and never assert HTTP wire-attempt counts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    unit: Literal["text_loader_download_invocation"] = "text_loader_download_invocation"
    invocations: DownloadDiagnosticCount = 0
    completed: DownloadDiagnosticCount = 0
    missing: DownloadDiagnosticCount = 0
    over_limit: DownloadDiagnosticCount = 0
    source_unavailable: DownloadDiagnosticCount = 0
    refused: DownloadDiagnosticCount = 0
    error: DownloadDiagnosticCount = 0
    pre_send_rejected: DownloadDiagnosticCount = 0
    unknown_host: DownloadDiagnosticCount = 0
    invalid_url: DownloadDiagnosticCount = 0
    unresolved: DownloadDiagnosticCount = 0
    cache_hits: DownloadDiagnosticCount = 0
    examples: tuple[DocumentDownloadFailureGroup, ...] = Field(
        default=(), max_length=MAX_DOWNLOAD_FAILURE_GROUPS
    )
    omitted_failures: DownloadDiagnosticCount = 0

    @model_validator(mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if isinstance(value, dict) and type(value.get("schema_version", 1)) is not int:
            raise ValueError("download diagnostics require an integer version")
        return value

    @model_validator(mode="after")
    def bounded_accounting(self) -> Self:
        failures = (
            self.missing
            + self.over_limit
            + self.source_unavailable
            + self.refused
            + self.error
            + self.pre_send_rejected
            + self.unknown_host
            + self.invalid_url
        )
        if self.completed + failures + self.unresolved != self.invocations:
            raise ValueError("download diagnostics do not account for loader invocations")
        if sum(group.count for group in self.examples) + self.omitted_failures != failures:
            raise ValueError("download failure examples do not account for omitted groups")
        identities = {group.model_dump_json(exclude={"count"}) for group in self.examples}
        if len(identities) != len(self.examples):
            raise ValueError("download diagnostic groups must be unique")
        for outcome in (
            "missing",
            "over_limit",
            "source_unavailable",
            "refused",
            "error",
            "pre_send_rejected",
            "unknown_host",
            "invalid_url",
        ):
            if sum(group.count for group in self.examples if group.outcome == outcome) > getattr(
                self, outcome
            ):
                raise ValueError("download failure examples exceed their outcome counter")
        if len(self.model_dump_json().encode()) > MAX_DOWNLOAD_DIAGNOSTIC_BYTES:
            raise ValueError("download diagnostics exceed the 2 KiB aggregate cap")
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
