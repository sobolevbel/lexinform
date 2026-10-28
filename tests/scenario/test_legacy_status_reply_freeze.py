from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from lexinform.errors import StateCheckpointError
from lexinform.models import Bill, Publication, PublicationStatus, StatusChange
from lexinform.ports import PublishResult
from tests.harness import World
from tests.scenario.test_card_remote_checkpoints import Mode, connect, git, reject_checkpoint
from tests.scenario.test_update_remote_checkpoints import (
    UpdateCase,
    plan,
    queued,
    refuse_render,
    restore_case,
    row,
    send,
)


def legacy_reply(
    mode: Mode = "off",
    *,
    with_act: bool = False,
    long: bool = False,
    with_saved_parent: bool = True,
) -> UpdateCase:
    case = queued(mode, with_act=with_act, long=long)
    saved = plan(case.world, case.identity).model_copy(update={"policy_version": "legacy"})
    assert saved.reply_to is not None and saved.edit_message_id is None, (
        "queued preserves the existing card as the reply parent"
    )
    if not with_saved_parent:
        saved = saved.model_copy(update={"reply_to": None})
    assert case.world.repo.replace_unattempted_delivery(case.identity, saved)
    case.world.repo.mark_publication(case.identity, PublicationStatus.FAILED)
    return case


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("with_act", [False, True])
def test_failed_legacy_reply_freezes_current_day_html_and_the_saved_parent_before_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: Mode,
    with_act: bool,
) -> None:
    case = legacy_reply(mode, with_act=with_act)
    world = case.world
    before = row(world, case.identity)
    saved = plan(world, case.identity)
    world.clock.current += timedelta(days=5)
    today = world.clock.now().astimezone(ZoneInfo("Europe/Warsaw")).date()
    expected = world.formatter.status_update(
        Bill.model_validate_json(saved.bill_json),
        StatusChange.model_validate_json(saved.change_json),
        with_act=with_act,
    ).text
    remote = connect(world, tmp_path)
    original_prepared = world.publisher.publish_prepared
    original_callback = world.publisher.publish_status_update
    observed: list[int] = []

    def checkpointed() -> None:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        current = row(durable, case.identity)
        assert current.status is PublicationStatus.PENDING and current.attempts == before.attempts
        assert current.delivery == saved.model_copy(
            update={
                "rendered_text": expected,
                "render_origin": "legacy_snapshot",
                "today": today,
            }
        )
        observed.append(case.identity)

    def prepared(publication: Publication) -> PublishResult:
        checkpointed()
        return original_prepared(publication)

    def callback(
        bill: Bill,
        change: StatusChange,
        reply_to: int | None,
        *,
        with_act: bool = False,
    ) -> PublishResult:
        checkpointed()
        return original_callback(bill, change, reply_to, with_act=with_act)

    monkeypatch.setattr(world.publisher, "publish_prepared", prepared)
    monkeypatch.setattr(world.publisher, "publish_status_update", callback)

    assert send(case)

    assert observed == [case.identity]
    outgoing = world.publisher.sent[-1]
    assert outgoing.text == expected and outgoing.reply_to == saved.reply_to
    assert row(world, case.identity).status is PublicationStatus.SENT
    assert row(world, case.identity).attempts == before.attempts


@pytest.mark.parametrize("boundary", [1, 2])
def test_legacy_reply_remote_failure_preserves_the_last_confirmed_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: int,
) -> None:
    case = legacy_reply()
    world = case.world
    original = row(world, case.identity)
    remote = connect(world, tmp_path)
    posts = len(world.publisher.sent)
    reject_checkpoint(monkeypatch, boundary)

    with pytest.raises(StateCheckpointError):
        send(case)

    assert len(world.publisher.sent) - posts == int(boundary == 2)
    fresh = restore_case(remote, case, tmp_path)
    current = row(fresh.world, case.identity)
    if boundary == 1:
        assert current == original
    else:
        assert current.status is PublicationStatus.PENDING
        assert plan(fresh.world, case.identity).rendered_text
        fresh.world.repo.mark_stale_pending_as_unknown(now=fresh.world.clock.now())
        assert row(fresh.world, case.identity).status is PublicationStatus.UNKNOWN
        before = len(fresh.world.publisher.sent)
        fresh.world.container.delivery_service(dry_run=False).run()
        assert len(fresh.world.publisher.sent) == before


def test_failed_legacy_reply_reuses_its_frozen_html_after_restore_and_formatter_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = legacy_reply()
    world = case.world
    remote = connect(world, tmp_path)
    world.publisher.fail_on.add("3039")
    assert not send(case)
    fresh = restore_case(remote, case, tmp_path)
    saved = plan(fresh.world, case.identity)
    assert saved.rendered_text and saved.render_origin == "legacy_snapshot"
    fresh.world.clock.current += timedelta(days=100)
    monkeypatch.setattr(fresh.world.publisher, "render_delivery", refuse_render)
    monkeypatch.setattr(fresh.world.publisher, "publish_status_update", refuse_render)
    monkeypatch.setattr(fresh.world.formatter, "status_update", refuse_render)
    fresh.world.publisher.fail_on.clear()

    assert send(fresh)

    assert fresh.world.publisher.sent[-1].text == saved.rendered_text
    assert fresh.world.publisher.sent[-1].reply_to == saved.reply_to
