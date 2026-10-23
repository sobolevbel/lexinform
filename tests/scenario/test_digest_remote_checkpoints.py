from dataclasses import replace
from pathlib import Path

import pytest

from lexinform.errors import DeliveryUncertainError, StateCheckpointError
from lexinform.models import (
    DIGEST_NUMBER,
    DIGEST_TERM,
    Digest,
    DigestPart,
    Publication,
    PublicationKind,
    PublicationStatus,
)
from lexinform.ports import PublishResult
from lexinform.services.digest import DigestService
from tests.harness import CHANNEL, COMMITTEE_STAGES, World
from tests.scenario.test_card_remote_checkpoints import (
    Mode,
    RunnerLost,
    connect,
    git,
    ready_world,
    reject_checkpoint,
    restored,
)
from tests.scenario.test_recent_changes_audit import digest_chain

REF = "2026-W37"


def pending_event(mode: Mode = "on") -> tuple[World, Publication]:
    world = ready_world(mode)
    world.run()
    world.set_stages("3039", COMMITTEE_STAGES)
    world.run(publish=False)
    source = world.publication("3039", PublicationKind.STATUS_UPDATE)
    assert source is not None and source.id is not None and source.delivery is not None, (
        "the unpublished tracking run saves this status update and its delivery plan"
    )
    plan = source.delivery.model_copy(
        update={
            "destination": "digest_only",
            "destination_reason": "test enrichment",
            "digest_eligible_at": world.clock.now(),
            "schedule": None,
            "wait_until": None,
        }
    )
    assert world.repo.replace_unattempted_delivery(source.id, plan), (
        "the source update has not been attempted"
    )
    world.clock.advance(hours=1)
    return world, source.model_copy(update={"delivery": plan})


def service_for(world: World, *, dry_run: bool = False) -> DigestService:
    service = world.container.digest_service(dry_run=dry_run)
    assert service is not None, "World provides the technical channel required for a digest"
    return service


def saved_digest(world: World, *, draft: bool = False) -> Publication:
    row = world.repo.get_publication(
        DIGEST_TERM,
        DIGEST_NUMBER,
        PublicationKind.DIGEST,
        world.container.drafts_channel_id() if draft else CHANNEL,
        ref=REF,
    )
    assert row is not None, "the first digest part owns the week reference"
    return row


