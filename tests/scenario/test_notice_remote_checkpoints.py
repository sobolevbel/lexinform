import datetime as dt
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Never

import pytest

from lexinform.errors import DeliveryUncertainError, StateCheckpointError, TelegramUnavailableError
from lexinform.models import (
    DeliveryPlan,
    Phase,
    Publication,
    PublicationKind,
    PublicationStatus,
    Stage,
)
from lexinform.ports import PublishResult
from tests.harness import CHANNEL, TERM, World, act, submission
from tests.scenario.test_card_remote_checkpoints import (
    Mode,
    RunnerLost,
    connect,
    git,
    ready_world,
    reject_checkpoint,
)
from tests.scenario.test_commands import _commands_only
from tests.scenario.test_group_remote_checkpoints import restored

KINDS = (
    PublicationKind.ACT_PUBLISHED,
    PublicationKind.CONSULTATION_RESULTS,
    PublicationKind.IN_FORCE,
    PublicationKind.CONSULTATION_DEADLINE,
    PublicationKind.DECISION_DEADLINE,
    PublicationKind.HEARING_DEADLINE,
)
DATED = frozenset(KINDS[2:])


@dataclass(frozen=True)
class NoticeCase:
    world: World
    identity: int
    saved_day: dt.date


def row(world: World, identity: int) -> Publication:
    publication = world.repo.publication_by_id(identity)
    assert publication is not None, "the fixture saves the notice before its first delivery"
    return publication


def plan(world: World, identity: int) -> DeliveryPlan:
    delivery = row(world, identity).delivery
    assert delivery is not None, "a notice keeps its original delivery snapshot"
    return delivery


def queued(kind: PublicationKind, mode: Mode = "on") -> NoticeCase:
    world = ready_world(mode)
    world.run()
    day = world.clock.now().date()
    world.repo.save_act(TERM, "3039", act(entry_into_force=day))
    world.repo.save_submission(
        TERM, "3039", submission(print_number="3039", consultation_end=day + dt.timedelta(days=2))
    )
    phase = Phase(key="senate", deadline=day + dt.timedelta(days=2), deadline_exact=True)
    hearing = Stage(
        stage_name="Wysłuchanie publiczne",
        stage_type="PublicHearing",
        date=day + dt.timedelta(days=12),
    )
    publication = world.container.poster(dry_run=False).prepare_message(
        world.bill("3039"),
        kind,
        today=day if kind in DATED else None,
        phase=phase if kind is PublicationKind.DECISION_DEADLINE else None,
        hearing=hearing if kind is PublicationKind.HEARING_DEADLINE else None,
    )
    assert publication.id is not None, "prepare_message assigns the immutable publication id"
    world.clock.advance(days=1)
    return NoticeCase(world, publication.id, day)


def deliver(case: NoticeCase) -> bool:
    return case.world.container.poster(dry_run=False).deliver(row(case.world, case.identity))


