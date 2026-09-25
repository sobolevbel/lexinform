"""The weekly digest: a draft to the technical channel, published to readers only on approval.

The draft carries a button; pressing it files `/digest publish ref=…` like any operator command,
and the run that executes it builds the week again from the database before it posts. So nothing
reaches readers unread, and nothing a reader sees was assembled more than a moment before.
"""

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    DIGEST_NUMBER,
    DIGEST_TERM,
    Bill,
    Digest,
    DigestEntry,
    MonthFigures,
    Publication,
    PublicationKind,
    PublicationStatus,
    RunMode,
    RunReport,
    Upcoming,
    WeekFigures,
    consultation_open,
    is_first_digest_of_month,
    iso_week,
    previous_week,
    update_event,
    week_bounds,
)
from lexinform.ports import BillRepository, Clock, Publisher

log = logging.getLogger(__name__)

CARD_KINDS = (PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL)
UPDATE_KINDS = (
    PublicationKind.STATUS_UPDATE,
    PublicationKind.ACT_PUBLISHED,
    PublicationKind.IN_FORCE,
    PublicationKind.CONSULTATION_RESULTS,
)
"""The replies a week is worth telling again. The reminders and the agenda posts are not: each
was about a date, and by Sunday that date has either passed or is in "what is ahead" below."""

SITTINGS_AHEAD_DAYS = 14
SUNDAY = 6
"""The day an ISO week ends on, which is not the same question as the day a digest goes out."""


def _add_or_replace(entries: list[DigestEntry], slots: dict[int, int], entry: DigestEntry) -> None:
    """A `new_bill` row and the alias a later link creates for it (`Linker._inherit_card` and its
    two siblings) share one Telegram `message_id` — one post, not two — so a project that got its
    druk the same week its card was sent must not appear twice under two numbers. `message_id` is
    `None` only for a row that was never actually sent, which the digest's own `sent`-only query
    should not produce; treated as never colliding, so such a row is not silently dropped either.
    The later post wins: its bill row is the one still current, the one aliased away is not.
    """
    if entry.message_id is None:
        entries.append(entry)
        return
    slot = slots.get(entry.message_id)
    if slot is None:
        slots[entry.message_id] = len(entries)
        entries.append(entry)
    else:
        entries[slot] = entry


def _wykaz_number(bill: Bill) -> str | None:
    """The number a government row is known by, the same one its card's header uses."""
    if bill.wykaz is not None:
        return bill.wykaz.number
    if bill.rcl is not None:
        return bill.rcl.wykaz_number
    return bill.linked_wykaz_number


@dataclass
class DigestResult:
    """What one digest phase or command did; `ref` is the week it was about."""

    ref: str = ""
    drafted: bool = False
    published: bool = False
    failed: bool = False
    note: str = ""
    message_id: int | None = None


