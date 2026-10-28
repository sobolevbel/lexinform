from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

from lexinform.adapters.telegram_format import length
from lexinform.errors import DeliveryUncertainError, StateCheckpointError
from lexinform.models import (
    DeliveryPlan,
    Publication,
    PublicationKind,
    PublicationStatus,
    SittingNews,
)
from lexinform.ports import PublishResult
from lexinform.services.tracking_result import TrackingResult
from tests.harness import CHANNEL, COMMITTEE_STAGES, World
from tests.scenario.test_card_remote_checkpoints import (
    Mode,
    RunnerLost,
    connect,
    git,
    reject_checkpoint,
)
from tests.scenario.test_commands import _commands_only
from tests.scenario.test_group_remote_checkpoints import restored
from tests.scenario.test_tracking_agenda import ASW, _sitting

Boundary = Literal["root_intent", "root_receipt", "final_intent", "final_receipt"]
TITLE = "Projekt ustawy o cudzoziemcach"
SAVED_HTML = "<b>Previously saved sitting</b>\nThe original target and facts stay unchanged."


@dataclass(frozen=True)
class SittingCase:
    world: World
    identity: int
    kind: PublicationKind
    card_id: int


def row(world: World, identity: int) -> Publication:
    publication = world.repo.publication_by_id(identity)
    assert publication is not None, "the real agenda watcher saves the source publication"
    return publication


def plan(world: World, identity: int) -> DeliveryPlan:
    delivery = row(world, identity).delivery
    assert delivery is not None, "the sitting retains its original delivery snapshot"
    return delivery


def queued(mode: Mode = "on", *, cancelled: bool = False) -> SittingCase:
    world = World(profile="production", reader_attention=mode)
    name = ASW.name + (
        " Komisja do spraw cudzoziemców; 200 PLN; art. 15. " * 160 if cancelled else ""
    )
    world.gateway.committees["ASW"] = ASW.model_copy(update={"name": name})
    world.add_bill("3039", TITLE, stages=COMMITTEE_STAGES)
    world.run()
    world.batch.resolve()
    world.run()
    card_id = world.card_id("3039")
    sitting = _sitting().model_copy(
        update={"room": "Sala ORYGINALNA; 200 PLN; art. 15; 🙂. " * 180}
    )
    world.gateway.committee_sittings["ASW"] = (sitting,)
    world.run(publish=False)
    kind = PublicationKind.AGENDA
    if cancelled:
        announcement = next(
            publication
            for publication in world.repo.list_open_deliveries(CHANNEL)
            if publication.kind is PublicationKind.AGENDA
        )
        assert announcement.id is not None, "the watcher persisted the original announcement"
        world.repo.mark_publication(
            announcement.id,
            PublicationStatus.SENT,
            message_id=777,
            sent_at=world.clock.now(),
            count_attempt=False,
        )
        world.gateway.committee_sittings["ASW"] = ()
        world.run(publish=False)
        kind = PublicationKind.AGENDA_CANCELLED
    source = next(
        publication
        for publication in world.repo.list_open_deliveries(CHANNEL)
        if publication.kind is kind
    )
    assert source.id is not None and source.delivery is not None, "the watcher queues the sitting"
    assert source.status is PublicationStatus.QUEUED and not source.attempts
    assert not source.delivery.rendered_text and not source.delivery.sitting_group
    assert length(world.publisher.render_sittings((SittingNews.of(kind, source.delivery),))) > 4096
    assert not world.llm.contexts
    return SittingCase(world, source.id, kind, card_id)


def flush(case: SittingCase) -> TrackingResult:
    poster = case.world.container.poster(dry_run=False)
    poster.start_run()
    poster.queue_sitting(row(case.world, case.identity))
    result = TrackingResult()
    poster.flush_sittings(result)
    return result


def expected_parts(case: SittingCase) -> tuple[str, ...]:
    text = case.world.publisher.render_sittings(
        (SittingNews.of(case.kind, plan(case.world, case.identity)),)
    )
    assert f"https://t.me/test/{case.card_id}" in text
    parts = case.world.publisher.split_message(text)
    assert len(parts) > 1, "the real sitting layout needs multiple physical Telegram messages"
    return parts


