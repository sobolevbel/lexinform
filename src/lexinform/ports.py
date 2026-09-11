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
    ChannelPost,
    CommandOutcome,
    CommandState,
    Committee,
    CommitteeSitting,
    IncomingCommand,
    LocatedText,
    Mp,
    PrintInfo,
    ProcessDetail,
    ProcessSummary,
    Publication,
    PublicationKind,
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
    WykazEntry,
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

    def find_process_by_rcl_num(
        self, term: int, rcl_num: str, *, since: date | None = None
    ) -> ProcessSummary | None:
        """The process whose `rclNum` names this RCL project (`RM-0610-7-26`), the other way
        round from `ProjectResolver`; None when the project has no print in this term. `rclNum`
        lives only in a process's detail, never in the listing, so an implementation reads
        details one by one and `since` (the hand-over to the Sejm) keeps that bounded."""
        ...

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

    def close(self) -> None: ...

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

    def close(self) -> None: ...


class ProjectResolver(Protocol):
    """The one question the Sejm discovery asks RCL: which project is behind a `rclNum`."""

    def resolve_project_id(self, rm_number: str) -> int | None: ...


class WykazGateway(Protocol):
    """The wykaz prac legislacyjnych RM on gov.pl (one CSV, downloaded once per run);
    `WykazUnavailableError` when gov.pl does not answer."""

    def entries(self) -> tuple[WykazEntry, ...]:
        """Every entry of the register, newest publication first, one per number."""
        ...

    def find(self, number: str) -> WykazEntry | None:
        """The entry with this wykaz number (`UD408`), however it is spelled."""
        ...

    def close(self) -> None: ...


class EliGateway(Protocol):
    """The ELI (European Legislation Identifier) API of the Sejm: published acts."""

    def get_act(self, eli: str) -> ActInfo | None: ...


class SejmApi(SejmGateway, EliGateway, Protocol):
    """The Sejm API as one client: the legislative process and the published acts (ELI)."""


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


class CommandInbox(Protocol):
    """Where operator commands wait for a run (JSON files of the `inbox` branch in production)."""

    def pending(self) -> list[IncomingCommand]:
        """Every command not yet taken, oldest update first."""
        ...

    def done(self, command: IncomingCommand) -> None:
        """The command was handled: take it out of the inbox."""
        ...


class OperatorReplier(Protocol):
    """Answers a command where it was given (under the message in the log channel)."""

    def reply(self, command: IncomingCommand, outcome: CommandOutcome) -> None: ...


class UpdatesSource(Protocol):
    """Telegram's `getUpdates`: the posts the bot has not confirmed yet; `offset` confirms
    every update below it. `TelegramUnavailableError` when Telegram is down."""

    def get_updates(self, *, offset: int | None, timeout: int) -> list[ChannelPost]: ...


class InboxWriter(Protocol):
    """Files a command where a run will find it (the git branch `inbox` in production).
    Raises when the file could not be written; filing the same update twice is not an error."""

    def put(self, command: IncomingCommand) -> None: ...


class CommandAcknowledger(Protocol):
    """Tells the operator the command was taken (a reply under it); best effort."""

    def queued(self, command: IncomingCommand) -> None: ...


class BillRepository(Protocol):
    """Persistence of bills, publications, status changes and runs (SQLite in production)."""

    def migrate(self) -> None: ...

    def close(self) -> None: ...

    def dump(self) -> str:
        """The whole database as SQL, the schema version included (the state branch)."""
        ...

    def restore(self, script: str) -> None:
        """Replace the contents with a dump; an older dump is migrated."""
        ...

    def get(self, term: int, number: str) -> Bill | None: ...

    def known_terms(self) -> list[int]:
        """Every Sejm term the database holds bills of, ascending."""
        ...

    def upsert_summary(self, summary: ProcessSummary, *, now: datetime) -> Bill: ...

    def set_status(
        self,
        term: int,
        number: str,
        status: BillStatus,
        *,
        prefilter_hits: list[str] | None = None,
        reason: str | None = None,
    ) -> None:
        """Move the bill to `status`; `prefilter_hits` and `reason` (kept in `last_error`: why a
        bill was skipped, readable from the state dump) are left as they are when None."""
        ...

    def save_stages(
        self, term: int, number: str, stages: tuple[Stage, ...], fingerprint: str
    ) -> None: ...

    def save_analysis(self, term: int, number: str, record: AnalysisRecord) -> None: ...

    def record_analysis_failure(self, term: int, number: str, error: str) -> None: ...

    def reset_bill(
        self, term: int, number: str, status: BillStatus, *, reason: str | None = None
    ) -> None:
        """Put a bill back into `status` with a clean retry budget; `reason` is kept as the
        bill's last error, so that a later answer can say who put it there."""
        ...

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
        count_attempt: bool = True,
    ) -> None:
        """Record the outcome of a send. A `FAILED` mark counts one attempt against the post's
        retry budget unless `count_attempt` is false: an outage of the channel is not the post's
        fault and must not use up its retries."""
        ...

    def get_publication(
        self,
        term: int,
        number: str,
        kind: PublicationKind,
        channel_id: str,
        *,
        ref: str | None = None,
    ) -> Publication | None:
        """The latest post of this kind for the bill; `ref` narrows to one sitting (agenda)."""
        ...

    def delete_publication(
        self, term: int, number: str, kind: PublicationKind, channel_id: str
    ) -> int: ...

    def mark_stale_pending_as_unknown(self, *, now: datetime) -> int: ...

    def add_status_change(self, change: StatusChange) -> int | None: ...

    def closure_announced(self, term: int, number: str) -> bool: ...

    def save_status_change_amendments(self, change_id: int, record: AmendmentsRecord) -> None:
        """Attach the amendments summary to a recorded change (made after the row exists)."""
        ...

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

    def save_act(self, term: int, number: str, act: ActInfo) -> None: ...

    def save_authors(self, term: int, number: str, authors: BillAuthors) -> None: ...

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

    def last_discovery_started_at(self) -> datetime | None: ...

    def prune_runs(self, *, before: datetime) -> int:
        """Delete run records started before `before`; returns how many."""
        ...

    def start_run(self, report: RunReport) -> int: ...

    def finish_run(self, run_id: int, report: RunReport) -> None: ...

    def list_runs(self, *, since: datetime) -> list[RunReport]:
        """Reports of the finished runs started at `since` or later, newest first."""
        ...

    def most_expensive_analyses(self, *, limit: int) -> list[Bill]:
        """Analysed bills by the input tokens of their analysis, largest first."""
        ...

    # One row per Telegram update.
    def record_command(self, command: IncomingCommand) -> bool:
        """Remember the command before it runs; False when the update was recorded already."""
        ...

    def command_state(self, update_id: int) -> CommandState | None:
        """What earlier runs did with the command; None when the update is unknown."""
        ...

    def mark_command_executed(self, update_id: int, *, outcome: str, at: datetime) -> None:
        """The command ran and its side effects are in the database; the answer may still fail."""
        ...

    def mark_command_handled(self, update_id: int, *, reply: str, at: datetime) -> None: ...

    # the transaction a dry run rolls back
    def begin(self) -> None: ...

    def rollback(self) -> None: ...
