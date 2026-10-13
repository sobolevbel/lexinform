import datetime as dt

from lexinform.models import AttentionAssessment, Bill, Phase, PublicationKind


def digest_reason(
    bill: Bill,
    kind: PublicationKind,
    assessment: AttentionAssessment | None,
    *,
    today: dt.date,
    phase: Phase | None = None,
    enrichment: bool = False,
) -> str:
    if assessment is None or assessment.urgency != "normal":
        return ""
    if any(
        reason in assessment.reasons
        for reason in (
            "operator_priority",
            "pilny_guard",
            "existing_window_guard",
            "near_reader_deadline",
        )
    ):
        return ""
    if enrichment:
        return "enrichment_of_delivered_decision"
    if kind is not PublicationKind.DECISION_DEADLINE:
        return ""
    if phase is not None and phase.key == "senate":
        senate = bill.senate
        if (
            senate is not None
            and not senate.committees_done(today)
            and any(committee.email for committee in senate.committees)
        ):
            return ""
    return "organ_deadline_without_confirmed_reader_action"
