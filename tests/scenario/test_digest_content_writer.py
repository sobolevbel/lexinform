import sqlite3
from contextlib import closing
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
from lexinform.models.digest_content import (
    DigestContentPlan,
    DigestRenderedPart,
    DigestRenderedPlan,
)
from lexinform.ports import PublishResult
from lexinform.services.content_inspection import inspect_content
from lexinform.telegram_text import MESSAGE_LIMIT, utf16_length
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
    if mode == "on":
        sent = world.repo.dump()
        with closing(sqlite3.connect(":memory:")) as db:
            db.executescript(sent)
            db.execute(
                "UPDATE publications SET status='unknown',sent_at=NULL,message_id=NULL WHERE id=?",
                (calls[0],),
            )
            corrupt = "\n".join([*db.iterdump(), sent.splitlines()[-1]])
        with pytest.raises(ValueError, match="invalid_content_history") as failure:
            world.repo.restore(corrupt)
        assert str(failure.value.__cause__) == "digest_content_receipt_prefix"
        assert world.repo.dump() == sent
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


@pytest.mark.parametrize("failure", ["none", "snapshot", "transport"])
def test_digest_history_budget_retains_native_parts_through_cleanup(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    world, event = pending_event()
    service = service_for(world)
    week = service.build(REF)
    assert week.cards, "the fixture includes the original published card"
    large = week.model_copy(
        update={"cards": (week.cards[0].model_copy(update={"title": "x" * 70_000}),)}
    )
    monkeypatch.setattr(service, "build", lambda *args, **kwargs: large)
    (native,) = world.publisher.render_digest_parts(large)
    assert utf16_length(native.text) <= MESSAGE_LIMIT and native.event_ids == (event.id,)
    original_save = world.repo.save_content_snapshot
    original_send = world.publisher.publish_prepared

    def fail(snapshot: ContentSnapshot) -> None:
        original_save(snapshot)
        if '"profile":"digest-rendered-part-v1"' in snapshot.payload_json:
            raise RuntimeError("injected part snapshot failure")

    def send(publication: Publication) -> PublishResult:
        assert publication.id is not None, "the digest effect has its physical ID"
        with saved_state(world.repo.dump()) as checkpoint:
            version = checkpoint.repo.get_publication_content(publication.id)
            assert version is not None and version.digest_profile == "digest-rendered-v1"
            pending = checkpoint.repo.publication_by_id(publication.id)
            assert pending is not None and pending.status is PublicationStatus.PENDING
        return original_send(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", send)
    if failure == "snapshot":
        before = world.repo.dump()
        with monkeypatch.context() as fault:
            fault.setattr(world.repo, "save_content_snapshot", fail)
            with pytest.raises(RuntimeError, match="injected part snapshot failure"):
                service.publish(REF)
        assert world.publisher.digest_send_attempts == 0
        assert world.repo.dump() == before

    failed_version = None
    if failure == "transport":
        world.publisher.fail_digest_attempt = 1
        assert service.publish(REF).failed
        failed = saved_digest(world)
        assert failed.status is PublicationStatus.FAILED and failed.id is not None
        failed_version = world.repo.get_publication_content(failed.id)
        assert failed_version is not None
        monkeypatch.setattr(service, "build", lambda *args, **kwargs: week)

    assert service.publish(REF).published
    root = saved_digest(world)
    assert root.id is not None and root.delivery is not None
    assert root.delivery.rendered_text == native.text
    version = world.repo.get_publication_content(root.id)
    assert version is not None and version.digest_profile == "digest-rendered-v1"
    if failed_version is not None:
        assert version == failed_version
    snapshot = world.repo.get_content_snapshot(version.plan_snapshot_id)
    assert snapshot is not None
    rendered = DigestRenderedPlan.model_validate_json(snapshot.payload_json)
    assert rendered.upstream_semantics == "not_retained"
    (ref,) = rendered.parts
    part_snapshot = world.repo.get_content_snapshot(ref.snapshot_id)
    assert part_snapshot is not None
    part = DigestRenderedPart.model_validate_json(part_snapshot.payload_json)
    assert (part.text, part.event_ids) == (native.text, native.event_ids)
    expiry = world.clock.now() + timedelta(days=180)
    world.repo.prune_history(before=expiry, memo_before=expiry)
    exact = world.repo.dump()
    with saved_state(exact) as first, saved_state(exact) as second:
        assert first.repo.dump() == second.repo.dump() == exact
        assert second.repo.get_publication_content(root.id) == version
        assert second.repo.get_content_snapshot(ref.snapshot_id) == part_snapshot
        assert [source.id for source in second.repo.digest_sources(root.id)] == [event.id]
    assert not service.publish(REF).published
    assert world.publisher.digest_send_attempts == (2 if failure == "transport" else 1)


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
