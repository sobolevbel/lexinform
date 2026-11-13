import pytest

from lexinform.errors import StateCheckpointError
from lexinform.models import BillStatus, Command, CommandOutcome, OutcomeStatus
from lexinform.services.run_requests import RunRequests
from tests.fakes import make_analysis
from tests.harness import World
from tests.scenario.test_commands import TITLE, _commands_only


def test_publish_override_survives_batch_and_a_fresh_database() -> None:
    world = World(batch_only=True, batch_script={"3039": make_analysis(score=2)})
    world.add_bill("3039", TITLE)
    request = world.command("/analyze 3039 publish")

    _commands_only(world)
    state = world.repo.command_state(request.update_id)
    assert state is not None and state.work is not None and state.work.publish_below_threshold
    world.repo.restore(world.repo.dump())
    world.batch.resolve()
    world.run()
    world.run()

    assert len(world.publisher.new_bills) == 1 and len(world.batch.submitted) == 1
    assert world.replier.replies[-1][0].update_id == request.update_id
    state = world.repo.command_state(request.update_id)
    assert state is not None and state.work is None and state.completed_work is not None
    assert state.completed_work.batch_key and not state.final_answer_pending


def test_invalid_reset_does_not_create_a_batch_without_a_request() -> None:
    world = World()
    world.add_bill("3039", TITLE)
    world.run()
    world.command("/reset 3039 to=batch_pending")

    _commands_only(world)

    assert world.bill("3039").status is BillStatus.ANALYZED
    assert world.replier.replies[-1][1].status is OutcomeStatus.ERROR


def test_repeated_actions_wakeup_runs_the_saved_request_once() -> None:
    world = World()
    incoming = world.command("/track dry").model_copy(
        update={"run_requested": True, "actor_id": 73}
    )
    world.inbox.commands[:] = [incoming]
    calls: list[dict[str, str]] = []
    durable: list[str] = []

    def execute(command: Command) -> CommandOutcome:
        calls.append(command.inputs)
        return CommandOutcome(status=OutcomeStatus.REPORTED, note="completed")

    def checkpoint() -> None:
        durable.append(world.repo.dump())

    service = RunRequests(
        world.repo,
        world.inbox,
        world.replier,
        checkpoint,
        world.clock,
        execute,
        workflow_url="https://example.test/actions/runs/42",
    )
    assert service.drain()
    world.repo.restore(durable[-1])
    world.inbox.commands.append(incoming)
    assert service.drain()
    world.command(f"/request {incoming.update_id}")
    _commands_only(world)

    assert calls == [{"command": "track", "dry_run": "true"}]
    assert len(world.replier.replies) == 2
    _, answer = world.replier.replies[-1]
    assert "completed" in answer.note and "73" in answer.note
    assert answer.request_links == ("https://example.test/actions/runs/42",)


def test_interrupted_run_request_keeps_the_original_claim() -> None:
    world = World()
    incoming = world.command("/run").model_copy(update={"run_requested": True})
    world.inbox.commands[:] = [incoming]
    snapshots: list[str] = []
    calls: list[Command] = []

    def execute(command: Command) -> CommandOutcome:
        calls.append(command)
        raise StateCheckpointError("worker stopped")

    def checkpoint() -> None:
        snapshots.append(world.repo.dump())

    service = RunRequests(world.repo, world.inbox, world.replier, checkpoint, world.clock, execute)
    with pytest.raises(StateCheckpointError):
        service.drain()
    world.repo.restore(snapshots[-1])

    assert not service.drain()
    assert len(calls) == 1 and not world.inbox.pending()
    assert "итог неизвестен" in world.replier.replies[-1][1].note


def test_request_inspection_links_an_immediately_published_card() -> None:
    world = World()
    world.add_bill("3039", TITLE)
    request = world.command("/analyze 3039")
    _commands_only(world)
    publication = world.publication("3039")
    assert publication is not None and publication.id is not None

    world.command(f"/request {request.update_id}")
    _commands_only(world)

    assert f"/delivery {publication.id}" in world.replier.replies[-1][1].note
