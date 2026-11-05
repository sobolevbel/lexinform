"""Elapsed age from a saved clock, with explicit unknowns instead of inferred dates."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

ClockUnknown = Literal[
    "missing_timestamp", "invalid_timestamp", "timezone_unknown", "timestamp_after_observation"
]


@dataclass(frozen=True)
class SavedClockAge:
    seconds: int | None = None
    unknown: ClockUnknown | None = None


def saved_clock_age(value: object, observed_at: datetime) -> SavedClockAge:
    observed_offset = observed_at.utcoffset()
    if observed_offset is None:
        raise ValueError("Observed time must include a timezone")
    if isinstance(value, datetime):
        started = value
    elif isinstance(value, str) and value:
        try:
            started = datetime.fromisoformat(value)
        except ValueError:
            return SavedClockAge(unknown="invalid_timestamp")
    else:
        return SavedClockAge(unknown="missing_timestamp")
    started_offset = started.utcoffset()
    if started_offset is None:
        return SavedClockAge(unknown="timezone_unknown")
    elapsed = (
        observed_at.replace(tzinfo=None)
        - started.replace(tzinfo=None)
        - (observed_offset - started_offset)
    ).total_seconds()
    if elapsed < 0:
        return SavedClockAge(unknown="timestamp_after_observation")
    return SavedClockAge(seconds=int(elapsed))
