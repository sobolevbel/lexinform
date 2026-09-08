"""After the Sejm: the act in Dziennik Ustaw (ELI API) and the day it enters into force."""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from lexinform.errors import ServiceUnavailableError
from lexinform.models import Bill, ProcessDetail, PublicationKind, PublicationStatus
from lexinform.ports import BillRepository, Clock, EliGateway
from lexinform.services.tracking.posting import Poster
from lexinform.services.tracking.result import TrackingResult

log = logging.getLogger(__name__)


class ActWatcher:
    def __init__(
        self,
        eli: EliGateway | None,
        repo: BillRepository,
        clock: Clock,
        poster: Poster,
        *,
        channel_id: str,
        local_tz: ZoneInfo,
        in_force_reminders: bool,
    ) -> None:
        self._eli = eli
        self._repo = repo
        self._clock = clock
        self._poster = poster
        self._channel_id = channel_id
        self._local_tz = local_tz
        self._in_force_reminders = in_force_reminders

    def check(
        self, bill: Bill, detail: ProcessDetail, result: TrackingResult, *, publish: bool
    ) -> None:
        """Once the process carries an ELI, fetch the act and announce the publication once."""
        if self._eli is None or not detail.eli:
            return
        act = bill.act
        if act is None or (act.entry_into_force is None and detail.eli == act.eli):
            fetched = self._eli.get_act(detail.eli)
            if fetched is None:
                log.info("druk %s: act %s not in the ELI API yet", bill.number, detail.eli)
                return
            if act is not None and fetched.entry_into_force is None:
                return  # nothing new
            act = fetched
            self._repo.save_act(bill.term, bill.number, act)
            log.info(
                "druk %s published as %s, in force %s",
                bill.number,
                act.display_address,
                act.entry_into_force,
            )
        if not publish or self._poster.posted(bill, PublicationKind.ACT_PUBLISHED):
            return
        fresh = self._repo.get(bill.term, bill.number) or bill
        if self._poster.one_off(fresh, PublicationKind.ACT_PUBLISHED):
            result.acts_published += 1
        else:
            result.failed += 1

    def remind_in_force(self, term: int, result: TrackingResult) -> None:
        """One reply on the day the act enters into force (Warsaw time)."""
        if not self._in_force_reminders:
            return
        today = self._clock.now().astimezone(self._local_tz).date()
        for bill in self._repo.list_due_in_force(term, self._channel_id, today=today):
            act = bill.act
            if act is None or act.entry_into_force is None:
                continue
            if act.already_in_force_when_fetched:
                # Discovered late: the publication notice already said "in force since ...".
                self._poster.record(bill, PublicationKind.IN_FORCE, PublicationStatus.SKIPPED)
                continue
            if self._poster.posted(bill, PublicationKind.IN_FORCE):
                continue
            try:
                if self._poster.one_off(bill, PublicationKind.IN_FORCE):
                    result.in_force_posted += 1
                else:
                    result.failed += 1
            except ServiceUnavailableError as exc:
                result.abort(exc, failed=True)
                return
