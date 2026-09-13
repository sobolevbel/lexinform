"""Turns a prefilter candidate into an LLM analysis: locate the text, read it, ask the model.

Also re-analyses a bill when a newer text appears (committee report with amendments, text after the
3rd reading, an updated print, a new version of an RCL project), feeding the previous analysis to
the model so it can say what changed. Where the text comes from is the `TextSource`'s business.
"""

import hashlib
import logging
import re
import threading
from dataclasses import dataclass, field

from lexinform.concurrency import fan_out
from lexinform.errors import ServiceUnavailableError
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
    Bill,
    BillContext,
    BillStatus,
    CallKind,
    Category,
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
    UsageRecord,
    add_usage,
    stage_fingerprint,
)
from lexinform.ports import AuthorsResolver, BillRepository, Clock, LlmAnalyzer
from lexinform.ports import TextSource as TextSourcePort
from lexinform.pricing import cost_usd, estimate_input_cost, estimate_scan_cost, input_cost
from lexinform.sections import (
    APPENDIX_KINDS,
    TextBudget,
    carries_the_document,
    document_kind,
    excerpts,
    has_cover_letter,
    trim_print,
)
from lexinform.services.documents import MIN_TEXT_CHARS, TextLoader
from lexinform.services.joint import primary_of

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

    `skipped_cost` counts the texts that were over the per-bill cost limit and never reached the
    model, `skipped_joint` the prints whose group is already carried by another print's card,
    `usage` is counted per model, and `stopped` says the phase ended early on the per-run cost
    limit, which is a note and not an error.
    """

    analyzed: int = 0
    triaged_out: int = 0
    skipped_cost: int = 0
    skipped_joint: int = 0
    failed: int = 0
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
            f"${_usd(estimate)} of input for {measure} exceeds the ${_usd(limit)} limit"
        )
        self.estimate = estimate


def _usd(amount: float) -> str:
    return f"{amount:.2f}" if amount >= 0.01 else f"{amount:.3f}"


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

    `first` marks a first analysis, which seeds the stages; a re-analysis leaves them to
    tracking. `triage` is a triage the bill passed, whose tokens count too. Three flags say the
    model was not asked at all: `unchanged`, when a re-analysis found the same text under a new
    URL and `record` is the previous one pointing at the new source, `unreadable`, when the
    new document could not be read and `record` is the previous one kept as it is — an analysis
    of the metadata must never replace one of a text — and `deferred`, when the run had spent
    its budget before the re-analysis and the text waits for the next one.
    """

    bill: Bill
    located: LocatedText
    text: str
    source: TextSource
    record: AnalysisRecord
    first: bool
    triage: TriageRecord | None = None
    unchanged: bool = False
    unreadable: bool = False
    deferred: bool = False


_PAGE_NUMBER_LINE = re.compile(r"^\s*[–\-—]?\s*\d{1,4}\s*[–\-—]?\s*$", re.MULTILINE)
_WHITESPACE = re.compile(r"\s+")


def text_digest(text: str) -> str:
    """SHA-256 of the text with page numbers and whitespace differences ignored, so the same
    bill text rendered by another layout (a republished file, a re-dated print) hashes alike."""
    normalised = _WHITESPACE.sub(" ", _PAGE_NUMBER_LINE.sub("", text)).strip()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


