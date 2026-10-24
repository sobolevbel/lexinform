import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

from lexinform.errors import DeliveryUncertainError
from lexinform.models import DeliveryPlan, Publication, PublicationKind, PublicationStatus
from lexinform.ports import PublishResult
from lexinform.services.card_edits import edit_card
from tests.harness import CHANNEL, TERM, World
from tests.scenario.test_card_edit_multipart import edition, prepare
from tests.scenario.test_card_remote_checkpoints import Mode, connect, restored
from tests.scenario.test_single_card_revision_producer import card, change, ready

Shape = Literal["single_many", "many_same"]


@dataclass(frozen=True)
class Retained:
    world: World
    root: Publication
    dismissed: Publication
    targets: tuple[int, ...]
    today: dt.date


@pytest.fixture(autouse=True)
def local_git_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")


def prune_and_clone(world: World, remote: Path, directory: Path) -> World:
    world.clock.advance(days=91)
    world.repo.prune_history(
        before=world.clock.now() - dt.timedelta(days=90),
        memo_before=world.clock.now() - dt.timedelta(days=180),
    )
    world.container.state_checkpoint()()
    fresh = restored(remote, world, directory)
    fresh.clock.current = world.clock.now()
    return fresh


def retained_dismissal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode, shape: Shape
) -> Retained:
    case = prepare(mode, shape)
    world = case.world
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared
    attempted: list[int] = []

    def uncertain_last(publication: Publication) -> PublishResult:
        assert publication.id is not None, "the producer saves physical identities before API"
        attempted.append(publication.id)
        if len(attempted) == len(case.texts):
            raise DeliveryUncertainError("the test knows no receipt for the final operation")
        return original(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", uncertain_last)
    world.container.tracking_service(dry_run=False).check_bill(world.bill("3039"))
    parts = edition(world)
    assert (
        parts[0].status is PublicationStatus.SENT and parts[-1].status is PublicationStatus.UNKNOWN
    )
    world.command(f"/delivery {parts[-1].id} dismiss checked absent")
    commands = world.container.command_service(dry_run=False)
    assert commands is not None, "the production fixture enables the audited command inbox"
    assert commands.handle_pending(min_score=3, run_started_at=world.clock.now()).failed == 0
    monkeypatch.setattr(world.publisher, "publish_prepared", original)
    settled = edition(world)
    assert settled[0].delivery is not None and settled[0].delivery.today is not None, (
        "the immutable edition saved its original reader day"
    )
    today = settled[0].delivery.today
    targets = tuple(
        target
        for part in settled
        for target in (
            part.message_id
            if part.status is PublicationStatus.SENT
            else part.delivery.edit_message_id
            if part.delivery is not None
            else None,
        )
        if target is not None
    )
    fresh = prune_and_clone(world, remote, tmp_path)
    pruned = edition(fresh)
    assert pruned[0].delivery is None, "retention clears the settled SENT root after 91 days"
    assert pruned[-1].delivery is not None and pruned[-1].status is PublicationStatus.DISMISSED
    return Retained(fresh, pruned[0], pruned[-1], targets, today)


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("shape", ["single_many", "many_same"])
def test_pruned_sent_root_and_dismissed_child_keep_same_hash_stopped_after_fresh_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode, shape: Shape
) -> None:
    case = retained_dismissal(tmp_path, monkeypatch, mode, shape)
    world = case.world
    original_card = card(world)
    before = len(world.publisher.edited), len(world.publisher.sent)

    assert case.root.rendered_sha256 is not None, (
        "the saved desired digest survives payload retention"
    )
    first = edit_card(
        world.repo,
        world.clock,
        world.publisher,
        world.bill("3039"),
        original_card,
        None,
        channel_id=CHANNEL,
        checkpoint=world.container.state_checkpoint(),
        digest=case.root.rendered_sha256,
        today=case.today,
    )
    second = edit_card(
        world.repo,
        world.clock,
        world.publisher,
        world.bill("3039"),
        original_card,
        None,
        channel_id=CHANNEL,
        checkpoint=world.container.state_checkpoint(),
        digest=case.root.rendered_sha256,
        today=case.today,
    )

    assert not first.completed and not second.completed
    assert (len(world.publisher.edited), len(world.publisher.sent)) == before
    assert edition(world)[0] == case.root and edition(world)[-1] == case.dismissed
    assert card(world) == original_card
    change(world, "Иная подтверждённая редакция: только 900 PLN.")
    observed: list[int | None] = []
    original = world.publisher.publish_prepared

    def inspect(publication: Publication) -> PublishResult:
        assert publication.delivery is not None, "the next edition freezes all physical targets"
        observed.append(publication.delivery.edit_message_id)
        return original(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)

    new = world.container.tracking_service(dry_run=False).check_bill(world.bill("3039"))

    assert new.cards_refreshed == 1
    assert tuple(observed) == case.targets
    assert len(world.publisher.sent) == before[1]
    assert all(part.status is PublicationStatus.SENT for part in edition(world))
    assert card(world).rendered_sha256 == world.publisher.card_digest(world.bill("3039"))
    assert world.repo.publication_by_id(case.root.id or 0) == case.root
    assert world.repo.publication_by_id(case.dismissed.id or 0) == case.dismissed


