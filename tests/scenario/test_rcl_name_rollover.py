import datetime as dt
from typing import Literal

import pytest

from lexinform.errors import BatchNotSubmittedError
from lexinform.models import BatchRequest, NameRecord, SejmTerm
from lexinform.services import analysis as analysis_module
from lexinform.services.analysis import Waiting
from tests.harness import RCL, World

Phase = Literal["open", "ready", "cleaned"]


def waiting_world(*, workers: int = 1, model: str = "gpt-5.1") -> World:
    world = World(
        batch=True,
        batch_only=True,
        name_cards=True,
        workers=workers,
        analysis_model=model,
        joint_model=model,
    )
    world.add_rcl_project()
    world.run()
    world.batch.resolve()
    world.run()
    assert world.bill(RCL).editorial.mnemonic is None
    assert [request.call_kind for request in world.batch.submitted] == ["analysis", "name"]
    assert world.repo.list_open_llm_batches()[-1].provider == (
        "openai" if model.startswith("gpt-") else "anthropic"
    )
    return world


def next_term(world: World) -> None:
    world.gateway.terms = [
        SejmTerm(num=10, start=dt.date(2023, 11, 13), end=dt.date(2027, 11, 12)),
        SejmTerm(num=11, start=dt.date(2027, 11, 13), current=True),
    ]
    world.clock.advance(days=1)


@pytest.mark.parametrize("phase", ["open", "ready", "cleaned"])
@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("model", ["gpt-5.1", "claude-opus-5-5"])
def test_rcl_name_keeps_its_original_paid_work_after_rollover(
    phase: Phase, workers: int, model: str
) -> None:
    world = waiting_world(workers=workers, model=model)
    request = world.batch.submitted[-1]
    if phase != "open":
        world.batch.resolve()
    if phase == "cleaned":
        world.analysis.collect_batches()
        world.analysis.acknowledge_batch_costs()
        world.clock.advance(days=200)
        world.repo.prune_history(before=world.clock.now(), memo_before=world.clock.now())
        assert not world.repo.load_analysis_memo()
    next_term(world)
    world.repo.restore(world.repo.dump())

    moved = world.run(term=None)

    assert moved.rehomed == 1
    assert sum(row.call_kind == "name" for row in world.batch.submitted) == 1
    if phase == "open":
        current = world.repo.get(11, RCL)
        assert current is not None and current.editorial.mnemonic is None
        world.repo.restore(world.repo.dump())
        world.batch.resolve()
        world.run(term=None)
    current = world.repo.get(11, RCL)
    assert current is not None and current.editorial.mnemonic is not None
    paid = next(
        row for row in world.repo.list_computation_results(10, RCL) if row.purpose == "name"
    )
    assert isinstance(paid.record, NameRecord), "the original owner retains the paid name"
    rows = [row for row in world.repo.list_result_applications(11, RCL) if row.target is not None]
    assert len(rows) == 1 and rows[0].result_id == paid.result_id
    assert rows[0].target is not None and rows[0].target.kind == "editorial"
    assert paid.custom_id == request.custom_id and paid.request_sha256 == request.payload_sha256
    assert not world.repo.list_computation_results(11, RCL)
    world.repo.restore(world.repo.dump())
    world.run(term=None)
    assert sum(row.call_kind == "name" for row in world.batch.submitted) == 1
    assert world.repo.get_computation_result(paid.result_id) == paid


def test_a_changed_rcl_name_context_does_not_reuse_an_old_pending_question() -> None:
    world = waiting_world()
    request = world.batch.submitted[-1]
    next_term(world)
    world.run(term=None, track=False)
    current = world.repo.get(11, RCL)
    assert current is not None, "rollover moved the RCL project"
    changed = current.model_copy(
        update={"summary": current.summary.model_copy(update={"title": "Inny projekt ustawy"})}
    )
    world.analysis.start_run()

    assert isinstance(world.analysis.name_bill(changed, may_wait=True), Waiting)
    world.analysis.submit_queued_batches()

    requests = [row for row in world.batch.submitted if row.call_kind == "name"]
    assert len(requests) == 2 and requests[-1].custom_id != request.custom_id
    assert requests[-1].payload_sha256 != request.payload_sha256


def test_matching_name_text_does_not_join_two_sejm_terms() -> None:
    world = World(batch=True, batch_only=True, name_cards=True)
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    world.batch.resolve()
    world.run()
    original = world.bill("3039")
    assert original.analysis is not None, "the original term has a completed analysis"
    summary = original.summary.model_copy(update={"term": 11})
    world.repo.upsert_summary(summary, now=world.clock.now())
    world.repo.save_analysis(11, "3039", original.analysis)
    current = world.repo.get(11, "3039")
    assert current is not None, "the second term has its own row"
    world.analysis.start_run()

    assert isinstance(world.analysis.name_bill(current, may_wait=True), Waiting)
    world.analysis.submit_queued_batches()

    requests = [row for row in world.batch.submitted if row.call_kind == "name"]
    assert len(requests) == 2 and requests[0].custom_id != requests[1].custom_id


