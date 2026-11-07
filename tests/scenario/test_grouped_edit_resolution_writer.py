import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from typing import Literal, Never

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.errors import DeliveryUncertainError
from lexinform.models import DeliveryResolution, IncomingCommand, PublicationStatus
from lexinform.models.commands import GroupedEditDeliveryResolution
from lexinform.services.content_inspection import inspect_content
from tests.checkpoint_state import saved_state
from tests.scenario.test_grouped_edit_dispatch import dispatch, reserved
from tests.scenario.test_grouped_telegram_receipt import CHAT_ID, MESSAGE_ID


@pytest.mark.parametrize("action", ["confirm", "dismiss"])
def test_grouped_resolution_is_exact_atomic_and_inert(
    action: Literal["confirm", "dismiss"],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    world, root, original, version, candidate, slot_id = reserved(monkeypatch)

    def lose_response(_chat_id: int, _message_id: int, _html: str) -> Never:
        raise DeliveryUncertainError("lost fixture response")

    assert (
        dispatch(
            world, world.repo, lose_response, lambda: None, original, version, candidate, slot_id
        )
        is PublicationStatus.UNKNOWN
    )
    slot = world.repo.publication_by_id(slot_id)
    assert slot is not None, "the reserved edit remains durable"
    now = world.clock.now() + timedelta(minutes=1)
    command = IncomingCommand(
        update_id=998, chat_id="-42", message_id=2, text="resolve grouped edit", received_at=now
    )
    confirmed = action == "confirm"
    audit = GroupedEditDeliveryResolution(
        publication_id=slot_id,
        publication_ids=(slot_id,),
        term=slot.term,
        number=slot.number,
        kind=slot.kind,
        channel_id=slot.channel_id,
        action=action,
        command=command,
        resolved_at=now,
        message_id=MESSAGE_ID if confirmed else None,
        reason="operator attestation",
        content_id=version.content_id,
        base_content_id=original.content_id,
        rendered_sha256=candidate.sha256,
        telegram_chat_id=CHAT_ID,
        target_message_id=MESSAGE_ID,
        observed_at=now if confirmed else None,
    )
    path = tmp_path / "state.db"
    with closing(SqliteBillRepository(str(path))) as repo:
        repo.restore(world.repo.dump())
        unknown = repo.dump()
        assert not repo.resolve_grouped_stage_edit(audit)
        assert repo.dump() == unknown
        assert repo.record_command(command)
        baseline = repo.dump()
        for invalid in (
            audit.model_copy(update={"content_id": original.content_id}),
            audit.model_copy(update={"base_content_id": version.content_id}),
            audit.model_copy(update={"telegram_chat_id": CHAT_ID - 1}),
            audit.model_copy(update={"rendered_sha256": "0" * 64}),
            audit.model_copy(update={"command": command.model_copy(update={"text": "other"})}),
        ):
            assert not repo.resolve_grouped_stage_edit(invalid)
            assert repo.dump() == baseline
        if confirmed:
            with pytest.raises(ValueError, match="invalid grouped edit observation"):
                repo.resolve_grouped_stage_edit(audit.model_copy(update={"observed_at": None}))
            assert repo.dump() == baseline
        legacy = DeliveryResolution.model_validate(
            audit.model_dump(include=set(DeliveryResolution.model_fields))
        )
        assert not repo.resolve_delivery(legacy)
        assert repo.dump() == baseline

        with closing(sqlite3.connect(path, isolation_level=None)) as sql:
            sql.execute(
                "INSERT INTO delivery_resolutions(publication_id,command_update_id,resolution_json)"
                " VALUES (?, ?, ?)",
                (root.id, command.update_id, legacy.model_dump_json()),
            )
            duplicate = repo.dump()
            assert not repo.resolve_grouped_stage_edit(audit)
            assert repo.dump() == duplicate
            sql.execute(
                "DELETE FROM delivery_resolutions WHERE command_update_id=?", (command.update_id,)
            )
            sql.execute(
                "CREATE TRIGGER fail_audit BEFORE INSERT ON delivery_resolutions"
                " BEGIN SELECT RAISE(ABORT, 'fixture audit failure'); END"
            )
            before_failure = repo.dump()
            with pytest.raises(sqlite3.IntegrityError, match="fixture audit failure"):
                repo.resolve_grouped_stage_edit(audit)
            assert repo.dump() == before_failure
            sql.execute("DROP TRIGGER fail_audit")

        assert repo.resolve_grouped_stage_edit(audit)
        terminal = repo.dump()
        assert not repo.resolve_grouped_stage_edit(audit)
        assert not repo.resolve_delivery(legacy)
        assert (
            dispatch(
                world, repo, lose_response, lambda: None, original, version, candidate, slot_id
            )
            is None
        )
        assert repo.dump() == terminal
        with saved_state(terminal) as first:
            assert first.repo.dump() == terminal
            with saved_state(first.repo.dump()) as second:
                assert second.repo.dump() == terminal
                assert second.repo.delivery_resolutions(slot_id) == [audit]
                history = inspect_content(second.repo, root)
                assert history is not None and history.integrity == "valid"
                assert history.current_id == (version.content_id if confirmed else None)
