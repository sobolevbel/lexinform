"""Operator commands from the technical channel, executed by a run.

Every command gets a `commands` row before anything runs, is executed, marked executed, answered
under its own message, marked handled and taken out of the inbox. A re-read file is measured
against the two marks: answered means drop it, executed but unanswered means repeat the answer
and do *not* run it again. An outage ends the phase and leaves the command for the next run.
"""

import datetime as dt
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from lexinform.errors import OrkaUnreachableError, ServiceUnavailableError
from lexinform.models import (
    DISPATCHED,
    SILENCED_BY_OPERATOR,
    Bill,
    BillStatus,
    Command,
    CommandName,
    CommandOutcome,
    CommandState,
    IncomingCommand,
    OutcomeStatus,
    PrintInfo,
    PublicationKind,
    PublicationStatus,
    SpendSnapshot,
    StatusSnapshot,
    TokenUsage,
    closure_event,
    is_over,
    parse_command,
)
from lexinform.ports import BillRepository, Clock, CommandInbox, OperatorReplier, Publisher
from lexinform.pricing import cost_usd
from lexinform.services.analysis import AnalysisService, TooExpensiveError
from lexinform.services.digest import DigestService
from lexinform.services.joint import primary_of
from lexinform.services.lookup import BillLookup, BillNotFoundError
from lexinform.services.publishing import PublishingService
from lexinform.services.text_prefilter import TextPrefilterService
from lexinform.services.tracking import StatusTrackingService, TrackingResult

log = logging.getLogger(__name__)

SKIPPED = frozenset(
    {
        BillStatus.SKIPPED_PREFILTER,
        BillStatus.SKIPPED_TEXT_PREFILTER,
        BillStatus.SKIPPED_COST,
        BillStatus.SKIPPED_CLOSED,
    }
)
WAITING = (
    BillStatus.ANALYSIS_PENDING,
    BillStatus.TEXT_PREFILTER_PENDING,
    BillStatus.ANALYSIS_FAILED,
    BillStatus.SKIPPED_COST,
)
FORCE_HINT = "add `force` to analyse anyway"
_CLOSURE_NOTES = {
    "veto_sustained": "the President's veto stood",
    "withdrawn_by_applicant": "withdrawn by the applicant",
    "rejected": "rejected",
    "not_enacted": "closed without a law",
}


def _day(when: dt.datetime | None) -> str:
    return str(when.date()) if when is not None else "an earlier run"


class _SourceDownError(ServiceUnavailableError):
    """A watcher of `/refresh` stopped because its source is down. `TrackingResult.fatal_error`
    already names the system, so the message is passed on as it is."""

    def describe(self) -> str:
        return str(self)


def _tracking_note(result: TrackingResult) -> str:
    """What `/refresh` found, in the counters that mean something to a reader of the channel.

    A source that was down while the rest of the phase ran is named: RCL is unreachable from a
    GitHub-hosted runner altogether, so without this every `/refresh` of an `RCL/…` bill in the
    daily workflow would answer "nothing new" about a page nobody read.
    """
    posts = (
        result.published
        + result.acts_published
        + result.in_force_posted
        + result.agenda_posted
        + result.consultation_results_posted
    )
    parts = [
        f"{posts} post(s)" if posts else "",
        "card refreshed" if result.cards_refreshed else "",
        f"{result.reanalyzed} re-analysis" if result.reanalyzed else "",
        f"{result.held} stage(s) held for the next post" if result.held else "",
        f"{result.failed} failure(s), see the log" if result.failed else "",
    ]
    said = " · ".join(p for p in parts if p)
    if result.partial_errors:
        down = "; ".join(result.partial_errors)
        return f"{said or 'nothing new from the sources that answered'} · not read: {down}"
    return said or "nothing new: the card and the stages are as they were"


@dataclass
class CommandsResult:
    """What the commands phase did; `lines` holds one line per command for the run report."""

    handled: int = 0
    failed: int = 0
    lines: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    usage: dict[str, TokenUsage] = field(default_factory=dict)
    fatal_error: str | None = None


