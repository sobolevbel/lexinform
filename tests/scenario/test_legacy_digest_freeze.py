from pathlib import Path
from typing import Literal

import pytest

from lexinform.errors import StateCheckpointError
from lexinform.models import (
    DIGEST_NUMBER,
    DIGEST_TERM,
    DeliveryPlan,
    Digest,
    DigestPart,
    Publication,
    PublicationKind,
    PublicationStatus,
)
from lexinform.ports import PublishResult
from lexinform.telegram_text import MESSAGE_LIMIT, split_html, utf16_length
from tests.harness import CHANNEL, World
from tests.scenario.test_card_remote_checkpoints import (
    Mode,
    RunnerLost,
    connect,
    git,
    reject_checkpoint,
    restored,
)
from tests.scenario.test_commands import _commands_only
from tests.scenario.test_digest_remote_checkpoints import REF, pending_event, service_for

Body = Literal["none", "empty", "html"]
SAVED = "<b>Сохранённый дайджест</b>\nСрок 30 дней; 200 PLN; старые заявления."


def legacy_digest(
    mode: Mode, body: Body, *, queued: bool = False, attempts: int = 1, text: str = SAVED
) -> tuple[World, int, Publication]:
    world, source = pending_event(mode)
    identity = world.repo.create_publication(
        Publication(
            term=DIGEST_TERM,
            number=DIGEST_NUMBER,
            kind=PublicationKind.DIGEST,
            channel_id=CHANNEL,
            status=PublicationStatus.QUEUED,
            ref=REF,
            created_at=world.clock.now(),
            delivery=None
            if body == "none"
            else DeliveryPlan(bill_json="", rendered_text=text if body == "html" else ""),
        )
    )
    for _ in range(attempts):
        world.repo.mark_publication(identity, PublicationStatus.FAILED)
    if queued:
        world.repo.mark_publication(identity, PublicationStatus.QUEUED, count_attempt=False)
    else:
        world.repo.mark_publication(identity, PublicationStatus.FAILED, count_attempt=False)
    return world, identity, source


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("body", ["none", "empty", "html"])
@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("attempts", [0, 1])
def test_legacy_reader_digest_freezes_its_chosen_future_payload_before_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: Mode,
    body: Body,
    queued: bool,
    attempts: int,
) -> None:
    world, identity, source = legacy_digest(mode, body, queued=queued, attempts=attempts)
    original = world.repo.publication_by_id(identity)
    assert original is not None, "legacy_digest stores the root before rendering"
    service = service_for(world)
    rebuilt = world.publisher.render_digest_parts(service.build(REF))
    assert rebuilt[0].text == world.formatter.digest(service.build(REF)).text
    expected = SAVED if body == "html" else rebuilt[0].text
    assert rebuilt[0].text != SAVED
    remote = connect(world, tmp_path)
    service = service_for(world)
    send = world.publisher.publish_prepared
    calls: list[int] = []
    renders: list[Digest] = []
    render = world.publisher.render_digest_parts

    def render_once(week: Digest, *, draft: bool = False) -> tuple[DigestPart, ...]:
        assert body != "html", "saved HTML is authoritative even if the current digest differs"
        renders.append(week)
        return render(week, draft=draft)

    def inspect(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        saved = durable.repo.publication_by_id(identity)
        assert saved is not None and saved.delivery is not None, "future intent precedes the API"
        assert saved.status is PublicationStatus.PENDING and saved.attempts == attempts
        assert saved.created_at == original.created_at
        assert saved.delivery.rendered_text == expected
        assert saved.delivery.digest_parts == (identity,) and saved.delivery.digest_ref == REF
        assert saved.delivery.reply_to is None and not saved.delivery.approve_label
        assert durable.repo.digest_owner(source.id or 0) == (None if body == "html" else identity)
        calls.append(identity)
        return send(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)
    monkeypatch.setattr(world.publisher, "render_digest_parts", render_once)

    result = service.publish(REF)

    assert result.published and calls == [identity]
    assert len(renders) == int(body != "html")
    saved = world.repo.publication_by_id(identity)
    assert saved is not None and saved.attempts == attempts
    assert world.publisher.sent[-1].text == expected and world.publisher.sent[-1].reply_to is None
    event = world.repo.publication_by_id(source.id or 0)
    assert event is not None and event.status is (
        PublicationStatus.QUEUED if body == "html" else PublicationStatus.SENT
    )
    assert not world.llm.contexts


def test_oversized_saved_html_freezes_all_parts_and_delays_its_known_whole_body_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = "<b>Срок 30 дней; 200 PLN; старые заявления.</b>\n" * 250
    world, identity, source = legacy_digest("off", "html", queued=True, attempts=0, text=text)
    assert source.id is not None, "pending_event stores the explicit source identity"
    source_id = source.id
    root = world.repo.publication_by_id(identity)
    assert root is not None and root.delivery is not None, "legacy_digest stores saved HTML"
    assert world.repo.replace_unattempted_delivery(
        identity, root.delivery.model_copy(update={"digest_event_ids": (source.id,)})
    )
    world.repo.reserve_digest_events(identity, (source.id,))
    world.repo.mark_publication(identity, PublicationStatus.FAILED)
    expected = split_html(text)
    assert len(expected) > 1 and all(utf16_length(part) <= MESSAGE_LIMIT for part in expected)
    remote = connect(world, tmp_path)
    send = world.publisher.publish_prepared
    calls: list[int] = []

    def inspect(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        saved = durable.repo.publication_by_id(identity)
        assert saved is not None and saved.delivery is not None, "all parts share one saved root"
        members = saved.delivery.digest_parts
        assert len(members) == len(expected) and members[0] == identity
        for index, member_id in enumerate(members):
            part = durable.repo.publication_by_id(member_id)
            assert part is not None and part.delivery is not None, (
                "every physical future part exists"
            )
            assert part.delivery.rendered_text == expected[index]
            assert part.delivery.digest_event_ids == (
                (source.id,) if index == len(members) - 1 else ()
            )
        assert durable.repo.digest_owner(source_id) == members[-1]
        event = durable.repo.publication_by_id(source_id)
        assert event is not None and event.status is PublicationStatus.QUEUED
        calls.append(publication.id or 0)
        return send(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)

    assert service_for(world).publish(REF).published

    assert len(calls) == len(expected)
    event = world.repo.publication_by_id(source.id)
    last = world.repo.publication_by_id(calls[-1])
    assert event is not None and last is not None
    assert event.status is PublicationStatus.SENT and event.message_id == last.message_id


@pytest.mark.parametrize("body", ["none", "html"])
@pytest.mark.parametrize("boundary", [1, 2])
def test_legacy_digest_checkpoint_failure_preserves_only_durable_future_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: Body, boundary: int
) -> None:
    world, identity, _ = legacy_digest("off", body)
    original = world.repo.publication_by_id(identity)
    remote = connect(world, tmp_path)
    posts = len(world.publisher.sent)
    reject_checkpoint(monkeypatch, boundary)

    with pytest.raises(StateCheckpointError, match="reader checkpoint failure"):
        service_for(world).publish(REF)

    assert len(world.publisher.sent) - posts == int(boundary == 2)
    fresh = restored(remote, world, tmp_path)
    saved = fresh.repo.publication_by_id(identity)
    assert saved is not None, "the physical root predates the attempted send"
    if boundary == 1:
        assert saved == original
    else:
        assert saved.status is PublicationStatus.PENDING and saved.delivery is not None
        assert saved.attempts == 1 and saved.delivery.digest_parts == (identity,)
        fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
        service_for(fresh).run()
        assert len(world.publisher.sent) - posts == 1


@pytest.mark.parametrize("action", ["retry", "confirm 777", "dismiss checked absence"])
def test_frozen_legacy_digest_recovers_without_rebuild_and_preserves_unknown_event_membership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    world, identity, source = legacy_digest("off", "html")
    remote = connect(world, tmp_path)
    send = world.publisher.publish_prepared

    def lost(publication: Publication) -> PublishResult:
        send(publication)
        raise RunnerLost()

    monkeypatch.setattr(world.publisher, "publish_prepared", lost)
    with pytest.raises(RunnerLost):
        service_for(world).publish(REF)
    fresh = restored(remote, world, tmp_path)
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    posts = len(world.publisher.sent)

    def forbid(*args: object, **kwargs: object) -> tuple[DigestPart, ...]:
        raise AssertionError("saved digest parts must not be rendered again")

    monkeypatch.setattr(fresh.publisher, "render_digest_parts", forbid)
    monkeypatch.setattr(fresh.publisher, "publish_prepared", send)
    incoming = fresh.command(f"/delivery {identity} {action}")
    _commands_only(fresh)
    assert fresh.repo.delivery_resolutions(identity)[-1].command == incoming
    fresh.clock.advance(days=100)
    service_for(fresh).run()

    assert len(world.publisher.sent) - posts == int(action == "retry")
    event = fresh.repo.publication_by_id(source.id or 0)
    assert event is not None and event.status is PublicationStatus.QUEUED
    assert fresh.repo.digest_owner(source.id or 0) is None
    if action == "retry":
        assert world.publisher.sent[-1].text == SAVED


@pytest.mark.parametrize("body", ["none", "html"])
def test_exhausted_legacy_root_keeps_its_budget_and_can_be_audited_after_freeze(body: Body) -> None:
    world, identity, _ = legacy_digest("off", body, attempts=3)
    posts = len(world.publisher.sent)

    assert service_for(world).publish(REF).failed

    saved = world.repo.publication_by_id(identity)
    assert saved is not None and saved.delivery is not None, (
        "the exhausted future intent is explicit"
    )
    assert saved.status is PublicationStatus.FAILED and saved.attempts == 3
    assert len(world.publisher.sent) == posts
    world.command(f"/delivery {identity} retry")
    _commands_only(world)
    assert len(world.publisher.sent) == posts + 1


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_legacy_technical_draft_retains_its_existing_approval_callback(mode: Mode) -> None:
    world, source = pending_event(mode)
    channel = world.container.drafts_channel_id()
    identity = world.repo.create_publication(
        Publication(
            term=DIGEST_TERM,
            number=DIGEST_NUMBER,
            kind=PublicationKind.DIGEST,
            channel_id=channel,
            status=PublicationStatus.FAILED,
            ref=REF,
            created_at=world.clock.now(),
        )
    )
    world.repo.mark_publication(identity, PublicationStatus.FAILED)

    assert service_for(world).draft(REF).drafted

    saved = world.repo.publication_by_id(identity)
    assert saved is not None and saved.delivery is None and saved.attempts == 1
    assert world.publisher.sent[-1].action is not None
    assert world.publisher.sent[-1].action[1] == f"digest:{REF}"
    assert world.repo.digest_owner(source.id or 0) is None
