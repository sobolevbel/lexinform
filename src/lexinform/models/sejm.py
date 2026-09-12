"""What the Sejm API tells us: processes, prints, stages, votes, submissions, acts, MPs, and
the pure helpers over them (URLs, stage fingerprints and diffs, the latest bill text)."""

from __future__ import annotations  # Stage and PrintInfo are trees: they refer to themselves

import datetime as dt
import hashlib
import json
from collections.abc import Iterable
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict

from lexinform.models.enums import (
    BILL_DOCUMENT_TYPE,
    PRE_PRINT_PREFIX,
    RCL_PREFIX,
    WYKAZ_PREFIX,
    WYKAZ_REGISTER_URL,
    ApplicantType,
    DocumentType,
    SourceKind,
)

NON_SEJM_PREFIXES = (PRE_PRINT_PREFIX, RCL_PREFIX, WYKAZ_PREFIX)


class ClubVotes(BaseModel):
    """How one parliamentary club voted; computed from the per-MP list of a voting."""

    model_config = ConfigDict(frozen=True)

    club: str
    yes: int = 0
    no: int = 0
    abstain: int = 0
    absent: int = 0


class Vote(BaseModel):
    """One MP's vote from GET /votings/{sitting}/{number}."""

    model_config = ConfigDict(frozen=True)

    mp: int
    club: str
    vote: str  # YES | NO | ABSTAIN | ABSENT | ...


class VotingSummary(BaseModel):
    """Result of a Sejm vote as embedded in a `Voting` stage (`clubs` is filled by us)."""

    model_config = ConfigDict(frozen=True)

    yes: int
    no: int
    abstain: int
    not_participating: int = 0
    sitting: int | None = None
    voting_number: int | None = None
    date: dt.datetime | None = None
    pdf_url: str | None = None
    clubs: tuple[ClubVotes, ...] = ()


class Mp(BaseModel):
    """A member of parliament from GET /MP (only what the author lookup needs)."""

    model_config = ConfigDict(frozen=True)

    id: int
    first_name: str
    last_name: str
    second_name: str | None = None
    accusative_name: str | None = None  # "Jana Kowalskiego": the form cover letters use
    club: str = "niez."

    @property
    def first_last_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def full_name(self) -> str:
        middle = f" {self.second_name}" if self.second_name else ""
        return f"{self.first_name}{middle} {self.last_name}"


class BillAuthors(BaseModel):
    """Who signed a bill, resolved to parliamentary clubs."""

    model_config = ConfigDict(frozen=True)

    representative: str | None = None
    representative_club: str | None = None
    clubs: tuple[tuple[str, int], ...] = ()  # (club, signatories) largest first
    signatories: int = 0
    unresolved: int = 0


class SejmTerm(BaseModel):
    """One item of GET /sejm/term: a term (kadencja) of the Sejm."""

    model_config = ConfigDict(frozen=True)

    num: int
    start: dt.date | None = None
    end: dt.date | None = None  # missing for the running term
    current: bool = False


def current_term(terms: Iterable[SejmTerm]) -> int | None:
    """The running term: the one the API flags `current`, else the highest number; None when
    the listing is empty."""
    listed = list(terms)
    for term in listed:
        if term.current:
            return term.num
    return max((term.num for term in listed), default=None)


class Committee(BaseModel):
    """A Sejm committee from GET /committees/{code}."""

    model_config = ConfigDict(frozen=True)

    term: int
    code: str
    name: str

    @property
    def web_url(self) -> str:
        return committee_web_url(self.term, self.code)


class Stage(BaseModel):
    """One node of the legislative process tree returned by /processes/{n}."""

    model_config = ConfigDict(frozen=True)

    stage_name: str
    stage_type: str
    date: dt.date | None = None
    print_number: str | None = None
    sitting_num: int | None = None
    decision: str | None = None
    committee_code: str | None = None
    report_file: str | None = None
    text_after3: str | None = None
    position: str | None = None  # SenatePosition: what the Senate did (not part of the fingerprint)
    proposal: str | None = None  # CommitteeReport: what the committee proposes
    sub_committee: bool = False  # CommitteeReport: a sub-committee report, not the final one
    # CommitteeReport: minority motions attached (voted at the 3rd reading); None when unknown
    minority_motions: int | None = None
    voting: VotingSummary | None = None  # Voting: results (not part of the fingerprint)
    # Referral: resolved by us from /committees (not fingerprinted)
    committee_name: str | None = None
    children: tuple[Stage, ...] = ()

    @property
    def carries_bill_text(self) -> bool:
        """True for committee reports whose PDF contains the (amended) bill text.

        Additional reports ("-A" prints) answering 2nd-reading amendments contain only tables of
        amendments; `proposal` says "załączony projekt ustawy" when the full text is attached.
        """
        if self.stage_type != "CommitteeReport" or not self.report_file or self.sub_committee:
            return False
        if self.proposal is not None:
            return "projekt" in self.proposal.lower()
        return not (self.print_number or "").upper().endswith("-A")


