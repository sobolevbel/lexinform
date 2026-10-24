import datetime as dt
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pytest

from lexinform.errors import StateCheckpointError
from lexinform.models import Bill, PrintInfo, Publication, PublicationKind, PublicationStatus
from lexinform.ports import PublishResult
from lexinform.services.card_edits import edit_single_card
from lexinform.services.publications import inherit_card
from tests.harness import CHANNEL, TERM, World, act
from tests.scenario.test_card_remote_checkpoints import (
    Mode,
    connect,
    git,
    ready_world,
    reject_checkpoint,
    restored,
)
from tests.scenario.test_single_card_revision_producer import card, change, ready, refresh

Caller = Literal["poster", "publishing"]


def owned(world: World, number: str) -> Publication:
    found = world.publication(number)
    assert found is not None and found.id is not None, "the owner is a stored sent logical card"
    return found


def latest(world: World, number: str) -> Publication:
    owner = owned(world, number)
    return world.repo.card_refresh_parts(owner.id or 0)[0]


def successor(world: World) -> Bill:
    world.add_bill("3040", "Projekt ustawy o cudzoziemcach: nowy druk")
    world.repo.upsert_summary(world.gateway.details["3040"], now=world.clock.now())
    record = world.bill("3039").analysis
    assert record is not None, "the production fixture has its batch analysis"
    world.repo.save_analysis(TERM, "3040", record)
    world.repo.link_bills(TERM, "3039", "3040")
    return world.bill("3040")


def editor(world: World, caller: Caller) -> tuple[str, Callable[[], bool]]:
    if caller == "poster":
        return "3039", lambda: world.container.poster(dry_run=False).rerender_card(
            world.bill("3039"), card(world)
        )
    bill = successor(world)
    return bill.number, lambda: world.container.publishing_service(dry_run=False).publish_bill(bill)


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("caller", ["poster", "publishing"])
def test_other_card_editors_save_matching_logical_owner_and_remote_frozen_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode, caller: Caller
) -> None:
    world = ready(mode)
    predecessor = card(world)
    number, send = editor(world, caller)
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared
    inspected: list[Publication] = []

    def inspect(saved: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        operation = latest(durable, number)
        owner = owned(durable, number)
        assert operation == saved
        assert operation.status is PublicationStatus.PENDING
        assert operation.number == owner.number == number
        assert operation.term == owner.term == TERM
        assert operation.channel_id == owner.channel_id == CHANNEL
        assert operation.delivery is not None, "the edit is frozen before the physical API"
        assert operation.delivery.revision_of == owner.id
        assert operation.delivery.edit_message_id == owner.message_id == predecessor.message_id
        assert operation.delivery.today == world.clock.now().date()
        assert operation.delivery.rendered_text
        assert Bill.model_validate_json(operation.delivery.bill_json).analysis is None
        assert owned(durable, "3039") == predecessor
        assert owner.status is PublicationStatus.SENT
        if caller == "publishing":
            assert owner.id != predecessor.id
            assert owner.sent_at == predecessor.sent_at
            assert owner.rendered_sha256 is None
        inspected.append(operation)
        return original(saved)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)
    sent_before = len(world.publisher.sent)

    assert send()

    assert len(inspected) == 1
    assert len(world.publisher.sent) == sent_before
    fresh = restored(remote, world, tmp_path)
    assert latest(fresh, number).status is PublicationStatus.SENT
    assert owned(fresh, number).rendered_sha256 == inspected[0].rendered_sha256
    if caller == "publishing":
        assert owned(fresh, "3039") == predecessor
    assert refresh(fresh) == 0


@pytest.mark.parametrize("caller", ["poster", "publishing"])
@pytest.mark.parametrize("at", [1, 2])
def test_other_editors_checkpoint_failure_preserves_original_remote_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caller: Caller, at: int
) -> None:
    world = ready()
    predecessor = card(world)
    number, send = editor(world, caller)
    remote = connect(world, tmp_path)
    before = len(world.publisher.edited)
    reject_checkpoint(monkeypatch, at)

    with pytest.raises(StateCheckpointError):
        send()

    assert len(world.publisher.edited) == before + int(at == 2)
    fresh = restored(remote, world, tmp_path)
    assert owned(fresh, "3039") == predecessor
    if at == 1:
        if caller == "publishing":
            assert fresh.publication(number) is None
        else:
            assert latest(fresh, number) == predecessor
    else:
        owner = owned(fresh, number)
        assert owner.status is PublicationStatus.SENT
        assert owner.rendered_sha256 == (
            predecessor.rendered_sha256 if caller == "poster" else None
        )
        fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
        saved = latest(fresh, number)
        assert saved.status is PublicationStatus.UNKNOWN
        change(fresh, "Новая текущая редакция: 999 PLN.")
        assert not fresh.container.poster(dry_run=False).rerender_card(fresh.bill(number), owner)
        assert latest(fresh, number) == saved
        assert fresh.container.delivery_service(dry_run=False).run().cards_refreshed == 0
        assert len(world.publisher.edited) == before + 1