@pytest.mark.parametrize("state", ["queued", "submitting"])
def test_original_rcl_name_intent_remains_the_only_submit_owner_after_rollover(
    state: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(batch=True, batch_only=True, name_cards=True)
    world.add_rcl_project()
    world.run()
    world.batch.resolve()

    def reject(requests: list[BatchRequest]) -> str:
        if state == "queued":
            raise BatchNotSubmittedError("injected definite refusal")
        raise RuntimeError("injected uncertain submission")

    with monkeypatch.context() as patch:
        patch.setattr(world.batch, "submit", reject)
        world.run(expect_bugs=True)

    intents = (
        world.repo.list_queued_batch_intents()
        if state == "queued"
        else world.repo.list_submitting_batch_intents()
    )
    assert len(intents) == 1 and intents[0].request.call_kind == "name"
    original = intents[0]
    world.repo.restore(world.repo.dump())
    next_term(world)

    world.run(term=None)
    world.run(term=None)

    names = [request for request in world.batch.submitted if request.call_kind == "name"]
    if state == "queued":
        assert len(names) == 1 and names[0] == original.request
        assert not world.repo.list_queued_batch_intents()
    else:
        assert not names
        assert world.repo.list_submitting_batch_intents() == [original]
    assert not world.repo.list_queued_batch_intents()


def test_legacy_name_memo_does_not_invent_an_application_ref_after_rollover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = waiting_world()
    key = world.batch.submitted[-1].custom_id
    save = world.repo.save_analysis_memo

    def legacy_memo(
        memo_key: str,
        raw: str,
        *,
        term: int | None = None,
        number: str | None = None,
        used_at: dt.datetime | None = None,
    ) -> None:
        if memo_key == key:
            record = NameRecord.model_validate_json(raw)
            raw = record.model_copy(update={"computation_id": None}).model_dump_json()
        save(memo_key, raw, term=term, number=number, used_at=used_at)

    world.batch.resolve()
    with monkeypatch.context() as patch:
        patch.setattr(world.repo, "save_analysis_memo", legacy_memo)
        world.analysis.collect_batches()
    world.analysis.acknowledge_batch_costs()
    assert (
        NameRecord.model_validate_json(world.repo.load_analysis_memo()[key]).computation_id is None
    )
    world.repo.restore(world.repo.dump())
    next_term(world)

    world.run(term=None)

    current = world.repo.get(11, RCL)
    assert current is not None and current.editorial.mnemonic is not None
    assert sum(request.call_kind == "name" for request in world.batch.submitted) == 1
    assert not [
        row for row in world.repo.list_result_applications(11, RCL) if row.target is not None
    ]


def failed_first_attempt() -> World:
    world = waiting_world()
    world.batch.secondary_script[(RCL, "name")] = RuntimeError("injected first name failure")
    world.batch.resolve()
    next_term(world)
    world.run(term=None)
    requests = [request for request in world.batch.submitted if request.call_kind == "name"]
    assert len(requests) == 2 and requests[0].custom_id == requests[1].custom_id
    assert requests[0].term == 10 and requests[1].term == 11
    return world


def test_successful_name_retry_under_the_new_owner_survives_memo_cleanup() -> None:
    world = failed_first_attempt()
    del world.batch.secondary_script[(RCL, "name")]
    world.batch.resolve()
    world.analysis.collect_batches()
    world.analysis.acknowledge_batch_costs()
    world.clock.advance(days=200)
    world.repo.prune_history(before=world.clock.now(), memo_before=world.clock.now())
    assert world.batch.submitted[-1].custom_id not in world.repo.load_analysis_memo()
    world.repo.restore(world.repo.dump())

    world.run(term=None)

    current = world.repo.get(11, RCL)
    assert current is not None and current.editorial.mnemonic is not None
    result = next(
        row for row in world.repo.list_computation_results(11, RCL) if row.purpose == "name"
    )
    assert result.validation == "validated"
    rows = [row for row in world.repo.list_result_applications(11, RCL) if row.target is not None]
    assert len(rows) == 1 and rows[0].result_id == result.result_id
    assert sum(request.call_kind == "name" for request in world.batch.submitted) == 2


def test_name_retry_budget_does_not_restart_when_the_rcl_owner_moves() -> None:
    world = failed_first_attempt()
    world.batch.resolve()
    world.repo.restore(world.repo.dump())

    world.run(term=None)
    world.repo.restore(world.repo.dump())
    world.run(term=None)

    assert sum(request.call_kind == "name" for request in world.batch.submitted) == 2
    assert not world.repo.list_queued_batch_intents()
    current = world.repo.get(11, RCL)
    assert current is not None and current.editorial.mnemonic is None
    assert not [
        row for row in world.repo.list_result_applications(11, RCL) if row.target is not None
    ]


@pytest.mark.parametrize("phase", ["open", "ready", "cleaned"])
def test_legacy_request_key_keeps_the_original_rcl_name_work(
    phase: Phase, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = analysis_module.text_digest

    def legacy_empty_digest(text: str) -> str:
        value = digest(text)
        return value.removeprefix("text-v2:") if not text else value

    with monkeypatch.context() as patch:
        patch.setattr(analysis_module, "text_digest", legacy_empty_digest)
        world = waiting_world()
    request = world.batch.submitted[-1]
    if phase != "open":
        world.batch.resolve()
    if phase == "cleaned":
        world.analysis.collect_batches()
        world.analysis.acknowledge_batch_costs()
        world.clock.advance(days=200)
        world.repo.prune_history(before=world.clock.now(), memo_before=world.clock.now())
        assert request.custom_id not in world.repo.load_analysis_memo()
    next_term(world)
    world.repo.restore(world.repo.dump())

    world.run(term=None)
    if phase == "open":
        world.batch.resolve()
        world.run(term=None)

    current = world.repo.get(11, RCL)
    assert current is not None and current.editorial.mnemonic is not None
    assert sum(row.call_kind == "name" for row in world.batch.submitted) == 1
    result = next(
        row for row in world.repo.list_computation_results(10, RCL) if row.purpose == "name"
    )
    assert result.custom_id == request.custom_id and result.request_sha256 == request.payload_sha256