class BillSubmission(BaseModel):
    """An item of GET /bills: a submitted bill, possibly before it gets a print (druk) number.

    This is the earliest public trace of a bill and the only place that carries the public
    consultation dates, so it is what lets readers act before the Sejm even starts working.
    """

    model_config = ConfigDict(frozen=True)

    term: int
    number: str  # "RPW/29075/2026"
    title: str
    description: str | None = None
    applicant: ApplicantType = ApplicantType.UNKNOWN
    status: str = "ACTIVE"  # ACTIVE | WITHDRAWN | NOT_PROCEEDED | OBSOLETE | ADOPTED
    submission_type: str = "BILL"  # BILL | DRAFT_RESOLUTION | BILL_AMENDMENT | RESOLUTION_AMENDMENT
    date_of_receipt: dt.date
    print_number: str | None = None
    eu_related: bool = False
    public_consultation: bool = False
    consultation_start: dt.date | None = None
    consultation_end: dt.date | None = None
    consultation_results: bool = False
    withdrawn_date: dt.date | None = None

    @property
    def is_bill(self) -> bool:
        return self.submission_type == "BILL"

    @property
    def is_closed(self) -> bool:
        return self.status in ("WITHDRAWN", "NOT_PROCEEDED", "OBSOLETE")

    @property
    def pdf_url(self) -> str:
        return submission_pdf_url(self.term, self.number)

    @property
    def consultation_url(self) -> str | None:
        """The Sejm page of a consulted bill: the form for opinions and, later, the opinions."""
        if not self.public_consultation:
            return None
        return consultation_web_url(self.term, self.number)


class CommitteeSitting(BaseModel):
    """An item of GET /committees/{code}/sittings."""

    model_config = ConfigDict(frozen=True)

    code: str
    num: int
    date: dt.date
    start_time: dt.time | None = None  # wall clock in Warsaw, as the API gives it
    room: str | None = None
    status: str = "PLANNED"  # PLANNED | FINISHED | ...
    agenda: str = ""  # HTML fragment, see `lexinform.agenda`
    video_url: str | None = None


class SejmSitting(BaseModel):
    """An item of GET /proceedings; `agenda` comes from GET /proceedings/{number} only."""

    model_config = ConfigDict(frozen=True)

    number: int  # 0 for a sitting that is only planned (no agenda yet)
    dates: tuple[dt.date, ...]
    agenda: str = ""  # HTML fragment

    @property
    def first_date(self) -> dt.date | None:
        return min(self.dates) if self.dates else None

    @property
    def last_date(self) -> dt.date | None:
        return max(self.dates) if self.dates else None


AgendaKind = Literal["committee", "sejm"]


class AgendaItem(BaseModel):
    """A future sitting whose agenda names the bill: the dated "what happens next"."""

    model_config = ConfigDict(frozen=True)

    kind: AgendaKind
    ref: str  # dedupe key of the post: "ASW/136/2026-09-17" or "sejm/65/2026-09-15"
    date: dt.date
    end_date: dt.date | None = None  # Sejm sittings span several days
    start_time: dt.time | None = None
    committee_code: str | None = None
    committee_name: str | None = None
    sitting_number: int | None = None
    room: str | None = None
    text: str = ""  # the agenda item, plain text
    video_url: str | None = None

    @property
    def last_date(self) -> dt.date:
        """The day the sitting ends: a Sejm sitting spans several, a committee's one."""
        return self.end_date or self.date

    @property
    def sitting_key(self) -> str:
        """The sitting itself, without the date `ref` carries: what tells a moved sitting from
        a different one."""
        return self.ref.rsplit("/", 1)[0]


