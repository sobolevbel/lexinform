"""Keeping a card true after it was posted.

Everything the card says about *now* — where the bill stands, what comes next, what a reader can
still do, how many days are left — is derived from the day it was rendered, and the message was
written once. A run re-renders the card of every bill it still follows and edits it in place when
the text has drifted; the digest of what was last sent (`publications.rendered_sha256`) is what
makes that cost nothing on the runs where nothing moved.

A bill whose road has ended keeps the card it had: there is nothing left to invite, and the
replies under it tell how the story ended.
"""

import logging

from lexinform.errors import ServiceUnavailableError
from lexinform.models import Bill, BillStatus, PublicationKind, PublicationStatus, is_over
from lexinform.ports import BillRepository, Clock, Publisher, SejmGateway
from lexinform.services.sources import fetch_print
from lexinform.services.tracking.result import TrackingResult

log = logging.getLogger(__name__)


class CardRefresher:
    def __init__(
        self,
        gateway: SejmGateway,
        repo: BillRepository,
        publisher: Publisher,
        clock: Clock,
        *,
        channel_id: str,
        max_edits: int,
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._publisher = publisher
        self._clock = clock
        self._channel_id = channel_id
        self._max_edits = max_edits

    def refresh(self, bills: list[Bill], result: TrackingResult, *, publish: bool) -> None:
        """Re-render the card of every bill still running and edit the ones that have drifted.

        Each row is read again: the phases above may have re-analysed the bill, moved its stages
        or handed its thread to a successor since `bills` was listed.
        """
        if not publish:
            return
        today = self._clock.now().date()
        edited = 0
        for stale in bills:
            if edited >= self._max_edits:
                log.info("card refresh capped at %d edits", self._max_edits)
                return
            bill = self._repo.get(stale.term, stale.number)
            if bill is None or bill.analysis is None or bill.discontinued_at is not None:
                continue
            if bill.status is BillStatus.LINKED or is_over(bill, today=today):
                continue
            card = self._repo.get_publication(
                bill.term, bill.number, PublicationKind.NEW_BILL, self._channel_id
            )
            if card is None or card.status is not PublicationStatus.SENT or card.id is None:
                continue
            digest = self._publisher.card_digest(bill)
            if digest == card.rendered_sha256 or card.message_id is None:
                continue
            try:
                refreshed = self._edit(bill, card.message_id)
            except ServiceUnavailableError as exc:
                # This is the last, cosmetic step of the phase: letting it out would throw away
                # the result object with everything the phase already posted and spent.
                result.abort(exc)
                return
            if refreshed:
                self._repo.set_card_digest(card.id, digest)
                result.cards_refreshed += 1
                edited += 1

    def _edit(self, bill: Bill, message_id: int) -> bool:
        """Edit the card in place, reading the print so it keeps its link to the PDF. The digest
        is taken without the print, so a run that changes nothing costs no request; this one is
        paid only when the card really moved."""
        print_info = fetch_print(self._gateway, bill) if bill.has_process else None
        try:
            self._publisher.edit_new_bill(bill, print_info, message_id=message_id)
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.warning("card of %s not refreshed: %s: %s", bill.number, type(exc).__name__, exc)
            return False
        return True
