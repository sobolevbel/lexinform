"""Operations on saved bills and their cards shared by CLI and Telegram."""

from collections.abc import Callable
from dataclasses import dataclass

from lexinform.models import (
    SILENCED_BY_OPERATOR,
    Bill,
    BillStatus,
    CommandOutcome,
    OutcomeStatus,
    PrintInfo,
    PublicationKind,
    PublicationStatus,
    closure_event,
    is_over,
)
from lexinform.models.source_checks import observation_label, operator_observation
from lexinform.ports import BillRepository, Clock, Publisher
from lexinform.services.joint import primary_of
from lexinform.services.lookup import BillLookup, supported_bill
from lexinform.services.publishing import PublishingResult, PublishingService

_CLOSURE_NOTES = {
    "veto_sustained": "the President's veto stood",
    "withdrawn_by_applicant": "withdrawn by the applicant",
    "rejected": "rejected",
    "not_enacted": "closed without a law",
}

RESET_TARGETS = (
    BillStatus.ANALYZED,
    BillStatus.ANALYSIS_PENDING,
    BillStatus.SKIPPED_PREFILTER,
    BillStatus.SKIPPED_COST,
)


@dataclass(frozen=True)
class BillOperations:
    repo: BillRepository
    publishing: PublishingService
    lookup: BillLookup
    clock: Clock
    publisher_for: Callable[[str], Publisher] | None = None

    def reset(self, bill: Bill, to: BillStatus) -> CommandOutcome:
        if to in (BillStatus.ANALYSIS_PENDING, BillStatus.ANALYZED):
            supported_bill(bill)
        if to is BillStatus.ANALYZED and bill.analysis is None:
            return CommandOutcome(
                status=OutcomeStatus.ERROR,
                bill=bill,
                note="Сохранённого анализа нет. Для повторной попытки используйте /reset BILL.",
            )
        if to not in RESET_TARGETS:
            return CommandOutcome(
                status=OutcomeStatus.ERROR,
                bill=bill,
                note="Допустимые состояния: analysis_pending, skipped_prefilter, skipped_cost "
                "и analyzed с сохранённым анализом. "
                "Используйте /unskip для восстановления анализа или /link для связи с druk.",
            )
        if bill.status is BillStatus.LINKED or bill.discontinued_at is not None:
            return CommandOutcome(
                status=OutcomeStatus.ERROR,
                bill=bill,
                note="Запись связана с другим проектом или закрыта. Проверьте /show.",
            )
        read_again = (
            to is BillStatus.ANALYSIS_PENDING
            and bill.rcl is not None
            and not bill.rcl.text_documents()
        )
        project = self.lookup.read_rcl_project(bill.number) if read_again else None
        reason = (
            SILENCED_BY_OPERATOR
            if to is BillStatus.SKIPPED_PREFILTER
            else "reset by the operator (/reset)"
        )
        with self.repo.atomic():
            if project is not None:
                self.repo.save_rcl(bill.term, bill.number, project)
            self.repo.reset_bill(bill.term, bill.number, to, reason=reason)
        note = f"was {bill.status} (attempts {bill.analysis_attempts}), now {to}"
        return CommandOutcome(
            status=OutcomeStatus.RESET,
            bill=self.repo.get(bill.term, bill.number),
            note=f"{note}; project documents re-read from RCL" if read_again else note,
        )

    def observation_preview(self, bill: Bill, choice: str, *, min_score: int) -> str:
        has_card = self.publishing.has_sent_card(bill)
        mode, basis = operator_observation(
            choice,
            bill.analysis.analysis if bill.analysis else None,
            has_card=has_card,
            min_score=min_score,
        )
        note = f"was {observation_label(bill.observation_mode, bill.observation_basis)}, now"
        note += f" {observation_label(mode, basis)}"
        if has_card:
            note += "; the bill has a card, which is followed in full whatever its mode"
        return note

    def observe(self, bill: Bill, choice: str, *, min_score: int) -> CommandOutcome:
        note = self.observation_preview(bill, choice, min_score=min_score)
        mode, basis = operator_observation(
            choice,
            bill.analysis.analysis if bill.analysis else None,
            has_card=self.publishing.has_sent_card(bill),
            min_score=min_score,
        )
        self.repo.set_observation(bill.term, bill.number, mode, basis)
        return CommandOutcome(
            status=OutcomeStatus.OBSERVED,
            bill=self.repo.get(bill.term, bill.number),
            note=note,
        )

    def over_note(self, bill: Bill) -> str | None:
        """Why no card may be posted for this bill, when its road has ended. A card is an
        invitation to act, and there is nothing left to act on; a bill the Sejm has only passed
        is not over, because the Senate is next, and neither is one whose act is published but
        does not apply yet."""
        if bill.discontinued_at is not None:
            return "lapsed with the end of its Sejm term: not posted"
        if not is_over(bill, today=self.clock.now().date()):
            return None
        act = bill.act
        if act is not None and act.entry_into_force is not None:
            return f"{act.display_address} in force since {act.entry_into_force}: not posted"
        ended = bill.summary.closure_date
        # A dropped plan's "closure date" is the day the register first published it (the
        # register gives no withdrawal date at all), so it dates nothing a reader asked about.
        when = f" on {ended}" if ended is not None and bill.wykaz is None else ""
        return f"the process ended{when} ({self._outcome(bill)}): not posted"

    @staticmethod
    def _outcome(bill: Bill) -> str:
        """How the road ended, in the words the channel already tells apart: a rejection, a
        withdrawal by the applicant and a veto the Sejm could not override are three different
        answers to "why is there no card", and `closureDate` alone tells none of them apart."""
        if bill.summary.passed:
            return "passed"
        if bill.wykaz is not None:
            return "dropped from the government's plan"
        if bill.rcl is not None:
            return "closed on RCL without reaching the Sejm"
        return _CLOSURE_NOTES[closure_event(bill)]

    def preview(self, bill: Bill, to: str | None = None) -> CommandOutcome:
        """The message as the channel would get it, rendered into the technical channel alone:
        what `/republish` would send, before it is sent.

        `PublishingService.plan` decides and not this method, or the operator would be shown a
        plain card where the channel sends a joint reply, or nothing at all.
        """
        if bill.analysis is None:
            # A print whose group already holds a card is a reply, and a reply is a message a
            # preview can render whether or not this print has been read yet — which is the one
            # thing a preview exists to show.
            if primary_of(self.repo, bill, self.publishing.channel_id) is None:
                return CommandOutcome(
                    status=OutcomeStatus.ERROR, bill=bill, note="not analysed: no card to render"
                )
        elif bill.analysis.text_source == "excerpts":
            return CommandOutcome(
                status=OutcomeStatus.SHOWN,
                bill=bill,
                explanation=bill.analysis,
                note="Есть только предварительная оценка по фрагментам. "
                f"Для полного разбора: /explain {bill.number}",
            )
        card = self.card_label(bill)
        note = f"the card in the channel is {card}" if card else "not posted to the channel"
        if bill.analysis is not None and not bill.analysis.analysis.relevant:
            note += "; нерелевантный проект: только предпросмотр"
        plan = self.publishing.plan(bill)
        if plan.inherited is not None:
            note = (
                f"continues {bill.linked_number}: no message of its own, its card is"
                f" message {plan.inherited.message_id} re-rendered with both tags"
            )
        elif plan.primary is not None:
            note = (
                f"considered jointly with {plan.primary.number}: a reply under its card"
                f" (message {plan.primary_message_id}), not a card of its own"
            )
            if plan.bill.joint is None:
                # A read-only command spends nothing, and the comparison is a model call: it is
                # made when the reply is actually sent, so the preview shows the reply without it.
                note += "; what differs is compared when the reply goes out"
        if to is not None:
            note = self._preview_to(plan.bill, plan.print_info, to)
        return CommandOutcome(
            status=OutcomeStatus.PREVIEWED,
            bill=plan.bill,
            note=note,
            print_info=plan.print_info,
            joint_primary=plan.primary,
        )

    def _preview_to(self, bill: Bill, print_info: PrintInfo | None, chat: str) -> str:
        """Send an unrecorded card through the factory that excludes the reader channel."""
        if self.publisher_for is None:
            return f"cannot send to {chat}: this run publishes nowhere but its own channel"
        result = self.publisher_for(chat).publish_new_bill(bill, print_info)
        return f"sent to {chat} as message {result.message_id}; nothing was recorded"

    def republish(self, bill: Bill, *, publish: bool) -> CommandOutcome:
        if bill.analysis is None or not bill.analysis.analysis.relevant:
            return CommandOutcome(
                status=OutcomeStatus.ERROR, bill=bill, note="no relevant analysis: nothing to post"
            )
        if (over := self.over_note(bill)) is not None:
            return CommandOutcome(status=OutcomeStatus.ERROR, bill=bill, note=over)
        if not publish:
            return CommandOutcome(
                status=OutcomeStatus.ERROR, bill=bill, note="publishing is off in this run"
            )
        self.publishing.forget_card(bill)
        return self.post(bill, OutcomeStatus.REPUBLISHED)

    def forget(self, bill: Bill, *, min_score: int) -> CommandOutcome:
        """`/forget`: drop what the channel remembers of the card and post nothing in its place.

        The way out of a card deleted by hand: the `sent` row keeps the bill followed and the
        refresher keeps editing a message that is not there. `/republish` clears that too, but by
        sending the card again, which is wrong when it was deleted on purpose. What happens next
        is the publishing rule's decision, and running this twice changes nothing.
        """
        card = self.card_label(bill)
        if self.publishing.card_of(bill) is None:
            return CommandOutcome(
                status=OutcomeStatus.FORGOTTEN,
                bill=bill,
                note="the channel remembers no card for it; nothing to forget",
            )
        self.publishing.forget_card(bill)
        verdict = bill.analysis.analysis if bill.analysis is not None else None
        candidate = (
            bill.status is BillStatus.ANALYZED
            and bill.discontinued_at is None
            and verdict is not None
            and verdict.relevant
            and verdict.score >= min_score
        )
        forgotten = f"forgotten: {card}" if card is not None else "forgotten"
        next_run = (
            "the next run can post a fresh card"
            if candidate
            else f"it will not be posted again ({bill.status})"
        )
        return CommandOutcome(
            status=OutcomeStatus.FORGOTTEN, bill=bill, note=f"{forgotten}; {next_run}"
        )

    def post(self, bill: Bill, status: OutcomeStatus, *, may_wait: bool = False) -> CommandOutcome:
        """Send the card through the normal path (pending row first, joint prints share a
        thread) and tell which message it became."""
        result = PublishingResult()
        ok = self.publishing.publish_bill(bill, result, may_wait=may_wait)
        if not ok and result.waiting:
            return CommandOutcome(
                status=OutcomeStatus.QUEUED, bill=bill, note="Ожидается разбор совместных проектов."
            )
        posted = self.publishing.card_of(bill)
        if not ok or posted is None or posted.message_id is None:
            return CommandOutcome(
                status=OutcomeStatus.ERROR, bill=bill, note="posting the card failed, see the log"
            )
        note = "" if posted.kind is PublicationKind.NEW_BILL else "as a reply under the joint print"
        return CommandOutcome(
            status=status,
            bill=bill,
            message_id=posted.message_id,
            publication_id=posted.id,
            note=note,
        )

    def card_label(self, bill: Bill) -> str | None:
        """`message 123` when the bill's card (or its reply under a joint print) is sent."""
        pub = self.publishing.card_of(bill)
        if pub is None or pub.status is not PublicationStatus.SENT:
            return None
        return f"message {pub.message_id}"
