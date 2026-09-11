"""The daily run: discover -> prefilter -> analyse -> publish -> track -> report.

Discovery works in the current Sejm term (see `TermResolver`); the queues and the tracking are
not scoped to a term (every row carries its own), so that the bills of the previous kadencja that
still matter (acts on their way to Dziennik Ustaw, reminders, failed posts) are not forgotten when
the Sejm moves on.
"""

import logging
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from pydantic import BaseModel

from lexinform.errors import ServiceUnavailableError
from lexinform.logging_setup import MemoryLogHandler
from lexinform.models import RunMode, RunReport, TokenUsage
from lexinform.ports import BillRepository, Clock, RunNotifier
from lexinform.services.analysis import AnalysisService
from lexinform.services.commands import CommandService
from lexinform.services.discovery import BillDiscoveryService
from lexinform.services.publishing import PublishingService
from lexinform.services.rcl_discovery import RclDiscoveryService
from lexinform.services.terms import TermResolver
from lexinform.services.text_prefilter import TextPrefilterService
from lexinform.services.tracking import StatusTrackingService
from lexinform.services.tracking.result import TrackingResult

log = logging.getLogger(__name__)


class RunOptions(BaseModel):
    """What one run should do; built by the CLI from settings and flags."""

    term: int | None = None  # None: the current term, as the Sejm API reports it
    since: datetime | None = None
    dry_run: bool = False
    discover: bool = True
    rcl: bool = True  # also look at legislacja.rcl.gov.pl (when the pipeline has the service)
    publish: bool = True
    track: bool = True
    max_publish: int = 10
    max_analyze: int = 40
    max_text_prefilter: int = 20
    min_score: int = 3
    full_track: bool = False  # check every followed bill, not only those the API lists as changed
    commands: bool = True  # answer the operator commands waiting in the inbox (when there is one)
    mode: RunMode = RunMode.RUN


