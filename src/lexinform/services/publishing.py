"""Publishes analysed, relevant bills exactly once per channel."""

import logging
from dataclasses import dataclass

from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    Bill,
    PrintInfo,
    Publication,
    PublicationKind,
    PublicationStatus,
)
from lexinform.ports import BillRepository, Clock, Publisher, SejmGateway

log = logging.getLogger(__name__)


@dataclass
class PublishingResult:
    """Counters of one publishing phase."""

    published: int = 0
    skipped: int = 0
    failed: int = 0
    fatal_error: str | None = None


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
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._publisher = publisher
        self._clock = clock
        self._channel_id = channel_id
        self._max_attempts = max_attempts

    def publish_new(self, *, min_score: int, limit: int, publish: bool = True) -> PublishingResult:
        result = PublishingResult()
        for bill in self._repo.list_publish_candidates(
            self._channel_id,
            min_score=min_score,
            limit=limit,
            max_attempts=self._max_attempts,
        ):
            if not publish:
                self._record_skipped(bill)
                result.skipped += 1
                continue
            try:
                ok = self.publish_bill(bill)
            except ServiceUnavailableError as exc:
                result.failed += 1
                result.fatal_error = exc.describe()
                log.error("aborting publishing phase: %s", result.fatal_error)
                break
            if ok:
                result.published += 1
            else:
                result.failed += 1
        return result

    def publish_bill(self, bill: Bill) -> bool:
        """Send one bill. Records a pending publication before sending so a crash cannot
        cause a duplicate post; returns True on success."""
        now = self._clock.now()
        pub_id = self._repo.create_publication(
            Publication(
                term=bill.term,
                number=bill.number,
                kind=PublicationKind.NEW_BILL,
                status=PublicationStatus.PENDING,
                channel_id=self._channel_id,
                created_at=now,
            )
        )
        print_info = self._safe_print(bill) if bill.has_process else None
        bill = self._with_submission(bill)
        try:
            sent = self._publisher.publish_new_bill(bill, print_info)
        except ServiceUnavailableError as exc:
            self._repo.mark_publication(pub_id, PublicationStatus.FAILED, error=exc.describe())
            raise
        except Exception as exc:
            log.exception("publishing druk %s failed: %s", bill.number, exc)
            self._repo.mark_publication(
                pub_id, PublicationStatus.FAILED, error=f"{type(exc).__name__}: {exc}"
            )
            return False
        self._repo.mark_publication(
            pub_id,
            PublicationStatus.SENT,
            message_id=sent.message_id,
            document_message_ids=list(sent.document_message_ids),
            sent_at=self._clock.now(),
        )
        log.info("published druk %s as message %s", bill.number, sent.message_id)
        return True

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

    def _safe_print(self, bill: Bill) -> PrintInfo | None:
        try:
            return self._gateway.get_print(bill.term, bill.number)
        except Exception as exc:
            log.warning("print %s unavailable, publishing without PDF: %s", bill.number, exc)
            return None
