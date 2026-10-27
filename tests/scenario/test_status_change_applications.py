from typing import Literal

import pytest

from lexinform.models import AmendmentsRecord, ResultApplication, StatusChange, SupplementRecord
from lexinform.services.analysis import Waiting
from lexinform.services.status_change_applications import apply_status_change_amendments
from tests.harness import World
from tests.scenario.test_secondary_scan_input import ask, ready_world
from tests.scenario.test_tracking_batch import tracked_world

Kind = Literal["amendments", "supplement"]


def applications(world: World) -> list[ResultApplication]:
    return [
        row for row in world.repo.list_result_applications(10, "3039") if row.target is not None
    ]


def paid_record(world: World, kind: Kind) -> AmendmentsRecord | SupplementRecord:
    assert isinstance(ask(world, kind), Waiting)
    world.analysis.submit_queued_batches()
    world.batch.resolve()
    world.analysis.collect_batches()
    world.repo.restore(world.repo.dump())
    world.analysis.start_run()
    answer = ask(world, kind)
    assert isinstance(answer, (AmendmentsRecord, SupplementRecord)), (
        "the collected item supplies its paid record"
    )
    assert not applications(world)
    return answer


def change(
    world: World, record: AmendmentsRecord | SupplementRecord, sequence: int
) -> StatusChange:
    return StatusChange(
        term=10,
        number="3039",
        old_fingerprint=f"before-{sequence}",
        new_fingerprint=f"after-{sequence}",
        new_stages=[],
        detected_at=world.clock.now(),
        amendments=record if isinstance(record, AmendmentsRecord) else None,
        supplements=[record] if isinstance(record, SupplementRecord) else [],
    )


@pytest.mark.parametrize("kind", ["amendments", "supplement"])
@pytest.mark.parametrize("workers", [1, 4])
def test_paid_result_can_apply_to_two_events_without_another_paid_computation(
    kind: Kind,
    workers: int,
) -> None:
    world = ready_world(workers=workers)
    record = paid_record(world, kind)
    history = world.repo.list_computation_results(10, "3039")
    poster = world.container.poster(dry_run=False)

    first = poster.record_change(change(world, record, 1))
    second = poster.record_change(change(world, record, 2))
    world.repo.restore(world.repo.dump())
    repeated = poster.record_change(change(world, record, 1))

    assert first is not None and first.id is not None, "the first event has its saved ID"
    assert second is not None and second.id is not None, "the second event has its saved ID"
    rows = applications(world)
    assert len(rows) == 2 and repeated is None
    assert {row.target.ref for row in rows if row.target is not None} == {
        str(first.id),
        str(second.id),
    }
    assert all(row.target is not None and row.target.kind == "status_change" for row in rows)
    assert all(row.generation is None and row.phase == "projection" for row in rows)
    assert all(row.result_id == record.computation_id for row in rows)
    assert world.repo.list_computation_results(10, "3039") == history
    assert len(world.batch.submitted) == 2 and not world.publisher.updates


@pytest.mark.parametrize("kind", ["amendments", "supplement"])
def test_new_event_and_its_application_receipt_roll_back_together(
    kind: Kind,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = ready_world()
    record = paid_record(world, kind)
    before = world.repo.list_status_changes(10, "3039")
    save = world.repo.save_result_application

    def crash(application: ResultApplication) -> None:
        if application.target is not None:
            raise RuntimeError("injected event receipt failure")
        save(application)

    with monkeypatch.context() as patch:
        patch.setattr(world.repo, "save_result_application", crash)
        with pytest.raises(RuntimeError, match="injected event"):
            world.container.poster(dry_run=False).record_change(change(world, record, 1))

    assert world.repo.list_status_changes(10, "3039") == before
    assert not applications(world)
    world.repo.restore(world.repo.dump())
    assert world.container.poster(dry_run=False).record_change(change(world, record, 1)) is not None
    assert len(applications(world)) == 1
    assert len(world.batch.submitted) == 2


@pytest.mark.parametrize("workers", [1, 4])
def test_observation_delivery_and_secondary_receipt_share_the_outer_checkpoint(
    workers: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = tracked_world(workers=workers)
    observed = world.bill("3039").observed_process
    world.run()
    world.batch.resolve()

    def crash(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected delivery checkpoint failure")

    with monkeypatch.context() as patch:
        patch.setattr(world.repo, "save_update_delivery", crash)
        world.run()

    assert world.bill("3039").observed_process == observed
    assert not world.repo.list_status_changes(10, "3039")
    assert not applications(world)
    world.repo.restore(world.repo.dump())

    world.run()
    world.run()

    assert len(applications(world)) == 1
    assert len(world.publisher.updates) == 1 and len(world.batch.submitted) == 1


def test_legacy_record_without_a_paid_ref_does_not_invent_an_application() -> None:
    world = ready_world(batch=False)
    record = ask(world, "supplement")
    assert isinstance(record, SupplementRecord), "the compatibility call returns a supplement"
    assert record.computation_id is None

    stored = world.container.poster(dry_run=False).record_change(change(world, record, 1))

    assert stored is not None and not applications(world)


def test_late_amendments_do_not_backfill_an_old_supplement_application() -> None:
    world = ready_world()
    supplement = paid_record(world, "supplement")
    amendments = paid_record(world, "amendments")
    assert isinstance(amendments, AmendmentsRecord), "the second paid result is amendments"
    change_id = world.repo.add_status_change(change(world, supplement, 1))
    assert change_id is not None, "the legacy event was stored without an application receipt"
    world.repo.restore(world.repo.dump())

    apply_status_change_amendments(world.repo, change_id, amendments, now=world.clock.now())
    world.repo.restore(world.repo.dump())
    apply_status_change_amendments(world.repo, change_id, amendments, now=world.clock.now())

    rows = applications(world)
    assert len(rows) == 1 and rows[0].result_id == amendments.computation_id
    assert rows[0].target is not None and rows[0].target.ref == str(change_id)
    stored = world.repo.get_status_change(change_id)
    assert stored is not None and stored.supplements == [supplement]
