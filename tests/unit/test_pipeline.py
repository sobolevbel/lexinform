"""End-to-end pipeline tests on fakes: idempotency, failure isolation, tracking."""

from __future__ import annotations

import datetime as dt

import pytest

from lexinform.adapters.pdf_text import TextBudget
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.keywords import KeywordPrefilter
from lexinform.models import (
    ActInfo,
    ApplicantType,
    BillStatus,
    BillSubmission,
    Category,
    Committee,
    CommitteeSitting,
    DocumentType,
    Mp,
    PrintInfo,
    ProcessDetail,
    ProcessSummary,
    PublicationStatus,
    SejmSitting,
    Stage,
    Vote,
    VotingSummary,
)
from lexinform.services.analysis import AnalysisService
from lexinform.services.discovery import BillDiscoveryService
from lexinform.services.documents import PdfTextLoader
from lexinform.services.pipeline import DailyPipeline, RunOptions
from lexinform.services.publishing import PublishingService
from lexinform.services.text_prefilter import TextPrefilterService
from lexinform.services.tracking import StatusTrackingService
from tests.fakes import (
    FakeLlm,
    FakeNotifier,
    FakePublisher,
    FakeSejmGateway,
    FakeTextExtractor,
    FixedClock,
    make_analysis,
)

CHANNEL = "@test"


def _summary(number: str, title: str, *, change: str = "2026-09-06T10:00:00") -> ProcessSummary:
    return ProcessSummary(
        term=10,
        number=number,
        title=title,
        document_type="projekt ustawy",
        document_type_enum=DocumentType.BILL,
        change_date=dt.datetime.fromisoformat(change),
        document_date=dt.date(2026, 9, 1),
        passed=False,
    )


def _detail(summary: ProcessSummary, stages: tuple[Stage, ...]) -> ProcessDetail:
    return ProcessDetail(**summary.model_dump(), stages=stages)


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


class World:
    def __init__(  # type: ignore[no-untyped-def]
        self,
        *,
        fail_publish: set[str] | None = None,
        llm_script=None,
        text_prefilter: bool = True,
        extractor=None,
        workers: int = 1,
        triage: bool = False,
        triage_script=None,
    ) -> None:
        self.clock = FixedClock()
        self.repo = SqliteBillRepository(":memory:")
        self.repo.migrate()
        self.gateway = FakeSejmGateway()
        self.llm = FakeLlm(script=llm_script, triage_script=triage_script)
        self.publisher = FakePublisher(fail_on=fail_publish)
        self.notifier = FakeNotifier()
        self.extractor = extractor or FakeTextExtractor()
        self.loader = PdfTextLoader(self.gateway, self.extractor, max_bytes=10_000_000)
        analysis = AnalysisService(
            self.gateway,
            self.repo,
            self.loader,
            self.llm,
            text_budget=TextBudget(10_000),
            workers=workers,
            triage=KeywordPrefilter() if triage else None,
            triage_min_chars=100,  # every fake PDF is "long" enough to be triaged
        )
        self.pipeline = DailyPipeline(
            self.repo,
            BillDiscoveryService(
                self.gateway,
                self.repo,
                KeywordPrefilter(),
                self.clock,
                text_prefilter=text_prefilter,
            ),
            analysis,
            PublishingService(
                self.gateway, self.repo, self.publisher, self.clock, channel_id=CHANNEL
            ),
            StatusTrackingService(
                self.gateway,
                self.repo,
                self.publisher,
                self.clock,
                channel_id=CHANNEL,
                analysis=analysis,
                eli=self.gateway,
                workers=workers,
            ),
            self.clock,
            notifier=self.notifier,
            text_prefilter=(
                TextPrefilterService(
                    self.gateway, self.repo, self.loader, KeywordPrefilter(), workers=workers
                )
                if text_prefilter
                else None
            ),
        )

    def add_bill(
        self, number: str, title: str, *, stages: tuple[Stage, ...] = START, with_pdf: bool = True
    ) -> None:
        s = _summary(number, title)
        self.gateway.processes.append(s)
        self.gateway.details[number] = _detail(s, stages)
        if with_pdf:
            url = f"https://api.test/sejm/term10/prints/{number}/{number}.pdf"
            from lexinform.models import Attachment

            self.gateway.prints[number] = PrintInfo(
                term=10,
                number=number,
                title=title,
                attachments=(Attachment(print_number=number, name=f"{number}.pdf", url=url),),
            )
            self.gateway.files[url] = b"%PDF"

    def run(self, **kw) -> object:  # type: ignore[no-untyped-def]
        kw.setdefault("since", dt.datetime(2026, 9, 1, tzinfo=dt.UTC))
        return self.pipeline.run(RunOptions(term=10, **kw))


def test_happy_path_publishes_relevant_bill_once() -> None:
    w = World()
    w.add_bill("3039", "Poselski projekt ustawy o zmianie ustawy o cudzoziemcach")
    w.add_bill("4000", "Rządowy projekt ustawy o podatku VAT")  # prefilter miss
    report = w.run()
    assert (
        report.discovered == 2
        and report.prefilter_hits == 1
        and report.analyzed == 1
        and report.published == 1
    )  # type: ignore[attr-defined]
    assert [b.number for b, _ in w.publisher.new_bills] == ["3039"]
    # the VAT bill's text (fake extractor: generic act text) does not mention foreigners either
    assert w.repo.get(10, "4000").status is BillStatus.SKIPPED_TEXT_PREFILTER  # type: ignore[union-attr]
    assert report.text_prefilter_checked == 1 and report.text_prefilter_hits == 0  # type: ignore[attr-defined]
    assert len(w.llm.contexts) == 1 and w.llm.contexts[0].text_source == "pdf"

    # second run: nothing new
    report2 = w.run()
    assert report2.published == 0 and report2.analyzed == 0  # type: ignore[attr-defined]
    assert len(w.publisher.new_bills) == 1


def test_irrelevant_analysis_is_not_published() -> None:
    w = World(llm_script={"3039": make_analysis(relevant=False, score=1, category=Category.NONE)})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    report = w.run()
    assert report.analyzed == 1 and report.published == 0  # type: ignore[attr-defined]
    assert [(v.number, v.reason) for v in report.rejected] == [("3039", "not relevant")]  # type: ignore[attr-defined]


def test_low_score_bills_are_listed_as_not_published() -> None:
    w = World(llm_script={"3039": make_analysis(score=2, category=Category.INDIRECT)})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o obywatelstwie polskim")  # default fake analysis: score 5
    report = w.run(min_score=3)
    assert report.published == 1  # type: ignore[attr-defined]
    assert [(v.number, v.reason) for v in report.rejected] == [("3039", "score 2")]  # type: ignore[attr-defined]


def test_min_score_filters_publication() -> None:
    w = World(llm_script={"3039": make_analysis(score=1, category=Category.MARGINAL)})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    assert w.run(min_score=2).published == 0  # type: ignore[attr-defined]