class ActInfo(BaseModel):
    """The published act, from the ELI API (GET /eli/acts/{publisher}/{year}/{pos})."""

    model_config = ConfigDict(frozen=True)

    eli: str  # "DU/2026/1099"
    display_address: str  # "Dz.U. 2026 poz. 1099"
    title: str
    act_date: dt.date | None = None  # `announcementDate`: the date in the act's title
    promulgation_date: dt.date | None = None  # publication in Dziennik Ustaw
    entry_into_force: dt.date | None = None  # single date; staged provisions are not modelled
    in_force: str | None = None  # IN_FORCE | NOT_IN_FORCE
    status: str | None = None  # "obowiązujący", ...
    text_pdf_url: str | None = None
    isap_url: str | None = None
    fetched_at: dt.datetime

    @property
    def already_in_force_when_fetched(self) -> bool:
        return self.entry_into_force is not None and self.entry_into_force <= self.fetched_at.date()


class ProcessSummary(BaseModel):
    """An item of GET /processes (or, for pre-print bills, derived from GET /bills)."""

    model_config = ConfigDict(frozen=True)

    term: int
    number: str
    title: str
    description: str | None = None
    document_type: str
    document_type_enum: DocumentType = DocumentType.OTHER
    process_start_date: dt.date | None = None
    document_date: dt.date | None = None
    change_date: dt.datetime
    closure_date: dt.date | None = None
    passed: bool | None = None
    urgency_status: str | None = None
    eu_related: bool = False
    rcl_num: str | None = None
    rcl_link: str | None = None
    prints_considered_jointly: tuple[str, ...] = ()
    applicant: ApplicantType | None = None  # explicit (from /bills); else derived from the title
    eli: str | None = None  # set once the act is published in Dziennik Ustaw
    display_address: str | None = None  # "Dz.U. 2026 poz. 1099"
    isap_url: str | None = None

    @property
    def web_url(self) -> str:
        return process_web_url(self.term, self.number)

    @property
    def applicant_type(self) -> ApplicantType:
        if self.applicant is not None and self.applicant is not ApplicantType.UNKNOWN:
            return self.applicant
        return applicant_from_title(self.title)

    @property
    def is_pre_print(self) -> bool:
        """A /bills entry (RPW number) that has not been assigned a print number yet."""
        return is_pre_print_number(self.number)

    @property
    def is_rcl(self) -> bool:
        """A government project followed on RCL, before it is sent to the Sejm."""
        return is_rcl_number(self.number)

    @property
    def is_wykaz(self) -> bool:
        """An entry of the wykaz prac legislacyjnych RM: a planned bill, with no text yet."""
        return is_wykaz_number(self.number)

    @property
    def has_process(self) -> bool:
        """True when the Sejm API has a legislative process (`/processes/{number}`) for it."""
        return has_process(self.number)

    @classmethod
    def from_submission(cls, sub: BillSubmission) -> Self:
        """A summary for a bill that has no legislative process yet (no print number)."""
        received = dt.datetime.combine(sub.date_of_receipt, dt.time(0, 0), tzinfo=dt.UTC)
        return cls(
            term=sub.term,
            number=sub.number,
            title=sub.title,
            description=sub.description,
            document_type=BILL_DOCUMENT_TYPE,
            document_type_enum=DocumentType.BILL,
            process_start_date=sub.date_of_receipt,
            document_date=sub.date_of_receipt,
            change_date=received,
            closure_date=sub.withdrawn_date if sub.is_closed else None,
            passed=False if sub.is_closed else None,
            eu_related=sub.eu_related,
            applicant=sub.applicant,
        )


class ProcessDetail(ProcessSummary):
    """GET /processes/{number}: summary + stages."""

    stages: tuple[Stage, ...] = ()
    title_final: str | None = None

    @property
    def last_stage(self) -> Stage | None:
        flat = flatten_stages(self.stages)
        return flat[-1] if flat else None


class Attachment(BaseModel):
    model_config = ConfigDict(frozen=True)

    print_number: str
    name: str
    url: str

    @property
    def is_pdf(self) -> bool:
        return self.name.lower().endswith(".pdf")


class PrintInfo(BaseModel):
    """GET /prints/{number}."""

    model_config = ConfigDict(frozen=True)

    term: int
    number: str
    title: str
    document_date: dt.date | None = None
    delivery_date: dt.date | None = None
    change_date: dt.datetime | None = None
    attachments: tuple[Attachment, ...] = ()
    additional_prints: tuple[PrintInfo, ...] = ()

    @property
    def web_url(self) -> str:
        return print_web_url(self.term, self.number)

    @property
    def main_pdf(self) -> Attachment | None:
        """The bill text itself: `{number}.pdf`, else the first PDF attachment."""
        preferred = f"{self.number}.pdf".lower()
        pdfs = [a for a in self.attachments if a.is_pdf]
        for a in pdfs:
            if a.name.lower() == preferred:
                return a
        return pdfs[0] if pdfs else None


