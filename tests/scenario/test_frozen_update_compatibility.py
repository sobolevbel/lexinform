import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal, Never

import pytest

from lexinform.models import (
    DeliveryPlan,
    Publication,
    PublicationKind,
    PublicationStatus,
    StatusChange,
)
from lexinform.ports import PublishResult
from tests.harness import CHANNEL, TERM, World, act

Mode = Literal["off", "shadow", "on"]
HTML = "<b>Сохранённое изменение</b>\nСрок 30 дней, не 60; старые заявления."
SAVED_DATE = dt.date(2026, 9, 7)


def stored_update(
    mode: Mode,
    *,
    edit: bool = False,
    status: PublicationStatus = PublicationStatus.FAILED,
) -> tuple[World, StatusChange, int, DeliveryPlan]:
    world = World(reader_attention=mode)
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    change = StatusChange(
        term=TERM,
        number="3039",
        old_fingerprint="before-saved-update",
        new_fingerprint="saved-update",
        detected_at=world.clock.now(),
        new_stages=[],
        content_changed=True,
    )
    change_id = world.repo.add_status_change(change)
    assert change_id is not None, "the fixture stores a previously unseen change"
    change.id = change_id
    plan = DeliveryPlan(
        bill_json=world.bill("3039").model_dump_json(),
        change_json=change.model_dump_json(),
        rendered_text=HTML,
        policy_version="legacy",
        reply_to=777,
        edit_message_id=444 if edit else None,
        today=SAVED_DATE,
    )
    identity = world.repo.create_publication(
        Publication(
            term=TERM,
            number="3039",
            kind=PublicationKind.STATUS_UPDATE,
            channel_id=CHANNEL,
            status=status,
            status_change_id=change_id,
            created_at=world.clock.now(),
            delivery=plan,
        )
    )
    world.repo.mark_publication(
        identity, PublicationStatus.FAILED, error="definite prior rejection"
    )
    if status is not PublicationStatus.FAILED:
        world.repo.mark_publication(identity, status, count_attempt=False)
    card = world.publication("3039")
    assert card is not None and card.id is not None, "the initial run published the bill's card"
    world.repo.mark_publication(
        card.id, PublicationStatus.SENT, message_id=999, count_attempt=False
    )
    world.clock.advance(days=30)
    world.repo.restore(world.repo.dump())
    return world, change, identity, plan


