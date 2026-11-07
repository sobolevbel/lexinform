import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import Publication, PublicationStatus
from lexinform.services.posting import Poster, Told
from lexinform.services.tracking_result import TrackingResult
from tests.harness import CHANNEL
from tests.scenario.test_group_remote_checkpoints import flush, queued_world, rows
from tests.scenario.test_update_remote_checkpoints import queued, send


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("pinned", [False, True])
def test_pruned_confirmed_update_is_a_noop_with_or_without_numeric_receipt(
    grouped: bool,
    pinned: bool,
    tmp_path: Path,
) -> None:
    if grouped:
        world, identities = queued_world("updates")
        flush(world, "updates", identities)
    else:
        case = queued(with_act=False)
        world = case.world
        identities = (case.identity,)
        assert send(case)
    original = rows(world, identities)
    assert all(member.status is PublicationStatus.SENT for member in original)
    database = tmp_path / "confirmed.sqlite"
    with closing(SqliteBillRepository(database)) as repo:
        repo.restore(world.repo.dump())
        if pinned:
            with closing(sqlite3.connect(database)) as raw, raw:
                raw.executemany(
                    "UPDATE publications SET telegram_chat_id=? WHERE id=?",
                    [(-1001234567890, identity) for identity in identities],
                )
        expiry = world.clock.now() + timedelta(days=1)
        repo.prune_history(before=expiry, memo_before=expiry)
        before = repo.dump()
        poster = Poster(
            repo,
            world.publisher,
            world.clock,
            gateway=world.gateway,
            channel_id=CHANNEL,
            attention_mode="on",
            max_attempts=3,
        )
        effect_count = (len(world.publisher.sent), len(world.publisher.edited))
        result = TrackingResult()

        for historical in original:
            assert historical.status_change_id is not None and historical.id is not None, (
                "the published fixture has an exact event and publication identity"
            )
            change = repo.get_status_change(historical.status_change_id)
            bill = world.bill(historical.number)
            assert change is not None, "cleanup removes the delivery, preserving the event"
            assert poster.prepare(bill, change, with_act=True) is None
            assert poster.tell(bill, change, result, publish=False) is Told.SENT
            assert poster.tell(bill, change, result, publish=True) is Told.SENT
            poster.hold(bill, change)
            assert poster.status_update(bill, change)
            assert historical.delivery is not None, "the pre-cleanup fixture retains the old plan"
            reset = historical.model_copy(update={"status": PublicationStatus.QUEUED})
            assert repo.create_publication(reset) == historical.id
            repo.save_update_delivery(historical.status_change_id, CHANNEL, historical.delivery)
            row = repo.publication_by_id(historical.id)
            assert row is not None and row.status is PublicationStatus.SENT and row.delivery is None
            assert row.telegram_chat_id == (-1001234567890 if pinned else None)

        assert result.published == 0
        assert (len(world.publisher.sent), len(world.publisher.edited)) == effect_count
        assert repo.dump() == before


def test_confirmed_group_keeps_its_existing_plan_without_retry() -> None:
    world, identities = queued_world("updates")
    flush(world, "updates", identities)
    before = world.repo.dump()
    effect_count = (len(world.publisher.sent), len(world.publisher.edited))
    poster = world.container.poster(dry_run=False)

    for row in rows(world, identities):
        assert row.status_change_id is not None and row.delivery is not None, (
            "the original group retains its exact shared render and event"
        )
        change = world.repo.get_status_change(row.status_change_id)
        assert change is not None, "the confirmed group has its source event"
        bill = world.bill(row.number)
        result = TrackingResult()
        assert poster.prepare(bill, change, with_act=True) == row.delivery
        assert poster.tell(bill, change, result, publish=True) is Told.SENT
        assert result.published == 0
        poster.hold(bill, change)

    assert world.repo.dump() == before
    assert (len(world.publisher.sent), len(world.publisher.edited)) == effect_count


def test_prepare_keeps_a_confirmation_observed_at_the_repository_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = queued(with_act=False)
    world = case.world
    poster = world.container.poster(dry_run=False)
    pending = poster.record_change(
        case.change.model_copy(update={"id": None, "new_fingerprint": "confirmed-during-prepare"})
    )
    assert pending is not None and pending.id is not None, "the fixture uses a new event"
    create = world.repo.create_publication
    dumps: list[str] = []

    def confirm_before_plan(publication: Publication) -> int:
        identity = create(publication)
        world.repo.mark_publication(
            identity, PublicationStatus.SENT, message_id=9001, sent_at=world.clock.now()
        )
        dumps.append(world.repo.dump())
        return identity

    monkeypatch.setattr(world.repo, "create_publication", confirm_before_plan)
    assert poster.prepare(world.bill("3039"), pending) is None
    assert dumps == [world.repo.dump()]
