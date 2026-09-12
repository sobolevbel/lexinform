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
    new_supplements,
    stage_fingerprint,
    supplement_kind,
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
from lexinform.services.tracking.deadlines import DeadlineReminder
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
class TrackingOptions:
    """The knobs of a tracking run, as `Settings` sets them.

    A reminder is switched off by giving it no days (`consultation_reminder_days`,
    `decision_reminder_days` = None), the agenda watcher by `agenda_watch`.
    """

    channel_id: str
    closed_grace_days: int = 90
    passed_max_days: int = 180
    pending_decision_max_days: int = 1095
    max_publish_attempts: int = 3
    club_breakdown: bool = True
    in_force_reminders: bool = True
    consultation_reminder_days: int | None = 3
    decision_reminder_days: int | None = 7
    agenda_watch: bool = True
    max_card_edits: int = 30
    local_tz: ZoneInfo = ZoneInfo("Europe/Warsaw")
    text_prefilter: bool = True
    workers: int = 1


def _consultation_reminder(
    repo: BillRepository, clock: Clock, poster: Poster, options: TrackingOptions
) -> ConsultationReminder | None:
    if options.consultation_reminder_days is None:
        return None
    return ConsultationReminder(
        repo,
        clock,
        poster,
        channel_id=options.channel_id,
        local_tz=options.local_tz,
        days_before=options.consultation_reminder_days,
    )


def _hearing_reminder(
    clock: Clock, poster: Poster, options: TrackingOptions
) -> HearingReminder | None:
    if options.consultation_reminder_days is None:
        return None
    return HearingReminder(
        clock,
        poster,
        local_tz=options.local_tz,
        days_before=options.consultation_reminder_days,
    )


def _deadline_reminder(
    repo: BillRepository, clock: Clock, poster: Poster, options: TrackingOptions
) -> DeadlineReminder | None:
    if options.decision_reminder_days is None:
        return None
    return DeadlineReminder(
        repo,
        clock,
        poster,
        local_tz=options.local_tz,
        days_before=options.decision_reminder_days,
    )


def _rcl_watcher(
    reader: RclProjectReader | None,
    repo: BillRepository,
    clock: Clock,
    poster: Poster,
    linker: Linker,
    gateway: SejmGateway,
    analysis: AnalysisService | None,
    consultations: ConsultationReminder | None,
    options: TrackingOptions,
) -> RclWatcher | None:
    if reader is None:
        return None
    return RclWatcher(
        reader,
        repo,
        clock,
        poster,
        linker,
        gateway,
        analysis=analysis,
        consultations=consultations,
        workers=options.workers,
    )


def _wykaz_watcher(
    wykaz: WykazGateway | None,
    reader: RclProjectReader | None,
    repo: BillRepository,
    clock: Clock,
    poster: Poster,
    analysis: AnalysisService | None,
    options: TrackingOptions,
) -> WykazWatcher | None:
    """A plan is followed only when its project can be read: the hand-over to RCL is the whole
    point of watching the register."""
    if wykaz is None or reader is None:
        return None
    linker = WykazLinker(
        reader, repo, clock, poster, channel_id=options.channel_id, analysis=analysis
    )
    return WykazWatcher(wykaz, repo, clock, poster, linker)


def _agenda_watcher(
    gateway: SejmGateway,
    repo: BillRepository,
    clock: Clock,
    poster: Poster,
    enricher: StageEnricher,
    options: TrackingOptions,
) -> AgendaWatcher | None:
    if not options.agenda_watch:
        return None
    return AgendaWatcher(
        gateway,
        repo,
        clock,
        poster,
        enricher,
        channel_id=options.channel_id,
        local_tz=options.local_tz,
    )


@dataclass(frozen=True)
class _Amendments:
    """An amendments document found among the new stages, with the committee's proposal."""

    document: TextDocument
    proposal: str | None


@dataclass(frozen=True)
class _Supplement:
    """A document filed to the print since the last check and worth telling: its own print
    number and title, and the file to read it from."""

    number: str
    title: str
    document: TextDocument


