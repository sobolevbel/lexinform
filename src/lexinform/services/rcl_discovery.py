"""Finds new and changed government projects on RCL and prefilters the new ones.

The list page sorted by modification date is read once per run. A new project costs one page:
the keyword prefilter runs on title, hasła and działy of the timeline page; only a candidate gets
its catalogs and consultation letter read, a title miss gets the one catalog with the newest text
for the text prefilter. Known projects only get their `change_date` refreshed, so that tracking
looks at them.
"""

import datetime as dt
import logging
from dataclasses import dataclass

from lexinform.concurrency import fan_out
from lexinform.errors import ServiceUnavailableError
from lexinform.keywords import KeywordPrefilter, accept_title_hits
from lexinform.models import (
    BillStatus,
    RclProject,
    RclProjectSummary,
    normalize_wykaz_number,
    process_summary,
    rcl_fingerprint,
    rcl_number,
    rcl_stages,
    wykaz_number,
)
from lexinform.ports import BillRepository, Clock, RclGateway
from lexinform.services.discovery import NOT_FOLLOWED
from lexinform.services.rcl_projects import RclProjectReader

log = logging.getLogger(__name__)


@dataclass
class RclDiscoveryResult:
    """Counters of one RCL discovery phase."""

    seen: int = 0
    new: int = 0
    refreshed: int = 0
    prefilter_hits: int = 0
    planned: int = 0  # projects that continue a plan we already follow
    over: int = 0  # first seen closed on RCL without reaching the Sejm: no analysis, no card
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
            # RCL ids are not bound to a Sejm term: a project joined to a druk of the previous
            # term stays stored under that term and must not come back as new.
            existing = self._repo.find_rcl(rcl_number(row.id))
            if existing is None:
                if self._continues_a_plan(row, result):
                    continue  # the wykaz row it continues takes it over in the tracking phase
                new_rows.append(row)
                continue
            if row.modified is None:
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
            "RCL discovery: seen=%d new=%d refreshed=%d prefilter_hits=%d over=%d failed=%d",
            result.seen,
            result.new,
            result.refreshed,
            result.prefilter_hits,
            result.over,
            result.failed,
        )
        return result

    def _continues_a_plan(self, row: RclProjectSummary, result: RclDiscoveryResult) -> bool:
        """A project published under the number of a plan we follow: stamp it on the plan and
        leave the row alone. Ingesting it here would give the same bill a second card before
        the linker could hand the plan's card over (`publishing._inherited_card` keys on the
        link, which does not exist yet)."""
        number = normalize_wykaz_number(row.wykaz_number)
        if number is None:
            return False
        plan = self._repo.find_wykaz(wykaz_number(number))
        if plan is None or plan.wykaz is None or plan.status in NOT_FOLLOWED:
            return False
        if plan.wykaz.rcl_project_id != row.id:
            self._repo.save_wykaz(
                plan.term, plan.number, plan.wykaz.model_copy(update={"rcl_project_id": row.id})
            )
            log.info("RCL %s is the project of the planned bill %s", row.id, plan.number)
        result.planned += 1
        return True

    def _read(self, row: RclProjectSummary) -> RclProject:
        """Network only: as much of the project as its prefilter verdict needs."""
        project = self._reader.timeline(row.id)
        if project.is_over:
            return project  # dropped before we ever saw it: `_ingest` records the skip
        if accept_title_hits(self._hits(project, term=0)):
            log.info("RCL %s (%s): candidate, reading its catalogs", row.id, row.wykaz_number)
            return self._reader.complete(project)
        if self._text_prefilter:
            log.info("RCL %s (%s): title miss, reading its newest text", row.id, row.wykaz_number)
            return self._reader.with_text(project)
        return project

    def _hits(self, project: RclProject, *, term: int) -> list[str]:
        summary = process_summary(project, term=term)
        return self._prefilter.match(summary.title, summary.description)

    def _ingest(self, term: int, project: RclProject, result: RclDiscoveryResult) -> None:
        summary = process_summary(project, term=term)
        bill = self._repo.upsert_summary(summary, now=self._clock.now())
        self._repo.save_rcl(term, bill.number, project)
        self._repo.save_stages(term, bill.number, rcl_stages(project), rcl_fingerprint(project))
        if project.is_over:
            # Closed on RCL without reaching the Sejm before we ever saw it: the project is
            # history, and a card would invite action on something nobody is working on.
            self._repo.set_status(
                term,
                bill.number,
                BillStatus.SKIPPED_CLOSED,
                reason=f"closed on RCL on {project.modified}, before it was first seen",
            )
            log.info(
                "%s was closed on RCL on %s before we saw it: not followed",
                bill.number,
                project.modified,
            )
            result.over += 1
            result.new += 1
            return
        hits = self._hits(project, term=term)
        if accept_title_hits(hits):
            status = BillStatus.ANALYSIS_PENDING
            result.prefilter_hits += 1
            log.info("candidate %s (%s): %s", bill.number, ", ".join(hits), summary.title)
        elif self._text_prefilter and project.text_documents():
            status = BillStatus.TEXT_PREFILTER_PENDING
        else:
            status = BillStatus.SKIPPED_PREFILTER
        self._repo.set_status(term, bill.number, status, prefilter_hits=hits)
        result.new += 1
