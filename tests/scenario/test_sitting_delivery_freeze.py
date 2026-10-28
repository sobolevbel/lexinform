from dataclasses import replace
from pathlib import Path

import pytest

from lexinform.errors import DeliveryUncertainError, StateCheckpointError, TelegramUnavailableError
from lexinform.models import (
    AgendaItem,
    AttentionAssessment,
    DeliveryPlan,
    ParticipationSnapshot,
    Publication,
    PublicationKind,
    PublicationStatus,
    SittingNews,
)
from lexinform.ports import PublishResult
from tests.checkpoint_state import saved_state
from tests.harness import World
from tests.scenario.test_card_remote_checkpoints import RunnerLost, connect, git, reject_checkpoint
from tests.scenario.test_commands import _commands_only
from tests.scenario.test_group_remote_checkpoints import flush, queued_world, restored, rows
from tests.scenario.test_long_sitting_remote_checkpoints import (
    expected_parts,
    plan,
    queued,
    row,
)
from tests.scenario.test_long_sitting_remote_checkpoints import (
    flush as flush_long,
)
from tests.scenario.test_long_sitting_remote_checkpoints import (
    identities as physical_ids,
)


def attempted_sittings(
    policy: str,
    status: PublicationStatus,
    attempts: int,
    *,
    single: bool = False,
    mixed: bool = False,
    cancelled: bool = False,
) -> tuple[World, tuple[int, ...]]:
    world, identities = queued_world("sittings", "off")
    selected: list[int] = []
    for index, original in enumerate(rows(world, identities)):
        assert original.id is not None and original.delivery is not None, (
            "queued_world saves both complete sitting plans"
        )
        if single and index:
            world.repo.mark_publication(original.id, PublicationStatus.SKIPPED, count_attempt=False)
            continue
        saved = original.delivery.model_copy(
            update={
                "policy_version": policy,
                "participation": ParticipationSnapshot(
                    consultation=None, checked_at=world.clock.now()
                ),
            }
        )
        assert world.repo.replace_unattempted_delivery(original.id, saved)
        identity = original.id
        if cancelled:
            world.repo.mark_publication(identity, PublicationStatus.SKIPPED, count_attempt=False)
            identity = world.repo.create_publication(
                original.model_copy(
                    update={
                        "id": None,
                        "kind": PublicationKind.AGENDA_CANCELLED,
                        "delivery": saved,
                    }
                )
            )
        if not mixed or not index:
            for _ in range(attempts):
                world.repo.mark_publication(identity, PublicationStatus.FAILED)
            world.repo.mark_publication(identity, status, count_attempt=False)
        selected.append(identity)
    return world, tuple(selected)


@pytest.mark.parametrize(
    "policy,status,attempts,single,mixed,cancelled",
    [
        ("legacy", PublicationStatus.FAILED, 0, True, False, False),
        ("attention-v1", PublicationStatus.FAILED, 1, False, False, True),
        ("legacy", PublicationStatus.QUEUED, 1, False, True, False),
        ("attention-v1", PublicationStatus.QUEUED, 1, True, False, True),
        ("attention-v1", PublicationStatus.FAILED, 0, False, False, False),
    ],
)
def test_retained_sitting_attempts_freeze_saved_callback_context_before_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    policy: str,
    status: PublicationStatus,
    attempts: int,
    single: bool,
    mixed: bool,
    cancelled: bool,
) -> None:
    world, identities = attempted_sittings(
        policy, status, attempts, single=single, mixed=mixed, cancelled=cancelled
    )
    original = rows(world, identities)
    news = tuple(SittingNews.of(pub.kind, pub.delivery) for pub in original if pub.delivery)
    world.publisher.publish_sittings(news)
    expected = world.publisher.sent[-1].text
    for pub in original:
        current = world.bill(pub.number)
        world.repo.upsert_summary(
            current.summary.model_copy(update={"title": "Newer unrelated card title"}),
            now=world.clock.now(),
        )
        card = world.publication(pub.number)
        assert card is not None and card.id is not None, "queued_world published the bill card"
        world.repo.mark_publication(
            card.id, PublicationStatus.SENT, message_id=999, count_attempt=False
        )
    remote = connect(world, tmp_path)
    publish = world.publisher.publish_prepared
    observed: list[int] = []

    def inspect(publication: Publication) -> PublishResult:
        with saved_state(git(remote, "show", "state:state.sql")) as durable:
            for before, current in zip(original, rows(durable, identities), strict=True):
                assert before.delivery is not None, "the fixture keeps every saved semantic plan"
                assert current.status is PublicationStatus.PENDING
                assert current.attempts == before.attempts
                assert current.delivery == before.delivery.model_copy(
                    update={
                        "sitting_group": identities,
                        "group_text": expected,
                        "render_origin": "legacy_snapshot"
                        if before.status is PublicationStatus.FAILED or before.attempts
                        else None,
                    }
                )
            observed.append(publication.id or 0)
            return publish(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)
    before_count = len(world.publisher.sent)

    flush(world, "sittings", identities)

    assert observed == [identities[0]]
    assert len(world.publisher.sent) == before_count + 1
    assert world.publisher.sent[-1].text == expected
    assert world.publisher.sent[-1].reply_to is None
    assert tuple(pub.attempts for pub in rows(world, identities)) == tuple(
        pub.attempts for pub in original
    )