def split_digest(source: Publication) -> tuple[DigestPart, ...]:
    assert source.id is not None, "the source event has a saved publication identity"
    return (
        DigestPart(text="Saved digest introduction"),
        DigestPart(text="Saved digest event", event_ids=(source.id,)),
        DigestPart(text="Saved digest conclusion"),
    )


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_new_digest_freezes_its_real_layout_and_membership_before_the_channel_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode
) -> None:
    world, source = pending_event(mode)
    remote = connect(world, tmp_path)
    service = service_for(world)
    expected = world.publisher.render_digest_parts(service.build(REF))
    original = world.publisher.publish_prepared
    before = len(world.publisher.sent)
    observed: list[int] = []

    def send(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        row = durable.repo.publication_by_id(publication.id or 0)
        assert row is not None and row.delivery is not None, "remote pending precedes the API"
        assert row.status is PublicationStatus.PENDING
        assert row.delivery.digest_parts == (row.id,)
        assert row.delivery.rendered_text == expected[0].text
        current = durable.repo.publication_by_id(source.id or 0)
        assert current is not None and current.status is PublicationStatus.QUEUED, (
            "a pending physical digest part has not completed its reserved event"
        )
        assert durable.repo.digest_owner(source.id or 0) == row.id
        assert not row.delivery.approve_label
        assert row.delivery.digest_ref == REF
        observed.append(publication.id or 0)
        return original(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", send)

    result = service.publish(REF)

    assert result.published
    assert len(observed) == 1
    assert world.publisher.sent[before].text == expected[0].text
    assert world.publisher.sent[before].action is None
    fresh = restored(remote, world, tmp_path)
    receipt = saved_digest(fresh)
    assert receipt.status is PublicationStatus.SENT and receipt.message_id is not None
    event = fresh.repo.publication_by_id(source.id or 0)
    assert event is not None, "checkpoint restores the reserved source event"
    assert event.status is PublicationStatus.SENT
    assert event.message_id == receipt.message_id
    assert not service_for(fresh).publish(REF).published


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_digest_draft_preserves_its_layout_and_approval_button_without_reader_checkpoints(
    tmp_path: Path, mode: Mode
) -> None:
    world, source = pending_event(mode)
    remote = connect(world, tmp_path)
    baseline = git(remote, "rev-parse", "state")
    service = service_for(world)
    expected = world.publisher.render_digest_parts(service.build(REF), draft=True)
    before = len(world.publisher.sent)

    result = service.draft(REF)

    assert result.drafted
    assert world.publisher.sent[before].text == expected[0].text
    action = world.publisher.sent[before].action
    assert action is not None and action[1] == f"digest:{REF}", (
        "the technical draft exposes the existing approval callback"
    )
    assert git(remote, "rev-parse", "state") == baseline
    assert world.repo.digest_owner(source.id or 0) is None
    event = world.repo.publication_by_id(source.id or 0)
    assert event is not None and event.status is PublicationStatus.QUEUED, (
        "a technical draft never completes a reader event"
    )
    draft = saved_digest(world, draft=True)
    assert (draft.delivery is not None) is (mode == "on")


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_each_new_digest_part_checkpoints_all_membership_and_prior_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode
) -> None:
    world, source = pending_event(mode)
    remote = connect(world, tmp_path)
    layout = split_digest(source)

    def render(week: Digest, *, draft: bool = False) -> tuple[DigestPart, ...]:
        return layout

    monkeypatch.setattr(world.publisher, "render_digest_parts", render)
    original = world.publisher.publish_prepared
    observed: list[int] = []

    def send(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        root = saved_digest(durable)
        assert root.delivery is not None and len(root.delivery.digest_parts) == 3, (
            "all rendered physical parts are frozen before the first API call"
        )
        identities = root.delivery.digest_parts
        index = identities.index(publication.id or 0)
        for position, identity in enumerate(identities):
            row = durable.repo.publication_by_id(identity)
            assert row is not None and row.delivery is not None, (
                "the root lists every saved physical part"
            )
            assert row.delivery.rendered_text == layout[position].text
            assert row.status is (
                PublicationStatus.SENT
                if position < index
                else PublicationStatus.PENDING
                if position == index
                else PublicationStatus.QUEUED
            )
        assert durable.repo.digest_owner(source.id or 0) == identities[1]
        event = durable.repo.publication_by_id(source.id or 0)
        assert event is not None, "the source publication survives each physical part checkpoint"
        assert event.status is (PublicationStatus.SENT if index == 2 else PublicationStatus.QUEUED)
        observed.append(publication.id or 0)
        return original(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", send)

    result = service_for(world).publish(REF)

    assert result.published and result.reader_parts == 3 and result.covered_events == 1
    fresh = restored(remote, world, tmp_path)
    assert all(
        row is not None and row.status is PublicationStatus.SENT
        for identity in observed
        for row in (fresh.repo.publication_by_id(identity),)
    )
    assert fresh.repo.digest_owner(source.id or 0) == observed[1]


@pytest.mark.parametrize("at", [1, 2, 3, 4, 5, 6])
def test_digest_checkpoint_failure_is_fatal_and_stops_later_physical_parts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, at: int
) -> None:
    world, source = pending_event()
    remote = connect(world, tmp_path)
    identities = digest_chain(world, source.id)
    world.container.state_checkpoint()()
    before = len(world.publisher.sent)
    calls = reject_checkpoint(monkeypatch, at)

    with pytest.raises(StateCheckpointError, match="reader checkpoint failure"):
        service_for(world).run()

    assert len(world.publisher.sent) - before == at // 2
    assert len(calls) == at
    local_event = world.repo.publication_by_id(source.id or 0)
    assert local_event is not None, "the source exists throughout its owner's delivery"
    assert local_event.status is (PublicationStatus.SENT if at == 6 else PublicationStatus.QUEUED)
    if at == 6:
        local_owner = world.repo.publication_by_id(identities[-1])
        assert local_owner is not None and local_owner.status is PublicationStatus.SENT, (
            "the last receipt and source effect share the local transaction before checkpoint"
        )
        assert local_event.message_id == local_owner.message_id
    fresh = restored(remote, world, tmp_path)
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    affected = fresh.repo.publication_by_id(identities[(at - 1) // 2])
    assert affected is not None, "the previous successful checkpoint saved all digest identities"
    assert affected.status is (PublicationStatus.QUEUED if at % 2 else PublicationStatus.UNKNOWN)
    event = fresh.repo.publication_by_id(source.id or 0)
    assert event is not None and event.status is PublicationStatus.QUEUED, (
        "the last part owns this source and has no durable successful receipt"
    )
    assert fresh.repo.digest_owner(source.id or 0) == identities[-1]
    if at % 2 == 0:
        service_for(fresh).run()
        fresh.container.delivery_service(dry_run=False).run()
        assert len(world.publisher.sent) - before == at // 2
        assert fresh.repo.publication_by_id(source.id or 0) == event
        assert fresh.repo.publication_by_id(identities[(at - 1) // 2]) == affected
        assert fresh.repo.digest_owner(source.id or 0) == identities[-1]
        assert service_for(fresh).build(REF).updates == ()


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_saved_digest_parts_keep_their_original_payloads_in_every_attention_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode
) -> None:
    world, source = pending_event(mode)
    identities = digest_chain(world, source.id)
    remote = connect(world, tmp_path)
    fresh = restored(remote, world, tmp_path)
    before = len(world.publisher.sent)

    def render(week: Digest, *, draft: bool = False) -> tuple[DigestPart, ...]:
        raise AssertionError("saved digest parts must not be rendered again")

    monkeypatch.setattr(fresh.publisher, "render_digest_parts", render)

    service_for(fresh).run()

    assert [outgoing.text for outgoing in world.publisher.sent[before:]] == [
        f"Saved digest part {index + 1}" for index in range(3)
    ]
    durable = World()
    durable.repo.restore(git(remote, "show", "state:state.sql"))
    assert all(
        row is not None and row.status is PublicationStatus.SENT
        for identity in identities
        for row in (durable.repo.publication_by_id(identity),)
    )


def test_uncertain_digest_receipt_is_durable_and_does_not_complete_its_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, source = pending_event()
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared

    def uncertain(publication: Publication) -> PublishResult:
        original(publication)
        raise DeliveryUncertainError("accepted but response lost")

    monkeypatch.setattr(world.publisher, "publish_prepared", uncertain)
    before = len(world.publisher.sent)

    with pytest.raises(DeliveryUncertainError):
        service_for(world).publish(REF)

    fresh = restored(remote, world, tmp_path)
    root = saved_digest(fresh)
    assert root.status is PublicationStatus.UNKNOWN and root.attempts == 0
    assert fresh.repo.digest_owner(source.id or 0) == root.id
    event = fresh.repo.publication_by_id(source.id or 0)
    assert event is not None and event.status is PublicationStatus.QUEUED, (
        "an uncertain digest receipt cannot complete its reserved source"
    )
    assert not service_for(fresh).publish(REF).published
    service_for(fresh).run()
    assert len(world.publisher.sent) - before == 1


def test_runner_loss_and_operator_retry_preserve_digest_payload_membership_and_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, source = pending_event()
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared

    def lost(publication: Publication) -> PublishResult:
        raise RunnerLost()

    monkeypatch.setattr(world.publisher, "publish_prepared", lost)
    with pytest.raises(RunnerLost):
        service_for(world).publish(REF)
    fresh = restored(remote, world, tmp_path)
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    row = saved_digest(fresh)
    assert row.id is not None and row.delivery is not None, (
        "the pre-send checkpoint saved the immutable digest payload"
    )
    assert row.status is PublicationStatus.UNKNOWN
    plan = row.delivery
    assert service_for(fresh).publish(REF).failed
    incoming = fresh.command(f"/delivery {row.id} retry")
    observed: list[int] = []

    def retry(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        assert publication.id == row.id, "an audited retry preserves the physical part identity"
        history = durable.repo.delivery_resolutions(publication.id or 0)
        assert len(history) == 1 and history[0].command == incoming
        current = durable.repo.publication_by_id(publication.id or 0)
        assert current is not None and current.delivery == plan, (
            "an audited retry uses the original digest identity and payload"
        )
        assert current.status is PublicationStatus.PENDING
        assert durable.repo.digest_owner(source.id or 0) == row.id
        observed.append(publication.id or 0)
        return original(publication)

    monkeypatch.setattr(fresh.publisher, "publish_prepared", retry)

    fresh.run(discover=False, analyze=False, track=False, max_publish=0)

    assert observed == [row.id]
    durable = World()
    durable.repo.restore(git(remote, "show", "state:state.sql"))
    event = durable.repo.publication_by_id(source.id or 0)
    assert event is not None and event.status is PublicationStatus.SENT, (
        "the successful retry checkpoints its source effect with the receipt"
    )


@pytest.mark.parametrize("at", [1, 2])
def test_digest_command_checkpoint_failure_stops_later_commands_without_acknowledging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, at: int
) -> None:
    world, _ = pending_event()
    connect(world, tmp_path)
    incoming = world.command(f"/digest publish ref={REF}")
    later = world.command("/status")
    reject_checkpoint(monkeypatch, at)

    with pytest.raises(StateCheckpointError, match="reader checkpoint failure"):
        world.run(discover=False, analyze=False, track=False, max_publish=0)

    command = world.repo.command_state(incoming.update_id)
    assert command is not None, "commands are recorded before a reader effect is attempted"
    assert command.executed_at is None and command.handled_at is None
    assert world.repo.command_state(later.update_id) is None
    assert world.inbox.pending() == [incoming, later]
    assert not world.replier.replies
    assert world.notifier.calls[-1][0].finished_at is not None


@pytest.mark.parametrize("dry_run", [False, True])
def test_pending_digest_dry_run_and_no_publish_do_not_push_or_send(
    tmp_path: Path, dry_run: bool
) -> None:
    world, source = pending_event()
    digest_chain(world, source.id)
    remote = connect(world, tmp_path)
    baseline = git(remote, "rev-parse", "state")
    before = len(world.publisher.sent)
    if dry_run:
        world.container = replace(world.container, publisher_override=None)
    world.pipeline = world.container.pipeline(dry_run=dry_run)

    world.run(dry_run=dry_run, publish=dry_run, discover=False, analyze=False, track=False)

    assert git(remote, "rev-parse", "state") == baseline
    assert len(world.publisher.sent) == before


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_attempted_legacy_digest_keeps_its_compatible_unfrozen_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode
) -> None:
    world, _ = pending_event(mode)
    identity = world.repo.create_publication(
        Publication(
            term=DIGEST_TERM,
            number=DIGEST_NUMBER,
            kind=PublicationKind.DIGEST,
            channel_id=CHANNEL,
            status=PublicationStatus.FAILED,
            ref=REF,
            created_at=world.clock.now(),
            attempts=1,
        )
    )
    remote = connect(world, tmp_path)

    def send(publication: Publication) -> PublishResult:
        raise AssertionError("an attempted legacy digest keeps its original fallback")

    monkeypatch.setattr(world.publisher, "publish_prepared", send)

    assert service_for(world).publish(REF).published

    fresh = restored(remote, world, tmp_path)
    row = fresh.repo.publication_by_id(identity)
    assert row is not None and row.status is PublicationStatus.SENT and row.delivery is None, (
        "the attempted legacy digest keeps its saved row without invented frozen HTML"
    )


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("outcome", ["failure", "outage"])
@pytest.mark.parametrize("commands", [1, 2])
def test_a_reader_digest_is_attempted_once_across_commands_and_the_digest_phase(
    mode: Mode, outcome: str, commands: int
) -> None:
    world, source = pending_event(mode)
    for _ in range(commands):
        world.command(f"/digest publish ref={REF}")
    if outcome == "outage":
        world.publisher.outage_on.add(DIGEST_NUMBER)
    else:
        world.publisher.fail_on.add(DIGEST_NUMBER)
    before = world.publisher.digest_send_attempts

    world.run(discover=False, analyze=False, track=False, max_publish=0)

    assert world.publisher.digest_send_attempts - before == 1
    row = saved_digest(world)
    assert row.status is PublicationStatus.FAILED
    assert row.attempts == (0 if outcome == "outage" else 1)
    assert world.repo.digest_owner(source.id or 0) == row.id
    event = world.repo.publication_by_id(source.id or 0)
    assert event is not None and event.status is PublicationStatus.QUEUED, (
        "a definite failed attempt does not complete its reserved source"
    )
    world.publisher.fail_on.clear()
    world.publisher.outage_on.clear()

    world.run(discover=False, analyze=False, track=False, max_publish=0)

    assert world.publisher.digest_send_attempts - before == 2
    assert saved_digest(world).status is PublicationStatus.SENT
    event = world.repo.publication_by_id(source.id or 0)
    assert event is not None and event.status is PublicationStatus.SENT, (
        "a new run retries the failed physical part and completes its source once"
    )


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("legacy", [False, True])
def test_standalone_command_drain_shares_one_digest_attempt_and_resets_next_invocation(
    mode: Mode, legacy: bool
) -> None:
    world, _ = pending_event(mode)
    if legacy:
        world.repo.create_publication(
            Publication(
                term=DIGEST_TERM,
                number=DIGEST_NUMBER,
                kind=PublicationKind.DIGEST,
                channel_id=CHANNEL,
                status=PublicationStatus.FAILED,
                ref=REF,
                created_at=world.clock.now(),
            )
        )
    commands = world.container.command_service(dry_run=False)
    assert commands is not None, "World supplies the inbox and technical channel"
    world.command(f"/digest publish ref={REF}")
    world.command(f"/digest publish ref={REF}")
    world.publisher.fail_on.add(DIGEST_NUMBER)
    before = world.publisher.digest_send_attempts

    result = commands.handle_pending(min_score=3, run_started_at=world.clock.now())

    assert result.handled == 2 and result.failed == 2
    assert world.publisher.digest_send_attempts - before == 1
    assert saved_digest(world).attempts == 1
    world.command(f"/digest publish ref={REF}")

    commands.handle_pending(min_score=3, run_started_at=world.clock.now())

    assert world.publisher.digest_send_attempts - before == 2
    assert saved_digest(world).attempts == 2


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_direct_digest_invocations_can_retry_a_failed_part_without_a_new_container(
    mode: Mode,
) -> None:
    world, _ = pending_event(mode)
    service = service_for(world)
    world.publisher.fail_on.add(DIGEST_NUMBER)
    before = world.publisher.digest_send_attempts

    assert service.publish(REF).failed
    assert service.publish(REF).failed

    assert world.publisher.digest_send_attempts - before == 2
    assert saved_digest(world).attempts == 2
    world.publisher.fail_on.clear()

    assert service.run().published

    assert world.publisher.digest_send_attempts - before == 3
    assert saved_digest(world).status is PublicationStatus.SENT
