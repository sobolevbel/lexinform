from datetime import UTC, datetime

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    AgendaItem,
    Bill,
    BillStatus,
    DeliveryPlan,
    ProcessSummary,
    Publication,
    PublicationKind,
    PublicationStatus,
)

HTML = "<b>Заседания</b>\nСрок 30 дней; 200 PLN; старые заявления."


def saved_sitting(
    repo: SqliteBillRepository,
    number: str,
    *,
    status: PublicationStatus = PublicationStatus.FAILED,
    patch: dict[str, object] | None = None,
    channel: str = "test-channel",
) -> Publication:
    now = datetime(2026, 10, 28, tzinfo=UTC)
    bill = Bill(
        summary=ProcessSummary(
            term=10, number=number, title="Saved bill", document_type="ustawa", change_date=now
        ),
        status=BillStatus.ANALYZED,
        first_seen_at=now,
        last_checked_at=now,
    )
    item = AgendaItem(kind="committee", ref=f"meeting/{number}", date=now.date())
    identity = repo.create_publication(
        Publication(
            term=10,
            number=number,
            kind=PublicationKind.AGENDA,
            status=status,
            channel_id=channel,
            ref=f"meeting/{number}",
            created_at=datetime(2026, 10, 28, tzinfo=UTC),
            delivery=DeliveryPlan(
                bill_json=bill.model_dump_json(), item_json=item.model_dump_json(), reply_to=101
            ).model_copy(update=patch or {}),
        )
    )
    if status is PublicationStatus.FAILED:
        repo.mark_publication(identity, PublicationStatus.FAILED)
    stored = repo.publication_by_id(identity)
    assert stored is not None, "create_publication saves the sitting before group preparation"
    return stored


def prepared(members: tuple[Publication, ...], text: str = HTML) -> tuple[DeliveryPlan, ...]:
    plans: list[DeliveryPlan] = []
    for member in members:
        assert member.delivery is not None, "saved_sitting supplies every member plan"
        plans.append(
            member.delivery.model_copy(
                update={
                    "sitting_group": tuple(row.id for row in members),
                    "group_text": text,
                    "render_origin": "legacy_snapshot"
                    if member.status is PublicationStatus.FAILED or member.attempts
                    else None,
                }
            )
        )
    return tuple(plans)


@pytest.mark.parametrize("singleton", [False, True])
@pytest.mark.parametrize("mixed", [False, True])
def test_sitting_writer_freezes_exact_future_membership_without_rewriting_semantics(
    repo: SqliteBillRepository, singleton: bool, mixed: bool
) -> None:
    first = saved_sitting(repo, "3039")
    members: tuple[Publication, ...] = (first,)
    if not singleton:
        members += (
            saved_sitting(
                repo, "3040", status=PublicationStatus.QUEUED if mixed else PublicationStatus.FAILED
            ),
        )
    identities = tuple(member.id for member in members)

    assert repo.freeze_sitting_delivery(members, prepared(members))
    assert not repo.freeze_sitting_delivery(members, prepared(members))

    for member in members:
        assert member.delivery is not None, "saved_sitting stores every semantic plan"
        expected = member.delivery.model_copy(
            update={
                "sitting_group": identities,
                "group_text": HTML,
                "render_origin": "legacy_snapshot"
                if member.status is PublicationStatus.FAILED
                else None,
            }
        )
        assert repo.publication_by_id(member.id or 0) == member.model_copy(
            update={"delivery": expected}
        )
    before = repo.dump()
    repo.restore(before)
    assert repo.dump() == before


@pytest.mark.parametrize(
    "patch",
    [
        {"destination": "digest_only"},
        {"bill_json": ""},
        {"item_json": None},
        {"item_json": "{}"},
        {"bill_json": "{}"},
        {"moved_from_json": "{}"},
        {"reply_to": -1},
        {"edit_message_id": 101},
        {"rendered_text": "existing HTML"},
        {"render_origin": "legacy_snapshot"},
        {"group_text": "existing group"},
        {"sitting_group": (999,)},
        {"continuation_ids": (999,)},
        {"continuation_parent": 999},
        {"continuation_root": 999},
        {"continuation_index": 2},
        {"digest_parts": (999,)},
        {"digest_event_ids": (999,)},
        {"absorbed_by": 999},
        {"revision_of": 999},
    ],
)
def test_unqualified_member_cannot_partially_freeze_its_eligible_partner(
    repo: SqliteBillRepository, patch: dict[str, object]
) -> None:
    first = saved_sitting(repo, "3039")
    second = saved_sitting(repo, "3040", patch=patch)
    before = repo.dump()

    assert not repo.freeze_sitting_delivery((first, second), prepared((first, second)))

    assert repo.dump() == before


