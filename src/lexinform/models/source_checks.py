"""How a bill is observed and when each of its aspects was last checked successfully."""

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel

from lexinform.models.analysis import Analysis

# docs/website/data.md §25.6: data that must be rechecked regularly is stale after 36 hours.
FRESH_FOR = dt.timedelta(hours=36)


class ObservationMode(StrEnum):
    FULL = "full"
    METADATA = "metadata"
    OFF = "off"


class ObservationBasis(StrEnum):
    TELEGRAM_THREAD = "telegram_thread"
    RELEVANT_ANALYSIS = "relevant_analysis"
    NOT_RELEVANT = "not_relevant"
    BELOW_BAR = "below_bar"
    OPERATOR = "operator"


class CheckAspect(StrEnum):
    PROCESS = "process"
    CONSULTATION = "consultation"
    TEXT = "text"
    ACT = "act"


class Freshness(StrEnum):
    UNKNOWN = "unknown"
    FRESH = "fresh"
    STALE = "stale"
    FAILING = "failing"
    OUTAGE = "outage"


class SourceCheck(BaseModel):
    """One aspect of one bill; a success is never inferred from a listing that did not return it."""

    aspect: CheckAspect
    last_success_at: dt.datetime | None = None
    last_attempt_at: dt.datetime | None = None
    failures: int = 0
    last_outage_at: dt.datetime | None = None

    def freshness(self, now: dt.datetime) -> Freshness:
        if self.last_success_at is None:
            return Freshness.UNKNOWN
        if now - self.last_success_at <= FRESH_FOR:
            return Freshness.FRESH
        if self.last_outage_at is not None and self.last_outage_at > self.last_success_at:
            return Freshness.OUTAGE
        return Freshness.FAILING if self.failures else Freshness.STALE


def observation_for(
    analysis: Analysis | None,
    *,
    has_card: bool,
    current: ObservationBasis | None,
    min_score: int,
) -> tuple[ObservationMode, ObservationBasis] | None:
    """The mode a bill should be observed in; an operator's choice is never overwritten."""
    if current is ObservationBasis.OPERATOR:
        return None
    if has_card:
        return ObservationMode.FULL, ObservationBasis.TELEGRAM_THREAD
    if analysis is None:
        return None
    # More than a point under the bar no new text lifts a bill to a card, so re-reading it only pays.
    if analysis.relevant and analysis.score >= min_score - 1:
        return ObservationMode.FULL, ObservationBasis.RELEVANT_ANALYSIS
    if analysis.relevant:
        return ObservationMode.METADATA, ObservationBasis.BELOW_BAR
    return ObservationMode.METADATA, ObservationBasis.NOT_RELEVANT


AUTO_OBSERVATION = "auto"
"""`/observe BILL mode=auto`: drop the operator's choice and let `observation_for` decide again."""


def operator_observation(
    choice: str, analysis: Analysis | None, *, has_card: bool, min_score: int
) -> tuple[ObservationMode | None, ObservationBasis | None]:
    """What `/observe` stores: a mode pinned by the operator, or for `auto` the rule's own."""
    if choice != AUTO_OBSERVATION:
        return ObservationMode(choice), ObservationBasis.OPERATOR
    rule = observation_for(analysis, has_card=has_card, current=None, min_score=min_score)
    return rule or (None, None)


def observation_label(mode: ObservationMode | None, basis: ObservationBasis | None) -> str:
    """`metadata (operator)`, or `undecided` for a row the rule has not reached yet."""
    if mode is None:
        return "undecided"
    return f"{mode} ({basis})" if basis is not None else str(mode)
