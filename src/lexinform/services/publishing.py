"""Publishes analysed, relevant bills exactly once per channel."""

from __future__ import annotations

import logging
from dataclasses import dataclass

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
    published: int = 0
    skipped: int = 0
    failed: int = 0


class PublishingService:
    def __init__(
        self,
        gateway: SejmGateway,
        repo: BillRepository,
        publisher: Publisher,
        clock: Clock,
        *,
        channel_id: str,
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._publisher = publisher
        self._clock = clock
        self._channel_id = channel_id

    def publish_new(
        self, term: int, *, min_score: int, limit: int, publish: bool = True
    ) -> PublishingResult:
        result = PublishingResult()
        for bill in self._repo.list_publish_candidates(
            term, self._channel_id, min_score=min_score, limit=limit
        ):
            if not publish:
                self._record_skipped(bill)
                result.skipped += 1
                continue
            if self.publish_bill(bill):
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
        print_info = self._safe_print(bill)
        try:
            sent = self._publisher.publish_new_bill(bill, print_info)
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

    def _safe_print(self, bill: Bill) -> PrintInfo | None:
        try:
            return self._gateway.get_print(bill.term, bill.number)
        except Exception as exc:
            log.warning("print %s unavailable, publishing without PDF: %s", bill.number, exc)
            return None
