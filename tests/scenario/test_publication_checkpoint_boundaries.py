from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

import pytest

from lexinform.errors import (
    DeliveryUncertainError,
    StateCheckpointError,
    TelegramUnavailableError,
)
from lexinform.models import (
    DeliveryPlan,
    Publication,
    PublicationKind,
    PublicationStatus,
    StatusChange,
)
from lexinform.ports import PublishResult
from lexinform.services.publications import send_publication, send_publications
from tests.fakes import FakePublishResult
from tests.harness import CHANNEL, TERM, World

Outcome = Literal["success", "definite_failure", "outage", "unknown"]


def frozen_pending(world: World, number: str) -> tuple[int, int]:
    bill = world.bill(number)
    held_change = world.repo.add_status_change(
        StatusChange(
            term=TERM,
            number=number,
            old_fingerprint=None,
            new_fingerprint="held",
            new_stages=[],
            detected_at=world.clock.now(),
        )
    )
    assert held_change is not None, "each fixture bill receives one new held change"
    held = world.repo.create_publication(
        Publication(
            term=TERM,
            number=number,
            kind=PublicationKind.STATUS_UPDATE,
            status=PublicationStatus.SKIPPED,
            channel_id=CHANNEL,
            status_change_id=held_change,
            created_at=world.clock.now(),
        )
    )
    publication = world.repo.create_publication(
        Publication(
            term=TERM,
            number=number,
            kind=PublicationKind.NEW_BILL,
            status=PublicationStatus.PENDING,
            channel_id=CHANNEL,
            created_at=world.clock.now(),
            delivery=DeliveryPlan(
                bill_json=bill.model_dump_json(),
                rendered_text=f"<b>Frozen {number}</b>",
                held_change_ids=(held_change,),
            ),
        )
    )
    return publication, held


def pending_world(*, group: bool = False) -> tuple[World, tuple[int, ...], tuple[int, ...]]:
    world = World()
    numbers = ("3039", "3040") if group else ("3039",)
    for number in numbers:
        world.add_bill(number, "Projekt ustawy o cudzoziemcach")
    world.run(max_publish=0)
    pairs = [frozen_pending(world, number) for number in numbers]
    return world, tuple(pair[0] for pair in pairs), tuple(pair[1] for pair in pairs)


def stored(world: World, identities: tuple[int, ...]) -> tuple[Publication, ...]:
    rows: list[Publication] = []
    for identity in identities:
        row = world.repo.publication_by_id(identity)
        assert row is not None, "the fixture stores every requested publication before sending"
        rows.append(row)
    return tuple(rows)


def perform(outcome: Outcome) -> PublishResult:
    if outcome == "definite_failure":
        raise ValueError("saved payload rejected")
    if outcome == "outage":
        raise TelegramUnavailableError("rate limited")
    if outcome == "unknown":
        raise DeliveryUncertainError("response lost after possible acceptance")
    return FakePublishResult(777, document_message_ids=[778, 779])


