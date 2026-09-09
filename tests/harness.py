"""`World`: the whole pipeline wired on fakes and an in-memory database, plus builders for the
domain objects the scenario tests need.

A test arranges the Sejm the fakes describe (`add_bill`, `gateway.submissions`, …), runs the
pipeline (`run`) and asserts on the report, the publisher's records and the database (`bill`).
"""

import datetime as dt
from typing import Any

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.keywords import KeywordPrefilter
from lexinform.models import (
    ActInfo,
    Analysis,
    ApplicantType,
    Attachment,
    Bill,
    BillSubmission,
    DocumentType,
    PrintInfo,
    ProcessDetail,
    ProcessSummary,
    Publication,
    PublicationKind,
    RclConsultation,
    RclDocument,
    RclFolder,
    RclProject,
    RclStage,
    RunReport,
    Stage,
    Triage,
)
from lexinform.models.rcl import StageState
from lexinform.ports import TextExtractor
from lexinform.sections import TextBudget
from lexinform.services.analysis import AnalysisService
from lexinform.services.discovery import BillDiscoveryService
from lexinform.services.documents import TextLoader
from lexinform.services.pipeline import DailyPipeline, RunOptions
from lexinform.services.publishing import PublishingService
from lexinform.services.rcl_discovery import RclDiscoveryService
from lexinform.services.rcl_projects import RclProjectReader
from lexinform.services.signatories import SejmAuthorsResolver
from lexinform.services.sources import RclTextSource, SejmTextSource, TextSources
from lexinform.services.terms import TermResolver
from lexinform.services.text_prefilter import TextPrefilterService
from lexinform.services.tracking import StatusTrackingService
from tests.fakes import (
    FakeLlm,
    FakeNotifier,
    FakePublisher,
    FakeRclGateway,
    FakeSejmGateway,
    FakeTextExtractor,
    FixedClock,
)

CHANNEL = "@test"
TERM = 10
RPW = "RPW/29075/2026"
ELI = "DU/2026/1099"
MAX_PDF_BYTES = 10_000_000
FILE_HOST = "api.test"  # the fake gateway serves every file the loader asks for from here
SINCE = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)

START = (
    Stage(stage_name="Projekt wpłynął do Sejmu", stage_type="Start", date=dt.date(2026, 9, 1)),
)
REFERRED = START + (
    Stage(
        stage_name="Skierowano do I czytania",
        stage_type="ReadingReferral",
        date=dt.date(2026, 9, 5),
    ),
)
COMMITTEE_STAGES = START + (
    Stage(
        stage_name="Skierowano do I czytania w komisjach",
        stage_type="ReadingReferral",
        date=dt.date(2026, 9, 3),
        children=(
            Stage(
                stage_name="Skierowanie",
                stage_type="Referral",
                date=dt.date(2026, 9, 3),
                committee_code="ASW",
            ),
        ),
    ),
)


def summary(number: str, title: str, *, change: str = "2026-09-06T10:00:00") -> ProcessSummary:
    return ProcessSummary(
        term=TERM,
        number=number,
        title=title,
        document_type="projekt ustawy",
        document_type_enum=DocumentType.BILL,
        change_date=dt.datetime.fromisoformat(change),
        document_date=dt.date(2026, 9, 1),
        passed=False,
    )


def detail(process: ProcessSummary, stages: tuple[Stage, ...]) -> ProcessDetail:
    return ProcessDetail(**process.model_dump(), stages=stages)


def print_url(number: str) -> str:
    return f"https://{FILE_HOST}/sejm/term{TERM}/prints/{number}/{number}.pdf"


def submission(**overrides: Any) -> BillSubmission:
    """A deputies' bill under public consultation, without a print number unless told."""
    fields: dict[str, Any] = dict(
        term=TERM,
        number=RPW,
        title="Poselski projekt ustawy o zmianie ustawy o udzielaniu cudzoziemcom ochrony",
        description="odejścia od sztywnego ograniczenia ...",
        applicant=ApplicantType.DEPUTIES,
        date_of_receipt=dt.date(2026, 9, 2),
        public_consultation=True,
        consultation_start=dt.date(2026, 9, 2),
        consultation_end=dt.date(2026, 9, 30),
    )
    fields.update(overrides)
    return BillSubmission(**fields)


