from datetime import datetime

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    DeliveryPlan,
    ProcessDetail,
    Publication,
    PublicationKind,
    PublicationStatus,
)
from lexinform.services.message_parts import continuation_ready


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