@pytest.mark.parametrize("status", [PublicationStatus.FAILED, PublicationStatus.QUEUED])
def test_attempted_long_sitting_freezes_all_parts_with_saved_link_and_receipt_parents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: PublicationStatus
) -> None:
    case = queued("on", cancelled=status is PublicationStatus.QUEUED)
    world = case.world
    saved = plan(world, case.identity)
    world.repo.mark_publication(case.identity, PublicationStatus.FAILED)
    world.repo.mark_publication(case.identity, status, count_attempt=False)
    world.clock.advance(days=100)
    expected = expected_parts(case)
    remote = connect(world, tmp_path)
    publish = world.publisher.publish_prepared
    observed: list[int] = []

    def inspect(publication: Publication) -> PublishResult:
        with saved_state(git(remote, "show", "state:state.sql")) as durable:
            frozen = plan(durable, case.identity)
            assert frozen == saved.model_copy(
                update={
                    "render_origin": "legacy_snapshot",
                    "rendered_text": expected[0],
                    "continuation_ids": frozen.continuation_ids,
                }
            )
            assert row(durable, case.identity).attempts == 1
            member_ids = physical_ids(durable, case.identity)
            assert (
                tuple(plan(durable, identity).rendered_text for identity in member_ids) == expected
            )
            current = row(durable, publication.id or 0)
            assert current.status is PublicationStatus.PENDING
            if current.id != case.identity:
                current_plan = plan(durable, current.id or 0)
                parent = row(durable, current_plan.continuation_parent or 0)
                assert parent.status is PublicationStatus.SENT
                assert current_plan.reply_to == parent.message_id
            observed.append(current.id or 0)
            return publish(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)
    before = len(world.publisher.sent)

    flush_long(case)

    assert tuple(observed) == physical_ids(world, case.identity)
    assert tuple(out.text for out in world.publisher.sent[before:]) == expected
    assert world.publisher.sent[before].reply_to is None


