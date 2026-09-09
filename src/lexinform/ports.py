"""Interfaces (typing.Protocol) that services depend on. Adapters implement them."""

from collections.abc import Iterator
from datetime import date, datetime
from typing import Protocol

from lexinform.models import (
    ActInfo,
    AgendaItem,
    AmendmentsContext,
    AmendmentsRecord,
    AnalysisRecord,
    Bill,
    BillAuthors,
    BillContext,
    BillStatus,
    BillSubmission,
    Committee,
    CommitteeSitting,
    LocatedText,
    Mp,
    PrintInfo,
    ProcessDetail,
    ProcessSummary,
    Publication,
    PublicationStatus,
    RclProject,
    RclProjectSummary,
    RclStage,
    RunReport,
    SejmSitting,
    SejmTerm,
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

    def list_terms(self) -> tuple[SejmTerm, ...]:
        """GET /sejm/term: every term of the Sejm, the running one flagged `current`."""
        ...

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


class RclGateway(Protocol):
    """legislacja.rcl.gov.pl (HTML, no API); `RclUnavailableError` when it is down or blocks us."""

    def list_projects(self, *, modified_since: date) -> Iterator[RclProjectSummary]:
        """Bills (typeId=2) modified on or after the date, newest change first."""
        ...

    def get_project(self, project_id: int) -> RclProject:
        """The project page: metadata and the timeline, folders not read yet."""
        ...

    def get_stage(self, project_id: int, stage_id: int) -> RclStage:
        """One stage with its folders and documents (the catalog page)."""
        ...

    def resolve_project_id(self, rm_number: str) -> int | None:
        """The RCL project behind a Sejm `rclNum` (`RM-0610-139-26`), if RCL knows it."""
        ...

    def download(self, url: str, *, max_bytes: int | None = None) -> bytes: ...


class ProjectResolver(Protocol):
    """The one question the Sejm discovery asks RCL: which project is behind a `rclNum`."""

    def resolve_project_id(self, rm_number: str) -> int | None: ...


class EliGateway(Protocol):
    """The ELI (European Legislation Identifier) API of the Sejm: published acts."""

    def get_act(self, eli: str) -> ActInfo | None: ...


class Downloader(Protocol):
    """Fetches one URL of a given host; `AttachmentTooLargeError` once `max_bytes` is exceeded,
    a `ServiceUnavailableError` subclass when the host is down."""

    def __call__(self, url: str, *, max_bytes: int | None = None) -> bytes: ...


class TextSource(Protocol):
    """Where a bill's text and fresh metadata come from. Network only: never touches the
    repository, so several bills can be located at once."""

    def locate(self, bill: Bill) -> LocatedText: ...


class AuthorsResolver(Protocol):
    """Who signed a bill, read from its text (deputies' and committee bills). Best effort:
    None when the text names nobody; only outages propagate."""

    def resolve(self, bill: Bill, text: str) -> BillAuthors | None: ...


class TextExtractor(Protocol):
    def extract(self, data: bytes) -> str: ...


class LlmAnalyzer(Protocol):
    def analyze(self, ctx: BillContext) -> AnalysisRecord: ...

    def triage(self, ctx: TriageContext) -> TriageRecord: ...

    def summarize_amendments(self, ctx: AmendmentsContext) -> AmendmentsRecord:
        """What a set of amendments (Senate, "-A" report) changes in the bill as described."""
        ...


class PublishResult(Protocol):
    @property
    def message_id(self) -> int: ...

    @property
    def document_message_ids(self) -> list[int]: ...


class Publisher(Protocol):
    """Where the messages go: Telegram in production, stdout in a dry run."""

    def publish_new_bill(self, bill: Bill, print_info: PrintInfo | None) -> PublishResult: ...

    def edit_new_bill(self, bill: Bill, print_info: PrintInfo | None, *, message_id: int) -> None:
        """Replace the card `message_id` with the bill's card as it renders now (used to add
        the print's tag to an RCL/RPW card once the druk exists)."""
        ...

    def publish_joint_bill(
        self, bill: Bill, primary: Bill, print_info: PrintInfo | None, reply_to: int | None
    ) -> PublishResult:
        """`bill` is considered jointly with `primary`, whose card is `reply_to`: a short reply
        there instead of a second card."""
        ...

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

    def publish_hearing_deadline(
        self, bill: Bill, hearing: Stage, reply_to: int | None, *, today: date
    ) -> PublishResult:
        """Applications to the public hearing `hearing` close in a few days."""
        ...


class RunNotifier(Protocol):
    def notify(self, report: RunReport, log_lines: list[str]) -> None: ...


class BillRepository(Protocol):
    """Persistence of bills, publications, status changes and runs (SQLite in production)."""

    # schema
    def migrate(self) -> None: ...

    # bills
    def get(self, term: int, number: str) -> Bill | None: ...

    def known_terms(self) -> list[int]:
        """Every Sejm term the database holds bills of, ascending."""
        ...

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

    # Listings span every term: a `Bill` carries its own, and the services group by `bill.term`
    # where an API path needs one. Only the end-of-term methods below are scoped to a term.
    def list_by_status(
        self,
        statuses: list[BillStatus],
        *,
        limit: int,
        max_attempts: int | None = None,
    ) -> list[Bill]: ...

    def list_publish_candidates(
        self, channel_id: str, *, min_score: int, limit: int, max_attempts: int = 3
    ) -> list[Bill]: ...

    def list_tracked(
        self,
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

    def save_status_change_amendments(self, change_id: int, record: AmendmentsRecord) -> None:
        """Attach the amendments summary to a recorded change (made after the row exists)."""
        ...

    # pre-print bills
    def save_submission(self, term: int, number: str, submission: BillSubmission) -> None: ...

    def list_pre_print(self) -> list[Bill]: ...

    def link_bills(
        self,
        term: int,
        pre_print_number: str,
        print_number: str,
        *,
        wykaz_number: str | None = None,
    ) -> None:
        """Point the two rows at each other; `wykaz_number` is the RCL project's, kept on the
        print so its replies carry the card's tag."""
        ...

    # RCL projects
    def save_rcl(self, term: int, number: str, project: RclProject) -> None: ...

    def list_rcl_awaiting_link(self) -> list[Bill]:
        """RCL rows whose project knows its druk number but that are not linked to it yet."""
        ...

    def find_rcl(self, number: str) -> Bill | None:
        """The row of an RCL project by its `RCL/{id}` number (ids never repeat across terms)."""
        ...

    def find_by_rm_number(self, rm_number: str) -> Bill | None:
        """The RCL row whose project shows this `RM-…` number (set once it went to the Sejm)."""
        ...

    def find_by_wykaz_number(self, wykaz_number: str) -> Bill | None: ...

    def move_rcl_projects(self, from_term: int, to_term: int) -> int:
        """Carry the RCL rows still waiting for their druk over to a new term (with their posts
        and status changes); a project already joined to a druk stays. Returns the count."""
        ...

    # end of a term (zasada dyskontynuacji)
    def list_unfinished_published(self, term: int, channel_id: str) -> list[Bill]:
        """Bills of the term with a card in the channel that the Sejm never finished with: no
        closure, not passed, not an RCL project, not yet marked discontinued."""
        ...

    def discontinue_unfinished(self, term: int, *, at: datetime) -> int:
        """Mark every unfinished Sejm bill of the term (published or not) as lapsed, so that no
        phase looks at it again. Returns how many rows were marked."""
        ...

    # published acts
    def save_act(self, term: int, number: str, act: ActInfo) -> None: ...

    # authors
    def save_authors(self, term: int, number: str, authors: BillAuthors) -> None: ...

    # agendas of upcoming sittings
    def save_agenda(self, term: int, number: str, items: tuple[AgendaItem, ...]) -> None: ...

    def list_awaiting_consultation_results(self, channel_id: str) -> list[Bill]:
        """Published bills with a public consultation whose opinions are not published yet."""
        ...

    def list_due_in_force(self, channel_id: str, *, today: date) -> list[Bill]: ...

    def list_due_consultations(
        self, channel_id: str, *, today: date, days_before: int
    ) -> list[Bill]: ...

    def list_failed_status_changes(
        self, channel_id: str, *, max_attempts: int
    ) -> list[StatusChange]: ...

    def list_held_status_changes(
        self, term: int, number: str, channel_id: str
    ) -> list[StatusChange]:
        """Changes held back as service stages (their update row is `skipped`), oldest first."""
        ...

    def release_held_status_changes(
        self, term: int, number: str, channel_id: str, *, message_id: int, sent_at: datetime
    ) -> int:
        """Mark the bill's held changes as sent inside `message_id`; returns how many."""
        ...

    # runs
    def last_discovery_started_at(self) -> datetime | None: ...

    def prune_runs(self, *, before: datetime) -> int:
        """Delete run records started before `before`; returns how many."""
        ...

    def start_run(self, report: RunReport) -> int: ...

    def finish_run(self, run_id: int, report: RunReport) -> None: ...

    # the transaction a dry run rolls back
    def begin(self) -> None: ...

    def rollback(self) -> None: ...
