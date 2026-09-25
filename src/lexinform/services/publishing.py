"""Publishes analysed, relevant bills exactly once per channel.

Prints the Sejm considers jointly share one thread: the first published gets the card, every
later one a short "alternative bill" reply under it, and the group is followed through the card's
bill. Within one run the government's print goes first, its text being the one the committee
usually works on. `CARD_KINDS` are the two ways a bill can be in the channel, never both.

The reply is judged and paid for like a card, and what it adds to the thread is how the print
differs from the ones the reader has already read about (`_compared`).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    SILENCED_BY_OPERATOR,
    ApplicantType,
    Bill,
    BillStatus,
    DeliveryPlan,
    PrintInfo,
    Publication,
    PublicationKind,
    PublicationStatus,
    is_over,
)
from lexinform.ports import BillRepository, Clock, Publisher, PublishResult, SejmGateway
from lexinform.services.analysis import AnalysisService, Waiting
from lexinform.services.joint import group_of, primary_of
from lexinform.services.sources import fetch_print

log = logging.getLogger(__name__)

Send = Callable[[], PublishResult]
CARD_KINDS = (PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL)


@dataclass(frozen=True)
class CardPlan:
    """What the channel would get for a bill, decided once and used twice: by `publish_bill` to
    send it and by the operator's `/preview` to render it first.

    `primary` is the jointly considered print whose card this bill replies under instead of
    getting one of its own; `inherited` the sent card of the row this print continues, where the
    channel gets no new message at all, only the card re-rendered with both tags.
    """

    bill: Bill
    print_info: PrintInfo | None
    primary: Bill | None = None
    primary_message_id: int | None = None
    inherited: Publication | None = None


@dataclass
class PublishingResult:
    """Counters of one publishing phase; `joined` counts the replies posted under the card of a
    jointly considered print instead of a card of the bill's own."""

    published: int = 0
    joined: int = 0
    skipped: int = 0
    failed: int = 0
    waiting: int = 0
    fatal_error: str | None = None


def government_first(bills: list[Bill]) -> list[Bill]:
    """The publishing order: as given, except that a government print moves ahead of the
    non-government prints it is considered jointly with, so that it gets the group's card."""
    ordered: list[Bill] = []
    for bill in bills:
        partners = set(bill.summary.prints_considered_jointly)
        at = len(ordered)
        if partners and bill.summary.applicant_type is ApplicantType.GOVERNMENT:
            at = next(
                (
                    i
                    for i, earlier in enumerate(ordered)
                    if earlier.term == bill.term
                    and earlier.number in partners
                    and earlier.summary.applicant_type is not ApplicantType.GOVERNMENT
                ),
                len(ordered),
            )
        ordered.insert(at, bill)
    return ordered


