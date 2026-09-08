"""The LLM side: what the model sees (contexts), what it answers (Analysis, Triage) and how
the answers are stored, plus token accounting."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field

from lexinform.models.enums import ApplicantType, Category, SourceKind, TextSource


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


class TokenUsage(BaseModel):
    """Tokens of one or more requests to one model, in the API's own categories."""

    input: int = 0  # uncached input
    output: int = 0
    cache_read: int = 0
    cache_creation: int = 0

    def plus(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input=self.input + other.input,
            output=self.output + other.output,
            cache_read=self.cache_read + other.cache_read,
            cache_creation=self.cache_creation + other.cache_creation,
        )


def usage_of(record: AnalysisRecord | TriageRecord) -> TokenUsage:
    return TokenUsage(
        input=record.input_tokens or 0,
        output=record.output_tokens or 0,
        cache_read=record.cache_read_input_tokens or 0,
        cache_creation=record.cache_creation_input_tokens or 0,
    )


def add_usage(target: dict[str, TokenUsage], record: AnalysisRecord | TriageRecord) -> None:
    """Accumulate a record's tokens under its model."""
    target[record.model] = target.get(record.model, TokenUsage()).plus(usage_of(record))


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
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    source_url: str | None = None
    source_kind: SourceKind = "print"
    revision: int = 1


class Triage(BaseModel):
    """Structured output of the cheap first pass: does the bill concern foreigners at all?"""

    affects_foreigners: bool = Field(
        description="True if the bill changes anything for non-citizens, or if in doubt."
    )
    confidence: float = Field(ge=0.0, le=1.0, description="How sure you are of the answer.")
    rationale: str = Field(description="One sentence in the output language.")


class TriageRecord(BaseModel):
    triage: Triage
    model: str
    prompt_version: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None

    def rejects(self, *, min_confidence: float) -> bool:
        return not self.triage.affects_foreigners and self.triage.confidence >= min_confidence


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
