from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lexinform.adapters.batch_checkpoint import GitBatchCheckpoint
from lexinform.adapters.inbox_files import FileInbox, inbox_file_name
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.cli import app
from lexinform.errors import StateCheckpointError
from lexinform.models import (
    CommandOutcome,
    IncomingCommand,
    OutcomeStatus,
    Publication,
    PublicationKind,
    PublicationStatus,
    RunMode,
)
from lexinform.services.commands import CommandsResult
from tests.harness import CHANNEL, TERM, World
from tests.scenario.test_card_remote_checkpoints import RunnerLost, connect, git, restored


def command_file(world: World, folder: Path, text: str = "/status") -> IncomingCommand:
    incoming = world.command(text)
    (folder / inbox_file_name(incoming)).write_text(incoming.model_dump_json())
    return incoming


def ready(tmp_path: Path) -> tuple[World, Path, Path]:
    world = World(profile="production")
    folder = tmp_path / "inbox"
    folder.mkdir()
    world.container = replace(world.container, inbox_override=FileInbox(folder))
    remote = connect(world, tmp_path)
    return world, remote, folder


def fresh_world(world: World, remote: Path, folder: Path, tmp_path: Path) -> World:
    fresh = restored(remote, world, tmp_path)
    fresh.container = replace(fresh.container, inbox_override=FileInbox(folder))
    fresh.pipeline = fresh.container.pipeline(dry_run=False)
    return fresh


def durable_world(remote: Path) -> World:
    durable = World(profile="production")
    durable.repo.restore(git(remote, "show", "state:state.sql"))
    return durable


def drain(world: World, *, publish: bool = True, dry_run: bool = False) -> CommandsResult:
    service = world.container.command_service(dry_run=False)
    assert service is not None, "World supplies a command inbox and technical replier"
    return service.handle_pending(
        min_score=3, run_started_at=world.clock.now(), publish=publish, dry_run=dry_run
    )


def test_command_start_outcome_and_handled_are_remote_before_their_next_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, remote, folder = ready(tmp_path)
    incoming = command_file(world, folder)
    original_status = world.repo.count_document_work
    original_reply = world.replier.reply
    original_done = FileInbox.done
    observed: list[str] = []

    def status() -> dict[str, int]:
        state = durable_world(remote).repo.command_state(incoming.update_id)
        assert state is not None and state.executed_at is None and state.handled_at is None
        observed.append("execute")
        return original_status()

    def reply(command: IncomingCommand, outcome: CommandOutcome) -> None:
        state = durable_world(remote).repo.command_state(command.update_id)
        assert state is not None and state.executed_at is not None and state.handled_at is None
        assert state.reply == outcome.line()
        observed.append("reply")
        original_reply(command, outcome)

    def done(inbox: FileInbox, command: IncomingCommand) -> None:
        state = durable_world(remote).repo.command_state(command.update_id)
        assert state is not None and state.executed_at is not None and state.handled_at is not None
        observed.append("acknowledge")
        original_done(inbox, command)

    monkeypatch.setattr(world.repo, "count_document_work", status)
    monkeypatch.setattr(world.replier, "reply", reply)
    monkeypatch.setattr(FileInbox, "done", done)

    result = drain(world, publish=False)

    assert result.handled == 1 and result.failed == 0
    assert observed == ["execute", "reply", "acknowledge"]
    assert not (folder / inbox_file_name(incoming)).exists()
    assert not world.publisher.sent and not world.llm.contexts and not world.batch.submitted
    assert git(remote, "show", "state:pending-batches.json").startswith('{"version":1,')


