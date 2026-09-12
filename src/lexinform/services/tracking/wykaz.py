"""Bills the government has only announced: what the register says about them next.

Two things can happen to a plan. Its project appears on RCL — then the RCL row takes over the
thread the way a druk takes over an RPW entry's (`WykazLinker`), and the text is analysed at
last. Or the government drops it, which art. 3 ust. 3 of the lobbying act obliges the register
to say (`Status realizacji`, `Informacja o rezygnacji`) and no other source tells at all.

A slipped quarter or a rewritten "istota" is stored and not posted: that is a chronicle.
A row that simply disappears from the register says the same as `Wycofany` and is read so.
"""

import logging

from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    Bill,
    Publication,
    PublicationKind,
    PublicationStatus,
    StatusChange,
    WykazEntry,
    process_summary,
    rcl_fingerprint,
    rcl_stages,
    wykaz_entry_number,
    wykaz_fingerprint,
    wykaz_summary,
)
from lexinform.ports import BillRepository, Clock, WykazGateway
from lexinform.services.analysis import AnalysisService
from lexinform.services.rcl_projects import RclProjectReader
from lexinform.services.sources import RclTextSource
from lexinform.services.tracking.posting import Poster
from lexinform.services.tracking.result import TrackingResult

log = logging.getLogger(__name__)

REMOVED_STATUS = "Wycofany"


