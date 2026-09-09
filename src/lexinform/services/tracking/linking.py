"""A followed entry without a print number (RPW, RCL) got one: the print inherits the card.

The card stays the root of the Telegram thread; the print row copies status and analysis, the
old row is marked `linked`, and one update announces the print number (with a re-analysis when
the print text differs from what was analysed before).
"""

import logging

from lexinform.models import (
    Bill,
    BillStatus,
    Publication,
    PublicationKind,
    PublicationStatus,
    StatusChange,
    diff_stages,
    stage_fingerprint,
)
from lexinform.ports import BillRepository, Clock, SejmGateway
from lexinform.services.analysis import AnalysisService
from lexinform.services.sources import SejmTextSource, fetch_print
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
        self._channel_id = channel_id
        self._analysis = analysis
        self._text_prefilter = text_prefilter
        self._texts = SejmTextSource(gateway)

    def _status_of_print(self, pre: Bill) -> BillStatus:
        """The print copies the entry's status, except a title miss: an RPW entry has no text
        to scan, the print has, so it goes through the text prefilter instead of inheriting
        the skip."""
        if pre.status is BillStatus.SKIPPED_PREFILTER and self._text_prefilter:
            return BillStatus.TEXT_PREFILTER_PENDING
        return pre.status

    def link(self, pre: Bill, print_number: str, result: TrackingResult, *, publish: bool) -> None:
        """Continue `pre` under the print, in the same thread."""
        now = self._clock.now()
        detail = self._gateway.get_process(pre.term, print_number)
        self._repo.upsert_summary(detail, now=now)
        self._repo.save_stages(
            pre.term, print_number, detail.stages, stage_fingerprint(detail.stages)
        )
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
        result.linked += 1
        log.info("%s became druk %s", pre.number, print_number)

        card = self._poster.card(pre)
        if card is None or card.status is not PublicationStatus.SENT:
            return  # never posted: the print goes through the normal publishing path
        # The card stays the thread root: the print inherits it instead of getting a second card.
        # Re-rendered first, so it shows the druk's tag next to its own.
        linked_pre = self._repo.get(pre.term, pre.number)
        if publish and linked_pre is not None:
            self._poster.retag_card(linked_pre, card)
        pub_id = self._repo.create_publication(
            Publication(
                term=pre.term,
                number=print_number,
                kind=PublicationKind.NEW_BILL,
                status=PublicationStatus.SENT,
                channel_id=self._channel_id,
                message_id=card.message_id,
                created_at=now,
            )
        )
        self._repo.mark_publication(pub_id, PublicationStatus.SENT, message_id=card.message_id)

        bill = self._repo.get(pre.term, print_number)
        if bill is None:
            return
        content_changed = False
        if self._analysis is not None and bill.analysis is not None:
            print_info = fetch_print(self._gateway, bill)
            document = self._texts.newer(bill, detail, print_info)
            if document is not None:
                record = self._analysis.reanalyze_bill(bill, document, summary=detail)
                if record is not None:
                    result.count_reanalysis(record)
                    content_changed = True
        fresh = self._repo.get(pre.term, print_number) or bill
        new_stages = [self._enricher.enrich(pre.term, st) for st in diff_stages((), detail.stages)]
        change = StatusChange(
            term=pre.term,
            number=print_number,
            old_fingerprint=pre.number,
            new_fingerprint=change_key(stage_fingerprint(detail.stages), fresh, closed=False),
            new_stages=new_stages,
            passed=detail.passed,
            content_changed=content_changed,
            detected_at=now,
        )
        change_id = self._repo.add_status_change(change)
        if change_id is None:
            return
        change.id = change_id
        result.changed += 1
        if publish:
            result.count_post(self._poster.status_update(fresh, change))
        else:
            self._poster.hold(fresh, change)  # told with the next update of the print
