from pathlib import Path

import pytest

from lexinform.errors import StateCheckpointError
from lexinform.models import DeliveryPlan, Publication, PublicationKind, PublicationStatus
from lexinform.ports import PublishResult
from tests.harness import World
from tests.scenario.test_card_remote_checkpoints import Mode, connect, git, reject_checkpoint
from tests.scenario.test_commands import _commands_only
from tests.scenario.test_group_remote_checkpoints import restored
from tests.scenario.test_notice_remote_checkpoints import (
    KINDS,
    NoticeCase,
    deliver,
    plan,
    queued,
    refuse_render,
    row,
)

LONG_HTML = "<b>Сохранённое уведомление</b>\n" + "Срок 30 дней; 200 PLN; старые заявления. " * 240


def legacy_notice(
    kind: PublicationKind = PublicationKind.ACT_PUBLISHED,
    mode: Mode = "off",
    *,
    attempts: int = 1,
) -> NoticeCase:
    case = queued(kind, mode)
    world = case.world
    saved = plan(world, case.identity).model_copy(update={"policy_version": "legacy"})
    assert saved.reply_to is not None and saved.reply_to > 0, (
        "queued supplies the existing card receipt as this notice's saved parent"
    )
    assert world.repo.replace_unattempted_delivery(case.identity, saved), (
        "the fixture stores the legacy policy before its definite failed attempt"
    )
    world.repo.mark_publication(
        case.identity, PublicationStatus.FAILED, count_attempt=bool(attempts)
    )
    return case


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("kind", KINDS)
def test_failed_legacy_notice_prepares_every_part_and_checkpoints_receipt_parents_before_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode, kind: PublicationKind
) -> None:
    case = legacy_notice(kind, mode)
    world = case.world
    saved = plan(world, case.identity)
    expected = world.publisher.split_message(LONG_HTML)
    assert len(expected) == 3, "the fixture exercises a root and two physical continuations"
    monkeypatch.setattr(world.publisher, "render_delivery", lambda publication: LONG_HTML)
    remote = connect(world, tmp_path)
    prepared = world.publisher.publish_prepared
    observed: list[int] = []

    def inspect(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        root = plan(durable, case.identity)
        identities = (case.identity, *root.continuation_ids)
        assert tuple(plan(durable, identity).rendered_text for identity in identities) == expected
        assert root == saved.model_copy(
            update={
                "rendered_text": expected[0],
                "render_origin": "legacy_snapshot",
                "continuation_ids": identities[1:],
            }
        )
        assert publication.id is not None, "every physical notice has a persisted identity"
        index = identities.index(publication.id)
        current = row(durable, publication.id)
        assert current.status is PublicationStatus.PENDING
        assert current.attempts == int(index == 0)
        assert current.delivery == publication.delivery
        if index:
            parent = row(durable, identities[index - 1])
            assert parent.status is PublicationStatus.SENT
            assert plan(durable, publication.id).reply_to == parent.message_id
        else:
            assert root.reply_to == saved.reply_to
        for position, identity in enumerate(identities):
            assert row(durable, identity).status is (
                PublicationStatus.SENT
                if position < index
                else PublicationStatus.PENDING
                if position == index
                else PublicationStatus.QUEUED
            )
        observed.append(publication.id)
        return prepared(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)

    assert deliver(case)

    assert observed == [case.identity, *plan(world, case.identity).continuation_ids]
    assert all(row(world, identity).status is PublicationStatus.SENT for identity in observed)
    assert not world.llm.contexts


@pytest.mark.parametrize("boundary", ["root_pre", "root_post", "last_pre", "last_post"])
def test_failed_legacy_notice_chain_faults_restore_only_confirmed_physical_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    case = legacy_notice()
    world = case.world
    before = row(world, case.identity)
    monkeypatch.setattr(world.publisher, "render_delivery", lambda publication: LONG_HTML)
    total = len(world.publisher.split_message(LONG_HTML))
    point = {"root_pre": 1, "root_post": 2, "last_pre": 2 * total - 1, "last_post": 2 * total}[
        boundary
    ]
    remote = connect(world, tmp_path)
    posts = len(world.publisher.sent)
    reject_checkpoint(monkeypatch, point)

    with pytest.raises(StateCheckpointError, match="reader checkpoint failure"):
        deliver(case)

    count = {"root_pre": 0, "root_post": 1, "last_pre": total - 1, "last_post": total}[boundary]
    assert len(world.publisher.sent) - posts == count
    fresh = restored(remote, world, tmp_path)
    if boundary == "root_pre":
        assert row(fresh, case.identity) == before
    else:
        identities = (case.identity, *plan(fresh, case.identity).continuation_ids)
        assert len(identities) == total
        pending = [
            identity
            for identity in identities
            if row(fresh, identity).status is PublicationStatus.PENDING
        ]
        assert len(pending) == int(boundary.endswith("post"))
        fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
        if pending:
            posts = len(fresh.publisher.sent)
            fresh.container.delivery_service(dry_run=False).run()
            assert len(fresh.publisher.sent) == posts
            assert row(fresh, pending[0]).status is PublicationStatus.UNKNOWN


@pytest.mark.parametrize("kind", KINDS)
def test_failed_legacy_notice_retry_uses_frozen_html_parent_and_day_after_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: PublicationKind
) -> None:
    case = legacy_notice(kind)
    world = case.world
    remote = connect(world, tmp_path)
    world.publisher.fail_on.add("3039")
    assert not deliver(case)
    fresh = restored(remote, world, tmp_path)
    saved = plan(fresh, case.identity)
    assert saved.rendered_text and saved.render_origin == "legacy_snapshot"
    fresh.clock.advance(days=100)
    monkeypatch.setattr(fresh.publisher, "render_delivery", refuse_render)
    fresh_case = NoticeCase(fresh, case.identity, case.saved_day)

    assert deliver(fresh_case)

    assert fresh.publisher.sent[-1].text == saved.rendered_text
    assert fresh.publisher.sent[-1].reply_to == saved.reply_to
    assert plan(fresh, case.identity) == saved
    assert not fresh.llm.contexts


