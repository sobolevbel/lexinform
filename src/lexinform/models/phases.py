"""What the legislative process holds next for a bill, and when its road has ended.

`next_phase` reads the top-level stages, the submission and the act and answers with a `Phase`:
which step the bill is on, which committees have it, the constitutional term of the step where
there is one, and the day the bill reached it. `is_over` is the same question asked the other way
round — whether there is anything left for a reader to act on. The formatter dates the phase from
`bill.agenda` or from the deadline; nothing here does I/O and nothing here renders a word.

The road itself, its deadlines and the public's windows are in `docs/legislative-process.md`.
"""

import datetime as dt
import re

from pydantic import BaseModel, ConfigDict

from lexinform.keywords import KEYWORD_PATTERNS
from lexinform.models.bill import (
    PLENARY_COMMITTEE_CODE,
    Bill,
    ConsultationWindow,
    process_stages,
    veto_stood,
)
from lexinform.models.enums import ApplicantType
from lexinform.models.rcl import RclProject
from lexinform.models.sejm import (
    ActInfo,
    Stage,
    reading_adjourned,
    reading_decision,
    second_reading_sent_back,
    senate_moved_rejection,
)


class Phase(BaseModel):
    """What the legislative process holds next for a bill (see `next_phase`).

    `committees` names the committees the bill sits in, `date` the consultation end or the entry
    into force when the key has one, `since` the day the bill reached this step. `deadline` is
    the constitutional term of the step where there is one: the Senate has 30 days from receiving
    the act (art. 121), the President 21 (art. 122), 14 and 7 for an urgent bill (art. 123),
    counted from the dates the Sejm API shows. Two bills get neither, because the term is not the
    one we model (`_senate_days`): the budget, where art. 223 gives the Senate twenty days, and a
    constitutional amendment, where art. 235 gives sixty.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    committees: tuple[str, ...] = ()
    date: dt.date | None = None
    deadline: dt.date | None = None
    deadline_exact: bool = False
    """Whether `deadline` is counted from the step that actually starts it. `ToPresident` is the
    hand-over itself, so the twenty-one days of art. 122 run from a date the API gives; the
    Senate's thirty are counted from the third reading, days before the Marszałek sends the act.
    A message that apologises for being early must not do it over an exact date."""
    since: dt.date | None = None


_PRESIDENT_NEXT = {"ToPresident", "SenatePositionConsideration"}


_SENATE_SPECIAL_TERM = re.compile(r"ustaw\w*\s+bud[żz]etow|zmianie\s+Konstytucji", re.IGNORECASE)
"""Bills whose Senate term is not the thirty days of art. 121: art. 223 gives twenty for the
budget and art. 235 sixty for a constitutional amendment. Neither is in reach of this channel's
keywords, but a date computed at thirty days would be wrong on the reader's screen, and the
silence rule of `_after_senate_silence` would then move the bill on while the Senate still had
it."""

SENATE_DAYS, SENATE_DAYS_URGENT = 30, 14
PRESIDENT_DAYS, PRESIDENT_DAYS_URGENT = 21, 7
PRESIDENT_DAYS_AFTER_VETO = 7
"""Art. 122 ust. 5: once the Sejm has overridden the veto the President signs within seven days,
whether or not the bill was pilny, and may no longer go to the Tribunal."""
DEADLINE_GRACE_DAYS = 7


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
GOVERNMENT_STEPS = frozenset({"wykaz", "rcl"})
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
    "president_after_veto": "president",
    "president_after_senate_silence": "president",
    "veto": "president",
    "tribunal": "president",
    "publication": "journal",
    "in_force": "in_force",
    "in_force_unknown": "in_force",
}
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
    "president_after_veto": 30,
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


def deadline_overdue(phase: Phase, today: dt.date) -> bool:
    """The step's constitutional term has run out, grace included.

    The Senate's thirty days and the President's twenty-one are counted from the stage before the
    hand-over unless `deadline_exact` says otherwise, so they fall a few days early; that is what
    `DEADLINE_GRACE_DAYS` covers. Past it the date is no longer something to promise a reader.
    """
    return phase.deadline is not None and (today - phase.deadline).days > DEADLINE_GRACE_DAYS


def stalled_days(phase: Phase, today: dt.date) -> int | None:
    """How long the bill has been on this step, once that outlived what the step usually takes;
    None while the step is still running on schedule or its start is unknown.

    `PHASE_PATIENCE` is deliberately two to three times the upper end of the durations the card
    quotes (90 days against "2–6 недель" for a first reading): a step that runs a fortnight over
    its average is still ordinary, and "без движения уже 7 нед." said of it would cry wolf.

    A phase that carries a `date` of its own is never stalled, whatever its age: the date is a
    certainty the reader can diarise — the day an act enters into force, the end of a
    consultation window — and a vacatio legis of a year is common in the acts we follow
    (`docs/legislative-process.md` §7). «вступление в силу 01.07.2027 · без движения уже 7 мес.»
    said of a law that is published, final and dated inverts the message.
    """
    since = phase.since
    if since is None or phase.date is not None:
        return None
    waited = (today - since).days
    patience = PHASE_PATIENCE.get(phase.key, DEFAULT_PATIENCE_DAYS)
    return waited if waited > patience else None


COMMITTEE_PHASES = frozenset(
    {
        "first_reading_committee",
        "committee_work",
        "second_reading_committee",
        "senate_amendments",
        "senate_rejection",
        "veto",
    }
)
"""Phases where a committee has the bill and takes opinions. A veto is one of them: the Sejm
refers the President's motion to the committee that carried the bill (art. 122 ust. 5), and
until the vote a reader can still write to it."""
SITTING_PHASES = frozenset(
    {
        "first_reading_sitting",
        "second_reading",
        "third_reading",
        "senate_amendments",
        "senate_rejection",
        "veto",
    }
)
_UKRAINE = next(p.regex for p in KEYWORD_PATTERNS if p.name == "obywatele_ukrainy")


def government_path(bill: Bill) -> bool:
    """Government bills start on RCL and, before that, on the wykaz: `PATH_STEPS` shows the
    `GOVERNMENT_STEPS` only for them."""
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
    """Whether an opinion can still be sent.

    A window with an end date answers for itself. One without is the RCL case where the letter
    carries an electronic time stamp instead of a date and no relative term either
    (`rcl_letters.parse_letter`): the project's own timeline is then what says the consultation is
    running, and `_rcl_phase` reads it. Without this the card of such a project invited an e-mail
    to the ministry in its action line and, three lines up, put the consultation stage behind it.
    """
    window = bill.consultation
    if window is None:
        return False
    if window.is_open(today):
        return True
    phase = next_phase(bill, today=today)
    return phase is not None and phase.key == "rcl_consultation"


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
    if phase is None:
        return None
    phase = _after_senate_silence(bill, phase, today)
    return phase.model_copy(update={"since": _phase_started(bill)})


def _after_senate_silence(bill: Bill, phase: Phase, today: dt.date) -> Phase:
    """Art. 121 ust. 2: thirty days gone with no uchwała from the Senate and the act counts as
    adopted in the wording the Sejm passed, so it is with the President.

    The term is *zawity* — the Senate can neither extend nor suspend it — which is why the step
    has really moved on and not merely gone quiet. Annotating the Senate step instead made one
    line say both things at once: «рассмотрение в Сенате (до 30 дней) · 30 дней Сената истекли:
    закон считается принятым без поправок» (product review, 2026-09-14).

    The derived phase carries no deadline of its own: the President's twenty-one days run from a
    receipt the API does not date, and a guess stacked on a guess is not worth a reminder. This is
    the one place the bot names a step the Sejm has not published, so the wording says so — and if
    the Senate did act and the listing is merely behind, its stage arrives and this unwinds on the
    next run.
    """
    if phase.key != "senate" or not deadline_overdue(phase, today):
        return phase
    return Phase(key="president_after_senate_silence")


def _phase_started(bill: Bill) -> dt.date | None:
    """When the bill reached the step it is on, so that "what comes next" can say how long it has
    been waiting instead of quoting an average that ran out long ago.

    A stage the API left undated started on an unknown day, not on the day the bill was submitted:
    reading it as the latter would age the step by the whole life of the bill. RCL leaves
    "rozpoczęcie" empty for most stages, so the stage's last modification stands in — it is what
    the page shows, and what stops moving when a project stalls. That fallback used to sit behind
    `if last is not None`, which an RCL project never fails: seven of the ten projects in the
    state dump of 2026-09-13 had an undated stage and therefore no start at all, so «без движения
    уже N мес.» could not fire on the one source where a project really does stand for a year.
    """
    last = bill.last_stage
    if last is not None and last.date is not None:
        return last.date
    if bill.rcl is not None:
        current = bill.rcl.current_stage
        return current.modified if current is not None else None
    if last is not None:
        return None
    if bill.wykaz is not None:
        return bill.wykaz.published_at.date()
    submission = bill.submission
    return submission.date_of_receipt if submission is not None else None


def _phase_of(bill: Bill, today: dt.date) -> Phase | None:
    """The source the bill belongs to decides which road it is on; a lapsed term ends every one
    of them, because a new Sejm must receive the bill again.

    An ELI on the process is the Sejm saying the act is out, and it is read before the stages
    even when the act itself has not been fetched: `is_over` has always taken it for the end of
    the road, and the two must not disagree. They did, and the reader was told the opposite of
    the truth — druk 2172 was adopted from an RCL project on 2026-09-14 carrying `DU/2026/203`,
    its act was never fetched, and the stage road ran out at `PresidentSignature`, so the card
    said «дальше: публикация в Dziennik Ustaw · без движения уже 6 мес.» and «пока ничего —
    ждём публикации» over a law that had been in force since 2026-03-05.
    """
    if bill.act is not None:
        return _act_phase(bill.act, today)
    if bill.summary.eli is not None:
        return Phase(key="in_force_unknown")
    if bill.discontinued_at is not None:
        return None
    if bill.wykaz is not None:
        return _wykaz_phase(bill)
    if bill.rcl is not None:
        return _rcl_phase(bill, today)
    if bill.is_pre_print or not bill.stages:
        return _pre_print_phase(bill, today)
    return _sejm_phase(bill, today)


def _act_phase(act: ActInfo, today: dt.date) -> Phase | None:
    """An act in Dziennik Ustaw is still ahead of the reader until its vacatio legis runs out."""
    if act.entry_into_force is None:
        return Phase(key="in_force_unknown")
    if act.entry_into_force > today:
        return Phase(key="in_force", date=act.entry_into_force)
    return None


def _pre_print_phase(bill: Bill, today: dt.date) -> Phase | None:
    """A bill submitted but not yet numbered: its own consultation, or the wait for the print.
    A closure date here means it was withdrawn before ever getting one."""
    if bill.summary.closure_date is not None:
        return None
    window = bill.consultation
    if window is not None and window.is_open(today):
        return Phase(key="pre_print_consultation", date=window.end)
    return Phase(key="pre_print")


def _sejm_phase(bill: Bill, today: dt.date) -> Phase | None:
    """The road through the Sejm, read from the stage the process stands on."""
    summary = bill.summary
    if summary.closure_date is not None and summary.passed is False:
        return None
    if veto_stood(bill.stages):
        return None
    top = process_stages(bill.stages)
    if not top:
        return Phase(key="first_reading")
    return _phase_after(
        top[-1],
        top,
        urgent=is_urgent(bill),
        passed=summary.passed,
        senate_days=_senate_days(bill),
    )


def _senate_days(bill: Bill) -> int | None:
    """How long the Senate has, or None when the Constitution gives it a term we do not model
    (`_SENATE_SPECIAL_TERM`). Art. 121 ust. 2 is thirty days, art. 123 ust. 3 fourteen for a bill
    the government declared pilny."""
    if _SENATE_SPECIAL_TERM.search(bill.summary.title):
        return None
    return SENATE_DAYS_URGENT if is_urgent(bill) else SENATE_DAYS


_PHASE_AFTER_STAGE_TYPE = {
    "PresidentSignature": "publication",
    "PresidentToTribunal": "tribunal",
    "Start": "first_reading",
}
ANSWERED_IN_COMMITTEE = ("Veto", "SenatePosition")
"""What a `CommitteeWork` after them is working on: the President's motion, the Senate's
resolution. The newest of them decides, because a bill can carry both."""


def _phase_after(
    last: Stage, top: list[Stage], *, urgent: bool, passed: bool | None, senate_days: int | None
) -> Phase | None:
    """The step that follows the stage the process stands on. `_PHASE_AFTER_STAGE_TYPE` holds the
    stages whose successor needs nothing but the stage's own type; the rest read the stage's
    decision, its committees or the tree before it."""
    kind = last.stage_type
    key = _PHASE_AFTER_STAGE_TYPE.get(kind)
    if key is not None:
        return Phase(key=key)
    if kind == "Veto":
        # The Sejm refers the President's motion to a committee before voting on it, and that
        # referral is a child of the `Veto` stage itself.
        return Phase(key="veto", committees=_committee_codes(last) or _latest_committees(top))
    if kind == "PresidentMotionConsideration":
        return _phase_after_veto_vote(last)
    if kind == "ConstitutionalTribunalRuling":
        # The Tribunal has answered and the road stops here whichever way it went: what the
        # President does with an act found unconstitutional in part is a fresh Sejm process.
        return None
    if kind == "SenatePositionConsideration" and _sejm_let_the_senate_win(last):
        return None
    if kind in _PRESIDENT_NEXT:
        days = PRESIDENT_DAYS_URGENT if urgent else PRESIDENT_DAYS
        # `ToPresident` is the hand-over itself, so its date is the day art. 122 starts counting;
        # after the Sejm's vote on the Senate's amendments the hand-over is still days away.
        return Phase(
            key="president",
            deadline=_days_after(last.date, days),
            deadline_exact=kind == "ToPresident",
        )
    if kind == "SenatePosition":
        return _phase_after_senate(last)
    if kind == "SejmReading":
        return _phase_after_reading(last, top, passed=passed, senate_days=senate_days)
    if kind == "CommitteeWork":
        return _phase_after_committee_work(last, top)
    if kind in ("Reading", "PublicHearing"):
        return Phase(key="committee_work", committees=_latest_committees(top))
    if kind == "ReadingReferral":
        return _phase_after_referral(last)
    return None


def _phase_after_veto_vote(last: Stage) -> Phase | None:
    """The Sejm has voted on the President's motion (`PresidentMotionConsideration`).

    A veto that stood ends the road, and the `End` node says so in its own words, so `veto_stood`
    has already answered before this is reached; what is left is the override, after which
    art. 122 ust. 5 gives the President seven days to sign and no way back to the Tribunal.
    """
    if "nie uchwalon" in (last.decision or "").lower():
        return None
    # Art. 122 ust. 5 counts the seven days from this vote, which is the stage's own date.
    return Phase(
        key="president_after_veto",
        deadline=_days_after(last.date, PRESIDENT_DAYS_AFTER_VETO),
        deadline_exact=True,
    )


def _sejm_let_the_senate_win(stage: Stage) -> bool:
    """Art. 121 ust. 3: the Senate moved rejection and the Sejm did not throw that motion out.

    "przyjęto uchwałę Senatu" against "odrzucono uchwałę Senatu", which is the override. The
    Senate's *amendments* are decided in words of their own ("przyjęto poprawki"), so a decision
    that names the uchwała is one where the whole act was at stake (druk 2898 of term 9, whose
    `End` reads "odrzucono na wniosek Senatu"). Without this the road ran on to «Президент
    подписывает» for a law the Sejm had just let die.
    """
    decided = (stage.decision or "").lower()
    return decided.startswith("przyjęto") and "uchwałę senatu" in decided


def _phase_after_senate(last: Stage) -> Phase:
    """Amendments and a rejection are two different stakes: art. 121 ust. 3 lets a rejection
    stand unless the Sejm throws it out by an absolute majority."""
    position = (last.position or "").lower()
    if "nie wniósł" in position:
        return Phase(key="president")
    key = "senate_rejection" if senate_moved_rejection(last) else "senate_amendments"
    return Phase(key=key, committees=_committee_codes(last))


def _phase_after_reading(
    last: Stage, top: list[Stage], *, passed: bool | None, senate_days: int | None
) -> Phase | None:
    name = last.stage_name.lower()
    if "iii czytanie" in name:
        decided = reading_decision(last)
        if decided.startswith("uchwal") or passed:
            deadline = _days_after(last.date, senate_days) if senate_days is not None else None
            return Phase(key="senate", deadline=deadline)
        # A reading the Sejm broke off decided nothing and is resumed: druk 2985 of term 8 stood
        # at "nie dokończone III czytanie" with the process still open, and reading that as a
        # decision made `is_over` true — no card, and a followed bill's card frozen for good.
        if not decided or reading_adjourned(last):
            return Phase(key="third_reading")
        return None
    if "ii czytanie" in name:
        if second_reading_sent_back(last):
            return Phase(key="second_reading_committee", committees=_latest_committees(top))
        return Phase(key="third_reading")
    return Phase(key="committee_work", committees=_latest_committees(top))


def _phase_after_committee_work(last: Stage, top: list[Stage]) -> Phase:
    """What the committee is working on decides what follows it.

    "Praca w komisjach nad stanowiskiem Senatu" and "…nad wnioskiem Prezydenta" are the same
    stage type as the work after the first reading, and only the tree before them tells the
    three apart (`ANSWERED_IN_COMMITTEE`). Otherwise the committee's own report decides, by its
    print number and not by what it proposes: an "-A" report answers the amendments made at the
    second reading, so the next vote is the third; any other report is the work after the first
    reading and goes to the second, whether the committee proposes the attached text, no
    amendments at all, or throwing the bill out.
    """
    pending = next(
        (st for st in reversed(top[:-1]) if st.stage_type in ANSWERED_IN_COMMITTEE), None
    )
    if pending is not None:
        committees = _latest_committees(top)
        if pending.stage_type == "Veto":
            return Phase(key="veto", committees=committees)
        return _phase_after_senate(pending).model_copy(update={"committees": committees})
    reports = [c for c in last.children if c.stage_type == "CommitteeReport"]
    if any(r.is_additional_report for r in reports):
        return Phase(key="third_reading")
    if reports:
        return Phase(key="second_reading")
    return Phase(key="committee_work", committees=_latest_committees(top))


def _phase_after_referral(last: Stage) -> Phase:
    codes = _committee_codes(last)
    if codes:
        return Phase(key="first_reading_committee", committees=codes)
    return Phase(key="first_reading_sitting")


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
    """A bill the government has only announced: waiting for its project, which RCL publishes.

    An entry taken off the plan has no road left; "Zrealizowany" means the Council of Ministers
    has adopted the project, so RCL is already behind it.
    """
    entry = bill.wykaz
    assert entry is not None
    if entry.is_withdrawn:
        return None
    if entry.is_adopted:
        return Phase(key="wykaz_adopted")
    if entry.rcl_project_id is not None:
        return Phase(key="wykaz_to_rcl")
    return Phase(key="wykaz")


def _rcl_phase(bill: Bill, today: dt.date) -> Phase | None:
    """The government path: consultations and opinions, the committees of the Council of
    Ministers, the Council, the hand-over to the Sejm (then the print number). A project that is
    over was closed on RCL without ever reaching the Sejm."""
    project = bill.rcl
    assert project is not None
    if project.is_over:
        return None
    if project.sent_to_sejm:
        return Phase(key="rcl_to_sejm")
    window = bill.consultation
    if window is not None and _consulting(project, window, today):
        return Phase(key="rcl_consultation", date=window.end)
    current = project.current_stage
    group = current.group if current else "opinions"
    return Phase(key=f"rcl_{group}")


def _consulting(project: RclProject, window: ConsultationWindow, today: dt.date) -> bool:
    """The public consultation is running. The deadline says so when the letter gave one; when it
    did not, the timeline does — the stage the project is on is the consultation itself."""
    if window.end is not None:
        return window.is_open(today)
    current = project.current_stage
    return current is not None and current.is_consultation


def _days_after(start: dt.date | None, days: int) -> dt.date | None:
    return start + dt.timedelta(days=days) if start is not None else None


def _committee_codes(stage: Stage) -> tuple[str, ...]:
    """Committees a stage refers the bill to; `PLENARY_COMMITTEE_CODE` is not one."""
    return tuple(
        c.committee_code
        for c in stage.children
        if c.stage_type == "Referral"
        and c.committee_code
        and c.committee_code != PLENARY_COMMITTEE_CODE
    )


def _latest_committees(top: list[Stage]) -> tuple[str, ...]:
    for stage in reversed(top):
        codes = _committee_codes(stage)
        if codes:
            return codes
    return ()