class DailyPipeline:
    """Runs the phases in order, isolates their failures and produces the run report."""

    def __init__(
        self,
        repo: BillRepository,
        discovery: BillDiscoveryService,
        analysis: AnalysisService,
        publishing: PublishingService,
        tracking: StatusTrackingService,
        clock: Clock,
        *,
        terms: TermResolver,
        notifier: RunNotifier | None = None,
        text_prefilter: TextPrefilterService | None = None,
        rcl_discovery: RclDiscoveryService | None = None,
        commands: CommandService | None = None,
        first_run_lookback_days: int = 1,
        rerun_overlap_days: int = 1,
        runs_retention_days: int | None = 90,
        pre_print: bool = True,
        full_track_weekday: int | None = 0,
    ) -> None:
        self._repo = repo
        self._discovery = discovery
        self._analysis = analysis
        self._publishing = publishing
        self._tracking = tracking
        self._clock = clock
        self._terms = terms
        self._notifier = notifier
        self._text_prefilter = text_prefilter
        self._rcl_discovery = rcl_discovery
        self._commands = commands  # None: no inbox configured
        self._first_run_lookback = timedelta(days=first_run_lookback_days)
        self._overlap = timedelta(days=rerun_overlap_days)
        self._runs_retention = (
            timedelta(days=runs_retention_days) if runs_retention_days is not None else None
        )
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
            started_at=started, since=started, mode=RunMode.DRY_RUN if opts.dry_run else opts.mode
        )
        captured = MemoryLogHandler()
        captured.install()
        run_id: int | None = None
        try:
            self._repo.migrate()
            report.since = self.resolve_since(opts.since)
            if opts.dry_run:
                self._repo.begin()
            run_id = self._repo.start_run(report)
            report.term = self._resolve_term(opts, report)
            if report.term is not None:
                log.info(
                    "run started (mode=%s, since=%s, term=%d)",
                    report.mode,
                    report.since.isoformat(),
                    report.term,
                )
                self._execute(opts, report.term, report.since, report)
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

    def _resolve_term(self, opts: RunOptions, report: RunReport) -> int | None:
        """The term to discover in; None (with the reason in the report) when nothing is known."""
        if opts.term is not None:
            return opts.term
        try:
            return self._terms.current()
        except ServiceUnavailableError as exc:
            message = f"term: {exc.describe()}"
        except Exception as exc:
            message = f"term: {type(exc).__name__}: {exc}"
        log.error(message, extra={"in_report": True})
        report.errors.append(message)
        return None

    def _execute(self, opts: RunOptions, current: int, since: datetime, report: RunReport) -> None:
        stale = self._repo.mark_stale_pending_as_unknown(now=self._clock.now())
        if stale:
            msg = f"{stale} publication(s) were left pending by a previous run; marked unknown"
            log.error(msg, extra={"in_report": True})
            report.errors.append(msg)
        if self._runs_retention is not None:
            pruned = self._repo.prune_runs(before=self._clock.now() - self._runs_retention)
            if pruned:
                log.info("%d run record(s) older than %s removed", pruned, self._runs_retention)

        # New bills come from the current Sejm; the queues and the tracking are not scoped to a
        # term (every row carries its own).
        previous = [term for term in self._repo.known_terms() if term < current]
        if previous:
            # First thing in a new term, before discovery sees the old rows: the unfinished bills
            # of the older terms lapsed (announced once), the RCL projects follow the Sejm.
            # Nothing to do once done, so it runs every time.
            self._phase(
                report, "end of term", lambda: self._close_terms(previous, current, opts, report)
            )
        if opts.commands and self._commands is not None:
            # Before discovery: a card a command posts is followed by this run's tracking.
            self._phase(report, "commands", lambda: self._handle_commands(opts, report))
        if opts.discover:
            self._phase(report, "discovery", lambda: self._discover(current, since, report))
        if opts.discover and opts.rcl and self._rcl_discovery is not None:
            # Its own phase: an RCL outage must not cost the Sejm discovery.
            self._phase(report, "rcl discovery", lambda: self._discover_rcl(current, since, report))
        if self._text_prefilter is not None:
            self._phase(report, "text prefilter", lambda: self._prefilter_text(opts, report))
        self._phase(report, "analysis", lambda: self._analyse(opts, report))
        self._phase(report, "publishing", lambda: self._publish(opts, report))
        if opts.track:
            self._phase(report, "tracking", lambda: self._track(opts, report))

    @staticmethod
    def _phase(report: RunReport, name: str, action: Callable[[], None]) -> None:
        """Run one phase; an outage or a bug ends the phase, not the run.

        `in_report` keeps these log lines out of the captured warnings: they are errors already.
        """
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

    def _handle_commands(self, opts: RunOptions, report: RunReport) -> None:
        assert self._commands is not None
        handled = self._commands.handle_pending(
            min_score=opts.min_score,
            run_started_at=report.started_at,
            publish=opts.publish,
            dry_run=opts.dry_run,
        )
        report.commands_handled = handled.handled
        report.commands_failed = handled.failed
        report.commands = handled.lines
        report.llm_input_tokens += handled.input_tokens
        report.llm_output_tokens += handled.output_tokens
        _merge_usage(report, handled.usage)
        if handled.fatal_error:
            report.errors.append(f"commands: {handled.fatal_error}")

    def _discover(self, term: int, since: datetime, report: RunReport) -> None:
        discovered = self._discovery.discover(term, since, pre_print=self._pre_print)
        report.discovery_ok = True
        report.discovered = discovered.new
        report.pre_print_discovered = discovered.pre_print_new
        report.prefilter_hits = discovered.prefilter_hits

    def _discover_rcl(self, term: int, since: datetime, report: RunReport) -> None:
        assert self._rcl_discovery is not None
        discovered = self._rcl_discovery.discover(term, since)
        report.rcl_discovered = discovered.new
        report.rcl_prefilter_hits = discovered.prefilter_hits
        if discovered.failed:
            report.errors.append(f"{discovered.failed} RCL project(s) could not be read")

    def _prefilter_text(self, opts: RunOptions, report: RunReport) -> None:
        assert self._text_prefilter is not None
        checked = self._text_prefilter.run(limit=opts.max_text_prefilter)
        report.text_prefilter_checked = checked.checked
        report.text_prefilter_hits = checked.hits
        report.text_prefilter_unreadable = checked.unreadable
        if checked.fatal_error:
            report.errors.append(f"text prefilter: {checked.fatal_error}")

    def _analyse(self, opts: RunOptions, report: RunReport) -> None:
        analysed = self._analysis.analyze_pending(limit=opts.max_analyze)
        report.analyzed = analysed.analyzed
        report.triaged_out = analysed.triaged_out
        report.analysis_failures = analysed.failed
        report.analysis_skipped_cost = analysed.skipped_cost
        if analysed.stopped:
            report.notes.append(f"analysis: {analysed.stopped}")
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
            min_score=opts.min_score, limit=opts.max_publish, publish=opts.publish
        )
        report.published = published.published
        report.joint_published = published.joined
        if published.fatal_error:
            report.errors.append(f"publishing: {published.fatal_error}")
        elif published.failed:
            report.errors.append(f"{published.failed} publication(s) failed")

    def _close_terms(
        self, previous: Sequence[int], current: int, opts: RunOptions, report: RunReport
    ) -> None:
        failed = 0
        for term in previous:
            closed = self._tracking.close_term(term, current, publish=opts.publish)
            failed += self._merge_tracking(report, closed, opts)
            if closed.fatal_error is not None:
                report.errors.append(f"end of term: {closed.fatal_error}")
                return
        if failed:
            report.errors.append(f"{failed} end-of-term update(s) failed")

    def _track(self, opts: RunOptions, report: RunReport) -> None:
        # Daily: only bills the API listed as modified. Weekly (or when discovery did not run):
        # every followed bill, in case something moved without a visible change.
        weekly = (
            self._full_track_weekday is not None
            and self._clock.now().weekday() == self._full_track_weekday
        )
        full = opts.full_track or not opts.discover or not report.discovery_ok or weekly
        tracked = self._tracking.check_updates(
            publish=opts.publish, changed_since=None if full else report.since
        )
        failed = self._merge_tracking(report, tracked, opts)
        if tracked.fatal_error is not None:
            report.errors.append(f"tracking: {tracked.fatal_error}")
        elif failed:
            report.errors.append(f"{failed} status update(s) failed")

    @staticmethod
    def _merge_tracking(report: RunReport, tracked: TrackingResult, opts: RunOptions) -> int:
        """Add one tracking result to the report; returns its failed-post count."""
        report.tracked += tracked.checked
        report.updates += tracked.published if opts.publish else tracked.changed
        report.reanalyzed += tracked.reanalyzed
        report.linked += tracked.linked
        report.acts_published += tracked.acts_published
        report.in_force_posted += tracked.in_force_posted
        report.consultation_reminders += tracked.consultation_reminders
        report.hearing_reminders += tracked.hearing_reminders
        report.held += tracked.held
        report.consultation_results_posted += tracked.consultation_results_posted
        report.agenda_posted += tracked.agenda_posted
        report.discontinued += tracked.discontinued
        report.rehomed += tracked.rehomed
        report.llm_input_tokens += tracked.input_tokens
        report.llm_output_tokens += tracked.output_tokens
        _merge_usage(report, tracked.usage)
        report.errors.extend(f"tracking: {error}" for error in tracked.partial_errors)
        return tracked.failed

    def _notify(self, report: RunReport, captured: MemoryLogHandler) -> None:
        if self._notifier is None:
            return
        if report.mode is RunMode.COMMANDS and report.ok:
            return  # the replies under the commands said everything; no report for a kick
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
