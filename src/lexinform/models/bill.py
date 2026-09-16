"""The aggregate the services work on: a bill with its status, stages, analysis, submission,
act and authors, plus the two bookkeeping rows (publications and detected status changes).

What the stage tree says about where the bill *stands* lives here too, because `Bill` itself asks
it (`process_stages`, `veto_stood`, `end_names_veto_sustained`). What it says about where the bill
is *going* is `models/phases.py`, which reads this module and is read by nothing in it.
"""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lexinform.models.analysis import (
    AmendmentsRecord,
    AnalysisRecord,
    JointRecord,
    SupplementRecord,
)
from lexinform.models.enums import BillStatus, PublicationKind, PublicationStatus, VetoOutcome
from lexinform.models.rcl import RclProject
from lexinform.models.sejm import (
    ActInfo,
    AgendaItem,
    BillAuthors,
    BillSubmission,
    ProcessSummary,
    Stage,
    TextDocument,
)
from lexinform.models.wykaz import WykazEntry


class ConsultationWindow(BaseModel):
    """The public consultation of a bill, whoever runs it.

    The Sejm consults deputies', Senate, presidential and citizens' bills: `form_url` is the
    project page, which carries the text and links the survey at `survey_url`, where the opinion
    is actually submitted. The government consults its own on RCL, by e-mail to the ministry
    named in the consultation letter (`email`, `letter_url`).
    """

    model_config = ConfigDict(frozen=True)

    source: Literal["sejm", "rcl"]
    start: dt.date | None = None
    end: dt.date | None = None
    form_url: str | None = None
    survey_url: str | None = None
    email: str | None = None
    letter_url: str | None = None
    results_published: bool = False

    def is_open(self, today: dt.date) -> bool:
        return self.end is not None and self.end >= today


class Bill(BaseModel):
    """One row of the `bills` table: everything we know and decided about a bill.

    A row keeps the numbers of its other lives: `linked_number` is the print an RPW entry or an
    RCL project became and the other way round, `linked_wykaz_number` the wykaz number a print
    carries on so the card keeps its tag. `discontinued_at` is stamped when a term ended with the
    bill unfinished (zasada dyskontynuacji): nothing more can happen to it under this number.
    """

    summary: ProcessSummary
    status: BillStatus
    prefilter_hits: list[str] = Field(default_factory=list)
    stages: tuple[Stage, ...] = ()
    stages_fingerprint: str | None = None
    observed_closure_date: dt.date | None = None
    analysis: AnalysisRecord | None = None
    analysis_attempts: int = 0
    last_error: str | None = None
    submission: BillSubmission | None = None
    linked_number: str | None = None
    linked_wykaz_number: str | None = None
    act: ActInfo | None = None
    authors: BillAuthors | None = None
    agenda: tuple[AgendaItem, ...] = ()
    rcl: RclProject | None = None
    wykaz: WykazEntry | None = None
    seen_supplements: tuple[str, ...] | None = None
    """The documents filed to the print that the channel already knows about. None means they
    were never recorded: the next run takes what the print has now as the starting point and
    tells none of it, the way a first sight of the stages seeds the fingerprint silently."""
    joint: JointRecord | None = None
    """How this print differs from the others considered jointly with it, when the channel
    answers for it under their card rather than giving it one of its own."""
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
        """Where the bill stands; `process_stages` names the nodes that do not answer that.

        The *top-level* stage, never a child of it: what the Sejm did is the parent, its children
        the paperwork that followed. The newest node of a flattened tree is one of those, so druk
        2842's card said «направлен в комиссию ENM» over a bill whose news was a veto.
        """
        top = process_stages(self.stages)
        return top[-1] if top else None

    @property
    def last_stage_detail(self) -> Stage | None:
        """The newest child of `last_stage`, when it adds something the parent does not say:
        which committee the bill went to, how the Sejm voted.

        A referral to `Sejm` names no committee — it is the API's way of saying the reading is a
        plenary one, which "направлен на I чтение" and "what comes next" both say already.
        """
        last = self.last_stage
        if last is None or not last.children:
            return None
        detail = last.children[-1]
        if detail.stage_type == "Referral" and detail.committee_code == PLENARY_COMMITTEE_CODE:
            return None
        return detail

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
            survey_url=sub.survey_url,
            results_published=sub.consultation_results,
        )


