"""One bill by its number or reference, for the operator commands (CLI and the channel).

The database first; a bill the bot has never seen is fetched from the Sejm API (a print or a
`/bills` entry) or from RCL (the whole project) and prefiltered by title the way discovery does,
so that `analyze`, `show` and the channel's `/analyze` work on any bill, not only on those a
run has discovered.
"""

import logging

from lexinform.errors import ServiceUnavailableError
from lexinform.keywords import KeywordPrefilter, accept_title_hits
from lexinform.models import (
    Bill,
    BillRef,
    BillStatus,
    BillSubmission,
    ProcessSummary,
    RclProject,
    RefKind,
    WykazEntry,
    is_pre_print_number,
    is_rcl_number,
    is_wykaz_number,
    parse_reference,
    process_summary,
    rcl_fingerprint,
    rcl_number,
    rcl_project_id,
    rcl_stages,
    wykaz_entry_number,
    wykaz_fingerprint,
    wykaz_number,
    wykaz_summary,
)
from lexinform.ports import BillRepository, Clock, ProjectResolver, SejmGateway, WykazGateway
from lexinform.services.discovery import NOT_FOLLOWED
from lexinform.services.rcl_projects import RclProjectReader
from lexinform.services.sources import print_of_project
from lexinform.services.terms import TermResolver

log = logging.getLogger(__name__)


class BillNotFoundError(LookupError):
    """Neither the database nor the source system knows the bill."""


