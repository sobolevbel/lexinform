"""In-memory fakes for the ports. Used by unit tests of services and the pipeline."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from lexinform.adapters.llm_prompts import PROMPT_VERSION
from lexinform.errors import AttachmentTooLargeError
from lexinform.models import (
    ActInfo,
    Analysis,
    AnalysisRecord,
    Bill,
    BillContext,
    BillSubmission,
    Category,
    Committee,
    Mp,
    PrintInfo,
    ProcessDetail,
    ProcessSummary,
    RunReport,
    StatusChange,
    Triage,
    TriageContext,
    TriageRecord,
    Vote,
)


class FixedClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime(2026, 9, 7, 6, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs: int) -> None:
        self.current += timedelta(**kwargs)


@dataclass
class FakeSejmGateway:
    processes: list[ProcessSummary] = field(default_factory=list)
    details: dict[str, ProcessDetail] = field(default_factory=dict)
    prints: dict[str, PrintInfo] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)
    votings: dict[tuple[int, int], tuple[Vote, ...]] = field(default_factory=dict)
    committees: dict[str, Committee] = field(default_factory=dict)
    mps: tuple[Mp, ...] = ()
    submissions: list[BillSubmission] = field(default_factory=list)
    acts: dict[str, ActInfo] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def get_act(self, eli: str) -> ActInfo | None:
        self.calls.append(f"get_act:{eli}")
        return self.acts.get(eli)

    def iter_bills(
        self, term: int, *, received_from: date | None = None
    ) -> Iterator[BillSubmission]:
        self.calls.append("iter_bills")
        for sub in self.submissions:
            if received_from is None or sub.date_of_receipt >= received_from:
                yield sub

    def find_submission(self, term: int, print_number: str) -> BillSubmission | None:
        self.calls.append(f"find_submission:{print_number}")
        return next((s for s in self.submissions if s.print_number == print_number), None)

    def iter_processes(
        self, term: int, *, modified_since: datetime | None = None, document_type: str | None = None
    ) -> Iterator[ProcessSummary]:
        self.calls.append("iter_processes")
        for p in self.processes:
            if modified_since is None or p.change_date >= modified_since.replace(tzinfo=None):
                yield p

    def get_process(self, term: int, number: str) -> ProcessDetail:
        self.calls.append(f"get_process:{number}")
        return self.details[number]

    def get_print(self, term: int, number: str) -> PrintInfo:
        self.calls.append(f"get_print:{number}")
        if number not in self.prints:
            raise RuntimeError(f"no print {number}")
        return self.prints[number]

    def get_voting(self, term: int, sitting: int, number: int) -> tuple[Vote, ...]:
        self.calls.append(f"get_voting:{sitting}/{number}")
        return self.votings[(sitting, number)]

    def get_committee(self, term: int, code: str) -> Committee:
        self.calls.append(f"get_committee:{code}")
        return self.committees[code]

    def list_mps(self, term: int) -> tuple[Mp, ...]:
        self.calls.append("list_mps")
        return self.mps

    def download(self, url: str, *, max_bytes: int | None = None) -> bytes:
        self.calls.append(f"download:{url}")
        data = self.files[url]
        if max_bytes is not None and len(data) > max_bytes:
            raise AttachmentTooLargeError(url, max_bytes)
        return data


class FakeTextExtractor:
    def __init__(self, text: str = "Art. 1. Tekst ustawy. " * 50) -> None:
        self.text = text

    def extract(self, data: bytes) -> str:
        return self.text


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
            model="fake-triage",
            prompt_version=PROMPT_VERSION,
            input_tokens=10,
            output_tokens=5,
        )

    def analyze(self, ctx: BillContext) -> AnalysisRecord:
        self.contexts.append(ctx)
        outcome = self.script.get(ctx.number, self.default)
        if isinstance(outcome, Exception):
            raise outcome
        return AnalysisRecord(
            analysis=outcome,
            model="fake",
            prompt_version=PROMPT_VERSION,
            input_chars=len(ctx.text),
            truncated=ctx.truncated,
            text_source=ctx.text_source,
            created_at=datetime(2026, 9, 7, tzinfo=UTC),
            input_tokens=100,
            output_tokens=50,
        )


@dataclass
class FakePublishResult:
    message_id: int
    document_message_ids: list[int] = field(default_factory=list)


class FakePublisher:
    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.new_bills: list[tuple[Bill, PrintInfo | None]] = []
        self.updates: list[tuple[Bill, StatusChange, int | None]] = []
        self.acts: list[tuple[Bill, int | None]] = []
        self.in_force: list[tuple[Bill, int | None]] = []
        self.consultations: list[tuple[Bill, int | None, date]] = []
        self.fail_on = fail_on or set()
        self._next_id = 100

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    def publish_new_bill(self, bill: Bill, print_info: PrintInfo | None) -> FakePublishResult:
        if bill.number in self.fail_on:
            raise RuntimeError("telegram down")
        self.new_bills.append((bill, print_info))
        return FakePublishResult(message_id=self._id(), document_message_ids=[self._id()])

    def publish_status_update(
        self, bill: Bill, change: StatusChange, reply_to: int | None
    ) -> FakePublishResult:
        if bill.number in self.fail_on:
            raise RuntimeError("telegram down")
        self.updates.append((bill, change, reply_to))
        return FakePublishResult(message_id=self._id())

    def publish_act_published(self, bill: Bill, reply_to: int | None) -> FakePublishResult:
        if bill.number in self.fail_on:
            raise RuntimeError("telegram down")
        self.acts.append((bill, reply_to))
        return FakePublishResult(message_id=self._id())

    def publish_in_force(self, bill: Bill, reply_to: int | None) -> FakePublishResult:
        if bill.number in self.fail_on:
            raise RuntimeError("telegram down")
        self.in_force.append((bill, reply_to))
        return FakePublishResult(message_id=self._id())

    def publish_consultation_deadline(
        self, bill: Bill, reply_to: int | None, *, today: date
    ) -> FakePublishResult:
        if bill.number in self.fail_on:
            raise RuntimeError("telegram down")
        self.consultations.append((bill, reply_to, today))
        return FakePublishResult(message_id=self._id())


class FakeNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[RunReport, list[str]]] = []

    def notify(self, report: RunReport, log_lines: list[str]) -> None:
        self.calls.append((report, log_lines))
