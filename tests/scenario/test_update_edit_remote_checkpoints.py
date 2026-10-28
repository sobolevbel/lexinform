from dataclasses import dataclass, replace
from pathlib import Path
from typing import Never

import pytest

from lexinform.errors import DeliveryUncertainError, StateCheckpointError
from lexinform.models import (
    DeliveryPlan,
    Publication,
    PublicationKind,
    PublicationStatus,
    Stage,
    StatusChange,
    Vote,
)
from lexinform.ports import PublishResult
from tests.harness import CHANNEL, World
from tests.scenario.test_card_remote_checkpoints import (
    Mode,
    connect,
    git,
    ready_world,
    reject_checkpoint,
    restored,
)
from tests.scenario.test_tracking_vote_completion import PASSED, VOTE


@dataclass(frozen=True)
class EditCase:
    world: World
    identity: int
    original: Publication


def row(world: World, identity: int) -> Publication:
    found = world.repo.publication_by_id(identity)
    assert found is not None, "late votes have a saved status-change publication"
    return found


def plan(world: World, identity: int) -> DeliveryPlan:
    delivery = row(world, identity).delivery
    assert delivery is not None, "late votes retain the adoption snapshot and date"
    return delivery


def late_vote(mode: Mode = "off", *, long: bool = False) -> EditCase:
    world = ready_world(mode)
    world.run()
    stages = world.bill("3039").stages
    world.set_stages("3039", (*stages, PASSED))
    world.clock.advance(days=4)
    world.run()
    original = world.publication("3039", PublicationKind.STATUS_UPDATE)
    assert original is not None and original.message_id is not None, "adoption was delivered"
    assert original.delivery is not None and original.delivery.today is not None, (
        "adoption stores its rendering date"
    )
    world.gateway.sitting_votings[62] = (VOTE,)
    world.gateway.votings[(62, 16)] = (Vote(mp=1, club="KO", vote="YES"),)
    world.clock.advance(days=1)
    world.run(publish=False)
    publication = world.publication("3039", PublicationKind.STATUS_UPDATE)
    assert publication is not None and publication.id is not None, "late votes have a new intent"
    assert publication.id != original.id and publication.delivery is not None
    assert publication.delivery.edit_message_id == original.message_id
    assert publication.status is PublicationStatus.QUEUED and not publication.delivery.rendered_text
    assert publication.delivery.today == original.delivery.today
    if long:
        change = StatusChange.model_validate_json(publication.delivery.change_json)
        change = change.model_copy(
            update={
                "new_stages": [
                    *change.new_stages,
                    *(
                        Stage(
                            stage_type="Other",
                            stage_name=f"Uzupełnienie danych głosowania {index}: "
                            "obowiązek złożenia wniosku przez cudzoziemca w terminie 30 dni",
                        )
                        for index in range(75)
                    ),
                ]
            }
        )
        assert world.repo.replace_unsent_update_delivery(
            publication.status_change_id or 0,
            CHANNEL,
            publication.delivery.model_copy(update={"change_json": change.model_dump_json()}),
        )
    return EditCase(world, publication.id, original)


def refuse_render(*args: object, **kwargs: object) -> Never:
    raise AssertionError("a saved edit must not be rendered again")


