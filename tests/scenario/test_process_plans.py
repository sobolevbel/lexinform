import datetime as dt

import pytest

from lexinform.models import (
    DeliveryPlan,
    PublicationKind,
    PublicationStatus,
    Stage,
    TextDocument,
    next_phase,
)
from tests.fakes import FakeTextExtractor, make_analysis
from tests.harness import COMMITTEE_STAGES, START, World
from tests.scenario.test_tracking import REPORT_TEXT, REPORT_URL, WITH_REPORT


@pytest.mark.parametrize("workers", [1, 4])
def test_queued_news_is_delivered_without_waiting_for_another_event(workers: int) -> None:
    w = World(workers=workers)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.set_stages("3039", COMMITTEE_STAGES)
    w.clock.advance(days=1)

    w.run(publish=False)
    row = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert row is not None and row.status is PublicationStatus.QUEUED
    assert row.delivery is not None and row.attempts == 0
    w.repo.restore(w.repo.dump())
    sent = w.run()
    repeated = w.run()

    assert (sent.updates, repeated.updates) == (1, 0)
    bill, change, _ = w.publisher.updates[0]
    assert "комисси" in w.formatter.status_update(bill, change).text.lower()
    assert len(w.llm.contexts) == 1


@pytest.mark.parametrize("workers", [1, 4])
def test_failed_delivery_keeps_its_analysis_and_process_snapshot(workers: int) -> None:
    w = World(workers=workers)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.set_stages("3039", COMMITTEE_STAGES)
    w.publisher.fail_on = {"3039"}
    w.clock.advance(days=1)
    w.run()
    failed = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert failed is not None and failed.delivery is not None, (
        "a failed update keeps its row and plan"
    )
    original = failed.delivery
    current = w.bill("3039")
    assert current.analysis is not None, "the bill was analysed before its card went out"
    replacement = current.analysis.model_copy(deep=True)
    replacement.analysis.summary = "Это уже совершенно другая редакция"
    replacement.revision += 1
    w.repo.save_analysis(10, "3039", replacement)
    w.repo.save_stages(10, "3039", START, "newer-snapshot")
    w.repo.upsert_summary(
        current.summary.model_copy(update={"title": "Другое название", "passed": False}),
        now=w.clock.now(),
    )
    w.publisher.fail_on = set()

    w.run(full_track=True)

    bill, change, _ = w.publisher.updates[0]
    assert bill.model_dump_json() == original.bill_json
    assert change.model_dump_json() == original.change_json
    text = w.formatter.status_update(bill, change).text
    assert "совершенно другая" not in text and "Другое название" not in text


