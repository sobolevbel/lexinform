from dataclasses import replace

import pytest

from lexinform.models import DeliveryPlan, Publication, PublicationKind, PublicationStatus
from tests.harness import CHANNEL, TERM, World
from tests.scenario.test_active_card_edit_barrier import changed
from tests.scenario.test_forget_card_edits import EditCase, row, saved_edit


def dismissed_case(*, legacy: bool, child: bool = False) -> EditCase:
    case = saved_edit(PublicationStatus.QUEUED, legacy=True, child=child)
    world = case.world
    changed(world)
    root = row(world, case.root)
    assert root.delivery is not None, "the fixture owns a saved edit plan"
    if not child:
        assert world.repo.replace_unattempted_delivery(
            case.root,
            root.delivery.model_copy(update={"revision_of": None if legacy else case.card.id}),
        )
    elif not legacy:
        return explicit_child_case(case)
    world.repo.set_card_digest(case.root, world.publisher.card_digest(world.bill("3039")))
    world.repo.mark_publication(case.operation, PublicationStatus.DISMISSED, count_attempt=False)
    return case


def explicit_child_case(case: EditCase) -> EditCase:
    world = case.world
    original = row(world, case.root)
    assert original.delivery is not None, "the sent legacy root carries the frozen payload"
    root = world.repo.create_publication(
        Publication(
            term=TERM,
            number="3039",
            kind=PublicationKind.MESSAGE_PART,
            channel_id=CHANNEL,
            status=PublicationStatus.QUEUED,
            created_at=world.clock.now(),
            ref=f"card/{case.card.id}/explicit-stopped/1",
            delivery=original.delivery.model_copy(
                update={"revision_of": case.card.id, "continuation_ids": ()}
            ),
        )
    )
    child = world.repo.create_publication(
        Publication(
            term=TERM,
            number="3039",
            kind=PublicationKind.MESSAGE_PART,
            channel_id=CHANNEL,
            status=PublicationStatus.DISMISSED,
            created_at=world.clock.now(),
            ref=f"card/{case.card.id}/explicit-stopped/2",
            delivery=DeliveryPlan(
                bill_json=world.bill("3039").model_dump_json(),
                rendered_text="Сохранённое отклонённое продолжение",
                continuation_root=root,
                continuation_parent=root,
                reply_to=case.card.message_id,
            ),
        )
    )
    saved = row(world, root)
    assert saved.delivery is not None, "the explicit root is persisted before membership freezes"
    assert world.repo.replace_unattempted_delivery(
        root, saved.delivery.model_copy(update={"continuation_ids": (child,)})
    )
    world.repo.mark_publication(case.operation, PublicationStatus.DISMISSED, count_attempt=False)
    world.repo.mark_publication(
        root, PublicationStatus.SENT, message_id=case.card.message_id, count_attempt=False
    )
    world.repo.set_card_digest(root, world.publisher.card_digest(world.bill("3039")))
    return replace(case, root=root, operation=child)


def refresh(world: World) -> int:
    return (
        world.container.tracking_service(dry_run=False)
        .check_bill(world.bill("3039"))
        .cards_refreshed
    )


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("child", [False, True])
def test_rollback_refresher_keeps_the_latest_dismissed_frozen_desired_edition_stopped(
    legacy: bool, child: bool
) -> None:
    case = dismissed_case(legacy=legacy, child=child)
    world = case.world
    root, operation = row(world, case.root), row(world, case.operation)
    before = len(world.publisher.sent), len(world.publisher.edited)

    assert refresh(world) == 0
    assert refresh(world) == 0

    assert (len(world.publisher.sent), len(world.publisher.edited)) == before
    assert row(world, case.root) == root and row(world, case.operation) == operation
    assert world.publication("3039") == case.card


@pytest.mark.parametrize("legacy", [False, True])
def test_a_new_semantic_desired_edition_remains_available_after_a_dismissed_single_edit(
    legacy: bool,
) -> None:
    case = dismissed_case(legacy=legacy)
    world = case.world
    old = row(world, case.root)
    record = world.bill("3039").analysis
    assert record is not None, "the production fixture has a completed analysis"
    world.repo.save_analysis(
        TERM,
        "3039",
        record.model_copy(
            update={
                "analysis": record.analysis.model_copy(
                    update={"practical_impact": "Иная новая редакция: 900 PLN."}
                )
            }
        ),
    )
    before = len(world.publisher.edited)

    assert refresh(world) == 1

    assert len(world.publisher.edited) == before + 1
    assert row(world, case.root) == old
    assert world.publication("3039") != case.card


@pytest.mark.parametrize("invalid", ["channel", "target", "owner", "no_html"])
def test_an_unqualified_dismissed_record_does_not_suppress_the_desired_card(invalid: str) -> None:
    case = saved_edit(PublicationStatus.QUEUED, legacy=True)
    world = case.world
    changed(world)
    original = row(world, case.root)
    assert original.delivery is not None, "the fixture saved its edit plan"
    world.repo.mark_publication(case.root, PublicationStatus.DISMISSED, count_attempt=False)
    desired = world.publisher.card_digest(world.bill("3039"))
    root = world.repo.create_publication(
        original.model_copy(
            update={
                "id": None,
                "ref": f"card/{case.card.id}/invalid-{invalid}/1",
                "status": PublicationStatus.DISMISSED,
                "channel_id": "@elsewhere" if invalid == "channel" else CHANNEL,
                "delivery": original.delivery.model_copy(
                    update={
                        "revision_of": 900000 if invalid == "owner" else case.card.id,
                        "edit_message_id": (case.card.message_id or 0) + 1
                        if invalid == "target"
                        else case.card.message_id,
                        "rendered_text": ""
                        if invalid == "no_html"
                        else original.delivery.rendered_text,
                    }
                ),
            }
        )
    )
    world.repo.set_card_digest(root, desired)
    before = len(world.publisher.edited)

    assert refresh(world) == 1

    assert len(world.publisher.edited) == before + 1
    assert row(world, root).status is PublicationStatus.DISMISSED
