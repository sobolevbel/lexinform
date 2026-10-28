from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.errors import (
    DeliveryUncertainError,
    StateCheckpointError,
    TelegramUnavailableError,
)
from lexinform.models import (
    Bill,
    DeliveryPlan,
    ProcessDetail,
    Publication,
    PublicationKind,
    PublicationStatus,
    StatusChange,
)
from lexinform.ports import PublishResult
from lexinform.services.publications import complete_delivery, send_publication, send_publications
from tests.database import empty_repository
from tests.fakes import FakePublishResult, FixedClock

CHANNEL = "@test"
TERM = 10

Outcome = Literal["success", "definite_failure", "outage", "unknown"]


@dataclass(frozen=True)
class PendingCase:
    repo: SqliteBillRepository
    clock: FixedClock


def frozen_pending(case: PendingCase, bill: Bill) -> tuple[int, int]:
    number = bill.number
    held_change = case.repo.add_status_change(
        StatusChange(
            term=TERM,
            number=number,
            old_fingerprint=None,
            new_fingerprint="held",
            new_stages=[],
            detected_at=case.clock.now(),
        )
    )
    assert held_change is not None, "each fixture bill receives one new held change"
    held = case.repo.create_publication(
        Publication(
            term=TERM,
            number=number,
            kind=PublicationKind.STATUS_UPDATE,
            status=PublicationStatus.SKIPPED,
            channel_id=CHANNEL,
            status_change_id=held_change,
            created_at=case.clock.now(),
        )
    )
    publication = case.repo.create_publication(
        Publication(
            term=TERM,
            number=number,
            kind=PublicationKind.NEW_BILL,
            status=PublicationStatus.PENDING,
            channel_id=CHANNEL,
            created_at=case.clock.now(),
            delivery=DeliveryPlan(
                bill_json=bill.model_dump_json(),
                rendered_text=f"<b>Frozen {number}</b>",
                held_change_ids=(held_change,),
            ),
        )
    )
    return publication, held


def pending_case(
    repo: SqliteBillRepository, process: ProcessDetail, now: datetime, *, group: bool = False
) -> tuple[PendingCase, tuple[int, ...], tuple[int, ...]]:
    case = PendingCase(repo, FixedClock(now))
    numbers = ("3039", "3040") if group else ("3039",)
    bills = [
        repo.upsert_summary(process.model_copy(update={"number": number}), now=now)
        for number in numbers
    ]
    pairs = [frozen_pending(case, bill) for bill in bills]
    return case, tuple(pair[0] for pair in pairs), tuple(pair[1] for pair in pairs)


def stored(case: PendingCase, identities: tuple[int, ...]) -> tuple[Publication, ...]:
    rows: list[Publication] = []
    for identity in identities:
        row = case.repo.publication_by_id(identity)
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
    group: bool,
    monkeypatch: pytest.MonkeyPatch,
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    now: datetime,
) -> None:
    case, identities, held = pending_case(repo, process_3039, now, group=group)
    atomic = case.repo.atomic
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
        rows = stored(case, identities)
        order.append(f"checkpoint/{rows[0].status}")
        snapshots.append(case.repo.dump())

    def send() -> PublishResult:
        assert depth == 0, "the external effect must run outside every local transaction"
        assert len(snapshots) == 1
        for row in stored(case, identities):
            assert row.status is PublicationStatus.PENDING
            assert row.delivery is not None, "each pending physical send has its saved payload"
            assert row.delivery.rendered_text == f"<b>Frozen {row.number}</b>"
            assert row.channel_id == CHANNEL
        assert all(row.status is PublicationStatus.SKIPPED for row in stored(case, held))
        order.append("api")
        return perform("success")

    monkeypatch.setattr(case.repo, "atomic", tracked_atomic)

    result = (
        send_publications(case.repo, case.clock, identities, send, checkpoint=checkpoint)
        if group
        else send_publication(case.repo, case.clock, identities[0], send, checkpoint=checkpoint)
    )

    assert result == 777
    assert order == ["checkpoint/pending", "api", "checkpoint/sent"]
    restored = PendingCase(empty_repository(), case.clock)
    restored.repo.restore(snapshots[0])
    assert all(row.status is PublicationStatus.PENDING for row in stored(restored, identities))
    assert all(row.status is PublicationStatus.SKIPPED for row in stored(restored, held))
    restored.repo.restore(snapshots[1])
    for row in stored(restored, identities):
        assert row.status is PublicationStatus.SENT
        assert row.message_id == 777 and row.document_message_ids == [778, 779]
        assert row.sent_at == case.clock.now() and row.attempts == 0
    assert all(
        row.status is PublicationStatus.SENT and row.message_id == 777
        for row in stored(restored, held)
    )


