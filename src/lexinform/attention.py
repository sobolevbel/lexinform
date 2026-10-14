import datetime as dt
from zoneinfo import ZoneInfo

from lexinform.models import (
    AgendaItem,
    AttentionAssessment,
    Bill,
    DecisionState,
    Phase,
    PublicationKind,
    Stage,
    StatusChange,
    flatten_stages,
    hearing_application_deadline,
    is_substantive,
    is_urgent,
    process_evidence,
    update_event,
    window_closes_within,
)

ROUTINE_EVENTS = frozenset(
    {
        "rcl_stage",
        "referral",
        "referrals",
        "committee_report",
        "subcommittee_report",
        "signed",
        "to_president",
    }
)
DECISIONS = frozenset(
    {
        "passed",
        "rejected",
        "not_enacted",
        "veto",
        "veto_overridden",
        "veto_sustained",
        "withdrawn_by_applicant",
        "senate_no_amendments",
        "senate_rejected",
        "senate_amendments",
        "tribunal",
        "tribunal_ruled",
    }
)

REASON_TEXT = {
    "substantive_content": "Substantive text or supporting documents changed.",
    "confirmed_decision": "Decision or process closure evidence changed.",
    "participation_opened": "A participation opportunity opened.",
    "editorial_frame": "A service stage adds no standalone news.",
    "routine_process": "Only routine process facts changed.",
    "unclassified_event": "The facts do not establish a routine event.",
    "new_anchor": "A new case needs its own reader anchor.",
    "act_evidence": "Publication or entry into force is confirmed.",
    "participation_reminder": "An existing participation window needs a reminder.",
    "participation_result": "Consultation results became available.",
    "agenda": "The case appears on a sitting agenda.",
    "agenda_correction": "A previously announced sitting changed.",
    "near_event_correction": "The affected sitting is close.",
    "organ_deadline_not_reader_deadline": "The organ's deadline is not a filing deadline.",
    "near_reader_deadline": "A dated reader action is close.",
    "expired_window": "The dated action window has passed.",
    "missing_reader_deadline": "The reader's deadline is unknown.",
    "existing_window_guard": "The existing participation guard applies.",
    "pilny_guard": "The urgent procedure bypasses editorial waiting.",
    "operator_priority": "An unexpired operator override raises priority.",
}


