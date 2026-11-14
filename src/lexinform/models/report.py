"""What one run reports to the log channel and stores in the `runs` table."""

import datetime as dt
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, model_validator

from lexinform.models.analysis import TokenUsage, usage_key
from lexinform.models.document_reads import (
    DocumentDownloadDiagnostics,
    DocumentPhysicalDiagnostics,
    DocumentReadMetrics,
)
from lexinform.models.enums import RunMode
from lexinform.models.metadata_omissions import MetadataUrlOmissions
from lexinform.models.report_content import (
    ReportContent,
    ReportField,
    has_report_content,
    report_content,
)


class AnalysisVerdict(BaseModel):
    """What the model said about one bill this run; the report lists the ones not published.

    `triaged` marks a bill the cheap first pass on excerpts rejected, without a full analysis.
    `term` is what the link to the process page needs; reports stored before it existed have none.
    """

    number: str
    title: str
    relevant: bool
    score: int
    triaged: bool = False
    term: int | None = None

    @property
    def reason(self) -> str:
        if self.triaged:
            return "triage"
        return f"score {self.score}" if self.relevant else "not relevant"


CallKind = Literal[
    "analysis",
    "reanalysis",
    "explanation",
    "triage",
    "change_review",
    "amendments",
    "supplement",
    "joint",
    "consultation_review",
    "commission_review",
    "act_application",
    "name",
]

CliReadOperation = Literal[
    "scan",
    "reprefilter",
    "analyze",
    "explain",
    "amendments",
    "republish",
    "forget",
    "reset",
    "observe",
]


class LlmCall(BaseModel):
    """One request to the model: which bill it was about, what kind of question, what it cost.

    A run reported its tokens as a single number, and that number could not be accounted for: the
    run of 13 Sept 2026 billed 316,767 input tokens with nothing in the report to say which call
    made them. The phases do not spend evenly — a re-analysis of an RCL package is worth a dozen
    triages — so the next saving is found by reading this list, not by digging through logs.
    """

    number: str
    kind: CallKind
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    batched: bool = False

    @property
    def usage(self) -> dict[str, TokenUsage]:
        """What it cost, in the shape `pricing.cost_usd` prices (which cannot be imported here:
        `pricing` reads these models)."""
        prompt = self.input_tokens + self.cache_read_input_tokens + self.cache_creation_input_tokens
        return {
            usage_key(self.model, prompt): TokenUsage(
                input=self.input_tokens,
                output=self.output_tokens,
                cache_read=self.cache_read_input_tokens,
                cache_creation=self.cache_creation_input_tokens,
            )
            if not self.batched
            else TokenUsage(
                batch_input=self.input_tokens,
                batch_output=self.output_tokens,
                batch_cache_read=self.cache_read_input_tokens,
                batch_cache_creation=self.cache_creation_input_tokens,
            )
        }


class PrefilterRejection(BaseModel):
    term: int
    number: str
    title: str
    stage: Literal["title", "text", "unreadable"]
    reason: str


