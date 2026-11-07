from datetime import timedelta
from typing import Literal

import pytest

from lexinform.errors import DeliveryUncertainError
from lexinform.models import ContentVersion, PublicationStatus
from lexinform.ports import PublishResult
from lexinform.services import content_history
from lexinform.services.content_inspection import inspect_content
from lexinform.services.tracking_result import TrackingResult
from tests.checkpoint_state import saved_state
from tests.scenario.test_group_remote_checkpoints import flush, queued_world, rows


@pytest.mark.parametrize("outcome", ["sent", "unknown"])
def test_first_group_freeze_records_history_before_the_single_shared_send(
    outcome: Literal["sent", "unknown"], monkeypatch: pytest.MonkeyPatch
) -> None:
    world, identities = queued_world("updates")
    publish = world.publisher.publish_updates
    offered: list[ContentVersion] = []

    def send(text: str) -> PublishResult:
        with saved_state(world.repo.dump()) as checkpoint:
            root, second = rows(checkpoint, identities)
            assert root.status is second.status is PublicationStatus.PENDING
            saved = checkpoint.repo.get_publication_content(identities[0])
            assert saved is not None and saved.grouped_stage_evidence is not None, (
                "the group freeze is durable before the fake Telegram boundary"
            )
            assert tuple(item.publication_id for item in saved.grouped_stage_evidence.members) == (
                identities
            )
            assert saved.rendered_text == text
            offered.append(saved)
        result = publish(text)
        if outcome == "unknown":
            raise DeliveryUncertainError("accepted but response lost")
        return result

    monkeypatch.setattr(world.publisher, "publish_updates", send)
    if outcome == "unknown":
        with pytest.raises(DeliveryUncertainError):
            flush(world, "updates", identities)
        world.container.delivery_service(dry_run=False).run()
    else:
        flush(world, "updates", identities)
        expiry = world.clock.now() + timedelta(days=1)
        world.repo.prune_history(before=expiry, memo_before=expiry)
        assert all(member.delivery is None for member in rows(world, identities))

    (saved,) = offered
    dump = world.repo.dump()
    for _ in range(2):
        with saved_state(dump) as restored:
            root, other = rows(restored, identities)
            assert root.status.value == other.status.value == outcome
            assert root.message_id == other.message_id
            assert restored.repo.list_content_versions(identities[0]) == [saved]
            history = inspect_content(restored.repo, root)
            assert history is not None and history.integrity == "valid"
            assert history.current_id == (saved.content_id if outcome == "sent" else None)
            assert restored.repo.dump() == dump


@pytest.mark.parametrize("reason", ["budget", "anchor"])
def test_unqualified_group_still_sends_without_history(
    reason: Literal["budget", "anchor"], monkeypatch: pytest.MonkeyPatch
) -> None:
    world, identities = queued_world("updates")
    before = len(world.publisher.sent)
    if reason == "budget":
        monkeypatch.setattr(content_history, "MAX_GROUPED_STAGE_CONTENT_BYTES", 1)
    else:
        monkeypatch.setattr(world.repo, "card_aliases", lambda channel_id, message_id: [])

    flush(world, "updates", identities)

    root, second = rows(world, identities)
    assert root.status is second.status is PublicationStatus.SENT
    assert root.message_id == second.message_id
    assert len(world.publisher.sent) == before + 1
    assert world.repo.list_content_versions(identities[0]) == []


def test_history_failure_rolls_back_the_group_freeze_before_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, identities = queued_world("updates")
    before = len(world.publisher.sent)

    def reject(version: ContentVersion) -> None:
        raise RuntimeError("history checkpoint failed")

    monkeypatch.setattr(world.repo, "save_content_version", reject)

    with pytest.raises(RuntimeError, match="history checkpoint failed"):
        flush(world, "updates", identities)

    assert len(world.publisher.sent) == before
    for member in rows(world, identities):
        assert member.status is PublicationStatus.QUEUED
        assert member.delivery is not None and not member.delivery.update_group
    assert world.repo.list_content_versions(identities[0]) == []


@pytest.mark.parametrize("original_writer", [False, True])
def test_definite_group_retry_does_not_rewrite_or_backfill_history(
    original_writer: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, identities = queued_world("updates")

    def failed(text: str) -> PublishResult:
        raise RuntimeError("definite rejection")

    with monkeypatch.context() as patch:
        patch.setattr(world.publisher, "publish_updates", failed)
        if not original_writer:
            patch.setattr(content_history, "MAX_GROUPED_STAGE_CONTENT_BYTES", 1)
        flush(world, "updates", identities)

    root, second = rows(world, identities)
    assert root.status is second.status is PublicationStatus.FAILED
    prior = world.repo.get_publication_content(identities[0])
    assert (prior is not None) == original_writer

    poster = world.container.poster(dry_run=False)
    poster.start_run()
    assert poster.retry_update_group(root, TrackingResult())

    assert world.repo.get_publication_content(identities[0]) == prior
    assert len(world.repo.list_content_versions(identities[0])) == int(original_writer)
    assert all(member.status is PublicationStatus.SENT for member in rows(world, identities))
