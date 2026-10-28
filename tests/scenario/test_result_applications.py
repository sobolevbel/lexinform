import pytest

from lexinform.models import BillStatus, TextDocument
from lexinform.services.analysis import AnalysisOutcome
from tests.fakes import FakeTextExtractor
from tests.harness import World


def collected_world(*, workers: int) -> World:
    world = World(profile="production", workers=workers)
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    world.batch.resolve()
    world.analysis.collect_batches()
    return world


@pytest.mark.parametrize("workers", [1, 4])
def test_collection_and_projection_have_separate_receipts(workers: int) -> None:
    world = collected_world(workers=workers)
    before = world.bill("3039")
    assert before.status is BillStatus.ANALYSIS_READY and before.analysis is None
    assert [row.phase for row in world.repo.list_result_applications(10, "3039")] == ["collection"]

    outcome = world.analysis.analyze_bill(before)

    assert isinstance(outcome, AnalysisOutcome)
    applications = world.repo.list_result_applications(10, "3039")
    assert [(row.phase, row.outcome) for row in applications] == [
        ("collection", "ready"),
        ("projection", "applied"),
    ]
    applied = applications[1]
    assert applied.result_id == outcome.record.computation_id
    assert applied.reason == "batch_result" and applied.generation == before.analysis_generation
    assert applied.observed_status is BillStatus.ANALYSIS_READY
    assert world.bill("3039").analysis == outcome.record
    assert not world.publisher.new_bills and len(world.batch.submitted) == 1


@pytest.mark.parametrize("workers", [1, 4])
def test_projection_receipt_failure_keeps_the_ready_result_retryable(
    workers: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = collected_world(workers=workers)
    before = world.bill("3039")

    def crash(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected projection receipt failure")

    with monkeypatch.context() as patch:
        patch.setattr(world.repo, "save_result_application", crash)
        with pytest.raises(RuntimeError, match="projection receipt failure"):
            world.analysis.analyze_bill(before)
    assert world.bill("3039") == before
    assert [row.phase for row in world.repo.list_result_applications(10, "3039")] == ["collection"]
    world.clock.advance(hours=1)

    outcome = world.analysis.analyze_bill(world.bill("3039"))

    assert isinstance(outcome, AnalysisOutcome)
    assert world.bill("3039").status is BillStatus.ANALYZED
    assert len(world.repo.list_computation_results(10, "3039")) == 1
    assert len(world.repo.list_result_applications(10, "3039")) == 2
    assert len(world.batch.submitted) == 1


def test_reuse_has_a_new_application_without_a_new_paid_result() -> None:
    world = collected_world(workers=1)
    world.analysis.analyze_bill(world.bill("3039"))
    original = world.repo.list_computation_results(10, "3039")[0]
    first = world.repo.list_result_applications(10, "3039")[1]
    world.clock.advance(hours=2)
    world.repo.reset_bill(10, "3039", BillStatus.ANALYSIS_PENDING)

    outcome = world.analysis.analyze_bill(world.bill("3039"))

    assert (
        isinstance(outcome, AnalysisOutcome) and outcome.record.computation_id == original.result_id
    )
    applications = world.repo.list_result_applications(10, "3039")
    assert len(applications) == 3 and applications[-1].reason == "memo_reuse"
    assert applications[-1].generation != first.generation
    assert world.repo.get_computation_result(original.result_id) == original
    assert len(world.batch.submitted) == 1 and not world.llm.contexts
    world.repo.save_result_application(first.model_copy(update={"created_at": world.clock.now()}))
    assert world.repo.list_result_applications(10, "3039")[1] == first


def test_reanalysis_preparation_and_quiet_checks_do_not_claim_new_applications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World(
        profile="production",
        extractor=FakeTextExtractor(by_content={b"new": "Nowy tekst ustawy. " * 100}),
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    world.batch.resolve()
    world.run(track=False)
    world.gateway.files["https://api.test/new.pdf"] = b"new"
    document = TextDocument(url="https://api.test/new.pdf", kind="print")
    assert world.analysis.reanalyze_bill(world.bill("3039"), document) is None
    world.analysis.submit_queued_batches()
    world.batch.resolve()
    world.analysis.collect_batches()
    before = world.bill("3039")
    applications = world.repo.list_result_applications(10, "3039")

    fresh, changed = world.analysis.prepare_reanalysis(before, document)

    assert changed and fresh.analysis != before.analysis
    assert world.repo.list_result_applications(10, "3039") == applications
    assert world.bill("3039") == before

    def crash(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected reanalysis receipt failure")

    with monkeypatch.context() as patch:
        patch.setattr(world.repo, "save_result_application", crash)
        with pytest.raises(RuntimeError, match="reanalysis receipt failure"):
            world.analysis.reanalyze_bill(before, document)
    assert world.bill("3039") == before
    assert world.repo.list_result_applications(10, "3039") == applications

    record = world.analysis.reanalyze_bill(world.bill("3039"), document)
    assert record is not None and record.revision == 2
    applied = world.repo.list_result_applications(10, "3039")
    assert len(applied) == 4 and applied[-1].result_id == record.computation_id
    assert applied[-1].phase == "projection" and applied[-1].reason == "batch_result"
    world.clock.advance(hours=1)
    assert world.analysis.reanalyze_bill(world.bill("3039"), document) is None
    assert world.repo.list_result_applications(10, "3039") == applied
    assert len(world.repo.list_computation_results(10, "3039")) == len(world.batch.submitted) == 2


def test_synchronous_legacy_analysis_has_no_invented_application() -> None:
    world = World()
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    world.run(track=False)

    record = world.bill("3039").analysis
    assert record is not None and record.computation_id is None
    assert not world.repo.list_computation_results(10, "3039")
    assert not world.repo.list_result_applications(10, "3039")
