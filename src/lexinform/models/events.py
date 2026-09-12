"""What a status update is about.

Not every new node of the Sejm's stage tree is news for a reader: "Skierowano do I czytania",
"Praca w komisjach" or the final "Uchwalono" only bracket the events that matter (the referral
itself, the committee's report, the vote). `is_substantive` tells the two apart, `has_news`
decides whether a detected change deserves its own post (service stages are held back and
listed with the next substantive one) and `update_event` names the post: the reader sees
"Сейм принял закон" or "Направлен в комиссии" in the header, not "Обновление".
"""

import datetime as dt

from lexinform.models.bill import Bill, StatusChange, veto_stood
from lexinform.models.rcl import RCL_STAGE_TYPE
from lexinform.models.sejm import Stage, flatten_stages, second_reading_sent_back

SERVICE_STAGE_TYPES = frozenset({"Start", "ReadingReferral", "Reading", "CommitteeWork", "End"})
HEARING_APPLICATION_DAYS = 10


def is_substantive(stage: Stage) -> bool:
    """True when the stage alone is worth telling the reader about.

    `SERVICE_STAGE_TYPES` only frame other events; "Ustawę przekazano Prezydentowi" is not one of
    them, because it starts the 21 days of art. 122, the reader's last window. A reading is
    decided case by case: the third one decides the bill, an earlier one is news only when it
    decided something — rejected the bill outright, or sent it back to the committee with
    amendments.
    """
    if stage.stage_type in SERVICE_STAGE_TYPES:
        return False
    if stage.stage_type == "SejmReading":
        if _reading_numeral(stage) == "III":
            return True
        return "odrzuc" in (stage.decision or "").lower() or second_reading_sent_back(stage)
    return True


def has_news(change: StatusChange, *, act_published: bool = False) -> bool:
    """Whether the change deserves a post now. A closure that arrives together with the act in
    Dziennik Ustaw is not news on its own: the publication notice says it all."""
    if change.withdrawn or change.discontinued or change.content_changed:
        return True
    if change.closure_detected and not act_published:
        return True
    if change.supplements:
        return True
    return any(is_substantive(st) for st in change.new_stages)


def update_event(change: StatusChange, bill: Bill) -> str:
    """The one event the post is named after (keys of `Labels.update_headers`)."""
    if change.discontinued:
        return "discontinued"
    if change.withdrawn:
        return "withdrawn"
    if _is_first_update_of_successor(change, bill):
        return "print_assigned" if bill.has_process else "rcl_started"
    named = _newest_stage_event(change.new_stages)
    if named is not None:
        return named
    if change.closure_detected:
        return _closure_event_of(change, bill)
    if change.content_changed:
        return "text_changed"
    supplement = supplement_event(change)
    if supplement is not None:
        return supplement
    if bill.wykaz is not None and bill.wykaz.is_adopted:
        return "wykaz_adopted"
    return "update"


def supplement_event(change: StatusChange) -> str | None:
    """The filed document the post is named after: the government's position outweighs its
    assessment of the effects. The arrival of the position is also a stage (`GovermentPosition`),
    and one with no name of its own — without this the post that carries the government's verdict
    would be headed "Обновление"."""
    kinds = {record.source_kind for record in change.supplements}
    for kind in ("government_position", "impact_assessment"):
        if kind in kinds:
            return kind
    return None


def _is_first_update_of_successor(change: StatusChange, bill: Bill) -> bool:
    """A row that inherited a thread opens it by saying what it is: the druk of an RPW entry or
    an RCL project, the project of a plan."""
    return bool(bill.linked_number) and change.old_fingerprint == bill.linked_number


def _newest_stage_event(stages: list[Stage]) -> str | None:
    """The newest stage names the post; referrals to several committees are told as a group."""
    referrals = sum(1 for st in stages if st.stage_type == "Referral")
    for stage in reversed(stages):
        key = _stage_event(stage)
        if key == "referral" and referrals > 1:
            return "referrals"
        if key is not None:
            return key
    return None


def _closure_event_of(change: StatusChange, bill: Bill) -> str:
    if bill.wykaz is not None:
        return "wykaz_withdrawn"
    if bill.rcl is not None:
        return "rcl_closed"
    return "passed" if change.passed else closure_event(bill)


