from datetime import timedelta

import pytest

from lexinform.adapters.telegram import TelegramEditReceipt
from lexinform.errors import DeliveryUncertainError, StateCheckpointError
from lexinform.models import PublicationStatus
from lexinform.ports import VerifiedEditOutcome
from lexinform.services.content_inspection import inspect_content
from lexinform.services.grouped_stage_date_approval import (
    approve_grouped_stage_date,
    preview_grouped_stage_date,
)
from lexinform.services.grouped_stage_date_candidate import GroupedStageDateCorrection
from tests.checkpoint_state import saved_state
from tests.scenario.test_group_remote_checkpoints import flush, queued_world
from tests.scenario.test_grouped_telegram_receipt import CHAT_ID, MESSAGE_ID, telegram_send


@pytest.mark.parametrize("mode", ["sent", "unknown", "checkpoint", "unpinned", "queued"])
def test_grouped_date_approval_checkpoints_exact_candidate(
    mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, (root_id, _sibling_id) = queued_world("updates")
    if mode == "unpinned":
        flush(world, "updates", (root_id, _sibling_id))
    else:
        with telegram_send(world, monkeypatch, {"id": CHAT_ID}):
            flush(world, "updates", (root_id, _sibling_id))
    original = world.repo.get_publication_content(root_id)
    root = world.repo.publication_by_id(root_id)
    assert original is not None and root is not None, "the group has a frozen original"
    evidence = original.grouped_stage_evidence
    assert (
        evidence is not None
        and evidence.render_witness is not None
        and original.rendered_text is not None
        and original.language is not None
    ), "the original has exact render evidence"
    baseline = evidence.render_witness.bind(original.rendered_text, original.language)
    member = evidence.members[0]
    correction = GroupedStageDateCorrection(
        root_publication_id=root_id,
        member_publication_id=member.publication_id,
        status_change_id=member.status_change_id,
        expected_content_id=original.content_id,
        expected_sha256=baseline.sha256,
        expected_date=baseline.today - timedelta(days=4),
        corrected_date=baseline.today - timedelta(days=2),
    )
    unchanged = world.repo.dump()
    preview = preview_grouped_stage_date(world.repo, world.formatter, correction)
    assert world.repo.dump() == unchanged
    calls: list[tuple[int, int, str]] = []
    dumps: list[str] = []

    def checkpoint() -> None:
        dumps.append(world.repo.dump())
        if mode == "checkpoint":
            raise StateCheckpointError("test checkpoint failed")

    def edit(chat: int, message: int, html: str) -> VerifiedEditOutcome:
        calls.append((chat, message, html))
        assert len(dumps) == 1
        with saved_state(dumps[0]) as restored:
            history = inspect_content(restored.repo, root)
            assert history is not None and history.current_id is None
            versions = restored.repo.list_content_versions(root_id)
            assert len(versions) == 2
            revision = next(version for version in versions if version.previous_version_id)
            assert revision.rendered_text == html
        if mode == "unknown":
            raise DeliveryUncertainError("lost response")
        return TelegramEditReceipt(chat, message)

    if mode == "unpinned":
        assert preview is None
        assert (
            approve_grouped_stage_date(
                world.repo,
                world.clock,
                world.formatter,
                edit,
                checkpoint,
                correction,
                approved_sha256="0" * 64,
                chat_id=CHAT_ID,
                message_id=MESSAGE_ID,
                enabled=True,
            )
            is None
        )
        assert world.repo.dump() == unchanged and not calls and not dumps
        return
    assert preview is not None, "the original carries an exact numeric pin"
    assert (preview.chat_id, preview.message_id) == (CHAT_ID, MESSAGE_ID)

    def approve(
        *,
        enabled: bool = True,
        sha256: str = preview.candidate_sha256,
        chat_id: int = CHAT_ID,
        message_id: int = MESSAGE_ID,
    ) -> PublicationStatus | None:
        result = approve_grouped_stage_date(
            world.repo,
            world.clock,
            world.formatter,
            edit,
            checkpoint,
            correction,
            approved_sha256=sha256,
            chat_id=chat_id,
            message_id=message_id,
            enabled=enabled,
        )
        return result.status if result is not None else None

    assert (
        approve_grouped_stage_date(
            world.repo,
            world.clock,
            world.formatter,
            edit,
            None,
            correction,
            approved_sha256=preview.candidate_sha256,
            chat_id=CHAT_ID,
            message_id=MESSAGE_ID,
            enabled=True,
        )
        is None
    )
    assert approve(enabled=False) is None
    assert approve(sha256="0" * 64) is None
    assert approve(chat_id=CHAT_ID - 1) is None
    assert approve(message_id=MESSAGE_ID + 1) is None
    with world.repo.atomic():
        assert approve() is None
    assert world.repo.dump() == unchanged and not calls and not dumps
    if mode == "queued":
        with monkeypatch.context() as refused:
            refused.setattr(world.repo, "claim_grouped_stage_edit", lambda *args, **kwargs: None)
            result = approve_grouped_stage_date(
                world.repo,
                world.clock,
                world.formatter,
                edit,
                checkpoint,
                correction,
                approved_sha256=preview.candidate_sha256,
                chat_id=CHAT_ID,
                message_id=MESSAGE_ID,
                enabled=True,
            )
        assert result is not None and result.status is None
        with saved_state(world.repo.dump()) as restored:
            slot = restored.repo.publication_by_id(result.publication_id)
            assert slot is not None and slot.status is PublicationStatus.QUEUED
        assert approve() is None and not calls and not dumps
        return
    if mode == "checkpoint":
        with pytest.raises(StateCheckpointError, match="test checkpoint failed"):
            approve()
        assert not calls and len(dumps) == 1
        assert approve() is None
        return
    assert approve() is (PublicationStatus.SENT if mode == "sent" else PublicationStatus.UNKNOWN)
    assert approve() is None
    assert preview_grouped_stage_date(world.repo, world.formatter, correction) is None
    assert calls == [(CHAT_ID, MESSAGE_ID, preview.html)]
    assert len(dumps) == 2
    with saved_state(dumps[-1]) as restored:
        history = inspect_content(restored.repo, root)
        assert history is not None
        assert (history.current_id is not None) is (mode == "sent")