class DigestService:
    """Builds the week's digest, drafts it for the operator and publishes it on approval."""

    def __init__(
        self,
        repo: BillRepository,
        publisher: Publisher,
        drafter: Publisher | None,
        clock: Clock,
        *,
        channel_id: str,
        draft_channel_id: str,
        approve_label: str,
        weekday: int = 6,
        local_tz: ZoneInfo = ZoneInfo("Europe/Warsaw"),
        closed_grace_days: int = 90,
        pending_decision_max_days: int = 1095,
    ) -> None:
        self._repo = repo
        self._publisher = publisher
        self._drafter = drafter
        self._clock = clock
        self._channel_id = channel_id
        self._draft_channel_id = draft_channel_id
        self._approve = approve_label
        self._weekday = weekday
        self._tz = local_tz
        self._closed_grace_days = closed_grace_days
        self._pending_decision_max_days = pending_decision_max_days

    def today(self) -> dt.date:
        """The reader's day, which is the Sejm's: a digest is due on a Warsaw Sunday."""
        return self._clock.now().astimezone(self._tz).date()

    def current_ref(self) -> str:
        """The last ISO week that has ended, which is what a digest sent now is about.

        Asked of the calendar and not of `digest_weekday`: an ISO week ends on a Sunday whatever
        day the digest goes out on, so a Monday digest was drafting the week that had just begun.
        """
        today = self.today()
        return iso_week(today) if today.weekday() == SUNDAY else previous_week(today)

    def run(self, *, current_report: RunReport | None = None) -> DigestResult:
        """The digest phase of a run: draft the week on its day, and nothing on any other."""
        if self._drafter is None:
            return DigestResult(note="no technical channel: a digest is never sent unread")
        if self.today().weekday() != self._weekday:
            return DigestResult(note="not the digest's day")
        return self.draft(self.current_ref(), current_report=current_report)

    def draft(self, ref: str, *, current_report: RunReport | None = None) -> DigestResult:
        """Post the week's digest to the technical channel with the button that publishes it."""
        drafter = self._drafter
        if drafter is None:
            return DigestResult(ref=ref, note="no technical channel to draft into")
        existing = self._repo.get_publication(
            DIGEST_TERM, DIGEST_NUMBER, PublicationKind.DIGEST, self._draft_channel_id, ref=ref
        )
        if existing is not None and existing.status is PublicationStatus.SENT:
            return DigestResult(ref=ref, message_id=existing.message_id, note="drafted already")
        week = self.build(ref, current_report=current_report)
        message_id = self._send(
            self._draft_channel_id,
            week,
            lambda: drafter.publish_digest(week, approve=self._approve).message_id,
        )
        if message_id is None:
            return DigestResult(ref=ref, failed=True, note=f"{ref} was not posted, see the log")
        return DigestResult(ref=ref, drafted=True, message_id=message_id)

    def publish(self, ref: str) -> DigestResult:
        """Post the week's digest to the readers' channel, rebuilt from the database first."""
        existing = self._repo.get_publication(
            DIGEST_TERM, DIGEST_NUMBER, PublicationKind.DIGEST, self._channel_id, ref=ref
        )
        if existing is not None and existing.status is PublicationStatus.SENT:
            return DigestResult(
                ref=ref, message_id=existing.message_id, note="this week is in the channel already"
            )
        week = self.build(ref)
        message_id = self._send(
            self._channel_id, week, lambda: self._publisher.publish_digest(week).message_id
        )
        if message_id is None:
            return DigestResult(ref=ref, failed=True, note=f"{ref} was not posted, see the log")
        return DigestResult(ref=ref, published=True, message_id=message_id)

    def build(self, ref: str, *, current_report: RunReport | None = None) -> Digest:
        """The week as the channel lived it, plus what a reader can still act on."""
        since, until = week_bounds(ref)
        posts = self._repo.list_publications_between(
            self._channel_id,
            since=self._midnight(since),
            until=self._midnight(until + dt.timedelta(days=1)),
        )
        bills: dict[tuple[int, str], Bill] = {}
        cards: list[DigestEntry] = []
        updates: list[DigestEntry] = []
        card_slots: dict[int, int] = {}  # message_id -> index in `cards`
        update_slots: dict[int, int] = {}
        for post in posts:
            entry = self._entry(post, bills)
            if entry is None:
                continue
            if post.kind in CARD_KINDS:
                _add_or_replace(cards, card_slots, entry)
            elif post.kind in UPDATE_KINDS:
                _add_or_replace(updates, update_slots, entry)
        consultations, sittings = self._ahead()
        starts = self._midnight(since)
        ends = self._midnight(until + dt.timedelta(days=1))
        reports = self._repo.list_runs(since=starts)
        if current_report is not None:
            reports.append(current_report)
        reports = [
            r for r in reports if starts <= r.started_at < ends and r.mode is not RunMode.DRY_RUN
        ]
        return Digest(
            ref=ref,
            since=since,
            until=until,
            # Everything the week held: how much of it fits one message is the renderer's
            # business, and only the renderer can say how many it left out.
            cards=tuple(cards),
            updates=tuple(updates),
            consultations=tuple(consultations),
            sittings=tuple(sittings),
            month=self._month(ref),
            figures=WeekFigures.of(reports) if reports else None,
        )

    def _entry(self, post: Publication, bills: dict[tuple[int, str], Bill]) -> DigestEntry | None:
        """One post as the digest names it; None when its bill is gone from the database."""
        key = (post.term, post.number)
        if key not in bills:
            found = self._repo.get(post.term, post.number)
            if found is None:
                return None
            bills[key] = found
        bill = bills[key]
        analysis = bill.analysis.analysis if bill.analysis is not None else None
        return DigestEntry(
            term=bill.term,
            number=bill.number,
            title=bill.summary.title,
            kind=post.kind,
            sent_at=post.sent_at or post.created_at,
            message_id=post.message_id,
            score=analysis.score if analysis is not None else None,
            category=analysis.category if analysis is not None else None,
            wykaz_number=_wykaz_number(bill),
            event=self._event(post, bill),
        )

    def _event(self, post: Publication, bill: Bill) -> str:
        """What an update said, read from the change it was written for and not from the bill's
        state today, which has moved on since."""
        if post.kind is not PublicationKind.STATUS_UPDATE or post.status_change_id is None:
            return ""
        change = self._repo.get_status_change(post.status_change_id)
        return update_event(change, bill) if change is not None else ""

    def _ahead(self) -> tuple[list[Upcoming], list[Upcoming]]:
        """What the followed bills still offer: an open consultation, a sitting in a fortnight."""
        today = self.today()
        horizon = today + dt.timedelta(days=SITTINGS_AHEAD_DAYS)
        consultations: list[Upcoming] = []
        sittings: list[Upcoming] = []
        for bill in self._followed():
            window = bill.consultation
            if window is not None and window.end is not None and consultation_open(bill, today):
                consultations.append(self._upcoming(bill, deadline=window.end))
            for item in sorted(bill.agenda, key=lambda i: (i.date, i.start_time or dt.time())):
                if today <= item.date <= horizon:
                    sittings.append(self._upcoming(bill, sitting=item))
                    break
        consultations.sort(key=lambda w: (w.deadline or horizon, w.number))
        sittings.sort(key=lambda w: (w.sitting.date if w.sitting else horizon, w.number))
        return consultations, sittings

    def _followed(self) -> list[Bill]:
        return self._repo.list_tracked(
            self._channel_id,
            closed_grace_days=self._closed_grace_days,
            pending_decision_max_days=self._pending_decision_max_days,
            now=self._clock.now(),
        )

    def _upcoming(self, bill: Bill, **what: object) -> Upcoming:
        card = self._repo.get_publication(
            bill.term, bill.number, PublicationKind.NEW_BILL, self._channel_id
        )
        return Upcoming(
            term=bill.term,
            number=bill.number,
            title=bill.summary.title,
            message_id=card.message_id if card is not None else None,
            wykaz_number=_wykaz_number(bill),
            **what,
        )

    def _month(self, ref: str) -> MonthFigures | None:
        """The figures of the month just ended, in the first digest of a month and in no other.

        The month reported is the one before the digest's own, so that it is complete; run
        records are kept for ninety days, which always covers it."""
        if not is_first_digest_of_month(ref):
            return None
        _, sunday = week_bounds(ref)
        this_month = sunday.replace(day=1)
        last_month = (this_month - dt.timedelta(days=1)).replace(day=1)
        ends = self._midnight(this_month)
        reports = [
            report
            for report in self._repo.list_runs(since=self._midnight(last_month))
            if report.started_at.astimezone(dt.UTC) < ends
        ]
        return MonthFigures.of(last_month, reports)

    def _midnight(self, day: dt.date) -> dt.datetime:
        """The start of a Warsaw day, in the UTC the `sent_at` column holds."""
        return dt.datetime.combine(day, dt.time(), tzinfo=self._tz).astimezone(dt.UTC)

    def _send(self, channel_id: str, week: Digest, post: Callable[[], int]) -> int | None:
        """Record the row before sending it, and the outcome after; the message id on success."""
        pub_id = self._repo.create_publication(
            Publication(
                term=DIGEST_TERM,
                number=DIGEST_NUMBER,
                kind=PublicationKind.DIGEST,
                status=PublicationStatus.PENDING,
                channel_id=channel_id,
                ref=week.ref,
                created_at=self._clock.now(),
            )
        )
        try:
            message_id = post()
        except ServiceUnavailableError as exc:
            self._repo.mark_publication(
                pub_id, PublicationStatus.FAILED, error=exc.describe(), count_attempt=False
            )
            raise
        except Exception as exc:
            log.exception("digest %s could not be posted: %s", week.ref, exc)
            self._repo.mark_publication(
                pub_id, PublicationStatus.FAILED, error=f"{type(exc).__name__}: {exc}"
            )
            return None
        self._repo.mark_publication(
            pub_id, PublicationStatus.SENT, message_id=message_id, sent_at=self._clock.now()
        )
        return message_id