def test_llm_failure_isolated_and_retried_next_run() -> None:
    w = World(llm_script={"3039": RuntimeError("llm down")})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o obywatelstwie polskim")
    report = w.run()
    assert report.analyzed == 1 and report.analysis_failures == 1 and report.published == 1  # type: ignore[attr-defined]
    failed = w.repo.get(10, "3039")
    assert (
        failed is not None
        and failed.status is BillStatus.ANALYSIS_FAILED
        and failed.analysis_attempts == 1
    )

    w.llm.script = {}
    report2 = w.run()
    assert report2.analyzed == 1 and report2.published == 1  # type: ignore[attr-defined]
    assert {b.number for b, _ in w.publisher.new_bills} == {"3039", "3040"}


def test_attempts_are_capped() -> None:
    w = World(llm_script={"3039": RuntimeError("llm down")})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    for _ in range(5):
        w.run()
    assert w.repo.get(10, "3039").analysis_attempts == 3  # type: ignore[union-attr]


def test_publish_failure_is_recorded_and_retried() -> None:
    w = World(fail_publish={"3039"})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    report = w.run()
    assert report.published == 0 and report.errors  # type: ignore[attr-defined]
    pub = w.repo.get_publication(10, "3039", "new_bill", CHANNEL)
    assert pub is not None and pub.status is PublicationStatus.FAILED
    w.publisher.fail_on = set()
    assert w.run().published == 1  # type: ignore[attr-defined]
    assert w.repo.get_publication(10, "3039", "new_bill", CHANNEL).status is PublicationStatus.SENT  # type: ignore[union-attr]


def test_no_publish_marks_skipped_forever() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run(publish=False)
    assert w.publisher.new_bills == []
    assert (
        w.repo.get_publication(10, "3039", "new_bill", CHANNEL).status is PublicationStatus.SKIPPED
    )  # type: ignore[union-attr]
    assert w.run().published == 0  # type: ignore[attr-defined]


def test_dry_run_leaves_database_empty() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    report = w.run(dry_run=True)
    assert report.published == 1 and report.mode == "dry_run"  # type: ignore[attr-defined]
    assert w.repo.get(10, "3039") is None
    assert w.repo.last_discovery_started_at() is None


def test_tracking_posts_exactly_one_update_per_change() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    assert w.publisher.updates == []

    # Sejm adds a stage
    s = w.gateway.processes[0]
    w.gateway.details["3039"] = _detail(s, REFERRED)
    w.clock.advance(days=1)
    report = w.run()
    assert report.updates == 1  # type: ignore[attr-defined]
    bill, change, reply_to = w.publisher.updates[0]
    assert [st.stage_name for st in change.new_stages] == ["Skierowano do I czytania"]
    assert reply_to == 101  # message id of the original card

    # nothing changed -> no second update
    w.clock.advance(days=1)
    assert w.run().updates == 0  # type: ignore[attr-defined]
    assert len(w.publisher.updates) == 1


def test_metadata_only_when_pdf_missing() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", with_pdf=False)
    w.run()
    assert w.llm.contexts[0].text_source == "metadata_only"
    assert w.publisher.new_bills[0][1] is None


def test_since_resolution_uses_last_run_with_overlap() -> None:
    w = World()
    assert w.pipeline.resolve_since(None) == w.clock.now() - dt.timedelta(days=1)
    w.run()
    w.clock.advance(days=3)
    assert w.pipeline.resolve_since(None) == w.clock.now() - dt.timedelta(days=4)


@pytest.mark.parametrize("bad_type", [DocumentType.DRAFT_RESOLUTION, DocumentType.OTHER])
def test_non_bills_are_ignored(bad_type: DocumentType) -> None:
    w = World()
    s = _summary("5", "Projekt uchwały o cudzoziemcach").model_copy(
        update={"document_type_enum": bad_type}
    )
    w.gateway.processes.append(s)
    assert w.run().discovered == 0  # type: ignore[attr-defined]


REPORT_URL = "https://api.test/sejm/term10/prints/2689/2689.pdf"
WITH_REPORT = REFERRED + (
    Stage(
        stage_name="Praca w komisjach po I czytaniu",
        stage_type="CommitteeWork",
        date=dt.date(2026, 9, 6),
        children=(
            Stage(
                stage_name="Sprawozdanie komisji",
                stage_type="CommitteeReport",
                date=dt.date(2026, 9, 6),
                print_number="2689",
                report_file=REPORT_URL,
            ),
        ),
    ),
)


def test_new_committee_report_triggers_reanalysis_with_previous_context() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    assert w.repo.get(10, "3039").analysis.revision == 1  # type: ignore[union-attr]

    updated = make_analysis(score=4)
    updated.changes_since_previous = ["Срок сокращён с 30 до 14 дней"]
    w.llm.script = {"3039": updated}
    w.gateway.details["3039"] = _detail(w.gateway.processes[0], WITH_REPORT)
    w.gateway.files[REPORT_URL] = b"%PDF-report"
    w.clock.advance(days=1)
    report = w.run()
    assert report.reanalyzed == 1 and report.updates == 1  # type: ignore[attr-defined]

    ctx = w.llm.contexts[-1]
    assert ctx.source_kind == "committee_report" and ctx.previous_summary
    stored = w.repo.get(10, "3039")
    assert stored is not None and stored.analysis is not None
    assert stored.analysis.revision == 2 and stored.analysis.source_url == REPORT_URL
    _, change, reply_to = w.publisher.updates[-1]
    assert change.content_changed and reply_to == 101
    assert [s.stage_type for s in change.new_stages] == [
        "ReadingReferral",
        "CommitteeWork",
        "CommitteeReport",
    ]

    # same document again -> no second re-analysis, no second update
    w.clock.advance(days=1)
    report3 = w.run()
    assert report3.reanalyzed == 0 and report3.updates == 0  # type: ignore[attr-defined]


def test_updated_print_triggers_reanalysis_without_stage_change() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.clock.advance(days=2)
    w.gateway.prints["3039"] = w.gateway.prints["3039"].model_copy(
        update={"change_date": w.clock.now().replace(tzinfo=None)}
    )
    report = w.run()
    assert report.reanalyzed == 1 and report.updates == 1  # type: ignore[attr-defined]
    _, change, _ = w.publisher.updates[-1]
    assert change.content_changed and change.new_stages == []


def test_notifier_receives_report_and_warnings() -> None:
    w = World(llm_script={"3039": RuntimeError("llm down")})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    report, lines = w.notifier.calls[-1]
    assert report.analysis_failures == 1
    assert any("analysis failed for druk 3039" in line for line in lines)


def test_max_analyze_zero_skips_llm() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    report = w.run(max_analyze=0)
    assert report.analyzed == 0 and report.analysis_failures == 0 and w.llm.contexts == []  # type: ignore[attr-defined]


