"""Replies under a bill's card: status updates and one-off notices.

Every post is recorded as `pending` before it is sent (see CLAUDE.md, "Pending-before-send"), a
failure is recorded on the same row and retried on later runs up to `max_attempts`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date

from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    Bill,
    Publication,
    PublicationKind,
    PublicationStatus,
    StatusChange,
)
from lexinform.ports import BillRepository, Clock, Publisher

log = logging.getLogger(__name__)


class Poster:
    def __init__(
        self,
        repo: BillRepository,
        publisher: Publisher,
        clock: Clock,
        *,
        channel_id: str,
        max_attempts: int,
    ) -> None:
        self._repo = repo
        self._publisher = publisher
        self._clock = clock
        self._channel_id = channel_id
        self._max_attempts = max_attempts

    def card(self, bill: Bill) -> Publication | None:
        """The bill's card in this channel: the message every reply is attached to."""
        return self._repo.get_publication(
            bill.term, bill.number, PublicationKind.NEW_BILL.value, self._channel_id
        )

    def posted(self, bill: Bill, kind: PublicationKind) -> bool:
        """True when a post of this kind exists and must not be attempted (again)."""
        pub = self._repo.get_publication(bill.term, bill.number, kind.value, self._channel_id)
        if pub is None:
            return False
        if pub.status is PublicationStatus.FAILED:
            return pub.attempts >= self._max_attempts
        return True

    def record(self, bill: Bill, kind: PublicationKind, status: PublicationStatus) -> int:
        return self._repo.create_publication(
            Publication(
                term=bill.term,
                number=bill.number,
                kind=kind,
                status=status,
                channel_id=self._channel_id,
                created_at=self._clock.now(),
            )
        )

    def status_update(self, bill: Bill, change: StatusChange) -> bool:
        """Post a detected change as a reply to the card; True on success."""
        assert change.id is not None
        pub_id = self._repo.create_publication(
            Publication(
                term=bill.term,
                number=bill.number,
                kind=PublicationKind.STATUS_UPDATE,
                status=PublicationStatus.PENDING,
                channel_id=self._channel_id,
                status_change_id=change.id,
                created_at=self._clock.now(),
            )
        )
        card = self.card(bill)
        fresh = self._repo.get(bill.term, bill.number) or bill
        reply_to = card.message_id if card else None
        return self._send(
            pub_id,
            bill,
            "status update",
            lambda: self._publisher.publish_status_update(fresh, change, reply_to).message_id,
        )

    def one_off(self, bill: Bill, kind: PublicationKind, *, today: date | None = None) -> bool:
        """Post an act notice, an in-force reminder or a consultation reminder (once per bill)."""
        pub_id = self.record(bill, kind, PublicationStatus.PENDING)
        card = self.card(bill)
        reply_to = card.message_id if card else None

        def send() -> int:
            if kind is PublicationKind.ACT_PUBLISHED:
                return self._publisher.publish_act_published(bill, reply_to).message_id
            if kind is PublicationKind.CONSULTATION_DEADLINE:
                assert today is not None
                return self._publisher.publish_consultation_deadline(
                    bill, reply_to, today=today
                ).message_id
            return self._publisher.publish_in_force(bill, reply_to).message_id

        return self._send(pub_id, bill, kind.value, send)

    def _send(self, pub_id: int, bill: Bill, what: str, send: Callable[[], int]) -> bool:
        """Run `send`, then mark the pending row sent or failed; outages propagate."""
        try:
            message_id = send()
        except ServiceUnavailableError as exc:
            self._repo.mark_publication(pub_id, PublicationStatus.FAILED, error=exc.describe())
            raise
        except Exception as exc:
            log.exception("%s for druk %s failed: %s", what, bill.number, exc)
            self._repo.mark_publication(
                pub_id, PublicationStatus.FAILED, error=f"{type(exc).__name__}: {exc}"
            )
            return False
        self._repo.mark_publication(
            pub_id, PublicationStatus.SENT, message_id=message_id, sent_at=self._clock.now()
        )
        return True
