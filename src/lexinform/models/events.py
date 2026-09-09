"""What a status update is about.

Not every new node of the Sejm's stage tree is news for a reader: "Skierowano do I czytania",
"Praca w komisjach" or the final "Uchwalono" only bracket the events that matter (the referral
itself, the committee's report, the vote). `is_substantive` tells the two apart, `has_news`
decides whether a detected change deserves its own post (service stages are held back and
listed with the next substantive one) and `update_event` names the post: the reader sees
"Сейм принял закон" or "Направлен в комиссии" in the header, not "Обновление".
"""

import datetime as dt

from lexinform.models.bill import Bill, StatusChange
from lexinform.models.rcl import RCL_STAGE_TYPE
from lexinform.models.sejm import Stage

# Nodes that only frame other events. A `SejmReading` is decided case by case (see below).
SERVICE_STAGE_TYPES = frozenset(
    {"Start", "ReadingReferral", "Reading", "CommitteeWork", "ToPresident", "End"}
)
# Regulamin Sejmu art. 70b: applications to a public hearing at least 10 days before it.
HEARING_APPLICATION_DAYS = 10


def is_substantive(stage: Stage) -> bool:
    """True when the stage alone is worth telling the reader about."""
    if stage.stage_type in SERVICE_STAGE_TYPES:
        return False
    if stage.stage_type == "SejmReading":
        # The 3rd reading decides the bill; an earlier reading is news only when it decided
        # something (rejected outright, sent back to the committee with amendments).
        if _reading_numeral(stage) == "III":
            return True
        decided = (stage.decision or "").lower()
        return "odrzuc" in decided or "ponownie" in decided
    return True


def has_news(change: StatusChange, *, act_published: bool = False) -> bool:
    """Whether the change deserves a post now. A closure that arrives together with the act in
    Dziennik Ustaw is not news on its own: the publication notice says it all."""
    if change.withdrawn or change.discontinued or change.content_changed:
        return True
    if change.closure_detected and not act_published:
        return True
    return any(is_substantive(st) for st in change.new_stages)


def update_event(change: StatusChange, bill: Bill) -> str:
    """The one event the post is named after (keys of `Labels.update_headers`)."""
    if change.discontinued:
        return "discontinued"
    if change.withdrawn:
        return "withdrawn"
    if bill.linked_number and bill.has_process and change.old_fingerprint == bill.linked_number:
        return "print_assigned"
    referrals = sum(1 for st in change.new_stages if st.stage_type == "Referral")
    for stage in reversed(change.new_stages):  # the newest stage names the post
        key = _stage_event(stage)
        if key == "referral" and referrals > 1:
            return "referrals"
        if key is not None:
            return key
    if change.closure_detected:
        if bill.rcl is not None:
            return "rcl_closed"
        return "passed" if change.passed else "rejected"
    if change.content_changed:
        return "text_changed"
    return "update"


def _stage_event(stage: Stage) -> str | None:
    kind = stage.stage_type
    decided = (stage.decision or "").lower()
    if kind == RCL_STAGE_TYPE:
        return "rcl_to_sejm" if "sejm" in stage.stage_name.lower() else "rcl_stage"
    if kind == "Referral":
        return "referral_plenary" if stage.committee_code == "Sejm" else "referral"
    if kind == "ReadingReferral":
        return "first_reading_referral"
    if kind == "Reading":
        return "first_reading"
    if kind == "SejmReading":
        numeral = _reading_numeral(stage)
        if "odrzuc" in decided:
            return "rejected"
        if numeral == "III":
            return "passed" if decided.startswith("uchwal") else "third_reading"
        if numeral == "II":
            return "second_reading_amendments" if "ponownie" in decided else "second_reading"
        return "first_reading"
    if kind == "CommitteeWork":
        return "committee_work"
    if kind == "CommitteeReport":
        if stage.sub_committee:
            return "subcommittee_report"
        proposal = (stage.proposal or "").lower()
        if "odrzuc" in proposal and "popraw" not in proposal:
            return "committee_rejects"
        return "committee_report"
    if kind == "SenatePosition":
        position = (stage.position or "").lower()
        if "nie wniósł" in position:
            return "senate_no_amendments"
        if "odrzuci" in position:
            return "senate_rejected"
        if "popraw" in position:
            return "senate_amendments"
        return "senate"
    return {
        "SenatePositionConsideration": "senate_considered",
        "ToPresident": "to_president",
        "PresidentSignature": "signed",
        "Veto": "veto",
        "PresidentToTribunal": "tribunal",
        "PublicHearing": "hearing",
        "Start": "start",
    }.get(kind)


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


def hearing_application_deadline(stage: Stage) -> dt.date | None:
    """Last day to apply for a public hearing (`PublicHearing` stage), if the hearing is dated."""
    if stage.stage_type != "PublicHearing" or stage.date is None:
        return None
    return stage.date - dt.timedelta(days=HEARING_APPLICATION_DAYS)
