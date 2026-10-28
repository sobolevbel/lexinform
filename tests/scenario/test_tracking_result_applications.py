import datetime as dt
from typing import Literal

import pytest

from lexinform.models import ObservedProcess, ResultApplication
from tests.fakes import FakeTextExtractor
from tests.harness import (
    RCL,
    RPW,
    WYKAZ,
    World,
    rcl_document,
    rcl_folder,
    rcl_project,
    rcl_stage,
    submission,
    submission_url,
)


@pytest.mark.parametrize("workers", [1, 4])
def test_linked_print_reuses_the_original_result_with_its_own_application(workers: int) -> None:
    world = World(
        profile="production",
        workers=workers,
        extractor=FakeTextExtractor(
            by_content={
                b"%PDF-orka": "Art. 1. Cudzoziemcy otrzymują poża ru ochronę. " * 40,
                b"%PDF": "Art. 1. Cudzoziemcy otrzymują pożaru ochronę.\n1) przypis " * 40,
            }
        ),
    )
    world.gateway.submissions.append(submission())
    world.gateway.files[submission_url()] = b"%PDF-orka"
    world.run()
    world.batch.resolve()
    world.run()
    original = world.repo.list_computation_results(10, RPW)[0]
    world.gateway.submissions[0] = submission(print_number="3100")
    world.add_bill(
        "3100", "Poselski projekt ustawy o zmianie ustawy o udzielaniu cudzoziemcom ochrony"
    )
    world.touch("3100", dt.datetime(2026, 9, 8, 9, 0))
    world.clock.advance(days=1)

    report = world.run()

    assert report.linked == 1 and report.reanalyzed == 0
    record = world.bill("3100").analysis
    assert record is not None and record.computation_id == original.result_id
    applications = [
        row for row in world.repo.list_result_applications(10, "3100") if row.phase == "projection"
    ]
    assert len(applications) == 1 and applications[0].result_id == original.result_id
    assert applications[0].generation == applications[0].observed_generation == 0
    assert applications[0].reason == "retained_result"
    assert world.repo.get_computation_result(original.result_id) == original
    assert len(world.batch.submitted) == len(world.publisher.new_bills) == 1


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("fault", [None, "receipt", "checkpoint"])
def test_rcl_result_application_shares_the_observation_checkpoint(
    workers: int, fault: Literal["receipt", "checkpoint"] | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    rewritten = b"%PDF-rewritten"
    world = World(
        profile="production",
        workers=workers,
        extractor=FakeTextExtractor(
            by_content={
                rewritten: "Art. 1. Cudzoziemiec składa wniosek o zezwolenie wyłącznie elektronicznie. "
                * 50,
            }
        ),
    )
    project = world.add_rcl_project()
    world.run()
    world.batch.resolve()
    world.run()
    world.clock.advance(days=1)
    date = dt.date(2026, 9, 8)
    document = rcl_document(801, "projekt_po_KP.pdf", created=date)
    world.add_rcl_project(
        project.model_copy(
            update={
                "stages": (
                    *project.stages[:4],
                    rcl_stage(9, "Stały Komitet Rady Ministrów", "reached", modified=date),
                    rcl_stage(
                        10,
                        "Komisja Prawnicza",
                        "active",
                        rcl_folder(777, "Projekt", document),
                        modified=date,
                    ),
                    *project.stages[5:],
                ),
                "modified": date,
            }
        )
    )
    world.rcl.files[document.url] = rewritten
    world.run()
    world.batch.resolve()
    world.analysis.collect_batches()
    before = world.bill(RCL)
    changes = world.repo.list_status_changes(10, RCL)
    paid = world.repo.list_computation_results(10, RCL)[-1]
    assert paid.purpose == "reanalysis"
    save = world.repo.save_result_application
    checkpoint = world.repo.save_observed_process

    def fail_projection(application: ResultApplication) -> None:
        if application.phase == "projection" and application.result_id == paid.result_id:
            raise RuntimeError("injected RCL application failure")
        save(application)

    def fail_checkpoint(term: int, number: str, observed: ObservedProcess) -> None:
        if number == RCL and observed.analysis_revision == 2:
            raise RuntimeError("injected RCL checkpoint failure")
        checkpoint(term, number, observed)

    if fault is not None:
        with monkeypatch.context() as patch:
            if fault == "receipt":
                patch.setattr(world.repo, "save_result_application", fail_projection)
            else:
                patch.setattr(world.repo, "save_observed_process", fail_checkpoint)
            failed = world.run()
        assert failed.errors
        assert world.bill(RCL).analysis == before.analysis
        assert world.bill(RCL).observed_process == before.observed_process
        assert world.repo.list_status_changes(10, RCL) == changes
        assert not [
            row
            for row in world.repo.list_result_applications(10, RCL)
            if row.phase == "projection" and row.result_id == paid.result_id
        ]

    world.run()

    current = world.bill(RCL).analysis
    assert current is not None and current.computation_id == paid.result_id
    applications = [
        row
        for row in world.repo.list_result_applications(10, RCL)
        if row.phase == "projection" and row.result_id == paid.result_id
    ]
    assert len(applications) == 1
    world.run(full_track=True)
    assert (
        len(
            [
                row
                for row in world.repo.list_result_applications(10, RCL)
                if row.phase == "projection" and row.result_id == paid.result_id
            ]
        )
        == 1
    )
    assert len(world.batch.submitted) == 2


@pytest.mark.parametrize("workers", [1, 4])
def test_wykaz_handover_records_the_inherited_result_at_the_rcl_owner(workers: int) -> None:
    world = World(profile="production", workers=workers)
    world.add_wykaz_entry()
    world.run()
    world.batch.resolve()
    world.run()
    original = world.repo.list_computation_results(10, WYKAZ)[0]

    world.add_rcl_project(rcl_project(wykaz_number="UD408"))
    world.run()

    record = world.bill(RCL).analysis
    assert record is not None and record.computation_id == original.result_id
    applications = [
        row for row in world.repo.list_result_applications(10, RCL) if row.phase == "projection"
    ]
    assert len(applications) == 1 and applications[0].reason == "retained_result"
    assert applications[0].generation == 0
    assert world.repo.get_computation_result(original.result_id) == original
    assert world.card_id(RCL) == world.card_id(WYKAZ)
    assert len(world.publisher.new_bills) == 1
