"""Operator commands from the technical channel, executed by a run.

Every command in the inbox gets a `commands` row before anything runs, is executed, marked
executed, answered under its own message in the technical channel, marked handled and taken
out of the inbox. The two marks are what a re-read inbox file is measured against: answered
means the file is only dropped, executed but unanswered means the answer is repeated and the
command is *not* run again (a second `/republish` would post a second card). An outage of a
source system or of the channel ends the phase and leaves the command for the next run; any
other failure is the command's own and is answered as such.
"""

import datetime as dt
import logging
import time
from dataclasses import dataclass, field

from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    Bill,
    BillStatus,
    Command,
    CommandName,
    CommandOutcome,
    CommandState,
    IncomingCommand,
    OutcomeStatus,
    PublicationKind,
    PublicationStatus,
    TokenUsage,
    is_over,
    parse_command,
)
from lexinform.ports import BillRepository, Clock, CommandInbox, OperatorReplier
from lexinform.services.analysis import AnalysisService, TooExpensiveError
from lexinform.services.lookup import BillLookup, BillNotFoundError
from lexinform.services.publishing import PublishingService
from lexinform.services.text_prefilter import TextPrefilterService

log = logging.getLogger(__name__)

SKIPPED = frozenset(
    {
        BillStatus.SKIPPED_PREFILTER,
        BillStatus.SKIPPED_TEXT_PREFILTER,
        BillStatus.SKIPPED_COST,
        BillStatus.SKIPPED_CLOSED,
    }
)
FORCE_HINT = "add `force` to analyse anyway"


def _day(when: dt.datetime | None) -> str:
    return str(when.date()) if when is not None else "an earlier run"


@dataclass
class CommandsResult:
    handled: int = 0
    failed: int = 0
    lines: list[str] = field(default_factory=list)  # one per command, for the run report
    input_tokens: int = 0
    output_tokens: int = 0
    usage: dict[str, TokenUsage] = field(default_factory=dict)
    fatal_error: str | None = None