@pytest.mark.parametrize("at", [1, 2, 3])
def test_failed_command_checkpoint_stops_before_the_next_effect_and_next_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, at: int
) -> None:
    world, remote, folder = ready(tmp_path)
    incoming = command_file(world, folder)
    later = command_file(world, folder)
    original_writer = GitBatchCheckpoint.__call__
    original_status = world.repo.count_document_work
    checkpoints: list[int] = []
    executed: list[int] = []

    def checkpoint(writer: GitBatchCheckpoint) -> None:
        checkpoints.append(len(checkpoints) + 1)
        if len(checkpoints) == at:
            raise StateCheckpointError("injected command checkpoint failure")
        original_writer(writer)

    def status() -> dict[str, int]:
        executed.append(incoming.update_id)
        return original_status()

    monkeypatch.setattr(GitBatchCheckpoint, "__call__", checkpoint)
    monkeypatch.setattr(world.repo, "count_document_work", status)

    with pytest.raises(StateCheckpointError, match="command checkpoint failure"):
        world.run(mode=RunMode.COMMANDS, discover=False, track=False, max_analyze=0, max_publish=0)

    assert checkpoints == list(range(1, at + 1))
    assert len(executed) == int(at > 1)
    assert len(world.replier.replies) == int(at > 2)
    assert world.repo.command_state(later.update_id) is None
    assert (folder / inbox_file_name(incoming)).exists()
    assert (folder / inbox_file_name(later)).exists()
    assert "digest" not in world.notifier.calls[-1][0].phase_seconds
    durable = durable_world(remote)
    state = durable.repo.command_state(incoming.update_id)
    if at == 1:
        assert state is None, "a rejected start checkpoint leaves no remote command claim"
    else:
        assert state is not None and state.handled_at is None
        assert (state.executed_at is not None) == (at == 3)


@pytest.mark.parametrize("after_execution", [False, True])
def test_fresh_clone_of_an_uncertain_command_answers_without_executing_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_execution: bool
) -> None:
    world, remote, folder = ready(tmp_path)
    incoming = command_file(world, folder)
    original_status = world.repo.count_document_work
    original_writer = GitBatchCheckpoint.__call__
    calls: list[int] = []

    def status() -> dict[str, int]:
        state = durable_world(remote).repo.command_state(incoming.update_id)
        assert state is not None and state.executed_at is None
        if not after_execution:
            raise RunnerLost()
        return original_status()

    def checkpoint(writer: GitBatchCheckpoint) -> None:
        calls.append(len(calls) + 1)
        if len(calls) == 2:
            raise StateCheckpointError("outcome was not saved")
        original_writer(writer)

    monkeypatch.setattr(world.repo, "count_document_work", status)
    monkeypatch.setattr(GitBatchCheckpoint, "__call__", checkpoint)
    with pytest.raises(StateCheckpointError if after_execution else RunnerLost):
        drain(world)
    monkeypatch.setattr(GitBatchCheckpoint, "__call__", original_writer)
    fresh = fresh_world(world, remote, folder, tmp_path)

    def no_execution() -> dict[str, int]:
        raise AssertionError("an uncertain command must never execute again")

    monkeypatch.setattr(fresh.repo, "count_document_work", no_execution)

    result = drain(fresh)

    assert result.handled == 1
    ((_, outcome),) = fresh.replier.replies
    assert outcome.status is OutcomeStatus.EXECUTED_EARLIER and "did not finish" in outcome.note
    state = durable_world(remote).repo.command_state(incoming.update_id)
    assert state is not None and state.executed_at is None and state.handled_at is not None
    assert not (folder / inbox_file_name(incoming)).exists()


