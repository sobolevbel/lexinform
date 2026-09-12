"""The aggregate the services work on: a bill with its status, stages, analysis, submission,
act and authors, plus the two bookkeeping rows (publications and detected status changes)."""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lexinform.keywords import KEYWORD_PATTERNS
from lexinform.models.analysis import AmendmentsRecord, AnalysisRecord
from lexinform.models.enums import ApplicantType, BillStatus, PublicationKind, PublicationStatus
from lexinform.models.rcl import RclProject
from lexinform.models.sejm import (
    ActInfo,
    AgendaItem,
    BillAuthors,
    BillSubmission,
    ProcessSummary,
    Stage,
    TextDocument,
    flatten_stages,
    second_reading_sent_back,
)
from lexinform.models.wykaz import WykazEntry


class ConsultationWindow(BaseModel):
    """The public consultation of a bill, whoever runs it.

    The Sejm consults deputies', Senate, presidential and citizens' bills through a web form
    (`form_url`); the government consults its own on RCL, by e-mail to the ministry named in the
    consultation letter (`email`, `letter_url`).
    """

    model_config = ConfigDict(frozen=True)

    source: Literal["sejm", "rcl"]
    start: dt.date | None = None
    end: dt.date | None = None
    form_url: str | None = None
    email: str | None = None
    letter_url: str | None = None
    results_published: bool = False

    def is_open(self, today: dt.date) -> bool:
        return self.end is not None and self.end >= today


class Bill(BaseModel):
    """One row of the `bills` table: everything we know and decided about a bill."""

    summary: ProcessSummary
    status: BillStatus
    prefilter_hits: list[str] = Field(default_factory=list)
    stages: tuple[Stage, ...] = ()
    stages_fingerprint: str | None = None
    analysis: AnalysisRecord | None = None
    analysis_attempts: int = 0
    last_error: str | None = None
    submission: BillSubmission | None = None  # the /bills entry (consultation dates, RPW number)
    linked_number: str | None = None  # RPW/RCL <-> print number once the print is assigned
    # Wykaz number (UC104) of the RCL project a print continues: its card is tagged with it.
    linked_wykaz_number: str | None = None
    act: ActInfo | None = None  # the published act, once it appears in Dziennik Ustaw
    authors: BillAuthors | None = None  # signatories of a deputies' bill, by club
    agenda: tuple[AgendaItem, ...] = ()  # upcoming sittings that name the bill, soonest first
    rcl: RclProject | None = None  # the RCL project of a government bill followed before the Sejm
    wykaz: WykazEntry | None = None  # the register entry of a bill the government only announced
    # Set when the Sejm term ended with the bill unfinished (zasada dyskontynuacji): nothing
    # more will happen to it under this number, so it is not tracked or analysed anymore.
    discontinued_at: dt.datetime | None = None
    first_seen_at: dt.datetime
    last_checked_at: dt.datetime

    @property
    def term(self) -> int:
        return self.summary.term

    @property
    def number(self) -> str:
        return self.summary.number

    @property
    def is_pre_print(self) -> bool:
        return self.summary.is_pre_print

    @property
    def is_rcl(self) -> bool:
        return self.summary.is_rcl

    @property
    def is_wykaz(self) -> bool:
        return self.summary.is_wykaz

    @property
    def has_process(self) -> bool:
        return self.summary.has_process

    @property
    def last_stage(self) -> Stage | None:
        """Where the bill stands; `process_stages` names the nodes that do not answer that."""
        flat = flatten_stages(tuple(process_stages(self.stages)))
        return flat[-1] if flat else None

    @property
    def consultation(self) -> ConsultationWindow | None:
        """The public consultation, if the bill has (or had) one."""
        if self.rcl is not None:
            rcl = self.rcl.consultation
            if rcl is None:
                return None
            return ConsultationWindow(
                source="rcl",
                start=rcl.letter_date,
                end=rcl.deadline,
                email=rcl.email,
                letter_url=rcl.letter_url,
                results_published=rcl.results_published,
            )
        sub = self.submission
        if sub is None or not sub.public_consultation or sub.consultation_end is None:
            return None
        return ConsultationWindow(
            source="sejm",
            start=sub.consultation_start,
            end=sub.consultation_end,
            form_url=sub.consultation_url,
            results_published=sub.consultation_results,
        )


