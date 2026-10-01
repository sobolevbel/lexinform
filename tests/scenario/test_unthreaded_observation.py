"""Bills with no card are observed for the website: facts and changes stored, nothing delivered."""

import datetime as dt

import pytest

from lexinform.models import (
    CheckAspect,
    ObservationBasis,
    ObservationMode,
    OutcomeStatus,
    PublicationKind,
    Stage,
)
from tests.fakes import FakeTextExtractor, make_analysis
from tests.harness import (
    COMMITTEE_STAGES,
    ELI,
    RCL,
    REFERRED,
    TERM,
    WYKAZ,
    World,
    act,
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


def test_a_relevant_bill_far_under_the_bar_is_observed_without_the_model() -> None:
    w = World(llm_script={"3039": make_analysis(relevant=True, score=1)})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.file_to_print("3039", "Stanowisko Rządu")
    w.clock.advance(days=1)

    w.run()

    bill = w.bill("3039")
    assert (bill.observation_mode, bill.observation_basis) == (
        ObservationMode.METADATA,
        ObservationBasis.BELOW_BAR,
    )
    assert w.llm.supplement_contexts == []
    assert len(w.repo.list_status_changes(TERM, "3039")) == 1


def test_an_operator_choice_survives_the_next_run() -> None:
    w = below_bar_world()
    w.run()
    w.repo.set_observation(TERM, "3040", ObservationMode.OFF, ObservationBasis.OPERATOR)
    w.set_stages("3040", COMMITTEE_STAGES)
    w.clock.advance(days=1)

    w.run()

    assert w.bill("3040").observation_mode == ObservationMode.OFF
    assert w.repo.list_status_changes(TERM, "3040") == []


def test_observe_pins_metadata_on_a_relevant_bill_and_auto_hands_it_back() -> None:
    w = below_bar_world()
    w.run()
    w.command("/observe 3039 mode=metadata")
    w.file_to_print("3039", "Stanowisko Rządu")
    w.clock.advance(days=1)

    w.run()

    (_, pinned), *_ = w.replier.replies
    assert pinned.status is OutcomeStatus.OBSERVED
    assert pinned.note == "was full (relevant_analysis), now metadata (operator)"
    assert (w.bill("3039").observation_mode, w.bill("3039").observation_basis) == (
        ObservationMode.METADATA,
        ObservationBasis.OPERATOR,
    )
    assert "3039" not in {ctx.number for ctx in w.llm.supplement_contexts}

    w.command("/observe 3039 mode=auto")
    w.clock.advance(days=1)
    w.run()

    assert w.replier.replies[-1][1].note == "was metadata (operator), now full (relevant_analysis)"
    assert w.bill("3039").observation_basis == ObservationBasis.RELEVANT_ANALYSIS


def test_observe_on_a_carded_bill_says_the_card_keeps_it_followed() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.command("/observe 3039 mode=off")

    w.run()

    (_, outcome), *_ = w.replier.replies
    assert outcome.note.endswith("the bill has a card, which is followed in full whatever its mode")
    assert w.bill("3039").observation_mode == ObservationMode.OFF


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


@pytest.mark.parametrize("workers", [1, 4])
def test_unthreaded_batch_wait_survives_restore_and_watermark(workers: int) -> None:
    w = World(
        batch=True,
        batch_kinds=frozenset({"supplement"}),
        llm_script={"3039": make_analysis(relevant=True, score=2)},
        workers=workers,
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    baseline = w.bill("3039").observed_process
    w.file_to_print("3039", "Stanowisko Rządu")
    w.clock.advance(hours=1)

    waiting = w.run()
    since = w.bill("3039").awaiting_batch_since
    assert waiting.batch_waiting == 1 and since == w.clock.now()
    assert w.bill("3039").observed_process == baseline
    w.repo.restore(w.repo.dump())
    w.clock.advance(hours=1)
    w.run(since=w.clock.now())
    assert w.bill("3039").awaiting_batch_since == since
    assert w.bill("3039").observed_process == baseline
    w.batch.resolve()

    completed = w.run(since=w.clock.now())
    repeated = w.run(since=w.clock.now())

    changes = w.repo.list_status_changes(TERM, "3039")
    assert len(changes) == 1 and changes[0].supplements[0].digest is not None
    assert w.bill("3039").awaiting_batch_since is None
    assert not completed.errors and not repeated.errors
    assert not repeated.llm_calls and not w.llm.supplement_contexts
    assert not w.publisher.updates and w.publication("3039") is None
    assert w.repo.list_due_status_changes("@test", max_attempts=3) == []


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("aspect", ["process", "act"])
def test_unthreaded_source_outage_preserves_attempts_and_recovers(
    workers: int, aspect: str
) -> None:
    w = below_bar_world(workers=workers)
    w.run()
    attempts = w.bill("3039").analysis_attempts
    w.set_stages("3039", COMMITTEE_STAGES)
    if aspect == "act":
        w.publish_act("3039")
        w.gateway.acts[ELI] = act()
    w.clock.advance(days=1)
    w.gateway.outages.add("get_act" if aspect == "act" else "get_process")

    failed = w.tracking.check_updates()

    assert failed.fatal_error is not None
    assert w.bill("3039").analysis_attempts == attempts
    check = w.repo.source_checks(TERM, "3039")[CheckAspect(aspect)]
    assert check.failures == 0 and check.last_outage_at == w.clock.now()
    w.gateway.outages.clear()
    w.clock.advance(hours=1)
    recovered = w.tracking.check_updates()
    assert recovered.fatal_error is None
    if aspect == "act":
        assert w.bill("3039").act is not None
    assert w.repo.source_checks(TERM, "3039")[CheckAspect(aspect)].last_success_at == w.clock.now()
    assert not w.publisher.updates and w.publication("3039") is None


def test_missing_act_is_not_fresh_and_a_cached_act_does_not_fake_a_new_check() -> None:
    w = below_bar_world()
    w.run()
    w.publish_act("3039")
    w.clock.advance(days=1)

    w.tracking.check_updates()

    missing = w.repo.source_checks(TERM, "3039")[CheckAspect.ACT]
    assert missing.last_success_at is None and missing.failures == 1
    w.gateway.acts[ELI] = act()
    w.clock.advance(hours=1)
    w.tracking.check_updates()
    checked = w.repo.source_checks(TERM, "3039")[CheckAspect.ACT]
    assert checked.last_success_at == w.clock.now() and checked.failures == 0
    w.clock.advance(hours=1)
    w.tracking.check_updates()
    assert w.repo.source_checks(TERM, "3039")[CheckAspect.ACT] == checked


REPORT_URL = "https://api.test/sejm/term10/prints/2689/2689.pdf"
WITH_REPORT = REFERRED + (
    Stage(
        stage_name="Praca w komisjach po I czytaniu",
        stage_type="CommitteeWork",
        date=dt.date(2026, 9, 6),
        children=(
            Stage(
                stage_name="Sprawozdanie komisji",
                stage_type="CommitteeReport",
                date=dt.date(2026, 9, 6),
                print_number="2689",
                report_file=REPORT_URL,
                proposal="uchwalić załączony projekt ustawy",
            ),
        ),
    ),
)


def test_a_new_text_that_reaches_the_bar_gets_a_card_without_replaying_its_history() -> None:
    w = World(
        llm_script={"3039": make_analysis(relevant=True, score=2)},
        extractor=FakeTextExtractor(by_content={b"%PDF-report": "Art. 1. Nowy tekst. " * 100}),
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.llm.script = {"3039": make_analysis(relevant=True, score=3)}
    w.set_stages("3039", WITH_REPORT)
    w.gateway.files[REPORT_URL] = b"%PDF-report"
    w.clock.advance(days=1)

    reanalysed = w.run()
    w.clock.advance(days=1)
    carded = w.run()
    w.clock.advance(days=1)
    settled = w.run()

    assert reanalysed.reanalyzed == 1
    assert len(w.publisher.texts(PublicationKind.NEW_BILL)) == 1
    assert w.publication("3039") is not None
    assert w.bill("3039").observation_basis == ObservationBasis.TELEGRAM_THREAD
    assert w.publisher.updates == []
    assert (carded.updates, settled.updates, settled.reanalyzed) == (0, 0, 0)
