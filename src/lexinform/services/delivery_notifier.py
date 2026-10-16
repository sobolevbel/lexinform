import hashlib
import json
from datetime import timedelta

from lexinform.models import (
    BackfillReport,
    Bill,
    DeliveryAlert,
    PublicationKind,
    PublicationStatus,
    RunMode,
    RunReport,
    participation_view,
)
from lexinform.ports import BillRepository, Clock, RunNotifier

REPEAT_AFTER = timedelta(hours=24)


class DeliveryNotifier:
    def __init__(
        self,
        notifier: RunNotifier,
        repo: BillRepository,
        clock: Clock,
        *,
        channel_id: str,
        dry_run: bool = False,
    ) -> None:
        self._notifier = notifier
        self._repo = repo
        self._clock = clock
        self._channel_id = channel_id
        self._dry_run = dry_run

    def notify(self, report: RunReport, log_lines: list[str]) -> None:
        if self._dry_run:
            self._notifier.notify(report, log_lines)
            return
        warning = bool(
            report.attention.get("daily_overdue")
            or report.attention.get("participation_unavailable")
            or report.attention_notifications.get("digest_queued_events")
        )
        fingerprint = self._fingerprint() if warning else ""
        only_warning = (
            report.mode is RunMode.DELIVERIES
            and report.model_copy(update={"attention": {}}).is_empty
            and not report.attention.get("expired")
        )
        previous = self._repo.get_delivery_alert(self._channel_id) if warning else None
        if only_warning and (
            not warning
            or (
                previous is not None
                and previous.fingerprint == fingerprint
                and self._clock.now() < previous.notified_at + REPEAT_AFTER
            )
        ):
            return
        self._notifier.notify(report, log_lines)
        if warning:
            self._repo.save_delivery_alert(
                DeliveryAlert(
                    channel_id=self._channel_id,
                    fingerprint=fingerprint,
                    notified_at=self._clock.now(),
                )
            )

    def notify_backfill(self, report: BackfillReport) -> None:
        self._notifier.notify_backfill(report)

    def _fingerprint(self) -> str:
        warnings: list[tuple[int | None, str, int, str | None]] = []
        for pub in self._repo.list_open_deliveries(self._channel_id):
            plan = pub.delivery
            assert plan is not None
            overdue = plan.schedule is not None and self._clock.now() > plan.schedule.deliver_by
            unavailable = False
            if (
                pub.kind is PublicationKind.CONSULTATION_DEADLINE
                and pub.status is PublicationStatus.QUEUED
                and pub.attempts == 0
                and not plan.rendered_text
            ):
                window = participation_view(
                    Bill.model_validate_json(plan.bill_json), plan.participation
                ).consultation
                unavailable = window is None or window.end is None
            approved_digest = (
                plan.destination == "digest_only"
                and pub.id is not None
                and self._repo.digest_owner(pub.id) is not None
            )
            if overdue or unavailable or approved_digest:
                warnings.append((pub.id, pub.status.value, pub.attempts, pub.error))
        return hashlib.sha256(json.dumps(warnings).encode()).hexdigest()
