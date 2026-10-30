"""Closed physical profile fields stream without copying complete models or large strings."""

import datetime as dt
import hashlib
import json
from collections.abc import Iterator

from pydantic import BaseModel

from lexinform.models.physical_inventory import (
    MAX_PHYSICAL_METADATA_BYTES,
    MAX_PHYSICAL_ROOT_BYTES,
    PhysicalMetadata,
)

_FUTURE_TIME = dt.datetime.max.replace(tzinfo=dt.UTC).isoformat()


def physical_json_chunks(value: object) -> Iterator[bytes]:
    if isinstance(value, BaseModel):
        yield from physical_json_chunks(
            {name: getattr(value, name) for name in type(value).model_fields}
        )
    elif isinstance(value, dict):
        yield b"{"
        for index, key in enumerate(sorted(value)):
            if index:
                yield b","
            yield from physical_json_chunks(key)
            yield b":"
            yield from physical_json_chunks(value[key])
        yield b"}"
    elif isinstance(value, (tuple, list)):
        yield b"["
        for index, item in enumerate(value):
            if index:
                yield b","
            yield from physical_json_chunks(item)
        yield b"]"
    elif isinstance(value, str):
        yield b'"'
        for offset in range(0, len(value), 1024):
            yield json.dumps(value[offset : offset + 1024], ensure_ascii=False)[1:-1].encode()
        yield b'"'
    elif isinstance(value, dt.datetime):
        yield from physical_json_chunks(value.isoformat())
    else:
        yield json.dumps(value, ensure_ascii=False, allow_nan=False).encode()


def physical_encoded_size(value: object, limit: int) -> int | None:
    total = 0
    for chunk in physical_json_chunks(value):
        total += len(chunk)
        if total > limit:
            return None
    return total


def physical_observed_hash(value: object) -> str | None:
    total = 0
    digest = hashlib.sha256()
    for chunk in physical_json_chunks(value):
        total += len(chunk)
        if total > MAX_PHYSICAL_ROOT_BYTES:
            return None
        digest.update(chunk)
    return digest.hexdigest()


def physical_record_fields(record: PhysicalMetadata, *, future: bool = True) -> dict[str, object]:
    values = {name: getattr(record, name) for name in type(record).model_fields}
    if future or record.recorded_at is None:
        values["recorded_at"] = _FUTURE_TIME
    return values


def physical_record_size(record: PhysicalMetadata, *, future: bool = True) -> int | None:
    return physical_encoded_size(
        physical_record_fields(record, future=future), MAX_PHYSICAL_METADATA_BYTES
    )


def validate_physical_record(record: PhysicalMetadata) -> PhysicalMetadata:
    if physical_record_size(record, future=False) is None:
        raise ValueError("supplied physical record exceeds its actual envelope budget")
    return PhysicalMetadata.model_validate_json(record.model_dump_json())
