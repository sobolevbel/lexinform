"""End-to-end pipeline tests on fakes: idempotency, failure isolation, tracking."""

from __future__ import annotations

import datetime as dt

import pytest

from lexinform.adapters.pdf_text import TextBudget
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.keywords import KeywordPrefilter
from lexinform.models import (
    BillStatus,
    Category,
    Committee,
    DocumentType,
    PrintInfo,
    ProcessDetail,
    ProcessSummary,
    PublicationStatus,
    Stage,
    Vote,
    VotingSummary,
)
from lexinform.services.analysis import AnalysisService
from lexinform.services.discovery import BillDiscoveryService
from lexinform.services.pipeline import DailyPipeline, RunOptions
from lexinform.services.publishing import PublishingService
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
    def __init__(self, *, fail_publish: set[str] | None = None, llm_script=None) -> None:  # type: ignore[no-untyped-def]
        self.clock = FixedClock()
        self.repo = SqliteBillRepository(":memory:")
        self.repo.migrate()
        self.gateway = FakeSejmGateway()
        self.llm = FakeLlm(script=llm_script)
        self.publisher = FakePublisher(fail_on=fail_publish)
        self.notifier = FakeNotifier()
        analysis = AnalysisService(
            self.gateway,
            self.repo,
            FakeTextExtractor(),
            self.llm,
            text_budget=TextBudget(10_000),
            max_pdf_bytes=10_000_000,
        )
        self.pipeline = DailyPipeline(
            self.repo,
            BillDiscoveryService(self.gateway, self.repo, KeywordPrefilter(), self.clock),
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
            ),
            self.clock,
            notifier=self.notifier,
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
        opts = RunOptions(term=10, since=dt.datetime(2026, 9, 1, tzinfo=dt.UTC), **kw)
        return self.pipeline.run(opts)


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
    assert w.repo.get(10, "4000").status is BillStatus.SKIPPED_PREFILTER  # type: ignore[union-attr]
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

    w = World()
    w.pipeline._analysis._extractor = _BrokenExtractor()  # type: ignore[attr-defined]
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
