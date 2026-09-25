"""Interfaces (typing.Protocol) that services depend on. Adapters implement them."""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import date, datetime
from typing import Protocol

from lexinform.models import (
    ActInfo,
    AgendaItem,
    AmendmentsContext,
    AmendmentsRecord,
    AnalysisRecord,
    BackfillReport,
    BatchIntent,
    BatchRequest,
    BatchResult,
    BatchStatus,
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
    DeliveryPlan,
    DeliveryResolution,
    Digest,
    IncomingCommand,
    JointContext,
    JointRecord,
    LlmBatch,
    LlmBatchItem,
    LocatedText,
    Mp,
    ObservedProcess,
    Phase,
    PrintInfo,
    ProcessDetail,
    ProcessSummary,
    Publication,
    PublicationKind,
    PublicationStatus,
    RclProject,
    RclProjectSummary,
    RclStage,
    ReadyAnalysis,
    RunReport,
    SejmSitting,
    SejmTerm,
    SenateAct,
    Stage,
    StatusChange,
    SupplementContext,
    SupplementRecord,
    TriageContext,
    TriageRecord,
    Vote,
    WykazEntry,
)
from lexinform.models.batch import BatchJob
from lexinform.models.report import CallKind


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


class SenateGateway(Protocol):
    """senat.gov.pl, which has no API; `SenateUnavailableError` when it does not answer."""

    def find_act(self, title_final: str, *, passed_on: date) -> SenateAct | None:
        """The page of the act the Sejm passed on `passed_on`; None while the Senate lists none."""
        ...

    def read_act(self, url: str) -> SenateAct:
        """The act's page read again: the committees and their sittings come days after it."""
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

    def pages(self, data: bytes) -> int:
        """How many pages the file has; 0 when its format has none (Word, ODT, an archive) or it
        cannot be read. A file with pages can be handed to the model as images when its text
        layer holds nothing — which is what most of what the Sejm publishes is."""
        ...

    def select_pages(self, data: bytes, *, first: int, count: int) -> bytes:
        """The same document cut down to `count` pages from `first` (0-based), so that the pages
        that are worth nothing are not paid for. The file unchanged when it has no pages."""
        ...


class AnalysisBackend(Protocol):
    """The full per-bill analysis, and the free local estimate that guards its cost — the two
    always travel together because the guard has to price the same model it is guarding."""

    def analyze(self, ctx: BillContext) -> AnalysisRecord: ...

    def count_input_tokens(self, ctx: BillContext) -> int | None:
        """What this analysis will be charged for its input, counted by the model's own
        tokenizer before the call; None when the count could not be obtained. Free, and exact
        where an estimate from the text length is not — a scanned document has no text to
        measure at all."""
        ...


class AmendmentsBackend(Protocol):
    def summarize_amendments(self, ctx: AmendmentsContext) -> AmendmentsRecord:
        """What a set of amendments (Senate, "-A" report) changes in the bill as described."""
        ...


class SupplementBackend(Protocol):
    def digest_supplement(self, ctx: SupplementContext) -> SupplementRecord:
        """What a document filed to a print (the government's position, the OSR, an opinion)
        says about the bill as described."""
        ...


class JointBackend(Protocol):
    def compare_joint(self, ctx: JointContext) -> JointRecord:
        """How one print of a jointly considered group differs from the others, read off the
        channel's own description of each."""
        ...


class TriageBackend(Protocol):
    def triage(self, ctx: TriageContext) -> TriageRecord: ...


class LlmAnalyzer(
    AnalysisBackend, AmendmentsBackend, SupplementBackend, JointBackend, TriageBackend, Protocol
):
    """Everything a bill's analysis can ask a model for. Each of the five capabilities above can
    be its own model, chosen independently in settings (`llm_analysis_model`,
    `llm_amendments_model`, `llm_supplement_model`, `llm_joint_model`, `llm_triage_model`) —
    `llm_hybrid.HybridAnalyzer` is the router that makes five backends answer as one port."""