def test_lost_handled_checkpoint_repeats_only_the_technical_answer_after_fresh_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, remote, folder = ready(tmp_path)
    incoming = command_file(world, folder)
    original_writer = GitBatchCheckpoint.__call__
    checkpoints: list[int] = []

    def checkpoint(writer: GitBatchCheckpoint) -> None:
        checkpoints.append(len(checkpoints) + 1)
        if len(checkpoints) == 3:
            raise StateCheckpointError("handled receipt was not saved")
        original_writer(writer)

    monkeypatch.setattr(GitBatchCheckpoint, "__call__", checkpoint)
    with pytest.raises(StateCheckpointError, match="handled receipt"):
        drain(world)
    assert len(world.replier.replies) == 1
    monkeypatch.setattr(GitBatchCheckpoint, "__call__", original_writer)
    fresh = fresh_world(world, remote, folder, tmp_path)

    def no_execution() -> dict[str, int]:
        raise AssertionError("the durable command outcome must not execute again")

    monkeypatch.setattr(fresh.repo, "count_document_work", no_execution)

    result = drain(fresh)

    assert result.handled == 1
    ((_, outcome),) = fresh.replier.replies
    assert outcome.status is OutcomeStatus.EXECUTED_EARLIER and "could not answer" in outcome.note
    state = durable_world(remote).repo.command_state(incoming.update_id)
    assert state is not None and state.executed_at is not None and state.handled_at is not None
    assert not (folder / inbox_file_name(incoming)).exists()


def test_already_handled_inbox_file_requires_a_checkpoint_before_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, remote, folder = ready(tmp_path)
    incoming = command_file(world, folder)
    drain(world)
    (folder / inbox_file_name(incoming)).write_text(incoming.model_dump_json())
    fresh = fresh_world(world, remote, folder, tmp_path)
    original_writer = GitBatchCheckpoint.__call__

    def fail(writer: GitBatchCheckpoint) -> None:
        raise StateCheckpointError("inbox acknowledgement requires durable state")

    monkeypatch.setattr(GitBatchCheckpoint, "__call__", fail)
    with pytest.raises(StateCheckpointError, match="inbox acknowledgement"):
        drain(fresh)
    assert (folder / inbox_file_name(incoming)).exists() and not fresh.replier.replies
    monkeypatch.setattr(GitBatchCheckpoint, "__call__", original_writer)

    result = drain(fresh)

    assert result.handled == 0 and not fresh.replier.replies
    assert not (folder / inbox_file_name(incoming)).exists()


def test_failed_technical_reply_retries_the_saved_answer_without_reexecuting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, remote, folder = ready(tmp_path)
    incoming = command_file(world, folder)
    world.replier.outage = True

    result = drain(world)

    assert result.handled == 0 and result.fatal_error is not None
    assert (folder / inbox_file_name(incoming)).exists() and not world.replier.replies
    state = durable_world(remote).repo.command_state(incoming.update_id)
    assert state is not None and state.executed_at is not None and state.handled_at is None
    fresh = fresh_world(world, remote, folder, tmp_path)

    def no_execution() -> dict[str, int]:
        raise AssertionError("a failed technical reply must not rerun the command")

    monkeypatch.setattr(fresh.repo, "count_document_work", no_execution)

    repeated = drain(fresh)

    assert repeated.handled == 1
    assert fresh.replier.replies[0][1].status is OutcomeStatus.EXECUTED_EARLIER
    assert not (folder / inbox_file_name(incoming)).exists()


def test_typed_checkpoint_error_from_replier_is_never_marked_handled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, remote, folder = ready(tmp_path)
    incoming = command_file(world, folder)

    def reply(command: IncomingCommand, outcome: CommandOutcome) -> None:
        raise StateCheckpointError("typed replier failure")

    monkeypatch.setattr(world.replier, "reply", reply)

    with pytest.raises(StateCheckpointError, match="typed replier failure"):
        drain(world)

    state = durable_world(remote).repo.command_state(incoming.update_id)
    assert state is not None and state.executed_at is not None and state.handled_at is None
    assert (folder / inbox_file_name(incoming)).exists()