def closure_event(bill: Bill) -> str:
    """How a Sejm process ended without a law. `closureDate` with `passed=false` covers a
    rejection, a withdrawal by the applicant and a veto the Sejm could not override, and the
    listing tells none of them apart — so the stages and the `/bills` entry do, and what neither
    explains is told without naming a culprit."""
    if veto_stood(bill.stages):
        return "veto_sustained"
    submission = bill.submission
    if submission is not None and submission.status == "WITHDRAWN":
        return "withdrawn_by_applicant"
    if any(_rejects(stage) for stage in flatten_stages(bill.stages)):
        return "rejected"
    return "not_enacted"


def _rejects(stage: Stage) -> bool:
    if stage.stage_type == "SejmReading":
        return "odrzuc" in (stage.decision or "").lower()
    if stage.stage_type == "CommitteeReport":
        proposal = (stage.proposal or "").lower()
        return "odrzuc" in proposal and "popraw" not in proposal
    return False


_EVENT_BY_STAGE_TYPE = {
    "ReadingReferral": "first_reading_referral",
    "Reading": "first_reading",
    "CommitteeWork": "committee_work",
    "SenatePositionConsideration": "senate_considered",
    "ToPresident": "to_president",
    "PresidentSignature": "signed",
    "Veto": "veto",
    "PresidentToTribunal": "tribunal",
    "PublicHearing": "hearing",
    "Start": "start",
}


def _stage_event(stage: Stage) -> str | None:
    """What one stage is called in a post; None when the stage has no name of its own.

    `_EVENT_BY_STAGE_TYPE` holds the stages whose name is their type; the rest read what the
    stage decided.
    """
    kind = stage.stage_type
    if kind == RCL_STAGE_TYPE:
        return "rcl_to_sejm" if "sejm" in stage.stage_name.lower() else "rcl_stage"
    if kind == "Referral":
        return "referral_plenary" if stage.committee_code == "Sejm" else "referral"
    if kind == "SejmReading":
        return _reading_event(stage)
    if kind == "CommitteeReport":
        return _report_event(stage)
    if kind == "SenatePosition":
        return _senate_event(stage)
    return _EVENT_BY_STAGE_TYPE.get(kind)


def _reading_event(stage: Stage) -> str:
    decided = (stage.decision or "").lower()
    if "odrzuc" in decided:
        return "rejected"
    numeral = _reading_numeral(stage)
    if numeral == "III":
        return "passed" if decided.startswith("uchwal") else "third_reading"
    if numeral == "II":
        return "second_reading_amendments" if second_reading_sent_back(stage) else "second_reading"
    return "first_reading"


def _report_event(stage: Stage) -> str:
    if stage.sub_committee:
        return "subcommittee_report"
    proposal = (stage.proposal or "").lower()
    if "odrzuc" in proposal and "popraw" not in proposal:
        return "committee_rejects"
    return "committee_report"


def _senate_event(stage: Stage) -> str:
    position = (stage.position or "").lower()
    if "nie wniósł" in position:
        return "senate_no_amendments"
    if "odrzuci" in position:
        return "senate_rejected"
    if "popraw" in position:
        return "senate_amendments"
    return "senate"


def _reading_numeral(stage: Stage) -> str:
    name = stage.stage_name.strip().upper()
    for numeral in ("III", "II", "I"):
        if name.startswith(numeral + " "):
            return numeral
    return ""


def amendments_stage(stages: list[Stage]) -> Stage | None:
    """The newest of the new stages that carries amendments as a document: the Senate's
    position with its resolution print, or a committee report that answers amendments (the
    "-A" print after the 2nd reading, the report on the Senate's position) rather than
    attaching a new bill text."""
    for stage in reversed(stages):
        senate = stage.stage_type == "SenatePosition" and bool(stage.print_number)
        position = (stage.position or "").lower()
        if senate and "popraw" in position and "nie wniósł" not in position:
            return stage
        report = stage.stage_type == "CommitteeReport" and bool(stage.report_file)
        if report and not stage.carries_bill_text and "popraw" in (stage.proposal or "").lower():
            return stage
    return None


