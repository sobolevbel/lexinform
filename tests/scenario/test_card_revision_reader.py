import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import pytest

from lexinform.errors import StateCheckpointError
from lexinform.models import DeliveryPlan, Publication, PublicationKind, PublicationStatus
from lexinform.ports import PublishResult
from lexinform.services.message_parts import continuation_snapshot
from lexinform.services.publications import card_revision_owner
from tests.harness import CHANNEL, TERM, World
from tests.scenario.test_card_remote_checkpoints import (
    Mode,
    connect,
    git,
    ready_world,
    reject_checkpoint,
    restored,
)

DESIRED = "d" * 64
OLD = "a" * 64


@dataclass(frozen=True)
class CardEdit:
    world: World
    card: Publication
    identities: tuple[int, ...]
    texts: tuple[str, ...]


def row(world: World, identity: int) -> Publication:
    found = world.repo.publication_by_id(identity)
    assert found is not None, "the fixture saves physical operations before delivery"
    return found


def saved_edit(
    mode: Mode = "on", *, legacy: bool = False, multipart: bool = False, attempted: bool = True
) -> CardEdit:
    world = ready_world(mode, long=multipart)
    world.run()
    card = world.publication("3039")
    assert card is not None and card.id is not None, (
        "the production batch fixture published its card"
    )
    world.repo.set_card_digest(card.id, OLD)
    card = row(world, card.id)
    physical = world.repo.card_refresh_parts(card.id or 0)
    assert (len(physical) > 1) is multipart
    identities: list[int] = []
    texts: list[str] = []
    parent = card.id
    assert parent is not None, "the logical card has its stored identity"
    for index, target in enumerate(physical, start=1):
        assert target.message_id is not None, "the original physical parts were delivered"
        text = f"<b>Saved card edition {index}</b>\n200 PLN, not 300 PLN."
        identity = world.repo.create_publication(
            Publication(
                term=TERM,
                number="3039",
                kind=PublicationKind.MESSAGE_PART,
                channel_id=CHANNEL,
                status=PublicationStatus.QUEUED,
                ref=f"card/{card.id}/{DESIRED}-{physical[0].id}/{index}",
                created_at=world.clock.now(),
                delivery=DeliveryPlan(
                    bill_json=continuation_snapshot(world.bill("3039")),
                    rendered_text=text,
                    edit_message_id=target.message_id,
                    revision_of=None if legacy or identities else card.id,
                    today=world.clock.now().date(),
                    reply_to=card.message_id if not identities else None,
                    continuation_parent=parent,
                    continuation_root=identities[0] if identities else None,
                    continuation_index=index,
                ),
            )
        )
        identities.append(identity)
        texts.append(text)
        parent = identity
    root = row(world, identities[0])
    assert root.delivery is not None, "the edit operation has its frozen plan"
    assert world.repo.replace_unattempted_delivery(
        identities[0], root.delivery.model_copy(update={"continuation_ids": tuple(identities[1:])})
    ), "the edit operation has not been attempted"
    world.repo.set_card_digest(identities[0], DESIRED)
    if attempted:
        world.repo.mark_publication(
            identities[0], PublicationStatus.FAILED, error="prior rejection"
        )
    return CardEdit(world, card, tuple(identities), tuple(texts))