@pytest.mark.parametrize("workers", [1, 4])
def test_checkpoint_failure_retries_news_without_repeating_the_paid_analysis(
    workers: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = World(
        workers=workers, extractor=FakeTextExtractor(by_content={b"%PDF-report": REPORT_TEXT})
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    before = w.bill("3039").observed_process
    w.set_stages("3039", WITH_REPORT)
    w.gateway.files[REPORT_URL] = b"%PDF-report"
    w.llm.script = {"3039": make_analysis(score=4)}
    w.clock.advance(days=1)

    def fail_save(change_id: int, channel_id: str, delivery: DeliveryPlan) -> None:
        raise RuntimeError("injected checkpoint failure")

    with monkeypatch.context() as patch:
        patch.setattr(w.repo, "save_update_delivery", fail_save)
        failed = w.run()

    assert failed.errors
    assert w.bill("3039").observed_process == before
    assert w.publication("3039", PublicationKind.STATUS_UPDATE) is None
    assert w.repo.get_status_change(1) is None
    assert len(w.llm.contexts) == 2

    recovered = w.run()
    repeated = w.run()

    assert (recovered.updates, repeated.updates) == (1, 0)
    assert len(w.llm.contexts) == 2
    bill, change, _ = w.publisher.updates[0]
    assert change.content_changed and bill.analysis is not None
    assert bill.analysis.source_url == REPORT_URL
    assert bill.analysis.revision == 2


def test_unrecognised_process_is_not_silently_discarded_as_finished() -> None:
    w = World()
    w.add_bill(
        "3039",
        "Projekt ustawy o cudzoziemcach",
        stages=(Stage(stage_type="NewUpstreamStage", stage_name="Nowy etap"),),
    )

    report = w.run()

    assert report.published == 1
    assert w.card_id("3039")


def test_unknown_senate_decision_does_not_start_a_presidential_deadline() -> None:
    w = World()
    stage = Stage(
        stage_type="SenatePositionConsideration",
        stage_name="Rozpatrywanie",
        date=dt.date(2026, 9, 7),
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=(stage,))
    w.run()
    text = w.formatter.new_bill(w.bill("3039"), None).text

    assert "21" not in text
    assert "Процесс завершён" not in text
    w.set_stages("3039", (stage.model_copy(update={"decision": "odrzucono uchwałę Senatu"}),))
    w.clock.advance(days=1)

    report = w.run()

    assert report.updates == 1
    bill, change, _ = w.publisher.updates[0]
    phase = next_phase(bill, today=w.clock.now().date())
    assert phase is not None and phase.key == "president"
    assert "Сенат" in w.formatter.status_update(bill, change).text


def test_amendments_memo_survives_restore_and_does_not_charge_tokens_twice() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    document = TextDocument(url=REPORT_URL, kind="committee_amendments")
    w.gateway.files[REPORT_URL] = b"%PDF-report"
    analysis = w.container.analysis_service()
    first = analysis.summarize_amendments(w.bill("3039"), document)
    moved = document.model_copy(update={"url": REPORT_URL + "?mirror=1"})
    w.gateway.files[moved.url] = b"%PDF-report"
    w.repo.restore(w.repo.dump())
    analysis.start_run()

    again = analysis.summarize_amendments(w.bill("3039"), moved)

    assert first is not None and again is not None, "the report is a readable amendments PDF"
    assert first.amendments == again.amendments
    assert again.source_url == moved.url
    assert len(w.llm.amendment_contexts) == 1
    assert again.input_tokens == 0 and again.output_tokens == 0
    assert analysis.calls == [] and analysis.spent_usd == 0


def test_explicit_forced_analysis_bypasses_the_memo() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    analysis = w.container.analysis_service()
    analysis.start_run()

    cached = analysis.analyze_bill(w.bill("3039"))

    assert cached.record.input_tokens == 0 and len(w.llm.contexts) == 1
    analysis.analyze_bill(w.bill("3039"), ignore_cost_limit=True)
    assert len(w.llm.contexts) == 2


def test_checkpoint_savepoints_respect_the_outer_dry_run_rollback() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    before = w.bill("3039").observed_process
    w.set_stages("3039", COMMITTEE_STAGES)
    w.repo.begin()

    result = w.tracking.check_updates(publish=False)
    queued = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert result.changed == 1 and queued is not None
    assert queued.status is PublicationStatus.QUEUED
    w.repo.rollback()

    assert w.bill("3039").observed_process == before
    assert w.publication("3039", PublicationKind.STATUS_UPDATE) is None
    assert w.repo.get_status_change(1) is None


@pytest.mark.parametrize("workers", [1, 4])
def test_senate_position_arriving_late_is_news_even_with_the_same_stage_identity(
    workers: int,
) -> None:
    w = World(workers=workers)
    stage = Stage(stage_type="SenatePosition", stage_name="Stanowisko Senatu")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=(stage,))
    w.run()
    w.set_stages("3039", (stage.model_copy(update={"position": "nie wniósł poprawek"}),))
    w.clock.advance(days=1)

    report = w.run()
    again = w.run()

    assert (report.updates, again.updates) == (1, 0)
    bill, change, _ = w.publisher.updates[0]
    assert "без поправок" in w.formatter.status_update(bill, change).text.lower()
    assert len(w.llm.contexts) == 1


@pytest.mark.parametrize("status", [PublicationStatus.PENDING, PublicationStatus.UNKNOWN])
def test_ambiguous_delivery_never_reenters_the_queue(status: PublicationStatus) -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.set_stages("3039", COMMITTEE_STAGES)
    w.run(publish=False)
    row = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert row is not None and row.id is not None, "an unpublished run queues the status update"
    w.repo.mark_publication(row.id, status, count_attempt=False)

    w.run()

    assert w.publisher.updates == []
