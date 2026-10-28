from datetime import date, timedelta
from pathlib import Path

import pytest

from lexinform.errors import DeliveryUncertainError, TelegramUnavailableError
from lexinform.models import Bill, Publication, PublicationKind, PublicationStatus, StatusChange
from lexinform.ports import PublishResult
from tests.checkpoint_state import saved_state
from tests.scenario.test_card_remote_checkpoints import Mode, connect, git
from tests.scenario.test_update_remote_checkpoints import (
    UpdateCase,
    plan,
    queued,
    refuse_render,
    restore_case,
    row,
    send,
)


def legacy_edit(mode: Mode, *, attempts: int = 1, with_act: bool = False) -> UpdateCase:
    case = queued(mode, with_act=with_act)
    saved = plan(case.world, case.identity).model_copy(
        update={"edit_message_id": 444, "policy_version": "legacy"}
    )
    assert case.world.repo.replace_unattempted_delivery(case.identity, saved)
    for _ in range(attempts):
        case.world.repo.mark_publication(case.identity, PublicationStatus.FAILED)
    if not attempts:
        case.world.repo.mark_publication(
            case.identity, PublicationStatus.FAILED, count_attempt=False
        )
    return case


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("attempts", [0, 1, 2])
@pytest.mark.parametrize("with_act", [False, True])
def test_legacy_edit_has_the_same_html_and_target_frozen_remotely_before_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode, attempts: int, with_act: bool
) -> None:
    case = legacy_edit(mode, attempts=attempts, with_act=with_act)
    world = case.world
    before = row(world, case.identity)
    saved = plan(world, case.identity)
    assert saved.today is not None, "legacy_edit retains the original edit date"
    expected = world.formatter.status_update(
        Bill.model_validate_json(saved.bill_json),
        StatusChange.model_validate_json(saved.change_json),
        today=saved.today,
        with_act=saved.with_act,
    ).text
    remote = connect(world, tmp_path)
    edits = len(world.publisher.edited)
    original_prepared = world.publisher.publish_prepared
    original_edit = world.publisher.edit_status_update

    def checkpointed() -> None:
        with saved_state(git(remote, "show", "state:state.sql")) as durable:
            current = row(durable, case.identity)
            assert current.status is PublicationStatus.PENDING and current.attempts == attempts
            assert current.delivery == saved.model_copy(
                update={"rendered_text": expected, "render_origin": "legacy_snapshot"}
            )

    def prepared(publication: Publication) -> PublishResult:
        checkpointed()
        return original_prepared(publication)

    def legacy(
        bill: Bill, change: StatusChange, *, message_id: int, today: date, with_act: bool = False
    ) -> None:
        checkpointed()
        original_edit(bill, change, message_id=message_id, today=today, with_act=with_act)

    monkeypatch.setattr(world.publisher, "publish_prepared", prepared)
    monkeypatch.setattr(world.publisher, "edit_status_update", legacy)

    assert send(case)

    current = row(world, case.identity)
    assert len(world.publisher.edited) - edits == 1
    outgoing, target = world.publisher.edited[-1]
    assert outgoing.text == expected and target == 444
    assert current.attempts == before.attempts and current.status is PublicationStatus.SENT
    assert current.created_at == before.created_at and current.message_id == 444
    assert row(world, case.included).status is PublicationStatus.SENT
    assert row(world, case.later).status is PublicationStatus.SKIPPED
    if with_act:
        notice = world.publication("3039", PublicationKind.ACT_PUBLISHED)
        assert notice is not None and notice.message_id == 444
    assert not world.llm.contexts


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("outcome", ["definite", "outage", "unknown"])
def test_frozen_legacy_retry_survives_runner_loss_and_never_rebuilds_or_retargets_the_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode, outcome: str
) -> None:
    case = legacy_edit(mode)
    world = case.world
    remote = connect(world, tmp_path)
    if outcome == "definite":
        world.publisher.fail_on.add("3039")
        assert not send(case)
    elif outcome == "outage":
        world.publisher.outage_on_edit.add("3039")
        with pytest.raises(TelegramUnavailableError):
            send(case)
    else:
        world.publisher.unknown_on_edit.add("3039")
        with pytest.raises(DeliveryUncertainError):
            send(case)
    fresh = restore_case(remote, case, tmp_path)
    frozen = plan(fresh.world, case.identity)
    assert frozen.rendered_text and frozen.render_origin == "legacy_snapshot"
    assert frozen.edit_message_id == 444
    saved = row(fresh.world, case.identity)
    assert saved.attempts == (2 if outcome == "definite" else 1)
    assert saved.status is (
        PublicationStatus.UNKNOWN if outcome == "unknown" else PublicationStatus.FAILED
    )
    fresh.world.clock.current += timedelta(days=120)
    current = fresh.world.bill("3039")
    fresh.world.repo.upsert_summary(
        current.summary.model_copy(update={"title": "Changed title after the saved edit"}),
        now=fresh.world.clock.now(),
    )
    monkeypatch.setattr(world.publisher, "render_delivery", refuse_render)
    monkeypatch.setattr(world.publisher, "edit_status_update", refuse_render)
    monkeypatch.setattr(fresh.world.formatter, "status_update", refuse_render)
    edits = len(world.publisher.edited)
    if outcome == "unknown":
        assert not send(fresh)
        fresh.world.container.delivery_service(dry_run=False).run()
        assert len(world.publisher.edited) == edits
    else:
        world.publisher.fail_on.clear()
        world.publisher.outage_on_edit.clear()
        assert send(fresh)
        outgoing, target = world.publisher.edited[-1]
        assert outgoing.text == frozen.rendered_text and target == 444
        assert len(world.publisher.edited) == edits + 1
        assert plan(fresh.world, case.identity) == frozen


def test_operator_view_distinguishes_the_first_frozen_retry_from_missing_historical_html() -> None:
    case = legacy_edit("off")
    world = case.world
    world.publisher.fail_on.add("3039")
    assert not send(case)
    incoming = world.command(f"/delivery {case.identity}")
    commands = world.container.command_service(dry_run=False)
    assert commands is not None, "the production fixture enables the technical command inbox"

    commands.handle_pending(min_score=3, run_started_at=world.clock.now())

    reply = world.formatter.command_reply(incoming, world.replier.replies[-1][1])
    assert "HTML frozen from legacy snapshot; earlier HTML unavailable." in reply.text
    assert plan(world, case.identity).render_origin == "legacy_snapshot"
