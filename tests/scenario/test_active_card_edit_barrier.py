from typing import Literal

import pytest

from lexinform.models import Bill, Publication, PublicationStatus
from lexinform.services.publications import inherit_card, open_card_edit_ids
from tests.harness import CHANNEL, TERM, World
from tests.scenario.test_commands import _commands_only
from tests.scenario.test_forget_card_edits import ACTIVE, EditCase, row, saved_edit

Caller = Literal["tracking", "poster", "publishing"]
CALLERS: tuple[Caller, ...] = ("tracking", "poster", "publishing")


def changed(world: World) -> None:
    record = world.bill("3039").analysis
    assert record is not None, "the production fixture completed its analysis batch"
    world.repo.save_analysis(
        TERM,
        "3039",
        record.model_copy(
            update={
                "analysis": record.analysis.model_copy(
                    update={"practical_impact": "Новая редакция: 300 PLN вместо 200 PLN."}
                )
            }
        ),
    )


def successor(world: World) -> Bill:
    world.add_bill("3040", "Projekt ustawy o cudzoziemcach: nowy druk")
    world.repo.upsert_summary(world.gateway.details["3040"], now=world.clock.now())
    record = world.bill("3039").analysis
    assert record is not None, "the predecessor provides the inherited analysis"
    world.repo.save_analysis(TERM, "3040", record)
    world.repo.link_bills(TERM, "3039", "3040")
    return world.bill("3040")


def edit(case: EditCase, caller: Caller, *, alias: Publication | None = None) -> None:
    world = case.world
    if caller == "publishing":
        assert world.container.publishing_service(dry_run=False).publish_bill(successor(world))
        return
    number = alias.number if alias is not None else "3039"
    owner = alias if alias is not None else case.card
    if caller == "poster":
        world.container.poster(dry_run=False).rerender_card(world.bill(number), owner)
    else:
        world.container.tracking_service(dry_run=False).check_bill(world.bill(number))


def readers_before(world: World) -> tuple[int, int]:
    return len(world.publisher.sent), len(world.publisher.edited)


@pytest.mark.parametrize("caller", CALLERS)
@pytest.mark.parametrize("status", ACTIVE)
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("child", [False, True])
def test_every_direct_editor_preserves_open_saved_root_or_child_before_producer_activation(
    caller: Caller, status: PublicationStatus, legacy: bool, child: bool
) -> None:
    case = saved_edit(status, legacy=legacy, child=child)
    world = case.world
    changed(world)
    root, operation = row(world, case.root), row(world, case.operation)
    stored = world.repo.dump()
    before = readers_before(world)

    assert open_card_edit_ids(world.repo, case.card) == (case.operation,)
    assert world.repo.dump() == stored
    edit(case, caller)

    assert readers_before(world) == before
    assert row(world, case.root) == root and row(world, case.operation) == operation
    assert world.publication("3039") == case.card
    if caller == "publishing":
        alias = world.publication("3040")
        assert alias is not None, "the logical successor still inherits its existing thread"
        assert alias.message_id == case.card.message_id and alias.status is PublicationStatus.SENT
        assert open_card_edit_ids(world.repo, alias) == (case.operation,)


@pytest.mark.parametrize("caller", ["tracking", "poster"])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("child", [False, True])
def test_a_shared_physical_alias_cannot_bypass_the_older_owners_active_edit(
    caller: Caller, legacy: bool, child: bool
) -> None:
    case = saved_edit(legacy=legacy, child=child)
    world = case.world
    changed(world)
    bill = successor(world)
    alias = inherit_card(
        world.repo,
        term=TERM,
        number=bill.number,
        channel_id=CHANNEL,
        card=case.card,
        now=world.clock.now(),
    )
    before = readers_before(world)
    operation = row(world, case.operation)

    assert open_card_edit_ids(world.repo, alias) == (case.operation,)
    edit(case, caller, alias=alias)

    assert readers_before(world) == before
    assert row(world, case.operation) == operation
    assert world.publication(bill.number) == alias
    assert world.publication("3039") == case.card


