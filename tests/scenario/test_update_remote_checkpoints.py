import datetime as dt
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Never

import pytest

from lexinform.errors import (
    DeliveryUncertainError,
    StateCheckpointError,
    TelegramUnavailableError,
)
from lexinform.models import (
    Bill,
    DeliveryPlan,
    Publication,
    PublicationKind,
    PublicationStatus,
    SejmTerm,
    StatusChange,
)
from lexinform.ports import PublishResult
from tests.harness import CHANNEL, COMMITTEE_STAGES, TERM, World, act
from tests.scenario.test_card_remote_checkpoints import (
    Mode,
    RunnerLost,
    connect,
    git,
    ready_world,
    reject_checkpoint,
    restored,
)
from tests.scenario.test_tracking_agenda import ASW


@dataclass(frozen=True)
class UpdateCase:
    world: World
    change: StatusChange
    identity: int
    included: int
    later: int


def row(world: World, identity: int) -> Publication:
    found = world.repo.publication_by_id(identity)
    assert found is not None, "the fixture saves every publication before delivery"
    return found


def plan(world: World, identity: int) -> DeliveryPlan:
    found = row(world, identity).delivery
    assert found is not None, "a physical update retains its saved delivery plan"
    return found


def hold(world: World, label: str) -> int:
    poster = world.container.poster(dry_run=False)
    change = poster.record_change(
        StatusChange(
            term=TERM,
            number="3039",
            old_fingerprint=None,
            new_fingerprint=f"held-{label}",
            new_stages=[],
            detected_at=world.clock.now(),
        )
    )
    assert change is not None and change.id is not None, "each held fixture has a new fingerprint"
    poster.hold(world.bill("3039"), change)
    publication = world.repo.get_update_publication(change.id, CHANNEL)
    assert publication is not None and publication.id is not None, "hold saves its publication"
    return publication.id


def queued(mode: Mode = "on", *, long: bool = False, with_act: bool = True) -> UpdateCase:
    world = ready_world(mode, long=long)
    if long:
        analysis = world.bill("3039").analysis
        assert analysis is not None, "the production batch fixture completed the bill analysis"
        world.repo.save_analysis(
            TERM,
            "3039",
            analysis.model_copy(
                update={
                    "analysis": analysis.analysis.model_copy(
                        update={"summary": "Старые заявления не подавать заново; 200 PLN. " * 220}
                    )
                }
            ),
        )
    world.run()
    if with_act:
        world.repo.save_act(TERM, "3039", act())
    included = hold(world, "included")
    poster = world.container.poster(dry_run=False)
    change = poster.record_change(
        StatusChange(
            term=TERM,
            number="3039",
            old_fingerprint="before-content-change",
            new_fingerprint="content-change",
            new_stages=[],
            detected_at=world.clock.now(),
            content_changed=True,
        )
    )
    assert change is not None and change.id is not None, "the content update has a new identity"
    poster.prepare(world.bill("3039"), change, with_act=with_act)
    publication = world.repo.get_update_publication(change.id, CHANNEL)
    assert publication is not None and publication.id is not None, "prepare saves the queued update"
    later = hold(world, "later")
    assert publication.delivery is not None, "prepare saves the source snapshot before rendering"
    assert not publication.delivery.rendered_text and publication.status is PublicationStatus.QUEUED
    assert not world.llm.contexts
    return UpdateCase(world, change, publication.id, included, later)


def send(case: UpdateCase) -> bool:
    return case.world.container.poster(dry_run=False).status_update(
        case.world.bill("3039"), case.change
    )


def restore_case(remote: Path, case: UpdateCase, tmp_path: Path) -> UpdateCase:
    world = restored(remote, case.world, tmp_path)
    world.clock.current = case.world.clock.now()
    return replace(case, world=world)


