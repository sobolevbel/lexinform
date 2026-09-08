"""fan_out: parallel steps, sequential consumption."""

from __future__ import annotations

import threading
import time

import pytest

from lexinform.concurrency import Outcome, fan_out


def test_outcomes_keep_input_order_and_carry_per_item_errors() -> None:
    def step(n: int) -> int:
        time.sleep(0.01 * (5 - n))  # later items finish first
        if n == 2:
            raise ValueError("boom")
        return n * 10

    outcomes = list(fan_out([1, 2, 3, 4], step, workers=4))
    assert [o.item for o in outcomes] == [1, 2, 3, 4]
    assert [o.value for o in outcomes] == [10, None, 30, 40]
    assert isinstance(outcomes[1].error, ValueError)
    with pytest.raises(ValueError, match="boom"):
        outcomes[1].result()
    assert outcomes[0].result() == 10


def test_single_worker_runs_in_the_calling_thread() -> None:
    threads: set[str] = set()

    def step(n: int) -> int:
        threads.add(threading.current_thread().name)
        return n

    assert [o.value for o in fan_out([1, 2, 3], step, workers=1)] == [1, 2, 3]
    assert threads == {threading.current_thread().name}


def test_several_workers_really_run_at_once() -> None:
    barrier = threading.Barrier(3, timeout=2)  # deadlocks (raises) unless 3 run concurrently

    def step(n: int) -> int:
        barrier.wait()
        return n

    assert [o.value for o in fan_out([1, 2, 3], step, workers=3)] == [1, 2, 3]


def test_breaking_out_early_does_not_start_the_remaining_items() -> None:
    started: list[int] = []

    def step(n: int) -> int:
        started.append(n)
        time.sleep(0.05)
        return n

    for outcome in fan_out(range(20), step, workers=2):
        assert outcome.value == 0
        break  # e.g. an outage: the phase stops here
    assert len(started) < 20


def test_empty_input_yields_nothing() -> None:
    assert list(fan_out([], lambda n: n, workers=4)) == []
    assert Outcome(item=1, value=2).result() == 2
