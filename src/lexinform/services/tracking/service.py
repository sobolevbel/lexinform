"""The tracking phase: which bills to look at, what changed, and the resulting posts.

Every update is a reply to the original card and restates the current summary. When the bill's
text changed (committee report, text after the 3rd reading, updated print) the bill is
re-analysed first and the update also lists what changed.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from lexinform.concurrency import fan_out
from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    AmendmentsRecord,
    Bill,
    PrintInfo,
    ProcessDetail,
    PublicationKind,
    StatusChange,
    TextDocument,
    amendments_stage,
    diff_stages,
    has_news,
    stage_fingerprint,
)
from lexinform.ports import (
    BillRepository,
    Clock,
    EliGateway,
    Publisher,
    SejmGateway,
    WykazGateway,
)
from lexinform.services.analysis import AnalysisService
from lexinform.services.rcl_projects import RclProjectReader
from lexinform.services.sources import SejmTextSource, fetch_print
from lexinform.services.tracking.acts import ActWatcher
from lexinform.services.tracking.agenda import AgendaWatcher
from lexinform.services.tracking.cards import CardRefresher
from lexinform.services.tracking.consultations import ConsultationReminder
from lexinform.services.tracking.hearings import HearingReminder
from lexinform.services.tracking.linking import Linker
from lexinform.services.tracking.posting import Poster
from lexinform.services.tracking.pre_print import PrePrintReconciler
from lexinform.services.tracking.rcl import RclWatcher
from lexinform.services.tracking.result import TrackingResult
from lexinform.services.tracking.rollover import TermRollover
from lexinform.services.tracking.stages import StageEnricher, change_key
from lexinform.services.tracking.wykaz import WykazLinker, WykazWatcher

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Amendments:
    """An amendments document found among the new stages, with the committee's proposal."""

    document: TextDocument
    proposal: str | None


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
        closed_grace_days: int = 90,
        passed_max_days: int = 180,
        pending_decision_max_days: int = 1095,
        max_publish_attempts: int = 3,
        club_breakdown: bool = True,
        in_force_reminders: bool = True,
        consultation_reminder_days: int | None = 3,
        agenda_watch: bool = True,
        max_card_edits: int = 30,
        rcl_reader: RclProjectReader | None = None,
        wykaz: WykazGateway | None = None,
        local_tz: ZoneInfo = ZoneInfo("Europe/Warsaw"),
        text_prefilter: bool = True,
        workers: int = 1,
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._clock = clock
        self._channel_id = channel_id
        self._analysis = analysis
        self._closed_grace_days = closed_grace_days
        self._passed_max_days = passed_max_days
        self._pending_decision_max_days = pending_decision_max_days
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
        self._hearings = (
            HearingReminder(
                clock, self._poster, local_tz=local_tz, days_before=consultation_reminder_days
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
            text_prefilter=text_prefilter,
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
        self._wykaz = (
            WykazWatcher(
                wykaz,
                repo,
                clock,
                self._poster,
                WykazLinker(
                    rcl_reader,
                    repo,
                    clock,
                    self._poster,
                    channel_id=channel_id,
                    analysis=analysis,
                ),
            )
            if wykaz is not None and rcl_reader is not None
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
        self._cards = CardRefresher(
            gateway, repo, publisher, clock, channel_id=channel_id, max_edits=max_card_edits
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
        everyone = tracked if changed_since is None else self._list_tracked()
        # Agendas change without touching the process: every followed bill is checked, and before
        # the stage loop, so that an update posted below already carries the sitting dates.
        if self._agenda is not None and not self._agenda.check(everyone, result, publish=publish):
            return result
        # Before RCL: a plan whose project is out hands its card over, and the project is then
        # among the rows the RCL watcher refreshes in the same run.
        if self._wykaz is not None and not self._wykaz.check(tracked, result, publish=publish):
            return result
        if self._rcl is not None and not self._rcl.check(tracked, result, publish=publish):
            return result
        followed = [bill for bill in tracked if bill.has_process]
        for outcome in fan_out(followed, self._fetch, workers=self._workers):
            bill = outcome.item
            result.checked += 1
            try:
                detail, print_info, amendments = outcome.result()
                change = self._detect(bill, detail, print_info, result, amendments)
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
                # The act notice goes first, so that the closure is suppressed only when the
                # notice that would have told it really went out; ELI can be a day behind the
                # process, and the change row is unique, so a suppressed closure is never
                # detected again.
                self._acts.check(bill, detail, result, publish=publish)
                if change is not None and publish:
                    announced = self._poster.posted(bill, PublicationKind.ACT_PUBLISHED)
                    if has_news(change, act_published=announced):
                        result.count_post(self._poster.status_update(bill, change))
                    else:
                        self._poster.hold(bill, change)
                        result.held += 1
            except ServiceUnavailableError as exc:
                result.abort(exc, failed=True)
                break
        if result.fatal_error is None:
            self._cards.refresh(everyone, result, publish=publish)
        if result.fatal_error is None and publish:
            self._acts.remind_in_force(result)
        if result.fatal_error is None and publish and self._consultations is not None:
            self._consultations.remind(result)
        if result.fatal_error is None and publish and self._hearings is not None:
            self._hearings.remind(everyone, result)
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
            pending_decision_max_days=self._pending_decision_max_days,
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

    def _fetch(self, bill: Bill) -> tuple[ProcessDetail, PrintInfo | None, _Amendments | None]:
        """What detection needs from the API for one bill (no database access): the process,
        the print and, when a new stage brings amendments, their document."""
        detail = self._gateway.get_process(bill.term, bill.number)
        return detail, fetch_print(self._gateway, bill), self._amendments_document(bill, detail)

    def _amendments_document(self, bill: Bill, detail: ProcessDetail) -> _Amendments | None:
        """The Senate's resolution print or the committee's report on amendments, if one of
        the stages new since the stored tree carries amendments (network: the Senate print)."""
        if self._analysis is None or bill.analysis is None or bill.stages_fingerprint is None:
            return None
        stage = amendments_stage(diff_stages(bill.stages, detail.stages))
        if stage is None:
            return None
        if stage.stage_type == "SenatePosition":
            assert stage.print_number is not None
            try:
                senate_print = self._gateway.get_print(bill.term, stage.print_number)
            except ServiceUnavailableError:
                raise
            except Exception as exc:
                log.warning("Senate print %s unavailable: %s", stage.print_number, exc)
                return None
            pdf = senate_print.main_pdf
            if pdf is None:
                return None
            return _Amendments(TextDocument(url=pdf.url, kind="senate_amendments"), None)
        assert stage.report_file is not None
        document = TextDocument(url=stage.report_file, kind="committee_amendments")
        return _Amendments(document, stage.proposal)

    def _summarize_amendments(
        self, bill: Bill, found: _Amendments, result: TrackingResult
    ) -> AmendmentsRecord | None:
        """Best effort: a failure degrades the update to the bare event; outages propagate."""
        assert self._analysis is not None
        try:
            record = self._analysis.summarize_amendments(
                bill, found.document, proposal=found.proposal
            )
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.warning("druk %s: amendments not summarised: %s", bill.number, exc)
            return None
        if record is not None:
            result.count_usage(record)
            log.info("druk %s: amendments summarised from %s", bill.number, found.document.url)
        return record

    def _detect(
        self,
        bill: Bill,
        detail: ProcessDetail,
        print_info: PrintInfo | None,
        result: TrackingResult,
        amendments: _Amendments | None = None,
    ) -> StatusChange | None:
        new_fp = stage_fingerprint(detail.stages)
        change = self._detect_change(bill, detail, new_fp, print_info, result, amendments)
        if new_fp != bill.stages_fingerprint:
            # Written last: a failure above (an LLM outage during the re-analysis) leaves the
            # old fingerprint in place, so the next run sees the same new stages and tells them
            # instead of a bare "text changed".
            self._repo.save_stages(bill.term, bill.number, detail.stages, new_fp)
        return change

    def _detect_change(
        self,
        bill: Bill,
        detail: ProcessDetail,
        new_fp: str,
        print_info: PrintInfo | None,
        result: TrackingResult,
        amendments: _Amendments | None,
    ) -> StatusChange | None:
        now = self._clock.now()
        old_fp = bill.stages_fingerprint
        stages_changed = new_fp != old_fp
        self._repo.upsert_summary(detail, now=now)

        document = self._texts.newer(bill, detail, print_info) if self._analysis else None
        content_changed = False
        if document is not None and self._analysis is not None:
            log.info("druk %s: new text (%s), re-analysing", bill.number, document.kind)
            record = self._analysis.reanalyze_bill(bill, document, summary=detail)
            if record is not None:
                result.count_reanalysis(record)
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
        if amendments is not None:
            # After the row exists: a change recorded by an earlier run never pays for a second
            # model call, and the summary is stored with the change it belongs to.
            change.amendments = self._summarize_amendments(bill, amendments, result)
            if change.amendments is not None:
                self._repo.save_status_change_amendments(change_id, change.amendments)
        log.info(
            "druk %s: %d new stage(s)%s%s: %s",
            bill.number,
            len(change.new_stages),
            " + new text" if content_changed else "",
            " + amendments" if change.amendments is not None else "",
            "; ".join(s.stage_name for s in change.new_stages) or "-",
        )
        return change
