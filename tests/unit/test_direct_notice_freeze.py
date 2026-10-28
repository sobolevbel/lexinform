from datetime import UTC, date, datetime

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import DeliveryPlan, Publication, PublicationKind, PublicationStatus
from tests.unit.test_direct_status_edit_freeze import frozen

KINDS = (
    PublicationKind.ACT_PUBLISHED,
    PublicationKind.CONSULTATION_RESULTS,
    PublicationKind.IN_FORCE,
    PublicationKind.CONSULTATION_DEADLINE,
    PublicationKind.DECISION_DEADLINE,
    PublicationKind.HEARING_DEADLINE,
)


def saved_notice(
    repo: SqliteBillRepository,
    kind: PublicationKind = PublicationKind.ACT_PUBLISHED,
    *,
    patch: dict[str, object] | None = None,
) -> Publication:
    identity = repo.create_publication(
        Publication(
            term=10,
            number="3039",
            kind=kind,
            status=PublicationStatus.FAILED,
            channel_id="test-channel",
            created_at=datetime(2026, 10, 28, tzinfo=UTC),
            delivery=DeliveryPlan(
                bill_json="{}",
                reply_to=101,
                today=date(2026, 10, 1),
                phase_json="{}",
                hearing_json="{}",
            ).model_copy(update=patch or {}),
        )
    )
    repo.mark_publication(identity, PublicationStatus.FAILED)
    stored = repo.publication_by_id(identity)
    assert stored is not None, "create_publication stores the notice before counting its failure"
    return stored


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("reply_to", [None, 101])
@pytest.mark.parametrize("policy", ["legacy", "attention-v1"])
def test_notice_writer_preserves_its_saved_day_parent_context_and_attempts(
    repo: SqliteBillRepository, kind: PublicationKind, reply_to: int | None, policy: str
) -> None:
    original = saved_notice(repo, kind, patch={"reply_to": reply_to, "policy_version": policy})
    assert original.id is not None, "saved_notice assigns its physical identity"
    prepared = frozen(original)

    assert repo.freeze_direct_delivery(original, prepared)
    assert not repo.freeze_direct_delivery(original, prepared)

    expected = original.model_copy(update={"delivery": prepared})
    assert repo.publication_by_id(original.id) == expected
    repo.restore(repo.dump())
    assert repo.publication_by_id(original.id) == expected


@pytest.mark.parametrize(
    "patch",
    [
        {"reply_to": 0},
        {"edit_message_id": 444},
        {"destination": "digest_only"},
        {"group_text": "group"},
        {"sitting_group": (999,)},
        {"continuation_ids": (999,)},
        {"continuation_parent": 999},
        {"continuation_root": 999},
        {"continuation_index": 2},
        {"rendered_text": "already frozen"},
        {"render_origin": "legacy_snapshot"},
        {"bill_json": ""},
        {"digest_parts": (999,)},
        {"digest_event_ids": (999,)},
        {"absorbed_by": 999},
        {"revision_of": 999},
    ],
)
def test_notice_writer_refuses_unqualified_or_already_owned_shapes(
    repo: SqliteBillRepository, patch: dict[str, object]
) -> None:
    original = saved_notice(repo, patch=patch)
    assert original.id is not None, "saved_notice assigns its physical identity"
    before = repo.dump()

    assert not repo.freeze_direct_delivery(original, frozen(original))

    assert repo.dump() == before


@pytest.mark.parametrize("kind", KINDS[2:])
def test_dated_notice_requires_its_original_day(
    repo: SqliteBillRepository, kind: PublicationKind
) -> None:
    original = saved_notice(repo, kind, patch={"today": None})
    assert original.id is not None, "saved_notice assigns its physical identity"

    assert not repo.freeze_direct_delivery(original, frozen(original))

    assert repo.publication_by_id(original.id) == original


@pytest.mark.parametrize(
    "kind,field",
    [
        (PublicationKind.DECISION_DEADLINE, "phase_json"),
        (PublicationKind.HEARING_DEADLINE, "hearing_json"),
    ],
)
def test_reminder_requires_the_context_used_by_its_old_dispatcher(
    repo: SqliteBillRepository, kind: PublicationKind, field: str
) -> None:
    original = saved_notice(repo, kind, patch={field: None})
    assert original.id is not None, "saved_notice assigns its physical identity"

    assert not repo.freeze_direct_delivery(original, frozen(original))

    assert repo.publication_by_id(original.id) == original


@pytest.mark.parametrize(
    "patch",
    [
        {"reply_to": 555},
        {"today": date(2026, 10, 28)},
        {"today": None},
        {"bill_json": "different"},
        {"phase_json": "different"},
        {"hearing_json": "different"},
        {"held_change_ids": (17,)},
        {"render_origin": None},
        {"rendered_text": ""},
        {"rendered_text": "😀" * 2049},
        {"continuation_ids": (999,)},
    ],
)
def test_notice_freeze_changes_only_future_html_and_its_origin(
    repo: SqliteBillRepository, patch: dict[str, object]
) -> None:
    original = saved_notice(repo)
    assert original.id is not None, "saved_notice assigns its physical identity"
    before = repo.dump()

    assert not repo.freeze_direct_delivery(original, frozen(original).model_copy(update=patch))

    assert repo.dump() == before


@pytest.mark.parametrize(
    "status", [s for s in PublicationStatus if s is not PublicationStatus.FAILED]
)
def test_notice_cas_refuses_a_state_changed_since_rendering(
    repo: SqliteBillRepository, status: PublicationStatus
) -> None:
    original = saved_notice(repo)
    assert original.id is not None, "saved_notice assigns its physical identity"
    repo.mark_publication(original.id, status, count_attempt=False)
    before = repo.dump()

    assert not repo.freeze_direct_delivery(original, frozen(original))

    assert repo.dump() == before


def test_notice_freeze_preserves_fields_from_a_compatible_newer_writer(
    repo: SqliteBillRepository,
) -> None:
    original = saved_notice(repo)
    assert original.id is not None and original.delivery is not None, (
        "saved_notice retains its plan"
    )
    raw = original.delivery.model_dump_json()
    extended = raw[:-1] + ',"future_contract":{"retain":["opaque",123]}}'
    quoted = "'" + raw.replace("'", "''") + "'"
    replacement = "'" + extended.replace("'", "''") + "'"
    source = repo.dump()
    assert source.count(quoted) == 1, "the fixture has one matching physical notice"
    repo.restore(source.replace(quoted, replacement))

    assert repo.freeze_direct_delivery(original, frozen(original))

    assert '"retain":["opaque",123]' in repo.dump()
