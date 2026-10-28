from datetime import datetime

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.errors import (
    DeliveryUncertainError,
    ServiceUnavailableError,
    TelegramUnavailableError,
)
from lexinform.models import (
    DeliveryPlan,
    ProcessDetail,
    Publication,
    PublicationKind,
    PublicationStatus,
)
from lexinform.ports import PublishResult
from lexinform.services.message_parts import continuation_ready, freeze_parts, send_continuations
from tests.fakes import FakePublisher, FixedClock


def pair(
    repo: SqliteBillRepository,
    process: ProcessDetail,
    now: datetime,
    *,
    parent_status: PublicationStatus = PublicationStatus.SENT,
    parent_message_id: int | None = 777,
) -> tuple[Publication, Publication]:
    bill = repo.upsert_summary(process, now=now)
    parent = Publication(
        term=10,
        number="3039",
        kind=PublicationKind.NEW_BILL,
        status=parent_status,
        channel_id="@test",
        message_id=parent_message_id,
        created_at=now,
    )
    parent = parent.model_copy(update={"id": repo.create_publication(parent)})
    child = Publication(
        term=10,
        number="3039",
        kind=PublicationKind.MESSAGE_PART,
        status=PublicationStatus.QUEUED,
        channel_id="@test",
        ref=f"message/{parent.id}/2",
        created_at=now,
        delivery=DeliveryPlan(
            bill_json=bill.model_dump_json(),
            rendered_text="Frozen continuation",
            continuation_parent=parent.id,
            continuation_root=parent.id,
            continuation_index=2,
        ),
    )
    return parent, child.model_copy(update={"id": repo.create_publication(child)})