def _count_usage(result: CommandsResult, spent: dict[str, TokenUsage]) -> None:
    for model, tokens in spent.items():
        result.usage[model] = result.usage.get(model, TokenUsage()).plus(tokens)
        result.input_tokens += tokens.input + tokens.cache_read
        result.output_tokens += tokens.output


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
        tracking: StatusTrackingService | None = None,
        digest: DigestService | None = None,
        publisher_for: Callable[[str], Publisher] | None = None,
        status_days: int = 7,
        status_bills: int = 10,
    ) -> None:
        self._repo = repo
        self._inbox = inbox
        self._replier = replier
        self._lookup = lookup
        self._analysis = analysis
        self._publishing = publishing
        self._clock = clock
        self._text_prefilter = text_prefilter
        self._tracking = tracking
        self._digest = digest
        self._publisher_for = publisher_for
        self._status_days = status_days
        self._status_bills = status_bills

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
            if not self._handle_one(
                incoming,
                result,
                min_score=min_score,
                run_started_at=run_started_at,
                publish=publish,
                dry_run=dry_run,
            ):
                break
        return result

    def _handle_one(
        self,
        incoming: IncomingCommand,
        result: CommandsResult,
        *,
        min_score: int,
        run_started_at: dt.datetime,
        publish: bool,
        dry_run: bool,
    ) -> bool:
        """Answer one command; False when the phase must stop.

        A command an earlier run already answered is only dropped from the inbox. When the answer
        is the one that cannot be delivered, the command stays in the inbox marked executed, and
        the next run repeats the answer alone.
        """
        earlier = self._earlier_state(incoming)
        if earlier is not None and earlier.handled_at is not None:
            log.info("command %d answered before; inbox file dropped", incoming.update_id)
            self._done(incoming, dry_run=dry_run)
            return True
        started = time.perf_counter()
        spent: dict[str, TokenUsage] = {}
        if earlier is not None:
            outcome = self._answer_of_an_earlier_run(earlier)
        else:
            ran = self._run_now(incoming, spent, result, min_score=min_score, publish=publish)
            if ran is None:
                return False
            outcome = ran
        outcome = outcome.model_copy(
            update={
                "run_started_at": run_started_at,
                "seconds": round(time.perf_counter() - started, 1),
                "usage": spent,
            }
        )
        _count_usage(result, spent)
        try:
            self._answer(incoming, outcome, dry_run=dry_run)
        except ServiceUnavailableError as exc:
            result.fatal_error = exc.describe()
            log.error("aborting commands phase, %d unanswered: %s", incoming.update_id, exc)
            return False
        result.handled += 1
        result.failed += int(not outcome.ok)
        result.lines.append(f"{incoming.text} → {outcome.line()}")
        return True

    def _run_now(
        self,
        incoming: IncomingCommand,
        spent: dict[str, TokenUsage],
        result: CommandsResult,
        *,
        min_score: int,
        publish: bool,
    ) -> CommandOutcome | None:
        """Run the command and record that it ran; None when a source is down and the phase must
        stop. `spent` is filled as the command goes rather than returned: a command that raises
        halfway has still spent what it spent, and the report must say so."""
        try:
            outcome = self._execute(incoming, spent, min_score=min_score, publish=publish)
        except ServiceUnavailableError as exc:
            result.fatal_error = exc.describe()
            log.error("aborting commands phase: %s", result.fatal_error)
            return None
        except Exception as exc:
            log.exception("command %d failed: %s", incoming.update_id, exc)
            outcome = CommandOutcome(
                status=OutcomeStatus.ERROR, note=f"{type(exc).__name__}: {exc}"
            )
        self._repo.mark_command_executed(
            incoming.update_id, outcome=outcome.line(), at=self._clock.now()
        )
        return outcome

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
        """Reply under the command's own message and drop its inbox file.

        An outage of the channel propagates: the phase ends and the answer is repeated next run.
        A reply that failed for any other reason is noted and the command is marked handled all
        the same — the command itself ran, and running it again could post a second card.
        """
        line = outcome.line()
        try:
            self._replier.reply(incoming, outcome)
        except ServiceUnavailableError:
            raise
        except Exception as exc:
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
        if command.name is CommandName.STATUS:
            return self._status()
        if command.name is CommandName.RUNS:
            return self._runs(command.count("days", 30))
        if command.name is CommandName.COST:
            return self._cost(command.count("days", 30), command.count("top", 5))
        if command.name is CommandName.FIND:
            assert command.query is not None
            return self._find(command.query)
        if command.name is CommandName.DIGEST:
            return self._digest_command(command, publish=publish)
        if command.name in DISPATCHED:
            # The relay starts the workflow itself and files nothing, so this can only be an
            # old file or a hand-written one; either way there is nothing here to execute.
            return CommandOutcome(
                status=OutcomeStatus.HELP,
                note=f"/{command.name} is the relay's own command: it starts this workflow with"
                " that phase, and a run cannot ask itself for one",
            )
        assert command.ref is not None
        try:
            if command.name is CommandName.ANALYZE:
                bill = self._lookup.load_ref(command.ref)
                done = self._analyze(bill, command, spent, min_score=min_score, publish=publish)
                return done.model_copy(update={"as_json": command.as_json})
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
        if command.name is CommandName.UNSKIP:
            return self._unskip(bill_or_none)
        if command.name is CommandName.RESET:
            wanted = command.options.get("to", BillStatus.ANALYSIS_PENDING.value)
            return self._reset(bill_or_none, BillStatus(wanted))
        if command.name is CommandName.PREVIEW:
            return self._preview(bill_or_none, command.options.get("to"))
        if command.name is CommandName.REFRESH:
            return self._refresh(bill_or_none, spent, publish=publish)
        if command.name is CommandName.FORGET:
            return self._forget(bill_or_none, min_score=min_score)
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
        """`/analyze`: the prefilter, the model and the publishing rule, as the daily run applies
        them; `force` gets past the first two, `publish` past the score threshold."""
        if not command.force:
            bill, skipped = self._skipped_by_prefilter(bill)
            if skipped is not None:
                return skipped
        if command.force or bill.analysis is None or bill.status is not BillStatus.ANALYZED:
            bill, too_expensive = self._analyse_now(bill, spent, force=command.force)
            if too_expensive is not None:
                return too_expensive
        assert bill.analysis is not None
        return self._card_verdict(bill, command, min_score=min_score, publish=publish)

    def _skipped_by_prefilter(self, bill: Bill) -> tuple[Bill, CommandOutcome | None]:
        """The prefilter's answer, with the row as it stands after the text stage may have run."""
        if bill.status in SKIPPED:
            reason = bill.last_error or "title prefilter: no keyword hits"
            return bill, CommandOutcome(
                status=OutcomeStatus.SKIPPED, bill=bill, note=f"{reason}; {FORCE_HINT}"
            )
        if bill.status is BillStatus.TEXT_PREFILTER_PENDING and self._text_prefilter:
            accepted = self._text_prefilter.check(bill)
            bill = self._reload(bill)
            if not accepted:
                reason = bill.last_error or "text prefilter: no keyword hits"
                return bill, CommandOutcome(
                    status=OutcomeStatus.SKIPPED, bill=bill, note=f"{reason}; {FORCE_HINT}"
                )
        return bill, None

    def _analyse_now(
        self, bill: Bill, spent: dict[str, TokenUsage], *, force: bool
    ) -> tuple[Bill, CommandOutcome | None]:
        """Ask the model, and count the triage too: the operator pays for both.

        A text over the per-bill limit gets the answer the analysis phase gives it, so that the
        queue looks the same whoever hit the guard, and so does a file the host would not hand
        over: the operator is told to ask again rather than being given a verdict on a text
        nobody read, and `force` is no answer to a WAF.
        """
        try:
            analysed = self._analysis.analyze_bill(bill, ignore_cost_limit=force)
        except OrkaUnreachableError as exc:
            return bill, CommandOutcome(
                status=OutcomeStatus.SKIPPED,
                bill=self._reload(bill),
                note=f"{exc}; the bill stays queued, send the command again later",
            )
        except TooExpensiveError as exc:
            self._repo.set_status(
                bill.term,
                bill.number,
                BillStatus.SKIPPED_COST,
                reason=f"analysis skipped: {exc}",
            )
            return bill, CommandOutcome(
                status=OutcomeStatus.SKIPPED,
                bill=self._reload(bill),
                note=f"{exc}; {FORCE_HINT}",
            )
        spent.update(analysed.usage)
        return self._reload(bill), None

    def _card_verdict(
        self, bill: Bill, command: Command, *, min_score: int, publish: bool
    ) -> CommandOutcome:
        """Whether the analysis earns a card now, and what to say when it does not."""
        verdict = bill.analysis.analysis if bill.analysis is not None else None
        assert verdict is not None
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
            reason=SILENCED_BY_OPERATOR,
        )
        card = self._card(bill)
        note = "silenced: it will not be analysed or posted"
        if card is not None:
            note += f"; its card ({card}) stays and is still followed"
        return CommandOutcome(status=OutcomeStatus.SILENCED, bill=self._reload(bill), note=note)

    def _unskip(self, bill: Bill) -> CommandOutcome:
        """The way back from `/skip` — and from any other skip the operator disagrees with: the
        bill queues for the next run's analysis with a clean budget of attempts.

        A skipped RCL row keeps only the project's skeleton, so its documents are read again
        first: after the status is cleared, an unreachable RCL would leave the bill queued to be
        analysed on its metadata alone.
        """
        if bill.status is BillStatus.ANALYZED:
            return CommandOutcome(
                status=OutcomeStatus.QUEUED,
                bill=bill,
                note="already analysed; /analyze BILL force asks the model again",
            )
        if bill.status is BillStatus.LINKED:
            # The row has handed its thread to its druk, which now renders that message id.
            # Queueing it again would pay for the analysis of a row nobody follows and put it
            # back into `CardRefresher`, where the two rows edit one card from two states.
            return CommandOutcome(
                status=OutcomeStatus.QUEUED,
                bill=bill,
                note="linked: its card belongs to"
                f" {bill.linked_number or 'the bill that continues it'}, ask for that one",
            )
        self._revive_rcl(bill, BillStatus.ANALYSIS_PENDING)
        self._repo.reset_bill(
            bill.term,
            bill.number,
            BillStatus.ANALYSIS_PENDING,
            reason="queued by the operator (/unskip)",
        )
        return CommandOutcome(
            status=OutcomeStatus.QUEUED,
            bill=self._reload(bill),
            note=f"was {bill.status}; the next run analyses it",
        )

    def _reset(self, bill: Bill, to: BillStatus) -> CommandOutcome:
        """`/reset BILL to=STATUS`: any status with a clean budget of attempts, what `/unskip`
        does for the one status worth a word of its own.

        Analyses and posts are untouched, so a bill reset to `analysis_pending` is read again
        and one reset to `skipped_prefilter` is silenced while its card stays.
        """
        was = bill.status
        read_again = self._revive_rcl(bill, to)
        self._repo.reset_bill(bill.term, bill.number, to, reason="reset by the operator (/reset)")
        note = f"was {was} (attempts {bill.analysis_attempts}), now {to}"
        return CommandOutcome(
            status=OutcomeStatus.RESET,
            bill=self._reload(bill),
            note=f"{note}; project documents re-read from RCL" if read_again else note,
        )

    def _revive_rcl(self, bill: Bill, to: BillStatus) -> bool:
        """A skipped RCL row keeps only the project's skeleton: read its documents again before
        the status is cleared, or an unreachable RCL leaves the bill queued on its metadata."""
        if to is not BillStatus.ANALYSIS_PENDING or bill.rcl is None or bill.rcl.text_documents():
            return False
        self._repo.save_rcl(bill.term, bill.number, self._lookup.read_rcl_project(bill.number))
        return True

    def _preview(self, bill: Bill, to: str | None = None) -> CommandOutcome:
        """The message as the channel would get it, rendered into the technical channel alone:
        what `/republish` would send, before it is sent.

        `PublishingService.plan` decides and not this method, or the operator would be shown a
        plain card where the channel sends a joint reply, or nothing at all.
        """
        if bill.analysis is None:
            # A print whose group already holds a card is a reply, and a reply is a message a
            # preview can render whether or not this print has been read yet — which is the one
            # thing a preview exists to show.
            if primary_of(self._repo, bill, self._publishing.channel_id) is None:
                return CommandOutcome(
                    status=OutcomeStatus.ERROR, bill=bill, note="not analysed: no card to render"
                )
        elif not bill.analysis.analysis.relevant:
            return CommandOutcome(
                status=OutcomeStatus.ERROR,
                bill=bill,
                note="not relevant: the channel would never get this card",
            )
        card = self._card(bill)
        note = f"the card in the channel is {card}" if card else "not posted to the channel"
        plan = self._publishing.plan(bill)
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
        """`/preview BILL to=CHAT`: the card into a test chat, `lexinform preview --to`'s twin.

        It is a card and not a publication: no `publications` row, so nothing about the channel
        changes and the bill is no more published afterwards than before.
        """
        if self._publisher_for is None:
            return f"cannot send to {chat}: this run publishes nowhere but its own channel"
        result = self._publisher_for(chat).publish_new_bill(bill, print_info)
        return f"sent to {chat} as message {result.message_id}; nothing was recorded"

    def _refresh(
        self, bill: Bill, spent: dict[str, TokenUsage], *, publish: bool
    ) -> CommandOutcome:
        """Everything the next run would look at for this bill, now: its stages, its source's
        watcher and its card. The reader waits for the Sejm, not for our schedule.

        A bill with no card is refused: every poster reads that card for the message to reply
        under, so tracking one would drop a status update into the channel on its own.
        """
        if self._tracking is None:
            return CommandOutcome(
                status=OutcomeStatus.ERROR, bill=bill, note="tracking is off in this run"
            )
        if publish and self._card(bill) is None:
            return CommandOutcome(
                status=OutcomeStatus.ERROR,
                bill=bill,
                note="no card in the channel: an update would have nothing to reply under."
                " /analyze BILL publish posts the card first",
            )
        result = self._tracking.check_bill(bill, publish=publish)
        for model, tokens in result.usage.items():
            spent[model] = spent.get(model, TokenUsage()).plus(tokens)
        if result.fatal_error is not None:
            raise _SourceDownError(result.fatal_error)
        return CommandOutcome(
            status=OutcomeStatus.REFRESHED, bill=self._reload(bill), note=_tracking_note(result)
        )

    def _status(self) -> CommandOutcome:
        """The queues, what is stuck and what the recent runs cost — the state between the run
        reports, which each say what one run did and nothing about what has piled up."""
        since = self._clock.now() - dt.timedelta(days=self._status_days)
        snapshot = StatusSnapshot(
            bills=self._repo.count_by_status(),
            publications=self._repo.count_publications(self._publishing.channel_id),
            followed=len(self._tracking.followed()) if self._tracking is not None else 0,
            waiting=tuple(self._repo.list_by_status(list(WAITING), limit=self._status_bills)),
            runs=tuple(self._repo.list_runs(since=since)),
            days=self._status_days,
        )
        return CommandOutcome(
            status=OutcomeStatus.REPORTED,
            snapshot=snapshot,
            note=f"{snapshot.followed} bills followed",
        )

    def _runs(self, days: int) -> CommandOutcome:
        """`/runs`: what each recorded run of the window found, posted and cost — `lexinform
        runs` in the channel, for the operator who has no shell open."""
        reports = self._repo.list_runs(since=self._clock.now() - dt.timedelta(days=days))
        return CommandOutcome(
            status=OutcomeStatus.LISTED,
            runs=tuple(reports),
            note=f"{len(reports)} run(s) in {days} days"
            if reports
            else f"no runs recorded in the last {days} days",
        )

    def _cost(self, days: int, top: int) -> CommandOutcome:
        """`/cost`: the window's model spend, per model, its dearest run and its dearest bills."""
        reports = self._repo.list_runs(since=self._clock.now() - dt.timedelta(days=days))
        usage: dict[str, TokenUsage] = {}
        for report in reports:
            for model, spent in report.llm_usage.items():
                usage[model] = usage.get(model, TokenUsage()).plus(spent)
        snapshot = SpendSnapshot(
            days=days,
            runs=len(reports),
            usage=usage,
            dearest=max(reports, key=lambda r: cost_usd(r.llm_usage) or 0.0) if reports else None,
            priciest=tuple(self._repo.most_expensive_analyses(limit=top)) if top else (),
        )
        return CommandOutcome(
            status=OutcomeStatus.SPENT,
            spend=snapshot,
            note=f"{len(reports)} run(s) in {days} days",
        )

    def _find(self, query: str) -> CommandOutcome:
        found = self._repo.search(query, limit=self._status_bills)
        if not found:
            return CommandOutcome(
                status=OutcomeStatus.FOUND, note=f"nothing in the database matches {query!r}"
            )
        return CommandOutcome(
            status=OutcomeStatus.FOUND,
            found=tuple(found),
            note=f"{len(found)} match(es) for {query!r}",
        )

    def _digest_command(self, command: Command, *, publish: bool) -> CommandOutcome:
        """`/digest` drafts the week into this channel; `publish` is what the button presses.

        A press that arrives twice costs nothing: the command row is answered rather than run a
        second time, and the digest's own publication row would refuse a second post anyway.
        """
        if self._digest is None:
            return CommandOutcome(
                status=OutcomeStatus.DIGESTED,
                note="the digest is off (LEXINFORM_DIGEST_ENABLED) or has no technical channel",
            )
        ref = command.options.get("ref") or self._digest.current_ref()
        if not command.publish:
            done = self._digest.draft(ref)
            note = done.note or f"draft of {ref} is in this channel; press the button to send it"
            return CommandOutcome(
                status=OutcomeStatus.DIGESTED, note=note, message_id=done.message_id
            )
        if not publish:
            return CommandOutcome(
                status=OutcomeStatus.DIGESTED, note=f"{ref} not posted: this run does not publish"
            )
        done = self._digest.publish(ref)
        note = done.note or f"{ref} posted to the channel"
        return CommandOutcome(status=OutcomeStatus.DIGESTED, note=note, message_id=done.message_id)

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

    def _forget(self, bill: Bill, *, min_score: int) -> CommandOutcome:
        """`/forget`: drop what the channel remembers of the card and post nothing in its place.

        The way out of a card deleted by hand: the `sent` row keeps the bill followed and the
        refresher keeps editing a message that is not there. `/republish` clears that too, but by
        sending the card again, which is wrong when it was deleted on purpose. What happens next
        is the publishing rule's decision, and running this twice changes nothing.
        """
        card = self._card(bill)
        if self._publishing.card_of(bill) is None:
            return CommandOutcome(
                status=OutcomeStatus.FORGOTTEN,
                bill=bill,
                note="the channel remembers no card for it; nothing to forget",
            )
        self._publishing.forget_card(bill)
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
            "the next run posts a fresh card"
            if candidate
            else f"it will not be posted again ({bill.status})"
        )
        return CommandOutcome(
            status=OutcomeStatus.FORGOTTEN, bill=bill, note=f"{forgotten}; {next_run}"
        )

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
