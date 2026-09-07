"""Detects new legislative stages and new bill texts for published bills, and posts updates.

Every update is posted as a reply to the original card and always restates the current summary.
When the text of the bill changed (committee report with amendments, text after the 3rd reading,
updated print) the bill is re-analysed first and the update also lists what changed.
"""

import hashlib
import logging
from dataclasses import dataclass

from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    Bill,
    PrintInfo,
    Publication,
    PublicationKind,
    PublicationStatus,
    StatusChange,
    diff_stages,
    stage_fingerprint,
)
from lexinform.ports import BillRepository, Clock, Publisher, SejmGateway
from lexinform.services.analysis import AnalysisService

log = logging.getLogger(__name__)


@dataclass
class TrackingResult:
    checked: int = 0
    changed: int = 0
    reanalyzed: int = 0
    published: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    fatal_error: str | None = None


class StatusTrackingService:
    def __init__(
        self,
        gateway: SejmGateway,
        repo: BillRepository,
        publisher: Publisher,
        clock: Clock,
        *,
        channel_id: str,
        analysis: AnalysisService | None = None,
        closed_grace_days: int = 30,
        max_publish_attempts: int = 3,
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._publisher = publisher
        self._clock = clock
        self._channel_id = channel_id
        self._analysis = analysis
        self._closed_grace_days = closed_grace_days
        self._max_publish_attempts = max_publish_attempts

    def check_updates(self, term: int, *, publish: bool = True) -> TrackingResult:
        result = TrackingResult()
        now = self._clock.now()
        if publish and not self._retry_failed(term, result):
            return result
        tracked = self._repo.list_tracked(
            term, self._channel_id, closed_grace_days=self._closed_grace_days, now=now
        )
        for bill in tracked:
            result.checked += 1
            try:
                change = self._detect(bill, result)
            except ServiceUnavailableError as exc:
                result.fatal_error = exc.describe()
                log.error("aborting tracking phase: %s", result.fatal_error)
                break
            except Exception as exc:
                result.failed += 1
                log.exception("tracking druk %s failed: %s", bill.number, exc)
                continue
            if change is None:
                continue
            result.changed += 1
            if not publish:
                continue
            try:
                ok = self._publish(bill, change)
            except ServiceUnavailableError as exc:
                result.failed += 1
                result.fatal_error = exc.describe()
                log.error("aborting tracking phase: %s", result.fatal_error)
                break
            if ok:
                result.published += 1
            else:
                result.failed += 1
        log.info(
            "tracking: checked=%d changed=%d reanalyzed=%d published=%d failed=%d",
            result.checked,
            result.changed,
            result.reanalyzed,
            result.published,
            result.failed,
        )
        return result

    def _retry_failed(self, term: int, result: TrackingResult) -> bool:
        """Re-send status updates whose post failed earlier. False if Telegram is down."""
        for change in self._repo.list_failed_status_changes(
            term, self._channel_id, max_attempts=self._max_publish_attempts
        ):
            bill = self._repo.get(change.term, change.number)
            if bill is None:
                continue
            log.info("retrying status update for druk %s", bill.number)
            try:
                ok = self._publish(bill, change)
            except ServiceUnavailableError as exc:
                result.failed += 1
                result.fatal_error = exc.describe()
                log.error("aborting tracking phase: %s", result.fatal_error)
                return False
            if ok:
                result.published += 1
            else:
                result.failed += 1
        return True

    # ------------------------------------------------------------------ detection

    def _detect(self, bill: Bill, result: TrackingResult) -> StatusChange | None:
        detail = self._gateway.get_process(bill.term, bill.number)
        print_info = self._safe_print(bill)
        now = self._clock.now()

        new_fp = stage_fingerprint(detail.stages)
        old_fp = bill.stages_fingerprint
        stages_changed = new_fp != old_fp
        if stages_changed:
            self._repo.save_stages(bill.term, bill.number, detail.stages, new_fp)
        self._repo.upsert_summary(detail, now=now)

        document = (
            self._analysis.newer_document(bill, detail, print_info) if self._analysis else None
        )
        content_changed = False
        if document is not None and self._analysis is not None:
            log.info("druk %s: new text (%s), re-analysing", bill.number, document.kind)
            record = self._analysis.reanalyze_bill(bill, detail, document)
            result.reanalyzed += 1
            result.input_tokens += record.input_tokens or 0
            result.output_tokens += record.output_tokens or 0
            content_changed = True

        if old_fp is None:
            return None  # first time we see stages for this bill: seed silently
        # Discovery refreshes the summary (and its closure date) before tracking runs, so closure
        # is detected against what was already announced, not against the stored summary.
        closure_detected = detail.closure_date is not None and not self._repo.closure_announced(
            bill.term, bill.number
        )
        new_stages = diff_stages(bill.stages, detail.stages) if stages_changed else []
        if not (new_stages or content_changed or closure_detected):
            return None  # nothing worth a post (e.g. a stage was edited or removed upstream)

        fresh = self._repo.get(bill.term, bill.number) or bill
        change = StatusChange(
            term=bill.term,
            number=bill.number,
            old_fingerprint=old_fp,
            new_fingerprint=self._change_key(new_fp, fresh, closed=closure_detected),
            new_stages=new_stages,
            closure_detected=closure_detected,
            passed=detail.passed,
            content_changed=content_changed,
            detected_at=now,
        )
        change_id = self._repo.add_status_change(change)
        if change_id is None:
            return None  # already recorded by an earlier run
        change.id = change_id
        log.info(
            "druk %s: %d new stage(s)%s: %s",
            bill.number,
            len(change.new_stages),
            " + new text" if content_changed else "",
            "; ".join(s.stage_name for s in change.new_stages) or "-",
        )
        return change

    @staticmethod
    def _change_key(stage_fp: str, bill: Bill, *, closed: bool) -> str:
        """Dedupe key for status_changes: stages, analysed text revision, closure announcement."""
        revision = bill.analysis.revision if bill.analysis else 0
        source = bill.analysis.source_url if bill.analysis else ""
        key = f"{stage_fp}|{revision}|{source}" + ("|closed" if closed else "")
        return hashlib.sha256(key.encode()).hexdigest()

    def _safe_print(self, bill: Bill) -> PrintInfo | None:
        try:
            return self._gateway.get_print(bill.term, bill.number)
        except Exception as exc:
            log.warning("print %s unavailable: %s", bill.number, exc)
            return None

    # ------------------------------------------------------------------ publishing

    def _publish(self, bill: Bill, change: StatusChange) -> bool:
        assert change.id is not None
        now = self._clock.now()
        pub_id = self._repo.create_publication(
            Publication(
                term=bill.term,
                number=bill.number,
                kind=PublicationKind.STATUS_UPDATE,
                status=PublicationStatus.PENDING,
                channel_id=self._channel_id,
                status_change_id=change.id,
                created_at=now,
            )
        )
        original = self._repo.get_publication(
            bill.term, bill.number, PublicationKind.NEW_BILL.value, self._channel_id
        )
        reply_to = original.message_id if original else None
        fresh = self._repo.get(bill.term, bill.number) or bill
        try:
            sent = self._publisher.publish_status_update(fresh, change, reply_to)
        except ServiceUnavailableError as exc:
            self._repo.mark_publication(pub_id, PublicationStatus.FAILED, error=exc.describe())
            raise
        except Exception as exc:
            log.exception("status update for druk %s failed: %s", bill.number, exc)
            self._repo.mark_publication(
                pub_id, PublicationStatus.FAILED, error=f"{type(exc).__name__}: {exc}"
            )
            return False
        self._repo.mark_publication(
            pub_id, PublicationStatus.SENT, message_id=sent.message_id, sent_at=self._clock.now()
        )
        return True