def is_pre_print_number(number: str) -> bool:
    return number.startswith(PRE_PRINT_PREFIX)


def is_rcl_number(number: str) -> bool:
    return number.startswith(RCL_PREFIX)


def is_wykaz_number(number: str) -> bool:
    return number.startswith(WYKAZ_PREFIX)


def has_process(number: str) -> bool:
    """False for the numbers we invent for bills the Sejm API has no process for.

    Every source that is not the Sejm invents its own prefix, and this is the one gate that keeps
    its rows away from `/processes`, from the print downloader and from the trackers: a prefix
    missing here would send `get_process("WPL/UD408")` to the API on the next run.
    """
    return not number.startswith(NON_SEJM_PREFIXES)


def submission_pdf_url(term: int, number: str) -> str:
    """Where the Sejm site serves the text of a bill without a print number (browser only)."""
    slug = f"{term}-{number.replace('/', '-')}"
    return f"https://orka.sejm.gov.pl/Druki{term}ka.nsf/Projekty/{slug}/$file/{slug}.pdf"


def process_web_url(term: int, number: str) -> str:
    if is_pre_print_number(number):
        return submission_pdf_url(term, number)
    if is_rcl_number(number):
        return f"https://legislacja.rcl.gov.pl/projekt/{number.removeprefix(RCL_PREFIX)}"
    if is_wykaz_number(number):
        return WYKAZ_REGISTER_URL  # the entry's own page is `WykazEntry.web_url`
    return f"https://www.sejm.gov.pl/Sejm{term}.nsf/PrzebiegProc.xsp?nr={number}"


def consultation_web_url(term: int, number: str) -> str:
    """The Sejm page of a bill under public consultation, keyed by the /bills number (RPW/…)."""
    return (
        f"https://www.sejm.gov.pl/Sejm{term}.nsf/agent.xsp?symbol=KONSULTOWANY_PROJEKT"
        f"&NrProjektu={number}"
    )


def committee_web_url(term: int, code: str) -> str:
    return (
        f"https://www.sejm.gov.pl/Sejm{term}.nsf/agent.xsp?symbol=KOMISJAST"
        f"&NrKadencji={term}&KodKom={code}"
    )


def aggregate_clubs(votes: Iterable[Vote]) -> tuple[ClubVotes, ...]:
    """Per-club totals, largest "yes" first (then "no", "abstain"); independents form a club."""
    counts: dict[str, dict[str, int]] = {}
    for vote in votes:
        club = counts.setdefault(
            vote.club or "niez.", {"yes": 0, "no": 0, "abstain": 0, "absent": 0}
        )
        key = {"YES": "yes", "NO": "no", "ABSTAIN": "abstain"}.get(vote.vote.upper(), "absent")
        club[key] += 1
    result = [ClubVotes(club=name, **c) for name, c in counts.items()]
    result.sort(key=lambda c: (-c.yes, -c.no, -c.abstain, c.club))
    return tuple(result)


# Where the Senate puts the laws the Sejm has passed, with the committee that has each one:
# the Senate has no API, so this listing is the only address a reader can be given.
SENATE_BILLS_URL = (
    "https://www.senat.gov.pl/prace/proces-legislacyjny-w-senacie/ustawy-uchwalone-przez-sejm/"
)


def print_web_url(term: int, number: str) -> str:
    return f"https://www.sejm.gov.pl/Sejm{term}.nsf/druk.xsp?nr={number}"


_APPLICANT_PREFIXES: tuple[tuple[str, ApplicantType], ...] = (
    ("rządowy", ApplicantType.GOVERNMENT),
    ("poselski", ApplicantType.DEPUTIES),
    ("senacki", ApplicantType.SENATE),
    ("przedstawiony przez prezydenta", ApplicantType.PRESIDENT),
    ("prezydencki", ApplicantType.PRESIDENT),
    ("przedstawiony przez prezydium", ApplicantType.PRESIDIUM),
    ("obywatelski", ApplicantType.CITIZENS),
    ("komisyjny", ApplicantType.COMMITTEE),
)


def applicant_from_title(title: str) -> ApplicantType:
    lowered = title.strip().lower()
    for prefix, kind in _APPLICANT_PREFIXES:
        if lowered.startswith(prefix):
            return kind
    return ApplicantType.UNKNOWN


