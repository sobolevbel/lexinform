"""Secondary requests keep their durable identity independently of bill analysis state."""

from datetime import timedelta

import pytest

from lexinform.models import JointRecord
from lexinform.services.analysis import Waiting
from tests import fakes
from tests.harness import World


def ready_world() -> World:
    w = World(batch=True, batch_kinds=frozenset({"joint"}))
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o obywatelstwie")
    w.run()
    return w


def ask(w: World, *, may_wait: bool = True) -> JointRecord | Waiting | None:
    return w.analysis.compare_joint(w.bill("3040"), [w.bill("3039")], may_wait=may_wait)


def test_secondary_request_is_deduplicated_collected_and_reused_after_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = ready_world()
    before = w.bill("3040").status
    first = ask(w)
    assert isinstance(first, Waiting)
    assert ask(w) == first
    assert len(w.repo.list_queued_batch_intents()) == 1
    w.analysis.submit_queued_batches()
    submitted_version = w.batch.submitted[0].prompt_version
    monkeypatch.setattr(fakes, "PROMPT_VERSION", "newer-than-submitted")
    assert ask(w) == first
    w.batch.resolve()
    w.analysis.collect_batches()
    w.repo.restore(w.repo.dump())
    w.analysis.start_run()

    answer = ask(w)

    assert isinstance(answer, JointRecord)
    assert answer.input_tokens == 0
    assert answer.prompt_version == submitted_version
    assert not w.llm.joint_contexts
    assert w.bill("3040").status == before
    assert len(w.batch.submitted) == 1


@pytest.mark.parametrize("submit", [False, True])
def test_secondary_timeout_includes_time_before_remote_submission(submit: bool) -> None:
    w = ready_world()
    assert isinstance(ask(w), Waiting)
    if submit:
        w.analysis.submit_queued_batches()
    w.clock.advance(hours=7)

    assert isinstance(ask(w), JointRecord)
    assert len(w.llm.joint_contexts) == 1
    assert isinstance(ask(w), JointRecord)
    assert len(w.llm.joint_contexts) == 1
    if not submit:
        assert not w.repo.list_queued_batch_intents()
        w.analysis.submit_queued_batches()
        assert not w.batch.submitted


def test_failed_secondary_item_falls_back_to_synchronous_call() -> None:
    w = ready_world()
    ask(w)
    w.batch.secondary_script[("3040", "joint")] = RuntimeError("refused")
    w.analysis.submit_queued_batches()
    w.batch.resolve()
    w.analysis.collect_batches()

    assert isinstance(ask(w), JointRecord)
    assert len(w.llm.joint_contexts) == 1


@pytest.mark.parametrize("dry_run", [False, True])
def test_manual_and_dry_run_secondary_calls_never_wait(dry_run: bool) -> None:
    w = ready_world()
    w.analysis.start_run(dry_run=dry_run)

    assert isinstance(ask(w, may_wait=dry_run), JointRecord)
    assert not w.repo.list_queued_batch_intents()


def test_deferred_bill_survives_watermark_filter_and_restore() -> None:
    w = ready_world()
    since = w.clock.now()
    w.repo.set_awaiting_batch(10, "3039", since)
    w.repo.restore(w.repo.dump())
    found = w.repo.list_tracked(
        w.container.channel_id(),
        closed_grace_days=30,
        pending_decision_max_days=365,
        now=since,
        changed_since=since + timedelta(days=1),
    )
    assert [bill.number for bill in found] == ["3039"]
    assert found[0].awaiting_batch_since == since


def test_uncertain_intent_is_retained_after_synchronous_fallback() -> None:
    w = ready_world()
    ask(w)
    intent = w.repo.list_queued_batch_intents()[0]
    w.repo.mark_batch_intents_submitting([intent.request.custom_id])
    w.clock.advance(hours=7)

    assert isinstance(ask(w), JointRecord)
    assert len(w.repo.list_submitting_batch_intents()) == 1
    w.repo.mark_batch_intents_queued([intent.request.custom_id])
    w.analysis.submit_queued_batches()
    assert not w.batch.submitted


def test_secondary_reservation_over_budget_waits_without_an_intent() -> None:
    w = World(batch=True, batch_kinds=frozenset({"joint"}), max_run_cost_usd=0.05)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o obywatelstwie")
    w.run()
    w.analysis.start_run()

    assert isinstance(ask(w), Waiting)
    assert not w.repo.list_queued_batch_intents()
    assert not w.llm.joint_contexts
    assert w.analysis.stopped is not None


def test_submission_never_mixes_models_in_one_batch() -> None:
    w = ready_world()
    ask(w)
    original = w.repo.list_queued_batch_intents()[0]
    for suffix, model in [("b", "claude-sonnet-5"), ("c", "claude-opus-5")]:
        request = original.request.model_copy(update={"custom_id": suffix, "model": model})
        w.repo.save_batch_intent(original.model_copy(update={"request": request}))

    w.analysis.submit_queued_batches()

    assert len(w.batch.submitted) == 3
    assert all(len({req.model for req in requests}) == 1 for requests in w.batch.batches.values())