class WykazLinker:
    """The project of a plan we follow: the RCL row inherits the plan's card."""

    def __init__(
        self,
        reader: RclProjectReader,
        repo: BillRepository,
        clock: Clock,
        poster: Poster,
        *,
        channel_id: str,
        analysis: AnalysisService | None,
    ) -> None:
        self._reader = reader
        self._repo = repo
        self._clock = clock
        self._poster = poster
        self._channel_id = channel_id
        self._analysis = analysis
        self._texts = RclTextSource()

    def link(self, plan: Bill, project_id: int, result: TrackingResult, *, publish: bool) -> None:
        """Continue `plan` as its RCL project, in the same thread.

        The plan was judged on an announcement and the project has the text, so the row is
        re-analysed here: without that, a thin description would decide the fate of the row that
        finally carries the real thing.
        """
        assert plan.wykaz is not None
        now = self._clock.now()
        project = self._reader.complete(self._reader.timeline(project_id))
        summary = process_summary(project, term=plan.term)
        self._repo.upsert_summary(summary, now=now)
        self._repo.save_rcl(plan.term, summary.number, project)
        self._repo.save_stages(
            plan.term, summary.number, rcl_stages(project), rcl_fingerprint(project)
        )
        self._repo.set_status(
            plan.term, summary.number, plan.status, prefilter_hits=plan.prefilter_hits
        )
        if plan.analysis is not None:
            self._repo.save_analysis(plan.term, summary.number, plan.analysis)
        self._repo.link_bills(
            plan.term, plan.number, summary.number, wykaz_number=wykaz_entry_number(plan.number)
        )
        result.linked += 1
        log.info("%s is now a project on RCL: %s", plan.number, summary.number)

        card = self._poster.card(plan)
        if card is not None and card.status is PublicationStatus.SENT:
            self._inherit_card(plan, summary.number, card, publish=publish)
        bill = self._repo.get(plan.term, summary.number)
        if bill is None:
            return
        content_changed = self._reanalyse(bill, result)
        fresh = self._repo.get(plan.term, summary.number) or bill
        change = StatusChange(
            term=plan.term,
            number=summary.number,
            old_fingerprint=plan.number,
            new_fingerprint=rcl_fingerprint(project),
            new_stages=list(rcl_stages(project)),
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
            self._poster.hold(fresh, change)

    def _inherit_card(self, plan: Bill, number: str, card: Publication, *, publish: bool) -> None:
        """The plan's card stays the thread root; the project inherits it instead of getting a
        second card, and the card is re-rendered so that it carries both numbers."""
        linked_plan = self._repo.get(plan.term, plan.number)
        if publish and linked_plan is not None:
            self._poster.retag_card(linked_plan, card)
        pub_id = self._repo.create_publication(
            Publication(
                term=plan.term,
                number=number,
                kind=PublicationKind.NEW_BILL,
                status=PublicationStatus.SENT,
                channel_id=self._channel_id,
                message_id=card.message_id,
                created_at=self._clock.now(),
                sent_at=card.sent_at,
            )
        )
        self._repo.mark_publication(
            pub_id, PublicationStatus.SENT, message_id=card.message_id, sent_at=card.sent_at
        )

    def _reanalyse(self, bill: Bill, result: TrackingResult) -> bool:
        """True when the project's documents gave the model a text to read."""
        if self._analysis is None or bill.analysis is None:
            return False
        document = self._texts.locate(bill).document
        if document is None:
            return False
        record = self._analysis.reanalyze_bill(bill, document)
        if record is None:
            return False
        result.count_reanalysis(record)
        return True


class WykazWatcher:
    def __init__(
        self,
        wykaz: WykazGateway,
        repo: BillRepository,
        clock: Clock,
        poster: Poster,
        linker: WykazLinker,
    ) -> None:
        self._wykaz = wykaz
        self._repo = repo
        self._clock = clock
        self._poster = poster
        self._linker = linker

    def check(self, bills: list[Bill], result: TrackingResult, *, publish: bool) -> bool:
        """Re-read the register for the followed plans and post what the government decided;
        link the ones whose project is out. False when Telegram is down."""
        if not self._link_pending(result, publish=publish):
            return False
        followed = [b for b in bills if b.wykaz is not None]
        if not followed:
            return True
        entries = self._entries(result)
        if entries is None:
            return True
        return all(self._check_one(bill, entries, result, publish=publish) for bill in followed)

    def _entries(self, result: TrackingResult) -> dict[str, WykazEntry] | None:
        """The whole register, by number; None when it could not be downloaded, which stops this
        watcher and nothing else."""
        try:
            return {e.number: e for e in self._wykaz.entries()}
        except ServiceUnavailableError as exc:
            result.partial_errors.append(f"wykaz: {exc.describe()}")
            log.error("wykaz tracking stopped: %s", exc.describe())
            return None

    def _check_one(
        self,
        bill: Bill,
        entries: dict[str, WykazEntry],
        result: TrackingResult,
        *,
        publish: bool,
    ) -> bool:
        """False when Telegram is down; a failure of this one plan is counted and passed over."""
        assert bill.wykaz is not None
        result.checked += 1
        entry = entries.get(bill.wykaz.number) or self._removed(bill, entries)
        if entry is None:
            return True
        try:
            change = self._detect(bill, entry, result)
        except Exception as exc:
            result.failed += 1
            log.exception("tracking %s failed: %s", bill.number, exc)
            return True
        if change is None:
            return True
        fresh = self._repo.get(bill.term, bill.number) or bill
        try:
            if publish:
                result.count_post(self._poster.status_update(fresh, change))
            else:
                self._poster.hold(fresh, change)
        except ServiceUnavailableError as exc:
            result.abort(exc, failed=True)
            return False
        return True

    def _link_pending(self, result: TrackingResult, *, publish: bool) -> bool:
        """Plans whose project the RCL discovery has seen: the project takes over the thread."""
        for bill in self._repo.list_wykaz_awaiting_link():
            assert bill.wykaz is not None and bill.wykaz.rcl_project_id is not None
            try:
                self._linker.link(bill, bill.wykaz.rcl_project_id, result, publish=publish)
            except ServiceUnavailableError as exc:
                result.abort(exc)
                return False
            except Exception as exc:
                result.failed += 1
                log.exception("linking %s to its RCL project failed: %s", bill.number, exc)
        return True

    def _removed(self, bill: Bill, entries: dict[str, WykazEntry]) -> WykazEntry | None:
        """A plan whose row is gone from the register: the government dropped it without saying
        so, which is the one thing only this source can tell. An empty register is a download
        that went wrong, not a government that dropped everything."""
        assert bill.wykaz is not None
        if not entries:
            log.warning("the register came back empty: absences are not read as removals")
            return None
        return bill.wykaz.model_copy(update={"status": REMOVED_STATUS})

    def _detect(self, bill: Bill, entry: WykazEntry, result: TrackingResult) -> StatusChange | None:
        """Store what the register says now; a change row only for a decision a reader can act
        on: the government dropping the project, or adopting it. A slipped quarter or a rewritten
        "istota" is stored and not posted."""
        assert bill.wykaz is not None
        entry = entry.model_copy(update={"rcl_project_id": bill.wykaz.rcl_project_id})
        new_fp = wykaz_fingerprint(entry)
        if new_fp == bill.stages_fingerprint:
            return None
        self._repo.upsert_summary(wykaz_summary(entry, term=bill.term), now=self._clock.now())
        self._repo.save_wykaz(bill.term, bill.number, entry)
        self._repo.save_stages(bill.term, bill.number, (), new_fp)
        dropped = entry.is_withdrawn and not bill.wykaz.is_withdrawn
        adopted = entry.is_adopted and not bill.wykaz.is_adopted
        if not dropped and not adopted:
            return None
        change = StatusChange(
            term=bill.term,
            number=bill.number,
            old_fingerprint=bill.stages_fingerprint,
            new_fingerprint=new_fp,
            new_stages=[],
            closure_detected=dropped,
            passed=False,
            detected_at=self._clock.now(),
        )
        change_id = self._repo.add_status_change(change)
        if change_id is None:
            return None
        change.id = change_id
        result.changed += 1
        what = "dropped" if dropped else "adopted"
        log.info("%s: the government %s the project (%s)", bill.number, what, entry.status)
        return change
