import datetime as dt

import pytest

from lexinform.models import BillStatus, PublicationKind, Stage
from tests.fakes import FakeTextExtractor
from tests.harness import TERM, World
from tests.scenario.test_law_fingerprint import _analysed_print_then_a_new_version
from tests.scenario.test_tracking import REPORT_TEXT, REPORT_URL, WITH_REPORT

REJECTED = Stage(
    stage_name="III czytanie na posiedzeniu Sejmu",
    stage_type="SejmReading",
    decision="odrzucono",
    date=dt.date(2026, 9, 10),
)
VETO_STOOD = Stage(
    stage_name="Rozpatrywanie na forum Sejmu wniosku Prezydenta",
    stage_type="PresidentMotionConsideration",
    decision="nie uchwalona ponownie",
    date=dt.date(2026, 9, 10),
)


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("decision", [REJECTED, VETO_STOOD])
def test_final_rejection_posts_the_decision_without_reading_the_text(
    workers: int, decision: Stage
) -> None:
    w = World(workers=workers)
    _analysed_print_then_a_new_version(w, b"%PDF-new")
    previous = w.bill("3039").analysis
    assert previous is not None and previous.text_sha256 is not None
    legacy = previous.model_copy(
        update={"text_sha256": previous.text_sha256.removeprefix("text-v2:"), "law_sha256": None}
    )
    w.repo.save_analysis(TERM, "3039", legacy)
    w.set_stages("3039", (decision,))
    w.touch("3039", w.clock.now(), passed=False, closure_date=w.clock.now().date())

    report = w.run(full_track=True)
    w.clock.advance(days=1)
    again = w.run(full_track=True)

    assert report.updates == 1 and report.reanalyzed == 0
    assert again.updates == 0 and again.reanalyzed == 0
    assert len(w.llm.contexts) == 1
    assert w.bill("3039").analysis == legacy
    assert not w.publisher.updates[0][1].content_changed
    assert "Новая версия текста" not in w.publisher.texts(PublicationKind.STATUS_UPDATE)[0]


@pytest.mark.parametrize("resolved", [False, True])
def test_rejection_invalidates_pending_or_ready_reanalysis_without_losing_accounting(
    resolved: bool,
) -> None:
    w = World(batch=True, extractor=FakeTextExtractor(by_content={b"%PDF-report": REPORT_TEXT}))
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.batch.resolve()
    w.run()
    original = w.bill("3039").analysis
    w.set_stages("3039", WITH_REPORT)
    w.gateway.files[REPORT_URL] = b"%PDF-report"
    w.run(full_track=True)
    assert w.bill("3039").status is BillStatus.BATCH_PENDING
    if resolved:
        w.batch.resolve()
    w.set_stages("3039", (*WITH_REPORT, VETO_STOOD))
    w.touch("3039", w.clock.now(), passed=False, closure_date=w.clock.now().date())

    report = w.run(full_track=True)
    w.batch.resolve()
    after = w.run(full_track=True)

    assert report.reanalyzed == after.reanalyzed == 0
    assert w.bill("3039").status is BillStatus.ANALYZED
    assert w.bill("3039").ready_analysis is None
    assert w.bill("3039").awaiting_batch_since is None
    assert w.bill("3039").analysis == original
    assert len(w.batch.submitted) == 2
    assert not w.repo.list_unaccounted_batch_items()
    assert all(not change.content_changed for _, change, _ in w.publisher.updates)


@pytest.mark.parametrize("decision", [None, "uchwalono ponownie"])
def test_an_unresolved_or_overridden_veto_does_not_block_text_analysis(
    decision: str | None,
) -> None:
    w = World(extractor=FakeTextExtractor(by_content={b"%PDF-new": REPORT_TEXT}))
    _analysed_print_then_a_new_version(w, b"%PDF-new")
    w.set_stages(
        "3039",
        (
            Stage(stage_name="Wniosek Prezydenta (weto)", stage_type="Veto"),
            VETO_STOOD.model_copy(update={"decision": decision}),
        ),
    )

    report = w.run(full_track=True)

    assert report.reanalyzed == 1
