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
]


@dataclass
class TrackingResult:
    """What the tracking phase did; copied into the run report by the pipeline."""

    checked: int = 0
    changed: int = 0
    linked: int = 0
    published: int = 0  # status updates
    acts_published: int = 0
    in_force_posted: int = 0
    consultation_reminders: int = 0
    consultation_results_posted: int = 0
    agenda_posted: int = 0
    hearing_reminders: int = 0
    held: int = 0  # service-stage changes kept for the next post
    discontinued: int = 0  # bills that lapsed with the end of the term, announced under the card
    rehomed: int = 0  # the government's own rows (RCL, wykaz) carried over to the new term
    reanalyzed: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usage: dict[str, TokenUsage] = field(default_factory=dict)  # per model
    fatal_error: str | None = None
    partial_errors: list[str] = field(default_factory=list)  # a side system down; the rest ran

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
