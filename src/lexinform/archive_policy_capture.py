"""Invocation-local policy facts never enter extraction-cache or public document payloads."""

import datetime as dt
import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

from lexinform.archive_dispositions import MAX_ARCHIVE_VALIDATION_BYTES
from lexinform.models.candidate_trace import ArchiveMemberPolicyReason

MAX_POLICY_CAPTURE_PARTS = MAX_ARCHIVE_VALIDATION_BYTES // 256

PolicyOutcome = Literal["selected", "policy_excluded", "non_bill", "unclassified"]


@dataclass(frozen=True)
class ArchiveVersionChoice:
    peer_index: int
    selected_first: bool
    date_filter: tuple[dt.date, dt.date] | None


@dataclass(frozen=True)
class ArchivePolicyDecision:
    index: int
    outcome: PolicyOutcome
    reason: ArchiveMemberPolicyReason
    leaf_sha256: str | None
    text_sha256: str
    version_choice: ArchiveVersionChoice | None = None
    compared_in_conflict: bool = False


_CURRENT: ContextVar[ArchivePolicyCapture | None] = ContextVar(
    "archive_policy_capture", default=None
)


class ArchivePolicyCapture:
    def __init__(self) -> None:
        self._decisions: list[ArchivePolicyDecision] = []
        self._bytes = 2
        self._expected: int | None = None
        self._failed = False
        self._thread: int | None = None

    @contextmanager
    def scope(self) -> Iterator[None]:
        token = _CURRENT.set(self)
        self._thread = threading.get_ident()
        try:
            yield
        finally:
            self._thread = None
            _CURRENT.reset(token)

    def native(self, expected: int) -> bool:
        self._expected = expected
        if expected > MAX_POLICY_CAPTURE_PARTS:
            self._failed = True
        return not self._failed

    def record(self, decision: ArchivePolicyDecision) -> None:
        if self._failed:
            return
        size = len(
            json.dumps(
                (
                    decision.index,
                    decision.outcome,
                    decision.reason,
                    decision.leaf_sha256,
                    decision.text_sha256,
                ),
                separators=(",", ":"),
            ).encode()
        ) + bool(self._decisions)
        if self._bytes + size > MAX_ARCHIVE_VALIDATION_BYTES:
            self._failed = True
            self._decisions.clear()
            return
        self._bytes += size
        self._decisions.append(decision)

    @property
    def decisions(self) -> tuple[ArchivePolicyDecision, ...] | None:
        return (
            tuple(self._decisions)
            if self._expected == len(self._decisions) and not self._failed
            else None
        )


def current_archive_policy_capture() -> ArchivePolicyCapture | None:
    capture = _CURRENT.get()
    return capture if capture is not None and capture._thread == threading.get_ident() else None


@contextmanager
def optional_archive_policy_capture() -> Iterator[ArchivePolicyCapture | None]:
    try:
        capture = ArchivePolicyCapture()
        manager = capture.scope()
        manager.__enter__()
    except Exception:
        yield None
        return
    error: BaseException | None = None
    try:
        yield capture
    except BaseException as caught:
        error = caught
        raise
    finally:
        with suppress(Exception):
            manager.__exit__(
                type(error) if error is not None else None,
                error,
                error.__traceback__ if error is not None else None,
            )


def native_archive_policy_sink(expected: int) -> Callable[[ArchivePolicyDecision], None] | None:
    try:
        capture = current_archive_policy_capture()
        if capture is not None and capture.native(expected):
            return capture.record
    except Exception:
        pass
    return None
