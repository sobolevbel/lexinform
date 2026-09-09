"""The tracking phase: which bills to look at, what changed, and the resulting posts.

Every update is a reply to the original card and restates the current summary. When the bill's
text changed (committee report, text after the 3rd reading, updated print) the bill is
re-analysed first and the update also lists what changed.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from lexinform.concurrency import fan_out
from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    Bill,
    PrintInfo,
    ProcessDetail,
    StatusChange,
    diff_stages,
    stage_fingerprint,
)
from lexinform.ports import BillRepository, Clock, EliGateway, Publisher, SejmGateway
from lexinform.services.analysis import AnalysisService
from lexinform.services.rcl_projects import RclProjectReader
from lexinform.services.sources import SejmTextSource, fetch_print
from lexinform.services.tracking.acts import ActWatcher
from lexinform.services.tracking.agenda import AgendaWatcher
from lexinform.services.tracking.consultations import ConsultationReminder
from lexinform.services.tracking.linking import Linker
from lexinform.services.tracking.posting import Poster
from lexinform.services.tracking.pre_print import PrePrintReconciler
from lexinform.services.tracking.rcl import RclWatcher
from lexinform.services.tracking.result import TrackingResult
from lexinform.services.tracking.rollover import TermRollover
from lexinform.services.tracking.stages import StageEnricher, change_key

log = logging.getLogger(__name__)


class StatusTrackingService:
    """Follows published bills: stage changes, new texts, sittings, acts, consultations.

    The work is split by concern into the sibling modules; this class owns the loop and the
    stage/text detection. Network calls for many bills run in parallel, every decision and
    database write happens here, in order.
    """

    def __init__(
        self,
        gateway: SejmGateway,
        repo: BillRepository,
        publisher: Publisher,
        clock: Clock,
        *,
        channel_id: str,
        analysis: AnalysisService | None = None,
        eli: EliGateway | None = None,
        closed_grace_days: int = 30,
        passed_max_days: int = 180,
        max_publish_attempts: int = 3,
        club_breakdown: bool = True,
        in_force_reminders: bool = True,
        consultation_reminder_days: int | None = 3,
        agenda_watch: bool = True,
        rcl_reader: RclProjectReader | None = None,
        local_tz: ZoneInfo = ZoneInfo("Europe/Warsaw"),
        workers: int = 1,
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._clock = clock
        self._channel_id = channel_id
        self._analysis = analysis
        self._closed_grace_days = closed_grace_days
        self._passed_max_days = passed_max_days
        self._max_publish_attempts = max_publish_attempts
        self._workers = workers
        self._poster = Poster(
            repo, publisher, clock, channel_id=channel_id, max_attempts=max_publish_attempts
        )
        self._enricher = StageEnricher(gateway, club_breakdown=club_breakdown)
        self._texts = SejmTextSource(gateway)
        self._consultations = (
            ConsultationReminder(
                repo,
                clock,
                self._poster,
                channel_id=channel_id,
                local_tz=local_tz,
                days_before=consultation_reminder_days,
            )
            if consultation_reminder_days is not None
            else None
        )
        linker = Linker(
            gateway,
            repo,
            clock,
            self._poster,
            self._enricher,
            channel_id=channel_id,
            analysis=analysis,
        )
        self._pre_print = PrePrintReconciler(
            gateway,
            repo,
            clock,
            self._poster,
            linker,
            channel_id=channel_id,
            consultations=self._consultations,
        )
        self._rcl = (
            RclWatcher(
                rcl_reader,
                repo,
                clock,
                self._poster,
                linker,
                analysis=analysis,
                consultations=self._consultations,
                workers=workers,
            )
            if rcl_reader is not None
            else None
        )
        self._agenda = (
            AgendaWatcher(
                gateway,
                repo,
                clock,
                self._poster,
                self._enricher,
                channel_id=channel_id,
                local_tz=local_tz,
            )
            if agenda_watch
            else None
        )
        self._acts = ActWatcher(
            eli,
            repo,
            clock,
            self._poster,
            channel_id=channel_id,
            local_tz=local_tz,
            in_force_reminders=in_force_reminders,
        )
        self._rollover = TermRollover(repo, clock, self._poster, channel_id=channel_id)

    def close_term(self, previous: int, current: int, *, publish: bool = True) -> TrackingResult:
        """The Sejm moved on to `current`: announce the lapsed bills of `previous` and carry its
        RCL projects over (see `TermRollover`). Cheap and idempotent once done."""
        result = TrackingResult()
        self._rollover.close_term(previous, current, result, publish=publish)
        return result

    def check_updates(
        self, *, publish: bool = True, changed_since: datetime | None = None
    ) -> TrackingResult:
        """Look for news on published bills, whichever term they belong to.

        `changed_since` limits the check to bills the Sejm API reported as modified since then
        (discovery refreshes their `change_date`); None checks every followed bill.
        """
        result = TrackingResult()
        if publish and not self._retry_failed(result):
            return result
        if not self._pre_print.reconcile(result, publish=publish):
            return result
        tracked = self._list_tracked(changed_since)
        # Agendas change without touching the process: every followed bill is checked, and before
        # the stage loop, so that an update posted below already carries the sitting dates.
        if self._agenda is not None:
            everyone = tracked if changed_since is None else self._list_tracked()
            if not self._agenda.check(everyone, result, publish=publish):
                return result
        if self._rcl is not None and not self._rcl.check(tracked, result, publish=publish):
            return result
        followed = [bill for bill in tracked if bill.has_process]
        for outcome in fan_out(followed, self._fetch, workers=self._workers):
            bill = outcome.item
            result.checked += 1
            try:
                detail, print_info = outcome.result()
                change = self._detect(bill, detail, print_info, result)
            except ServiceUnavailableError as exc:
                result.abort(exc)
                break
            except Exception as exc:
                result.failed += 1
                log.exception("tracking druk %s failed: %s", bill.number, exc)
                continue
            if change is not None:
                result.changed += 1
            try:
                if change is not None and publish:
                    result.count_post(self._poster.status_update(bill, change))
                self._acts.check(bill, detail, result, publish=publish)
            except ServiceUnavailableError as exc:
                result.abort(exc, failed=True)
                break
        if result.fatal_error is None and publish:
            self._acts.remind_in_force(result)
        if result.fatal_error is None and publish and self._consultations is not None:
            self._consultations.remind(result)
        scope = "all" if changed_since is None else f"changed since {changed_since:%F %R}"
        log.info(
            "tracking (%s): checked=%d changed=%d reanalyzed=%d published=%d agenda=%d failed=%d",
            scope,
            result.checked,
            result.changed,
            result.reanalyzed,
            result.published,
            result.agenda_posted,
            result.failed,
        )
        return result

    def _list_tracked(self, changed_since: datetime | None = None) -> list[Bill]:
        return self._repo.list_tracked(
            self._channel_id,
            closed_grace_days=self._closed_grace_days,
            passed_max_days=self._passed_max_days,
            now=self._clock.now(),
            changed_since=changed_since,
        )

    def _retry_failed(self, result: TrackingResult) -> bool:
        """Re-send status updates whose post failed earlier. False if Telegram is down."""
        for change in self._repo.list_failed_status_changes(
            self._channel_id, max_attempts=self._max_publish_attempts
        ):
            bill = self._repo.get(change.term, change.number)
            if bill is None:
                continue
            log.info("retrying status update for druk %s", bill.number)
            try:
                result.count_post(self._poster.status_update(bill, change))
            except ServiceUnavailableError as exc:
                result.abort(exc, failed=True)
                return False
        return True

    # ------------------------------------------------------------------ detection

    def _fetch(self, bill: Bill) -> tuple[ProcessDetail, PrintInfo | None]:
        """What detection needs from the API for one bill (no database access)."""
        return self._gateway.get_process(bill.term, bill.number), fetch_print(self._gateway, bill)

    def _detect(
        self,
        bill: Bill,
        detail: ProcessDetail,
        print_info: PrintInfo | None,
        result: TrackingResult,
    ) -> StatusChange | None:
        now = self._clock.now()

        new_fp = stage_fingerprint(detail.stages)
        old_fp = bill.stages_fingerprint
        stages_changed = new_fp != old_fp
        if stages_changed:
            self._repo.save_stages(bill.term, bill.number, detail.stages, new_fp)
        self._repo.upsert_summary(detail, now=now)

        document = self._texts.newer(bill, detail, print_info) if self._analysis else None
        content_changed = False
        if document is not None and self._analysis is not None:
            log.info("druk %s: new text (%s), re-analysing", bill.number, document.kind)
            result.count_reanalysis(self._analysis.reanalyze_bill(bill, document, summary=detail))
            content_changed = True

        if old_fp is None:
            return None  # first sight of the stages: seed silently
        # Discovery refreshes the stored summary before tracking runs, so closure is detected
        # against what was announced, not against the stored closure date.
        closure_detected = detail.closure_date is not None and not self._repo.closure_announced(
            bill.term, bill.number
        )
        new_stages = diff_stages(bill.stages, detail.stages) if stages_changed else []
        if not (new_stages or content_changed or closure_detected):
            return None  # a stage was edited or removed upstream: nothing to tell
        new_stages = [self._enricher.enrich(bill.term, st) for st in new_stages]

        fresh = self._repo.get(bill.term, bill.number) or bill
        change = StatusChange(
            term=bill.term,
            number=bill.number,
            old_fingerprint=old_fp,
            new_fingerprint=change_key(new_fp, fresh, closed=closure_detected),
            new_stages=new_stages,
            closure_detected=closure_detected,
            passed=detail.passed,
            content_changed=content_changed,
            detected_at=now,
        )
        change_id = self._repo.add_status_change(change)
        if change_id is None:
            return None  # already recorded by an earlier run
        change.id = change_id
        log.info(
            "druk %s: %d new stage(s)%s: %s",
            bill.number,
            len(change.new_stages),
            " + new text" if content_changed else "",
            "; ".join(s.stage_name for s in change.new_stages) or "-",
        )
        return change
