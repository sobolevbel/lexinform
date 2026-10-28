from pathlib import Path

import pytest

from lexinform.errors import StateCheckpointError
from lexinform.models import OutcomeStatus, PublicationStatus, SittingNews
from tests.scenario.test_card_remote_checkpoints import Mode, connect, reject_checkpoint, restored
from tests.scenario.test_group_remote_checkpoints import flush, queued_world, rows
from tests.scenario.test_update_remote_checkpoints import plan, queued, row, send


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("attempted", [False, True])
@pytest.mark.parametrize("boundary", [1, 2])
def test_legacy_status_edit_freezes_its_saved_context_at_the_remote_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode, attempted: bool, boundary: int
) -> None:
    case = queued(mode, with_act=False)
    world = case.world
    saved = plan(world, case.identity).model_copy(
        update={"edit_message_id": 444, "policy_version": "legacy"}
    )
    assert world.repo.replace_unattempted_delivery(case.identity, saved)
    world.repo.mark_publication(case.identity, PublicationStatus.FAILED, count_attempt=attempted)
    before = row(world, case.identity)
    frozen = saved.model_copy(
        update={
            "rendered_text": world.publisher.render_delivery(before),
            "render_origin": "legacy_snapshot",
        }
    )
    remote = connect(world, tmp_path)
    edits = len(world.publisher.edited)
    reject_checkpoint(monkeypatch, boundary)

    with pytest.raises(StateCheckpointError, match="reader checkpoint failure"):
        send(case)

    assert len(world.publisher.edited) - edits == int(boundary == 2)
    assert plan(world, case.identity) == frozen
    assert row(world, case.identity).attempts == before.attempts
    fresh = restored(remote, world, tmp_path)
    current = row(fresh, case.identity)
    if boundary == 1:
        assert current == before
    else:
        assert current.delivery == frozen
        assert current.status is PublicationStatus.PENDING
        fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
        assert row(fresh, case.identity).status is PublicationStatus.UNKNOWN
        before_counts = len(fresh.publisher.edited), len(fresh.publisher.sent)
        fresh.container.delivery_service(dry_run=False).run()
        assert (len(fresh.publisher.edited), len(fresh.publisher.sent)) == before_counts


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("boundary", [1, 2])
def test_attempted_sitting_checkpoints_preserve_exact_or_unsupported_legacy_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode, boundary: int
) -> None:
    world, identities = queued_world("sittings", mode)
    for identity in identities:
        world.repo.mark_publication(identity, PublicationStatus.FAILED)
    original = rows(world, identities)
    text = world.publisher.render_sittings(
        [
            SittingNews.of(member.kind, member.delivery)
            for member in original
            if member.delivery is not None
        ]
    )
    remote = connect(world, tmp_path)
    posts = len(world.publisher.sent)
    reject_checkpoint(monkeypatch, boundary)

    with pytest.raises(StateCheckpointError, match="reader checkpoint failure"):
        flush(world, "sittings", identities)

    assert len(world.publisher.sent) - posts == int(boundary == 2)
    assert all(
        saved.delivery is not None
        and current.delivery
        == (
            saved.delivery.model_copy(
                update={
                    "sitting_group": identities,
                    "group_text": text,
                    "render_origin": "legacy_snapshot",
                }
            )
            if mode != "on"
            else saved.delivery
        )
        and saved.attempts == current.attempts
        for saved, current in zip(original, rows(world, identities), strict=True)
    )
    fresh = restored(remote, world, tmp_path)
    if boundary == 1:
        assert rows(fresh, identities) == original
    else:
        assert all(
            current.status is PublicationStatus.PENDING for current in rows(fresh, identities)
        )
        fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
        assert all(
            current.status is PublicationStatus.UNKNOWN for current in rows(fresh, identities)
        )
        before_posts = len(fresh.publisher.sent)
        fresh.container.delivery_service(dry_run=False).run()
        assert len(fresh.publisher.sent) == before_posts


def test_legacy_sitting_resolution_keeps_missing_membership_explicit() -> None:
    world, identities = queued_world("sittings", "off")
    for identity in identities:
        world.repo.mark_publication(identity, PublicationStatus.UNKNOWN, count_attempt=False)
    world.command(f"/delivery {identities[0]} confirm 555")
    commands = world.container.command_service(dry_run=False)
    assert commands is not None, "the production fixture enables audited operator recovery"

    result = commands.handle_pending(min_score=3, run_started_at=world.clock.now())

    assert not result.failed and world.replier.replies[-1][1].status is OutcomeStatus.DELIVERY
    first, second = rows(world, identities)
    assert first.status is PublicationStatus.SENT and first.message_id == 555
    assert second.status is PublicationStatus.UNKNOWN
    first_plan, second_plan = first.delivery, second.delivery
    assert first_plan is not None and second_plan is not None, "legacy rows retain semantic plans"
    assert not first_plan.sitting_group and not second_plan.sitting_group
    assert world.repo.delivery_resolutions(identities[0])[0].publication_ids == (identities[0],)
    assert not world.repo.delivery_resolutions(identities[1])