@dataclass(frozen=True)
class _Found:
    """What one bill's network step brought back, for the detection that follows it."""

    detail: ProcessDetail
    print_info: PrintInfo | None
    amendments: _Amendments | None
    supplements: list[_Supplement]


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
        options: TrackingOptions,
        *,
        analysis: AnalysisService | None = None,
        eli: EliGateway | None = None,
        rcl_reader: RclProjectReader | None = None,
        wykaz: WykazGateway | None = None,
    ) -> None:
        channel_id = options.channel_id
        poster = Poster(
            repo, publisher, clock, channel_id=channel_id, max_attempts=options.max_publish_attempts
        )
        enricher = StageEnricher(gateway, club_breakdown=options.club_breakdown)
        consultations = _consultation_reminder(repo, clock, poster, options)
        linker = Linker(
            gateway,
            repo,
            clock,
            poster,
            enricher,
            channel_id=channel_id,
            analysis=analysis,
            text_prefilter=options.text_prefilter,
        )
        self._gateway = gateway
        self._repo = repo
        self._clock = clock
        self._analysis = analysis
        self._options = options
        self._poster = poster
        self._enricher = enricher
        self._texts = SejmTextSource(gateway)
        self._consultations = consultations
        self._hearings = _hearing_reminder(clock, poster, options)
        self._deadlines = _deadline_reminder(repo, clock, poster, options)
        self._pre_print = PrePrintReconciler(
            gateway,
            repo,
            clock,
            poster,
            linker,
            channel_id=channel_id,
            consultations=consultations,
        )
        self._rcl = _rcl_watcher(
            rcl_reader, repo, clock, poster, linker, gateway, analysis, consultations, options
        )
        self._wykaz = _wykaz_watcher(wykaz, rcl_reader, repo, clock, poster, analysis, options)
        self._agenda = _agenda_watcher(gateway, repo, clock, poster, enricher, options)
        self._acts = ActWatcher(
            eli,
            repo,
            clock,
            poster,
            channel_id=channel_id,
            local_tz=options.local_tz,
            in_force_reminders=options.in_force_reminders,
        )
        self._cards = CardRefresher(
            gateway,
            repo,
            publisher,
            clock,
            channel_id=channel_id,
            local_tz=options.local_tz,
            max_edits=options.max_card_edits,
        )
        self._rollover = TermRollover(repo, clock, poster, channel_id=channel_id)

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
        (discovery refreshes their `change_date`); None checks every followed bill. The reminders
        of `_remind` and the card refresh see every followed bill either way.
        """
        result = TrackingResult()
        if publish and not self._retry_failed(result):
            return result
        if not self._pre_print.reconcile(result, publish=publish):
            return result
        tracked = self._list_tracked(changed_since)
        everyone = tracked if changed_since is None else self._list_tracked()
        if not self._check_other_sources(tracked, everyone, result, publish=publish):
            return result
        self._check_processes(tracked, result, publish=publish)
        self._remind_and_refresh(everyone, result, publish=publish)
        self._log_outcome(result, changed_since)
        return result

    def _check_other_sources(
        self, tracked: list[Bill], everyone: list[Bill], result: TrackingResult, *, publish: bool
    ) -> bool:
        """The watchers that do not read a Sejm process; False when one of them had to stop.

        Agendas change without the process being touched, so every followed bill is checked, and
        before the stage loop, so that an update posted there already carries the sitting dates.
        The wykaz comes before RCL: a plan whose project is out hands its card over, and the
        project is then among the rows the RCL watcher refreshes in the same run.
        """
        if self._agenda is not None and not self._agenda.check(everyone, result, publish=publish):
            return False
        if self._wykaz is not None and not self._wykaz.check(tracked, result, publish=publish):
            return False
        return self._rcl is None or self._rcl.check(tracked, result, publish=publish)

    def _check_processes(
        self, tracked: list[Bill], result: TrackingResult, *, publish: bool
    ) -> None:
        """Read the process of every followed Sejm bill and tell what is new about it."""
        followed = [bill for bill in tracked if bill.has_process]
        for outcome in fan_out(followed, self._fetch, workers=self._options.workers):
            bill = outcome.item
            result.checked += 1
            try:
                found = outcome.result()
                detail = found.detail
                change = self._detect(bill, found, result)
            except ServiceUnavailableError as exc:
                result.abort(exc)
                return
            except Exception as exc:
                result.failed += 1
                log.exception("tracking druk %s failed: %s", bill.number, exc)
                continue
            if change is not None:
                result.changed += 1
            try:
                self._post_news(bill, detail, change, result, publish=publish)
            except ServiceUnavailableError as exc:
                result.abort(exc, failed=True)
                return
            except Exception as exc:
                result.failed += 1
                log.exception("posting for druk %s failed: %s", bill.number, exc)

    def _post_news(
        self,
        bill: Bill,
        detail: ProcessDetail,
        change: StatusChange | None,
        result: TrackingResult,
        *,
        publish: bool,
    ) -> None:
        """Tell what the run found about one bill.

        The act notice goes first, so that the closure is suppressed only when the notice that
        would have told it really went out; ELI can be a day behind the process, and the change
        row is unique, so a suppressed closure is never detected again. A change with no news is
        held rather than dropped for the same reason: with publishing off the row already exists,
        so nothing would ever detect those stages again.
        """
        self._acts.check(bill, detail, result, publish=publish)
        if change is None:
            return
        announced = self._poster.sent(bill, PublicationKind.ACT_PUBLISHED)
        if publish and has_news(change, act_published=announced):
            result.count_post(self._poster.status_update(bill, change))
        else:
            self._poster.hold(bill, change)
            result.held += 1

    def _remind_and_refresh(
        self, everyone: list[Bill], result: TrackingResult, *, publish: bool
    ) -> None:
        if publish:
            self._remind(everyone, result)
        if result.fatal_error is None:
            self._cards.refresh(everyone, result, publish=publish)

    def _remind(self, everyone: list[Bill], result: TrackingResult) -> None:
        """The posts with a deadline behind them — three days for a consultation, the day itself
        for an act entering into force — and the next run is twelve hours away, twenty-four at a
        weekend. They come before the card refresh, and the refresh cannot stop them: re-rendering
        a card is worth none of that."""
        if result.fatal_error is None:
            self._acts.remind_in_force(result)
        if result.fatal_error is None and self._consultations is not None:
            self._consultations.remind(result)
        if result.fatal_error is None and self._hearings is not None:
            self._hearings.remind(everyone, result)
        if result.fatal_error is None and self._deadlines is not None:
            self._deadlines.remind(everyone, result)

    def _log_outcome(self, result: TrackingResult, changed_since: datetime | None) -> None:
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

    def _list_tracked(self, changed_since: datetime | None = None) -> list[Bill]:
        options = self._options
        return self._repo.list_tracked(
            options.channel_id,
            closed_grace_days=options.closed_grace_days,
            passed_max_days=options.passed_max_days,
            pending_decision_max_days=options.pending_decision_max_days,
            now=self._clock.now(),
            changed_since=changed_since,
        )

    def _retry_failed(self, result: TrackingResult) -> bool:
        """Re-send status updates whose post failed earlier. False if Telegram is down."""
        for change in self._repo.list_failed_status_changes(
            self._options.channel_id, max_attempts=self._options.max_publish_attempts
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

    def _fetch(self, bill: Bill) -> _Found:
        """What detection needs from the API for one bill (no database access): the process,
        the print, whatever document a new stage brings amendments in, and the documents filed
        to the print since the last check."""
        detail = self._gateway.get_process(bill.term, bill.number)
        print_info = fetch_print(self._gateway, bill)
        return _Found(
            detail,
            print_info,
            self._amendments_document(bill, detail),
            self._supplements(bill, print_info),
        )

    def _supplements(self, bill: Bill, print_info: PrintInfo | None) -> list[_Supplement]:
        """The documents filed to the print that this run should tell (no network: the print
        carries them). None of them until the bill's own list has been recorded once — the first
        sight seeds it silently, the way the stage fingerprint is seeded."""
        if self._analysis is None or bill.analysis is None or bill.seen_supplements is None:
            return []
        found = []
        for filed in new_supplements(print_info, bill.seen_supplements):
            kind = supplement_kind(filed.title)
            pdf = filed.main_pdf
            if kind is None or pdf is None:
                continue
            found.append(
                _Supplement(filed.number, filed.title, TextDocument(url=pdf.url, kind=kind))
            )
        return found

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

    def _detect(self, bill: Bill, found: _Found, result: TrackingResult) -> StatusChange | None:
        """What is new about the bill, with the fingerprint written last: a failure above (an LLM
        outage during the re-analysis) leaves the old one in place, so the next run sees the same
        new stages and tells them instead of a bare "text changed"."""
        detail = found.detail
        new_fp = stage_fingerprint(detail.stages)
        change = self._detect_change(bill, found, new_fp, result)
        if new_fp != bill.stages_fingerprint:
            self._repo.save_stages(bill.term, bill.number, detail.stages, new_fp)
        self._remember_supplements(bill, found.print_info)
        return change

    def _remember_supplements(self, bill: Bill, print_info: PrintInfo | None) -> None:
        """Written last, and only when the print was actually read: a run that could not fetch
        it must not forget the documents it knew about, and a run that told them must not tell
        them twice."""
        if print_info is None:
            return
        filed = tuple(p.number for p in print_info.additional_prints)
        if filed != bill.seen_supplements:
            self._repo.save_seen_supplements(bill.term, bill.number, filed)

    def _detect_change(
        self, bill: Bill, found: _Found, new_fp: str, result: TrackingResult
    ) -> StatusChange | None:
        """The one change worth a post, or None when there is nothing to tell.

        A first sight of the stages seeds them silently: there is no "before" to compare with.
        Closure is detected against what was announced rather than against the stored closure
        date, because discovery refreshes the stored summary before tracking runs. New stages
        that are gone again (a stage edited or removed upstream) leave nothing to tell either,
        and neither does a change an earlier run already recorded.
        """
        detail = found.detail
        now = self._clock.now()
        self._repo.upsert_summary(detail, now=now)
        content_changed = self._reanalyze_new_text(bill, detail, found.print_info, result)
        old_fp = bill.stages_fingerprint
        if old_fp is None:
            return None
        closure_detected = detail.closure_date is not None and not self._repo.closure_announced(
            bill.term, bill.number
        )
        new_stages = diff_stages(bill.stages, detail.stages) if new_fp != old_fp else []
        filed = found.supplements
        if not (new_stages or content_changed or closure_detected or filed):
            return None

        fresh = self._repo.get(bill.term, bill.number) or bill
        change = StatusChange(
            term=bill.term,
            number=bill.number,
            old_fingerprint=old_fp,
            new_fingerprint=change_key(
                new_fp,
                fresh,
                closed=closure_detected,
                supplements=[s.number for s in filed],
            ),
            new_stages=[self._enricher.enrich(bill.term, st) for st in new_stages],
            closure_detected=closure_detected,
            passed=detail.passed,
            content_changed=content_changed,
            detected_at=now,
        )
        change_id = self._repo.add_status_change(change)
        if change_id is None:
            return None
        change.id = change_id
        if found.amendments is not None:
            self._attach_amendments(change, bill, found.amendments, result)
        if filed:
            self._attach_supplements(change, bill, filed, result)
        _log_change(bill, change)
        return change

    def _reanalyze_new_text(
        self,
        bill: Bill,
        detail: ProcessDetail,
        print_info: PrintInfo | None,
        result: TrackingResult,
    ) -> bool:
        """True when the source published a text we had not read and the model read it now."""
        if self._analysis is None:
            return False
        document = self._texts.newer(bill, detail, print_info)
        if document is None:
            return False
        log.info("druk %s: new text (%s), re-analysing", bill.number, document.kind)
        record = self._analysis.reanalyze_bill(bill, document, summary=detail)
        if record is None:
            return False
        result.count_reanalysis(record)
        return True

    def _attach_supplements(
        self,
        change: StatusChange,
        bill: Bill,
        filed: list[_Supplement],
        result: TrackingResult,
    ) -> None:
        """Read after the change row exists, for the same reason the amendments are: a change an
        earlier run recorded never pays for the model again. A document the model could not be
        asked about is still announced, so nothing filed is lost."""
        assert change.id is not None
        assert self._analysis is not None
        for supplement in filed:
            try:
                record = self._analysis.digest_supplement(
                    bill, supplement.document, number=supplement.number, title=supplement.title
                )
            except ServiceUnavailableError:
                raise
            except Exception as exc:
                log.warning("druk %s: %s not digested: %s", bill.number, supplement.number, exc)
                record = self._analysis.bare_supplement(
                    supplement.document, number=supplement.number, title=supplement.title
                )
            if record.digest is not None:
                result.count_usage(record)
            change.supplements.append(record)
        self._repo.save_status_change_supplements(change.id, change.supplements)

    def _attach_amendments(
        self, change: StatusChange, bill: Bill, found: _Amendments, result: TrackingResult
    ) -> None:
        """Summarised once the change row exists: a change an earlier run recorded never pays for
        a second model call, and the summary is stored with the change it belongs to."""
        assert change.id is not None
        change.amendments = self._summarize_amendments(bill, found, result)
        if change.amendments is not None:
            self._repo.save_status_change_amendments(change.id, change.amendments)


def _log_change(bill: Bill, change: StatusChange) -> None:
    log.info(
        "druk %s: %d new stage(s)%s%s%s: %s",
        bill.number,
        len(change.new_stages),
        " + new text" if change.content_changed else "",
        " + amendments" if change.amendments is not None else "",
        f" + {len(change.supplements)} filed document(s)" if change.supplements else "",
        "; ".join(st.stage_name for st in change.new_stages) or "-",
    )
