"""Bounded header observation stays independent of extraction hooks and persistence."""

import datetime as dt
import hashlib
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Self

from pydantic import TypeAdapter

from lexinform.models.artifacts import ArtifactIdentity
from lexinform.models.physical_inventory import (
    MAX_PHYSICAL_METADATA_BYTES,
    HeaderRow,
    PhysicalHeaders,
    PhysicalMetadata,
    PhysicalOmission,
    PhysicalOmittedCount,
)

_MAX_COUNT = 2**63 - 1
_ROW = TypeAdapter(HeaderRow)
_CURRENT: ContextVar[PhysicalHeaderCapture | None] = ContextVar(
    "physical_header_capture", default=None
)


def _canonical_chunks(value: object) -> Iterator[bytes]:
    if isinstance(value, str):
        yield b'"'
        for offset in range(0, len(value), 1024):
            yield json.dumps(value[offset : offset + 1024], ensure_ascii=False)[1:-1].encode()
        yield b'"'
    elif isinstance(value, dict):
        yield b"{"
        for index, key in enumerate(sorted(value)):
            if index:
                yield b","
            yield from _canonical_chunks(key)
            yield b":"
            yield from _canonical_chunks(value[key])
        yield b"}"
    elif isinstance(value, (tuple, list)):
        yield b"["
        for index, item in enumerate(value):
            if index:
                yield b","
            yield from _canonical_chunks(item)
        yield b"]"
    else:
        yield json.dumps(value, ensure_ascii=False, allow_nan=False).encode()


class PhysicalHeaderBuilder:
    def __init__(self, header: PhysicalHeaders) -> None:
        if header.rows:
            raise ValueError("header observation starts before its first observed row")
        self._header = PhysicalHeaders.model_validate(header)
        self._retained: list[HeaderRow] = []
        self._retained_bytes = 0
        self._base_budget = self._retained_budget(self._header)
        self._count = 0
        self._omitted = 0
        self._closed = False
        self._failed = False
        self._hash = hashlib.sha256()
        payload = header.model_dump(mode="json")
        payload.pop("rows")
        prefix = b"".join(_canonical_chunks(payload))
        before, after = prefix.split(b',"v":', 1)
        self._hash.update(before + b',"rows":[')
        self._suffix = b'],"v":' + after

    def append(self, row: HeaderRow) -> None:
        if self._closed:
            raise ValueError("closed header observation cannot accept more rows")
        if self._count == _MAX_COUNT:
            self._failed = True
            raise ValueError("header observation count exceeds the integer cap")
        try:
            self._append(row)
        except BaseException:
            self._failed = True
            raise

    def _append(self, row: HeaderRow) -> None:
        row = _ROW.validate_python(row, strict=True)
        PhysicalHeaders.model_validate(self._header.model_copy(update={"rows": (row,)}))
        digest = self._hash.copy()
        if self._count:
            digest.update(b",")
        row_bytes = 0
        for chunk in _canonical_chunks(row):
            digest.update(chunk)
            row_bytes += len(chunk)
        retained_bytes = self._retained_bytes + row_bytes + bool(self._retained)
        if not self._omitted and self._base_budget + retained_bytes <= MAX_PHYSICAL_METADATA_BYTES:
            self._retained.append(row)
            self._retained_bytes = retained_bytes
        else:
            self._omitted += 1
        self._hash = digest
        self._count += 1

    @staticmethod
    def _retained_budget(header: PhysicalHeaders) -> int:
        omission = PhysicalOmission(
            reason="missing_metadata",
            full_metadata_sha256="f" * 64,
            omitted=(PhysicalOmittedCount(field="rows", count=_MAX_COUNT),),
            unknown_fields=_MAX_COUNT,
        )
        envelope = {
            "metadata_id": "f" * 64,
            "kind": "headers",
            "scope": "observed",
            "body_sha256": header.body_sha,
            "header_id": None,
            "metadata": header.model_dump(mode="json"),
            "coverage": "partial",
            "omission": omission.model_dump(mode="json"),
            "recorded_at": dt.datetime.max.replace(tzinfo=dt.UTC).isoformat(),
        }
        return sum(len(chunk) for chunk in _canonical_chunks(envelope))

    def finish(self, *, complete: bool) -> PhysicalMetadata:
        if self._closed:
            raise ValueError("header observation can be finalized only once")
        payload = self._header.model_copy(update={"rows": tuple(self._retained)})
        complete = complete and not self._failed
        omission = None
        if self._omitted or not complete:
            digest = self._hash.copy()
            digest.update(self._suffix)
            omission = PhysicalOmission(
                reason="over_limit" if complete else "missing_metadata",
                full_metadata_sha256=digest.hexdigest() if complete else None,
                omitted=(PhysicalOmittedCount(field="rows", count=self._omitted),)
                if self._omitted
                else (),
                unknown_fields=0 if complete else 1,
            )
        record = PhysicalMetadata.of(payload, omission=omission)
        self._closed = True
        return record


@dataclass(frozen=True)
class PhysicalHeaderCaptureFailure:
    artifact: ArtifactIdentity
    failure_class: str


class PhysicalHeaderCapture:
    def __init__(self, artifact: ArtifactIdentity, header: PhysicalHeaders) -> None:
        if artifact.sha256 != header.body_sha:
            raise ValueError("header capture must retain its actual complete input body")
        self.artifact = artifact
        self.builder = PhysicalHeaderBuilder(header)
        self.record: PhysicalMetadata | None = None
        self.failure: PhysicalHeaderCaptureFailure | None = None
        self._thread: int | None = None
        self._entered = False

    @contextmanager
    def scope(self) -> Iterator[Self]:
        if self._entered:
            raise ValueError("a header capture scope belongs to one actual call")
        self._entered = True
        self._thread = threading.get_ident()
        token = _CURRENT.set(self)
        failed = False
        try:
            yield self
        except BaseException:
            failed = True
            raise
        finally:
            self._thread = None
            _CURRENT.reset(token)
            try:
                self.record = self.builder.finish(complete=not failed)
            except BaseException as exc:
                name = type(exc).__name__[:128]
                safe_name = name if name.isascii() and name.isidentifier() else "CaptureError"
                self.failure = PhysicalHeaderCaptureFailure(self.artifact, safe_name)
                if not failed and not isinstance(exc, Exception):
                    raise


def current_physical_header_capture() -> PhysicalHeaderCapture | None:
    current = _CURRENT.get()
    return current if current is not None and current._thread == threading.get_ident() else None
