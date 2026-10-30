"""Interfaces (typing.Protocol) that services depend on. Adapters implement them."""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from lexinform.models import (
    ActInfo,
    AgendaItem,
    AmendmentsContext,
    AmendmentsRecord,
    AnalysisRecord,
    ArtifactIdentity,
    ArtifactMetadata,
    ArtifactReadIdentity,
    ArtifactReadReceipt,
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
    CandidateDispositionTrace,
    ChangeContext,
    ChangeReviewRecord,
    ChannelPost,
    CheckAspect,
    CommandOutcome,
    CommandState,
    Committee,
    CommitteeSitting,
    ComputationResult,
    ContentSnapshot,
    ContentVersion,
    DeliveryAlert,
    DeliveryPlan,
    DeliveryResolution,
    Digest,
    DigestPart,
    DocumentDownloadDiagnostics,
    DocumentEdition,
    DocumentExtraction,
    DocumentReadMetrics,
    DocumentReviewRecord,
    DocumentSelectionRecord,
    DocumentWork,
    Editorial,
    ExtractedDocument,
    ExtractionResult,
    IncomingCommand,
    JointContext,
    JointRecord,
    LegacyResultImport,
    LegacyResultOccurrence,
    LegacyResultValue,
    LlmBatch,
    LlmBatchItem,
    LocatedText,
    MetadataUrlOmissions,
    Mp,
    NameContext,
    NameRecord,
    ObservationBasis,
    ObservationMode,
    ObservedProcess,
    OdtTextExtraction,
    PdfTextExtraction,
    Phase,
    PlenaryVoting,
    PrintInfo,
    ProcessDetail,
    ProcessSummary,
    Publication,
    PublicationKind,
    PublicationStatus,
    RclLetterUse,
    RclLetterUseObservation,
    RclLetterUseRecord,
    RclProject,
    RclProjectSummary,
    RclStage,
    ReadyAnalysis,
    ResultApplicabilityObservation,
    ResultApplication,
    ReviewPurpose,
    ReviewSource,
    ReviewWork,
    RunReport,
    SejmSitting,
    SejmTerm,
    SelectionArtifactReference,
    SelectionEditionReference,
    SelectionExtractionReference,
    SenateAct,
    SittingNews,
    SourceCheck,
    SourceMetadataOmission,
    SourceObservation,
    Stage,
    StatusChange,
    SupplementContext,
    SupplementRecord,
    TextDocument,
    TriageContext,
    TriageRecord,
    UpdateNews,
    Vote,
    WordDetailedExtraction,
    WordStructuredExtraction,
    WordStructuredParts,
    WordTextExtraction,
    WykazEntry,
)
from lexinform.models.batch import BatchJob, BatchKind
from lexinform.models.document_reads import DocumentPhysicalDiagnostics
from lexinform.models.physical_inventory import PhysicalCapture, PhysicalMetadata
from lexinform.models.report import CallKind
from lexinform.models.senate_inspection import SenateInspectionLink, SenateInspectionRecord
from lexinform.models.source_catalog_attempts import SourceCatalogAttempt, SourceCatalogSnapshot
from lexinform.models.word_view_capture import WordViewExtraction
from lexinform.physical_observations import OrderedPhysicalObservation


class Clock(Protocol):
    def now(self) -> datetime: ...


class StateCheckpoint(Protocol):
    def __call__(self) -> None: ...


class DocumentReadMonitor(Protocol):
    def reset_read_metrics(self) -> None: ...

    def read_metrics(self) -> DocumentReadMetrics: ...


class DocumentDownloadMonitor(Protocol):
    def reset_download_diagnostics(self) -> None: ...

    def read_download_diagnostics(self) -> DocumentDownloadDiagnostics | None: ...

    def read_metadata_url_omissions(self) -> MetadataUrlOmissions | None: ...


class DocumentArtifactObserver(Protocol):
    def read_artifacts(self) -> tuple[ArtifactIdentity, ...]: ...

    def read_extraction_results(self) -> tuple[ExtractionResult, ...]: ...


