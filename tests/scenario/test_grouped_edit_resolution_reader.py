import json
import sqlite3
from contextlib import closing
from datetime import timedelta, timezone
from typing import Literal, Never

import pytest

from lexinform.errors import DeliveryUncertainError
from lexinform.models import DeliveryResolution, IncomingCommand, PublicationKind, PublicationStatus
from lexinform.models.commands import (
    GroupedEditDeliveryResolution,
    GroupedEditResolutionEnvelope,
    read_delivery_resolution,
)
from lexinform.services.content_inspection import inspect_content
from tests.checkpoint_state import saved_state
from tests.scenario.test_grouped_edit_dispatch import dispatch, reserved
from tests.scenario.test_grouped_telegram_receipt import CHAT_ID, MESSAGE_ID


@pytest.mark.parametrize("action", ["confirm", "dismiss"])
@pytest.mark.parametrize(
    "mutation",
    [None, "hash", "content", "base", "target", "command", "partial", "duplicate", "orphan"],
)
def test_grouped_resolution_restore(
    action: Literal["confirm", "dismiss"],
    mutation: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, root, original, version, candidate, slot = reserved(monkeypatch)

    def lost_response(_chat_id: int, _message_id: int, _html: str) -> Never:
        raise DeliveryUncertainError("lost fixture response")

    assert (
        dispatch(world, world.repo, lost_response, lambda: None, original, version, candidate, slot)
        is PublicationStatus.UNKNOWN
    )
    now = (world.clock.now() + timedelta(minutes=1)).astimezone(timezone(timedelta(hours=2)))
    command = IncomingCommand(
        update_id=999, chat_id="-42", message_id=1, text="grouped resolution", received_at=now
    )
    assert world.repo.record_command(command)
    confirmation = action == "confirm"
    decision = GroupedEditDeliveryResolution(
        publication_id=slot,
        publication_ids=(slot,),
        term=root.term,
        number=root.number,
        kind=PublicationKind.MESSAGE_PART,
        channel_id=root.channel_id,
        action=action,
        command=command,
        resolved_at=now,
        message_id=MESSAGE_ID if confirmation else None,
        reason="observed channel message",
        content_id=version.content_id,
        base_content_id=original.content_id,
        rendered_sha256=candidate.sha256,
        telegram_chat_id=CHAT_ID,
        target_message_id=MESSAGE_ID,
        observed_at=now if confirmation else None,
    )
    envelope = GroupedEditResolutionEnvelope(
        profile="grouped-edit-resolution-v1", resolution=decision
    ).model_dump(mode="json")
    if mutation == "hash":
        envelope["resolution"]["rendered_sha256"] = "0" * 64
    elif mutation == "content":
        envelope["resolution"]["content_id"] = original.content_id
    elif mutation == "base":
        envelope["resolution"]["base_content_id"] = version.content_id
    elif mutation == "target":
        envelope["resolution"]["telegram_chat_id"] = CHAT_ID - 1
    elif mutation == "command":
        envelope["resolution"]["command"]["text"] = "different command"
    elif mutation == "partial":
        del envelope["profile"]
    raw = json.dumps(envelope)
    if mutation == "duplicate":
        raw = '{"profile":"grouped-edit-resolution-v1",' + raw[1:]
    legacy = DeliveryResolution.model_validate(
        decision.model_dump(include=set(DeliveryResolution.model_fields))
    ).model_dump_json()
    assert read_delivery_resolution(legacy).model_dump_json() == legacy
    baseline = world.repo.dump()
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript(baseline)
        db.execute(
            "UPDATE publications SET status=?, message_id=?, sent_at=? WHERE id=?",
            (
                decision.status.value,
                decision.message_id,
                now.isoformat() if confirmation else None,
                slot,
            ),
        )
        db.execute(
            "INSERT INTO delivery_resolutions(publication_id, command_update_id, resolution_json)"
            " VALUES (?, ?, ?)",
            (root.id if mutation == "orphan" else slot, command.update_id, raw),
        )
        future = "\n".join([*db.iterdump(), baseline.splitlines()[-1]])

    if mutation is not None:
        with saved_state(baseline) as unchanged:
            exact = unchanged.repo.dump()
            with pytest.raises(ValueError, match="invalid_content_history"):
                unchanged.repo.restore(future)
            assert unchanged.repo.dump() == exact
        return
    with saved_state(future) as first:
        exact = first.repo.dump()
        with saved_state(exact) as second:
            assert second.repo.dump() == exact
            assert second.repo.delivery_resolutions(slot) == [decision]
            assert second.repo.resolve_delivery(decision) is False
            history = inspect_content(second.repo, root)
            assert history is not None
            assert history.current_id == (version.content_id if confirmation else None)
