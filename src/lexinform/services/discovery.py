"""Finds new/changed bills in the Sejm API and runs the keyword prefilter on new ones."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from lexinform.adapters.sejm_api import BILL_DOCUMENT_TYPE
from lexinform.keywords import KeywordPrefilter
from lexinform.models import BillStatus, DocumentType, ProcessSummary
from lexinform.ports import BillRepository, Clock, SejmGateway

log = logging.getLogger(__name__)


@dataclass
class DiscoveryResult:
    seen: int = 0
    new: int = 0
    prefilter_hits: int = 0


class BillDiscoveryService:
    def __init__(
        self,
        gateway: SejmGateway,
        repo: BillRepository,
        prefilter: KeywordPrefilter,
        clock: Clock,
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._prefilter = prefilter
        self._clock = clock

    def discover(self, term: int, since: datetime) -> DiscoveryResult:
        result = DiscoveryResult()
        for summary in self._gateway.iter_processes(
            term, modified_since=since, document_type=BILL_DOCUMENT_TYPE
        ):
            if summary.document_type_enum != DocumentType.BILL:
                continue
            result.seen += 1
            if self._ingest(summary, result):
                result.new += 1
        if result.seen == 0:
            log.warning("Sejm API returned no bills modified since %s", since.isoformat())
        log.info(
            "discovery: seen=%d new=%d prefilter_hits=%d",
            result.seen,
            result.new,
            result.prefilter_hits,
        )
        return result

    def _ingest(self, summary: ProcessSummary, result: DiscoveryResult) -> bool:
        existing = self._repo.get(summary.term, summary.number)
        now = self._clock.now()
        bill = self._repo.upsert_summary(summary, now=now)
        is_new = existing is None
        title_changed = existing is not None and existing.summary.title != summary.title
        needs_prefilter = is_new or (
            title_changed
            and existing is not None
            and existing.status == BillStatus.SKIPPED_PREFILTER
        )
        if needs_prefilter:
            hits = self._prefilter.match(summary.title, summary.description)
            status = BillStatus.ANALYSIS_PENDING if hits else BillStatus.SKIPPED_PREFILTER
            self._repo.set_status(bill.term, bill.number, status, prefilter_hits=hits)
            if hits:
                result.prefilter_hits += 1
                log.info("candidate druk %s (%s): %s", bill.number, ", ".join(hits), summary.title)
        return is_new
