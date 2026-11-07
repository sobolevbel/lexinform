from collections.abc import Sequence
from datetime import date, timedelta
from typing import Literal
from unittest.mock import patch

import pytest

from lexinform.content_versions import content_snapshot_refs
from lexinform.errors import DeliveryUncertainError
from lexinform.models import ContentVersion, PublicationStatus, UpdateNews
from lexinform.ports import PublishResult
from lexinform.services import content_history
from lexinform.services.content_inspection import inspect_content
from tests.checkpoint_state import saved_state
from tests.harness import World
from tests.scenario.test_group_remote_checkpoints import flush, queued_world, rows


def history(world: World, identity: int) -> ContentVersion:
    version = world.repo.get_publication_content(identity)
    assert version is not None and version.grouped_stage_evidence is not None, (
        "the supported group records its original compact content"
    )
    return version


@pytest.mark.parametrize("outcome", ["sent", "unknown"])
def test_fragment_witness_uses_one_original_fake_render_and_survives_restore(
    outcome: Literal["sent", "unknown"], monkeypatch: pytest.MonkeyPatch
) -> None:
    world, identities = queued_world("updates")
    render = world.publisher.render_updates
    publish = world.publisher.publish_updates
    captured: list[ContentVersion] = []

    def observe(news: Sequence[UpdateNews], *, today: date) -> str:
        with (
            patch.object(world.formatter, "updates", wraps=world.formatter.updates) as formatted,
            patch.object(world.formatter, "updates_with_witness") as replay,
        ):
            text = render(news, today=today)
            assert formatted.call_count == 1
            replay.assert_not_called()
        return text

    def send(text: str) -> PublishResult:
        with saved_state(world.repo.dump()) as checkpoint:
            assert all(
                member.status is PublicationStatus.PENDING
                for member in rows(checkpoint, identities)
            )
            version = checkpoint.repo.get_publication_content(identities[0])
            assert version is not None and version.grouped_stage_evidence is not None, (
                "the original freeze persisted complete group history before fake Telegram"
            )
            witness = version.grouped_stage_evidence.render_witness
            assert witness is not None and version.language is not None, (
                "the exact native render supplied its optional fragment witness"
            )
            assert witness.bind(text, version.language).text == version.rendered_text
            captured.append(version)
        result = publish(text)
        if outcome == "unknown":
            raise DeliveryUncertainError("response lost after acceptance")
        return result

    monkeypatch.setattr(world.publisher, "render_updates", observe)
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

    (version,) = captured
    original = dump = world.repo.dump()
    for _ in range(2):
        with saved_state(dump) as restored:
            root, sibling = rows(restored, identities)
            assert root.status.value == sibling.status.value == outcome
            assert restored.repo.get_publication_content(identities[0]) == version
            inspected = inspect_content(restored.repo, root)
            assert inspected is not None and inspected.integrity == "valid"
            assert inspected.current_id == (version.content_id if outcome == "sent" else None)
            dump = restored.repo.dump()
            assert dump == original


@pytest.mark.parametrize("hook", ["opaque", "rewrites", "twice"])
def test_custom_publishers_keep_original_render_path_without_inferred_witness(
    hook: Literal["opaque", "rewrites", "twice"], monkeypatch: pytest.MonkeyPatch
) -> None:
    world, identities = queued_world("updates")
    render = world.publisher.render_updates
    offered: list[str] = []

    def custom(news: Sequence[UpdateNews], *, today: date) -> str:
        if hook == "opaque":
            text = "Custom HTML"
        else:
            text = render(news, today=today)
            if hook == "rewrites":
                text += "\n"
            else:
                assert render(news, today=today) == text
        offered.append(text)
        return text

    monkeypatch.setattr(world.publisher, "render_updates", custom)
    flush(world, "updates", identities)

    version = history(world, identities[0])
    assert version.grouped_stage_evidence is not None, "the ordinary compact history still exists"
    assert version.grouped_stage_evidence.render_witness is None
    assert offered == [version.rendered_text]
    assert world.publisher.sent[-1].text == offered[0]


def test_optional_witness_yields_to_original_group_content_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    example, example_ids = queued_world("updates")
    flush(example, "updates", example_ids)
    original = history(example, example_ids[0])
    assert original.rendered_text is not None, "the original group records its exact text"
    size = len(original.rendered_text.encode())
    for _, identity in content_snapshot_refs(original):
        snapshot = example.repo.get_content_snapshot(identity)
        assert snapshot is not None, "each editorial snapshot survives separately"
        size += len(snapshot.payload_json.encode())
    monkeypatch.setattr(content_history, "MAX_GROUPED_STAGE_CONTENT_BYTES", size)
    world, identities = queued_world("updates")

    flush(world, "updates", identities)

    bounded = history(world, identities[0])
    assert bounded.grouped_stage_evidence is not None, "the base record fits exactly"
    assert bounded.grouped_stage_evidence.render_witness is None
    assert bounded.rendered_text == original.rendered_text
