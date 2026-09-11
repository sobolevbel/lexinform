"""Publishes analysed, relevant bills exactly once per channel.

Bills the Sejm considers jointly (`ProcessSummary.prints_considered_jointly`: several prints on
the same subject, one committee report for all of them) share one thread: the first of them to
be published gets the card, every later one a short "alternative bill" reply under it, and the
group is followed through the card's bill (the stages of the joint prints coincide from the
joint referral on). Within one run the government's print goes first: its text is usually the
one the committee works on.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    ApplicantType,
    Bill,
    PrintInfo,
    Publication,
    PublicationKind,
    PublicationStatus,
)
from lexinform.ports import BillRepository, Clock, Publisher, PublishResult, SejmGateway

log = logging.getLogger(__name__)

Send = Callable[[], PublishResult]

# The two ways a bill can be in the channel: its own card, or the "alternative bill" reply it
# got under the card of a print it is considered jointly with. One of them, never both.
CARD_KINDS = (PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL)


@dataclass
class PublishingResult:
    """Counters of one publishing phase."""

    published: int = 0
    joined: int = 0  # replies under the card of a jointly considered print, instead of a card
    skipped: int = 0
    failed: int = 0
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
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._publisher = publisher
        self._clock = clock
        self._channel_id = channel_id
        self._max_attempts = max_attempts

    def publish_new(self, *, min_score: int, limit: int, publish: bool = True) -> PublishingResult:
        result = PublishingResult()
        candidates = self._repo.list_publish_candidates(
            self._channel_id,
            min_score=min_score,
            limit=limit,
            max_attempts=self._max_attempts,
        )
        for bill in government_first(candidates):
            if not publish:
                self._record_skipped(bill)
                result.skipped += 1
                continue
            try:
                ok = self.publish_bill(bill, result)
            except ServiceUnavailableError as exc:
                result.failed += 1
                result.fatal_error = exc.describe()
                log.error("aborting publishing phase: %s", result.fatal_error)
                break
            if not ok:
                result.failed += 1
        return result

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

    def publish_bill(self, bill: Bill, result: PublishingResult | None = None) -> bool:
        """Send one bill: a card, or a reply under the card of a print it is considered jointly
        with. Records a pending publication before sending so a crash cannot cause a duplicate
        post; returns True on success and counts the post in `result`."""
        result = result if result is not None else PublishingResult()
        primary = self._primary_of(bill)
        if primary is not None:
            return self._publish_joint(bill, primary, result)
        inherited = self._inherited_card(bill)
        if inherited is not None:
            return self._inherit_card(bill, inherited)
        # Everything the card needs from the Sejm API is fetched before the pending row exists:
        # an outage here must leave no row behind (a stale pending row becomes `unknown` and is
        # never sent), so that the bill is simply a candidate again on the next run.
        print_info = self._safe_print(bill) if bill.has_process else None
        bill = self._with_submission(bill)
        pub_id = self._repo.create_publication(
            Publication(
                term=bill.term,
                number=bill.number,
                kind=PublicationKind.NEW_BILL,
                status=PublicationStatus.PENDING,
                channel_id=self._channel_id,
                created_at=self._clock.now(),
            )
        )
        if not self._send(pub_id, bill, lambda: self._publisher.publish_new_bill(bill, print_info)):
            return False
        result.published += 1
        return True

    def _publish_joint(
        self, bill: Bill, primary: tuple[Bill, int], result: PublishingResult
    ) -> bool:
        card_bill, card_message_id = primary
        print_info = self._safe_print(bill)  # before the pending row, as in `publish_bill`
        pub_id = self._repo.create_publication(
            Publication(
                term=bill.term,
                number=bill.number,
                kind=PublicationKind.JOINT_BILL,
                status=PublicationStatus.PENDING,
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
        result.joined += 1
        log.info("druk %s joined the thread of druk %s", bill.number, card_bill.number)
        return True

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
        assert bill.linked_number is not None
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
        assert card.message_id is not None
        try:
            self._publisher.edit_new_bill(pre, None, message_id=card.message_id)
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.warning("card of %s not re-tagged: %s: %s", pre.number, type(exc).__name__, exc)

    def _primary_of(self, bill: Bill) -> tuple[Bill, int] | None:
        """The bill `bill` is considered jointly with that already has a card in this channel
        and is still followed, with the card's message id; None when `bill` gets its own card."""
        for number in bill.summary.prints_considered_jointly:
            card = self._repo.get_publication(
                bill.term, number, PublicationKind.NEW_BILL, self._channel_id
            )
            if card is None or card.status is not PublicationStatus.SENT or card.message_id is None:
                continue
            other = self._repo.get(bill.term, number)
            if other is None or other.discontinued_at is not None:
                continue
            if other.summary.closure_date is not None and not other.summary.passed:
                continue  # withdrawn or rejected: its thread is over, the bill gets its own card
            return other, card.message_id
        return None

    def _send(self, pub_id: int, bill: Bill, send: Send) -> bool:
        try:
            sent = send()
        except ServiceUnavailableError as exc:
            # The channel is down, not the post: keep its retry budget.
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

    def _safe_print(self, bill: Bill) -> PrintInfo | None:
        try:
            return self._gateway.get_print(bill.term, bill.number)
        except Exception as exc:
            log.warning("print %s unavailable, publishing without PDF: %s", bill.number, exc)
            return None
