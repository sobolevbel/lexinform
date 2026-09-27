"""Bills with no card are observed for the website: facts and changes stored, nothing delivered."""

import datetime as dt

from lexinform.models import (
    CheckAspect,
    ObservationBasis,
    ObservationMode,
    PublicationKind,
)
from tests.fakes import make_analysis
from tests.harness import (
    COMMITTEE_STAGES,
    RCL,
    TERM,
    WYKAZ,
    World,
    rcl_stage,
    wykaz_entry,
)


def below_bar_world(*, workers: int = 1, observe_unthreaded: bool = True) -> World:
    w = World(
        llm_script={
            "3039": make_analysis(relevant=True, score=2),
            "3040": make_analysis(relevant=False, score=1),
        },
        workers=workers,
        observe_unthreaded=observe_unthreaded,
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o zmianie ustawy o cudzoziemcach")
    return w


def test_modes_follow_the_card_and_the_analysis() -> None:
    w = below_bar_world()
    w.add_bill("3041", "Projekt ustawy o ochronie cudzoziemców")

    w.run()

    assert (w.bill("3039").observation_mode, w.bill("3039").observation_basis) == (
        ObservationMode.FULL,
        ObservationBasis.RELEVANT_ANALYSIS,
    )
    assert (w.bill("3040").observation_mode, w.bill("3040").observation_basis) == (
        ObservationMode.METADATA,
        ObservationBasis.NOT_RELEVANT,
    )
    assert w.bill("3041").observation_basis == ObservationBasis.TELEGRAM_THREAD
    assert w.publication("3039") is None and w.publication("3040") is None


def test_a_change_without_a_card_is_stored_once_and_never_delivered() -> None:
    w = below_bar_world()
    w.run()
    w.set_stages("3039", COMMITTEE_STAGES)
    w.clock.advance(days=1)

    report = w.run()
    w.clock.advance(days=1)
    again = w.run()

    changes = w.repo.list_status_changes(TERM, "3039")
    assert [[s.stage_type for s in c.new_stages] for c in changes] == [
        ["ReadingReferral", "Referral"]
    ]
    assert report.updates == again.updates == 0
    assert w.publisher.updates == []
    assert w.repo.list_due_status_changes("@test", max_attempts=3) == []
    assert w.bill("3039").stages == COMMITTEE_STAGES
    assert w.publication("3039", PublicationKind.STATUS_UPDATE) is None


def test_process_checks_are_recorded_per_bill() -> None:
    w = below_bar_world()
    w.run()
    w.clock.advance(days=1)

    w.run()

    check = w.repo.source_checks(TERM, "3039")[CheckAspect.PROCESS]
    assert check.last_success_at == w.clock.now() and check.failures == 0
    assert CheckAspect.PROCESS in w.repo.source_checks(TERM, "3040")


def test_metadata_observation_asks_the_model_nothing() -> None:
    w = below_bar_world()
    w.run()
    w.file_to_print("3040", "Stanowisko Rządu")
    w.file_to_print("3039", "Stanowisko Rządu")
    w.clock.advance(days=1)

    w.run()

    digested = {ctx.number for ctx in w.llm.supplement_contexts}
    assert "3040" not in digested and "3039" in digested
    metadata = w.repo.list_status_changes(TERM, "3040")
    assert len(metadata) == 1 and metadata[0].supplements[0].digest is None
    assert w.publisher.updates == []


def test_an_operator_choice_survives_the_next_run() -> None:
    w = below_bar_world()
    w.run()
    w.repo.set_observation(TERM, "3040", ObservationMode.OFF, ObservationBasis.OPERATOR)
    w.set_stages("3040", COMMITTEE_STAGES)
    w.clock.advance(days=1)

    w.run()

    assert w.bill("3040").observation_mode == ObservationMode.OFF
    assert w.repo.list_status_changes(TERM, "3040") == []


def test_one_and_four_workers_store_the_same_changes() -> None:
    stored = []
    for workers in (1, 4):
        w = below_bar_world(workers=workers)
        w.run()
        w.set_stages("3039", COMMITTEE_STAGES)
        w.set_stages("3040", COMMITTEE_STAGES)
        w.clock.advance(days=1)
        w.run()
        stored.append(
            [
                (c.number, c.new_fingerprint)
                for number in ("3039", "3040")
                for c in w.repo.list_status_changes(TERM, number)
            ]
        )
    assert stored[0] == stored[1] and len(stored[0]) == 2


def test_switched_off_observation_reads_nothing_without_a_card() -> None:
    w = below_bar_world(observe_unthreaded=False)
    w.run()
    w.set_stages("3039", COMMITTEE_STAGES)
    w.clock.advance(days=1)

    w.run()

    assert w.repo.list_status_changes(TERM, "3039") == []
    assert w.bill("3039").observation_mode is None


def test_an_rcl_project_without_a_card_is_refreshed_and_not_told() -> None:
    w = World(llm_script={RCL: make_analysis(relevant=True, score=2)})
    project = w.add_rcl_project()
    w.run()
    w.clock.advance(days=1)
    w.rcl.put(
        project.model_copy(
            update={
                "stages": (
                    *project.stages[:3],
                    rcl_stage(4, "Opiniowanie", "reached", modified=dt.date(2026, 9, 8)),
                    rcl_stage(
                        9, "Stały Komitet Rady Ministrów", "active", modified=dt.date(2026, 9, 8)
                    ),
                    *project.stages[5:],
                ),
                "modified": dt.date(2026, 9, 8),
            }
        )
    )

    report = w.run()

    assert w.publication(RCL) is None
    assert w.bill(RCL).observation_basis == ObservationBasis.RELEVANT_ANALYSIS
    assert [len(c.new_stages) for c in w.repo.list_status_changes(TERM, RCL)] == [1]
    assert report.updates == 0 and w.publisher.updates == []
    assert w.repo.source_checks(TERM, RCL)[CheckAspect.PROCESS].failures == 0


def test_a_plan_without_a_card_stores_its_withdrawal_silently() -> None:
    w = World(llm_script={WYKAZ: make_analysis(relevant=False, score=1)})
    w.add_wykaz_entry()
    w.run()
    w.clock.advance(days=1)
    w.add_wykaz_entry(entry=wykaz_entry(status="Wycofany"))

    report = w.run()

    stored = w.bill(WYKAZ)
    assert stored.observation_mode == ObservationMode.METADATA
    assert stored.wykaz is not None and stored.wykaz.is_withdrawn
    changes = w.repo.list_status_changes(TERM, WYKAZ)
    assert len(changes) == 1 and changes[0].closure_detected
    assert report.updates == 0 and w.publisher.updates == []
    assert w.wykaz.calls == 4  # discovery and tracking, once each per run
