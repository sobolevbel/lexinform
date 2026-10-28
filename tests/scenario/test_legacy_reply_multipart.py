from pathlib import Path

import pytest

from lexinform.errors import StateCheckpointError
from lexinform.models import DeliveryPlan, Publication, PublicationKind, PublicationStatus
from lexinform.ports import PublishResult
from tests.harness import World
from tests.scenario.test_card_remote_checkpoints import Mode, connect, git, reject_checkpoint
from tests.scenario.test_legacy_status_reply_freeze import legacy_reply
from tests.scenario.test_update_remote_checkpoints import (
    plan,
    refuse_render,
    restore_case,
    row,
    send,
)


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_legacy_long_reply_freezes_every_part_and_checkpoints_each_real_parent_before_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: Mode,
) -> None:
    case = legacy_reply(mode, long=True, with_act=True)
    world = case.world
    expected = world.publisher.split_message(
        world.publisher.render_delivery(row(world, case.identity))
    )
    assert len(expected) > 1, "the legacy fixture reaches a physical continuation"
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared
    observed: list[int] = []

    def inspect(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        saved = plan(durable, case.identity)
        identities = (case.identity, *saved.continuation_ids)
        assert len(identities) == len(expected)
        index = identities.index(publication.id or 0)
        current = row(durable, identities[index])
        assert current.status is PublicationStatus.PENDING
        assert current.attempts == (1 if index == 0 else 0)
        assert current.delivery == publication.delivery
        assert plan(durable, identities[index]).rendered_text == expected[index]
        if index:
            parent = row(durable, identities[index - 1])
            assert parent.status is PublicationStatus.SENT
            assert plan(durable, identities[index]).reply_to == parent.message_id
        for position, identity in enumerate(identities):
            assert plan(durable, identity).rendered_text == expected[position]
            assert row(durable, identity).status is (
                PublicationStatus.SENT
                if position < index
                else PublicationStatus.PENDING
                if position == index
                else PublicationStatus.QUEUED
            )
        assert row(durable, case.included).status is PublicationStatus.SKIPPED
        assert durable.publication("3039", PublicationKind.ACT_PUBLISHED) is None
        observed.append(identities[index])
        return original(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)

    assert send(case)

    assert observed == [case.identity, *plan(world, case.identity).continuation_ids]
    assert all(row(world, identity).status is PublicationStatus.SENT for identity in observed)
    assert row(world, case.included).status is PublicationStatus.SENT
    assert row(world, case.later).status is PublicationStatus.SKIPPED
    notice = world.publication("3039", PublicationKind.ACT_PUBLISHED)
    assert notice is not None and notice.message_id == row(world, case.identity).message_id


@pytest.mark.parametrize("boundary", ["root_pre", "root_post", "last_pre", "last_post"])
def test_legacy_chain_faults_restore_only_remotely_confirmed_parts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    case = legacy_reply(long=True)
    world = case.world
    total = len(
        world.publisher.split_message(world.publisher.render_delivery(row(world, case.identity)))
    )
    point = {"root_pre": 1, "root_post": 2, "last_pre": 2 * total - 1, "last_post": 2 * total}[
        boundary
    ]
    before = row(world, case.identity)
    remote = connect(world, tmp_path)
    posts = len(world.publisher.sent)
    reject_checkpoint(monkeypatch, point)

    with pytest.raises(StateCheckpointError):
        send(case)

    count = {"root_pre": 0, "root_post": 1, "last_pre": total - 1, "last_post": total}[boundary]
    assert len(world.publisher.sent) - posts == count
    fresh = restore_case(remote, case, tmp_path).world
    if boundary == "root_pre":
        assert row(fresh, case.identity) == before
    else:
        ids = (case.identity, *plan(fresh, case.identity).continuation_ids)
        assert len(ids) == total
        pending = [
            identity for identity in ids if row(fresh, identity).status is PublicationStatus.PENDING
        ]
        assert len(pending) == int(boundary.endswith("post"))
        fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
        if pending:
            posts = len(fresh.publisher.sent)
            fresh.container.delivery_service(dry_run=False).run()
            assert len(fresh.publisher.sent) == posts
            assert row(fresh, pending[0]).status is PublicationStatus.UNKNOWN


def test_failed_legacy_continuation_retries_saved_parts_without_reposting_the_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = legacy_reply(long=True)
    world = case.world
    remote = connect(world, tmp_path)
    world.publisher.fail_continuation_attempt = world.publisher.continuation_attempts + 1
    assert send(case)
    ids = (case.identity, *plan(world, case.identity).continuation_ids)
    assert row(world, ids[0]).status is PublicationStatus.SENT
    assert row(world, ids[1]).status is PublicationStatus.FAILED
    first = row(world, ids[0]).message_id
    fresh = restore_case(remote, case, tmp_path).world
    fresh.publisher.fail_continuation_attempt = None
    posts = len(fresh.publisher.sent)
    monkeypatch.setattr(fresh.publisher, "render_delivery", refuse_render)
    monkeypatch.setattr(fresh.publisher, "publish_status_update", refuse_render)
    monkeypatch.setattr(fresh.formatter, "status_update", refuse_render)

    fresh.container.delivery_service(dry_run=False).run()

    assert row(fresh, ids[0]).message_id == first
    assert len(fresh.publisher.sent) - posts == len(ids) - 1
    assert all(row(fresh, identity).status is PublicationStatus.SENT for identity in ids)


def test_unknown_legacy_continuation_needs_audited_confirmation_before_its_child_can_send(
    tmp_path: Path,
) -> None:
    case = legacy_reply(long=True)
    world = case.world
    remote = connect(world, tmp_path)
    world.publisher.unknown_continuation_attempt = world.publisher.continuation_attempts + 1
    assert send(case)
    ids = (case.identity, *plan(world, case.identity).continuation_ids)
    assert len(ids) >= 3, "the fixture has a child after the uncertain physical part"
    assert row(world, ids[1]).status is PublicationStatus.UNKNOWN
    fresh = restore_case(remote, case, tmp_path).world
    posts = len(fresh.publisher.sent)
    fresh.container.delivery_service(dry_run=False).run()
    assert len(fresh.publisher.sent) == posts
    fresh.command(f"/delivery {ids[1]} confirm 9001")
    commands = fresh.container.command_service(dry_run=False)
    assert commands is not None, "the World enables audited fake operator recovery"

    result = commands.handle_pending(min_score=3, run_started_at=fresh.clock.now())

    assert not result.failed
    assert row(fresh, ids[1]).status is PublicationStatus.SENT
    assert row(fresh, ids[1]).message_id == 9001
    assert len(fresh.publisher.sent) == posts
    fresh.publisher.unknown_continuation_attempt = None
    fresh.container.delivery_service(dry_run=False).run()
    assert len(fresh.publisher.sent) - posts == len(ids) - 2
    assert fresh.publisher.sent[posts].reply_to == 9001
    assert all(row(fresh, identity).status is PublicationStatus.SENT for identity in ids)


def test_mismatched_orphan_part_stops_legacy_freeze_without_api_or_partial_writes() -> None:
    case = legacy_reply(long=True)
    world = case.world
    original = row(world, case.identity)
    world.repo.create_publication(
        Publication(
            term=original.term,
            number=original.number,
            kind=PublicationKind.MESSAGE_PART,
            channel_id=original.channel_id,
            status=PublicationStatus.QUEUED,
            ref=f"message/{case.identity}/2",
            created_at=world.clock.now(),
            delivery=DeliveryPlan(
                bill_json=world.bill("3039").model_dump_json(),
                rendered_text="Unrelated orphan",
                continuation_parent=case.identity,
                continuation_root=case.identity,
                continuation_index=2,
                policy_version="attention-v1",
            ),
        )
    )
    before = world.repo.dump()
    posts = len(world.publisher.sent)

    with pytest.raises(ValueError, match="mismatched legacy continuation"):
        send(case)

    assert world.repo.dump() == before
    assert len(world.publisher.sent) == posts


def test_root_cas_failure_rolls_back_every_prepared_legacy_part_before_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = legacy_reply(long=True)
    world = case.world
    before = world.repo.dump()
    posts = len(world.publisher.sent)

    def refuse(original: Publication, delivery: DeliveryPlan) -> bool:
        return False

    monkeypatch.setattr(world.repo, "freeze_direct_delivery", refuse)

    with pytest.raises(ValueError, match="Cannot change an attempted message payload"):
        send(case)

    assert world.repo.dump() == before
    assert len(world.publisher.sent) == posts