class RunReport(BaseModel):
    """The counters one run reports to the log channel and stores in the `runs` table.

    `discovery_ok` says discovery finished, which is what lets the watermark advance.
    `over_on_arrival`: first seen with the road already over, stored and never posted.
    `text_prefilter_scans`: sent to the model unsearched, the file being paper. `*_unanswered`:
    the host would not hand the file over, which says nothing about the bill, so the row keeps
    its place in the queue. `held`: stage changes kept for the next post. `notes` are worth
    telling without being errors; `rejected` the bills analysed and not published.
    """

    started_at: Annotated[dt.datetime, ReportField(role="metadata")]
    finished_at: Annotated[dt.datetime | None, ReportField(role="metadata")] = None
    since: Annotated[dt.datetime, ReportField(role="metadata")]
    mode: Annotated[RunMode, ReportField(role="metadata")]
    cli_read_operation: Annotated[CliReadOperation | None, ReportField(role="metadata")] = Field(
        default=None, exclude_if=lambda value: value is None
    )
    term: Annotated[int | None, ReportField(role="metadata")] = None
    discovery_ok: Annotated[bool, ReportField(role="metadata")] = False
    sejm_discovery_unavailable: Annotated[
        bool, ReportField("discovery", "Sejm discovery unavailable", row=5)
    ] = Field(default=False, exclude_if=lambda value: not value)
    discovered: Annotated[int, ReportField("discovery", "Sejm: {} new")] = 0
    pre_print_discovered: Annotated[
        int,
        ReportField("discovery", "{} submitted to the Sejm, no druk number yet (RPW)", order=1),
    ] = 0
    rcl_discovered: Annotated[int, ReportField("discovery", "RCL: {} new", row=1)] = 0
    rcl_prefilter_hits: Annotated[
        int, ReportField("discovery", "prefilter hits: {}", row=1, order=1)
    ] = 0
    wykaz_discovered: Annotated[int, ReportField("discovery", "wykaz prac RM: {} new", row=2)] = 0
    over_on_arrival: Annotated[
        int,
        ReportField("discovery", "{} already over when first seen, not followed", row=3),
    ] = 0
    linked: Annotated[int, ReportField("posts", "linked: {}", order=6)] = 0
    prefilter_hits: Annotated[int, ReportField("discovery", "prefilter hits: {}", order=2)] = 0
    prefilter_rejected: Annotated[list[str] | None, ReportField(section=None)] = None
    prefilter_details: Annotated[list[PrefilterRejection], ReportField(section=None)] = Field(
        default_factory=list
    )
    text_prefilter_checked: Annotated[
        int, ReportField("discovery", "text prefilter: checked {}", row=4)
    ] = 0
    text_prefilter_hits: Annotated[int, ReportField("discovery", "hits {}", row=4, order=1)] = 0
    text_prefilter_scans: Annotated[
        int, ReportField("discovery", "scans to the model {}", row=4, order=2)
    ] = 0
    text_prefilter_unreadable: Annotated[
        int, ReportField("discovery", "unreadable {}", row=4, order=3)
    ] = 0
    text_prefilter_unanswered: Annotated[
        int, ReportField("discovery", "no file to read yet, still pending {}", row=4, order=4)
    ] = 0
    acts_published: Annotated[int, ReportField("posts", "acts: {}", row=1)] = 0
    in_force_posted: Annotated[int, ReportField("posts", "in force: {}", row=1, order=1)] = 0
    consultation_reminders: Annotated[
        int, ReportField("posts", "consultation reminders: {}", row=1, order=2)
    ] = 0
    hearing_reminders: Annotated[int, ReportField("posts", "hearings: {}", row=1, order=12)] = 0
    decision_reminders: Annotated[
        int, ReportField("posts", "Senate/President deadline: {}", row=1, order=13)
    ] = 0
    held: Annotated[int, ReportField("posts", "held: {}", order=4)] = 0
    cards_refreshed: Annotated[
        int, ReportField("posts", "cards refreshed: {}", row=1, order=14)
    ] = 0
    digest_drafted: Annotated[bool, ReportField("digest delivery", "draft prepared")] = False
    digest_reader_parts: Annotated[
        int, ReportField("digest delivery", "reader parts: {}", row=1)
    ] = 0
    digest_events_covered: Annotated[
        int, ReportField("digest delivery", "covered events: {}", row=2)
    ] = 0
    consultation_results_posted: Annotated[
        int, ReportField("posts", "results: {}", row=1, order=3)
    ] = 0
    consultation_reviews_posted: Annotated[
        int, ReportField("posts", "consultation review messages: {}", row=1, order=4)
    ] = 0
    commission_reviews_posted: Annotated[
        int, ReportField("posts", "commission review messages: {}", row=1, order=5)
    ] = 0
    act_reviews_posted: Annotated[
        int, ReportField("posts", "act review messages: {}", row=1, order=6)
    ] = 0
    effective_rule_messages: Annotated[
        int, ReportField("posts", "effective-rule messages: {}", row=1, order=7)
    ] = 0
    amendment_messages_updated: Annotated[
        int, ReportField("posts", "amendment messages updated: {}", row=1, order=8)
    ] = 0
    message_parts_sent: Annotated[
        int, ReportField("posts", "additional physical parts: {}", row=1, order=9)
    ] = 0
    agenda_posted: Annotated[int, ReportField("posts", "agenda: {}", row=1, order=10)] = 0
    agenda_cancelled: Annotated[
        int, ReportField("posts", "sittings called off: {}", row=1, order=11)
    ] = 0
    discontinued: Annotated[int, ReportField("posts", "end of term: {} bill(s) lapsed", row=2)] = 0
    rehomed: Annotated[
        int, ReportField("posts", "{} government row(s) carried over", row=2, order=1)
    ] = 0
    analyzed: Annotated[int, ReportField("analysis", "analyzed: {}")] = 0
    triaged_out: Annotated[int, ReportField("analysis", "triaged out: {}", order=1)] = 0
    analysis_failures: Annotated[int, ReportField("analysis", "failures: {}", order=2)] = 0
    analysis_skipped_cost: Annotated[
        int, ReportField("analysis", "over the cost limit: {}", order=3)
    ] = 0
    analysis_unanswered: Annotated[
        int, ReportField("analysis", "file refused, still queued: {}", order=4)
    ] = 0
    joint_revived: Annotated[int, ReportField("analysis", "joint prints revived: {}", order=5)] = 0
    notes: Annotated[list[str], ReportField(section=None, scope="all")] = Field(
        default_factory=list
    )
    rejected: Annotated[list[AnalysisVerdict], ReportField(section=None)] = Field(
        default_factory=list
    )
    commands_handled: Annotated[int, ReportField("operator activity", "commands handled: {}")] = 0
    commands_failed: Annotated[
        int, ReportField("operator activity", "commands failed: {}", order=1)
    ] = 0
    commands: Annotated[list[str], ReportField(section=None)] = Field(default_factory=list)
    published: Annotated[int, ReportField("posts", "new cards: {}")] = 0
    joint_published: Annotated[int, ReportField("posts", "alternatives: {}", order=1)] = 0
    updates: Annotated[int, ReportField("posts", "updates: {}", order=2)] = 0
    update_events: Annotated[int, ReportField("posts", "status events: {}", order=3)] = 0
    attention: Annotated[
        dict[str, int],
        ReportField(
            "attention: delivery",
            context_keys=("daily_queued", "daily_oldest_minutes"),
            separate_lines=True,
            bare_keys=True,
        ),
    ] = Field(default_factory=dict)
    reanalyzed: Annotated[int, ReportField("posts", "re-analyzed: {}", order=5)] = 0
    tracked: Annotated[int, ReportField("posts", "tracked: {}", order=7, role="context")] = 0
    batch_waiting: Annotated[
        int, ReportField("posts", "awaiting a batch answer: {}", order=8, role="context")
    ] = 0
    batch_requests_submitted: Annotated[
        int, ReportField("analysis", "batch requests submitted: {}", row=1)
    ] = 0
    batch_requests_pending: Annotated[
        int, ReportField("analysis", "batch results pending: {}", row=1, order=1, role="context")
    ] = 0
    batch_requests_queued: Annotated[
        int, ReportField("analysis", "queued for batch: {}", row=1, order=2, role="context")
    ] = 0
    batch_requests_uncertain: Annotated[
        int,
        ReportField("analysis", "batch submission unconfirmed: {}", row=1, order=3, role="context"),
    ] = 0
    errors: Annotated[list[str], ReportField(section=None, scope="all")] = Field(
        default_factory=list
    )
    llm_input_tokens: Annotated[int, ReportField(section=None)] = 0
    llm_output_tokens: Annotated[int, ReportField(section=None)] = 0
    llm_usage: Annotated[dict[str, TokenUsage], ReportField(section=None)] = Field(
        default_factory=dict
    )
    llm_calls: Annotated[list[LlmCall], ReportField(section=None)] = Field(default_factory=list)
    phase_seconds: Annotated[dict[str, float], ReportField(section=None, role="context")] = Field(
        default_factory=dict
    )
    document_reads: Annotated[
        DocumentReadMetrics | None, ReportField(section=None, scope="reads")
    ] = Field(default=None, exclude_if=lambda value: value is None)
    document_download_diagnostics: Annotated[
        DocumentDownloadDiagnostics | None, ReportField(section=None, scope="reads")
    ] = Field(default=None, exclude_if=lambda value: value is None)

    metadata_url_omissions: Annotated[
        MetadataUrlOmissions | None, ReportField(section=None, scope="reads")
    ] = Field(default=None, exclude_if=lambda value: value is None)

    document_physical_diagnostics: Annotated[
        DocumentPhysicalDiagnostics | None, ReportField(role="metadata")
    ] = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def cli_read_scope(self) -> Self:
        if (self.mode is RunMode.CLI_READS) != (self.cli_read_operation is not None):
            raise ValueError("CLI document-read reports require their operation and distinct mode")
        return self

    @property
    def execution_accounting_measured(self) -> bool:
        return self.mode is not RunMode.CLI_READS

    @property
    def ok(self) -> bool:
        return not has_report_content(self.errors)

    @property
    def attention_notifications(self) -> dict[str, int]:
        metrics = {
            key: count for key, count in sorted(self.attention.items()) if has_report_content(count)
        }
        if metrics.get("digest_queued_events", 0) <= metrics.get("digest_awaiting_approval", 0):
            for key in (
                "digest_queued_events",
                "digest_awaiting_approval",
                "digest_oldest_minutes",
            ):
                metrics.pop(key, None)
        return metrics

    @property
    def content(self) -> ReportContent:
        return report_content(
            self,
            execution=self.execution_accounting_measured,
            overrides={
                "attention": self.attention_notifications,
                "update_events": self.update_events if self.update_events != self.updates else 0,
            },
        )

    @property
    def is_empty(self) -> bool:
        return self.content.is_empty

    @property
    def duration_seconds(self) -> int | None:
        if self.finished_at is None:
            return None
        return int((self.finished_at - self.started_at).total_seconds())


class BackfillOutcome(BaseModel):
    """One bill the text-prefilter backfill looked at again, and what it decided."""

    number: str
    title: str
    accepted: bool
    hits: tuple[str, ...] = ()
    reason: str | None = None


class BackfillReport(BaseModel):
    """What `lexinform reprefilter` did, for the log channel: it is a step of its own before the
    run and writes to the same database, so nothing it does reaches the run's report."""

    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    limit: int = 0
    include_text_skipped: bool = False
    outcomes: list[BackfillOutcome] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def scanned(self) -> int:
        return len(self.outcomes)

    @property
    def accepted(self) -> list[BackfillOutcome]:
        return [o for o in self.outcomes if o.accepted]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def duration_seconds(self) -> int | None:
        if self.finished_at is None:
            return None
        return int((self.finished_at - self.started_at).total_seconds())