class LocatedText(BaseModel):
    """What a text source found for a bill: fresh metadata, the stage tree (when the source has
    one) and the document to read. Any part may be missing."""

    model_config = ConfigDict(frozen=True)

    summary: ProcessSummary | None = None
    stages: tuple[Stage, ...] | None = None
    document: TextDocument | None = None


class Phase(BaseModel):
    """What the legislative process holds next for a bill (see `next_phase`)."""

    model_config = ConfigDict(frozen=True)

    key: str
    committees: tuple[str, ...] = ()  # codes of the committees the bill sits in, if any
    date: dt.date | None = None  # consultation end or entry into force, when the key needs one
    # The constitutional deadline of the step, when the process gives one: the Senate has 30
    # days from receiving the act (art. 121), the President 21 from receiving it (art. 122);
    # 14 and 7 for an urgent bill (art. 123). Counted from the dates the Sejm API shows.
    deadline: dt.date | None = None
    since: dt.date | None = None  # when the bill reached this step; stamped by `next_phase`


_PRESIDENT_NEXT = {"ToPresident", "SenatePositionConsideration"}
# Stages that arrive next to the process without moving it: the government's position on a
# deputies' bill, an opinion of local-government bodies. They land last in the tree while the
# bill sits in committee, and taking them for the current step loses "what comes next" entirely.
ASIDE_STAGE_TYPES = frozenset({"GovermentPosition", "Opinion"})


def veto_stood(stages: tuple[Stage, ...]) -> bool:
    """The Sejm voted on the President's veto and did not reach the 3/5 majority: the process
    closes with "nie uchwalona ponownie po wecie Prezydenta" as its last node."""
    return any(
        stage.stage_type == "End" and "nie uchwalona ponownie" in stage.stage_name.lower()
        for stage in stages
    )


def process_stages(stages: tuple[Stage, ...]) -> list[Stage]:
    """The top-level stages that say where the bill stands.

    Two kinds of node are dropped. `ASIDE_STAGE_TYPES` arrive beside the process without moving
    it. And `End` ("Uchwalono") is appended by the Sejm at the third reading and kept last while
    the Senate, the President and Dziennik Ustaw are all still ahead — druk 2799, read on
    2026-09-12: III czytanie "uchwalono" on 2026-09-04, `End` already there, no Senate stage and
    no act — so taking it for the current step marks the reader's last two windows as passed.
    The `End` of a bill a veto killed says something of its own and stays.
    """
    top = [st for st in stages if st.stage_type not in ASIDE_STAGE_TYPES]
    if top and top[-1].stage_type == "End" and not veto_stood((top[-1],)):
        top.pop()
    return top


SENATE_DAYS, SENATE_DAYS_URGENT = 30, 14
PRESIDENT_DAYS, PRESIDENT_DAYS_URGENT = 21, 7
# art. 121/122 count from the day the act is handed over, which the API does not give: the
# deadline is computed from the stage before it and is therefore early by a few days.
DEADLINE_GRACE_DAYS = 7


# The "path" of a bill as a reader sees it, and which step each phase sits on. RCL phases
# (except the hand-over) sit on "rcl"; the government steps are skipped for bills that never
# went through the government.
PATH_STEPS = (
    "wykaz",
    "rcl",
    "sejm",
    "committee",
    "readings",
    "senate",
    "president",
    "journal",
    "in_force",
)
GOVERNMENT_STEPS = frozenset({"wykaz", "rcl"})  # shown only for bills that came from the government
PHASE_STEP = {
    "wykaz": "wykaz",
    "wykaz_to_rcl": "rcl",
    "wykaz_adopted": "sejm",
    "rcl_to_sejm": "sejm",
    "pre_print": "sejm",
    "pre_print_consultation": "sejm",
    "first_reading": "sejm",
    "first_reading_sitting": "sejm",
    "first_reading_committee": "committee",
    "committee_work": "committee",
    "second_reading": "readings",
    "second_reading_committee": "readings",
    "third_reading": "readings",
    "senate": "senate",
    "senate_amendments": "senate",
    "senate_rejection": "senate",
    "president": "president",
    "veto": "president",
    "tribunal": "president",
    "publication": "journal",
    "in_force": "in_force",
    "in_force_unknown": "in_force",
}
# How long a step may take before saying "usually N weeks" contradicts the dates on the same
# card. The numbers are the upper end of `Labels.typical_durations`, which must say the same in
# words; a step not listed here is given the default.
PHASE_PATIENCE = {
    "rcl_to_sejm": 30,
    "wykaz_to_rcl": 60,
    "wykaz_adopted": 60,
    "second_reading": 60,
    "second_reading_committee": 60,
    "third_reading": 60,
    "senate_amendments": 60,
    "senate_rejection": 60,
    "publication": 60,
    "first_reading": 90,
    "first_reading_committee": 90,
    "first_reading_sitting": 90,
    "rcl_council": 90,
    "rcl_committees": 120,
    "wykaz": 210,
    "committee_work": 400,
}
DEFAULT_PATIENCE_DAYS = 180
DAYS_PER_MONTH = 30