@pytest.mark.parametrize("group", [False, True])
def test_success_checkpoints_frozen_pending_then_receipts_and_effects_outside_transactions(
    group: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, identities, held = pending_world(group=group)
    atomic = world.repo.atomic
    depth = 0
    snapshots: list[str] = []
    order: list[str] = []

    @contextmanager
    def tracked_atomic() -> Iterator[None]:
        nonlocal depth
        depth += 1
        try:
            with atomic():
                yield
        finally:
            depth -= 1

    def checkpoint() -> None:
        assert depth == 0, "the durable checkpoint must run after every local transaction closes"
        rows = stored(world, identities)
        order.append(f"checkpoint/{rows[0].status}")
        snapshots.append(world.repo.dump())

    def send() -> PublishResult:
        assert depth == 0, "the external effect must run outside every local transaction"
        assert len(snapshots) == 1
        for row in stored(world, identities):
            assert row.status is PublicationStatus.PENDING
            assert row.delivery is not None, "each pending physical send has its saved payload"
            assert row.delivery.rendered_text == f"<b>Frozen {row.number}</b>"
            assert row.channel_id == CHANNEL
        assert all(row.status is PublicationStatus.SKIPPED for row in stored(world, held))
        order.append("api")
        return perform("success")

    monkeypatch.setattr(world.repo, "atomic", tracked_atomic)

    result = (
        send_publications(world.repo, world.clock, identities, send, checkpoint=checkpoint)
        if group
        else send_publication(world.repo, world.clock, identities[0], send, checkpoint=checkpoint)
    )

    assert result == 777
    assert order == ["checkpoint/pending", "api", "checkpoint/sent"]
    restored = World()
    restored.repo.restore(snapshots[0])
    assert all(row.status is PublicationStatus.PENDING for row in stored(restored, identities))
    assert all(row.status is PublicationStatus.SKIPPED for row in stored(restored, held))
    restored.repo.restore(snapshots[1])
    for row in stored(restored, identities):
        assert row.status is PublicationStatus.SENT
        assert row.message_id == 777 and row.document_message_ids == [778, 779]
        assert row.sent_at == world.clock.now() and row.attempts == 0
    assert all(
        row.status is PublicationStatus.SENT and row.message_id == 777
        for row in stored(restored, held)
    )


@pytest.mark.parametrize("outcome", ["definite_failure", "outage", "unknown"])
@pytest.mark.parametrize("group", [False, True])
def test_delivery_errors_checkpoint_the_whole_group_without_releasing_effects(
    outcome: Outcome, group: bool
) -> None:
    world, identities, held = pending_world(group=group)
    snapshots: list[str] = []
    sends: list[Outcome] = []

    def checkpoint() -> None:
        snapshots.append(world.repo.dump())

    def send() -> PublishResult:
        sends.append(outcome)
        return perform(outcome)

    if outcome == "definite_failure":
        assert (
            send_publications(world.repo, world.clock, identities, send, checkpoint=checkpoint)
            is None
        )
    else:
        error = TelegramUnavailableError if outcome == "outage" else DeliveryUncertainError
        with pytest.raises(error):
            send_publications(world.repo, world.clock, identities, send, checkpoint=checkpoint)

    assert sends == [outcome] and len(snapshots) == 2
    restored = World()
    restored.repo.restore(snapshots[-1])
    for row in stored(restored, identities):
        assert row.status is (
            PublicationStatus.UNKNOWN if outcome == "unknown" else PublicationStatus.FAILED
        )
        assert row.attempts == int(outcome == "definite_failure")
        assert row.message_id is None and row.sent_at is None
        assert row.error
    assert all(row.status is PublicationStatus.SKIPPED for row in stored(restored, held))


def test_pre_send_checkpoint_failure_prevents_the_api_and_preserves_pending_identity() -> None:
    world, identities, held = pending_world(group=True)
    before = world.repo.dump()
    sends: list[int] = []

    def checkpoint() -> None:
        raise StateCheckpointError("pending snapshot refused")

    def send() -> int:
        sends.append(777)
        return 777

    with pytest.raises(StateCheckpointError, match="pending snapshot refused"):
        send_publications(world.repo, world.clock, identities, send, checkpoint=checkpoint)

    assert not sends and world.repo.dump() == before
    assert all(
        row.status is PublicationStatus.PENDING and row.attempts == 0
        for row in stored(world, identities)
    )
    assert all(row.status is PublicationStatus.SKIPPED for row in stored(world, held))


@pytest.mark.parametrize("outcome", ["success", "definite_failure", "outage", "unknown"])
def test_outcome_checkpoint_failure_is_fatal_without_reclassifying_delivery(
    outcome: Outcome,
) -> None:
    world, identities, held = pending_world(group=True)
    snapshots: list[str] = []
    sends: list[Outcome] = []

    def checkpoint() -> None:
        if snapshots:
            raise StateCheckpointError("outcome snapshot refused")
        snapshots.append(world.repo.dump())

    def send() -> PublishResult:
        sends.append(outcome)
        return perform(outcome)

    with pytest.raises(StateCheckpointError, match="outcome snapshot refused"):
        send_publications(world.repo, world.clock, identities, send, checkpoint=checkpoint)

    assert sends == [outcome]
    expected = (
        PublicationStatus.SENT
        if outcome == "success"
        else PublicationStatus.UNKNOWN
        if outcome == "unknown"
        else PublicationStatus.FAILED
    )
    for row in stored(world, identities):
        assert row.status is expected
        assert row.attempts == int(outcome == "definite_failure")
        assert row.message_id == (777 if outcome == "success" else None)
    assert all(
        row.status
        is (PublicationStatus.SENT if outcome == "success" else PublicationStatus.SKIPPED)
        for row in stored(world, held)
    )
    restored = World()
    restored.repo.restore(snapshots[0])
    restored.repo.mark_stale_pending_as_unknown(now=world.clock.now())
    assert all(
        row.status is PublicationStatus.UNKNOWN and row.attempts == 0
        for row in stored(restored, identities)
    )
    assert not restored.repo.list_due_deliveries(CHANNEL, max_attempts=3)


@pytest.mark.parametrize("with_checkpoint", [False, True])
def test_typed_persistence_failure_from_send_is_not_a_definite_delivery_failure(
    with_checkpoint: bool,
) -> None:
    world, identities, _ = pending_world()
    before = world.repo.dump()
    snapshots: list[str] = []

    def checkpoint() -> None:
        snapshots.append(world.repo.dump())

    def send() -> PublishResult:
        raise StateCheckpointError("nested persistence failure")

    with pytest.raises(StateCheckpointError, match="nested persistence failure"):
        send_publication(
            world.repo,
            world.clock,
            identities[0],
            send,
            checkpoint=checkpoint if with_checkpoint else None,
        )

    assert world.repo.dump() == before
    assert len(snapshots) == int(with_checkpoint)


def test_integer_receipt_keeps_legacy_optional_checkpoint_callers_compatible() -> None:
    world, identities, _ = pending_world()

    assert send_publication(world.repo, world.clock, identities[0], lambda: 777) == 777

    publication = stored(world, identities)[0]
    assert publication.status is PublicationStatus.SENT
    assert publication.message_id == 777 and publication.document_message_ids == []