_EVENT_TAG = {
    "passed": "passed",
    "rejected": "rejected",
    "not_enacted": "rejected",
    "veto_sustained": "rejected",
    "withdrawn_by_applicant": "withdrawn",
    "referral": "committee",
    "referrals": "committee",
    "committee_report": "committee",
    "committee_rejects": "committee",
    "subcommittee_report": "committee",
    "hearing": "hearing",
    "tribunal": "tribunal",
}
_SENATE_STAGES = frozenset({"SenatePosition", "SenatePositionConsideration"})
_PRESIDENT_STAGES = frozenset({"ToPresident", "PresidentSignature"})
FRAME_STAGE_TYPES = frozenset({"ReadingReferral", "CommitteeWork"})


def event_keys(change: StatusChange, event: str) -> list[str]:
    """Which searchable events a status update carries, in display order (the tags). `event` is
    what the post is named after, so that a reader can find every bill the Sejm passed or
    rejected, not only the ones whose stages happen to carry a recognised type; `_EVENT_TAG` maps
    those names to the events a reader searches the channel for."""
    types = {stage.stage_type for stage in flatten_stages(tuple(change.new_stages))}
    keys = [key for key in (_EVENT_TAG.get(event),) if key]
    if "Voting" in types or any(st.voting for st in change.new_stages):
        keys.append("voting")
    if types & _SENATE_STAGES:
        keys.append("senate")
    if types & _PRESIDENT_STAGES:
        keys.append("president")
    if "Veto" in types:
        keys.append("veto")
    if change.amendments is not None or event == "second_reading_amendments":
        keys.append("amendments")
    if any(r.source_kind == "government_position" for r in change.supplements):
        keys.append("government_position")
    if change.content_changed:
        keys.append("new_text")
    if change.withdrawn:
        keys.append("withdrawn")
    if change.discontinued:
        keys.append("discontinued")
    return list(dict.fromkeys(keys))


def reaches_sejm(change: StatusChange) -> bool:
    """The update carries the RCL stage "Skierowanie projektu ustawy do Sejmu"."""
    return any(
        st.stage_type == RCL_STAGE_TYPE and "sejm" in st.stage_name.lower()
        for st in change.new_stages
    )


def told_stages(stages: list[Stage]) -> list[Stage]:
    """The stages worth telling on their own: a frame node ("Skierowano do I czytania", "Praca
    w komisjach") says nothing its children do not, so it is dropped when they are listed
    anyway."""
    return [
        st
        for st in stages
        if not (st.stage_type in FRAME_STAGE_TYPES and any(c in stages for c in st.children))
    ]


def open_hearing(bill: Bill, today: dt.date) -> Stage | None:
    """A public hearing that is announced and has not taken place yet."""
    return next(
        (
            st
            for st in flatten_stages(bill.stages)
            if st.stage_type == "PublicHearing" and (st.date is None or st.date >= today)
        ),
        None,
    )


def hearings_due(bill: Bill, today: dt.date, *, days_before: int) -> list[Stage]:
    """Public hearings a reader should be reminded of now: the application deadline is within
    the next `days_before` days, or it is already the last one — a hearing announced with less
    than the ten days art. 70b asks for would otherwise never be reminded at all, and that is
    the case where a reader most needs to hear about it."""
    return [
        stage
        for stage in flatten_stages(bill.stages)
        if _hearing_reminder_due(stage, today, days_before=days_before)
    ]


def _hearing_reminder_due(stage: Stage, today: dt.date, *, days_before: int) -> bool:
    deadline = hearing_application_deadline(stage)
    if deadline is None or stage.date is None:
        return False
    if today <= deadline <= today + dt.timedelta(days=days_before):
        return True
    return deadline < today <= stage.date


def hearing_application_deadline(stage: Stage) -> dt.date | None:
    """Last day to apply for a public hearing (`PublicHearing` stage), if the hearing is dated:
    Regulamin Sejmu art. 70b asks for the application at least ten days before it."""
    if stage.stage_type != "PublicHearing" or stage.date is None:
        return None
    return stage.date - dt.timedelta(days=HEARING_APPLICATION_DAYS)
