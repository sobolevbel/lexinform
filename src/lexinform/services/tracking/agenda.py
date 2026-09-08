"""Upcoming sittings that name a followed bill: the dated "what happens next".

Committee sittings come from `/committees/{code}/sittings` (one request per committee a followed
bill was referred to), Sejm sittings from `/proceedings` plus one `/proceedings/{n}` per sitting
that is not over yet. A bill's upcoming items are stored on the bill (so cards and updates can
say "II чтение — 15–18.09.2026") and every new (bill, sitting) pair is posted once.
"""

from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from lexinform.agenda import items_mentioning
from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    AgendaItem,
    Bill,
    CommitteeSitting,
    PublicationKind,
    SejmSitting,
    flatten_stages,
)
from lexinform.ports import BillRepository, Clock, SejmGateway
from lexinform.services.tracking.posting import Poster
from lexinform.services.tracking.result import TrackingResult
from lexinform.services.tracking.stages import StageEnricher

log = logging.getLogger(__name__)

NO_COMMITTEE = "Sejm"  # `committeeCode` of a referral to a reading at a sitting


class AgendaWatcher:
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

    def check(self, term: int, bills: list[Bill], result: TrackingResult, *, publish: bool) -> bool:
        """Refresh the upcoming sittings of every bill and post the new ones. False on an outage."""
        followed = [b for b in bills if not b.is_pre_print]
        if not followed:
            return True
        today = self._clock.now().astimezone(self._local_tz).date()
        try:
            committee_sittings, failed_codes = self._committee_sittings(term, followed, today)
            sejm_sittings = self._sejm_sittings(term, today)
        except ServiceUnavailableError as exc:
            result.abort(exc)
            return False
        for bill in followed:
            try:
                items = self._items_for(term, bill, committee_sittings, sejm_sittings)
                # A committee whose listing failed keeps what we knew about it.
                items += tuple(
                    old
                    for old in bill.agenda
                    if old.kind == "committee"
                    and old.committee_code in failed_codes
                    and old.date >= today
                )
                items = tuple(sorted(items, key=lambda i: (i.date, i.ref)))
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

    # ------------------------------------------------------------------ network

    def _committee_sittings(
        self, term: int, bills: list[Bill], today: dt.date
    ) -> tuple[dict[str, list[CommitteeSitting]], set[str]]:
        codes = sorted(
            {
                st.committee_code
                for bill in bills
                for st in flatten_stages(bill.stages)
                if st.stage_type == "Referral"
                and st.committee_code
                and st.committee_code != NO_COMMITTEE
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
            upcoming[code] = [s for s in sittings if s.date >= today and s.agenda]
        return upcoming, failed

    def _sejm_sittings(self, term: int, today: dt.date) -> list[SejmSitting]:
        current = [
            s
            for s in self._gateway.list_sittings(term)
            if s.number > 0 and s.last_date is not None and s.last_date >= today
        ]
        detailed: list[SejmSitting] = []
        for sitting in sorted(current, key=lambda s: s.number):
            try:
                full = self._gateway.get_sitting(term, sitting.number)
            except ServiceUnavailableError:
                raise
            except Exception as exc:
                log.warning("agenda of Sejm sitting %d unavailable: %s", sitting.number, exc)
                continue
            if full.agenda:
                detailed.append(
                    full if full.dates else full.model_copy(update={"dates": sitting.dates})
                )
        return detailed

    # ------------------------------------------------------------------ matching

    def _items_for(
        self,
        term: int,
        bill: Bill,
        committee_sittings: dict[str, list[CommitteeSitting]],
        sejm_sittings: list[SejmSitting],
    ) -> tuple[AgendaItem, ...]:
        numbers = {bill.number, *bill.summary.prints_considered_jointly}
        items: list[AgendaItem] = []
        codes = {
            st.committee_code
            for st in flatten_stages(bill.stages)
            if st.stage_type == "Referral" and st.committee_code
        }
        for code in sorted(codes & committee_sittings.keys()):
            for sitting in committee_sittings[code]:
                texts = items_mentioning(sitting.agenda, numbers)
                if not texts:
                    continue
                items.append(
                    AgendaItem(
                        kind="committee",
                        ref=f"{code}/{sitting.num}/{sitting.date.isoformat()}",
                        date=sitting.date,
                        start_time=sitting.start_time,
                        committee_code=code,
                        committee_name=self._committee_name(term, code),
                        sitting_number=sitting.num,
                        room=sitting.room,
                        text=" ".join(texts),
                        video_url=sitting.video_url,
                    )
                )
        for plenary in sejm_sittings:
            texts = items_mentioning(plenary.agenda, numbers)
            first, last = plenary.first_date, plenary.last_date
            if not texts or first is None:
                continue
            items.append(
                AgendaItem(
                    kind="sejm",
                    ref=f"sejm/{plenary.number}/{first.isoformat()}",
                    date=first,
                    end_date=last,
                    sitting_number=plenary.number,
                    text=" ".join(texts),
                )
            )
        return tuple(items)

    def _committee_name(self, term: int, code: str) -> str | None:
        try:
            return self._enricher.committee_name(term, code)
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.warning("name of committee %s unavailable: %s", code, exc)
            return None

    # ------------------------------------------------------------------ posting

    def _post_new(self, bill: Bill, items: tuple[AgendaItem, ...], result: TrackingResult) -> None:
        for item in items:
            if self._poster.posted(bill, PublicationKind.AGENDA, ref=item.ref):
                continue
            fresh = self._repo.get(bill.term, bill.number) or bill
            log.info("druk %s on the agenda: %s", bill.number, item.ref)
            if self._poster.one_off(fresh, PublicationKind.AGENDA, agenda=item):
                result.agenda_posted += 1
            else:
                result.failed += 1