RCL_ID = 12414100
RCL = f"RCL/{RCL_ID}"
RCL_HOST = "rcl.test"  # the fake RCL gateway serves documents from here


def rcl_document(doc_id: int, name: str, *, created: dt.date = dt.date(2026, 9, 1)) -> RclDocument:
    extension = name.rsplit(".", 1)[-1]
    return RclDocument(
        id=doc_id,
        name=name,
        url=f"https://{RCL_HOST}/docs//2/{RCL_ID}/1/1/dokument{doc_id}.{extension}",
        created=created,
        author="Minister Spraw Wewnętrznych i Administracji",
    )


def rcl_folder(folder_id: int, name: str, *documents: RclDocument) -> RclFolder:
    modified = max((d.created for d in documents if d.created), default=None)
    return RclFolder(id=folder_id, name=name, modified=modified, documents=documents)


def rcl_stage(
    number: int,
    name: str,
    state: StageState = "reached",
    *folders: RclFolder,
    modified: dt.date | None = dt.date(2026, 9, 1),
) -> RclStage:
    return RclStage(
        id=13223800 + number,
        number=number,
        name=name,
        state=state,
        modified=modified,
        folders=folders,
    )


CONSULTATION_LETTER = rcl_document(794894, "konsultacje_publiczne.DOCX")
CONSULTATION_FOLDERS = (
    rcl_folder(
        13223896,
        "Projekt",
        rcl_document(794885, "projekt_ustawy_o_udziale_RP_w_SIS_i_VIS.DOCX"),
        rcl_document(794886, "uzasadnienie.doc"),
        rcl_document(794887, "OSR.DOCX"),
    ),
    rcl_folder(13223897, "Pisma kierujące projekt do konsultacji publicznych", CONSULTATION_LETTER),
    rcl_folder(13223898, "Stanowiska zgłoszone w ramach konsultacji publicznych"),
    rcl_folder(13223899, "Odniesienie się wnioskodawcy do uwag"),
)
RCL_CONSULTED = (
    rcl_stage(1, "Zgłoszenia lobbingowe", "not_started", modified=None),
    rcl_stage(2, "Uzgodnienia"),
    rcl_stage(3, "Konsultacje publiczne", "reached", *CONSULTATION_FOLDERS),
    # Every stage republishes the current text in its own "Projekt" folder, as on the live site.
    rcl_stage(
        4,
        "Opiniowanie",
        "active",
        rcl_folder(13223875, "Projekt", *CONSULTATION_FOLDERS[0].documents),
    ),
    rcl_stage(9, "Stały Komitet Rady Ministrów", "not_started", modified=None),
    rcl_stage(12, "Rada Ministrów", "not_started", modified=None),
    rcl_stage(14, "Skierowanie projektu ustawy do Sejmu", "not_started", modified=None),
)
RCL_CONSULTATION = RclConsultation(
    letter_url=CONSULTATION_LETTER.url,
    days=7,
    deadline=dt.date(2026, 9, 8),
    email="dep.prawny@mswia.gov.pl",
)


LETTER_BYTES = b"PK\x03\x04 letter"  # what the fake RCL serves for the consultation letter
LETTER_TEXT = (
    "Warszawa /elektroniczny znacznik czasu/ Szanowni Państwo, przekazuję projekt ustawy (UC164)."
    " Zwracam się z prośbą o zajęcie stanowiska w terminie 7 dni od dnia otrzymania niniejszego"
    " pisma, a w przypadku uwag przekazanie ich na adres: dep.prawny@mswia.gov.pl."
)


