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

import hashlib
import logging

from lexinform.errors import ServiceUnavailableError
from lexinform.models import Bill, PublicationStatus, StatusChange
from lexinform.ports import BillRepository, Clock
from lexinform.services.tracking.posting import Poster
from lexinform.services.tracking.result import TrackingResult

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
        """Announce the lapsed bills of `previous` and carry its RCL projects over to `current`.
        False when Telegram is down (the unposted bills are left for the next run)."""
        moved = self._repo.move_rcl_projects(previous, current)
        if moved:
            result.rcl_rehomed += moved
            log.warning("term %d -> %d: %d RCL project(s) carried over", previous, current, moved)
        unfinished = self._repo.list_unfinished_published(previous, self._channel_id)
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
        marked = self._repo.discontinue_unfinished(previous, at=self._clock.now())
        if marked:
            log.warning(
                "term %d -> %d: %d unfinished bill(s) lapsed, %d of them announced",
                previous,
                current,
                marked,
                result.discontinued,
            )
        return True

    def _announce(self, bill: Bill, result: TrackingResult, *, publish: bool) -> bool:
        """One update under the card; False when it was posted by an earlier run already."""
        card = self._poster.card(bill)
        if card is None or card.status is not PublicationStatus.SENT:
            return False
        change = StatusChange(
            term=bill.term,
            number=bill.number,
            old_fingerprint=bill.stages_fingerprint,
            new_fingerprint=hashlib.sha256(f"discontinued|{bill.number}".encode()).hexdigest(),
            new_stages=[],
            closure_detected=True,
            passed=False,
            discontinued=True,
            detected_at=self._clock.now(),
        )
        change_id = self._repo.add_status_change(change)
        if change_id is None:
            return False
        change.id = change_id
        result.changed += 1
        log.info("%s lapsed with the end of term %d", bill.number, bill.term)
        if publish:
            sent = self._poster.status_update(bill, change)
            result.count_post(sent)
            result.discontinued += int(sent)
        else:
            # Bookkeeping even without a post: the change row alone would make the next
            # publishing run believe the announcement was made.
            self._poster.hold(bill, change)
            result.discontinued += 1
        return True