def test_failed_legacy_notice_continuation_retry_preserves_the_sent_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = legacy_notice()
    world = case.world
    monkeypatch.setattr(world.publisher, "render_delivery", lambda publication: LONG_HTML)
    remote = connect(world, tmp_path)
    world.publisher.fail_continuation_attempt = world.publisher.continuation_attempts + 1

    assert deliver(case)

    identities = (case.identity, *plan(world, case.identity).continuation_ids)
    assert row(world, identities[1]).status is PublicationStatus.FAILED
    receipt = row(world, case.identity).message_id
    fresh = restored(remote, world, tmp_path)
    monkeypatch.setattr(fresh.publisher, "render_delivery", refuse_render)

    fresh.container.delivery_service(dry_run=False).run()

    assert row(fresh, case.identity).message_id == receipt
    assert len(fresh.publisher.sent) == len(identities) - 1
    assert all(row(fresh, identity).status is PublicationStatus.SENT for identity in identities)


def test_unknown_legacy_notice_part_requires_operator_confirmation_before_its_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = legacy_notice()
    world = case.world
    monkeypatch.setattr(world.publisher, "render_delivery", lambda publication: LONG_HTML)
    remote = connect(world, tmp_path)
    world.publisher.unknown_continuation_attempt = world.publisher.continuation_attempts + 1
    assert deliver(case)
    identities = (case.identity, *plan(world, case.identity).continuation_ids)
    assert row(world, identities[1]).status is PublicationStatus.UNKNOWN
    fresh = restored(remote, world, tmp_path)

    fresh.container.delivery_service(dry_run=False).run()

    assert not fresh.publisher.sent
    incoming = fresh.command(f"/delivery {identities[1]} confirm 9001")
    _commands_only(fresh)
    assert not fresh.publisher.sent
    assert fresh.repo.delivery_resolutions(identities[1])[0].command == incoming
    fresh.container.delivery_service(dry_run=False).run()
    assert fresh.publisher.sent[0].reply_to == 9001
    assert len(fresh.publisher.sent) == len(identities) - 2
    assert all(row(fresh, identity).status is PublicationStatus.SENT for identity in identities)


