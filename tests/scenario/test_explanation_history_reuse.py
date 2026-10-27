import threading

import pytest

from lexinform.models import AnalysisRecord, BatchIntent, ComputationResult
from lexinform.models.batch import BatchKind
from lexinform.services.analysis import Waiting
from tests.harness import World


def explained_world(
    *, model: str, workers: int, legacy: bool = False
) -> tuple[World, ComputationResult]:
    world = World(profile="production", workers=workers, analysis_model=model)
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    world.batch.resolve()
    world.run(track=False)
    world.analysis.start_run()
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
        assert isinstance(world.analysis.explain_bill(world.bill("3039")), Waiting)
    world.batch.resolve()
    world.analysis.collect_batches()
    world.analysis.acknowledge_batch_costs()
    result = next(
        row
        for row in world.repo.list_computation_results(10, "3039")
        if row.purpose == "explanation"
    )
    assert (result.input_binding is None) == legacy
    world.clock.advance(days=200)
    world.repo.prune_history(before=world.clock.now(), memo_before=world.clock.now())
    world.repo.restore(world.repo.dump())
    assert not world.repo.load_analysis_memo()
    world.analysis.start_run()
    return world, result


@pytest.mark.parametrize("model", ["gpt-5.1", "claude-opus-5-5"])
@pytest.mark.parametrize("workers", [1, 4])
def test_explanation_reuses_its_recorded_input_after_memo_cleanup(model: str, workers: int) -> None:
    world, result = explained_world(model=model, workers=workers)
    before = world.bill("3039")
    applications = world.repo.list_result_applications(10, "3039")
    requests = list(world.batch.submitted)

    reused = world.analysis.explain_bill(before)

    assert isinstance(reused, AnalysisRecord)
    assert reused.computation_id == result.result_id
    assert reused.request_sha256 == result.request_sha256
    assert reused.input_tokens == reused.output_tokens == 0
    assert world.batch.submitted == requests
    assert not world.repo.list_queued_batch_intents()
    assert world.bill("3039") == before
    assert world.repo.list_result_applications(10, "3039") == applications
    assert world.repo.get_computation_result(result.result_id) == result


@pytest.mark.parametrize("model", ["gpt-5.1", "claude-opus-5-5"])
def test_an_unbound_legacy_explanation_does_not_get_a_guessed_question_scope(model: str) -> None:
    world, result = explained_world(model=model, workers=4, legacy=True)
    requests = list(world.batch.submitted)

    with pytest.raises(RuntimeError, match="batch"):
        world.analysis.explain_bill(world.bill("3039"))

    assert world.batch.submitted == requests
    assert world.repo.get_computation_result(result.result_id) == result
    assert not world.repo.list_queued_batch_intents()


@pytest.mark.parametrize("model", ["gpt-5.1", "claude-opus-5-5"])
def test_a_changed_explanation_question_does_not_reuse_the_old_paid_result(model: str) -> None:
    world, result = explained_world(model=model, workers=4)
    requests = len(world.batch.submitted)
    world.gateway.details["3039"] = world.gateway.details["3039"].model_copy(
        update={"title": "Inny projekt ustawy o cudzoziemcach"}
    )

    answer = world.analysis.explain_bill(world.bill("3039"))

    assert isinstance(answer, Waiting)
    assert len(world.batch.submitted) == requests + 1
    assert world.batch.submitted[-1].custom_id != result.custom_id
    assert world.repo.get_computation_result(result.result_id) == result


def test_history_is_read_on_the_caller_and_never_by_primary_prepare_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World(profile="production", workers=4)
    owner = threading.get_ident()
    calls: list[BatchKind | None] = []
    read = world.repo.list_computation_results

    def guarded_read(
        term: int, number: str, *, purpose: BatchKind | None = None
    ) -> list[ComputationResult]:
        assert threading.get_ident() == owner, "repository history belongs to the calling thread"
        calls.append(purpose)
        return read(term, number, purpose=purpose)

    monkeypatch.setattr(world.repo, "list_computation_results", guarded_read)
    for number in ("3039", "3040"):
        world.add_bill(number, "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    world.batch.resolve()
    world.run(track=False)

    assert isinstance(world.analysis.explain_bill(world.bill("3039")), Waiting)
    assert calls == ["analysis", "analysis", "analysis", "analysis", "explanation"]
