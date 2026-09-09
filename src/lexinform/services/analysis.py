"""Turns a prefilter candidate into an LLM analysis: locate the text, read it, ask the model.

Also re-analyses a bill when a newer text appears (committee report with amendments, text after the
3rd reading, an updated print, a new version of an RCL project), feeding the previous analysis to
the model so it can say what changed. Where the text comes from is the `TextSource`'s business.
"""

import logging
from dataclasses import dataclass, field

from lexinform.concurrency import fan_out
from lexinform.errors import ServiceUnavailableError
from lexinform.keywords import KeywordPrefilter
from lexinform.models import (
    FULL_TEXT_SOURCES,
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
from lexinform.sections import TextBudget, excerpts, trim_print
from lexinform.services.documents import TextLoader

log = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    """Counters and verdicts of one analysis phase."""

    analyzed: int = 0
    triaged_out: int = 0
    failed: int = 0
    verdicts: list[AnalysisVerdict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    usage: dict[str, TokenUsage] = field(default_factory=dict)  # per model
    fatal_error: str | None = None


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
        self._triage = triage  # the keyword patterns that cut the excerpts; None disables it
        self._triage_min_chars = triage_min_chars
        self._triage_min_confidence = triage_min_confidence

    # ------------------------------------------------------------------ first analysis

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
        return result

    def analyze_bill(self, bill: Bill) -> AnalysisRecord:
        """First analysis from the original text. Persists the result; raises on failure."""
        return self._persist(self._prepare_first(bill))

    def _prepare_first(self, bill: Bill) -> _Prepared:
        return self._prepare(bill, self._texts.locate(bill), previous=None)

    # ------------------------------------------------------------------ re-analysis

    def reanalyze_bill(
        self, bill: Bill, document: TextDocument, *, summary: ProcessSummary | None = None
    ) -> AnalysisRecord:
        """Analyse a newer text of an already analysed bill; the model also reports what changed.
        `summary` is fresher metadata than the stored one, when the caller has it."""
        assert bill.analysis is not None
        located = LocatedText(summary=summary, document=document)
        return self._persist(self._prepare(bill, located, previous=bill.analysis))

    # ------------------------------------------------------------------ internals

    def _prepare(
        self, bill: Bill, located: LocatedText, *, previous: AnalysisRecord | None
    ) -> _Prepared:
        """Load the text and ask the model. Network only: safe to run for several bills at once."""
        document = located.document
        text, truncated, source = self._load_text(document)
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
            source_kind=document.kind if document and source in FULL_TEXT_SOURCES else "metadata",
            previous_summary=previous.analysis.summary if previous else None,
            previous_key_changes=list(previous.analysis.key_changes) if previous else [],
        )
        record = self._llm.analyze(ctx)
        record.source_url = document.url if document else None
        record.source_kind = ctx.source_kind
        record.revision = previous.revision + 1 if previous else 1
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

    def _load_text(self, document: TextDocument | None) -> tuple[str, bool, TextSource]:
        """Trimmed, budgeted text of the document; metadata-only when it cannot be fetched or read.

        Only an outage of the document's host propagates: a missing or broken file falls back to
        the metadata instead of costing the bill an analysis attempt.
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
