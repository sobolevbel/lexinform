"""Turns a prefilter candidate into an LLM analysis: stages, print, PDF text, model call.

Also re-analyses a bill when a newer text appears (committee report with amendments, text after the
3rd reading, or an updated print), feeding the previous analysis to the model so it can say what
changed.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from lexinform.authors import MpDirectory, parse_cover_letter
from lexinform.concurrency import fan_out
from lexinform.errors import ServiceUnavailableError
from lexinform.keywords import KeywordPrefilter
from lexinform.models import (
    Analysis,
    AnalysisRecord,
    AnalysisVerdict,
    ApplicantType,
    Bill,
    BillContext,
    BillStatus,
    Category,
    PrintInfo,
    ProcessDetail,
    ProcessSummary,
    TextDocument,
    TextSource,
    TokenUsage,
    TriageContext,
    TriageRecord,
    add_usage,
    latest_text_document,
    stage_fingerprint,
)
from lexinform.ports import BillRepository, Clock, LlmAnalyzer, SejmGateway
from lexinform.sections import TextBudget, excerpts, trim_print
from lexinform.services.documents import PdfTextLoader

log = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
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
    detail: ProcessDetail | None
    document: TextDocument | None
    text: str
    source: TextSource
    record: AnalysisRecord
    first: bool  # first analysis seeds the stages; a re-analysis leaves them to tracking
    triage: TriageRecord | None = None  # a triage the bill passed: its tokens count too


class AnalysisService:
    def __init__(
        self,
        gateway: SejmGateway,
        repo: BillRepository,
        loader: PdfTextLoader,
        llm: LlmAnalyzer,
        clock: Clock,
        *,
        text_budget: TextBudget,
        max_attempts: int = 3,
        workers: int = 1,
        triage: KeywordPrefilter | None = None,
        triage_min_chars: int = 20_000,
        triage_min_confidence: float = 0.8,
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._loader = loader
        self._llm = llm
        self._clock = clock
        self._budget = text_budget
        self._max_attempts = max_attempts
        self._workers = workers
        self._triage = triage  # the keyword patterns that cut the excerpts; None disables it
        self._triage_min_chars = triage_min_chars
        self._triage_min_confidence = triage_min_confidence
        self._mps: MpDirectory | None = None

    # ------------------------------------------------------------------ first analysis

    def analyze_pending(self, term: int, *, limit: int) -> AnalysisResult:
        result = AnalysisResult()
        if limit <= 0:
            return result
        candidates = self._repo.list_by_status(
            term,
            [BillStatus.ANALYSIS_PENDING, BillStatus.ANALYSIS_FAILED],
            limit=limit,
            max_attempts=self._max_attempts,
        )
        # Fetching the print and waiting for the model happen in parallel; results are written
        # here, one bill at a time, in the order they were listed.
        for outcome in fan_out(candidates, self._prepare_first, workers=self._workers):
            bill = outcome.item
            try:
                prepared = outcome.result()
                record = self._persist(prepared)
            except ServiceUnavailableError as exc:
                # Not the bill's fault: stop the phase without consuming its retry attempts.
                result.fatal_error = exc.describe()
                log.error("aborting analysis phase: %s", result.fatal_error)
                break
            except Exception as exc:  # per-bill isolation: one failure must not block the rest
                result.failed += 1
                log.exception("analysis failed for druk %s: %s", bill.number, exc)
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
        """First analysis from the original print. Persists the result; raises on failure."""
        return self._persist(self._prepare_first(bill))

    def _prepare_first(self, bill: Bill) -> _Prepared:
        if bill.is_pre_print:
            # No process, no print, and the PDF sits behind the Sejm website's bot protection:
            # the official title and description are all we have at this stage.
            return self._prepare(bill, None, None, previous=None)
        detail = self._gateway.get_process(bill.term, bill.number)
        print_info = self._safe_print(bill)
        document = self._original_document(print_info)
        return self._prepare(bill, detail, document, previous=None)

    # ------------------------------------------------------------------ re-analysis

    def newer_document(
        self, bill: Bill, detail: ProcessDetail, print_info: PrintInfo | None
    ) -> TextDocument | None:
        """The document to re-analyse from, if the bill text changed since the stored analysis."""
        record = bill.analysis
        if record is None:
            return None
        candidate = latest_text_document(detail.stages) or self._original_document(print_info)
        if candidate is None:
            return None
        if candidate.url != record.source_url:
            return candidate
        if (
            candidate.kind == "print"
            and print_info is not None
            and print_info.change_date is not None
            and _as_utc(print_info.change_date) > _as_utc(record.created_at)
        ):
            return candidate
        return None

    def reanalyze_bill(
        self, bill: Bill, detail: ProcessDetail, document: TextDocument
    ) -> AnalysisRecord:
        """Analyse a newer text of an already analysed bill; the model also reports what changed."""
        assert bill.analysis is not None
        return self._persist(self._prepare(bill, detail, document, previous=bill.analysis))

    # ------------------------------------------------------------------ internals

    def _prepare(
        self,
        bill: Bill,
        detail: ProcessDetail | None,
        document: TextDocument | None,
        *,
        previous: AnalysisRecord | None,
    ) -> _Prepared:
        """Load the text and ask the model. Network only: safe to run for several bills at once."""
        text, truncated, source = self._load_text(document)
        meta = detail or bill.summary
        triage: TriageRecord | None = None
        if previous is None and source == "pdf" and len(text) >= self._triage_min_chars:
            triage, rejection = self._triage_verdict(bill, meta, text)
            if rejection is not None:
                return _Prepared(bill, detail, document, text, source, rejection, first=True)
        ctx = BillContext(
            number=bill.number,
            title=meta.title or bill.summary.title,
            description=meta.description or bill.summary.description,
            document_date=meta.document_date or bill.summary.document_date,
            applicant_type=meta.applicant_type,
            text=text,
            truncated=truncated,
            text_source=source,
            source_kind=document.kind if document and source == "pdf" else "metadata",
            previous_summary=previous.analysis.summary if previous else None,
            previous_key_changes=list(previous.analysis.key_changes) if previous else [],
        )
        record = self._llm.analyze(ctx)
        record.source_url = document.url if document else None
        record.source_kind = ctx.source_kind
        record.revision = previous.revision + 1 if previous else 1
        return _Prepared(
            bill, detail, document, text, source, record, first=previous is None, triage=triage
        )

    def _triage_verdict(
        self, bill: Bill, meta: ProcessSummary, text: str
    ) -> tuple[TriageRecord | None, AnalysisRecord | None]:
        """Ask a cheap first question on excerpts; a confident "no" becomes the final record.

        Long prints are where the money goes, and half of them turn out to be about something
        else: the keyword prefilter is deliberately over-inclusive. A rejected bill is stored as a
        regular, non-relevant analysis (model and text_source say how it was decided).
        Returns the triage record and, when it rejects the bill, that final record.
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
                "druk %s passes triage (%s, %.2f): full analysis",
                bill.number,
                "relevant" if verdict.triage.affects_foreigners else "unsure",
                verdict.triage.confidence,
            )
            return verdict, None
        log.info(
            "druk %s rejected by triage (%.2f): %s",
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
        bill, detail, document = prepared.bill, prepared.detail, prepared.document
        if prepared.first and detail is not None:
            self._repo.save_stages(
                bill.term, bill.number, detail.stages, stage_fingerprint(detail.stages)
            )
        if prepared.source == "pdf" and document is not None and document.kind == "print":
            self._save_authors(bill, (detail or bill.summary).applicant_type, prepared.text)
        self._repo.save_analysis(bill.term, bill.number, prepared.record)
        return prepared.record

    def _save_authors(self, bill: Bill, applicant: ApplicantType, text: str) -> None:
        """Signatories (deputies' bills) or the representative (committee bills) from the cover
        letter. Best effort: never fails the analysis."""
        if applicant not in (ApplicantType.DEPUTIES, ApplicantType.COMMITTEE):
            return
        try:
            letter = parse_cover_letter(text)
            if not letter.signatories and not letter.representative:
                return
            if self._mps is None:
                self._mps = MpDirectory.from_mps(self._gateway.list_mps(bill.term))
            self._repo.save_authors(bill.term, bill.number, self._mps.resolve(letter))
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.warning("authors of druk %s not resolved: %s", bill.number, exc)

    @staticmethod
    def _original_document(print_info: PrintInfo | None) -> TextDocument | None:
        pdf = print_info.main_pdf if print_info else None
        return TextDocument(url=pdf.url, kind="print") if pdf else None

    def _safe_print(self, bill: Bill) -> PrintInfo | None:
        try:
            return self._gateway.get_print(bill.term, bill.number)
        except Exception as exc:
            log.warning("print %s unavailable: %s", bill.number, exc)
            return None

    def _load_text(self, document: TextDocument | None) -> tuple[str, bool, TextSource]:
        """Text of the document, or metadata-only when it cannot be fetched or read.

        Only an outage of the Sejm API propagates; a missing file or a broken PDF is not a reason to
        burn one of the bill's analysis attempts, the designed fallback is analysing the metadata.
        """
        if document is None:
            return "", False, "metadata_only"
        try:
            text = self._loader.load(document.url)
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
        return budgeted.text, budgeted.truncated, "pdf"


def _as_utc(value: datetime) -> datetime:
    """Naive datetimes in our own records are UTC; API timestamps arrive already aware."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
