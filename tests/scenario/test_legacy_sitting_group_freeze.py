from pathlib import Path
from typing import Literal

import pytest

from lexinform.errors import StateCheckpointError
from lexinform.models import Publication, PublicationKind, PublicationStatus, SittingNews
from lexinform.ports import PublishResult
from tests.harness import World
from tests.scenario.test_card_remote_checkpoints import (
    Mode,
    RunnerLost,
    connect,
    git,
    reject_checkpoint,
)
from tests.scenario.test_commands import _commands_only
from tests.scenario.test_group_remote_checkpoints import flush, queued_world, restored, rows

Shape = Literal["single", "failed", "mixed"]


def legacy_sittings(
    mode: Mode = "off", *, shape: Shape = "failed", attempts: int = 1, cancelled: bool = False
) -> tuple[World, tuple[int, ...]]:
    world, identities = queued_world("sittings", mode)
    selected: list[int] = []
    for index, original in enumerate(rows(world, identities)):
        assert original.id is not None and original.delivery is not None, (
            "queued_world prepares both immutable sitting snapshots"
        )
        if shape == "single" and index:
            world.repo.mark_publication(original.id, PublicationStatus.SKIPPED, count_attempt=False)
            continue
        saved = original.delivery.model_copy(update={"policy_version": "legacy"})
        assert world.repo.replace_unattempted_delivery(original.id, saved)
        identity = original.id
        if cancelled:
            world.repo.mark_publication(original.id, PublicationStatus.SKIPPED, count_attempt=False)
            identity = world.repo.create_publication(
                original.model_copy(
                    update={"id": None, "kind": PublicationKind.AGENDA_CANCELLED, "delivery": saved}
                )
            )
        if shape != "mixed" or not index:
            world.repo.mark_publication(
                identity, PublicationStatus.FAILED, count_attempt=bool(attempts)
            )
        selected.append(identity)
    return world, tuple(selected)


def refuse_render(*args: object, **kwargs: object) -> str:
    raise AssertionError("a prepared sitting group must retain its exact saved HTML")


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("shape", ["single", "failed", "mixed"])
@pytest.mark.parametrize("attempts", [0, 1])
@pytest.mark.parametrize("cancelled", [False, True])
def test_legacy_sitting_batch_freezes_future_text_and_members_before_its_top_level_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: Mode,
    shape: Shape,
    attempts: int,
    cancelled: bool,
) -> None:
    world, identities = legacy_sittings(mode, shape=shape, attempts=attempts, cancelled=cancelled)
    original = rows(world, identities)
    expected = world.publisher.render_sittings(
        [
            SittingNews.of(member.kind, member.delivery)
            for member in original
            if member.delivery is not None
        ]
    )
    remote = connect(world, tmp_path)
    send = world.publisher.publish_prepared
    observed: list[int] = []

    def inspect(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        for before, current in zip(original, rows(durable, identities), strict=True):
            assert before.delivery is not None, (
                "the fixture retains each original semantic snapshot"
            )
            assert (
                current.status is PublicationStatus.PENDING and current.attempts == before.attempts
            )
            assert current.delivery == before.delivery.model_copy(
                update={
                    "sitting_group": identities,
                    "group_text": expected,
                    "render_origin": "legacy_snapshot"
                    if before.status is PublicationStatus.FAILED
                    else None,
                }
            )
        assert publication.id is not None, "the shared send uses a saved member identity"
        observed.append(publication.id)
        return send(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)
    before = len(world.publisher.sent)

    flush(world, "sittings", identities)

    assert len(world.publisher.sent) == before + 1 and observed == [identities[0]]
    assert world.publisher.sent[-1].text == expected
    assert world.publisher.sent[-1].reply_to is None
    assert len({member.message_id for member in rows(world, identities)}) == 1
    assert all(member.status is PublicationStatus.SENT for member in rows(world, identities))
    assert not world.llm.contexts


@pytest.mark.parametrize("shape", ["single", "failed", "mixed"])
@pytest.mark.parametrize("boundary", [1, 2])
def test_legacy_sitting_checkpoint_faults_preserve_only_confirmed_group_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: Shape, boundary: int
) -> None:
    world, identities = legacy_sittings(shape=shape)
    original = rows(world, identities)
    remote = connect(world, tmp_path)
    before = len(world.publisher.sent)
    reject_checkpoint(monkeypatch, boundary)

    with pytest.raises(StateCheckpointError, match="reader checkpoint failure"):
        flush(world, "sittings", identities)

    assert len(world.publisher.sent) - before == int(boundary == 2)
    fresh = restored(remote, world, tmp_path)
    if boundary == 1:
        assert rows(fresh, identities) == original
    else:
        assert all(member.status is PublicationStatus.PENDING for member in rows(fresh, identities))
        fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
        fresh.container.delivery_service(dry_run=False).run()
        assert not fresh.publisher.sent
        assert all(member.status is PublicationStatus.UNKNOWN for member in rows(fresh, identities))


@pytest.mark.parametrize("action", ["retry", "confirm 777"])
def test_future_group_recovery_uses_saved_membership_after_restore_and_formatter_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    world, identities = legacy_sittings(shape="mixed")
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared

    def uncertain(publication: Publication) -> PublishResult:
        original(publication)
        raise RunnerLost()

    monkeypatch.setattr(world.publisher, "publish_prepared", uncertain)
    with pytest.raises(RunnerLost):
        flush(world, "sittings", identities)
    fresh = restored(remote, world, tmp_path)
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    frozen = rows(fresh, identities)
    incoming = fresh.command(f"/delivery {identities[-1]} {action}")
    _commands_only(fresh)
    assert fresh.repo.delivery_resolutions(identities[-1])[0].command == incoming
    assert fresh.repo.delivery_resolutions(identities[-1])[0].publication_ids == identities
    fresh.clock.advance(days=100)
    monkeypatch.setattr(fresh.publisher, "render_sittings", refuse_render)
    monkeypatch.setattr(fresh.publisher, "publish_sittings", refuse_render)
    monkeypatch.setattr(fresh.formatter, "sittings", refuse_render)

    fresh.container.delivery_service(dry_run=False).run()

    assert len(fresh.publisher.sent) == int(action == "retry")
    assert all(member.status is PublicationStatus.SENT for member in rows(fresh, identities))
    assert tuple(member.delivery for member in rows(fresh, identities)) == tuple(
        member.delivery for member in frozen
    )
    if action == "retry":
        assert frozen[0].delivery is not None, "the restored group retains its prepared HTML"
        assert fresh.publisher.sent[0].text == frozen[0].delivery.group_text
        assert fresh.publisher.sent[0].reply_to is None


def test_refused_legacy_group_freeze_stops_before_any_api_or_pending_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, identities = legacy_sittings(shape="mixed")
    before = world.repo.dump()
    posts = len(world.publisher.sent)

    def refuse(members: tuple[Publication, ...], text: str) -> bool:
        return False

    monkeypatch.setattr(world.repo, "freeze_legacy_sitting_group", refuse)

    with pytest.raises(ValueError, match="Cannot freeze the legacy sitting group"):
        flush(world, "sittings", identities)

    assert world.repo.dump() == before
    assert len(world.publisher.sent) == posts
