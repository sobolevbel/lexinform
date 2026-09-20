"""Bills that were published before they had a print (druk) number, and the /bills entry of
every followed bill that had a public consultation.

When the RPW entry gets its number the print inherits the card (same Telegram thread, see
`Linker`); when the entry is withdrawn the thread is closed with one last update. The same
listing tells when the Sejm publishes the opinions received in a consultation
(`consultationResults`), which is announced once.
"""

import logging

from lexinform.errors import ServiceUnavailableError
from lexinform.models import Bill, BillSubmission, SourceOutcome, StatusChange, submission_evidence
from lexinform.ports import BillRepository, Clock, SejmGateway
from lexinform.services.tracking.consultations import ConsultationReminder
from lexinform.services.tracking.linking import Linker
from lexinform.services.tracking.posting import Poster
from lexinform.services.tracking.result import TrackingResult
from lexinform.services.tracking.stages import synthetic_key

log = logging.getLogger(__name__)

PRE_PRINT_MAX_DAYS = 365


class PrePrintReconciler:
    """Re-reads `/bills` for the entries we follow and reacts to what changed there.

    An entry older than `PRE_PRINT_MAX_DAYS` that `/bills` no longer lists is over: the Sejm
    gives a print within weeks, and the register keeps the rest.
    """

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

    def reconcile(self, result: TrackingResult, *, publish: bool) -> bool:
        """Link RPW entries to their print, notice withdrawal, announce published consultation
        opinions. False when the phase must stop."""
        pending = self._repo.list_pre_print()
        awaiting = [
            b
            for b in self._repo.list_awaiting_consultation_results(self._channel_id)
            if not b.is_pre_print
        ]
        rows = pending + awaiting
        if not rows:
            return True
        listed = self._listing(rows, result)
        if listed is None:
            return True
        return all(self._reconcile_one(bill, listed, result, publish=publish) for bill in rows)

    def _listing(
        self, rows: list[Bill], result: TrackingResult
    ) -> dict[tuple[int, str], BillSubmission] | None:
        """One `/bills` listing per term the followed entries belong to, by (term, number).

        None when `/bills` is down, which says nothing about the rest of the run: the Dziennik
        Ustaw notices, the reminders and the stage updates are all still due.
        """
        latest: dict[tuple[int, str], BillSubmission] = {}
        for term in sorted({b.term for b in rows}):
            of_term = [b for b in rows if b.term == term]
            earliest = min(
                (b.submission.date_of_receipt for b in of_term if b.submission),
                default=self._clock.now().date(),
            )
            try:
                for listed in self._gateway.iter_bills(term, received_from=earliest):
                    latest[(term, listed.number)] = listed
            except ServiceUnavailableError as exc:
                result.partial_errors.append(f"/bills: {exc.describe()}")
                log.error("pre-print reconciliation stopped: %s", exc.describe())
                return None
        return latest

    def _reconcile_one(
        self,
        bill: Bill,
        listed: dict[tuple[int, str], BillSubmission],
        result: TrackingResult,
        *,
        publish: bool,
    ) -> bool:
        """False when the phase must stop; a failure of this one bill is counted and passed over."""
        key = bill.submission.number if bill.submission else bill.number
        sub = listed.get((bill.term, key))
        if sub is None:
            if listed and self._long_gone(bill):
                self._announce_withdrawal(bill, result, publish=publish)
            return True
        try:
            self._apply(bill, sub, result, publish=publish)
        except ServiceUnavailableError as exc:
            result.abort(exc)
            return False
        except Exception as exc:
            result.failed += 1
            log.exception("reconciling %s failed: %s", bill.number, exc)
        return True

    def _apply(
        self, bill: Bill, sub: BillSubmission, result: TrackingResult, *, publish: bool
    ) -> None:
        """What the `/bills` row says has happened to the entry since it was stored."""
        evidence = submission_evidence(sub)
        if bill.is_pre_print and evidence.source is SourceOutcome.LINKED:
            assert sub.print_number is not None
            self._linker.link(
                bill.model_copy(update={"submission": sub}),
                sub.print_number,
                result,
                publish=publish,
            )
            return
        if (
            bill.is_pre_print
            and evidence.source is SourceOutcome.WITHDRAWN
            and not self._repo.closure_announced(bill.term, bill.number)
        ):
            self._announce_withdrawal(bill, result, publish=publish, submission=sub)
            return
        if self._results_appeared(bill, sub) and self._consultations is not None:
            news = bill.model_copy(update={"submission": sub})
            with self._repo.atomic():
                self._repo.save_submission(bill.term, bill.number, sub)
                self._consultations.prepare_results(news)
            self._consultations.results_published(news, result, publish=publish)
            return
        self._repo.save_submission(bill.term, bill.number, sub)

    @staticmethod
    def _results_appeared(bill: Bill, sub: BillSubmission) -> bool:
        before = bill.submission
        return sub.consultation_results and not (before is not None and before.consultation_results)

    def _long_gone(self, bill: Bill) -> bool:
        """An entry the Sejm has stopped listing and that is too old to still be waiting for a
        print. Without this its thread ends at "ждём номер druku" and is re-queried for years.

        Only an entry: the rows read here also include numbered prints waiting for their
        consultation results, and one whose year-old `/bills` row has dropped out of the listing
        would otherwise be announced as withdrawn in the middle of its process — and the mark
        left behind would then suppress the real closure when it came.
        """
        submission = bill.submission
        if not bill.is_pre_print:
            return False
        if submission is None or self._repo.closure_announced(bill.term, bill.number):
            return False
        age = (self._clock.now().date() - submission.date_of_receipt).days
        return age > PRE_PRINT_MAX_DAYS

    def _announce_withdrawal(
        self,
        bill: Bill,
        result: TrackingResult,
        *,
        publish: bool,
        submission: BillSubmission | None = None,
    ) -> None:
        with self._repo.atomic():
            if submission is not None:
                self._repo.save_submission(bill.term, bill.number, submission)
                bill = bill.model_copy(update={"submission": submission})
            change = self._poster.record_change(
                StatusChange(
                    term=bill.term,
                    number=bill.number,
                    old_fingerprint=bill.stages_fingerprint,
                    new_fingerprint=synthetic_key("withdrawn", bill.number),
                    new_stages=[],
                    closure_detected=True,
                    passed=False,
                    withdrawn=True,
                    detected_at=self._clock.now(),
                )
            )
            if change is None:
                return
            self._poster.prepare(bill, change)
        result.changed += 1
        log.info("%s withdrawn before getting a print number", bill.number)
        self._poster.tell(bill, change, result, publish=publish)