def rcl_project(**overrides: Any) -> RclProject:
    """A government bill under public consultation on RCL (the UC164 Schengen project)."""
    fields: dict[str, Any] = dict(
        id=RCL_ID,
        title=(
            "Projekt ustawy o zmianie ustawy o udziale Rzeczypospolitej Polskiej w Systemie"
            " Informacyjnym Schengen oraz Wizowym Systemie Informacyjnym"
        ),
        applicant="Minister Spraw Wewnętrznych i Administracji",
        wykaz_number="UC164",
        wykaz_url="https://www.gov.pl/web/premier/wplip-rm",
        created=dt.date(2026, 8, 31),
        modified=dt.date(2026, 9, 1),
        departments=("sprawy wewnętrzne",),
        keywords=("CUDZOZIEMCY", "SYSTEM INFORMACYJNY SCHENGEN"),
        term_label="X",
        stages=RCL_CONSULTED,
        consultation=RCL_CONSULTATION,
    )
    fields.update(overrides)
    return RclProject(**fields)


def act(**overrides: Any) -> ActInfo:
    fields: dict[str, Any] = dict(
        eli=ELI,
        display_address="Dz.U. 2026 poz. 1099",
        title="Ustawa z dnia 17 lipca 2026 r. o zmianie ustawy o cudzoziemcach",
        act_date=dt.date(2026, 7, 17),
        promulgation_date=dt.date(2026, 9, 9),
        entry_into_force=dt.date(2026, 9, 20),
        in_force="NOT_IN_FORCE",
        text_pdf_url="https://api.test/eli/acts/DU/2026/1099/text.pdf",
        fetched_at=dt.datetime(2026, 9, 9, 6, 0, tzinfo=dt.UTC),
    )
    fields.update(overrides)
    return ActInfo(**fields)


