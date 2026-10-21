import pytest

from lexinform.models.phases import _phase_after, senate_position_reported
from lexinform.models.sejm import Stage

SENATE = Stage(
    stage_type="SenatePosition",
    stage_name="Stanowisko Senatu",
    position="wniósł poprawki",
    print_number="3213",
)
VETO = Stage(stage_type="Veto", stage_name="Weto Prezydenta")


def _report(number: str) -> Stage:
    return Stage(
        stage_type="CommitteeReport",
        stage_name="Sprawozdanie komisji",
        print_number=number,
        report_file=f"{number}.pdf",
    )


def _work(name: str, report: Stage) -> Stage:
    return Stage(stage_type="CommitteeWork", stage_name=name, children=(report,))


def test_a_report_on_the_senates_position_answers_the_senates_amendments() -> None:
    report = _report("3255")
    stages = (
        _work("Praca w komisjach po II czytaniu", _report("2792-A")),
        SENATE,
        _work("Praca w komisjach nad stanowiskiem Senatu", report),
    )

    assert senate_position_reported(report, stages) is SENATE
    assert senate_position_reported(_report("2792-A"), stages) is None


def test_a_senate_position_filed_at_the_head_of_the_tree_does_not_claim_the_a_report() -> None:
    """Druk 1532 of term 9: the API put the Senate's position before the bill's own `Start`."""
    misfiled = (SENATE, _work("Praca w komisjach po II czytaniu", _report("1597-A")))

    assert senate_position_reported(_report("1597-A"), misfiled) is None


@pytest.mark.parametrize(
    "before",
    [
        (VETO,),
        (SENATE, VETO),
        (SENATE.model_copy(update={"position": "nie wniósł poprawek"}),),
        (SENATE.model_copy(update={"print_number": None}),),
    ],
)
def test_a_report_answering_no_amending_senate_position_reads_on_its_own(
    before: tuple[Stage, ...],
) -> None:
    report = _report("3300")

    assert senate_position_reported(report, (*before, _work("Praca w komisjach", report))) is None


def test_an_unrecognised_last_stage_reads_as_unknown_not_over() -> None:
    """A stage type `_phase_after` has no branch for must never read as "road over": that is
    what let a followed bill silently stop getting a card (bug #7, closed alongside incident
    2111 — `_phase_after`'s fallthrough used to be `return None`)."""
    last = Stage(stage_type="CommitteeReport", stage_name="Sprawozdanie komisji")
    phase = _phase_after(last, [last], urgent=False, passed=None, senate_days=30)
    assert phase is not None, "an unrecognised last stage is an unknown decision, not the end"
    assert phase.key == "decision_unknown"
