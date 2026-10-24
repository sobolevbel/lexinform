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
from dataclasses import dataclass, field
from functools import partial
from zoneinfo import ZoneInfo

from lexinform.attention import assess_attention
from lexinform.errors import ServiceUnavailableError, StateCheckpointError
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
from lexinform.ports import (
    BillRepository,
    Clock,
    Publisher,
    PublishResult,
    SejmGateway,
    StateCheckpoint,
)
from lexinform.services.analysis import AnalysisService, Waiting
from lexinform.services.card_edits import edit_single_card
from lexinform.services.joint import group_of, primary_of
from lexinform.services.message_parts import freeze_parts, send_continuations
from lexinform.services.predecessors import rcl_predecessor
from lexinform.services.publications import inherit_card, open_card_edit_ids, send_publication
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
    message_parts_sent: int = 0
    attention: dict[str, int] = field(default_factory=dict)
    partial_errors: list[str] = field(default_factory=list)
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
        attention_enabled: bool = False,
        attention_assess: bool = True,
        delivery_attempts: set[int] | None = None,
        checkpoint: StateCheckpoint | None = None,
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._publisher = publisher
        self._clock = clock
        self._channel_id = channel_id
        self._max_attempts = max_attempts
        self._analysis = analysis
        self._attention_enabled = attention_enabled
        self._attention_assess = attention_assess
        self._parts_sent = 0
        self._part_errors: list[str] = []
        self._delivery_attempts = delivery_attempts if delivery_attempts is not None else set[int]()
        self._checkpoint = checkpoint

    @property
    def channel_id(self) -> str:
        """The channel the cards go to: what a caller counting its posts must ask about."""
        return self._channel_id

    def start_run(self) -> None:
        self._delivery_attempts.clear()

    def publish_new(
        self,
        *,
        min_score: int,
        limit: int,
        publish: bool = True,
        may_wait: bool = True,
        keep_delivery_attempts: bool = False,
    ) -> PublishingResult:
        """Post the cards this run has earned.

        A bill whose road ended between the analysis and the card — publishing was off, or the
        channel was down — gets none: a card invites action, and there is none left. The skipped
        row settles it, the way `--no-publish` does.
        """
        result = PublishingResult()
        self._parts_sent = 0
        self._part_errors.clear()
        if not keep_delivery_attempts:
            self.start_run()
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
            if not planned and self.became_druk(bill) is not None:
                continue
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

    def has_sent_card(self, bill: Bill) -> bool:
        """Whether the bill has a thread of its own: the test `observation_for` takes."""
        pub = self._repo.get_publication(
            bill.term, bill.number, PublicationKind.NEW_BILL, self._channel_id
        )
        return pub is not None and pub.status is PublicationStatus.SENT

    def forget_card(self, bill: Bill) -> None:
        """Drop what the channel remembers of the bill's card, both kinds, so that the next
        `publish_bill` decides again (the operator's `/republish`)."""
        card = self.card_of(bill)
        if card is not None and card.delivery is not None:
            unfinished = [
                identity
                for identity in card.delivery.continuation_ids
                for part in (self._repo.publication_by_id(identity),)
                if part is not None
                and part.status
                not in (
                    PublicationStatus.SENT,
                    PublicationStatus.DISMISSED,
                )
            ]
            if unfinished:
                raise ValueError(
                    "Resolve unfinished physical parts before forgetting the card: "
                    + ", ".join(map(str, unfinished))
                )
        if card is not None and card.message_id is not None:
            edits = open_card_edit_ids(self._repo, card)
            if edits:
                raise ValueError(
                    "Resolve unfinished card edit operations before forgetting the card: "
                    + ", ".join(map(str, edits))
                )
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
        if existing is None and self.became_druk(bill) is not None:
            return False
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
            return self._inherit_card(plan.bill, plan.inherited, plan.print_info)
        bill, print_info = plan.bill, plan.print_info
        pub_id = self._repo.create_publication(
            Publication(
                term=identity.term,
                number=identity.number,
                kind=PublicationKind.NEW_BILL,
                delivery=DeliveryPlan(
                    bill_json=bill.model_dump_json(),
                    policy_version="attention-v1" if self._attention_enabled else "legacy",
                    today=self._clock.now().astimezone(ZoneInfo("Europe/Warsaw")).date(),
                    attention=assess_attention(
                        bill, PublicationKind.NEW_BILL, now=self._clock.now()
                    )
                    if self._attention_assess
                    else None,
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
        self._count_attention(pub_id, result)
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
            return CardPlan(
                bill=self._with_submission(bill),
                print_info=self.print_info(bill),
                inherited=inherited,
            )
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
                    policy_version="attention-v1" if self._attention_enabled else "legacy",
                    today=self._clock.now().astimezone(ZoneInfo("Europe/Warsaw")).date(),
                    attention=assess_attention(
                        bill, PublicationKind.JOINT_BILL, now=self._clock.now()
                    )
                    if self._attention_assess
                    else None,
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
        self._count_attention(pub_id, result)
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
        except StateCheckpointError:
            raise
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

    def became_druk(self, bill: Bill) -> Bill | None:
        """The druk, or the followed RCL project, that already carries this entry's thread."""
        if bill.status is BillStatus.LINKED:
            return None
        if bill.has_process:
            return self._threaded_project(bill)
        druk = None
        if bill.rcl is not None:
            if bill.rcl.print_number:
                druk = self._repo.get(bill.term, bill.rcl.print_number)
            elif bill.rcl.rm_number:
                druk = self._repo.find_print_by_rcl_num(bill.term, bill.rcl.rm_number)
        elif bill.submission is not None and bill.submission.print_number:
            druk = self._repo.get(bill.term, bill.submission.print_number)
        elif bill.submission is not None and bill.submission.senders_number:
            druk = rcl_predecessor(self._repo, None, bill.submission.senders_number)
        if druk is not None:
            log.info("%s is %s already: no card of its own", bill.number, druk.number)
        return druk

    def _threaded_project(self, druk: Bill) -> Bill | None:
        """The carded RCL project this druk continues, stamped so the RCL watcher links it."""
        rm_number = druk.summary.rcl_num or (
            druk.submission.senders_number if druk.submission is not None else None
        )
        project = rcl_predecessor(self._repo, None, rm_number)
        if project is None or not self.has_sent_card(project):
            return None
        assert project.rcl is not None, "rcl_predecessor returns only a row with its project"
        if project.rcl.print_number != druk.number:
            self._repo.save_rcl(
                project.term,
                project.number,
                project.rcl.model_copy(update={"print_number": druk.number}),
            )
        log.info(
            "druk %s continues %s: tracking links it into that thread",
            druk.number,
            project.number,
        )
        return project

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

    def _inherit_card(self, bill: Bill, card: Publication, print_info: PrintInfo | None) -> bool:
        """The print joins the thread of the entry it continues: the card is already in the
        channel, only the record of it is missing (every tracker joins on the print's own row)."""
        assert bill.linked_number is not None, (
            "_inherited_card finds a card only through linked_number"
        )
        alias = inherit_card(
            self._repo,
            term=bill.term,
            number=bill.number,
            channel_id=self._channel_id,
            card=card,
            now=self._clock.now(),
        )
        log.info(
            "druk %s continues %s: its card is message %s",
            bill.number,
            bill.linked_number,
            card.message_id,
        )
        self._retag(bill, alias, print_info, predecessor=card)
        return True

    def _retag(
        self,
        bill: Bill,
        card: Publication,
        print_info: PrintInfo | None,
        *,
        predecessor: Publication,
    ) -> None:
        """The inherited logical owner keeps its physical predecessor's message target."""
        if predecessor.delivery is not None and predecessor.delivery.continuation_ids:
            return
        if open_card_edit_ids(self._repo, card):
            return
        saved = edit_single_card(
            self._repo,
            self._clock,
            self._publisher,
            bill,
            card,
            print_info,
            channel_id=self._channel_id,
            checkpoint=self._checkpoint,
            delivery_attempts=self._delivery_attempts,
        )
        if saved is not None:
            return
        assert card.message_id is not None, "an inherited card retains the sent physical target"
        try:
            self._publisher.edit_new_bill(bill, print_info, message_id=card.message_id)
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.warning("card of %s not re-tagged: %s: %s", bill.number, type(exc).__name__, exc)

    def _primary_of(self, bill: Bill) -> tuple[Bill, int] | None:
        return primary_of(self._repo, bill, self._channel_id)

    def _send(self, pub_id: int, bill: Bill, send: Send) -> bool:
        pub = self._repo.publication_by_id(pub_id)
        if (
            pub is not None
            and pub.delivery is not None
            and not pub.delivery.rendered_text
            and not pub.attempts
        ):
            pub = freeze_parts(
                self._repo, self._clock, self._publisher, pub, self._publisher.render_delivery(pub)
            )
        if pub is not None and pub.delivery is not None and pub.delivery.rendered_text:
            send = partial(self._publisher.publish_prepared, pub)
        self._repo.mark_publication(pub_id, PublicationStatus.PENDING, count_attempt=False)
        message_id = send_publication(
            self._repo, self._clock, pub_id, send, checkpoint=self._checkpoint
        )
        if message_id is None:
            return False
        if pub is not None and pub.delivery is not None:
            parts = send_continuations(
                self._repo,
                self._clock,
                self._publisher,
                pub,
                self._delivery_attempts,
                checkpoint=self._checkpoint,
            )
            self._parts_sent += parts.sent
            self._part_errors.extend(parts.errors)
        log.info("published druk %s as message %s", bill.number, message_id)
        return True

    def _count_attention(self, pub_id: int, result: PublishingResult) -> None:
        result.message_parts_sent = self._parts_sent
        result.partial_errors = list(self._part_errors)
        pub = self._repo.publication_by_id(pub_id)
        if pub is None or pub.attention is None:
            return
        keys = ["events", "messages", pub.attention.importance]
        if pub.attention.urgency == "urgent":
            keys.append("urgent")
        keys.extend(f"reason/{reason}" for reason in pub.attention.reasons)
        for key in keys:
            result.attention[key] = result.attention.get(key, 0) + 1

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
