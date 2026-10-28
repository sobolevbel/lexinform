import pytest

from lexinform.models import BatchIntent, BillStatus, ComputationResult, TextDocument
from lexinform.services.analysis import AnalysisOutcome
from tests.fakes import FakeTextExtractor
from tests.harness import World


def primary_world(
    *, model: str, workers: int, legacy: bool = False
) -> tuple[World, ComputationResult]:
    world = World(profile="production", workers=workers, analysis_model=model)
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    save = world.repo.save_batch_intent

    def old_intent(intent: BatchIntent) -> None:
        save(
            intent.model_copy(
                update={"meta": intent.meta.model_copy(update={"input_binding": None})}
            )
        )

    with pytest.MonkeyPatch.context() as patch:
        if legacy:
            patch.setattr(world.repo, "save_batch_intent", old_intent)
        world.run(track=False)
    world.batch.resolve()
    world.run(track=False)
    paid = world.repo.list_computation_results(10, "3039", purpose="analysis")[0]
    world.clock.advance(days=200)
    world.repo.prune_history(before=world.clock.now(), memo_before=world.clock.now())
    world.repo.restore(world.repo.dump())
    assert not world.repo.load_analysis_memo()
    world.analysis.start_run()
    return world, paid


@pytest.mark.parametrize("model", ["gpt-5.1", "claude-opus-5-5"])
@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("path", ["direct", "pending"])
def test_primary_history_after_cleanup(model: str, workers: int, path: str) -> None:
    world, paid = primary_world(model=model, workers=workers)
    world.repo.reset_bill(10, "3039", BillStatus.ANALYSIS_PENDING)
    before = world.bill("3039")
    applications = world.repo.list_result_applications(10, "3039")
    submitted = list(world.batch.submitted)

    if path == "direct":
        answer = world.analysis.analyze_bill(world.bill("3039"))
        assert isinstance(answer, AnalysisOutcome)
        assert answer.record.computation_id == paid.result_id
    else:
        result = world.analysis.analyze_pending(limit=10)
        assert result.analyzed == 1 and result.batched == 0
    assert world.batch.submitted == submitted
    assert not world.repo.list_queued_batch_intents()
    assert world.repo.get_computation_result(paid.result_id) == paid
    record = world.bill("3039").analysis
    assert record is not None, "exact history reuse applies an analysis to the requested generation"
    assert record.computation_id == paid.result_id
    assert record.request_sha256 == paid.request_sha256
    assert record.input_tokens == record.output_tokens == 0
    receipt = world.repo.list_result_applications(10, "3039")
    assert receipt[:-1] == applications
    assert receipt[-1].result_id == paid.result_id
    assert receipt[-1].generation == before.analysis_generation
    assert receipt[-1].reason == "memo_reuse"


@pytest.mark.parametrize("model", ["gpt-5.1", "claude-opus-5-5"])
@pytest.mark.parametrize("legacy", [False, True])
def test_primary_history_does_not_guess_an_unbound_or_changed_question(
    model: str, legacy: bool
) -> None:
    world, paid = primary_world(model=model, workers=4, legacy=legacy)
    world.repo.reset_bill(10, "3039", BillStatus.ANALYSIS_PENDING)
    if not legacy:
        world.gateway.details["3039"] = world.gateway.details["3039"].model_copy(
            update={"title": "Inny projekt ustawy o cudzoziemcach"}
        )
    applications = world.repo.list_result_applications(10, "3039")

    result = world.analysis.analyze_pending(limit=10)

    assert result.batched == 1 and result.analyzed == 0
    assert world.repo.list_queued_batch_intents()
    assert world.repo.list_result_applications(10, "3039") == applications
    assert world.repo.get_computation_result(paid.result_id) == paid


@pytest.mark.parametrize("model", ["gpt-5.1", "claude-opus-5-5"])
def test_explicit_force_keeps_its_existing_cache_bypass(model: str) -> None:
    world, paid = primary_world(model=model, workers=4)
    submitted = len(world.batch.submitted)

    answer = world.analysis.analyze_bill(world.bill("3039"), ignore_cost_limit=True)

    assert not isinstance(answer, AnalysisOutcome)
    assert len(world.batch.submitted) == submitted + 1
    assert world.repo.get_computation_result(paid.result_id) == paid


@pytest.mark.parametrize("model", ["gpt-5.1", "claude-opus-5-5"])
@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("persist", [False, True])
def test_reanalysis_reuses_exact_paid_input_after_a_generation_reset(
    model: str, workers: int, persist: bool
) -> None:
    old = "Art. 1. Cudzoziemiec posiadający zezwolenie na pobyt otrzymuje 200 zł. " * 40
    new = old.replace("200 zł", "300 zł")
    world = World(
        profile="production",
        workers=workers,
        analysis_model=model,
        extractor=FakeTextExtractor(by_content={b"%PDF": old, b"%PDF-new": new}),
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    world.batch.resolve()
    world.run(track=False)
    baseline = world.bill("3039").analysis
    url = "https://api.test/reanalysis-history.pdf"
    world.gateway.files[url] = b"%PDF-new"
    document = TextDocument(url=url, kind="print")
    world.analysis.start_run()
    assert world.analysis.reanalyze_bill(world.bill("3039"), document) is None
    world.analysis.submit_queued_batches()
    world.repo.reset_bill(10, "3039", BillStatus.ANALYZED)
    world.batch.resolve()
    world.analysis.collect_batches()
    world.analysis.acknowledge_batch_costs()
    paid = world.repo.list_computation_results(10, "3039", purpose="reanalysis")[0]
    assert world.bill("3039").analysis == baseline
    assert world.repo.list_result_applications(10, "3039")[-1].outcome == "superseded"
    world.clock.advance(days=200)
    world.repo.prune_history(before=world.clock.now(), memo_before=world.clock.now())
    world.repo.restore(world.repo.dump())
    assert not world.repo.load_analysis_memo()
    world.analysis.start_run()
    before = world.bill("3039")
    applications = world.repo.list_result_applications(10, "3039")
    submitted = list(world.batch.submitted)

    if persist:
        record = world.analysis.reanalyze_bill(before, document)
    else:
        fresh, changed = world.analysis.prepare_reanalysis(before, document)
        assert changed and world.bill("3039") == before
        record = fresh.analysis
    assert record is not None, "the exact paid reanalysis is available for this input"
    assert record.computation_id == paid.result_id
    assert record.request_sha256 == paid.request_sha256
    assert record.input_tokens == record.output_tokens == 0
    assert world.batch.submitted == submitted
    assert not world.repo.list_queued_batch_intents()
    assert world.repo.get_computation_result(paid.result_id) == paid
    receipts = world.repo.list_result_applications(10, "3039")
    if persist:
        assert receipts[:-1] == applications
        assert receipts[-1].result_id == paid.result_id and receipts[-1].reason == "memo_reuse"
        assert receipts[-1].generation == before.analysis_generation
    else:
        assert receipts == applications