class BatchBackend(Protocol):
    """Homogeneous batches of typed analysis, amendment, document or comparison requests."""

    def prepare_request(self, request: BatchRequest) -> BatchRequest:
        """Freeze provider parameters and reserve the input plus maximum output cost."""
        ...

    def count_input_tokens(self, ctx: BillContext) -> int | None: ...

    def submit(self, requests: Sequence[BatchRequest]) -> str:
        """File one submission; returns the provider's own batch id."""
        ...

    def poll(self, batch_id: str) -> BatchStatus:
        """Whether the provider is done with this batch yet."""
        ...

    def fetch_results(self, batch_id: str, kind: CallKind = "analysis") -> Iterator[BatchResult]:
        """Every answer of an `"ended"` batch, in no particular order (`custom_id` says which
        request each is)."""
        ...

    def forget(self, batch_id: str) -> None:
        """Delete a collected batch at the provider; one already gone is not an error."""
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
        """Replace the card `message_id` with the bill's card as it renders now."""
        ...

    def card_digest(self, bill: Bill) -> str:
        """Digest of the card as it would render now, without sending anything: what lets a run
        tell a card that is still true from one that has drifted."""
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

    def publish_in_force(
        self, bill: Bill, reply_to: int | None, *, today: date | None = None
    ) -> PublishResult: ...

    def publish_consultation_deadline(
        self, bill: Bill, reply_to: int | None, *, today: date
    ) -> PublishResult: ...

    def publish_consultation_results(self, bill: Bill, reply_to: int | None) -> PublishResult: ...

    def publish_agenda(
        self,
        bill: Bill,
        item: AgendaItem,
        reply_to: int | None,
        moved_from: AgendaItem | None = None,
    ) -> PublishResult:
        """`moved_from` is the same sitting as it was last announced, when it has moved: another
        day, another hour or another room."""
        ...

    def publish_agenda_cancelled(
        self, bill: Bill, item: AgendaItem, reply_to: int | None, *, still_meets: bool
    ) -> PublishResult:
        """Take back an announced sitting. `still_meets` when the sitting goes ahead without the
        bill on its agenda, rather than being called off itself."""
        ...

    def publish_hearing_deadline(
        self, bill: Bill, hearing: Stage, reply_to: int | None, *, today: date
    ) -> PublishResult:
        """Applications to the public hearing `hearing` close in a few days."""
        ...

    def publish_decision_deadline(
        self, bill: Bill, phase: Phase, reply_to: int | None, *, today: date
    ) -> PublishResult:
        """The Senate's or the President's constitutional term is running out."""
        ...

    def publish_digest(self, digest: Digest, *, approve: str = "") -> PublishResult:
        """The week's digest; `approve` labels the button only the draft carries."""
        ...


class RunNotifier(Protocol):
    def notify(self, report: RunReport, log_lines: list[str]) -> None: ...

    def notify_backfill(self, report: BackfillReport) -> None:
        """What `lexinform reprefilter` did. A step of its own before the run and writing to the
        same database, it left no trace in the run's report at all."""
        ...


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


class WorkflowStarter(Protocol):
    """Starts the daily workflow with the inputs `/run` names; answers with a link to it.

    Separate from `InboxWriter` because it is the one command that is not filed: `/run` is the
    run, so there is nothing for a run to execute."""

    @property
    def workflow_url(self) -> str: ...

    def start_run(self, inputs: Mapping[str, str]) -> str: ...


class CommandAcknowledger(Protocol):
    """Tells the operator the command was taken (a reply under it); best effort."""

    def queued(self, command: IncomingCommand) -> int: ...

    def started(self, command: IncomingCommand, note: str, *, url: str | None = None) -> None:
        """A `/run` the relay has already acted on: what it started and where to watch it."""
        ...

    def pressed(self, callback_id: str) -> None:
        """Stop a pressed inline button spinning; what it asked for is filed like any command."""
        ...