@pytest.mark.parametrize("invalid", ["channel", "owner", "target"])
def test_an_unrelated_dismissed_ref_cannot_suppress_the_real_card_edition(invalid: str) -> None:
    world = ready()
    owner = card(world)
    desired = world.publisher.card_digest(world.bill("3039"))
    identity = world.repo.create_publication(
        Publication(
            term=TERM,
            number="3039",
            kind=PublicationKind.MESSAGE_PART,
            status=PublicationStatus.DISMISSED,
            channel_id="@unrelated" if invalid == "channel" else CHANNEL,
            created_at=world.clock.now(),
            ref=f"card/{owner.id}/unrelated-{invalid}/1",
            rendered_sha256=desired,
            delivery=DeliveryPlan(
                bill_json=world.bill("3039").model_dump_json(),
                rendered_text="Чужая отклонённая операция",
                revision_of=900000 if invalid == "owner" else owner.id,
                edit_message_id=(owner.message_id or 0) + 900
                if invalid == "target"
                else owner.message_id,
            ),
        )
    )
    unrelated = world.repo.publication_by_id(identity)
    world.repo.set_card_digest(identity, desired)
    unrelated = world.repo.publication_by_id(identity)
    before = len(world.publisher.edited)

    result = world.container.tracking_service(dry_run=False).check_bill(world.bill("3039"))

    assert result.cards_refreshed == 1
    assert len(world.publisher.edited) == before + 1
    assert world.repo.publication_by_id(identity) == unrelated


@pytest.mark.parametrize("invalid", ["channel", "owner", "root", "parent"])
def test_pruned_unvalidated_child_cannot_redirect_a_new_edition_to_an_unrelated_target(
    tmp_path: Path, invalid: str
) -> None:
    world = ready()
    owner = card(world)
    remote = connect(world, tmp_path)
    root = world.repo.create_publication(
        Publication(
            term=TERM,
            number="3039",
            kind=PublicationKind.MESSAGE_PART,
            channel_id=CHANNEL,
            status=PublicationStatus.QUEUED,
            ref=f"card/{owner.id}/unvalidated/1",
            created_at=world.clock.now(),
            rendered_sha256=world.publisher.card_digest(world.bill("3039")),
            delivery=DeliveryPlan(
                bill_json=world.bill("3039").model_dump_json(),
                rendered_text="Подтверждённая первая часть",
                revision_of=owner.id,
                edit_message_id=owner.message_id,
            ),
        )
    )
    unrelated_target = (owner.message_id or 0) + 900
    child = world.repo.create_publication(
        Publication(
            term=TERM,
            number="unrelated" if invalid == "owner" else "3039",
            kind=PublicationKind.MESSAGE_PART,
            channel_id="@unrelated" if invalid == "channel" else CHANNEL,
            status=PublicationStatus.DISMISSED,
            ref=f"card/{owner.id}/unvalidated/2",
            created_at=world.clock.now(),
            delivery=DeliveryPlan(
                bill_json=world.bill("3039").model_dump_json(),
                rendered_text="Чужая физическая часть",
                edit_message_id=unrelated_target,
                continuation_root=root + 900 if invalid == "root" else root,
                continuation_parent=root + 900 if invalid == "parent" else root,
                continuation_index=2,
            ),
        )
    )
    saved = world.repo.publication_by_id(root)
    assert saved is not None and saved.delivery is not None, (
        "the root is queued before membership freezes"
    )
    assert world.repo.replace_unattempted_delivery(
        root, saved.delivery.model_copy(update={"continuation_ids": (child,)})
    )
    world.repo.mark_publication(
        root,
        PublicationStatus.SENT,
        message_id=owner.message_id,
        sent_at=world.clock.now(),
        count_attempt=False,
    )
    fresh = prune_and_clone(world, remote, tmp_path)
    assert edition(fresh)[0].delivery is None
    change(fresh, "Другая редакция: только 1200 PLN.")
    before = len(world.publisher.edited), len(world.publisher.sent)

    result = fresh.container.tracking_service(dry_run=False).check_bill(fresh.bill("3039"))

    assert result.cards_refreshed == 0
    assert (len(world.publisher.edited), len(world.publisher.sent)) == before
    assert fresh.repo.publication_by_id(child) is not None
