"""Operator commands from the technical channel, executed by a run.

Every command gets a `commands` row before anything runs, is executed, marked executed, answered
under its own message, marked handled and taken out of the inbox. A re-read file is measured
against the two marks: answered means drop it, executed but unanswered means repeat the answer
and do *not* run it again. An outage ends the phase and leaves the command for the next run.
"""

import datetime as dt
import html
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from lexinform.content_versions import is_grouped_stage_revision_slot
from lexinform.errors import (
    DeliveryUncertainError,
    OrkaUnreachableError,
    ServiceUnavailableError,
    StateCheckpointError,
)
from lexinform.log_context import log_context
from lexinform.models import (
    DISPATCHED,
    SILENCED_BY_OPERATOR,
    AnalysisRecord,
    Bill,
    BillStatus,
    Command,
    CommandName,
    CommandOutcome,
    CommandState,
    CommandWork,
    DeliveryResolution,
    IncomingCommand,
    Mnemonic,
    OutcomeStatus,
    PriorityOverride,
    PublicationKind,
    PublicationStatus,
    ResultApplicationTarget,
    TokenUsage,
    merge_usage,
    parse_command,
    valid_mnemonic,
)
from lexinform.models.commands import COMMANDS, GroupedEditDeliveryResolution, command_help
from lexinform.models.grouped_stage_date import (
    GroupedStageDateCommand,
    GroupedStageResolutionCommand,
)
from lexinform.ports import (
    BillRepository,
    Clock,
    CommandInbox,
    OperatorReplier,
    Publisher,
    StateCheckpoint,
    VerifiedEditOutcome,
)
from lexinform.services.amendments import AmendmentsService
from lexinform.services.analysis import AnalysisService, TooExpensiveError, Waiting
from lexinform.services.bill_operations import BillOperations
from lexinform.services.command_queries import CommandQueries
from lexinform.services.delivery_commands import DeliveryCommands
from lexinform.services.digest import DigestResult, DigestService
from lexinform.services.grouped_stage_date_approval import (
    approve_grouped_stage_date,
    preview_grouped_stage_date,
)
from lexinform.services.grouped_stage_date_candidate import GroupedStageBulletRenderer
from lexinform.services.joint import primary_of
from lexinform.services.lookup import BillLookup, BillNotFoundError
from lexinform.services.publishing import PublishingService
from lexinform.services.result_applications import record_secondary_application
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
FORCE_HINT = "add `force` to analyse anyway"


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
    merge_usage(result.usage, spent)
    for tokens in spent.values():
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
        amendments: AmendmentsService | None = None,
        digest: DigestService | None = None,
        publisher_for: Callable[[str], Publisher] | None = None,
        status_days: int = 7,
        status_bills: int = 10,
        language: str = "ru",
        checkpoint: StateCheckpoint | None = None,
        source_context_retry: Callable[[DeliveryResolution], bool] | None = None,
        grouped_stage_formatter: GroupedStageBulletRenderer | None = None,
        grouped_stage_editor: Callable[[int, int, str], VerifiedEditOutcome] | None = None,
        grouped_stage_resolution_enabled: bool = False,
        grouped_stage_candidate_refs_enabled: bool = False,
        workflow_url: str | None = None,
    ) -> None:
        self._repo = repo
        self._inbox = inbox
        self._replier = replier
        self._lookup = lookup
        self._analysis = analysis
        self._publishing = publishing
        self._clock = clock
        self._language = language
        self._text_prefilter = text_prefilter
        self._tracking = tracking
        self._amendments = amendments
        self._digest = digest
        self._bills = BillOperations(repo, publishing, lookup, clock, publisher_for)
        self._checkpoint = checkpoint
        self._grouped_stage_formatter = grouped_stage_formatter
        self._grouped_stage_editor = grouped_stage_editor
        self._grouped_stage_resolution_enabled = grouped_stage_resolution_enabled
        self._grouped_stage_candidate_refs_enabled = grouped_stage_candidate_refs_enabled
        self._deliveries = DeliveryCommands(
            repo, clock, publishing.channel_id, digest, source_context_retry
        )
        self._queries = CommandQueries(
            repo,
            inbox,
            clock,
            publishing.channel_id,
            tracking,
            days=status_days,
            limit=status_bills,
        )
        self._workflow_url = workflow_url

    def handle_pending(
        self,
        *,
        min_score: int,
        run_started_at: dt.datetime,
        publish: bool = True,
        dry_run: bool = False,
        keep_delivery_attempts: bool = False,
    ) -> CommandsResult:
        """Answer every waiting command; `run_started_at` is the run's own start, which every
        reply names. `dry_run` is the run's option, not the service's: the inbox is then left
        as it is, because its files belong to a real run (the database is rolled back with
        them)."""
        result = CommandsResult()
        pending = self._inbox.pending()
        if not keep_delivery_attempts:
            self._publishing.start_run()
            if self._digest is not None:
                self._digest.start_run()
        for incoming in pending:
            if incoming.run_requested:
                continue
            with log_context(command_id=incoming.update_id):
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
        if earlier is None:
            self._save_state(dry_run=dry_run)
        if earlier is not None and earlier.handled_at is not None:
            log.info("command %d answered before; inbox file dropped", incoming.update_id)
            self._done(incoming, dry_run=dry_run)
            return True
        started = time.perf_counter()
        spent: dict[str, TokenUsage] = {}
        if earlier is not None:
            outcome = self._answer_of_an_earlier_run(earlier).model_copy(
                update={"rendered_reply": earlier.rendered_reply}
            )
        else:
            ran = self._run_now(
                incoming, spent, result, min_score=min_score, publish=publish, dry_run=dry_run
            )
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
        self._save_state(dry_run=dry_run)
        try:
            self._answer(
                incoming, outcome, dry_run=dry_run, saved_line=earlier.reply if earlier else None
            )
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
        dry_run: bool,
    ) -> CommandOutcome | None:
        """Run the command and record that it ran; None when a source is down and the phase must
        stop. `spent` is filled as the command goes rather than returned: a command that raises
        halfway has still spent what it spent, and the report must say so."""
        try:
            outcome = self._execute(
                incoming, spent, min_score=min_score, publish=publish, dry_run=dry_run
            )
        except StateCheckpointError:
            raise
        except ServiceUnavailableError as exc:
            result.fatal_error = exc.describe()
            log.error("aborting commands phase: %s", result.fatal_error)
            return None
        except Exception as exc:
            log.exception("command %d failed: %s", incoming.update_id, exc)
            outcome = CommandOutcome(
                status=OutcomeStatus.ERROR, note=f"{type(exc).__name__}: {exc}"
            )
        with self._repo.atomic():
            self._repo.mark_command_executed(
                incoming.update_id,
                outcome=outcome.line(),
                at=self._clock.now(),
                work=outcome.work,
                rendered_reply=self._replier.render(incoming, outcome),
                failed=not outcome.ok,
            )
            if outcome.explanation is not None and outcome.bill is not None:
                record_secondary_application(
                    self._repo,
                    outcome.bill.term,
                    outcome.bill.number,
                    outcome.explanation.computation_id,
                    target=ResultApplicationTarget(kind="command", ref=str(incoming.update_id)),
                    now=self._clock.now(),
                )
        return outcome

    def _earlier_state(self, incoming: IncomingCommand) -> CommandState | None:
        """What earlier runs did with this command, recording it when it is new. None means
        nobody has run it: the inbox file is its only trace."""
        if self._repo.record_command(incoming, workflow_url=self._workflow_url):
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

    def _answer(
        self,
        incoming: IncomingCommand,
        outcome: CommandOutcome,
        *,
        dry_run: bool,
        saved_line: str | None = None,
    ) -> None:
        """Reply under the command's own message and drop its inbox file.

        An outage of the channel propagates: the phase ends and the answer is repeated next run.
        A reply that failed for any other reason is noted and the command is marked handled all
        the same — the command itself ran, and running it again could post a second card.
        """
        line = saved_line or outcome.line()
        try:
            self._replier.reply(incoming, outcome)
        except StateCheckpointError:
            raise
        except DeliveryUncertainError:
            line += " (исход отправки технического ответа неизвестен)"
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.exception("reply to command %d failed: %s", incoming.update_id, exc)
            line += f" (reply failed: {exc})"
        self._repo.mark_command_handled(incoming.update_id, reply=line, at=self._clock.now())
        self._done(incoming, dry_run=dry_run)

    def _done(self, incoming: IncomingCommand, *, dry_run: bool) -> None:
        if not dry_run:
            self._save_state(dry_run=False)
            self._inbox.done(incoming)

    def _save_state(self, *, dry_run: bool) -> None:
        if not dry_run and self._checkpoint is not None:
            self._checkpoint()

    def complete_work(self, *, min_score: int, publish: bool) -> None:
        for state in self._repo.pending_command_work():
            incoming = state.incoming
            assert incoming is not None, "pending work belongs to a recorded command"
            if state.handled_at is None:
                continue
            if state.final_answer_pending:
                outcome = self._answer_of_an_earlier_run(state).model_copy(
                    update={"rendered_reply": state.rendered_reply}
                )
            else:
                work = state.work
                assert work is not None, "the repository selects pending work or a pending answer"
                resolved = self._work_result(incoming, work, min_score=min_score, publish=publish)
                if resolved is None:
                    continue
                outcome = resolved
                with self._repo.atomic():
                    self._repo.mark_command_executed(
                        incoming.update_id,
                        outcome=outcome.line(),
                        at=self._clock.now(),
                        work=outcome.work,
                        publication_id=outcome.publication_id,
                        rendered_reply=self._replier.render(incoming, outcome),
                        failed=not outcome.ok,
                        final_answer_pending=True,
                    )
                    if outcome.explanation is not None and outcome.bill is not None:
                        record_secondary_application(
                            self._repo,
                            outcome.bill.term,
                            outcome.bill.number,
                            outcome.explanation.computation_id,
                            target=ResultApplicationTarget(
                                kind="command", ref=str(incoming.update_id)
                            ),
                            now=self._clock.now(),
                        )
                self._save_state(dry_run=False)
            self._answer(
                incoming,
                outcome,
                dry_run=False,
                saved_line=state.reply if state.final_answer_pending else None,
            )

    def _work_result(
        self, incoming: IncomingCommand, work: CommandWork, *, min_score: int, publish: bool
    ) -> CommandOutcome | None:
        bill = self._repo.get(work.term, work.number)
        if work.kind == "explanation" and work.batch_key:
            record = self._repo.analysis_memo_record(work.memo_key or work.batch_key)
            if record is not None:
                if bill is None and work.summary is not None:
                    bill = Bill(
                        summary=work.summary,
                        status=BillStatus.DISCOVERED,
                        first_seen_at=incoming.received_at,
                        last_checked_at=incoming.received_at,
                    )
                explanation = AnalysisRecord.model_validate_json(record)
                return CommandOutcome(
                    status=OutcomeStatus.SHOWN,
                    bill=bill.model_copy(update={"analysis": explanation})
                    if bill is not None
                    else None,
                    explanation=explanation,
                    note=f"Разбор по запросу №{incoming.update_id} готов.",
                )
            job = self._repo.batch_job(work.batch_key)
            if job is not None and job.state != "failed":
                return None
            return CommandOutcome(
                status=OutcomeStatus.ERROR, note="Batch разбора не дал результата."
            )
        if work.kind in ("amendments", "delivery"):
            return self._work_delivery(work)
        if work.kind == "refresh":
            if bill is None or bill.status is BillStatus.LINKED:
                return CommandOutcome(
                    status=OutcomeStatus.ERROR, note="Состояние проекта изменилось."
                )
            if bill.awaiting_batch_since is not None or bill.status is BillStatus.REANALYSIS_READY:
                return None
            return CommandOutcome(
                status=OutcomeStatus.REFRESHED,
                bill=bill,
                note="Ожидание batch для обновления завершено. Сведения в /show.",
            )
        if bill is None or not self._same_analysis(bill, work) or bill.status is BillStatus.LINKED:
            return CommandOutcome(
                status=OutcomeStatus.ERROR, note="Запрос заменён более новым состоянием проекта."
            )
        if bill.status in (
            BillStatus.BATCH_PENDING,
            BillStatus.ANALYSIS_READY,
            BillStatus.ANALYSIS_PENDING,
        ):
            return None
        if bill.status is not BillStatus.ANALYZED or bill.analysis is None:
            return CommandOutcome(
                status=OutcomeStatus.ERROR,
                bill=bill,
                note=f"Анализ не завершён: {bill.last_error or bill.status.value}",
            )
        command = parse_command(incoming.text)
        assert command is not None, "analysis work is recorded only for parsed commands"
        verdict = bill.analysis.analysis
        card = self._publishing.card_of(bill)
        if card is not None and card.status in (
            PublicationStatus.PENDING,
            PublicationStatus.UNKNOWN,
            PublicationStatus.FAILED,
            PublicationStatus.QUEUED,
        ):
            return self._work_delivery(
                work.model_copy(update={"kind": "delivery", "publication_id": card.id})
            )
        if (
            verdict.relevant
            and (verdict.score >= min_score or work.publish_below_threshold)
            and card is None
            and self._bills.over_note(bill) is None
            and work.publish_allowed
            and (not publish or not work.publish_below_threshold)
        ):
            return None
        options = {key: value for key, value in command.options.items() if key != "publish"}
        if work.publish_below_threshold:
            options["publish"] = "true"
        outcome = self._card_verdict(
            bill,
            command.model_copy(update={"options": options}),
            min_score=min_score,
            publish=publish and work.publish_allowed,
            may_wait=True,
        )
        if outcome.status is OutcomeStatus.QUEUED:
            return None
        card = self._publishing.card_of(bill)
        if card is not None and card.id is not None:
            outcome = outcome.model_copy(update={"publication_id": card.id})
        return outcome.model_copy(update={"as_json": command.as_json})

    def _same_analysis(self, bill: Bill, work: CommandWork) -> bool:
        if bill.analysis_generation == work.generation:
            return True
        if bill.analysis is None or bill.analysis.computation_id is None or work.generation is None:
            return False
        result = self._repo.get_computation_result(bill.analysis.computation_id)
        if result is None or result.purpose != "analysis" or result.generation != work.generation:
            return False
        applied = self._repo.get_primary_projection_application(
            result.result_id, bill.term, bill.number, work.generation
        )
        return (
            applied is not None
            and applied.projected_generation == bill.analysis_generation
            and bill.analysis_generation == work.generation + 1
        )

    def _work_delivery(self, work: CommandWork) -> CommandOutcome | None:
        publication = None
        if work.publication_id is not None:
            if work.kind == "amendments":
                publication = next(
                    (
                        item
                        for item in self._repo.list_amendment_revisions(work.publication_id)
                        if work.batch_key is None or (item.ref or "").endswith(f"/{work.batch_key}")
                    ),
                    None,
                )
                if publication is None:
                    if self._analysis.pending_amendment_revision(work.publication_id) is not None:
                        return None
                    job = self._repo.batch_job(work.batch_key) if work.batch_key else None
                    if job is not None and job.state != "failed":
                        return None
            else:
                publication = self._repo.publication_by_id(work.publication_id)
        if publication is None:
            return CommandOutcome(
                status=OutcomeStatus.ERROR, note="Ожидаемая публикация не создана."
            )
        if publication.status is PublicationStatus.SENT and publication.delivery:
            publication = next(
                (
                    part
                    for identity in publication.delivery.continuation_ids
                    if (part := self._repo.publication_by_id(identity)) is not None
                    and part.status is not PublicationStatus.SENT
                ),
                publication,
            )
        if publication.status in (PublicationStatus.PENDING, PublicationStatus.QUEUED):
            return None
        return CommandOutcome(
            status=OutcomeStatus.REFRESHED
            if publication.status is PublicationStatus.SENT
            else OutcomeStatus.ERROR,
            note=f"Доставка {publication.id}: {publication.status.value}. "
            f"/delivery {publication.id}",
            publication_id=publication.id,
            message_id=publication.message_id
            if publication.status is PublicationStatus.SENT
            else None,
        )

    def _execute(
        self,
        incoming: IncomingCommand,
        spent: dict[str, TokenUsage],
        *,
        min_score: int,
        publish: bool,
        dry_run: bool,
    ) -> CommandOutcome:
        command = parse_command(incoming.text)
        if command is None:
            return CommandOutcome(status=OutcomeStatus.HELP, note="not a command")
        if command.error:
            return CommandOutcome(
                status=OutcomeStatus.HELP,
                note=command.error + "\n" + command_help(command.name).splitlines()[0],
            )
        if command.name is CommandName.HELP:
            return CommandOutcome(
                status=OutcomeStatus.HELP,
                note=command_help(command.help_topic, all_commands=command.help_all),
            )
        if command.name is CommandName.QUEUE:
            return self._queries.queue(incoming.update_id, command.count("page", 1))
        if command.name is CommandName.REQUEST:
            assert command.request_id is not None, "/request requires an ID"
            return self._queries.request(command.request_id)
        if command.name is CommandName.STATUS:
            return self._queries.status()
        if command.name is CommandName.GROUPEDIT:
            if command.grouped_stage_resolution is not None:
                return self._resolve_groupedit(
                    command.grouped_stage_resolution, incoming, publish=publish, dry_run=dry_run
                )
            assert command.grouped_stage_date is not None, "the parser provides typed group input"
            return self._groupedit(command.grouped_stage_date, publish=publish, dry_run=dry_run)
        if command.name is CommandName.DELIVERY:
            return self._deliveries.execute(command, incoming, publish=publish)
        if command.name is CommandName.RUNS:
            return self._queries.runs(
                command.count("days", 30),
                include_all="all" in command.options,
                page=command.count("page", 1),
            )
        if command.name is CommandName.COST:
            return self._queries.cost(command.count("days", 30), command.count("top", 5))
        if command.name is CommandName.FIND:
            assert command.query is not None, "parse_command gives /find a query or an error"
            return self._queries.find(command.query, page=command.count("page", 1))
        if command.name is CommandName.DIGEST:
            return self._digest_command(command, publish=publish)
        if command.name in DISPATCHED:
            return CommandOutcome(
                status=OutcomeStatus.HELP,
                note=f"/{command.name}: отправьте через реле, чтобы создать запрос запуска.",
            )
        assert command.ref is not None, (
            "only a bill command is left, and parse_command gives it a ref"
        )
        try:
            lookup = COMMANDS[command.name].lookup
            if lookup == "source":
                bill = self._lookup.read_ref(command.ref)
                explanation = self._analysis.explain_bill(bill, submit=False)
                if isinstance(explanation, Waiting):
                    return CommandOutcome(
                        status=OutcomeStatus.QUEUED,
                        bill=bill,
                        note="Разбор ожидает batch. Результат придёт ответом на эту команду.",
                        work=CommandWork(
                            kind="explanation",
                            term=bill.term,
                            number=bill.number,
                            batch_key=explanation.batch_key,
                            memo_key=explanation.memo_key,
                            summary=bill.summary,
                        ),
                    )
                return CommandOutcome(
                    status=OutcomeStatus.SHOWN,
                    bill=bill.model_copy(update={"analysis": explanation}),
                    explanation=explanation,
                    note="Разбор по запросу; сохранён отдельно от анализа для канала.",
                )
            if lookup == "load":
                bill = self._lookup.continuation(self._lookup.load_ref(command.ref))
                done = self._analyze(bill, command, spent, min_score=min_score, publish=publish)
                return done.model_copy(update={"as_json": command.as_json})
            bill_or_none = self._lookup.find_ref(command.ref)
            if (
                bill_or_none is not None
                and lookup == "current"
                and command.name
                not in (
                    CommandName.SHOW,
                    CommandName.REFRESH,
                )
            ):
                bill_or_none = self._lookup.continuation(bill_or_none)
        except BillNotFoundError as exc:
            return CommandOutcome(status=OutcomeStatus.NOT_FOUND, note=str(exc))
        if bill_or_none is None:
            return CommandOutcome(
                status=OutcomeStatus.NOT_FOUND,
                note=f"{command.ref.label} is not in the database; /analyze fetches it",
            )
        if command.name is CommandName.AMENDMENTS:
            if self._amendments is None:
                return CommandOutcome(status=OutcomeStatus.ERROR, note="amendments service is off")
            return self._amendments.request(
                bill_or_none,
                publication_id=command.count("publication", 0) or None,
                force_id=str(incoming.update_id) if command.force else "",
            )
        if command.name in (CommandName.SHOW, CommandName.REFRESH):
            return self._inspect(
                bill_or_none, spent, refresh=command.name is CommandName.REFRESH, publish=publish
            )
        if command.name is CommandName.SKIP:
            return self._skip(bill_or_none)
        if command.name is CommandName.UNSKIP:
            return self._unskip(bill_or_none)
        if command.name is CommandName.RESET:
            wanted = command.options.get("to", BillStatus.ANALYSIS_PENDING.value)
            return self._bills.reset(bill_or_none, BillStatus(wanted))
        if command.name is CommandName.OBSERVE:
            return self._bills.observe(bill_or_none, command.options["mode"], min_score=min_score)
        if command.name in (CommandName.NAME, CommandName.ATTENTION):
            return self._editorial(bill_or_none, command, incoming)
        if command.name is CommandName.PREVIEW:
            return self._bills.preview(bill_or_none, command.options.get("to"))
        if command.name is CommandName.FORGET:
            return self._bills.forget(bill_or_none, min_score=min_score)
        if command.name is CommandName.LINK:
            return self._link(bill_or_none, command.options["to"], publish=publish)
        if command.name is CommandName.REPUBLISH:
            return self._bills.republish(bill_or_none, publish=publish)
        return CommandOutcome(status=OutcomeStatus.ERROR, note=f"Нет обработчика /{command.name}")

    def _editorial(self, bill: Bill, command: Command, incoming: IncomingCommand) -> CommandOutcome:
        editorial = bill.editorial
        if command.name is CommandName.NAME:
            if not command.editorial_name:
                name = editorial.mnemonic
                proposed = editorial.proposed
                return CommandOutcome(
                    status=OutcomeStatus.SHOWN,
                    bill=bill,
                    note=(
                        f"name: {name.name if name else 'fallback'}; "
                        f"proposal: {proposed.name if proposed else 'none'}; "
                        f"previous: {', '.join(item.name for item in editorial.previous) or 'none'}"
                    ),
                )
            if not valid_mnemonic(command.editorial_name):
                return CommandOutcome(
                    status=OutcomeStatus.ERROR,
                    bill=bill,
                    note="Name must contain 1–4 words, normal spaces and no HTML, URL or hashtag.",
                )
            candidate = Mnemonic(
                name=command.editorial_name,
                language=self._language,
                basis=command.editorial_reason,
                selected_at=self._clock.now(),
                origin="operator",
                command_update_id=incoming.update_id,
                actor_id=incoming.actor_id,
                author_signature=incoming.author_signature,
            )
            self._repo.save_editorial(bill.term, bill.number, editorial.rename(candidate))
            return CommandOutcome(
                status=OutcomeStatus.SHOWN,
                bill=self._reload(bill),
                note="Editorial name saved; existing messages keep their saved payload.",
            )
        if command.editorial_priority is None or command.editorial_until is None:
            posts = self._repo.list_attention_publications(bill.term, bill.number, limit=20)
            digest_owners = self._repo.digest_owners(
                tuple(
                    post.id for post in posts if post.id is not None and post.attention is not None
                )
            )
            notes = [
                f"{post.id}: {post.kind} · {post.status}"
                f" · {post.attention.importance}/{post.attention.urgency}"
                f" · {', '.join(post.attention.reasons)}"
                f" · novelty={post.attention.novelty}"
                " · destination="
                f"{post.delivery.destination if post.delivery else 'retained history'}"
                f" · owner={digest_owners.get(post.id) if post.id is not None else None}"
                for post in posts
                if post.attention is not None
            ]
            notes.extend(
                f"document review {work.purpose}: waiting for "
                f"{'batch result' if work.context is not None else 'official documents'}"
                f" · queued since {work.since.isoformat()}"
                f" · {work.limitation}"
                for work in bill.review_work
            )
            notes.extend(
                f"document review {record.review.purpose}: complete={record.review.complete}"
                f" · unresolved={len(record.review.unresolved)}"
                for record in (
                    bill.consultation_review,
                    bill.commission_review,
                    bill.act_application_review,
                )
                if record is not None
            )
            return CommandOutcome(
                status=OutcomeStatus.SHOWN,
                bill=bill,
                note="\n".join(notes) or "No saved event assessment yet.",
            )
        expires_at = dt.datetime.combine(
            command.editorial_until + dt.timedelta(days=1),
            dt.time(),
            tzinfo=ZoneInfo("Europe/Warsaw"),
        ).astimezone(dt.UTC)
        if expires_at <= self._clock.now():
            return CommandOutcome(
                status=OutcomeStatus.ERROR,
                bill=bill,
                note="Priority expiry must be today or later in Warsaw.",
            )
        override = PriorityOverride(
            reason=command.editorial_reason,
            expires_at=expires_at,
            selected_at=self._clock.now(),
            command_update_id=incoming.update_id,
            urgent=command.editorial_priority == "urgent",
            important=command.editorial_priority == "important",
            actor_id=incoming.actor_id,
            author_signature=incoming.author_signature,
        )
        self._repo.save_editorial(
            bill.term,
            bill.number,
            editorial.model_copy(
                update={
                    "priority": override,
                    "priority_history": editorial.priority_history
                    + ((editorial.priority,) if editorial.priority else ()),
                }
            ),
        )
        return CommandOutcome(
            status=OutcomeStatus.SHOWN,
            bill=self._reload(bill),
            note="Priority raised until the stated Warsaw date; attempted deliveries keep plans.",
        )

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
        if not command.force and "recheck" not in command.options:
            bill, skipped = self._skipped_by_prefilter(bill)
            if skipped is not None:
                return skipped
        if (
            command.force
            or "reanalyze" in command.options
            or bill.analysis is None
            or bill.status is not BillStatus.ANALYZED
        ):
            bill, too_expensive = self._analyse_now(bill, spent, command=command, publish=publish)
            if too_expensive is not None:
                return too_expensive
        assert bill.analysis is not None, "the bill was analysed before or by _analyse_now above"
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
        self, bill: Bill, spent: dict[str, TokenUsage], *, command: Command, publish: bool
    ) -> tuple[Bill, CommandOutcome | None]:
        """Ask the model, and count the triage too: the operator pays for both.

        A text over the per-bill limit gets the answer the analysis phase gives it, so that the
        queue looks the same whoever hit the guard, and so does a file the host would not hand
        over: the operator is told to ask again rather than being given a verdict on a text
        nobody read, and `force` is no answer to a WAF.
        """
        try:
            analysed = self._analysis.analyze_bill(
                bill,
                ignore_cost_limit=command.force or "allow_over_budget" in command.options,
                redo=command.force or "reanalyze" in command.options,
                submit=False,
            )
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
        if isinstance(analysed, Waiting):
            fresh = self._reload(bill)
            return fresh, CommandOutcome(
                status=OutcomeStatus.QUEUED,
                bill=fresh,
                note="queued to the batch; the result is applied when the batch completes",
                work=CommandWork(
                    kind="analysis",
                    term=fresh.term,
                    number=fresh.number,
                    generation=fresh.analysis_generation,
                    batch_key=analysed.batch_key,
                    publish_below_threshold=command.publish and publish,
                    publish_allowed=publish,
                ),
            )
        return self._reload(bill), None

    def _card_verdict(
        self, bill: Bill, command: Command, *, min_score: int, publish: bool, may_wait: bool = False
    ) -> CommandOutcome:
        """Whether the analysis earns a card now, and what to say when it does not."""
        assert bill.analysis is not None, "_analyze asks for a verdict only of an analysed bill"
        verdict = bill.analysis.analysis
        if not verdict.relevant:
            return CommandOutcome(
                status=OutcomeStatus.ANALYSED, bill=bill, note="not relevant: no card"
            )
        card = self._bills.card_label(bill)
        if card is not None:
            return CommandOutcome(
                status=OutcomeStatus.ANALYSED,
                bill=bill,
                note=f"the card is in the channel already ({card})",
            )
        if (over := self._bills.over_note(bill)) is not None:
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
        return self._bills.post(bill, OutcomeStatus.ANALYSED, may_wait=may_wait)

    def _skip(self, bill: Bill) -> CommandOutcome:
        self._repo.reset_bill(
            bill.term,
            bill.number,
            BillStatus.SKIPPED_PREFILTER,
            reason=SILENCED_BY_OPERATOR,
        )
        card = self._bills.card_label(bill)
        note = "silenced: it will not be analysed or posted"
        if card is not None:
            note += f"; its card ({card}) stays and is still followed"
        return CommandOutcome(status=OutcomeStatus.SILENCED, bill=self._reload(bill), note=note)

    def _unskip(self, bill: Bill) -> CommandOutcome:
        """Restoring a saved analysis must not schedule a new paid analysis."""
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
        if bill.status is BillStatus.BATCH_PENDING:
            return CommandOutcome(
                status=OutcomeStatus.QUEUED,
                bill=bill,
                note="batch already pending; waiting for its result",
            )
        if bill.status in SKIPPED and bill.analysis is not None:
            self._repo.reset_bill(bill.term, bill.number, BillStatus.ANALYZED)
            return CommandOutcome(
                status=OutcomeStatus.QUEUED,
                bill=self._reload(bill),
                note="saved analysis restored; /analyze BILL force asks the model again",
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

    def _link(self, entry: Bill, print_number: str, *, publish: bool) -> CommandOutcome:
        """`/link ENTRY to=DRUK`: for a druk the Sejm printed without naming its RCL project."""
        if entry.rcl is None and not entry.is_pre_print:
            return CommandOutcome(
                status=OutcomeStatus.ERROR,
                bill=entry,
                note="/link takes an RCL project or an RPW entry, then the druk it became:"
                " /link RCL/12398102 to=3281",
            )
        if entry.status is BillStatus.LINKED:
            same = entry.linked_number == print_number
            return CommandOutcome(
                status=OutcomeStatus.LINKED if same else OutcomeStatus.ERROR,
                bill=entry,
                note=f"already continues as {entry.linked_number}; nothing changed",
            )
        if self._tracking is None:
            return CommandOutcome(
                status=OutcomeStatus.ERROR, bill=entry, note="tracking is off in this run"
            )
        druk_card = self._bills.card_label(self._repo.get(entry.term, print_number) or entry)
        own_card = self._bills.card_label(entry)
        try:
            result = self._tracking.link(entry, print_number, publish=publish)
        except StateCheckpointError:
            raise
        except Exception as exc:
            log.exception("/link %s to=%s failed", entry.number, print_number)
            return CommandOutcome(
                status=OutcomeStatus.ERROR, bill=entry, note=f"druk {print_number}: {exc}"
            )
        if result.fatal_error is not None:
            raise _SourceDownError(result.fatal_error)
        if entry.rcl is not None:
            self._repo.save_rcl(
                entry.term,
                entry.number,
                entry.rcl.model_copy(update={"print_number": print_number}),
            )
        if druk_card is not None and own_card is not None and druk_card != own_card:
            note = (
                f"druk {print_number} keeps its own card ({druk_card}); the entry's {own_card} is"
                " left without a thread: reply under it with a link to the druk's card"
            )
        elif own_card is not None:
            note = f"druk {print_number} continues the thread of {own_card}"
        else:
            note = f"druk {print_number} continues {entry.number}; the entry had no card"
        return CommandOutcome(status=OutcomeStatus.LINKED, bill=self._reload(entry), note=note)

    def _revive_rcl(self, bill: Bill, to: BillStatus) -> bool:
        """A skipped RCL row keeps only the project's skeleton: read its documents again before
        the status is cleared, or an unreachable RCL leaves the bill queued on its metadata."""
        if to is not BillStatus.ANALYSIS_PENDING or bill.rcl is None or bill.rcl.text_documents():
            return False
        self._repo.save_rcl(bill.term, bill.number, self._lookup.read_rcl_project(bill.number))
        return True

    def _inspect(
        self, bill: Bill, spent: dict[str, TokenUsage], *, refresh: bool, publish: bool
    ) -> CommandOutcome:
        path = [bill.number]
        try:
            bill = self._lookup.continuation(bill, path)
            outcome = (
                self._refresh(bill, spent, publish=publish)
                if refresh
                else CommandOutcome(status=OutcomeStatus.SHOWN, bill=bill)
            )
            if outcome.bill is not None:
                bill = self._lookup.continuation(outcome.bill, path)
                outcome = outcome.model_copy(update={"bill": bill})
            if not refresh:
                channel = self._publishing.channel_id
                reply = self._repo.get_publication(
                    bill.term, bill.number, PublicationKind.JOINT_BILL, channel
                )
                if reply is not None and reply.status is PublicationStatus.SENT:
                    primary = primary_of(self._repo, bill, channel, include_closed=True)
                    if primary is not None:
                        outcome = outcome.model_copy(update={"joint_primary": primary[0]})
        except BillNotFoundError as exc:
            return CommandOutcome(status=OutcomeStatus.ERROR, note=str(exc))
        if len(path) > 1:
            note = "linked: " + " → ".join(path)
            if outcome.note:
                note += "\n" + outcome.note
            outcome = outcome.model_copy(update={"note": note})
        return outcome

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
        if publish and self._bills.card_label(bill) is None:
            return CommandOutcome(
                status=OutcomeStatus.ERROR,
                bill=bill,
                note="no card in the channel: an update would have nothing to reply under."
                " /analyze BILL publish posts the card first",
            )
        result = self._tracking.check_bill(bill, publish=publish, keep_delivery_attempts=True)
        merge_usage(spent, result.usage)
        if result.fatal_error is not None:
            raise _SourceDownError(result.fatal_error)
        return CommandOutcome(
            status=OutcomeStatus.REFRESHED,
            bill=self._reload(bill),
            note=_tracking_note(result),
            work=CommandWork(
                kind="refresh",
                term=bill.term,
                number=bill.number,
                generation=bill.analysis_generation,
            )
            if self._reload(bill).awaiting_batch_since is not None
            else None,
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
            # The draft sits in the technical channel; the reply links an id as the readers'.
            return self._digest_outcome(
                done,
                done.drafted,
                f"draft of {ref} is in this channel; press the button to send it",
                message_id=None,
            )
        if not publish:
            return CommandOutcome(
                status=OutcomeStatus.DIGESTED, note=f"{ref} not posted: this run does not publish"
            )
        done = self._digest.publish(ref, keep_attempts=True)
        return self._digest_outcome(
            done, done.published, f"{ref} posted to the channel", message_id=done.message_id
        )

    @staticmethod
    def _digest_outcome(
        done: DigestResult, sent: bool, said: str, *, message_id: int | None
    ) -> CommandOutcome:
        """A send that failed says so: the note is empty on success, so answering `note or said`
        told the operator the digest was posted when nothing had been."""
        return CommandOutcome(
            status=OutcomeStatus.ERROR if done.failed else OutcomeStatus.DIGESTED,
            note=said if sent else done.note,
            message_id=message_id,
        )

    def _groupedit(
        self, command: GroupedStageDateCommand, *, publish: bool, dry_run: bool
    ) -> CommandOutcome:
        formatter = self._grouped_stage_formatter
        if formatter is None:
            return CommandOutcome(status=OutcomeStatus.ERROR, note="grouped stage edits disabled")
        correction = command.correction
        root = self._repo.publication_by_id(correction.root_publication_id)
        if root is None or root.channel_id != self._publishing.channel_id:
            return CommandOutcome(
                status=OutcomeStatus.NOT_FOUND, note="publication not found in this channel"
            )
        approval = command.approval
        if approval is None:
            preview = preview_grouped_stage_date(self._repo, formatter, correction)
            if preview is None:
                return CommandOutcome(
                    status=OutcomeStatus.ERROR, note="grouped stage date preview unavailable"
                )
            copy = (
                f"/groupedit approve {correction.root_publication_id}"
                f" {correction.member_publication_id} {correction.status_change_id}"
                f" {correction.expected_content_id} {correction.expected_sha256}"
                f" {correction.expected_date} {correction.corrected_date}"
                f" {preview.candidate_sha256} {preview.chat_id} {preview.message_id}"
            )
            note = (
                f"− {html.unescape(preview.before_line)}\n"
                f"+ {html.unescape(preview.after_line)}\n{copy}"
            )
            if len(note.encode()) > 2000:
                return CommandOutcome(
                    status=OutcomeStatus.ERROR, note="grouped stage date preview is too long"
                )
            return CommandOutcome(status=OutcomeStatus.SHOWN, note=note)
        editor = self._grouped_stage_editor
        if not publish or dry_run or self._checkpoint is None or editor is None:
            return CommandOutcome(
                status=OutcomeStatus.ERROR,
                note="grouped stage approval requires a publishing run and durable checkpoint",
            )
        result = approve_grouped_stage_date(
            self._repo,
            self._clock,
            formatter,
            editor,
            self._checkpoint,
            correction,
            approved_sha256=approval.candidate_sha256,
            chat_id=approval.chat_id,
            message_id=approval.message_id,
            enabled=True,
            candidate_refs_enabled=self._grouped_stage_candidate_refs_enabled,
        )
        if result is None:
            return CommandOutcome(status=OutcomeStatus.ERROR, note="grouped stage approval refused")
        if result.status is PublicationStatus.SENT:
            return CommandOutcome(
                status=OutcomeStatus.DELIVERY,
                message_id=approval.message_id,
                note=f"grouped stage edit slot {result.publication_id} confirmed",
            )
        status = result.status.value if result.status is not None else "queued"
        return CommandOutcome(
            status=OutcomeStatus.ERROR,
            note=f"grouped stage edit slot {result.publication_id} is {status}",
        )

    def _resolve_groupedit(
        self,
        command: GroupedStageResolutionCommand,
        incoming: IncomingCommand,
        *,
        publish: bool,
        dry_run: bool,
    ) -> CommandOutcome:
        if (
            not self._grouped_stage_resolution_enabled
            or not publish
            or dry_run
            or self._checkpoint is None
        ):
            return CommandOutcome(
                status=OutcomeStatus.ERROR,
                note=(
                    "grouped edit resolution requires an enabled publishing run"
                    " and durable checkpoint"
                ),
            )
        slot = self._repo.publication_by_id(command.slot_id)
        if (
            slot is None
            or slot.channel_id != self._publishing.channel_id
            or not is_grouped_stage_revision_slot(slot)
        ):
            return CommandOutcome(
                status=OutcomeStatus.NOT_FOUND, note="grouped edit slot not found in this channel"
            )
        try:
            audit = GroupedEditDeliveryResolution(
                publication_id=command.slot_id,
                publication_ids=(command.slot_id,),
                term=slot.term,
                number=slot.number,
                kind=slot.kind,
                channel_id=slot.channel_id,
                action=command.action,
                command=incoming,
                resolved_at=self._clock.now(),
                message_id=command.message_id if command.action == "confirm" else None,
                reason=command.reason,
                content_id=command.content_id,
                base_content_id=command.base_content_id,
                rendered_sha256=command.rendered_sha256,
                telegram_chat_id=command.chat_id,
                target_message_id=command.message_id,
                observed_at=command.observed_at,
            )
        except ValueError:
            return CommandOutcome(
                status=OutcomeStatus.ERROR, note="invalid grouped edit attestation"
            )
        if not self._repo.resolve_grouped_stage_edit(audit):
            return CommandOutcome(
                status=OutcomeStatus.ERROR, note="grouped edit resolution refused"
            )
        return CommandOutcome(
            status=OutcomeStatus.DELIVERY,
            note=f"grouped stage edit slot {command.slot_id} is {audit.status.value}",
        )

    def _reload(self, bill: Bill) -> Bill:
        fresh = self._repo.get(bill.term, bill.number)
        return fresh if fresh is not None else bill
