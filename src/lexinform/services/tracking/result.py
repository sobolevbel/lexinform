"""Counters of one tracking phase."""

import logging
from dataclasses import dataclass, field

from lexinform.errors import ServiceUnavailableError
from lexinform.models import AnalysisRecord, TokenUsage, add_usage

log = logging.getLogger(__name__)


@dataclass
class TrackingResult:
    checked: int = 0
    changed: int = 0
    linked: int = 0
    acts_published: int = 0
    in_force_posted: int = 0
    consultation_reminders: int = 0
    consultation_results: int = 0
    agenda_posted: int = 0
    reanalyzed: int = 0
    published: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usage: dict[str, TokenUsage] = field(default_factory=dict)  # per model
    fatal_error: str | None = None

    def count_post(self, ok: bool) -> None:
        if ok:
            self.published += 1
        else:
            self.failed += 1

    def count_reanalysis(self, record: AnalysisRecord) -> None:
        self.reanalyzed += 1
        self.input_tokens += record.input_tokens or 0
        self.output_tokens += record.output_tokens or 0
        add_usage(self.usage, record)

    def abort(self, exc: ServiceUnavailableError, *, failed: bool = False) -> None:
        """An external system is down: the phase stops here and the report says why."""
        if failed:
            self.failed += 1
        self.fatal_error = exc.describe()
        log.error("aborting tracking phase: %s", self.fatal_error)