def identities(world: World, identity: int) -> tuple[int, ...]:
    return (identity, *plan(world, identity).continuation_ids)


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("direct", [False, True])
def test_every_long_sitting_part_has_remote_html_and_parent_receipt_before_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode, cancelled: bool, direct: bool
) -> None:
    case = queued(mode, cancelled=cancelled)
    world = case.world
    expected = expected_parts(case)
    card = world.publication("3039")
    assert card is not None and card.id is not None, "the initial production batch published a card"
    world.repo.mark_publication(
        card.id, PublicationStatus.SENT, message_id=999, count_attempt=False
    )
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared
    observed: list[int] = []
    before = len(world.publisher.sent)

    def send(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        source = row(durable, case.identity)
        saved = plan(durable, case.identity)
        members = identities(durable, case.identity)
        assert len(members) == len(expected)
        assert not saved.sitting_group and not saved.group_text
        assert saved.reply_to == case.card_id, "the saved card receipt remains line-link context"
        assert tuple(plan(durable, identity).rendered_text for identity in members) == expected
        assert f"https://t.me/test/{case.card_id}" in "\n".join(expected)
        current = row(durable, publication.id or 0)
        assert current.status is PublicationStatus.PENDING and not current.attempts
        assert current.delivery == publication.delivery
        if current.id == source.id:
            assert current.kind is case.kind
            assert all(
                row(durable, identity).status is PublicationStatus.QUEUED
                for identity in members[1:]
            )
        else:
            current_plan = plan(durable, current.id or 0)
            parent = row(durable, current_plan.continuation_parent or 0)
            assert parent.status is PublicationStatus.SENT and parent.message_id is not None
            assert current_plan.reply_to == parent.message_id
            assert current_plan.continuation_root == case.identity
        observed.append(publication.id or 0)
        return original(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", send)

    if direct:
        poster = world.container.poster(dry_run=False)
        poster.start_run()
        assert poster.deliver(row(world, case.identity))
    else:
        result = flush(case)
        assert result.agenda_cancelled == int(cancelled)
        assert result.agenda_posted == int(not cancelled)
    sent = world.publisher.sent[before:]
    assert tuple(message.text for message in sent) == expected
    assert sent[0].reply_to is None
    assert tuple(observed) == identities(world, case.identity)
    fresh = restored(remote, world, tmp_path)
    assert all(row(fresh, identity).status is PublicationStatus.SENT for identity in observed)
    assert not fresh.llm.contexts


@pytest.mark.parametrize(
    "boundary", ["root_intent", "root_receipt", "final_intent", "final_receipt"]
)
@pytest.mark.parametrize("cancelled", [False, True])
def test_long_sitting_checkpoint_failure_stops_later_parts_after_remote_only_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: Boundary, cancelled: bool
) -> None:
    case = queued(cancelled=cancelled)
    world = case.world
    count = len(expected_parts(case))
    at = {
        "root_intent": 1,
        "root_receipt": 2,
        "final_intent": count * 2 - 1,
        "final_receipt": count * 2,
    }[boundary]
    remote = connect(world, tmp_path)
    before = len(world.publisher.sent)
    reject_checkpoint(monkeypatch, at)

    with pytest.raises(StateCheckpointError, match="reader checkpoint failure"):
        flush(case)

    assert len(world.publisher.sent) - before == at // 2
    fresh = restored(remote, world, tmp_path)
    if boundary == "root_intent":
        assert not plan(fresh, case.identity).rendered_text
        assert not plan(fresh, case.identity).continuation_ids
        assert row(fresh, case.identity).status is PublicationStatus.QUEUED
        return
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    member_ids = identities(fresh, case.identity)
    affected = member_ids[0] if boundary == "root_receipt" else member_ids[-1]
    assert row(fresh, affected).status is (
        PublicationStatus.QUEUED if boundary == "final_intent" else PublicationStatus.UNKNOWN
    )
    if boundary.endswith("receipt"):
        fresh.container.delivery_service(dry_run=False).run()
        assert not fresh.publisher.sent
        assert row(fresh, affected).status is PublicationStatus.UNKNOWN


@pytest.mark.parametrize("accepted", [False, True])
@pytest.mark.parametrize("cancelled", [False, True])
def test_lost_long_sitting_root_is_unknown_and_never_automatically_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, accepted: bool, cancelled: bool
) -> None:
    case = queued(cancelled=cancelled)
    world = case.world
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared

    def lost(publication: Publication) -> PublishResult:
        if accepted:
            original(publication)
            raise DeliveryUncertainError("accepted sitting response lost")
        raise RunnerLost()

    monkeypatch.setattr(world.publisher, "publish_prepared", lost)
    with pytest.raises(DeliveryUncertainError if accepted else RunnerLost):
        flush(case)
    fresh = restored(remote, world, tmp_path)
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    before = len(fresh.publisher.sent)

    fresh.container.delivery_service(dry_run=False).run()

    assert row(fresh, case.identity).status is PublicationStatus.UNKNOWN
    assert len(fresh.publisher.sent) == before
    assert all(
        row(fresh, identity).status is PublicationStatus.QUEUED
        for identity in identities(fresh, case.identity)[1:]
    )


