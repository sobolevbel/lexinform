"""Turns a prefilter candidate into an LLM analysis: locate the text, read it, ask the model.

Also re-analyses a bill when a newer text appears (committee report with amendments, text after the
3rd reading, an updated print, a new version of an RCL project), feeding the previous analysis to
the model so it can say what changed. Where the text comes from is the `TextSource`'s business.
"""

import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from lexinform.analysis_records import RecordFactory, UsageFields
from lexinform.commission_reviews import COMMISSION_ROLES, commission_text_matches
from lexinform.concurrency import fan_out
from lexinform.document_input import amended_input_projection, append_self_amendments
from lexinform.document_selection import selection_for_input
from lexinform.document_versions import draft_version
from lexinform.errors import (
    BatchNotSubmittedError,
    DocumentSelectionError,
    LlmContextExceededError,
    LlmUnavailableError,
    OrkaUnreachableError,
    ServiceUnavailableError,
)
from lexinform.keywords import KeywordPrefilter
from lexinform.law_diff import diff_laws
from lexinform.law_digest import law_digest
from lexinform.law_technical import DECORATED_PAGE
from lexinform.models import (
    AMENDMENT_SOURCES,
    FULL_TEXT_SOURCES,
    SUPPLEMENT_SOURCES,
    AmendmentRevisionTarget,
    Amendments,
    AmendmentsContext,
    AmendmentsRecord,
    Analysis,
    AnalysisRecord,
    AnalysisVerdict,
    ApplicantType,
    BatchIntent,
    BatchItemMeta,
    BatchProvider,
    BatchRequest,
    BatchResult,
    Bill,
    BillContext,
    BillName,
    BillStatus,
    Category,
    ChangeContext,
    ChangedPassage,
    ComputationResult,
    DigestItemMeta,
    DocumentDigest,
    DocumentPart,
    DocumentReview,
    DocumentReviewRecord,
    DocumentSelection,
    JointBillDescription,
    JointComparison,
    JointContext,
    JointRecord,
    LlmBatch,
    LlmBatchItem,
    LlmCall,
    LocatedText,
    NameContext,
    NameRecord,
    PrintInfo,
    ProcessSummary,
    Publication,
    PublicationKind,
    PublicationStatus,
    ReadyAnalysis,
    ResultApplication,
    ResultApplicationTarget,
    ReviewContext,
    ReviewDocument,
    ReviewInputReference,
    ReviewPurpose,
    ReviewResultRecord,
    ReviewSource,
    ScannedDocument,
    Stage,
    StatusChange,
    SupplementContext,
    SupplementRecord,
    TextDocument,
    TextSource,
    TokenUsage,
    TriageContext,
    TriageRecord,
    computation_id,
    is_rcl_number,
    merge_usage,
    observe,
    provider_of,
    stage_fingerprint,
    usage_of,
    validate_review,
    window_closes_within,
)
from lexinform.models.batch import (
    AmendmentsQuestion,
    AnalysisQuestion,
    BatchJob,
    BatchKind,
    BatchQuestion,
    DocumentReviewQuestion,
    JointQuestion,
    NameQuestion,
    SupplementQuestion,
)
from lexinform.models.computations import CollectionReason, ComputationRecord
from lexinform.ports import (
    AnalysisBackend,
    AuthorsResolver,
    BatchBackend,
    BillRepository,
    Clock,
    LlmAnalyzer,
    StateCheckpoint,
    TextIdentity,
)
from lexinform.ports import TextSource as TextSourcePort
from lexinform.pricing import (
    CHARS_PER_TOKEN,
    TOKENS_PER_SCANNED_PAGE,
    estimate_input_cost,
    estimate_scan_cost,
    format_usd,
    input_cost,
)
from lexinform.sections import (
    APPENDIX_KINDS,
    PAGE_BREAK,
    TextBudget,
    carries_the_document,
    document_kind,
    excerpt_projection,
    excerpts,
    has_cover_letter,
    trim_print,
)
from lexinform.senate_documents import validate_senate_scan
from lexinform.services.analysis_applications import apply_analysis
from lexinform.services.computation_reuse import retained_secondary
from lexinform.services.cost import CostLedger
from lexinform.services.documents import MIN_TEXT_CHARS, TextLoader
from lexinform.services.joint import primary_of, revive_prefilter_skips
from lexinform.services.result_applications import record_secondary_application
from lexinform.services.senate_documents import resolve_senate_document, verified_senate_text
from lexinform.text_projection import TextProjection

log = logging.getLogger(__name__)


def _batch_records(
    result: BatchResult,
    prompt_version: str,
    now: datetime,
    *,
    request_sha256: str | None = None,
    computation_id: str | None = None,
) -> RecordFactory:
    return RecordFactory(
        result.model or "",
        prompt_version,
        now,
        UsageFields(
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_input_tokens=result.cache_read_input_tokens,
            cache_creation_input_tokens=result.cache_creation_input_tokens,
        ),
        request_sha256=request_sha256,
        computation_id=computation_id,
    )


_FIT_MARGIN = 0.9
"""How much of the cost limit a shortened text aims at, the rest being the prompt and the schema.

The counted input covers the system prompt, the structured-output schema and the bill's metadata
as well as its text, so scaling the characters by the overshoot alone would still land a little
over. Nine tenths clears the ~2k tokens of prefix with room to spare and costs one re-count.
"""

_FIT_MIN_CHARS = 20_000
"""Below this a shortened text is not a document any more, and refusing is the honest answer."""

SECONDARY_BATCH_SUBMISSIONS = 2
"""A batch expires, is cancelled or answers a 5xx: one more submission, then the post goes bare."""


