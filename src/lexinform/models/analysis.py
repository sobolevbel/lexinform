"""The LLM side: what the model sees (contexts), what it answers (Analysis, Triage) and how
the answers are stored, plus token accounting."""

import datetime as dt
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from lexinform.models.enums import ApplicantType, Category, SourceKind, TextSource


class Analysis(BaseModel):
    """Structured output of the LLM, and the persisted shape.

    The field descriptions travel with the prompt as the structured-output contract, so what they
    say about length must be what the prompt says.
    """

    relevant: bool = Field(description="True if the bill changes anything for non-citizens.")
    score: int = Field(ge=1, le=5, description="Importance 1-5; 5 = legalization of stay.")
    category: Category
    summary: str = Field(description="2-3 plain sentences, at most ~350 characters.")
    key_changes: list[str] = Field(
        default_factory=list, description="Up to 5 bullets, at most ~120 characters each."
    )
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


class TokenUsage(BaseModel):
    """Tokens of one or more requests to one model, in the API's own categories; `input` is the
    uncached part of it."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_creation: int = 0

    def plus(self, other: Self) -> Self:
        return type(self)(
            input=self.input + other.input,
            output=self.output + other.output,
            cache_read=self.cache_read + other.cache_read,
            cache_creation=self.cache_creation + other.cache_creation,
        )


class AnalysisRecord(BaseModel):
    """A stored analysis: the model's answer plus how it was obtained (model, text, tokens).

    `text_sha256` is the digest of the trimmed, budgeted, whitespace-normalised text the model
    saw, so that a document republished under a new URL with the same text is not analysed again.
    `source_checked_at` is when the source was last found unchanged — a print re-dated by an
    attachment, a republished RCL file — and the print's `changeDate` is compared with it, not
    with `created_at`.
    """

    analysis: Analysis
    model: str
    prompt_version: str
    input_chars: int
    truncated: bool
    text_source: TextSource
    created_at: dt.datetime
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    source_url: str | None = None
    source_kind: SourceKind = "print"
    revision: int = 1
    text_sha256: str | None = None
    source_checked_at: dt.datetime | None = None


class Triage(BaseModel):
    """Structured output of the cheap first pass: does the bill concern foreigners at all?"""

    affects_foreigners: bool = Field(
        description="True if the bill changes anything for non-citizens, or if in doubt."
    )
    confidence: float = Field(ge=0.0, le=1.0, description="How sure you are of the answer.")
    rationale: str = Field(description="One sentence in the output language.")


class TriageRecord(BaseModel):
    """A triage answer with its provenance; not persisted on its own."""

    triage: Triage
    model: str
    prompt_version: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None

    def rejects(self, *, min_confidence: float) -> bool:
        """A confident "does not affect foreigners" ends the analysis here."""
        return not self.triage.affects_foreigners and self.triage.confidence >= min_confidence


class Amendments(BaseModel):
    """Structured output about a set of amendments (the Senate's, or those tabled at the 2nd
    reading as answered by the committee): what they change, not a new analysis of the bill."""

    summary: str = Field(description="1-2 plain sentences: what the amendments do overall.")
    changes: list[str] = Field(
        default_factory=list, description="Up to 6 concrete changes, one per bullet."
    )
    affects_foreigners: bool = Field(
        description="True if any amendment changes something for non-citizens."
    )
    confidence: float = Field(ge=0.0, le=1.0)


class AmendmentsRecord(BaseModel):
    """A stored amendments summary: the answer plus its provenance; lives on the status change
    that announced the amendments."""

    amendments: Amendments
    model: str
    prompt_version: str
    source_url: str
    source_kind: SourceKind
    created_at: dt.datetime
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None


class AmendmentsContext(BaseModel):
    """What the model sees to summarise amendments: the current analysis and the document, with
    the committee's proposal on them where there is one."""

    number: str
    title: str
    source_kind: SourceKind
    text: str
    truncated: bool
    previous_summary: str
    previous_key_changes: list[str] = Field(default_factory=list)
    proposal: str | None = None


class ScannedDocument(BaseModel):
    """A document the model reads as pages, because its file carries no text to read.

    `data` is the file itself, base64 without line breaks, as the API's document block wants it.
    It is never stored: what the analysis keeps of a scan is `sha256`, which plays the part
    `text_sha256` plays for a text, so a republished scan is recognised as the same document.
    """

    model_config = ConfigDict(frozen=True)

    media_type: str = "application/pdf"
    data: str
    pages: int
    """How many pages were sent."""
    of_pages: int
    """How many the document has: more than `pages` when the covering letter or the tail of an
    OSR form was left behind, which is what the card's "partial text" note is rendered from."""
    sha256: str

    @property
    def truncated(self) -> bool:
        return self.pages < self.of_pages