@pytest.mark.parametrize("live_service", [False, True])
def test_dry_run_never_pushes_a_command_checkpoint_or_acknowledges_its_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_service: bool
) -> None:
    world, remote, folder = ready(tmp_path)
    incoming = command_file(world, folder)
    before = git(remote, "rev-parse", "state")

    def no_checkpoint(writer: GitBatchCheckpoint) -> None:
        raise AssertionError("dry-run must not invoke the configured live state writer")

    monkeypatch.setattr(GitBatchCheckpoint, "__call__", no_checkpoint)
    if live_service:
        result = drain(world, dry_run=True)
        assert result.handled == 1
    else:
        world.pipeline = world.container.pipeline(dry_run=True)
        report = world.run(
            dry_run=True, mode=RunMode.COMMANDS, discover=False, track=False, max_analyze=0
        )
        assert report.commands_handled == 1
        assert world.repo.command_state(incoming.update_id) is None
    assert git(remote, "rev-parse", "state") == before
    assert durable_world(remote).repo.command_state(incoming.update_id) is None
    assert (folder / inbox_file_name(incoming)).exists()
    assert not world.publisher.sent and not world.llm.contexts


@pytest.mark.parametrize("command", ["cost", "runs"])
def test_read_only_cli_does_not_construct_a_state_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    database = tmp_path / "read-only.db"
    repo = SqliteBillRepository(database)
    repo.migrate()
    repo.close()
    monkeypatch.setenv("LEXINFORM_DB_PATH", str(database))
    monkeypatch.setenv("LEXINFORM_STATE_CHECKPOINT_REQUIRED", "true")
    monkeypatch.setenv("LEXINFORM_LLM_BATCH_STATE_FILE", str(tmp_path / "not-a-worktree.sql"))
    monkeypatch.setenv("LEXINFORM_TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("LEXINFORM_TELEGRAM_LOG_CHANNEL_ID", "")

    result = CliRunner().invoke(app, [command])

    assert result.exit_code == 0, result.output
    assert not (tmp_path / "not-a-worktree.sql").exists()


@pytest.mark.parametrize("action", ["confirm 777", "dismiss superseded"])
def test_delivery_resolution_and_command_outcome_are_durable_before_technical_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    world, remote, folder = ready(tmp_path)
    identity = world.repo.create_publication(
        Publication(
            term=TERM,
            number="3039",
            kind=PublicationKind.NEW_BILL,
            status=PublicationStatus.UNKNOWN,
            channel_id=CHANNEL,
            created_at=world.clock.now(),
        )
    )
    world.container.state_checkpoint()()
    incoming = command_file(world, folder, f"/delivery {identity} {action}")
    original_reply = world.replier.reply
    observed: list[int] = []

    def reply(command: IncomingCommand, outcome: CommandOutcome) -> None:
        durable = durable_world(remote)
        history = durable.repo.delivery_resolutions(identity)
        assert len(history) == 1 and history[0].command == command
        assert history[0].action == action.split()[0]
        publication = durable.repo.publication_by_id(identity)
        assert publication is not None and publication.status is history[0].status
        state = durable.repo.command_state(command.update_id)
        assert state is not None and state.executed_at is not None and state.handled_at is None
        observed.append(command.update_id)
        original_reply(command, outcome)

    monkeypatch.setattr(world.replier, "reply", reply)

    result = drain(world)

    assert result.handled == 1 and result.failed == 0
    assert observed == [incoming.update_id]
    fresh = fresh_world(world, remote, folder, tmp_path)
    history = fresh.repo.delivery_resolutions(identity)
    assert len(history) == 1 and history[0].command == incoming
    assert not world.publisher.sent and not world.llm.contexts


def test_failed_outcome_checkpoint_does_not_acknowledge_a_local_delivery_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, remote, folder = ready(tmp_path)
    identity = world.repo.create_publication(
        Publication(
            term=TERM,
            number="3039",
            kind=PublicationKind.NEW_BILL,
            status=PublicationStatus.UNKNOWN,
            channel_id=CHANNEL,
            created_at=world.clock.now(),
        )
    )
    world.container.state_checkpoint()()
    incoming = command_file(world, folder, f"/delivery {identity} confirm 777")
    original_writer = GitBatchCheckpoint.__call__

    def checkpoint(writer: GitBatchCheckpoint) -> None:
        state = world.repo.command_state(incoming.update_id)
        if state is not None and state.executed_at is not None:
            raise StateCheckpointError("delivery outcome was not saved")
        original_writer(writer)

    monkeypatch.setattr(GitBatchCheckpoint, "__call__", checkpoint)
    with pytest.raises(StateCheckpointError, match="delivery outcome"):
        drain(world)
    assert len(world.repo.delivery_resolutions(identity)) == 1
    assert (folder / inbox_file_name(incoming)).exists() and not world.replier.replies
    monkeypatch.setattr(GitBatchCheckpoint, "__call__", original_writer)
    fresh = fresh_world(world, remote, folder, tmp_path)
    assert not fresh.repo.delivery_resolutions(identity)
    publication = fresh.repo.publication_by_id(identity)
    assert publication is not None and publication.status is PublicationStatus.UNKNOWN

    result = drain(fresh)

    assert result.handled == 1 and not fresh.repo.delivery_resolutions(identity)
    assert "did not finish" in fresh.replier.replies[0][1].note


def test_generic_technical_reply_failure_keeps_its_existing_handled_failure_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, remote, folder = ready(tmp_path)
    incoming = command_file(world, folder)

    def reply(command: IncomingCommand, outcome: CommandOutcome) -> None:
        raise RuntimeError("invalid technical reply")

    monkeypatch.setattr(world.replier, "reply", reply)

    result = drain(world)

    assert result.handled == 1
    state = durable_world(remote).repo.command_state(incoming.update_id)
    assert state is not None and state.handled_at is not None and state.reply is not None
    assert "reply failed: invalid technical reply" in state.reply
    assert not (folder / inbox_file_name(incoming)).exists()


def test_typed_inbox_acknowledgement_error_stops_the_next_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, remote, folder = ready(tmp_path)
    incoming = command_file(world, folder)
    later = command_file(world, folder)

    def done(inbox: FileInbox, command: IncomingCommand) -> None:
        raise StateCheckpointError("typed inbox acknowledgement failure")

    monkeypatch.setattr(FileInbox, "done", done)

    with pytest.raises(StateCheckpointError, match="inbox acknowledgement"):
        drain(world)

    state = durable_world(remote).repo.command_state(incoming.update_id)
    assert state is not None and state.handled_at is not None
    assert world.repo.command_state(later.update_id) is None
    assert (folder / inbox_file_name(incoming)).exists()
    assert (folder / inbox_file_name(later)).exists()


def test_same_service_revalidates_an_earlier_local_outcome_before_answering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, remote, folder = ready(tmp_path)
    incoming = command_file(world, folder)
    original_writer = GitBatchCheckpoint.__call__
    original_status = world.repo.count_document_work
    executed: list[int] = []
    rejected: list[int] = []

    def status() -> dict[str, int]:
        executed.append(incoming.update_id)
        return original_status()

    def checkpoint(writer: GitBatchCheckpoint) -> None:
        state = world.repo.command_state(incoming.update_id)
        if state is not None and state.executed_at is not None:
            rejected.append(incoming.update_id)
            raise StateCheckpointError("the local outcome is still not durable")
        original_writer(writer)

    monkeypatch.setattr(world.repo, "count_document_work", status)
    monkeypatch.setattr(GitBatchCheckpoint, "__call__", checkpoint)

    for _ in range(2):
        with pytest.raises(StateCheckpointError, match="local outcome"):
            drain(world)

    assert executed == [incoming.update_id] and rejected == [incoming.update_id] * 2
    state = durable_world(remote).repo.command_state(incoming.update_id)
    assert state is not None and state.executed_at is None and state.handled_at is None
    assert not world.replier.replies and (folder / inbox_file_name(incoming)).exists()
