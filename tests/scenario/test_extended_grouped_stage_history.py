from datetime import timedelta
from pathlib import Path

import pytest

from lexinform.errors import DeliveryUncertainError
from lexinform.models import PublicationStatus
from lexinform.ports import PublishResult
from lexinform.services.content_inspection import inspect_content
from tests.checkpoint_state import saved_state
from tests.scenario.test_card_remote_checkpoints import connect
from tests.scenario.test_commands import _commands_only
from tests.scenario.test_group_remote_checkpoints import flush, queued_world, restored, rows


@pytest.mark.parametrize("size", [3, 5])
def test_extended_group_retains_one_render_and_every_member_after_cleanup(size: int) -> None:
    world, identities = queued_world("updates", size=size)
    before = len(world.publisher.sent)
    flush(world, "updates", identities)
    sent = world.publisher.sent[before:]
    assert len(sent) == 1
    members = rows(world, identities)
    assert all(row.status is PublicationStatus.SENT for row in members)
    receipts = {row.message_id for row in members}
    assert len(receipts) == 1 and None not in receipts
    version = world.repo.get_publication_content(identities[0])
    assert version is not None and version.grouped_stage_evidence is not None
    evidence = version.grouped_stage_evidence
    assert tuple(member.publication_id for member in evidence.members) == identities
    assert evidence.render_witness is None and evidence.revision is None
    assert version.rendered_text == sent[0].text
    cutoff = world.clock.now() + timedelta(days=999)
    world.repo.prune_history(before=cutoff, memo_before=cutoff)
    sql = world.repo.dump()
    for _ in range(2):
        with saved_state(sql) as durable:
            assert durable.repo.dump() == sql
            assert durable.repo.get_publication_content(identities[0]) == version
            for row in rows(durable, identities):
                assert row.delivery is None and row.message_id in receipts
                view = inspect_content(durable.repo, row)
                assert view is not None and view.current_id == version.content_id


@pytest.mark.parametrize("action", ["confirm 777", "dismiss checked absence"])
def test_extended_unknown_group_recovers_as_one_shared_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    world, identities = queued_world("updates", size=3)
    remote = connect(world, tmp_path)
    publish = world.publisher.publish_updates

    def lose_response(text: str) -> PublishResult:
        publish(text)
        raise DeliveryUncertainError("accepted but response lost")

    monkeypatch.setattr(world.publisher, "publish_updates", lose_response)
    with pytest.raises(DeliveryUncertainError):
        flush(world, "updates", identities)
    fresh = restored(remote, world, tmp_path)
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    version = fresh.repo.get_publication_content(identities[0])
    assert version is not None, "the exact original was saved before API"
    for row in rows(fresh, identities):
        assert row.status is PublicationStatus.UNKNOWN
        view = inspect_content(fresh.repo, row)
        assert view is not None and view.current_id is None
    fresh.container.delivery_service(dry_run=False).run()
    assert not fresh.publisher.sent

    fresh.command(f"/delivery {identities[-1]} {action}")
    _commands_only(fresh)
    confirmed = action.startswith("confirm")
    sql = fresh.repo.dump()
    with saved_state(sql) as durable:
        assert durable.repo.dump() == sql
        assert durable.repo.get_publication_content(identities[0]) == version
        for row in rows(durable, identities):
            assert row.status is (
                PublicationStatus.SENT if confirmed else PublicationStatus.DISMISSED
            )
            assert row.message_id == (777 if confirmed else None)
            view = inspect_content(durable.repo, row)
            assert view is not None
            assert view.current_id == (version.content_id if confirmed else None)