def stalled_days(phase: Phase, today: dt.date) -> int | None:
    """How long the bill has been on this step, once that outlived what the step usually takes;
    None while the step is still running on schedule or its start is unknown."""
    since = phase.since
    if since is None:
        return None
    waited = (today - since).days
    patience = PHASE_PATIENCE.get(phase.key, DEFAULT_PATIENCE_DAYS)
    return waited if waited > patience else None


# Phases during which a reader can address a committee, or watch a sitting.
COMMITTEE_PHASES = frozenset(
    {
        "first_reading_committee",
        "committee_work",
        "second_reading_committee",
        "senate_amendments",
        "senate_rejection",
    }
)
SITTING_PHASES = frozenset(
    {
        "first_reading_sitting",
        "second_reading",
        "third_reading",
        "senate_amendments",
        "senate_rejection",
    }
)
_UKRAINE = next(p.regex for p in KEYWORD_PATTERNS if p.name == "obywatele_ukrainy")


def government_path(bill: Bill) -> bool:
    """Government bills start on RCL; the path shows that step only for them."""
    return (
        bill.rcl is not None
        or bool(bill.summary.rcl_num)
        or bill.summary.applicant_type is ApplicantType.GOVERNMENT
    )


def is_urgent(bill: Bill) -> bool:
    """The government declared the bill pilny (art. 123): every step of the road is shorter —
    the Senate has 14 days instead of 30, the President 7 instead of 21, and the Sejm measured
    2–17 days from submission to the third reading (5 urgent bills of term 10, median 3, against
    56 for the rest)."""
    status = bill.summary.urgency_status
    return bool(status) and status != "NORMAL"


def consultation_open(bill: Bill, today: dt.date) -> bool:
    window = bill.consultation
    return window is not None and window.is_open(today)


def about_ukraine(bill: Bill) -> bool:
    """Prefilter hit on "obywatele Ukrainy" (title or text), or the title says so itself."""
    hits = {hit.removeprefix("text:") for hit in bill.prefilter_hits}
    if "obywatele_ukrainy" in hits:
        return True
    s = bill.summary
    return bool(_UKRAINE.search(f"{s.title} {s.description or ''}"))


def next_phase(bill: Bill, *, today: dt.date) -> Phase | None:
    """The next step of the process, derived from the top-level stages, the submission and the
    act; None when the process is over (in force, rejected, withdrawn) or unknown.

    Keys: rcl_consultation, rcl_opinions, rcl_committees, rcl_council, rcl_to_sejm (government
    projects before the Sejm), wykaz, wykaz_to_rcl, wykaz_adopted, pre_print,
    pre_print_consultation, first_reading, first_reading_committee, first_reading_sitting,
    committee_work, second_reading, second_reading_committee, third_reading, senate,
    senate_amendments, senate_rejection, president, publication, in_force, in_force_unknown,
    veto, tribunal.
    """
    phase = _phase_of(bill, today)
    return phase if phase is None else phase.model_copy(update={"since": _phase_started(bill)})


