"""Finds new/changed bills in the Sejm API and runs the keyword prefilter on new ones."""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from lexinform.errors import ServiceUnavailableError
from lexinform.keywords import KeywordPrefilter, accept_title_hits
from lexinform.models import (
    BILL_DOCUMENT_TYPE,
    ActInfo,
    Bill,
    BillStatus,
    BillSubmission,
    DocumentType,
    ProcessSummary,
    Stage,
    is_over,
)
from lexinform.ports import BillRepository, Clock, EliGateway, ProjectResolver, SejmGateway
from lexinform.services.predecessors import rcl_predecessor

log = logging.getLogger(__name__)


@dataclass
class DiscoveryResult:
    """Counters of one discovery phase; `over` counts the bills first seen with the process
    already ended, which get neither an analysis nor a card."""

    seen: int = 0
    new: int = 0
    pre_print_seen: int = 0
    pre_print_new: int = 0
    prefilter_hits: int = 0
    rejected: list[str] = field(default_factory=list)
    over: int = 0


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
        local_tz: ZoneInfo = ZoneInfo("Europe/Warsaw"),
        projects: ProjectResolver | None = None,
        eli: EliGateway | None = None,
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._prefilter = prefilter
        self._clock = clock
        self._text_prefilter = text_prefilter
        self._local_tz = local_tz
        self._projects = projects
        self._eli = eli

    def discover(self, term: int, since: datetime, *, pre_print: bool = True) -> DiscoveryResult:
        result = DiscoveryResult()
        self._discover_processes(term, since, result)
        if pre_print:
            self._discover_submissions(term, since, result)
        log.info(
            "discovery: seen=%d new=%d pre_print_seen=%d pre_print_new=%d prefilter_hits=%d"
            " over=%d",
            result.seen,
            result.new,
            result.pre_print_seen,
            result.pre_print_new,
            result.prefilter_hits,
            result.over,
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
            log.info("Sejm API returned no bills modified since %s", since.isoformat())

    def _miss_status(self, summary: ProcessSummary) -> BillStatus:
        """A title miss goes on to the text stage, unless there is no document to read.

        A bill without a print number has one too: its file on orka.sejm.gov.pl. That host was
        taken for unreachable until the probe of 2026-09-12, and while it was, a title miss at
        the consultation stage ended the bill — the one stage where the reader still has a say.
        """
        if self._text_prefilter and (summary.has_process or summary.is_pre_print):
            return BillStatus.TEXT_PREFILTER_PENDING
        return BillStatus.SKIPPED_PREFILTER

    def _ended_before_first_sight(self, bill: Bill, now: datetime) -> bool:
        """ELI publication alone cannot prove that the act is already in force."""
        if bill.summary.eli is not None:
            act = None if self._eli is None else self._act_of(bill.summary.eli)
            if act is None:
                return False
            self._repo.save_act(bill.term, bill.number, act)
            bill = bill.model_copy(update={"act": act})
        elif bill.has_process:
            stages = self._stages_of(bill.summary)
            if stages is None:
                return False
            bill = bill.model_copy(update={"stages": stages})
        return is_over(bill, today=now.astimezone(self._local_tz).date())

    def _act_of(self, eli: str) -> ActInfo | None:
        """The published act, so that its vacatio legis can be seen; None when ELI has not
        indexed it yet (an act published today often is not)."""
        assert self._eli is not None, "_act_of is called only when an ELI client is wired"
        try:
            return self._eli.get_act(eli)
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.warning("act %s not read: %s", eli, exc)
            return None

    def _stages_of(self, summary: ProcessSummary) -> tuple[Stage, ...] | None:
        try:
            return self._gateway.get_process(summary.term, summary.number).stages
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.warning("process %s not read: %s", summary.number, exc)
            return None

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
        """Bills from /bills that have no print number yet: the earliest, consultation stage.

        Numbered prints come through `/processes` and closed ones are history, so neither is
        taken from here. A known entry is left to tracking, which compares the new `/bills` row
        with the stored one (print number assigned, withdrawn, opinions published).
        """
        for sub in self._gateway.iter_bills(term, received_from=since.date()):
            if not sub.is_bill or sub.print_number or sub.is_closed:
                continue
            result.pre_print_seen += 1
            summary = ProcessSummary.from_submission(sub)
            if self._ingest(summary, result):
                result.pre_print_new += 1
                self._repo.save_submission(sub.term, sub.number, sub)

    def _ingest(self, summary: ProcessSummary, result: DiscoveryResult) -> bool:
        """Upsert the summary; prefilter new bills (and re-prefilter skipped ones whose title
        changed). True when the bill is new.

        A print continuing a row we follow is not ingested here: tracking links the two and keeps
        the thread. A row in `NOT_FOLLOWED` has no thread, so its druk takes the normal path.
        """
        existing = self._repo.get(summary.term, summary.number)
        now = self._clock.now()
        if existing is None and (summary.closure_date or summary.eli):
            stored = self._repo.upsert_summary(summary, now=now)
            if self._ended_before_first_sight(stored, now):
                self._repo.set_status(
                    stored.term,
                    stored.number,
                    BillStatus.SKIPPED_CLOSED,
                    reason=f"the process ended on {summary.closure_date}, before it was first seen",
                )
                log.info("%s ended before we saw it: no analysis, no card", stored.number)
                result.over += 1
                return True
        submission = None
        if existing is None and summary.has_process:
            submission = self._find_submission(summary)
            if submission is not None:
                if self._repo.get(summary.term, submission.number) is not None:
                    log.info(
                        "druk %s continues %s; linked by tracking",
                        summary.number,
                        submission.number,
                    )
                    return False
                summary = summary.model_copy(update={"applicant": submission.applicant})
            project = rcl_predecessor(self._repo, self._projects, summary.rcl_num)
            if project is not None:
                assert project.rcl is not None, (
                    "rcl_predecessor returns only a row with its project"
                )
                self._repo.save_rcl(
                    project.term,
                    project.number,
                    project.rcl.model_copy(
                        update={"print_number": summary.number, "rm_number": summary.rcl_num}
                    ),
                )
                log.info("druk %s continues %s; linked by tracking", summary.number, project.number)
                return False
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
            candidate = accept_title_hits(hits)
            status = BillStatus.ANALYSIS_PENDING if candidate else self._miss_status(summary)
            self._repo.set_status(bill.term, bill.number, status, prefilter_hits=hits)
            if status is BillStatus.SKIPPED_PREFILTER:
                result.rejected.append(f"{bill.term}/{bill.number}")
            if candidate:
                result.prefilter_hits += 1
                log.info("candidate druk %s (%s): %s", bill.number, ", ".join(hits), summary.title)
        return is_new
