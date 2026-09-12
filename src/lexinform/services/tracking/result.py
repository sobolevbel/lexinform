"""Counters of one tracking phase."""

import logging
from dataclasses import dataclass, field
from typing import Literal

from lexinform.errors import ServiceUnavailableError
from lexinform.models import AnalysisRecord, TokenUsage, UsageRecord, add_usage

log = logging.getLogger(__name__)

PostCounter = Literal[
    "published",
    "acts_published",
    "in_force_posted",
    "consultation_reminders",
    "consultation_results_posted",
    "agenda_posted",
    "hearing_reminders",
    "decision_reminders",
]


@dataclass
class TrackingResult:
    """What the tracking phase did; copied into the run report by the pipeline.

    `published` counts status updates, `decision_reminders` the warnings that the Senate's 30
    days or the President's 21 are running out, `cards_refreshed` the cards edited in place
    because what they said had drifted, `held` the service-stage changes kept back for the next
    post, `discontinued` the bills that lapsed with the end of a term and `rehomed` the
    government's own rows carried over to the new one. `usage` is counted per model, and
    `partial_errors` names a side system that was down while the rest of the phase ran.
    """

    checked: int = 0
    changed: int = 0
    linked: int = 0
    published: int = 0
    acts_published: int = 0
    in_force_posted: int = 0
    consultation_reminders: int = 0
    consultation_results_posted: int = 0
    agenda_posted: int = 0
    hearing_reminders: int = 0
    decision_reminders: int = 0
    cards_refreshed: int = 0
    held: int = 0
    discontinued: int = 0
    rehomed: int = 0
    reanalyzed: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usage: dict[str, TokenUsage] = field(default_factory=dict)
    fatal_error: str | None = None
    partial_errors: list[str] = field(default_factory=list)

    def count_post(self, sent: bool, counter: PostCounter = "published") -> None:
        if sent:
            setattr(self, counter, getattr(self, counter) + 1)
        else:
            self.failed += 1

    def count_reanalysis(self, record: AnalysisRecord) -> None:
        self.reanalyzed += 1
        self.count_usage(record)

    def count_usage(self, record: UsageRecord) -> None:
        """Tokens of one model call (a re-analysis, an amendments summary)."""
        self.input_tokens += record.input_tokens or 0
        self.output_tokens += record.output_tokens or 0
        add_usage(self.usage, record)

    def abort(self, exc: ServiceUnavailableError, *, failed: bool = False) -> None:
        """An external system is down: the phase stops here and the report says why."""
        if failed:
            self.failed += 1
        self.fatal_error = exc.describe()
        log.error("aborting tracking phase: %s", self.fatal_error)
