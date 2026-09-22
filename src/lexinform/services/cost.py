"""What a run has spent on the model, and whether it may spend more.

Every phase that asks the model goes through one `AnalysisService` and therefore through one
ledger, so this is the whole run's bill however it was reached — the analysis phase, a
re-analysis during tracking, an amendments summary, a digest of a filed document. The run report
takes `calls` from it: one tokens figure for a whole run cannot be accounted for afterwards, and
the phases do not spend evenly.
"""

import threading

from lexinform.models import CallKind, LlmCall, UsageRecord
from lexinform.pricing import cost_usd, format_usd


class CostLedger:
    """The run's model spend, its per-run limit, and the note that says the limit held.

    Charged from the worker threads of `fan_out`, so the total and the list of calls are locked.
    A limit of 0 switches the guard off, which is what `Settings` means by an empty limit.
    """

    def __init__(self, *, max_run_usd: float = 0.0) -> None:
        self._max_run_usd = max_run_usd
        self._lock = threading.Lock()
        self._spent = 0.0
        self._reserved = 0.0
        self._calls: list[LlmCall] = []
        self._stopped: str | None = None

    def start_run(self) -> None:
        """A run is one budget. In production the container is built once per process, so the
        counter would be run-scoped anyway; a test drives several runs through one container."""
        with self._lock:
            self._spent = 0.0
            self._reserved = 0.0
            self._calls.clear()
            self._stopped = None

    def charge(
        self, record: UsageRecord | None, *, number: str, kind: CallKind, batched: bool = False
    ) -> None:
        """Count what one model call cost towards the run's budget, and write down which call it
        was — `calls` is what answers "where did the money go" without reading the logs."""
        if record is None:
            return
        call = LlmCall(
            number=number,
            kind=kind,
            model=record.model,
            input_tokens=record.input_tokens or 0,
            output_tokens=record.output_tokens or 0,
            cache_read_input_tokens=record.cache_read_input_tokens or 0,
            cache_creation_input_tokens=record.cache_creation_input_tokens or 0,
            batched=batched,
        )
        spent = cost_usd(call.usage)
        with self._lock:
            self._calls.append(call)
            if spent is not None:
                self._spent += spent

    @property
    def spent_usd(self) -> float:
        """What the model has cost this run, over every phase that asks it something."""
        return self._spent

    def reserve(self, estimate_usd: float) -> bool:
        with self._lock:
            committed = self._spent + self._reserved
            if self._max_run_usd and committed >= self._max_run_usd:
                return False
            self._reserved += estimate_usd
            return True

    @property
    def calls(self) -> list[LlmCall]:
        """Every model call this run has made, in the order they were charged."""
        with self._lock:
            return list(self._calls)

    @property
    def exhausted(self) -> bool:
        return bool(self._max_run_usd) and self._spent + self._reserved >= self._max_run_usd

    @property
    def over_budget(self) -> str:
        """`run cost limit reached (≈$0.002 ≥ $0.001)`: how every message about the run's budget
        opens. What waits for the next run differs by phase, so the caller adds it."""
        return (
            f"run cost limit reached (≈{format_usd(self._spent + self._reserved)}"
            f" ≥ {format_usd(self._max_run_usd)})"
        )

    @property
    def stopped(self) -> str | None:
        """Set once the per-run limit has held work back, for the run report to say so. The
        analysis phase says it for itself (`AnalysisResult.stopped`); this is for the work that
        has no phase of its own — a re-analysis during tracking."""
        return self._stopped

    def stop(self, waiting: str) -> None:
        self._stopped = f"{self.over_budget}; {waiting}"
