from typing import Literal

import pytest

from lexinform.models import ComputationResult, NameRecord, ResultApplication, RunMode
from lexinform.services.result_applications import record_retained_name_application
from lexinform.services.tracking.rollover import TermRollover
from lexinform.services.tracking_result import TrackingResult
from tests.fakes import make_analysis
from tests.harness import CHANNEL, RCL, WYKAZ, World
from tests.scenario.test_rcl_name_rollover import waiting_world
from tests.scenario.test_rollover_computation_history import rollover


def named_world() -> tuple[World, ComputationResult]:
    world = waiting_world()
    world.batch.resolve()
    world.run()
    selected = world.bill(RCL).editorial.mnemonic
    assert selected is not None and selected.computation_id is not None, (
        "the actual paid backfill writer binds its selected candidate"
    )
    result = world.repo.get_computation_result(selected.computation_id)
    assert result is not None and isinstance(result.record, NameRecord), (
        "the selected binding resolves to the actual retained paid name"
    )
    return world, result


def test_selected_paid_name_survives_repeated_rollover_restore_and_quiet_repeat() -> None:
    world, paid = named_world()
    original = world.bill(RCL)
    receipts = world.repo.list_result_applications(10, RCL)
    world.clock.advance(days=200)
    world.repo.prune_history(before=world.clock.now(), memo_before=world.clock.now())
    world.repo.restore(world.repo.dump())
    assert rollover(world).rehomed == 1
    service = TermRollover(
        world.repo, world.clock, world.container.poster(dry_run=False), channel_id=CHANNEL
    )
    second = TrackingResult()
    assert service.close_term(11, 12, second, publish=False) and second.rehomed == 1
    current = world.repo.get(12, RCL)
    assert current is not None and current.editorial == original.editorial, (
        "the second rollover retains the selected bound editorial value"
    )
    for term in (11, 12):
        applied = [row for row in world.repo.list_result_applications(term, RCL) if row.target]
        assert len(applied) == 1 and applied[0].result_id == paid.result_id
        assert not world.repo.list_computation_results(term, RCL)
    assert world.repo.list_result_applications(10, RCL) == receipts
    assert world.repo.get_computation_result(paid.result_id) == paid
    world.repo.restore(world.repo.dump())
    frozen = world.repo.dump()
    assert service.close_term(11, 12, TrackingResult(), publish=False)
    assert world.repo.dump() == frozen


@pytest.mark.parametrize("same_name", [False, True])
def test_manual_override_does_not_inherit_paid_binding_or_transfer_old_receipt(
    same_name: bool,
) -> None:
    world, paid = named_world()
    selected = world.bill(RCL).editorial.mnemonic
    assert selected is not None, "named_world applies the selected name"
    name = selected.name if same_name else "Другое имя проекта"
    world.command(f"/name {RCL} {name} | Operator decision")
    world.run(mode=RunMode.COMMANDS, discover=False, track=False, max_analyze=0, max_publish=0)
    current = world.bill(RCL).editorial
    assert current.mnemonic is not None, "the command installs an operator candidate"
    assert current.mnemonic.origin == "operator"
    assert current.mnemonic.computation_id is None and current.mnemonic.request_sha256 is None
    assert current.previous[-1].computation_id == paid.result_id
    assert rollover(world).rehomed == 1
    assert not [row for row in world.repo.list_result_applications(11, RCL) if row.target]


@pytest.mark.parametrize("change", ["unknown", "request", "result", "basis"])
def test_old_or_mismatched_current_name_cannot_borrow_its_historical_receipt(change: str) -> None:
    world, _ = named_world()
    bill = world.bill(RCL)
    selected = bill.editorial.mnemonic
    assert selected is not None, "named_world applies the selected name"
    update: dict[str, object] = (
        {"computation_id": None, "request_sha256": None}
        if change == "unknown"
        else {"request_sha256": "f" * 64}
        if change == "request"
        else {"computation_id": "result-v1:" + "f" * 64}
        if change == "result"
        else {"basis": "An unrelated current basis"}
    )
    world.repo.save_editorial(
        10, RCL, bill.editorial.model_copy(update={"mnemonic": selected.model_copy(update=update)})
    )
    assert rollover(world).rehomed == 1
    assert not [row for row in world.repo.list_result_applications(11, RCL) if row.target]


def test_transfer_failure_rolls_back_government_row_and_all_new_owner_receipts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, paid = named_world()
    before = world.repo.dump()
    save = world.repo.save_result_application

    def reject(application: ResultApplication) -> None:
        save(application)
        if application.target is not None and application.target.kind == "editorial":
            raise RuntimeError("injected selected-name transfer")

    monkeypatch.setattr(world.repo, "save_result_application", reject)
    with pytest.raises(RuntimeError, match="selected-name transfer"):
        rollover(world)
    assert world.repo.dump() == before
    assert world.repo.get_computation_result(paid.result_id) == paid


