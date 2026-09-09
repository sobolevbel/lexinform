"""In-memory fakes for the ports, used by the service and pipeline tests.

Every fake records what was asked of it (`calls`, `contexts`, `new_bills`, …) so tests assert on
observable behaviour, and can be told to fail like the real system would: `outages` on the
gateway and `outage_on` on the publisher raise the phase-fatal `ServiceUnavailableError`,
`fail_on` raises an ordinary per-bill error.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from lexinform.adapters.llm_prompts import PROMPT_VERSION
from lexinform.errors import (
    AttachmentTooLargeError,
    RclUnavailableError,
    SejmApiUnavailableError,
    TelegramUnavailableError,
)
from lexinform.models import (
    ActInfo,
    AgendaItem,
    Analysis,
    AnalysisRecord,
    Bill,
    BillContext,
    BillSubmission,
    Category,
    Committee,
    CommitteeSitting,
    Mp,
    PrintInfo,
    ProcessDetail,
    ProcessSummary,
    RclProject,
    RclProjectSummary,
    RclStage,
    RunReport,
    SejmSitting,
    SejmTerm,
    StatusChange,
    Triage,
    TriageContext,
    TriageRecord,
    Vote,
)


class FixedClock:
    """A clock that moves only when a test tells it to."""

    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime(2026, 9, 7, 6, 0, tzinfo=UTC)  # a Monday

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs: int) -> None:
        self.current += timedelta(**kwargs)


@dataclass
class FakeSejmGateway:
    """`SejmGateway` and `EliGateway` over dictionaries; missing entries raise per-item errors."""

    processes: list[ProcessSummary] = field(default_factory=list)
    details: dict[str, ProcessDetail] = field(default_factory=dict)
    prints: dict[str, PrintInfo] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)
    votings: dict[tuple[int, int], tuple[Vote, ...]] = field(default_factory=dict)
    committees: dict[str, Committee] = field(default_factory=dict)
    mps: tuple[Mp, ...] = ()
    submissions: list[BillSubmission] = field(default_factory=list)
    acts: dict[str, ActInfo] = field(default_factory=dict)
    committee_sittings: dict[str, tuple[CommitteeSitting, ...]] = field(default_factory=dict)
    sittings: list[SejmSitting] = field(default_factory=list)  # with agendas
    terms: list[SejmTerm] = field(
        default_factory=lambda: [SejmTerm(num=10, start=date(2023, 11, 13), current=True)]
    )
    outages: set[str] = field(default_factory=set)  # method names that behave as "API down"
    calls: list[str] = field(default_factory=list)

    def _called(self, method: str, detail: str = "") -> None:
        self.calls.append(f"{method}:{detail}" if detail else method)
        if method in self.outages:
            raise SejmApiUnavailableError(f"{method}: connection refused")

    def list_terms(self) -> tuple[SejmTerm, ...]:
        self._called("list_terms")
        return tuple(self.terms)

    def iter_processes(
        self, term: int, *, modified_since: datetime | None = None, document_type: str | None = None
    ) -> Iterator[ProcessSummary]:
        self._called("iter_processes", str(term))
        for p in self.processes:
            if modified_since is None or p.change_date >= modified_since.replace(tzinfo=None):
                yield p

    def iter_bills(
        self, term: int, *, received_from: date | None = None
    ) -> Iterator[BillSubmission]:
        self._called("iter_bills")
        for sub in self.submissions:
            if received_from is None or sub.date_of_receipt >= received_from:
                yield sub

    def find_submission(self, term: int, print_number: str) -> BillSubmission | None:
        self._called("find_submission", print_number)
        return next((s for s in self.submissions if s.print_number == print_number), None)

    def get_process(self, term: int, number: str) -> ProcessDetail:
        self._called("get_process", number)
        return self.details[number]

    def get_print(self, term: int, number: str) -> PrintInfo:
        self._called("get_print", number)
        if number not in self.prints:
            raise RuntimeError(f"no print {number}")
        return self.prints[number]

    def get_act(self, eli: str) -> ActInfo | None:
        self._called("get_act", eli)
        return self.acts.get(eli)

    def get_voting(self, term: int, sitting: int, number: int) -> tuple[Vote, ...]:
        self._called("get_voting", f"{sitting}/{number}")
        return self.votings[(sitting, number)]

    def get_committee(self, term: int, code: str) -> Committee:
        self._called("get_committee", code)
        return self.committees[code]

    def list_committee_sittings(self, term: int, code: str) -> tuple[CommitteeSitting, ...]:
        self._called("list_committee_sittings", code)
        if code not in self.committee_sittings:
            raise RuntimeError(f"no sittings for {code}")
        return self.committee_sittings[code]

    def list_sittings(self, term: int) -> tuple[SejmSitting, ...]:
        self._called("list_sittings")
        return tuple(s.model_copy(update={"agenda": ""}) for s in self.sittings)

    def get_sitting(self, term: int, number: int) -> SejmSitting:
        self._called("get_sitting", str(number))
        return next(s for s in self.sittings if s.number == number)

    def list_mps(self, term: int) -> tuple[Mp, ...]:
        self._called("list_mps")
        return self.mps

    def download(self, url: str, *, max_bytes: int | None = None) -> bytes:
        self._called("download", url)
        data = self.files[url]
        if max_bytes is not None and len(data) > max_bytes:
            raise AttachmentTooLargeError(url, max_bytes)
        return data


class FakeTextExtractor:
    """Returns one fixed text for every file (or a text chosen by the file's bytes), or raises
    when told to."""

    def __init__(
        self,
        text: str = "Art. 1. Tekst ustawy. " * 50,
        *,
        error: Exception | None = None,
        by_content: dict[bytes, str] | None = None,
    ) -> None:
        self.text = text
        self.error = error
        self.by_content = by_content or {}

    def extract(self, data: bytes) -> str:
        if self.error is not None:
            raise self.error
        return self.by_content.get(data, self.text)


@dataclass
class FakeRclGateway:
    """`RclGateway` over dictionaries: the list rows, projects (timelines), stage catalogs, files
    and the RM-number redirects. Missing entries raise per-item errors."""

    listing: list[RclProjectSummary] = field(default_factory=list)
    projects: dict[int, RclProject] = field(default_factory=dict)  # timelines (no folders)
    stages: dict[int, RclStage] = field(default_factory=dict)  # by stage id, with folders
    files: dict[str, bytes] = field(default_factory=dict)
    rm_numbers: dict[str, int] = field(default_factory=dict)
    outages: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)

    def _called(self, method: str, detail: str = "") -> None:
        self.calls.append(f"{method}:{detail}" if detail else method)
        if method in self.outages:
            raise RclUnavailableError(f"{method}: request rejected")

    def list_projects(self, *, modified_since: date) -> Iterator[RclProjectSummary]:
        self._called("list_projects")
        rows = sorted(self.listing, key=lambda r: r.modified, reverse=True)
        for row in rows:
            if row.modified >= modified_since:
                yield row

    def get_project(self, project_id: int) -> RclProject:
        self._called("get_project", str(project_id))
        project = self.projects[project_id]
        # The page shows the timeline only; folders come from the catalog pages.
        return project.model_copy(
            update={
                "stages": tuple(st.model_copy(update={"folders": ()}) for st in project.stages),
                "consultation": None,
            }
        )

    def get_stage(self, project_id: int, stage_id: int) -> RclStage:
        self._called("get_stage", f"{project_id}/{stage_id}")
        return self.stages[stage_id]

    def resolve_project_id(self, rm_number: str) -> int | None:
        self._called("resolve_project_id", rm_number)
        return self.rm_numbers.get(rm_number)

    def download(self, url: str, *, max_bytes: int | None = None) -> bytes:
        self._called("download", url)
        data = self.files[url]
        if max_bytes is not None and len(data) > max_bytes:
            raise AttachmentTooLargeError(url, max_bytes)
        return data

    def put(self, project: RclProject) -> None:
        """Make the fake RCL show this project (list row, timeline, catalogs)."""
        self.projects[project.id] = project
        for stage in project.stages:
            self.stages[stage.id] = stage
        row = RclProjectSummary(
            id=project.id,
            title=project.title,
            applicant=project.applicant,
            wykaz_number=project.wykaz_number,
            created=project.created,
            modified=project.modified,
        )
        self.listing = [r for r in self.listing if r.id != project.id] + [row]


def make_analysis(
    *, relevant: bool = True, score: int = 5, category: Category = Category.LEGAL_STAY
) -> Analysis:
    return Analysis(
        relevant=relevant,
        score=score,
        category=category,
        summary="Проект меняет правила легализации пребывания.",
        key_changes=["Изменение 1", "Изменение 2"],
        affected_groups=["держатели ВНЖ"],
        practical_impact="Нужно подать заявление раньше.",
        effective_date="через 14 дней после публикации",
        confidence=0.9,
        rationale="Меняет ustawa o cudzoziemcach.",
    )


class FakeLlm:
    """Answers from a script keyed by bill number; an Exception in the script is raised."""

    TRIAGE_MODEL = "fake-triage"
    MODEL = "fake"
    TRIAGE_TOKENS = (10, 5)
    ANALYSIS_TOKENS = (100, 50)

    def __init__(
        self,
        script: dict[str, Analysis | Exception] | None = None,
        default: Analysis | None = None,
        triage_script: dict[str, Triage | Exception] | None = None,
    ) -> None:
        self.script = script or {}
        self.default = default or make_analysis()
        self.triage_script = triage_script or {}
        self.contexts: list[BillContext] = []
        self.triage_contexts: list[TriageContext] = []

    def triage(self, ctx: TriageContext) -> TriageRecord:
        self.triage_contexts.append(ctx)
        outcome = self.triage_script.get(
            ctx.number, Triage(affects_foreigners=True, confidence=0.9, rationale="касается")
        )
        if isinstance(outcome, Exception):
            raise outcome
        return TriageRecord(
            triage=outcome,
            model=self.TRIAGE_MODEL,
            prompt_version=PROMPT_VERSION,
            input_tokens=self.TRIAGE_TOKENS[0],
            output_tokens=self.TRIAGE_TOKENS[1],
        )

    def analyze(self, ctx: BillContext) -> AnalysisRecord:
        self.contexts.append(ctx)
        outcome = self.script.get(ctx.number, self.default)
        if isinstance(outcome, Exception):
            raise outcome
        return AnalysisRecord(
            analysis=outcome,
            model=self.MODEL,
            prompt_version=PROMPT_VERSION,
            input_chars=len(ctx.text),
            truncated=ctx.truncated,
            text_source=ctx.text_source,
            created_at=datetime(2026, 9, 7, tzinfo=UTC),
            input_tokens=self.ANALYSIS_TOKENS[0],
            output_tokens=self.ANALYSIS_TOKENS[1],
        )


@dataclass
class FakePublishResult:
    message_id: int
    document_message_ids: list[int] = field(default_factory=list)


class FakePublisher:
    """Records every post; message ids start at 101 and grow by one per post."""

    def __init__(
        self, fail_on: set[str] | None = None, *, outage_on: set[str] | None = None
    ) -> None:
        self.new_bills: list[tuple[Bill, PrintInfo | None]] = []
        self.updates: list[tuple[Bill, StatusChange, int | None]] = []
        self.acts: list[tuple[Bill, int | None]] = []
        self.in_force: list[tuple[Bill, int | None]] = []
        self.consultations: list[tuple[Bill, int | None, date]] = []
        self.consultation_results: list[tuple[Bill, int | None]] = []
        self.agendas: list[tuple[Bill, AgendaItem, int | None]] = []
        self.fail_on = fail_on or set()  # bill numbers whose post fails (per-bill error)
        self.outage_on = outage_on or set()  # bill numbers whose post finds Telegram down
        self._next_id = 100

    def _send(self, bill: Bill) -> FakePublishResult:
        if bill.number in self.outage_on:
            raise TelegramUnavailableError("sendMessage: ConnectError after 3 attempts")
        if bill.number in self.fail_on:
            raise RuntimeError("telegram rejected the message")
        self._next_id += 1
        return FakePublishResult(message_id=self._next_id)

    def publish_new_bill(self, bill: Bill, print_info: PrintInfo | None) -> FakePublishResult:
        result = self._send(bill)
        self.new_bills.append((bill, print_info))
        return result

    def publish_status_update(
        self, bill: Bill, change: StatusChange, reply_to: int | None
    ) -> FakePublishResult:
        result = self._send(bill)
        self.updates.append((bill, change, reply_to))
        return result

    def publish_act_published(self, bill: Bill, reply_to: int | None) -> FakePublishResult:
        result = self._send(bill)
        self.acts.append((bill, reply_to))
        return result

    def publish_in_force(self, bill: Bill, reply_to: int | None) -> FakePublishResult:
        result = self._send(bill)
        self.in_force.append((bill, reply_to))
        return result

    def publish_consultation_deadline(
        self, bill: Bill, reply_to: int | None, *, today: date
    ) -> FakePublishResult:
        result = self._send(bill)
        self.consultations.append((bill, reply_to, today))
        return result

    def publish_consultation_results(self, bill: Bill, reply_to: int | None) -> FakePublishResult:
        result = self._send(bill)
        self.consultation_results.append((bill, reply_to))
        return result

    def publish_agenda(
        self, bill: Bill, item: AgendaItem, reply_to: int | None
    ) -> FakePublishResult:
        result = self._send(bill)
        self.agendas.append((bill, item, reply_to))
        return result


class FakeNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[RunReport, list[str]]] = []

    def notify(self, report: RunReport, log_lines: list[str]) -> None:
        self.calls.append((report, log_lines))
