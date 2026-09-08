"""The daily run: discover -> prefilter -> analyse -> publish -> track -> report."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from pydantic import BaseModel

from lexinform.errors import ServiceUnavailableError
from lexinform.logging_setup import MemoryLogHandler
from lexinform.models import RunReport, TokenUsage
from lexinform.ports import BillRepository, Clock, RunNotifier
from lexinform.services.analysis import AnalysisService
from lexinform.services.discovery import BillDiscoveryService
from lexinform.services.publishing import PublishingService
from lexinform.services.text_prefilter import TextPrefilterService
from lexinform.services.tracking import StatusTrackingService

log = logging.getLogger(__name__)


class RunOptions(BaseModel):
    term: int
    since: datetime | None = None
    dry_run: bool = False
    discover: bool = True
    publish: bool = True
    track: bool = True
    max_publish: int = 10
    max_analyze: int = 40
    max_text_prefilter: int = 20
    min_score: int = 3
    full_track: bool = False  # check every followed bill, not only those the API lists as changed
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
        text_prefilter: TextPrefilterService | None = None,
        first_run_lookback_days: int = 1,
        rerun_overlap_days: int = 1,
        pre_print: bool = True,
        full_track_weekday: int | None = 0,
    ) -> None:
        self._repo = repo
        self._discovery = discovery
        self._analysis = analysis
        self._publishing = publishing
        self._tracking = tracking
        self._clock = clock
        self._notifier = notifier
        self._text_prefilter = text_prefilter
        self._first_run_lookback = timedelta(days=first_run_lookback_days)
        self._overlap = timedelta(days=rerun_overlap_days)
        self._pre_print = pre_print
        self._full_track_weekday = full_track_weekday  # Monday by default; None = never

    def resolve_since(self, requested: datetime | None) -> datetime:
        """Discovery watermark: the start of the last run that completed discovery, minus overlap.

        Publishing or analysis errors do not hold the watermark back: bills are stored on discovery
        and retried from the database, so only a failed discovery must be re-scanned.
        """
        if requested is not None:
            return requested
        last = self._repo.last_discovery_started_at()
        if last is not None:
            return last - self._overlap
        return self._clock.now() - self._first_run_lookback

    def run(self, opts: RunOptions) -> RunReport:
        started = self._clock.now()
        report = RunReport(
            started_at=started, since=started, mode="dry_run" if opts.dry_run else opts.mode
        )
        captured = MemoryLogHandler()
        captured.install()
        run_id: int | None = None
        try:
            self._repo.migrate()
            report.since = self.resolve_since(opts.since)
            log.info(
                "run started (mode=%s, since=%s, term=%d)",
                report.mode,
                report.since.isoformat(),
                opts.term,
            )
            if opts.dry_run:
                self._repo.begin()
            run_id = self._repo.start_run(report)
            self._execute(opts, report.since, report)
        except Exception as exc:  # last line of defence: report, never crash the process
            log.exception("run failed unexpectedly: %s", exc)
            report.errors.append(f"unexpected failure: {type(exc).__name__}: {exc}")
        finally:
            report.finished_at = self._clock.now()
            if run_id is not None:
                try:
                    self._repo.finish_run(run_id, report)
                except Exception as exc:
                    log.exception("could not record the run: %s", exc)
                    report.errors.append(f"could not record the run: {type(exc).__name__}: {exc}")
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
            log.error(msg, extra={"in_report": True})
            report.errors.append(msg)

        if opts.discover:
            self._phase(report, "discovery", lambda: self._discover(opts, since, report))
        if self._text_prefilter is not None:
            self._phase(report, "text prefilter", lambda: self._prefilter_text(opts, report))
        self._phase(report, "analysis", lambda: self._analyse(opts, report))
        self._phase(report, "publishing", lambda: self._publish(opts, report))
        if opts.track:
            self._phase(report, "tracking", lambda: self._track(opts, report))

    @staticmethod
    def _phase(report: RunReport, name: str, action: Callable[[], None]) -> None:
        """Run one phase; an external outage or a bug ends the phase, not the run."""
        # `in_report` keeps these out of the captured warnings: they are listed as errors already.
        started = time.perf_counter()
        try:
            action()
        except ServiceUnavailableError as exc:
            message = f"{name}: {exc.describe()}"
            log.error(message, extra={"in_report": True})
            report.errors.append(message)
        except Exception as exc:
            message = f"{name} failed: {type(exc).__name__}: {exc}"
            log.exception(message, extra={"in_report": True})
            report.errors.append(message)
        finally:
            elapsed = time.perf_counter() - started
            report.phase_seconds[name] = round(elapsed, 1)
            log.info("%s took %.1fs", name, elapsed)

    def _discover(self, opts: RunOptions, since: datetime, report: RunReport) -> None:
        discovered = self._discovery.discover(opts.term, since, pre_print=self._pre_print)
        report.discovery_ok = True
        report.discovered = discovered.new
        report.pre_print_discovered = discovered.pre_print_new
        report.prefilter_hits = discovered.prefilter_hits

    def _prefilter_text(self, opts: RunOptions, report: RunReport) -> None:
        assert self._text_prefilter is not None
        checked = self._text_prefilter.run(opts.term, limit=opts.max_text_prefilter)
        report.text_prefilter_checked = checked.checked
        report.text_prefilter_hits = checked.hits
        if checked.fatal_error:
            report.errors.append(f"text prefilter: {checked.fatal_error}")

    def _analyse(self, opts: RunOptions, report: RunReport) -> None:
        analysed = self._analysis.analyze_pending(opts.term, limit=opts.max_analyze)
        report.analyzed = analysed.analyzed
        report.triaged_out = analysed.triaged_out
        report.analysis_failures = analysed.failed
        report.rejected = [
            v for v in analysed.verdicts if not v.relevant or v.score < opts.min_score
        ]
        report.llm_input_tokens += analysed.input_tokens
        report.llm_output_tokens += analysed.output_tokens
        _merge_usage(report, analysed.usage)
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
        # Bills the API did not list as modified since the watermark have nothing new, so the
        # daily check touches only the changed ones; once a week every followed bill is fetched
        # in case something moved without a visible change (or discovery was skipped/failed).
        weekly = (
            self._full_track_weekday is not None
            and self._clock.now().weekday() == self._full_track_weekday
        )
        full = opts.full_track or not opts.discover or not report.discovery_ok or weekly
        tracked = self._tracking.check_updates(
            opts.term, publish=opts.publish, changed_since=None if full else report.since
        )
        report.tracked = tracked.checked
        report.updates = tracked.published if opts.publish else tracked.changed
        report.reanalyzed = tracked.reanalyzed
        report.linked = tracked.linked
        report.acts_published = tracked.acts_published
        report.in_force_posted = tracked.in_force_posted
        report.consultation_reminders = tracked.consultation_reminders
        report.llm_input_tokens += tracked.input_tokens
        report.llm_output_tokens += tracked.output_tokens
        _merge_usage(report, tracked.usage)
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


def _merge_usage(report: RunReport, usage: dict[str, TokenUsage]) -> None:
    for model, tokens in usage.items():
        report.llm_usage[model] = report.llm_usage.get(model, TokenUsage()).plus(tokens)
