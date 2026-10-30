from collections.abc import Sequence
from typing import Literal

import pytest

from lexinform.errors import BatchNotSubmittedError
from lexinform.models import BatchRequest, NameRecord
from lexinform.services.analysis import Waiting
from tests.harness import WYKAZ, World
from tests.scenario.test_rcl_name_rollover import next_term


def primary_waiting(*, model: str = "gpt-5.1") -> World:
    world = World(profile="production", name_cards=True, analysis_model=model, joint_model=model)
    world.add_wykaz_entry()
    world.run()
    world.batch.resolve()
    return world


def naming_waiting(*, model: str = "gpt-5.1") -> World:
    world = primary_waiting(model=model)
    world.run()
    assert world.bill(WYKAZ).editorial.mnemonic is None
    assert [request.call_kind for request in world.batch.submitted] == ["analysis", "name"]
    return world


def test_queued_wykaz_name_keeps_original_request_without_preparing_again_after_rollover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = primary_waiting()
    prepared: list[BatchRequest] = []
    prepare = world.batch.prepare_request
    submit = world.batch.submit

    def observe_prepare(request: BatchRequest) -> BatchRequest:
        if request.call_kind == "name":
            prepared.append(request)
        return prepare(request)

    def refuse_name(requests: Sequence[BatchRequest]) -> str:
        if any(request.call_kind == "name" for request in requests):
            raise BatchNotSubmittedError("controlled refusal before name submission")
        return submit(requests)

    with monkeypatch.context() as patch:
        patch.setattr(world.batch, "prepare_request", observe_prepare)
        with patch.context() as refusal:
            refusal.setattr(world.batch, "submit", refuse_name)
            world.run(expect_bugs=True)
        queued = world.repo.list_queued_batch_intents()
        assert len(queued) == 1 and queued[0].request.call_kind == "name"
        original = queued[0]
        assert len(prepared) == 1
        world.repo.restore(world.repo.dump())
        next_term(world)

        report = world.run(term=None)

    assert report.rehomed == 1 and len(prepared) == 1
    names = [request for request in world.batch.submitted if request.call_kind == "name"]
    assert names == [original.request] and names[0].term == 10
    assert not world.repo.list_queued_batch_intents()
    assert world.repo.computation_owner_terms(WYKAZ, "name") == (10,)
    batch = next(batch for batch in world.repo.list_open_llm_batches() if batch.call_kind == "name")
    assert world.repo.list_llm_batch_items(batch.batch_id)[0].term == 10
    world.batch.resolve()
    world.run(term=None)
    current = world.repo.get(11, WYKAZ)
    assert current is not None and current.editorial.mnemonic is not None, (
        "rollover selects the name retained under the original Wykaz owner"
    )
    assert sum(request.call_kind == "name" for request in world.batch.submitted) == 1


@pytest.mark.parametrize("model", ["gpt-5.1", "claude-opus-5-5"])
def test_collected_wykaz_name_survives_cleanup_then_applies_to_new_owner_once(model: str) -> None:
    world = naming_waiting(model=model)
    request = world.batch.submitted[-1]
    world.batch.resolve()
    world.analysis.collect_batches()
    world.analysis.acknowledge_batch_costs()
    paid = next(
        result
        for result in world.repo.list_computation_results(10, WYKAZ)
        if result.purpose == "name"
    )
    assert isinstance(paid.record, NameRecord), "the original owner retained the collected name"
    original = paid.model_dump_json()
    assert paid.provider == ("openai" if model.startswith("gpt-") else "anthropic")
    assert world.bill(WYKAZ).editorial.mnemonic is None
    world.clock.advance(days=200)
    world.repo.prune_history(before=world.clock.now(), memo_before=world.clock.now())
    assert not world.repo.load_analysis_memo()
    world.repo.restore(world.repo.dump())
    next_term(world)

    report = world.run(term=None)

    assert report.rehomed == 1 and not report.llm_calls
    current = world.repo.get(11, WYKAZ)
    assert current is not None and current.editorial.mnemonic is not None, (
        "rollover selects the name retained under the original Wykaz owner"
    )
    assert current.editorial.mnemonic.computation_id == paid.result_id
    all_applications = world.repo.list_result_applications(11, WYKAZ)
    applications = [
        item
        for item in all_applications
        if item.target is not None and item.target.kind == "editorial"
    ]
    assert len(applications) == 1 and applications[0].result_id == paid.result_id
    assert applications[0].target is not None and applications[0].target.ref == "mnemonic"
    assert paid.custom_id == request.custom_id and paid.request_sha256 == request.payload_sha256
    assert not world.repo.list_computation_results(11, WYKAZ)
    assert world.repo.get_computation_result(paid.result_id) == paid
    assert sum(request.call_kind == "name" for request in world.batch.submitted) == 1
    world.repo.restore(world.repo.dump())
    repeated = world.run(term=None)
    saved = world.repo.get_computation_result(paid.result_id)
    assert saved is not None and saved.model_dump_json() == original
    assert world.repo.list_result_applications(11, WYKAZ) == all_applications
    assert not repeated.llm_calls
    assert sum(request.call_kind == "name" for request in world.batch.submitted) == 1


@pytest.mark.parametrize("field", ["title", "summary", "key_changes", "language"])
def test_changed_wykaz_name_context_does_not_borrow_old_term_work(
    field: Literal["title", "summary", "key_changes", "language"],
) -> None:
    world = naming_waiting()
    original = world.batch.submitted[-1]
    next_term(world)
    world.run(term=None, track=False)
    current = world.repo.get(11, WYKAZ)
    assert current is not None and current.analysis is not None, (
        "rollover carries the analyzed plan"
    )
    if field == "title":
        changed = current.model_copy(
            update={"summary": current.summary.model_copy(update={"title": "Inny projekt ustawy"})}
        )
    else:
        answer = current.analysis.analysis
        update: dict[str, object] = (
            {"summary": "A different practical rule"}
            if field == "summary"
            else {"key_changes": [*answer.key_changes, "A different rule"]}
            if field == "key_changes"
            else {"mnemonic_language": "en"}
        )
        changed = current.model_copy(
            update={
                "analysis": current.analysis.model_copy(
                    update={"analysis": answer.model_copy(update=update)}
                )
            }
        )
    world.analysis.start_run()

    assert isinstance(world.analysis.name_bill(changed, may_wait=True), Waiting)
    world.analysis.submit_queued_batches()

    names = [request for request in world.batch.submitted if request.call_kind == "name"]
    assert len(names) == 2 and names[-1].custom_id != original.custom_id
    assert names[-1].payload_sha256 != original.payload_sha256