def test_failed_status_update_is_retried_on_the_next_run() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.gateway.details["3039"] = _detail(w.gateway.processes[0], REFERRED)
    w.publisher.fail_on = {"3039"}  # Telegram rejects the update
    w.clock.advance(days=1)
    report = w.run()
    assert report.updates == 0 and report.errors  # type: ignore[attr-defined]
    assert w.publisher.updates == []

    w.publisher.fail_on = set()
    w.clock.advance(days=1)
    report2 = w.run()  # stages unchanged since, yet the lost update is posted now
    assert report2.updates == 1 and not report2.errors  # type: ignore[attr-defined]
    _, change, reply_to = w.publisher.updates[0]
    assert [st.stage_name for st in change.new_stages] == ["Skierowano do I czytania"]
    assert reply_to == 101

    w.clock.advance(days=1)
    assert w.run().updates == 0  # type: ignore[attr-defined]


def test_closure_is_announced_once_even_though_discovery_refreshes_the_summary() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()

    closed = w.gateway.processes[0].model_copy(
        update={
            "closure_date": dt.date(2026, 9, 8),
            "passed": True,
            "change_date": dt.datetime(2026, 9, 8, 10, 0),
        }
    )
    w.gateway.processes[0] = closed
    w.gateway.details["3039"] = _detail(closed, REFERRED)
    w.clock.advance(days=1)
    report = w.run()
    assert report.updates == 1  # type: ignore[attr-defined]
    _, change, _ = w.publisher.updates[0]
    assert change.closure_detected and change.passed

    w.clock.advance(days=1)
    assert w.run().updates == 0  # type: ignore[attr-defined]


def test_removed_stage_without_new_stages_does_not_post_an_empty_update() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    w.run()
    w.gateway.details["3039"] = _detail(w.gateway.processes[0], START)  # Sejm removed a stage
    w.clock.advance(days=1)
    assert w.run().updates == 0  # type: ignore[attr-defined]
    assert w.publisher.updates == []


def test_watermark_advances_after_a_run_with_publishing_errors() -> None:
    w = World(fail_publish={"3039"})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    first_start = w.clock.now()
    assert w.run().errors  # type: ignore[attr-defined]
    w.clock.advance(days=5)
    assert w.pipeline.resolve_since(None) == first_start - dt.timedelta(days=1)


def test_unreadable_pdf_falls_back_to_metadata_without_burning_attempts() -> None:
    class _BrokenExtractor:
        def extract(self, data: bytes) -> str:
            raise ValueError("not a PDF")

    w = World(extractor=_BrokenExtractor())
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    report = w.run()
    assert report.analyzed == 1 and report.analysis_failures == 0  # type: ignore[attr-defined]
    assert w.llm.contexts[0].text_source == "metadata_only"
    assert w.repo.get(10, "3039").analysis_attempts == 0  # type: ignore[union-attr]


def test_migration_failure_is_reported_not_raised() -> None:
    w = World()

    def boom() -> None:
        raise RuntimeError("database is locked")

    w.repo.migrate = boom  # type: ignore[method-assign]
    report = w.run()
    assert report.errors and "database is locked" in report.errors[0]  # type: ignore[attr-defined]
    assert w.notifier.calls  # the log channel still gets the report


VOTED = REFERRED + (
    Stage(
        stage_name="III czytanie na posiedzeniu Sejmu",
        stage_type="SejmReading",
        date=dt.date(2026, 9, 10),
        decision="uchwalono",
        sitting_num=62,
        children=(
            Stage(
                stage_name="Głosowanie",
                stage_type="Voting",
                date=dt.date(2026, 9, 10),
                voting=VotingSummary(yes=261, no=0, abstain=182, sitting=62, voting_number=16),
            ),
        ),
    ),
)


def test_tracking_enriches_votes_with_clubs_and_referrals_with_committee_names() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.gateway.votings[(62, 16)] = (
        Vote(mp=1, club="KO", vote="YES"),
        Vote(mp=2, club="PiS", vote="ABSTAIN"),
    )
    w.gateway.committees["ASW"] = Committee(term=10, code="ASW", name="Komisja ASW")
    referral = Stage(stage_name="Skierowanie", stage_type="Referral", committee_code="ASW")
    stages = VOTED[:2] + (VOTED[1].model_copy(update={"children": (referral,)}),) + VOTED[2:]
    w.gateway.details["3039"] = _detail(w.gateway.processes[0], stages)
    w.clock.advance(days=1)
    assert w.run().updates == 1  # type: ignore[attr-defined]
    _, change, _ = w.publisher.updates[0]
    voting = next(s for s in change.new_stages if s.stage_type == "Voting")
    assert voting.voting is not None
    assert [(c.club, c.yes, c.abstain) for c in voting.voting.clubs] == [
        ("KO", 1, 0),
        ("PiS", 0, 1),
    ]
    ref = next(s for s in change.new_stages if s.stage_type == "Referral")
    assert ref.committee_name == "Komisja ASW"


def test_vote_detail_failure_degrades_to_totals_only() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    # no entry in gateway.votings -> KeyError inside enrichment
    w.gateway.details["3039"] = _detail(w.gateway.processes[0], VOTED)
    w.clock.advance(days=1)
    report = w.run()
    assert report.updates == 1 and not report.errors  # type: ignore[attr-defined]
    _, change, _ = w.publisher.updates[0]
    voting = next(s for s in change.new_stages if s.stage_type == "Voting")
    assert voting.voting is not None and voting.voting.clubs == () and voting.voting.yes == 261


RPW = "RPW/29075/2026"


def _submission(**kw: object) -> BillSubmission:
    base: dict[str, object] = dict(
        term=10,
        number=RPW,
        title="Poselski projekt ustawy o zmianie ustawy o udzielaniu cudzoziemcom ochrony",
        description="odejścia od sztywnego ograniczenia ...",
        applicant=ApplicantType.DEPUTIES,
        date_of_receipt=dt.date(2026, 9, 2),
        public_consultation=True,
        consultation_start=dt.date(2026, 9, 2),
        consultation_end=dt.date(2026, 9, 30),
    )
    base.update(kw)
    return BillSubmission(**base)  # type: ignore[arg-type]


def test_pre_print_bill_is_discovered_analysed_from_metadata_and_published() -> None:
    w = World()
    w.gateway.submissions.append(_submission())
    report = w.run()
    assert report.pre_print_discovered == 1 and report.analyzed == 1 and report.published == 1  # type: ignore[attr-defined]
    assert not any(c.startswith("get_process") for c in w.gateway.calls)
    ctx = w.llm.contexts[0]
    assert ctx.number == RPW and ctx.text_source == "metadata_only"
    assert ctx.applicant_type is ApplicantType.DEPUTIES
    bill, print_info = w.publisher.new_bills[0]
    assert bill.is_pre_print and print_info is None
    assert bill.submission is not None and bill.submission.consultation_end == dt.date(2026, 9, 30)
    text = MessageFormatter("ru").new_bill(bill, None).text
    assert "RPW/29075/2026 (номер druku ещё не присвоен)" in text
    assert "Общественные консультации:</b> 02.09.2026 — 30.09.2026" in text
    assert "orka.sejm.gov.pl/Druki10ka.nsf/Projekty/10-RPW-29075-2026/" in text
    assert "#RPW_29075_2026" in text and "ожидает присвоения номера druku" in text

    # second run: nothing new, and the pre-print bill is not polled as a process
    w.clock.advance(days=1)
    report2 = w.run()
    assert report2.published == 0 and report2.updates == 0 and report2.tracked == 0  # type: ignore[attr-defined]