def test_ready_continuation_returns_the_same_parent_binding_that_was_persisted(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    parent, child = pair(repo, process_3039, now)

    ready = continuation_ready(repo, child)

    assert ready is not None and ready.delivery is not None, (
        "the confirmed parent permits binding this queued continuation"
    )
    assert ready.delivery.reply_to == parent.message_id == 777
    assert child.delivery is not None and child.delivery.reply_to is None, (
        "the caller's frozen input has not been rewritten in place"
    )
    assert child.id is not None, "pair persists the queued continuation"
    assert repo.publication_by_id(child.id) == ready


@pytest.mark.parametrize(
    "status,message_id",
    [
        (PublicationStatus.QUEUED, 777),
        (PublicationStatus.FAILED, 777),
        (PublicationStatus.UNKNOWN, 777),
        (PublicationStatus.SENT, None),
    ],
)
def test_only_a_confirmed_parent_with_a_message_id_unlocks_its_child(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    now: datetime,
    status: PublicationStatus,
    message_id: int | None,
) -> None:
    _, child = pair(repo, process_3039, now, parent_status=status, parent_message_id=message_id)
    before = repo.dump()

    assert continuation_ready(repo, child) is None

    assert repo.dump() == before


@pytest.mark.parametrize("missing", [None, 999])
def test_an_orphan_never_borrows_the_first_publications_parent(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime, missing: int | None
) -> None:
    _, child = pair(repo, process_3039, now)
    assert child.id is not None and child.delivery is not None, "pair persists its delivery plan"
    plan = child.delivery.model_copy(update={"continuation_parent": missing})
    assert repo.replace_unattempted_delivery(child.id, plan)
    child = child.model_copy(update={"delivery": plan})
    before = repo.dump()

    assert continuation_ready(repo, child) is None

    assert repo.dump() == before


def test_an_existing_reply_target_survives_a_different_parent_receipt(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    _, child = pair(repo, process_3039, now)
    assert child.id is not None and child.delivery is not None, "pair persists its delivery plan"
    plan = child.delivery.model_copy(update={"reply_to": 888})
    assert repo.replace_unattempted_delivery(child.id, plan)
    child = child.model_copy(update={"delivery": plan})
    before = repo.dump()

    assert continuation_ready(repo, child) == child

    assert repo.dump() == before


def test_a_stale_queued_snapshot_cannot_rewrite_an_already_pending_part(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    _, child = pair(repo, process_3039, now)
    assert child.id is not None, "pair persists the continuation before its attempt begins"
    repo.mark_publication(child.id, PublicationStatus.PENDING, count_attempt=False)
    before = repo.dump()

    assert continuation_ready(repo, child) is None

    assert repo.dump() == before


def unfrozen_root(
    repo: SqliteBillRepository, process: ProcessDetail, now: datetime
) -> tuple[Publication, FakePublisher]:
    bill = repo.upsert_summary(process, now=now)
    root = Publication(
        term=bill.term,
        number=bill.number,
        kind=PublicationKind.NEW_BILL,
        channel_id="@test",
        status=PublicationStatus.QUEUED,
        created_at=now,
        delivery=DeliveryPlan(bill_json=bill.model_dump_json(), held_change_ids=(123, 456)),
    )
    root = root.model_copy(update={"id": repo.create_publication(root)})
    publisher = FakePublisher(publication_by_id=repo.publication_by_id)
    return root, publisher


def frozen_chain(
    repo: SqliteBillRepository, process: ProcessDetail, now: datetime
) -> tuple[Publication, FakePublisher]:
    root, publisher = unfrozen_root(repo, process, now)
    root = freeze_parts(repo, FixedClock(now), publisher, root, "A fact. " * 1200)
    assert root.id is not None, "the root was persisted before freezing its physical parts"
    repo.mark_publication(root.id, PublicationStatus.SENT, message_id=777, count_attempt=False)
    return root.model_copy(update={"status": PublicationStatus.SENT, "message_id": 777}), publisher


def test_a_frozen_payload_and_membership_survive_a_later_rendering(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    root, publisher = frozen_chain(repo, process_3039, now)
    before = repo.dump()

    assert freeze_parts(repo, FixedClock(now), publisher, root, "A replacement.") == root

    assert repo.dump() == before


def test_a_refused_root_freeze_rolls_back_every_new_physical_child(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    root, publisher = unfrozen_root(repo, process_3039, now)
    before = repo.dump()
    refused: list[int] = []

    def persist(identity: int, plan: DeliveryPlan) -> bool:
        refused.append(identity)
        assert plan.continuation_ids
        return False

    with pytest.raises(ValueError):
        freeze_parts(repo, FixedClock(now), publisher, root, "A fact. " * 1200, persist=persist)

    assert refused == [root.id]
    assert repo.dump() == before


def test_a_conflicting_legacy_child_cannot_be_adopted_by_a_new_freeze(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    root, publisher = unfrozen_root(repo, process_3039, now)
    assert root.delivery is not None, "unfrozen_root saves the original bill snapshot"
    repo.create_publication(
        Publication(
            term=root.term,
            number=root.number,
            kind=PublicationKind.MESSAGE_PART,
            channel_id=root.channel_id,
            ref=f"message/{root.id}/2",
            status=PublicationStatus.QUEUED,
            created_at=now,
            delivery=DeliveryPlan(
                bill_json=root.delivery.bill_json,
                rendered_text="A different frozen physical message.",
            ),
        )
    )
    before = repo.dump()
    persisted: list[int] = []

    def persist(identity: int, plan: DeliveryPlan) -> bool:
        persisted.append(identity)
        return repo.replace_unattempted_delivery(identity, plan)

    with pytest.raises(ValueError):
        freeze_parts(repo, FixedClock(now), publisher, root, "A fact. " * 1200, persist=persist)

    assert not persisted and repo.dump() == before


def test_only_the_last_frozen_part_owns_the_saved_held_changes(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    root, _ = frozen_chain(repo, process_3039, now)
    assert root.delivery is not None, "frozen_chain stores the complete physical membership"
    assert root.delivery.held_change_ids == ()
    children = [repo.publication_by_id(identity) for identity in root.delivery.continuation_ids]
    assert len(children) >= 2
    parent = root.id
    for index, child in enumerate(children):
        assert child is not None and child.delivery is not None, "freeze_parts stores each child"
        assert child.delivery.held_change_ids == ((123, 456) if index == len(children) - 1 else ())
        assert child.delivery.continuation_index == index + 2
        assert child.delivery.continuation_parent == parent
        parent = child.id


def test_a_sent_prefix_is_skipped_and_its_next_part_uses_the_saved_receipt(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    root, publisher = frozen_chain(repo, process_3039, now)
    assert root.delivery is not None, "the frozen root owns its saved physical membership"
    first, *following = root.delivery.continuation_ids
    repo.mark_publication(first, PublicationStatus.SENT, message_id=888, count_attempt=False)

    result = send_continuations(repo, FixedClock(now), publisher, root, set())

    assert result.sent == len(following) and result.errors == []
    assert len(publisher.sent) == len(following)
    assert publisher.sent[0].reply_to == 888
    saved = repo.publication_by_id(first)
    assert saved is not None, "skipping a confirmed prefix retains its stored receipt"
    assert saved.message_id == 888


@pytest.mark.parametrize("blocked", [PublicationStatus.UNKNOWN, PublicationStatus.FAILED])
def test_an_unresolved_part_stops_the_chain_and_returns_a_result(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    now: datetime,
    blocked: PublicationStatus,
) -> None:
    root, publisher = frozen_chain(repo, process_3039, now)
    assert root.delivery is not None, "the frozen root owns its saved physical membership"
    repo.mark_publication(root.delivery.continuation_ids[0], blocked, count_attempt=False)
    before = repo.dump()

    result = send_continuations(repo, FixedClock(now), publisher, root, set())

    assert result.sent == 0 and result.errors == []
    assert not publisher.sent and repo.dump() == before


def test_a_missing_parent_receipt_stops_without_attempting_the_child(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    root, publisher = frozen_chain(repo, process_3039, now)
    assert root.id is not None, "the frozen root has a stored identity"
    repo.mark_publication(root.id, PublicationStatus.UNKNOWN, count_attempt=False)
    before = repo.dump()

    result = send_continuations(repo, FixedClock(now), publisher, root, set())

    assert result.sent == 0 and result.errors == []
    assert not publisher.sent and repo.dump() == before


@pytest.mark.parametrize(
    "error", [None, TelegramUnavailableError("offline"), DeliveryUncertainError("lost receipt")]
)
def test_continuations_checkpoint_pending_without_attempts_and_keep_partial_errors(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    now: datetime,
    monkeypatch: pytest.MonkeyPatch,
    error: ServiceUnavailableError | None,
) -> None:
    root, publisher = frozen_chain(repo, process_3039, now)
    assert root.delivery is not None, "the frozen root owns its saved physical membership"
    identities = root.delivery.continuation_ids
    checkpoints: list[str] = []
    sends: list[int] = []
    attempted: set[int] = set()
    original = publisher.publish_prepared

    def checkpoint() -> None:
        checkpoints.append(repo.dump())

    def send(publication: Publication) -> PublishResult:
        assert publication.id is not None, "send_continuations passes a stored physical part"
        saved = repo.publication_by_id(publication.id)
        assert saved is not None, "the physical part exists before the external effect"
        assert saved.status is PublicationStatus.PENDING and saved.attempts == 0
        assert publication.id in attempted
        assert len(checkpoints) == 2 * len(sends) + 1
        sends.append(publication.id)
        if error is not None:
            raise error
        return original(publication)

    monkeypatch.setattr(publisher, "publish_prepared", send)

    result = send_continuations(
        repo, FixedClock(now), publisher, root, attempted, checkpoint=checkpoint
    )

    assert sends == list(identities if error is None else identities[:1])
    assert len(checkpoints) == len(sends) * 2
    assert result.sent == (len(identities) if error is None else 0)
    if error is not None:
        assert result.errors == [
            f"Continuation {identities[0]}: {error.describe()}; inspect /delivery {identities[0]}"
        ]
    else:
        assert result.errors == []