@pytest.mark.parametrize("cancelled", [False, True])
def test_operator_retry_keeps_the_saved_long_sitting_and_audit_after_mode_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancelled: bool
) -> None:
    case = queued(cancelled=cancelled)
    world = case.world
    remote = connect(world, tmp_path)

    def lost(publication: Publication) -> PublishResult:
        raise RunnerLost()

    with monkeypatch.context() as patch:
        patch.setattr(world.publisher, "publish_prepared", lost)
        with pytest.raises(RunnerLost):
            flush(case)
    expected = tuple(
        plan(world, identity).rendered_text for identity in identities(world, case.identity)
    )
    fresh = restored(remote, world, tmp_path, mode="off")
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    saved = plan(fresh, case.identity)
    assert saved.replayable(case.kind), "a frozen top-level sitting is auditable replayable work"
    incoming = fresh.command(f"/delivery {case.identity} retry")
    _commands_only(fresh)
    original = fresh.publisher.publish_prepared
    checked: list[int] = []

    def send(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        if publication.id == case.identity:
            history = durable.repo.delivery_resolutions(case.identity)
            assert len(history) == 1 and history[0].command == incoming
            assert plan(durable, case.identity) == saved
            assert row(durable, case.identity).status is PublicationStatus.PENDING
            checked.append(case.identity)
        return original(publication)

    monkeypatch.setattr(fresh.publisher, "publish_prepared", send)

    fresh.container.delivery_service(dry_run=False).run()

    assert checked == [case.identity]
    assert tuple(message.text for message in fresh.publisher.sent) == expected
    assert fresh.publisher.sent[0].reply_to is None
    assert all(
        row(fresh, identity).status is PublicationStatus.SENT
        for identity in identities(fresh, case.identity)
    )


@pytest.mark.parametrize("cancelled", [False, True])
def test_confirming_a_long_sitting_root_unblocks_only_its_unsent_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancelled: bool
) -> None:
    case = queued(cancelled=cancelled)
    world = case.world
    remote = connect(world, tmp_path)

    def lost(publication: Publication) -> PublishResult:
        raise RunnerLost()

    with monkeypatch.context() as patch:
        patch.setattr(world.publisher, "publish_prepared", lost)
        with pytest.raises(RunnerLost):
            flush(case)
    fresh = restored(remote, world, tmp_path)
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    incoming = fresh.command(f"/delivery {case.identity} confirm 888")
    _commands_only(fresh)
    original = fresh.publisher.publish_prepared
    checked: list[int] = []

    def send(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        root = row(durable, case.identity)
        assert root.status is PublicationStatus.SENT and root.message_id == 888
        history = durable.repo.delivery_resolutions(case.identity)
        assert len(history) == 1 and history[0].command == incoming
        assert publication.id != case.identity
        checked.append(publication.id or 0)
        return original(publication)

    monkeypatch.setattr(fresh.publisher, "publish_prepared", send)

    fresh.container.delivery_service(dry_run=False).run()

    assert tuple(checked) == identities(fresh, case.identity)[1:]
    assert fresh.publisher.sent[0].reply_to == 888
    assert not fresh.publisher.texts(case.kind)


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("attempts", [0, 1])
def test_failed_long_sitting_freezes_parts_without_rewriting_saved_context(
    mode: Mode, attempts: int
) -> None:
    case = queued(mode)
    world = case.world
    saved = plan(world, case.identity)
    expected = expected_parts(case)
    world.repo.mark_publication(case.identity, PublicationStatus.FAILED, count_attempt=False)
    for _ in range(attempts):
        world.repo.mark_publication(case.identity, PublicationStatus.FAILED)
    before = len(world.publisher.sent)

    flush(case)

    frozen = plan(world, case.identity)
    assert frozen == saved.model_copy(
        update={
            "render_origin": "legacy_snapshot",
            "rendered_text": expected[0],
            "continuation_ids": frozen.continuation_ids,
        }
    )
    assert tuple(out.text for out in world.publisher.sent[before:]) == expected
    assert world.publisher.sent[before].reply_to is None
    assert row(world, case.identity).attempts == attempts


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("attempts", [0, 1])
def test_failed_legacy_long_sitting_with_html_keeps_its_original_target(
    monkeypatch: pytest.MonkeyPatch, mode: Mode, attempts: int
) -> None:
    case = queued(mode)
    world = case.world
    saved = plan(world, case.identity).model_copy(update={"rendered_text": SAVED_HTML})
    assert world.repo.replace_unattempted_delivery(case.identity, saved), (
        "the fixture is still queued"
    )
    world.repo.mark_publication(case.identity, PublicationStatus.FAILED, count_attempt=False)
    for _ in range(attempts):
        world.repo.mark_publication(case.identity, PublicationStatus.FAILED)
    before = len(world.publisher.sent)

    def forbidden(*args: object, **kwargs: object) -> str:
        raise AssertionError("an attempted saved payload cannot be rendered again")

    monkeypatch.setattr(world.publisher, "render_delivery", forbidden)

    flush(case)

    assert plan(world, case.identity) == saved
    assert not plan(world, case.identity).continuation_ids
    assert len(world.publisher.sent) == before + 1
    assert world.publisher.sent[-1].text == SAVED_HTML
    assert saved.reply_to == case.card_id
    assert world.publisher.sent[-1].reply_to is None
