from datetime import UTC, datetime

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import Publication, PublicationKind, PublicationStatus
from tests.unit.test_direct_status_edit_freeze import frozen
from tests.unit.test_direct_status_reply_freeze import saved_reply


def sent_parent(repo: SqliteBillRepository, *, patch: dict[str, object] | None = None) -> int:
    return repo.create_publication(
        Publication(
            term=10,
            number="3039",
            kind=PublicationKind.NEW_BILL,
            status=PublicationStatus.SENT,
            channel_id="test-channel",
            message_id=777,
            created_at=datetime(2026, 10, 28, tzinfo=UTC),
        ).model_copy(update=patch or {})
    )


def test_missing_legacy_parent_is_filled_only_from_the_same_owners_sent_card(
    repo: SqliteBillRepository,
) -> None:
    root = saved_reply(repo, patch={"reply_to": None})
    assert root.id is not None, "saved_reply stores its physical identity"
    sent_parent(repo)
    delivery = frozen(root).model_copy(update={"reply_to": 777})

    assert repo.freeze_direct_delivery(root, delivery)

    assert repo.publication_by_id(root.id) == root.model_copy(update={"delivery": delivery})
    repo.restore(repo.dump())
    assert repo.publication_by_id(root.id) == root.model_copy(update={"delivery": delivery})


@pytest.mark.parametrize(
    "patch",
    [
        {"term": 11},
        {"number": "other"},
        {"channel_id": "other"},
        {"status": PublicationStatus.UNKNOWN},
        {"status": PublicationStatus.PENDING},
        {"status": PublicationStatus.FAILED},
        {"status": PublicationStatus.QUEUED},
        {"message_id": None},
        {"message_id": 0},
        {"message_id": 888},
    ],
)
def test_an_unconfirmed_foreign_or_changed_card_cannot_supply_the_new_parent(
    repo: SqliteBillRepository,
    patch: dict[str, object],
) -> None:
    root = saved_reply(repo, patch={"reply_to": None})
    assert root.id is not None, "saved_reply stores its physical identity"
    sent_parent(repo, patch=patch)
    before = repo.dump()

    assert not repo.freeze_direct_delivery(root, frozen(root).model_copy(update={"reply_to": 777}))

    assert repo.dump() == before


def test_a_current_card_cannot_retarget_an_already_saved_legacy_parent(
    repo: SqliteBillRepository,
) -> None:
    root = saved_reply(repo)
    assert root.id is not None, "saved_reply stores its physical identity"
    sent_parent(repo)
    before = repo.dump()

    assert not repo.freeze_direct_delivery(root, frozen(root).model_copy(update={"reply_to": 777}))

    assert repo.dump() == before


def test_an_absent_card_cannot_be_replaced_with_an_arbitrary_positive_parent(
    repo: SqliteBillRepository,
) -> None:
    root = saved_reply(repo, patch={"reply_to": None})
    assert root.id is not None, "saved_reply stores its physical identity"
    before = repo.dump()

    assert not repo.freeze_direct_delivery(root, frozen(root).model_copy(update={"reply_to": 777}))

    assert repo.dump() == before
