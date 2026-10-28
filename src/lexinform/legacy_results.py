"""Read latent legacy projections while preserving immutable originals and opaque strings."""

import hashlib
import json
import re
from collections.abc import Callable

from pydantic import JsonValue

from lexinform.models.legacy_results import (
    LEGACY_ANSWER_FIELDS,
    LEGACY_REFERENCE_FIELDS,
    LegacyResultValue,
    canonical_legacy_json,
    has_legacy_marker,
    legacy_json,
)

LegacyValueReader = Callable[[str], LegacyResultValue | None]
LEGACY_PROJECTION_COLUMNS = frozenset(
    {
        "analysis_json",
        "ready_analysis_json",
        "joint_json",
        "supplements_json",
        "consultation_review_json",
        "commission_review_json",
        "act_application_review_json",
        "review_work_json",
        "amendments_json",
        "delivery_json",
        "record_json",
        "work_json",
        "meta_json",
    }
)
_VALUE_ID = re.compile(r"legacy-value-v1:[0-9a-f]{64}")
_BODY_SHA = re.compile(r"[0-9a-f]{64}")
_EDITORIAL_UPCAST = "analysis-editorial-v1"
_EDITORIAL_DEFAULTS = (
    ("mnemonic_candidate", ""),
    ("mnemonic_fallback", ""),
    ("mnemonic_basis", ""),
    ("mnemonic_language", "ru"),
)
_PROJECTION_FIELDS = frozenset(
    {
        "source_checked_at",
        "source_url",
        "source_kind",
        "selection",
        "text_sha256",
        "law_sha256",
        "revision",
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    }
)


def legacy_analysis_body(body_json: str, body_sha256: str) -> dict[str, JsonValue]:
    if (
        _BODY_SHA.fullmatch(body_sha256) is None
        or hashlib.sha256(body_json.encode()).hexdigest() != body_sha256
    ):
        raise ValueError("immutable legacy analysis body checksum mismatch")
    body = legacy_json(body_json)
    if not isinstance(body, dict) or canonical_legacy_json(body) != body_json:
        raise ValueError("a retained analysis body requires canonical exact object JSON")
    if has_legacy_marker(body):
        raise ValueError("an immutable analysis body cannot contain projection references")
    return body


def materialize_legacy_value(
    envelope_json: str, *, body_sha256: str | None = None, body_json: str | None = None
) -> LegacyResultValue:
    if body_sha256 is None:
        if body_json is not None:
            raise ValueError("an inline legacy value cannot claim an external body")
        return LegacyResultValue(value_json=envelope_json)
    if body_json is None:
        raise ValueError("missing immutable legacy analysis body")
    envelope = legacy_json(envelope_json)
    if (
        not isinstance(envelope, dict)
        or "analysis" in envelope
        or canonical_legacy_json(envelope) != envelope_json
    ):
        raise ValueError("a shared analysis envelope must omit exactly its external answer member")
    value = {**envelope, "analysis": legacy_analysis_body(body_json, body_sha256)}
    return LegacyResultValue(value_json=canonical_legacy_json(value))


def _original(identifier: JsonValue, read: LegacyValueReader) -> dict[str, JsonValue]:
    if not isinstance(identifier, str) or _VALUE_ID.fullmatch(identifier) is None:
        raise ValueError("invalid legacy result reference")
    record = read(identifier)
    if record is None:
        raise ValueError("missing immutable legacy value")
    if record.value_id != identifier:
        raise ValueError("immutable legacy value checksum mismatch")
    value = legacy_json(record.value_json)
    assert isinstance(value, dict), "LegacyResultValue validates an exact saved record object"
    return value