def assess_attention(
    bill: Bill,
    kind: PublicationKind,
    *,
    now: dt.datetime,
    detected_at: dt.datetime | None = None,
    change: StatusChange | None = None,
    item: AgendaItem | None = None,
    moved_from: AgendaItem | None = None,
    hearing: Stage | None = None,
    with_act: bool = False,
    phase: Phase | None = None,
    urgent_days: int = 3,
) -> AttentionAssessment:
    today = now.astimezone(ZoneInfo("Europe/Warsaw")).date()
    reasons: list[str] = []
    importance: str = "uncertain"
    urgency: str = "normal"
    action = "wait"
    deadline: dt.date | None = None
    source: str | None = None
    if kind is PublicationKind.STATUS_UPDATE and change is not None:
        event = update_event(change, bill)
        if event == "decision_unknown" or (
            bill.has_process
            and change.closure_detected
            and process_evidence(bill.stages, passed=change.passed, closed=True).state
            is DecisionState.UNKNOWN
        ):
            reasons.append("unclassified_event")
        elif change.content_changed or change.amendments or change.supplements:
            importance = "important"
            reasons.append("substantive_content")
        elif change.withdrawn or change.discontinued or event in DECISIONS:
            importance = "important"
            reasons.append("confirmed_decision")
        elif change.consultation_opened or event == "hearing":
            importance = "important"
            reasons.append("participation_opened")
        elif change.new_stages and all(
            not is_substantive(stage) for stage in flatten_stages(change.new_stages)
        ):
            importance = "routine"
            reasons.append("editorial_frame")
        elif event in ROUTINE_EVENTS and all(
            not is_substantive(stage)
            or stage.stage_type
            in {"RclStage", "Referral", "CommitteeReport", "PresidentSignature", "ToPresident"}
            for stage in flatten_stages(change.new_stages)
        ):
            importance = "routine"
            reasons.append("routine_process")
        else:
            reasons.append("unclassified_event")
    elif kind in (PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL):
        importance = "important"
        reasons.append("new_anchor")
    elif (
        kind
        in (
            PublicationKind.ACT_PUBLISHED,
            PublicationKind.IN_FORCE,
            PublicationKind.ACT_APPLICATION,
            PublicationKind.EFFECTIVE_RULES,
        )
        or with_act
    ):
        importance = "important"
        reasons.append("act_evidence")
    elif kind in (PublicationKind.CONSULTATION_DEADLINE, PublicationKind.HEARING_DEADLINE):
        importance = "important"
        reasons.append("participation_reminder")
    elif kind in (PublicationKind.CONSULTATION_RESULTS, PublicationKind.CONSULTATION_REVIEW):
        importance = "important"
        reasons.append("participation_result")
    elif kind in (PublicationKind.AGENDA, PublicationKind.AGENDA_CANCELLED):
        importance = "routine"
        reasons.append(
            "agenda_correction"
            if moved_from or kind is PublicationKind.AGENDA_CANCELLED
            else "agenda"
        )
        dates = [event.date for event in (item, moved_from) if event is not None]
        if dates:
            near = [day for day in dates if 0 <= (day - today).days <= urgent_days]
            deadline = min(near or dates)
            source = "agenda"
        if (
            (moved_from or kind is PublicationKind.AGENDA_CANCELLED)
            and deadline is not None
            and 0 <= (deadline - today).days <= urgent_days
        ):
            urgency = "urgent"
            reasons.append("near_event_correction")
    elif kind is PublicationKind.DECISION_DEADLINE:
        importance = "routine"
        reasons.append("organ_deadline_not_reader_deadline")
        if phase is not None:
            deadline = phase.deadline
            source = f"statutory_term:{phase.key}:" + (
                "receipt" if phase.deadline_exact else "vote_estimate"
            )

    if with_act:
        importance = "important"
        reasons.append("act_evidence")
    groups = (
        tuple(bill.analysis.analysis.affected_groups)
        if bill.analysis is not None
        and (kind is PublicationKind.NEW_BILL or (change is not None and change.content_changed))
        else ()
    )
    if kind is PublicationKind.CONSULTATION_REVIEW and bill.consultation_review is not None:
        groups = tuple(
            dict.fromkeys(
                group
                for finding in bill.consultation_review.review.consultation_findings
                for group in finding.affected_groups
            )
        )
    if (
        kind in (PublicationKind.ACT_APPLICATION, PublicationKind.EFFECTIVE_RULES)
        and bill.act_application_review is not None
    ):
        groups = tuple(
            dict.fromkeys(
                group
                for rule in bill.act_application_review.review.effective_rules
                for group in rule.affected_groups
            )
        )
    window = bill.consultation
    if (
        window is not None
        and window.end is not None
        and (window.end >= today or kind is PublicationKind.CONSULTATION_DEADLINE)
        and (window.email or window.survey_url or window.form_url)
    ):
        deadline = window.end
        source = window.letter_url or window.survey_url or window.form_url or "consultation"
        action = "submit_opinion"
    if hearing is not None:
        deadline = hearing_application_deadline(hearing)
        source = "public_hearing"
        action = "apply_for_hearing"
    elif change is not None:
        for stage in flatten_stages(change.new_stages):
            last_day = hearing_application_deadline(stage)
            if (
                last_day is not None
                and last_day >= today
                and (deadline is None or last_day < deadline)
            ):
                deadline, source, action = last_day, "public_hearing", "apply_for_hearing"
    if (
        (kind in (PublicationKind.ACT_PUBLISHED, PublicationKind.IN_FORCE) or with_act)
        and bill.act is not None
        and bill.act.entry_into_force is not None
    ):
        deadline, source, action = bill.act.entry_into_force, bill.act.eli, "prepare"
    if deadline is not None and action != "wait":
        if 0 <= (deadline - today).days <= urgent_days:
            urgency = "urgent"
            reasons.append("near_reader_deadline")
        elif deadline < today:
            reasons.append("expired_window")
            action = "wait"
    if deadline is None and kind in (
        PublicationKind.CONSULTATION_DEADLINE,
        PublicationKind.HEARING_DEADLINE,
    ):
        urgency = "uncertain"
        reasons.append("missing_reader_deadline")
    if window_closes_within(bill, today, urgent_days) and not is_urgent(bill):
        urgency = "urgent"
        reasons.append("existing_window_guard")
    if is_urgent(bill):
        reasons.append("pilny_guard")
    override = bill.editorial.priority
    if override is not None and now < override.expires_at:
        if override.important:
            importance = "important"
        if override.urgent:
            urgency = "urgent"
        reasons.append("operator_priority")
    return AttentionAssessment(
        importance=importance,
        novelty="new_consequence"
        if "substantive_content" in reasons
        or kind
        in (
            PublicationKind.CONSULTATION_REVIEW,
            PublicationKind.ACT_APPLICATION,
            PublicationKind.EFFECTIVE_RULES,
        )
        else "new_decision"
        if "confirmed_decision" in reasons
        else "new_participation"
        if "participation_opened" in reasons
        else "unknown",
        urgency=urgency,
        reasons=tuple(dict.fromkeys(reasons)),
        explanation=" ".join(REASON_TEXT[reason] for reason in dict.fromkeys(reasons)),
        affected_groups=groups,
        action=action,
        deadline=deadline,
        deadline_source=source,
        deadline_precision=(
            "estimated"
            if kind is PublicationKind.DECISION_DEADLINE
            and phase is not None
            and not phase.deadline_exact
            else "day"
        )
        if deadline is not None
        else "unknown",
        detected_at=detected_at or (change.detected_at if change else now),
        assessed_at=now,
    )