PageRole = Literal["cover", "bill", "justification", "impact", "finance", "comparison", "appendix"]
"""What one page of a scanned document holds. The vocabulary is `sections.trim_print`'s, in
pages instead of characters: `cover` is the letter that hands the document over and the page it
is signed on, `finance` the public-finance tables a printed OSR is cut before, `appendix` the
comment tables, compliance tables, consultation reports and draft regulations that make up most
of a government print, and `comparison` the survey of how other countries solved it."""


class PageMap(BaseModel):
    """Structured output of the page map: one role per page, in order."""

    roles: list[PageRole] = Field(
        description="One entry per page of the document, in the order the pages were given."
    )


class PageMapRecord(BaseModel):
    """A page map and what it cost; the model that read the pages is a cheap one."""

    map: PageMap
    model: str
    prompt_version: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None


class PageMapContext(BaseModel):
    """What the mapping model sees: the pages, small, and what the document is supposed to be."""

    number: str
    document_title: str
    source_kind: SourceKind
    pages: list[str]
    """One base64 JPEG per page, rendered small: a heading is legible, the body is not, and a
    heading is all the map needs."""


class DocumentDigest(BaseModel):
    """Structured output about a document filed to a print: what it says about the bill that is
    already there, not a new analysis of the bill."""

    summary: str = Field(
        description="1-2 plain sentences: what the document asks for or objects to, never a"
        " repetition of the verdict `supports` already carries."
    )
    points: list[str] = Field(
        default_factory=list, description="Up to 5 concrete statements, one per bullet."
    )
    supports: bool | None = Field(
        default=None,
        description="Only for the government's position: true when it backs the bill, false when"
        " it is against, null when it is neither or the document is not a position.",
    )
    affects_foreigners: bool = Field(
        description="True if what the document says bears on non-citizens."
    )
    confidence: float = Field(ge=0.0, le=1.0)


class SupplementRecord(BaseModel):
    """One document filed to a print after its submission, as the status change that announces it
    carries it: what the document is (`number` and `title` are the additional print's own), and
    what the model made of it.

    `digest` is None when the document could not be read or the model call failed. The reply then
    names the document and links it — a scan of an opinion is still news, and dropping it would
    lose it for good, since the run records it as told either way.
    """

    number: str
    title: str
    source_kind: SourceKind
    source_url: str
    digest: DocumentDigest | None = None
    model: str = ""
    prompt_version: str = ""
    created_at: dt.datetime | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None


class SupplementContext(BaseModel):
    """What the model sees to digest a filed document: the bill as the channel currently
    describes it, and the document."""

    number: str
    title: str
    document_title: str
    source_kind: SourceKind
    text: str
    truncated: bool
    scan: ScannedDocument | None = None
    """The file itself, when the document is scanned paper and `text` is empty."""
    previous_summary: str
    previous_key_changes: list[str] = Field(default_factory=list)


UsageRecord = AnalysisRecord | TriageRecord | AmendmentsRecord | SupplementRecord | PageMapRecord


def usage_of(record: UsageRecord) -> TokenUsage:
    return TokenUsage(
        input=record.input_tokens or 0,
        output=record.output_tokens or 0,
        cache_read=record.cache_read_input_tokens or 0,
        cache_creation=record.cache_creation_input_tokens or 0,
    )


def add_usage(target: dict[str, TokenUsage], record: UsageRecord) -> None:
    """Accumulate a record's tokens under its model."""
    target[record.model] = target.get(record.model, TokenUsage()).plus(usage_of(record))


class TriageContext(BaseModel):
    """What the triage model sees: metadata plus excerpts of the text, never the whole print;
    `text_chars` is the length of the full (trimmed) text they were taken from."""

    number: str
    title: str
    description: str | None
    applicant_type: ApplicantType
    excerpts: str
    text_chars: int


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
    scan: ScannedDocument | None = None
    """The file itself, when `text_source` is "scan" and the pages are what the model reads."""
    source_kind: SourceKind = "print"
    previous_summary: str | None = None
    previous_key_changes: list[str] = Field(default_factory=list)