@pytest.mark.parametrize(
    "physical,action",
    [(False, "dismiss"), (True, "retry"), (True, "confirm 888"), (True, "dismiss")],
)
def test_attempted_sitting_unknown_waits_for_operator_after_remote_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, physical: bool, action: str
) -> None:
    ids: tuple[int, ...]
    case = queued("on") if physical else None
    if case is not None:
        world, ids = case.world, (case.identity,)
        world.repo.mark_publication(case.identity, PublicationStatus.FAILED)
        world.repo.mark_publication(case.identity, PublicationStatus.QUEUED, count_attempt=False)
    else:
        world, ids = attempted_sittings("attention-v1", PublicationStatus.QUEUED, 1)
    remote = connect(world, tmp_path)

    def lost(publication: Publication) -> PublishResult:
        raise RunnerLost()

    with monkeypatch.context() as patch:
        patch.setattr(world.publisher, "publish_prepared", lost)
        with pytest.raises(RunnerLost):
            flush(world, "sittings", ids)
    fresh = restored(remote, world, tmp_path)
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    root = row(fresh, ids[0])
    assert root.delivery is not None, "the remote intent retains frozen HTML and membership"
    members = physical_ids(fresh, ids[0]) if physical else ids
    saved = tuple(
        plan(fresh, identity).rendered_text or plan(fresh, identity).group_text
        for identity in members
    )

    fresh.container.delivery_service(dry_run=False).run()
    assert not fresh.publisher.sent
    assert row(fresh, ids[0]).status is PublicationStatus.UNKNOWN
    command = fresh.command(
        f"/delivery {ids[-1]} {action}" + (" checked absence" if action == "dismiss" else "")
    )
    _commands_only(fresh)
    fresh.container.delivery_service(dry_run=False).run()

    assert fresh.repo.delivery_resolutions(ids[-1])[0].command == command
    assert (
        tuple(
            plan(fresh, identity).rendered_text or plan(fresh, identity).group_text
            for identity in members
        )
        == saved
    )
    if action == "dismiss":
        assert all(
            row(fresh, identity).status is PublicationStatus.DISMISSED for identity in members
        )
        assert not fresh.publisher.sent
    else:
        assert all(row(fresh, identity).status is PublicationStatus.SENT for identity in members)
        expected = saved if action == "retry" else saved[1:]
        assert tuple(out.text for out in fresh.publisher.sent) == expected
        assert fresh.publisher.sent[0].reply_to == (None if action == "retry" else 888)


@pytest.mark.parametrize("boundary", [1, 2])
def test_attempted_attention_sitting_checkpoint_failure_is_never_adopted_as_new_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: int
) -> None:
    world, ids = attempted_sittings("attention-v1", PublicationStatus.QUEUED, 1)
    original = rows(world, ids)
    remote = connect(world, tmp_path)
    reject_checkpoint(monkeypatch, boundary)

    with pytest.raises(StateCheckpointError):
        flush(world, "sittings", ids)

    fresh = restored(remote, world, tmp_path)
    if boundary == 1:
        assert rows(fresh, ids) == original
    else:
        fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
        fresh.container.delivery_service(dry_run=False).run()
        assert all(pub.status is PublicationStatus.UNKNOWN for pub in rows(fresh, ids))
        assert not fresh.publisher.sent


