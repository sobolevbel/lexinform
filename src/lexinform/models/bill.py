"""The aggregate the services work on: a bill with its status, stages, analysis, submission,
act and authors, plus the two bookkeeping rows (publications and detected status changes)."""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lexinform.models.analysis import AnalysisRecord
from lexinform.models.enums import BillStatus, PublicationKind, PublicationStatus
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
)


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
    linked_number: str | None = None  # RPW <-> print number once the print is assigned
    act: ActInfo | None = None  # the published act, once it appears in Dziennik Ustaw
    authors: BillAuthors | None = None  # signatories of a deputies' bill, by club
    agenda: tuple[AgendaItem, ...] = ()  # upcoming sittings that name the bill, soonest first
    rcl: RclProject | None = None  # the RCL project of a government bill followed before the Sejm
    # Set when the Sejm term ended with the bill unfinished (zasada dyskontynuacji): nothing
    # more will happen to it under this number, so it is not tracked or analysed any more.
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
    def has_process(self) -> bool:
        return self.summary.has_process

    @property
    def last_stage(self) -> Stage | None:
        flat = flatten_stages(self.stages)
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


_PRESIDENT_NEXT = {"ToPresident", "SenatePositionConsideration"}


def next_phase(bill: Bill, *, today: dt.date) -> Phase | None:
    """The next step of the process, derived from the top-level stages, the submission and the
    act; None when the process is over (in force, rejected, withdrawn) or unknown.

    Keys: rcl_consultation, rcl_opinions, rcl_committees, rcl_council, rcl_to_sejm (government
    projects before the Sejm), pre_print, pre_print_consultation, first_reading,
    first_reading_committee, first_reading_sitting, committee_work, second_reading,
    third_reading, senate, senate_amendments, president, publication, in_force,
    in_force_unknown, veto, tribunal.
    """
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
    top = list(bill.stages)
    last = top[-1]
    kind = last.stage_type
    if kind == "End":
        return Phase(key="publication") if summary.passed else None
    if kind == "PresidentSignature":
        return Phase(key="publication")
    if kind == "Veto":
        return Phase(key="veto")
    if kind == "PresidentToTribunal":
        return Phase(key="tribunal")
    if kind in _PRESIDENT_NEXT:
        return Phase(key="president")
    if kind == "SenatePosition":
        if "nie wniósł" in (last.position or "").lower():
            return Phase(key="president")
        return Phase(key="senate_amendments", committees=_committee_codes(last))
    if any(st.stage_type == "SenatePosition" for st in top):
        return Phase(key="senate_amendments", committees=_latest_committees(top))
    if kind == "SejmReading":
        name = last.stage_name.lower()
        if "iii czytanie" in name:
            decided = (last.decision or "").lower()
            if decided.startswith("uchwal") or summary.passed:
                return Phase(key="senate")
            return None if decided else Phase(key="third_reading")
        if "ii czytanie" in name:
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


def _rcl_phase(bill: Bill, today: dt.date) -> Phase | None:
    """The government path: consultations and opinions, the committees of the Council of
    Ministers, the Council, the hand-over to the Sejm (then the print number)."""
    project = bill.rcl
    assert project is not None
    if project.sent_to_sejm:
        return Phase(key="rcl_to_sejm")
    if not project.is_open:
        return None  # closed on RCL without reaching the Sejm
    window = bill.consultation
    if window is not None and window.is_open(today):
        return Phase(key="rcl_consultation", date=window.end)
    current = project.current_stage
    group = current.group if current else "opinions"
    return Phase(key=f"rcl_{group}")


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
    detected_at: dt.datetime