@pytest.mark.parametrize("outcome", ["definite_failure", "outage", "unknown"])
@pytest.mark.parametrize("group", [False, True])
def test_delivery_errors_checkpoint_the_whole_group_without_releasing_effects(
    outcome: Outcome,
    group: bool,
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    now: datetime,
) -> None:
    case, identities, held = pending_case(repo, process_3039, now, group=group)
    snapshots: list[str] = []
    sends: list[Outcome] = []

    def checkpoint() -> None:
        snapshots.append(case.repo.dump())

    def send() -> PublishResult:
        sends.append(outcome)
        return perform(outcome)

    if outcome == "definite_failure":
        assert (
            send_publications(case.repo, case.clock, identities, send, checkpoint=checkpoint)
            is None
        )
    else:
        error = TelegramUnavailableError if outcome == "outage" else DeliveryUncertainError
        with pytest.raises(error):
            send_publications(case.repo, case.clock, identities, send, checkpoint=checkpoint)

    assert sends == [outcome] and len(snapshots) == 2
    restored = PendingCase(empty_repository(), case.clock)
    restored.repo.restore(snapshots[-1])
    for row in stored(restored, identities):
        assert row.status is (
            PublicationStatus.UNKNOWN if outcome == "unknown" else PublicationStatus.FAILED
        )
        assert row.attempts == int(outcome == "definite_failure")
        assert row.message_id is None and row.sent_at is None
        assert row.error
    assert all(row.status is PublicationStatus.SKIPPED for row in stored(restored, held))


