"""Government projects followed on RCL: stage progress, consultation news, new text versions,
the hand-over to the Sejm and, once the druk exists, the link that continues the thread.

An RCL outage stops only this part of the tracking phase: the Sejm side of the run goes on.
"""

import logging

from lexinform.concurrency import fan_out
from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    Bill,
    PublicationKind,
    RclProject,
    Stage,
    StatusChange,
    diff_stages,
    process_summary,
    rcl_fingerprint,
    rcl_stages,
)
from lexinform.ports import BillRepository, Clock
from lexinform.services.analysis import AnalysisService
from lexinform.services.rcl_projects import RclProjectReader
from lexinform.services.sources import RclTextSource
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
        *,
        analysis: AnalysisService | None,
        consultations: ConsultationReminder | None,
        workers: int = 1,
    ) -> None:
        self._reader = reader
        self._repo = repo
        self._clock = clock
        self._poster = poster
        self._linker = linker
        self._analysis = analysis
        self._consultations = consultations
        self._texts = RclTextSource()
        self._workers = workers

    def check(self, bills: list[Bill], result: TrackingResult, *, publish: bool) -> bool:
        """Refresh the followed RCL projects among `bills` and post what changed; link the ones
        whose druk appeared. False when Telegram is down (RCL being down is only reported)."""
        if not self._link_pending(result, publish=publish):
            return False
        followed = [b for b in bills if b.rcl is not None]
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
                    if publish:
                        result.count_post(self._poster.status_update(fresh, change))
                if results_due and self._consultations is not None:
                    self._consultations.results_published(fresh, result, publish=publish)
            except ServiceUnavailableError as exc:
                result.abort(exc, failed=True)
                return False
        return True

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
        new_fp = rcl_fingerprint(project)
        stages = rcl_stages(project)
        change = self._detect_change(bill, project, stages, new_fp, result)
        if new_fp != bill.stages_fingerprint:
            # Written last: a failure above (an LLM outage during the re-analysis) leaves the
            # old fingerprint in place, so the next run sees the same new stages and tells them.
            self._repo.save_stages(bill.term, bill.number, stages, new_fp)
        return change

    def _detect_change(
        self,
        bill: Bill,
        project: RclProject,
        stages: tuple[Stage, ...],
        new_fp: str,
        result: TrackingResult,
    ) -> StatusChange | None:
        now = self._clock.now()
        stored = bill.rcl
        assert stored is not None
        self._repo.save_rcl(bill.term, bill.number, project)
        self._repo.upsert_summary(process_summary(project, term=bill.term), now=now)

        content_changed = False
        document = self._texts.locate(bill.model_copy(update={"rcl": project})).document
        if (
            self._analysis is not None
            and bill.analysis is not None
            and document is not None
            and document.url != bill.analysis.source_url
        ):
            log.info("%s: new text version on RCL, re-analysing", bill.number)
            record = self._analysis.reanalyze_bill(bill, document)
            if record is not None:
                result.count_reanalysis(record)
                content_changed = True

        closure = (
            not project.is_open
            and not project.sent_to_sejm
            and not self._repo.closure_announced(bill.term, bill.number)
        )
        new_stages = diff_stages(bill.stages, stages)
        consultation_opened = stored.consultation is None and project.consultation is not None
        if not (new_stages or content_changed or closure or consultation_opened):
            return None
        fresh = self._repo.get(bill.term, bill.number) or bill
        change = StatusChange(
            term=bill.term,
            number=bill.number,
            old_fingerprint=bill.stages_fingerprint,
            new_fingerprint=change_key(new_fp, fresh, closed=closure),
            new_stages=new_stages,
            closure_detected=closure,
            passed=False if closure else None,
            content_changed=content_changed,
            detected_at=now,
        )
        change_id = self._repo.add_status_change(change)
        if change_id is None:
            return None  # already recorded by an earlier run
        change.id = change_id
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
        if before is None or not before.results_published:
            return True
        return self._poster.retry_due(bill, PublicationKind.CONSULTATION_RESULTS)
