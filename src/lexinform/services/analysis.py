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
from datetime import datetime

from pydantic import BaseModel

from lexinform.concurrency import fan_out
from lexinform.errors import BatchNotSubmittedError, OrkaUnreachableError, ServiceUnavailableError
from lexinform.keywords import KeywordPrefilter
from lexinform.models import (
    AMENDMENT_SOURCES,
    FULL_TEXT_SOURCES,
    SUPPLEMENT_SOURCES,
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
    BillStatus,
    CallKind,
    Category,
    JointBillDescription,
    JointContext,
    JointRecord,
    LlmBatch,
    LlmBatchItem,
    LlmCall,
    LocatedText,
    ProcessSummary,
    ScannedDocument,
    SupplementContext,
    SupplementRecord,
    TextDocument,
    TextSource,
    TokenUsage,
    TriageContext,
    TriageRecord,
    add_usage,
    observe,
    stage_fingerprint,
)
from lexinform.ports import AuthorsResolver, BatchBackend, BillRepository, Clock, LlmAnalyzer
from lexinform.ports import TextSource as TextSourcePort
from lexinform.pricing import (
    estimate_input_cost,
    estimate_scan_cost,
    format_usd,
    input_cost,
)
from lexinform.sections import (
    APPENDIX_KINDS,
    TextBudget,
    carries_the_document,
    document_kind,
    excerpts,
    has_cover_letter,
    trim_print,
)
from lexinform.services.cost import CostLedger
from lexinform.services.documents import MIN_TEXT_CHARS, TextLoader
from lexinform.services.joint import primary_of, revive_prefilter_skips

log = logging.getLogger(__name__)

_FIT_MARGIN = 0.9
"""How much of the cost limit a shortened text aims at, the rest being the prompt and the schema.

The counted input covers the system prompt, the structured-output schema and the bill's metadata
as well as its text, so scaling the characters by the overshoot alone would still land a little
over. Nine tenths clears the ~2k tokens of prefix with room to spare and costs one re-count.
"""

