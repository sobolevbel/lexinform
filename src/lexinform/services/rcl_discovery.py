"""Finds new and changed government projects on RCL and prefilters the new ones.

The list page sorted by modification date is read once per run; new projects are fetched in full
(timeline, catalogs, consultation letter) and prefiltered on title, hasła and działy; known
projects only get their `change_date` refreshed, so that tracking looks at them.
"""

import datetime as dt
import logging
from dataclasses import dataclass

from lexinform.concurrency import fan_out
from lexinform.errors import ServiceUnavailableError
from lexinform.keywords import KeywordPrefilter
from lexinform.models import (
    BillStatus,
    RclProject,
    RclProjectSummary,
    process_summary,
    rcl_fingerprint,
    rcl_number,
    rcl_stages,
)
from lexinform.ports import BillRepository, Clock, RclGateway
from lexinform.services.rcl_projects import RclProjectReader

log = logging.getLogger(__name__)


@dataclass
class RclDiscoveryResult:
    """Counters of one RCL discovery phase."""

    seen: int = 0
    new: int = 0
    refreshed: int = 0
    prefilter_hits: int = 0
    failed: int = 0


class RclDiscoveryService:
    def __init__(
        self,
        rcl: RclGateway,
        repo: BillRepository,
        reader: RclProjectReader,
        prefilter: KeywordPrefilter,
        clock: Clock,
        *,
        text_prefilter: bool = True,
        workers: int = 1,
    ) -> None:
        self._rcl = rcl
        self._repo = repo
        self._reader = reader
        self._prefilter = prefilter
        self._clock = clock
        self._text_prefilter = text_prefilter
        self._workers = workers

    def discover(self, term: int, since: dt.datetime) -> RclDiscoveryResult:
        """Projects modified since `since`; an RCL outage propagates (the phase is over)."""
        result = RclDiscoveryResult()
        new_rows: list[RclProjectSummary] = []
        for row in self._rcl.list_projects(modified_since=since.date()):
            result.seen += 1
            existing = self._repo.get(term, rcl_number(row.id))
            if existing is None:
                new_rows.append(row)
                continue
            modified = dt.datetime.combine(row.modified, dt.time(0, 0), tzinfo=dt.UTC)
            if modified > existing.summary.change_date:
                refreshed = existing.summary.model_copy(update={"change_date": modified})
                self._repo.upsert_summary(refreshed, now=self._clock.now())
                result.refreshed += 1
        for outcome in fan_out(new_rows, self._read, workers=self._workers):
            try:
                project = outcome.result()
            except ServiceUnavailableError:
                raise
            except Exception as exc:
                result.failed += 1
                log.warning("RCL project %s not read: %s", outcome.item.id, exc)
                continue
            self._ingest(term, project, result)
        log.info(
            "RCL discovery: seen=%d new=%d refreshed=%d prefilter_hits=%d failed=%d",
            result.seen,
            result.new,
            result.refreshed,
            result.prefilter_hits,
            result.failed,
        )
        return result

    def _read(self, row: RclProjectSummary) -> RclProject:
        return self._reader.read(row.id)

    def _ingest(self, term: int, project: RclProject, result: RclDiscoveryResult) -> None:
        summary = process_summary(project, term=term)
        bill = self._repo.upsert_summary(summary, now=self._clock.now())
        self._repo.save_rcl(term, bill.number, project)
        self._repo.save_stages(term, bill.number, rcl_stages(project), rcl_fingerprint(project))
        hits = self._prefilter.match(summary.title, summary.description)
        if hits:
            status = BillStatus.ANALYSIS_PENDING
            result.prefilter_hits += 1
            log.info("candidate %s (%s): %s", bill.number, ", ".join(hits), summary.title)
        elif self._text_prefilter and project.text_documents():
            status = BillStatus.TEXT_PREFILTER_PENDING
        else:
            status = BillStatus.SKIPPED_PREFILTER
        self._repo.set_status(term, bill.number, status, prefilter_hits=hits)
        result.new += 1
