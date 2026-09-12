"""The LLM side: what the model sees (contexts), what it answers (Analysis, Triage) and how
the answers are stored, plus token accounting."""

import datetime as dt
from typing import Self

from pydantic import BaseModel, Field

from lexinform.models.enums import ApplicantType, Category, SourceKind, TextSource


class Analysis(BaseModel):
    """Structured output of the LLM. Also the persisted shape."""

    relevant: bool = Field(description="True if the bill changes anything for non-citizens.")
    score: int = Field(ge=1, le=5, description="Importance 1-5; 5 = legalization of stay.")
    category: Category
    # The schema travels with the prompt as the structured-output contract, so what it says
    # about length must be what the prompt says.
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
    """Tokens of one or more requests to one model, in the API's own categories."""

    input: int = 0  # uncached input
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
    """A stored analysis: the model's answer plus how it was obtained (model, text, tokens)."""

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
    # Digest of the (trimmed, budgeted, whitespace-normalised) text the model saw: a document
    # published under a new URL with the same text is not analysed again.
    text_sha256: str | None = None
    # When the source was last found unchanged (a print re-dated by an attachment, a republished
    # RCL file); the print's `changeDate` is compared with this, not with `created_at`.
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
    """What the model sees to summarise amendments: the current analysis and the document."""

    number: str
    title: str
    source_kind: SourceKind  # senate_amendments | committee_amendments
    text: str
    truncated: bool
    previous_summary: str
    previous_key_changes: list[str] = Field(default_factory=list)
    proposal: str | None = None  # the committee's proposal on the amendments, when it is one


UsageRecord = AnalysisRecord | TriageRecord | AmendmentsRecord


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
    """What the triage model sees: metadata plus excerpts of the text, never the whole print."""

    number: str
    title: str
    description: str | None
    applicant_type: ApplicantType
    excerpts: str
    text_chars: int  # length of the full (trimmed) text the excerpts were taken from


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
