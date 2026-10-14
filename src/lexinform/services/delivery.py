import logging
from datetime import timedelta

from lexinform.errors import ServiceUnavailableError
from lexinform.logging_setup import MemoryLogHandler
from lexinform.models import Bill, PublicationKind, PublicationStatus, RunMode, RunReport
from lexinform.ports import BillRepository, Clock, RunNotifier
from lexinform.services.posting import Poster
from lexinform.services.tracking_result import PostCounter, TrackingResult, merge_tracking

log = logging.getLogger(__name__)


class DeliveryService:
    def __init__(
        self,
        repo: BillRepository,
        clock: Clock,
        poster: Poster,
        *,
        channel_id: str,
        max_attempts: int,
        notifier: RunNotifier | None = None,
        runs_retention_days: int | None = 90,
    ) -> None:
        self._repo = repo
        self._clock = clock
        self._poster = poster
        self._channel_id = channel_id
        self._max_attempts = max_attempts
        self._notifier = notifier
        self._runs_retention_days = runs_retention_days

    def retry(self, result: TrackingResult) -> list[Bill]:
        """Only confirmed non-delivery can be retried without an operator decision."""
        completed: list[Bill] = []
        counters: dict[PublicationKind, PostCounter] = {
            PublicationKind.HEARING_DEADLINE: "hearing_reminders",
            PublicationKind.DECISION_DEADLINE: "decision_reminders",
            PublicationKind.ACT_PUBLISHED: "acts_published",
            PublicationKind.IN_FORCE: "in_force_posted",
            PublicationKind.CONSULTATION_RESULTS: "consultation_results_posted",
            PublicationKind.CONSULTATION_REVIEW: "consultation_reviews_posted",
            PublicationKind.ACT_APPLICATION: "act_reviews_posted",
            PublicationKind.EFFECTIVE_RULES: "effective_rule_messages",
            PublicationKind.CONSULTATION_DEADLINE: "consultation_reminders",
        }
        for publication in self._repo.list_due_deliveries(
            self._channel_id, max_attempts=self._max_attempts
        ):
            if self._poster.delivery_paused(publication):
                continue
            if publication.kind in (
                PublicationKind.NEW_BILL,
                PublicationKind.JOINT_BILL,
                PublicationKind.DIGEST,
            ):
                continue
            if publication.kind in (PublicationKind.AGENDA, PublicationKind.AGENDA_CANCELLED):
                self._poster.queue_sitting(publication)
                continue
            try:
                sent = self._poster.deliver(publication)
                latest = (
                    self._repo.publication_by_id(publication.id)
                    if publication.id is not None
                    else None
                )
                if latest is not None and (
                    latest.status is PublicationStatus.SKIPPED
                    or (
                        not sent
                        and latest.status is PublicationStatus.QUEUED
                        and not latest.attempts
                    )
                ):
                    continue
                if publication.kind is PublicationKind.MESSAGE_PART:
                    if not sent:
                        result.failed += 1
                else:
                    result.count_post(sent, counters.get(publication.kind, "published"))
            except ServiceUnavailableError as exc:
                result.abort(exc, failed=True)
                return completed
        for change in self._repo.list_due_status_changes(
            self._channel_id, max_attempts=self._max_attempts
        ):
            bill = self._repo.get(change.term, change.number)
            if bill is None:
                continue
            assert change.id is not None
            pub = self._repo.get_update_publication(change.id, self._channel_id)
            if pub is not None and pub.delivery is not None:
                plan = pub.delivery
                if plan.absorbed_by is not None or plan.destination == "digest_only":
                    continue
                if plan.update_group:
                    try:
                        self._poster.retry_update_group(pub, result)
                    except ServiceUnavailableError as exc:
                        result.abort(exc, failed=True)
                        return completed
                    continue
                if (
                    pub.status is PublicationStatus.QUEUED
                    and not pub.attempts
                    and (plan.wait_until is not None or plan.schedule is not None)
                ):
                    self._poster.queue_update(pub)
                    continue
            log.info("retrying status update for druk %s", bill.number)
            try:
                sent = self._poster.status_update(bill, change)
                result.count_post(sent)
                if sent and self._poster.carries_act(change):
                    result.acts_published += 1
                if sent and change.discontinued:
                    completed.append(bill)
            except ServiceUnavailableError as exc:
                result.abort(exc, failed=True)
                return completed
        return completed

    def run(self, *, dry_run: bool = False) -> RunReport:
        started = self._clock.now()
        report = RunReport(
            started_at=started,
            since=started,
            mode=RunMode.DRY_RUN if dry_run else RunMode.DELIVERIES,
        )
        result = TrackingResult()
        captured = MemoryLogHandler()
        captured.install()
        run_id: int | None = None
        if dry_run:
            self._repo.begin()
        try:
            self._poster.start_run()
            run_id = self._repo.start_run(report)
            stale = self._repo.mark_stale_pending_as_unknown(now=started)
            if stale:
                report.errors.append("Stale pending deliveries marked unknown: " + ", ".join(stale))
            self.retry(result)
            if result.fatal_error is None:
                self._poster.flush_sittings(result)
                self._poster.flush_updates(result)
        except ServiceUnavailableError as exc:
            result.abort(exc, failed=True)
        except Exception as exc:
            log.exception("delivery drain failed")
            report.errors.append(f"deliveries: {type(exc).__name__}: {exc}")
        finally:
            report.finished_at = self._clock.now()
            result.attention = self._poster.attention_metrics()
            merge_tracking(report, result, publish=True)
            if result.fatal_error:
                report.errors.append("deliveries: " + result.fatal_error)
            elif result.failed:
                report.errors.append(f"deliveries: {result.failed} failed sends")
            try:
                if run_id is not None:
                    self._repo.finish_run(run_id, report)
                if self._runs_retention_days is not None:
                    self._repo.prune_runs(
                        before=started - timedelta(days=self._runs_retention_days)
                    )
            finally:
                if dry_run:
                    self._repo.rollback()
                captured.uninstall()
            if self._notifier is not None and not report.is_empty:
                try:
                    self._notifier.notify(report, list(captured.lines))
                except Exception:
                    log.exception("delivery drain notification failed")
        return report