@pytest.mark.parametrize(
    "error",
    [
        ValueError("definite refusal"),
        TelegramUnavailableError("down"),
        DeliveryUncertainError("lost receipt"),
    ],
)
def test_attempted_sitting_error_preserves_frozen_group_and_attempt_history(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    world, ids = attempted_sittings("attention-v1", PublicationStatus.QUEUED, 1)

    def fail(publication: Publication) -> PublishResult:
        raise error

    monkeypatch.setattr(world.publisher, "publish_prepared", fail)
    if isinstance(error, (TelegramUnavailableError, DeliveryUncertainError)):
        with pytest.raises(type(error)):
            flush(world, "sittings", ids)
    else:
        flush(world, "sittings", ids)

    expected_status = (
        PublicationStatus.UNKNOWN
        if isinstance(error, DeliveryUncertainError)
        else PublicationStatus.FAILED
    )
    assert all(pub.status is expected_status for pub in rows(world, ids))
    for pub in rows(world, ids):
        assert pub.delivery is not None and pub.delivery.sitting_group == ids
        assert pub.delivery.group_text
        assert pub.attempts == (2 if isinstance(error, ValueError) else 1)


@pytest.mark.parametrize(
    "patch",
    [{"edit_message_id": 777}, {"revision_of": 777}, {"item_json": "{}"}, {"bill_json": "{}"}],
)
def test_invalid_attempted_sitting_never_sends_preview_or_marks_pending(
    monkeypatch: pytest.MonkeyPatch, patch: dict[str, object]
) -> None:
    world, ids = queued_world("sittings", "off")
    original = rows(world, ids)[0]
    assert original.id is not None and original.delivery is not None, (
        "the watcher saved its context"
    )
    assert world.repo.replace_unattempted_delivery(
        original.id, original.delivery.model_copy(update=patch)
    )
    world.repo.mark_publication(original.id, PublicationStatus.FAILED)
    before = world.repo.dump()
    sent = len(world.publisher.sent)

    with pytest.raises(ValueError):
        flush(world, "sittings", (original.id,))

    assert world.repo.dump() == before
    assert len(world.publisher.sent) == sent


def test_first_sitting_attention_refresh_keeps_the_cas_snapshot_consistent() -> None:
    world, ids = attempted_sittings("attention-v1", PublicationStatus.QUEUED, 0)
    before = len(world.publisher.sent)

    flush(world, "sittings", ids)

    assert len(world.publisher.sent) == before + 1
    for pub in rows(world, ids):
        assert pub.status is PublicationStatus.SENT
        assert pub.delivery is not None and pub.delivery.sitting_group == ids
        assert pub.attention == pub.delivery.attention


def test_sitting_child_collision_rolls_back_the_root_and_every_new_child() -> None:
    case = queued("on")
    world = case.world
    world.repo.mark_publication(case.identity, PublicationStatus.FAILED)
    saved = plan(world, case.identity)
    collision = world.repo.create_publication(
        Publication(
            term=10,
            number="3039",
            kind=PublicationKind.MESSAGE_PART,
            channel_id=row(world, case.identity).channel_id,
            status=PublicationStatus.QUEUED,
            ref=f"message/{case.identity}/2",
            created_at=world.clock.now(),
            delivery=DeliveryPlan(
                bill_json=saved.bill_json, rendered_text="Conflicting physical HTML"
            ),
        )
    )
    before = world.repo.dump()
    sent = len(world.publisher.sent)

    with pytest.raises(ValueError, match="mismatched legacy continuation"):
        flush_long(case)

    assert world.repo.dump() == before
    assert plan(world, collision).rendered_text == "Conflicting physical HTML"
    assert len(world.publisher.sent) == sent


@pytest.mark.parametrize("boundary", ["protected", "policy", "max_group", "fit"])
def test_attempted_sitting_preparation_retains_selected_batch_boundaries(boundary: str) -> None:
    world, original_ids = queued_world("sittings", "off")
    first = rows(world, original_ids)[0]
    assert first.delivery is not None, "queued_world retains the first complete sitting plan"
    ids = list(original_ids)
    for offset in range(3):
        ids.append(
            world.repo.create_publication(
                first.model_copy(
                    update={
                        "id": None,
                        "number": str(3041 + offset),
                        "ref": f"meeting/{offset}",
                    }
                )
            )
        )
    world.container = replace(
        world.container,
        settings=world.container.settings.model_copy(
            update={
                "attention_max_group": 5 if boundary == "fit" else 2,
            }
        ),
    )
    for index, original in enumerate(rows(world, tuple(ids))):
        assert original.id is not None and original.delivery is not None, (
            "every selected row is stored"
        )
        assessment = AttentionAssessment(
            importance="important" if boundary == "protected" and index == 1 else "routine",
            urgency="normal",
            detected_at=world.clock.now(),
            assessed_at=world.clock.now(),
        )
        item = AgendaItem.model_validate_json(original.delivery.item_json or "")
        if boundary == "fit":
            item = item.model_copy(update={"room": "M" * 2200})
        planned = original.delivery.model_copy(
            update={
                "policy_version": "legacy"
                if boundary == "policy" and index == 2
                else "attention-v1",
                "attention": assessment,
                "item_json": item.model_dump_json(),
            }
        )
        assert world.repo.replace_unattempted_delivery(original.id, planned)
        world.repo.mark_publication(original.id, PublicationStatus.FAILED)
    expected = (
        ((ids[0],), (ids[1],), tuple(ids[2:4]), (ids[4],))
        if boundary == "protected"
        else (tuple(ids[:2]), (ids[2],), tuple(ids[3:]))
        if boundary == "policy"
        else tuple((identity,) for identity in ids)
        if boundary == "fit"
        else (tuple(ids[:2]), tuple(ids[2:4]), (ids[4],))
    )
    texts = tuple(
        world.publisher.render_sittings(
            tuple(
                SittingNews.of(pub.kind, pub.delivery) for pub in rows(world, batch) if pub.delivery
            )
        )
        for batch in expected
    )
    before = len(world.publisher.sent)

    flush(world, "sittings", tuple(ids))

    assert tuple(out.text for out in world.publisher.sent[before:]) == texts
    for batch in expected:
        assert all(plan(world, identity).sitting_group == batch for identity in batch)
