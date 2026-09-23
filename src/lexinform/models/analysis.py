"""The LLM side: what the model sees (contexts), what it answers (Analysis, Triage) and how
the answers are stored, plus token accounting."""

import datetime as dt
from collections.abc import Mapping
from typing import Self

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
    batch_input: int = 0
    batch_output: int = 0
    batch_cache_read: int = 0
    batch_cache_creation: int = 0

    def plus(self, other: Self) -> Self:
        return type(self)(
            input=self.input + other.input,
            output=self.output + other.output,
            cache_read=self.cache_read + other.cache_read,
            cache_creation=self.cache_creation + other.cache_creation,
            batch_input=self.batch_input + other.batch_input,
            batch_output=self.batch_output + other.batch_output,
            batch_cache_read=self.batch_cache_read + other.batch_cache_read,
            batch_cache_creation=self.batch_cache_creation + other.batch_cache_creation,
        )


class AnalysisRecord(BaseModel):
    """A stored analysis: the model's answer plus how it was obtained (model, text, tokens).

    `text_sha256` is the digest of the normalised text the model saw, so a document republished
    under a new URL is not analysed again. `source_checked_at` is when the source was last found
    unchanged, and the print's `changeDate` is compared with it rather than with `created_at`.
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


class JointComparison(BaseModel):
    """Structured output about one print of a jointly considered group: how it differs from the
    others, not a new analysis of it."""

    same_substance: bool = Field(
        description="True when this bill does the same thing as the others and differs only in"
        " wording, numbering or detail."
    )
    summary: str = Field(
        description="1-2 plain sentences: what this bill does that the others do not, or in what"
        " it takes a different approach. At most ~300 characters."
    )
    differences: list[str] = Field(
        default_factory=list,
        description="Up to 5 bullets, at most ~120 characters each: one concrete difference per"
        " bullet, naming the other print when the group has more than two.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Lower it when the descriptions are too general to tell the bills apart.",
    )


class JointBillDescription(BaseModel):
    """One bill of a jointly considered group as the channel currently describes it: what the
    comparison is made of, on both sides."""

    number: str
    title: str
    applicant_type: ApplicantType
    summary: str
    key_changes: list[str] = Field(default_factory=list)
    affected_groups: list[str] = Field(default_factory=list)
    practical_impact: str = ""


class JointContext(BaseModel):
    """What the model sees to compare one print with the others considered jointly with it: the
    channel's own description of each, and no bill text at all.

    Each text was read once already, and the card is what the reader has: the question is how this
    print differs from what the card told them, which the descriptions carry.
    """

    subject: JointBillDescription
    others: list[JointBillDescription]


class JointRecord(BaseModel):
    """A stored comparison with its provenance. `compared_with` is what it was made against, so a
    group that gains a print is compared again instead of showing an answer about fewer bills."""

    comparison: JointComparison
    compared_with: list[str]
    model: str
    prompt_version: str
    created_at: dt.datetime
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None


class ScannedDocument(BaseModel):
    """A document the model reads as pages, because its file carries no text to read.

    `data` is the file itself, base64 without line breaks, as the API's document block wants it.
    It is never stored: what the analysis keeps of a scan is `sha256`, which plays the part
    `text_sha256` plays for a text, so a republished scan is recognised as the same document.
    """

    model_config = ConfigDict(frozen=True)

    media_type: str = "application/pdf"
    data: str
    pages: int = Field(description="How many pages were sent.")
    of_pages: int = Field(description="How many the document has.")
    sha256: str
    cover_letter_pages: int = Field(
        default=0,
        description="How many of the pages not sent were the letter handing the document to the Marshal.",
    )

    @property
    def truncated(self) -> bool:
        """Whether the model was kept from part of the document itself.

        Dropping the covering letter is not that: it is one page that names the bill and says who
        will present it, and calling the reading "partial" because of it made every scanned print
        tell its readers that the analysis had seen less than the document — and told the model to
        lower its confidence for the same reason.
        """
        return self.of_pages - self.pages > self.cover_letter_pages


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

    `digest` is None when the document could not be read or the call failed; the reply still names
    and links it, the run recording it as told either way.
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


UsageRecord = AnalysisRecord | TriageRecord | AmendmentsRecord | SupplementRecord | JointRecord


def usage_of(record: UsageRecord) -> dict[str, TokenUsage]:
    """A record's tokens under its model, the shape `merge_usage` and `cost_usd` take."""
    return {
        record.model: TokenUsage(
            input=record.input_tokens or 0,
            output=record.output_tokens or 0,
            cache_read=record.cache_read_input_tokens or 0,
            cache_creation=record.cache_creation_input_tokens or 0,
        )
    }


def merge_usage(target: dict[str, TokenUsage], usage: Mapping[str, TokenUsage]) -> None:
    """Add each model's tokens in `usage` to that model's total in `target`."""
    for model, tokens in usage.items():
        target[model] = target.get(model, TokenUsage()).plus(tokens)


class TriageContext(BaseModel):
    """What the triage model sees: metadata plus excerpts of the text, never the whole print;
    `text_chars` is the length of the full (trimmed) text they were taken from.

    `scan` is the first pages of a document with no text to excerpt. A few are enough for the
    question the triage asks: of 18 real scans asked this way, 18 were rejected correctly at a
    mean confidence of 0.93.
    """

    number: str
    title: str
    description: str | None
    applicant_type: ApplicantType
    excerpts: str
    text_chars: int
    scan: ScannedDocument | None = None


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
    scan: ScannedDocument | None = Field(
        default=None,
        description=(
            "The file itself, when text_source is scan and the pages are what the model reads"
        ),
    )
    source_kind: SourceKind = "print"
    previous_summary: str | None = None
    previous_key_changes: list[str] = Field(default_factory=list)
