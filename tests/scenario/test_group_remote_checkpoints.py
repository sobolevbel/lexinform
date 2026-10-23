from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from lexinform.errors import DeliveryUncertainError, StateCheckpointError
from lexinform.models import Publication, PublicationKind, PublicationStatus, SittingNews
from lexinform.ports import PublishResult
from lexinform.services.tracking_result import TrackingResult
from tests.harness import CHANNEL, COMMITTEE_STAGES, START, World
from tests.scenario.test_card_remote_checkpoints import RunnerLost, connect, git, reject_checkpoint
from tests.scenario.test_commands import _commands_only
from tests.scenario.test_tracking_agenda import ASW, _sitting

Mode = Literal["off", "shadow", "on"]
Group = Literal["updates", "sittings"]
TITLE = "Projekt ustawy o cudzoziemcach"


def queued_world(group: Group, mode: Mode = "on") -> tuple[World, tuple[int, ...]]:
    world = World(profile="production", reader_attention=mode, attention_roundup="daily")
    for number in ("3039", "3040"):
        world.add_bill(number, TITLE, stages=COMMITTEE_STAGES if group == "sittings" else START)
    world.gateway.committees["ASW"] = ASW
    world.run()
    world.batch.resolve()
    world.run()
    if group == "updates":
        for number in ("3039", "3040"):
            world.set_stages(number, COMMITTEE_STAGES)
    else:
        world.gateway.committee_sittings["ASW"] = (
            _sitting(agenda="Pierwsze czytanie (druki nr 3039 i 3040)"),
        )
    world.run(publish=False)
    world.clock.advance(hours=10)
    kind = PublicationKind.STATUS_UPDATE if group == "updates" else PublicationKind.AGENDA
    identities = tuple(
        row.id
        for row in world.repo.list_open_deliveries(CHANNEL)
        if row.kind is kind and row.id is not None
    )
    assert len(identities) == 2, "the unpublished run queues both source events"
    assert not world.llm.contexts
    return world, identities


def rows(world: World, identities: tuple[int, ...]) -> tuple[Publication, ...]:
    result = tuple(world.repo.publication_by_id(identity) for identity in identities)
    assert all(row is not None for row in result), "all group members are retained"
    return tuple(row for row in result if row is not None)


def flush(world: World, group: Group, identities: tuple[int, ...]) -> TrackingResult:
    poster = world.container.poster(dry_run=False)
    poster.start_run()
    for row in rows(world, identities):
        if group == "updates":
            poster.queue_update(row)
        else:
            poster.queue_sitting(row)
    result = TrackingResult()
    if group == "updates":
        poster.flush_updates(result)
    else:
        poster.flush_sittings(result)
    return result


def restored(remote: Path, world: World, tmp_path: Path, *, mode: Mode | None = None) -> World:
    clone = tmp_path / "restored"
    git(tmp_path, "clone", "--branch", "state", str(remote), str(clone))
    fresh = World(
        profile="production",
        reader_attention=mode or world.container.settings.reader_attention,
        attention_roundup="daily",
    )
    fresh.clock.current = world.clock.now()
    fresh.repo.restore((clone / "state.sql").read_text())
    fresh.container = replace(
        fresh.container,
        settings=fresh.container.settings.model_copy(
            update={
                "llm_batch_state_file": clone / "state.sql",
                "state_checkpoint_required": True,
            }
        ),
    )
    fresh.pipeline = fresh.container.pipeline(dry_run=False)
    return fresh


