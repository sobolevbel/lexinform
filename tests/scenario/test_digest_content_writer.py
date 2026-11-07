from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest

from lexinform.errors import DeliveryUncertainError
from lexinform.models import (
    ContentSnapshot,
    ContentVersion,
    Digest,
    DigestPart,
    Publication,
    PublicationStatus,
)
from lexinform.models.digest_content import DigestContentPlan
from lexinform.ports import PublishResult
from lexinform.services.content_inspection import inspect_content
from tests.checkpoint_state import saved_state
from tests.scenario.test_card_remote_checkpoints import connect, git
from tests.scenario.test_digest_remote_checkpoints import (
    REF,
    pending_event,
    saved_digest,
    service_for,
)


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_digest_writer_normal_freeze_is_atomic_before_each_reader_effect(
    mode: Literal["off", "shadow", "on"], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    world, source = pending_event(mode)
    remote = connect(world, tmp_path)
    service = service_for(world)
    original = world.publisher.publish_prepared
    calls: list[int] = []
    assert source.id is not None, "the normal tracker creates this digest event"
    original_now = world.clock.now

    def advancing() -> datetime:
        value = original_now()
        world.clock.advance(microseconds=1)
        return value

    def parts(_week: Digest, *, draft: bool = False) -> tuple[DigestPart, ...]:
        return (
            DigestPart(text="Introduction"),
            DigestPart(text="Event", event_ids=(source.id,) if mode == "on" else ()),
            DigestPart(text="Conclusion"),
        )

    def send(publication: Publication) -> PublishResult:
        assert publication.id is not None, "normal digest effect has a durable physical identity"
        with saved_state(git(remote, "show", "state:state.sql")) as saved:
            version = saved.repo.get_publication_content(publication.id)
            assert version is not None and version.digest_profile == "digest-content-v1"
            snapshot = saved.repo.get_content_snapshot(version.plan_snapshot_id)
            assert snapshot is not None, (
                "the first remote checkpoint includes the full frozen input"
            )
            plan = DigestContentPlan.model_validate_json(snapshot.payload_json)
            assert len(plan.parts) == 3
            assert all(
                saved.repo.get_publication_content(p.publication_id) == version for p in plan.parts
            )
            current = saved.repo.publication_by_id(publication.id)
            assert current is not None and current.status is PublicationStatus.PENDING
        calls.append(publication.id)
        return original(publication)

    monkeypatch.setattr(world.publisher, "render_digest_parts", parts)
    monkeypatch.setattr(world.publisher, "publish_prepared", send)
    monkeypatch.setattr(world.clock, "now", advancing)
    assert service.publish(REF).published
    root = saved_digest(world)
    version = world.repo.get_publication_content(root.id or 0)
    assert version is not None and len(calls) == 3
    history = inspect_content(world.repo, root)
    assert history is not None and history.current_id == version.content_id
    world.repo.prune_history(
        before=world.clock.now() + timedelta(days=180),
        memo_before=world.clock.now() + timedelta(days=180),
    )
    exact = world.repo.dump()
    with saved_state(exact) as first:
        assert first.repo.dump() == exact
        with saved_state(first.repo.dump()) as second:
            assert second.repo.dump() == exact
            assert second.repo.get_publication_content(root.id or 0) == version
    assert not service.publish(REF).published and len(calls) == 3


def test_digest_writer_unknown_part_restores_full_history_without_resend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    world, source = pending_event()
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared
    calls = 0
    assert source.id is not None, "the queued digest has its source event"

    def parts(_week: Digest, *, draft: bool = False) -> tuple[DigestPart, ...]:
        return (DigestPart(text="Introduction"), DigestPart(text="Event", event_ids=(source.id,)))

    def send(publication: Publication) -> PublishResult:
        nonlocal calls
        calls += 1
        result = original(publication)
        if calls == 2:
            raise DeliveryUncertainError("lost digest receipt")
        return result

    monkeypatch.setattr(world.publisher, "render_digest_parts", parts)
    monkeypatch.setattr(world.publisher, "publish_prepared", send)
    service = service_for(world)
    with pytest.raises(DeliveryUncertainError):
        service.publish(REF)
    root = saved_digest(world)
    with saved_state(git(remote, "show", "state:state.sql")) as first:
        version = first.repo.get_publication_content(root.id or 0)
        assert version is not None
        history = inspect_content(first.repo, root)
        assert history is not None and history.current_id is None
        with saved_state(first.repo.dump()) as second:
            assert second.repo.get_publication_content(root.id or 0) == version
    assert not service.publish(REF).published
    assert calls == 2


def test_digest_history_budget_yields_to_the_existing_freeze(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    world, _source = pending_event()
    service = service_for(world)
    week = service.build(REF)
    assert week.cards, "the fixture includes the original published card"
    large = week.model_copy(
        update={"cards": (week.cards[0].model_copy(update={"title": "x" * 70_000}),)}
    )
    monkeypatch.setattr(service, "build", lambda *args, **kwargs: large)
    monkeypatch.setattr(
        world.publisher,
        "render_digest_parts",
        lambda *args, **kwargs: (DigestPart(text="Bounded rendered digest"),),
    )
    assert service.publish(REF).published
    root = saved_digest(world)
    assert world.repo.get_publication_content(root.id or 0) is None
    assert root.delivery is not None and root.delivery.rendered_text == "Bounded rendered digest"
    assert f"publication={root.id} reason=budget" in caplog.text


def test_repeated_digest_drafts_keep_distinct_content_and_delivery_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, _source = pending_event()
    clock_now = world.clock.now

    def advancing() -> datetime:
        value = clock_now()
        world.clock.advance(microseconds=1)
        return value

    monkeypatch.setattr(world.clock, "now", advancing)
    monkeypatch.setattr(
        world.publisher,
        "render_digest_parts",
        lambda *args, **kwargs: (
            DigestPart(text="Draft introduction"),
            DigestPart(text="Draft body"),
            DigestPart(text="Draft conclusion"),
        ),
    )
    service = service_for(world)
    assert service.draft(REF).drafted
    assert service.draft(REF).drafted
    roots = [
        p
        for p in world.repo.list_publications_between(
            world.container.drafts_channel_id(),
            since=world.clock.now() - timedelta(days=1),
            until=world.clock.now() + timedelta(days=1),
        )
        if p.kind.value == "digest" and ":part:" not in (p.ref or "")
    ]
    assert len(roots) == 2
    versions = [world.repo.get_publication_content(p.id or 0) for p in roots]
    assert all(v is not None for v in versions)
    assert versions[0] != versions[1]


def test_digest_freeze_rolls_back_all_bindings_and_events_if_history_commit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, _source = pending_event()
    service = service_for(world)
    original = world.repo.save_digest_content
    before = world.repo.dump()
    sent = len(world.publisher.sent)

    def fail(snapshot: ContentSnapshot, version: ContentVersion) -> None:
        original(snapshot, version)
        raise ValueError("injected digest freeze failure")

    monkeypatch.setattr(world.repo, "save_digest_content", fail)
    with pytest.raises(ValueError, match="injected digest freeze failure"):
        service.publish(REF)
    assert world.repo.dump() == before
    assert len(world.publisher.sent) == sent


def test_digest_unknown_language_records_a_bounded_omission(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    world, _source = pending_event()
    monkeypatch.setattr(type(world.publisher), "language", property(lambda self: None))
    assert service_for(world).publish(REF).published
    root = saved_digest(world)
    assert world.repo.get_publication_content(root.id or 0) is None
    assert f"publication={root.id} reason=language" in caplog.text


def test_digest_history_part_bound_records_a_bounded_omission(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    world, _source = pending_event()
    monkeypatch.setattr(
        world.publisher,
        "render_digest_parts",
        lambda *args, **kwargs: tuple(DigestPart(text=f"Part {index}") for index in range(17)),
    )
    assert service_for(world).publish(REF).published
    root = saved_digest(world)
    assert world.repo.get_publication_content(root.id or 0) is None
    assert f"publication={root.id} reason=invalid" in caplog.text