@pytest.mark.parametrize("caller", ["lookup", "tracking_adopt", "tracking_own_card"])
def test_actual_link_callers_transfer_only_selected_bound_name(
    caller: Literal["lookup", "tracking_adopt", "tracking_own_card"],
) -> None:
    world, paid = named_world()
    original = world.bill(RCL)
    project = original.rcl
    assert project is not None, "named_world enrolls the real RCL predecessor"
    world.add_bill("3100", project.title)
    if caller == "lookup":
        world.repo.save_rcl(10, RCL, project.model_copy(update={"rm_number": "RM-0610-139-26"}))
        world.gateway.details["3100"] = world.gateway.details["3100"].model_copy(
            update={"rcl_num": "RM-0610-139-26"}
        )
        target = world.container.bill_lookup().load("3100")
    else:
        world.repo.upsert_summary(world.gateway.details["3100"], now=world.clock.now())
        if caller == "tracking_own_card":
            assert original.analysis is not None, "named_world completes the original analysis"
            world.repo.save_analysis(10, "3100", original.analysis)
            world.container.publishing_service(dry_run=False).publish_new(min_score=3, limit=10)
            assert world.publication("3100") is not None, "the print has a card before linking"
        moved = project.model_copy(update={"print_number": "3100"})
        world.rcl.put(moved)
        world.repo.save_rcl(10, RCL, moved)
        assert world.run(discover=False, max_analyze=0, max_publish=0).linked == 1
        target = world.bill("3100")
    assert target.editorial.mnemonic == original.editorial.mnemonic
    receipts = [row for row in world.repo.list_result_applications(10, "3100") if row.target]
    assert len(receipts) == 1 and receipts[0].result_id == paid.result_id
    assert world.repo.get_computation_result(paid.result_id) == paid
    world.repo.restore(world.repo.dump())
    before = world.repo.dump()
    with world.repo.atomic():
        record_retained_name_application(world.repo, world.bill(RCL), target, now=world.clock.now())
    assert world.repo.dump() == before


@pytest.mark.parametrize("caller", ["lookup", "tracking"])
def test_link_caller_rolls_back_inheritance_when_selected_name_receipt_fails(
    caller: Literal["lookup", "tracking"], monkeypatch: pytest.MonkeyPatch
) -> None:
    world, _ = named_world()
    original = world.bill(RCL)
    project = original.rcl
    assert project is not None, "named_world enrolls the RCL predecessor"
    world.add_bill("3100", project.title)
    if caller == "lookup":
        world.repo.save_rcl(10, RCL, project.model_copy(update={"rm_number": "RM-0610-139-26"}))
        world.gateway.details["3100"] = world.gateway.details["3100"].model_copy(
            update={"rcl_num": "RM-0610-139-26"}
        )
    else:
        world.repo.upsert_summary(world.gateway.details["3100"], now=world.clock.now())
        moved = project.model_copy(update={"print_number": "3100"})
        world.rcl.put(moved)
        world.repo.save_rcl(10, RCL, moved)
    save = world.repo.save_result_application

    def reject(application: ResultApplication) -> None:
        save(application)
        if application.target is not None and application.target.kind == "editorial":
            raise RuntimeError("injected linked name receipt")

    monkeypatch.setattr(world.repo, "save_result_application", reject)
    if caller == "lookup":
        with pytest.raises(RuntimeError, match="linked name receipt"):
            world.container.bill_lookup().load("3100")
    else:
        world.run(discover=False, max_analyze=0, max_publish=0, expect_bugs=True)
    assert world.bill(RCL).editorial == original.editorial
    assert world.bill(RCL).linked_number is None
    target = world.repo.get(10, "3100")
    assert target is not None, "lookup stores the fetched print before its linking transaction"
    assert target.editorial.mnemonic is None
    assert not [row for row in world.repo.list_result_applications(10, "3100") if row.target]


def test_actual_primary_writer_transfers_selected_wykaz_name_without_rewriting_paid_result() -> (
    None
):
    answer = make_analysis().model_copy(
        update={"mnemonic_candidate": "Срок решения ВНЖ", "mnemonic_basis": "A residence deadline"}
    )
    world = World(profile="production", batch_script={WYKAZ: answer})
    world.add_wykaz_entry()
    world.run(track=False)
    world.batch.resolve()
    world.run(track=False)
    original = world.bill(WYKAZ)
    selected = original.editorial.mnemonic
    assert selected is not None and selected.computation_id is not None, (
        "the real primary projection binds its selected analysis-origin name"
    )
    paid = world.repo.get_computation_result(selected.computation_id)
    assert paid is not None, "the selected reference resolves to the immutable paid result"
    assert selected.origin == "analysis"
    assert rollover(world).rehomed == 1
    target = world.repo.get(11, WYKAZ)
    assert target is not None and target.editorial.mnemonic == selected, (
        "the government transfer preserves the exact selected editorial value"
    )
    editorial = [row for row in world.repo.list_result_applications(11, WYKAZ) if row.target]
    assert len(editorial) == 1 and editorial[0].result_id == paid.result_id
    assert world.repo.get_computation_result(paid.result_id) == paid
