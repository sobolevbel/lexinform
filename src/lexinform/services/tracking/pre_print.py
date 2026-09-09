"""Bills that were published before they had a print (druk) number, and the /bills entry of
every followed bill that had a public consultation.

When the RPW entry gets its number the print inherits the card (same Telegram thread, see
`Linker`); when the entry is withdrawn the thread is closed with one last update. The same
listing tells when the Sejm publishes the opinions received in a consultation
(`consultationResults`), which is announced once.
"""

import hashlib
import logging

from lexinform.errors import ServiceUnavailableError
from lexinform.models import Bill, BillSubmission, StatusChange
from lexinform.ports import BillRepository, Clock, SejmGateway
from lexinform.services.tracking.consultations import ConsultationReminder
from lexinform.services.tracking.linking import Linker
from lexinform.services.tracking.posting import Poster
from lexinform.services.tracking.result import TrackingResult

log = logging.getLogger(__name__)


class PrePrintReconciler:
    """Re-reads `/bills` for the entries we follow and reacts to what changed there."""

    def __init__(
        self,
        gateway: SejmGateway,
        repo: BillRepository,
        clock: Clock,
        poster: Poster,
        linker: Linker,
        *,
        channel_id: str,
        consultations: ConsultationReminder | None = None,
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._clock = clock
        self._poster = poster
        self._linker = linker
        self._channel_id = channel_id
        self._consultations = consultations

    def reconcile(self, term: int, result: TrackingResult, *, publish: bool) -> bool:
        """Link RPW entries to their print, notice withdrawal, announce published consultation
        opinions. False when the phase must stop."""
        pending = self._repo.list_pre_print(term)
        awaiting = [
            b
            for b in self._repo.list_awaiting_consultation_results(term, self._channel_id)
            if not b.is_pre_print
        ]
        if not pending and not awaiting:
            return True
        earliest = min(
            (b.submission.date_of_receipt for b in pending + awaiting if b.submission),
            default=self._clock.now().date(),
        )
        try:
            latest = {
                sub.number: sub for sub in self._gateway.iter_bills(term, received_from=earliest)
            }
        except ServiceUnavailableError as exc:
            result.abort(exc)
            return False
        for bill in pending + awaiting:
            key = bill.submission.number if bill.submission else bill.number
            sub = latest.get(key)
            if sub is None:
                continue
            try:
                self._repo.save_submission(term, bill.number, sub)
                if bill.is_pre_print and sub.print_number:
                    self._linker.link(bill, sub.print_number, result, publish=publish)
                elif (
                    bill.is_pre_print
                    and sub.is_closed
                    and not self._repo.closure_announced(term, bill.number)
                ):
                    self._announce_withdrawal(bill, result, publish=publish)
                elif self._results_appeared(bill, sub) and self._consultations is not None:
                    fresh = self._repo.get(term, bill.number) or bill
                    self._consultations.results_published(fresh, result, publish=publish)
            except ServiceUnavailableError as exc:
                result.abort(exc)
                return False
            except Exception as exc:
                result.failed += 1
                log.exception("reconciling %s failed: %s", bill.number, exc)
        return True

    @staticmethod
    def _results_appeared(bill: Bill, sub: BillSubmission) -> bool:
        """`consultationResults` flipped to true since the stored copy of the entry."""
        before = bill.submission
        return sub.consultation_results and not (before is not None and before.consultation_results)

    def _announce_withdrawal(self, bill: Bill, result: TrackingResult, *, publish: bool) -> None:
        """Close the thread of an RPW entry that was withdrawn before getting a print number."""
        change = StatusChange(
            term=bill.term,
            number=bill.number,
            old_fingerprint=bill.stages_fingerprint,
            new_fingerprint=hashlib.sha256(f"withdrawn|{bill.number}".encode()).hexdigest(),
            new_stages=[],
            closure_detected=True,
            passed=False,
            withdrawn=True,
            detected_at=self._clock.now(),
        )
        change_id = self._repo.add_status_change(change)
        if change_id is None:
            return
        change.id = change_id
        result.changed += 1
        log.info("%s withdrawn before getting a print number", bill.number)
        if publish:
            result.count_post(self._poster.status_update(bill, change))
