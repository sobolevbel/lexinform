"""A collected batch is deleted at the provider, and only once its answers are persisted."""

from dataclasses import replace
from pathlib import Path

import pytest

from lexinform.adapters.batch_checkpoint import GitBatchCheckpoint
from lexinform.errors import LlmUnavailableError
from lexinform.services.pipeline import RunOptions
from tests.harness import TERM, World


def _collected() -> World:
    w = World(batch=True)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.batch.resolve()
    return w


def test_without_a_checkpoint_the_batch_is_forgotten_by_the_next_run_and_only_once() -> None:
    w = _collected()

    w.run()
    assert w.bill("3039").analysis is not None
    assert w.batch.forgotten == []

    w.run()
    w.run()

    assert w.batch.forgotten == ["batch-1"]
    assert not w.repo.list_unforgotten_llm_batches()


def test_with_a_checkpoint_the_batch_is_forgotten_after_the_state_is_pushed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = _collected()
    checkpointed = replace(
        w.container,
        settings=w.container.settings.model_copy(
            update={"llm_batch_state_file": Path("state.sql")}
        ),
    )
    forgotten_at_push: list[list[str]] = []

    def push(checkpoint: GitBatchCheckpoint) -> None:
        forgotten_at_push.append(list(w.batch.forgotten))

    monkeypatch.setattr(GitBatchCheckpoint, "__call__", push)

    checkpointed.pipeline(dry_run=False).run(RunOptions(term=TERM))

    assert forgotten_at_push == [[]]
    assert w.batch.forgotten == ["batch-1"]


def test_a_failed_checkpoint_forgets_nothing_collected_in_this_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = _collected()
    checkpointed = replace(
        w.container,
        settings=w.container.settings.model_copy(
            update={"llm_batch_state_file": Path("state.sql")}
        ),
    )

    def refuse(checkpoint: GitBatchCheckpoint) -> None:
        raise RuntimeError("state push rejected")

    monkeypatch.setattr(GitBatchCheckpoint, "__call__", refuse)

    report = checkpointed.pipeline(dry_run=False).run(RunOptions(term=TERM))

    assert report.errors
    assert w.bill("3039").analysis is not None
    assert w.batch.forgotten == []


def test_a_dry_run_never_forgets() -> None:
    w = _collected()
    w.run()

    w.container.pipeline(dry_run=True).run(RunOptions(dry_run=True, term=TERM))

    assert w.batch.forgotten == []


def test_a_provider_that_will_not_delete_is_asked_again_next_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = _collected()
    w.run()

    def unavailable(batch_id: str) -> None:
        raise LlmUnavailableError("overloaded")

    with monkeypatch.context() as patch:
        patch.setattr(w.batch, "forget", unavailable)
        failed = w.run()
    assert any("forget" in error for error in failed.errors)

    w.run()

    assert w.batch.forgotten == ["batch-1"]
