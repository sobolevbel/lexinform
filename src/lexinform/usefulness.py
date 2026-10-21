import datetime as dt

from lexinform.models import (
    RCL_STAGE_TYPE,
    AttentionAssessment,
    Bill,
    Phase,
    PublicationKind,
    Stage,
    StatusChange,
    flatten_stages,
    is_substantive,
)


def _news_of_its_own(stage: Stage) -> bool:
    """A committee to write to, a consultation to join, or the President's signature."""
    if stage.stage_type == "Referral":
        return True
    if stage.stage_type == RCL_STAGE_TYPE:
        return "konsultacje publiczne" in stage.stage_name.lower()
    return stage.stage_type == "PresidentSignature"


def digest_reason(
    bill: Bill,
    kind: PublicationKind,
    assessment: AttentionAssessment | None,
    *,
    today: dt.date,
    phase: Phase | None = None,
    enrichment: bool = False,
    change: StatusChange | None = None,
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
    if (
        kind is PublicationKind.STATUS_UPDATE
        and change is not None
        and assessment.importance == "routine"
        and "routine_process" in assessment.reasons
        and any(
            stage.stage_type == RCL_STAGE_TYPE and "komisja prawnicza" in stage.stage_name.lower()
            for stage in flatten_stages(change.new_stages)
        )
        and all(
            not is_substantive(stage)
            or (
                stage.stage_type == RCL_STAGE_TYPE
                and "komisja prawnicza" in stage.stage_name.lower()
            )
            for stage in flatten_stages(change.new_stages)
        )
    ):
        return "legal_commission_without_confirmed_consequence"
    if (
        kind is PublicationKind.STATUS_UPDATE
        and change is not None
        and assessment.importance == "routine"
        and not any(_news_of_its_own(stage) for stage in flatten_stages(change.new_stages))
    ):
        return "routine_step_without_reader_action"
    if kind is PublicationKind.CONSULTATION_RESULTS:
        return "documents_without_confirmed_consequence"
    if kind is PublicationKind.COMMISSION_REVIEW:
        record = bill.commission_review
        if record is None or not any(
            item.text_change or (item.practical_impact and item.affected_groups)
            for item in record.review.commission_findings
        ):
            return "commission_without_reader_consequence"
        return ""
    if kind is PublicationKind.CONSULTATION_REVIEW:
        record = bill.consultation_review
        if record is None or not any(
            finding.text_change or (finding.outcome != "unknown" and finding.affected_groups)
            for finding in record.review.consultation_findings
        ):
            return "documents_without_confirmed_consequence"
        return ""
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
