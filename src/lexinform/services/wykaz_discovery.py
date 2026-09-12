"""Finds bills the government has announced but not yet drafted, in the wykaz prac RM.

The whole register arrives as one file, so every run sees all of it and the prefilter runs on
the title and on what art. 3 ust. 2 obliges the entry to say (the reasons and the essence of the
planned solutions). Only entries published since the watermark are stored: an entry we reject is
never revisited — `Data publikacji` does not move when the register is edited — so storing the
misses would grow the state dump by megabytes and buy nothing. The rest of the register is
counted as a backlog in the report, and `lexinform scan --since …` takes it when it is wanted.
"""

import datetime as dt
import logging
from dataclasses import dataclass

from lexinform.keywords import KeywordPrefilter, accept_title_hits
from lexinform.models import BillStatus, WykazEntry, wykaz_fingerprint, wykaz_summary
from lexinform.ports import BillRepository, Clock, WykazGateway

log = logging.getLogger(__name__)


@dataclass
class WykazDiscoveryResult:
    """Counters of one wykaz discovery phase."""

    seen: int = 0
    new: int = 0
    prefilter_hits: int = 0
    backlog: int = 0
    over: int = 0


class WykazDiscoveryService:
    def __init__(
        self,
        wykaz: WykazGateway,
        repo: BillRepository,
        prefilter: KeywordPrefilter,
        clock: Clock,
    ) -> None:
        self._wykaz = wykaz
        self._repo = repo
        self._prefilter = prefilter
        self._clock = clock

    def discover(self, term: int, since: dt.datetime) -> WykazDiscoveryResult:
        """Entries published since `since`; a gov.pl outage propagates (the phase is over).

        Only bills are followed, not rozporządzenia or government programmes, and only entries
        published since the watermark: the rest are counted as `backlog`, which `scan --since`
        takes. A plan that was realised or withdrawn before we ever saw it gets no card — there
        is no action left to invite — and neither does one whose project is already on RCL under
        the same number: that row is the bill, with its text, and a second thread for the plan
        behind it would only repeat it.
        """
        result = WykazDiscoveryResult()
        for entry in self._wykaz.entries():
            if not entry.is_bill:
                continue
            result.seen += 1
            hits = self._hits(entry, term=term)
            if not accept_title_hits(hits):
                continue
            if self._repo.find_wykaz(entry.bill_number) is not None:
                continue
            if entry.published_at < since:
                result.backlog += 1
                continue
            if not entry.is_open:
                log.info("wykaz %s: %s already, not followed", entry.number, entry.status)
                result.over += 1
                continue
            if self._repo.find_by_wykaz_number(entry.number) is not None:
                log.info("wykaz %s: already followed as an RCL project", entry.number)
                continue
            self._ingest(term, entry, hits, result)
        log.info(
            "wykaz discovery: seen=%d new=%d prefilter_hits=%d backlog=%d over=%d",
            result.seen,
            result.new,
            result.prefilter_hits,
            result.backlog,
            result.over,
        )
        return result

    def _hits(self, entry: WykazEntry, *, term: int) -> list[str]:
        summary = wykaz_summary(entry, term=term)
        return self._prefilter.match(summary.title, summary.description)

    def _ingest(
        self, term: int, entry: WykazEntry, hits: list[str], result: WykazDiscoveryResult
    ) -> None:
        """Follow the plan: one announcement is not a timeline, so the row gets no stages, only
        a fingerprint of what the government says it plans."""
        summary = wykaz_summary(entry, term=term)
        bill = self._repo.upsert_summary(summary, now=self._clock.now())
        self._repo.save_wykaz(term, bill.number, entry)
        self._repo.save_stages(term, bill.number, (), wykaz_fingerprint(entry))
        self._repo.set_status(term, bill.number, BillStatus.ANALYSIS_PENDING, prefilter_hits=hits)
        result.new += 1
        result.prefilter_hits += 1
        log.info("planned bill %s (%s): %s", bill.number, ", ".join(hits), summary.title)