def test_pre_print_bill_that_gets_a_print_number_continues_in_the_same_thread() -> None:
    w = World()
    w.gateway.submissions.append(_submission())
    w.run()
    card_message_id = w.repo.get_publication(10, RPW, "new_bill", CHANNEL).message_id  # type: ignore[union-attr]

    # the Sejm assigns druk 3100; /processes and /bills both show it now
    w.gateway.submissions[0] = _submission(print_number="3100")
    w.add_bill("3100", "Poselski projekt ustawy o zmianie ustawy o udzielaniu cudzoziemcom ochrony")
    w.gateway.processes[-1] = w.gateway.processes[-1].model_copy(
        update={"change_date": dt.datetime(2026, 9, 8, 9, 0)}
    )
    w.clock.advance(days=1)
    report = w.run()
    assert report.linked == 1 and report.published == 0 and report.updates == 1  # type: ignore[attr-defined]
    assert (
        report.reanalyzed == 1
    )  # the real print text is analysed against the metadata analysis  # type: ignore[attr-defined]
    assert len(w.publisher.new_bills) == 1  # no second card
    bill, change, reply_to = w.publisher.updates[0]
    assert reply_to == card_message_id and bill.number == "3100"
    assert bill.linked_number == RPW and change.content_changed
    assert [st.stage_type for st in change.new_stages] == ["Start"]
    text = MessageFormatter("ru").status_update(bill, change).text
    assert (
        "Обновление — druk nr 3100" in text and "Проекту присвоен номер druku: <b>3100</b>" in text
    )
    pre = w.repo.get(10, RPW)
    assert pre is not None and pre.status is BillStatus.LINKED and pre.linked_number == "3100"
    assert w.repo.get(10, "3100").analysis.revision == 2  # type: ignore[union-attr]

    # afterwards the print is tracked like any other bill, the RPW entry is not
    w.clock.advance(days=1)
    report3 = w.run()
    assert report3.tracked == 1 and report3.updates == 0 and report3.linked == 0  # type: ignore[attr-defined]


def test_withdrawn_pre_print_bill_is_announced_once() -> None:
    w = World()
    w.gateway.submissions.append(_submission())
    w.run()
    w.gateway.submissions[0] = _submission(status="WITHDRAWN", withdrawn_date=dt.date(2026, 9, 5))
    w.clock.advance(days=1)
    assert w.run().updates == 1  # type: ignore[attr-defined]
    bill, change, _ = w.publisher.updates[0]
    assert change.withdrawn and change.closure_detected
    assert "Проект отозван" in MessageFormatter("ru").status_update(bill, change).text
    w.clock.advance(days=1)
    assert w.run().updates == 0  # type: ignore[attr-defined]


def test_numbered_print_card_shows_consultation_dates_and_explicit_applicant() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")  # title gives no applicant
    w.gateway.submissions.append(
        _submission(
            number="RPW/26666/2026", print_number="3039", applicant=ApplicantType.GOVERNMENT
        )
    )
    w.run()
    bill, _ = w.publisher.new_bills[0]
    assert bill.summary.applicant_type is ApplicantType.GOVERNMENT
    assert "Общественные консультации" in MessageFormatter("ru").new_bill(bill, None).text
    assert w.repo.get(10, "RPW/26666/2026") is None  # never seen as pre-print: no phantom row


def test_submissions_with_a_print_or_closed_are_not_pre_print_bills() -> None:
    w = World()
    w.gateway.submissions.append(_submission(number="RPW/1/2026", print_number="9"))
    w.gateway.submissions.append(_submission(number="RPW/2/2026", status="WITHDRAWN"))
    w.gateway.submissions.append(
        _submission(number="RPW/3/2026", submission_type="DRAFT_RESOLUTION")
    )
    report = w.run()
    assert report.pre_print_discovered == 0 and report.discovered == 0  # type: ignore[attr-defined]


FOREIGNER_TEXT = (
    "Art. 1. W ustawie o cudzoziemcach wprowadza się zmiany. "
    "Art. 2. Zezwolenie na pobyt czasowy wydaje wojewoda. "
    "Art. 3. Cudzoziemiec składa wniosek osobiście. " * 3
)


def test_text_prefilter_catches_bills_with_neutral_titles() -> None:
    w = World(extractor=FakeTextExtractor(FOREIGNER_TEXT))
    w.add_bill("4100", "Rządowy projekt ustawy o zmianie niektórych ustaw w związku z cyfryzacją")
    report = w.run()
    assert report.prefilter_hits == 0  # the title said nothing  # type: ignore[attr-defined]
    assert report.text_prefilter_checked == 1 and report.text_prefilter_hits == 1  # type: ignore[attr-defined]
    assert report.analyzed == 1 and report.published == 1  # type: ignore[attr-defined]
    bill = w.repo.get(10, "4100")
    assert bill is not None and bill.status is BillStatus.ANALYZED
    assert bill.prefilter_hits == [
        "text:cudzoziemcy",
        "text:zezwolenie_pobyt",
        "text:pobyt_kwalifikowany",
    ]
    assert "Найден по тексту проекта" in MessageFormatter("ru").new_bill(bill, None).text
    # the PDF was downloaded once: the analysis reused the prefilter's text
    assert sum(1 for c in w.gateway.calls if c.startswith("download:")) == 1


def test_text_prefilter_rejects_a_single_stray_mention() -> None:
    w = World(extractor=FakeTextExtractor("Art. 1. " * 40 + "cudzoziemiec " + "Art. 2. " * 40))
    w.add_bill("4100", "Rządowy projekt ustawy o podatku")
    report = w.run()
    assert report.text_prefilter_hits == 0 and report.analyzed == 0  # type: ignore[attr-defined]
    bill = w.repo.get(10, "4100")
    assert bill is not None and bill.status is BillStatus.SKIPPED_TEXT_PREFILTER
    assert bill.prefilter_hits == ["text:cudzoziemcy"]  # weak hit kept for tuning


def test_text_prefilter_without_pdf_or_with_broken_pdf_skips_quietly() -> None:
    class _Broken:
        def extract(self, data: bytes) -> str:
            raise ValueError("not a PDF")

    w = World(extractor=_Broken())
    w.add_bill("4100", "Rządowy projekt ustawy o podatku")
    w.add_bill("4101", "Rządowy projekt ustawy o lasach", with_pdf=False)
    report = w.run()
    assert report.text_prefilter_checked == 2 and not report.errors  # type: ignore[attr-defined]
    assert w.repo.get(10, "4100").status is BillStatus.SKIPPED_TEXT_PREFILTER  # type: ignore[union-attr]
    assert w.repo.get(10, "4101").status is BillStatus.SKIPPED_TEXT_PREFILTER  # type: ignore[union-attr]


