"""Upcoming sittings that name a followed bill: the dated "what happens next".

Committee sittings come from `/committees/{code}/sittings` (one request per committee a followed
bill was referred to), Sejm sittings from `/proceedings` plus one `/proceedings/{n}` per sitting
that is not over yet. A bill's upcoming items are stored on the bill (so cards and updates can
say "II чтение — 15–18.09.2026") and every new (bill, sitting) pair is posted once.
"""

import datetime as dt
import logging
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from lexinform.agenda import (
    condition_covers,
    hearing_application,
    items_mentioning,
    sitting_condition,
)
from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    PLENARY_COMMITTEE_CODE,
    AgendaItem,
    Bill,
    CommitteeSitting,
    PublicationKind,
    PublicationStatus,
    SejmSitting,
    derived_print_numbers,
    flatten_stages,
)
from lexinform.ports import BillRepository, Clock, SejmGateway
from lexinform.services.tracking.posting import Poster
from lexinform.services.tracking.result import TrackingResult
from lexinform.services.tracking.stages import StageEnricher

log = logging.getLogger(__name__)

PLANNED = "PLANNED"


@dataclass(frozen=True)
class _Listings:
    """What one term's sittings look like this run, and what could not be read.

    The failures matter as much as the listings: an item that is missing because a request failed
    must not read as a sitting that was called off, so `kept` puts those items back.
    """

    committee: dict[str, list[CommitteeSitting]]
    sejm: list[SejmSitting]
    failed_codes: set[str]
    failed_sittings: set[int]

    def kept(self, bill: Bill, today: dt.date) -> tuple[AgendaItem, ...]:
        """What we already knew about a committee or a Sejm sitting whose listing failed."""
        return tuple(
            old
            for old in bill.agenda
            if old.last_date >= today
            and (
                old.committee_code in self.failed_codes
                if old.kind == "committee"
                else old.sitting_number in self.failed_sittings
            )
        )

    def announced(self, item: AgendaItem) -> bool:
        """The sitting is still announced, whatever became of this bill's place on its agenda."""
        if item.kind == "committee":
            listed = self.committee.get(item.committee_code or "", [])
            return any(s.num == item.sitting_number for s in listed)
        return any(s.number == item.sitting_number for s in self.sejm)


def _committee_codes(bill: Bill) -> set[str]:
    """Committees that have the bill: the referral names one, and the work and the report that
    follow name whichever took it over."""
    wanted = ("Referral", "CommitteeWork", "CommitteeReport")
    return {
        st.committee_code
        for st in flatten_stages(bill.stages)
        if st.stage_type in wanted and st.committee_code
    }


