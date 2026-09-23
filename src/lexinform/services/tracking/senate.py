"""The act in the Senate: which committees took it, their e-mail and the day they meet on it."""

import logging
from zoneinfo import ZoneInfo

from lexinform.errors import ServiceUnavailableError
from lexinform.models import Bill, SenateAct, next_phase
from lexinform.ports import BillRepository, Clock, SejmGateway, SenateGateway
from lexinform.services.tracking.result import TrackingResult

log = logging.getLogger(__name__)


class SenateWatcher:
    """Keeps `bill.senate` current while the act is in the Senate; posts nothing — the card does."""

    def __init__(
        self,
        senate: SenateGateway,
        gateway: SejmGateway,
        repo: BillRepository,
        clock: Clock,
        *,
        local_tz: ZoneInfo,
    ) -> None:
        self._senate = senate
        self._gateway = gateway
        self._repo = repo
        self._clock = clock
        self._local_tz = local_tz

    def check(self, bills: list[Bill], result: TrackingResult) -> None:
        """Every followed act the Senate has before it; an outage stops this watcher alone."""
        today = self._clock.now().astimezone(self._local_tz).date()
        for bill in bills:
            phase = next_phase(bill, today=today)
            if phase is None or phase.key != "senate":
                continue
            try:
                act = self._read(bill)
            except ServiceUnavailableError as exc:
                result.partial_errors.append(f"senate: {exc.describe()}")
                log.error("senate tracking stopped: %s", exc.describe())
                return
            except Exception as exc:
                log.warning(
                    "senate page of %s unread: %s: %s", bill.number, type(exc).__name__, exc
                )
                continue
            if act is None:
                log.info("druk %s: not listed by the Senate yet", bill.number)
            elif act != bill.senate:
                self._repo.save_senate(bill.term, bill.number, act)

    def _read(self, bill: Bill) -> SenateAct | None:
        if bill.senate is not None:
            return self._senate.read_act(bill.senate.url)
        third_reading = bill.last_stage  # the senate phase starts at it: `End` is not a stage
        passed_on = third_reading.date if third_reading else bill.summary.closure_date
        if passed_on is None:
            return None
        title_final = self._gateway.get_process(bill.term, bill.number).title_final
        if not title_final:
            return None
        return self._senate.find_act(title_final, passed_on=passed_on)
