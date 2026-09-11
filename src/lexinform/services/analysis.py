"""Turns a prefilter candidate into an LLM analysis: locate the text, read it, ask the model.

Also re-analyses a bill when a newer text appears (committee report with amendments, text after the
3rd reading, an updated print, a new version of an RCL project), feeding the previous analysis to
the model so it can say what changed. Where the text comes from is the `TextSource`'s business.
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field

from lexinform.concurrency import fan_out
from lexinform.errors import ServiceUnavailableError
from lexinform.keywords import KeywordPrefilter
from lexinform.models import (
    AMENDMENT_SOURCES,
    FULL_TEXT_SOURCES,
    AmendmentsContext,
    AmendmentsRecord,
    Analysis,
    AnalysisRecord,
    AnalysisVerdict,
    Bill,
    BillContext,
    BillStatus,
    Category,
    LocatedText,
    ProcessSummary,
    TextDocument,
    TextSource,
    TokenUsage,
    TriageContext,
    TriageRecord,
    add_usage,
    stage_fingerprint,
)
from lexinform.ports import AuthorsResolver, BillRepository, Clock, LlmAnalyzer
from lexinform.ports import TextSource as TextSourcePort
from lexinform.pricing import cost_usd, estimate_input_cost
from lexinform.sections import TextBudget, excerpts, trim_print
from lexinform.services.documents import TextLoader

log = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    """Counters and verdicts of one analysis phase."""

    analyzed: int = 0
    triaged_out: int = 0
    skipped_cost: int = 0  # texts over the per-bill cost limit: not sent to the model
    failed: int = 0
    verdicts: list[AnalysisVerdict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    usage: dict[str, TokenUsage] = field(default_factory=dict)  # per model
    fatal_error: str | None = None
    stopped: str | None = None  # the phase ended early on the per-run cost limit (not an error)


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

    def __init__(self, estimate: float, limit: float, chars: int) -> None:
        super().__init__(
            f"~${_usd(estimate)} of input for {chars} chars exceeds the ${_usd(limit)} limit"
        )
        self.estimate = estimate


def _usd(amount: float) -> str:
    return f"{amount:.2f}" if amount >= 0.01 else f"{amount:.3f}"


@dataclass(frozen=True)
class _Prepared:
    """Everything an analysis needs to be written down: produced without touching the database,
    so several bills can be prepared at the same time."""

    bill: Bill
    located: LocatedText
    text: str
    source: TextSource
    record: AnalysisRecord
    first: bool  # first analysis seeds the stages; a re-analysis leaves them to tracking
    triage: TriageRecord | None = None  # a triage the bill passed: its tokens count too
    # A re-analysis that found the same text under a new URL: `record` is the previous one
    # pointing at the new source, the model was not asked.
    unchanged: bool = False
    # A re-analysis whose new document could not be read: `record` is the previous one, kept as
    # it is; the model was not asked (an analysis of the metadata must never replace one of a text).
    unreadable: bool = False


_PAGE_NUMBER_LINE = re.compile(r"^\s*[–\-—]?\s*\d{1,4}\s*[–\-—]?\s*$", re.MULTILINE)
_WHITESPACE = re.compile(r"\s+")


def text_digest(text: str) -> str:
    """SHA-256 of the text with page numbers and whitespace differences ignored, so the same
    bill text rendered by another layout (a republished file, a re-dated print) hashes alike."""
    normalised = _WHITESPACE.sub(" ", _PAGE_NUMBER_LINE.sub("", text)).strip()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


class AnalysisService:
    """First analyses of candidates and re-analyses of bills whose text changed."""

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
        triage: KeywordPrefilter | None = None,
        triage_min_chars: int = 20_000,
        triage_min_confidence: float = 0.8,
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
        # Guard rails (0 disables): the estimate needs the model's input price.
        self._input_price = input_price_usd_per_mtok
        self._max_bill_cost = max_bill_cost_usd
        self._max_run_cost = max_run_cost_usd
        self._triage = triage  # the keyword patterns that cut the excerpts; None disables it
        self._triage_min_chars = triage_min_chars
        self._triage_min_confidence = triage_min_confidence

    def analyze_pending(self, *, limit: int) -> AnalysisResult:
        """Analyse up to `limit` candidates; an outage stops the phase, a bill's own error
        costs it one attempt."""
        result = AnalysisResult()
        if limit <= 0:
            return result
        candidates = self._repo.list_by_status(
            [BillStatus.ANALYSIS_PENDING, BillStatus.ANALYSIS_FAILED],
            limit=limit,
            max_attempts=self._max_attempts,
        )
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
            spent = cost_usd(result.usage)
            if self._max_run_cost and spent is not None and spent >= self._max_run_cost:
                # The remaining candidates keep their status and wait for the next run.
                result.stopped = (
                    f"run cost limit reached (≈${_usd(spent)} ≥ ${_usd(self._max_run_cost)}); "
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

    def reanalyze_bill(
        self, bill: Bill, document: TextDocument, *, summary: ProcessSummary | None = None
    ) -> AnalysisRecord | None:
        """Analyse a newer text of an already analysed bill; the model also reports what changed.
        `summary` is fresher metadata than the stored one, when the caller has it.

        None when the document turns out to carry the text already analysed (a file republished
        on RCL, a print re-dated by an attachment): the stored analysis then points at the new
        source and nothing else happens."""
        assert bill.analysis is not None
        located = LocatedText(summary=summary, document=document)
        prepared = self._prepare(bill, located, previous=bill.analysis)
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
        None when the document has no readable text (a scan): the update then only names the
        event. Only an outage propagates."""
        assert bill.analysis is not None
        assert document.kind in AMENDMENT_SOURCES
        text, truncated, source = self._load_text(document, trim=False)
        if source == "metadata_only" or not text.strip():
            return None
        ctx = AmendmentsContext(
            number=bill.number,
            title=bill.summary.title,
            source_kind=document.kind,
            text=text,
            truncated=truncated,
            previous_summary=bill.analysis.analysis.summary,
            previous_key_changes=list(bill.analysis.analysis.key_changes),
            proposal=proposal,
        )
        record = self._llm.summarize_amendments(ctx)
        record.source_url = document.url
        return record

    def _prepare(
        self,
        bill: Bill,
        located: LocatedText,
        *,
        previous: AnalysisRecord | None,
        cost_guard: bool = True,
    ) -> _Prepared:
        """Load the text and ask the model. Network only: safe to run for several bills at once."""
        document = located.document
        text, truncated, source = self._load_text(document)
        if previous is not None and source not in FULL_TEXT_SOURCES:
            return _Prepared(bill, located, text, source, previous, first=False, unreadable=True)
        digest = text_digest(text) if source in FULL_TEXT_SOURCES else None
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
        meta = located.summary or bill.summary
        triage: TriageRecord | None = None
        if previous is None and source in FULL_TEXT_SOURCES and len(text) >= self._triage_min_chars:
            triage, rejection = self._triage_verdict(bill, meta, text)
            if rejection is not None:
                return _Prepared(bill, located, text, source, rejection, first=True)
        if (
            previous is None
            and cost_guard
            and self._max_bill_cost
            and self._input_price is not None
        ):
            # First analyses only: a re-analysis reads a new version of a text that already
            # passed, and skipping it would leave the card's analysis behind the bill.
            estimate = estimate_input_cost(len(text), self._input_price)
            if estimate > self._max_bill_cost:
                raise TooExpensiveError(estimate, self._max_bill_cost, len(text))
        ctx = BillContext(
            number=bill.number,
            title=meta.title or bill.summary.title,
            description=meta.description or bill.summary.description,
            document_date=meta.document_date or bill.summary.document_date,
            applicant_type=meta.applicant_type,
            text=text,
            truncated=truncated,
            text_source=source,
            source_kind=document.kind if document and source in FULL_TEXT_SOURCES else "metadata",
            previous_summary=previous.analysis.summary if previous else None,
            previous_key_changes=list(previous.analysis.key_changes) if previous else [],
        )
        record = self._llm.analyze(ctx)
        record.source_url = document.url if document else None
        record.source_kind = ctx.source_kind
        record.revision = previous.revision + 1 if previous else 1
        record.text_sha256 = digest
        return _Prepared(bill, located, text, source, record, first=previous is None, triage=triage)

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
        if (
            self._authors is not None
            and document is not None
            and document.kind == "print"
            and prepared.source in FULL_TEXT_SOURCES
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

    def _load_text(
        self, document: TextDocument | None, *, trim: bool = True
    ) -> tuple[str, bool, TextSource]:
        """Trimmed, budgeted text of the document; metadata-only when it cannot be fetched or read.

        Only an outage of the document's host propagates: a missing or broken file falls back to
        the metadata instead of costing the bill an analysis attempt. `trim=False` keeps the
        whole text (an amendments document has no appendices to drop, and its uzasadnienie
        explains the amendments).
        """
        if document is None:
            return "", False, "metadata_only"
        try:
            text = self._loader.load_document(document)
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.warning(
                "%s unreadable (%s: %s); using metadata only",
                document.url,
                type(exc).__name__,
                exc,
            )
            return "", False, "metadata_only"
        if text is None:
            return "", False, "metadata_only"
        if not trim:
            budgeted = self._budget.apply(text)
            return budgeted.text, budgeted.truncated, "pdf"
        trimmed = trim_print(text)
        if trimmed.dropped:
            log.info(
                "%s: %d chars, sending %d (dropped %s)",
                document.url,
                len(text),
                len(trimmed.text),
                ", ".join(f"{d.name} {d.chars}" for d in trimmed.dropped),
            )
        budgeted = self._budget.apply(trimmed.text)
        return budgeted.text, budgeted.truncated, "documents" if document.kind == "rcl" else "pdf"
