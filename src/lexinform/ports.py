"""Interfaces (typing.Protocol) that services depend on. Adapters implement them."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Protocol

from lexinform.models import (
    AnalysisRecord,
    Bill,
    BillContext,
    BillStatus,
    PrintInfo,
    ProcessDetail,
    ProcessSummary,
    Publication,
    PublicationStatus,
    RunReport,
    Stage,
    StatusChange,
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class SejmGateway(Protocol):
    def iter_processes(
        self,
        term: int,
        *,
        modified_since: datetime | None = None,
        document_type: str | None = None,
    ) -> Iterator[ProcessSummary]: ...

    def get_process(self, term: int, number: str) -> ProcessDetail: ...

    def get_print(self, term: int, number: str) -> PrintInfo: ...

    def attachment_size(self, url: str) -> int | None: ...

    def download(self, url: str) -> bytes: ...


class TextExtractor(Protocol):
    def extract(self, data: bytes) -> str: ...


class LlmAnalyzer(Protocol):
    def analyze(self, ctx: BillContext) -> AnalysisRecord: ...


class PublishResult(Protocol):
    @property
    def message_id(self) -> int: ...

    @property
    def document_message_ids(self) -> list[int]: ...


class Publisher(Protocol):
    def publish_new_bill(self, bill: Bill, print_info: PrintInfo | None) -> PublishResult: ...

    def publish_status_update(
        self, bill: Bill, change: StatusChange, reply_to: int | None
    ) -> PublishResult: ...


class RunNotifier(Protocol):
    def notify(self, report: RunReport, log_lines: list[str]) -> None: ...


class BillRepository(Protocol):
    # schema
    def migrate(self) -> None: ...

    # bills
    def get(self, term: int, number: str) -> Bill | None: ...

    def upsert_summary(self, summary: ProcessSummary, *, now: datetime) -> Bill: ...

    def set_status(
        self, term: int, number: str, status: BillStatus, *, prefilter_hits: list[str] | None = None
    ) -> None: ...

    def save_stages(
        self, term: int, number: str, stages: tuple[Stage, ...], fingerprint: str
    ) -> None: ...

    def save_analysis(self, term: int, number: str, record: AnalysisRecord) -> None: ...

    def record_analysis_failure(self, term: int, number: str, error: str) -> None: ...

    def list_by_status(
        self, term: int, statuses: list[BillStatus], *, limit: int
    ) -> list[Bill]: ...

    def list_publish_candidates(
        self, term: int, channel_id: str, *, min_score: int, limit: int
    ) -> list[Bill]: ...

    def list_tracked(
        self, term: int, channel_id: str, *, closed_grace_days: int, now: datetime
    ) -> list[Bill]: ...

    # publications
    def create_publication(self, publication: Publication) -> int: ...

    def mark_publication(
        self,
        publication_id: int,
        status: PublicationStatus,
        *,
        message_id: int | None = None,
        document_message_ids: list[int] | None = None,
        error: str | None = None,
        sent_at: datetime | None = None,
    ) -> None: ...

    def get_publication(
        self, term: int, number: str, kind: str, channel_id: str
    ) -> Publication | None: ...

    def mark_stale_pending_as_unknown(self, *, now: datetime) -> int: ...

    # status changes
    def add_status_change(self, change: StatusChange) -> int | None: ...

    # runs
    def last_successful_run_started_at(self) -> datetime | None: ...

    def start_run(self, report: RunReport) -> int: ...

    def finish_run(self, run_id: int, report: RunReport) -> None: ...

    # transactions (used by --dry-run)
    def begin(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
