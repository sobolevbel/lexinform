import sqlite3
from collections.abc import Callable

import pytest

from lexinform.models import AnalysisRecord, Bill, ResultApplication
from lexinform.primary_projections import PrimaryProjectionError
from lexinform.services.tracking.rollover import TermRollover
from lexinform.services.tracking_result import TrackingResult
from tests.harness import CHANNEL, RCL, WYKAZ, World
from tests.primary_projections import reference


def compact_projection(world: World, term: int, number: str) -> None:
    bill = world.repo.get(term, number)
    assert bill is not None and bill.analysis is not None, "the paid government fixture was applied"
    assert bill.analysis.computation_id is not None, "the actual batch retained its computation"
    result = world.repo.get_computation_result(bill.analysis.computation_id)
    assert result is not None and isinstance(result.record, AnalysisRecord), "the result is primary"
    observation = {
        key: value
        for key, value in bill.analysis.model_dump(mode="json").items()
        if value != result.record.model_dump(mode="json").get(key)
    }
    ref = reference(result).model_copy(update={"observation": observation})
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(world.repo.dump())
        connection.execute(
            "UPDATE bills SET analysis_json=? WHERE term=? AND number=?",
            (
                ref.model_dump_json(),
                term,
                number,
            ),
        )
        script = "\n".join(connection.iterdump())
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        connection.close()
    world.repo.restore(script + f"\nPRAGMA user_version = {version};\n")


def paid_world(source: str) -> tuple[World, str]:
    world = World(profile="production")
    if source == "rcl":
        world.add_rcl_project()
        number = RCL
    else:
        world.add_wykaz_entry()
        number = WYKAZ
    world.run(track=False)
    world.batch.resolve()
    world.run(track=False)
    compact_projection(world, 10, number)
    return world, number


def close_term(world: World, old: int, new: int) -> TrackingResult:
    result = TrackingResult()
    service = TermRollover(
        world.repo,
        world.clock,
        world.container.poster(dry_run=False),
        channel_id=CHANNEL,
    )
    assert service.close_term(old, new, result, publish=False)
    return result


@pytest.mark.parametrize("source", ["rcl", "wykaz"])
def test_compact_current_government_projection_rehomes_before_target_hydration(source: str) -> None:
    world, number = paid_world(source)
    original = world.bill(number)
    world.clock.advance(days=300)
    world.repo.prune_history(before=world.clock.now(), memo_before=world.clock.now())
    assert not world.repo.load_analysis_memo()
    paid = world.repo.list_computation_results(10, number)
    assert close_term(world, 10, 11).rehomed == 1
    current = world.repo.get(11, number)
    assert current is not None and current.analysis == original.analysis
    assert close_term(world, 11, 12).rehomed == 1
    carried = world.repo.get(12, number)
    assert carried is not None and carried.analysis == original.analysis
    assert world.repo.list_computation_results(10, number) == paid
    before = world.repo.dump()
    assert close_term(world, 11, 12).rehomed == 0
    world.repo.restore(world.repo.dump())
    assert world.repo.dump() == before


def test_pre_hydration_receipt_failure_rolls_back_owner_move(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, _ = paid_world("rcl")
    before = world.repo.dump()
    original: Callable[[ResultApplication], None] = world.repo.save_result_application

    def fail(application: ResultApplication) -> None:
        if application.term == 11:
            raise RuntimeError("receipt failed")
        original(application)

    monkeypatch.setattr(world.repo, "save_result_application", fail)
    with pytest.raises(RuntimeError, match="receipt failed"):
        close_term(world, 10, 11)
    assert world.repo.dump() == before


def test_unconfirmed_move_count_rolls_back_without_target_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, _ = paid_world("wykaz")
    before = world.repo.dump()
    original = world.repo.move_government_rows

    def wrong_count(old: int, new: int) -> int:
        original(old, new)
        return 0

    monkeypatch.setattr(world.repo, "move_government_rows", wrong_count)
    with pytest.raises(ValueError, match="move count"):
        close_term(world, 10, 11)
    assert world.repo.dump() == before


def test_same_government_number_without_recorded_move_is_not_an_owner_bridge() -> None:
    world, number = paid_world("rcl")
    script = world.repo.dump()
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(script)
        connection.execute(
            "UPDATE bills SET term=11,summary_json=json_set(summary_json,'$.term',11)"
        )
        corrupted = "\n".join(connection.iterdump())
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        connection.close()
    with pytest.raises(PrimaryProjectionError):
        world.repo.restore(corrupted + f"\nPRAGMA user_version = {version};\n")
    assert world.repo.dump() == script and world.repo.get(10, number) is not None


def test_mismatched_hydrated_projection_rolls_back_move_and_new_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, number = paid_world("rcl")
    before = world.repo.dump()
    original: Callable[[int, str], Bill | None] = world.repo.get

    def mismatched(term: int, requested: str) -> Bill | None:
        bill = original(term, requested)
        if term == 11 and requested == number and bill is not None and bill.analysis is not None:
            return bill.model_copy(
                update={
                    "analysis": bill.analysis.model_copy(
                        update={"revision": bill.analysis.revision + 1}
                    )
                }
            )
        return bill

    monkeypatch.setattr(world.repo, "get", mismatched)
    with pytest.raises(ValueError, match="differs from its snapshot"):
        close_term(world, 10, 11)
    assert world.repo.dump() == before
