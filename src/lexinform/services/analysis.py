"""Turns a prefilter candidate into an LLM analysis: stages, print, PDF text, model call.

Also re-analyses a bill when a newer text appears (committee report with amendments, text after the
3rd reading, or an updated print), feeding the previous analysis to the model so it can say what
changed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from lexinform.adapters.pdf_text import TextBudget
from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    AnalysisRecord,
    Bill,
    BillContext,
    BillStatus,
    PrintInfo,
    ProcessDetail,
    TextDocument,
    TextSource,
    latest_text_document,
    stage_fingerprint,
)
from lexinform.ports import BillRepository, LlmAnalyzer, SejmGateway, TextExtractor

log = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    analyzed: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    fatal_error: str | None = None


class AnalysisService:
    def __init__(
        self,
        gateway: SejmGateway,
        repo: BillRepository,
        extractor: TextExtractor,
        llm: LlmAnalyzer,
        *,
        text_budget: TextBudget,
        max_pdf_bytes: int,
        max_attempts: int = 3,
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._extractor = extractor
        self._llm = llm
        self._budget = text_budget
        self._max_pdf_bytes = max_pdf_bytes
        self._max_attempts = max_attempts

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
        for bill in candidates:
            try:
                record = self.analyze_bill(bill)
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
            result.analyzed += 1
            result.input_tokens += record.input_tokens or 0
            result.output_tokens += record.output_tokens or 0
        return result

    def analyze_bill(self, bill: Bill) -> AnalysisRecord:
        """First analysis from the original print. Persists the result; raises on failure."""
        detail = self._gateway.get_process(bill.term, bill.number)
        self._repo.save_stages(
            bill.term, bill.number, detail.stages, stage_fingerprint(detail.stages)
        )
        print_info = self._safe_print(bill)
        document = self._original_document(print_info)
        return self._run(bill, detail, document, previous=None)

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
        return self._run(bill, detail, document, previous=bill.analysis)

    # ------------------------------------------------------------------ internals

    def _run(
        self,
        bill: Bill,
        detail: ProcessDetail,
        document: TextDocument | None,
        *,
        previous: AnalysisRecord | None,
    ) -> AnalysisRecord:
        text, truncated, source = self._load_text(document)
        ctx = BillContext(
            number=bill.number,
            title=detail.title or bill.summary.title,
            description=detail.description or bill.summary.description,
            document_date=detail.document_date or bill.summary.document_date,
            applicant_type=detail.applicant_type,
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
        self._repo.save_analysis(bill.term, bill.number, record)
        return record

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
            return self._load_pdf_text(document)
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.warning(
                "%s unreadable (%s: %s); using metadata only", document.url, type(exc).__name__, exc
            )
            return "", False, "metadata_only"

    def _load_pdf_text(self, document: TextDocument) -> tuple[str, bool, TextSource]:
        size = self._gateway.attachment_size(document.url)
        if size is not None and size > self._max_pdf_bytes:
            log.warning("%s is %d bytes, over limit; using metadata only", document.url, size)
            return "", False, "metadata_only"
        data = self._gateway.download(document.url)
        if len(data) > self._max_pdf_bytes:
            log.warning("%s is %d bytes, over limit; using metadata only", document.url, len(data))
            return "", False, "metadata_only"
        text = self._extractor.extract(data)
        if len(text.strip()) < 200:
            log.warning("%s yielded almost no text (%d chars)", document.url, len(text))
            return "", False, "metadata_only"
        budgeted = self._budget.apply(text)
        return budgeted.text, budgeted.truncated, "pdf"


def _as_utc(value: datetime) -> datetime:
    """Naive datetimes in our own records are UTC; API timestamps arrive already aware."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