@pytest.mark.parametrize("mode", ["off", "shadow"])
@pytest.mark.parametrize("long", [False, True])
def test_late_vote_edit_checkpoints_original_date_html_target_and_every_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode, long: bool
) -> None:
    case = late_vote(mode, long=long)
    world = case.world
    world.clock.advance(days=25)
    expected = world.publisher.split_message(
        world.publisher.render_delivery(row(world, case.identity))
    )
    assert (len(expected) > 1) is long
    remote = connect(world, tmp_path)
    before_posts = len(world.publisher.sent)
    before_edits = len(world.publisher.edited)
    original_send = world.publisher.publish_prepared
    observed: list[int] = []

    def inspect(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        root = row(durable, case.identity)
        saved = plan(durable, case.identity)
        identities = (case.identity, *saved.continuation_ids)
        index = identities.index(publication.id or 0)
        assert len(identities) == len(expected)
        current = row(durable, identities[index])
        assert current.status is PublicationStatus.PENDING and current.attempts == 0
        assert publication.delivery == current.delivery
        assert plan(durable, identities[index]).rendered_text == expected[index]
        assert saved.today == plan(world, case.identity).today
        if index == 0:
            assert saved.edit_message_id == case.original.message_id
        else:
            parent = row(durable, identities[index - 1])
            assert parent.status is PublicationStatus.SENT
            assert plan(durable, identities[index]).reply_to == parent.message_id
        assert row(durable, case.original.id or 0) == case.original
        assert (
            root.delivery is not None and saved.continuation_ids == root.delivery.continuation_ids
        )
        observed.append(publication.id or 0)
        return original_send(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)

    assert world.container.poster(dry_run=False).deliver(row(world, case.identity))

    assert len(world.publisher.edited) - before_edits == 1
    assert len(world.publisher.sent) - before_posts == len(expected) - 1
    durable = restored(remote, world, tmp_path)
    assert all(row(durable, identity).status is PublicationStatus.SENT for identity in observed)
    assert row(durable, case.identity).message_id == case.original.message_id
    assert row(durable, case.original.id or 0) == case.original


@pytest.mark.parametrize("boundary", ["root_pre", "root_post", "last_pre", "last_post"])
def test_edit_checkpoint_failure_stops_later_effects_and_restore_preserves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    case = late_vote(long=True)
    world = case.world
    total = len(
        world.publisher.split_message(world.publisher.render_delivery(row(world, case.identity)))
    )
    assert total > 1, "the long adoption exercises a final continuation checkpoint"
    at = {"root_pre": 1, "root_post": 2, "last_pre": 2 * total - 1, "last_post": 2 * total}[
        boundary
    ]
    remote = connect(world, tmp_path)
    reject_checkpoint(monkeypatch, at)
    before = len(world.publisher.sent) + len(world.publisher.edited)

    with pytest.raises(StateCheckpointError, match="reader checkpoint failure"):
        world.container.poster(dry_run=False).deliver(row(world, case.identity))

    assert len(world.publisher.sent) + len(world.publisher.edited) - before == at // 2
    fresh = restored(remote, world, tmp_path)
    identities = (case.identity, *plan(fresh, case.identity).continuation_ids)
    affected = identities[0] if boundary.startswith("root") else identities[-1]
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    assert row(fresh, affected).status is (
        PublicationStatus.UNKNOWN if boundary.endswith("post") else PublicationStatus.QUEUED
    )
    assert row(fresh, case.original.id or 0) == case.original
    if boundary.endswith("post"):
        fresh.container.delivery_service(dry_run=False).run()
        assert len(world.publisher.sent) + len(world.publisher.edited) - before == at // 2


@pytest.mark.parametrize("unknown", [False, True])
@pytest.mark.parametrize("reader_mode", ["off", "shadow", "on"])
def test_edit_retry_restores_frozen_payload_without_reanalysis_or_new_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unknown: bool, reader_mode: Mode
) -> None:
    case = late_vote("shadow")
    world = case.world
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared

    def uncertain(publication: Publication) -> PublishResult:
        original(publication)
        raise DeliveryUncertainError("accepted edit but response lost")

    if unknown:
        monkeypatch.setattr(world.publisher, "publish_prepared", uncertain)
        with pytest.raises(DeliveryUncertainError):
            world.container.poster(dry_run=False).deliver(row(world, case.identity))
    else:
        world.publisher.fail_on.add("3039")
        assert not world.container.poster(dry_run=False).deliver(row(world, case.identity))
    fresh = restored(remote, world, tmp_path)
    fresh.container = replace(
        fresh.container,
        settings=fresh.container.settings.model_copy(update={"reader_attention": reader_mode}),
    )
    fresh.pipeline = fresh.container.pipeline(dry_run=False)
    frozen = plan(fresh, case.identity)
    assert frozen.rendered_text and frozen.edit_message_id == case.original.message_id
    assert row(fresh, case.identity).status is (
        PublicationStatus.UNKNOWN if unknown else PublicationStatus.FAILED
    )
    monkeypatch.setattr(world.publisher, "publish_prepared", original)
    monkeypatch.setattr(world.publisher, "render_delivery", refuse_render)
    world.publisher.fail_on.clear()
    fresh.clock.advance(days=120)
    before = len(world.publisher.edited)

    fresh.container.delivery_service(dry_run=False).run()

    assert len(world.publisher.edited) - before == (0 if unknown else 1)
    assert plan(fresh, case.identity) == frozen
    assert row(fresh, case.original.id or 0) == case.original
    if not unknown:
        outgoing, target = world.publisher.edited[-1]
        assert outgoing.text == frozen.rendered_text and target == case.original.message_id


def test_edit_without_original_date_is_rejected_before_pending_or_api() -> None:
    case = late_vote()
    world = case.world
    publication = row(world, case.identity)
    assert publication.delivery is not None, "late votes have a stored plan"
    assert world.repo.replace_unsent_update_delivery(
        publication.status_change_id or 0,
        CHANNEL,
        publication.delivery.model_copy(update={"today": None}),
    )
    before = len(world.publisher.sent) + len(world.publisher.edited)

    with pytest.raises(ValueError, match="original edit date"):
        world.container.poster(dry_run=False).deliver(row(world, case.identity))

    assert row(world, case.identity).status is PublicationStatus.QUEUED
    assert not row(world, case.identity).attempts
    assert len(world.publisher.sent) + len(world.publisher.edited) == before


@pytest.mark.parametrize("mode", ["off", "shadow"])
@pytest.mark.parametrize("attempted", [False, True])
def test_attempted_legacy_edit_freezes_html_and_keeps_the_original_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode, attempted: bool
) -> None:
    case = late_vote(mode)
    world = case.world
    saved = plan(world, case.identity)
    world.repo.mark_publication(
        case.identity, PublicationStatus.FAILED, error="legacy refusal", count_attempt=attempted
    )
    expected = world.publisher.render_delivery(row(world, case.identity))
    frozen = saved.model_copy(
        update={"rendered_text": expected, "render_origin": "legacy_snapshot"}
    )
    remote = connect(world, tmp_path)
    before_tip = git(remote, "rev-parse", "state")
    before = len(world.publisher.edited)
    original = world.publisher.publish_prepared

    def checkpointed(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        assert row(durable, case.identity).status is PublicationStatus.PENDING
        assert plan(durable, case.identity) == publication.delivery == frozen
        assert row(durable, case.original.id or 0) == case.original
        return original(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", checkpointed)
    monkeypatch.setattr(world.publisher, "edit_status_update", refuse_render)
    world.clock.advance(days=25)

    assert world.container.poster(dry_run=False).deliver(row(world, case.identity))

    assert plan(world, case.identity) == frozen
    assert len(world.publisher.edited) - before == 1
    assert world.publisher.edited[-1][1] == case.original.message_id
    assert row(world, case.original.id or 0) == case.original
    assert git(remote, "rev-parse", "state") != before_tip
    durable = World()
    durable.repo.restore(git(remote, "show", "state:state.sql"))
    assert row(durable, case.identity).status is PublicationStatus.SENT
    assert plan(durable, case.identity) == frozen
    assert row(durable, case.original.id or 0) == case.original
