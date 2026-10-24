"""Keep followed cards true with immutable physical editions."""

import logging
from functools import partial
from zoneinfo import ZoneInfo

from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    Bill,
    BillStatus,
    PublicationKind,
    PublicationStatus,
)
from lexinform.ports import BillRepository, Clock, Publisher, SejmGateway, StateCheckpoint
from lexinform.services.card_edits import edit_card
from lexinform.services.publications import open_card_edit_ids
from lexinform.services.sources import fetch_print
from lexinform.services.tracking_result import TrackingResult

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
        checkpoint: StateCheckpoint | None = None,
        local_tz: ZoneInfo = ZoneInfo("Europe/Warsaw"),
        delivery_attempts: set[int] | None = None,
    ) -> None:
        self._clock = clock
        self._gateway = gateway
        self._repo = repo
        self._publisher = publisher
        self._channel_id = channel_id
        self._max_edits = max_edits
        self._checkpoint = checkpoint
        self._local_tz = local_tz
        self._attempted = delivery_attempts if delivery_attempts is not None else set[int]()

    def refresh(self, bills: list[Bill], result: TrackingResult, *, publish: bool) -> None:
        """Only confirmed physical edits advance the logical card digest."""
        if not publish:
            return
        edited = 0
        for stale in bills:
            if edited >= self._max_edits:
                log.info("card refresh capped at %d edits", self._max_edits)
                return
            bill = self._repo.get(stale.term, stale.number)
            if bill is None or bill.analysis is None or bill.status is BillStatus.LINKED:
                continue
            card = self._repo.get_publication(
                bill.term, bill.number, PublicationKind.NEW_BILL, self._channel_id
            )
            if card is None or card.status is not PublicationStatus.SENT or card.id is None:
                continue
            if open_card_edit_ids(self._repo, card):
                continue
            today = self._clock.now().astimezone(self._local_tz).date()
            digest = self._publisher.card_digest(bill, today=today)
            if digest == card.rendered_sha256 or card.message_id is None:
                continue
            try:
                outcome = edit_card(
                    self._repo,
                    self._clock,
                    self._publisher,
                    bill,
                    card,
                    None,
                    channel_id=self._channel_id,
                    checkpoint=self._checkpoint,
                    local_tz=self._local_tz,
                    digest=digest,
                    today=today,
                    delivery_attempts=self._attempted,
                    load_print=partial(fetch_print, self._gateway, bill)
                    if bill.has_process
                    else None,
                )
            except ServiceUnavailableError as exc:
                result.abort(exc)
                return
            result.partial_errors.extend(outcome.errors)
            result.attention["continuation_messages"] = (
                result.attention.get("continuation_messages", 0) + outcome.new_messages
            )
            if outcome.completed:
                result.cards_refreshed += 1
                edited += 1
