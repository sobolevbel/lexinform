"""Public consultations after the card: the reminder a few days before they close (the moment
readers can still send an opinion, which is the point of the whole channel) and the notice that
the Sejm published the opinions received."""

import logging
from zoneinfo import ZoneInfo

from lexinform.errors import ServiceUnavailableError
from lexinform.models import Bill, PublicationKind, PublicationStatus
from lexinform.ports import BillRepository, Clock
from lexinform.services.tracking.posting import Poster
from lexinform.services.tracking.result import TrackingResult

log = logging.getLogger(__name__)


class ConsultationReminder:
    """The two consultation replies: the deadline reminder and the "opinions published" notice."""

    def __init__(
        self,
        repo: BillRepository,
        clock: Clock,
        poster: Poster,
        *,
        channel_id: str,
        local_tz: ZoneInfo,
        days_before: int,
    ) -> None:
        self._repo = repo
        self._clock = clock
        self._poster = poster
        self._channel_id = channel_id
        self._local_tz = local_tz
        self._days_before = days_before

    def results_published(self, bill: Bill, result: TrackingResult, *, publish: bool) -> bool:
        """The entry now says the opinions are published: one reply under the card.

        False only when the post was attempted and failed: the caller then keeps its stored copy
        of the entry, so that the flip is detected again on the next run and the post retried
        (a `failed` row alone would not do: the trigger is the difference to the stored copy)."""
        card = self._poster.card(bill)
        if card is None or card.status is not PublicationStatus.SENT:
            return True
        if self._poster.posted(bill, PublicationKind.CONSULTATION_RESULTS):
            return True
        if not publish:
            kind = PublicationKind.CONSULTATION_RESULTS
            self._poster.record(bill, kind, PublicationStatus.SKIPPED)
            return True
        sent = self._poster.consultation_results(bill)
        result.count_post(sent, "consultation_results_posted")
        return sent

    def remind(self, result: TrackingResult) -> None:
        """One reply `days_before` days before a consultation closes (Warsaw time)."""
        today = self._clock.now().astimezone(self._local_tz).date()
        due = self._repo.list_due_consultations(
            self._channel_id, today=today, days_before=self._days_before
        )
        for bill in due:
            if self._poster.posted(bill, PublicationKind.CONSULTATION_DEADLINE):
                continue
            try:
                sent = self._poster.consultation_deadline(bill, today=today)
                result.count_post(sent, "consultation_reminders")
            except ServiceUnavailableError as exc:
                result.abort(exc, failed=True)
                return
