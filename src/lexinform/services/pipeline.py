"""The daily run: discover -> prefilter -> analyse -> publish -> track -> report."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from pydantic import BaseModel

from lexinform.errors import ServiceUnavailableError
from lexinform.logging_setup import MemoryLogHandler
from lexinform.models import RunReport
from lexinform.ports import BillRepository, Clock, RunNotifier
from lexinform.services.analysis import AnalysisService
from lexinform.services.discovery import BillDiscoveryService
from lexinform.services.publishing import PublishingService
from lexinform.services.tracking import StatusTrackingService

log = logging.getLogger(__name__)


class RunOptions(BaseModel):
    term: int
    since: datetime | None = None
    dry_run: bool = False
    publish: bool = True
    track: bool = True
    max_publish: int = 10
    max_analyze: int = 40
    min_score: int = 2
    mode: str = "run"


class DailyPipeline:
    def __init__(
        self,
        repo: BillRepository,
        discovery: BillDiscoveryService,
        analysis: AnalysisService,
        publishing: PublishingService,
        tracking: StatusTrackingService,
        clock: Clock,
        *,
        notifier: RunNotifier | None = None,
        first_run_lookback_days: int = 1,
        rerun_overlap_days: int = 1,
    ) -> None:
        self._repo = repo
        self._discovery = discovery
        self._analysis = analysis
        self._publishing = publishing
        self._tracking = tracking
        self._clock = clock
        self._notifier = notifier
        self._first_run_lookback = timedelta(days=first_run_lookback_days)
        self._overlap = timedelta(days=rerun_overlap_days)

    def resolve_since(self, requested: datetime | None) -> datetime:
        if requested is not None:
            return requested
        last = self._repo.last_successful_run_started_at()
        if last is not None:
            return last - self._overlap
        return self._clock.now() - self._first_run_lookback

    def run(self, opts: RunOptions) -> RunReport:
        self._repo.migrate()
        started = self._clock.now()
        since = self.resolve_since(opts.since)
        report = RunReport(
            started_at=started, since=since, mode="dry_run" if opts.dry_run else opts.mode
        )
        log.info(
            "run started (mode=%s, since=%s, term=%d)", report.mode, since.isoformat(), opts.term
        )

        captured = MemoryLogHandler()
        captured.install()
        if opts.dry_run:
            self._repo.begin()
        run_id = self._repo.start_run(report)
        try:
            self._execute(opts, since, report)
        except Exception as exc:  # last line of defence: report, never crash the process
            log.exception("run failed unexpectedly: %s", exc)
            report.errors.append(f"unexpected failure: {type(exc).__name__}: {exc}")
        finally:
            report.finished_at = self._clock.now()
            self._repo.finish_run(run_id, report)
            if opts.dry_run:
                self._repo.rollback()
                log.info("dry run: database changes rolled back")
            captured.uninstall()
            self._notify(report, captured)
        log.info(
            "run finished ok=%s discovered=%d hits=%d analyzed=%d failures=%d "
            "published=%d updates=%d reanalyzed=%d",
            report.ok,
            report.discovered,
            report.prefilter_hits,
            report.analyzed,
            report.analysis_failures,
            report.published,
            report.updates,
            report.reanalyzed,
        )
        return report

    def _execute(self, opts: RunOptions, since: datetime, report: RunReport) -> None:
        stale = self._repo.mark_stale_pending_as_unknown(now=self._clock.now())
        if stale:
            msg = f"{stale} publication(s) were left pending by a previous run; marked unknown"
            log.error(msg)
            report.errors.append(msg)

        self._phase(report, "discovery", lambda: self._discover(opts, since, report))
        self._phase(report, "analysis", lambda: self._analyse(opts, report))
        self._phase(report, "publishing", lambda: self._publish(opts, report))
        if opts.track:
            self._phase(report, "tracking", lambda: self._track(opts, report))

    @staticmethod
    def _phase(report: RunReport, name: str, action: Callable[[], None]) -> None:
        """Run one phase; an external outage or a bug ends the phase, not the run."""
        try:
            action()
        except ServiceUnavailableError as exc:
            message = f"{name}: {exc.describe()}"
            log.error(message)
            report.errors.append(message)
        except Exception as exc:
            message = f"{name} failed: {type(exc).__name__}: {exc}"
            log.exception(message)
            report.errors.append(message)

    def _discover(self, opts: RunOptions, since: datetime, report: RunReport) -> None:
        discovered = self._discovery.discover(opts.term, since)
        report.discovered = discovered.new
        report.prefilter_hits = discovered.prefilter_hits

    def _analyse(self, opts: RunOptions, report: RunReport) -> None:
        analysed = self._analysis.analyze_pending(opts.term, limit=opts.max_analyze)
        report.analyzed = analysed.analyzed
        report.analysis_failures = analysed.failed
        report.llm_input_tokens += analysed.input_tokens
        report.llm_output_tokens += analysed.output_tokens
        if analysed.fatal_error:
            report.errors.append(f"analysis: {analysed.fatal_error}")

    def _publish(self, opts: RunOptions, report: RunReport) -> None:
        published = self._publishing.publish_new(
            opts.term, min_score=opts.min_score, limit=opts.max_publish, publish=opts.publish
        )
        report.published = published.published
        if published.fatal_error:
            report.errors.append(f"publishing: {published.fatal_error}")
        elif published.failed:
            report.errors.append(f"{published.failed} publication(s) failed")

    def _track(self, opts: RunOptions, report: RunReport) -> None:
        tracked = self._tracking.check_updates(opts.term, publish=opts.publish)
        report.tracked = tracked.checked
        report.updates = tracked.published if opts.publish else tracked.changed
        report.reanalyzed = tracked.reanalyzed
        report.llm_input_tokens += tracked.input_tokens
        report.llm_output_tokens += tracked.output_tokens
        if tracked.fatal_error:
            report.errors.append(f"tracking: {tracked.fatal_error}")
        elif tracked.failed:
            report.errors.append(f"{tracked.failed} status update(s) failed")

    def _notify(self, report: RunReport, captured: MemoryLogHandler) -> None:
        if self._notifier is None:
            return
        lines = list(captured.lines)
        if captured.dropped:
            lines.append(f"... and {captured.dropped} more")
        try:
            self._notifier.notify(report, lines)
        except Exception as exc:  # the log channel must never break the run itself
            log.exception("run notification failed: %s", exc)