def text_and_members(row: Publication, group: Group) -> tuple[str, tuple[int, ...]]:
    plan = row.delivery
    assert plan is not None, "group members carry the frozen shared delivery"
    members = (
        tuple(item.publication_id for item in plan.update_group)
        if group == "updates"
        else plan.sitting_group
    )
    return plan.group_text, members


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("group", ["updates", "sittings"])
def test_shared_send_checkpoints_exact_text_members_and_receipt_as_one_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode, group: Group
) -> None:
    world, identities = queued_world(group, mode)
    remote = connect(world, tmp_path)
    update = world.publisher.publish_updates
    prepared = world.publisher.publish_prepared
    sent: list[str] = []

    def inspect(text: str) -> None:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        for row in rows(durable, identities):
            assert row.status is PublicationStatus.PENDING and row.attempts == 0
            assert text_and_members(row, group) == (text, identities)
            assert row.channel_id == CHANNEL
        sent.append(text)

    def send_update(text: str) -> PublishResult:
        inspect(text)
        return update(text)

    def send_prepared(row: Publication) -> PublishResult:
        inspect(text_and_members(row, group)[0])
        return prepared(row)

    monkeypatch.setattr(world.publisher, "publish_updates", send_update)
    monkeypatch.setattr(world.publisher, "publish_prepared", send_prepared)

    result = flush(world, group, identities)

    assert len(sent) == 1
    assert result.published == (1 if group == "updates" else 0)
    fresh = restored(remote, world, tmp_path)
    members = rows(fresh, identities)
    assert all(row.status is PublicationStatus.SENT for row in members)
    assert members[0].message_id == members[1].message_id
    assert all(text_and_members(row, group) == (sent[0], identities) for row in members)


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("group", ["updates", "sittings"])
@pytest.mark.parametrize("at", [1, 2])
def test_shared_checkpoint_failure_never_allows_partial_or_automatic_redelivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode, group: Group, at: int
) -> None:
    world, identities = queued_world(group, mode)
    remote = connect(world, tmp_path)
    before = len(world.publisher.sent)
    reject_checkpoint(monkeypatch, at)

    with pytest.raises(StateCheckpointError, match="reader checkpoint failure"):
        flush(world, group, identities)

    assert len(world.publisher.sent) - before == int(at == 2)
    assert all(row.attempts == 0 for row in rows(world, identities))
    fresh = restored(remote, world, tmp_path)
    if at == 1:
        assert all(row.status is PublicationStatus.QUEUED for row in rows(fresh, identities))
    else:
        fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
        assert all(row.status is PublicationStatus.UNKNOWN for row in rows(fresh, identities))
        fresh.container.delivery_service(dry_run=False).run()
        assert not fresh.publisher.sent


@pytest.mark.parametrize("group", ["updates", "sittings"])
@pytest.mark.parametrize("accepted", [False, True])
def test_runner_loss_or_lost_response_preserves_unknown_group_after_remote_only_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, group: Group, accepted: bool
) -> None:
    world, identities = queued_world(group)
    remote = connect(world, tmp_path)
    update = world.publisher.publish_updates
    prepared = world.publisher.publish_prepared

    def send_update(text: str) -> PublishResult:
        if accepted:
            update(text)
            raise DeliveryUncertainError("accepted but response lost")
        raise RunnerLost()

    def send_prepared(row: Publication) -> PublishResult:
        if accepted:
            prepared(row)
            raise DeliveryUncertainError("accepted but response lost")
        raise RunnerLost()

    monkeypatch.setattr(world.publisher, "publish_updates", send_update)
    monkeypatch.setattr(world.publisher, "publish_prepared", send_prepared)
    with pytest.raises(DeliveryUncertainError if accepted else RunnerLost):
        flush(world, group, identities)
    fresh = restored(remote, world, tmp_path)
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())

    fresh.container.delivery_service(dry_run=False).run()

    assert not fresh.publisher.sent
    assert all(row.status is PublicationStatus.UNKNOWN for row in rows(fresh, identities))