def send(case: CardEdit) -> bool:
    return case.world.container.poster(dry_run=False).deliver(row(case.world, case.identities[0]))


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("multipart", [False, True])
def test_saved_card_edits_checkpoint_intent_and_apply_only_explicit_card_revision_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode, legacy: bool, multipart: bool
) -> None:
    case = saved_edit(mode, legacy=legacy, multipart=multipart)
    world = case.world
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared
    before_sent, before_edits = len(world.publisher.sent), len(world.publisher.edited)
    observed: list[int] = []

    def inspect(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        assert publication.id is not None and publication.delivery is not None, (
            "prepared edits have their saved identity and target"
        )
        index = case.identities.index(publication.id)
        current = row(durable, publication.id)
        assert current.status is PublicationStatus.PENDING
        assert current.delivery == publication.delivery
        assert publication.delivery.rendered_text == case.texts[index]
        assert publication.delivery.edit_message_id is not None
        assert row(durable, case.card.id or 0) == case.card
        owner = card_revision_owner(durable.repo, row(durable, case.identities[0]))
        assert owner is not None and owner.id == case.card.id, (
            "both supported formats resolve the owner"
        )
        if index:
            previous = row(durable, case.identities[index - 1])
            assert previous.status is PublicationStatus.SENT
            assert publication.delivery.reply_to == previous.message_id
        observed.append(publication.id)
        return original(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)

    assert send(case)

    assert observed == list(case.identities)
    assert len(world.publisher.sent) == before_sent
    assert len(world.publisher.edited) == before_edits + len(case.identities)
    fresh = restored(remote, world, tmp_path)
    assert row(fresh, case.card.id or 0) == case.card.model_copy(
        update={"rendered_sha256": OLD if legacy else DESIRED}
    )
    assert all(
        row(fresh, identity).status is PublicationStatus.SENT for identity in case.identities
    )


@pytest.mark.parametrize("boundary", ["root_pre", "root_post", "last_pre", "last_post"])
def test_card_edit_checkpoint_failure_keeps_the_confirmed_projection_unchanged_after_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    case = saved_edit(multipart=True)
    world = case.world
    remote = connect(world, tmp_path)
    total = len(case.identities)
    at = {"root_pre": 1, "root_post": 2, "last_pre": total * 2 - 1, "last_post": total * 2}[
        boundary
    ]
    before = len(world.publisher.edited)
    reject_checkpoint(monkeypatch, at)

    with pytest.raises(StateCheckpointError):
        send(case)

    assert len(world.publisher.edited) - before == at // 2
    assert row(world, case.card.id or 0).rendered_sha256 == (
        DESIRED if boundary == "last_post" else OLD
    )
    fresh = restored(remote, world, tmp_path)
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    affected = case.identities[0] if boundary.startswith("root") else case.identities[-1]
    expected = (
        PublicationStatus.UNKNOWN
        if boundary.endswith("post")
        else PublicationStatus.FAILED
        if boundary == "root_pre"
        else PublicationStatus.QUEUED
    )
    assert row(fresh, affected).status is expected
    assert row(fresh, case.card.id or 0) == case.card
    if boundary.endswith("post"):
        fresh.container.delivery_service(dry_run=False).run()
        assert len(world.publisher.edited) - before == at // 2
        assert row(fresh, case.card.id or 0).rendered_sha256 == OLD


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("action", ["retry", "confirm", "dismiss", "mismatch"])
def test_card_revision_operator_resolution_preserves_target_audit_and_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy: bool, action: str
) -> None:
    case = saved_edit(legacy=legacy, multipart=True)
    world = case.world
    world.repo.mark_publication(case.identities[0], PublicationStatus.UNKNOWN, count_attempt=False)
    remote = connect(world, tmp_path)
    before = len(world.publisher.edited)
    original = world.publisher.publish_prepared
    root = row(world, case.identities[0])
    assert root.delivery is not None and root.delivery.edit_message_id is not None, (
        "the unknown edit has an immutable physical target"
    )
    suffix = {
        "retry": "retry",
        "confirm": f"confirm {root.delivery.edit_message_id}",
        "dismiss": "dismiss checked absence",
        "mismatch": f"confirm {root.delivery.edit_message_id + 999}",
    }[action]
    incoming = world.command(f"/delivery {root.id} {suffix}")
    commands = world.container.command_service(dry_run=False)
    assert commands is not None, "the production World enables the technical command inbox"
    world.container.delivery_service(dry_run=False).run()
    assert len(world.publisher.edited) == before

    result = commands.handle_pending(min_score=3, run_started_at=world.clock.now())

    assert result.failed == int(action == "mismatch")
    assert row(world, case.card.id or 0).rendered_sha256 == OLD

    def inspect(publication: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        history = durable.repo.delivery_resolutions(case.identities[0])
        assert len(history) == 1 and history[0].command == incoming
        return original(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)
    world.container.delivery_service(dry_run=False).run()
    world.container.state_checkpoint()()
    durable = World()
    durable.repo.restore(git(remote, "show", "state:state.sql"))
    if action == "mismatch":
        assert row(durable, root.id or 0).status is PublicationStatus.UNKNOWN
        assert not durable.repo.delivery_resolutions(root.id or 0)
        assert row(durable, case.card.id or 0) == case.card
        assert len(world.publisher.edited) == before
    elif action == "dismiss":
        assert all(
            row(durable, identity).status is PublicationStatus.DISMISSED
            for identity in case.identities
        )
        assert row(durable, case.card.id or 0) == case.card
        assert len(world.publisher.edited) == before
    else:
        assert row(durable, case.card.id or 0).rendered_sha256 == (OLD if legacy else DESIRED)
        assert len(world.publisher.edited) - before == len(case.identities) - int(
            action == "confirm"
        )
        assert all(
            row(durable, identity).status is PublicationStatus.SENT for identity in case.identities
        )
        history = durable.repo.delivery_resolutions(root.id or 0)
        assert len(history) == 1 and history[0].command == incoming


def test_confirmed_final_card_edit_after_retention_applies_its_saved_digest_without_an_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = saved_edit(multipart=True)
    world = case.world
    remote = connect(world, tmp_path)
    reject_checkpoint(monkeypatch, len(case.identities) * 2)
    with pytest.raises(StateCheckpointError):
        send(case)
    fresh = restored(remote, world, tmp_path)
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    fresh.repo.prune_history(
        before=fresh.clock.now() + dt.timedelta(days=1), memo_before=fresh.clock.now()
    )
    assert row(fresh, case.card.id or 0).delivery is None, "the completed original payload expires"
    root = row(fresh, case.identities[0])
    last = row(fresh, case.identities[-1])
    assert root.delivery is not None, "retention keeps the sent root while its child is unresolved"
    assert last.delivery is not None and last.delivery.edit_message_id is not None, (
        "an unresolved final edit retains its immutable target"
    )
    incoming = fresh.command(f"/delivery {last.id} confirm {last.delivery.edit_message_id}")
    commands = fresh.container.command_service(dry_run=False)
    assert commands is not None, "the production World enables operator resolutions"
    before = len(world.publisher.edited)

    result = commands.handle_pending(min_score=3, run_started_at=fresh.clock.now())
    fresh.container.state_checkpoint()()

    assert result.failed == 0
    assert len(world.publisher.edited) == before
    durable = World()
    durable.repo.restore(git(remote, "show", "state:state.sql"))
    assert row(durable, case.card.id or 0).rendered_sha256 == DESIRED
    history = durable.repo.delivery_resolutions(last.id or 0)
    assert len(history) == 1 and history[0].command == incoming


def test_a_card_edit_without_a_saved_desired_digest_cannot_change_the_card_projection(
    tmp_path: Path,
) -> None:
    case = saved_edit()
    case.world.repo.set_card_digest(case.identities[0], "")
    remote = connect(case.world, tmp_path)

    assert send(case)

    durable = World()
    durable.repo.restore(git(remote, "show", "state:state.sql"))
    assert row(durable, case.identities[0]).status is PublicationStatus.SENT
    assert row(durable, case.card.id or 0) == case.card


@pytest.mark.parametrize("invalid", ["missing", "target", "channel", "kind", "bill"])
def test_explicit_card_revision_with_an_invalid_owner_never_starts_its_edit(invalid: str) -> None:
    case = saved_edit(attempted=False)
    world = case.world
    root = row(world, case.identities[0])
    assert root.delivery is not None, "the queued edit has its saved plan"
    if invalid == "missing":
        broken = root.delivery.model_copy(update={"revision_of": 99999})
    elif invalid == "target":
        broken = root.delivery.model_copy(update={"edit_message_id": 99999})
    else:
        owner = world.repo.create_publication(
            Publication(
                term=TERM,
                number="3040" if invalid == "bill" else "3039",
                kind=PublicationKind.ACT_PUBLISHED
                if invalid == "kind"
                else PublicationKind.NEW_BILL,
                channel_id="@other" if invalid == "channel" else CHANNEL,
                status=PublicationStatus.SENT,
                message_id=case.card.message_id,
                created_at=world.clock.now(),
                ref="unrelated-owner" if invalid == "kind" else None,
            )
        )
        broken = root.delivery.model_copy(update={"revision_of": owner})
    assert world.repo.replace_unattempted_delivery(root.id or 0, broken)
    before_sent, before_edits = len(world.publisher.sent), len(world.publisher.edited)

    with pytest.raises(ValueError, match="no matching delivered owner"):
        send(case)

    assert len(world.publisher.sent) == before_sent
    assert len(world.publisher.edited) == before_edits
    assert row(world, root.id or 0).status is PublicationStatus.QUEUED
    assert row(world, root.id or 0).attempts == 0
    assert row(world, case.card.id or 0) == case.card


def test_card_revision_keeps_its_logical_owner_when_the_physical_parent_belongs_to_a_predecessor(
    tmp_path: Path,
) -> None:
    case = saved_edit(attempted=False)
    world = case.world
    predecessor = world.repo.create_publication(
        Publication(
            term=TERM,
            number="rcl:12345",
            kind=PublicationKind.NEW_BILL,
            channel_id=CHANNEL,
            status=PublicationStatus.SENT,
            message_id=case.card.message_id,
            created_at=world.clock.now(),
        )
    )
    root = row(world, case.identities[0])
    assert root.delivery is not None, "the new style revision saves its logical owner"
    assert world.repo.replace_unattempted_delivery(
        root.id or 0, root.delivery.model_copy(update={"continuation_parent": predecessor})
    )
    remote = connect(world, tmp_path)

    assert send(case)

    durable = World()
    durable.repo.restore(git(remote, "show", "state:state.sql"))
    assert row(durable, case.card.id or 0).rendered_sha256 == DESIRED
    assert row(durable, predecessor).rendered_sha256 is None
