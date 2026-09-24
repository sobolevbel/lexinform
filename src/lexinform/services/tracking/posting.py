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
from enum import StrEnum

from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    AgendaItem,
    Bill,
    DeliveryPlan,
    Phase,
    Publication,
    PublicationKind,
    PublicationStatus,
    Stage,
    StatusChange,
)
from lexinform.ports import BillRepository, Clock, Publisher
from lexinform.services.tracking.result import TrackingResult

log = logging.getLogger(__name__)

Send = Callable[[int | None], int]
"""Sends one post under an optional reply-to message and answers with its message id."""


class Told(StrEnum):
    """Disabled delivery queues news; only an explicit editorial hold suppresses it."""

    SENT = "sent"
    FAILED = "failed"
    QUEUED = "queued"


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
        self._attempted: set[int] = set()

    def start_run(self) -> None:
        self._attempted.clear()

    def card(self, bill: Bill) -> Publication | None:
        """The bill's card in this channel: the message every reply is attached to."""
        return self._repo.get_publication(
            bill.term, bill.number, PublicationKind.NEW_BILL, self._channel_id
        )

    def posted(self, bill: Bill, kind: PublicationKind, *, ref: str | None = None) -> bool:
        """True when this post exists and must not be attempted (again)."""
        pub = self._repo.get_publication(bill.term, bill.number, kind, self._channel_id, ref=ref)
        if pub is None:
            return False
        if pub.id in self._attempted:
            return True
        if pub.status is PublicationStatus.QUEUED:
            return False
        if pub.status is PublicationStatus.FAILED:
            return pub.attempts >= self._max_attempts
        return True

    def sent(self, bill: Bill, kind: PublicationKind, *, ref: str | None = None) -> bool:
        """True when this post actually reached the channel. `posted` answers a different
        question — whether it may be attempted — and a row a crashed run left behind counts as
        posted there while the reader never saw a word of it."""
        pub = self._repo.get_publication(bill.term, bill.number, kind, self._channel_id, ref=ref)
        return pub is not None and pub.status is PublicationStatus.SENT

    def told_jointly(self, bill: Bill, kind: PublicationKind, ref: str) -> bool:
        """A print considered jointly with this one has already told the channel about this very
        event. A sitting and a hearing belong to the whole group — one committee report for all
        of them — so telling it once per print is the same news twice."""
        for number in bill.summary.prints_considered_jointly:
            pub = self._repo.get_publication(bill.term, number, kind, self._channel_id, ref=ref)
            if pub is not None and pub.status is PublicationStatus.SENT:
                return True
        return False

    def retry_due(self, bill: Bill, kind: PublicationKind, *, ref: str | None = None) -> bool:
        """True when this post was attempted, failed, and still has attempts left."""
        pub = self._repo.get_publication(bill.term, bill.number, kind, self._channel_id, ref=ref)
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

    def rerender_card(self, bill: Bill, card: Publication) -> bool:
        """Re-render the card in place from `bill` as it stands now; one attempt, a refusal is
        logged and not retried.

        For a thread that gained a number this is the tags; for a term that ended it is the whole
        card, which `CardRefresher` cannot reach — `list_tracked` drops a discontinued row, so
        nothing would ever render the card that says the bill lapsed.
        """
        if card.message_id is None or card.id is None:
            return False
        try:
            self._publisher.edit_new_bill(bill, None, message_id=card.message_id)
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.warning("card of %s not re-rendered: %s: %s", bill.number, type(exc).__name__, exc)
            return False
        self._repo.set_card_digest(card.id, self._publisher.card_digest(bill))
        log.info("card of %s re-rendered in place", bill.number)
        return True

    def record_change(self, change: StatusChange) -> StatusChange | None:
        """Write the change down before anything is said about it; None when an earlier run
        recorded it already (the row is unique per bill and fingerprint), which is what keeps
        the same news from going out twice.

        The change comes back carrying its `id`, so the caller never has to fill it in — which
        is half of a prologue that stood in seven places, each free to forget a piece of it.
        (`record` is the same idea for a publication row and is a different table.)
        """
        change_id = self._repo.add_status_change(change)
        if change_id is None:
            return None
        change.id = change_id
        return change

    def tell(
        self, bill: Bill, change: StatusChange, result: TrackingResult, *, publish: bool
    ) -> Told:
        """A recorded substantive event must have durable delivery work even with publishing off."""
        if not publish:
            self.prepare(bill, change)
            return Told.QUEUED
        sent = self.status_update(bill, change)
        result.count_post(sent)
        return Told.SENT if sent else Told.FAILED

    def hold(self, bill: Bill, change: StatusChange) -> None:
        """Keep a service-stage change for the next post instead of sending it now."""
        assert change.id is not None, "only a change record_change stored reaches the poster"
        self._repo.create_publication(self._update_row(bill, change.id, PublicationStatus.SKIPPED))
        log.info(
            "druk %s: %s held for the next update",
            bill.number,
            "; ".join(st.stage_name for st in change.new_stages) or "closure",
        )

    def status_update(self, bill: Bill, change: StatusChange) -> bool:
        """Post a detected change as a reply to the card; True on success.

        Stages held since the previous post are listed first and released against the message
        just sent, not against "the newest update row": a retried update keeps its old row id, so
        a later held row would be the newest one.
        """
        assert change.id is not None, "only a change record_change stored reaches the poster"
        existing = self._repo.get_update_publication(change.id, self._channel_id)
        if existing is not None:
            if existing.status is PublicationStatus.SENT:
                return True
            if existing.status in (
                PublicationStatus.PENDING,
                PublicationStatus.UNKNOWN,
                PublicationStatus.DISMISSED,
            ):
                return False
        delivery = self.prepare(bill, change)
        pub_id = self._repo.create_publication(
            self._update_row(bill, change.id, PublicationStatus.PENDING)
        )
        snapshot = Bill.model_validate_json(delivery.bill_json)
        planned_change = StatusChange.model_validate_json(delivery.change_json)
        message_id = self._send(
            pub_id,
            snapshot,
            lambda reply_to: (
                self._publisher.publish_status_update(
                    snapshot, planned_change, delivery.reply_to or reply_to
                ).message_id
            ),
            held_change_ids=delivery.held_change_ids,
        )
        return message_id is not None

    def prepare(self, bill: Bill, change: StatusChange) -> DeliveryPlan:
        assert change.id is not None, "only a change record_change stored reaches the poster"
        stored = self._repo.get_update_publication(change.id, self._channel_id)
        if stored is not None and stored.delivery is not None:
            return stored.delivery
        held = self._repo.list_held_status_changes(bill.term, bill.number, self._channel_id)
        stages = [st for earlier in held for st in earlier.new_stages]
        planned = change.model_copy(update={"new_stages": stages + list(change.new_stages)})
        snapshot = self._repo.get(bill.term, bill.number) or bill
        card = self.card(bill)
        delivery = DeliveryPlan(
            bill_json=snapshot.model_dump_json(),
            change_json=planned.model_dump_json(),
            held_change_ids=tuple(c.id for c in held if c.id is not None),
            reply_to=card.message_id if card else None,
        )
        with self._repo.atomic():
            self._repo.create_publication(
                self._update_row(bill, change.id, PublicationStatus.QUEUED)
            )
            self._repo.save_update_delivery(change.id, self._channel_id, delivery)
        return delivery

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
        return self._once(bill, PublicationKind.ACT_PUBLISHED)

    def in_force(self, bill: Bill, *, today: date) -> bool:
        return self._once(bill, PublicationKind.IN_FORCE, today=today)

    def consultation_deadline(self, bill: Bill, *, today: date) -> bool:
        return self._once(bill, PublicationKind.CONSULTATION_DEADLINE, today=today)

    def consultation_results(self, bill: Bill) -> bool:
        return self._once(bill, PublicationKind.CONSULTATION_RESULTS)

    def agenda(self, bill: Bill, item: AgendaItem, moved_from: AgendaItem | None = None) -> bool:
        return self._once(
            bill, PublicationKind.AGENDA, ref=item.ref, item=item, moved_from=moved_from
        )

    def agenda_cancelled(self, bill: Bill, item: AgendaItem, *, still_meets: bool) -> bool:
        return self._once(
            bill,
            PublicationKind.AGENDA_CANCELLED,
            ref=item.ref,
            item=item,
            still_meets=still_meets,
        )

    def decision_deadline(self, bill: Bill, phase: Phase, *, today: date) -> bool:
        return self._once(
            bill, PublicationKind.DECISION_DEADLINE, ref=phase.key, phase=phase, today=today
        )

    def hearing_deadline(self, bill: Bill, hearing: Stage, *, today: date) -> bool:
        assert hearing.date is not None, "hearings_due keeps only hearings with a date"
        return self._once(
            bill,
            PublicationKind.HEARING_DEADLINE,
            ref=hearing.date.isoformat(),
            hearing=hearing,
            today=today,
        )

    def prepare_message(
        self,
        bill: Bill,
        kind: PublicationKind,
        *,
        ref: str | None = None,
        today: date | None = None,
        item: AgendaItem | None = None,
        moved_from: AgendaItem | None = None,
        phase: Phase | None = None,
        hearing: Stage | None = None,
        still_meets: bool = False,
    ) -> Publication:
        stored = self._repo.get_publication(bill.term, bill.number, kind, self._channel_id, ref=ref)
        if stored is not None and stored.delivery is not None:
            return stored
        card = self.card(bill)
        delivery = DeliveryPlan(
            bill_json=bill.model_dump_json(),
            today=today,
            item_json=item.model_dump_json() if item else None,
            moved_from_json=moved_from.model_dump_json() if moved_from else None,
            phase_json=phase.model_dump_json() if phase else None,
            hearing_json=hearing.model_dump_json() if hearing else None,
            still_meets=still_meets,
            reply_to=card.message_id if card else None,
        )
        pub = Publication(
            term=bill.term,
            number=bill.number,
            kind=kind,
            status=stored.status if stored else PublicationStatus.QUEUED,
            channel_id=self._channel_id,
            ref=ref,
            delivery=delivery,
            created_at=self._clock.now(),
            attempts=stored.attempts if stored else 0,
        )
        pub.id = self._repo.create_publication(pub)
        return pub

    def _once(
        self,
        bill: Bill,
        kind: PublicationKind,
        *,
        ref: str | None = None,
        today: date | None = None,
        item: AgendaItem | None = None,
        moved_from: AgendaItem | None = None,
        phase: Phase | None = None,
        hearing: Stage | None = None,
        still_meets: bool = False,
    ) -> bool:
        pub = self.prepare_message(
            bill,
            kind,
            ref=ref,
            today=today,
            item=item,
            moved_from=moved_from,
            phase=phase,
            hearing=hearing,
            still_meets=still_meets,
        )
        return self.deliver(pub)

    def deliver(self, pub: Publication) -> bool:
        if pub.status is PublicationStatus.SENT:
            return True
        if pub.status not in (PublicationStatus.QUEUED, PublicationStatus.FAILED):
            return False
        if pub.attempts >= self._max_attempts:
            return False
        assert pub.id is not None and pub.delivery is not None, (
            "prepare_message and list_due_deliveries give only stored rows with a delivery plan"
        )
        if pub.id in self._attempted:
            return False
        self._attempted.add(pub.id)
        plan = pub.delivery
        bill = Bill.model_validate_json(plan.bill_json)
        self._repo.mark_publication(pub.id, PublicationStatus.PENDING, count_attempt=False)
        return self._send(pub.id, bill, lambda _: self._dispatch(pub.kind, bill, plan)) is not None

    def _dispatch(self, kind: PublicationKind, bill: Bill, plan: DeliveryPlan) -> int:
        reply = plan.reply_to
        if kind is PublicationKind.ACT_PUBLISHED:
            return self._publisher.publish_act_published(bill, reply).message_id
        if kind is PublicationKind.CONSULTATION_RESULTS:
            return self._publisher.publish_consultation_results(bill, reply).message_id
        if kind in (PublicationKind.AGENDA, PublicationKind.AGENDA_CANCELLED):
            assert plan.item_json is not None, "an agenda post is prepared with its item"
            item = AgendaItem.model_validate_json(plan.item_json)
            if kind is PublicationKind.AGENDA_CANCELLED:
                return self._publisher.publish_agenda_cancelled(
                    bill, item, reply, still_meets=plan.still_meets
                ).message_id
            moved = (
                AgendaItem.model_validate_json(plan.moved_from_json)
                if plan.moved_from_json
                else None
            )
            return self._publisher.publish_agenda(bill, item, reply, moved).message_id
        assert plan.today is not None, f"a {kind} reminder is prepared with the day it is for"
        if kind is PublicationKind.IN_FORCE:
            return self._publisher.publish_in_force(bill, reply, today=plan.today).message_id
        if kind is PublicationKind.CONSULTATION_DEADLINE:
            return self._publisher.publish_consultation_deadline(
                bill, reply, today=plan.today
            ).message_id
        if kind is PublicationKind.DECISION_DEADLINE:
            assert plan.phase_json is not None, "a decision reminder is prepared with its phase"
            phase = Phase.model_validate_json(plan.phase_json)
            return self._publisher.publish_decision_deadline(
                bill, phase, reply, today=plan.today
            ).message_id
        assert kind is PublicationKind.HEARING_DEADLINE and plan.hearing_json is not None, (
            f"{kind} is not dispatched above, or a hearing reminder lacks its hearing"
        )
        hearing = Stage.model_validate_json(plan.hearing_json)
        return self._publisher.publish_hearing_deadline(
            bill, hearing, reply, today=plan.today
        ).message_id

    def _send(
        self, pub_id: int, bill: Bill, send: Send, *, held_change_ids: tuple[int, ...] = ()
    ) -> int | None:
        """Send and record the outcome; the message id on success, None on a per-bill failure.

        An outage of the channel propagates and does not count an attempt: it is the channel that
        is down, not the post, so the post keeps its retry budget.
        """
        card = self.card(bill)
        reply_to = card.message_id if card else None
        try:
            message_id = send(reply_to)
        except ServiceUnavailableError as exc:
            self._repo.mark_publication(
                pub_id, PublicationStatus.FAILED, error=exc.describe(), count_attempt=False
            )
            raise
        except Exception as exc:
            log.exception("post for druk %s failed: %s", bill.number, exc)
            self._repo.mark_publication(
                pub_id, PublicationStatus.FAILED, error=f"{type(exc).__name__}: {exc}"
            )
            return None
        with self._repo.atomic():
            self._repo.mark_publication(
                pub_id, PublicationStatus.SENT, message_id=message_id, sent_at=self._clock.now()
            )
            self._repo.release_planned_changes(
                held_change_ids,
                self._channel_id,
                message_id=message_id,
                sent_at=self._clock.now(),
            )
        return message_id
