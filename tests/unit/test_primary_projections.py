import json
from collections.abc import Callable

import pytest
from pydantic import JsonValue

from lexinform.json_storage import canonical_json, read_json
from lexinform.models import AnalysisRecord, ComputationResult
from lexinform.primary_projections import (
    PrimaryProjectionContext,
    PrimaryProjectionError,
    PrimaryProjectionSource,
    ProjectionOwner,
    primary_record_matches,
    read_primary_json,
    resolve_primary_projection,
    retained_record_sha256,
)
from tests.computations import retained_result
from tests.primary_projections import receipt, reference

OWNER = ProjectionOwner(10, "3039")


def test_observation_view_can_differ_from_paid_input_without_mutating_it() -> None:
    result = retained_result("analysis")
    original = result.model_dump_json()
    ref = reference(result).model_copy(
        update={
            "observation": {
                "source_url": "https://example.test/current.pdf",
                "source_kind": "government_position",
                "source_checked_at": "2026-10-30T12:00:00Z",
                "text_sha256": "new-current-text",
                "law_sha256": "law-v2:current",
                "selection": {"reason": "current-observation"},
                "revision": 9,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            }
        }
    )
    loaded = resolve_primary_projection(
        ref.model_dump(mode="json"),
        lambda _: PrimaryProjectionSource(original),
        PrimaryProjectionContext(OWNER),
    )
    assert isinstance(result.record, AnalysisRecord), "the fixture is a primary paid record"
    assert (
        loaded.analysis == result.record.analysis
        and loaded.input_chars == result.record.input_chars
    )
    assert loaded.source_url == "https://example.test/current.pdf" and loaded.revision == 9
    assert loaded.text_sha256 == "new-current-text" and loaded.input_tokens == 0
    assert loaded.output_tokens == 0 and loaded.source_checked_at is not None
    assert primary_record_matches(loaded, result)
    assert result.model_dump_json() == original


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value.update(request_sha256="b" * 64),
        lambda value: value.update(retained_record_sha256="b" * 64),
        lambda value: value.update(result_schema_version=2),
        lambda value: value.update(version=2),
        lambda value: value.update(observation={"model": "forged-model"}),
        lambda value: value.update(observation={"input_chars": 1}),
        lambda value: value.update(observation={"analysis": {}}),
        lambda value: value.update(observation={"revision": "invalid"}),
        lambda value: value.update(observation={"input_tokens": 100}),
        lambda value: value.update(observation={"input_tokens": 0}),
        lambda value: value.update(observation={"output_tokens": None}),
    ],
)
def test_reserved_reference_rejects_uncorroborated_identity_or_paid_fact_changes(
    change: Callable[[dict[str, JsonValue]], None],
) -> None:
    result = retained_result("analysis")
    value = reference(result).model_dump(mode="json")
    change(value)
    with pytest.raises(PrimaryProjectionError):
        resolve_primary_projection(
            value,
            lambda _: PrimaryProjectionSource(result.model_dump_json()),
            PrimaryProjectionContext(OWNER),
        )


def test_foreign_first_write_without_alias_or_receipt_is_not_reader_eligible() -> None:
    result = retained_result("analysis")
    ref = reference(result)
    with pytest.raises(PrimaryProjectionError, match="owner bridge"):
        resolve_primary_projection(
            ref.model_dump(mode="json"),
            lambda _: PrimaryProjectionSource(result.model_dump_json()),
            PrimaryProjectionContext(ProjectionOwner(11, OWNER.number)),
        )


def test_explicit_alias_needs_source_receipt_and_repeated_transfer_uses_known_receipt() -> None:
    result = retained_result("analysis")
    ref = reference(result).model_dump(mode="json")
    target = ProjectionOwner(11, "11")
    source = PrimaryProjectionSource(result.model_dump_json(), (receipt(result, OWNER),))
    context = PrimaryProjectionContext(target, (OWNER,))
    assert resolve_primary_projection(ref, lambda _: source, context) == result.record
    with pytest.raises(PrimaryProjectionError):
        resolve_primary_projection(
            ref,
            lambda _: PrimaryProjectionSource(source.result_json),
            context,
        )
    repeated = PrimaryProjectionContext(ProjectionOwner(12, "11"), (target,))
    source = PrimaryProjectionSource(result.model_dump_json(), (receipt(result, target),))
    assert resolve_primary_projection(ref, lambda _: source, repeated) == result.record


