import pytest

from lexinform.models import BillStatus, DeliveryPlan
from lexinform.services.analysis_applications import apply_analysis
from tests.fakes import FakeTextExtractor, make_analysis
from tests.harness import World
from tests.scenario.test_tracking import REPORT_TEXT, REPORT_URL, WITH_REPORT


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("batch", [False, True])
def test_sejm_analysis_and_application_share_the_observation_checkpoint(
    workers: int, batch: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(
        workers=workers,
        profile="production" if batch else "default",
        extractor=FakeTextExtractor(by_content={b"%PDF-report": REPORT_TEXT}),
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    if batch:
        world.batch.resolve()
        world.run()
    original = world.bill("3039")
    world.set_stages("3039", WITH_REPORT)
    world.gateway.files[REPORT_URL] = b"%PDF-report"
    world.clock.advance(days=1)
    if batch:
        world.run()
        world.batch.resolve()
        world.analysis.collect_batches()
    before = world.bill("3039")
    applications = world.repo.list_result_applications(10, "3039")

    def crash(change_id: int, channel_id: str, delivery: DeliveryPlan) -> None:
        raise RuntimeError("injected Sejm delivery checkpoint failure")

    with monkeypatch.context() as patch:
        patch.setattr(world.repo, "save_update_delivery", crash)
        failed = world.run()

    assert failed.errors
    current = world.bill("3039")
    assert current.analysis == original.analysis
    assert failed.reanalyzed == 0
    assert current.observed_process == before.observed_process
    assert world.repo.list_result_applications(10, "3039") == applications
    assert current.analysis_generation == before.analysis_generation
    if batch:
        assert current.status is BillStatus.REANALYSIS_READY and current.ready_analysis is not None
        assert len(world.repo.list_computation_results(10, "3039")) == 2
    assert len(world.batch.submitted) == (2 if batch else 0)
    assert len(world.llm.contexts) == (0 if batch else 2)

    recovered = world.run()
    repeated = world.run()

    assert (recovered.reanalyzed, repeated.reanalyzed) == (1, 0)
    assert (recovered.updates, repeated.updates) == (1, 0)
    record = world.bill("3039").analysis
    assert record is not None, "the committed checkpoint applies its prepared analysis"
    assert record.source_url == REPORT_URL and record.revision == 2
    assert world.bill("3039").ready_analysis is None
    assert len(world.batch.submitted) == (2 if batch else 0)
    assert len(world.llm.contexts) == (0 if batch else 2)
    sent_bill, change, _ = world.publisher.updates[-1]
    assert sent_bill.analysis == record and change.content_changed


@pytest.mark.parametrize("workers", [1, 4])
def test_pre_upgrade_applied_analysis_keeps_its_uncommitted_news(workers: int) -> None:
    world = World(
        workers=workers,
        profile="production",
        extractor=FakeTextExtractor(by_content={b"%PDF-report": REPORT_TEXT}),
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    world.batch.resolve()
    world.run()
    world.set_stages("3039", WITH_REPORT)
    world.gateway.files[REPORT_URL] = b"%PDF-report"
    world.clock.advance(days=1)
    world.run()
    world.batch.resolve()
    world.analysis.collect_batches()
    before = world.bill("3039")
    ready = before.ready_analysis
    assert ready is not None, "the old checkpoint has collected the second reading"
    apply_analysis(
        world.repo, 10, "3039", ready.record, now=world.clock.now(), reason="batch_result"
    )
    world.repo.restore(world.repo.dump())

    report = world.run()
    repeated = world.run()

    assert (report.updates, repeated.updates) == (1, 0)
    assert len(world.batch.submitted) == 2
    assert world.publisher.updates[-1][1].content_changed


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("new_summary", [False, True])
def test_prepared_analysis_waits_for_the_secondary_result_without_reapplication(
    workers: int,
    new_summary: bool,
) -> None:
    world = World(
        workers=workers,
        profile="production",
        extractor=FakeTextExtractor(by_content={b"%PDF-report": REPORT_TEXT}),
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    world.batch.resolve()
    world.run()
    if new_summary:
        world.batch.secondary_script[("3039", "reanalysis")] = make_analysis(score=4).model_copy(
            update={"summary": "Новая редакция меняет электронную подачу заявления."}
        )
    world.set_stages("3039", WITH_REPORT)
    world.gateway.files[REPORT_URL] = b"%PDF-report"
    world.clock.advance(days=1)
    world.run()
    world.batch.resolve()
    world.analysis.collect_batches()
    before = world.bill("3039")
    paid = world.repo.list_computation_results(10, "3039")[-1]
    world.file_to_print("3039", "Do druku nr 3039 - ocena skutków regulacji")

    waiting = world.run()

    assert waiting.batch_waiting == 1 and waiting.reanalyzed == 0
    assert world.bill("3039").analysis == before.analysis
    assert world.bill("3039").status is BillStatus.REANALYSIS_READY
    assert world.bill("3039").ready_analysis == before.ready_analysis
    assert not [
        row
        for row in world.repo.list_result_applications(10, "3039")
        if row.phase == "projection" and row.result_id == paid.result_id
    ]
    world.batch.resolve()
    world.clock.advance(hours=1)

    posted = world.run()
    repeated = world.run()

    assert (posted.reanalyzed, repeated.reanalyzed) == (1, 0)
    assert posted.updates == 1 and world.publisher.updates[-1][1].content_changed
    record = world.bill("3039").analysis
    assert record is not None, "the complete update commits its prepared primary result"
    assert record.computation_id == paid.result_id
    assert world.publisher.updates[-1][0].analysis == record
    assert (
        len(
            [
                row
                for row in world.repo.list_result_applications(10, "3039")
                if row.phase == "projection" and row.result_id == paid.result_id
            ]
        )
        == 1
    )
    assert len(world.batch.submitted) == 3
