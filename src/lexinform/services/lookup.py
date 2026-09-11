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
    is_pre_print_number,
    is_rcl_number,
    normalize_wykaz_number,
    parse_reference,
    process_summary,
    rcl_fingerprint,
    rcl_number,
    rcl_project_id,
    rcl_stages,
)
from lexinform.ports import BillRepository, Clock, ProjectResolver, SejmGateway
from lexinform.services.rcl_projects import RclProjectReader
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
        text_prefilter: bool = True,
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._prefilter = prefilter
        self._clock = clock
        self._terms = terms
        self._rcl_reader = rcl_reader
        self._projects = projects
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
            return self._repo.find_by_wykaz_number(ref.value)
        if ref.kind is RefKind.RM:
            return self._repo.find_by_rm_number(ref.value)
        if ref.term is not None:
            return self._repo.get(ref.term, ref.value)
        return self.find(ref.value)

    def resolve_number(self, number: str) -> str:
        """A wykaz number (UC164) names the RCL row it belongs to; anything else is passed on."""
        ref = parse_reference(number)
        if ref is None or ref.kind is not RefKind.WYKAZ:
            return number
        wykaz = normalize_wykaz_number(number) or number
        bill = self._repo.find_by_wykaz_number(wykaz)
        if bill is None:
            raise BillNotFoundError(f"{wykaz}: no RCL project with this number in the database")
        return bill.number

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
            raise BillNotFoundError(f"{ref.value}: no RCL project with this number in the database")
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

    def find_submission(self, term: int, number: str) -> BillSubmission:
        sub = next((b for b in self._gateway.iter_bills(term) if b.number == number), None)
        if sub is None:
            raise BillNotFoundError(f"{number}: not found in /bills of term {term}")
        return sub

    def _fetch(self, term: int, number: str) -> Bill:
        project: RclProject | None = None
        if is_rcl_number(number):
            project = self.read_rcl_project(number)
            summary: ProcessSummary = process_summary(project, term=term)
            bill = self._repo.upsert_summary(summary, now=self._clock.now())
            self._repo.save_rcl(bill.term, bill.number, project)
            self._repo.save_stages(
                bill.term, bill.number, rcl_stages(project), rcl_fingerprint(project)
            )
            has_text = bool(project.text_documents())
        elif is_pre_print_number(number):
            sub = self.find_submission(term, number)
            summary = ProcessSummary.from_submission(sub)
            bill = self._repo.upsert_summary(summary, now=self._clock.now())
            self._repo.save_submission(bill.term, bill.number, sub)
            has_text = False  # the PDF on orka.sejm.gov.pl is not downloadable
        else:
            summary = self._gateway.get_process(term, number)
            bill = self._repo.upsert_summary(summary, now=self._clock.now())
            has_text = True
        hits = self._prefilter.match(summary.title, summary.description)
        if accept_title_hits(hits):
            status = BillStatus.ANALYSIS_PENDING
        elif self._text_prefilter and has_text:
            status = BillStatus.TEXT_PREFILTER_PENDING  # a title miss goes to the text stage
        else:
            status = BillStatus.SKIPPED_PREFILTER
        self._repo.set_status(bill.term, bill.number, status, prefilter_hits=hits)
        log.info("%s fetched on request: %s (%s)", number, summary.title, status.value)
        if project is not None:
            print_number = self._print_of(project, term)
            if print_number is not None:
                # The project already reached the Sejm: the print is the bill to work with, and
                # the RCL row joins its thread. Without this the project would get a card of its
                # own saying "waiting for a druk number", possibly for something already in force.
                printed = self._fetch(term, print_number)
                self._repo.link_bills(
                    term, bill.number, printed.number, wykaz_number=project.wykaz_number
                )
                log.info("%s is druk %s in the Sejm; linked", bill.number, printed.number)
                return self._repo.get(printed.term, printed.number) or printed
        stored = self._repo.get(bill.term, bill.number)
        assert stored is not None
        return stored

    def _print_of(self, project: RclProject, term: int) -> str | None:
        """The print number of a project that already went to the Sejm; None while it has not.
        The RM number the hand-over stage carries names the process (`ProcessSummary.rcl_num`)."""
        if project.print_number:
            return project.print_number
        if not project.rm_number:
            return None
        try:
            handover = next(
                (st.modified for st in reversed(project.stages) if st.is_sejm and st.reached), None
            )
            found = self._gateway.find_process_by_rcl_num(term, project.rm_number, since=handover)
        except ServiceUnavailableError:
            raise
        except Exception as exc:  # the project is still worth having without its print
            log.warning("looking up the print of %s failed: %s", project.rm_number, exc)
            return None
        return found.number if found is not None else None
