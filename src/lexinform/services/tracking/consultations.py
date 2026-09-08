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

    def results_published(self, bill: Bill, result: TrackingResult, *, publish: bool) -> None:
        """The /bills entry now says `consultationResults`: one reply under the card."""
        card = self._poster.card(bill)
        if card is None or card.status is not PublicationStatus.SENT:
            return
        if not publish or self._poster.posted(bill, PublicationKind.CONSULTATION_RESULTS):
            return
        if self._poster.one_off(bill, PublicationKind.CONSULTATION_RESULTS):
            result.consultation_results += 1
        else:
            result.failed += 1

    def remind(self, term: int, result: TrackingResult) -> None:
        today = self._clock.now().astimezone(self._local_tz).date()
        due = self._repo.list_due_consultations(
            term, self._channel_id, today=today, days_before=self._days_before
        )
        for bill in due:
            if self._poster.posted(bill, PublicationKind.CONSULTATION_DEADLINE):
                continue
            try:
                if self._poster.one_off(bill, PublicationKind.CONSULTATION_DEADLINE, today=today):
                    result.consultation_reminders += 1
                else:
                    result.failed += 1
            except ServiceUnavailableError as exc:
                result.abort(exc, failed=True)
                return