class BillRepository(Protocol):
    """Persistence of bills, publications, status changes and runs (SQLite in production).

    The listings span every term: a `Bill` carries its own, and the services group by `bill.term`
    where an API path needs one. Only the end-of-term methods, which serve the zasada
    dyskontynuacji, are scoped to a term. The command methods keep one row per Telegram update.
    """

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

    def save_observed_closure(self, term: int, number: str, closed: date | None) -> None: ...

    def atomic(self) -> AbstractContextManager[None]: ...

    def load_analysis_memo(self) -> dict[str, str]: ...

    def save_analysis_memo(
        self,
        key: str,
        record_json: str,
        *,
        term: int | None = None,
        number: str | None = None,
        used_at: datetime | None = None,
    ) -> None: ...

    def prune_history(self, *, before: datetime, memo_before: datetime) -> dict[str, int]: ...

    def save_observed_process(self, term: int, number: str, observed: ObservedProcess) -> None: ...

    def save_update_delivery(
        self, change_id: int, channel_id: str, delivery: DeliveryPlan
    ) -> None: ...

    def get_update_publication(self, change_id: int, channel_id: str) -> Publication | None: ...

    def release_planned_changes(
        self, ids: tuple[int, ...], channel_id: str, *, message_id: int, sent_at: datetime
    ) -> None: ...

    def save_analysis(self, term: int, number: str, record: AnalysisRecord) -> None: ...

    def save_ready_analysis(self, term: int, number: str, ready: ReadyAnalysis) -> None: ...

    def record_analysis_failure(self, term: int, number: str, error: str) -> None: ...

    def save_llm_batch(self, batch: LlmBatch, items: Sequence[LlmBatchItem]) -> None:
        """File a submission and which bill each of its requests answers for, together: a batch
        with no items to collect it against would never be marked done."""
        ...

    def save_batch_intent(self, intent: BatchIntent) -> None: ...

    def batch_job(self, custom_id: str) -> BatchJob | None: ...

    def set_awaiting_batch(self, term: int, number: str, since: datetime | None) -> None: ...

    def list_queued_batch_intents(self) -> list[BatchIntent]: ...

    def list_submitting_batch_intents(self) -> list[BatchIntent]: ...

    def mark_batch_intents_submitting(self, custom_ids: Sequence[str]) -> None: ...

    def mark_batch_intents_queued(self, custom_ids: Sequence[str]) -> None: ...

    def delete_batch_intents(self, custom_ids: Sequence[str]) -> None: ...

    def list_open_llm_batches(self) -> list[LlmBatch]:
        """Batches not yet fully collected (`status != "failed"` and not every item consumed),
        oldest first: what a collect run has to ask the provider about."""
        ...

    def mark_llm_batch_polled(
        self, batch_id: str, *, status: BatchStatus, polled_at: datetime
    ) -> None: ...

    def mark_llm_batch_collected(self, batch_id: str, *, completed_at: datetime) -> None: ...

    def list_unforgotten_llm_batches(self) -> list[LlmBatch]:
        """Collected batches the provider has not yet been told to delete, oldest first."""
        ...

    def mark_llm_batch_forgotten(self, batch_id: str, *, forgotten_at: datetime) -> None: ...

    def list_llm_batch_items(self, batch_id: str) -> list[LlmBatchItem]:
        """Every item of this batch not yet written back, oldest `custom_id` first."""
        ...

    def mark_llm_batch_item_consumed(
        self,
        batch_id: str,
        custom_id: str,
        *,
        consumed_at: datetime,
        result: BatchResult | None = None,
    ) -> None: ...

    def list_unaccounted_batch_items(self) -> list[LlmBatchItem]: ...

    def mark_batch_item_accounted(self, batch_id: str, custom_id: str, *, at: datetime) -> None: ...

    def reset_bill(
        self, term: int, number: str, status: BillStatus, *, reason: str | None = None
    ) -> None:
        """Put a bill back into `status` with a clean retry budget; `reason` is kept as the
        bill's last error, so that a later answer can say who put it there."""
        ...

    def list_by_status(
        self,
        statuses: list[BillStatus],
        *,
        limit: int,
        max_attempts: int | None = None,
    ) -> list[Bill]: ...

    def list_rcl_missing_consultation(self, *, limit: int) -> list[Bill]:
        """Analysed or pending RCL rows whose consultation window was never read, oldest
        `change_date` first: the longest-stale rows are the likeliest to have a window already
        closed and untold."""
        ...

    def count_by_status(self) -> dict[str, int]:
        """How many bills sit in each status, the empty statuses left out."""
        ...

    def count_publications(self, channel_id: str) -> dict[str, int]:
        """How many posts of the channel are in each publication status."""
        ...

    def list_stuck_publications(self, channel_id: str, *, limit: int) -> list[Publication]:
        """`pending`/`unknown` posts, oldest first: an ambiguous delivery `posted()` will not
        retry on its own, so the operator is who has to notice it (BUGS.md #4)."""
        ...

    def search(self, text: str, *, limit: int) -> list[Bill]:
        """Bills whose title or number contains `text`, newest change first."""
        ...

    def list_joint_reply_candidates(
        self, channel_id: str, *, limit: int, max_attempts: int = 3
    ) -> list[Bill]:
        """Analysed, relevant bills that name prints considered jointly with them and have no
        post yet, whatever their own score: the publisher decides which of them is really a
        reply."""
        ...

    def list_publish_candidates(
        self, channel_id: str, *, min_score: int, limit: int, max_attempts: int = 3
    ) -> list[Bill]: ...

    def list_tracked(
        self,
        channel_id: str,
        *,
        closed_grace_days: int,
        pending_decision_max_days: int,
        now: datetime,
        changed_since: datetime | None = None,
    ) -> list[Bill]: ...

    def create_publication(self, publication: Publication) -> int: ...

    def publication_by_id(self, publication_id: int) -> Publication | None: ...

    def resolve_delivery(self, resolution: DeliveryResolution) -> bool: ...

    def delivery_resolutions(self, publication_id: int) -> list[DeliveryResolution]: ...

    def list_due_deliveries(self, channel_id: str, *, max_attempts: int) -> list[Publication]: ...

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

    def list_publications_between(
        self, channel_id: str, *, since: datetime, until: datetime
    ) -> list[Publication]:
        """Every post the channel sent in the half-open window, oldest first (the digest)."""
        ...

    def delete_publication(
        self, term: int, number: str, kind: PublicationKind, channel_id: str
    ) -> int: ...

    def set_card_digest(self, publication_id: int, digest: str) -> None: ...

    def mark_stale_pending_as_unknown(self, *, now: datetime) -> list[str]:
        """Settle the publications a crashed run left pending; returns "number kind" for each."""
        ...

    def add_status_change(self, change: StatusChange) -> int | None: ...

    def closure_announced(self, term: int, number: str) -> bool: ...

    def save_status_change_amendments(self, change_id: int, record: AmendmentsRecord) -> None:
        """Attach the amendments summary to a recorded change (made after the row exists)."""
        ...

    def save_status_change_supplements(
        self, change_id: int, records: list[SupplementRecord]
    ) -> None:
        """Attach the digests of the documents this change announces (after the row exists)."""
        ...

    def save_seen_supplements(self, term: int, number: str, numbers: tuple[str, ...]) -> None:
        """Record which documents filed to the print the channel now knows about."""
        ...

    def save_joint_comparison(self, term: int, number: str, record: JointRecord) -> None:
        """Store how this print differs from the others considered jointly with it."""
        ...

    def list_skipped_with_joint_prints(self) -> list[Bill]:
        """Rows a prefilter skipped that name prints considered jointly with them: the group is
        in the `/processes` listing, so even a row whose text was never read has one."""
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

    def find_by_wykaz_number(self, wykaz_number: str) -> Bill | None:
        """The RCL row of a project with this wykaz number; the register's own row is
        `find_wykaz` (an `UD408` names both once the project is published)."""
        ...

    def save_wykaz(self, term: int, number: str, entry: WykazEntry) -> None: ...

    def remember_rcl_wykaz_number(
        self, wykaz_number: str, project_id: int, created: date | None
    ) -> None:
        """Note that this RCL project carries this number of the wykaz prac RM — for every row
        of the listing, followed or not: it is the only join between a plan and its project."""
        ...

    def find_rcl_project_of_plan(self, wykaz_number: str, announced: date) -> int | None:
        """The project published under a plan's number since the plan was announced, if any.
        The register reuses its numbers, so the date is part of the question."""
        ...

    def list_wykaz_awaiting_link(self) -> list[Bill]:
        """Wykaz rows whose project RCL discovery has seen but that are not linked to it yet."""
        ...

    def find_wykaz(self, number: str) -> Bill | None:
        """The row of a register entry by its `WPL/UD408` number."""
        ...

    def move_government_rows(self, from_term: int, to_term: int) -> int:
        """Carry the government's own rows — RCL projects still waiting for their druk, and
        wykaz entries — over to a new term (with their posts and status changes); a project
        already joined to a druk stays. Returns the count."""
        ...

    def list_unfinished_published(self, term: int, channel_id: str) -> list[Bill]:
        """Bills of the term with a card in the channel that the Sejm never finished with: no
        closure, not passed, not an RCL project, not yet marked discontinued."""
        ...

    def discontinue_unfinished(self, term: int, *, at: datetime) -> int:
        """Mark every unfinished Sejm bill of the term (published or not) as lapsed, so that no
        phase looks at it again. Returns how many rows were marked."""
        ...

    def save_act(self, term: int, number: str, act: ActInfo) -> None: ...

    def save_senate(self, term: int, number: str, act: SenateAct) -> None: ...

    def save_authors(self, term: int, number: str, authors: BillAuthors) -> None: ...

    def save_agenda(self, term: int, number: str, items: tuple[AgendaItem, ...]) -> None: ...

    def list_awaiting_consultation_results(self, channel_id: str) -> list[Bill]:
        """Published bills with a public consultation whose opinions are not published yet."""
        ...

    def list_due_in_force(self, channel_id: str, *, today: date) -> list[Bill]: ...

    def list_due_consultations(
        self, channel_id: str, *, today: date, days_before: int
    ) -> list[Bill]: ...

    def list_due_status_changes(
        self, channel_id: str, *, max_attempts: int
    ) -> list[StatusChange]: ...

    def list_held_status_changes(
        self, term: int, number: str, channel_id: str
    ) -> list[StatusChange]:
        """Changes held back as service stages (their update row is `skipped`), oldest first."""
        ...

    def get_status_change(self, change_id: int) -> StatusChange | None:
        """The change one update post was about; None when the row is gone."""
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

    def begin(self) -> None:
        """Open the transaction a dry run rolls back."""
        ...

    def rollback(self) -> None: ...
