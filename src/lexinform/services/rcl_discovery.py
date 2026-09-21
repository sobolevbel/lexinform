"""Finds new and changed government projects on RCL and prefilters the new ones.

The list page sorted by modification date is read once per run. A new project costs one page:
the keyword prefilter runs on title, hasła and działy of the timeline page; only a candidate gets
its catalogs and consultation letter read, a title miss gets the one catalog with the newest text
for the text prefilter. Known projects only get their `change_date` refreshed, so that tracking
looks at them.
"""

import datetime as dt
import logging
from collections.abc import Sequence
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

CONSULTATION_READ_ATTEMPTS = 2

# A page's worth of network reads, not an LLM budget: kept apart from `opts.max_analyze` so this
# phase's own batch size never depends on the analysis cost cap.
CONSULTATION_READ_BATCH = 40


@dataclass
class RclDiscoveryResult:
    """Counters of one RCL discovery phase."""

    seen: int = 0
    new: int = 0
    refreshed: int = 0
    prefilter_hits: int = 0
    planned: int = 0
    over: int = 0
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

    def discover(
        self, term: int, since: dt.datetime, *, from_register: Sequence[int] = ()
    ) -> RclDiscoveryResult:
        """Projects modified since `since`, plus the ones the register named; an RCL outage
        propagates (the phase is over).

        A project is looked up without a term, RCL ids not being bound to one. One that continues
        a followed plan is left to the tracking phase, where the wykaz row hands its thread over.

        `from_register` is the wykaz phase's find and the only way in for a project the listing
        does not show as changed: the walk is by modification date, so a project untouched since
        this bot's first run is invisible to it (twelve were live on 15 Sept 2026).
        """
        result = RclDiscoveryResult()
        new_rows: list[RclProjectSummary] = []
        for row in self._rcl.list_projects(modified_since=since.date()):
            result.seen += 1
            self._remember_number(row)
            existing = self._repo.find_rcl(rcl_number(row.id))
            if existing is None:
                if self._continues_a_plan(row, result):
                    continue
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
        for project_id in from_register:
            self._take(term, project_id, result)
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

    def _take(self, term: int, project_id: int, result: RclDiscoveryResult) -> None:
        """One project by its id, read and judged exactly as the walk of the listing would.

        A project already stored is left alone: the watcher, not discovery, refreshes it. A
        single project that cannot be read is a warning and not the end of the phase, as in the
        walk itself — only an outage propagates.
        """
        if self._repo.find_rcl(rcl_number(project_id)) is not None:
            return
        result.seen += 1
        try:
            project = self._deepen(self._reader.timeline(project_id))
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            result.failed += 1
            log.warning("RCL project %s named by the register not read: %s", project_id, exc)
            return
        log.info("RCL %s: named by the register, not seen by the listing", project_id)
        self._ingest(term, project, result)

    def read_consultations(self, *, limit: int = CONSULTATION_READ_BATCH) -> int:
        """Read a missing consultation letter before a card can lose its action window.

        A project taken by its text is read for that text alone (`_deepen` → `with_text`), so the
        letter sits in a catalog nobody opened and nothing opens later — the watcher only refreshes
        a project the listing shows as changed. Without it the card goes out with no deadline, no
        address and no reminder, and `action_rcl_window_closed` cannot be reached at all.

        A project can have moved to `analyzed` after a transient failure, so that status belongs
        in the retry queue too. Two failed reads are recorded on the project: this repairs old
        rows without making a project that genuinely has no letter cost a request forever.
        `list_rcl_missing_consultation`, not `list_by_status`: a candidate pool ordered by
        recency could strand a stale row outside `limit` for good as newer bills piled up.
        """
        read = 0
        for bill in self._repo.list_rcl_missing_consultation(limit=limit):
            project = bill.rcl
            if project is None:  # the query's own guarantee, kept for mypy's narrowing
                continue
            if project.consultation_attempts >= CONSULTATION_READ_ATTEMPTS:
                continue
            if project.consultation_stage is None:
                continue
            try:
                complete = self._reader.with_consultation(project)
            except ServiceUnavailableError:
                raise
            except Exception as exc:
                log.warning("consultation of %s not read: %s", bill.number, exc)
                self._repo.save_rcl(
                    bill.term,
                    bill.number,
                    project.model_copy(
                        update={"consultation_attempts": project.consultation_attempts + 1}
                    ),
                )
                continue
            complete = complete.model_copy(
                update={
                    "consultation_attempts": (
                        CONSULTATION_READ_ATTEMPTS
                        if complete.consultation is None
                        else project.consultation_attempts + 1
                    )
                }
            )
            self._repo.save_rcl(bill.term, bill.number, complete)
            read += 1
            log.info("%s: consultation %s", bill.number, complete.consultation)
        return read

    def index_numbers(self, since: dt.date) -> int:
        """Walk the listing and only write down which project carries which wykaz number.

        The listing is the one place that join is published, and reading it costs nothing beyond
        the pages themselves — no timeline, no catalog. A run does this as it goes; this is the
        way to fill in the years before the bot, which is what tells a plan still worth a card
        from one whose project has been on RCL since 2025.
        """
        seen = 0
        for row in self._rcl.list_projects(modified_since=since):
            seen += 1
            self._remember_number(row)
        log.info("RCL numbers indexed: %d row(s) since %s", seen, since)
        return seen

    def _remember_number(self, row: RclProjectSummary) -> None:
        number = normalize_wykaz_number(row.wykaz_number)
        if number is not None:
            self._repo.remember_rcl_wykaz_number(number, row.id, row.created)

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
        """Network only: as much of the project as its prefilter verdict needs.

        A project that is already over is read no further: `_ingest` records the skip.
        """
        return self._deepen(self._reader.timeline(row.id))

    def _deepen(self, project: RclProject) -> RclProject:
        """How much of a project is read is what its title's verdict decides."""
        if project.is_over:
            return project
        if accept_title_hits(self._hits(project, term=0)):
            log.info(
                "RCL %s (%s): candidate, reading its catalogs", project.id, project.wykaz_number
            )
            return self._reader.complete(project)
        if self._text_prefilter:
            log.info(
                "RCL %s (%s): title miss, reading its newest text", project.id, project.wykaz_number
            )
            return self._reader.with_text(project)
        return project

    def _hits(self, project: RclProject, *, term: int) -> list[str]:
        summary = process_summary(project, term=term)
        return self._prefilter.match(summary.title, summary.description)

    def _ingest(self, term: int, project: RclProject, result: RclDiscoveryResult) -> None:
        """Store the project and decide what happens to it.

        One closed on RCL without ever reaching the Sejm, before we saw it, is history: a card
        would invite action on something nobody is working on.
        """
        summary = process_summary(project, term=term)
        bill = self._repo.upsert_summary(summary, now=self._clock.now())
        self._repo.save_rcl(term, bill.number, project)
        self._repo.save_stages(term, bill.number, rcl_stages(project), rcl_fingerprint(project))
        if project.is_over:
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
