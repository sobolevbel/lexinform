from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Never

import pytest

from lexinform.container import Container
from lexinform.errors import DeliveryUncertainError, StateCheckpointError
from lexinform.models import OutcomeStatus, PublicationStatus, parse_command
from lexinform.models.commands import GroupedEditDeliveryResolution
from lexinform.services.content_inspection import inspect_content
from tests.checkpoint_state import saved_state
from tests.scenario.test_card_remote_checkpoints import connect, git, reject_checkpoint
from tests.scenario.test_grouped_edit_dispatch import dispatch, reserved
from tests.scenario.test_grouped_telegram_receipt import CHAT_ID, MESSAGE_ID


@pytest.mark.parametrize(
    "mode", ["confirm", "dismiss", "rollback", "disabled", "no-checkpoint", "wrong-channel"]
)
def test_groupedit_resolution_uses_recorded_command_and_durable_checkpoint(
    mode: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    world, root, original, version, candidate, slot_id = reserved(monkeypatch)

    def lose_response(_chat: int, _message: int, _text: str) -> Never:
        raise DeliveryUncertainError("fixture lost response")

    assert (
        dispatch(
            world, world.repo, lose_response, lambda: None, original, version, candidate, slot_id
        )
        is PublicationStatus.UNKNOWN
    )
    slot = world.repo.publication_by_id(slot_id)
    assert slot is not None, "the fixture preserves the uncertain edit"
    observed = slot.created_at + timedelta(minutes=1)
    world.clock.advance(days=10)
    world.container = replace(
        world.container,
        settings=world.container.settings.model_copy(
            update={
                "grouped_stage_date_edits_enabled": mode != "disabled",
                "telegram_bot_token": "",
                "telegram_channel_id": "@other_reader"
                if mode == "wrong-channel"
                else world.container.settings.telegram_channel_id,
            }
        ),
    )

    def reject_telegram(_container: Container, *, for_channel: bool = True) -> Never:
        raise AssertionError("local resolution cannot use the Telegram editor")

    monkeypatch.setattr(Container, "telegram_client", reject_telegram)
    remote = connect(world, tmp_path) if mode != "no-checkpoint" else None
    service = world.container.command_service(dry_run=False)
    assert service is not None, "the fake inbox is wired through the production container"

    def handle(*, publish: bool = True, dry_run: bool = False) -> None:
        service.handle_pending(
            min_score=3, run_started_at=world.clock.now(), publish=publish, dry_run=dry_run
        )

    confirmation = mode != "dismiss"
    action = "confirm" if confirmation else "dismiss"
    timestamp = f" {observed.isoformat()}" if confirmation else ""
    text = (
        f"/groupedit {action} {slot_id} {version.content_id} {original.content_id}"
        f" {candidate.sha256} {CHAT_ID} {MESSAGE_ID}{timestamp} | operator verified fixture"
    )
    if mode in ("disabled", "no-checkpoint"):
        world.command(text)
        handle()
        assert "requires an enabled publishing run" in world.replier.replies[-1][1].note
        assert world.repo.publication_by_id(slot_id) == slot
        return
    if mode == "wrong-channel":
        world.command(text)
        handle()
        assert world.replier.replies[-1][1].status is OutcomeStatus.NOT_FOUND
        assert world.repo.publication_by_id(slot_id) == slot
        return
    assert remote is not None, "successful resolution requires the real Git checkpoint"
    if mode == "rollback":
        command = world.command(text)
        calls = reject_checkpoint(monkeypatch, at=2)
        with pytest.raises(StateCheckpointError, match="injected reader checkpoint failure"):
            handle()
        assert calls == [1, 2]
        assert not world.replier.replies
        authoritative = git(remote, "show", "state:state.sql")
        with saved_state(authoritative) as restored:
            assert restored.repo.publication_by_id(slot_id) == slot
            assert restored.repo.delivery_resolutions(slot_id) == []
            recorded = restored.repo.command_state(command.update_id)
            assert recorded is not None and recorded.executed_at is None
        world.repo.restore(authoritative)
        handle()
        assert world.replier.replies[-1][1].status is OutcomeStatus.EXECUTED_EARLIER
        assert world.repo.publication_by_id(slot_id) == slot
        assert world.repo.delivery_resolutions(slot_id) == []
        return

    dry = world.command(text)
    world.repo.begin()
    try:
        handle(dry_run=True)
    finally:
        world.repo.rollback()
    assert world.repo.command_state(dry.update_id) is None
    assert world.repo.publication_by_id(slot_id) == slot
    world.inbox.done(dry)
    world.command(text)
    handle(publish=False)
    assert world.repo.publication_by_id(slot_id) == slot
    world.command(f"/delivery {slot_id} confirm {MESSAGE_ID}")
    handle()
    assert world.repo.publication_by_id(slot_id) == slot
    assert "delivery resolution is disabled" in world.replier.replies[-1][1].note
    for invalid in (
        text.replace(candidate.sha256, "0" * 64),
        text.replace(original.content_id, version.content_id),
        text.replace(str(CHAT_ID), str(CHAT_ID - 1)),
        text.replace(str(MESSAGE_ID), str(MESSAGE_ID + 1)),
        text.replace(f"{action} {slot_id}", f"{action} {root.id}"),
    ):
        world.command(invalid)
        handle()
        assert world.replier.replies[-1][1].status in (OutcomeStatus.ERROR, OutcomeStatus.NOT_FOUND)
        assert world.repo.publication_by_id(slot_id) == slot
        assert world.repo.delivery_resolutions(slot_id) == []
    command = world.command(text)
    handle()
    assert world.replier.replies[-1][1].status is OutcomeStatus.DELIVERY
    terminal = git(remote, "show", "state:state.sql")
    with saved_state(terminal) as first, saved_state(first.repo.dump()) as second:
        assert second.repo.dump() == first.repo.dump()
        (audit,) = second.repo.delivery_resolutions(slot_id)
        assert isinstance(audit, GroupedEditDeliveryResolution), "the dedicated audit is typed"
        assert audit.command == command
        assert audit.observed_at == (observed if confirmation else None)
        assert audit.action == action
        history = inspect_content(second.repo, root)
        assert history is not None and history.current_id == (
            version.content_id if confirmation else None
        )
    world.command(text, update_id=command.update_id)
    world.command(text)
    handle()
    assert world.replier.replies[-1][1].note == "grouped edit resolution refused"
    assert world.repo.delivery_resolutions(slot_id) == [audit]


@pytest.mark.parametrize(
    ("action", "tail"),
    [
        ("confirm", "2026-09-08T09:00:00 | no offset"),
        ("confirm", "2026-09-08T09:00:00-00:00 | unknown offset"),
        ("confirm", "2026-09-08T09:00:00+03:99 | malformed offset"),
        ("confirm", "2026-09-08T09:00:00Z |"),
        ("dismiss", "2026-09-08T09:00:00Z | unexpected observation"),
        ("dismiss", "| " + "x" * 501),
    ],
)
def test_groupedit_resolution_rejects_invalid_observation_or_reason(action: str, tail: str) -> None:
    content = "content-v1:" + "a" * 64
    base = "content-v1:" + "b" * 64
    command = parse_command(f"/groupedit {action} 1 {content} {base} {'0' * 64} -100 1 {tail}")
    assert command is not None and command.error is not None
