import datetime as dt
from zoneinfo import ZoneInfo

from lexinform.models import (
    AgendaItem,
    AttentionAssessment,
    Bill,
    PublicationKind,
    Stage,
    StatusChange,
    flatten_stages,
    hearing_application_deadline,
    is_urgent,
    update_event,
    window_closes_within,
)

ROUTINE_EVENTS = frozenset(
    {"rcl_stage", "referral", "referrals", "committee_report", "subcommittee_report", "signed"}
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
        if change.content_changed or change.amendments or change.supplements:
            importance = "important"
            reasons.append("substantive_content")
        elif (
            change.withdrawn or change.discontinued or change.closure_detected or event in DECISIONS
        ):
            importance = "important"
            reasons.append("confirmed_decision")
        elif change.consultation_opened or event == "hearing":
            importance = "important"
            reasons.append("participation_opened")
        elif event in ROUTINE_EVENTS:
            importance = "routine"
            reasons.append("routine_process")
        else:
            reasons.append("unclassified_event")
    elif kind in (PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL):
        importance = "important"
        reasons.append("new_anchor")
    elif kind in (PublicationKind.ACT_PUBLISHED, PublicationKind.IN_FORCE) or with_act:
        importance = "important"
        reasons.append("act_evidence")
    elif kind in (PublicationKind.CONSULTATION_DEADLINE, PublicationKind.HEARING_DEADLINE):
        importance = "important"
        reasons.append("participation_reminder")
    elif kind is PublicationKind.CONSULTATION_RESULTS:
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
            deadline = min(dates)
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

    if with_act:
        importance = "important"
        reasons.append("act_evidence")
    window = bill.consultation
    if (
        window is not None
        and window.end is not None
        and window.end >= today
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
    if window_closes_within(bill, today, urgent_days):
        urgency = "urgent"
        reasons.append("existing_window_guard")
    if is_urgent(bill):
        urgency = "urgent"
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
        urgency=urgency,
        reasons=tuple(dict.fromkeys(reasons)),
        explanation="; ".join(dict.fromkeys(reasons)),
        action=action,
        deadline=deadline,
        deadline_source=source,
        deadline_precision="day" if deadline is not None else "unknown",
        detected_at=detected_at or (change.detected_at if change else now),
        assessed_at=now,
    )
