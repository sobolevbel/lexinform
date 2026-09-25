"""What one run reports to the log channel and stores in the `runs` table."""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

from lexinform.models.analysis import TokenUsage
from lexinform.models.enums import RunMode


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


CallKind = Literal["analysis", "reanalysis", "triage", "amendments", "supplement", "joint"]


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
        return {
            self.model: TokenUsage(
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


class RunReport(BaseModel):
    """The counters one run reports to the log channel and stores in the `runs` table.

    `discovery_ok` says discovery finished, which is what lets the watermark advance.
    `over_on_arrival`: first seen with the road already over, stored and never posted.
    `text_prefilter_scans`: sent to the model unsearched, the file being paper. `*_unanswered`:
    the host would not hand the file over, which says nothing about the bill, so the row keeps
    its place in the queue. `held`: stage changes kept for the next post. `notes` are worth
    telling without being errors; `rejected` the bills analysed and not published.
    """

    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    since: dt.datetime
    mode: RunMode
    term: int | None = None
    discovery_ok: bool = False
    discovered: int = 0
    pre_print_discovered: int = 0
    rcl_discovered: int = 0
    rcl_prefilter_hits: int = 0
    wykaz_discovered: int = 0
    over_on_arrival: int = 0
    linked: int = 0
    prefilter_hits: int = 0
    prefilter_rejected: list[str] | None = None
    text_prefilter_checked: int = 0
    text_prefilter_hits: int = 0
    text_prefilter_scans: int = 0
    text_prefilter_unreadable: int = 0
    text_prefilter_unanswered: int = 0
    acts_published: int = 0
    in_force_posted: int = 0
    consultation_reminders: int = 0
    hearing_reminders: int = 0
    decision_reminders: int = 0
    held: int = 0
    cards_refreshed: int = 0
    digest_drafted: bool = False
    consultation_results_posted: int = 0
    agenda_posted: int = 0
    agenda_cancelled: int = 0
    discontinued: int = 0
    rehomed: int = 0
    analyzed: int = 0
    triaged_out: int = 0
    analysis_failures: int = 0
    analysis_skipped_cost: int = 0
    analysis_unanswered: int = 0
    joint_revived: int = 0
    notes: list[str] = Field(default_factory=list)
    rejected: list[AnalysisVerdict] = Field(default_factory=list)
    commands_handled: int = 0
    commands_failed: int = 0
    commands: list[str] = Field(default_factory=list)
    published: int = 0
    joint_published: int = 0
    updates: int = 0
    reanalyzed: int = 0
    tracked: int = 0
    batch_waiting: int = 0
    batch_requests_submitted: int = 0
    batch_requests_pending: int = 0
    batch_requests_queued: int = 0
    batch_requests_uncertain: int = 0
    errors: list[str] = Field(default_factory=list)
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_usage: dict[str, TokenUsage] = Field(default_factory=dict)
    llm_calls: list[LlmCall] = Field(default_factory=list)
    phase_seconds: dict[str, float] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def is_empty(self) -> bool:
        """Routine checks, batch waiting and timing alone are not run results."""
        return not any(
            value
            for name, value in self.model_dump().items()
            if name
            not in {
                "started_at",
                "finished_at",
                "since",
                "mode",
                "term",
                "discovery_ok",
                "tracked",
                "batch_waiting",
                "batch_requests_pending",
                "batch_requests_queued",
                "batch_requests_uncertain",
                "phase_seconds",
            }
        )

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
