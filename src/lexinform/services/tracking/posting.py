"""Replies under a bill's card, recorded as `pending` before they are sent.

A failure is recorded on the same row and retried on later runs up to `max_attempts`; an outage
(`ServiceUnavailableError`) is recorded and re-raised so the phase stops.
"""

import logging
from collections.abc import Callable
from datetime import date

from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    AgendaItem,
    Bill,
    Publication,
    PublicationKind,
    PublicationStatus,
    StatusChange,
)
from lexinform.ports import BillRepository, Clock, Publisher

log = logging.getLogger(__name__)

Send = Callable[[int | None], int]  # reply_to -> message id


class Poster:
    """Sends the replies of the tracking phase and keeps the `publications` bookkeeping."""

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

    def posted(self, bill: Bill, kind: PublicationKind, *, ref: str | None = None) -> bool:
        """True when this post exists and must not be attempted (again)."""
        pub = self._repo.get_publication(
            bill.term, bill.number, kind.value, self._channel_id, ref=ref
        )
        if pub is None:
            return False
        if pub.status is PublicationStatus.FAILED:
            return pub.attempts >= self._max_attempts
        return True

    def record(
        self,
        bill: Bill,
        kind: PublicationKind,
        status: PublicationStatus,
        *,
        ref: str | None = None,
    ) -> int:
        """Write the bookkeeping row without sending anything (e.g. `skipped`)."""
        return self._repo.create_publication(
            Publication(
                term=bill.term,
                number=bill.number,
                kind=kind,
                status=status,
                channel_id=self._channel_id,
                ref=ref,
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
        fresh = self._repo.get(bill.term, bill.number) or bill
        return self._send(
            pub_id,
            bill,
            lambda reply_to: (
                self._publisher.publish_status_update(fresh, change, reply_to).message_id
            ),
        )

    def act_published(self, bill: Bill) -> bool:
        return self._once(
            bill,
            PublicationKind.ACT_PUBLISHED,
            lambda reply_to: self._publisher.publish_act_published(bill, reply_to).message_id,
        )

    def in_force(self, bill: Bill) -> bool:
        return self._once(
            bill,
            PublicationKind.IN_FORCE,
            lambda reply_to: self._publisher.publish_in_force(bill, reply_to).message_id,
        )

    def consultation_deadline(self, bill: Bill, *, today: date) -> bool:
        return self._once(
            bill,
            PublicationKind.CONSULTATION_DEADLINE,
            lambda reply_to: (
                self._publisher.publish_consultation_deadline(
                    bill, reply_to, today=today
                ).message_id
            ),
        )

    def consultation_results(self, bill: Bill) -> bool:
        return self._once(
            bill,
            PublicationKind.CONSULTATION_RESULTS,
            lambda reply_to: (
                self._publisher.publish_consultation_results(bill, reply_to).message_id
            ),
        )

    def agenda(self, bill: Bill, item: AgendaItem) -> bool:
        """One post per (bill, sitting): `item.ref` tells the sittings apart."""
        return self._once(
            bill,
            PublicationKind.AGENDA,
            lambda reply_to: self._publisher.publish_agenda(bill, item, reply_to).message_id,
            ref=item.ref,
        )

    def _once(
        self, bill: Bill, kind: PublicationKind, send: Send, *, ref: str | None = None
    ) -> bool:
        pub_id = self.record(bill, kind, PublicationStatus.PENDING, ref=ref)
        return self._send(pub_id, bill, send)

    def _send(self, pub_id: int, bill: Bill, send: Send) -> bool:
        card = self.card(bill)
        reply_to = card.message_id if card else None
        try:
            message_id = send(reply_to)
        except ServiceUnavailableError as exc:
            self._repo.mark_publication(pub_id, PublicationStatus.FAILED, error=exc.describe())
            raise
        except Exception as exc:
            log.exception("post for druk %s failed: %s", bill.number, exc)
            self._repo.mark_publication(
                pub_id, PublicationStatus.FAILED, error=f"{type(exc).__name__}: {exc}"
            )
            return False
        self._repo.mark_publication(
            pub_id, PublicationStatus.SENT, message_id=message_id, sent_at=self._clock.now()
        )
        return True