@pytest.mark.parametrize("group", ["updates", "sittings"])
def test_operator_retry_after_mode_change_saves_shared_audit_before_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, group: Group
) -> None:
    world, identities = queued_world(group)
    remote = connect(world, tmp_path)

    def lost(*args: object) -> PublishResult:
        raise RunnerLost()

    with monkeypatch.context() as patch:
        patch.setattr(world.publisher, "publish_updates", lost)
        patch.setattr(world.publisher, "publish_prepared", lost)
        with pytest.raises(RunnerLost):
            flush(world, group, identities)
    frozen = text_and_members(rows(world, identities)[0], group)[0]
    fresh = restored(remote, world, tmp_path, mode="off")
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    incoming = fresh.command(f"/delivery {identities[-1]} retry")
    _commands_only(fresh)
    update = fresh.publisher.publish_updates
    prepared = fresh.publisher.publish_prepared
    observed: list[str] = []

    def inspect(text: str) -> None:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        for identity in identities:
            audit = durable.repo.delivery_resolutions(identity)
            assert len(audit) == 1 and audit[0].command == incoming
        assert text == frozen
        observed.append(text)

    def send_update(text: str) -> PublishResult:
        inspect(text)
        return update(text)

    def send_prepared(row: Publication) -> PublishResult:
        inspect(text_and_members(row, group)[0])
        return prepared(row)

    monkeypatch.setattr(fresh.publisher, "publish_updates", send_update)
    monkeypatch.setattr(fresh.publisher, "publish_prepared", send_prepared)

    fresh.container.delivery_service(dry_run=False).run()
    fresh.container.delivery_service(dry_run=False).run()

    assert observed == [frozen]
    assert all(row.status is PublicationStatus.SENT for row in rows(fresh, identities))


def test_attempted_legacy_sitting_without_membership_keeps_its_compatible_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, identities = queued_world("sittings", "off")
    for identity in identities:
        world.repo.mark_publication(identity, PublicationStatus.FAILED, error="old refusal")
    connect(world, tmp_path)
    observed: list[tuple[SittingNews, ...]] = []
    original = world.publisher.publish_sittings

    def send(news: tuple[SittingNews, ...] | list[SittingNews]) -> PublishResult:
        observed.append(tuple(news))
        return original(news)

    monkeypatch.setattr(world.publisher, "publish_sittings", send)

    flush(world, "sittings", identities)

    assert len(observed) == 1 and len(observed[0]) == 2
    assert all(not text_and_members(row, "sittings")[1] for row in rows(world, identities))


def failed_group(
    world: World, group: Group, identities: tuple[int, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object) -> PublishResult:
        raise ValueError("definite shared refusal")

    with monkeypatch.context() as patch:
        patch.setattr(world.publisher, "publish_updates", fail)
        patch.setattr(world.publisher, "publish_prepared", fail)
        flush(world, group, identities)
    assert all(row.status is PublicationStatus.FAILED for row in rows(world, identities))


@pytest.mark.parametrize("group", ["updates", "sittings"])
@pytest.mark.parametrize("at", [1, 2])
def test_group_failure_escapes_tracking_and_stops_following_pipeline_phases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, group: Group, at: int
) -> None:
    world, identities = queued_world(group)
    failed_group(world, group, identities, monkeypatch)
    connect(world, tmp_path)
    before, edits = len(world.publisher.sent), len(world.publisher.edited)
    reject_checkpoint(monkeypatch, at)

    with pytest.raises(StateCheckpointError, match="reader checkpoint failure"):
        world.run()

    assert len(world.publisher.sent) - before == int(at == 2)
    assert len(world.publisher.edited) == edits
    report = world.notifier.calls[-1][0]
    assert report.finished_at is not None and "digest" not in report.phase_seconds
    assert any("state persistence failed" in error for error in report.errors)


@pytest.mark.parametrize("group", ["updates", "sittings"])
def test_a_missing_frozen_group_member_cannot_be_sent_as_a_partial_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, group: Group
) -> None:
    world, identities = queued_world(group)
    failed_group(world, group, identities, monkeypatch)
    missing = rows(world, identities)[-1]
    world.repo.delete_publication(missing.term, missing.number, missing.kind, missing.channel_id)
    connect(world, tmp_path)
    before = len(world.publisher.sent)

    world.container.delivery_service(dry_run=False).run()

    assert len(world.publisher.sent) == before
    first = world.repo.publication_by_id(identities[0])
    assert first is not None and first.status is PublicationStatus.FAILED