def test_text_prefilter_outage_leaves_bills_pending() -> None:
    from lexinform.errors import SejmApiUnavailableError

    class _DownOnDownload(FakeSejmGateway):
        def download(self, url: str, *, max_bytes: int | None = None) -> bytes:
            raise SejmApiUnavailableError("GET pdf: connection refused")

    w = World()
    down = _DownOnDownload()
    down.processes, down.details, down.prints, down.files = (
        w.gateway.processes,
        w.gateway.details,
        w.gateway.prints,
        w.gateway.files,
    )
    w.gateway = down
    w.loader._gateway = down  # type: ignore[attr-defined]
    w.pipeline._text_prefilter._gateway = down  # type: ignore[attr-defined]
    w.add_bill("4100", "Rządowy projekt ustawy o podatku")
    report = w.run()
    assert any(e.startswith("text prefilter:") for e in report.errors)  # type: ignore[attr-defined]
    assert w.repo.get(10, "4100").status is BillStatus.TEXT_PREFILTER_PENDING  # type: ignore[union-attr]


def test_text_prefilter_can_be_disabled() -> None:
    w = World(text_prefilter=False, extractor=FakeTextExtractor(FOREIGNER_TEXT))
    w.add_bill("4100", "Rządowy projekt ustawy o podatku")
    w.run()
    assert w.repo.get(10, "4100").status is BillStatus.SKIPPED_PREFILTER  # type: ignore[union-attr]
    assert not any(c.startswith("download:") for c in w.gateway.calls)


ELI = "DU/2026/1099"