@pytest.mark.parametrize(
    "status",
    [
        PublicationStatus.QUEUED,
        PublicationStatus.PENDING,
        PublicationStatus.FAILED,
        PublicationStatus.UNKNOWN,
    ],
)
def test_an_active_predecessor_edit_blocks_successor_retag_and_other_editors(
    tmp_path: Path, status: PublicationStatus
) -> None:
    world = ready()
    predecessor = card(world)
    remote = connect(world, tmp_path)
    world.publisher.fail_on.add("3039")
    assert refresh(world) == 0
    saved = latest(world, "3039")
    world.repo.mark_publication(saved.id or 0, status, count_attempt=False)
    active = latest(world, "3039")
    world.publisher.fail_on.clear()
    number, send = editor(world, "publishing")
    before = len(world.publisher.edited)

    assert send()

    alias = owned(world, number)
    assert alias.id != predecessor.id and alias.message_id == predecessor.message_id
    assert latest(world, number) == alias
    assert not world.container.poster(dry_run=False).rerender_card(world.bill(number), alias)
    assert not edit_single_card(
        world.repo,
        world.clock,
        world.publisher,
        world.bill("3039"),
        predecessor,
        None,
        channel_id=CHANNEL,
    )
    assert latest(world, "3039") == active
    assert owned(world, "3039") == predecessor
    assert len(world.publisher.edited) == before
    assert git(remote, "rev-parse", "state")


@pytest.mark.parametrize("mismatch", ["number", "term", "channel", "target", "status", "analysis"])
def test_reusable_card_editor_rejects_mismatched_owner_without_sending(mismatch: str) -> None:
    world = ready()
    owner = card(world)
    wrong = owner
    if mismatch == "analysis":
        bill = world.bill("3039").model_copy(update={"analysis": None})
    elif mismatch == "number":
        bill = world.bill("3039").model_copy(
            update={"summary": world.bill("3039").summary.model_copy(update={"number": "3040"})}
        )
    elif mismatch == "term":
        bill = world.bill("3039").model_copy(
            update={"summary": world.bill("3039").summary.model_copy(update={"term": TERM + 1})}
        )
    else:
        bill = world.bill("3039")
    if mismatch == "target":
        wrong = owner.model_copy(update={"message_id": 999})
    if mismatch == "status":
        world.repo.mark_publication(owner.id or 0, PublicationStatus.UNKNOWN, count_attempt=False)
    before = len(world.publisher.edited)

    assert not edit_single_card(
        world.repo,
        world.clock,
        world.publisher,
        bill,
        wrong,
        None,
        channel_id="@other" if mismatch == "channel" else CHANNEL,
    )

    assert len(world.publisher.edited) == before
    assert latest(world, "3039").kind is PublicationKind.NEW_BILL


def test_card_alias_query_scopes_physical_identity_to_sent_cards_and_channel() -> None:
    world = ready()
    predecessor = card(world)
    alias = inherit_card(
        world.repo,
        term=TERM,
        number="3040",
        channel_id=CHANNEL,
        card=predecessor,
        now=world.clock.now(),
    )
    assert world.repo.card_aliases(CHANNEL, predecessor.message_id or 0) == [predecessor, alias]
    assert world.repo.card_aliases("@other", predecessor.message_id or 0) == []
    world.repo.mark_publication(alias.id or 0, PublicationStatus.FAILED, count_attempt=False)
    assert world.repo.card_aliases(CHANNEL, predecessor.message_id or 0) == [predecessor]