_FIT_MIN_CHARS = 20_000
"""Below this a shortened text is not a document any more, and refusing is the honest answer."""


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
        tokens: dict[str, TokenUsage] = {}
        for used in (self.triage, self.record):
            if used is not None:
                add_usage(tokens, used)
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
    `triage_min_chars` is the length from which the cheap pass pays (a scan is triaged whatever
    its text). `channel_id`: a print whose group already holds a card there skips that pass.
    `submit_batches` files an analysis to the batch instead of calling the model (collecting one
    never turns on it); `batch_provider` names who a queued request is filed under.
    """

    max_attempts: int = 3
    workers: int = 1
    input_price_usd_per_mtok: float | None = None
    batch_input_price_usd_per_mtok: float | None = None
    prompt_version: str = ""
    max_bill_cost_usd: float = 0.0
    max_run_cost_usd: float = 0.0
    triage_min_chars: int = 20_000
    triage_min_confidence: float = 0.8
    triage_scan_pages: int = 8
    channel_id: str | None = None
    batch_provider: BatchProvider | None = None
    submit_batches: bool = False


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

    @property
    def digest(self) -> str | None:
        """What says this is the same document as last time: the normalised text, or the file."""
        if self.scan is not None:
            return self.scan.sha256
        return text_digest(self.text) if self.source in FULL_TEXT_SOURCES else None

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
class _Prepared:
    """Everything an analysis needs to be written down: produced without touching the database,
    so several bills can be prepared at the same time.

    `first` marks a first analysis, which seeds the stages. Four flags say the model was not
    asked: `unchanged` (a re-analysis found the analysed text under a new URL), `unreadable` (the
    new document could not be read — an analysis of the metadata must never replace one of a
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
    unreadable: bool = False
    deferred: bool = False
    queued: bool = False
    batch_request: BatchRequest | None = None
    batch_meta: BatchItemMeta | None = None
    memo_key: str | None = None
    triage_memo_key: str | None = None


_PAGE_NUMBER_LINE = re.compile(r"^\s*[–\-—]?\s*\d{1,4}\s*[–\-—]?\s*$", re.MULTILINE)
_WHITESPACE = re.compile(r"\s+")


def _describe_for_comparison(bill: Bill) -> JointBillDescription:
    """One bill of a jointly considered group as the channel describes it today."""
    assert bill.analysis is not None
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
    """SHA-256 of the text with page numbers and whitespace differences ignored, so the same
    bill text rendered by another layout (a republished file, a re-dated print) hashes alike."""
    normalised = _WHITESPACE.sub(" ", _PAGE_NUMBER_LINE.sub("", text)).strip()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _memo_key(bill: Bill, purpose: str, context: BaseModel) -> str:
    data = context.model_dump(mode="json", exclude={"scan", "text"})
    payload = context.model_dump()
    text = str(payload.get("text", ""))
    data["text_sha256"] = text_digest(text)
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
        authors: AuthorsResolver | None = None,
        keywords: KeywordPrefilter | None = None,
        triage: KeywordPrefilter | None = None,
        batch: BatchBackend | None = None,
        batch_resolver: Callable[[BatchProvider], BatchBackend | None] | None = None,
    ) -> None:
        self._repo = repo
        self._texts = texts
        self._loader = loader
        self._llm = llm
        self._clock = clock
        self._options = options
        self._budget = text_budget
        self._authors = authors
        self._keywords = keywords or KeywordPrefilter()
        self._triage = triage
        self._batch = batch
        self._batch_resolver = batch_resolver
        self._dry_run = False
        self._ledger = CostLedger(max_run_usd=options.max_run_cost_usd)
        self._memo = repo.load_analysis_memo()

    def start_run(self, *, dry_run: bool = False) -> None:
        self._dry_run = dry_run
        self._ledger.start_run()
        self._memo = self._repo.load_analysis_memo()

    @property
    def spent_usd(self) -> float:
        """What the model has cost this run, over every phase that asks it something."""
        return self._ledger.spent_usd

    @property
    def _submits_batches(self) -> bool:
        """Whether a memo miss is filed to the batch; `collect_batches` never asks."""
        return self._batch is not None and self._options.submit_batches and not self._dry_run

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
                bill, triage=bill.number not in carried, submit_batch=self._submits_batches
            )

        for outcome in fan_out(candidates, prepare, workers=self._options.workers):
            bill = outcome.item
            try:
                prepared = outcome.result()
                if prepared.queued:
                    if self._enqueue(bill, prepared):
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
            add_usage(result.usage, record)
            # A rejection's record is the triage call; adding `prepared.triage` too would double it.
            if prepared.triage is not None and record.text_source != "excerpts":
                result.input_tokens += prepared.triage.input_tokens or 0
                result.output_tokens += prepared.triage.output_tokens or 0
                add_usage(result.usage, prepared.triage)
            if self._ledger.exhausted:
                result.stopped = (
                    f"{self._ledger.over_budget}; the remaining candidates wait for the next run"
                )
                log.warning("analysis phase stopped: %s", result.stopped)
                break
        return result

    def submit_queued_batches(self) -> None:
        """Submit durable queued intents; an uncertain remote outcome stays operator-visible."""
        intents = self._repo.list_queued_batch_intents()
        stale = [
            intent.request.custom_id
            for intent in intents
            if (bill := self._repo.get(intent.request.term, intent.request.number)) is None
            or bill.status is not BillStatus.BATCH_PENDING
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
            for offset in range(0, len(provider_intents), 100):
                group = provider_intents[offset : offset + 100]
                requests = [intent.request for intent in group]
                ids = [request.custom_id for request in requests]
                self._repo.mark_batch_intents_submitting(ids)
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
                            estimated_cost_usd=sum(
                                self._estimate_request_cost(req.ctx) for req in requests
                            ),
                        ),
                        [
                            LlmBatchItem(
                                batch_id=batch_id,
                                custom_id=intent.request.custom_id,
                                call_kind=intent.request.call_kind,
                                term=intent.request.term,
                                number=intent.request.number,
                                meta=intent.meta,
                            )
                            for intent in group
                        ],
                    )
                    self._repo.delete_batch_intents(ids)
                log.info("filed batch %s: %d request(s) with %s", batch_id, len(group), provider)

    def _estimate_request_cost(self, ctx: BillContext) -> float:
        """Conservative reservation at the selected batch model's discounted input price."""
        price = self._options.batch_input_price_usd_per_mtok
        if price is None:
            return 0.0
        tokens = self._llm.count_input_tokens(ctx)
        upper_bound = max(tokens or 0, len(ctx.text) + len(ctx.title) + 2_000)
        return input_cost(upper_bound, price) * 0.5

    def collect_batches(self) -> None:
        """Write down every batch the provider has finished. A bill whose answer landed goes
        back to the status the normal pipeline reads: `_prepare` will find the same memo key it
        queued and finish for free, with no new model call — this is the only place that reads
        one back."""
        if self._batch is None:
            return
        now = self._clock.now()
        for batch in self._repo.list_open_llm_batches():
            backend = (
                self._batch_resolver(batch.provider)
                if self._batch_resolver is not None
                else self._batch
                if batch.provider == self._options.batch_provider
                else None
            )
            if backend is None:
                log.warning("batch %s waits for %s backend", batch.batch_id, batch.provider)
                continue
            status = backend.poll(batch.batch_id)
            self._repo.mark_llm_batch_polled(batch.batch_id, status=status, polled_at=now)
            items = self._repo.list_llm_batch_items(batch.batch_id)
            if status == "failed":
                for item in items:
                    self._consume(item, BatchResult(custom_id=item.custom_id, error="batch failed"))
                self._repo.mark_llm_batch_collected(batch.batch_id, completed_at=now)
                continue
            if status != "ended":
                continue
            by_id = {item.custom_id: item for item in items}
            received: set[str] = set()
            for batch_result in backend.fetch_results(batch.batch_id):
                found = by_id.get(batch_result.custom_id)
                if found is not None and batch_result.custom_id not in received:
                    self._consume(found, batch_result)
                    received.add(batch_result.custom_id)
            for custom_id, item in by_id.items():
                if custom_id not in received:
                    self._consume(
                        item,
                        BatchResult(custom_id=custom_id, error="batch item missing from results"),
                    )
            self._repo.mark_llm_batch_collected(batch.batch_id, completed_at=now)

    def uncertain_batch_intent_ids(self) -> list[str]:
        return [intent.request.custom_id for intent in self._repo.list_submitting_batch_intents()]

    def _consume(self, item: LlmBatchItem, batch_result: BatchResult) -> None:
        now = self._clock.now()
        current = self._repo.get(item.term, item.number)
        if current is None or current.status is not BillStatus.BATCH_PENDING:
            self._repo.mark_llm_batch_item_consumed(item.batch_id, item.custom_id, consumed_at=now)
            return
        with self._repo.atomic():
            self._consume_current(item, batch_result, now)

    def _consume_current(
        self, item: LlmBatchItem, batch_result: BatchResult, now: datetime
    ) -> None:
        if batch_result.analysis is not None:
            record = AnalysisRecord(
                analysis=batch_result.analysis,
                model=batch_result.model or "",
                prompt_version=item.meta.prompt_version or batch_result.prompt_version or "",
                input_chars=item.meta.input_chars,
                truncated=item.meta.truncated,
                text_source=item.meta.text_source,
                created_at=now,
                input_tokens=batch_result.input_tokens,
                output_tokens=batch_result.output_tokens,
                cache_read_input_tokens=batch_result.cache_read_input_tokens,
                cache_creation_input_tokens=batch_result.cache_creation_input_tokens,
                source_url=item.meta.source_url,
                source_kind=item.meta.source_kind,
                revision=item.meta.revision,
                text_sha256=item.meta.text_sha256,
                source_checked_at=now,
            )
            self._ledger.charge(record, number=item.number, kind=item.call_kind, batched=True)
            self._remember_analysis(item.custom_id, record)
            # Back to what the memo hit will finish for free: the queue, or the steady state.
            done = (
                BillStatus.ANALYSIS_READY
                if item.call_kind == "analysis"
                else BillStatus.REANALYSIS_READY
            )
            self._repo.set_status(item.term, item.number, done)
        else:
            log.warning(
                "%s: batch answer (%s) is %s", item.number, item.call_kind, batch_result.error
            )
            if item.call_kind == "analysis":
                self._repo.record_analysis_failure(
                    item.term, item.number, batch_result.error or "batch: no answer"
                )
            else:
                # A reanalysis that failed keeps the old analysis, same as a synchronous one would.
                self._repo.set_status(item.term, item.number, BillStatus.ANALYZED)
        self._repo.mark_llm_batch_item_consumed(item.batch_id, item.custom_id, consumed_at=now)

    def analyze_bill(self, bill: Bill, *, ignore_cost_limit: bool = False) -> AnalysisOutcome:
        """First analysis from the current text. Persists the result; raises on failure.
        `ignore_cost_limit` is the operator's explicit wish (a forced command): the per-bill
        cost guard does not apply."""
        prepared = self._prepare_first(
            bill, cost_guard=not ignore_cost_limit, triage=not self._joint_card_exists(bill)
        )
        return AnalysisOutcome(self._persist(prepared), prepared.triage)

    def _prepare_first(
        self,
        bill: Bill,
        *,
        cost_guard: bool = True,
        triage: bool = True,
        submit_batch: bool = False,
    ) -> _Prepared:
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
        self, bill: Bill, document: TextDocument, *, summary: ProcessSummary | None = None
    ) -> AnalysisRecord | None:
        """Analyse a newer text of an already analysed bill; the model also reports what changed.
        `summary` is fresher metadata than the stored one, when the caller has it.

        None when the document turns out to carry the text already analysed (a file republished
        on RCL, a print re-dated by an attachment): the stored analysis then points at the new
        source and nothing else happens. None too when the run has spent its budget, and then
        nothing is written at all, so the next run offers the same document again."""
        fresh, changed = self.prepare_reanalysis(bill, document, summary=summary)
        if fresh is not bill:
            assert fresh.analysis is not None
            with self._repo.atomic():
                self._repo.save_analysis(bill.term, bill.number, fresh.analysis)
                if fresh.authors is not None:
                    self._repo.save_authors(bill.term, bill.number, fresh.authors)
        return fresh.analysis if changed else None

    def prepare_reanalysis(
        self, bill: Bill, document: TextDocument, *, summary: ProcessSummary | None = None
    ) -> tuple[Bill, bool]:
        """Memoize paid work without advancing the bill before its source checkpoint."""
        assert bill.analysis is not None
        if bill.status is BillStatus.BATCH_PENDING:
            # Already filed and not yet collected: re-detecting it now would queue it twice.
            return bill, False
        located = LocatedText(summary=summary, document=document)
        prepared = self._prepare(
            bill, located, previous=bill.analysis, submit_batch=self._submits_batches
        )
        if prepared.queued:
            self._enqueue(bill, prepared)
            return bill, False
        if prepared.deferred:
            log.warning("%s: %s", bill.number, self._ledger.stopped)
            return bill, False
        if prepared.unreadable:
            log.warning(
                "%s: %s cannot be read; the analysis of the previous text is kept",
                bill.number,
                document.url,
            )
            return bill, False
        assert prepared.record is not None
        if prepared.unchanged:
            log.info("%s: %s carries the analysed text; not re-analysed", bill.number, document.url)
            return bill.model_copy(update={"analysis": prepared.record}), False
        if prepared.memo_key is not None:
            self._remember_analysis(prepared.memo_key, prepared.record)
        authors = bill.authors
        if self._authors is not None and document.kind == "print" and prepared.text:
            described = bill if summary is None else bill.model_copy(update={"summary": summary})
            authors = self._authors.resolve(described, prepared.text) or authors
        return bill.model_copy(update={"analysis": prepared.record, "authors": authors}), True

    def summarize_amendments(
        self, bill: Bill, document: TextDocument, *, proposal: str | None = None
    ) -> AmendmentsRecord | None:
        """What the amendments in `document` change, against the bill's current analysis.
        None when the document has no readable text (a scan) or costs more to read than the
        per-bill limit allows: the update then only names the event, which is the same thing a
        failed digest degrades to. Only an outage propagates."""
        assert bill.analysis is not None
        assert document.kind in AMENDMENT_SOURCES
        loaded = self._load_text(document, trim=False)
        if not loaded.text.strip():
            return None
        if self._too_expensive_to_digest(loaded):
            log.info(
                "%s: %s is over the per-document cost limit; told without a summary",
                bill.number,
                document.url,
            )
            return None
        ctx = AmendmentsContext(
            number=bill.number,
            title=bill.summary.title,
            source_kind=document.kind,
            text=loaded.text,
            truncated=loaded.truncated,
            previous_summary=bill.analysis.analysis.summary,
            previous_key_changes=list(bill.analysis.analysis.key_changes),
            proposal=proposal,
        )
        key = _memo_key(bill, "amendments", ctx)
        cached = self._memo.get(key)
        if cached is None:
            record = self._llm.summarize_amendments(ctx)
            self._ledger.charge(record, number=bill.number, kind="amendments")
            record.source_url = document.url
            self._remember_analysis(key, record)
        else:
            record = _reused(AmendmentsRecord.model_validate_json(cached))
        record.source_url = document.url
        return record

    def digest_supplement(
        self, bill: Bill, document: TextDocument, *, number: str, title: str
    ) -> SupplementRecord:
        """What the document filed to the print says about the bill, against its current
        analysis. A document with no readable text (a scan) comes back as the bare record, which
        the reply still names and links. Only an outage propagates."""
        assert bill.analysis is not None
        assert document.kind in SUPPLEMENT_SOURCES
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
        key = _memo_key(bill, "supplement", ctx) if loaded.scan is None else None
        cached = self._memo.get(key) if key is not None else None
        if cached is None:
            record = self._llm.digest_supplement(ctx)
            self._ledger.charge(record, number=bill.number, kind="supplement")
            record.number = number
            record.source_url = document.url
            if key is not None:
                self._remember_analysis(key, record)
        else:
            record = _reused(SupplementRecord.model_validate_json(cached))
        record.number = number
        record.source_url = document.url
        return record

    def compare_joint(self, bill: Bill, others: list[Bill]) -> JointRecord | None:
        """How `bill` differs from the prints considered jointly with it, read off the channel's
        own description of each. None when nothing has been analysed to compare with.

        The descriptions and not the texts: each has been read once already, they are what the
        reader sees, and they cost about a cent. Only an outage propagates — a failed call leaves
        the reply as it was before comparisons existed.
        """
        assert bill.analysis is not None
        described = [other for other in others if other.analysis is not None]
        if not described:
            return None
        ctx = JointContext(
            subject=_describe_for_comparison(bill),
            others=[_describe_for_comparison(other) for other in described],
        )
        record = self._llm.compare_joint(ctx)
        self._ledger.charge(record, number=bill.number, kind="joint")
        return record

    def _too_expensive_to_digest(self, loaded: _Loaded) -> bool:
        """The per-bill cost limit applies to a filed document too, and here it is the whole
        answer rather than a reason to skip the bill: a reader who is told what the document is
        and where it lies has lost little. A bill's own text is worth its price; the assessment
        of it is worth a bounded one."""
        limit, price = self._options.max_bill_cost_usd, self._options.input_price_usd_per_mtok
        if not limit or price is None:
            return False
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
        cost_guard: bool = True,
        triage: bool = True,
        submit_batch: bool = False,
    ) -> _Prepared:
        """Load the text and ask the model. Network only: safe to run for several bills at once.

        The per-bill cost guard never refuses a re-analysis, only shortens it: a re-analysis
        reads a new version of a text that already passed the guard, and a bill whose new text
        we decline to read would keep a card describing the old one.

        `submit_batch` files a memo miss to the batch instead of calling the model — false for a
        manual `/analyze`, which the operator is waiting on. Still network-only: the request is
        built and returned, not sent; `_enqueue` (the calling thread) is what touches the batch.
        """
        document = located.document
        try:
            loaded = self._load_text(document)
        except OrkaUnreachableError:
            if previous is None:
                raise
            # A bill that already has an analysis keeps it, the same as for any other document
            # that could not be read: the card goes on describing the text it was written from.
            return _Prepared(
                bill, located, "", "metadata_only", previous, first=False, unreadable=True
            )
        text, truncated, source = loaded.text, loaded.truncated, loaded.source
        if previous is not None and source not in FULL_TEXT_SOURCES:
            return _Prepared(bill, located, text, source, previous, first=False, unreadable=True)
        digest = loaded.digest
        if previous is not None and digest is not None and digest == previous.text_sha256:
            assert document is not None
            pointer = previous.model_copy(
                update={
                    "source_url": document.url,
                    "source_kind": document.kind,
                    "source_checked_at": self._clock.now(),
                }
            )
            return _Prepared(bill, located, text, source, pointer, first=False, unchanged=True)
        if previous is not None and self._ledger.exhausted:
            # The per-bill guard does not apply to a re-analysis, but the run's budget does: a
            # bill whose text is not read now keeps the analysis and the `source_url` it had, so
            # the next run sees the same new document and reads it then.
            self._ledger.stop("the new text(s) wait for the next run")
            return _Prepared(bill, located, text, source, previous, first=False, deferred=True)
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
            previous_summary=previous.analysis.summary if previous else None,
            previous_key_changes=list(previous.analysis.key_changes) if previous else [],
        )
        if cost_guard:
            ctx = self._fit_to_budget(ctx, loaded, first=previous is None)
        purpose: CallKind = "analysis" if previous is None else "reanalysis"
        key = _memo_key(bill, purpose, ctx) if cost_guard else None
        cached = self._memo.get(key) if key is not None else None
        if cached is None and submit_batch and self._batch is not None and key is not None:
            return _Prepared(
                bill,
                located,
                text,
                source,
                None,
                first=previous is None,
                triage=triaged,
                queued=True,
                batch_request=BatchRequest(
                    custom_id=key, call_kind=purpose, term=bill.term, number=bill.number, ctx=ctx
                ),
                batch_meta=BatchItemMeta(
                    input_chars=len(ctx.text),
                    truncated=ctx.truncated,
                    text_source=source,
                    source_kind=ctx.source_kind,
                    source_url=document.url if document else None,
                    revision=previous.revision + 1 if previous else 1,
                    text_sha256=digest,
                    prompt_version=self._options.prompt_version,
                ),
                memo_key=key,
                triage_memo_key=triage_memo_key,
            )
        if cached is None:
            record = self._llm.analyze(ctx)
            self._ledger.charge(record, number=bill.number, kind=purpose)
        else:
            record = _reused(AnalysisRecord.model_validate_json(cached))
        record.source_url = document.url if document else None
        record.source_kind = ctx.source_kind
        record.revision = previous.revision + 1 if previous else 1
        record.text_sha256 = digest
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

    def _fit_to_budget(self, ctx: BillContext, loaded: _Loaded, *, first: bool) -> BillContext:
        """The context the model is actually sent: this one, or a shorter one that fits the
        per-bill cost limit.

        The model counts a text's input itself, free and exact; a scan is priced from its pages
        (1,600 tokens each, measured) rather than uploading tens of megabytes to learn a number.

        A text over the limit is cut down, not refused — the triage has already said the bill
        matters, and the card says «неполный текст» either way. A scan cannot be thinned, its
        pages being substance throughout, so there the answer is still to refuse.

        `first` is what every refusal turns on: a re-analysis is cut to fit but never raises, or
        it would land in the tracking loop's per-bill `except`, fail on the same text every run
        and keep a card describing the text before this one.
        """
        limit, price = self._options.max_bill_cost_usd, self._options.input_price_usd_per_mtok
        if not limit or price is None:
            return ctx
        if loaded.scan is not None:
            cost = loaded.estimate(price)
            if cost > limit and first:
                raise TooExpensiveError(cost, limit, measure=loaded.measure(None))
            return ctx
        tokens = self._llm.count_input_tokens(ctx)
        if tokens is None:
            cost = loaded.estimate(price)
            if cost > limit and first:
                raise TooExpensiveError(cost, limit, measure=loaded.measure(None))
            return ctx
        cost = input_cost(tokens, price)
        if cost <= limit:
            return ctx
        shorter = self._shorten(ctx.text, over_by=cost / limit)
        if shorter is None:
            if not first:
                return ctx
            raise TooExpensiveError(cost, limit, measure=loaded.measure(tokens))
        reduced = ctx.model_copy(update={"text": shorter, "truncated": True})
        counted = self._llm.count_input_tokens(reduced)
        log.info(
            "%s: %d tokens is %s, over the %s limit; sending %d of %d chars instead",
            ctx.number,
            tokens,
            format_usd(cost),
            format_usd(limit),
            len(shorter),
            len(ctx.text),
        )
        if first and counted is not None and input_cost(counted, price) > limit:
            raise TooExpensiveError(
                input_cost(counted, price),
                limit,
                measure=f"{counted} tokens after trimming to {len(shorter)} chars",
            )
        return reduced

    def _shorten(self, text: str, *, over_by: float) -> str | None:
        """The text cut to what the cost limit pays for: the head of the bill, the head of the
        justification and a window around every keyword hit, in document order.

        `over_by` is how many times the counted input overshot the limit, so the same ratio on
        the characters lands inside it whatever the text tokenizes at; `_FIT_MARGIN` leaves room
        for the prompt and schema. None when too little is left to be a document.
        """
        target = int(len(text) / over_by * _FIT_MARGIN)
        if target < _FIT_MIN_CHARS:
            return None
        return excerpts(text, self._keywords.spans(text), head_chars=target // 4, max_chars=target)

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
                created_at=self._clock.now(),
                input_tokens=verdict.input_tokens,
                output_tokens=verdict.output_tokens,
            ),
            key,
        )

    def _enqueue(self, bill: Bill, prepared: _Prepared) -> bool:
        """Hand a queued request to the batch and mark the bill so nothing re-submits it while
        it is in flight. Calling thread only, like `_persist`."""
        assert prepared.batch_request is not None and prepared.batch_meta is not None
        if not self._ledger.reserve(self._estimate_request_cost(prepared.batch_request.ctx)):
            self._ledger.stop("the batch analysis waits for the next run")
            return False
        if prepared.triage_memo_key is not None and prepared.triage is not None:
            self._remember_analysis(prepared.triage_memo_key, prepared.triage)
        with self._repo.atomic():
            self._repo.save_batch_intent(
                BatchIntent(
                    request=prepared.batch_request,
                    meta=prepared.batch_meta,
                    provider=self._options.batch_provider or "anthropic",
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
            self._remember_analysis(prepared.memo_key, prepared.record)
        if prepared.triage_memo_key is not None and prepared.triage is not None:
            self._remember_analysis(prepared.triage_memo_key, prepared.triage)
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
            if prepared.first and located.summary is not None:
                summary = located.summary
                if summary.applicant_type is ApplicantType.UNKNOWN:
                    summary = summary.model_copy(update={"applicant": bill.summary.applicant_type})
                self._repo.upsert_summary(summary, now=self._clock.now())
            if authors is not None:
                self._repo.save_authors(bill.term, bill.number, authors)
            self._repo.save_analysis(bill.term, bill.number, prepared.record)
            if prepared.first and located.stages is not None:
                self._repo.save_stages(
                    bill.term, bill.number, located.stages, stage_fingerprint(located.stages)
                )
                self._repo.save_observed_closure(
                    bill.term, bill.number, (located.summary or bill.summary).closure_date
                )
                fresh = self._repo.get(bill.term, bill.number)
                assert fresh is not None
                self._repo.save_observed_process(
                    bill.term,
                    bill.number,
                    observe(fresh, closure_date=(located.summary or bill.summary).closure_date),
                )
        return prepared.record

    def _remember_analysis(self, key: str, record: BaseModel) -> None:
        encoded = record.model_dump_json()
        self._repo.save_analysis_memo(key, encoded)
        self._memo.setdefault(key, encoded)

    def _load_text(self, document: TextDocument | None, *, trim: bool = True) -> _Loaded:
        """Trimmed, budgeted text of the document — or its pages, when the file has no text in it.

        Much of what the Sejm publishes is signed paper filed as images, whose text layer is
        empty or holds only the transmittal letter. Such a file goes to the model as pages
        (`scan`); the extracted text comes back either way, so the signatures are still resolved.

        An outage propagates, and so does orka refusing the file: an analysis of the metadata must
        never be what a bill with a text ends up with. A missing or broken file falls back to the
        metadata without costing an attempt. `trim=False` keeps the whole text.
        """
        if document is None:
            return _Loaded("", False, "metadata_only")
        try:
            file = self._loader.read_document(document)
        except ServiceUnavailableError:
            raise
        except OrkaUnreachableError as exc:
            if not exc.file_is_missing:
                # The WAF's decision about our address on the day. Falling back to the metadata
                # would turn it into a verdict on a text nobody has read; the caller waits.
                raise
            log.warning("%s is not there (%s); using metadata only", document.url, exc)
            return _Loaded("", False, "metadata_only")
        except Exception as exc:
            log.warning(
                "%s unreadable (%s: %s); using metadata only",
                document.url,
                type(exc).__name__,
                exc,
            )
            return _Loaded("", False, "metadata_only")
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
            return _Loaded(budgeted.text, budgeted.truncated, "pdf")
        trimmed = trim_print(text)
        # Said whether anything was dropped or not: a text that goes in whole is the expensive
        # case, and it was the silent one — the package that cost $1.63 logged nothing at all.
        log.info(
            "%s: %d chars, sending %d (dropped %s)",
            document.url,
            len(text),
            len(trimmed.text),
            ", ".join(f"{d.name} {d.chars}" for d in trimmed.dropped) or "nothing",
        )
        budgeted = self._budget.apply(trimmed.text, self._keywords.spans(trimmed.text))
        source: TextSource = "documents" if document.kind == "rcl" else "pdf"
        return _Loaded(budgeted.text, budgeted.truncated, source)

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
        return _Loaded(text, scan.truncated, "scan", scan)
