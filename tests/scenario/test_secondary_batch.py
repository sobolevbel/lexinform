"""Secondary requests keep their durable identity independently of bill analysis state."""

from datetime import timedelta

import pytest

from lexinform.models import JointRecord
from lexinform.services.analysis import Waiting
from tests.harness import World


def ready_world() -> World:
    w = World(batch=True, batch_kinds=frozenset({"joint"}))
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o obywatelstwie")
    w.run()
    return w


def ask(w: World, *, may_wait: bool = True) -> JointRecord | Waiting | None:
    return w.analysis.compare_joint(w.bill("3040"), [w.bill("3039")], may_wait=may_wait)


def test_secondary_request_is_deduplicated_collected_and_reused_after_restore() -> None:
    w = ready_world()
    before = w.bill("3040").status
    first = ask(w)
    assert isinstance(first, Waiting)
    assert ask(w) == first
    assert len(w.repo.list_queued_batch_intents()) == 1
    w.analysis.submit_queued_batches()
    assert ask(w) == first
    w.batch.resolve()
    w.analysis.collect_batches()
    w.repo.restore(w.repo.dump())
    w.analysis.start_run()

    answer = ask(w)

    assert isinstance(answer, JointRecord)
    assert answer.input_tokens == 0
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
