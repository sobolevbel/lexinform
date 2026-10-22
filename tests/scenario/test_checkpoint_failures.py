from dataclasses import replace
from pathlib import Path

import pytest

from lexinform.adapters.batch_checkpoint import GitBatchCheckpoint
from lexinform.container import Container
from lexinform.errors import StateCheckpointError
from lexinform.models import BillStatus
from lexinform.services.pipeline import RunOptions
from tests.harness import SINCE, TERM, World

TITLE = "Projekt ustawy o cudzoziemcach"


def checkpointed(w: World) -> Container:
    return replace(
        w.container,
        settings=w.container.settings.model_copy(
            update={"llm_batch_state_file": Path("state.sql")}
        ),
    )


def queued_world() -> World:
    w = World(profile="production", text_prefilter=False)
    w.add_bill("3039", TITLE)
    w.discovery.discover(TERM, SINCE)
    w.analysis.start_run()
    result = w.analysis.analyze_pending(limit=10)
    assert result.batched == 1
    return w


def refuse(checkpoint: GitBatchCheckpoint) -> None:
    raise StateCheckpointError("injected state push failure")


def test_pre_submission_checkpoint_failure_never_calls_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = queued_world()
    container = checkpointed(w)
    monkeypatch.setattr(GitBatchCheckpoint, "__call__", refuse)

    with pytest.raises(StateCheckpointError, match="injected state push failure"):
        container.analysis_service().submit_queued_batches()

    assert not w.batch.submitted
    assert not w.repo.list_open_llm_batches()
    assert len(w.repo.list_submitting_batch_intents()) == 1
    assert w.bill("3039").analysis_attempts == 0


def test_receipt_checkpoint_failure_stops_the_next_model_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = queued_world()
    original = w.repo.list_queued_batch_intents()[0]
    second = original.request.model_copy(update={"custom_id": "second", "model": "claude-sonnet-5"})
    w.repo.save_batch_intent(original.model_copy(update={"request": second}))
    checkpoints: list[int] = []

    def fail_receipt(checkpoint: GitBatchCheckpoint) -> None:
        checkpoints.append(len(w.batch.batches))
        if len(checkpoints) == 2:
            raise StateCheckpointError("receipt not saved")

    monkeypatch.setattr(GitBatchCheckpoint, "__call__", fail_receipt)

    with pytest.raises(StateCheckpointError, match="receipt not saved"):
        checkpointed(w).analysis_service().submit_queued_batches()

    assert checkpoints == [0, 1]
    assert len(w.batch.batches) == 1
    assert len(w.batch.submitted) == 1
    assert [intent.request.custom_id for intent in w.repo.list_queued_batch_intents()] == ["second"]
    assert len(w.repo.list_open_llm_batches()) == 1


def test_collection_checkpoint_failure_prevents_forget_and_reader_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = World(profile="production")
    w.add_bill("3039", TITLE)
    w.run()
    w.batch.resolve()
    incoming = w.command("/status")
    monkeypatch.setattr(GitBatchCheckpoint, "__call__", refuse)

    with pytest.raises(StateCheckpointError, match="injected state push failure"):
        checkpointed(w).pipeline(dry_run=False).run(RunOptions(term=TERM))

    assert not w.batch.forgotten
    assert not w.publisher.sent
    assert not w.publisher.edited
    assert not w.replier.replies
    assert w.repo.command_state(incoming.update_id) is None
    assert w.inbox.pending() == [incoming]
    assert w.notifier.calls[-1][0].finished_at is not None
    assert any("state persistence failed" in error for error in w.notifier.calls[-1][0].errors)
    assert w.bill("3039").analysis_attempts == 0


@pytest.mark.parametrize("command", ["/analyze 3039", "/explain 3039"])
def test_command_checkpoint_failure_leaves_the_command_unexecuted_and_stops_the_run(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    w = World(profile="production")
    w.add_bill("3039", TITLE)
    incoming = w.command(command)
    later = w.command("/status")
    monkeypatch.setattr(GitBatchCheckpoint, "__call__", refuse)

    with pytest.raises(StateCheckpointError, match="injected state push failure"):
        checkpointed(w).pipeline(dry_run=False).run(RunOptions(term=TERM))

    state = w.repo.command_state(incoming.update_id)
    assert state is not None, "commands are recorded before their analysis or explanation runs"
    assert state.executed_at is None
    assert state.handled_at is None
    assert w.repo.command_state(later.update_id) is None
    assert w.inbox.pending() == [incoming, later]
    assert not w.inbox.done_ids
    assert not w.replier.replies
    assert not w.batch.submitted
    assert not w.publisher.sent
    bill = w.repo.get(TERM, "3039")
    assert bill is None or bill.analysis_attempts == 0


def test_submission_checkpoint_failure_escapes_pipeline_phase_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = World(profile="production")
    w.add_bill("3039", TITLE)
    monkeypatch.setattr(GitBatchCheckpoint, "__call__", refuse)

    with pytest.raises(StateCheckpointError, match="injected state push failure"):
        checkpointed(w).pipeline(dry_run=False).run(RunOptions(term=TERM))

    assert not w.batch.submitted
    assert not w.publisher.sent
    assert w.bill("3039").status is BillStatus.BATCH_PENDING
    report = w.notifier.calls[-1][0]
    assert report.finished_at is not None
    assert "submit batches" in report.phase_seconds
    assert "digest" not in report.phase_seconds
    assert report.errors == ["state persistence failed: injected state push failure"]