def flatten_stages(stages: tuple[Stage, ...] | list[Stage]) -> list[Stage]:
    """Depth-first flattening of the stage tree, children after their parent."""
    out: list[Stage] = []
    for stage in stages:
        out.append(stage)
        if stage.children:
            out.extend(flatten_stages(stage.children))
    return out


def _stage_key(stage: Stage, depth: int) -> tuple[object, ...]:
    return (
        depth,
        stage.stage_type,
        stage.stage_name,
        stage.date.isoformat() if stage.date else None,
        stage.print_number,
        stage.sitting_num,
        stage.decision,
        stage.committee_code,
    )


def _stage_keys(
    stages: tuple[Stage, ...] | list[Stage], depth: int = 0
) -> list[tuple[object, ...]]:
    keys: list[tuple[object, ...]] = []
    for stage in stages:
        keys.append(_stage_key(stage, depth))
        if stage.children:
            keys.extend(_stage_keys(stage.children, depth + 1))
    return keys


def stage_fingerprint(stages: tuple[Stage, ...] | list[Stage]) -> str:
    """Stable hash of the stage tree. Excludes volatile fields (URLs, vote counts)."""
    payload = json.dumps(_stage_keys(stages), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def diff_stages(
    old: tuple[Stage, ...] | list[Stage], new: tuple[Stage, ...] | list[Stage]
) -> list[Stage]:
    """Stages present in `new` but not in `old`, in the order they appear in `new`.

    Because the key includes the date, a stage that existed with `date: None` and now has a
    date is reported once more (as the dated version): for the reader it just "happened".
    """
    old_keys = set(_stage_keys(old))
    return [
        stage
        for stage, key in zip(flatten_stages(new), _stage_keys(new), strict=True)
        if key not in old_keys
    ]


class TextDocument(BaseModel):
    """A document carrying the bill text at some point of the process.

    `extra_urls` are read after the main one and appended (an RCL project publishes the bill,
    its uzasadnienie and the OSR as separate files; a Sejm print has them in one PDF).
    """

    model_config = ConfigDict(frozen=True)

    url: str
    kind: SourceKind
    extra_urls: tuple[str, ...] = ()


def latest_text_document(
    stages: tuple[Stage, ...] | list[Stage], *, before_third_reading: bool = False
) -> TextDocument | None:
    """The most recent stage document that contains an (amended) bill text, if any.

    Text after the 3rd reading beats committee reports, which beat the original print (None here).
    `before_third_reading` ignores the text after the 3rd reading: the text the Sejm voted on.
    """
    text_after3: str | None = None
    report: str | None = None
    for stage in flatten_stages(stages):
        if stage.text_after3:
            text_after3 = stage.text_after3
        if stage.carries_bill_text:
            report = stage.report_file
    if text_after3 and not before_third_reading:
        return TextDocument(url=text_after3, kind="text_after3")
    if report:
        return TextDocument(url=report, kind="committee_report")
    return None


# A second reading whose `decision` says one of these left the bill with the committee, which
# works the amendments into an additional ("-A") report before the Sejm votes. Observed:
# "skierowano ponownie do komisji…", "niedokończone II czytanie" (druk 1929).
_SECOND_READING_SENT_BACK = ("ponownie", "niedokończone")


def second_reading_sent_back(stage: Stage) -> bool:
    """True when the second reading did not hand the bill on to the third."""
    decided = (stage.decision or "").lower()
    return any(marker in decided for marker in _SECOND_READING_SENT_BACK)


def third_reading_kept_the_text(stages: tuple[Stage, ...] | list[Stage]) -> bool:
    """True when the text after the 3rd reading cannot differ from the text the committee (or
    the print) put before the Sejm: no amendments were tabled at the 2nd reading (the Sejm went
    straight on to the 3rd), so no additional report exists, and the report carried no minority
    motions to vote on. Unknown facts (no 2nd reading, motions not parsed) count as "may differ"."""
    top = list(stages)
    second = [
        st
        for st in top
        if st.stage_type == "SejmReading" and st.stage_name.strip().upper().startswith("II ")
    ]
    if not second or "niezwłocznie" not in (second[-1].decision or "").lower():
        return False
    reports = [st for st in flatten_stages(top) if st.stage_type == "CommitteeReport"]
    if any(r.print_number and r.print_number.upper().endswith("-A") for r in reports):
        return False
    final = [r for r in reports if not r.sub_committee]
    return all(r.minority_motions == 0 for r in final)
