import json
from datetime import UTC, date, datetime

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    DeliveryPlan,
    Publication,
    PublicationKind,
    PublicationStatus,
    StatusChange,
    UpdateSnapshot,
)


def saved_edit(
    repo: SqliteBillRepository, *, attempts: int = 1, patch: dict[str, object] | None = None
) -> Publication:
    change = StatusChange(
        term=10,
        number="3039",
        old_fingerprint=None,
        new_fingerprint="saved-edit",
        new_stages=[],
        detected_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    change_id = repo.add_status_change(change)
    assert change_id is not None, "the fixture records one new status change per test"
    plan = DeliveryPlan(
        bill_json="{}",
        change_json=change.model_copy(update={"id": change_id}).model_dump_json(),
        today=date(2026, 10, 1),
        edit_message_id=444,
        held_change_ids=(17,),
    )
    publication = Publication(
        term=10,
        number="3039",
        kind=PublicationKind.STATUS_UPDATE,
        status=PublicationStatus.FAILED,
        channel_id="test-channel",
        created_at=datetime(2026, 10, 24, tzinfo=UTC),
        status_change_id=change_id,
        delivery=plan.model_copy(update=patch or {}),
    )
    identity = repo.create_publication(publication)
    for _ in range(attempts):
        repo.mark_publication(identity, PublicationStatus.FAILED)
    stored = repo.publication_by_id(identity)
    assert stored is not None, "create_publication stores the fixture before counting failures"
    return stored


def frozen(publication: Publication) -> DeliveryPlan:
    assert publication.delivery is not None, "saved_edit supplies a persisted semantic snapshot"
    return publication.delivery.model_copy(
        update={
            "rendered_text": "<b>Original 30 days; 200 PLN.</b>",
            "render_origin": "legacy_snapshot",
        }
    )


@pytest.mark.parametrize("attempts", [0, 1, 2])
def test_only_html_and_its_origin_are_added_without_resetting_the_failed_attempt_budget(
    repo: SqliteBillRepository, attempts: int
) -> None:
    original = saved_edit(repo, attempts=attempts)
    assert original.id is not None, "create_publication assigns its physical identity"
    prepared = frozen(original)

    assert not repo.replace_unattempted_delivery(original.id, prepared)
    assert repo.freeze_direct_delivery(original, prepared)

    assert repo.publication_by_id(original.id) == original.model_copy(update={"delivery": prepared})
    assert not repo.freeze_direct_delivery(original, prepared)
    fresh = SqliteBillRepository(":memory:")
    try:
        fresh.restore(repo.dump())
        assert fresh.publication_by_id(original.id) == original.model_copy(
            update={"delivery": prepared}
        )
    finally:
        fresh.close()


@pytest.mark.parametrize(
    "status", [state for state in PublicationStatus if state is not PublicationStatus.FAILED]
)
def test_stale_edit_snapshot_cannot_freeze_an_operation_whose_state_changed(
    repo: SqliteBillRepository, status: PublicationStatus
) -> None:
    publication = saved_edit(repo)
    assert publication.id is not None, "saved_edit persists its physical identity"
    repo.mark_publication(publication.id, status, count_attempt=False)
    before = repo.publication_by_id(publication.id)

    assert not repo.freeze_direct_delivery(publication, frozen(publication))

    assert repo.publication_by_id(publication.id) == before


@pytest.mark.parametrize(
    "patch",
    [
        {"edit_message_id": 555},
        {"reply_to": 555},
        {"today": date(2026, 10, 24)},
        {"bill_json": "different bill"},
        {"change_json": "different change"},
        {"with_act": True},
        {"held_change_ids": ()},
        {"disable_notification": True},
        {"continuation_ids": (999,)},
        {"sitting_group": (999,)},
        {"render_origin": None},
        {"rendered_text": ""},
        {"rendered_text": " \n"},
        {"rendered_text": "😀" * 2049},
    ],
)
def test_a_freeze_cannot_change_the_saved_context_target_flags_or_physical_shape(
    repo: SqliteBillRepository, patch: dict[str, object]
) -> None:
    original = saved_edit(repo)
    assert original.id is not None, "saved_edit persists its physical identity"

    assert not repo.freeze_direct_delivery(original, frozen(original).model_copy(update=patch))

    assert repo.publication_by_id(original.id) == original


@pytest.mark.parametrize(
    "patch",
    [
        {"edit_message_id": 0},
        {"today": None},
        {"rendered_text": "existing HTML"},
        {"render_origin": "legacy_snapshot"},
        {"group_text": "shared HTML"},
        {"sitting_group": (999,)},
        {"continuation_ids": (999,)},
        {"continuation_parent": 999},
        {"continuation_root": 999},
        {"continuation_index": 2},
        {"digest_parts": (999,)},
        {"digest_event_ids": (999,)},
        {"destination": "digest_only"},
        {"absorbed_by": 999},
        {"revision_of": 999},
        {"update_group": (UpdateSnapshot(publication_id=999, bill_json="{}", change_json="{}"),)},
    ],
)
def test_incomplete_or_already_frozen_legacy_shapes_remain_unchanged(
    repo: SqliteBillRepository, patch: dict[str, object]
) -> None:
    publication = saved_edit(repo, patch=patch)
    assert publication.id is not None, "saved_edit persists its physical identity"

    assert not repo.freeze_direct_delivery(publication, frozen(publication))

    assert repo.publication_by_id(publication.id) == publication


@pytest.mark.parametrize("kind", [PublicationKind.NEW_BILL, PublicationKind.MESSAGE_PART])
def test_a_card_or_part_cannot_adopt_the_status_edit_contract(
    repo: SqliteBillRepository, kind: PublicationKind
) -> None:
    original = saved_edit(repo)
    unrelated = original.model_copy(update={"id": None, "kind": kind, "status_change_id": None})
    identity = repo.create_publication(unrelated)
    before = repo.publication_by_id(identity)
    assert before is not None, "create_publication stores the unrelated operation"

    assert not repo.freeze_direct_delivery(before, frozen(before))

    assert repo.publication_by_id(identity) == before


def test_freezing_preserves_unknown_delivery_fields_from_a_compatible_newer_writer(
    repo: SqliteBillRepository,
) -> None:
    original = saved_edit(repo)
    assert original.id is not None and original.delivery is not None, (
        "saved_edit stores its context"
    )
    raw = original.delivery.model_dump_json()
    extended = json.loads(raw)
    extended["future_contract"] = {"retain": ["opaque", 123]}
    quoted = "'" + raw.replace("'", "''") + "'"
    replacement = "'" + json.dumps(extended, separators=(",", ":")).replace("'", "''") + "'"
    source = repo.dump()
    assert source.count(quoted) == 1, "the fixture has one physical edit with this saved plan"
    repo.restore(source.replace(quoted, replacement))

    assert repo.freeze_direct_delivery(original, frozen(original))

    assert repo.dump().count("future_contract") == 1
    assert '"retain":["opaque",123]' in repo.dump()
