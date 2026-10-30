"""Resolve paid primary references without reconstructing input or application permission."""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import JsonValue

from lexinform.json_storage import canonical_json, read_json
from lexinform.models.analysis import AnalysisRecord
from lexinform.models.computations import ComputationResult, ResultApplication
from lexinform.models.primary_projections import (
    PRIMARY_OBSERVATION_FIELDS,
    PRIMARY_USAGE_FIELDS,
    PrimaryProjectionReference,
)


class PrimaryProjectionError(ValueError):
    pass


@dataclass(frozen=True)
class ProjectionOwner:
    term: int
    number: str


@dataclass(frozen=True)
class PrimaryProjectionContext:
    owner: ProjectionOwner
    source_aliases: tuple[ProjectionOwner, ...] = ()
    role: Literal["current", "ready", "memo"] = "current"
    generation: int | None = None


@dataclass(frozen=True)
class PrimaryProjectionSource:
    result_json: str
    applications: tuple[ResultApplication, ...] = ()


PrimarySourceReader = Callable[[str], PrimaryProjectionSource | None]


def retained_record_sha256(result_json: str) -> str:
    value = read_json(result_json)
    if not isinstance(value, dict) or not isinstance(value.get("record"), dict):
        raise PrimaryProjectionError("a primary reference requires an immutable record object")
    return hashlib.sha256(canonical_json(value["record"]).encode()).hexdigest()


def has_primary_application(
    applications: tuple[ResultApplication, ...], result_id: str, owner: ProjectionOwner
) -> bool:
    return any(
        item.result_id == result_id
        and (item.term, item.number) == (owner.term, owner.number)
        and item.target is None
        and item.generation is not None
        and item.phase == "projection"
        and item.outcome == "applied"
        for item in applications
    )


def resolve_primary_projection(
    value: JsonValue, read: PrimarySourceReader, context: PrimaryProjectionContext
) -> AnalysisRecord:
    try:
        reference = PrimaryProjectionReference.model_validate(value)
        source = read(reference.modern_primary_ref)
        if source is None:
            raise PrimaryProjectionError("missing immutable primary result")
        result = ComputationResult.model_validate_json(source.result_json)
        if (
            result.validation != "validated"
            or result.purpose not in ("analysis", "reanalysis")
            or not isinstance(result.record, AnalysisRecord)
            or result.result_id != reference.modern_primary_ref
            or result.request_sha256 != reference.request_sha256
            or result.schema_version != reference.result_schema_version
            or retained_record_sha256(source.result_json) != reference.retained_record_sha256
        ):
            raise PrimaryProjectionError("primary result identity or retained record mismatch")
        owner = ProjectionOwner(result.term, result.number)
        if context.role == "ready" and (
            context.generation is None or context.generation != result.generation
        ):
            raise PrimaryProjectionError(
                "ready primary reference has a stale or unknown generation"
            )
        if owner != context.owner:
            if context.role == "ready":
                permitted = any(
                    item.result_id == result.result_id
                    and ProjectionOwner(item.term, item.number) in context.source_aliases
                    and item.target is None
                    and item.generation == result.generation
                    and item.phase == "collection"
                    and item.outcome == "ready"
                    for item in source.applications
                )
            else:
                permitted = any(
                    has_primary_application(source.applications, result.result_id, candidate)
                    for candidate in (context.owner, *context.source_aliases)
                )
            if not permitted:
                raise PrimaryProjectionError("foreign primary result has no recorded owner bridge")
        raw = read_json(source.result_json)
        assert isinstance(raw, dict) and isinstance(raw["record"], dict), (
            "retained_record_sha256 validates the immutable saved record object"
        )
        record = dict(raw["record"])
        record.update(reference.observation)
        projected = AnalysisRecord.model_validate(record)
        if not _accounting_matches(projected, result.record):
            raise PrimaryProjectionError(
                "primary accounting must retain paid values or known reuse"
            )
        return projected
    except (ValueError, TypeError, OverflowError, RecursionError) as error:
        if isinstance(error, PrimaryProjectionError):
            raise
        raise PrimaryProjectionError("invalid primary projection") from error


def read_primary_json(
    raw: str,
    read: PrimarySourceReader,
    context: PrimaryProjectionContext,
) -> str:
    if "modern_primary_ref" not in raw and "\\u" not in raw:
        return raw
    value = read_json(raw)
    if not isinstance(value, dict):
        return raw
    ready = context.role == "ready"
    candidate = value.get("record") if ready else value
    if not isinstance(candidate, dict) or "modern_primary_ref" not in candidate:
        return raw
    record = resolve_primary_projection(candidate, read, context).model_dump(
        mode="json", exclude_unset=True
    )
    return canonical_json({**value, "record": record} if ready else record)


def primary_record_matches(record: AnalysisRecord, result: ComputationResult | None) -> bool:
    return (
        result is not None
        and result.validation == "validated"
        and result.purpose in ("analysis", "reanalysis")
        and isinstance(result.record, AnalysisRecord)
        and record.computation_id == result.result_id
        and record.request_sha256 is not None
        and record.request_sha256 == result.request_sha256
        and _accounting_matches(record, result.record)
        and record.model_dump(exclude=set(PRIMARY_OBSERVATION_FIELDS))
        == result.record.model_dump(exclude=set(PRIMARY_OBSERVATION_FIELDS))
    )


def _accounting_matches(record: AnalysisRecord, paid: AnalysisRecord) -> bool:
    observed = tuple(getattr(record, key) for key in PRIMARY_USAGE_FIELDS)
    return observed == tuple(getattr(paid, key) for key in PRIMARY_USAGE_FIELDS) or observed == (
        0,
        0,
        0,
        0,
    )
