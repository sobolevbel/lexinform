"""In-memory fakes for the ports, used by the service and pipeline tests.

Every fake records what was asked of it (`calls`, `contexts`, `new_bills`, …) so tests assert on
observable behaviour, and can be told to fail like the real system would: `outages` on the
gateway and `outage_on` on the publisher raise the phase-fatal `ServiceUnavailableError`,
`fail_on` raises an ordinary per-bill error.
"""

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from lexinform.adapters.llm_prompts import PROMPT_VERSION
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.errors import (
    AttachmentTooLargeError,
    RclUnavailableError,
    SejmApiUnavailableError,
    TelegramUnavailableError,
    WykazUnavailableError,
)
from lexinform.models import (
    ActInfo,
    AgendaItem,
    Amendments,
    AmendmentsContext,
    AmendmentsRecord,
    Analysis,
    AnalysisRecord,
    Bill,
    BillContext,
    BillSubmission,
    Category,
    ChannelPost,
    CommandOutcome,
    Committee,
    CommitteeSitting,
    IncomingCommand,
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
    Stage,
    StatusChange,
    Triage,
    TriageContext,
    TriageRecord,
    Vote,
    WykazEntry,
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

    def close(self) -> None:
        pass

    def list_terms(self) -> tuple[SejmTerm, ...]:
        self._called("list_terms")
        return tuple(self.terms)

    # Like the API, the fake answers per term: a process, print or /bills entry of another term
    # does not exist under this one (print numbers restart with every kadencja).

    def iter_processes(
        self, term: int, *, modified_since: datetime | None = None, document_type: str | None = None
    ) -> Iterator[ProcessSummary]:
        self._called("iter_processes", str(term))
        for p in self.processes:
            if p.term != term:
                continue
            if modified_since is None or p.change_date >= modified_since.replace(tzinfo=None):
                yield p

    def iter_bills(
        self, term: int, *, received_from: date | None = None
    ) -> Iterator[BillSubmission]:
        self._called("iter_bills", str(term))
        for sub in self.submissions:
            if sub.term != term:
                continue
            if received_from is None or sub.date_of_receipt >= received_from:
                yield sub

    def find_submission(self, term: int, print_number: str) -> BillSubmission | None:
        self._called("find_submission", print_number)
        return next(
            (s for s in self.submissions if s.term == term and s.print_number == print_number),
            None,
        )

    def find_process_by_rcl_num(
        self, term: int, rcl_num: str, *, since: date | None = None
    ) -> ProcessSummary | None:
        self._called("find_process_by_rcl_num", rcl_num)
        return next(
            (
                p
                for p in self.processes
                if p.term == term
                and p.rcl_num == rcl_num
                and (since is None or (p.document_date or since) >= since - timedelta(days=7))
            ),
            None,
        )

    def get_process(self, term: int, number: str) -> ProcessDetail:
        self._called("get_process", number)
        detail = self.details.get(number)
        if detail is None or detail.term != term:
            raise RuntimeError(f"no process {number} in term {term}")
        return detail

    def get_print(self, term: int, number: str) -> PrintInfo:
        self._called("get_print", number)
        info = self.prints.get(number)
        if info is None or info.term != term:
            raise RuntimeError(f"no print {number} in term {term}")
        return info

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

    def close(self) -> None:
        pass

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


@dataclass
class FakeWykazGateway:
    """`WykazGateway` over a list of entries: the register as one download."""

    entries_by_number: dict[str, WykazEntry] = field(default_factory=dict)
    outage: bool = False
    calls: int = 0

    def entries(self) -> tuple[WykazEntry, ...]:
        self.calls += 1
        if self.outage:
            raise WykazUnavailableError("gov.pl does not answer")
        return tuple(
            sorted(self.entries_by_number.values(), key=lambda e: e.published_at, reverse=True)
        )

    def find(self, number: str) -> WykazEntry | None:
        return self.entries_by_number.get(number)

    def close(self) -> None:
        pass

    def put(self, entry: WykazEntry) -> None:
        """Make the register show this entry."""
        self.entries_by_number[entry.number] = entry


def make_amendments(**overrides: object) -> Amendments:
    fields: dict[str, object] = dict(
        summary="Сенат смягчил проект: срок подачи заявления продлён.",
        changes=["Срок подачи заявления продлён с 14 до 30 дней", "Убран сбор за дубликат"],
        affects_foreigners=True,
        confidence=0.85,
    )
    fields.update(overrides)
    return Amendments.model_validate(fields)


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
    AMENDMENTS_TOKENS = (40, 20)

    def __init__(
        self,
        script: dict[str, Analysis | Exception] | None = None,
        default: Analysis | None = None,
        triage_script: dict[str, Triage | Exception] | None = None,
        amendments_script: dict[str, Amendments | Exception] | None = None,
    ) -> None:
        self.script = script or {}
        self.default = default or make_analysis()
        self.triage_script = triage_script or {}
        self.amendments_script = amendments_script or {}
        self.contexts: list[BillContext] = []
        self.triage_contexts: list[TriageContext] = []
        self.amendment_contexts: list[AmendmentsContext] = []

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

    def summarize_amendments(self, ctx: AmendmentsContext) -> AmendmentsRecord:
        self.amendment_contexts.append(ctx)
        outcome = self.amendments_script.get(ctx.number, make_amendments())
        if isinstance(outcome, Exception):
            raise outcome
        return AmendmentsRecord(
            amendments=outcome,
            model=self.MODEL,
            prompt_version=PROMPT_VERSION,
            source_url="",
            source_kind=ctx.source_kind,
            created_at=datetime(2026, 9, 7, tzinfo=UTC),
            input_tokens=self.AMENDMENTS_TOKENS[0],
            output_tokens=self.AMENDMENTS_TOKENS[1],
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
        self.edits: list[tuple[Bill, int]] = []  # cards re-rendered in place (bill, message id)
        # "alternative bill" replies: (bill, the bill whose card it went under, reply_to)
        self.joint_bills: list[tuple[Bill, Bill, int | None]] = []
        self.updates: list[tuple[Bill, StatusChange, int | None]] = []
        self.acts: list[tuple[Bill, int | None]] = []
        self.in_force: list[tuple[Bill, int | None]] = []
        self.consultations: list[tuple[Bill, int | None, date]] = []
        self.consultation_results: list[tuple[Bill, int | None]] = []
        self.agendas: list[tuple[Bill, AgendaItem, int | None]] = []
        self.hearings: list[tuple[Bill, Stage, int | None, date]] = []
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

    def edit_new_bill(self, bill: Bill, print_info: PrintInfo | None, *, message_id: int) -> None:
        self._send(bill)
        self.edits.append((bill, message_id))

    def card_digest(self, bill: Bill) -> str:
        """The real card's digest: a test sees the card change exactly when a reader would."""
        text = MessageFormatter("ru").new_bill(bill, None).text
        return hashlib.sha256(text.encode()).hexdigest()

    def publish_joint_bill(
        self, bill: Bill, primary: Bill, print_info: PrintInfo | None, reply_to: int | None
    ) -> FakePublishResult:
        result = self._send(bill)
        self.joint_bills.append((bill, primary, reply_to))
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

    def publish_hearing_deadline(
        self, bill: Bill, hearing: Stage, reply_to: int | None, *, today: date
    ) -> FakePublishResult:
        result = self._send(bill)
        self.hearings.append((bill, hearing, reply_to, today))
        return result


class FakeNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[RunReport, list[str]]] = []

    def notify(self, report: RunReport, log_lines: list[str]) -> None:
        self.calls.append((report, log_lines))


class FakeInbox:
    """Operator commands as the relay would file them; `put` adds one, `done` takes it out."""

    def __init__(self) -> None:
        self.commands: list[IncomingCommand] = []
        self.done_ids: list[int] = []
        self._next_id = 0

    def put(self, text: str, *, update_id: int | None = None) -> IncomingCommand:
        self._next_id += 1
        command = IncomingCommand(
            update_id=update_id if update_id is not None else self._next_id,
            chat_id="-1001",
            message_id=self._next_id + 500,
            text=text,
            received_at=datetime(2026, 9, 11, 8, 0, tzinfo=UTC),
        )
        self.commands.append(command)
        return command

    def pending(self) -> list[IncomingCommand]:
        return sorted(self.commands, key=lambda c: c.update_id)

    def done(self, command: IncomingCommand) -> None:
        self.done_ids.append(command.update_id)
        self.commands = [c for c in self.commands if c.update_id != command.update_id]


class FakeReplier:
    """Records the answer to every command; `outage` makes the channel unreachable, and
    `outage_after` lets it fall over in the middle of a phase (after N answers)."""

    def __init__(self, *, outage: bool = False, outage_after: int | None = None) -> None:
        self.replies: list[tuple[IncomingCommand, CommandOutcome]] = []
        self.outage = outage
        self.outage_after = outage_after

    def reply(self, command: IncomingCommand, outcome: CommandOutcome) -> None:
        if self.outage or (
            self.outage_after is not None and len(self.replies) >= self.outage_after
        ):
            raise TelegramUnavailableError("sendMessage: ConnectError after 3 attempts")
        self.replies.append((command, outcome))


class FakeUpdates:
    """Telegram's getUpdates over scripted batches: every call returns the next batch (an empty
    list when the script is exhausted) and records the offset it was asked for."""

    def __init__(self, *batches: list[ChannelPost], outage: bool = False) -> None:
        self.batches = list(batches)
        self.offsets: list[int | None] = []
        self.outage = outage

    def get_updates(self, *, offset: int | None, timeout: int) -> list[ChannelPost]:
        self.offsets.append(offset)
        if self.outage:
            raise TelegramUnavailableError("getUpdates: ConnectError after 3 attempts")
        return self.batches.pop(0) if self.batches else []


class FakeInboxWriter:
    def __init__(self, *, fail: bool = False) -> None:
        self.filed: list[IncomingCommand] = []
        self.fail = fail

    def put(self, command: IncomingCommand) -> None:
        if self.fail:
            raise SejmApiUnavailableError("PUT inbox: HTTP 503")  # any outage-class error
        self.filed.append(command)


class FakeAcknowledger:
    def __init__(self) -> None:
        self.acknowledged: list[int] = []

    def queued(self, command: IncomingCommand) -> None:
        self.acknowledged.append(command.update_id)


def channel_post(update_id: int, text: str | None, *, chat_id: int = -1001) -> ChannelPost:
    return ChannelPost(
        update_id=update_id,
        chat_id=chat_id,
        message_id=update_id + 500,
        text=text,
        date=datetime(2026, 9, 11, 8, 0, tzinfo=UTC),
    )