class CommandService:
    def __init__(
        self,
        repo: BillRepository,
        inbox: CommandInbox,
        replier: OperatorReplier,
        lookup: BillLookup,
        analysis: AnalysisService,
        publishing: PublishingService,
        clock: Clock,
        *,
        text_prefilter: TextPrefilterService | None = None,
    ) -> None:
        self._repo = repo
        self._inbox = inbox
        self._replier = replier
        self._lookup = lookup
        self._analysis = analysis
        self._publishing = publishing
        self._clock = clock
        self._text_prefilter = text_prefilter

    def handle_pending(
        self,
        *,
        min_score: int,
        run_started_at: dt.datetime,
        publish: bool = True,
        dry_run: bool = False,
    ) -> CommandsResult:
        """Answer every waiting command; `run_started_at` is the run's own start, which every
        reply names. `dry_run` is the run's option, not the service's: the inbox is then left
        as it is, because its files belong to a real run (the database is rolled back with
        them)."""
        result = CommandsResult()
        for incoming in self._inbox.pending():
            earlier = self._earlier_state(incoming)
            if earlier is not None and earlier.handled_at is not None:
                log.info("command %d answered before; inbox file dropped", incoming.update_id)
                self._done(incoming, dry_run=dry_run)
                continue
            started = time.perf_counter()
            spent: dict[str, TokenUsage] = {}
            if earlier is not None:
                outcome = self._answer_of_an_earlier_run(earlier)
            else:
                try:
                    # `spent` is filled as the command goes, not returned: a command that raises
                    # halfway has still spent what it spent, and the report must say so.
                    outcome = self._execute(incoming, spent, min_score=min_score, publish=publish)
                except ServiceUnavailableError as exc:
                    result.fatal_error = exc.describe()
                    log.error("aborting commands phase: %s", result.fatal_error)
                    break
                except Exception as exc:
                    log.exception("command %d failed: %s", incoming.update_id, exc)
                    outcome = CommandOutcome(
                        status=OutcomeStatus.ERROR, note=f"{type(exc).__name__}: {exc}"
                    )
                self._repo.mark_command_executed(
                    incoming.update_id, outcome=outcome.line(), at=self._clock.now()
                )
            # What this one command took, so the reply can say it (the run report sums them up).
            outcome = outcome.model_copy(
                update={
                    "run_started_at": run_started_at,
                    "seconds": round(time.perf_counter() - started, 1),
                    "usage": spent,
                }
            )
            for model, tokens in spent.items():
                result.usage[model] = result.usage.get(model, TokenUsage()).plus(tokens)
                result.input_tokens += tokens.input + tokens.cache_read
                result.output_tokens += tokens.output
            try:
                self._answer(incoming, outcome, dry_run=dry_run)
            except ServiceUnavailableError as exc:
                # The command ran; only its answer did not reach the channel. It stays in the
                # inbox, marked executed, and the next run repeats the answer alone.
                result.fatal_error = exc.describe()
                log.error("aborting commands phase, %d unanswered: %s", incoming.update_id, exc)
                break
            result.handled += 1
            result.failed += int(not outcome.ok)
            result.lines.append(f"{incoming.text} → {outcome.line()}")
        return result

    def _earlier_state(self, incoming: IncomingCommand) -> CommandState | None:
        """What earlier runs did with this command, recording it when it is new. None means
        nobody has run it: the inbox file is its only trace."""
        if self._repo.record_command(incoming):
            return None
        return self._repo.command_state(incoming.update_id)

    @staticmethod
    def _answer_of_an_earlier_run(earlier: CommandState) -> CommandOutcome:
        """A command an earlier run took on. Running it again could post a second card, so its
        recorded outcome is repeated instead — and when the run died before recording one, the
        operator is told that and can send the command again, which is the safer way round."""
        if earlier.executed_at is None:
            return CommandOutcome(
                status=OutcomeStatus.EXECUTED_EARLIER,
                note=(
                    f"a run of {_day(earlier.received_at)} started this command and did not"
                    " finish; send it again if it left nothing behind"
                ),
            )
        return CommandOutcome(
            status=OutcomeStatus.EXECUTED_EARLIER,
            note=(
                f"the run of {earlier.executed_at.date()} ran this command but could not"
                f" answer: {earlier.reply or 'no record'}"
            ),
        )

    def _answer(self, incoming: IncomingCommand, outcome: CommandOutcome, *, dry_run: bool) -> None:
        line = outcome.line()
        try:
            self._replier.reply(incoming, outcome)
        except ServiceUnavailableError:
            raise  # the channel is down: the phase ends, the answer is repeated next run
        except Exception as exc:  # the answer failed, the command did not: do not run it again
            log.exception("reply to command %d failed: %s", incoming.update_id, exc)
            line += f" (reply failed: {exc})"
        self._repo.mark_command_handled(incoming.update_id, reply=line, at=self._clock.now())
        self._done(incoming, dry_run=dry_run)

    def _done(self, incoming: IncomingCommand, *, dry_run: bool) -> None:
        if not dry_run:
            self._inbox.done(incoming)

    def _execute(
        self,
        incoming: IncomingCommand,
        spent: dict[str, TokenUsage],
        *,
        min_score: int,
        publish: bool,
    ) -> CommandOutcome:
        command = parse_command(incoming.text)
        if command is None:
            return CommandOutcome(status=OutcomeStatus.HELP, note="not a command")
        if command.error or command.name is CommandName.HELP:
            return CommandOutcome(status=OutcomeStatus.HELP, note=command.error or "")
        assert command.ref is not None
        try:
            if command.name is CommandName.ANALYZE:
                bill = self._lookup.load_ref(command.ref)
                return self._analyze(bill, command, spent, min_score=min_score, publish=publish)
            bill_or_none = self._lookup.find_ref(command.ref)
        except BillNotFoundError as exc:
            return CommandOutcome(status=OutcomeStatus.NOT_FOUND, note=str(exc))
        if bill_or_none is None:
            return CommandOutcome(
                status=OutcomeStatus.NOT_FOUND,
                note=f"{command.ref.label} is not in the database; /analyze fetches it",
            )
        if command.name is CommandName.SHOW:
            return CommandOutcome(status=OutcomeStatus.SHOWN, bill=bill_or_none)
        if command.name is CommandName.SKIP:
            return self._skip(bill_or_none)
        return self._republish(bill_or_none, publish=publish)

    def _analyze(
        self,
        bill: Bill,
        command: Command,
        spent: dict[str, TokenUsage],
        *,
        min_score: int,
        publish: bool,
    ) -> CommandOutcome:
        if not command.force:
            if bill.status in SKIPPED:
                reason = bill.last_error or "title prefilter: no keyword hits"
                return CommandOutcome(
                    status=OutcomeStatus.SKIPPED, bill=bill, note=f"{reason}; {FORCE_HINT}"
                )
            if bill.status is BillStatus.TEXT_PREFILTER_PENDING and self._text_prefilter:
                accepted = self._text_prefilter.check(bill)
                bill = self._reload(bill)
                if not accepted:
                    reason = bill.last_error or "text prefilter: no keyword hits"
                    return CommandOutcome(
                        status=OutcomeStatus.SKIPPED, bill=bill, note=f"{reason}; {FORCE_HINT}"
                    )
        if command.force or bill.analysis is None or bill.status is not BillStatus.ANALYZED:
            try:
                analysed = self._analysis.analyze_bill(bill, ignore_cost_limit=command.force)
            except TooExpensiveError as exc:
                # The same answer the analysis phase gives a text over the per-bill limit, so
                # that the queue looks the same whoever hit the guard.
                self._repo.set_status(
                    bill.term,
                    bill.number,
                    BillStatus.SKIPPED_COST,
                    reason=f"analysis skipped: {exc}",
                )
                return CommandOutcome(
                    status=OutcomeStatus.SKIPPED,
                    bill=self._reload(bill),
                    note=f"{exc}; {FORCE_HINT}",
                )
            spent.update(analysed.usage)  # the triage counts too: the operator pays for both
            bill = self._reload(bill)
        assert bill.analysis is not None
        verdict = bill.analysis.analysis
        if not verdict.relevant:
            return CommandOutcome(
                status=OutcomeStatus.ANALYSED, bill=bill, note="not relevant: no card"
            )
        card = self._card(bill)
        if card is not None:
            return CommandOutcome(
                status=OutcomeStatus.ANALYSED,
                bill=bill,
                note=f"the card is in the channel already ({card})",
            )
        if (over := self._over_note(bill)) is not None:
            return CommandOutcome(status=OutcomeStatus.ANALYSED, bill=bill, note=over)
        if verdict.score < min_score and not command.publish:
            return CommandOutcome(
                status=OutcomeStatus.ANALYSED,
                bill=bill,
                note=f"importance {verdict.score} is under the threshold {min_score}:"
                " not posted; add `publish` to post anyway",
            )
        if not publish:
            return CommandOutcome(
                status=OutcomeStatus.ANALYSED, bill=bill, note="publishing is off in this run"
            )
        return self._post(bill, OutcomeStatus.ANALYSED)

    def _skip(self, bill: Bill) -> CommandOutcome:
        self._repo.reset_bill(
            bill.term,
            bill.number,
            BillStatus.SKIPPED_PREFILTER,
            reason="silenced by the operator (/skip)",
        )
        card = self._card(bill)
        note = "silenced: it will not be analysed or posted"
        if card is not None:
            note += f"; its card ({card}) stays and is still followed"
        return CommandOutcome(status=OutcomeStatus.SILENCED, bill=self._reload(bill), note=note)

    def _over_note(self, bill: Bill) -> str | None:
        """Why no card may be posted for this bill, when its road has ended. A card is an
        invitation to act, and there is nothing left to act on; a bill the Sejm has only passed
        is not over, because the Senate is next, and neither is one whose act is published but
        does not apply yet."""
        if bill.discontinued_at is not None:
            return "lapsed with the end of its Sejm term: not posted"
        if not is_over(bill, today=self._clock.now().date()):
            return None
        act = bill.act
        if act is not None and act.entry_into_force is not None:
            return f"{act.display_address} in force since {act.entry_into_force}: not posted"
        ended = bill.summary.closure_date
        outcome = "passed" if bill.summary.passed else "closed"
        when = f" on {ended}" if ended is not None else ""
        return f"the process ended{when} ({outcome}): not posted"

    def _republish(self, bill: Bill, *, publish: bool) -> CommandOutcome:
        if bill.analysis is None or not bill.analysis.analysis.relevant:
            return CommandOutcome(
                status=OutcomeStatus.ERROR, bill=bill, note="no relevant analysis: nothing to post"
            )
        if (over := self._over_note(bill)) is not None:
            return CommandOutcome(status=OutcomeStatus.ERROR, bill=bill, note=over)
        if not publish:
            return CommandOutcome(
                status=OutcomeStatus.ERROR, bill=bill, note="publishing is off in this run"
            )
        self._publishing.forget_card(bill)
        return self._post(bill, OutcomeStatus.REPUBLISHED)

    def _post(self, bill: Bill, status: OutcomeStatus) -> CommandOutcome:
        """Send the card through the normal path (pending row first, joint prints share a
        thread) and tell which message it became."""
        ok = self._publishing.publish_bill(bill)
        posted = self._publishing.card_of(bill)
        if not ok or posted is None or posted.message_id is None:
            return CommandOutcome(
                status=OutcomeStatus.ERROR, bill=bill, note="posting the card failed, see the log"
            )
        note = "" if posted.kind is PublicationKind.NEW_BILL else "as a reply under the joint print"
        return CommandOutcome(status=status, bill=bill, message_id=posted.message_id, note=note)

    def _reload(self, bill: Bill) -> Bill:
        fresh = self._repo.get(bill.term, bill.number)
        return fresh if fresh is not None else bill

    def _card(self, bill: Bill) -> str | None:
        """`message 123` when the bill's card (or its reply under a joint print) is sent."""
        pub = self._publishing.card_of(bill)
        if pub is None or pub.status is not PublicationStatus.SENT:
            return None
        return f"message {pub.message_id}"
