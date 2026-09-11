"""Operator commands from the technical channel, executed by a run.

Every command in the inbox gets a `commands` row before anything runs (an inbox file the
workflow could not delete is not executed twice), is executed, answered under its own message
in the technical channel, marked handled and taken out of the inbox. An outage of a source
system ends the phase and leaves the command for the next run; any other failure is the
command's own and is answered as such.
"""

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
    IncomingCommand,
    OutcomeStatus,
    Publication,
    PublicationKind,
    PublicationStatus,
    TokenUsage,
    parse_command,
)
from lexinform.ports import BillRepository, Clock, CommandInbox, OperatorReplier
from lexinform.services.analysis import AnalysisService
from lexinform.services.lookup import BillLookup, BillNotFoundError
from lexinform.services.publishing import PublishingService
from lexinform.services.text_prefilter import TextPrefilterService

log = logging.getLogger(__name__)

SKIPPED = frozenset(
    {BillStatus.SKIPPED_PREFILTER, BillStatus.SKIPPED_TEXT_PREFILTER, BillStatus.SKIPPED_COST}
)
FORCE_HINT = "add `force` to analyse anyway"


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
        channel_id: str,
        text_prefilter: TextPrefilterService | None = None,
    ) -> None:
        self._repo = repo
        self._inbox = inbox
        self._replier = replier
        self._lookup = lookup
        self._analysis = analysis
        self._publishing = publishing
        self._clock = clock
        self._channel_id = channel_id
        self._text_prefilter = text_prefilter
        self._dry_run = False

    def handle_pending(
        self, *, min_score: int, publish: bool = True, dry_run: bool = False
    ) -> CommandsResult:
        """Answer every waiting command. In a dry run the inbox is left as it is: the files
        belong to a real run (the database is rolled back with them)."""
        self._dry_run = dry_run
        run_started_at = self._clock.now()
        result = CommandsResult()
        for incoming in self._inbox.pending():
            if not self._repo.record_command(incoming) and self._repo.command_handled(
                incoming.update_id
            ):
                log.info("command %d handled before; inbox file dropped", incoming.update_id)
                self._done(incoming)
                continue
            started = time.perf_counter()
            spent: dict[str, TokenUsage] = {}
            try:
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
            self._answer(incoming, outcome)
            result.handled += 1
            result.failed += int(not outcome.ok)
            result.lines.append(f"{incoming.text} → {outcome.line()}")
        return result

    def _answer(self, incoming: IncomingCommand, outcome: CommandOutcome) -> None:
        line = outcome.line()
        try:
            self._replier.reply(incoming, outcome)
        except ServiceUnavailableError:
            raise
        except Exception as exc:  # the answer failed, the command did not: do not run it again
            log.exception("reply to command %d failed: %s", incoming.update_id, exc)
            line += f" (reply failed: {exc})"
        self._repo.mark_command_handled(incoming.update_id, reply=line, at=self._clock.now())
        self._done(incoming)

    def _done(self, incoming: IncomingCommand) -> None:
        if not self._dry_run:
            self._inbox.done(incoming)

    # ------------------------------------------------------------------ the commands

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
            analysed = self._analysis.analyze_bill(bill, ignore_cost_limit=command.force)
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
        if bill.discontinued_at is not None:
            return CommandOutcome(
                status=OutcomeStatus.ANALYSED,
                bill=bill,
                note="lapsed with the end of its Sejm term: not posted",
            )
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
        self._repo.reset_bill(bill.term, bill.number, BillStatus.SKIPPED_PREFILTER)
        card = self._card(bill)
        note = "silenced: it will not be analysed or posted"
        if card is not None:
            note += f"; its card ({card}) stays and is still followed"
        return CommandOutcome(status=OutcomeStatus.SILENCED, bill=self._reload(bill), note=note)

    def _republish(self, bill: Bill, *, publish: bool) -> CommandOutcome:
        if bill.analysis is None or not bill.analysis.analysis.relevant:
            return CommandOutcome(
                status=OutcomeStatus.ERROR, bill=bill, note="no relevant analysis: nothing to post"
            )
        if not publish:
            return CommandOutcome(
                status=OutcomeStatus.ERROR, bill=bill, note="publishing is off in this run"
            )
        for kind in (PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL):
            self._repo.delete_publication(bill.term, bill.number, kind.value, self._channel_id)
        return self._post(bill, OutcomeStatus.REPUBLISHED)

    def _post(self, bill: Bill, status: OutcomeStatus) -> CommandOutcome:
        """Send the card through the normal path (pending row first, joint prints share a
        thread) and tell which message it became."""
        ok = self._publishing.publish_bill(bill)
        posted = self._publication(bill)
        if not ok or posted is None or posted.message_id is None:
            return CommandOutcome(
                status=OutcomeStatus.ERROR, bill=bill, note="posting the card failed, see the log"
            )
        note = "" if posted.kind is PublicationKind.NEW_BILL else "as a reply under the joint print"
        return CommandOutcome(status=status, bill=bill, message_id=posted.message_id, note=note)

    # ------------------------------------------------------------------ helpers

    def _reload(self, bill: Bill) -> Bill:
        fresh = self._repo.get(bill.term, bill.number)
        return fresh if fresh is not None else bill

    def _publication(self, bill: Bill) -> Publication | None:
        for kind in (PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL):
            pub = self._repo.get_publication(bill.term, bill.number, kind.value, self._channel_id)
            if pub is not None:
                return pub
        return None

    def _card(self, bill: Bill) -> str | None:
        """`message 123` when the bill's card (or its reply under a joint print) is sent."""
        pub = self._publication(bill)
        if pub is None or pub.status is not PublicationStatus.SENT:
            return None
        return f"message {pub.message_id}"