class DocumentPhysicalObserver(Protocol):
    def reset_physical_observations(self) -> None: ...

    def read_physical_observations(self) -> tuple[OrderedPhysicalObservation, ...]: ...

    def read_physical_diagnostics(self) -> DocumentPhysicalDiagnostics | None: ...


class DocumentFileReadObserver(Protocol):
    def read_artifact_reads(self) -> tuple[ArtifactReadIdentity, ...]: ...


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

    def self_amendments(self, term: int, number: str) -> tuple[PrintInfo, ...]: ...

    def get_voting(self, term: int, sitting: int, number: int) -> tuple[Vote, ...]: ...

    def list_votings(self, term: int, sitting: int) -> tuple[PlenaryVoting, ...]: ...

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


class ConsultationDocuments(Protocol):
    def read(self, submission: BillSubmission) -> tuple[ReviewSource, ...]: ...


class TextSource(Protocol):
    """Where a bill's text and fresh metadata come from. Network only: never touches the
    repository, so several bills can be located at once."""

    def locate(self, bill: Bill) -> LocatedText: ...

    def supporting_documents(
        self, bill: Bill, purpose: ReviewPurpose
    ) -> tuple[ReviewSource, ...]: ...


class TextIdentity(Protocol):
    """Source evidence that a new document republishes the previously analysed text."""

    def same_text(self, bill: Bill, previous: AnalysisRecord, document: TextDocument) -> bool: ...


class AuthorsResolver(Protocol):
    """Who signed a bill, read from its text (deputies' and committee bills). Best effort:
    None when the text names nobody; only outages propagate."""

    def resolve(self, bill: Bill, text: str) -> BillAuthors | None: ...


@runtime_checkable
class StructuredTextExtractor(Protocol):
    def extract_document(self, data: bytes) -> ExtractedDocument: ...


@runtime_checkable
class ProvenanceTextExtractor(Protocol):
    def extract_document_with_provenance(self, data: bytes) -> DocumentExtraction: ...


@runtime_checkable
class PdfPageTextExtractor(Protocol):
    def extract_pages(self, data: bytes) -> PdfTextExtraction: ...


@runtime_checkable
class WordBlockTextExtractor(Protocol):
    def extract_blocks(self, data: bytes) -> WordTextExtraction: ...


@runtime_checkable
class WordNoteTextExtractor(Protocol):
    def extract_with_notes(self, data: bytes) -> WordDetailedExtraction: ...


@runtime_checkable
class WordViewTextExtractor(Protocol):
    def extract_with_view_status(self, data: bytes) -> WordViewExtraction: ...


@runtime_checkable
class WordStructureTextExtractor(Protocol):
    def extract_with_structure(self, data: bytes) -> WordStructuredExtraction: ...


@runtime_checkable
class WordSemanticStructureTextExtractor(Protocol):
    def extract_with_semantic_structure(self, data: bytes) -> WordStructuredExtraction: ...


@runtime_checkable
class WordSemanticStructuredPartsExtractor(Protocol):
    def extract_parts_with_semantic_word_structure(
        self, data: bytes, *, include_pdf_pages: bool = True
    ) -> WordStructuredParts: ...


@runtime_checkable
class WordStructuredPartsExtractor(Protocol):
    def extract_parts_with_word_structure(
        self, data: bytes, *, include_pdf_pages: bool = True
    ) -> WordStructuredParts: ...


@runtime_checkable
class OdtBlockTextExtractor(Protocol):
    def extract_odt_blocks(self, data: bytes) -> OdtTextExtraction: ...


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

    def name_bill(self, ctx: NameContext) -> NameRecord:
        """The short name readers know a bill by, read off the channel's description of it."""
        ...


