"""Replies under a bill's card, recorded as `pending` before they are sent.

A failure is recorded on the same row and retried on later runs up to `max_attempts`; an outage
(`ServiceUnavailableError`) is recorded and re-raised so the phase stops.

A status change made of service stages only (see `models.has_news`) is *held*: its row gets a
`skipped` update publication and nothing is sent; the next update of the bill lists the held
stages before its own and releases them (their rows become `sent` with its message id).
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
    Stage,
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

    def retry_due(self, bill: Bill, kind: PublicationKind, *, ref: str | None = None) -> bool:
        """True when this post was attempted, failed, and still has attempts left."""
        pub = self._repo.get_publication(
            bill.term, bill.number, kind.value, self._channel_id, ref=ref
        )
        return (
            pub is not None
            and pub.status is PublicationStatus.FAILED
            and pub.attempts < self._max_attempts
        )

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

    def retag_card(self, bill: Bill, card: Publication) -> bool:
        """Re-render the card in place so it carries the tags of the whole thread; one attempt,
        a refusal is logged and not retried (the replies carry both tags anyway)."""
        if card.message_id is None:
            return False
        try:
            self._publisher.edit_new_bill(bill, None, message_id=card.message_id)
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.warning("card of %s not re-tagged: %s: %s", bill.number, type(exc).__name__, exc)
            return False
        log.info("card of %s re-rendered with the tags of its druk", bill.number)
        return True

    def hold(self, bill: Bill, change: StatusChange) -> None:
        """Keep a service-stage change for the next post instead of sending it now."""
        assert change.id is not None
        self._repo.create_publication(self._update_row(bill, change.id, PublicationStatus.SKIPPED))
        log.info(
            "druk %s: %s held for the next update",
            bill.number,
            "; ".join(st.stage_name for st in change.new_stages) or "closure",
        )

    def status_update(self, bill: Bill, change: StatusChange) -> bool:
        """Post a detected change as a reply to the card; True on success. Stages held since the
        previous post are listed first and released with it."""
        assert change.id is not None
        pub_id = self._repo.create_publication(
            self._update_row(bill, change.id, PublicationStatus.PENDING)
        )
        held = self._repo.list_held_status_changes(bill.term, bill.number, self._channel_id)
        if held:
            stages: list[Stage] = [st for earlier in held for st in earlier.new_stages]
            change = change.model_copy(update={"new_stages": stages + list(change.new_stages)})
        fresh = self._repo.get(bill.term, bill.number) or bill
        sent = self._send(
            pub_id,
            bill,
            lambda reply_to: (
                self._publisher.publish_status_update(fresh, change, reply_to).message_id
            ),
        )
        if sent and held:
            posted = self._repo.get_publication(
                bill.term, bill.number, PublicationKind.STATUS_UPDATE.value, self._channel_id
            )
            if posted is not None and posted.message_id is not None:
                self._repo.release_held_status_changes(
                    bill.term,
                    bill.number,
                    self._channel_id,
                    message_id=posted.message_id,
                    sent_at=self._clock.now(),
                )
        return sent

    def _update_row(self, bill: Bill, change_id: int, status: PublicationStatus) -> Publication:
        return Publication(
            term=bill.term,
            number=bill.number,
            kind=PublicationKind.STATUS_UPDATE,
            status=status,
            channel_id=self._channel_id,
            status_change_id=change_id,
            created_at=self._clock.now(),
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

    def hearing_deadline(self, bill: Bill, hearing: Stage, *, today: date) -> bool:
        """One reminder per (bill, hearing): the hearing date is the `ref`."""
        assert hearing.date is not None
        return self._once(
            bill,
            PublicationKind.HEARING_DEADLINE,
            lambda reply_to: (
                self._publisher.publish_hearing_deadline(
                    bill, hearing, reply_to, today=today
                ).message_id
            ),
            ref=hearing.date.isoformat(),
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
            # The channel is down, not the post: keep its retry budget.
            self._repo.mark_publication(
                pub_id, PublicationStatus.FAILED, error=exc.describe(), count_attempt=False
            )
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
