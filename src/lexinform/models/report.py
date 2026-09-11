"""What one run reports to the log channel and stores in the `runs` table."""

import datetime as dt

from pydantic import BaseModel, Field

from lexinform.models.analysis import TokenUsage


class AnalysisVerdict(BaseModel):
    """What the model said about one bill this run; the report lists the ones not published."""

    number: str
    title: str
    relevant: bool
    score: int
    triaged: bool = False  # rejected by the cheap first pass on excerpts
    term: int | None = None  # for the link to the process page (None in reports stored before)

    @property
    def reason(self) -> str:
        if self.triaged:
            return "triage"
        return f"score {self.score}" if self.relevant else "not relevant"


class RunReport(BaseModel):
    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    since: dt.datetime
    mode: str
    term: int | None = None  # the Sejm term discovery ran in (None: could not be resolved)
    discovery_ok: bool = False  # discovery finished: the watermark may advance past `started_at`
    discovered: int = 0
    pre_print_discovered: int = 0
    rcl_discovered: int = 0  # government projects first seen on RCL
    rcl_prefilter_hits: int = 0
    linked: int = 0
    prefilter_hits: int = 0
    text_prefilter_checked: int = 0
    text_prefilter_hits: int = 0
    text_prefilter_unreadable: int = 0  # skipped without a text: no file, no text layer, failed
    acts_published: int = 0
    in_force_posted: int = 0
    consultation_reminders: int = 0
    hearing_reminders: int = 0
    held: int = 0  # stage changes held for the next post (service stages only)
    consultation_results_posted: int = 0
    agenda_posted: int = 0  # "the bill is on the agenda of a sitting" notices
    discontinued: int = 0  # bills that lapsed with the end of a term, announced under their card
    rcl_rehomed: int = 0  # RCL projects carried over to the new term
    analyzed: int = 0
    triaged_out: int = 0  # rejected by the cheap first pass, no full analysis
    analysis_failures: int = 0
    analysis_skipped_cost: int = 0  # texts over the per-bill cost limit, not sent to the model
    notes: list[str] = Field(default_factory=list)  # worth telling, not an error (a budget stop)
    rejected: list[AnalysisVerdict] = Field(default_factory=list)  # analysed, not published
    commands_handled: int = 0  # operator commands answered (from the technical channel)
    commands_failed: int = 0
    commands: list[str] = Field(default_factory=list)  # one line per command: what happened
    published: int = 0
    joint_published: int = 0  # "alternative bill" replies under the card of a joint print
    updates: int = 0
    reanalyzed: int = 0
    tracked: int = 0
    errors: list[str] = Field(default_factory=list)
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_usage: dict[str, TokenUsage] = Field(default_factory=dict)  # per model, for the cost line
    phase_seconds: dict[str, float] = Field(default_factory=dict)  # wall time per phase

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def duration_seconds(self) -> int | None:
        if self.finished_at is None:
            return None
        return int((self.finished_at - self.started_at).total_seconds())
