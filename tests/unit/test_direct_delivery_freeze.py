from datetime import date

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import DeliveryPlan, PublicationStatus
from tests.unit.test_direct_reply_parts_freeze import reply_parts
from tests.unit.test_direct_status_edit_freeze import frozen, saved_edit


@pytest.mark.parametrize("status", [PublicationStatus.QUEUED, PublicationStatus.FAILED])
@pytest.mark.parametrize("attempts", [0, 1, 2])
@pytest.mark.parametrize("policy", ["legacy", "attention-v1", "future-compatible"])
def test_direct_writer_preserves_retry_state_and_semantics(
    repo: SqliteBillRepository, status: PublicationStatus, attempts: int, policy: str
) -> None:
    original = saved_edit(repo, attempts=attempts, patch={"policy_version": policy})
    assert original.id is not None, "saved_edit persists the operation before counting attempts"
    identity = original.id
    repo.mark_publication(identity, status, count_attempt=False)
    current = repo.publication_by_id(identity)
    assert current is not None, "mark_publication retains the saved operation"
    original = current
    prepared = frozen(original)

    assert repo.freeze_direct_delivery(original, prepared)

    assert repo.publication_by_id(identity) == original.model_copy(update={"delivery": prepared})
    assert not repo.freeze_direct_delivery(original, prepared)


@pytest.mark.parametrize(
    "status",
    [s for s in PublicationStatus if s not in (PublicationStatus.QUEUED, PublicationStatus.FAILED)],
)
def test_direct_writer_never_adopts_an_uncertain_or_completed_root(
    repo: SqliteBillRepository, status: PublicationStatus
) -> None:
    original = saved_edit(repo)
    assert original.id is not None, "saved_edit persists the physical root"
    repo.mark_publication(original.id, status, count_attempt=False)
    current = repo.publication_by_id(original.id)
    assert current is not None, "changing the operation state retains its identity"
    before = repo.dump()

    assert not repo.freeze_direct_delivery(current, frozen(current))

    assert repo.dump() == before


@pytest.mark.parametrize(
    "patch",
    [
        {"bill_json": "replacement"},
        {"change_json": "replacement"},
        {"today": date(2026, 10, 28)},
        {"edit_message_id": 555},
        {"reply_to": 555},
        {"policy_version": "replacement"},
        {"with_act": True},
        {"held_change_ids": ()},
        {"rendered_text": ""},
        {"rendered_text": "😀" * 2049},
        {"render_origin": None},
        {"continuation_ids": (999,)},
    ],
)
def test_direct_writer_cannot_rewrite_semantic_context_targets_or_invent_parts(
    repo: SqliteBillRepository, patch: dict[str, object]
) -> None:
    original = saved_edit(repo)
    before = repo.dump()

    assert not repo.freeze_direct_delivery(original, frozen(original).model_copy(update=patch))

    assert repo.dump() == before


def test_direct_writer_compares_status_attempts_and_saved_payload(
    repo: SqliteBillRepository,
) -> None:
    original = saved_edit(repo)
    assert original.id is not None, "saved_edit persists the physical root"
    repo.mark_publication(original.id, PublicationStatus.FAILED)
    before = repo.dump()

    assert not repo.freeze_direct_delivery(original, frozen(original))

    assert repo.dump() == before


def test_direct_writer_preserves_unrecognized_raw_json(repo: SqliteBillRepository) -> None:
    original = saved_edit(repo, patch={"policy_version": "attention-v1"})
    assert original.delivery is not None, "saved_edit supplies the original delivery JSON"
    raw = original.delivery.model_dump_json()
    extended = raw[:-1] + ',"future_contract":{"retain":["opaque",123]}}'
    quoted = "'" + raw.replace("'", "''") + "'"
    replacement = "'" + extended.replace("'", "''") + "'"
    source = repo.dump()
    assert source.count(quoted) == 1, "the fixture contains one exact delivery JSON value"
    repo.restore(source.replace(quoted, replacement))

    assert repo.freeze_direct_delivery(original, frozen(original))

    assert '"retain":["opaque",123]' in repo.dump()


def test_direct_edit_writer_accepts_only_the_complete_receipt_parent_chain(
    repo: SqliteBillRepository,
) -> None:
    original, prepared, identities = reply_parts(repo)
    assert original.id is not None and original.delivery is not None, (
        "reply_parts persists the root and both physical children"
    )
    source = repo.dump()
    before = original.delivery.model_dump_json()
    edited = original.delivery.model_copy(update={"edit_message_id": 444}).model_dump_json()
    repo.restore(source.replace("'" + before + "'", "'" + edited + "'"))
    current = repo.publication_by_id(original.id)
    assert current is not None, "restore preserves the root identity"
    prepared = prepared.model_copy(update={"edit_message_id": 444})

    assert not repo.freeze_direct_delivery(
        current, prepared.model_copy(update={"continuation_ids": identities[::-1]})
    )
    assert repo.freeze_direct_delivery(current, prepared)

    assert repo.publication_by_id(original.id) == current.model_copy(update={"delivery": prepared})


def test_direct_writer_rejects_orphan_continuation_without_consuming_its_root(
    repo: SqliteBillRepository,
) -> None:
    original = saved_edit(repo)
    before = repo.dump()
    prepared: DeliveryPlan = frozen(original).model_copy(
        update={"continuation_ids": (999,), "held_change_ids": ()}
    )

    assert not repo.freeze_direct_delivery(original, prepared)

    assert repo.dump() == before
