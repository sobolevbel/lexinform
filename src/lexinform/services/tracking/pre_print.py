"""Bills that were published before they had a print (druk) number.

When the RPW entry gets its number the print inherits the card (same Telegram thread); when the
entry is withdrawn the thread is closed with one last update.
"""

from __future__ import annotations

import hashlib
import logging

from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    Bill,
    Publication,
    PublicationKind,
    PublicationStatus,
    StatusChange,
    diff_stages,
    stage_fingerprint,
)
from lexinform.ports import BillRepository, Clock, SejmGateway
from lexinform.services.analysis import AnalysisService
from lexinform.services.tracking.posting import Poster
from lexinform.services.tracking.result import TrackingResult
from lexinform.services.tracking.stages import StageEnricher, change_key, fetch_print

log = logging.getLogger(__name__)


class PrePrintReconciler:
    def __init__(
        self,
        gateway: SejmGateway,
        repo: BillRepository,
        clock: Clock,
        poster: Poster,
        enricher: StageEnricher,
        *,
        channel_id: str,
        analysis: AnalysisService | None,
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._clock = clock
        self._poster = poster
        self._enricher = enricher
        self._channel_id = channel_id
        self._analysis = analysis

    def reconcile(self, term: int, result: TrackingResult, *, publish: bool) -> bool:
        """Link RPW entries to their print, or notice withdrawal. False when the phase must stop."""
        pending = self._repo.list_pre_print(term)
        if not pending:
            return True
        earliest = min(
            (b.submission.date_of_receipt for b in pending if b.submission),
            default=self._clock.now().date(),
        )
        try:
            latest = {
                sub.number: sub for sub in self._gateway.iter_bills(term, received_from=earliest)
            }
        except ServiceUnavailableError as exc:
            result.abort(exc)
            return False
        for bill in pending:
            sub = latest.get(bill.number)
            if sub is None:
                continue
            try:
                self._repo.save_submission(term, bill.number, sub)
                if sub.print_number:
                    self._link(bill, sub.print_number, result, publish=publish)
                elif sub.is_closed and not self._repo.closure_announced(term, bill.number):
                    self._announce_withdrawal(bill, result, publish=publish)
            except ServiceUnavailableError as exc:
                result.abort(exc)
                return False
            except Exception as exc:
                result.failed += 1
                log.exception("reconciling %s failed: %s", bill.number, exc)
        return True

    def _link(self, pre: Bill, print_number: str, result: TrackingResult, *, publish: bool) -> None:
        """The RPW entry got a print number: continue under the print, in the same thread."""
        now = self._clock.now()
        detail = self._gateway.get_process(pre.term, print_number)
        self._repo.upsert_summary(detail, now=now)
        self._repo.save_stages(
            pre.term, print_number, detail.stages, stage_fingerprint(detail.stages)
        )
        self._repo.set_status(pre.term, print_number, pre.status, prefilter_hits=pre.prefilter_hits)
        if pre.analysis is not None:
            self._repo.save_analysis(pre.term, print_number, pre.analysis)
        if pre.submission is not None:
            self._repo.save_submission(pre.term, print_number, pre.submission)
        self._repo.link_bills(pre.term, pre.number, print_number)
        result.linked += 1
        log.info("%s became druk %s", pre.number, print_number)

        card = self._poster.card(pre)
        if card is None or card.status is not PublicationStatus.SENT:
            return  # never posted: the print goes through the normal publishing path
        # The card stays the thread root: the print inherits it instead of getting a second card.
        pub_id = self._repo.create_publication(
            Publication(
                term=pre.term,
                number=print_number,
                kind=PublicationKind.NEW_BILL,
                status=PublicationStatus.SENT,
                channel_id=self._channel_id,
                message_id=card.message_id,
                created_at=now,
            )
        )
        self._repo.mark_publication(pub_id, PublicationStatus.SENT, message_id=card.message_id)

        bill = self._repo.get(pre.term, print_number)
        if bill is None:
            return
        content_changed = False
        if self._analysis is not None and bill.analysis is not None:
            print_info = fetch_print(self._gateway, bill)
            document = self._analysis.newer_document(bill, detail, print_info)
            if document is not None:
                result.count_reanalysis(self._analysis.reanalyze_bill(bill, detail, document))
                content_changed = True
        fresh = self._repo.get(pre.term, print_number) or bill
        new_stages = [self._enricher.enrich(pre.term, st) for st in diff_stages((), detail.stages)]
        change = StatusChange(
            term=pre.term,
            number=print_number,
            old_fingerprint=pre.number,
            new_fingerprint=change_key(stage_fingerprint(detail.stages), fresh, closed=False),
            new_stages=new_stages,
            passed=detail.passed,
            content_changed=content_changed,
            detected_at=now,
        )
        change_id = self._repo.add_status_change(change)
        if change_id is None:
            return
        change.id = change_id
        result.changed += 1
        if publish:
            result.count_post(self._poster.status_update(fresh, change))

    def _announce_withdrawal(self, bill: Bill, result: TrackingResult, *, publish: bool) -> None:
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