def refuse_render(*args: object, **kwargs: object) -> Never:
    raise AssertionError("the retry must use its frozen physical payload")


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("long", [False, True])
def test_new_status_update_checkpoints_exact_parts_and_receipts_before_each_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode, long: bool
) -> None:
    case = queued(mode, long=long)
    world = case.world
    expected = world.publisher.split_message(
        world.publisher.render_delivery(row(world, case.identity))
    )
    assert (len(expected) > 1) is long
    saved_reply = plan(world, case.identity).reply_to
    later = row(world, case.later)
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared
    observed: list[int] = []

    def inspect(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        root = row(durable, case.identity)
        root_plan = plan(durable, case.identity)
        identities = (case.identity, *root_plan.continuation_ids)
        assert len(identities) == len(expected)
        index = identities.index(publication.id or 0)
        current = row(durable, identities[index])
        assert current.status is PublicationStatus.PENDING and current.attempts == 0
        assert current.channel_id == CHANNEL
        assert plan(durable, current.id or 0).rendered_text == expected[index]
        assert publication.delivery == current.delivery
        if index == 0:
            assert root_plan.reply_to == saved_reply
        else:
            parent = row(durable, identities[index - 1])
            assert parent.status is PublicationStatus.SENT and parent.message_id is not None
            assert plan(durable, current.id or 0).reply_to == parent.message_id
        for position, identity in enumerate(identities):
            member = row(durable, identity)
            assert plan(durable, identity).rendered_text == expected[position]
            assert member.status is (
                PublicationStatus.SENT
                if position < index
                else PublicationStatus.PENDING
                if position == index
                else PublicationStatus.QUEUED
            )
        assert row(durable, case.included).status is PublicationStatus.SKIPPED
        assert durable.publication("3039", PublicationKind.ACT_PUBLISHED) is None
        assert root.delivery is not None, "the pending root retains the complete physical-part map"
        observed.append(publication.id or 0)
        return original(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)

    assert send(case)

    assert len(observed) == len(expected)
    fresh = restore_case(remote, case, tmp_path).world
    assert all(row(fresh, identity).status is PublicationStatus.SENT for identity in observed)
    assert row(fresh, case.included).status is PublicationStatus.SENT
    assert row(fresh, case.included).message_id == row(fresh, observed[-1]).message_id
    assert row(fresh, case.later) == later
    notice = fresh.publication("3039", PublicationKind.ACT_PUBLISHED)
    assert notice is not None and notice.status is PublicationStatus.SENT, (
        "the final physical receipt completes the saved act notice"
    )
    assert notice.message_id == row(fresh, case.identity).message_id


@pytest.mark.parametrize("boundary", ["root_pre", "root_post", "last_pre", "last_post"])
def test_status_checkpoint_failure_stops_the_chain_and_only_durable_effects_survive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    case = queued(long=True)
    world = case.world
    total = len(
        world.publisher.split_message(world.publisher.render_delivery(row(world, case.identity)))
    )
    assert total >= 2, "the fixture must reach a final child that owns the held effects"
    at = {"root_pre": 1, "root_post": 2, "last_pre": total * 2 - 1, "last_post": total * 2}[
        boundary
    ]
    remote = connect(world, tmp_path)
    before = len(world.publisher.sent)
    reject_checkpoint(monkeypatch, at)

    with pytest.raises(StateCheckpointError, match="reader checkpoint failure"):
        send(case)

    assert len(world.publisher.sent) - before == at // 2
    assert row(world, case.included).status is (
        PublicationStatus.SENT if boundary == "last_post" else PublicationStatus.SKIPPED
    )
    if boundary == "last_post":
        notice = world.publication("3039", PublicationKind.ACT_PUBLISHED)
        assert notice is not None and notice.status is PublicationStatus.SENT, (
            "the last local receipt and effects commit before the failed remote checkpoint"
        )
    fresh = restore_case(remote, case, tmp_path)
    fresh.world.repo.mark_stale_pending_as_unknown(now=fresh.world.clock.now())
    identities = (case.identity, *plan(fresh.world, case.identity).continuation_ids)
    affected = identities[0] if boundary.startswith("root") else identities[-1]
    assert row(fresh.world, affected).status is (
        PublicationStatus.UNKNOWN if boundary.endswith("post") else PublicationStatus.QUEUED
    )
    assert row(fresh.world, case.included).status is PublicationStatus.SKIPPED
    assert row(fresh.world, case.later).status is PublicationStatus.SKIPPED
    assert fresh.world.publication("3039", PublicationKind.ACT_PUBLISHED) is None
    if boundary.endswith("post"):
        fresh.world.container.delivery_service(dry_run=False).run()
        assert len(world.publisher.sent) - before == at // 2
        assert row(fresh.world, affected).status is PublicationStatus.UNKNOWN
        assert row(fresh.world, case.included).status is PublicationStatus.SKIPPED
        assert fresh.world.publication("3039", PublicationKind.ACT_PUBLISHED) is None


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("outcome", ["definite", "outage", "unknown"])
def test_status_outcome_and_frozen_retry_are_checkpointed_without_spending_outage_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode, outcome: str
) -> None:
    case = queued(mode)
    world = case.world
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared
    if outcome == "definite":
        world.publisher.fail_on.add("3039")
    elif outcome == "outage":
        world.publisher.outage_on.add("3039")

    def uncertain(publication: Publication) -> PublishResult:
        original(publication)
        raise DeliveryUncertainError("accepted status update but response lost")

    if outcome == "unknown":
        monkeypatch.setattr(world.publisher, "publish_prepared", uncertain)
        with pytest.raises(DeliveryUncertainError):
            send(case)
    elif outcome == "outage":
        with pytest.raises(TelegramUnavailableError):
            send(case)
    else:
        assert not send(case)
    fresh = restore_case(remote, case, tmp_path)
    saved = row(fresh.world, case.identity)
    frozen = plan(fresh.world, case.identity)
    assert saved.status is (
        PublicationStatus.UNKNOWN if outcome == "unknown" else PublicationStatus.FAILED
    )
    assert saved.attempts == (1 if outcome == "definite" else 0)
    assert frozen.rendered_text
    assert row(fresh.world, case.included).status is PublicationStatus.SKIPPED
    assert fresh.world.publication("3039", PublicationKind.ACT_PUBLISHED) is None
    before = len(world.publisher.sent)
    if outcome == "unknown":
        assert not send(fresh)
        fresh.world.container.delivery_service(dry_run=False).run()
        assert len(world.publisher.sent) == before
    else:
        world.publisher.fail_on.clear()
        world.publisher.outage_on.clear()
        monkeypatch.setattr(world.publisher, "render_delivery", refuse_render)
        monkeypatch.setattr(fresh.world.formatter, "status_update", refuse_render)
        assert send(fresh)
        assert world.publisher.sent[-1].text == frozen.rendered_text
        assert plan(fresh.world, case.identity) == frozen
        assert row(fresh.world, case.included).status is PublicationStatus.SENT


