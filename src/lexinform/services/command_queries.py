"""Read-only operator queries over the current state and inbox."""

import datetime as dt

from lexinform.intent_ages import intent_ages
from lexinform.models import (
    FAILING_CHECK_MIN,
    BillStatus,
    CommandOutcome,
    OutcomeStatus,
    SpendSnapshot,
    StatusSnapshot,
    TokenUsage,
    merge_usage,
)
from lexinform.ports import BillRepository, Clock, CommandInbox
from lexinform.pricing import cost_usd
from lexinform.services.tracking import StatusTrackingService

WAITING = (
    BillStatus.ANALYSIS_PENDING,
    BillStatus.TEXT_PREFILTER_PENDING,
    BillStatus.ANALYSIS_FAILED,
    BillStatus.SKIPPED_COST,
    BillStatus.DOCUMENT_UNSUPPORTED,
    BillStatus.DOCUMENT_AMBIGUOUS,
)


class CommandQueries:
    def __init__(
        self,
        repo: BillRepository,
        inbox: CommandInbox,
        clock: Clock,
        channel_id: str,
        tracking: StatusTrackingService | None,
        *,
        days: int,
        limit: int,
    ) -> None:
        self._repo = repo
        self._inbox = inbox
        self._clock = clock
        self._channel_id = channel_id
        self._tracking = tracking
        self._status_days = days
        self._status_bills = limit

    def status(self) -> CommandOutcome:
        """The queues, what is stuck and what the recent runs cost — the state between the run
        reports, which each say what one run did and nothing about what has piled up."""
        observed_at = self._clock.now()
        since = observed_at - dt.timedelta(days=self._status_days)
        batches = tuple(self._repo.list_open_llm_batches())
        queued = self._repo.list_queued_batch_intents()
        uncertain = self._repo.list_submitting_batch_intents()
        held = {
            (i.request.term, i.request.number)
            for i in (*queued, *uncertain)
            if i.request.call_kind in ("analysis", "reanalysis")
        } | {
            (item.term, item.number)
            for batch in batches
            for item in self._repo.list_llm_batch_items(batch.batch_id)
            if item.call_kind in ("analysis", "reanalysis")
        }
        bills = self._repo.count_by_status()
        in_flight = self._repo.list_by_status(
            [BillStatus.BATCH_PENDING], limit=bills.get(BillStatus.BATCH_PENDING, 0)
        )
        snapshot = StatusSnapshot(
            bills=bills,
            publications=self._repo.count_publications(self._channel_id),
            followed=len(self._tracking.followed()) if self._tracking is not None else 0,
            waiting=tuple(self._repo.list_by_status(list(WAITING), limit=self._status_bills)),
            stuck=tuple(
                self._repo.list_stuck_publications(self._channel_id, limit=self._status_bills)
            ),
            runs=tuple(
                report
                for report in self._repo.list_runs(since=since)
                if report.execution_accounting_measured
            ),
            days=self._status_days,
            batches=batches,
            queued_intents=len(queued),
            uncertain_intents=len(uncertain),
            intent_ages=intent_ages((*queued, *uncertain), observed_at),
            document_work=self._repo.count_document_work(),
            orphaned=tuple(b for b in in_flight if (b.term, b.number) not in held),
            awaiting_batch=tuple(
                b for b in self._tracking.followed() if b.awaiting_batch_since is not None
            )
            if self._tracking is not None
            else (),
            failing=tuple(
                self._repo.list_failing_checks(
                    min_failures=FAILING_CHECK_MIN, limit=self._status_bills
                )
            ),
            observed_at=observed_at,
        )
        return CommandOutcome(
            status=OutcomeStatus.REPORTED,
            snapshot=snapshot,
            note=f"{snapshot.followed} bills followed",
        )

    def runs(self, days: int, *, include_all: bool = False, page: int = 1) -> CommandOutcome:
        """`/runs`: what each recorded run of the window found, posted and cost — `lexinform
        runs` in the channel, for the operator who has no shell open."""
        reports = self._repo.list_runs(since=self._clock.now() - dt.timedelta(days=days))
        if not include_all:
            reports = [report for report in reports if not report.is_empty]
        label = "runs" if include_all else "non-empty runs"
        offset = (page - 1) * self._status_bills
        next_page = (
            f"\n/runs days={days}{' all' if include_all else ''} page={page + 1}"
            if len(reports) > offset + self._status_bills
            else ""
        )
        return CommandOutcome(
            status=OutcomeStatus.LISTED,
            runs=tuple(reports[offset : offset + self._status_bills]),
            note=f"{len(reports)} run(s) in {days} days. Страница {page}.{next_page}"
            if reports
            else f"no {label} recorded in the last {days} days",
        )

    def cost(self, days: int, top: int) -> CommandOutcome:
        """`/cost`: the window's model spend, per model, its dearest run and its dearest bills."""
        reports = [
            report
            for report in self._repo.list_runs(since=self._clock.now() - dt.timedelta(days=days))
            if report.execution_accounting_measured
        ]
        usage: dict[str, TokenUsage] = {}
        for report in reports:
            merge_usage(usage, report.llm_usage)
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

    def find(self, query: str, *, page: int = 1) -> CommandOutcome:
        found = self._repo.search(
            query, limit=self._status_bills + 1, offset=(page - 1) * self._status_bills
        )
        if not found:
            return CommandOutcome(
                status=OutcomeStatus.FOUND, note=f"nothing in the database matches {query!r}"
            )
        return CommandOutcome(
            status=OutcomeStatus.FOUND,
            found=tuple(found[: self._status_bills]),
            note=f"Страница {page}."
            + (f"\n/find {query} page={page + 1}" if len(found) > self._status_bills else ""),
        )

    def queue(self, update_id: int, page: int) -> CommandOutcome:
        pending = {
            command.update_id: command
            for command in self._inbox.pending()
            if command.update_id != update_id
            and (
                (state := self._repo.command_state(command.update_id)) is None
                or state.handled_at is None
            )
        }
        pending.update(
            {
                state.incoming.update_id: state.incoming
                for state in self._repo.pending_command_work()
                if state.incoming is not None
            }
        )
        start = (page - 1) * self._status_bills
        lines = [f"Ожидают: {len(pending)}. Страница {page}."]
        for identity in sorted(pending)[start : start + self._status_bills]:
            lines.append(f"/request {identity} · {pending[identity].text[:160]}")
        if len(pending) > start + self._status_bills:
            lines.append(f"/queue page={page + 1}")
        return CommandOutcome(status=OutcomeStatus.REPORTED, note="\n".join(lines))

    def request(self, identity: int) -> CommandOutcome:
        state = self._repo.command_state(identity)
        if state is None:
            incoming = next(
                (item for item in self._inbox.pending() if item.update_id == identity), None
            )
            if incoming is None:
                return CommandOutcome(
                    status=OutcomeStatus.NOT_FOUND, note=f"Запрос №{identity} не найден."
                )
            return CommandOutcome(
                status=OutcomeStatus.QUEUED,
                note=f"Запрос №{identity} в очереди.\n{incoming.text[:300]}",
            )
        lines = [f"Запрос №{identity}"]
        if state.incoming is not None:
            lines.append(state.incoming.text[:300])
            if actor := state.incoming.actor_id or state.incoming.author_signature:
                lines.append(f"Автор: {actor}")
        if state.work is not None:
            work = state.work
            lines.append(f"Ожидается результат · {work.number} · {work.kind}")
            if work.batch_key:
                job = self._repo.batch_job(work.batch_key)
                lines.append(f"Batch {work.batch_key}" + (f" · {job.state}" if job else ""))
            if work.publication_id:
                lines.append(f"/delivery {work.publication_id}")
        elif state.executed_at is None:
            lines.append("Выполнение начато, итог пока неизвестен.")
        else:
            lines.append("Завершён с ошибкой." if state.failed else "Выполнение завершено.")
        if state.completed_work is not None:
            if state.completed_work.batch_key:
                lines.append(f"Batch: {state.completed_work.batch_key}")
            if state.completed_work.publication_id:
                lines.append(f"/delivery {state.completed_work.publication_id}")
        if state.executed_at is not None and (
            state.handled_at is None or state.final_answer_pending
        ):
            lines.append("Ожидается отправка ответа.")
        if state.reply:
            lines.append(state.reply[:1600])
        return CommandOutcome(
            status=OutcomeStatus.REPORTED,
            note="\n".join(lines),
            request_links=(state.workflow_url,) if state.workflow_url else (),
        )