def _act(**kw: object) -> ActInfo:
    base: dict[str, object] = dict(
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
    base.update(kw)
    return ActInfo(**base)  # type: ignore[arg-type]


def _publish_act(w: World, number: str = "3039") -> None:
    """The process gains an ELI address (the act appeared in Dziennik Ustaw)."""
    s = w.gateway.processes[0].model_copy(
        update={
            "closure_date": dt.date(2026, 9, 8),
            "passed": True,
            "eli": ELI,
            "display_address": "Dz.U. 2026 poz. 1099",
            "change_date": dt.datetime(2026, 9, 9, 9, 0),
        }
    )
    w.gateway.processes[0] = s
    w.gateway.details[number] = _detail(s, REFERRED)


def test_act_publication_is_announced_once_and_reminded_on_entry_into_force() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    card_id = w.repo.get_publication(10, "3039", "new_bill", CHANNEL).message_id  # type: ignore[union-attr]

    _publish_act(w)
    w.clock.advance(days=2)  # 2026-09-09: ELI on the process, but the ELI API lags behind
    report = w.run()
    assert report.acts_published == 0 and not report.errors  # type: ignore[attr-defined]
    assert w.repo.get(10, "3039").act is None  # type: ignore[union-attr]

    w.gateway.acts[ELI] = _act()
    w.clock.advance(days=1)
    report2 = w.run()
    assert report2.acts_published == 1 and report2.in_force_posted == 0  # type: ignore[attr-defined]
    bill, reply_to = w.publisher.acts[0]
    assert reply_to == card_id and bill.act is not None and bill.act.eli == ELI
    text = MessageFormatter("ru").new_bill  # keep formatter import used
    rendered = MessageFormatter("ru").act_published(bill).text
    assert "Опубликован в Dziennik Ustaw — druk nr 3039" in rendered
    assert "Dz.U. 2026 poz. 1099 (опубликован 09.09.2026)" in rendered
    assert (
        "Вступает в силу:</b> 20.09.2026" in rendered and "#закон #kadencja10druk3039" in rendered
    )
    assert text  # noqa: S101  (formatter reference)

    w.clock.advance(days=1)
    assert w.run().acts_published == 0 and len(w.publisher.acts) == 1  # type: ignore[attr-defined]

    # entry into force: 2026-09-20 (Warsaw); the 06:00 UTC run on the 19th is still the 19th
    w.clock.current = dt.datetime(2026, 9, 19, 6, 0, tzinfo=dt.UTC)
    assert w.run().in_force_posted == 0  # type: ignore[attr-defined]
    w.clock.current = dt.datetime(2026, 9, 20, 6, 0, tzinfo=dt.UTC)
    report3 = w.run()
    assert report3.in_force_posted == 1  # type: ignore[attr-defined]
    bill2, reply_to2 = w.publisher.in_force[0]
    assert reply_to2 == card_id
    rendered2 = MessageFormatter("ru").in_force(bill2).text
    assert "С сегодняшнего дня действует — druk nr 3039" in rendered2
    assert "Суть закона" in rendered2 and "#вступилвсилу #kadencja10druk3039" in rendered2
    w.clock.advance(days=1)
    assert w.run().in_force_posted == 0 and len(w.publisher.in_force) == 1  # type: ignore[attr-defined]


def test_act_discovered_already_in_force_gets_no_separate_reminder() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    _publish_act(w)
    w.clock.current = dt.datetime(2026, 10, 1, 6, 0, tzinfo=dt.UTC)
    w.gateway.acts[ELI] = _act(fetched_at=w.clock.current, in_force="IN_FORCE")
    report = w.run()
    assert report.acts_published == 1 and report.in_force_posted == 0  # type: ignore[attr-defined]
    bill, _ = w.publisher.acts[0]
    assert "Уже действует с</b> 20.09.2026" in MessageFormatter("ru").act_published(bill).text
    assert w.publisher.in_force == []
    pub = w.repo.get_publication(10, "3039", "in_force", CHANNEL)
    assert pub is not None and pub.status is PublicationStatus.SKIPPED
    w.clock.advance(days=1)
    assert w.run().in_force_posted == 0  # type: ignore[attr-defined]


def test_failed_act_notice_is_retried_next_run() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    _publish_act(w)
    w.gateway.acts[ELI] = _act()
    w.publisher.fail_on = {"3039"}
    w.clock.advance(days=2)
    report = w.run()
    assert report.acts_published == 0 and report.errors  # type: ignore[attr-defined]
    w.publisher.fail_on = set()
    w.clock.advance(days=1)
    assert w.run().acts_published == 1 and len(w.publisher.acts) == 1  # type: ignore[attr-defined]


DEPUTIES_LETTER = (
    "Druk nr 4200\nniżej podpisani posłowie wnoszą projekt ustawy:\n- o zmianie ustawy o cudzoziemcach.\n"
    "Do reprezentowania wnioskodawców w pracach nad projektem ustawy upoważniamy posła Jana Kowalskiego.\n\n"
    " (-)  Jan Kowalski;  (-)  Anna Nowak;  (-)  Piotr Zieliński.\n\n"
    "Tłoczono z polecenia Marszałka Sejmu\n\nProjekt\nUSTAWA\n" + FOREIGNER_TEXT
)


def test_deputies_bill_card_shows_signatory_clubs_and_representative() -> None:
    w = World(extractor=FakeTextExtractor(DEPUTIES_LETTER))
    w.gateway.mps = (
        Mp(
            id=1,
            first_name="Jan",
            last_name="Kowalski",
            accusative_name="Jana Kowalskiego",
            club="KO",
        ),
        Mp(id=2, first_name="Anna", last_name="Nowak", club="KO"),
        Mp(id=3, first_name="Piotr", last_name="Zieliński", club="Lewica"),
    )
    w.add_bill("4200", "Poselski projekt ustawy o zmianie ustawy o cudzoziemcach")
    w.run()
    bill = w.repo.get(10, "4200")
    assert bill is not None and bill.authors is not None
    assert bill.authors.clubs == (("KO", 2), ("Lewica", 1))
    assert (
        bill.authors.representative == "Jan Kowalski" and bill.authors.representative_club == "KO"
    )
    text = MessageFormatter("ru").new_bill(bill, None).text
    assert (
        "Инициатор:</b> депутатский (подписали: KO 2, Lewica 1 · представитель: Jan Kowalski, KO)"
        in text
    )
    assert w.gateway.calls.count("list_mps") == 1  # the directory is fetched once per process


def test_government_bill_does_not_fetch_the_mp_directory() -> None:
    w = World()
    w.add_bill("4201", "Rządowy projekt ustawy o zmianie ustawy o cudzoziemcach")
    w.run()
    assert "list_mps" not in w.gateway.calls
    assert w.repo.get(10, "4201").authors is None  # type: ignore[union-attr]


# --------------------------------------------------------------------------- parallel fetching


def _counters(report) -> dict[str, int]:  # type: ignore[no-untyped-def]
    return {
        k: getattr(report, k)
        for k in (
            "discovered",
            "prefilter_hits",
            "text_prefilter_checked",
            "text_prefilter_hits",
            "analyzed",
            "analysis_failures",
            "published",
            "tracked",
            "updates",
        )
    }


def test_parallel_workers_produce_the_same_result_as_a_plain_loop() -> None:
    """Downloads, analyses and process lookups may run side by side; every decision and every
    database write still happens in order, so the outcome cannot depend on the worker count."""
    runs = []
    for workers in (1, 4):
        w = World(extractor=FakeTextExtractor(FOREIGNER_TEXT), workers=workers)
        w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
        w.add_bill("3040", "Projekt ustawy o obywatelstwie polskim")
        w.add_bill("4000", "Rządowy projekt ustawy o podatku VAT")  # caught by the PDF text
        w.add_bill("4001", "Rządowy projekt ustawy o zmianie niektórych ustaw")
        first = _counters(w.run())
        for number in ("3039", "3040", "4000", "4001"):
            detail = w.gateway.details[number]
            w.gateway.details[number] = detail.model_copy(update={"stages": REFERRED})
        second = _counters(w.run())
        runs.append((first, second, sorted(b.number for b, _ in w.publisher.new_bills)))
    assert runs[0] == runs[1]
    first, second, published = runs[1]
    assert first["analyzed"] == 4 and first["published"] == 4 and first["text_prefilter_hits"] == 2
    assert second["tracked"] == 4 and second["updates"] == 4
    assert published == ["3039", "3040", "4000", "4001"]


def test_oversized_pdf_is_analysed_from_metadata() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    url = "https://api.test/sejm/term10/prints/3039/3039.pdf"
    w.gateway.files[url] = b"%" * (10_000_000 + 1)  # one byte over the loader's limit
    report = w.run()
    assert report.analyzed == 1 and report.published == 1  # type: ignore[attr-defined]
    assert w.llm.contexts[0].text_source == "metadata_only"


def test_report_carries_phase_timings() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    report = w.run()
    assert set(report.phase_seconds) == {  # type: ignore[attr-defined]
        "discovery",
        "text prefilter",
        "analysis",
        "publishing",
        "tracking",
    }


# --------------------------------------------------------------------------- triage


def test_triage_rejection_is_stored_as_a_non_relevant_analysis() -> None:
    from lexinform.models import Triage

    w = World(
        extractor=FakeTextExtractor(FOREIGNER_TEXT),
        triage=True,
        triage_script={
            "4000": Triage(affects_foreigners=False, confidence=0.95, rationale="о бананах"),
            "4001": Triage(affects_foreigners=False, confidence=0.5, rationale="не уверен"),
        },
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")  # triage says relevant -> full analysis
    w.add_bill("4000", "Rządowy projekt ustawy o jakości handlowej")  # confident no
    w.add_bill("4001", "Rządowy projekt ustawy o zmianie niektórych ustaw")  # unsure -> full
    report = w.run()
    assert report.analyzed == 2 and report.triaged_out == 1 and report.published == 2  # type: ignore[attr-defined]
    assert [(v.number, v.reason) for v in report.rejected] == [("4000", "triage")]  # type: ignore[attr-defined]
    assert report.rejected[0].title == "Rządowy projekt ustawy o jakości handlowej"  # type: ignore[attr-defined]
    assert sorted(c.number for c in w.llm.triage_contexts) == ["3039", "4000", "4001"]
    assert sorted(c.number for c in w.llm.contexts) == ["3039", "4001"]
    rejected = w.repo.get(10, "4000")
    assert rejected is not None and rejected.status is BillStatus.ANALYZED
    assert rejected.analysis is not None
    assert rejected.analysis.text_source == "excerpts" and rejected.analysis.model == "fake-triage"
    assert (
        not rejected.analysis.analysis.relevant
        and rejected.analysis.analysis.summary == "о бананах"
    )
    assert report.llm_input_tokens == 3 * 10 + 2 * 100  # type: ignore[attr-defined]
    assert report.llm_usage["fake-triage"].input == 30 and report.llm_usage["fake"].input == 200  # type: ignore[attr-defined]
    # the excerpts are what the triage saw: keyword windows from the text, never the whole print
    ctx = w.llm.triage_contexts[0]
    assert "cudzoziem" in ctx.excerpts.lower() and ctx.text_chars > 0
    # nothing is published for it and a second run leaves it alone
    assert w.run().analyzed == 0  # type: ignore[attr-defined]


def test_triage_failure_is_a_per_bill_failure() -> None:
    w = World(triage=True, triage_script={"3039": RuntimeError("bad json")})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    report = w.run()
    assert report.analysis_failures == 1 and report.analyzed == 0  # type: ignore[attr-defined]
    assert w.repo.get(10, "3039").analysis_attempts == 1  # type: ignore[union-attr]


def test_short_texts_skip_the_triage() -> None:
    w = World(triage=True)
    w.pipeline._analysis._triage_min_chars = 10_000_000  # type: ignore[attr-defined]
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    assert w.run().analyzed == 1  # type: ignore[attr-defined]
    assert w.llm.triage_contexts == []


# --------------------------------------------------------------------------- tracking scope


def test_daily_tracking_checks_only_bills_the_api_listed_as_changed() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o obywatelstwie polskim")
    w.run()  # FixedClock starts on Monday 2026-09-07: a full check, both published
    assert len(w.publisher.new_bills) == 2

    w.clock.advance(days=1)  # Tuesday: only what changed
    w.gateway.processes = [
        p.model_copy(update={"change_date": dt.datetime(2026, 9, 8, 9, 0)})
        if p.number == "3040"
        else p
        for p in w.gateway.processes
    ]
    w.gateway.details["3040"] = w.gateway.details["3040"].model_copy(
        update={"stages": REFERRED, "change_date": dt.datetime(2026, 9, 8, 9, 0)}
    )
    w.gateway.calls.clear()
    report = w.run(since=dt.datetime(2026, 9, 7, tzinfo=dt.UTC))
    assert report.tracked == 1 and report.updates == 1  # type: ignore[attr-defined]
    assert "get_process:3040" in w.gateway.calls and "get_process:3039" not in w.gateway.calls

    w.gateway.calls.clear()
    report = w.run(since=dt.datetime(2026, 9, 7, tzinfo=dt.UTC), full_track=True)
    assert report.tracked == 2 and report.updates == 0  # type: ignore[attr-defined]
    assert "get_process:3039" in w.gateway.calls

    w.clock.advance(days=6)  # next Monday: the weekly full check needs no flag
    w.gateway.calls.clear()
    assert w.run(since=dt.datetime(2026, 9, 14, tzinfo=dt.UTC)).tracked == 2  # type: ignore[attr-defined]


def test_passed_bills_waiting_for_their_act_are_always_checked() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.gateway.details["3039"] = w.gateway.details["3039"].model_copy(
        update={"passed": True, "closure_date": dt.date(2026, 9, 7)}
    )
    w.run()  # Monday: full check stores passed=True
    w.clock.advance(days=1)
    w.gateway.calls.clear()
    report = w.run(since=dt.datetime(2026, 9, 8, tzinfo=dt.UTC))  # nothing listed as changed
    assert report.tracked == 1 and "get_process:3039" in w.gateway.calls  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- consultation reminder


def test_consultation_deadline_is_reminded_once_a_few_days_ahead() -> None:
    w = World()  # clock: 2026-09-07
    w.gateway.submissions.append(_submission(consultation_end=dt.date(2026, 9, 20)))
    report = w.run()
    assert report.published == 1 and report.consultation_reminders == 0  # type: ignore[attr-defined]
    card_id = w.repo.get_publication(10, RPW, "new_bill", CHANNEL).message_id  # type: ignore[union-attr]

    w.clock.advance(days=10)  # 2026-09-17: three days left
    report = w.run()
    assert report.consultation_reminders == 1  # type: ignore[attr-defined]
    bill, reply_to, today = w.publisher.consultations[0]
    assert bill.number == RPW and reply_to == card_id and today == dt.date(2026, 9, 17)
    text = MessageFormatter("ru").consultation_deadline(bill, today=today).text
    assert "Консультации заканчиваются — RPW/29075/2026" in text
    assert "до 20.09.2026 · осталось дней: 3" in text and "#консультации #RPW_29075_2026" in text
    assert (
        "сегодня последний день"
        in MessageFormatter("ru").consultation_deadline(bill, today=dt.date(2026, 9, 20)).text
    )

    w.clock.advance(days=1)
    assert w.run().consultation_reminders == 0  # type: ignore[attr-defined]
    assert len(w.publisher.consultations) == 1

    w.clock.advance(days=5)  # 2026-09-23: over, never reminded late
    w.gateway.submissions.append(
        _submission(number="RPW/1/2026", consultation_end=dt.date(2026, 9, 21))
    )
    assert w.run().consultation_reminders == 0  # type: ignore[attr-defined]


def test_consultation_reminder_failure_is_retried_next_run() -> None:
    w = World()
    w.gateway.submissions.append(_submission(consultation_end=dt.date(2026, 9, 9)))
    w.publisher.fail_on = {RPW}
    w.run()  # card fails too: nothing to remind under
    w.publisher.fail_on = set()
    report = w.run()
    assert report.published == 1 and report.consultation_reminders == 1  # type: ignore[attr-defined]
    w.publisher.fail_on = {RPW}
    w.gateway.submissions.append(
        _submission(number="RPW/2/2026", consultation_end=dt.date(2026, 9, 8))
    )
    w.publisher.fail_on = set()
    report = w.run()
    assert report.consultation_reminders == 1 and len(w.publisher.consultations) == 2  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- sitting agendas

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
ASW = Committee(term=10, code="ASW", name="Komisja Administracji i Spraw Wewnętrznych")
FIRST_READING_AGENDA = (
    '<div class="agenda-indent-0">Pierwsze czytanie projektu (druk nr 3039)</div>\n'
    '<div class="agenda-indent-0">– uzasadnia poseł X.</div>'
)


def _committee_sitting(
    num: int = 136,
    date: dt.date = dt.date(2026, 9, 17),
    *,
    agenda: str = FIRST_READING_AGENDA,
    status: str = "PLANNED",
) -> CommitteeSitting:
    return CommitteeSitting(
        code="ASW",
        num=num,
        date=date,
        start_time=dt.time(9, 0),
        room="sala 412",
        status=status,
        agenda=agenda,
        video_url="https://sejm.gov.pl/Sejm10.nsf/transmisje_arch.xsp?unid=1",
    )


def test_committee_sitting_naming_the_bill_is_posted_once_and_dates_the_next_step() -> None:
    w = World()  # clock: 2026-09-07
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=COMMITTEE_STAGES)
    w.gateway.committees["ASW"] = ASW
    w.gateway.committee_sittings["ASW"] = (
        _committee_sitting(130, dt.date(2026, 9, 2), status="FINISHED"),  # over: ignored
        _committee_sitting(135, dt.date(2026, 9, 15), agenda="<div>Inne sprawy (druk nr 1)</div>"),
        _committee_sitting(),
    )
    report = w.run()
    assert report.published == 1 and report.agenda_posted == 1  # type: ignore[attr-defined]
    bill, item, reply_to = w.publisher.agendas[0]
    assert reply_to == 101 and bill.number == "3039"
    assert item.ref == "ASW/136/2026-09-17" and item.kind == "committee"
    assert item.committee_name == "Komisja Administracji i Spraw Wewnętrznych"
    assert item.text == "Pierwsze czytanie projektu (druk nr 3039) – uzasadnia poseł X."
    assert item.start_time == dt.time(9, 0) and item.room == "sala 412"
    stored = w.repo.get(10, "3039")
    assert stored is not None and [i.ref for i in stored.agenda] == ["ASW/136/2026-09-17"]

    # the same sitting on the next run: nothing new; a stage update now carries the date
    w.clock.advance(days=1)
    reading = Stage(
        stage_name="I czytanie w komisjach", stage_type="Reading", date=dt.date(2026, 9, 8)
    )
    w.gateway.details["3039"] = _detail(w.gateway.processes[0], COMMITTEE_STAGES + (reading,))
    report = w.run()
    assert report.agenda_posted == 0 and report.updates == 1  # type: ignore[attr-defined]
    updated, change, _ = w.publisher.updates[0]
    text = MessageFormatter("ru").status_update(updated, change, today=dt.date(2026, 9, 8)).text
    assert "Komisja Administracji i Spraw Wewnętrznych (ASW) (sprawozdanie)" in text
    assert "· 17.09.2026, 09:00" in text and "до заседания 17.09.2026" in text

    # rescheduled: same sitting number, new date -> posted again, the old item is gone
    w.gateway.committee_sittings["ASW"] = (_committee_sitting(136, dt.date(2026, 9, 24)),)
    w.clock.advance(days=1)
    assert w.run().agenda_posted == 1  # type: ignore[attr-defined]
    assert [i.ref for i in w.repo.get(10, "3039").agenda] == ["ASW/136/2026-09-24"]  # type: ignore[union-attr]

    # the sitting is over: the item is dropped, no post
    w.clock.advance(days=20)
    assert w.run().agenda_posted == 0  # type: ignore[attr-defined]
    assert w.repo.get(10, "3039").agenda == ()  # type: ignore[union-attr]
    assert len(w.publisher.agendas) == 2


def test_sejm_sitting_agenda_naming_the_bill_is_posted() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    w.gateway.sittings = [
        SejmSitting(
            number=64,
            dates=(dt.date(2026, 9, 2), dt.date(2026, 9, 4)),
            agenda="<li>Stare (druk nr 3039)</li>",  # over: not fetched
        ),
        SejmSitting(
            number=65,
            dates=tuple(dt.date(2026, 9, d) for d in (15, 16, 17, 18)),
            agenda=(
                "<ol><li>Sprawozdanie Komisji (druki nr 3039 i 3055) - sprawozdawca poseł Y.</li>"
                "<li>Inne (druk nr 1)</li></ol>"
            ),
        ),
        SejmSitting(number=0, dates=(dt.date(2026, 10, 7),), title="planned"),  # no agenda yet
    ]
    report = w.run()
    assert report.agenda_posted == 1  # type: ignore[attr-defined]
    bill, item, _ = w.publisher.agendas[0]
    assert item.kind == "sejm" and item.ref == "sejm/65/2026-09-15"
    assert item.date == dt.date(2026, 9, 15) and item.end_date == dt.date(2026, 9, 18)
    assert item.sitting_number == 65
    assert item.text == "Sprawozdanie Komisji (druki nr 3039 i 3055) - sprawozdawca poseł Y."
    fetched = [c for c in w.gateway.calls if c.startswith("get_sitting:")]
    assert fetched == ["get_sitting:65"]
    text = MessageFormatter("ru").agenda(bill, item, today=dt.date(2026, 9, 7)).text
    assert "заседание Сейма № 65, 15–18.09.2026" in text
    # no committee referral yet: the next step is the first reading, dated by the sitting
    card = MessageFormatter("ru").new_bill(bill, None, today=dt.date(2026, 9, 7)).text
    assert "Что дальше:</b> I чтение на заседании Сейма · заседание Сейма № 65" in card


def test_agenda_check_keeps_known_items_when_one_listing_fails_and_aborts_on_an_outage() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=COMMITTEE_STAGES)
    w.gateway.committees["ASW"] = ASW
    w.gateway.committee_sittings["ASW"] = (_committee_sitting(),)
    assert w.run().agenda_posted == 1  # type: ignore[attr-defined]

    del w.gateway.committee_sittings["ASW"]  # a 4xx for this committee: per-item problem
    w.clock.advance(days=1)
    report = w.run()
    assert report.agenda_posted == 0 and report.errors == []  # type: ignore[attr-defined]
    assert [i.ref for i in w.repo.get(10, "3039").agenda] == ["ASW/136/2026-09-17"]  # type: ignore[union-attr]

    w.gateway.outages.add("list_committee_sittings")
    w.clock.advance(days=1)
    report = w.run()
    assert any(e.startswith("tracking: Sejm API unavailable") for e in report.errors)  # type: ignore[attr-defined]
    assert [i.ref for i in w.repo.get(10, "3039").agenda] == ["ASW/136/2026-09-17"]  # type: ignore[union-attr]
    assert len(w.publisher.agendas) == 1


