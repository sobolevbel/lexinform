"""Summarize saved submission intents without inferring provider or state-entry clocks."""

from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from lexinform.models.batch import BatchIntent, BatchKind
from lexinform.models.commands import IntentAgeGroup
from lexinform.saved_clock import ClockUnknown, saved_clock_age


def intent_ages(
    intents: Sequence[BatchIntent], observed_at: datetime
) -> tuple[IntentAgeGroup, ...]:
    if observed_at.utcoffset() is None:
        raise ValueError("Observed time must include a timezone")
    groups: dict[tuple[BatchKind, Literal["queued", "submitting"]], list[BatchIntent]] = {}
    for intent in intents:
        groups.setdefault((intent.request.call_kind, intent.state), []).append(intent)
    records: list[IntentAgeGroup] = []
    for (purpose, state), items in sorted(groups.items()):
        known: list[tuple[int, BatchIntent]] = []
        unknown: Counter[ClockUnknown] = Counter()
        for intent in items:
            age = saved_clock_age(intent.created_at, observed_at)
            if age.unknown is not None:
                unknown[age.unknown] += 1
            elif age.seconds is not None:
                known.append((age.seconds, intent))
        oldest = max(known, key=lambda item: item[0]) if known else None
        records.append(
            IntentAgeGroup(
                purpose=purpose,
                state=state,
                count=len(items),
                known_ages=len(known),
                unknown_ages=dict(sorted(unknown.items())),
                oldest_seconds=oldest[0] if oldest is not None else None,
                oldest_term=oldest[1].request.term if oldest is not None else None,
                oldest_number=oldest[1].request.number if oldest is not None else None,
            )
        )
    return tuple(records)