class BillLookup:
    def __init__(
        self,
        gateway: SejmGateway,
        repo: BillRepository,
        prefilter: KeywordPrefilter,
        clock: Clock,
        terms: TermResolver,
        *,
        rcl_reader: RclProjectReader | None = None,
        projects: ProjectResolver | None = None,
        wykaz: WykazGateway | None = None,
        text_prefilter: bool = True,
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._prefilter = prefilter
        self._clock = clock
        self._terms = terms
        self._rcl_reader = rcl_reader
        self._projects = projects
        self._wykaz = wykaz
        self._text_prefilter = text_prefilter

    def find(self, number: str) -> Bill | None:
        """The stored row for a number: the working term first, then older terms (print numbers
        restart with every kadencja, RPW and RCL numbers do not)."""
        current = self._terms.current()
        older = [t for t in reversed(self._repo.known_terms()) if t != current]
        for term in (current, *older):
            bill = self._repo.get(term, number)
            if bill is not None:
                return bill
        return None

    def find_ref(self, ref: BillRef) -> Bill | None:
        """The stored row a reference points at, without touching the network for a miss."""
        if ref.kind is RefKind.WYKAZ:
            # The RCL row first: once the project is out, it is the row with the text.
            found = self._repo.find_by_wykaz_number(ref.value)
            return found or self._repo.find_wykaz(wykaz_number(ref.value))
        if ref.kind is RefKind.RM:
            return self._repo.find_by_rm_number(ref.value)
        if ref.term is not None:
            return self._repo.get(ref.term, ref.value)
        return self.find(ref.value)

    def resolve_number(self, number: str) -> str:
        """A wykaz number (UC164) names the row it belongs to — the RCL project when there is
        one, else the register entry; anything else is passed on."""
        ref = parse_reference(number)
        if ref is None or ref.kind is not RefKind.WYKAZ:
            return number
        bill = self._repo.find_by_wykaz_number(ref.value)
        return bill.number if bill is not None else wykaz_number(ref.value)

    def load(self, number: str) -> Bill:
        """The bill from the database, fetched from the API (or RCL) and prefiltered on first
        sight; raises `BillNotFoundError` when no system knows it."""
        number = self.resolve_number(number)
        bill = self.find(number)
        if bill is not None:
            return bill
        return self._fetch(self._terms.current(), number)

    def load_ref(self, ref: BillRef) -> Bill:
        """The bill behind a reference; an RM number is resolved to its RCL project first."""
        bill = self.find_ref(ref)
        if bill is not None:
            return bill
        if ref.kind is RefKind.WYKAZ:
            return self.load(wykaz_number(ref.value))
        if ref.kind is RefKind.RM:
            if self._projects is None:
                raise BillNotFoundError(f"{ref.value}: RCL is disabled, cannot look the number up")
            project_id = self._projects.resolve_project_id(ref.value)
            if project_id is None:
                raise BillNotFoundError(f"{ref.value}: RCL knows no project with this number")
            return self.load(rcl_number(project_id))
        term = ref.term if ref.term is not None else self._terms.current()
        return self._fetch(term, ref.value)

    def read_rcl_project(self, number: str) -> RclProject:
        """The whole project from RCL: timeline, every reached stage's catalog, the letter."""
        if self._rcl_reader is None:
            raise BillNotFoundError(f"{number}: RCL is disabled (LEXINFORM_RCL_ENABLED=false)")
        return self._rcl_reader.complete(self._rcl_reader.timeline(rcl_project_id(number)))

    def read_wykaz_entry(self, number: str) -> WykazEntry:
        """One entry of the wykaz prac RM, straight from the register."""
        if self._wykaz is None:
            raise BillNotFoundError(f"{number}: the wykaz is disabled (LEXINFORM_WYKAZ_ENABLED)")
        entry = self._wykaz.find(wykaz_entry_number(number))
        if entry is None:
            raise BillNotFoundError(f"{number}: no such entry in the wykaz prac RM")
        return entry

    def find_submission(self, term: int, number: str) -> BillSubmission:
        sub = next((b for b in self._gateway.iter_bills(term) if b.number == number), None)
        if sub is None:
            raise BillNotFoundError(f"{number}: not found in /bills of term {term}")
        return sub

    def _fetch(self, term: int, number: str) -> Bill:
        """Read a bill the database has never seen from its source system, store it and
        prefilter it by title. An entry and the druk it became are linked whichever of the two
        was asked for, and the druk is what comes back: it is the bill to work with."""
        if is_rcl_number(number):
            return self._fetch_project(term, number)
        if is_pre_print_number(number):
            return self._fetch_entry(term, number)
        if is_wykaz_number(number):
            return self._fetch_plan(term, number)
        return self._fetch_print(term, number)

    def _fetch_project(self, term: int, number: str) -> Bill:
        """A government project read whole from RCL: timeline, catalogs, consultation letter."""
        project = self.read_rcl_project(number)
        summary = process_summary(project, term=term)
        bill = self._repo.upsert_summary(summary, now=self._clock.now())
        self._repo.save_rcl(bill.term, bill.number, project)
        self._repo.save_stages(
            bill.term, bill.number, rcl_stages(project), rcl_fingerprint(project)
        )
        self._prefilter_by_title(bill, summary, has_text=bool(project.text_documents()))
        print_number = print_of_project(self._gateway, project, term)
        if print_number is None:
            return self._stored(bill)
        return self._link(bill, print_number, wykaz_number=project.wykaz_number)

    def _fetch_plan(self, term: int, number: str) -> Bill:
        """An entry of the wykaz prac RM: the government's own words about a bill it has not
        drafted yet. The whole register is one download, so this costs nothing extra in a run
        that already read it."""
        entry = self.read_wykaz_entry(number)
        summary = wykaz_summary(entry, term=term)
        bill = self._repo.upsert_summary(summary, now=self._clock.now())
        self._repo.save_wykaz(bill.term, bill.number, entry)
        self._repo.save_stages(bill.term, bill.number, (), wykaz_fingerprint(entry))
        self._prefilter_by_title(bill, summary, has_text=False)
        return self._stored(bill)

    def _fetch_entry(self, term: int, number: str) -> Bill:
        """A `/bills` entry (RPW): applicant and consultation dates, no text to read (its PDF on
        orka.sejm.gov.pl is behind Incapsula). The entry names its print as soon as it has one."""
        sub = self.find_submission(term, number)
        summary = ProcessSummary.from_submission(sub)
        bill = self._repo.upsert_summary(summary, now=self._clock.now())
        self._repo.save_submission(bill.term, bill.number, sub)
        self._prefilter_by_title(bill, summary, has_text=False)
        if not sub.print_number:
            return self._stored(bill)
        return self._link(bill, sub.print_number)

    def _fetch_print(self, term: int, number: str) -> Bill:
        """A numbered print, with the `/bills` entry it came from: the entry carries the
        applicant and the consultation dates the card needs, and names the row the print
        continues when the bot already follows the bill under the entry's RPW number."""
        summary = self._gateway.get_process(term, number)
        sub = self._submission_of(term, number)
        if sub is not None:
            summary = summary.model_copy(update={"applicant": sub.applicant})
        bill = self._repo.upsert_summary(summary, now=self._clock.now())
        if sub is not None:
            self._repo.save_submission(bill.term, bill.number, sub)
        self._prefilter_by_title(bill, summary, has_text=True)
        entry = self._repo.get(term, sub.number) if sub is not None else None
        if entry is not None and entry.number != bill.number:
            return self._link(entry, bill.number)
        project = self._project_of(summary)
        if project is None or project.rcl is None:
            return self._stored(bill)
        return self._link(project, bill.number, wykaz_number=project.rcl.wykaz_number)

    def _prefilter_by_title(self, bill: Bill, summary: ProcessSummary, *, has_text: bool) -> None:
        """The status a bill fetched on request starts from, as discovery would decide it."""
        hits = self._prefilter.match(summary.title, summary.description)
        if accept_title_hits(hits):
            status = BillStatus.ANALYSIS_PENDING
        elif self._text_prefilter and has_text:
            status = BillStatus.TEXT_PREFILTER_PENDING  # a title miss goes to the text stage
        else:
            status = BillStatus.SKIPPED_PREFILTER
        self._repo.set_status(bill.term, bill.number, status, prefilter_hits=hits)
        log.info("%s fetched on request: %s (%s)", bill.number, summary.title, status)

    def _link(self, pre: Bill, print_number: str, *, wykaz_number: str | None = None) -> Bill:
        """`pre` (an RPW entry or an RCL project) already became a druk: the druk is the bill to
        work with and the entry joins its thread. Without this the entry would get a card of its
        own saying "waiting for a druk number", possibly for something already in force."""
        printed = self._repo.get(pre.term, print_number) or self._fetch(pre.term, print_number)
        self._repo.link_bills(pre.term, pre.number, printed.number, wykaz_number=wykaz_number)
        log.info("%s is druk %s; linked", pre.number, printed.number)
        return self._stored(printed)

    def _project_of(self, summary: ProcessSummary) -> Bill | None:
        """The RCL project a government print continues, when the bot follows it: `rclNum` names
        it, through the stored RM number or one `getIdFromLegislacja` request, as in discovery."""
        if not summary.rcl_num:
            return None
        bill = self._repo.find_by_rm_number(summary.rcl_num)
        if bill is None and self._projects is not None:
            try:
                project_id = self._projects.resolve_project_id(summary.rcl_num)
            except Exception as exc:  # RCL is unreachable from CI: the print stands on its own
                log.warning("RCL lookup of %s skipped: %s", summary.rcl_num, exc)
                return None
            if project_id is not None:
                bill = self._repo.find_rcl(rcl_number(project_id))
        if bill is None or bill.status in NOT_FOLLOWED:
            return None
        return bill

    def _submission_of(self, term: int, print_number: str) -> BillSubmission | None:
        """The `/bills` entry of a print: one filtered request, as discovery makes it."""
        try:
            return self._gateway.find_submission(term, print_number)
        except ServiceUnavailableError:
            raise
        except Exception as exc:  # the print is worth having without its consultation dates
            log.warning("submission lookup for druk %s failed: %s", print_number, exc)
            return None

    def _stored(self, bill: Bill) -> Bill:
        fresh = self._repo.get(bill.term, bill.number)
        assert fresh is not None
        return fresh