def refuse_render(*args: object, **kwargs: object) -> Never:
    raise AssertionError("a frozen notice must not be rendered again")


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("kind", KINDS)
def test_notice_freezes_legacy_equivalent_html_date_and_target_before_its_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode, kind: PublicationKind
) -> None:
    case = queued(kind, mode)
    world = case.world
    expected_day = world.clock.now().date() if mode == "on" else case.saved_day
    original_plan = plan(world, case.identity)
    expected = world.publisher.render_delivery(
        row(world, case.identity).model_copy(
            update={"delivery": original_plan.model_copy(update={"today": expected_day})}
        )
    )
    card = world.publication("3039")
    assert card is not None and card.id is not None, "the notice has an existing reader card"
    world.repo.mark_publication(
        card.id, PublicationStatus.SENT, message_id=999, count_attempt=False
    )
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared
    observed: list[int] = []

    def inspect(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        saved = row(durable, case.identity)
        assert saved.status is PublicationStatus.PENDING and saved.attempts == 0
        assert saved.channel_id == CHANNEL and saved.delivery == publication.delivery
        frozen = plan(durable, case.identity)
        assert frozen.rendered_text == expected
        assert frozen.reply_to == original_plan.reply_to != 999
        if kind in DATED:
            assert frozen.today == expected_day
        observed.append(case.identity)
        return original(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)
    before = len(world.publisher.sent)

    assert deliver(case)
    assert deliver(case)

    assert observed == [case.identity]
    assert len(world.publisher.sent) == before + 1
    assert world.publisher.sent[-1].text == expected
    if kind is PublicationKind.DECISION_DEADLINE:
        assert world.publisher.sent[-1].detail == "senate"
    fresh = restored(remote, world, tmp_path)
    assert row(fresh, case.identity).status is PublicationStatus.SENT
    assert row(fresh, case.identity).message_id == row(world, case.identity).message_id
    assert not world.llm.contexts


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("attempts", [0, 1])
def test_attempted_notice_without_html_preserves_legacy_payload_and_date(
    monkeypatch: pytest.MonkeyPatch, mode: Mode, kind: PublicationKind, attempts: int
) -> None:
    case = queued(kind, mode)
    world = case.world
    original = plan(world, case.identity)
    world.repo.mark_publication(
        case.identity,
        PublicationStatus.FAILED,
        error="prior transport refusal",
        count_attempt=bool(attempts),
    )
    expected = world.publisher.render_delivery(row(world, case.identity))
    monkeypatch.setattr(world.publisher, "publish_prepared", refuse_render)
    before = len(world.publisher.sent)

    assert deliver(case)

    assert len(world.publisher.sent) == before + 1
    assert world.publisher.sent[-1].text == expected
    assert plan(world, case.identity) == original
    assert not original.rendered_text and not original.continuation_ids
    assert row(world, case.identity).attempts == attempts


@pytest.mark.parametrize("at", [1, 2, 5, 6])
def test_notice_multipart_checkpoint_failure_keeps_only_remote_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, at: int
) -> None:
    case = queued(PublicationKind.ACT_PUBLISHED)
    world = case.world
    text = "<b>Акт опубликован</b>\n" + "Срок 30 дней; 200 PLN; старые заявления. " * 240
    expected = world.publisher.split_message(text)
    assert len(expected) == 3, "the public renderer seam creates a three-part notice"
    monkeypatch.setattr(world.publisher, "render_delivery", lambda publication: text)
    remote = connect(world, tmp_path)
    before = len(world.publisher.sent)
    reject_checkpoint(monkeypatch, at)

    with pytest.raises(StateCheckpointError, match="reader checkpoint failure"):
        deliver(case)

    assert len(world.publisher.sent) - before == at // 2
    fresh = restored(remote, world, tmp_path)
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    frozen = plan(fresh, case.identity)
    identities = (case.identity, *frozen.continuation_ids)
    affected = identities[0] if at <= 2 else identities[-1]
    if at % 2 == 0:
        assert row(fresh, affected).status is PublicationStatus.UNKNOWN
        before_restore_send = len(fresh.publisher.sent)
        fresh.container.delivery_service(dry_run=False).run()
        assert len(fresh.publisher.sent) == before_restore_send
    else:
        assert row(fresh, affected).status is PublicationStatus.QUEUED
    if at > 1:
        assert tuple(plan(fresh, identity).rendered_text for identity in identities) == expected


