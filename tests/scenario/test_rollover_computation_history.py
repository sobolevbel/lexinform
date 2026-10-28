import sqlite3
from typing import Literal

import pytest

from lexinform.models import ResultApplicabilityObservation, ResultApplication
from lexinform.services.tracking.rollover import TermRollover
from lexinform.services.tracking_result import TrackingResult
from tests.harness import CHANNEL, RCL, WYKAZ, World
from tests.scenario.test_rcl_name_rollover import next_term

Source = Literal["rcl", "wykaz"]


def analyzed_government_world(
    source: Source, *, workers: int = 1, batch: bool = True
) -> tuple[World, str]:
    world = World(profile="production" if batch else "default", workers=workers)
    if source == "rcl":
        world.add_rcl_project()
        number = RCL
    else:
        world.add_wykaz_entry()
        number = WYKAZ
    world.run(track=False)
    if batch:
        world.batch.resolve()
        world.run(track=False)
    assert world.bill(number).analysis is not None, (
        "collection gives the government row its analysis"
    )
    return world, number


def rollover(world: World) -> TrackingResult:
    result = TrackingResult()
    service = TermRollover(
        world.repo, world.clock, world.container.poster(dry_run=False), channel_id=CHANNEL
    )
    assert service.close_term(10, 11, result, publish=False)
    return result


@pytest.mark.parametrize("source", ["rcl", "wykaz"])
@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("cleaned", [False, True])
def test_government_rollover_records_the_retained_analysis_without_rekeying_paid_work(
    source: Source, workers: int, cleaned: bool
) -> None:
    world, number = analyzed_government_world(source, workers=workers)
    original = world.bill(number)
    assert original.analysis is not None and original.analysis.computation_id is not None, (
        "the production profile retains the actual paid result"
    )
    paid = world.repo.get_computation_result(original.analysis.computation_id)
    old_receipts = world.repo.list_result_applications(10, number)
    old_sources = world.repo.list_applicability_observations(10, number)
    requests = list(world.batch.submitted)
    messages = list(world.publisher.sent)
    if cleaned:
        world.clock.advance(days=200)
        world.repo.prune_history(before=world.clock.now(), memo_before=world.clock.now())
        assert not world.repo.load_analysis_memo()
    next_term(world)

    result = rollover(world)
    world.repo.restore(world.repo.dump())
    current = world.repo.get(11, number)

    assert result.rehomed == 1 and world.repo.get(10, number) is None
    assert current is not None and current.analysis == original.analysis
    assert current.analysis_generation == original.analysis_generation
    assert world.repo.get_computation_result(original.analysis.computation_id) == paid
    assert not world.repo.list_computation_results(11, number)
    assert world.repo.list_result_applications(10, number) == old_receipts
    assert world.repo.list_applicability_observations(10, number) == old_sources
    receipts = world.repo.list_result_applications(11, number)
    assert len(receipts) == 1 and receipts[0].result_id == original.analysis.computation_id
    assert receipts[0].generation == original.analysis_generation
    assert receipts[0].phase == "projection" and receipts[0].outcome == "applied"
    sources = world.repo.list_applicability_observations(11, number)
    assert len(sources) == len(old_sources)
    assert [row.source for row in sources] == [row.source for row in old_sources]
    assert world.batch.submitted == requests and world.publisher.sent == messages
    frozen = world.repo.dump()
    assert rollover(world).rehomed == 0
    assert world.repo.dump() == frozen


@pytest.mark.parametrize("source", ["rcl", "wykaz"])
def test_legacy_analysis_without_a_paid_identity_moves_without_inventing_a_receipt(
    source: Source,
) -> None:
    world, number = analyzed_government_world(source, batch=False)
    original = world.bill(number)
    assert original.analysis is not None and original.analysis.computation_id is None, (
        "the legacy sync fixture has no retained paid identity"
    )

    assert rollover(world).rehomed == 1

    current = world.repo.get(11, number)
    assert current is not None and current.analysis == original.analysis
    assert not world.repo.list_result_applications(11, number)
    assert not world.repo.list_applicability_observations(11, number)


@pytest.mark.parametrize("fault", ["application", "source"])
def test_rollover_and_primary_provenance_roll_back_together(
    fault: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, number = analyzed_government_world("rcl")
    original = world.bill(number)
    assert original.analysis is not None and original.analysis.computation_id is not None, (
        "the source has retained paid provenance"
    )
    before = world.repo.dump()

    def refuse_application(application: ResultApplication) -> None:
        raise RuntimeError("injected rollover provenance failure")

    def refuse_source(observation: ResultApplicabilityObservation) -> None:
        raise RuntimeError("injected rollover provenance failure")

    with monkeypatch.context() as patch:
        if fault == "application":
            patch.setattr(world.repo, "save_result_application", refuse_application)
        else:
            patch.setattr(world.repo, "save_applicability_observation", refuse_source)
        with pytest.raises(RuntimeError, match="injected rollover provenance failure"):
            rollover(world)

    assert world.repo.dump() == before
    assert world.repo.get(10, number) == original
    assert world.repo.get(11, number) is None
    assert rollover(world).rehomed == 1


@pytest.mark.parametrize("source", ["rcl", "wykaz"])
def test_an_uncollected_primary_batch_keeps_its_existing_term_and_work_owner(
    source: Source,
) -> None:
    world = World(profile="production")
    if source == "rcl":
        world.add_rcl_project()
        number = RCL
    else:
        world.add_wykaz_entry()
        number = WYKAZ
    world.run(track=False)
    frozen = world.repo.dump()

    assert rollover(world).rehomed == 0

    assert world.repo.dump() == frozen
    assert world.repo.get(10, number) is not None
    assert world.repo.get(11, number) is None


def test_a_target_collision_rolls_back_the_move_and_all_provenance() -> None:
    world, number = analyzed_government_world("rcl")
    original = world.bill(number)
    world.repo.upsert_summary(
        original.summary.model_copy(update={"term": 11}), now=world.clock.now()
    )
    frozen = world.repo.dump()

    with pytest.raises(sqlite3.IntegrityError):
        rollover(world)

    assert world.repo.dump() == frozen


def test_failure_on_the_second_receipt_keeps_both_projects_in_the_original_term(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World(profile="production")
    world.add_rcl_project()
    world.add_wykaz_entry()
    world.run(track=False)
    world.batch.resolve()
    world.run(track=False)
    assert world.bill(RCL).analysis is not None and world.bill(WYKAZ).analysis is not None
    frozen = world.repo.dump()
    save = world.repo.save_result_application
    recorded: list[ResultApplication] = []

    def fail_second(application: ResultApplication) -> None:
        recorded.append(application)
        if len(recorded) == 2:
            raise RuntimeError("second receipt failed")
        save(application)

    with monkeypatch.context() as patch:
        patch.setattr(world.repo, "save_result_application", fail_second)
        with pytest.raises(RuntimeError, match="second receipt failed"):
            rollover(world)

    assert len(recorded) == 2
    assert world.repo.dump() == frozen
