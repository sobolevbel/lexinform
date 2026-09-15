"""Finds bills the government has announced but not yet drafted, in the wykaz prac RM.

The whole register arrives as one file, so every run sees all of it and judges every entry: the
gates are a keyword match and two indexed lookups, so skipping the older ones saves nothing. A
rejected entry is not stored — the 775 bill entries with their paragraphs would add megabytes to
the state dump.

The watermark decides only what counts as news in the report. Deciding what was *judged* left an
entry rewritten into relevance behind it for ever, `Data publikacji` never moving on an edit.
"""

import datetime as dt
import logging
from dataclasses import dataclass, field

from lexinform.keywords import KeywordPrefilter, accept_title_hits
from lexinform.models import BillStatus, WykazEntry, wykaz_fingerprint, wykaz_summary
from lexinform.ports import BillRepository, Clock, WykazGateway

log = logging.getLogger(__name__)


@dataclass
class WykazDiscoveryResult:
    """Counters of one wykaz discovery phase; `on_rcl` names the projects RCL discovery takes."""

    seen: int = 0
    new: int = 0
    prefilter_hits: int = 0
    over: int = 0
    on_rcl: list[int] = field(default_factory=list)


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
        """Every entry of the register; a gov.pl outage propagates (the phase is over).

        Only bills are followed, not rozporządzenia or programmes. A plan realised or withdrawn
        before we saw it gets no card, there being no action left to invite, and neither does one
        whose project is already on RCL: that row is the bill, with its text. `since` decides what
        is *news* and not what is judged.
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
            news = entry.published_at >= since
            if not entry.is_open:
                if news:
                    log.info("wykaz %s: %s already, not followed", entry.number, entry.status)
                    result.over += 1
                continue
            if self._repo.find_by_wykaz_number(entry.number) is not None:
                log.info("wykaz %s: already followed as an RCL project", entry.number)
                continue
            project_id = self._repo.find_rcl_project_of_plan(
                entry.number, entry.published_at.date()
            )
            if project_id is not None:
                log.info(
                    "wykaz %s: its project is out on RCL (%d); the plan gets no card and the"
                    " project is handed to RCL discovery",
                    entry.number,
                    project_id,
                )
                result.on_rcl.append(project_id)
                continue
            self._ingest(term, entry, hits, result)
        log.info(
            "wykaz discovery: seen=%d new=%d prefilter_hits=%d over=%d on_rcl=%d",
            result.seen,
            result.new,
            result.prefilter_hits,
            result.over,
            len(result.on_rcl),
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