@pytest.mark.parametrize("action", ["retry", "confirm 777"])
def test_lost_final_status_part_keeps_its_audit_payload_and_effects_on_operator_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    case = queued(long=True)
    world = case.world
    total = len(
        world.publisher.split_message(world.publisher.render_delivery(row(world, case.identity)))
    )
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared
    calls = 0

    def lose_last(publication: Publication) -> PublishResult:
        nonlocal calls
        calls += 1
        if calls == total:
            raise RunnerLost()
        return original(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", lose_last)
    with pytest.raises(RunnerLost):
        send(case)
    fresh = restore_case(remote, case, tmp_path)
    fresh.world.repo.mark_stale_pending_as_unknown(now=fresh.world.clock.now())
    last = plan(fresh.world, case.identity).continuation_ids[-1]
    saved = plan(fresh.world, last)
    assert row(fresh.world, last).status is PublicationStatus.UNKNOWN
    incoming = fresh.world.command(f"/delivery {last} {action}")
    commands = fresh.world.container.command_service(dry_run=False)
    assert commands is not None, "the production World has a technical command inbox"
    before = len(world.publisher.sent)
    commands.handle_pending(min_score=3, run_started_at=fresh.world.clock.now())

    def retry(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        assert publication.id == last, "operator retry preserves the final physical identity"
        assert plan(durable, last) == saved
        history = durable.repo.delivery_resolutions(last)
        assert len(history) == 1 and history[0].command == incoming
        return original(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", retry)
    fresh.world.container.delivery_service(dry_run=False).run()
    fresh.world.container.state_checkpoint()()

    assert len(world.publisher.sent) - before == int(action == "retry")
    durable = World()
    durable.repo.restore(git(remote, "show", "state:state.sql"))
    assert row(durable, last).status is PublicationStatus.SENT
    assert row(durable, case.included).status is PublicationStatus.SENT
    assert row(durable, case.later).status is PublicationStatus.SKIPPED
    assert durable.publication("3039", PublicationKind.ACT_PUBLISHED) is not None
    history = durable.repo.delivery_resolutions(last)
    assert len(history) == 1 and history[0].command == incoming


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("attempts", [0, 1])
@pytest.mark.parametrize("long", [False, True])
@pytest.mark.parametrize("policy", ["legacy", "attention-v1"])
def test_attempted_status_without_html_keeps_its_legacy_callback_and_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: Mode,
    attempts: int,
    long: bool,
    policy: str,
) -> None:
    case = queued(mode, long=long)
    world = case.world
    legacy = plan(world, case.identity).model_copy(update={"policy_version": policy})
    assert world.repo.replace_unattempted_delivery(case.identity, legacy)
    world.repo.mark_publication(
        case.identity, PublicationStatus.FAILED, error="prior failure", count_attempt=bool(attempts)
    )
    connect(world, tmp_path)
    original = world.publisher.publish_status_update
    observed: list[int] = []

    def send_legacy(
        bill: Bill, change: StatusChange, reply_to: int | None, *, with_act: bool = False
    ) -> PublishResult:
        assert not plan(world, case.identity).rendered_text
        observed.append(case.identity)
        return original(bill, change, reply_to, with_act=with_act)

    monkeypatch.setattr(world.publisher, "publish_status_update", send_legacy)

    assert send(case)

    assert observed == [case.identity]
    assert plan(world, case.identity) == legacy
    assert row(world, case.identity).attempts == attempts


@pytest.mark.parametrize("attempts", [0, 1])
@pytest.mark.parametrize("policy", ["legacy", "attention-v1"])
def test_failed_long_status_drains_its_legacy_plan_without_inventing_frozen_parts(
    tmp_path: Path, attempts: int, policy: str
) -> None:
    case = queued(long=True)
    world = case.world
    legacy = plan(world, case.identity).model_copy(update={"policy_version": policy})
    assert world.repo.replace_unattempted_delivery(case.identity, legacy)
    world.repo.mark_publication(
        case.identity, PublicationStatus.FAILED, error="prior failure", count_attempt=bool(attempts)
    )
    connect(world, tmp_path)

    world.container.delivery_service(dry_run=False).run()

    assert row(world, case.identity).status is PublicationStatus.SENT
    assert plan(world, case.identity) == legacy
    assert not legacy.rendered_text and not legacy.continuation_ids


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("attempted", [False, True])
def test_failed_legacy_status_edit_checkpoints_frozen_html_and_keeps_its_target(
    tmp_path: Path, mode: Mode, attempted: bool
) -> None:
    case = queued(mode, with_act=False)
    world = case.world
    edit = plan(world, case.identity).model_copy(
        update={"edit_message_id": 444, "policy_version": "legacy"}
    )
    assert world.repo.replace_unattempted_delivery(case.identity, edit)
    world.repo.mark_publication(
        case.identity, PublicationStatus.FAILED, error="legacy refusal", count_attempt=attempted
    )
    remote = connect(world, tmp_path)
    baseline = git(remote, "rev-parse", "state")
    before_sent, before_edits = len(world.publisher.sent), len(world.publisher.edited)
    frozen = edit.model_copy(
        update={
            "rendered_text": world.publisher.render_delivery(row(world, case.identity)),
            "render_origin": "legacy_snapshot",
        }
    )

    assert send(case)

    assert git(remote, "rev-parse", "state") != baseline
    assert plan(world, case.identity) == frozen
    assert len(world.publisher.sent) == before_sent
    assert len(world.publisher.edited) == before_edits + 1
    assert world.publisher.edited[-1][1] == row(world, case.identity).message_id == 444
    durable = World()
    durable.repo.restore(git(remote, "show", "state:state.sql"))
    assert row(durable, case.identity).status is PublicationStatus.SENT
    assert plan(durable, case.identity) == frozen


@pytest.mark.parametrize("dry_run", [False, True])
def test_unattempted_status_dry_run_and_no_publish_do_not_push_or_send(
    tmp_path: Path, dry_run: bool
) -> None:
    case = queued(with_act=False)
    world = case.world
    remote = connect(world, tmp_path)
    baseline = git(remote, "rev-parse", "state")
    before = len(world.publisher.sent)
    if dry_run:
        world.container = replace(world.container, publisher_override=None)
    world.pipeline = world.container.pipeline(dry_run=dry_run)

    world.run(dry_run=dry_run, publish=dry_run, discover=False, analyze=False)

    assert git(remote, "rev-parse", "state") == baseline
    assert len(world.publisher.sent) == before
    assert row(world, case.identity).status is PublicationStatus.QUEUED
    assert not plan(world, case.identity).rendered_text


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("outcome", ["definite", "outage"])
def test_direct_status_attempts_are_shared_with_deliver_until_the_next_run(
    mode: Mode, outcome: str
) -> None:
    case = queued(mode, with_act=False)
    world = case.world
    poster = world.container.poster(dry_run=False)
    if outcome == "definite":
        world.publisher.fail_on.add("3039")
    else:
        world.publisher.outage_on.add("3039")
    poster.start_run()
    if outcome == "outage":
        with pytest.raises(TelegramUnavailableError):
            send(case)
    else:
        assert not send(case)
    failed = row(world, case.identity)

    assert not send(case)
    assert not poster.deliver(failed)
    assert row(world, case.identity) == failed
    world.publisher.fail_on.clear()
    world.publisher.outage_on.clear()
    poster.start_run()

    assert send(case)
    assert row(world, case.identity).status is PublicationStatus.SENT


@pytest.mark.parametrize("standalone", [False, True])
def test_two_refreshes_and_publishing_keep_one_status_attempt_until_next_execution(
    monkeypatch: pytest.MonkeyPatch, standalone: bool
) -> None:
    world = ready_world("off")
    world.run()
    world.gateway.committees["ASW"] = ASW
    world.set_stages("3039", COMMITTEE_STAGES)
    world.command("/refresh 3039")
    world.command("/refresh 3039")
    world.publisher.fail_on.add("3039")
    original = world.publisher.publish_prepared
    attempted: list[int] = []

    def count(publication: Publication) -> PublishResult:
        if publication.kind is PublicationKind.STATUS_UPDATE:
            attempted.append(publication.id or 0)
        return original(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", count)
    commands = world.container.command_service(dry_run=False)
    assert commands is not None, "World enables the technical command inbox"
    if standalone:
        commands.handle_pending(min_score=3, run_started_at=world.clock.now())
        world.container.publishing_service(dry_run=False).publish_new(
            min_score=3, limit=10, keep_delivery_attempts=True
        )
        world.container.tracking_service(dry_run=False).check_updates(keep_delivery_attempts=True)
    else:
        world.run(discover=False, analyze=False)

    assert len(attempted) == 1
    failed = row(world, attempted[0])
    assert failed.status is PublicationStatus.FAILED and failed.attempts == 1
    if standalone:
        commands.handle_pending(min_score=3, run_started_at=world.clock.now())
        world.container.tracking_service(dry_run=False).check_updates(keep_delivery_attempts=True)
    else:
        world.run(discover=False, analyze=False)

    assert attempted == [failed.id, failed.id]
    assert row(world, attempted[0]).attempts == 2


def test_new_status_checkpoint_failure_cannot_be_swallowed_by_the_tracking_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = ready_world("off")
    world.run()
    world.gateway.committees["ASW"] = ASW
    world.set_stages("3039", COMMITTEE_STAGES)
    connect(world, tmp_path)
    before = len(world.publisher.sent)
    reject_checkpoint(monkeypatch, 1)

    with pytest.raises(StateCheckpointError, match="reader checkpoint failure"):
        world.run(discover=False, analyze=False)

    assert len(world.publisher.sent) == before
    assert world.notifier.calls[-1][0].finished_at is not None
    assert "digest" not in world.notifier.calls[-1][0].phase_seconds


@pytest.mark.parametrize("outcome", ["definite", "outage"])
@pytest.mark.parametrize("with_command", [False, True])
def test_rollover_status_attempt_survives_commands_publishing_and_tracking_until_next_run(
    monkeypatch: pytest.MonkeyPatch, outcome: str, with_command: bool
) -> None:
    world = ready_world("on")
    world.run()
    world.gateway.terms = [
        SejmTerm(num=TERM, start=dt.date(2023, 11, 13), end=dt.date(2027, 11, 12)),
        SejmTerm(num=11, start=dt.date(2027, 11, 13), current=True),
    ]
    world.clock.advance(days=1)
    if with_command:
        world.command("/status")
    if outcome == "definite":
        world.publisher.fail_on.add("3039")
    else:
        world.publisher.outage_on.add("3039")
    original = world.publisher.publish_prepared
    attempted: list[int] = []

    def count(publication: Publication) -> PublishResult:
        if publication.kind is PublicationKind.STATUS_UPDATE:
            attempted.append(publication.id or 0)
        return original(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", count)

    first = world.run(term=None, discover=False, analyze=False)

    assert first.term == 11
    assert first.commands_handled == int(with_command)
    assert {"end of term", "commands", "publishing", "tracking"} <= first.phase_seconds.keys()
    assert len(attempted) == 1
    failed = row(world, attempted[0])
    assert failed.status is PublicationStatus.FAILED
    assert failed.attempts == (1 if outcome == "definite" else 0)
    assert world.bill("3039").discontinued_at is not None
    world.publisher.fail_on.clear()
    world.publisher.outage_on.clear()

    world.run(term=None, discover=False, analyze=False)

    assert attempted == [failed.id, failed.id]
    assert row(world, attempted[0]).status is PublicationStatus.SENT