def _phase_started(bill: Bill) -> dt.date | None:
    """When the bill reached the step it is on, so that "what comes next" can say how long it
    has been waiting instead of quoting an average that ran out long ago."""
    last = bill.last_stage
    if last is not None:
        # Its own date or nothing: a stage the API left undated started on an unknown day, not
        # on the day the bill was submitted, and reading it as the latter ages the step by the
        # whole life of the bill.
        return last.date
    if bill.rcl is not None:
        # RCL's "rozpoczęcie" is usually absent; the stage's last modification is what the page
        # actually shows, and it is what stops moving when a project stalls.
        current = bill.rcl.current_stage
        return current.modified if current is not None else None
    if bill.wykaz is not None:
        return bill.wykaz.published_at.date()
    submission = bill.submission
    return submission.date_of_receipt if submission is not None else None


def _phase_of(bill: Bill, today: dt.date) -> Phase | None:
    summary = bill.summary
    act = bill.act
    if act is not None:
        if act.entry_into_force is None:
            return Phase(key="in_force_unknown")
        if act.entry_into_force > today:
            return Phase(key="in_force", date=act.entry_into_force)
        return None
    if bill.discontinued_at is not None:
        return None  # lapsed with the end of the term: a new Sejm must receive it again
    if bill.wykaz is not None:
        return _wykaz_phase(bill)
    if bill.rcl is not None:
        return _rcl_phase(bill, today)
    if bill.is_pre_print or not bill.stages:
        if summary.closure_date is not None:
            return None  # withdrawn before getting a print number
        window = bill.consultation
        if window is not None and window.is_open(today):
            return Phase(key="pre_print_consultation", date=window.end)
        return Phase(key="pre_print")
    if summary.closure_date is not None and summary.passed is False:
        return None  # rejected or withdrawn
    if veto_stood(bill.stages):
        return None  # the Sejm could not override the veto: the law is dead
    top = process_stages(bill.stages)
    if not top:
        return Phase(key="first_reading")
    last = top[-1]
    kind = last.stage_type
    if kind == "PresidentSignature":
        return Phase(key="publication")
    if kind == "Veto":
        return Phase(key="veto")
    if kind == "PresidentToTribunal":
        return Phase(key="tribunal")
    urgent = is_urgent(bill)
    if kind in _PRESIDENT_NEXT:
        days = PRESIDENT_DAYS_URGENT if urgent else PRESIDENT_DAYS
        return Phase(key="president", deadline=_days_after(last.date, days))
    if kind == "SenatePosition":
        position = (last.position or "").lower()
        if "nie wniósł" in position:
            return Phase(key="president")
        if "odrzuci" in position:
            # art. 121 ust. 3: the rejection stands unless the Sejm throws it out by an
            # absolute majority — a different stake from amendments, and a different majority.
            return Phase(key="senate_rejection", committees=_committee_codes(last))
        return Phase(key="senate_amendments", committees=_committee_codes(last))
    if any(st.stage_type == "SenatePosition" for st in top):
        return Phase(key="senate_amendments", committees=_latest_committees(top))
    if kind == "SejmReading":
        name = last.stage_name.lower()
        if "iii czytanie" in name:
            decided = (last.decision or "").lower()
            if decided.startswith("uchwal") or summary.passed:
                days = SENATE_DAYS_URGENT if urgent else SENATE_DAYS
                return Phase(key="senate", deadline=_days_after(last.date, days))
            return None if decided else Phase(key="third_reading")
        if "ii czytanie" in name:
            if second_reading_sent_back(last):
                return Phase(key="second_reading_committee", committees=_latest_committees(top))
            return Phase(key="third_reading")
        return Phase(key="committee_work", committees=_latest_committees(top))
    if kind == "CommitteeWork":
        reports = [c for c in last.children if c.stage_type == "CommitteeReport"]
        if any(r.carries_bill_text for r in reports):
            return Phase(key="second_reading")
        if reports:
            return Phase(key="third_reading")  # an "-A" report answering 2nd-reading amendments
        return Phase(key="committee_work", committees=_latest_committees(top))
    if kind in ("Reading", "PublicHearing"):
        return Phase(key="committee_work", committees=_latest_committees(top))
    if kind == "ReadingReferral":
        codes = _committee_codes(last)
        if codes:
            return Phase(key="first_reading_committee", committees=codes)
        return Phase(key="first_reading_sitting")
    if kind == "Start":
        return Phase(key="first_reading")
    return None