def test_notice_parts_have_remote_frozen_membership_and_receipt_parents_before_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = queued(PublicationKind.ACT_PUBLISHED)
    world = case.world
    text = "<b>Акт опубликован</b>\n" + "Срок 30 дней; 200 PLN; старые заявления. " * 240
    expected = world.publisher.split_message(text)
    monkeypatch.setattr(world.publisher, "render_delivery", lambda publication: text)
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared
    observed: list[int] = []

    def inspect(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        identities = (case.identity, *plan(durable, case.identity).continuation_ids)
        assert tuple(plan(durable, identity).rendered_text for identity in identities) == expected
        assert publication.id is not None, "every physical notice has its own saved identity"
        saved = row(durable, publication.id)
        assert saved.status is PublicationStatus.PENDING and saved.delivery == publication.delivery
        position = identities.index(publication.id)
        if position:
            parent = row(durable, identities[position - 1])
            assert parent.status is PublicationStatus.SENT
            assert plan(durable, publication.id).reply_to == parent.message_id
        observed.append(publication.id)
        return original(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)

    assert deliver(case)

    assert len(observed) == len(expected)
    fresh = restored(remote, world, tmp_path)
    assert all(row(fresh, identity).status is PublicationStatus.SENT for identity in observed)


@pytest.mark.parametrize("action", ["retry", "confirm 777"])
def test_lost_notice_runner_requires_audited_recovery_of_its_original_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    case = queued(PublicationKind.HEARING_DEADLINE)
    world = case.world
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared

    def lost(publication: Publication) -> PublishResult:
        original(publication)
        raise RunnerLost()

    monkeypatch.setattr(world.publisher, "publish_prepared", lost)
    with pytest.raises(RunnerLost):
        deliver(case)
    fresh = restored(remote, world, tmp_path)
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    saved = plan(fresh, case.identity)
    before = len(fresh.publisher.sent)
    fresh.container.delivery_service(dry_run=False).run()
    assert len(fresh.publisher.sent) == before
    incoming = fresh.command(f"/delivery {case.identity} {action}")
    _commands_only(fresh)
    assert len(fresh.repo.delivery_resolutions(case.identity)) == 1
    assert plan(fresh, case.identity) == saved
    fresh.clock.advance(days=120)
    monkeypatch.setattr(fresh.publisher, "render_delivery", refuse_render)
    send = fresh.publisher.publish_prepared

    def retry(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        audit = durable.repo.delivery_resolutions(case.identity)
        assert len(audit) == 1 and audit[0].command == incoming
        assert plan(durable, case.identity) == saved
        return send(publication)

    monkeypatch.setattr(fresh.publisher, "publish_prepared", retry)

    fresh.container.delivery_service(dry_run=False).run()

    assert len(fresh.publisher.sent) - before == int(action == "retry")
    assert row(fresh, case.identity).status is PublicationStatus.SENT
    if action == "confirm 777":
        assert row(fresh, case.identity).message_id == 777


@pytest.mark.parametrize("outcome", ["definite", "outage", "unknown"])
def test_notice_transport_outcomes_are_durable_and_spend_only_definite_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    case = queued(PublicationKind.ACT_PUBLISHED)
    world = case.world
    remote = connect(world, tmp_path)
    if outcome == "definite":
        world.publisher.fail_on.add("3039")
    elif outcome == "outage":
        world.publisher.outage_on.add("3039")
    else:
        original = world.publisher.publish_prepared

        def unknown(publication: Publication) -> PublishResult:
            original(publication)
            raise DeliveryUncertainError("accepted, response lost")

        monkeypatch.setattr(world.publisher, "publish_prepared", unknown)
    if outcome == "definite":
        assert not deliver(case)
    else:
        with pytest.raises(
            DeliveryUncertainError if outcome == "unknown" else TelegramUnavailableError
        ):
            deliver(case)
    fresh = restored(remote, world, tmp_path)
    assert row(fresh, case.identity).status is (
        PublicationStatus.UNKNOWN if outcome == "unknown" else PublicationStatus.FAILED
    )
    assert row(fresh, case.identity).attempts == int(outcome == "definite")
    assert plan(fresh, case.identity).rendered_text


@pytest.mark.parametrize(
    "kind,missing",
    [
        (PublicationKind.IN_FORCE, "today"),
        (PublicationKind.DECISION_DEADLINE, "phase_json"),
        (PublicationKind.HEARING_DEADLINE, "hearing_json"),
    ],
)
def test_incomplete_new_notice_never_sends_a_preview_error_as_reader_text(
    kind: PublicationKind, missing: str
) -> None:
    case = queued(kind, "off")
    world = case.world
    broken = plan(world, case.identity).model_copy(update={missing: None})
    assert world.repo.replace_unattempted_delivery(case.identity, broken)
    before = len(world.publisher.sent)

    with pytest.raises(ValueError, match="saved .* lacks"):
        deliver(case)

    assert len(world.publisher.sent) == before
    assert row(world, case.identity).status is PublicationStatus.QUEUED
    assert row(world, case.identity).attempts == 0


@pytest.mark.parametrize("dry_run", [False, True])
def test_unattempted_notice_dry_run_and_no_publish_do_not_push(
    tmp_path: Path, dry_run: bool
) -> None:
    case = queued(PublicationKind.ACT_PUBLISHED)
    world = case.world
    remote = connect(world, tmp_path)
    tip = git(remote, "rev-parse", "state")
    before = len(world.publisher.sent)
    if dry_run:
        world.container = replace(world.container, publisher_override=None)
    world.pipeline = world.container.pipeline(dry_run=dry_run)

    world.run(dry_run=dry_run, publish=dry_run, discover=False, analyze=False)

    assert git(remote, "rev-parse", "state") == tip
    assert len(world.publisher.sent) == before
    assert row(world, case.identity).status is PublicationStatus.QUEUED
    assert not plan(world, case.identity).rendered_text


@pytest.mark.parametrize(
    "kind", [PublicationKind.CONSULTATION_DEADLINE, PublicationKind.HEARING_DEADLINE]
)
def test_existing_attention_expiry_gate_still_runs_before_notice_freeze(
    tmp_path: Path, kind: PublicationKind
) -> None:
    case = queued(kind)
    world = case.world
    world.clock.advance(days=3)
    remote = connect(world, tmp_path)
    tip = git(remote, "rev-parse", "state")
    before = len(world.publisher.sent)

    assert not deliver(case)

    assert len(world.publisher.sent) == before
    assert row(world, case.identity).status is PublicationStatus.SKIPPED
    assert row(world, case.identity).attempts == 0
    assert not plan(world, case.identity).rendered_text
    assert git(remote, "rev-parse", "state") == tip