class AnalysisService:
    """First analyses of candidates and re-analyses of bills whose text changed.

    The cost guards are off when their limit is 0, and the per-bill estimate needs the model's
    input price; `keywords` holds the patterns that pick what survives when a text has to be cut
    down to the limit, and `triage` the same patterns again when the cheap first pass is on —
    None there disables that pass. `channel_id` is the channel the phase is analysing for,
    and only the scheduled phase uses it, to leave a jointly considered print alone when the
    group's card is another print's; None means the question is not asked.
    """

    def __init__(
        self,
        repo: BillRepository,
        texts: TextSourcePort,
        loader: TextLoader,
        llm: LlmAnalyzer,
        clock: Clock,
        *,
        text_budget: TextBudget,
        authors: AuthorsResolver | None = None,
        max_attempts: int = 3,
        workers: int = 1,
        input_price_usd_per_mtok: float | None = None,
        max_bill_cost_usd: float = 0.0,
        max_run_cost_usd: float = 0.0,
        keywords: KeywordPrefilter | None = None,
        triage: KeywordPrefilter | None = None,
        triage_min_chars: int = 20_000,
        triage_min_confidence: float = 0.8,
        channel_id: str | None = None,
    ) -> None:
        self._repo = repo
        self._texts = texts
        self._loader = loader
        self._llm = llm
        self._clock = clock
        self._budget = text_budget
        self._authors = authors
        self._max_attempts = max_attempts
        self._workers = workers
        self._input_price = input_price_usd_per_mtok
        self._max_bill_cost = max_bill_cost_usd
        self._max_run_cost = max_run_cost_usd
        self._keywords = keywords or KeywordPrefilter()
        self._triage = triage
        self._triage_min_chars = triage_min_chars
        self._triage_min_confidence = triage_min_confidence
        self._channel_id = channel_id
        self._spent = 0.0
        self._calls: list[LlmCall] = []
        self._spent_lock = threading.Lock()
        self._stopped: str | None = None

    def start_run(self) -> None:
        """A run is one budget. In production the container is built once per process, so the
        counter would be run-scoped anyway; a test drives several runs through one container."""
        with self._spent_lock:
            self._spent = 0.0
            self._calls.clear()
        self._stopped = None

    @property
    def spent_usd(self) -> float:
        """What the model has cost this run, over every phase that asks it something."""
        return self._spent

    @property
    def stopped(self) -> str | None:
        """Set once the per-run limit has held a re-analysis back, for the run report to say so.
        The analysis phase says it for itself (`AnalysisResult.stopped`)."""
        return self._stopped

    def _charge(self, record: UsageRecord | None, *, number: str, kind: CallKind) -> None:
        """Count what a model call cost towards the run's budget, and write down which call it
        was. Called from the worker threads of `fan_out`, so both are locked.

        The run reported one tokens figure for everything and it could not be accounted for
        afterwards; `calls` is what answers "where did the money go" without reading the logs.
        """
        if record is None:
            return
        usage: dict[str, TokenUsage] = {}
        add_usage(usage, record)
        spent = cost_usd(usage)
        with self._spent_lock:
            self._calls.append(
                LlmCall(
                    number=number,
                    kind=kind,
                    model=record.model,
                    input_tokens=record.input_tokens or 0,
                    output_tokens=record.output_tokens or 0,
                )
            )
            if spent is not None:
                self._spent += spent

    @property
    def calls(self) -> list[LlmCall]:
        """Every model call this run has made, in the order they were charged."""
        with self._spent_lock:
            return list(self._calls)

    def _run_budget_reached(self) -> bool:
        return bool(self._max_run_cost) and self._spent >= self._max_run_cost

    def analyze_pending(self, *, limit: int) -> AnalysisResult:
        """Analyse up to `limit` candidates; an outage stops the phase, a bill's own error
        costs it one attempt.

        The phase also stops when the run's model spend reaches the per-run limit: the remaining
        candidates keep their status and wait for the next run.
        """
        result = AnalysisResult()
        if limit <= 0:
            return result
        candidates = self._repo.list_by_status(
            [BillStatus.ANALYSIS_PENDING, BillStatus.ANALYSIS_FAILED],
            limit=limit,
            max_attempts=self._max_attempts,
        )
        candidates = [
            bill for bill in candidates if not self._carried_by_a_joint_card(bill, result)
        ]
        for outcome in fan_out(candidates, self._prepare_first, workers=self._workers):
            bill = outcome.item
            try:
                prepared = outcome.result()
                record = self._persist(prepared)
            except ServiceUnavailableError as exc:
                result.fatal_error = exc.describe()
                log.error("aborting analysis phase: %s", result.fatal_error)
                break
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
            if prepared.triage is not None:
                result.input_tokens += prepared.triage.input_tokens or 0
                result.output_tokens += prepared.triage.output_tokens or 0
                add_usage(result.usage, prepared.triage)
            if self._run_budget_reached():
                result.stopped = (
                    f"run cost limit reached (≈${_usd(self._spent)} ≥ "
                    f"${_usd(self._max_run_cost)}); "
                    "the remaining candidates wait for the next run"
                )
                log.warning("analysis phase stopped: %s", result.stopped)
                break
        return result

    def analyze_bill(self, bill: Bill, *, ignore_cost_limit: bool = False) -> AnalysisOutcome:
        """First analysis from the original text. Persists the result; raises on failure.
        `ignore_cost_limit` is the operator's explicit wish (a forced command): the per-bill
        cost guard does not apply."""
        prepared = self._prepare_first(bill, cost_guard=not ignore_cost_limit)
        return AnalysisOutcome(self._persist(prepared), prepared.triage)

    def _prepare_first(self, bill: Bill, *, cost_guard: bool = True) -> _Prepared:
        return self._prepare(bill, self._texts.locate(bill), previous=None, cost_guard=cost_guard)

    def _carried_by_a_joint_card(self, bill: Bill, result: AnalysisResult) -> bool:
        """Whether another print of this bill's group already carries the card, so that this
        print will be a reply that shows no analysis and the model need not be asked.

        The question is the publisher's own (`services.joint.primary_of`), asked a phase earlier:
        `PublishingService` settles the same bill as a `joint_bill` reply, which carries the
        title, the applicant and the links but no analysis at all. Druk 1933 was analysed for
        305,132 input tokens and published as one. If the group's card is later withdrawn or
        rejected, `primary_of` stops finding it and `/unskip` puts the print back in the queue.
        An operator's `/analyze` never comes through here: asking for one explicitly is a wish
        to have it.
        """
        if self._channel_id is None or not bill.summary.prints_considered_jointly:
            return False
        primary = primary_of(self._repo, bill, self._channel_id)
        if primary is None:
            return False
        other, _ = primary
        result.skipped_joint += 1
        log.info(
            "%s is considered jointly with %s, which carries the card; not analysed",
            bill.number,
            other.number,
        )
        self._repo.set_status(
            bill.term,
            bill.number,
            BillStatus.SKIPPED_JOINT,
            reason=(
                f"considered jointly with druk {other.number}, which carries the card; "
                "its reply shows no analysis (/unskip to analyse anyway)"
            ),
        )
        return True

    def reanalyze_bill(
        self, bill: Bill, document: TextDocument, *, summary: ProcessSummary | None = None
    ) -> AnalysisRecord | None:
        """Analyse a newer text of an already analysed bill; the model also reports what changed.
        `summary` is fresher metadata than the stored one, when the caller has it.

        None when the document turns out to carry the text already analysed (a file republished
        on RCL, a print re-dated by an attachment): the stored analysis then points at the new
        source and nothing else happens. None too when the run has spent its budget, and then
        nothing is written at all, so the next run offers the same document again."""
        assert bill.analysis is not None
        located = LocatedText(summary=summary, document=document)
        prepared = self._prepare(bill, located, previous=bill.analysis)
        if prepared.deferred:
            log.warning("%s: %s", bill.number, self._stopped)
            return None
        if prepared.unreadable:
            log.warning(
                "%s: %s cannot be read; the analysis of the previous text is kept",
                bill.number,
                document.url,
            )
            return None
        if prepared.unchanged:
            log.info("%s: %s carries the analysed text; not re-analysed", bill.number, document.url)
            self._repo.save_analysis(bill.term, bill.number, prepared.record)
            return None
        return self._persist(prepared)

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
        record = self._llm.summarize_amendments(ctx)
        self._charge(record, number=bill.number, kind="amendments")
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
        record = self._llm.digest_supplement(ctx)
        self._charge(record, number=bill.number, kind="supplement")
        record.number = number
        record.source_url = document.url
        return record

    def _too_expensive_to_digest(self, loaded: _Loaded) -> bool:
        """The per-bill cost limit applies to a filed document too, and here it is the whole
        answer rather than a reason to skip the bill: a reader who is told what the document is
        and where it lies has lost little. A bill's own text is worth its price; the assessment
        of it is worth a bounded one."""
        if not self._max_bill_cost or self._input_price is None:
            return False
        return loaded.estimate(self._input_price) > self._max_bill_cost

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
    ) -> _Prepared:
        """Load the text and ask the model. Network only: safe to run for several bills at once.

        The per-bill cost guard applies to first analyses alone: a re-analysis reads a new
        version of a text that already passed it, and skipping that would leave the card's
        analysis behind the bill.
        """
        document = located.document
        loaded = self._load_text(document)
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
        if previous is not None and self._run_budget_reached():
            # The per-bill guard does not apply to a re-analysis, but the run's budget does: a
            # bill whose text is not read now keeps the analysis and the `source_url` it had, so
            # the next run sees the same new document and reads it then.
            self._stopped = (
                f"run cost limit reached (≈${_usd(self._spent)} ≥ ${_usd(self._max_run_cost)});"
                " the new text(s) wait for the next run"
            )
            return _Prepared(bill, located, text, source, previous, first=False, deferred=True)
        meta = located.summary or bill.summary
        triage: TriageRecord | None = None
        if previous is None and source in FULL_TEXT_SOURCES and len(text) >= self._triage_min_chars:
            triage, rejection = self._triage_verdict(bill, meta, text)
            if rejection is not None:
                return _Prepared(bill, located, text, source, rejection, first=True)
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
        record = self._llm.analyze(ctx)
        self._charge(
            record, number=bill.number, kind="analysis" if previous is None else "reanalysis"
        )
        record.source_url = document.url if document else None
        record.source_kind = ctx.source_kind
        record.revision = previous.revision + 1 if previous else 1
        record.text_sha256 = digest
        return _Prepared(bill, located, text, source, record, first=previous is None, triage=triage)

    def _fit_to_budget(self, ctx: BillContext, loaded: _Loaded, *, first: bool) -> BillContext:
        """The context the model is actually sent: this one, or a shorter one that fits the
        per-bill cost limit.

        For a text the model counts the input itself — free, and exact where two characters per
        token is a rule of thumb. A scan is priced from its pages instead (1,600 tokens each,
        measured): asking the tokenizer would mean uploading the whole file, tens of megabytes,
        to learn a number we can multiply out. When the counting request fails, the estimate
        from the text length stands in.

        A text over the limit is cut down rather than refused. The triage has already said the
        bill matters, and dropping it there left the reader with nothing and the operator with a
        `reset` to run by hand; a text read with gaps is worth more than a bill not read at all,
        and the card says «неполный текст» either way. What a scan cannot do is be thinned: its
        pages are substance from the first to the last, so there the answer is still to refuse —
        and for a re-analysis not even that, because a bill whose new text we decline to read
        must not be left with a card that describes the old one.
        """
        if not self._max_bill_cost or self._input_price is None:
            return ctx
        if loaded.scan is not None:
            cost = loaded.estimate(self._input_price)
            if cost > self._max_bill_cost and first:
                raise TooExpensiveError(cost, self._max_bill_cost, measure=loaded.measure(None))
            return ctx
        tokens = self._llm.count_input_tokens(ctx)
        if tokens is None:
            cost = loaded.estimate(self._input_price)
            if cost > self._max_bill_cost and first:
                raise TooExpensiveError(cost, self._max_bill_cost, measure=loaded.measure(None))
            return ctx
        cost = input_cost(tokens, self._input_price)
        if cost <= self._max_bill_cost:
            return ctx
        shorter = self._shorten(ctx.text, over_by=cost / self._max_bill_cost)
        if shorter is None:
            raise TooExpensiveError(cost, self._max_bill_cost, measure=loaded.measure(tokens))
        reduced = ctx.model_copy(update={"text": shorter, "truncated": True})
        counted = self._llm.count_input_tokens(reduced)
        log.info(
            "%s: %d tokens is $%s, over the $%s limit; sending %d of %d chars instead",
            ctx.number,
            tokens,
            _usd(cost),
            _usd(self._max_bill_cost),
            len(shorter),
            len(ctx.text),
        )
        if counted is not None and input_cost(counted, self._input_price) > self._max_bill_cost:
            raise TooExpensiveError(
                input_cost(counted, self._input_price),
                self._max_bill_cost,
                measure=f"{counted} tokens after trimming to {len(shorter)} chars",
            )
        return reduced

    def _shorten(self, text: str, *, over_by: float) -> str | None:
        """The text cut to what the cost limit pays for: the head of the bill, the head of the
        justification and a window around every keyword hit, in document order.

        The target is measured, not assumed: `over_by` is how many times the counted input
        overshot the limit, so the same ratio applied to the characters lands inside it whatever
        the text tokenizes at. `_FIT_MARGIN` leaves room for the prompt and the schema, which
        the count included and the ratio therefore over-charges the text for. None when there is
        not enough left to be a document.
        """
        target = int(len(text) / over_by * _FIT_MARGIN)
        if target < _FIT_MIN_CHARS:
            return None
        return excerpts(text, self._keywords.spans(text), head_chars=target // 4, max_chars=target)

    def _triage_verdict(
        self, bill: Bill, meta: ProcessSummary, text: str
    ) -> tuple[TriageRecord | None, AnalysisRecord | None]:
        """Ask the cheap model about excerpts; a confident "no" becomes the final record.

        Returns the triage record and, when it rejects the bill, a non-relevant analysis record
        (its `text_source="excerpts"` says how it was decided).
        """
        if self._triage is None:
            return None, None
        ctx = TriageContext(
            number=bill.number,
            title=meta.title or bill.summary.title,
            description=meta.description or bill.summary.description,
            applicant_type=meta.applicant_type,
            excerpts=excerpts(text, self._triage.spans(text)),
            text_chars=len(text),
        )
        verdict = self._llm.triage(ctx)
        self._charge(verdict, number=bill.number, kind="triage")
        if not verdict.rejects(min_confidence=self._triage_min_confidence):
            log.info(
                "%s passes triage (%s, %.2f): full analysis",
                bill.number,
                "relevant" if verdict.triage.affects_foreigners else "unsure",
                verdict.triage.confidence,
            )
            return verdict, None
        log.info(
            "%s rejected by triage (%.2f): %s",
            bill.number,
            verdict.triage.confidence,
            verdict.triage.rationale,
        )
        return verdict, AnalysisRecord(
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
        )

    def _persist(self, prepared: _Prepared) -> AnalysisRecord:
        """Write one prepared analysis down (stages, authors, the record). Calling thread only."""
        bill, located = prepared.bill, prepared.located
        if prepared.first and located.stages is not None:
            self._repo.save_stages(
                bill.term, bill.number, located.stages, stage_fingerprint(located.stages)
            )
        document = located.document
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
            if authors is not None:
                self._repo.save_authors(bill.term, bill.number, authors)
        self._repo.save_analysis(bill.term, bill.number, prepared.record)
        return prepared.record

    def _load_text(self, document: TextDocument | None, *, trim: bool = True) -> _Loaded:
        """Trimmed, budgeted text of the document — or its pages, when the file has no text in it.

        Much of what the Sejm publishes is signed paper, filed as images: the text layer is empty
        or holds only the letter that hands the document to the Marshal. Such a file goes to the
        model as pages instead (`scan`), with the letter's page and, for an OSR, the tail of the
        13-point form left behind. The extracted text comes back either way, so the signatures
        under the letter are still resolved; the prompt shows the model the text only when it is
        the document.

        Only an outage of the document's host propagates: a missing or broken file falls back to
        the metadata instead of costing the bill an analysis attempt. `trim=False` keeps the
        whole text (an amendments document has no appendices to drop, and its uzasadnienie
        explains the amendments; a document filed to a print is one such document end to end).
        """
        if document is None:
            return _Loaded("", False, "metadata_only")
        try:
            file = self._loader.read_document(document)
        except ServiceUnavailableError:
            raise
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
