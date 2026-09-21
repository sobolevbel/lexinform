from lexinform.models.phases import _phase_after
from lexinform.models.sejm import Stage


def test_an_unrecognised_last_stage_reads_as_unknown_not_over() -> None:
    """A stage type `_phase_after` has no branch for must never read as "road over": that is
    what let a followed bill silently stop getting a card (bug #7, closed alongside incident
    2111 — `_phase_after`'s fallthrough used to be `return None`)."""
    last = Stage(stage_type="CommitteeReport", stage_name="Sprawozdanie komisji")
    phase = _phase_after(last, [last], urgent=False, passed=None, senate_days=30)
    assert phase is not None
    assert phase.key == "decision_unknown"