class LocatedText(BaseModel):
    """What a text source found for a bill: fresh metadata, the stage tree (when the source has
    one) and the document to read. Any part may be missing."""

    model_config = ConfigDict(frozen=True)

    summary: ProcessSummary | None = None
    stages: tuple[Stage, ...] | None = None
    document: TextDocument | None = None


ASIDE_STAGE_TYPES = frozenset({"GovermentPosition", "Opinion"})
PLENARY_COMMITTEE_CODE = "Sejm"
"""The `committeeCode` the API puts on a referral to a reading at a sitting of the whole
Sejm: not a committee, and nothing a reader can write to."""


def end_names_veto_sustained(stage: Stage) -> bool:
    """The `End` node renamed to "nie uchwalona ponownie po wecie Prezydenta": the node itself
    says the road is over, so it is the one `End` that is not dropped as bookkeeping."""
    return stage.stage_type == "End" and "nie uchwalona ponownie" in stage.stage_name.lower()


def veto_stood(stages: tuple[Stage, ...]) -> bool:
    """The Sejm voted on the President's veto and did not reach the 3/5 majority.

    The Sejm's vote settles it, not the `End` node: of the fifteen processes of terms 8-10 whose
    `PresidentMotionConsideration` decided "nie uchwalona ponownie", eight keep `End` =
    "Uchwalono" and `passed` = true, exactly as if the law had survived.
    """
    return veto_outcome(stages) is VetoOutcome.SUSTAINED


def veto_decision(stage: Stage) -> VetoOutcome:
    """A motion's presence is not evidence that the Sejm has voted on it."""
    decision = (stage.decision or "").strip().lower()
    if "nie uchwalon" in decision:
        return VetoOutcome.SUSTAINED
    if decision.startswith("uchwalono ponownie"):
        return VetoOutcome.OVERRIDDEN
    return VetoOutcome.PENDING


def veto_outcome(stages: tuple[Stage, ...]) -> VetoOutcome | None:
    outcome = None
    for stage in stages:
        if stage.stage_type == "Veto":
            outcome = VetoOutcome.PENDING
        elif stage.stage_type == "PresidentMotionConsideration":
            outcome = veto_decision(stage)
        elif end_names_veto_sustained(stage):
            outcome = VetoOutcome.SUSTAINED
    return outcome


def process_stages(stages: tuple[Stage, ...]) -> list[Stage]:
    """The top-level stages that say where the bill stands.

    Two kinds of node are dropped. `ASIDE_STAGE_TYPES` arrive beside the process without moving
    it and land last in the tree while the bill sits in committee. And `End` ("Uchwalono") is
    appended at the third reading and kept last while the Senate, the President and Dziennik
    Ustaw are all still ahead, so taking it for the current step marks the reader's last two
    windows as passed. The `End` of a bill a veto killed says something of its own and stays,
    decided by the node's own wording and not by `veto_stood`.
    """
    top = [st for st in stages if st.stage_type not in ASIDE_STAGE_TYPES]
    if top and top[-1].stage_type == "End" and not end_names_veto_sustained(top[-1]):
        top.pop()
    return top


class Publication(BaseModel):
    """One Telegram post (or the decision not to send one), written before sending."""

    id: int | None = None
    term: int
    number: str
    attempts: int = 0
    kind: PublicationKind
    status: PublicationStatus
    channel_id: str
    ref: str | None = None
    """Distinguishes posts of one kind that recur per bill (agenda: the sitting)."""
    message_id: int | None = None
    document_message_ids: list[int] = Field(default_factory=list)
    status_change_id: int | None = None
    created_at: dt.datetime
    sent_at: dt.datetime | None = None
    error: str | None = None
    rendered_sha256: str | None = None
    """Digest of the card text as last sent (`new_bill`)."""


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
    withdrawn: bool = False
    """A pre-print bill withdrawn before getting a print number."""
    discontinued: bool = False
    """The term ended before the Sejm finished with the bill."""
    consultation_opened: bool = False
    """The project's public consultation opened with this change (RCL). Not stored: a retried
    post takes its wording from the row, and by then the window is on the bill either way — what
    this decides is only the header of the post that announces it."""
    amendments: AmendmentsRecord | None = None
    """What the amendments announced by this change do (Senate resolution, "-A" report), when
    their document could be read and summarised."""
    supplements: list[SupplementRecord] = Field(default_factory=list)
    """The documents filed to the print since the last check, digested: the government's
    position, the OSR, an opinion that raised something."""
    detected_at: dt.datetime