def test_legacy_notice_root_write_failure_rolls_back_children_before_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = legacy_notice()
    world = case.world
    monkeypatch.setattr(world.publisher, "render_delivery", lambda publication: LONG_HTML)
    before = world.repo.dump()
    posts = len(world.publisher.sent)

    def refuse(original: Publication, delivery: DeliveryPlan) -> bool:
        return False

    monkeypatch.setattr(world.repo, "freeze_direct_delivery", refuse)

    with pytest.raises(ValueError, match="Cannot change an attempted message payload"):
        deliver(case)

    assert world.repo.dump() == before
    assert len(world.publisher.sent) == posts


@pytest.mark.parametrize("kind", KINDS)
def test_attempted_attention_notice_freezes_its_callback_payload(kind: PublicationKind) -> None:
    case = queued(kind, "on")
    world = case.world
    saved = plan(world, case.identity)
    assert saved.policy_version == "attention-v1", (
        "the fixture preserves its attempted attention policy"
    )
    world.repo.mark_publication(case.identity, PublicationStatus.FAILED)

    assert deliver(case)

    frozen = plan(world, case.identity)
    assert frozen.rendered_text == world.publisher.sent[-1].text
    assert frozen == saved.model_copy(
        update={"rendered_text": frozen.rendered_text, "render_origin": "legacy_snapshot"}
    )
    assert world.publisher.sent[-1].reply_to == saved.reply_to


@pytest.mark.parametrize("kind", KINDS)
def test_legacy_notice_without_a_saved_parent_freezes_its_top_level_payload(
    kind: PublicationKind,
) -> None:
    case = queued(kind, "off")
    world = case.world
    saved = plan(world, case.identity).model_copy(update={"reply_to": None})
    assert world.repo.replace_unattempted_delivery(case.identity, saved), (
        "the legacy fixture has never saved a parent"
    )
    world.repo.mark_publication(case.identity, PublicationStatus.FAILED)

    assert deliver(case)

    assert world.publisher.sent[-1].reply_to is None
    frozen = plan(world, case.identity)
    assert frozen.rendered_text == world.publisher.sent[-1].text
    assert frozen == saved.model_copy(
        update={"rendered_text": frozen.rendered_text, "render_origin": "legacy_snapshot"}
    )


@pytest.mark.parametrize(
    "kind,missing",
    [
        (PublicationKind.IN_FORCE, "today"),
        (PublicationKind.CONSULTATION_DEADLINE, "today"),
        (PublicationKind.DECISION_DEADLINE, "today"),
        (PublicationKind.HEARING_DEADLINE, "today"),
        (PublicationKind.DECISION_DEADLINE, "phase_json"),
        (PublicationKind.HEARING_DEADLINE, "hearing_json"),
    ],
)
def test_incomplete_failed_legacy_notice_fails_closed_before_api_without_an_attempt(
    monkeypatch: pytest.MonkeyPatch, kind: PublicationKind, missing: str
) -> None:
    case = queued(kind, "off")
    world = case.world
    saved = plan(world, case.identity).model_copy(update={missing: None})
    assert world.repo.replace_unattempted_delivery(case.identity, saved), (
        "the fixture omits historical context before its prior failure"
    )
    world.repo.mark_publication(case.identity, PublicationStatus.FAILED)
    monkeypatch.setattr(world.publisher, "publish_prepared", refuse_render)
    posts = len(world.publisher.sent)

    with pytest.raises(ValueError, match="saved .* lacks"):
        deliver(case)

    assert plan(world, case.identity) == saved
    assert len(world.publisher.sent) == posts
    assert row(world, case.identity).attempts == 1


def test_legacy_notice_operator_view_marks_only_the_future_payload_as_frozen() -> None:
    case = legacy_notice()
    world = case.world
    world.publisher.fail_on.add("3039")
    assert not deliver(case)
    incoming = world.command(f"/delivery {case.identity}")
    _commands_only(world)

    reply = world.formatter.command_reply(incoming, world.replier.replies[-1][1])

    assert "HTML frozen from legacy snapshot; earlier HTML unavailable." in reply.text
    assert plan(world, case.identity).render_origin == "legacy_snapshot"
