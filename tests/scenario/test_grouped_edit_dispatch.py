import json
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

import httpx2 as httpx
import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.telegram import TelegramBotClient, TelegramEditReceipt
from lexinform.errors import DeliveryUncertainError, StateCheckpointError
from lexinform.grouped_update_preview import GroupedUpdateRender
from lexinform.models import ContentVersion, Publication, PublicationStatus, UpdateNews
from lexinform.ports import StateCheckpoint, VerifiedEditOutcome, VerifiedEditRejection
from lexinform.services.content_inspection import inspect_content
from lexinform.services.grouped_edit_dispatch import dispatch_reserved_grouped_edit
from tests.checkpoint_state import saved_state
from tests.harness import World
from tests.scenario.test_group_remote_checkpoints import flush, queued_world, rows
from tests.scenario.test_grouped_stage_revision_reader import (
    corrected_render,
    original_fixture,
    revision_fixture,
    save_slot,
)
from tests.scenario.test_grouped_stage_revision_reservation import reserve
from tests.scenario.test_grouped_telegram_receipt import CHAT_ID, MESSAGE_ID, telegram_send


def reserved(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[World, Publication, ContentVersion, ContentVersion, GroupedUpdateRender, int]:
    world, ids = queued_world("updates")
    prior_render = world.publisher.render_updates
    news: list[tuple[UpdateNews, ...]] = []

    def capture(items: Sequence[UpdateNews], *, today: date) -> str:
        news.append(tuple(items))
        return prior_render(items, today=today)

    monkeypatch.setattr(world.publisher, "render_updates", capture)
    with telegram_send(world, monkeypatch, {"id": CHAT_ID}) as calls:
        flush(world, "updates", ids)
    assert calls == ["sendMessage"]
    root, sibling = rows(world, ids)
    assert root.telegram_chat_id == sibling.telegram_chat_id == CHAT_ID
    expiry = world.clock.now() + timedelta(days=1)
    world.repo.prune_history(before=expiry, memo_before=expiry)
    assert all(member.delivery is None for member in rows(world, ids))
    original = world.repo.get_publication_content(ids[0])
    assert root.id is not None and original is not None, "the pinned group has its original"
    (items,) = news
    candidate = corrected_render(world, original, items, days=1)
    version = revision_fixture(original, candidate, target=MESSAGE_ID, changed_member=root.id)
    slot = reserve(world.repo, original, version, candidate)
    assert slot is not None, "the exact confirmed group accepts one reservation"
    return world, root, original, version, candidate, slot


def dispatch(
    world: World,
    repo: SqliteBillRepository,
    edit: Callable[[int, int, str], VerifiedEditOutcome],
    checkpoint: StateCheckpoint,
    original: ContentVersion,
    version: ContentVersion,
    candidate: GroupedUpdateRender,
    slot: int,
    *,
    chat_id: int = CHAT_ID,
    approved_sha256: str | None = None,
) -> PublicationStatus | None:
    return dispatch_reserved_grouped_edit(
        repo,
        world.clock,
        edit,
        checkpoint,
        slot,
        content_id=version.content_id,
        base_content_id=original.content_id,
        approved_sha256=approved_sha256 or candidate.sha256,
        chat_id=chat_id,
    )


def test_verified_edit_uses_exact_target_after_pending_checkpoint_and_never_resends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, root, original, version, candidate, slot = reserved(monkeypatch)
    dumps: list[str] = []
    calls: list[dict[str, object]] = []

    def checkpoint() -> None:
        dumps.append(world.repo.dump())

    def transport(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/editMessageText")
        assert len(dumps) == 1
        with saved_state(dumps[0]) as fresh:
            (pending,) = rows(fresh, (slot,))
            assert pending.status is PublicationStatus.PENDING
            assert fresh.repo.get_publication_content(slot) == version
            history = inspect_content(fresh.repo, root)
            assert history is not None and history.current_id is None
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {"message_id": MESSAGE_ID, "chat": {"id": CHAT_ID}},
            },
        )

    with closing(TelegramBotClient("fixture", transport=httpx.MockTransport(transport))) as client:
        edit = client.attempt_verified_edit
        assert (
            dispatch(world, world.repo, edit, checkpoint, original, version, candidate, slot)
            is PublicationStatus.SENT
        )
        assert (
            dispatch(world, world.repo, edit, checkpoint, original, version, candidate, slot)
            is None
        )

    assert calls == [
        {
            "chat_id": CHAT_ID,
            "message_id": MESSAGE_ID,
            "text": candidate.text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
    ]
    assert len(dumps) == 2
    with saved_state(dumps[-1]) as fresh:
        (sent,) = rows(fresh, (slot,))
        assert sent.status is PublicationStatus.SENT and sent.message_id == MESSAGE_ID
        history = inspect_content(fresh.repo, root)
        assert history is not None and history.current_id == version.content_id


@pytest.mark.parametrize("result", ["mismatch", "lost", "rejected"])
def test_nonmatching_or_rejected_edit_is_inert_after_restore(
    result: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, root, original, version, candidate, slot = reserved(monkeypatch)
    dumps: list[str] = []
    edits: list[tuple[int, int, str]] = []

    def checkpoint() -> None:
        dumps.append(world.repo.dump())

    def edit(chat: int, message: int, text: str) -> VerifiedEditOutcome:
        edits.append((chat, message, text))
        if result == "lost":
            raise DeliveryUncertainError("the edit response was lost")
        if result == "rejected":

            def reject(request: httpx.Request) -> httpx.Response:
                assert request.url.path.endswith("/editMessageText")
                return httpx.Response(
                    400,
                    json={"ok": False, "error_code": 400, "description": "Bad Request"},
                )

            with closing(
                TelegramBotClient("fixture", transport=httpx.MockTransport(reject))
            ) as client:
                rejected = client.attempt_verified_edit(chat, message, text)
            assert rejected == VerifiedEditRejection(400)
            return rejected
        return TelegramEditReceipt(chat, message + 1)

    expected = PublicationStatus.FAILED if result == "rejected" else PublicationStatus.UNKNOWN
    assert (
        dispatch(world, world.repo, edit, checkpoint, original, version, candidate, slot)
        is expected
    )
    with saved_state(dumps[-1]) as fresh:
        (current,) = rows(fresh, (slot,))
        assert current.status is expected and current.message_id is None
        assert (
            dispatch(world, fresh.repo, edit, checkpoint, original, version, candidate, slot)
            is None
        )
        history = inspect_content(fresh.repo, root)
        assert history is not None
        assert history.current_id == (original.content_id if result == "rejected" else None)
    assert len(edits) == 1 and len(dumps) == 2


def test_reader_accepts_a_candidate_scoped_slot_alongside_a_rejected_legacy_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as patch:
        world, root, original, version, candidate, slot = reserved(patch)
    assert (
        dispatch(
            world,
            world.repo,
            lambda *_: VerifiedEditRejection(429),
            lambda: None,
            original,
            version,
            candidate,
            slot,
        )
        is PublicationStatus.FAILED
    )
    rejected = world.repo.publication_by_id(slot)
    with monkeypatch.context() as patch:
        _, _, _, news = original_fixture(patch)
    alternative = corrected_render(world, original, news, days=2)
    assert root.id is not None, "the confirmed original has a publication identity"
    revision = revision_fixture(original, alternative, target=MESSAGE_ID, changed_member=root.id)
    alternative_slot = save_slot(world, root, revision, candidate_scoped=True)
    frozen = world.repo.dump()

    with saved_state(frozen) as restored:
        stored = restored.repo.publication_by_id(alternative_slot)
        assert stored is not None and stored.ref is not None, "the exact candidate ref was saved"
        assert restored.repo.get_publication_content(alternative_slot) == revision
        assert (
            dispatch(
                world,
                restored.repo,
                lambda chat, message, _: TelegramEditReceipt(chat, message),
                lambda: None,
                original,
                revision,
                alternative,
                alternative_slot,
            )
            is PublicationStatus.SENT
        )
        assert restored.repo.publication_by_id(slot) == rejected
        confirmed = restored.repo.dump()

    with saved_state(confirmed) as restored:
        history = inspect_content(restored.repo, root)
        assert history is not None and history.current_id == revision.content_id
        assert restored.repo.dump() == confirmed

    wrong_ref = stored.ref.replace(revision.content_id.removeprefix("content-v1:"), "f" * 64)
    with (
        pytest.raises(ValueError, match="invalid_content_history"),
        saved_state(confirmed.replace(stored.ref, wrong_ref)),
    ):
        pytest.fail("an unrelated candidate digest must fail restore")


@pytest.mark.parametrize("phase", ["before", "after"])
def test_checkpoint_failure_never_replays_the_edit(
    phase: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, _, original, version, candidate, slot = reserved(monkeypatch)
    durable = [world.repo.dump()]
    calls: list[str] = []

    def checkpoint() -> None:
        if phase == "before" or len(durable) == 2:
            raise StateCheckpointError("checkpoint unavailable")
        durable.append(world.repo.dump())

    def edit(chat: int, message: int, html: str) -> TelegramEditReceipt:
        calls.append(html)
        return TelegramEditReceipt(chat, message)

    with pytest.raises(StateCheckpointError, match="checkpoint unavailable"):
        dispatch(world, world.repo, edit, checkpoint, original, version, candidate, slot)
    assert len(calls) == int(phase == "after")
    with saved_state(durable[-1]) as fresh:
        (stored,) = rows(fresh, (slot,))
        if phase == "before":
            assert stored.status is PublicationStatus.QUEUED
        else:
            assert stored.status is PublicationStatus.PENDING
            fresh.repo.mark_stale_pending_as_unknown(now=world.clock.now())
            assert (
                dispatch(world, fresh.repo, edit, checkpoint, original, version, candidate, slot)
                is None
            )
            (unknown,) = rows(fresh, (slot,))
            assert unknown.status is PublicationStatus.UNKNOWN
    assert len(calls) == int(phase == "after")


@pytest.mark.parametrize("drift", ["before_effect", "after_effect"])
def test_target_drift_cannot_send_to_another_chat_or_confirm_a_receipt(
    drift: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    world, _, original, version, candidate, slot = reserved(monkeypatch)
    group = original.grouped_stage_evidence
    assert group is not None, "the exact original has two pinned members"
    database = tmp_path / "drift.db"
    with closing(SqliteBillRepository(database)) as repo:
        repo.restore(world.repo.dump())
        dumps: list[str] = []
        edits: list[tuple[int, int, str]] = []

        def move_target() -> None:
            with closing(sqlite3.connect(database)) as raw, raw:
                raw.executemany(
                    "UPDATE publications SET telegram_chat_id=? WHERE id=?",
                    [(CHAT_ID - 1, member.publication_id) for member in group.members],
                )

        def checkpoint() -> None:
            dumps.append(repo.dump())
            if len(dumps) == 1 and drift == "before_effect":
                move_target()

        def edit(chat: int, message: int, html: str) -> TelegramEditReceipt:
            edits.append((chat, message, html))
            if drift == "after_effect":
                move_target()
            return TelegramEditReceipt(chat, message)

        before = repo.dump()
        assert (
            dispatch(
                world,
                repo,
                edit,
                checkpoint,
                original,
                version,
                candidate,
                slot,
                chat_id=CHAT_ID - 1,
            )
            is None
        )
        assert (
            dispatch(
                world,
                repo,
                edit,
                checkpoint,
                original,
                version,
                candidate,
                slot,
                approved_sha256="0" * 64,
            )
            is None
        )
        with repo.atomic():
            assert (
                dispatch(world, repo, edit, checkpoint, original, version, candidate, slot) is None
            )
        assert repo.dump() == before and not dumps

        assert (
            dispatch(world, repo, edit, checkpoint, original, version, candidate, slot)
            is PublicationStatus.UNKNOWN
        )
        assert len(dumps) == 2
        assert edits == ([(CHAT_ID, MESSAGE_ID, candidate.text)] if drift == "after_effect" else [])
        stored = repo.publication_by_id(slot)
        assert stored is not None and stored.status is PublicationStatus.UNKNOWN
        assert stored.message_id is None