class World:
    def __init__(
        self,
        *,
        fail_publish: set[str] | None = None,
        llm_script: dict[str, Analysis | Exception] | None = None,
        triage: bool = False,
        triage_script: dict[str, Triage | Exception] | None = None,
        triage_min_chars: int = 100,  # every fake PDF is "long" enough to be triaged
        text_prefilter: bool = True,
        extractor: TextExtractor | None = None,
        workers: int = 1,
        max_bill_cost_usd: float = 0.0,  # the cost guard rails are off unless a test turns them on
        max_run_cost_usd: float = 0.0,
    ) -> None:
        self.clock = FixedClock()
        self.repo = SqliteBillRepository(":memory:")
        self.repo.migrate()
        self.gateway = FakeSejmGateway()
        self.llm = FakeLlm(script=llm_script, triage_script=triage_script)
        self.publisher = FakePublisher(fail_on=fail_publish)
        self.notifier = FakeNotifier()
        self.extractor = extractor or FakeTextExtractor()
        self.rcl = FakeRclGateway()
        loader = TextLoader(
            {FILE_HOST: self.gateway.download, RCL_HOST: self.rcl.download},
            self.extractor,
            max_bytes=MAX_PDF_BYTES,
        )
        texts = TextSources(SejmTextSource(self.gateway), rcl=RclTextSource())
        rcl_reader = RclProjectReader(self.rcl, loader)
        self.discovery = BillDiscoveryService(
            self.gateway,
            self.repo,
            KeywordPrefilter(),
            self.clock,
            text_prefilter=text_prefilter,
            projects=self.rcl,
        )
        self.rcl_discovery = RclDiscoveryService(
            self.rcl,
            self.repo,
            rcl_reader,
            KeywordPrefilter(),
            self.clock,
            text_prefilter=text_prefilter,
            workers=workers,
        )
        self.analysis = AnalysisService(
            self.repo,
            texts,
            loader,
            self.llm,
            self.clock,
            text_budget=TextBudget(10_000),
            authors=SejmAuthorsResolver(self.gateway),
            workers=workers,
            input_price_usd_per_mtok=5.0,  # Opus 5
            max_bill_cost_usd=max_bill_cost_usd,
            max_run_cost_usd=max_run_cost_usd,
            triage=KeywordPrefilter() if triage else None,
            triage_min_chars=triage_min_chars,
        )
        self.tracking = StatusTrackingService(
            self.gateway,
            self.repo,
            self.publisher,
            self.clock,
            channel_id=CHANNEL,
            analysis=self.analysis,
            eli=self.gateway,
            rcl_reader=rcl_reader,
            text_prefilter=text_prefilter,
            workers=workers,
        )
        self.pipeline = DailyPipeline(
            self.repo,
            self.discovery,
            self.analysis,
            PublishingService(
                self.gateway, self.repo, self.publisher, self.clock, channel_id=CHANNEL
            ),
            self.tracking,
            self.clock,
            terms=TermResolver(self.gateway, self.repo),
            notifier=self.notifier,
            text_prefilter=(
                TextPrefilterService(self.repo, texts, loader, KeywordPrefilter(), workers=workers)
                if text_prefilter
                else None
            ),
            rcl_discovery=self.rcl_discovery,
        )

    # ------------------------------------------------------------------ arrange

    def add_bill(
        self, number: str, title: str, *, stages: tuple[Stage, ...] = START, with_pdf: bool = True
    ) -> None:
        """A numbered print in /processes, with a small PDF unless `with_pdf=False`."""
        process = summary(number, title)
        self.gateway.processes.append(process)
        self.gateway.details[number] = detail(process, stages)
        if with_pdf:
            self.gateway.prints[number] = PrintInfo(
                term=TERM,
                number=number,
                title=title,
                attachments=(
                    Attachment(print_number=number, name=f"{number}.pdf", url=print_url(number)),
                ),
            )
            self.gateway.files[print_url(number)] = b"%PDF"

    def add_rcl_project(
        self, project: RclProject | None = None, *, letter: str = LETTER_TEXT
    ) -> RclProject:
        """A government project on RCL with its documents; the consultation letter reads as
        `letter`, every other file as the extractor's default text."""
        project = project or rcl_project(consultation=None)
        self.rcl.put(project)
        for stage in project.stages:
            for folder in stage.folders:
                for doc in folder.documents:
                    self.rcl.files[doc.url] = LETTER_BYTES if folder.kind == "letters" else b"%PDF"
        if isinstance(self.extractor, FakeTextExtractor):
            self.extractor.by_content[LETTER_BYTES] = letter
        return project

    def set_stages(self, number: str, stages: tuple[Stage, ...]) -> None:
        """The Sejm added or changed stages of a followed bill."""
        process = next(p for p in self.gateway.processes if p.number == number)
        self.gateway.details[number] = detail(process, stages)

    def touch(self, number: str, when: dt.datetime, **summary_updates: Any) -> None:
        """The API lists the bill as modified at `when` (and changes summary fields)."""
        updates = {"change_date": when, **summary_updates}
        self.gateway.processes = [
            p.model_copy(update=updates) if p.number == number else p
            for p in self.gateway.processes
        ]
        current = self.gateway.details[number]
        self.gateway.details[number] = current.model_copy(update=updates)

    def publish_act(self, number: str) -> None:
        """The process gained an ELI address: the act appeared in Dziennik Ustaw."""
        self.touch(
            number,
            dt.datetime(2026, 9, 9, 9, 0),
            closure_date=dt.date(2026, 9, 8),
            passed=True,
            eli=ELI,
            display_address="Dz.U. 2026 poz. 1099",
        )

    # ------------------------------------------------------------------ act

    def run(self, **options: Any) -> RunReport:
        """One pipeline run pinned to `TERM`; `term=None` resolves it from the fake API."""
        options.setdefault("since", SINCE)
        options.setdefault("term", TERM)
        return self.pipeline.run(RunOptions(**options))

    # ------------------------------------------------------------------ assert

    def bill(self, number: str) -> Bill:
        stored = self.repo.get(TERM, number)
        assert stored is not None, f"bill {number} is not in the database"
        return stored

    def publication(
        self, number: str, kind: PublicationKind = PublicationKind.NEW_BILL
    ) -> Publication | None:
        return self.repo.get_publication(TERM, number, kind.value, CHANNEL)

    def card_id(self, number: str) -> int:
        card = self.publication(number)
        assert card is not None and card.message_id is not None, f"no card for {number}"
        return card.message_id