class TriageBackend(Protocol):
    def triage(self, ctx: TriageContext) -> TriageRecord: ...

    def review_change(self, ctx: ChangeContext) -> ChangeReviewRecord:
        """Whether a new text's changed passages alter the analysis: the cheap gate of a re-read."""
        ...


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

    def render_card_parts(
        self, bill: Bill, print_info: PrintInfo | None, *, minimum: int, today: date | None = None
    ) -> tuple[str, ...]: ...

    def card_digest(self, bill: Bill, *, today: date | None = None) -> str:
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
        self, bill: Bill, change: StatusChange, reply_to: int | None, *, with_act: bool = False
    ) -> PublishResult:
        """`with_act` when the act published in the same run is told in this message too."""
        ...

    def edit_status_update(
        self,
        bill: Bill,
        change: StatusChange,
        *,
        message_id: int,
        today: date,
        with_act: bool = False,
    ) -> None: ...

    def publish_act_published(self, bill: Bill, reply_to: int | None) -> PublishResult: ...

    def publish_in_force(
        self, bill: Bill, reply_to: int | None, *, today: date | None = None
    ) -> PublishResult: ...

    def publish_consultation_deadline(
        self, bill: Bill, reply_to: int | None, *, today: date
    ) -> PublishResult: ...

    def publish_consultation_results(self, bill: Bill, reply_to: int | None) -> PublishResult: ...

    def publish_sittings(self, news: Sequence[SittingNews]) -> PublishResult:
        """One top-level post for the sittings a run announced, moved or took back."""
        ...

    def sittings_fit(self, news: Sequence[SittingNews]) -> bool:
        """Whether every entry fits in one message without dropping facts; no I/O."""
        ...

    def render_updates(self, news: Sequence[UpdateNews], *, today: date) -> str: ...

    def updates_fit(self, news: Sequence[UpdateNews], *, today: date) -> bool: ...

    def publish_updates(self, text: str) -> PublishResult: ...

    def render_delivery(self, publication: Publication) -> str: ...

    def split_message(self, text: str) -> tuple[str, ...]: ...

    def render_sittings(self, news: Sequence[SittingNews]) -> str: ...

    def render_digest(self, digest: Digest, *, draft: bool = False) -> tuple[str, ...]: ...

    def render_digest_parts(
        self, digest: Digest, *, draft: bool = False
    ) -> tuple[DigestPart, ...]: ...

    def publish_prepared(self, publication: Publication) -> PublishResult: ...

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

    def save_document_work(self, work: DocumentWork) -> None: ...

    def list_document_work(self, *, due: datetime) -> list[DocumentWork]: ...

    def migrate(self) -> None: ...

    def close(self) -> None: ...

    def dump(self) -> str:
        """The whole database as SQL, the schema version included (the state branch)."""
        ...

    def restore(self, script: str) -> None:
        """Replace the contents with a dump; an older dump is migrated."""
        ...

    def get(self, term: int, number: str) -> Bill | None: ...

    def bill_exists(self, term: int, number: str) -> bool: ...

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
        clear_reason: bool = False,
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

    def import_legacy_results(self, imported: LegacyResultImport) -> bool: ...

    def get_legacy_result(self, value_id: str) -> LegacyResultValue | None: ...

    def list_legacy_result_occurrences(
        self, term: int, number: str
    ) -> list[LegacyResultOccurrence]: ...

    def save_computation_result(self, result: ComputationResult) -> None: ...

    def get_computation_result(self, result_id: str) -> ComputationResult | None: ...

    def list_computation_results(
        self, term: int, number: str, *, purpose: BatchKind | None = None
    ) -> list[ComputationResult]: ...

    def save_result_application(self, application: ResultApplication) -> None: ...

    def list_result_applications(self, term: int, number: str) -> list[ResultApplication]: ...

    def save_applicability_observation(
        self, observation: ResultApplicabilityObservation
    ) -> None: ...

    def get_applicability_observation(
        self, observation_id: str
    ) -> ResultApplicabilityObservation | None: ...

    def list_applicability_observations(
        self, term: int, number: str
    ) -> list[ResultApplicabilityObservation]: ...

    def save_review_work(self, term: int, number: str, work: tuple[ReviewWork, ...]) -> None: ...

    def save_source_observation(
        self, selection: DocumentSelectionRecord, observation: SourceObservation
    ) -> None: ...

    def save_source_catalog_snapshot(
        self, snapshot: SourceCatalogSnapshot, *, recorded_at: datetime
    ) -> None: ...

    def get_source_catalog_snapshot(self, catalog_id: str) -> SourceCatalogSnapshot | None: ...

    def save_source_catalog_attempt(
        self, attempt: SourceCatalogAttempt, *, recorded_at: datetime
    ) -> None: ...

    def get_source_catalog_attempt(self, attempt_id: str) -> SourceCatalogAttempt | None: ...

    def list_source_catalog_attempts(self) -> list[SourceCatalogAttempt]: ...

    def save_source_metadata_omission(
        self, omission: SourceMetadataOmission, *, recorded_at: datetime
    ) -> None: ...

    def get_source_metadata_omission(self, omission_id: str) -> SourceMetadataOmission | None: ...

    def list_source_metadata_omissions(self) -> list[SourceMetadataOmission]: ...

    def get_document_selection(self, selection_id: str) -> DocumentSelectionRecord | None: ...

    def save_selection_edition(self, selection_id: str, edition: DocumentEdition) -> None: ...

    def get_document_edition(self, edition_id: str) -> DocumentEdition | None: ...

    def get_selection_edition(self, selection_id: str) -> SelectionEditionReference | None: ...

    def get_source_observation(self, observation_id: str) -> SourceObservation | None: ...

    def list_source_observations(self, term: int, number: str) -> list[SourceObservation]: ...

    def save_candidate_disposition_trace(
        self, observation_id: str, trace: CandidateDispositionTrace
    ) -> None: ...

    def get_candidate_disposition_trace(
        self, trace_id: str
    ) -> CandidateDispositionTrace | None: ...

    def list_candidate_disposition_traces(
        self, observation_id: str
    ) -> list[CandidateDispositionTrace]: ...

    def save_content_snapshot(self, snapshot: ContentSnapshot) -> None: ...

    def get_content_snapshot(self, snapshot_id: str) -> ContentSnapshot | None: ...

    def save_content_version(self, version: ContentVersion) -> None: ...

    def get_content_version(self, content_id: str) -> ContentVersion | None: ...

    def list_content_versions(self, root_publication_id: int) -> list[ContentVersion]: ...

    def bind_publication_content(self, publication_id: int, content_id: str) -> None: ...

    def get_publication_content(self, publication_id: int) -> ContentVersion | None: ...

    def list_content_publications(self, root_publication_id: int) -> list[Publication]: ...

    def save_artifact_metadata(self, artifact: ArtifactMetadata) -> None: ...

    def get_artifact_metadata(self, sha256: str) -> ArtifactMetadata | None: ...

    def save_artifact_read(self, receipt: ArtifactReadReceipt) -> None: ...

    def get_artifact_read(self, read_id: str) -> ArtifactReadReceipt | None: ...

    def list_artifact_reads(
        self, *, sha256: str | None = None, requested_url: str | None = None
    ) -> list[ArtifactReadReceipt]: ...

    def save_selection_artifact(self, reference: SelectionArtifactReference) -> None: ...

    def list_selection_artifacts(self, selection_id: str) -> list[SelectionArtifactReference]: ...

    def save_physical_inventory(
        self,
        metadata: tuple[PhysicalMetadata, ...] = (),
        captures: tuple[PhysicalCapture, ...] = (),
        *,
        recorded_at: datetime,
    ) -> None: ...

    def save_physical_metadata(
        self, metadata: PhysicalMetadata, *, recorded_at: datetime
    ) -> None: ...

    def get_physical_metadata(self, metadata_id: str) -> PhysicalMetadata | None: ...

    def save_physical_capture(self, capture: PhysicalCapture, *, recorded_at: datetime) -> None: ...

    def get_physical_capture(self, capture_id: str) -> PhysicalCapture | None: ...

    def iter_physical_captures(
        self, artifact_sha256: str | None = None
    ) -> Iterator[PhysicalCapture]: ...

    def save_senate_inspection(
        self,
        record: SenateInspectionRecord,
        links: tuple[SenateInspectionLink, ...] = (),
        *,
        recorded_at: datetime,
    ) -> None: ...

    def get_senate_inspection(self, inspection_id: str) -> SenateInspectionRecord | None: ...

    def iter_senate_inspections(
        self, term: int, number: str
    ) -> Iterator[SenateInspectionRecord]: ...

    def list_senate_inspection_links(
        self, inspection_id: str
    ) -> tuple[SenateInspectionLink, ...]: ...

    def save_rcl_letter_use(self, use: RclLetterUse, *, recorded_at: datetime) -> None: ...

    def get_rcl_letter_use(self, use_id: str) -> RclLetterUseRecord | None: ...

    def iter_rcl_letter_uses(self, project_id: int) -> Iterator[RclLetterUseRecord]: ...

    def save_extraction_result(
        self, result: ExtractionResult, *, recorded_at: datetime
    ) -> None: ...

    def get_extraction_result(self, result_id: str) -> ExtractionResult | None: ...

    def save_selection_extraction(self, reference: SelectionExtractionReference) -> None: ...

    def list_selection_extractions(
        self, selection_id: str
    ) -> list[SelectionExtractionReference]: ...

    def save_document_review(
        self, term: int, number: str, review: DocumentReviewRecord
    ) -> None: ...

    def list_review_work(self) -> list[Bill]: ...

    def list_acts_for_review(self, channel_id: str) -> list[Bill]: ...

    def effective_rule_publications(
        self, term: int, number: str, channel_id: str
    ) -> list[Publication]: ...

    def effective_rule_refs(self, term: int, number: str, channel_id: str) -> set[str]: ...

    def reserve_effective_rules(self, pub: Publication, refs: tuple[str, ...]) -> None: ...

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

    def replace_unsent_update_delivery(
        self, change_id: int, channel_id: str, delivery: DeliveryPlan
    ) -> bool:
        """Replace a queued individual plan before its first send; shared payloads stay frozen."""
        ...

    def get_update_publication(self, change_id: int, channel_id: str) -> Publication | None: ...

    def list_amendment_revisions(self, publication_id: int) -> list[Publication]: ...

    def list_deferred_updates(self, channel_id: str) -> list[Publication]: ...

    def list_scheduled_deliveries(self, channel_id: str) -> list[Publication]: ...

    def list_open_deliveries(self, channel_id: str) -> list[Publication]: ...

    def get_delivery_alert(self, channel_id: str) -> DeliveryAlert | None: ...

    def save_delivery_alert(self, alert: DeliveryAlert) -> None: ...

    def tag_update_group(self, publication_ids: tuple[int, ...], ref: str) -> None: ...

    def get_act_update(self, term: int, number: str, channel_id: str) -> Publication | None:
        """The status update whose saved delivery includes this bill's act notice."""
        ...

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

    def computation_owner_terms(self, number: str, kind: BatchKind) -> tuple[int, ...]: ...

    def context_rejected_batch_ids(self) -> set[str]: ...

    def set_observation(
        self,
        term: int,
        number: str,
        mode: ObservationMode | None,
        basis: ObservationBasis | None,
    ) -> None:
        """None and None is "not decided yet": the next tracking phase asks the rule again."""
        ...

    def record_check(
        self,
        term: int,
        number: str,
        aspect: CheckAspect,
        *,
        at: datetime,
        ok: bool,
        outage: bool = False,
    ) -> None: ...

    def list_status_changes(self, term: int, number: str) -> list[StatusChange]: ...

    def list_observation_candidates(self, channel_id: str) -> list[tuple[Bill, bool]]: ...

    def source_checks(self, term: int, number: str) -> dict[CheckAspect, SourceCheck]: ...

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

    def count_document_work(self) -> dict[str, int]: ...

    def count_by_status(self) -> dict[str, int]:
        """How many bills sit in each status, the empty statuses left out."""
        ...

    def count_publications(self, channel_id: str) -> dict[str, int]:
        """How many posts of the channel are in each publication status."""
        ...

    def list_failing_checks(
        self, *, min_failures: int, limit: int
    ) -> list[tuple[Bill, SourceCheck]]:
        """Checks of live rows failing `min_failures` times in a row or more, worst first."""
        ...

    def list_stuck_publications(self, channel_id: str, *, limit: int) -> list[Publication]:
        """`pending`/`unknown` posts, oldest first: an ambiguous delivery `posted()` will not
        retry on its own, so the operator is who has to notice it (BUGS.md #4)."""
        ...

    def search(self, text: str, *, limit: int) -> list[Bill]:
        """Bills whose title or number contains `text`, newest change first."""
        ...

    def save_editorial(self, term: int, number: str, editorial: Editorial) -> None: ...

    def list_attention_publications(
        self, term: int, number: str, *, limit: int
    ) -> list[Publication]: ...

    def replace_unattempted_delivery(self, publication_id: int, delivery: DeliveryPlan) -> bool: ...

    def freeze_direct_delivery(self, original: Publication, delivery: DeliveryPlan) -> bool: ...

    def freeze_sitting_delivery(
        self, members: tuple[Publication, ...], plans: tuple[DeliveryPlan, ...]
    ) -> bool: ...

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
        unthreaded: bool = False,
    ) -> list[Bill]: ...

    def create_publication(self, publication: Publication) -> int: ...

    def publication_by_id(self, publication_id: int) -> Publication | None: ...

    def resolve_delivery(self, resolution: DeliveryResolution) -> bool: ...

    def delivery_resolutions(self, publication_id: int) -> list[DeliveryResolution]: ...

    def list_digest_candidates(
        self, channel_id: str, *, eligible_before: datetime
    ) -> list[Publication]: ...

    def digest_owner(self, source_id: int) -> int | None: ...

    def digest_sources(self, owner_id: int) -> list[Publication]: ...

    def reserve_digest_events(self, owner_id: int, source_ids: tuple[int, ...]) -> None: ...

    def freeze_legacy_digest_parts(
        self, original: Publication, parts: tuple[DigestPart, ...], *, created_at: datetime
    ) -> Publication | None: ...

    def complete_digest_events(
        self, owner_id: int, *, message_id: int, sent_at: datetime
    ) -> list[Publication]: ...

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

    def card_aliases(self, channel_id: str, message_id: int) -> list[Publication]: ...

    def card_refresh_parts(self, card_id: int) -> list[Publication]: ...

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

    def save_committee_documents(
        self, term: int, number: str, sources: tuple[ReviewSource, ...]
    ) -> None: ...

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

    def move_primary_analysis_work(self, term: int, from_number: str, to_number: str) -> None:
        """Hand primary analysis work to its successor while preserving secondary subjects."""
        ...

    def save_rcl(self, term: int, number: str, project: RclProject) -> None: ...

    def list_rcl_awaiting_link(self) -> list[Bill]:
        """RCL rows whose project knows its druk number but that are not linked to it yet."""
        ...

    def rcl_thread_awaits_druk(self) -> bool:
        """Whether an RCL project with a Telegram thread does not know its druk number yet."""
        ...

    def find_rcl(self, number: str) -> Bill | None:
        """The row of an RCL project by its `RCL/{id}` number (ids never repeat across terms)."""
        ...

    def find_by_rm_number(self, rm_number: str) -> Bill | None:
        """The RCL row whose project shows this `RM-…` number (set once it went to the Sejm)."""
        ...

    def find_print_by_rcl_num(self, term: int, rm_number: str) -> Bill | None:
        """The druk whose process names this `RM-…` number, spacing and case aside."""
        ...

    def prints_by_rcl_num(self, term: int) -> dict[str, str]:
        """Every stored druk of the term by the `RM-…` number its process names (`rcl_num_key`)."""
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

    def list_government_rollover_candidates(self, term: int) -> list[Bill]:
        """Rows eligible for the same government-only transfer as move_government_rows."""
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


class RclLetterUseObserver(Protocol):
    def observe_rcl_letter_use(self, observation: RclLetterUseObservation) -> None: ...

    def read_rcl_letter_uses(self) -> tuple[RclLetterUseObservation, ...]: ...


class SourceHistoryPersistence(Protocol):
    def persist_source_history(self, repo: BillRepository, *, recorded_at: datetime) -> None: ...
