"""Public hearings (wysłuchanie publiczne): the reminder before applications close.

A committee announces the hearing at least 14 days ahead; anyone who wants to speak applies at
least 10 days before it (Regulamin Sejmu art. 70a–70i). The stage update names the deadline;
this reminder repeats it `days_before` days ahead, once per hearing.
"""

import logging
from zoneinfo import ZoneInfo

from lexinform.errors import ServiceUnavailableError
from lexinform.models import Bill, PublicationKind, PublicationStatus, hearings_due
from lexinform.ports import BillRepository, Clock
from lexinform.services.tracking.posting import Poster
from lexinform.services.tracking.result import TrackingResult

log = logging.getLogger(__name__)


class HearingReminder:
    def __init__(
        self,
        repo: BillRepository,
        clock: Clock,
        poster: Poster,
        *,
        local_tz: ZoneInfo,
        days_before: int,
    ) -> None:
        self._repo = repo
        self._clock = clock
        self._poster = poster
        self._local_tz = local_tz
        self._days_before = days_before

    def remind(self, bills: list[Bill], result: TrackingResult) -> None:
        """One reply per hearing whose application deadline is `days_before` days away or less
        and not over (Warsaw time).

        Each row is read again, because the stage loop that ran before this is what puts the
        hearing on the bill; reading the list as it was loaded made the reminder a run late.
        """
        today = self._clock.now().astimezone(self._local_tz).date()
        for stale in bills:
            bill = self._repo.get(stale.term, stale.number)
            if bill is None:
                continue
            card = self._poster.card(bill)
            if card is None or card.status is not PublicationStatus.SENT:
                continue
            for hearing in hearings_due(bill, today, days_before=self._days_before):
                assert hearing.date is not None
                ref = hearing.date.isoformat()
                if self._poster.posted(bill, PublicationKind.HEARING_DEADLINE, ref=ref):
                    continue
                if self._poster.told_jointly(bill, PublicationKind.HEARING_DEADLINE, ref):
                    self._poster.record(
                        bill, PublicationKind.HEARING_DEADLINE, PublicationStatus.SKIPPED, ref=ref
                    )
                    continue
                try:
                    sent = self._poster.hearing_deadline(bill, hearing, today=today)
                    result.count_post(sent, "hearing_reminders")
                except ServiceUnavailableError as exc:
                    result.abort(exc, failed=True)
                    return
