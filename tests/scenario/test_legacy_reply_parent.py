from pathlib import Path

import pytest

from lexinform.models import Bill, Publication, PublicationKind, PublicationStatus, StatusChange
from lexinform.ports import PublishResult
from tests.harness import CHANNEL, RCL, World
from tests.scenario.test_card_remote_checkpoints import Mode, connect, git
from tests.scenario.test_legacy_status_reply_freeze import legacy_reply
from tests.scenario.test_rollover_computation_history import rollover
from tests.scenario.test_update_remote_checkpoints import plan, row, send


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("long", [False, True])
def test_absent_legacy_parent_is_confirmed_and_frozen_before_the_first_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: Mode,
    long: bool,
) -> None:
    case = legacy_reply(mode, long=long, with_saved_parent=False)
    world = case.world
    before = row(world, case.identity)
    card = world.publication("3039", PublicationKind.NEW_BILL)
    assert card is not None and card.message_id is not None, "the fixture has its sent reader card"
    assert plan(world, case.identity).reply_to is None
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared
    observed: list[int] = []

    def inspect(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        root = row(durable, case.identity)
        assert root.delivery is not None, "the root retains its frozen context"
        assert root.delivery.reply_to == card.message_id
        assert root.attempts == before.attempts
        if publication.id == case.identity:
            assert root.status is PublicationStatus.PENDING
            assert publication.delivery is not None, "the physical root has a prepared payload"
            assert publication.delivery.reply_to == card.message_id
        observed.append(publication.id or 0)
        return original(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)

    assert send(case)

    assert observed == [case.identity, *plan(world, case.identity).continuation_ids]
    assert row(world, case.identity).attempts == before.attempts
    assert world.publisher.sent[-len(observed)].reply_to == card.message_id


@pytest.mark.parametrize(
    "status", [PublicationStatus.UNKNOWN, PublicationStatus.PENDING, PublicationStatus.FAILED]
)
def test_no_api_or_attempt_charge_without_the_legacy_owners_confirmed_card(
    status: PublicationStatus,
) -> None:
    case = legacy_reply(long=True, with_saved_parent=False)
    world = case.world
    card = world.publication("3039", PublicationKind.NEW_BILL)
    assert card is not None and card.id is not None, "the fixture has its stored reader card"
    world.repo.mark_publication(card.id, status, count_attempt=False)
    before = world.repo.dump()
    posts = len(world.publisher.sent)

    with pytest.raises(ValueError, match="sent card receipt"):
        send(case)

    assert world.repo.dump() == before
    assert len(world.publisher.sent) == posts


def test_parent_capture_uses_the_current_owner_after_a_government_term_transfer() -> None:
    world = World(profile="production", reader_attention="off")
    world.add_rcl_project()
    world.run(track=False)
    world.batch.resolve()
    world.run(track=False)
    poster = world.container.poster(dry_run=False)
    change = poster.record_change(
        StatusChange(
            term=10,
            number=RCL,
            old_fingerprint="old",
            new_fingerprint="new",
            new_stages=[],
            content_changed=True,
            detected_at=world.clock.now(),
        )
    )
    assert change is not None and change.id is not None, "the fixture stores its RCL event"
    poster.prepare(world.bill(RCL), change)
    root = world.repo.get_update_publication(change.id, CHANNEL)
    assert root is not None and root.id is not None and root.delivery is not None, (
        "prepare stores the RCL reply context"
    )
    assert world.repo.replace_unattempted_delivery(
        root.id, root.delivery.model_copy(update={"reply_to": None})
    )
    world.repo.mark_publication(root.id, PublicationStatus.FAILED)
    assert rollover(world).rehomed == 1
    current = row(world, root.id)
    saved = plan(world, root.id)
    assert current.term == 11 and Bill.model_validate_json(saved.bill_json).term == 10
    parent = world.repo.get_publication(11, RCL, PublicationKind.NEW_BILL, current.channel_id)
    assert parent is not None and parent.message_id is not None, (
        "rollover preserves its physical card"
    )

    assert world.container.poster(dry_run=False).deliver(current)

    assert plan(world, root.id).reply_to == parent.message_id
    assert world.publisher.sent[-1].reply_to == parent.message_id
    assert plan(world, root.id).bill_json == saved.bill_json