@pytest.mark.parametrize("caller", CALLERS)
@pytest.mark.parametrize("status", [PublicationStatus.SENT, PublicationStatus.DISMISSED])
def test_settled_physical_edits_allow_the_legacy_direct_editor_to_proceed(
    caller: Caller, status: PublicationStatus
) -> None:
    case = saved_edit(status)
    world = case.world
    changed(world)
    before = len(world.publisher.edited)

    assert not open_card_edit_ids(world.repo, case.card)
    edit(case, caller)

    assert len(world.publisher.edited) == before + 1
    assert row(world, case.operation).status is status


@pytest.mark.parametrize("caller", CALLERS)
@pytest.mark.parametrize("elsewhere", ["channel", "target"])
def test_unrelated_saved_targets_do_not_pause_the_direct_editor(
    caller: Caller, elsewhere: str
) -> None:
    case = saved_edit(elsewhere=elsewhere)
    world = case.world
    changed(world)
    before = len(world.publisher.edited)

    assert not open_card_edit_ids(world.repo, case.card)
    edit(case, caller)

    assert len(world.publisher.edited) == before + 1
    assert row(world, case.operation).status is PublicationStatus.UNKNOWN


@pytest.mark.parametrize("status", [PublicationStatus.QUEUED, PublicationStatus.FAILED])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("child", [False, True])
def test_reader_only_barrier_keeps_frozen_recovery_available_then_releases_direct_editors(
    status: PublicationStatus, legacy: bool, child: bool
) -> None:
    case = saved_edit(status, legacy=legacy, child=child)
    world = case.world
    changed(world)
    operation = row(world, case.operation)
    assert operation.delivery is not None, "saved recovery retains its immutable HTML"
    assert open_card_edit_ids(world.repo, case.card) == (case.operation,)
    baseline = world.publication("3039")

    result = world.container.delivery_service(dry_run=False).run()

    assert not result.errors
    assert row(world, case.operation).status is PublicationStatus.SENT
    assert not open_card_edit_ids(world.repo, case.card)
    if child:
        assert world.publisher.sent[-1].text == operation.delivery.rendered_text
    else:
        assert world.publisher.edited[-1][0].text == operation.delivery.rendered_text
        assert world.publisher.edited[-1][1] == operation.delivery.edit_message_id
    assert world.publication("3039") == baseline
    before = len(world.publisher.edited)

    edit(case, "poster")

    assert len(world.publisher.edited) == before + 1


@pytest.mark.parametrize("action", ["confirm", "dismiss"])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("child", [False, True])
def test_unknown_edits_remain_blocked_until_audited_resolution_releases_the_barrier(
    action: str, legacy: bool, child: bool
) -> None:
    case = saved_edit(legacy=legacy, child=child)
    world = case.world
    changed(world)
    before = readers_before(world)
    world.container.delivery_service(dry_run=False).run()
    assert readers_before(world) == before
    assert open_card_edit_ids(world.repo, case.card) == (case.operation,)
    suffix = (
        f"confirm {888 if child else case.card.message_id}"
        if action == "confirm"
        else "dismiss checked"
    )
    incoming = world.command(f"/delivery {case.operation} {suffix}")

    report = _commands_only(world)

    assert not report.commands_failed
    assert world.repo.delivery_resolutions(case.operation)[0].command == incoming
    assert not open_card_edit_ids(world.repo, case.card)
    assert readers_before(world) == before
    edit(case, "poster")
    assert len(world.publisher.edited) == before[1] + 1


def test_reader_barrier_handles_missing_card_target_without_querying_other_messages() -> None:
    case = saved_edit()
    without_target = case.card.model_copy(update={"message_id": None})

    assert open_card_edit_ids(case.world.repo, without_target) == ()