def test_agenda_items_are_stored_but_not_posted_without_publishing() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=COMMITTEE_STAGES)
    w.gateway.committees["ASW"] = ASW
    w.run()
    w.gateway.committee_sittings["ASW"] = (_committee_sitting(),)
    w.clock.advance(days=1)
    report = w.run(publish=False)
    assert report.agenda_posted == 0 and w.publisher.agendas == []  # type: ignore[attr-defined]
    assert [i.ref for i in w.repo.get(10, "3039").agenda] == ["ASW/136/2026-09-17"]  # type: ignore[union-attr]
    w.clock.advance(days=1)
    assert w.run().agenda_posted == 1  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- consultation results


def test_published_consultation_opinions_are_announced_once() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.gateway.submissions.append(_submission(number="RPW/26666/2026", print_number="3039"))
    report = w.run()
    assert report.published == 1 and report.consultation_results_posted == 0  # type: ignore[attr-defined]

    w.gateway.submissions[0] = _submission(
        number="RPW/26666/2026", print_number="3039", consultation_results=True
    )
    w.clock.advance(days=1)
    report = w.run()
    assert report.consultation_results_posted == 1  # type: ignore[attr-defined]
    bill, reply_to = w.publisher.consultation_results[0]
    assert bill.number == "3039" and reply_to == 101
    assert bill.submission is not None and bill.submission.consultation_results
    text = MessageFormatter("ru").consultation_results(bill).text
    assert "Опубликованы мнения из консультаций — druk nr 3039" in text
    assert "NrProjektu=RPW/26666/2026" in text

    w.clock.advance(days=1)
    assert w.run().consultation_results_posted == 0  # type: ignore[attr-defined]
    assert len(w.publisher.consultation_results) == 1

    # a pre-print bill: no notice when discovered with the opinions already out, one when they appear
    w.gateway.submissions.append(_submission(consultation_results=True))  # RPW/29075/2026
    w.gateway.submissions.append(_submission(number="RPW/2/2026"))
    w.clock.advance(days=1)
    report = w.run()
    assert report.published == 2 and report.consultation_results_posted == 0  # type: ignore[attr-defined]
    w.gateway.submissions[-1] = _submission(number="RPW/2/2026", consultation_results=True)
    w.clock.advance(days=1)
    assert w.run().consultation_results_posted == 1  # type: ignore[attr-defined]
    assert w.publisher.consultation_results[-1][0].number == "RPW/2/2026"