def _upcast_analysis(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    answer = value.get("analysis")
    if not isinstance(answer, dict):
        raise ValueError("the editorial upcast requires a saved analysis object")
    current = dict(answer)
    for key, default in _EDITORIAL_DEFAULTS:
        if key not in current:
            current[key] = default
    return {**value, "analysis": current}


def _projection(value: dict[str, JsonValue], read: LegacyValueReader) -> dict[str, JsonValue]:
    if "legacy_result_ref" not in value or not set(value) <= LEGACY_REFERENCE_FIELDS:
        raise ValueError("invalid reserved legacy projection shape")
    original = _original(value["legacy_result_ref"], read)
    if "projection_upcast" in value:
        if value["projection_upcast"] != _EDITORIAL_UPCAST:
            raise ValueError("unknown legacy current-schema upcast")
        original = _upcast_analysis(original)
    overrides = value.get("projection_overrides", {})
    removed = value.get("projection_removed", [])
    if not isinstance(overrides, dict) or not set(overrides) <= _PROJECTION_FIELDS:
        raise ValueError("invalid legacy projection metadata overrides")
    if has_legacy_marker(overrides):
        raise ValueError("legacy projection overrides cannot contain references")
    if (
        not isinstance(removed, list)
        or any(not isinstance(key, str) or key not in _PROJECTION_FIELDS for key in removed)
        or len(set(removed)) != len(removed)
        or set(removed).intersection(overrides)
    ):
        raise ValueError("invalid legacy projection metadata removals")
    result = dict(original)
    for key in removed:
        assert isinstance(key, str), "projection metadata removal keys were validated above"
        result.pop(key, None)
    result.update(overrides)
    return result


def hydrate_legacy_value(value: JsonValue, read: LegacyValueReader) -> JsonValue:
    if isinstance(value, dict):
        if LEGACY_REFERENCE_FIELDS.intersection(value):
            return _projection(value, read)
        return {key: hydrate_legacy_value(child, read) for key, child in value.items()}
    if isinstance(value, list):
        return [hydrate_legacy_value(child, read) for child in value]
    return value


def read_legacy_json(raw: str, read: LegacyValueReader) -> str:
    if "\\u" not in raw and not any('"' + key + '"' in raw for key in LEGACY_REFERENCE_FIELDS):
        return raw
    marked = False

    def inspect(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
        nonlocal marked
        marked = marked or bool(LEGACY_REFERENCE_FIELDS.intersection(key for key, _ in pairs))
        return dict(pairs)

    json.loads(raw, object_pairs_hook=inspect)
    if not marked:
        return raw
    value = legacy_json(raw)
    hydrated = hydrate_legacy_value(value, read)
    return json.dumps(hydrated, ensure_ascii=False, separators=(",", ":"))


def legacy_projection(
    value: JsonValue, base_id: str, read: LegacyValueReader
) -> dict[str, JsonValue] | None:
    """Return a lossless direct projection only while immutable record fields still agree."""
    if not isinstance(value, dict) or value.get("computation_id") is not None:
        return None
    if LEGACY_REFERENCE_FIELDS.intersection(value):
        raise ValueError("a projection input must be a materialized saved record")
    base = _original(base_id, read)
    upcast = False
    if isinstance(base.get("analysis"), dict) and canonical_legacy_json(
        value.get("analysis")
    ) != canonical_legacy_json(base.get("analysis")):
        current_schema = _upcast_analysis(base)
        if canonical_legacy_json(current_schema["analysis"]) == canonical_legacy_json(
            value.get("analysis")
        ):
            base = current_schema
            upcast = True
    changed = {
        key
        for key in base.keys() | value.keys()
        if key not in base
        or key not in value
        or canonical_legacy_json(base[key]) != canonical_legacy_json(value[key])
    }
    if not changed <= _PROJECTION_FIELDS or not LEGACY_ANSWER_FIELDS.intersection(base):
        return None
    result: dict[str, JsonValue] = {"legacy_result_ref": base_id}
    if upcast:
        result["projection_upcast"] = _EDITORIAL_UPCAST
    overrides: dict[str, JsonValue] = {key: value[key] for key in sorted(changed) if key in value}
    removed: list[JsonValue] = [key for key in sorted(changed) if key not in value]
    if overrides:
        result["projection_overrides"] = overrides
    if removed:
        result["projection_removed"] = removed
    if canonical_legacy_json(hydrate_legacy_value(result, read)) != canonical_legacy_json(value):
        raise ValueError("legacy projection did not preserve the incoming exact value")
    return result


def preserve_legacy_json(raw: str, previous: str | None, read: LegacyValueReader) -> str:
    """Retain valid existing projections at their saved paths without creating new references."""
    if previous is None or read_legacy_json(previous, read) == previous:
        return raw

    def preserve(value: JsonValue, saved: JsonValue) -> JsonValue:
        if isinstance(saved, dict):
            if LEGACY_REFERENCE_FIELDS.intersection(saved):
                identifier = saved.get("legacy_result_ref")
                assert isinstance(identifier, str), "read_legacy_json validates saved references"
                projection = legacy_projection(value, identifier, read)
                return value if projection is None else projection
            if isinstance(value, dict):
                return {
                    key: preserve(child, saved[key]) if key in saved else child
                    for key, child in value.items()
                }
        if isinstance(saved, list) and isinstance(value, list):
            return [
                preserve(child, saved[index]) if index < len(saved) else child
                for index, child in enumerate(value)
            ]
        return value

    return json.dumps(
        preserve(legacy_json(raw), legacy_json(previous)), ensure_ascii=False, separators=(",", ":")
    )