class AgendaWatcher:
    """Matches followed bills against the agendas of upcoming committee and Sejm sittings.

    A sitting counts only while its status is `PLANNED`; anything else is over or called off.
    `PLENARY_COMMITTEE_CODE` is the `committeeCode` a referral to a reading at a sitting has.
    """

    def __init__(
        self,
        gateway: SejmGateway,
        repo: BillRepository,
        clock: Clock,
        poster: Poster,
        enricher: StageEnricher,
        *,
        channel_id: str,
        local_tz: ZoneInfo,
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._clock = clock
        self._poster = poster
        self._enricher = enricher
        self._channel_id = channel_id
        self._local_tz = local_tz

    def check(self, bills: list[Bill], result: TrackingResult, *, publish: bool) -> bool:
        """Refresh the upcoming sittings of every bill and post the new ones. False on an outage.
        Sittings are listed once per term the bills belong to."""
        followed = [b for b in bills if b.has_process]
        today = self._clock.now().astimezone(self._local_tz).date()
        for term in sorted({b.term for b in followed}):
            of_term = [b for b in followed if b.term == term]
            try:
                listings = self._listings(term, of_term, today)
            except ServiceUnavailableError as exc:
                result.partial_errors.append(f"sittings: {exc.describe()}")
                log.error("agenda watch stopped for term %d: %s", term, exc.describe())
                continue
            if not self._check_term(of_term, listings, result, today, publish=publish):
                return False
        return True

    def _listings(self, term: int, bills: list[Bill], today: dt.date) -> _Listings:
        committee, failed_codes = self._committee_sittings(term, bills, today)
        sejm, failed_sittings = self._sejm_sittings(term, today)
        return _Listings(committee, sejm, failed_codes, failed_sittings)

    def _check_term(
        self,
        bills: list[Bill],
        listings: _Listings,
        result: TrackingResult,
        today: dt.date,
        *,
        publish: bool,
    ) -> bool:
        for bill in bills:
            try:
                items = self._items_for(bill, listings)
                items += listings.kept(bill, today)
                items = tuple(sorted(items, key=lambda i: (i.date, i.ref)))
                if publish:
                    self._retract_gone(bill, items, listings, result)
                if items != bill.agenda:
                    self._repo.save_agenda(bill.term, bill.number, items)
                if publish:
                    self._post_new(bill, items, result)
            except ServiceUnavailableError as exc:
                result.abort(exc, failed=True)
                return False
            except Exception as exc:
                result.failed += 1
                log.exception("agenda check for druk %s failed: %s", bill.number, exc)
        return True

    def _retract_gone(
        self, bill: Bill, items: tuple[AgendaItem, ...], listings: _Listings, result: TrackingResult
    ) -> None:
        """Take back a sitting the channel announced that is not on the agenda any more.

        Only a `sitting_key` that has disappeared is a retraction: one that merely moved keeps its
        key and is told as a new post saying where it moved from. A sitting that has started or
        passed is never retracted (`_already_happened`), nor is one whose listing failed —
        `_Listings.kept` puts those items back before this runs.
        """
        now = self._clock.now().astimezone(self._local_tz)
        keys = {item.sitting_key for item in items}
        for old in bill.agenda:
            if old.sitting_key in keys or _already_happened(old, now):
                continue
            if not self._poster.sent(bill, PublicationKind.AGENDA, ref=old.ref):
                continue  # the reader was never told about this sitting
            if self._poster.posted(bill, PublicationKind.AGENDA_CANCELLED, ref=old.ref):
                continue
            still_meets = listings.announced(old)
            log.info(
                "druk %s: %s is off (%s)",
                bill.number,
                old.ref,
                "the bill left its agenda" if still_meets else "the sitting is not announced",
            )
            result.count_post(
                self._poster.agenda_cancelled(bill, old, still_meets=still_meets),
                "agenda_cancelled",
            )

    def _committee_sittings(
        self, term: int, bills: list[Bill], today: dt.date
    ) -> tuple[dict[str, list[CommitteeSitting]], set[str]]:
        """Upcoming sittings per committee the bills were referred to, and the codes that failed."""
        codes = sorted(
            {
                code
                for bill in bills
                for code in _committee_codes(bill)
                if code != PLENARY_COMMITTEE_CODE
            }
        )
        upcoming: dict[str, list[CommitteeSitting]] = {}
        failed: set[str] = set()
        for code in codes:
            try:
                sittings = self._gateway.list_committee_sittings(term, code)
            except ServiceUnavailableError:
                raise
            except Exception as exc:
                failed.add(code)
                log.warning("sittings of committee %s unavailable: %s", code, exc)
                continue
            upcoming[code] = [
                s for s in sittings if s.date >= today and s.agenda and s.status == PLANNED
            ]
        return upcoming, failed

    def _sejm_sittings(self, term: int, today: dt.date) -> tuple[list[SejmSitting], set[int]]:
        """Sejm sittings that are not over yet, with their agendas (one request each), and the
        numbers whose agenda could not be read — those must not read as a cancellation."""
        current = [
            s
            for s in self._gateway.list_sittings(term)
            if s.number > 0 and s.last_date is not None and s.last_date >= today
        ]
        detailed: list[SejmSitting] = []
        failed: set[int] = set()
        for sitting in sorted(current, key=lambda s: s.number):
            try:
                full = self._gateway.get_sitting(term, sitting.number)
            except ServiceUnavailableError:
                raise
            except Exception as exc:
                failed.add(sitting.number)
                log.warning("agenda of Sejm sitting %d unavailable: %s", sitting.number, exc)
                continue
            if full.agenda:
                detailed.append(
                    full if full.dates else full.model_copy(update={"dates": sitting.dates})
                )
        return detailed, failed

    def _items_for(self, bill: Bill, listings: _Listings) -> tuple[AgendaItem, ...]:
        """Agenda items naming the bill — by its own druk, by a print considered jointly with it,
        or by one of the prints its process produced (`derived_print_numbers`), which is the only
        name a sitting on the Senate's resolution or the President's motion gives it.

        Committees sitting together are listed once each under a `num` of their own, so a bill
        before two of them was announced twice — 328 (bill, day) pairs of term 10. `meeting_key`
        collapses the group, keeping the item already told so that a committee joining later
        cannot move the `ref` and make `_retract_gone` take back a sitting still on.
        """
        numbers = {
            bill.number,
            *bill.summary.prints_considered_jointly,
            *derived_print_numbers(bill.stages),
        }
        items: list[AgendaItem] = []
        codes = _committee_codes(bill)
        told = {old.ref for old in bill.agenda}
        meetings: dict[tuple[dt.date, dt.time | None, str], AgendaItem] = {}
        for code in sorted(codes & listings.committee.keys()):
            for sitting in listings.committee[code]:
                texts = items_mentioning(sitting.agenda, numbers)
                if not texts:
                    continue
                apply = hearing_application(sitting.notes)
                item = AgendaItem.for_committee(
                    sitting,
                    committee_name=self._enricher.committee_name_or_none(bill.term, code),
                    text=" ".join(texts),
                    condition=_condition_on(sitting, numbers),
                    apply_email=apply.email if apply else None,
                    apply_by=apply.deadline if apply else None,
                )
                item = _keep_told_ref(item, told)
                kept = meetings.get(sitting.meeting_key)
                if kept is None or (item.ref in told and kept.ref not in told):
                    meetings[sitting.meeting_key] = item
        items.extend(meetings.values())
        for plenary in listings.sejm:
            texts = items_mentioning(plenary.agenda, numbers)
            first = plenary.first_date
            if not texts or first is None:
                continue
            items.append(AgendaItem.for_sejm(plenary, first=first, text=" ".join(texts)))
        return tuple(items)

    def _post_new(self, bill: Bill, items: tuple[AgendaItem, ...], result: TrackingResult) -> None:
        now = self._clock.now().astimezone(self._local_tz)
        for item in items:
            if _already_happened(item, now):
                continue
            if self._poster.posted(bill, PublicationKind.AGENDA, ref=item.ref):
                continue
            if self._poster.told_jointly(bill, PublicationKind.AGENDA, item.ref):
                self._poster.record(
                    bill, PublicationKind.AGENDA, PublicationStatus.SKIPPED, ref=item.ref
                )
                continue
            fresh = self._repo.get(bill.term, bill.number) or bill
            log.info("druk %s on the agenda: %s", bill.number, item.ref)
            result.count_post(
                self._poster.agenda(fresh, item, self._moved_from(bill, item)), "agenda_posted"
            )

    def _moved_from(self, bill: Bill, item: AgendaItem) -> AgendaItem | None:
        """The same sitting as the channel last announced it, when anything it named has changed.

        The `ref` carries day, hour and room, so a sitting that moves is a new post. The hour and
        the room are in there because they move on their own: of the 886 term-10 sittings whose
        `comments` record a change, 204 say "zmiana godziny", 97 "zmiana sali" and 61 both.
        """
        previous = [
            old
            for old in bill.agenda
            if old.sitting_key == item.sitting_key and old.ref != item.ref
        ]
        return max(previous, key=lambda old: old.date, default=None)


def _condition_on(sitting: CommitteeSitting, numbers: set[str]) -> str | None:
    """What the sitting waits for, but only where the condition reaches this bill.

    Eleven of the 21 conditional notes of term 10 name the points they cover, and on two of them
    the point carries no print of ours (FPB/86, SPC/52): hedging those announcements would be
    exactly as wrong as the fact the others were stated as.
    """
    condition = sitting_condition(sitting.notes)
    if condition is None:
        return None
    return condition.kind if condition_covers(sitting.agenda, numbers, condition.points) else None


def _keep_told_ref(item: AgendaItem, told: set[str]) -> AgendaItem:
    """Keep the `ref` a sitting was announced under when it named only the day
    (`AgendaItem.legacy_ref`, which says why)."""
    legacy = item.legacy_ref
    if item.ref != legacy and legacy in told:
        return item.model_copy(update={"ref": legacy})
    return item


def _already_happened(item: AgendaItem, now: dt.datetime) -> bool:
    """Whether the sitting is behind the reader. The day alone does not tell: a committee meets
    at 08:30 and the evening run would announce it at 22:00, hours after it closed. A sitting the
    API gives no time for (a plenary spans days) keeps the whole of its last day."""
    if item.last_date < now.date():
        return True
    return item.date == now.date() and item.start_time is not None and item.start_time <= now.time()
