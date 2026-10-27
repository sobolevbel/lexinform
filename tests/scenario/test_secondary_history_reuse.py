from typing import Literal

import pytest

from lexinform.models import (
    AmendmentsRecord,
    BatchRequest,
    ComputationResult,
    JointRecord,
    NameRecord,
    SupplementRecord,
)
from lexinform.services.analysis import Waiting
from tests.harness import World
from tests.scenario.test_secondary_computation_history import ask, prepared_world

Kind = Literal["amendments", "supplement", "joint", "name"]
KINDS: tuple[Kind, ...] = ("amendments", "supplement", "joint", "name")


def cleaned_world(kind: Kind, *, workers: int = 1) -> tuple[World, ComputationResult]:
    world = prepared_world(workers=workers)
    assert isinstance(ask(world, kind), Waiting)
    world.analysis.submit_queued_batches()
    world.batch.resolve()
    world.analysis.collect_batches()
    world.analysis.acknowledge_batch_costs()
    result = next(
        row for row in world.repo.list_computation_results(10, "3039") if row.purpose == kind
    )
    world.clock.advance(days=200)
    world.repo.prune_history(before=world.clock.now(), memo_before=world.clock.now())
    world.repo.restore(world.repo.dump())
    assert not world.repo.load_analysis_memo()
    world.analysis.start_run()
    return world, result


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("workers", [1, 4])
def test_exact_secondary_question_reuses_paid_history_after_memo_cleanup(
    kind: Kind, workers: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, result = cleaned_world(kind, workers=workers)
    before = world.bill("3039")
    requests = len(world.batch.submitted)

    def forbid_preparation(request: BatchRequest) -> BatchRequest:
        raise AssertionError("a retained exact answer needs no new provider request")

    monkeypatch.setattr(world.batch, "prepare_request", forbid_preparation)

    reused = ask(world, kind)

    assert isinstance(reused, (AmendmentsRecord, SupplementRecord, JointRecord, NameRecord))
    assert reused.computation_id == result.result_id
    assert reused.request_sha256 == result.request_sha256
    assert reused.input_tokens == reused.output_tokens == 0
    assert world.repo.get_computation_result(result.result_id) == result
    assert len(world.batch.submitted) == requests
    assert not world.repo.list_queued_batch_intents()
    after = world.bill("3039")
    assert (after.status, after.analysis_generation) == (before.status, before.analysis_generation)

    world.clock.advance(days=200)
    world.repo.prune_history(before=world.clock.now(), memo_before=world.clock.now())
    world.repo.restore(world.repo.dump())
    world.analysis.start_run()
    repeated = ask(world, kind)
    assert isinstance(repeated, type(reused))
    assert repeated.computation_id == result.result_id
    assert len(world.batch.submitted) == requests


@pytest.mark.parametrize("kind", KINDS)
def test_a_changed_secondary_question_does_not_reuse_an_old_answer(kind: Kind) -> None:
    world, result = cleaned_world(kind)
    bill = world.bill("3039")
    changed = bill.model_copy(
        update={"summary": bill.summary.model_copy(update={"title": "Inny projekt ustawy"})}
    )

    answer = ask(world, kind, bill=changed)

    assert isinstance(answer, Waiting)
    intents = world.repo.list_queued_batch_intents()
    assert len(intents) == 1 and intents[0].request.custom_id != result.custom_id
    assert world.repo.get_computation_result(result.result_id) == result


@pytest.mark.parametrize("kind", KINDS)
def test_matching_secondary_text_does_not_cross_sejm_terms(kind: Kind) -> None:
    world, result = cleaned_world(kind)
    bill = world.bill("3039")
    current = bill.model_copy(update={"summary": bill.summary.model_copy(update={"term": 11})})
    world.repo.upsert_summary(current.summary, now=world.clock.now())

    answer = ask(world, kind, bill=current)

    assert isinstance(answer, Waiting)
    intents = world.repo.list_queued_batch_intents()
    assert len(intents) == 1 and intents[0].request.term == 11
    assert intents[0].request.custom_id != result.custom_id
    assert world.repo.get_computation_result(result.result_id) == result


@pytest.mark.parametrize("kind", KINDS)
def test_an_existing_legacy_memo_keeps_its_unknown_result_ref(kind: Kind) -> None:
    world, result = cleaned_world(kind)
    assert result.record is not None, "the successful fixture retained its normalized answer"
    legacy = result.record.model_copy(update={"computation_id": None, "request_sha256": None})
    world.repo.save_analysis_memo(
        result.custom_id,
        legacy.model_dump_json(),
        term=10,
        number="3039",
        used_at=world.clock.now(),
    )
    world.repo.restore(world.repo.dump())
    world.analysis.start_run()

    answer = ask(world, kind)

    assert isinstance(answer, (AmendmentsRecord, SupplementRecord, JointRecord, NameRecord))
    assert answer.computation_id is None and answer.request_sha256 is None
    assert not world.repo.list_queued_batch_intents()
    assert world.repo.get_computation_result(result.result_id) == result
