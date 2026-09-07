"""Domain models. Pure data + a few pure functions; no I/O here."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Iterable
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- enums


class DocumentType(StrEnum):
    BILL = "BILL"
    DRAFT_RESOLUTION = "DRAFT_RESOLUTION"
    OTHER = "OTHER"


class BillStatus(StrEnum):
    DISCOVERED = "discovered"
    SKIPPED_PREFILTER = "skipped_prefilter"
    ANALYSIS_PENDING = "analysis_pending"
    ANALYSIS_FAILED = "analysis_failed"
    ANALYZED = "analyzed"


class Category(StrEnum):
    LEGAL_STAY = "legal_stay"
    EMPLOYMENT = "employment"
    SOCIAL = "social"
    INDIRECT = "indirect"
    MARGINAL = "marginal"
    NONE = "none"


BILL_DOCUMENT_TYPE = "projekt ustawy"  # the `documentType` display string used by the API filter


class ApplicantType(StrEnum):
    GOVERNMENT = "government"
    DEPUTIES = "deputies"
    SENATE = "senate"
    PRESIDENT = "president"
    PRESIDIUM = "presidium"
    CITIZENS = "citizens"
    COMMITTEE = "committee"
    UNKNOWN = "unknown"


class PublicationKind(StrEnum):
    NEW_BILL = "new_bill"
    STATUS_UPDATE = "status_update"


class PublicationStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    SKIPPED = "skipped"
    FAILED = "failed"
    UNKNOWN = "unknown"


TextSource = Literal["pdf", "metadata_only"]
SourceKind = Literal["print", "committee_report", "text_after3", "metadata"]


# --------------------------------------------------------------------------- Sejm data


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
    total_voted: int | None = None
    majority_type: str | None = None
    majority_votes: int | None = None
    sitting: int | None = None
    voting_number: int | None = None
    date: dt.datetime | None = None
    description: str | None = None
    topic: str | None = None
    pdf_url: str | None = None
    clubs: tuple[ClubVotes, ...] = ()


class Committee(BaseModel):
    model_config = ConfigDict(frozen=True)

    term: int
    code: str
    name: str
    name_genitive: str | None = None

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
    voting: VotingSummary | None = None  # Voting: results (not part of the fingerprint)
    committee_name: str | None = (
        None  # Referral: resolved by us from /committees (not fingerprinted)
    )
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


class ProcessSummary(BaseModel):
    """An item of GET /processes."""

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

    @property
    def web_url(self) -> str:
        return process_web_url(self.term, self.number)

    @property
    def applicant_type(self) -> ApplicantType:
        return applicant_from_title(self.title)


class ProcessDetail(ProcessSummary):
    """GET /processes/{number}: summary + stages."""

    stages: tuple[Stage, ...] = ()
    title_final: str | None = None
    eli: str | None = None

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


# --------------------------------------------------------------------------- analysis


class Analysis(BaseModel):
    """Structured output of the LLM. Also the persisted shape."""

    relevant: bool = Field(description="True if the bill changes anything for non-citizens.")
    score: int = Field(ge=1, le=5, description="Importance 1-5; 5 = legalization of stay.")
    category: Category
    summary: str = Field(description="3-6 plain-language sentences in the output language.")
    key_changes: list[str] = Field(default_factory=list, description="Up to 6 bullets.")
    affected_groups: list[str] = Field(default_factory=list)
    practical_impact: str = Field(description="What changes for a foreigner in practice.")
    effective_date: str | None = Field(
        default=None, description="Vacatio legis as stated in the text, or null."
    )
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(description="One sentence justifying score and category.")
    changes_since_previous: list[str] = Field(
        default_factory=list,
        description="Only when a previous analysis is provided: concrete differences between the"
        " previous version of the bill and the current text. Empty otherwise.",
    )


class AnalysisRecord(BaseModel):
    analysis: Analysis
    model: str
    prompt_version: str
    input_chars: int
    truncated: bool
    text_source: TextSource
    created_at: dt.datetime
    input_tokens: int | None = None
    output_tokens: int | None = None
    source_url: str | None = None
    source_kind: SourceKind = "print"
    revision: int = 1


class BillContext(BaseModel):
    """Everything the LLM gets to see about one bill."""

    number: str
    title: str
    description: str | None
    document_date: dt.date | None
    applicant_type: ApplicantType
    text: str
    truncated: bool
    text_source: TextSource
    source_kind: SourceKind = "print"
    previous_summary: str | None = None
    previous_key_changes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- aggregate


class Bill(BaseModel):
    summary: ProcessSummary
    status: BillStatus
    prefilter_hits: list[str] = Field(default_factory=list)
    stages: tuple[Stage, ...] = ()
    stages_fingerprint: str | None = None
    analysis: AnalysisRecord | None = None
    analysis_attempts: int = 0
    last_error: str | None = None
    first_seen_at: dt.datetime
    last_checked_at: dt.datetime

    @property
    def term(self) -> int:
        return self.summary.term

    @property
    def number(self) -> str:
        return self.summary.number

    @property
    def last_stage(self) -> Stage | None:
        flat = flatten_stages(self.stages)
        return flat[-1] if flat else None


class Publication(BaseModel):
    id: int | None = None
    term: int
    number: str
    kind: PublicationKind
    status: PublicationStatus
    channel_id: str
    message_id: int | None = None
    document_message_ids: list[int] = Field(default_factory=list)
    status_change_id: int | None = None
    created_at: dt.datetime
    sent_at: dt.datetime | None = None
    error: str | None = None


class StatusChange(BaseModel):
    id: int | None = None
    term: int
    number: str
    old_fingerprint: str | None
    new_fingerprint: str
    new_stages: list[Stage]
    closure_detected: bool = False
    passed: bool | None = None
    content_changed: bool = False
    detected_at: dt.datetime


class RunReport(BaseModel):
    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    since: dt.datetime
    mode: str
    discovery_ok: bool = False  # discovery finished: the watermark may advance past `started_at`
    discovered: int = 0
    prefilter_hits: int = 0
    analyzed: int = 0
    analysis_failures: int = 0
    published: int = 0
    updates: int = 0
    reanalyzed: int = 0
    tracked: int = 0
    errors: list[str] = Field(default_factory=list)
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def duration_seconds(self) -> int | None:
        if self.finished_at is None:
            return None
        return int((self.finished_at - self.started_at).total_seconds())


# --------------------------------------------------------------------------- pure helpers


def process_web_url(term: int, number: str) -> str:
    return f"https://www.sejm.gov.pl/Sejm{term}.nsf/PrzebiegProc.xsp?nr={number}"


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
    """A document carrying the bill text at some point of the process."""

    model_config = ConfigDict(frozen=True)

    url: str
    kind: SourceKind


def latest_text_document(stages: tuple[Stage, ...] | list[Stage]) -> TextDocument | None:
    """The most recent stage document that contains an (amended) bill text, if any.

    Text after the 3rd reading beats committee reports, which beat the original print (None here).
    """
    text_after3: str | None = None
    report: str | None = None
    for stage in flatten_stages(stages):
        if stage.text_after3:
            text_after3 = stage.text_after3
        if stage.carries_bill_text:
            report = stage.report_file
    if text_after3:
        return TextDocument(url=text_after3, kind="text_after3")
    if report:
        return TextDocument(url=report, kind="committee_report")
    return None
