"""Finds new/changed bills in the Sejm API and runs the keyword prefilter on new ones."""

import logging
from dataclasses import dataclass
from datetime import datetime

from lexinform.errors import ServiceUnavailableError
from lexinform.keywords import KeywordPrefilter
from lexinform.models import (
    BILL_DOCUMENT_TYPE,
    BillStatus,
    BillSubmission,
    DocumentType,
    ProcessSummary,
)
from lexinform.ports import BillRepository, Clock, SejmGateway

log = logging.getLogger(__name__)


@dataclass
class DiscoveryResult:
    """Counters of one discovery phase."""

    seen: int = 0
    new: int = 0
    pre_print_seen: int = 0
    pre_print_new: int = 0
    prefilter_hits: int = 0


class BillDiscoveryService:
    """Stores every bill the API lists as new or changed and prefilters the new ones by title."""

    def __init__(
        self,
        gateway: SejmGateway,
        repo: BillRepository,
        prefilter: KeywordPrefilter,
        clock: Clock,
        *,
        text_prefilter: bool = True,
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._prefilter = prefilter
        self._clock = clock
        self._text_prefilter = text_prefilter

    def discover(self, term: int, since: datetime, *, pre_print: bool = True) -> DiscoveryResult:
        result = DiscoveryResult()
        self._discover_processes(term, since, result)
        if pre_print:
            self._discover_submissions(term, since, result)
        log.info(
            "discovery: seen=%d new=%d pre_print_seen=%d pre_print_new=%d prefilter_hits=%d",
            result.seen,
            result.new,
            result.pre_print_seen,
            result.pre_print_new,
            result.prefilter_hits,
        )
        return result

    def _discover_processes(self, term: int, since: datetime, result: DiscoveryResult) -> None:
        for summary in self._gateway.iter_processes(
            term, modified_since=since, document_type=BILL_DOCUMENT_TYPE
        ):
            if summary.document_type_enum != DocumentType.BILL:
                continue
            result.seen += 1
            if self._ingest(summary, result):
                result.new += 1
        if result.seen == 0:
            log.warning("Sejm API returned no bills modified since %s", since.isoformat())

    def _miss_status(self, summary: ProcessSummary) -> BillStatus:
        """A title miss goes on to the text stage, unless there is no print to read."""
        if self._text_prefilter and not summary.is_pre_print:
            return BillStatus.TEXT_PREFILTER_PENDING
        return BillStatus.SKIPPED_PREFILTER

    def _find_submission(self, summary: ProcessSummary) -> BillSubmission | None:
        """The /bills entry of a numbered print: consultation dates, applicant, RPW number."""
        try:
            return self._gateway.find_submission(summary.term, summary.number)
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.warning("submission lookup for druk %s failed: %s", summary.number, exc)
            return None

    def _discover_submissions(self, term: int, since: datetime, result: DiscoveryResult) -> None:
        """Bills from /bills that have no print number yet: the earliest, consultation stage."""
        for sub in self._gateway.iter_bills(term, received_from=since.date()):
            if not sub.is_bill or sub.print_number or sub.is_closed:
                continue  # numbered prints come through /processes; closed ones are history
            result.pre_print_seen += 1
            summary = ProcessSummary.from_submission(sub)
            if self._ingest(summary, result):
                result.pre_print_new += 1
                # Known entries are refreshed by tracking, which compares the new /bills row with
                # the stored one (print number assigned, withdrawn, opinions published).
                self._repo.save_submission(sub.term, sub.number, sub)

    def _ingest(self, summary: ProcessSummary, result: DiscoveryResult) -> bool:
        """Upsert the summary; prefilter new bills (and re-prefilter skipped ones whose title
        changed). True when the bill is new."""
        existing = self._repo.get(summary.term, summary.number)
        now = self._clock.now()
        submission = None
        if existing is None and not summary.is_pre_print:
            submission = self._find_submission(summary)
            if submission is not None:
                if self._repo.get(summary.term, submission.number) is not None:
                    # Already followed under its RPW number: tracking links the two and keeps
                    # the Telegram thread; a second card here would duplicate it.
                    log.info(
                        "druk %s continues %s; linked by tracking",
                        summary.number,
                        submission.number,
                    )
                    return False
                summary = summary.model_copy(update={"applicant": submission.applicant})
        bill = self._repo.upsert_summary(summary, now=now)
        if submission is not None:
            self._repo.save_submission(bill.term, bill.number, submission)
        is_new = existing is None
        title_changed = existing is not None and existing.summary.title != summary.title
        needs_prefilter = is_new or (
            title_changed
            and existing is not None
            and existing.status in (BillStatus.SKIPPED_PREFILTER, BillStatus.SKIPPED_TEXT_PREFILTER)
        )
        if needs_prefilter:
            hits = self._prefilter.match(summary.title, summary.description)
            status = BillStatus.ANALYSIS_PENDING if hits else self._miss_status(summary)
            self._repo.set_status(bill.term, bill.number, status, prefilter_hits=hits)
            if hits:
                result.prefilter_hits += 1
                log.info("candidate druk %s (%s): %s", bill.number, ", ".join(hits), summary.title)
        return is_new
