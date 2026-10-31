import pytest

from lexinform.models import BillStatus, ResultApplication, TextDocument
from lexinform.services.analysis import AnalysisOutcome
from lexinform.services.analysis_applications import apply_analysis, record_analysis_application
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
    selected = world.bill("3039")
    assert applied.result_id == outcome.record.computation_id
    assert applied.reason == "batch_result" and applied.generation == before.analysis_generation
    assert applied.observed_generation == before.analysis_generation
    assert applied.projected_generation == selected.analysis_generation
    assert applied.projected_generation != applied.generation
    assert applications[0].projected_generation is None
    assert applied.observed_status is BillStatus.ANALYSIS_READY
    assert selected.analysis == outcome.record
    assert not world.publisher.new_bills and len(world.batch.submitted) == 1
    before_quiet = world.repo.dump()
    apply_analysis(world.repo, 10, "3039", outcome.record, now=world.clock.now())
    assert world.repo.dump() == before_quiet


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
    assert applications[-1].projected_generation == world.bill("3039").analysis_generation
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


@pytest.mark.parametrize("legacy_none", [False, True])
def test_known_projection_key_is_idempotent_and_never_enriches_a_legacy_none(
    legacy_none: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = collected_world(workers=1)
    before = world.bill("3039")
    save = world.repo.save_result_application

    def persist_legacy(application: ResultApplication) -> None:
        save(application.model_copy(update={"projected_generation": None}))

    with monkeypatch.context() as patch:
        if legacy_none:
            patch.setattr(world.repo, "save_result_application", persist_legacy)
        outcome = world.analysis.analyze_bill(before)
    assert isinstance(outcome, AnalysisOutcome), "the public ready application projects its answer"
    selected = world.bill("3039")
    original = world.repo.list_result_applications(10, "3039")[1]
    assert original.projected_generation == (None if legacy_none else selected.analysis_generation)
    raw = original.model_dump_json()
    world.clock.advance(hours=2)

    def no_history(*args: object, **kwargs: object) -> None:
        raise AssertionError("a known projection key uses its exact indexed lookup")

    with monkeypatch.context() as patch:
        patch.setattr(world.repo, "list_result_applications", no_history)
        for projected in (selected.analysis_generation, None):
            with world.repo.atomic():
                record_analysis_application(
                    world.repo,
                    before,
                    outcome.record,
                    now=world.clock.now(),
                    reason="batch_result",
                    projected_generation=projected,
                )
    applications = world.repo.list_result_applications(10, "3039")
    assert len(applications) == 2 and applications[1].model_dump_json() == raw
    assert world.bill("3039") == selected
    assert len(world.batch.submitted) == 1


def test_conflicting_known_projected_witness_rolls_back_the_real_ready_application() -> None:
    world = collected_world(workers=1)
    before = world.bill("3039")
    ready = before.ready_analysis
    assert ready is not None and ready.record.computation_id is not None, (
        "collection leaves the actual paid primary answer ready for projection"
    )
    conflicting = ResultApplication(
        result_id=ready.record.computation_id,
        term=10,
        number="3039",
        generation=before.analysis_generation,
        projected_generation=9999,
        phase="projection",
        outcome="applied",
        reason="batch_result",
        observed_generation=before.analysis_generation,
        observed_status=before.status,
        created_at=world.clock.now(),
    )
    world.repo.save_result_application(conflicting)
    baseline = world.repo.dump()

    with pytest.raises(ValueError, match="immutable result application"):
        world.analysis.analyze_bill(before)

    assert world.repo.dump() == baseline and world.bill("3039") == before
    assert world.repo.list_result_applications(10, "3039")[-1] == conflicting
    assert len(world.batch.submitted) == 1