def test_missing_failed_or_changed_retained_record_does_not_fall_back_to_live_state() -> None:
    result = retained_result("analysis")
    ref = reference(result).model_dump(mode="json")
    raw = read_json(result.model_dump_json())
    assert isinstance(raw, dict), "the paid fixture serializes as an object"
    raw.update(validation="error", record=None, error="invalid response")
    with pytest.raises(PrimaryProjectionError):
        resolve_primary_projection(ref, lambda _: None, PrimaryProjectionContext(OWNER))
    failed = PrimaryProjectionSource(canonical_json(raw))
    with pytest.raises(PrimaryProjectionError):
        resolve_primary_projection(ref, lambda _: failed, PrimaryProjectionContext(OWNER))
    raw = read_json(result.model_dump_json())
    assert isinstance(raw, dict) and isinstance(raw["record"], dict), "the fixture has a record"
    raw["record"]["model"] = "changed"
    with pytest.raises(PrimaryProjectionError):
        resolve_primary_projection(
            ref,
            lambda _: PrimaryProjectionSource(canonical_json(raw)),
            PrimaryProjectionContext(OWNER),
        )


def test_inline_and_opaque_strings_are_unchanged_while_ready_has_one_admitted_position() -> None:
    result = retained_result("analysis")
    context = PrimaryProjectionContext(OWNER)
    inline = result.record.model_dump_json() if result.record is not None else "{}"
    assert read_primary_json(inline, lambda _: None, context) == inline
    opaque = json.dumps({"analysis": {"summary": "modern_primary_ref"}})
    assert read_primary_json(opaque, lambda _: None, context) == opaque
    ready: dict[str, JsonValue] = {
        "record": reference(result).model_dump(mode="json"),
        "text": "frozen text",
    }
    loaded = read_json(
        read_primary_json(
            canonical_json(ready),
            lambda _: PrimaryProjectionSource(result.model_dump_json()),
            PrimaryProjectionContext(OWNER, role="ready", generation=result.generation),
        )
    )
    assert isinstance(loaded, dict) and loaded["text"] == "frozen text"
    assert result.record is not None, "the paid fixture retains its primary record"
    assert loaded["record"] == result.record.model_dump(mode="json")


def test_retained_raw_shape_and_unknown_usage_survive_current_model_defaults() -> None:
    result = retained_result("analysis")
    raw = read_json(result.model_dump_json())
    assert isinstance(raw, dict) and isinstance(raw["record"], dict), "the fixture has a record"
    raw["record"].pop("source_url")
    raw["record"].pop("selection")
    raw["record"]["output_tokens"] = None
    original = canonical_json(raw)
    stored = ComputationResult.model_validate_json(original)
    ref = reference(stored).model_copy(
        update={
            "retained_record_sha256": retained_record_sha256(original),
        }
    )
    materialized = read_primary_json(
        ref.model_dump_json(),
        lambda _: PrimaryProjectionSource(original),
        PrimaryProjectionContext(OWNER),
    )
    loaded = AnalysisRecord.model_validate_json(materialized)
    assert loaded == stored.record and loaded.output_tokens is None
    assert stored.record is not None, "the saved primary source retains its validated record"
    assert loaded.model_fields_set == stored.record.model_fields_set


def test_modified_answer_projection_cannot_claim_an_unchanged_paid_body() -> None:
    result = retained_result("analysis")
    assert isinstance(result.record, AnalysisRecord), "the fixture has its primary paid record"
    changed = result.record.model_copy(
        update={
            "analysis": result.record.analysis.model_copy(
                update={"changes_since_previous": ["changed"]}
            )
        }
    )
    assert not primary_record_matches(changed, result)
