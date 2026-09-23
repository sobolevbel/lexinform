"""A followed entry without a print number (RPW, RCL) got one: the print inherits the card.

The card stays the root of the Telegram thread; the print row copies status and analysis, the
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
    PublicationKind,
    PublicationStatus,
    Stage,
    StatusChange,
    diff_stages,
    observe,
    stage_fingerprint,
)
from lexinform.ports import BillRepository, Clock, SejmGateway
from lexinform.services.analysis import AnalysisService
from lexinform.services.sources import SejmTextSource, fetch_print
from lexinform.services.tracking.acts import ActWatcher
from lexinform.services.tracking.posting import Poster
from lexinform.services.tracking.result import TrackingResult
from lexinform.services.tracking.stages import StageEnricher, change_key

log = logging.getLogger(__name__)


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
        now = self._clock.now()
        detail = self._gateway.get_process(pre.term, print_number)
        stages = self._enricher.name_committees(pre.term, detail.stages)
        card = self._poster.card(pre)
        bill = pre.model_copy(
            update={
                "summary": detail,
                "stages": stages,
                "stages_fingerprint": stage_fingerprint(detail.stages),
                "linked_number": pre.number,
                "status": self._status_of_print(pre),
            }
        )
        change = None
        if card is not None and card.status is PublicationStatus.SENT:
            bill, changed = self._reanalyze_print(bill, detail, result)
            change = self._print_change(bill, pre, detail, content_changed=changed, now=now)
        alias = None
        with self._repo.atomic():
            self._adopt(pre, print_number, detail, stages, now=now)
            if bill.analysis is not None:
                self._repo.save_analysis(bill.term, bill.number, bill.analysis)
            if bill.authors is not None:
                self._repo.save_authors(bill.term, bill.number, bill.authors)
            if card is not None and card.status is PublicationStatus.SENT:
                alias = self._inherit_card(pre, print_number, card, now=now)
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
        self._render_card(pre.term, print_number, alias, publish=publish)

    def _adopt(
        self,
        pre: Bill,
        print_number: str,
        detail: ProcessDetail,
        stages: tuple[Stage, ...],
        *,
        now: datetime,
    ) -> None:
        """The print takes over everything the entry knew about the bill."""
        self._repo.upsert_summary(detail, now=now)
        self._repo.save_stages(
            pre.term,
            print_number,
            stages,
            stage_fingerprint(detail.stages),
        )
        self._repo.save_observed_closure(pre.term, print_number, detail.closure_date)
        status = self._status_of_print(pre)
        self._repo.set_status(pre.term, print_number, status, prefilter_hits=pre.prefilter_hits)
        if status is not pre.status:
            log.info(
                "druk %s: %s was a title miss; its text is scanned next", print_number, pre.number
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

    def _inherit_card(
        self, pre: Bill, print_number: str, card: Publication, *, now: datetime
    ) -> Publication:
        """The card stays the root of the thread: the print is aliased to it instead of getting a
        second card. The alias comes back so that `_render_card` can write the print's own text
        into that message and keep the digest on the print's row."""
        alias = Publication(
            term=pre.term,
            number=print_number,
            kind=PublicationKind.NEW_BILL,
            status=PublicationStatus.SENT,
            channel_id=self._channel_id,
            message_id=card.message_id,
            created_at=now,
            sent_at=card.sent_at,
        )
        alias.id = self._repo.create_publication(alias)
        self._repo.mark_publication(
            alias.id, PublicationStatus.SENT, message_id=card.message_id, sent_at=card.sent_at
        )
        return alias

    def _render_card(
        self, term: int, print_number: str, alias: Publication, *, publish: bool
    ) -> None:
        """Write the print's own card into the thread's root message, once, at the end.

        Rendered from the entry at the moment of the alias, the card showed the druk's tag and
        nothing else the print knows. Nothing renders it again either: `CardRefresher` works from
        the list taken before the tracking phase, where this row did not yet exist.
        """
        if not publish:
            return
        bill = self._repo.get(term, print_number)
        if bill is not None:
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
        """True when the print carries a text the model had not seen under the entry's number."""
        if self._analysis is None or bill.analysis is None:
            return bill, False
        document = self._texts.newer(bill, detail, fetch_print(self._gateway, bill))
        if document is None:
            return bill, False
        fresh, changed = self._analysis.prepare_reanalysis(bill, document, summary=detail)
        if changed and fresh.analysis is not None:
            result.count_reanalysis(fresh.analysis)
        return fresh, changed
