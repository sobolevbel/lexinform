"""The end of a Sejm term (kadencja): what happens to the bills of the old one.

Zasada dyskontynuacji: a bill the Sejm has not finished with lapses when the term ends, citizens'
bills excepted. Nothing in the API says so — the processes just stop moving — so the first run of
a new term draws the line: every published, unfinished bill of the old one gets a last update and
is marked discontinued, which takes it out of every listing. Passed bills stay followed, the
Senate and the President not caring about the term, and RCL rows still waiting for their druk
move to the new term so that druk can take their thread.

Discontinuation and delivery plans commit together before any network sends.
"""

import logging

from lexinform.errors import ServiceUnavailableError
from lexinform.models import Bill, PublicationStatus, StatusChange
from lexinform.ports import BillRepository, Clock
from lexinform.services.tracking.posting import Poster, Told
from lexinform.services.tracking.result import TrackingResult
from lexinform.services.tracking.stages import synthetic_key

log = logging.getLogger(__name__)


class TermRollover:
    def __init__(self, repo: BillRepository, clock: Clock, poster: Poster, *, channel_id: str):
        self._repo = repo
        self._clock = clock
        self._poster = poster
        self._channel_id = channel_id

    def close_term(
        self, previous: int, current: int, result: TrackingResult, *, publish: bool
    ) -> bool:
        """Queued announcements survive discontinuation even when Telegram is unavailable."""
        moved = self._repo.move_government_rows(previous, current)
        if moved:
            result.rehomed += moved
            log.warning(
                "term %d -> %d: %d government row(s) carried over", previous, current, moved
            )
        announced: list[Bill] = []
        planned: list[tuple[Bill, StatusChange]] = []
        with self._repo.atomic():
            unfinished = self._repo.list_unfinished_published(previous, self._channel_id)
            for bill in unfinished:
                change = self._announce(bill)
                if change is not None:
                    planned.append((bill, change))
            marked = self._repo.discontinue_unfinished(previous, at=self._clock.now())
            for bill, change in planned:
                fresh = self._repo.get(bill.term, bill.number) or bill
                self._poster.prepare(fresh, change)
        result.changed += len(planned)
        for bill, change in planned:
            try:
                told = self._poster.tell(bill, change, result, publish=publish)
                result.discontinued += int(told is not Told.FAILED)
                announced.append(bill)
            except ServiceUnavailableError as exc:
                result.abort(exc, failed=True)
                return False
        if publish:
            self._close_cards(announced)
        if marked:
            log.warning(
                "term %d -> %d: %d unfinished bill(s) lapsed, %d of them announced",
                previous,
                current,
                marked,
                result.discontinued,
            )
        return True

    def _close_cards(self, announced: list[Bill]) -> None:
        """Re-render the card of every bill that lapsed, now that the row says it did.

        `CardRefresher` cannot do this one: it works off `list_tracked`, which drops a row the
        moment `discontinued_at` is set, so the card would keep «Что дальше: работа в комиссии»
        and «направить мнение в комиссию» over a bill no Sejm is working on any more. Cosmetic
        and best effort — the update that announced the lapse has already gone out.
        """
        for stale in announced:
            bill = self._repo.get(stale.term, stale.number)
            card = self._poster.card(stale)
            if bill is None or card is None or card.status is not PublicationStatus.SENT:
                continue
            try:
                self._poster.rerender_card(bill, card)
            except ServiceUnavailableError as exc:
                log.warning("cards of the lapsed term left as they are: %s", exc.describe())
                return

    def _announce(self, bill: Bill) -> StatusChange | None:
        card = self._poster.card(bill)
        if card is None or card.status is not PublicationStatus.SENT:
            return None
        change = self._poster.record_change(
            StatusChange(
                term=bill.term,
                number=bill.number,
                old_fingerprint=bill.stages_fingerprint,
                new_fingerprint=synthetic_key("discontinued", bill.number),
                new_stages=[],
                closure_detected=True,
                passed=False,
                discontinued=True,
                detected_at=self._clock.now(),
            )
        )
        return change
