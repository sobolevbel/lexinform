"""A followed entry without a print number (RPW, RCL) got one: the print inherits the card.

The card stays the root of the Telegram thread; the print row copies status and analysis unless
it has a reading of its own, the
old row is marked `linked`, and one update announces the print number (with a re-analysis when
the print text differs from what was analysed before).
"""

import logging
from datetime import datetime

from lexinform.models import (
    Bill,
    BillStatus,
    ProcessDetail,
    Publication,
    PublicationStatus,
    Stage,
    StatusChange,
    diff_stages,
    observe,
    stage_fingerprint,
)
from lexinform.ports import BillRepository, Clock, SejmGateway
from lexinform.services.analysis import AnalysisService
from lexinform.services.publications import inherit_card
from lexinform.services.sources import RclTextSource, SejmTextSource, fetch_print
from lexinform.services.tracking.acts import ActWatcher
from lexinform.services.tracking.posting import Poster
from lexinform.services.tracking.result import TrackingResult
from lexinform.services.tracking.stages import StageEnricher, change_key

log = logging.getLogger(__name__)

_BEING_READ = frozenset(
    {BillStatus.BATCH_PENDING, BillStatus.ANALYSIS_READY, BillStatus.REANALYSIS_READY}
)


def _read_on_its_own(druk: Bill) -> bool:
    """Whether the print was read, or is being read, from its own text."""
    return druk.status in _BEING_READ or (
        druk.status is BillStatus.ANALYZED and druk.analysis is not None
    )


