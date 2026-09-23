"""Joint comparisons delay the reply without allocating a publication or retry attempt."""

import pytest

from lexinform.models import PublicationKind
from tests.fakes import make_comparison
from tests.harness import World


def joint_world(*, workers: int = 1) -> World:
    w = World(batch=True, batch_kinds=frozenset({"joint"}), workers=workers)
    w.add_bill("1933", "Poselski projekt ustawy o cudzoziemcach")
    w.run()
    w.add_bill("1929", "Rządowy projekt ustawy o cudzoziemcach")
    w.touch("1929", w.clock.now(), prints_considered_jointly=("1933",))
    return w


@pytest.mark.parametrize("workers", [1, 4])
def test_joint_reply_waits_then_posts_once_with_comparison(workers: int) -> None:
    w = joint_world(workers=workers)

    waiting = w.run()
    assert waiting.batch_waiting == 1 and not waiting.errors
    assert not w.publisher.joint_bills
    assert w.publication("1929", PublicationKind.JOINT_BILL) is None
    w.batch.resolve()
    posted = w.run()
    repeated = w.run()

    assert (posted.joint_published, repeated.joint_published) == (1, 0)
    assert not w.llm.joint_contexts
    bill = w.publisher.joint_bills[0][0]
    assert bill.joint is not None and bill.joint.comparison == make_comparison()


@pytest.mark.parametrize("failed", [False, True])
def test_joint_fallback_posts_once_even_when_a_late_batch_arrives(failed: bool) -> None:
    w = joint_world()
    w.run()
    if failed:
        w.batch.secondary_script[("1929", "joint")] = RuntimeError("invalid")
        w.batch.resolve()
    else:
        w.clock.advance(hours=7)

    posted = w.run()
    w.batch.resolve()
    repeated = w.run()

    assert (posted.joint_published, repeated.joint_published) == (1, 0)
    assert len(w.llm.joint_contexts) == 1


def test_manual_joint_republish_does_not_wait() -> None:
    w = joint_world()
    w.run()

    w.command("/republish 1929")
    w.run()

    assert len(w.llm.joint_contexts) == 1
    assert len(w.publisher.joint_bills) == 1


def test_joint_dry_run_calls_synchronously_and_submits_nothing() -> None:
    w = joint_world()

    report = w.run(dry_run=True)

    assert report.joint_published == 1
    assert len(w.llm.joint_contexts) == 1
    assert not w.batch.submitted