@dataclass
class AnalysisResult:
    """Counters and verdicts of one analysis phase.

    `skipped_cost`: over the per-bill limit. `unanswered`: the host would not hand the file over,
    so the bill waits. `revived_joint`: prints a prefilter skipped that a group's card put back.
    `stopped`: the per-run limit ended the phase, which is a note and not an error.
    """

    analyzed: int = 0
    triaged_out: int = 0
    skipped_cost: int = 0
    unanswered: int = 0
    revived_joint: int = 0
    failed: int = 0
    batched: int = 0
    verdicts: list[AnalysisVerdict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    usage: dict[str, TokenUsage] = field(default_factory=dict)
    fatal_error: str | None = None
    stopped: str | None = None


@dataclass(frozen=True)
class AnalysisOutcome:
    """One analysis and what it cost: the stored record plus the triage that preceded it (the
    triage's own tokens are lost otherwise, and for the caller they are part of the bill)."""

    record: AnalysisRecord
    triage: TriageRecord | None = None

    @property
    def usage(self) -> dict[str, TokenUsage]:
        # A rejection's record is the triage call itself; adding `triage` too would double it.
        if self.triage is None or self.record.text_source == "excerpts":
            return usage_of(self.record)
        tokens = usage_of(self.triage)
        merge_usage(tokens, usage_of(self.record))
        return tokens


class TooExpensiveError(Exception):
    """A first analysis whose input alone would cost more than the per-bill limit allows."""

    def __init__(self, estimate: float, limit: float, *, measure: str) -> None:
        super().__init__(
            f"{format_usd(estimate)} of input for {measure} exceeds the {format_usd(limit)} limit"
        )
        self.estimate = estimate


@dataclass(frozen=True)
class AnalysisOptions:
    """The knobs of the analysis phase, as `Settings` sets them (cf. `TrackingOptions`).

    Both cost guards are off at 0, and the per-bill estimate needs the model's input price.
    `max_input_tokens` routes oversized inputs to the overflow backend before cost fitting.
    `triage_min_chars` is the length from which the cheap pass pays (a scan is triaged whatever
    its text). `channel_id`: a print whose group already holds a card there skips that pass.
    `submit_batches` files an analysis to the batch instead of calling the model (collecting one
    never turns on it); `batch_provider` names who a queued request is filed under.
    """

    max_attempts: int = 3
    workers: int = 1
    input_price_usd_per_mtok: float | None = None
    batch_input_price_usd_per_mtok: float | None = None
    supplement_input_price_usd_per_mtok: float | None = None
    max_input_tokens: int | None = None
    batch_max_input_tokens: int | None = None
    overflow_input_price_usd_per_mtok: float | None = None
    overflow_max_input_tokens: int | None = None
    overflow_model: str = "claude-opus-5-5"
    overflow_provider: BatchProvider = "anthropic"
    prompt_version: str = ""
    max_bill_cost_usd: float = 0.0
    max_run_cost_usd: float = 0.0
    triage_min_chars: int = 20_000
    triage_min_confidence: float = 0.8
    triage_scan_pages: int = 8
    channel_id: str | None = None
    batch_provider: BatchProvider | None = None
    submit_batches: bool = False
    require_batch: bool = False
    batch_sync_within_days: int = 0
    batch_kinds: frozenset[BatchKind] = frozenset({"analysis", "reanalysis"})
    consultation_reviews_enabled: bool = False
    commission_reviews_enabled: bool = False
    batch_models: dict[BatchKind, str] = field(default_factory=dict)
    batch_max_wait: timedelta = timedelta(hours=6)
    change_review_max_ratio: float = 0.15
    change_review_max_chars: int = 60_000


@dataclass(frozen=True)
class Waiting:
    since: datetime
    usage: dict[str, TokenUsage] = field(default_factory=dict)


@dataclass(frozen=True)
class _Loaded:
    """What a document turned into for the model: its text, or its pages when it has none.

    `text` is what was extracted whatever the source — for a scan that is the covering letter, or
    nothing at all — because the signatures on the letter are read from it even when the model is
    not shown a word of it.
    """

    text: str
    truncated: bool
    source: TextSource
    scan: ScannedDocument | None = None
    law: str | None = None
    whole: str | None = None
    """The trimmed text before the budget cut it: what a change between two texts is read from."""
    document: TextDocument | None = None
    selection: DocumentSelection = DocumentSelection()
    projection: TextProjection | None = None

    @property
    def digest(self) -> str | None:
        """What says this is the same document as last time: the normalised text, or the file."""
        if self.scan is not None:
            return self.scan.sha256
        return (
            text_digest(self.whole if self.whole is not None else self.text)
            if self.source in FULL_TEXT_SOURCES
            else None
        )

    def matches(self, record: AnalysisRecord) -> bool:
        return (self.law is not None and self.law == record.law_sha256) or (
            self.digest is not None and self.digest == record.text_sha256
        )

    def estimate(self, input_price: float) -> float:
        """What the model will charge to read it, before it is asked."""
        if self.scan is not None:
            return estimate_scan_cost(self.scan.pages, input_price)
        return estimate_input_cost(len(self.text), input_price)

    def measure(self, tokens: int | None) -> str:
        """What the price was worked out from, for the record a skipped bill keeps."""
        if self.scan is not None:
            return f"{self.scan.pages} scanned page(s)"
        return f"{tokens} tokens" if tokens is not None else f"~{len(self.text)} chars"


@dataclass(frozen=True)
class _FittedContext:
    ctx: BillContext
    projection: TextProjection | None

    def __post_init__(self) -> None:
        if self.projection is not None and self.projection.text != self.ctx.text:
            raise ValueError("Fitted context differs from its text projection")

    def input_selection(self, selection: DocumentSelection) -> DocumentSelection:
        return selection_for_input(
            selection, self.ctx.text, projection=self.projection
        ).without_text()


@dataclass(frozen=True)
class _Prepared:
    """Everything an analysis needs to be written down: produced without touching the database,
    so several bills can be prepared at the same time.

    `first` marks a first analysis, which seeds the stages. Four flags say the model was not
    asked: `unchanged` (a re-analysis found the analysed text under a new URL), `unreadable_reason`
    (the new document was not usable — an analysis of the metadata must never replace one of a
    text), `deferred` (the run's budget was spent; the text waits for the next run) and `queued`
    (filed to the batch instead — `record` is None then, `batch_request`/`batch_meta` carry what
    a submission needs).
    """

    bill: Bill
    located: LocatedText
    text: str
    source: TextSource
    record: AnalysisRecord | None
    first: bool
    triage: TriageRecord | None = None
    unchanged: bool = False
    unreadable_reason: str | None = None
    deferred: bool = False
    queued: bool = False
    batch_request: BatchRequest | None = None
    batch_meta: BatchItemMeta | None = None
    batch_provider: BatchProvider | None = None
    memo_key: str | None = None
    triage_memo_key: str | None = None
    from_batch: bool = False


_WHITESPACE = re.compile(r"\s+")


def _describe_for_comparison(bill: Bill) -> JointBillDescription:
    """One bill of a jointly considered group as the channel describes it today."""
    assert bill.analysis is not None, "compare_joint describes only prints that were analysed"
    analysis = bill.analysis.analysis
    return JointBillDescription(
        number=bill.number,
        title=bill.summary.title,
        applicant_type=bill.summary.applicant_type,
        summary=analysis.summary,
        key_changes=list(analysis.key_changes),
        affected_groups=list(analysis.affected_groups),
        practical_impact=analysis.practical_impact,
    )


def text_digest(text: str) -> str:
    """Versioned full-text identity; bare numeric lines may be table values."""
    normalised = _WHITESPACE.sub(" ", DECORATED_PAGE.sub("", text)).strip()
    return "text-v2:" + hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _same_text_read_again(previous: AnalysisRecord, fresh: AnalysisRecord) -> AnalysisRecord:
    """An unversioned digest read again from its own file to the same digest names no change."""
    old, new = previous.text_sha256 or "", fresh.text_sha256 or ""
    if not old or ":" in old or new != f"text-v2:{old}" or previous.source_url != fresh.source_url:
        return fresh
    if fresh.analysis.changes_since_previous:
        log.info("%s read again to the digest it had; no change is named", fresh.source_url)
    analysis = fresh.analysis.model_copy(update={"changes_since_previous": []})
    return fresh.model_copy(update={"analysis": analysis})


def _memo_key(bill: Bill, purpose: str, context: BaseModel, *, legacy: bool = False) -> str:
    data = context.model_dump(mode="json", exclude={"scan", "text"})
    if data.get("source_check") is None:
        data.pop("source_check", None)
    if data.get("recommendation") is None:
        data.pop("recommendation", None)
    if data.get("support_applicability_unknown") is False:
        data.pop("support_applicability_unknown", None)
    payload = context.model_dump()
    text = str(payload.get("text", ""))
    data["text_sha256"] = text_digest(text)
    if legacy:
        # Legacy identity locates in-flight work only; it never certifies a reusable answer.
        without_numbers = re.sub(
            r"^\s*[–\-—]?\s*\d{1,4}\s*[–\-—]?\s*$", "", text, flags=re.MULTILINE
        )
        normalized = _WHITESPACE.sub(" ", without_numbers).strip()
        data["text_sha256"] = hashlib.sha256(normalized.encode()).hexdigest()
    scan = payload.get("scan")
    if isinstance(scan, dict):
        data["scan"] = {
            "sha256": scan.get("sha256"),
            "pages": scan.get("pages"),
            "of_pages": scan.get("of_pages"),
        }
    data["term"] = bill.term
    data["purpose"] = purpose
    encoded = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _reused_selection(current: DocumentSelection, paid: DocumentSelection) -> DocumentSelection:
    """A fresh observation cannot certify the scan input of an earlier paid call."""
    return current.model_copy(update={"scan_input": paid.scan_input})


def _source_of(record: AnalysisRecord | None) -> TextDocument | None:
    """The file a stored analysis was read from, as far as the record itself can name it."""
    if record is None or record.source_url is None or record.text_source not in FULL_TEXT_SOURCES:
        return None
    return TextDocument(url=record.source_url, kind=record.source_kind)


def _reused[Record: BaseModel](record: Record) -> Record:
    return record.model_copy(
        deep=True,
        update={
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    )


class AnalysisService:
    """First analyses of candidates and re-analyses of bills whose text changed.

    The `CostLedger` is how every phase that asks the model reaches the run's budget. `keywords`
    picks what survives when a text is cut to the per-bill limit; `triage` None disables the
    cheap pass.
    """

    def __init__(
        self,
        repo: BillRepository,
        texts: TextSourcePort,
        loader: TextLoader,
        llm: LlmAnalyzer,
        clock: Clock,
        options: AnalysisOptions,
        *,
        text_budget: TextBudget,
        text_identity: TextIdentity | None = None,
        overflow_batch: BatchBackend | None = None,
        authors: AuthorsResolver | None = None,
        keywords: KeywordPrefilter | None = None,
        triage: KeywordPrefilter | None = None,
        batch: BatchBackend | None = None,
        batch_resolver: Callable[[BatchProvider], BatchBackend | None] | None = None,
        batch_checkpoint: StateCheckpoint | None = None,
    ) -> None:
        self._repo = repo
        self._texts = texts
        self._text_identity = text_identity
        self._loader = loader
        self._llm = llm
        self._overflow_batch = overflow_batch
        self._clock = clock
        self._options = options
        self._budget = text_budget
        self._authors = authors
        self._keywords = keywords or KeywordPrefilter()
        self._triage = triage
        self._batch = batch
        self._batch_resolver = batch_resolver
        self._batch_checkpoint = batch_checkpoint
        self._dry_run = False
        self._restored_collected: set[str] = set()
        self._accounted_items: set[tuple[str, str]] = set()
        self._ledger = CostLedger(max_run_usd=options.max_run_cost_usd)
        self._memo = repo.load_analysis_memo()
        self._context_rejections = repo.context_rejected_batch_ids()
        self._read_since_last_run = True

    def start_run(self, *, dry_run: bool = False) -> None:
        self._dry_run = dry_run
        self._restored_collected = {b.batch_id for b in self._repo.list_unforgotten_llm_batches()}
        committed = sum(batch.estimated_cost_usd for batch in self._repo.list_open_llm_batches())
        committed += sum(
            intent.request.estimated_cost_usd
            for intent in self._repo.list_queued_batch_intents()
            if (
                intent.request.question.kind != "consultation_review"
                or self._options.consultation_reviews_enabled
            )
            and (
                intent.request.question.kind != "commission_review"
                or self.review_enabled("commission_review")
            )
        )
        committed += sum(
            intent.request.estimated_cost_usd
            for intent in self._repo.list_submitting_batch_intents()
        )
        self._ledger.start_run(reserved_usd=committed)
        self._accounted_items.clear()
        for item in self._repo.list_unaccounted_batch_items():
            if item.result is not None:
                self._charge_batch_result(item, item.result)
        # A rolled-back dry run leaves both ahead of the database; a new service has just read them.
        if not self._read_since_last_run:
            self._memo = self._repo.load_analysis_memo()
            self._context_rejections = self._repo.context_rejected_batch_ids()
        self._read_since_last_run = False

    def acknowledge_batch_costs(self) -> None:
        for batch_id, custom_id in self._accounted_items:
            self._repo.mark_batch_item_accounted(batch_id, custom_id, at=self._clock.now())

    def _charge_batch_result(self, item: LlmBatchItem, result: BatchResult) -> None:
        identity = (item.batch_id, item.custom_id)
        if identity in self._accounted_items:
            return
        if result.model is not None:
            self._ledger.charge_call(
                LlmCall(
                    number=item.number,
                    kind=item.call_kind,
                    model=result.model,
                    input_tokens=result.input_tokens or 0,
                    output_tokens=result.output_tokens or 0,
                    cache_read_input_tokens=result.cache_read_input_tokens or 0,
                    cache_creation_input_tokens=result.cache_creation_input_tokens or 0,
                    batched=True,
                )
            )
        self._accounted_items.add(identity)

    @property
    def spent_usd(self) -> float:
        """What the model has cost this run, over every phase that asks it something."""
        return self._ledger.spent_usd

    @property
    def _submits_batches(self) -> bool:
        """Whether a memo miss is filed to the batch; `collect_batches` never asks."""
        return (
            self._batch is not None
            and (self._options.submit_batches or self._options.require_batch)
            and not self._dry_run
        )

    def _batches(self, bill: Bill) -> bool:
        """Whether this bill's memo miss may wait for the batch; one the reader must act on
        within `batch_sync_within_days` is asked at once."""
        if self._options.require_batch:
            return True
        days = self._options.batch_sync_within_days
        if not self._submits_batches:
            return False
        if days > 0 and window_closes_within(bill, self._clock.now().date(), days):
            log.info("%s skips the batch: the reader's window closes soon", bill.number)
            return False
        return True

    @property
    def stopped(self) -> str | None:
        """Set once the per-run limit has held a re-analysis back, for the run report to say so.
        The analysis phase says it for itself (`AnalysisResult.stopped`)."""
        return self._ledger.stopped

    @property
    def calls(self) -> list[LlmCall]:
        """Every model call this run has made, in the order they were charged."""
        return self._ledger.calls

    def analyze_pending(self, *, limit: int) -> AnalysisResult:
        """Analyse up to `limit` candidates; an outage stops the phase, a bill's own error
        costs it one attempt.

        The phase also stops when the run's model spend reaches the per-run limit: the remaining
        candidates keep their status and wait for the next run.
        """
        result = AnalysisResult()
        if limit <= 0:
            return result
        if self._options.channel_id is not None:
            # Before the candidates are listed, so a print the keywords dropped beside one the
            # channel carded is analysed in this run and not the next.
            result.revived_joint = len(revive_prefilter_skips(self._repo, self._options.channel_id))
        candidates = self._repo.list_by_status(
            [BillStatus.ANALYSIS_PENDING, BillStatus.ANALYSIS_READY, BillStatus.ANALYSIS_FAILED],
            limit=limit,
            max_attempts=self._options.max_attempts,
        )
        # Asked here and not in `_prepare`, which runs in the worker threads and never touches
        # the repository.
        carried = {bill.number for bill in candidates if self._joint_card_exists(bill)}

        def prepare(bill: Bill) -> _Prepared:
            return self._prepare_first(
                bill,
                triage=bill.number not in carried,
                submit_batch=self._batches(bill)
                and (self._options.require_batch or "analysis" in self._options.batch_kinds),
            )

        for outcome in fan_out(candidates, prepare, workers=self._options.workers):
            bill = outcome.item
            try:
                prepared = outcome.result()
                if prepared.queued:
                    if not self._enqueue(bill, prepared):
                        result.stopped = self._ledger.stopped
                        break
                    result.batched += 1
                    if self._ledger.exhausted:
                        self._ledger.stop("the remaining candidates wait for the next run")
                        result.stopped = self._ledger.stopped
                        break
                    continue
                record = self._persist(prepared)
            except ServiceUnavailableError as exc:
                result.fatal_error = exc.describe()
                log.error("aborting analysis phase: %s", result.fatal_error)
                break
            except DocumentSelectionError as exc:
                result.unanswered += 1
                status = (
                    BillStatus.ANALYSIS_PENDING
                    if exc.state == "waiting"
                    else BillStatus.DOCUMENT_AMBIGUOUS
                    if exc.state == "ambiguous"
                    else BillStatus.DOCUMENT_UNSUPPORTED
                )
                self._repo.set_status(bill.term, bill.number, status, reason=str(exc))
                continue
            except OrkaUnreachableError as exc:
                # The file was not read because the host would not hand it over today, which
                # says nothing about the bill. Analysing the metadata instead would write down
                # a verdict nothing ever revisits (RPW/30695/2026, 2026-09-14): the bill waits
                # for the next run instead, and the attempt is not one of its three.
                result.unanswered += 1
                log.warning("analysis of %s waits for the file: %s", bill.number, exc)
                self._repo.set_status(
                    bill.term,
                    bill.number,
                    BillStatus.ANALYSIS_PENDING,
                    reason=f"analysis: {type(exc).__name__}: {exc}",
                )
                continue
            except TooExpensiveError as exc:
                result.skipped_cost += 1
                log.warning("analysis of %s skipped: %s", bill.number, exc)
                self._repo.set_status(
                    bill.term,
                    bill.number,
                    BillStatus.SKIPPED_COST,
                    reason=f"analysis skipped: {exc} (lexinform reset --to analysis_pending)",
                )
                continue
            except Exception as exc:
                result.failed += 1
                log.exception("analysis failed for %s: %s", bill.number, exc)
                self._repo.record_analysis_failure(
                    bill.term, bill.number, f"{type(exc).__name__}: {exc}"
                )
                continue
            if record.text_source == "excerpts":
                result.triaged_out += 1
            else:
                result.analyzed += 1
            result.verdicts.append(
                AnalysisVerdict(
                    number=bill.number,
                    title=bill.summary.title,
                    relevant=record.analysis.relevant,
                    score=record.analysis.score,
                    triaged=record.text_source == "excerpts",
                    term=bill.term,
                )
            )
            result.input_tokens += record.input_tokens or 0
            result.output_tokens += record.output_tokens or 0
            merge_usage(result.usage, usage_of(record))
            # A rejection's record is the triage call; adding `prepared.triage` too would double it.
            if prepared.triage is not None and record.text_source != "excerpts":
                result.input_tokens += prepared.triage.input_tokens or 0
                result.output_tokens += prepared.triage.output_tokens or 0
                merge_usage(result.usage, usage_of(prepared.triage))
            if self._ledger.exhausted:
                result.stopped = (
                    f"{self._ledger.over_budget}; the remaining candidates wait for the next run"
                )
                log.warning("analysis phase stopped: %s", result.stopped)
                break
        return result

    def submit_queued_batches(self) -> None:
        """Submit durable queued intents; an uncertain remote outcome stays operator-visible."""
        if self._dry_run:
            return
        intents = self._repo.list_queued_batch_intents()
        intents = [
            i
            for i in intents
            if i.request.question.kind != "act_application"
            and (
                i.request.question.kind != "commission_review"
                or self.review_enabled("commission_review")
            )
            and (
                i.request.question.kind != "consultation_review"
                or self._options.consultation_reviews_enabled
            )
            and (
                self._options.require_batch
                or i.request.question.kind == "explanation"
                or (isinstance(i.meta, DigestItemMeta) and i.meta.amendment_revision is not None)
                or (
                    isinstance(i.meta, DigestItemMeta)
                    and i.meta.amendment_source is not None
                    and i.meta.amendment_source.scan
                )
                or (self._submits_batches and i.request.question.kind in self._options.batch_kinds)
                or (isinstance(i.meta, BatchItemMeta) and i.meta.overflow)
            )
        ]
        answered = {
            intent.request.custom_id
            for intent in intents
            if isinstance(intent.meta, DigestItemMeta) and intent.meta.memo_key in self._memo
        }
        for key in answered:
            self._cancel_queued_digest(key)
        intents = [intent for intent in intents if intent.request.custom_id not in answered]
        stale = [
            intent.request.custom_id
            for intent in intents
            if isinstance(intent.meta, BatchItemMeta)
            and (
                (bill := self._repo.get(intent.request.term, intent.request.number)) is None
                or bill.status is not BillStatus.BATCH_PENDING
                or bill.analysis_generation != intent.meta.generation
            )
        ]
        if stale:
            self._repo.delete_batch_intents(stale)
            intents = [intent for intent in intents if intent.request.custom_id not in stale]
        for provider in ("anthropic", "openai"):
            provider_intents = [intent for intent in intents if intent.provider == provider]
            backend = (
                self._batch_resolver(provider)
                if self._batch_resolver is not None
                else self._batch
                if provider == self._options.batch_provider
                else None
            )
            if backend is None:
                continue
            for group in _submission_groups(provider_intents):
                requests = [intent.request for intent in group]
                ids = [request.custom_id for request in requests]
                with self._repo.atomic():
                    self._repo.mark_batch_intents_submitting(ids)
                if self._batch_checkpoint is not None:
                    self._batch_checkpoint()
                try:
                    batch_id = backend.submit(requests)
                except BatchNotSubmittedError:
                    self._repo.mark_batch_intents_queued(ids)
                    raise
                with self._repo.atomic():
                    self._repo.save_llm_batch(
                        LlmBatch(
                            batch_id=batch_id,
                            provider=provider,
                            call_kind=requests[0].call_kind,
                            submitted_at=self._clock.now(),
                            status="submitted",
                            request_count=len(requests),
                            estimated_cost_usd=sum(req.estimated_cost_usd for req in requests),
                        ),
                        [LlmBatchItem.from_intent(batch_id, intent) for intent in group],
                    )
                    self._repo.delete_batch_intents(ids)
                if self._batch_checkpoint is not None:
                    self._batch_checkpoint()
                log.info("filed batch %s: %d request(s) with %s", batch_id, len(group), provider)

    def collect_batches(self) -> None:
        """Write down every batch the provider has finished. A bill whose answer landed goes
        back to the status the normal pipeline reads: `_prepare` will find the same memo key it
        queued and finish for free, with no new model call — this is the only place that reads
        one back."""
        if self._batch is None:
            return
        errors: list[str] = []
        collected = False
        for batch in self._repo.list_open_llm_batches():
            try:
                backend = self._batch_backend_of(batch)
                if backend is None:
                    log.warning("batch %s waits for %s backend", batch.batch_id, batch.provider)
                    continue
                collected |= self._collect_batch(batch, backend)
            except ServiceUnavailableError as exc:
                errors.append(f"{batch.provider}/{batch.batch_id}: {exc}")
        if not self._dry_run:
            if collected and self._batch_checkpoint is not None:
                self._batch_checkpoint()
            errors.extend(self._forget_collected(checkpointed=self._batch_checkpoint is not None))
        if errors:
            raise LlmUnavailableError("; ".join(errors))

    def _batch_backend_of(self, batch: LlmBatch) -> BatchBackend | None:
        if self._batch_resolver is not None:
            return self._batch_resolver(batch.provider)
        return self._batch if batch.provider == self._options.batch_provider else None

    def _forget_collected(self, *, checkpointed: bool) -> list[str]:
        """Delete at the provider every batch whose answers are already in a persisted state."""
        errors: list[str] = []
        for batch in self._repo.list_unforgotten_llm_batches():
            # Unless checkpointed, this run's collections exist only until its dump is pushed.
            if not checkpointed and batch.batch_id not in self._restored_collected:
                continue
            backend = self._batch_backend_of(batch)
            if backend is None:
                continue
            try:
                backend.forget(batch.batch_id)
            except ServiceUnavailableError as exc:
                errors.append(f"forget {batch.provider}/{batch.batch_id}: {exc}")
                continue
            self._repo.mark_llm_batch_forgotten(batch.batch_id, forgotten_at=self._clock.now())
        return errors

    def _collect_batch(self, batch: LlmBatch, backend: BatchBackend) -> bool:
        now = self._clock.now()
        status = backend.poll(batch.batch_id)
        self._repo.mark_llm_batch_polled(batch.batch_id, status=status, polled_at=now)
        items = self._repo.list_llm_batch_items(batch.batch_id)
        if status == "failed":
            for item in items:
                self._consume(
                    item,
                    BatchResult(custom_id=item.custom_id, error="batch failed"),
                    batch.provider,
                )
            self._repo.mark_llm_batch_collected(batch.batch_id, completed_at=now)
            self._ledger.release(batch.estimated_cost_usd)
            return True
        if status != "ended":
            return False
        by_id = {item.custom_id: item for item in items}
        received: set[str] = set()
        for batch_result in backend.fetch_results(batch.batch_id, batch.call_kind):
            found = by_id.get(batch_result.custom_id)
            if found is not None and batch_result.custom_id not in received:
                self._consume(found, batch_result, batch.provider)
                received.add(batch_result.custom_id)
        for custom_id, item in by_id.items():
            if custom_id not in received:
                self._consume(
                    item,
                    BatchResult(custom_id=custom_id, error="batch item missing from results"),
                    batch.provider,
                )
        self._repo.mark_llm_batch_collected(batch.batch_id, completed_at=now)
        self._ledger.release(batch.estimated_cost_usd)
        return True

    def uncertain_batch_intent_ids(self) -> list[str]:
        return [intent.request.custom_id for intent in self._repo.list_submitting_batch_intents()]

    def _consume(
        self, item: LlmBatchItem, batch_result: BatchResult, provider: BatchProvider
    ) -> None:
        if isinstance(item.meta, DigestItemMeta):
            self._consume_digest(item, batch_result, item.meta, provider)
        else:
            now = self._clock.now()
            current = self._repo.get(item.term, item.number)
            active = (
                current is not None
                and current.status is BillStatus.BATCH_PENDING
                and current.analysis_generation == item.meta.generation
            )
            with self._repo.atomic():
                record = self._consume_current(
                    item, batch_result, now, active=active, provider=provider
                )
                self._record_collection(
                    item, batch_result, record, current, provider, now, active=active
                )
            self._charge_batch_result(item, batch_result)
            if record is not None and active:
                self._memo.setdefault(
                    item.meta.memo_key or item.custom_id, record.model_dump_json()
                )
        if batch_result.context_exceeded:
            self._context_rejections.add(item.custom_id)

    def _record_collection(
        self,
        item: LlmBatchItem,
        response: BatchResult,
        record: AnalysisRecord | None,
        current: Bill | None,
        provider: BatchProvider,
        now: datetime,
        *,
        active: bool,
    ) -> None:
        assert isinstance(item.meta, BatchItemMeta), (
            "primary collection uses frozen analysis metadata"
        )
        assert item.call_kind in ("analysis", "reanalysis"), (
            "primary metadata belongs to primary purposes"
        )
        result = self._retain_computation(item, response, record, provider, now)
        reason: CollectionReason
        if current is None:
            reason = "bill_missing"
        elif current.analysis_generation != item.meta.generation:
            reason = "generation_changed"
        elif not active:
            reason = "inactive_status"
        elif record is None:
            reason = "no_validated_result"
        else:
            reason = "current_generation"
        self._repo.save_result_application(
            ResultApplication(
                result_id=result.result_id,
                term=item.term,
                number=item.number,
                generation=item.meta.generation,
                outcome=("ready" if record is not None else "failed") if active else "superseded",
                reason=reason,
                observed_generation=current.analysis_generation if current else None,
                observed_status=current.status if current else None,
                created_at=now,
            )
        )

    def _retain_computation(
        self,
        item: LlmBatchItem,
        response: BatchResult,
        record: ComputationRecord | None,
        provider: BatchProvider,
        now: datetime,
        *,
        review_input: ReviewInputReference | None = None,
    ) -> ComputationResult:
        assert item.call_kind not in ("triage", "change_review"), (
            "only batch purposes produce durable computation items"
        )
        primary = isinstance(item.meta, BatchItemMeta)
        review = item.call_kind in ("consultation_review", "commission_review", "act_application")
        result = ComputationResult(
            schema_version=1 if primary else 3 if review else 2,
            provider=provider,
            batch_id=item.batch_id,
            custom_id=item.custom_id,
            purpose=item.call_kind,
            term=item.term,
            number=item.number,
            generation=item.meta.generation if isinstance(item.meta, BatchItemMeta) else None,
            collected_at=now,
            input_binding=item.meta.input_binding,
            review_input=review_input,
            request_sha256=item.meta.request_sha256,
            validation="validated" if record is not None else "error",
            record=record,
            model=response.model,
            usage=TokenUsage(
                batch_input=response.input_tokens or 0,
                batch_output=response.output_tokens or 0,
                batch_cache_read=response.cache_read_input_tokens or 0,
                batch_cache_creation=response.cache_creation_input_tokens or 0,
            ),
            error=response.error,
            context_exceeded=response.context_exceeded,
        )
        self._repo.save_computation_result(result)
        return result

    def _consume_digest(
        self,
        item: LlmBatchItem,
        result: BatchResult,
        meta: DigestItemMeta,
        provider: BatchProvider,
    ) -> None:
        now = self._clock.now()
        review = item.call_kind in ("consultation_review", "commission_review", "act_application")
        retain = review or item.call_kind in (
            "amendments",
            "supplement",
            "joint",
            "name",
            "explanation",
        )
        records = _batch_records(
            result,
            meta.prompt_version,
            now,
            request_sha256=meta.request_sha256,
            computation_id=computation_id(provider, item.batch_id, item.custom_id)
            if retain
            else None,
        )
        record: (
            AnalysisRecord
            | AmendmentsRecord
            | SupplementRecord
            | JointRecord
            | DocumentReviewRecord
            | NameRecord
            | None
        ) = None
        if (
            item.call_kind == "explanation"
            and isinstance(result.answer, Analysis)
            and meta.explanation is not None
        ):
            source = meta.explanation
            record = records.analysis(
                result.answer,
                input_chars=source.input_chars,
                truncated=source.truncated,
                text_source=source.text_source,
            ).model_copy(
                update={
                    "source_url": source.source_url,
                    "source_kind": source.source_kind,
                    "text_sha256": source.text_sha256,
                    "law_sha256": source.law_sha256,
                    "source_checked_at": now,
                    "selection": source.selection,
                }
            )
        elif item.call_kind == "amendments" and isinstance(result.answer, Amendments):
            try:
                if meta.amendment_source is not None and meta.amendment_source.scan:
                    validate_senate_scan(result.answer.source_evidence, meta.amendment_source)
                record = records.amendments(result.answer, source_kind=meta.source_kind)
            except ValueError as exc:
                result = result.model_copy(update={"error": str(exc)})
        elif item.call_kind == "supplement" and isinstance(result.answer, DocumentDigest):
            record = records.supplement(
                result.answer, title=meta.title, source_kind=meta.source_kind
            )
        elif item.call_kind == "joint" and isinstance(result.answer, JointComparison):
            record = records.joint(result.answer, compared_with=meta.compared_with)
        elif item.call_kind == "name" and isinstance(result.answer, BillName):
            record = records.name(result.answer)
        elif (
            item.call_kind in ("consultation_review", "commission_review", "act_application")
            and isinstance(result.answer, DocumentReview)
            and meta.review_context is not None
        ):
            try:
                reviewed = validate_review(result.answer, meta.review_context)
                record = DocumentReviewRecord(
                    review=reviewed, context=meta.review_context, **records.fields()
                )
            except ValueError as exc:
                result = result.model_copy(update={"error": str(exc)})
        elif result.error is None:
            result = result.model_copy(update={"error": "batch answer does not match call kind"})
        if isinstance(record, (AmendmentsRecord, SupplementRecord)):
            record.scan_input = meta.scan_input
            record.source_url = meta.source_url or ""
        if isinstance(record, SupplementRecord):
            record.number = meta.document_number or ""
        with self._repo.atomic():
            revision_id = None
            if record is not None:
                if isinstance(record, AmendmentsRecord) and meta.amendment_revision is not None:
                    revision_id = self._queue_amendment_revision(
                        item, meta.amendment_revision, record
                    )
                self._repo.save_analysis_memo(
                    meta.memo_key,
                    record.model_dump_json(),
                    term=item.term,
                    number=item.number,
                    used_at=now,
                )
                if meta.delivery_key:
                    self._repo.save_analysis_memo(
                        meta.delivery_key,
                        record.model_dump_json(),
                        term=item.term,
                        number=item.number,
                        used_at=now,
                    )
            if retain:
                normalized = (
                    ReviewResultRecord.from_record(record)
                    if isinstance(record, DocumentReviewRecord)
                    else record
                )
                review_input = (
                    ReviewInputReference.from_context(
                        meta.review_context,
                        term=item.term,
                        recorded_key=meta.input_binding.reuse_key if meta.input_binding else None,
                    )
                    if review and meta.review_context is not None
                    else None
                )
                self._retain_computation(
                    item, result, normalized, provider, now, review_input=review_input
                )
            if revision_id is not None and record is not None:
                record_secondary_application(
                    self._repo,
                    item.term,
                    item.number,
                    record.computation_id,
                    target=ResultApplicationTarget(kind="publication", ref=str(revision_id)),
                    now=now,
                )
            self._repo.mark_llm_batch_item_consumed(
                item.batch_id, item.custom_id, consumed_at=now, result=result
            )
        self._charge_batch_result(item, result)
        if record is not None:
            self._memo.setdefault(meta.memo_key, record.model_dump_json())
            if meta.delivery_key:
                self._memo[meta.delivery_key] = record.model_dump_json()

    def _queue_amendment_revision(
        self, item: LlmBatchItem, target: AmendmentRevisionTarget, record: AmendmentsRecord
    ) -> int | None:
        original = self._repo.publication_by_id(target.publication_id)
        if (
            original is None
            or original.status is not PublicationStatus.SENT
            or original.message_id != target.delivery.edit_message_id
            or original.channel_id != target.channel_id
            or (original.term, original.number) != (item.term, item.number)
        ):
            raise ValueError("The original amendment message no longer matches its saved target")
        ref = f"amendments/{target.publication_id}/{item.custom_id}"
        if (
            self._repo.get_publication(
                item.term,
                item.number,
                PublicationKind.AMENDMENTS_REVISION,
                target.channel_id,
                ref=ref,
            )
            is not None
        ):
            return None
        record.source_url = target.source_url
        change = StatusChange.model_validate_json(target.delivery.change_json)
        change.amendments = record
        delivery = target.delivery.model_copy(update={"change_json": change.model_dump_json()})
        return self._repo.create_publication(
            Publication(
                term=item.term,
                number=item.number,
                kind=PublicationKind.AMENDMENTS_REVISION,
                status=PublicationStatus.QUEUED,
                channel_id=target.channel_id,
                ref=ref,
                created_at=self._clock.now(),
                delivery=delivery,
            )
        )

    def revise_amendments(
        self,
        bill: Bill,
        document: TextDocument,
        target: AmendmentRevisionTarget,
        *,
        proposal: str | None = None,
        recommendation: TextDocument | None = None,
        force_id: str = "",
    ) -> Waiting | Publication:
        assert bill.analysis is not None
        loaded = self._load_amendments_text(document)
        if not loaded.text.strip() and loaded.scan is None:
            raise ValueError("Полный текст поправок не извлечён; исправление не запущено")
        if self._too_expensive_to_digest(loaded):
            raise ValueError("Разбор поправок превышает лимит стоимости документа")
        context = self._amendments_context_of(bill, document, loaded, proposal, recommendation)
        content_key = _memo_key(bill, "amendments", context)
        revisions = self._repo.list_amendment_revisions(target.publication_id)
        latest = revisions[0] if revisions else None
        if (
            not force_id
            and latest is not None
            and latest.delivery is not None
            and latest.delivery.amendment_key == content_key
        ):
            return latest
        generation = force_id or f"after/{latest.id if latest else 0}"
        key = hashlib.sha256(
            f"amendments-revision/{target.publication_id}/{content_key}/{generation}".encode()
        ).hexdigest()
        ref = f"amendments/{target.publication_id}/{key}"
        existing = self._repo.get_publication(
            bill.term, bill.number, PublicationKind.AMENDMENTS_REVISION, target.channel_id, ref=ref
        )
        if existing is not None:
            return existing
        job = self._repo.batch_job(key)
        if job is not None:
            if job.state != "failed":
                return Waiting(job.since)
            if job.submissions >= SECONDARY_BATCH_SUBMISSIONS:
                raise ValueError("Batch разбора поправок не удался дважды; используйте force")
        model = self._options.batch_models.get("amendments", "")
        provider = provider_of(model)
        backend = self._batch_resolver(provider) if self._batch_resolver else self._batch
        if not model or backend is None:
            raise LlmUnavailableError("batch backend is unavailable for amendments")
        request = backend.prepare_request(
            BatchRequest(
                custom_id=key,
                term=bill.term,
                number=bill.number,
                question=AmendmentsQuestion(ctx=context),
                model=model,
            )
        )
        if not self._ledger.reserve(request.estimated_cost_usd):
            raise ValueError("Бюджет прогона исчерпан; повторите команду в следующем прогоне")
        delivery = target.delivery.model_copy(update={"amendment_key": content_key})
        now = self._clock.now()
        try:
            self._repo.save_batch_intent(
                BatchIntent.from_request(
                    request=request,
                    provider=provider,
                    created_at=now,
                    meta=DigestItemMeta(
                        memo_key=key,
                        queued_at=now,
                        prompt_version=request.prompt_version,
                        source_kind=document.kind,
                        source_url=document.url,
                        amendment_source=context.source_check,
                        scan_input=context.scan.scan_input if context.scan else None,
                        amendment_revision=target.model_copy(update={"delivery": delivery}),
                    ),
                )
            )
        except Exception:
            self._ledger.release(request.estimated_cost_usd)
            raise
        return Waiting(now)

    def _consume_current(
        self,
        item: LlmBatchItem,
        batch_result: BatchResult,
        now: datetime,
        *,
        active: bool,
        provider: BatchProvider,
    ) -> AnalysisRecord | None:
        assert isinstance(item.meta, BatchItemMeta)
        record = None
        if batch_result.analysis is not None:
            record = _batch_records(
                batch_result,
                item.meta.prompt_version or batch_result.prompt_version or "",
                now,
                request_sha256=item.meta.request_sha256,
            ).analysis(
                batch_result.analysis,
                input_chars=item.meta.input_chars,
                truncated=item.meta.truncated,
                text_source=item.meta.text_source,
            )
            record = record.model_copy(
                update={
                    "source_url": item.meta.source_url,
                    "source_kind": item.meta.source_kind,
                    "revision": item.meta.revision,
                    "text_sha256": item.meta.text_sha256,
                    "law_sha256": item.meta.law_sha256,
                    "source_checked_at": now,
                    "selection": item.meta.selection,
                    "computation_id": computation_id(provider, item.batch_id, item.custom_id),
                }
            )
            if active:
                self._repo.save_analysis_memo(
                    item.meta.memo_key or item.custom_id,
                    record.model_dump_json(),
                    term=item.term,
                    number=item.number,
                    used_at=now,
                )
                if item.meta.located is not None:
                    self._repo.save_ready_analysis(
                        item.term,
                        item.number,
                        ReadyAnalysis(
                            record=record, located=item.meta.located, text=item.meta.text
                        ),
                    )
                done = (
                    BillStatus.ANALYSIS_READY
                    if item.call_kind == "analysis"
                    else BillStatus.REANALYSIS_READY
                )
                self._repo.set_status(item.term, item.number, done)
        elif active:
            log.warning(
                "%s: batch answer (%s) is %s", item.number, item.call_kind, batch_result.error
            )
            if (
                batch_result.context_exceeded
                and self._overflow_batch is not None
                and not item.meta.overflow
            ):
                self._repo.set_status(
                    item.term,
                    item.number,
                    BillStatus.ANALYSIS_PENDING
                    if item.call_kind == "analysis"
                    else BillStatus.ANALYZED,
                )
            elif item.call_kind == "analysis":
                self._repo.record_analysis_failure(
                    item.term, item.number, batch_result.error or "batch: no answer"
                )
            else:
                # A reanalysis that failed keeps the old analysis, same as a synchronous one would.
                self._repo.set_status(item.term, item.number, BillStatus.ANALYZED)
        self._repo.mark_llm_batch_item_consumed(
            item.batch_id, item.custom_id, consumed_at=now, result=batch_result
        )
        return record

    def explain_bill(self, bill: Bill) -> AnalysisRecord | Waiting:
        prepared = self._prepare(
            bill,
            self._texts.locate(bill),
            previous=None,
            triage=False,
            submit_batch=True,
            purpose="explanation",
        )
        if prepared.record is not None:
            assert prepared.memo_key is not None
            self._remember_analysis(bill, prepared.memo_key, prepared.record)
            return prepared.record
        request, source = prepared.batch_request, prepared.batch_meta
        assert request is not None and source is not None and prepared.memo_key is not None
        job = self._repo.batch_job(request.custom_id)
        if job is not None:
            if job.state == "failed":
                raise RuntimeError("Разбор не получен: batch завершился с ошибкой")
            return Waiting(job.since)
        if len((request.payload_json or "").encode()) > 100_000_000:
            raise ValueError("one batch request exceeds the 100 MB payload limit")
        provider = prepared.batch_provider or self._options.batch_provider
        if provider is None:
            raise LlmUnavailableError("batch provider is unavailable for explanation")
        if not self._ledger.reserve(request.estimated_cost_usd):
            raise ValueError("Разбор не поставлен в очередь: исчерпан бюджет прогона")
        now = self._clock.now()
        try:
            self._repo.save_batch_intent(
                BatchIntent.from_request(
                    request=request,
                    meta=DigestItemMeta(
                        memo_key=prepared.memo_key,
                        queued_at=now,
                        prompt_version=request.prompt_version,
                        explanation=source.model_copy(update={"located": None, "text": ""}),
                    ),
                    provider=provider,
                    created_at=now,
                )
            )
        except Exception:
            self._ledger.release(request.estimated_cost_usd)
            raise
        self.submit_queued_batches()
        return Waiting(now)

    def analyze_bill(
        self, bill: Bill, *, ignore_cost_limit: bool = False
    ) -> AnalysisOutcome | Waiting:
        """First analysis from the current text. Persists the result; raises on failure.
        `ignore_cost_limit` is the operator's explicit wish (a forced command): the per-bill
        cost guard does not apply."""
        if bill.status is BillStatus.BATCH_PENDING:
            return Waiting(self._clock.now())
        if ignore_cost_limit:
            self._repo.reset_bill(bill.term, bill.number, BillStatus.ANALYSIS_PENDING)
            bill = self._repo.get(bill.term, bill.number) or bill
        prepared = self._prepare_first(
            bill,
            cost_guard=not ignore_cost_limit,
            triage=not self._joint_card_exists(bill),
            submit_batch=self._options.require_batch,
        )
        if prepared.queued:
            if not self._enqueue(bill, prepared):
                return Waiting(self._clock.now())
            self.submit_queued_batches()
            return Waiting(self._clock.now(), usage_of(prepared.triage) if prepared.triage else {})
        return AnalysisOutcome(self._persist(prepared), prepared.triage)

    def _prepare_first(
        self,
        bill: Bill,
        *,
        cost_guard: bool = True,
        triage: bool = True,
        submit_batch: bool = False,
    ) -> _Prepared:
        if (
            cost_guard
            and bill.status is BillStatus.ANALYSIS_READY
            and bill.ready_analysis is not None
        ):
            ready = bill.ready_analysis
            return _Prepared(
                bill,
                ready.located,
                ready.text,
                ready.record.text_source,
                _reused(ready.record),
                first=True,
                from_batch=True,
            )
        return self._prepare(
            bill,
            self._texts.locate(bill),
            previous=None,
            cost_guard=cost_guard,
            triage=triage,
            submit_batch=submit_batch,
        )

    def _joint_card_exists(self, bill: Bill) -> bool:
        """Whether another print of this bill's group already holds the card in the channel.

        Such a print is read and judged like any other but skips the cheap pass: the card has
        already answered the question the triage asks, and all the pass could do is take an
        alternative bill out of the channel silently.
        """
        if self._options.channel_id is None or not bill.summary.prints_considered_jointly:
            return False
        return primary_of(self._repo, bill, self._options.channel_id) is not None

    def reanalyze_bill(
        self,
        bill: Bill,
        document: TextDocument,
        *,
        summary: ProcessSummary | None = None,
        previous_document: TextDocument | None = None,
    ) -> AnalysisRecord | None:
        """Analyse a newer text of an already analysed bill; the model also reports what changed.
        `summary` is fresher metadata than the stored one, when the caller has it.

        None when the document turns out to carry the text already analysed (a file republished
        on RCL, a print re-dated by an attachment): the stored analysis then points at the new
        source and nothing else happens. None too when the run has spent its budget, and then
        nothing is written at all, so the next run offers the same document again."""
        fresh, changed = self.prepare_reanalysis(
            bill, document, summary=summary, previous_document=previous_document
        )
        if fresh is not bill:
            assert fresh.analysis is not None, (
                "prepare_reanalysis returns a new bill only with its record"
            )
            with self._repo.atomic():
                apply_analysis(
                    self._repo,
                    bill.term,
                    bill.number,
                    fresh.analysis,
                    now=self._clock.now(),
                    reason=(
                        "batch_result"
                        if bill.status is BillStatus.REANALYSIS_READY
                        else "memo_reuse"
                    )
                    if changed
                    else None,
                )
                if fresh.authors is not None:
                    self._repo.save_authors(bill.term, bill.number, fresh.authors)
        return fresh.analysis if changed else None

    def prepare_reanalysis(
        self,
        bill: Bill,
        document: TextDocument,
        *,
        summary: ProcessSummary | None = None,
        previous_document: TextDocument | None = None,
    ) -> tuple[Bill, bool]:
        """Memoize paid work without advancing the bill; `previous_document` was read before."""
        assert bill.analysis is not None, (
            "only a bill with a card is re-analysed, and a card needs an analysis"
        )
        if bill.status is BillStatus.REANALYSIS_READY and bill.ready_analysis is not None:
            ready = _same_text_read_again(bill.analysis, _reused(bill.ready_analysis.record))
            return bill.model_copy(update={"analysis": ready}), True
        if bill.status is BillStatus.BATCH_PENDING:
            # Already filed and not yet collected: re-detecting it now would queue it twice.
            return bill, False
        located = LocatedText(summary=summary, document=document)
        prepared = self._prepare(
            bill,
            located,
            previous=bill.analysis,
            previous_document=previous_document or _source_of(bill.analysis),
            submit_batch=self._batches(bill)
            and (self._options.require_batch or "reanalysis" in self._options.batch_kinds),
        )
        if prepared.queued:
            self._enqueue(bill, prepared)
            return bill, False
        if prepared.deferred:
            log.warning("%s: %s", bill.number, self._ledger.stopped)
            return bill, False
        if prepared.unreadable_reason is not None:
            log.warning(
                "%s: reanalysis blocked for %s: %s; the previous analysis is kept",
                bill.number,
                document.url,
                prepared.unreadable_reason,
            )
            return bill, False
        assert prepared.record is not None, (
            "only a queued _Prepared lacks a record, and it returned above"
        )
        if prepared.unchanged:
            log.info("%s: %s carries the analysed text; not re-analysed", bill.number, document.url)
            return bill.model_copy(update={"analysis": prepared.record}), False
        if prepared.memo_key is not None:
            self._remember_analysis(bill, prepared.memo_key, prepared.record)
        authors = bill.authors
        if self._authors is not None and document.kind == "print" and prepared.text:
            described = bill if summary is None else bill.model_copy(update={"summary": summary})
            authors = self._authors.resolve(described, prepared.text) or authors
        record = _same_text_read_again(bill.analysis, prepared.record)
        return bill.model_copy(update={"analysis": record, "authors": authors}), True

    def may_wait_for_batch(self, bill: Bill) -> bool:
        if self._options.require_batch:
            return True
        days = self._options.batch_sync_within_days
        if days > 0 and window_closes_within(bill, self._clock.now().date(), days):
            return False
        return bill.awaiting_batch_since is None or (
            self._clock.now() - bill.awaiting_batch_since < self._options.batch_max_wait
        )

    def _ask_or_wait[Record: (AmendmentsRecord, SupplementRecord, JointRecord, NameRecord)](
        self,
        bill: Bill,
        question: BatchQuestion,
        key: str,
        meta: DigestItemMeta,
        record_model: type[Record],
        sync_call: Callable[[], Record],
        *,
        may_wait: bool,
    ) -> Record | Waiting:
        require_batch = self._options.require_batch or (
            isinstance(question.ctx, AmendmentsContext) and question.ctx.scan is not None
        )
        legacy_key = _memo_key(bill, question.kind, question.ctx, legacy=True)
        legacy_job = self._repo.batch_job(legacy_key)
        if legacy_job is not None and legacy_job.state in {"queued", "submitting", "open"}:
            return Waiting(legacy_job.since)
        cached = self._memo.get(key)
        if cached is None:
            retained = retained_secondary(
                self._repo, bill.term, bill.number, question.kind, key, record_model
            )
            if retained is not None:
                cached = retained.model_dump_json()
        if cached is not None:
            self._cancel_queued_digest(key)
            record = record_model.model_validate_json(cached)
            self._remember_analysis(bill, key, record)
            return _reused(record)
        kind = question.kind
        model = self._options.batch_models.get(kind, "")
        provider = provider_of(model)
        backend = self._batch_resolver(provider) if self._batch_resolver else self._batch
        if require_batch or (
            may_wait
            and self._batches(bill)
            and kind in self._options.batch_kinds
            and model
            and provider == self._options.batch_provider
        ):
            job = self._repo.batch_job(key)
            now = self._clock.now()
            if job is not None and not self._files_again(job, key):
                if require_batch:
                    if job.state == "failed":
                        raise RuntimeError(f"{kind} batch failed; no synchronous fallback")
                    return Waiting(job.since)
                if job.state != "failed" and now - job.since < self._options.batch_max_wait:
                    return Waiting(job.since)
            else:
                if require_batch and (not model or backend is None):
                    raise LlmUnavailableError("batch backend is unavailable for the selected model")
                assert backend is not None
                request = backend.prepare_request(
                    BatchRequest(
                        custom_id=key,
                        term=bill.term,
                        number=bill.number,
                        question=question,
                        model=model,
                    )
                )
                if self._ledger.reserve(request.estimated_cost_usd):
                    try:
                        self._repo.save_batch_intent(
                            BatchIntent.from_request(
                                request=request,
                                meta=meta.model_copy(
                                    update={
                                        "prompt_version": request.prompt_version,
                                        "queued_at": now,
                                    }
                                ),
                                provider=provider,
                                created_at=now,
                            )
                        )
                    except Exception:
                        self._ledger.release(request.estimated_cost_usd)
                        raise
                    return Waiting(now)
                self._ledger.stop("secondary analysis waits for the next run")
                return Waiting(bill.awaiting_batch_since or now)
        if self._ledger.exhausted:
            self._ledger.stop("secondary analysis waits for the next run")
            return Waiting(bill.awaiting_batch_since or self._clock.now())
        estimate = 0.0
        if self._batch is not None and model and provider == self._options.batch_provider:
            request = self._batch.prepare_request(
                BatchRequest(
                    custom_id=key,
                    term=bill.term,
                    number=bill.number,
                    question=question,
                    model=model,
                )
            )
            estimate = request.estimated_cost_usd * 2
            if not self._ledger.reserve(estimate):
                self._ledger.stop("secondary analysis waits for the next run")
                return Waiting(bill.awaiting_batch_since or self._clock.now())
        try:
            record = sync_call()
        finally:
            self._ledger.release(estimate)
        if isinstance(record, (AmendmentsRecord, SupplementRecord)):
            record.scan_input = meta.scan_input
        self._ledger.charge(record, number=bill.number, kind=kind)
        self._remember_analysis(bill, key, record)
        self._cancel_queued_digest(key)
        return record

    def _files_again(self, job: BatchJob, key: str) -> bool:
        """Whether a failed secondary request goes to the batch once more instead of failing."""
        return (
            self._options.require_batch
            and job.state == "failed"
            and job.submissions < SECONDARY_BATCH_SUBMISSIONS
            and key not in self._context_rejections
        )

    def _cancel_queued_digest(self, key: str) -> None:
        if self._dry_run:
            return
        job = self._repo.batch_job(key)
        if job is None or job.state != "queued":
            return
        for intent in self._repo.list_queued_batch_intents():
            if intent.request.custom_id == key and isinstance(intent.meta, DigestItemMeta):
                self._repo.delete_batch_intents([key])
                self._ledger.release(intent.request.estimated_cost_usd)
                return

    def review_enabled(self, purpose: ReviewPurpose) -> bool:
        return (
            purpose in self._options.batch_kinds
            and {
                "consultation_review": self._options.consultation_reviews_enabled,
                "commission_review": self._options.commission_reviews_enabled,
                "act_application": False,
            }[purpose]
        )

    def review_context(
        self,
        bill: Bill,
        purpose: ReviewPurpose,
        documents: tuple[ReviewSource, ...],
    ) -> ReviewContext | None:
        if not self.review_enabled(purpose):
            return None
        sources: list[ReviewDocument] = []
        for requested, outcome in zip(
            documents,
            fan_out(documents, self._read_review_document, workers=self._options.workers),
            strict=True,
        ):
            source = outcome.result()
            if source is not None:
                sources.append(source)
            elif purpose == "commission_review":
                sources.append(
                    ReviewDocument(
                        identity=requested.identity,
                        url=requested.document.url,
                        role=requested.role,
                        text="",
                        sha256="",
                        normalized_sha256="",
                        complete=False,
                        author=requested.author,
                        body=requested.body,
                        forum=requested.forum,
                    )
                )
        if not any(source.text.strip() for source in sources):
            return None
        if purpose == "commission_review" and not any(
            source.complete and source.role in COMMISSION_ROLES for source in sources
        ):
            return None
        act = bill.act
        return ReviewContext(
            number=bill.number,
            title=bill.summary.title,
            purpose=purpose,
            documents=tuple(sources),
            act_eli=act.eli if act else None,
            promulgation_date=act.promulgation_date if act else None,
            canonical_date=act.entry_into_force if act else None,
            comparison_sha256=next(
                (
                    source.normalized_sha256
                    for source in sources
                    if source.role == "comparison_text"
                ),
                None,
            ),
        )

    def _read_review_document(self, source: ReviewSource) -> ReviewDocument | None:
        if source.snapshot is not None:
            return source.snapshot
        document = source.document
        if document.compare_versions:
            document, file = self._loader.select_document(document)
        else:
            file = self._loader.read_document(document)
        text = file.text or ""
        if source.expected_digest is not None and text_digest(text) != source.expected_digest:
            return None
        if not commission_text_matches(source.role, text):
            return None
        return ReviewDocument(
            identity=source.identity if document.url == source.document.url else document.url,
            url=document.url,
            role=source.role,
            draft_date=draft_version(text),
            text=text,
            sha256=hashlib.sha256(text.encode()).hexdigest(),
            normalized_sha256=text_digest(text),
            complete=(
                bool(text.strip())
                and not file.oversize
                and carries_the_document(text, min_chars=MIN_TEXT_CHARS, pages=file.pages)
                and not file.selection.rejected
                and file.selection.state not in ("ambiguous", "unsupported")
            ),
            file_sha256=file.sha256,
            author=source.author,
            body=source.body,
            forum=source.forum,
        )

    def review_exhausted(self, term: int, context: ReviewContext) -> bool:
        """Exhausted submissions stop only reviews without a reusable validated result."""
        key = context.memo_key(term)
        if key in self._memo:
            return False
        bill = self._repo.get(term, context.number)
        if bill is not None:
            retained = {
                "consultation_review": bill.consultation_review,
                "commission_review": bill.commission_review,
                "act_application": bill.act_application_review,
            }[context.purpose]
            if retained is not None and retained.context.memo_key(term) == key:
                return False
        job = self._repo.batch_job(self._review_request_key(key))
        return (
            job is not None
            and job.state == "failed"
            and job.submissions >= SECONDARY_BATCH_SUBMISSIONS
        )

    def _review_request_key(self, key: str) -> str:
        return "overflow:" + key if key in self._context_rejections else key

    def request_document_review(
        self, bill: Bill, context: ReviewContext
    ) -> DocumentReviewRecord | Waiting:
        if context.number != bill.number:
            raise ValueError("document review context belongs to a different bill")
        if (
            context.purpose == "consultation_review"
            and not self._options.consultation_reviews_enabled
        ) or (context.purpose == "commission_review" and not self.review_enabled(context.purpose)):
            return Waiting(self._clock.now())
        key = context.memo_key(bill.term)
        retained = {
            "consultation_review": bill.consultation_review,
            "commission_review": bill.commission_review,
            "act_application": bill.act_application_review,
        }[context.purpose]
        if retained is not None and retained.context.memo_key(bill.term) == key:
            self._remember_analysis(bill, key, retained)
            return _reused(retained)
        cached = self._memo.get(key)
        if cached is not None:
            record = DocumentReviewRecord.model_validate_json(cached)
            self._remember_analysis(bill, key, record)
            return _reused(record)
        model = self._options.batch_models.get(context.purpose, "")
        provider = provider_of(model)
        request_key = self._review_request_key(key)
        if request_key != key:
            model, provider = self._options.overflow_model, self._options.overflow_provider
        job = self._repo.batch_job(request_key)
        if job is not None and job.state != "failed":
            return Waiting(job.since)
        if job is not None and job.submissions >= SECONDARY_BATCH_SUBMISSIONS:
            raise RuntimeError("document review batch failed; submission limit reached")
        backend = self._batch_resolver(provider) if self._batch_resolver else self._batch
        if not model or backend is None:
            raise LlmUnavailableError("document review batch backend is unavailable")
        request = backend.prepare_request(
            BatchRequest(
                custom_id=request_key,
                term=bill.term,
                number=bill.number,
                question=DocumentReviewQuestion(kind=context.purpose, ctx=context),
                model=model,
            )
        )
        if (
            self._options.max_bill_cost_usd
            and request.estimated_cost_usd > self._options.max_bill_cost_usd
        ):
            self._ledger.stop_bill(
                number=bill.number,
                estimate_usd=request.estimated_cost_usd,
                limit_usd=self._options.max_bill_cost_usd,
                waiting="document review remains pending",
            )
            return Waiting(self._clock.now())
        if not self._ledger.reserve(request.estimated_cost_usd):
            self._ledger.stop("document review waits for the next run")
            return Waiting(self._clock.now())
        try:
            self._repo.save_batch_intent(
                BatchIntent.from_request(
                    request=request,
                    meta=DigestItemMeta(
                        memo_key=key,
                        review_context=context,
                        queued_at=self._clock.now(),
                        prompt_version=request.prompt_version,
                    ),
                    provider=provider,
                    created_at=self._clock.now(),
                )
            )
        except Exception:
            self._ledger.release(request.estimated_cost_usd)
            raise
        return Waiting(self._clock.now())

    def senate_amendments_document(
        self,
        info: PrintInfo,
        stage: Stage,
        *,
        titles: tuple[str, ...],
        passed_on: date | None = None,
    ) -> TextDocument | None:
        if (
            stage.date is None
            or stage.print_number != info.number
            or stage.position not in ("wniósł poprawki", "wniósł poprawkę")
        ):
            return None
        return resolve_senate_document(
            self._loader, info, adopted_on=stage.date, titles=titles, passed_on=passed_on
        )

    def _load_amendments_text(self, document: TextDocument) -> _Loaded:
        if document.kind != "senate_amendments":
            loaded = self._load_text(document, trim=False)
            if loaded.truncated:
                raise ValueError("Полный текст поправок комиссии не помещается в бюджет текста")
            return loaded
        if document.senate is not None and document.senate.scan:
            scan = self._loader.load_scan(document.url, cover_letter=False)
            if (
                scan is None
                or scan.sha256 != document.senate.sha256
                or scan.pages != document.senate.pages
                or scan.truncated
            ):
                raise ValueError("Полный скан постановления не прочитан или файл изменился")
            return _Loaded("", False, "scan", scan=scan)
        text = verified_senate_text(self._loader, document)
        budgeted = self._budget.apply(text, self._keywords.spans(text))
        if budgeted.truncated:
            raise ValueError("Полный текст поправок Сената не помещается в бюджет текста")
        return _Loaded(text, False, "pdf", whole=text)

    def amendments_context(
        self,
        bill: Bill,
        document: TextDocument,
        *,
        proposal: str | None = None,
        recommendation: TextDocument | None = None,
    ) -> AmendmentsContext | None:
        assert bill.analysis is not None
        loaded = self._load_amendments_text(document)
        if not loaded.text.strip() and loaded.scan is None:
            return None
        if self._too_expensive_to_digest(loaded):
            log.info(
                "%s: %s is over the per-document cost limit; told without a summary",
                bill.number,
                document.url,
            )
            return None
        return self._amendments_context_of(bill, document, loaded, proposal, recommendation)

    def _amendments_context_of(
        self,
        bill: Bill,
        document: TextDocument,
        loaded: _Loaded,
        proposal: str | None,
        recommendation: TextDocument | None,
    ) -> AmendmentsContext:
        assert bill.analysis is not None
        report = None
        if recommendation is not None:
            read = self._load_text(recommendation, trim=False)
            if read.text.strip() and not read.truncated:
                report = read.text
            else:
                log.info("%s: %s not read whole", bill.number, recommendation.url)
        return AmendmentsContext(
            number=bill.number,
            title=bill.summary.title,
            source_kind=document.kind,
            text=loaded.text,
            truncated=loaded.truncated,
            previous_summary=bill.analysis.analysis.summary,
            previous_key_changes=list(bill.analysis.analysis.key_changes),
            proposal=proposal,
            recommendation=report,
            scan=loaded.scan,
            source_check=document.senate if loaded.scan else None,
        )

    def summarize_amendments(
        self,
        bill: Bill,
        document: TextDocument,
        *,
        proposal: str | None = None,
        recommendation: TextDocument | None = None,
        may_wait: bool = False,
        context: AmendmentsContext | None = None,
    ) -> AmendmentsRecord | Waiting | None:
        """Scanned Senate resolutions require a batch answer with verified source evidence."""
        assert bill.analysis is not None, (
            "the tracker looks for amendments only of an analysed bill"
        )
        assert document.kind in AMENDMENT_SOURCES, f"not an amendments document: {document.kind}"
        ctx = context or self.amendments_context(
            bill, document, proposal=proposal, recommendation=recommendation
        )
        if ctx is None:
            return None
        key = _memo_key(bill, "amendments", ctx)
        record = self._ask_or_wait(
            bill,
            AmendmentsQuestion(ctx=ctx),
            key,
            DigestItemMeta(
                memo_key=key,
                source_kind=document.kind,
                source_url=document.url,
                amendment_source=ctx.source_check,
                scan_input=ctx.scan.scan_input if ctx.scan else None,
            ),
            AmendmentsRecord,
            lambda: self._llm.summarize_amendments(ctx),
            may_wait=may_wait,
        )
        if not isinstance(record, Waiting):
            if ctx.source_check is not None:
                validate_senate_scan(record.amendments.source_evidence, ctx.source_check)
            record.source_url = document.url
        return record

    def digest_supplement(
        self, bill: Bill, document: TextDocument, *, number: str, title: str, may_wait: bool = False
    ) -> SupplementRecord | Waiting:
        """What the document filed to the print says about the bill, against its current
        analysis. A document with no readable text (a scan) comes back as the bare record, which
        the reply still names and links. Only an outage propagates."""
        assert bill.analysis is not None, (
            "the tracker collects filed documents only of an analysed bill"
        )
        assert document.kind in SUPPLEMENT_SOURCES, f"not a filed document: {document.kind}"
        delivery_context = json.dumps(
            {
                "term": bill.term,
                "bill": bill.number,
                "number": number,
                "title": title,
                "document": document.model_dump(mode="json"),
                "summary": bill.analysis.analysis.summary,
                "changes": bill.analysis.analysis.key_changes,
                "observation": bill.observed_process.model_dump(mode="json")
                if bill.observed_process is not None
                else None,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        delivery_key = (
            "supplement-delivery:" + hashlib.sha256(delivery_context.encode()).hexdigest()
        )
        cached = self._memo.get(delivery_key)
        if bill.awaiting_batch_since is not None and cached is not None:
            delivered = _reused(SupplementRecord.model_validate_json(cached))
            delivered.number = number
            delivered.source_url = document.url
            self._remember_analysis(bill, delivery_key, delivered)
            return delivered
        loaded = self._load_text(document, trim=False)
        if loaded.scan is None and (loaded.source == "metadata_only" or not loaded.text.strip()):
            return self.bare_supplement(document, number=number, title=title)
        if self._too_expensive_to_digest(loaded):
            log.info(
                "%s: %s is over the per-document cost limit; told without a digest",
                bill.number,
                number,
            )
            return self.bare_supplement(document, number=number, title=title)
        ctx = SupplementContext(
            number=bill.number,
            title=bill.summary.title,
            document_title=title,
            source_kind=document.kind,
            text=loaded.text if loaded.scan is None else "",
            truncated=loaded.truncated,
            scan=loaded.scan,
            previous_summary=bill.analysis.analysis.summary,
            previous_key_changes=list(bill.analysis.analysis.key_changes),
        )
        key = _memo_key(bill, "supplement", ctx)
        record = self._ask_or_wait(
            bill,
            SupplementQuestion(ctx=ctx),
            key,
            DigestItemMeta(
                memo_key=key,
                delivery_key=delivery_key,
                source_kind=document.kind,
                title=title,
                source_url=document.url,
                document_number=number,
                scan_input=ctx.scan.scan_input if ctx.scan else None,
            ),
            SupplementRecord,
            lambda: self._llm.digest_supplement(ctx),
            may_wait=may_wait,
        )
        if not isinstance(record, Waiting):
            record.number = number
            record.source_url = document.url
        return record

    def compare_joint(
        self, bill: Bill, others: list[Bill], *, may_wait: bool = False
    ) -> JointRecord | Waiting | None:
        """How `bill` differs from the prints considered jointly with it, read off the channel's
        own description of each. None when nothing has been analysed to compare with.

        The descriptions and not the texts: each has been read once already, they are what the
        reader sees, and they cost about a cent. Only an outage propagates — a failed call leaves
        the reply as it was before comparisons existed.
        """
        assert bill.analysis is not None, "a joint reply candidate is listed only once analysed"
        described = [other for other in others if other.analysis is not None]
        if not described:
            return None
        ctx = JointContext(
            subject=_describe_for_comparison(bill),
            others=[_describe_for_comparison(other) for other in described],
        )
        key = _memo_key(bill, "joint", ctx)
        return self._ask_or_wait(
            bill,
            JointQuestion(ctx=ctx),
            key,
            DigestItemMeta(memo_key=key, compared_with=[other.number for other in described]),
            JointRecord,
            lambda: self._llm.compare_joint(ctx),
            may_wait=may_wait,
        )

    def name_bill(self, bill: Bill, *, may_wait: bool = False) -> NameRecord | Waiting:
        """A short name for a bill analysed before names were asked with the analysis."""
        assert bill.analysis is not None, "a bill is named only from its analysis"
        answer = bill.analysis.analysis
        ctx = NameContext(
            number=bill.number,
            title=bill.summary.title,
            summary=answer.summary,
            key_changes=list(answer.key_changes),
            language=answer.mnemonic_language,
        )
        key = self._name_request_key(bill, ctx)
        return self._ask_or_wait(
            bill,
            NameQuestion(ctx=ctx),
            key,
            DigestItemMeta(memo_key=key),
            NameRecord,
            lambda: self._llm.name_bill(ctx),
            may_wait=may_wait,
        )

    def _name_request_key(self, bill: Bill, context: NameContext) -> str:
        """Only known original owner terms can locate a retained RCL name question."""
        key = _memo_key(bill, "name", context)
        if not is_rcl_number(bill.number) or key in self._memo:
            return key
        terms = dict.fromkeys((bill.term, *self._repo.computation_owner_terms(bill.number, "name")))
        results = [
            result
            for term in terms
            for result in reversed(self._repo.list_computation_results(term, bill.number))
            if result.purpose == "name"
        ]
        for term in terms:
            original = bill.model_copy(
                update={"summary": bill.summary.model_copy(update={"term": term})}
            )
            # NameContext has no source text, so the legacy hash preserves every name input.
            candidates = dict.fromkeys(
                (
                    _memo_key(original, "name", context),
                    _memo_key(original, "name", context, legacy=True),
                )
            )
            for candidate in candidates:
                if candidate in self._memo:
                    return candidate
                for result in results:
                    if result.custom_id == candidate and isinstance(result.record, NameRecord):
                        self._memo[candidate] = result.record.model_dump_json()
                        return candidate
                if self._repo.batch_job(candidate) is not None:
                    return candidate
        return key

    def _too_expensive_to_digest(self, loaded: _Loaded) -> bool:
        """The per-bill cost limit applies to a filed document too, and here it is the whole
        answer rather than a reason to skip the bill: a reader who is told what the document is
        and where it lies has lost little. A bill's own text is worth its price; the assessment
        of it is worth a bounded one."""
        limit = self._options.max_bill_cost_usd
        price = self._options.supplement_input_price_usd_per_mtok
        if not limit or price is None:
            return False
        if self._options.require_batch:
            price *= 0.5
        return loaded.estimate(price) > limit

    def bare_supplement(
        self, document: TextDocument, *, number: str, title: str
    ) -> SupplementRecord:
        """The document named and linked, with nothing read: what a failed digest degrades to."""
        return SupplementRecord(
            number=number, title=title, source_kind=document.kind, source_url=document.url
        )

    def _prepare(
        self,
        bill: Bill,
        located: LocatedText,
        *,
        previous: AnalysisRecord | None,
        previous_document: TextDocument | None = None,
        cost_guard: bool = True,
        triage: bool = True,
        submit_batch: bool = False,
        purpose: Literal["analysis", "reanalysis", "explanation"] | None = None,
    ) -> _Prepared:
        """Load the text and ask the model. Network only: safe to run for several bills at once.

        The per-bill cost guard never refuses a re-analysis, only shortens it: a re-analysis
        reads a new version of a text that already passed the guard, and a bill whose new text
        we decline to read would keep a card describing the old one.

        `submit_batch` builds a request; only `_enqueue` on the calling thread persists it.
        """
        document = located.document
        try:
            loaded = self._load_text(document)
        except (OrkaUnreachableError, DocumentSelectionError) as exc:
            if previous is None:
                raise
            # A bill that already has an analysis keeps it, the same as for any other document
            # that could not be read: the card goes on describing the text it was written from.
            return _Prepared(
                bill,
                located,
                "",
                "metadata_only",
                previous,
                first=False,
                unreadable_reason=str(exc) or type(exc).__name__,
            )
        if loaded.document is not None:
            document = loaded.document
            located = located.model_copy(update={"document": document})
        text, truncated, source = loaded.text, loaded.truncated, loaded.source
        if purpose == "explanation" and source not in FULL_TEXT_SOURCES:
            raise ValueError("Текст проекта пока недоступен; повторите /explain после загрузки")
        if previous is not None and source not in FULL_TEXT_SOURCES:
            return _Prepared(
                bill,
                located,
                text,
                source,
                previous,
                first=False,
                unreadable_reason=f"no usable full text (source: {source})",
            )
        digest = loaded.digest
        law = loaded.law
        if previous is not None and (
            (
                document is not None
                and self._text_identity is not None
                and not document.amendment_urls
                and self._text_identity.same_text(bill, previous, document)
            )
            or (digest is not None and digest == previous.text_sha256)
            or (law is not None and law == self._previous_law(previous, previous_document))
        ):
            return self._unchanged(bill, located, loaded, previous)
        if previous is not None and self._ledger.exhausted:
            # The per-bill guard does not apply to a re-analysis, but the run's budget does: a
            # bill whose text is not read now keeps the analysis and the `source_url` it had, so
            # the next run sees the same new document and reads it then.
            self._ledger.stop("the new text(s) wait for the next run")
            return _Prepared(bill, located, text, source, previous, first=False, deferred=True)
        if (
            previous is not None
            and not (document and document.amendment_urls)
            and self._changes_settled(bill, loaded, previous, previous_document)
        ):
            return self._unchanged(bill, located, loaded, previous)
        meta = located.summary or bill.summary
        triaged: TriageRecord | None = None
        triage_memo_key: str | None = None
        if previous is None and triage and self._worth_triaging(loaded):
            triaged, rejection, triage_memo_key = self._triage_verdict(
                bill, meta, text, loaded.scan
            )
            if rejection is not None:
                return _Prepared(
                    bill,
                    located,
                    text,
                    source,
                    rejection,
                    first=True,
                    triage=triaged,
                    triage_memo_key=triage_memo_key,
                )
        ctx = BillContext(
            number=bill.number,
            title=meta.title or bill.summary.title,
            description=meta.description or bill.summary.description,
            document_date=meta.document_date or bill.summary.document_date,
            applicant_type=meta.applicant_type,
            text=text,
            truncated=truncated,
            text_source=source,
            scan=loaded.scan,
            source_kind=document.kind if document and source in FULL_TEXT_SOURCES else "metadata",
            support_applicability_unknown=(
                "support_applicability_unknown" in loaded.selection.limitations
            ),
            previous_summary=previous.analysis.summary if previous else None,
            previous_key_changes=list(previous.analysis.key_changes) if previous else [],
        )
        original_ctx = ctx
        first = previous is None
        purpose = purpose or ("analysis" if first else "reanalysis")
        generation = 0 if purpose == "explanation" else bill.analysis_generation

        def to_overflow(reason: str) -> tuple[_FittedContext, str]:
            log.info("%s: %s; using the overflow model", bill.number, reason)
            fitted = self._fit_to_budget(
                original_ctx, loaded, first=first, cost_guard=cost_guard, batch=True, overflow=True
            )
            return fitted, _memo_key(bill, purpose, fitted.ctx)

        overflow = self._needs_overflow(ctx, batch=submit_batch)
        if overflow:
            submit_batch = True
            fitted, key = to_overflow("input exceeds the primary model's context")
        else:
            fitted = self._fit_to_budget(
                ctx, loaded, first=first, batch=submit_batch, cost_guard=cost_guard
            )
            key = _memo_key(bill, purpose, fitted.ctx)
            custom_id = hashlib.sha256(f"{key}:{generation}".encode()).hexdigest()
            if self._overflow_batch is not None and custom_id in self._context_rejections:
                overflow = submit_batch = True
                fitted, key = to_overflow("the primary batch rejected the context")
        ctx = fitted.ctx
        cached = self._memo.get(key) if cost_guard else None
        record: AnalysisRecord | None = None
        if cached is None and not submit_batch:
            try:
                record = self._llm.analyze(ctx)
            except LlmContextExceededError:
                if self._overflow_batch is None:
                    raise
                overflow = submit_batch = True
                fitted, key = to_overflow("the primary model rejected the context")
                ctx = fitted.ctx
                cached = self._memo.get(key) if cost_guard else None
        backend = self._overflow_batch if overflow else self._batch
        if cached is None and submit_batch and backend is None:
            raise LlmUnavailableError(
                "batch backend is unavailable for the selected analysis model"
            )
        if cached is None and submit_batch and backend is not None:
            request_identity = f"{key}:{generation}"
            if overflow:
                request_identity += f":overflow:{self._options.overflow_model}"
            custom_id = hashlib.sha256(request_identity.encode()).hexdigest()
            if overflow and custom_id in self._context_rejections:
                if not first:
                    return _Prepared(
                        bill,
                        located,
                        text,
                        source,
                        previous,
                        first=False,
                        unreadable_reason="both primary and overflow models rejected the context",
                    )
                raise LlmContextExceededError("overflow batch also rejected the input context")
            return _Prepared(
                bill,
                located,
                text,
                source,
                None,
                first=previous is None,
                triage=triaged,
                queued=True,
                batch_provider=(
                    self._options.overflow_provider if overflow else self._options.batch_provider
                ),
                batch_request=backend.prepare_request(
                    BatchRequest(
                        custom_id=custom_id,
                        term=bill.term,
                        number=bill.number,
                        question=AnalysisQuestion(kind=purpose, ctx=ctx),
                        model=self._options.overflow_model if overflow else "",
                    )
                ),
                batch_meta=BatchItemMeta(
                    overflow=overflow,
                    input_chars=len(ctx.text),
                    truncated=ctx.truncated,
                    text_source=source,
                    source_kind=ctx.source_kind,
                    source_url=document.url if document else None,
                    selection=fitted.input_selection(loaded.selection),
                    revision=previous.revision + 1 if previous else 1,
                    text_sha256=digest,
                    law_sha256=law,
                    prompt_version=self._options.prompt_version,
                    generation=bill.analysis_generation,
                    memo_key=key,
                    located=located,
                    text=text,
                ),
                memo_key=key,
                triage_memo_key=triage_memo_key,
            )
        if cached is None:
            assert record is not None
            self._ledger.charge(record, number=bill.number, kind=purpose)
        else:
            record = _reused(AnalysisRecord.model_validate_json(cached))
        input_selection = fitted.input_selection(loaded.selection)
        if cached is not None:
            input_selection = _reused_selection(input_selection, record.selection)
        record.selection = input_selection
        record.source_url = document.url if document else None
        record.source_kind = ctx.source_kind
        record.revision = previous.revision + 1 if previous else 1
        record.text_sha256 = digest
        record.law_sha256 = law
        record.source_checked_at = self._clock.now()
        return _Prepared(
            bill,
            located,
            text,
            source,
            record,
            first=previous is None,
            triage=triaged,
            memo_key=key,
            triage_memo_key=triage_memo_key,
        )

    def _unchanged(
        self, bill: Bill, located: LocatedText, loaded: _Loaded, previous: AnalysisRecord
    ) -> _Prepared:
        """The analysis stands for the new document: only the pointer and its digests move."""
        document = located.document
        assert document is not None, "a text digest comes only from a document that was read"
        selection = _reused_selection(
            selection_for_input(loaded.selection, loaded.text).without_text(), previous.selection
        )
        pointer = previous.model_copy(
            update={
                "source_url": document.url,
                "source_kind": document.kind,
                "source_checked_at": self._clock.now(),
                "selection": selection,
                "text_sha256": loaded.digest or previous.text_sha256,
                "law_sha256": loaded.law or previous.law_sha256,
            }
        )
        return _Prepared(
            bill, located, loaded.text, loaded.source, pointer, first=False, unchanged=True
        )

    def _changes_settled(
        self,
        bill: Bill,
        loaded: _Loaded,
        previous: AnalysisRecord,
        previous_document: TextDocument | None,
    ) -> bool:
        """Noise alone keeps the analysis free; a small change needs the triage model's sure no."""
        ratio_cap = self._options.change_review_max_ratio
        source = _source_of(previous)
        if not ratio_cap or loaded.whole is None or source is None:
            return False
        # The caller can recover the old package, but its project may have advanced already.
        document = (
            previous_document
            if previous_document and previous_document.url == source.url
            else source
        )
        try:
            baseline = self._load_text(document)
            if not baseline.matches(previous):
                return False
            before = self._load_text(source).whole if document.extra_urls else baseline.whole
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.warning("%s: the analysed text not re-read (%s)", source.url, exc)
            return False
        diff = diff_laws(before, loaded.whole) if before is not None else None
        if diff is None:
            return False
        if not diff.hunks:
            log.info(
                "%s: technical equivalence (%s)",
                bill.number,
                ", ".join(sorted({proof.rule for proof in diff.evidence})) or "word_layout",
            )
            return True
        if diff.ratio > ratio_cap or diff.chars > self._options.change_review_max_chars:
            return False
        ctx = ChangeContext(
            number=bill.number,
            title=bill.summary.title,
            summary=previous.analysis.summary,
            key_changes=list(previous.analysis.key_changes),
            passages=[
                ChangedPassage(old=h.old, new=h.new, before=h.before, after=h.after)
                for h in diff.hunks
            ],
            changed_words=diff.changed_words,
            total_words=diff.total_words,
            old_law=diff.old_law,
            new_law=diff.new_law,
        )
        try:
            reviewed = self._llm.review_change(ctx)
        except LlmUnavailableError:
            raise
        except Exception as exc:
            log.warning("%s: changes not reviewed (%s); reading the text", bill.number, exc)
            return False
        self._ledger.charge(reviewed, number=bill.number, kind="change_review")
        settled = reviewed.settles(min_confidence=self._options.triage_min_confidence)
        log.info(
            "%s: %d changed word(s) of %d %s (%.2f): %s",
            bill.number,
            diff.changed_words,
            diff.total_words,
            "leave the analysis standing" if settled else "need a reading",
            reviewed.review.confidence,
            reviewed.review.rationale,
        )
        return settled

    def _previous_law(self, previous: AnalysisRecord, document: TextDocument | None) -> str | None:
        """The stored act's digest; an older record's comes from its file if that hashes alike."""
        if previous.law_sha256 is not None or document is None or previous.text_sha256 is None:
            return previous.law_sha256
        try:
            loaded = self._load_text(document)
        except Exception as exc:
            # Best effort: an old file out of reach costs a full reading, never the phase.
            log.warning("%s: the analysed text not re-read (%s)", document.url, exc)
            return None
        return loaded.law if loaded.digest == previous.text_sha256 else None

    def _needs_overflow(self, ctx: BillContext, *, batch: bool) -> bool:
        ceiling = self._options.batch_max_input_tokens if batch else self._options.max_input_tokens
        if ceiling is None or self._overflow_batch is None:
            return False
        counter: AnalysisBackend | BatchBackend = (
            self._batch if batch and self._batch is not None else self._llm
        )
        if ctx.scan is not None:
            tokens = ctx.scan.pages * TOKENS_PER_SCANNED_PAGE
        else:
            counted = counter.count_input_tokens(ctx)
            tokens = counted if counted is not None else int(len(ctx.text) / CHARS_PER_TOKEN)
        return tokens > ceiling

    def _fit_to_budget(
        self,
        ctx: BillContext,
        loaded: _Loaded,
        *,
        first: bool,
        batch: bool = False,
        cost_guard: bool = True,
        overflow: bool = False,
    ) -> _FittedContext:
        """Fit the original context and its copy map to the model's cost and input limits."""
        projection = loaded.projection if ctx.text == loaded.text else None
        fitted = _FittedContext(ctx, projection)
        limit = self._options.max_bill_cost_usd if cost_guard else 0.0
        price = (
            self._options.batch_input_price_usd_per_mtok
            if batch
            else self._options.input_price_usd_per_mtok
        )
        ceiling = self._options.batch_max_input_tokens if batch else self._options.max_input_tokens
        counter: AnalysisBackend | BatchBackend = (
            self._batch if batch and self._batch is not None else self._llm
        )
        if overflow:
            assert self._overflow_batch is not None
            counter = self._overflow_batch
            price = self._options.overflow_input_price_usd_per_mtok
            ceiling = self._options.overflow_max_input_tokens
        if batch and price is not None:
            price *= 0.5
        if not limit:
            price = None
        if price is None and ceiling is None:
            return fitted
        tokens = None if loaded.scan is not None else counter.count_input_tokens(ctx)
        if tokens is None:
            cost = loaded.estimate(price) if price is not None else 0.0
            if cost > limit and first:
                raise TooExpensiveError(cost, limit, measure=loaded.measure(None))
            return fitted
        cost = input_cost(tokens, price) if price is not None else 0.0
        over_by = max(cost / limit if limit else 0.0, tokens / ceiling if ceiling else 0.0)
        if over_by <= 1:
            return fitted
        shorter = self._shorten(ctx.text, over_by=over_by)
        if shorter is None:
            if not first:
                return fitted
            raise TooExpensiveError(cost, limit, measure=loaded.measure(tokens))
        reduced = ctx.model_copy(update={"text": shorter.text, "truncated": True})
        projection = shorter.compose(projection) if projection is not None else None
        counted = counter.count_input_tokens(reduced)
        if ceiling is not None and counted is not None and counted > ceiling:
            again = self._shorten(shorter.text, over_by=counted / ceiling)
            if again is not None:
                reduced = reduced.model_copy(update={"text": again.text})
                projection = again.compose(projection) if projection is not None else None
                counted = counter.count_input_tokens(reduced)
        log.info(
            "%s: %d tokens at %s, over the limit %s or the ceiling %s; sending %d of %d chars",
            ctx.number,
            tokens,
            format_usd(cost),
            format_usd(limit),
            ceiling,
            len(reduced.text),
            len(ctx.text),
        )
        if (
            first
            and counted is not None
            and price is not None
            and input_cost(counted, price) > limit
        ):
            raise TooExpensiveError(
                input_cost(counted, price),
                limit,
                measure=f"{counted} tokens after trimming to {len(reduced.text)} chars",
            )
        return _FittedContext(reduced, projection)

    def _shorten(self, text: str, *, over_by: float) -> TextProjection | None:
        """Trace the same keyword cut, leaving room for the prompt and schema."""
        target = int(len(text) / over_by * _FIT_MARGIN)
        if target < _FIT_MIN_CHARS:
            return None
        return excerpt_projection(
            text, self._keywords.spans(text), head_chars=target // 4, max_chars=target
        )

    def _worth_triaging(self, loaded: _Loaded) -> bool:
        """Whether the cheap pass has something to judge.

        `triage_min_chars` measures when a full analysis would be dear enough to be worth it. A
        scan is worth it whatever its text says: its text is the transmittal letter, so the gate
        used to send the term's 317 scans — 7,763 pages, $62 on Opus — straight to Opus.
        """
        if loaded.scan is not None:
            return True
        long_enough = len(loaded.text) >= self._options.triage_min_chars
        return loaded.source in FULL_TEXT_SOURCES and long_enough

    def _triage_verdict(
        self, bill: Bill, meta: ProcessSummary, text: str, scan: ScannedDocument | None = None
    ) -> tuple[TriageRecord | None, AnalysisRecord | None, str | None]:
        """Ask the cheap model about excerpts, or about the first pages of a scan; a confident
        "no" becomes the final record.

        The rejection becomes a non-relevant analysis record whose `text_source="excerpts"` says
        how it was decided. A scan is shown `triage_scan_pages` pages, so the cheap call costs the
        same whatever the document's length: $0.044 on Sonnet, breaking even at a 7% rejection
        rate. The prompt says how many pages of how many are attached and an unsure verdict passes
        the bill on, so only a confident wrong "no" loses one.
        """
        if self._triage is None:
            return None, None, None
        window = (
            self._loader.first_pages(scan, self._options.triage_scan_pages)
            if scan is not None
            else None
        )
        shown = window or scan
        ctx = TriageContext(
            number=bill.number,
            title=meta.title or bill.summary.title,
            description=meta.description or bill.summary.description,
            applicant_type=meta.applicant_type,
            excerpts="" if shown is not None else excerpts(text, self._triage.spans(text)),
            text_chars=len(text),
            scan=shown,
        )
        key = _memo_key(bill, "triage", ctx)
        cached = self._memo.get(key)
        if cached is None:
            verdict = self._llm.triage(ctx)
            verdict.scan_input = shown.scan_input if shown else None
            self._ledger.charge(verdict, number=bill.number, kind="triage")
        else:
            verdict = _reused(TriageRecord.model_validate_json(cached))
        if not verdict.rejects(min_confidence=self._options.triage_min_confidence):
            log.info(
                "%s passes triage (%s, %.2f): full analysis",
                bill.number,
                "relevant" if verdict.triage.affects_foreigners else "unsure",
                verdict.triage.confidence,
            )
            return verdict, None, key
        log.info(
            "%s rejected by triage (%.2f): %s",
            bill.number,
            verdict.triage.confidence,
            verdict.triage.rationale,
        )
        return (
            verdict,
            AnalysisRecord(
                analysis=Analysis(
                    relevant=False,
                    score=1,
                    category=Category.NONE,
                    summary=verdict.triage.rationale,
                    practical_impact="",
                    confidence=verdict.triage.confidence,
                    rationale=verdict.triage.rationale,
                ),
                model=verdict.model,
                prompt_version=verdict.prompt_version,
                input_chars=len(ctx.excerpts),
                truncated=True,
                text_source="excerpts",
                selection=(
                    DocumentSelection(scan_input=verdict.scan_input)
                    if shown
                    else DocumentSelection()
                ),
                created_at=self._clock.now(),
                input_tokens=verdict.input_tokens,
                output_tokens=verdict.output_tokens,
            ),
            key,
        )

    def _enqueue(self, bill: Bill, prepared: _Prepared) -> bool:
        """Hand a queued request to the batch and mark the bill so nothing re-submits it while
        it is in flight. Calling thread only, like `_persist`."""
        assert prepared.batch_request is not None and prepared.batch_meta is not None, (
            "a queued _Prepared carries its batch request and meta"
        )
        if len((prepared.batch_request.payload_json or "").encode()) > 100_000_000:
            raise ValueError("one batch request exceeds the 100 MB payload limit")
        if not self._ledger.reserve(prepared.batch_request.estimated_cost_usd):
            self._ledger.stop("the batch analysis waits for the next run")
            return False
        if prepared.triage_memo_key is not None and prepared.triage is not None:
            self._remember_analysis(bill, prepared.triage_memo_key, prepared.triage)
        with self._repo.atomic():
            self._repo.save_batch_intent(
                BatchIntent.from_request(
                    request=prepared.batch_request,
                    meta=prepared.batch_meta.model_copy(
                        update={
                            "prompt_version": prepared.batch_request.prompt_version,
                        }
                    ),
                    provider=prepared.batch_provider or self._options.batch_provider or "anthropic",
                    created_at=self._clock.now(),
                )
            )
            self._repo.set_status(bill.term, bill.number, BillStatus.BATCH_PENDING)
        return True

    def _persist(self, prepared: _Prepared) -> AnalysisRecord:
        """Write one prepared analysis down (stages, authors, the record). Calling thread only."""
        assert prepared.record is not None, "a queued _Prepared must not reach _persist"
        bill, located = prepared.bill, prepared.located
        if prepared.memo_key is not None:
            self._remember_analysis(bill, prepared.memo_key, prepared.record)
        if prepared.triage_memo_key is not None and prepared.triage is not None:
            self._remember_analysis(bill, prepared.triage_memo_key, prepared.triage)
        document = located.document
        authors = None
        # A scanned print is still signed on its first page: the letter is the one part of it
        # that has a text layer, so the signatures survive an analysis made without the text.
        if (
            self._authors is not None
            and document is not None
            and document.kind == "print"
            and prepared.text
        ):
            described = (
                bill
                if located.summary is None
                else bill.model_copy(update={"summary": located.summary})
            )
            authors = self._authors.resolve(described, prepared.text)
        with self._repo.atomic():
            if prepared.first and located.summary is not None and not prepared.from_batch:
                summary = located.summary
                if summary.applicant_type is ApplicantType.UNKNOWN:
                    summary = summary.model_copy(update={"applicant": bill.summary.applicant_type})
                self._repo.upsert_summary(summary, now=self._clock.now())
            if authors is not None:
                self._repo.save_authors(bill.term, bill.number, authors)
            apply_analysis(
                self._repo,
                bill.term,
                bill.number,
                prepared.record,
                now=self._clock.now(),
                reason="batch_result" if prepared.from_batch else "memo_reuse",
            )
            if prepared.first and located.stages is not None:
                self._repo.save_stages(
                    bill.term, bill.number, located.stages, stage_fingerprint(located.stages)
                )
                self._repo.save_observed_closure(
                    bill.term, bill.number, (located.summary or bill.summary).closure_date
                )
                fresh = self._repo.get(bill.term, bill.number)
                assert fresh is not None, "the row was written in this transaction"
                self._repo.save_observed_process(
                    bill.term,
                    bill.number,
                    observe(fresh, closure_date=(located.summary or bill.summary).closure_date),
                )
        return prepared.record

    def _remember_analysis(self, bill: Bill, key: str, record: BaseModel) -> None:
        encoded = record.model_dump_json()
        self._repo.save_analysis_memo(
            key, encoded, term=bill.term, number=bill.number, used_at=self._clock.now()
        )
        self._memo.setdefault(key, encoded)

    def _load_text(self, document: TextDocument | None, *, trim: bool = True) -> _Loaded:
        """Trimmed, budgeted text of the document — or its pages, when the file has no text in it.

        Much of what the Sejm publishes is signed paper filed as images, whose text layer is
        empty or holds only the transmittal letter. Such a file goes to the model as pages
        (`scan`); the extracted text comes back either way, so the signatures are still resolved.

        An outage propagates, and so does orka refusing the file: an analysis of the metadata must
        never be what a bill with a text ends up with. `trim=False` keeps the whole text.
        """
        if document is None:
            return _Loaded("", False, "metadata_only")
        try:
            document, file = self._loader.select_document(document)
        except ServiceUnavailableError:
            raise
        except OrkaUnreachableError as exc:
            if not exc.file_is_missing:
                raise
            log.warning("%s is not there (%s); using metadata only", document.url, exc)
            return _Loaded("", False, "metadata_only")
        except Exception as exc:
            raise DocumentSelectionError(
                "unsupported" if isinstance(exc, ValueError) else "waiting",
                f"{document.url}: {type(exc).__name__}: {exc}",
            ) from exc
        if file.selection.state in ("waiting", "ambiguous", "unsupported"):
            raise DocumentSelectionError(file.selection.state, "; ".join(file.selection.rejected))
        text = file.text
        if text is None or not carries_the_document(
            text, min_chars=MIN_TEXT_CHARS, pages=file.pages
        ):
            if file.pages > 0 or not text:
                return self._load_scan(document, text or "")
            # A format with no pages to fall back on (Word, an archive): whatever we could not
            # find the document in this text, the text is all there will ever be, and a file of
            # this length is not a covering letter.
            log.info("%s: no pages to read; the text is taken as it is", document.url)
        kind = document_kind(text)
        if kind in APPENDIX_KINDS:
            # The file the name pointed at turned out to be published beside the bill, not to be
            # it: a compliance table, a consultation report, a draft regulation. Analysing it
            # would describe the wrong document with every appearance of describing the right
            # one. A print opening with its covering letter is not this case (`letter` is not an
            # appendix kind), and neither is a layout we simply do not recognise.
            log.warning(
                "%s opens as a %s, not as the bill; using metadata only", document.url, kind
            )
            return _Loaded("", False, "metadata_only")
        if not trim:
            budgeted = self._budget.apply(text, self._keywords.spans(text))
            return _Loaded(
                budgeted.text,
                budgeted.truncated,
                "pdf",
                law=law_digest(text),
                whole=text,
                document=document,
                selection=file.selection,
                projection=(
                    budgeted.projection
                    if text == PAGE_BREAK.join(part.text for part in file.selection.parts)
                    else None
                ),
            )
        trimmed = trim_print(text)
        amendments = tuple(part for part in file.selection.parts if part.role == "self_amendment")
        combined = append_self_amendments(trimmed.text, amendments)
        # Said whether anything was dropped or not: a text that goes in whole is the expensive
        # case, and it was the silent one — the package that cost $1.63 logged nothing at all.
        log.info(
            "%s: %d chars, sending %d (dropped %s)",
            document.url,
            len(text),
            len(trimmed.text),
            ", ".join(f"{d.name} {d.chars}" for d in trimmed.dropped) or "nothing",
        )
        budgeted = self._budget.apply(combined, self._keywords.spans(combined))
        projection = None
        if amendments and trimmed.projection is not None and budgeted.projection is not None:
            amended = amended_input_projection(file.selection, trimmed.projection, combined)
            if amended is not None:
                projection = budgeted.projection.compose(amended)
        if (
            not amendments
            and trimmed.projection is not None
            and budgeted.projection is not None
            and text == PAGE_BREAK.join(part.text for part in file.selection.parts)
        ):
            projection = budgeted.projection.compose(trimmed.projection)
        source: TextSource = "documents" if document.kind == "rcl" else "pdf"
        return _Loaded(
            budgeted.text,
            budgeted.truncated or any(part.losses for part in file.selection.parts),
            source,
            law=None if amendments else law_digest(trimmed.text),
            whole=combined,
            document=document,
            selection=file.selection,
            projection=projection,
        )

    def _load_scan(self, document: TextDocument, text: str) -> _Loaded:
        """The document as pages, when its file carries no text of its own; metadata when it has
        no pages either (a Word file, an archive, a PDF too big for the model to take)."""
        scan = self._loader.load_scan(document.url, cover_letter=has_cover_letter(text))
        if scan is None:
            log.info(
                "%s: %d chars, none of them the document itself; using metadata only",
                document.url,
                len(text),
            )
            return _Loaded(text, False, "metadata_only")
        log.info(
            "%s: no text, reading %d of %d page(s) as images",
            document.url,
            scan.pages,
            scan.of_pages,
        )
        selection = DocumentSelection(
            belonging=document.version_basis[:1],
            scan_input=scan.scan_input,
            parts=(
                DocumentPart(
                    url=document.url,
                    sha256=scan.sha256,
                    pages=scan.of_pages,
                    page_start=scan.cover_letter_pages + 1,
                    page_end=scan.of_pages,
                    losses=("text unavailable; sent as PDF pages",),
                ),
            ),
        )
        return _Loaded(text, scan.truncated, "scan", scan, document=document, selection=selection)


def _submission_groups(intents: list[BatchIntent]) -> list[list[BatchIntent]]:
    groups: list[list[BatchIntent]] = []
    current: list[BatchIntent] = []
    size = 0
    for intent in intents:
        payload_size = len(
            (intent.request.payload_json or intent.request.model_dump_json()).encode()
        )
        if payload_size > 100_000_000:
            raise BatchNotSubmittedError("one batch request exceeds the 100 MB payload limit")
        if current and (
            len(current) >= 100
            or size + payload_size > 100_000_000
            or current[0].request.call_kind != intent.request.call_kind
            or current[0].request.model != intent.request.model
        ):
            groups.append(current)
            current, size = [], 0
        current.append(intent)
        size += payload_size + 2
    if current:
        groups.append(current)
    return groups