def is_over(bill: Bill, *, today: dt.date) -> bool:
    """True when the road has ended: nothing is left that a reader could act on.

    The Sejm's `closureDate` does not say this on its own — it is set at the third reading,
    while the Senate (30 days), the President (21) and Dziennik Ustaw are still ahead: druk
    2799 is closed on 2026-09-04, `passed`, with no act yet. What ends the road is the act
    *applying*, a rejection or a withdrawal, a project closed on RCL, a plan taken off the
    wykaz, a lapsed term — and the stages are what tell those apart, so a Sejm bill whose
    stages were never read is not over but unknown.

    An act in Dziennik Ustaw does not end it either: its vacatio legis runs for weeks or months
    (druk 2699: promulgated 2026-08-18, in force 2026-11-19), and that is the span in which a
    reader has a known date to prepare for. Deciding that needs the act, so a bill whose ELI has
    not been fetched yet is taken as over: callers that can fetch it (discovery) do.
    """
    if bill.act is not None:
        return next_phase(bill, today=today) is None
    if bill.summary.eli is not None:
        return True
    if bill.has_process and not bill.stages:
        return False
    return next_phase(bill, today=today) is None


def _wykaz_phase(bill: Bill) -> Phase | None:
    """A bill the government has only announced: waiting for its project, which RCL publishes."""
    entry = bill.wykaz
    assert entry is not None
    if entry.is_withdrawn:
        return None  # taken off the plan
    if entry.is_adopted:
        # "Zrealizowany": the Council of Ministers has adopted the project, so RCL is behind it.
        return Phase(key="wykaz_adopted")
    if entry.rcl_project_id is not None:
        return Phase(key="wykaz_to_rcl")
    return Phase(key="wykaz")


def _rcl_phase(bill: Bill, today: dt.date) -> Phase | None:
    """The government path: consultations and opinions, the committees of the Council of
    Ministers, the Council, the hand-over to the Sejm (then the print number)."""
    project = bill.rcl
    assert project is not None
    if project.is_over:
        return None  # closed on RCL without reaching the Sejm
    if project.sent_to_sejm:
        return Phase(key="rcl_to_sejm")
    window = bill.consultation
    if window is not None and window.is_open(today):
        return Phase(key="rcl_consultation", date=window.end)
    current = project.current_stage
    group = current.group if current else "opinions"
    return Phase(key=f"rcl_{group}")


def _days_after(start: dt.date | None, days: int) -> dt.date | None:
    return start + dt.timedelta(days=days) if start is not None else None


def _committee_codes(stage: Stage) -> tuple[str, ...]:
    """Committees a stage refers the bill to ("Sejm" is a reading at a sitting, no committee)."""
    return tuple(
        c.committee_code
        for c in stage.children
        if c.stage_type == "Referral" and c.committee_code and c.committee_code != "Sejm"
    )


def _latest_committees(top: list[Stage]) -> tuple[str, ...]:
    for stage in reversed(top):
        codes = _committee_codes(stage)
        if codes:
            return codes
    return ()


class Publication(BaseModel):
    """One Telegram post (or the decision not to send one), written before sending."""

    id: int | None = None
    term: int
    number: str
    attempts: int = 0
    kind: PublicationKind
    status: PublicationStatus
    channel_id: str
    ref: str | None = None  # distinguishes posts of one kind that recur per bill (agenda: sitting)
    message_id: int | None = None
    document_message_ids: list[int] = Field(default_factory=list)
    status_change_id: int | None = None
    created_at: dt.datetime
    sent_at: dt.datetime | None = None
    error: str | None = None
    rendered_sha256: str | None = None  # digest of the card text as last sent (new_bill)


class StatusChange(BaseModel):
    """A detected change worth one update post; unique per (bill, new_fingerprint)."""

    id: int | None = None
    term: int
    number: str
    old_fingerprint: str | None
    new_fingerprint: str
    new_stages: list[Stage]
    closure_detected: bool = False
    passed: bool | None = None
    content_changed: bool = False
    withdrawn: bool = False  # pre-print bill withdrawn before getting a print number
    discontinued: bool = False  # the term ended before the Sejm finished with the bill
    # What the amendments announced by this change do (Senate resolution, "-A" report), when
    # their document could be read and summarised.
    amendments: AmendmentsRecord | None = None
    detected_at: dt.datetime