class Linker:
    def __init__(
        self,
        gateway: SejmGateway,
        repo: BillRepository,
        clock: Clock,
        poster: Poster,
        enricher: StageEnricher,
        acts: ActWatcher,
        *,
        channel_id: str,
        analysis: AnalysisService | None,
        text_prefilter: bool = True,
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._clock = clock
        self._poster = poster
        self._enricher = enricher
        self._acts = acts
        self._channel_id = channel_id
        self._analysis = analysis
        self._text_prefilter = text_prefilter
        self._texts = SejmTextSource(gateway)

    def _status_of_print(self, pre: Bill) -> BillStatus:
        """The print copies the entry's status, except a prefilter skip: the print's own PDF is
        scanned instead of the skip being inherited.

        The skip may mean the keywords missed the entry's own file, or that the file was a scan,
        or that the WAF refused us, and the status cannot say which. The print comes from
        api.sejm.gov.pl and costs a download and no tokens, so it is scanned instead.
        """
        skipped = (BillStatus.SKIPPED_PREFILTER, BillStatus.SKIPPED_TEXT_PREFILTER)
        if pre.status in skipped and self._text_prefilter:
            return BillStatus.TEXT_PREFILTER_PENDING
        return pre.status

    def link(self, pre: Bill, print_number: str, result: TrackingResult, *, publish: bool) -> None:
        """Continue `pre` under the print, in the same thread.

        An entry whose card was never posted has no thread to continue and takes the normal
        publishing path. The act comes before the announcement, as in the daily loop: a project
        can be found long after the Sejm was done with it, and a print that old is past every
        `list_tracked` window the moment it is created, so this is its only chance at the act.
        """
        own = self._repo.get(pre.term, print_number)
        own_card = self._poster.card(own) if own is not None else None
        if own_card is not None and own_card.status is PublicationStatus.SENT:
            self._keep_prints_card(pre, print_number, own_card, result, publish=publish)
            return
        now = self._clock.now()
        detail = self._gateway.get_process(pre.term, print_number)
        stages = self._enricher.name_committees(pre.term, detail.stages)
        card = self._poster.card(pre)
        read = own is not None and _read_on_its_own(own)
        status = own.status if own is not None and read else self._status_of_print(pre)
        analysis = own.analysis if own is not None and own.analysis is not None and read else None
        bill = pre.model_copy(
            update={
                "summary": detail,
                "stages": stages,
                "stages_fingerprint": stage_fingerprint(detail.stages),
                "linked_number": pre.number,
                "status": status,
                "analysis": analysis or pre.analysis,
            }
        )
        change = None
        if card is not None and card.status is PublicationStatus.SENT:
            changed = False
            if not read:
                bill, changed = self._reanalyze_print(bill, detail, result)
            change = self._print_change(bill, pre, detail, content_changed=changed, now=now)
        alias = None
        with self._repo.atomic():
            self._adopt(pre, print_number, detail, stages, now=now, status=status, read=read)
            # Saving an analysis resets the status and the generation, so a pending batch too.
            if bill.analysis is not None and not read:
                self._repo.save_analysis(bill.term, bill.number, bill.analysis)
            if bill.authors is not None:
                self._repo.save_authors(bill.term, bill.number, bill.authors)
            if card is not None and card.status is PublicationStatus.SENT:
                alias = inherit_card(
                    self._repo,
                    term=pre.term,
                    number=print_number,
                    channel_id=self._channel_id,
                    card=card,
                    now=now,
                )
            if change is not None:
                change = self._poster.record_change(change)
                if change is not None:
                    self._poster.prepare(bill, change)
            fresh = self._repo.get(pre.term, print_number)
            assert fresh is not None, "_adopt wrote the print's row in this transaction"
            self._repo.save_observed_process(
                pre.term, print_number, observe(fresh, closure_date=detail.closure_date)
            )
        result.linked += 1
        log.info("%s became druk %s", pre.number, print_number)
        if alias is None:
            return
        self._acts.check(bill, detail, result, publish=publish)
        if change is not None:
            result.changed += 1
            self._poster.tell(bill, change, result, publish=publish)
        self._render_card(pre.term, print_number, alias, publish=publish, fallback=pre)

    def _keep_prints_card(
        self,
        pre: Bill,
        print_number: str,
        card: Publication,
        result: TrackingResult,
        *,
        publish: bool,
    ) -> None:
        """The print was carded first: it keeps its card and analysis; the entry points at it."""
        with self._repo.atomic():
            self._repo.link_bills(
                pre.term,
                pre.number,
                print_number,
                wykaz_number=pre.rcl.wykaz_number if pre.rcl is not None else None,
            )
        result.linked += 1
        stale = self._poster.card(pre)
        if stale is not None and stale.status is PublicationStatus.SENT:
            log.warning(
                "%s became druk %s, which has its own card: message %s is left without a thread",
                pre.number,
                print_number,
                stale.message_id,
            )
        else:
            log.info("%s became druk %s, which keeps its own card", pre.number, print_number)
        self._render_card(pre.term, print_number, card, publish=publish)

    def _adopt(
        self,
        pre: Bill,
        print_number: str,
        detail: ProcessDetail,
        stages: tuple[Stage, ...],
        *,
        now: datetime,
        status: BillStatus,
        read: bool,
    ) -> None:
        """The print takes over what the entry knew, never over a reading of its own."""
        self._repo.upsert_summary(detail, now=now)
        self._repo.save_stages(
            pre.term,
            print_number,
            stages,
            stage_fingerprint(detail.stages),
        )
        self._repo.save_observed_closure(pre.term, print_number, detail.closure_date)
        if not read:
            self._repo.set_status(pre.term, print_number, status, prefilter_hits=pre.prefilter_hits)
            if status is not pre.status:
                log.info(
                    "druk %s: %s was a title miss; its text is scanned next",
                    print_number,
                    pre.number,
                )
            if pre.analysis is not None:
                self._repo.save_analysis(pre.term, print_number, pre.analysis)
        if pre.submission is not None:
            self._repo.save_submission(pre.term, print_number, pre.submission)
        self._repo.link_bills(
            pre.term,
            pre.number,
            print_number,
            wykaz_number=pre.rcl.wykaz_number if pre.rcl is not None else None,
        )

    def _render_card(
        self,
        term: int,
        print_number: str,
        alias: Publication,
        *,
        publish: bool,
        fallback: Bill | None = None,
    ) -> None:
        """Write the print's own card into the thread's root message, once, at the end.

        Rendered from the entry at the moment of the alias, the card showed the druk's tag and
        nothing else the print knows. Nothing renders it again either: `CardRefresher` works from
        the list taken before the tracking phase, where this row did not yet exist.
        """
        if not publish:
            return
        bill = self._repo.get(term, print_number)
        if bill is None:
            return
        if bill.analysis is None and fallback is not None:
            # A print still being read shows the entry's analysis until its own arrives.
            bill = bill.model_copy(update={"analysis": fallback.analysis})
        self._poster.rerender_card(bill, alias)

    def _print_change(
        self,
        bill: Bill,
        pre: Bill,
        detail: ProcessDetail,
        *,
        now: datetime,
        content_changed: bool,
    ) -> StatusChange:
        return StatusChange(
            term=bill.term,
            number=bill.number,
            old_fingerprint=pre.number,
            new_fingerprint=change_key(stage_fingerprint(detail.stages), bill, closed=False),
            new_stages=[
                self._enricher.enrich(bill.term, st) for st in diff_stages((), detail.stages)
            ],
            passed=detail.passed,
            content_changed=content_changed,
            detected_at=now,
        )

    def _reanalyze_print(
        self, bill: Bill, detail: ProcessDetail, result: TrackingResult
    ) -> tuple[Bill, bool]:
        """True when the model read the print as a new text and named what changed in it."""
        if self._analysis is None or bill.analysis is None:
            return bill, False
        document = self._texts.newer(bill, detail, fetch_print(self._gateway, bill))
        if document is None:
            return bill, False
        fresh, reanalysed = self._analysis.prepare_reanalysis(
            bill,
            document,
            summary=detail,
            previous_document=RclTextSource().locate(bill).document,
        )
        if not reanalysed or fresh.analysis is None:
            return fresh, False
        result.count_reanalysis(fresh.analysis)
        return fresh, fresh.analysis.analysis.names_changes
