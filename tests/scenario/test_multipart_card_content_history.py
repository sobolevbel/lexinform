from datetime import timedelta
from typing import Literal

import pytest

from lexinform.models import PublicationKind
from lexinform.models.multipart_card_history import MultipartCardPlan
from lexinform.services.card_edits import edit_card
from lexinform.services.content_inspection import inspect_content
from tests.checkpoint_state import saved_state
from tests.harness import CHANNEL, RCL, TERM, World
from tests.scenario.test_card_edit_multipart import edition, prepare, refresh
from tests.scenario.test_single_card_revision_producer import change


@pytest.mark.parametrize("shape", ["single_many", "many_same"])
def test_multipart_revision_preserves_ordered_history_after_cleanup(
    shape: Literal["single_many", "many_same"],
) -> None:
    case = prepare("off", shape)
    world = case.world
    owner = case.baseline
    assert owner.id is not None and case.previous[0].id is not None
    predecessor = world.repo.get_publication_content(case.previous[0].id)
    assert predecessor is not None, "the first freeze retained its version"
    refresh(world)
    slots = edition(world)
    first = slots[0]
    assert first.id is not None
    version = world.repo.get_publication_content(first.id)
    assert version is not None and version.previous_version_id == predecessor.content_id
    assert version.multipart_card_revision == "card-multipart-revision-v1"
    cutoff = world.clock.now() + timedelta(days=999)
    world.repo.prune_history(before=cutoff, memo_before=cutoff)
    sql = world.repo.dump()
    for _ in range(2):
        with saved_state(sql) as durable:
            assert durable.repo.dump() == sql
            view = inspect_content(durable.repo, owner)
            assert view is not None and view.current_id == version.content_id
            snapshot = durable.repo.get_content_snapshot(version.plan_snapshot_id)
            assert snapshot is not None, "the version retains the physical edit vector"
            plan = MultipartCardPlan.model_validate_json(snapshot.payload_json)
            assert plan.predecessor_slots == tuple(row.id for row in case.previous)
            assert tuple(slot.html for slot in plan.slots) == case.texts
            assert [slot.edit_message_id for slot in plan.slots] == [
                row.message_id for row in case.previous
            ] + [None] * (len(slots) - len(case.previous))
            for identity, expected in zip(plan.bindings, slots, strict=True):
                physical = durable.repo.publication_by_id(identity)
                assert physical is not None and physical.delivery is None
                assert physical.message_id == expected.message_id
                assert durable.repo.get_publication_content(identity) == version

    change(world, "Следующая точная редакция: 900 PLN. " * 230)
    refresh(world)
    view = inspect_content(world.repo, owner)
    assert view is not None and view.current_id != version.content_id
    next_slot = edition(world)[0]
    assert next_slot.id is not None
    subsequent = world.repo.get_publication_content(next_slot.id)
    assert subsequent is not None and subsequent.previous_version_id == version.content_id


def test_government_multipart_revision_retains_frozen_term_after_rollover() -> None:
    world = World(reader_attention="off")
    world.add_rcl_project()
    world.run()
    original = world.publication(RCL)
    assert original is not None and original.id is not None
    assert original.delivery is not None and not original.delivery.continuation_ids
    assert world.repo.get_publication_content(original.id) is not None
    record = world.bill(RCL).analysis
    assert record is not None, "the initial government card completed analysis"
    analysis = record.analysis.model_copy(
        update={
            "practical_impact": "Новая сумма 500 PLN. " * 450,
        }
    )
    world.repo.save_analysis(TERM, RCL, record.model_copy(update={"analysis": analysis}))
    result = edit_card(
        world.repo,
        world.clock,
        world.publisher,
        world.bill(RCL),
        original,
        None,
        channel_id=CHANNEL,
    )
    assert result.completed
    before = inspect_content(world.repo, original)
    assert before is not None and before.current_id is not None and before.version_count == 2
    assert world.repo.move_government_rows(TERM, TERM + 1) == 1
    sql = world.repo.dump()
    with saved_state(sql) as durable:
        owner = durable.repo.get_publication(TERM + 1, RCL, PublicationKind.NEW_BILL, CHANNEL)
        assert owner is not None and owner.id == original.id
        assert inspect_content(durable.repo, owner) == before
        assert durable.repo.dump() == sql


def test_multipart_revision_over_budget_preserves_normal_physical_edits() -> None:
    case = prepare("off", "many_grow")
    refresh(case.world)
    slots = edition(case.world)
    assert tuple(row.delivery.rendered_text for row in slots if row.delivery) == case.texts
    assert all(row.message_id is not None for row in slots)
    assert len(slots) > len(case.previous)
    assert all(
        case.world.repo.get_publication_content(row.id) is None
        for row in slots
        if row.id is not None
    )
    view = inspect_content(case.world.repo, case.baseline)
    assert view is not None and view.current_id is None