def test_publishing_preserves_legacy_multipart_retag_skip() -> None:
    world = ready_world(long=True)
    world.run()
    predecessor = card(world)
    assert predecessor.delivery is not None and predecessor.delivery.continuation_ids, (
        "the production fixture published a multipart card"
    )
    bill = successor(world)
    before = len(world.publisher.edited)

    assert world.container.publishing_service(dry_run=False).publish_bill(bill)

    alias = owned(world, bill.number)
    assert latest(world, bill.number) == alias
    assert alias.rendered_sha256 is None
    assert owned(world, "3039") == predecessor
    assert len(world.publisher.edited) == before
    assert (
        edit_single_card(
            world.repo,
            world.clock,
            world.publisher,
            bill,
            alias,
            None,
            channel_id=CHANNEL,
        )
        is None
    )


def test_an_unfinished_predecessor_part_blocks_alias_card_edits() -> None:
    world = ready_world(long=True)
    world.run()
    predecessor = card(world)
    assert predecessor.delivery is not None and predecessor.delivery.continuation_ids, (
        "the production fixture published a multipart card"
    )
    bill = successor(world)
    alias = inherit_card(
        world.repo,
        term=TERM,
        number=bill.number,
        channel_id=CHANNEL,
        card=predecessor,
        now=world.clock.now(),
    )
    world.repo.mark_publication(
        predecessor.delivery.continuation_ids[0], PublicationStatus.UNKNOWN, count_attempt=False
    )
    before = len(world.publisher.edited)

    assert not world.container.poster(dry_run=False).rerender_card(bill, alias)

    assert latest(world, bill.number) == alias
    assert len(world.publisher.edited) == before


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("caller", ["poster", "publishing"])
def test_saved_callers_share_one_delivery_attempt_with_normal_tracking_in_the_same_run(
    monkeypatch: pytest.MonkeyPatch, mode: Mode, caller: Caller
) -> None:
    world = ready(mode)
    number, send = editor(world, caller)
    if caller == "publishing":
        world.container.tracking_service(dry_run=False).check_bill(
            world.bill(number), publish=False
        )
    original = world.publisher.publish_prepared
    attempted: list[int] = []

    def inspect(saved: Publication) -> PublishResult:
        if saved.kind is PublicationKind.MESSAGE_PART:
            assert saved.id is not None, "the physical edit is saved before the API call"
            attempted.append(saved.id)
        return original(saved)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)
    world.publisher.fail_on.add(number)
    if caller == "publishing":
        world.run(discover=False, analyze=False)
    else:
        assert not send()
        world.container.tracking_service(dry_run=False).check_updates(keep_delivery_attempts=True)

    saved = latest(world, number)
    assert attempted == [saved.id]
    assert saved.status is PublicationStatus.FAILED and saved.attempts == 1
    world.publisher.fail_on.clear()

    result = world.run(discover=False, analyze=False)

    assert attempted == [saved.id, saved.id]
    assert latest(world, number).status is PublicationStatus.SENT
    assert result.cards_refreshed == 1
    assert owned(world, number).rendered_sha256 == saved.rendered_sha256


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_poster_freezes_one_reader_day_before_print_fetch_crosses_midnight(
    monkeypatch: pytest.MonkeyPatch, mode: Mode
) -> None:
    world = ready(mode)
    world.clock.current = dt.datetime(2026, 9, 19, 21, 59, 30, tzinfo=dt.UTC)
    before, after = dt.date(2026, 9, 19), dt.date(2026, 9, 20)
    world.repo.save_act(TERM, "3039", act(entry_into_force=after))
    original = world.gateway.get_print
    calls: list[bool] = []

    def print_info(term: int, number: str) -> PrintInfo:
        calls.append(True)
        world.clock.advance(minutes=2)
        return original(term, number)

    monkeypatch.setattr(world.gateway, "get_print", print_info)

    assert world.container.poster(dry_run=False).rerender_card(world.bill("3039"), card(world))

    saved = latest(world, "3039")
    assert calls == [True]
    assert saved.delivery is not None, "the saved edit retains its one reader day"
    assert saved.delivery.today == before
    assert saved.rendered_sha256 == world.publisher.card_digest(world.bill("3039"), today=before)
    assert saved.rendered_sha256 != world.publisher.card_digest(world.bill("3039"), today=after)
    assert (
        saved.delivery.rendered_text
        == world.publisher.render_card_parts(
            world.bill("3039"), original(TERM, "3039"), minimum=1, today=before
        )[0]
    )
