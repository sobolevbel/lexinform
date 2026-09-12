"""The two constitutional terms a reader can still act inside: the Senate's 30 days (art. 121)
and the President's 21 (art. 122).

Between the Sejm's vote and the act in Dziennik Ustaw nothing happens that the stage tree records,
so the channel goes quiet for up to seven weeks — exactly over the Senate's committee stage, where
opinions are still taken, and the President's, where a veto is still possible. The card carries
the date from the moment the Sejm passes the bill; this is the reply that comes back once the term
is nearly out.

The deadline is derived, not stored, so there is no "due" query: the bills are the ones tracking
already holds, and `list_tracked` always returns those the Sejm has passed and whose act has not
appeared yet, whatever the run's `changed_since`.
"""

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from lexinform.errors import ServiceUnavailableError
from lexinform.models import Bill, Phase, PublicationKind, next_phase
from lexinform.ports import BillRepository, Clock
from lexinform.services.tracking.posting import Poster
from lexinform.services.tracking.result import TrackingResult

log = logging.getLogger(__name__)

# The phases whose deadline is a window, not a formality: someone outside the process can still
# write to the Senate's committee, and the President can still be asked not to sign.
REMINDED_PHASES = frozenset({"senate", "president"})


class DeadlineReminder:
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
        """One reply per bill and phase, `days_before` days before the term runs out."""
        today = self._today()
        for stale in bills:
            # Read again: the stage loop above is what moves a bill to the Senate or the
            # President, and these are the two phases this whole reply exists for.
            bill = self._repo.get(stale.term, stale.number)
            if bill is None:
                continue
            phase = self._due(bill, today)
            if phase is None:
                continue
            if self._poster.posted(bill, PublicationKind.DECISION_DEADLINE, ref=phase.key):
                continue
            try:
                sent = self._poster.decision_deadline(bill, phase, today=today)
                result.count_post(sent, "decision_reminders")
            except ServiceUnavailableError as exc:
                result.abort(exc, failed=True)
                return

    def _due(self, bill: Bill, today: dt.date) -> Phase | None:
        """The phase whose deadline is within the window, today included. A deadline already
        behind us is not reminded: it is counted from the Sejm's vote and so runs a few days
        early, but once it is past, saying so invites nothing."""
        phase = next_phase(bill, today=today)
        if phase is None or phase.key not in REMINDED_PHASES or phase.deadline is None:
            return None
        if today <= phase.deadline <= today + dt.timedelta(days=self._days_before):
            return phase
        return None

    def _today(self) -> dt.date:
        return self._clock.now().astimezone(self._local_tz).date()
