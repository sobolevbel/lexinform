"""Interfaces (typing.Protocol) that services depend on. Adapters implement them."""

from collections.abc import Iterator
from datetime import date, datetime
from typing import Protocol

from lexinform.models import (
    ActInfo,
    AgendaItem,
    AnalysisRecord,
    Bill,
    BillAuthors,
    BillContext,
    BillStatus,
    BillSubmission,
    Committee,
    CommitteeSitting,
    Mp,
    PrintInfo,
    ProcessDetail,
    ProcessSummary,
    Publication,
    PublicationStatus,
    RunReport,
    SejmSitting,
    Stage,
    StatusChange,
    TriageContext,
    TriageRecord,
    Vote,
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class SejmGateway(Protocol):
    """The Sejm REST API (api.sejm.gov.pl); `ServiceUnavailableError` when it is down."""

    def iter_processes(
        self,
        term: int,
        *,
        modified_since: datetime | None = None,
        document_type: str | None = None,
    ) -> Iterator[ProcessSummary]: ...

    def iter_bills(
        self, term: int, *, received_from: date | None = None
    ) -> Iterator[BillSubmission]: ...

    def find_submission(self, term: int, print_number: str) -> BillSubmission | None: ...

    def get_process(self, term: int, number: str) -> ProcessDetail: ...

    def get_print(self, term: int, number: str) -> PrintInfo: ...

    def get_voting(self, term: int, sitting: int, number: int) -> tuple[Vote, ...]: ...

    def get_committee(self, term: int, code: str) -> Committee: ...

    def list_committee_sittings(self, term: int, code: str) -> tuple[CommitteeSitting, ...]: ...

    def list_sittings(self, term: int) -> tuple[SejmSitting, ...]:
        """GET /proceedings: past, current and planned Sejm sittings, without agendas."""
        ...

    def get_sitting(self, term: int, number: int) -> SejmSitting:
        """GET /proceedings/{number}: one sitting with its agenda (HTML)."""
        ...

    def list_mps(self, term: int) -> tuple[Mp, ...]: ...

    def download(self, url: str, *, max_bytes: int | None = None) -> bytes:
        """The attachment body; raises `AttachmentTooLargeError` once `max_bytes` is exceeded."""
        ...


class EliGateway(Protocol):
    """The ELI (European Legislation Identifier) API of the Sejm: published acts."""

    def get_act(self, eli: str) -> ActInfo | None: ...


class TextExtractor(Protocol):
    def extract(self, data: bytes) -> str: ...


class LlmAnalyzer(Protocol):
    def analyze(self, ctx: BillContext) -> AnalysisRecord: ...

    def triage(self, ctx: TriageContext) -> TriageRecord: ...


class PublishResult(Protocol):
    @property
    def message_id(self) -> int: ...

    @property
    def document_message_ids(self) -> list[int]: ...


class Publisher(Protocol):
    """Where the messages go: Telegram in production, stdout in a dry run."""

    def publish_new_bill(self, bill: Bill, print_info: PrintInfo | None) -> PublishResult: ...

    def publish_status_update(
        self, bill: Bill, change: StatusChange, reply_to: int | None
    ) -> PublishResult: ...

    def publish_act_published(self, bill: Bill, reply_to: int | None) -> PublishResult: ...

    def publish_in_force(self, bill: Bill, reply_to: int | None) -> PublishResult: ...

    def publish_consultation_deadline(
        self, bill: Bill, reply_to: int | None, *, today: date
    ) -> PublishResult: ...

    def publish_consultation_results(self, bill: Bill, reply_to: int | None) -> PublishResult: ...

    def publish_agenda(
        self, bill: Bill, item: AgendaItem, reply_to: int | None
    ) -> PublishResult: ...


class RunNotifier(Protocol):
    def notify(self, report: RunReport, log_lines: list[str]) -> None: ...


class BillRepository(Protocol):
    """Persistence of bills, publications, status changes and runs (SQLite in production)."""

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

    def reset_bill(self, term: int, number: str, status: BillStatus) -> None: ...

    def list_by_status(
        self,
        term: int,
        statuses: list[BillStatus],
        *,
        limit: int,
        max_attempts: int | None = None,
    ) -> list[Bill]: ...

    def list_publish_candidates(
        self, term: int, channel_id: str, *, min_score: int, limit: int, max_attempts: int = 3
    ) -> list[Bill]: ...

    def list_tracked(
        self,
        term: int,
        channel_id: str,
        *,
        closed_grace_days: int,
        passed_max_days: int,
        now: datetime,
        changed_since: datetime | None = None,
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
        self, term: int, number: str, kind: str, channel_id: str, *, ref: str | None = None
    ) -> Publication | None:
        """The latest post of this kind for the bill; `ref` narrows to one sitting (agenda)."""
        ...

    def delete_publication(self, term: int, number: str, kind: str, channel_id: str) -> int: ...

    def mark_stale_pending_as_unknown(self, *, now: datetime) -> int: ...

    # status changes
    def add_status_change(self, change: StatusChange) -> int | None: ...

    def closure_announced(self, term: int, number: str) -> bool: ...

    # pre-print bills
    def save_submission(self, term: int, number: str, submission: BillSubmission) -> None: ...

    def list_pre_print(self, term: int) -> list[Bill]: ...

    def link_bills(self, term: int, pre_print_number: str, print_number: str) -> None: ...

    # published acts
    def save_act(self, term: int, number: str, act: ActInfo) -> None: ...

    # authors
    def save_authors(self, term: int, number: str, authors: BillAuthors) -> None: ...

    # agendas of upcoming sittings
    def save_agenda(self, term: int, number: str, items: tuple[AgendaItem, ...]) -> None: ...

    def list_awaiting_consultation_results(self, term: int, channel_id: str) -> list[Bill]:
        """Published bills with a public consultation whose opinions are not published yet."""
        ...

    def list_due_in_force(self, term: int, channel_id: str, *, today: date) -> list[Bill]: ...

    def list_due_consultations(
        self, term: int, channel_id: str, *, today: date, days_before: int
    ) -> list[Bill]: ...

    def list_failed_status_changes(
        self, term: int, channel_id: str, *, max_attempts: int
    ) -> list[StatusChange]: ...

    # runs
    def last_discovery_started_at(self) -> datetime | None: ...

    def start_run(self, report: RunReport) -> int: ...

    def finish_run(self, run_id: int, report: RunReport) -> None: ...

    # the transaction a dry run rolls back
    def begin(self) -> None: ...

    def rollback(self) -> None: ...
