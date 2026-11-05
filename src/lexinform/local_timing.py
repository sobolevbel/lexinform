"""Invocation-local loading clocks and optional frozen-input timing enrichment."""

import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from lexinform.models import BatchIntent, LocalTimingWitness

ReadKind = Literal["fresh", "cache_reuse", "mixed"]


@dataclass(frozen=True)
class LocalReadTiming:
    completed_at: datetime | None
    kind: ReadKind | None
    unavailable: bool = False


def _instant(value: datetime) -> int:
    offset = value.utcoffset()
    if offset is None:
        raise ValueError("Local read clocks require a timezone")
    elapsed = value.replace(tzinfo=None) - datetime.min - offset
    return (elapsed.days * 86400 + elapsed.seconds) * 1000000 + elapsed.microseconds


def combine_read_timings(values: Sequence[LocalReadTiming | None]) -> LocalReadTiming | None:
    if any(value is not None and value.unavailable for value in values):
        return LocalReadTiming(None, None, unavailable=True)
    if not values or any(value is None for value in values):
        return None
    known = [value for value in values if value is not None]
    latest = max(
        known,
        key=lambda value: _instant(value.completed_at) if value.completed_at is not None else 0,
    )
    kinds = {value.kind for value in known}
    return LocalReadTiming(latest.completed_at, latest.kind if len(kinds) == 1 else "mixed")


class ReadTimingCapture:
    def __init__(self, now: Callable[[], datetime]) -> None:
        self.now = now
        self.timing: LocalReadTiming | None = None
        self.failed = False

    def observe(self, kind: Literal["fresh", "cache_reuse"]) -> None:
        if self.failed:
            return
        try:
            observed = LocalReadTiming(self.now(), kind)
            _instant(observed.completed_at) if observed.completed_at is not None else None
            self.timing = combine_read_timings([self.timing, observed]) if self.timing else observed
        except Exception:
            self.failed = True
            self.timing = None

    def snapshot(self) -> LocalReadTiming | None:
        return LocalReadTiming(None, None, unavailable=True) if self.failed else self.timing


_READ_TIMING: ContextVar[tuple[object, int, ReadTimingCapture] | None] = ContextVar(
    "local_read_timing", default=None
)


@contextmanager
def optional_read_timing(
    owner: object, now: Callable[[], datetime]
) -> Iterator[ReadTimingCapture | None]:
    try:
        capture = ReadTimingCapture(now)
        token = _READ_TIMING.set((owner, threading.get_ident(), capture))
    except Exception:
        yield None
        return
    try:
        yield capture
    finally:
        try:
            _READ_TIMING.reset(token)
        except Exception:
            capture.failed = True


def observe_read_timing(owner: object, kind: Literal["fresh", "cache_reuse"]) -> None:
    try:
        scope = _READ_TIMING.get()
        if scope is not None and scope[0] is owner and scope[1] == threading.get_ident():
            scope[2].observe(kind)
    except Exception:
        return


def read_timing(capture: ReadTimingCapture | None) -> LocalReadTiming | None:
    return (
        capture.snapshot() if capture is not None else LocalReadTiming(None, None, unavailable=True)
    )


def enrich_local_timing_intent(
    intent: BatchIntent,
    read: LocalReadTiming | None,
    *,
    discovery_at: datetime | None = None,
    requested_at: datetime | None = None,
) -> BatchIntent:
    if read is not None and read.unavailable:
        return intent
    if intent.meta.local_timing_witness is not None:
        return intent
    request = intent.request
    if request.payload_sha256 is None or intent.meta.input_binding is None:
        return intent
    if intent.meta.request_sha256 not in (None, request.payload_sha256):
        raise ValueError("Local timing cannot replace a known frozen request hash")
    witness = LocalTimingWitness(
        purpose=request.call_kind,
        request_sha256=request.payload_sha256,
        input_scope=intent.meta.input_binding,
        trigger_kind="bill_discovered" if discovery_at is not None else "work_requested",
        triggered_at=discovery_at
        if discovery_at is not None
        else requested_at
        if requested_at is not None
        else intent.created_at,
        read_kind=read.kind if read is not None else None,
        read_completed_at=read.completed_at if read is not None else None,
    )
    return BatchIntent.model_validate(
        {
            "request": request,
            "meta": intent.meta.model_copy(
                update={"request_sha256": request.payload_sha256, "local_timing_witness": witness}
            ),
            "provider": intent.provider,
            "state": intent.state,
            "created_at": intent.created_at,
        }
    )
