from datetime import date

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import Publication, PublicationStatus
from tests.unit.test_legacy_status_edit_freeze import frozen, saved_edit


def saved_reply(
    repo: SqliteBillRepository, *, patch: dict[str, object] | None = None
) -> Publication:
    return saved_edit(repo, patch={"edit_message_id": None, "reply_to": 101, **(patch or {})})


def test_reply_freeze_records_its_render_day_without_changing_the_saved_parent_or_attempts(
    repo: SqliteBillRepository,
) -> None:
    original = saved_reply(repo)
    assert original.id is not None, "saved_reply persists the physical operation"
    prepared = frozen(original).model_copy(update={"today": date(2026, 10, 28)})

    assert not repo.freeze_legacy_status_edit(original.id, prepared)
    assert repo.freeze_legacy_status_reply(original.id, prepared)

    assert repo.publication_by_id(original.id) == original.model_copy(update={"delivery": prepared})
    assert not repo.freeze_legacy_status_reply(original.id, prepared)
    repo.restore(repo.dump())
    assert repo.publication_by_id(original.id) == original.model_copy(update={"delivery": prepared})


@pytest.mark.parametrize(
    "status", [s for s in PublicationStatus if s is not PublicationStatus.FAILED]
)
def test_reply_writer_refuses_states_without_definite_non_delivery(
    repo: SqliteBillRepository,
    status: PublicationStatus,
) -> None:
    original = saved_reply(repo)
    assert original.id is not None, "saved_reply persists the physical operation"
    repo.mark_publication(original.id, status, count_attempt=False)
    before = repo.dump()

    assert not repo.freeze_legacy_status_reply(original.id, frozen(original))

    assert repo.dump() == before


@pytest.mark.parametrize(
    "patch",
    [
        {"reply_to": None},
        {"reply_to": 0},
        {"edit_message_id": 444},
        {"policy_version": "attention-v1"},
        {"group_text": "group"},
        {"continuation_ids": (999,)},
        {"continuation_parent": 999},
        {"sitting_group": (999,)},
        {"rendered_text": "frozen"},
    ],
)
def test_unverified_parent_or_existing_physical_shape_is_not_frozen_as_a_single_reply(
    repo: SqliteBillRepository,
    patch: dict[str, object],
) -> None:
    original = saved_reply(repo, patch=patch)
    assert original.id is not None, "saved_reply persists the physical operation"
    before = repo.dump()

    assert not repo.freeze_legacy_status_reply(original.id, frozen(original))

    assert repo.dump() == before


@pytest.mark.parametrize(
    "patch",
    [
        {"reply_to": 555},
        {"edit_message_id": 444},
        {"today": None},
        {"bill_json": "different"},
        {"change_json": "different"},
        {"with_act": True},
        {"held_change_ids": ()},
        {"disable_notification": True},
        {"continuation_ids": (999,)},
        {"render_origin": None},
        {"rendered_text": ""},
        {"rendered_text": "😀" * 2049},
    ],
)
def test_reply_writer_changes_only_html_origin_and_the_new_render_day(
    repo: SqliteBillRepository,
    patch: dict[str, object],
) -> None:
    original = saved_reply(repo)
    assert original.id is not None, "saved_reply persists the physical operation"
    before = repo.dump()

    assert not repo.freeze_legacy_status_reply(
        original.id, frozen(original).model_copy(update=patch)
    )

    assert repo.dump() == before


def test_reply_render_day_update_preserves_opaque_fields_from_a_newer_writer(
    repo: SqliteBillRepository,
) -> None:
    original = saved_reply(repo)
    assert original.id is not None and original.delivery is not None, (
        "saved_reply retains its stored semantic plan"
    )
    raw = original.delivery.model_dump_json()
    extended = raw[:-1] + ',"future_contract":{"retain":["opaque",123]}}'
    quoted = "'" + raw.replace("'", "''") + "'"
    replacement = "'" + extended.replace("'", "''") + "'"
    source = repo.dump()
    assert source.count(quoted) == 1, "the fixture has one matching physical reply"
    repo.restore(source.replace(quoted, replacement))

    assert repo.freeze_legacy_status_reply(
        original.id, frozen(original).model_copy(update={"today": date(2026, 10, 28)})
    )

    assert repo.dump().count("future_contract") == 1
    assert '"retain":["opaque",123]' in repo.dump()
    stored = repo.publication_by_id(original.id)
    assert stored is not None and stored.delivery is not None, "the writer preserves the row"
    assert stored.delivery.today == date(2026, 10, 28)
