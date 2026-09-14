"""The end of a Sejm term (kadencja): what happens to the bills of the old one.

Zasada dyskontynuacji: a bill the Sejm has not finished with when the term ends lapses; a new Sejm
must receive it again (citizens' bills are the statutory exception and are taken over). Nothing
in the API says so, the processes just stop moving, so the bot draws the line itself the first
time it runs in the new term: every published, unfinished bill of the old term gets one last
update under its card and is marked discontinued, which takes it out of every listing (tracking,
reminders, analysis and publishing queues). Bills the Sejm passed stay followed: the Senate, the
President and Dziennik Ustaw do not care about the term. Government projects on RCL are not bound
to a term either: the rows still waiting for their druk move to the new term, so that the druk
which appears there can take over their thread.

Idempotent and safe to repeat every run: the posts are recorded before the rows are marked, so
a Telegram outage half-way leaves the rest for the next run.
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
        """Announce the lapsed bills of `previous` and carry the government's own rows over to
        `current`. False when Telegram is down (the unposted bills are left for the next run)."""
        moved = self._repo.move_government_rows(previous, current)
        if moved:
            result.rehomed += moved
            log.warning(
                "term %d -> %d: %d government row(s) carried over", previous, current, moved
            )
        unfinished = self._repo.list_unfinished_published(previous, self._channel_id)
        announced: list[Bill] = []
        for bill in unfinished:
            try:
                if not self._announce(bill, result, publish=publish):
                    continue
            except ServiceUnavailableError as exc:
                result.abort(exc, failed=True)
                return False
            except Exception as exc:
                result.failed += 1
                log.exception("announcing the end of term for %s failed: %s", bill.number, exc)
            else:
                announced.append(bill)
        marked = self._repo.discontinue_unfinished(previous, at=self._clock.now())
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

    def _announce(self, bill: Bill, result: TrackingResult, *, publish: bool) -> bool:
        """One update under the card; False when it was posted by an earlier run already.

        With publishing off the change is held rather than dropped: the change row alone would
        make the next publishing run believe the announcement had been made.
        """
        card = self._poster.card(bill)
        if card is None or card.status is not PublicationStatus.SENT:
            return False
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
        if change is None:
            return False
        result.changed += 1
        log.info("%s lapsed with the end of term %d", bill.number, bill.term)
        # A bill whose last word is held counts as laid to rest all the same: the row is there
        # and the next post carries it; only a failed send leaves it for another run.
        told = self._poster.tell(bill, change, result, publish=publish)
        result.discontinued += int(told is not Told.FAILED)
        return True
