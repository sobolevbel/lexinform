"""What one run reports to the log channel and stores in the `runs` table."""

import datetime as dt

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


class RunReport(BaseModel):
    """The counters one run reports to the log channel and stores in the `runs` table.

    `discovery_ok` says discovery finished, which is what lets the watermark advance past
    `started_at`. `wykaz_backlog` counts older register entries that match the keywords and were
    left unfollowed (`scan --since` takes them), `over_on_arrival` bills first seen with their
    road already over — the act published, the bill rejected or withdrawn, a project dropped on
    RCL, a plan realised or taken off the wykaz — which are stored and never posted.
    `text_prefilter_unreadable` counts bills skipped without a text at all: no file, no text
    layer, or a download that failed. `held` counts stage changes kept back for the next post
    (service stages only), `rehomed` the government's own rows carried over to a new Sejm term,
    `joint_published` the "alternative bill" replies under the card of a jointly considered
    print. `notes` are worth telling without being errors (a cost budget that stopped a phase),
    `commands` one line per operator command, `rejected` the bills that were analysed and not
    published.
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
    wykaz_backlog: int = 0
    over_on_arrival: int = 0
    linked: int = 0
    prefilter_hits: int = 0
    text_prefilter_checked: int = 0
    text_prefilter_hits: int = 0
    text_prefilter_unreadable: int = 0
    acts_published: int = 0
    in_force_posted: int = 0
    consultation_reminders: int = 0
    hearing_reminders: int = 0
    decision_reminders: int = 0
    held: int = 0
    cards_refreshed: int = 0
    consultation_results_posted: int = 0
    agenda_posted: int = 0
    discontinued: int = 0
    rehomed: int = 0
    analyzed: int = 0
    triaged_out: int = 0
    analysis_failures: int = 0
    analysis_skipped_cost: int = 0
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
    errors: list[str] = Field(default_factory=list)
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_usage: dict[str, TokenUsage] = Field(default_factory=dict)
    phase_seconds: dict[str, float] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def duration_seconds(self) -> int | None:
        if self.finished_at is None:
            return None
        return int((self.finished_at - self.started_at).total_seconds())
