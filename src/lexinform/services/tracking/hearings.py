"""Public hearings (wysłuchanie publiczne): the reminder before applications close.

A committee announces the hearing at least 14 days ahead; anyone who wants to speak applies at
least 10 days before it (Regulamin Sejmu art. 70a–70i). The stage update names the deadline;
this reminder repeats it `days_before` days ahead, once per hearing.
"""

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    Bill,
    PublicationKind,
    PublicationStatus,
    Stage,
    flatten_stages,
    hearing_application_deadline,
)
from lexinform.ports import Clock
from lexinform.services.tracking.posting import Poster
from lexinform.services.tracking.result import TrackingResult

log = logging.getLogger(__name__)


class HearingReminder:
    def __init__(
        self, clock: Clock, poster: Poster, *, local_tz: ZoneInfo, days_before: int
    ) -> None:
        self._clock = clock
        self._poster = poster
        self._local_tz = local_tz
        self._days_before = days_before

    def remind(self, bills: list[Bill], result: TrackingResult) -> None:
        """One reply per hearing whose application deadline is `days_before` days away or less
        and not over (Warsaw time)."""
        today = self._clock.now().astimezone(self._local_tz).date()
        for bill in bills:
            card = self._poster.card(bill)
            if card is None or card.status is not PublicationStatus.SENT:
                continue
            for hearing in _due_hearings(bill, today, self._days_before):
                assert hearing.date is not None
                if self._poster.posted(
                    bill, PublicationKind.HEARING_DEADLINE, ref=hearing.date.isoformat()
                ):
                    continue
                try:
                    sent = self._poster.hearing_deadline(bill, hearing, today=today)
                    result.count_post(sent, "hearing_reminders")
                except ServiceUnavailableError as exc:
                    result.abort(exc, failed=True)
                    return


def _due_hearings(bill: Bill, today: dt.date, days_before: int) -> list[Stage]:
    due: list[Stage] = []
    for stage in flatten_stages(bill.stages):
        deadline = hearing_application_deadline(stage)
        if deadline is not None and 0 <= (deadline - today).days <= days_before:
            due.append(stage)
    return due