@pytest.mark.parametrize(
    "status",
    [
        status
        for status in PublicationStatus
        if status not in (PublicationStatus.FAILED, PublicationStatus.QUEUED)
    ],
)
def test_uncertain_or_completed_member_never_acquires_future_group_membership(
    repo: SqliteBillRepository, status: PublicationStatus
) -> None:
    first = saved_sitting(repo, "3039")
    second = saved_sitting(repo, "3040", status=status)
    before = repo.dump()

    assert not repo.freeze_sitting_delivery((first, second), prepared((first, second)))

    assert repo.dump() == before


@pytest.mark.parametrize(
    "shape",
    [
        "empty",
        "duplicate",
        "foreign",
        "missing",
        "stale",
        "context",
        "wrong_kind",
    ],
)
def test_sitting_writer_rejects_invalid_identity_or_stale_group_sources(
    repo: SqliteBillRepository, shape: str
) -> None:
    first = saved_sitting(repo, "3039")
    second = saved_sitting(
        repo, "3040", channel="elsewhere" if shape == "foreign" else "test-channel"
    )
    members: tuple[Publication, ...] = (first, second)
    if shape == "empty":
        members = ()
    elif shape == "duplicate":
        members = (first, first)
    elif shape == "missing":
        members = (first, second.model_copy(update={"id": 99999}))
    elif shape == "stale":
        assert second.id is not None, "saved_sitting stores its identity"
        repo.mark_publication(second.id, PublicationStatus.FAILED)
    elif shape == "context":
        assert second.delivery is not None, "saved_sitting retains its original semantic plan"
        members = (
            first,
            second.model_copy(
                update={"delivery": second.delivery.model_copy(update={"reply_to": 999})}
            ),
        )
    elif shape == "wrong_kind":
        members = (first, second.model_copy(update={"kind": PublicationKind.NEW_BILL}))
    before = repo.dump()

    assert not repo.freeze_sitting_delivery(members, prepared(members))

    assert repo.dump() == before


@pytest.mark.parametrize("owns_sources", [False, True])
def test_digest_ownership_blocks_a_sitting_group_without_partial_changes(
    repo: SqliteBillRepository, owns_sources: bool
) -> None:
    members = (saved_sitting(repo, "3039"), saved_sitting(repo, "3040"))
    owner = repo.create_publication(
        Publication(
            term=0,
            number="digest",
            kind=PublicationKind.DIGEST,
            status=PublicationStatus.QUEUED,
            channel_id="test-channel",
            ref="2026-W44",
            created_at=datetime(2026, 10, 28, tzinfo=UTC),
            delivery=DeliveryPlan(bill_json="", rendered_text="saved digest"),
        )
    )
    second = members[1]
    assert second.id is not None, "saved_sitting stores its identity"
    source, target = (owner, second.id) if owns_sources else (second.id, owner)
    repo.restore(repo.dump() + f"\nINSERT INTO digest_event_links VALUES ({source}, {target});\n")
    before = repo.dump()

    assert not repo.freeze_sitting_delivery(members, prepared(members))

    assert repo.dump() == before


@pytest.mark.parametrize("text", ["", "   ", "😀" * 2049])
def test_sitting_group_requires_nonempty_bounded_exact_html(
    repo: SqliteBillRepository, text: str
) -> None:
    member = saved_sitting(repo, "3039")
    before = repo.dump()

    assert not repo.freeze_sitting_delivery((member,), prepared((member,), text))

    assert repo.dump() == before