def test_pre_send_checkpoint_failure_prevents_the_api_and_preserves_pending_identity(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    case, identities, held = pending_case(repo, process_3039, now, group=True)
    before = case.repo.dump()
    sends: list[int] = []

    def checkpoint() -> None:
        raise StateCheckpointError("pending snapshot refused")

    def send() -> int:
        sends.append(777)
        return 777

    with pytest.raises(StateCheckpointError, match="pending snapshot refused"):
        send_publications(case.repo, case.clock, identities, send, checkpoint=checkpoint)

    assert not sends and case.repo.dump() == before
    assert all(
        row.status is PublicationStatus.PENDING and row.attempts == 0
        for row in stored(case, identities)
    )
    assert all(row.status is PublicationStatus.SKIPPED for row in stored(case, held))


@pytest.mark.parametrize("outcome", ["success", "definite_failure", "outage", "unknown"])
def test_outcome_checkpoint_failure_is_fatal_without_reclassifying_delivery(
    outcome: Outcome,
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    now: datetime,
) -> None:
    case, identities, held = pending_case(repo, process_3039, now, group=True)
    snapshots: list[str] = []
    sends: list[Outcome] = []

    def checkpoint() -> None:
        if snapshots:
            raise StateCheckpointError("outcome snapshot refused")
        snapshots.append(case.repo.dump())

    def send() -> PublishResult:
        sends.append(outcome)
        return perform(outcome)

    with pytest.raises(StateCheckpointError, match="outcome snapshot refused"):
        send_publications(case.repo, case.clock, identities, send, checkpoint=checkpoint)

    assert sends == [outcome]
    expected = (
        PublicationStatus.SENT
        if outcome == "success"
        else PublicationStatus.UNKNOWN
        if outcome == "unknown"
        else PublicationStatus.FAILED
    )
    for row in stored(case, identities):
        assert row.status is expected
        assert row.attempts == int(outcome == "definite_failure")
        assert row.message_id == (777 if outcome == "success" else None)
    assert all(
        row.status
        is (PublicationStatus.SENT if outcome == "success" else PublicationStatus.SKIPPED)
        for row in stored(case, held)
    )
    restored = PendingCase(empty_repository(), case.clock)
    restored.repo.restore(snapshots[0])
    restored.repo.mark_stale_pending_as_unknown(now=case.clock.now())
    assert all(
        row.status is PublicationStatus.UNKNOWN and row.attempts == 0
        for row in stored(restored, identities)
    )
    assert not restored.repo.list_due_deliveries(CHANNEL, max_attempts=3)


@pytest.mark.parametrize("with_checkpoint", [False, True])
def test_typed_persistence_failure_from_send_is_not_a_definite_delivery_failure(
    with_checkpoint: bool,
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    now: datetime,
) -> None:
    case, identities, _ = pending_case(repo, process_3039, now)
    before = case.repo.dump()
    snapshots: list[str] = []

    def checkpoint() -> None:
        snapshots.append(case.repo.dump())

    def send() -> PublishResult:
        raise StateCheckpointError("nested persistence failure")

    with pytest.raises(StateCheckpointError, match="nested persistence failure"):
        send_publication(
            case.repo,
            case.clock,
            identities[0],
            send,
            checkpoint=checkpoint if with_checkpoint else None,
        )

    assert case.repo.dump() == before
    assert len(snapshots) == int(with_checkpoint)


def test_integer_receipt_keeps_legacy_optional_checkpoint_callers_compatible(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    case, identities, _ = pending_case(repo, process_3039, now)

    assert send_publication(case.repo, case.clock, identities[0], lambda: 777) == 777

    publication = stored(case, identities)[0]
    assert publication.status is PublicationStatus.SENT
    assert publication.message_id == 777 and publication.document_message_ids == []


@pytest.mark.parametrize(
    "notice_status", [None, PublicationStatus.QUEUED, PublicationStatus.FAILED]
)
def test_a_final_part_confirms_the_act_with_its_roots_receipt_and_completion_time(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    now: datetime,
    notice_status: PublicationStatus | None,
) -> None:
    bill = repo.upsert_summary(process_3039, now=now)
    plan = DeliveryPlan(
        bill_json=bill.model_dump_json(), rendered_text="Frozen event", with_act=True
    )
    change_id = repo.add_status_change(
        StatusChange(
            term=bill.term,
            number=bill.number,
            old_fingerprint=None,
            new_fingerprint="event-with-act",
            new_stages=[],
            detected_at=now,
        )
    )
    assert change_id is not None, "the fixture records the event before queuing its status update"
    root_id = repo.create_publication(
        Publication(
            term=bill.term,
            number=bill.number,
            kind=PublicationKind.STATUS_UPDATE,
            status=PublicationStatus.QUEUED,
            channel_id=CHANNEL,
            status_change_id=change_id,
            delivery=plan,
            created_at=now,
        )
    )
    child = Publication(
        term=bill.term,
        number=bill.number,
        kind=PublicationKind.MESSAGE_PART,
        status=PublicationStatus.SENT,
        channel_id=CHANNEL,
        ref=f"message/{root_id}/2",
        message_id=888,
        delivery=DeliveryPlan(
            bill_json=bill.model_dump_json(), rendered_text="Final part", continuation_root=root_id
        ),
        created_at=now,
    )
    child = child.model_copy(update={"id": repo.create_publication(child)})
    assert child.id is not None, "the final physical part is stored before freezing membership"
    assert repo.replace_unattempted_delivery(
        root_id, plan.model_copy(update={"continuation_ids": (child.id,)})
    )
    repo.mark_publication(root_id, PublicationStatus.SENT, message_id=777, count_attempt=False)
    notice_id = None
    if notice_status is not None:
        notice_id = repo.create_publication(
            Publication(
                term=bill.term,
                number=bill.number,
                kind=PublicationKind.ACT_PUBLISHED,
                status=notice_status,
                channel_id=CHANNEL,
                created_at=now - timedelta(days=1),
            )
        )

    complete_delivery(repo, child, message_id=888, sent_at=now)

    notice = repo.get_publication(bill.term, bill.number, PublicationKind.ACT_PUBLISHED, CHANNEL)
    assert notice is not None, "the complete status update also confirms its saved act notice"
    assert notice.status is PublicationStatus.SENT
    assert notice.message_id == 777 and notice.sent_at == now
    if notice_id is not None:
        assert notice.id == notice_id
        assert notice.created_at == now - timedelta(days=1)
