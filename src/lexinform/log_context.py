"""Bounded correlation metadata follows an operation without changing its persisted payload."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TypedDict, Unpack


class LogFields(TypedDict, total=False):
    run_id: int | None
    command_id: int | None
    bill_term: int | None
    bill_number: str | None
    observation_id: str | None
    artifact_sha256: str | None
    artifact_read_id: str | None
    selection_id: str | None
    request_sha256: str | None
    computation_id: str | None
    application_phase: str | None
    application_generation: int | None
    application_target_kind: str | None
    application_target_ref: str | None
    content_id: str | None
    publication_id: int | None
    delivery_status: str | None
    batch_id: str | None
    custom_id: str | None


_context: ContextVar[LogFields | None] = ContextVar("lexinform_log_context", default=None)


@contextmanager
def log_context(**fields: Unpack[LogFields]) -> Iterator[None]:
    token = _context.set({**(_context.get() or {}), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def log_extra(**fields: Unpack[LogFields]) -> dict[str, object]:
    return {"correlation": {**(_context.get() or {}), **fields}}


def correlation_fields(value: object) -> dict[str, int | str]:
    """Only known scalar identifiers enter the output; oversized opaque values stay in SQL."""
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if key in LogFields.__annotations__
        and (type(item) is int or isinstance(item, str) and 0 < len(item) <= 256)
    }


def current_log_fields() -> dict[str, int | str]:
    return correlation_fields(_context.get())