class PublishingService:
    """Posts the card of every relevant, analysed bill once per channel."""

    def __init__(
        self,
        gateway: SejmGateway,
        repo: BillRepository,
        publisher: Publisher,
        clock: Clock,
        *,
        channel_id: str,
        max_attempts: int = 3,
        analysis: AnalysisService | None = None,
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._publisher = publisher
        self._clock = clock
        self._channel_id = channel_id
        self._max_attempts = max_attempts
        self._analysis = analysis

    @property
    def channel_id(self) -> str:
        """The channel the cards go to: what a caller counting its posts must ask about."""
        return self._channel_id

    def publish_new(
        self, *, min_score: int, limit: int, publish: bool = True, may_wait: bool = True
    ) -> PublishingResult:
        """Post the cards this run has earned.

        A bill whose road ended between the analysis and the card — publishing was off, or the
        channel was down — gets none: a card invites action, and there is none left. The skipped
        row settles it, the way `--no-publish` does.
        """
        result = PublishingResult()
        candidates = self._repo.list_publish_candidates(
            self._channel_id,
            min_score=min_score,
            limit=limit,
            max_attempts=self._max_attempts,
        )
        candidates += self._replies_under_the_bar(candidates, limit=limit)
        queued = [
            bill
            for pub in self._repo.list_due_deliveries(
                self._channel_id, max_attempts=self._max_attempts
            )
            if pub.kind in CARD_KINDS
            and pub.delivery is not None
            and (bill := self._repo.get(pub.term, pub.number)) is not None
            and bill.status is not BillStatus.LINKED
            and bill.last_error != SILENCED_BY_OPERATOR
        ]
        planned_keys = {(bill.term, bill.number) for bill in queued}
        candidates = queued + [
            bill for bill in candidates if (bill.term, bill.number) not in planned_keys
        ]
        today = self._clock.now().date()
        for bill in government_first(candidates[:limit]):
            planned = (bill.term, bill.number) in planned_keys
            if not planned and is_over(bill, today=today):
                log.info("%s is over: no card", bill.number)
                self._record_skipped(bill)
                result.skipped += 1
                continue
            if not publish:
                if planned:
                    continue
                self._record_skipped(bill)
                result.skipped += 1
                continue
            try:
                waiting = result.waiting
                ok = self.publish_bill(bill, result, may_wait=may_wait)
            except ServiceUnavailableError as exc:
                result.failed += 1
                result.fatal_error = exc.describe()
                log.error("aborting publishing phase: %s", result.fatal_error)
                break
            if not ok and result.waiting == waiting:
                result.failed += 1
        return result

    def _replies_under_the_bar(self, carded: list[Bill], *, limit: int) -> list[Bill]:
        """Prints that will reply under a card of their own group and did not clear `min_score`.

        The bar decides whether a bill earns a card in every reader's feed; a reply goes into a
        thread they already follow, and by then the print has been read and judged, so dropping it
        would mean paying for a reading and throwing it away. `primary_of` is still the whole
        decision, so a print with no card to hang under gets none of its own below the bar.
        """
        known = {(bill.term, bill.number) for bill in carded}
        under = []
        for bill in self._repo.list_joint_reply_candidates(
            self._channel_id, limit=limit, max_attempts=self._max_attempts
        ):
            if (bill.term, bill.number) in known or self._primary_of(bill) is None:
                continue
            under.append(bill)
        return under

    def card_of(self, bill: Bill) -> Publication | None:
        """How the bill is in this channel: its card, or its reply under a joint print's card.
        A sent row wins over a failed or forgotten attempt of the other kind."""
        rows = [
            pub
            for kind in CARD_KINDS
            if (pub := self._repo.get_publication(bill.term, bill.number, kind, self._channel_id))
        ]
        sent = next((pub for pub in rows if pub.status is PublicationStatus.SENT), None)
        return sent or next(iter(rows), None)

    def forget_card(self, bill: Bill) -> None:
        """Drop what the channel remembers of the bill's card, both kinds, so that the next
        `publish_bill` decides again (the operator's `/republish`)."""
        for kind in CARD_KINDS:
            self._repo.delete_publication(bill.term, bill.number, kind, self._channel_id)

    def publish_bill(
        self, bill: Bill, result: PublishingResult | None = None, *, may_wait: bool = False
    ) -> bool:
        """Send one bill: a card, or a reply under the card of a print it is considered jointly
        with. Returns True on success and counts the post in `result`.

        What it sends is `plan`'s decision, so `/preview` renders the same message beforehand. The
        pending row is written after that decision and before the post, so a crash cannot cause a
        duplicate and an outage of the Sejm API leaves no row behind.
        """
        result = result if result is not None else PublishingResult()
        identity = bill
        existing = self.card_of(bill)
        if existing is not None and existing.attempts >= self._max_attempts:
            return False
        if existing is not None and existing.status in (
            PublicationStatus.SENT,
            PublicationStatus.PENDING,
            PublicationStatus.UNKNOWN,
            PublicationStatus.SKIPPED,
            PublicationStatus.DISMISSED,
        ):
            return existing.status is PublicationStatus.SENT
        if existing is not None and existing.delivery is not None:
            delivery = existing.delivery
            plan = CardPlan(
                bill=Bill.model_validate_json(delivery.bill_json),
                print_info=PrintInfo.model_validate_json(delivery.print_json)
                if delivery.print_json
                else None,
                primary=Bill.model_validate_json(delivery.primary_json)
                if delivery.primary_json
                else None,
                primary_message_id=delivery.reply_to,
            )
        else:
            plan = self.plan(bill)
        if plan.primary is not None:
            return self._publish_joint(plan, result, identity, may_wait=may_wait)
        if plan.inherited is not None:
            return self._inherit_card(plan.bill, plan.inherited)
        bill, print_info = plan.bill, plan.print_info
        pub_id = self._repo.create_publication(
            Publication(
                term=identity.term,
                number=identity.number,
                kind=PublicationKind.NEW_BILL,
                delivery=DeliveryPlan(
                    bill_json=bill.model_dump_json(),
                    print_json=print_info.model_dump_json() if print_info else None,
                ),
                status=PublicationStatus.QUEUED,
                channel_id=self._channel_id,
                created_at=self._clock.now(),
            )
        )
        if not self._send(pub_id, bill, lambda: self._publisher.publish_new_bill(bill, print_info)):
            return False
        self._repo.set_card_digest(pub_id, self._publisher.card_digest(bill))
        result.published += 1
        return True

    def plan(self, bill: Bill) -> CardPlan:
        """Which of the three shapes the channel would get for `bill`, with everything fetched
        that the message needs. Read-only but for the `/bills` entry it may store on the way
        (the row had none; the publish would store it too).

        Every fetch happens here and none of it after a publication row exists: an outage must
        leave no pending row behind, or the bill would never be a candidate again.
        """
        primary = self._primary_of(bill)
        if primary is not None:
            card_bill, message_id = primary
            return CardPlan(
                bill=bill,
                print_info=fetch_print(self._gateway, bill),
                primary=card_bill,
                primary_message_id=message_id,
            )
        inherited = self._inherited_card(bill)
        if inherited is not None:
            # No message is sent: the card is re-rendered in place, and `_retag` renders it
            # without the print's files, so there is nothing to fetch.
            return CardPlan(bill=bill, print_info=None, inherited=inherited)
        print_info = self.print_info(bill)
        return CardPlan(bill=self._with_submission(bill), print_info=print_info)

    def _publish_joint(
        self, plan: CardPlan, result: PublishingResult, identity: Bill, *, may_wait: bool
    ) -> bool:
        bill, print_info = plan.bill, plan.print_info
        card_bill, card_message_id = plan.primary, plan.primary_message_id
        assert card_bill is not None, (
            "publish reaches _publish_joint only for a plan with a primary"
        )
        stored = self._repo.get_publication(
            identity.term, identity.number, PublicationKind.JOINT_BILL, self._channel_id
        )
        if stored is None or stored.delivery is None:
            compared = self._compared(bill, may_wait=may_wait)
            if isinstance(compared, Waiting):
                if bill.awaiting_batch_since is None:
                    self._repo.set_awaiting_batch(bill.term, bill.number, compared.since)
                result.waiting += 1
                return False
            bill = compared
        pub_id = self._repo.create_publication(
            Publication(
                term=identity.term,
                number=identity.number,
                kind=PublicationKind.JOINT_BILL,
                delivery=DeliveryPlan(
                    bill_json=bill.model_dump_json(),
                    print_json=print_info.model_dump_json() if print_info else None,
                    primary_json=card_bill.model_dump_json(),
                    reply_to=card_message_id,
                ),
                status=PublicationStatus.QUEUED,
                channel_id=self._channel_id,
                created_at=self._clock.now(),
            )
        )
        sent = self._send(
            pub_id,
            bill,
            lambda: self._publisher.publish_joint_bill(
                bill, card_bill, print_info, card_message_id
            ),
        )
        if not sent:
            return False
        self._repo.set_awaiting_batch(identity.term, identity.number, None)
        result.joined += 1
        log.info("druk %s joined the thread of druk %s", bill.number, card_bill.number)
        return True

    def _compared(self, bill: Bill, *, may_wait: bool) -> Bill | Waiting:
        """The bill with an up-to-date answer to "how does it differ from the others", asked of
        the model once and stored on the row.

        Asked here and not in the analysis phase: a group commonly arrives in one run, where at
        analysis time no print of it has been read yet. The comparison only embellishes the reply,
        so even a model outage — everywhere else the end of a phase — is caught and the reply goes.
        """
        if self._analysis is None:
            return bill
        others = group_of(self._repo, bill)
        numbers = [other.number for other in others]
        if not numbers or (bill.joint is not None and bill.joint.compared_with == numbers):
            return bill
        try:
            record = self._analysis.compare_joint(
                bill, others, may_wait=may_wait and self._analysis.may_wait_for_batch(bill)
            )
        except Exception as exc:
            log.warning(
                "%s not compared with %s (%s: %s); the reply says what it can",
                bill.number,
                ", ".join(numbers),
                type(exc).__name__,
                exc,
            )
            return bill
        if record is None:
            return bill
        if isinstance(record, Waiting):
            return record
        self._repo.save_joint_comparison(bill.term, bill.number, record)
        return bill.model_copy(update={"joint": record})

    def _inherited_card(self, bill: Bill) -> Publication | None:
        """The sent card of the row this print continues (its RPW entry or its RCL project).
        `Linker` hands the card over when tracking sees the print appear; this catches the print
        that was linked outside tracking — fetched by an operator command before the run did it."""
        if bill.linked_number is None:
            return None
        card = self._repo.get_publication(
            bill.term, bill.linked_number, PublicationKind.NEW_BILL, self._channel_id
        )
        if card is None or card.status is not PublicationStatus.SENT or card.message_id is None:
            return None
        return card

    def _inherit_card(self, bill: Bill, card: Publication) -> bool:
        """The print joins the thread of the entry it continues: the card is already in the
        channel, only the record of it is missing (every tracker joins on the print's own row)."""
        assert bill.linked_number is not None, (
            "_inherited_card finds a card only through linked_number"
        )
        pub_id = self._repo.create_publication(
            Publication(
                term=bill.term,
                number=bill.number,
                kind=PublicationKind.NEW_BILL,
                status=PublicationStatus.SENT,
                channel_id=self._channel_id,
                message_id=card.message_id,
                created_at=self._clock.now(),
                sent_at=card.sent_at,
            )
        )
        self._repo.mark_publication(
            pub_id, PublicationStatus.SENT, message_id=card.message_id, sent_at=card.sent_at
        )
        log.info(
            "druk %s continues %s: its card is message %s",
            bill.number,
            bill.linked_number,
            card.message_id,
        )
        pre = self._repo.get(bill.term, bill.linked_number)
        if pre is not None:
            self._retag(pre, card)
        return True

    def _retag(self, pre: Bill, card: Publication) -> None:
        """The card heads a thread that has a druk number now: re-render it in place so that it
        carries both tags. One attempt; a refusal is logged, the replies carry both tags anyway."""
        assert card.message_id is not None, (
            "_inherited_card returns only a sent card with its message id"
        )
        try:
            self._publisher.edit_new_bill(pre, None, message_id=card.message_id)
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.warning("card of %s not re-tagged: %s: %s", pre.number, type(exc).__name__, exc)

    def _primary_of(self, bill: Bill) -> tuple[Bill, int] | None:
        return primary_of(self._repo, bill, self._channel_id)

    def _send(self, pub_id: int, bill: Bill, send: Send) -> bool:
        """Send the post and record what became of it. An outage of the channel propagates and
        counts no attempt: the channel is down, not the post, so it keeps its retry budget."""
        self._repo.mark_publication(pub_id, PublicationStatus.PENDING, count_attempt=False)
        try:
            sent = send()
        except ServiceUnavailableError as exc:
            self._repo.mark_publication(
                pub_id, PublicationStatus.FAILED, error=exc.describe(), count_attempt=False
            )
            raise
        except Exception as exc:
            log.exception("publishing druk %s failed: %s", bill.number, exc)
            self._repo.mark_publication(
                pub_id, PublicationStatus.FAILED, error=f"{type(exc).__name__}: {exc}"
            )
            return False
        self._repo.mark_publication(
            pub_id,
            PublicationStatus.SENT,
            message_id=sent.message_id,
            document_message_ids=list(sent.document_message_ids),
            sent_at=self._clock.now(),
        )
        log.info("published druk %s as message %s", bill.number, sent.message_id)
        return True

    def _record_skipped(self, bill: Bill) -> None:
        self._repo.create_publication(
            Publication(
                term=bill.term,
                number=bill.number,
                kind=PublicationKind.NEW_BILL,
                status=PublicationStatus.SKIPPED,
                channel_id=self._channel_id,
                created_at=self._clock.now(),
            )
        )
        log.info("druk %s marked as skipped (publishing disabled)", bill.number)

    def _with_submission(self, bill: Bill) -> Bill:
        """Attach the /bills entry (public consultation dates) to a numbered print, best effort."""
        if bill.submission is not None or not bill.has_process:
            return bill
        try:
            sub = self._gateway.find_submission(bill.term, bill.number)
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.warning("submission lookup for druk %s failed: %s", bill.number, exc)
            return bill
        if sub is None:
            return bill
        self._repo.save_submission(bill.term, bill.number, sub)
        return bill.model_copy(update={"submission": sub})

    def print_info(self, bill: Bill) -> PrintInfo | None:
        """The print whose files the card links, or None when the row has none (an RCL project,
        a wykaz entry) or the API is having a bad day: a card without the links is still a card.
        """
        return fetch_print(self._gateway, bill) if bill.has_process else None
