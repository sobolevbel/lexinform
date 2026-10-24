"""Keep followed cards true while retaining every attempted physical edit."""

import logging
from datetime import date
from functools import partial
from zoneinfo import ZoneInfo

from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    Bill,
    BillStatus,
    DeliveryPlan,
    Publication,
    PublicationKind,
    PublicationStatus,
)
from lexinform.ports import BillRepository, Clock, Publisher, SejmGateway, StateCheckpoint
from lexinform.services.message_parts import continuation_snapshot, send_continuations
from lexinform.services.publications import (
    card_revision_owner,
    open_card_edit_ids,
    send_publication,
)
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
            parts = self._repo.card_refresh_parts(card.id)
            if (
                parts
                and parts[0].delivery is not None
                and parts[0].delivery.rendered_text
                and parts[0].rendered_sha256 == digest
                and any(part.status is PublicationStatus.DISMISSED for part in parts)
                and card_revision_owner(self._repo, parts[0]) == card
            ):
                continue
            try:
                if len(parts) > 1:
                    refreshed = self._edit_parts(bill, card, digest, result)
                else:
                    refreshed = self._edit_single(bill, card, digest, today)
            except ServiceUnavailableError as exc:
                result.abort(exc)
                return
            if refreshed:
                self._repo.set_card_digest(card.id, digest)
                result.cards_refreshed += 1
                edited += 1

    def _edit_single(self, bill: Bill, card: Publication, digest: str, today: date) -> bool:
        assert card.id is not None and card.message_id is not None, "refresh targets a sent card"
        current = self._repo.card_refresh_parts(card.id)
        if not current:
            return False
        previous = current[0]
        if previous.kind is PublicationKind.MESSAGE_PART and previous.status not in (
            PublicationStatus.SENT,
            PublicationStatus.DISMISSED,
        ):
            return False
        print_info = fetch_print(self._gateway, bill) if bill.has_process else None
        texts = self._publisher.render_card_parts(bill, print_info, minimum=1, today=today)
        if len(texts) != 1:
            return self._edit(bill, card.message_id)
        ref = f"card/{card.id}/{digest}-{previous.id}/1"
        existing = self._repo.get_publication(
            bill.term, bill.number, PublicationKind.MESSAGE_PART, card.channel_id, ref=ref
        )
        if existing is not None:
            return False
        with self._repo.atomic():
            identity = self._repo.create_publication(
                Publication(
                    term=bill.term,
                    number=bill.number,
                    channel_id=card.channel_id,
                    kind=PublicationKind.MESSAGE_PART,
                    status=PublicationStatus.QUEUED,
                    created_at=self._clock.now(),
                    ref=ref,
                    delivery=DeliveryPlan(
                        bill_json=continuation_snapshot(bill),
                        rendered_text=texts[0],
                        edit_message_id=card.message_id,
                        revision_of=card.id,
                        today=today,
                        reply_to=card.message_id,
                        continuation_parent=card.id,
                        continuation_index=1,
                    ),
                )
            )
            self._repo.set_card_digest(identity, digest)
            self._repo.mark_publication(identity, PublicationStatus.PENDING, count_attempt=False)
            operation = self._repo.publication_by_id(identity)
            assert operation is not None, "the immutable operation is saved before its edit"
        self._attempted.add(identity)
        return (
            send_publication(
                self._repo,
                self._clock,
                identity,
                partial(self._publisher.publish_prepared, operation),
                checkpoint=self._checkpoint,
            )
            is not None
        )

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