def refuse_render(*args: object, **kwargs: object) -> Never:
    raise AssertionError("a saved physical update must not be rendered again")


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("edit", [False, True])
def test_short_saved_update_uses_its_html_target_and_date_before_a_new_producer(
    monkeypatch: pytest.MonkeyPatch, mode: Mode, edit: bool
) -> None:
    world, change, identity, plan = stored_update(mode, edit=edit)
    publisher = world.publisher
    original = publisher.publish_prepared
    prepared: list[int] = []
    before_sent, before_edits = len(publisher.sent), len(publisher.edited)
    monkeypatch.setattr(publisher, "render_delivery", refuse_render)
    monkeypatch.setattr(world.formatter, "status_update", refuse_render)

    def send(publication: Publication) -> PublishResult:
        assert publication.id == identity, "retry preserves its saved publication identity"
        assert publication.delivery == plan, "retry preserves the exact saved plan after restore"
        assert publication.delivery is not None, "the saved plan belongs to this physical update"
        assert not publication.delivery.continuation_ids
        assert publication.delivery.today == SAVED_DATE
        prepared.append(identity)
        return original(publication)

    monkeypatch.setattr(publisher, "publish_prepared", send)
    poster = world.container.poster(dry_run=False)
    card = poster.card(world.bill("3039"))
    assert card is not None and card.message_id == 999, "the current card differs from saved reply"

    assert poster.status_update(world.bill("3039"), change)
    assert poster.status_update(world.bill("3039"), change)

    assert prepared == [identity]
    row = world.repo.publication_by_id(identity)
    assert row is not None and row.status is PublicationStatus.SENT, (
        "a successful prepared delivery settles the original publication"
    )
    assert row.delivery == plan
    assert poster.deliver(row)
    if edit:
        assert len(publisher.sent) == before_sent
        assert len(publisher.edited) == before_edits + 1
        outgoing, target = publisher.edited[-1]
        assert outgoing.kind is PublicationKind.STATUS_UPDATE and outgoing.text == HTML
        assert target == row.message_id == plan.edit_message_id == 444
    else:
        assert len(publisher.sent) == before_sent + 1
        assert len(publisher.edited) == before_edits
        outgoing = publisher.sent[-1]
        assert outgoing.kind is PublicationKind.STATUS_UPDATE and outgoing.text == HTML
        assert outgoing.reply_to == plan.reply_to == 777
        assert publisher.texts(PublicationKind.STATUS_UPDATE) == [HTML]
    assert prepared == [identity]


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize(
    "status",
    [PublicationStatus.PENDING, PublicationStatus.UNKNOWN, PublicationStatus.DISMISSED],
)
def test_saved_update_reader_does_not_retry_uncertain_or_dismissed_work(
    monkeypatch: pytest.MonkeyPatch, mode: Mode, status: PublicationStatus
) -> None:
    world, change, identity, plan = stored_update(mode, status=status)
    before_sent, before_edits = len(world.publisher.sent), len(world.publisher.edited)
    monkeypatch.setattr(world.publisher, "render_delivery", refuse_render)
    monkeypatch.setattr(world.formatter, "status_update", refuse_render)
    poster = world.container.poster(dry_run=False)

    assert not poster.status_update(world.bill("3039"), change)

    row = world.repo.publication_by_id(identity)
    assert row is not None and row.status is status, (
        "the saved delivery resolution stays authoritative"
    )
    assert not poster.deliver(row)
    assert row.delivery == plan
    assert len(world.publisher.sent) == before_sent
    assert len(world.publisher.edited) == before_edits


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_saved_short_update_commits_only_its_saved_held_changes_and_act_notice(
    monkeypatch: pytest.MonkeyPatch, mode: Mode
) -> None:
    world, _, _, _ = stored_update(mode)
    held: list[tuple[int, int]] = []

    def hold(label: str) -> None:
        change_id = world.repo.add_status_change(
            StatusChange(
                term=TERM,
                number="3039",
                old_fingerprint=None,
                new_fingerprint=f"held-{label}",
                new_stages=[],
                detected_at=world.clock.now(),
            )
        )
        assert change_id is not None, "each held fixture change has a distinct fingerprint"
        held.append(
            (
                change_id,
                world.repo.create_publication(
                    Publication(
                        term=TERM,
                        number="3039",
                        kind=PublicationKind.STATUS_UPDATE,
                        channel_id=CHANNEL,
                        status=PublicationStatus.SKIPPED,
                        status_change_id=change_id,
                        created_at=world.clock.now(),
                    )
                ),
            )
        )

    hold("included")
    change = StatusChange(
        term=TERM,
        number="3039",
        old_fingerprint="before-with-act",
        new_fingerprint="saved-with-act",
        new_stages=[],
        detected_at=world.clock.now(),
        content_changed=True,
    )
    change.id = world.repo.add_status_change(change)
    assert change.id is not None, "this update has a distinct saved change identity"
    plan = DeliveryPlan(
        bill_json=world.bill("3039").model_copy(update={"act": act()}).model_dump_json(),
        change_json=change.model_dump_json(),
        rendered_text=HTML,
        policy_version="legacy",
        reply_to=777,
        today=SAVED_DATE,
        with_act=True,
        held_change_ids=(held[0][0],),
    )
    identity = world.repo.create_publication(
        Publication(
            term=TERM,
            number="3039",
            kind=PublicationKind.STATUS_UPDATE,
            channel_id=CHANNEL,
            status=PublicationStatus.FAILED,
            status_change_id=change.id,
            created_at=world.clock.now(),
            delivery=plan,
        )
    )
    world.repo.mark_publication(
        identity, PublicationStatus.FAILED, error="definite prior rejection"
    )
    world.clock.advance(hours=1)
    hold("later")
    later = world.repo.publication_by_id(held[1][1])
    atomic = world.repo.atomic
    committed: list[bool] = []

    @contextmanager
    def observe_commit() -> Iterator[None]:
        with atomic():
            yield
        root = world.repo.publication_by_id(identity)
        if root is not None and root.status is PublicationStatus.SENT:
            included = world.repo.publication_by_id(held[0][1])
            notice = world.publication("3039", PublicationKind.ACT_PUBLISHED)
            committed.append(
                included is not None
                and included.status is PublicationStatus.SENT
                and notice is not None
                and notice.status is PublicationStatus.SENT
                and included.message_id == notice.message_id == root.message_id
                and world.repo.publication_by_id(held[1][1]) == later
            )

    monkeypatch.setattr(world.repo, "atomic", observe_commit)
    monkeypatch.setattr(world.publisher, "render_delivery", refuse_render)
    monkeypatch.setattr(world.formatter, "status_update", refuse_render)
    before = len(world.publisher.sent)

    assert world.container.poster(dry_run=False).status_update(world.bill("3039"), change)

    assert committed == [True]
    assert len(world.publisher.sent) == before + 1
    assert world.publisher.sent[-1].text == HTML
    assert world.publisher.sent[-1].reply_to == 777
    row = world.repo.publication_by_id(identity)
    assert row is not None and row.delivery == plan, "receipt completion preserves saved work"
