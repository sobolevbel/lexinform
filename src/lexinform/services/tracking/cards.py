"""Keeping a card true after it was posted.

Everything the card says about *now* is derived from the day it was rendered, and the message was
written once. A run re-renders every followed bill's card and edits it where the text has
drifted; the digest of what was last sent (`publications.rendered_sha256`) makes a quiet run free.

A card that outlives its bill is re-rendered one last time and then left alone — the digest is
what makes that true, a finished card being stable. Refusing to render it at all kept a rejected
bill's card inviting opinions to a committee for ever.
"""

import logging
from functools import partial

from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    Bill,
    BillStatus,
    DeliveryPlan,
    Publication,
    PublicationKind,
    PublicationStatus,
)
from lexinform.ports import BillRepository, Clock, Publisher, SejmGateway
from lexinform.services.message_parts import continuation_snapshot, send_continuations
from lexinform.services.publications import send_publication
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
    ) -> None:
        self._clock = clock
        self._gateway = gateway
        self._repo = repo
        self._publisher = publisher
        self._channel_id = channel_id
        self._max_edits = max_edits

    def refresh(self, bills: list[Bill], result: TrackingResult, *, publish: bool) -> None:
        """Re-render the card of every followed bill and edit the ones that have drifted.

        Each row is read again: the phases above may have re-analysed the bill, moved its stages
        or handed its thread to a successor since `bills` was listed. Drift is the only test — a
        second one on `next_phase` fired a run before the card would have said the road ended. A
        `LINKED` row is skipped, its card belonging to the successor.

        An outage is caught rather than raised: this is the last, cosmetic step of the phase, and
        letting it out would throw away everything the phase already posted and spent.
        """
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
            digest = self._publisher.card_digest(bill)
            if digest == card.rendered_sha256 or card.message_id is None:
                continue
            try:
                if len(self._repo.card_refresh_parts(card.id)) > 1:
                    refreshed = self._edit_parts(bill, card, digest, result)
                else:
                    refreshed = self._edit(bill, card.message_id)
            except ServiceUnavailableError as exc:
                result.abort(exc)
                return
            if refreshed:
                self._repo.set_card_digest(card.id, digest)
                result.cards_refreshed += 1
                edited += 1

    def _edit_parts(
        self, bill: Bill, card: Publication, digest: str, result: TrackingResult
    ) -> bool:
        assert card.id is not None
        current = self._repo.card_refresh_parts(card.id)
        if not current or any(
            part.status is not PublicationStatus.SENT or part.message_id is None for part in current
        ):
            return False
        if current[0].rendered_sha256 == digest:
            return True
        print_info = fetch_print(self._gateway, bill) if bill.has_process else None
        texts = self._publisher.render_card_parts(bill, print_info, minimum=len(current))
        identities: list[int] = []
        snapshot = continuation_snapshot(bill)
        with self._repo.atomic():
            for index, text in enumerate(texts):
                identity = self._repo.create_publication(
                    Publication(
                        term=bill.term,
                        number=bill.number,
                        channel_id=card.channel_id,
                        kind=PublicationKind.MESSAGE_PART,
                        status=PublicationStatus.QUEUED,
                        created_at=self._clock.now(),
                        ref=f"card/{card.id}/{digest}-{current[0].id}/{index + 1}",
                        delivery=DeliveryPlan(
                            bill_json=snapshot,
                            rendered_text=text,
                            edit_message_id=current[index].message_id
                            if index < len(current)
                            else None,
                            continuation_parent=identities[-1] if identities else card.id,
                            reply_to=card.message_id if not identities else None,
                            continuation_root=identities[0] if identities else None,
                            continuation_index=index + 1,
                        ),
                    )
                )
                identities.append(identity)
            root = self._repo.publication_by_id(identities[0])
            assert root is not None and root.delivery is not None
            plan = root.delivery.model_copy(update={"continuation_ids": tuple(identities[1:])})
            assert self._repo.replace_unattempted_delivery(identities[0], plan)
            self._repo.set_card_digest(identities[0], digest)
            root = root.model_copy(update={"delivery": plan})
        self._repo.mark_publication(identities[0], PublicationStatus.PENDING, count_attempt=False)
        sent = send_publication(
            self._repo, self._clock, identities[0], partial(self._publisher.publish_prepared, root)
        )
        if sent is None:
            return False
        parts = send_continuations(self._repo, self._clock, self._publisher, root, set())
        result.partial_errors.extend(parts.errors)
        result.attention["continuation_messages"] = (
            result.attention.get("continuation_messages", 0) + parts.sent
        )
        completed = self._repo.card_refresh_parts(card.id)
        return all(part.status is PublicationStatus.SENT for part in completed)

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
