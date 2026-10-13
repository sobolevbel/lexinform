from lexinform.models import DecisionState, Stage, process_evidence, process_rejected


def test_process_listing_does_not_end_an_unfinished_bill() -> None:
    from_result = process_evidence((), passed=False, closed=False)
    assert from_result.state is DecisionState.ABSENT
    assert not process_rejected(from_result)


def test_process_pending_veto_overrules_listing() -> None:
    stages = (Stage(stage_name="Weto", stage_type="Veto"),)
    evidence = process_evidence(stages, passed=True, closed=True)
    assert evidence.state is DecisionState.PENDING
    assert not process_rejected(evidence)


def test_negative_listing_conflicting_with_adoption_stays_unknown() -> None:
    stages = (Stage(stage_name="III czytanie", stage_type="SejmReading", decision="uchwalono"),)
    evidence = process_evidence(stages, passed=False, closed=True)
    assert evidence.state is DecisionState.UNKNOWN
    assert not process_rejected(evidence)


def test_positive_listing_never_overrules_a_sustained_veto() -> None:
    stages = (
        Stage(stage_name="Weto", stage_type="Veto"),
        Stage(
            stage_name="Rozpatrywanie wniosku",
            stage_type="PresidentMotionConsideration",
            decision="nie uchwalona ponownie",
        ),
    )
    assert process_rejected(process_evidence(stages, passed=True, closed=True))