def test_group_freeze_preserves_unknown_json_fields_in_each_member(
    repo: SqliteBillRepository,
) -> None:
    members = (saved_sitting(repo, "3039"), saved_sitting(repo, "3040"))
    source = repo.dump()
    for member in members:
        assert member.delivery is not None, "saved_sitting retains its original plan"
        raw = member.delivery.model_dump_json()
        extended = raw[:-1] + f',"future_contract":{{"owner":"{member.number}"}}}}'
        quoted = "'" + raw.replace("'", "''") + "'"
        replacement = "'" + extended.replace("'", "''") + "'"
        source = source.replace(quoted, replacement, 1)
    repo.restore(source)

    assert repo.freeze_sitting_delivery(members, prepared(members))

    assert '"future_contract":{"owner":"3039"}' in repo.dump()
    assert '"future_contract":{"owner":"3040"}' in repo.dump()


def test_late_member_cas_failure_rolls_back_already_prepared_group_rows(
    repo: SqliteBillRepository,
) -> None:
    members = (saved_sitting(repo, "3039"), saved_sitting(repo, "3040"))
    second = members[1]
    assert second.id is not None, "saved_sitting stores every group member"
    repo.restore(
        repo.dump()
        + f"\nCREATE TRIGGER refuse_group BEFORE UPDATE OF delivery_json ON publications WHEN NEW.id = {second.id} BEGIN SELECT RAISE(IGNORE); END;\n"
    )
    before = repo.dump()

    with pytest.raises(ValueError, match="changed sitting delivery"):
        repo.freeze_sitting_delivery(members, prepared(members))

    assert repo.dump() == before


@pytest.mark.parametrize(
    "policy,status,attempts",
    [
        ("legacy", PublicationStatus.QUEUED, 1),
        ("attention-v1", PublicationStatus.FAILED, 0),
        ("attention-v1", PublicationStatus.QUEUED, 1),
    ],
)
def test_sitting_writer_preserves_policy_and_retained_queued_attempts(
    repo: SqliteBillRepository, policy: str, status: PublicationStatus, attempts: int
) -> None:
    original = saved_sitting(
        repo, "3039", status=PublicationStatus.QUEUED, patch={"policy_version": policy}
    )
    assert original.id is not None, "saved_sitting persists its identity before counting attempts"
    for _ in range(attempts):
        repo.mark_publication(original.id, PublicationStatus.FAILED)
    repo.mark_publication(original.id, status, count_attempt=False)
    current = repo.publication_by_id(original.id)
    assert current is not None, "mark_publication retains the sitting identity"
    plans = prepared((current,))

    assert repo.freeze_sitting_delivery((current,), plans)

    assert repo.publication_by_id(original.id) == current.model_copy(update={"delivery": plans[0]})


def test_sitting_writer_rejects_a_plan_that_changes_one_semantic_or_group_member(
    repo: SqliteBillRepository,
) -> None:
    members = (saved_sitting(repo, "3039"), saved_sitting(repo, "3040"))
    first, second = prepared(members)
    before = repo.dump()
    for patch in (
        {"reply_to": 999},
        {"group_text": "different HTML"},
        {"sitting_group": (members[1].id, members[0].id)},
        {"policy_version": "replacement"},
    ):
        assert not repo.freeze_sitting_delivery(members, (first, second.model_copy(update=patch)))
        assert repo.dump() == before
    assert not repo.freeze_sitting_delivery(members, (first,))
    assert repo.dump() == before


def test_sitting_source_owner_transfer_keeps_identity_and_original_bill_context(
    repo: SqliteBillRepository,
) -> None:
    original = saved_sitting(repo, "rcl:123")
    assert original.id is not None and original.delivery is not None, (
        "saved_sitting stores the old source identity"
    )
    repo.restore(
        repo.dump() + f"\nUPDATE publications SET number = '3040' WHERE id = {original.id};\n"
    )
    current = repo.publication_by_id(original.id)
    assert current is not None and current.number == "3040", (
        "the source transfer keeps the publication id"
    )
    plans = prepared((current,))

    assert repo.freeze_sitting_delivery((current,), plans)

    frozen = repo.publication_by_id(original.id)
    assert frozen == current.model_copy(update={"delivery": plans[0]})
    assert plans[0].bill_json == original.delivery.bill_json
