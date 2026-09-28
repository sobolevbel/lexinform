from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")


class ParticipationState(StrEnum):
    UNCONFIRMED = "unconfirmed"
    UPCOMING = "upcoming"
    OPEN = "open"
    LAST_DAY = "last_day"
    CLOSED = "closed"


@dataclass(frozen=True)
class ParticipationWindow:
    """A consultation window as sourced; `confirmed` is false for stale or unverified data."""

    start: date | None
    end: date | None
    confirmed: bool
    basis: str = ""


@dataclass(frozen=True)
class ParticipationDisplay:
    state: ParticipationState
    window: ParticipationWindow
    today: date

    @property
    def is_action(self) -> bool:
        """Only a confirmed window the reader can still use gets the action rule."""
        return self.state in (ParticipationState.OPEN, ParticipationState.LAST_DAY)

    @property
    def days_left(self) -> int | None:
        if not self.is_action or self.window.end is None:
            return None
        return (self.window.end - self.today).days


def participation_display(window: ParticipationWindow, now: datetime) -> ParticipationDisplay:
    """The state on the Warsaw day of `now`; the end date is inclusive, as in the bot."""
    assert now.tzinfo is not None, "Clock.now() is timezone-aware by contract"
    today = now.astimezone(WARSAW).date()
    end = window.end
    if not window.confirmed or end is None:
        state = ParticipationState.UNCONFIRMED
    elif window.start is not None and today < window.start:
        state = ParticipationState.UPCOMING
    elif today < end:
        state = ParticipationState.OPEN
    elif today == end:
        state = ParticipationState.LAST_DAY
    else:
        state = ParticipationState.CLOSED
    return ParticipationDisplay(state=state, window=window, today=today)
