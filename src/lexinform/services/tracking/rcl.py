"""Government projects followed on RCL: stage progress, consultation news, new text versions,
the hand-over to the Sejm and, once the druk exists, the link that continues the thread.

An RCL outage stops only this part of the tracking phase: the Sejm side of the run goes on.
"""

import logging
from zoneinfo import ZoneInfo

from lexinform.concurrency import fan_out
from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    Bill,
    BillStatus,
    PublicationKind,
    RclProject,
    Stage,
    StatusChange,
    consultation_open,
    diff_stages,
    observe,
    process_summary,
    rcl_fingerprint,
    rcl_stages,
)
from lexinform.ports import BillRepository, Clock, SejmGateway
from lexinform.services.analysis import AnalysisService
from lexinform.services.rcl_projects import RclProjectReader
from lexinform.services.sources import RclTextSource, print_of_project
from lexinform.services.tracking.consultations import ConsultationReminder
from lexinform.services.tracking.linking import Linker
from lexinform.services.tracking.posting import Poster
from lexinform.services.tracking.result import TrackingResult
from lexinform.services.tracking.stages import change_key

log = logging.getLogger(__name__)


class RclWatcher:
    def __init__(
        self,
        reader: RclProjectReader,
        repo: BillRepository,
        clock: Clock,
        poster: Poster,
        linker: Linker,
        gateway: SejmGateway,
        *,
        analysis: AnalysisService | None,
        max_print_lookups: int = 3,
        consultations: ConsultationReminder | None,
        local_tz: ZoneInfo = ZoneInfo("Europe/Warsaw"),
        workers: int = 1,
    ) -> None:
        self._reader = reader
        self._repo = repo
        self._clock = clock
        self._poster = poster
        self._linker = linker
        self._gateway = gateway
        self._analysis = analysis
        self._max_print_lookups = max_print_lookups
        self._consultations = consultations
        self._local_tz = local_tz
        self._texts = RclTextSource()
        self._workers = workers

    def check(self, bills: list[Bill], result: TrackingResult, *, publish: bool) -> bool:
        """Refresh the followed RCL projects among `bills` and post what changed; link the ones
        whose druk appeared. False when Telegram is down (RCL being down is only reported)."""
        followed = [b for b in bills if b.rcl is not None]
        try:
            followed = self._find_prints(followed)
        except ServiceUnavailableError as exc:
            result.partial_errors.append(f"Sejm: {exc.describe()}")
        if not self._link_pending(result, publish=publish):
            return False
        followed = [
            bill
            for bill in followed
            if (current := self._repo.get(bill.term, bill.number)) is not None
            and current.status is not BillStatus.LINKED
        ]
        for outcome in fan_out(followed, self._refresh, workers=self._workers):
            bill = outcome.item
            result.checked += 1
            try:
                project = outcome.result()
                results_due = self._results_due(bill, project)
                change = self._detect(bill, project, result)
            except ServiceUnavailableError as exc:
                result.partial_errors.append(f"RCL: {exc.describe()}")
                log.error("RCL tracking stopped: %s", exc.describe())
                break
            except Exception as exc:
                result.failed += 1
                log.exception("tracking %s failed: %s", bill.number, exc)
                continue
            fresh = self._repo.get(bill.term, bill.number) or bill
            try:
                if change is not None:
                    result.changed += 1
                    self._poster.tell(fresh, change, result, publish=publish)
                if results_due and self._consultations is not None:
                    self._consultations.results_published(fresh, result, publish=publish)
            except ServiceUnavailableError as exc:
                result.abort(exc, failed=True)
                return False
        return True

    def _find_prints(self, bills: list[Bill]) -> list[Bill]:
        """Ask the Sejm for the druk of a project that has been handed over and has none stored.

        Discovery stamps the print number on the run in which it first sees a druk whose
        `rclNum` names a followed project — once, and only then. A process without `rclNum`, or
        an outage in that one run, would leave the thread frozen at "направлен в Сейм" for good,
        so the hand-over is asked about again on every run, for a few projects at a time."""
        pending = [
            b for b in bills if b.rcl is not None and b.rcl.sent_to_sejm and not b.rcl.print_number
        ][: self._max_print_lookups]
        found: dict[str, Bill] = {}
        for bill in pending:
            assert bill.rcl is not None
            number = print_of_project(self._gateway, bill.rcl, bill.term)
            if number is None:
                continue
            project = bill.rcl.model_copy(update={"print_number": number})
            self._repo.save_rcl(bill.term, bill.number, project)
            found[bill.number] = bill.model_copy(update={"rcl": project})
            log.info("%s went to the Sejm as druk %s", bill.number, number)
        return [found.get(b.number, b) for b in bills]

    def _link_pending(self, result: TrackingResult, *, publish: bool) -> bool:
        """Projects whose druk the Sejm discovery has seen: the print takes over the thread."""
        for bill in self._repo.list_rcl_awaiting_link():
            assert bill.rcl is not None and bill.rcl.print_number is not None
            try:
                self._linker.link(bill, bill.rcl.print_number, result, publish=publish)
            except ServiceUnavailableError as exc:
                result.abort(exc)
                return False
            except Exception as exc:
                result.failed += 1
                log.exception("linking %s to its druk failed: %s", bill.number, exc)
        return True

    def _refresh(self, bill: Bill) -> RclProject:
        """Network only: the project as RCL shows it now."""
        assert bill.rcl is not None
        return self._reader.refresh(bill.rcl)

    def _detect(
        self, bill: Bill, project: RclProject, result: TrackingResult
    ) -> StatusChange | None:
        """Source observations advance only with their delivery work after network preparation."""
        fresh = bill
        content_changed = False
        document = self._texts.locate(bill.model_copy(update={"rcl": project})).document
        if (
            self._analysis is not None
            and bill.analysis is not None
            and document is not None
            and document.url != bill.analysis.source_url
        ):
            fresh, content_changed = self._analysis.prepare_reanalysis(bill, document)
            if content_changed and fresh.analysis is not None:
                result.count_reanalysis(fresh.analysis)
        new_fp = rcl_fingerprint(project)
        stages = rcl_stages(project)
        with self._repo.atomic():
            if fresh.analysis is not None:
                self._repo.save_analysis(bill.term, bill.number, fresh.analysis)
            self._repo.save_rcl(bill.term, bill.number, project)
            self._repo.upsert_summary(
                process_summary(project, term=bill.term), now=self._clock.now()
            )
            self._repo.save_stages(bill.term, bill.number, stages, new_fp)
            change = self._detect_change(bill, project, stages, new_fp, content_changed)
            fresh = self._repo.get(bill.term, bill.number) or bill
            if change is not None:
                self._poster.prepare(fresh, change)
            if self._results_due(bill, project) and self._consultations is not None:
                self._consultations.prepare_results(fresh)
            self._repo.save_observed_process(
                bill.term, bill.number, observe(fresh, closure_date=fresh.observed_closure_date)
            )
        return change

    def _detect_change(
        self,
        bill: Bill,
        project: RclProject,
        stages: tuple[Stage, ...],
        new_fp: str,
        content_changed: bool,
    ) -> StatusChange | None:
        now = self._clock.now()
        stored = bill.rcl
        assert stored is not None
        closure = (
            not project.is_open
            and not project.sent_to_sejm
            and not self._repo.closure_announced(bill.term, bill.number)
        )
        new_stages = diff_stages(bill.stages, stages)
        # Reading the letter late is not an event. A project taken by its text arrives with no
        # window at all (`RclDiscoveryService.read_consultations` fills it in), and a window
        # that shut in June is nothing a reader can act on: «Открылись публичные консультации»
        # over it invites them to a door that is closed.
        consultation_opened = (
            stored.consultation is None
            and project.consultation is not None
            and consultation_open(
                bill.model_copy(update={"rcl": project}),
                now.astimezone(self._local_tz).date(),
            )
        )
        if not (new_stages or content_changed or closure or consultation_opened):
            return None
        fresh = self._repo.get(bill.term, bill.number) or bill
        change = self._poster.record_change(
            StatusChange(
                term=bill.term,
                number=bill.number,
                old_fingerprint=bill.stages_fingerprint,
                new_fingerprint=change_key(new_fp, fresh, closed=closure),
                new_stages=new_stages,
                closure_detected=closure,
                passed=False if closure else None,
                content_changed=content_changed,
                consultation_opened=consultation_opened,
                detected_at=now,
            )
        )
        if change is None:
            return None
        log.info(
            "%s: %s%s%s",
            bill.number,
            "; ".join(st.stage_name for st in new_stages) or "no new stage",
            " + new text" if content_changed else "",
            " + consultation opened" if consultation_opened else "",
        )
        return change

    def _results_due(self, bill: Bill, project: RclProject) -> bool:
        """The "opinions published" notice is due: opinions (stanowiska) or the ministry's answer
        appeared since the stored copy, or they were seen before and the notice failed to post
        (the stored copy is refreshed before the post, so the failed row is the only trace)."""
        now = project.consultation
        if now is None or not now.results_published:
            return False
        before = bill.rcl.consultation if bill.rcl else None
        if before is None:
            # The first sight of a window is not news about it: a project taken by its text
            # arrives with none at all, and the stanowiska such a project already carries were
            # published while nobody here was watching. What appears under our eyes is told.
            return False
        if not before.results_published:
            return True
        return self._poster.retry_due(bill, PublicationKind.CONSULTATION_RESULTS)
