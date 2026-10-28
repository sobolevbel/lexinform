from datetime import UTC, datetime

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import DeliveryPlan, Publication, PublicationKind, PublicationStatus
from tests.unit.test_direct_status_edit_freeze import frozen
from tests.unit.test_direct_status_reply_freeze import saved_reply


def reply_parts(
    repo: SqliteBillRepository, *, child_patch: dict[str, object] | None = None
) -> tuple[Publication, DeliveryPlan, tuple[int, ...]]:
    root = saved_reply(repo)
    assert root.id is not None and root.delivery is not None, "saved_reply stores its plan"
    parent = root.id
    children: list[int] = []
    for index in (2, 3):
        child = Publication(
            term=root.term,
            number=root.number,
            kind=PublicationKind.MESSAGE_PART,
            channel_id=root.channel_id,
            status=PublicationStatus.QUEUED,
            ref=f"message/{root.id}/{index}",
            created_at=datetime(2026, 10, 28, tzinfo=UTC),
            delivery=DeliveryPlan(
                bill_json="{}",
                rendered_text=f"part {index}",
                policy_version="attention-v1",
                continuation_root=root.id,
                continuation_parent=parent,
                continuation_index=index,
                held_change_ids=root.delivery.held_change_ids if index == 3 else (),
            ),
        )
        if index == 2 and child_patch:
            child = child.model_copy(update=child_patch)
        parent = repo.create_publication(child)
        children.append(parent)
    ids = tuple(children)
    return (
        root,
        frozen(root).model_copy(update={"continuation_ids": ids, "held_change_ids": ()}),
        ids,
    )


def test_qualified_legacy_root_can_freeze_the_exact_new_chain_with_release_on_its_leaf(
    repo: SqliteBillRepository,
) -> None:
    with repo.atomic():
        root, delivery, identities = reply_parts(repo)
        assert root.id is not None, "reply_parts persists its root"
        assert repo.freeze_direct_delivery(root, delivery)

    stored = repo.publication_by_id(root.id)
    assert stored == root.model_copy(update={"delivery": delivery})
    repo.restore(repo.dump())
    assert repo.publication_by_id(root.id) == stored
    assert all(repo.publication_by_id(identity) is not None for identity in identities)


@pytest.mark.parametrize("fault", ["missing", "repeated", "order", "root_release"])
def test_legacy_root_refuses_an_incomplete_or_ambiguous_chain(
    repo: SqliteBillRepository,
    fault: str,
) -> None:
    root, delivery, identities = reply_parts(repo)
    assert root.id is not None, "reply_parts persists its root"
    if fault == "missing":
        delivery = delivery.model_copy(update={"continuation_ids": (999,)})
    elif fault == "repeated":
        delivery = delivery.model_copy(update={"continuation_ids": (identities[0], identities[0])})
    elif fault == "order":
        delivery = delivery.model_copy(update={"continuation_ids": identities[::-1]})
    else:
        delivery = delivery.model_copy(update={"held_change_ids": (17,)})
    before = repo.dump()

    assert not repo.freeze_direct_delivery(root, delivery)

    assert repo.dump() == before


@pytest.mark.parametrize(
    "patch",
    [
        {"continuation_parent": 999},
        {"continuation_root": 999},
        {"continuation_index": 4},
        {"continuation_ids": (999,)},
        {"reply_to": 555},
        {"edit_message_id": 444},
        {"held_change_ids": ()},
        {"rendered_text": ""},
        {"rendered_text": "😀" * 2049},
        {"destination": "digest_only"},
        {"policy_version": "legacy"},
        {"revision_of": 999},
    ],
)
def test_legacy_root_refuses_changed_child_addresses_payloads_or_release_membership(
    repo: SqliteBillRepository,
    patch: dict[str, object],
) -> None:
    root, delivery, identities = reply_parts(repo)
    assert root.id is not None, "reply_parts persists its root"
    leaf = repo.publication_by_id(identities[-1])
    assert leaf is not None and leaf.delivery is not None, "reply_parts creates its leaf"
    assert repo.replace_unattempted_delivery(identities[-1], leaf.delivery.model_copy(update=patch))
    before = repo.dump()

    assert not repo.freeze_direct_delivery(root, delivery)

    assert repo.dump() == before


@pytest.mark.parametrize(
    "status",
    [
        PublicationStatus.SENT,
        PublicationStatus.PENDING,
        PublicationStatus.UNKNOWN,
        PublicationStatus.FAILED,
    ],
)
def test_legacy_root_never_adopts_an_already_attempted_physical_part(
    repo: SqliteBillRepository,
    status: PublicationStatus,
) -> None:
    root, delivery, identities = reply_parts(repo)
    assert root.id is not None, "reply_parts persists its root"
    repo.mark_publication(identities[0], status)
    before = repo.dump()

    assert not repo.freeze_direct_delivery(root, delivery)

    assert repo.dump() == before


@pytest.mark.parametrize(
    "patch",
    [
        {"number": "different"},
        {"channel_id": "different"},
        {"kind": PublicationKind.NEW_BILL},
        {"ref": "different"},
    ],
)
def test_legacy_root_cannot_adopt_another_operations_physical_part(
    repo: SqliteBillRepository,
    patch: dict[str, object],
) -> None:
    root, delivery, _ = reply_parts(repo, child_patch=patch)
    assert root.id is not None, "reply_parts persists its root"
    before = repo.dump()

    assert not repo.freeze_direct_delivery(root, delivery)

    assert repo.dump() == before
