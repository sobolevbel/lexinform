import json
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from lexinform.models import OdtEmittedSpan, OdtNoteBlockBound, OdtNotePrefix, OdtNoteStatus
from lexinform.models.extractions import ExtractedPartSummary, ExtractionResult
from lexinform.models.odt_notes import OdtNoteOrigins
from tests.unit.test_odt_note_origins import body, call, entry, origins
from tests.unit.test_odt_note_status import result, summary


def captured(count: int = 1, identity: str = "n1") -> OdtNoteOrigins:
    return origins(tuple(entry(index, index, identity) for index in range(count)))


def prefix(value: OdtNoteOrigins, index: int = 0) -> OdtNotePrefix:
    return OdtNotePrefix(
        physical_index=index,
        origins=value,
        blocks=(OdtNoteBlockBound(path=(0,), kind="paragraph", chars=len(body().legacy_text)),)
        if value.entries or value.calls
        else (),
    )


def part(stored: OdtNotePrefix, capture: OdtNoteOrigins | None = None) -> ExtractedPartSummary:
    source = capture or stored.origins
    status = OdtNoteStatus(
        known_walk=source.walk_state,
        input_sha256=source.input_sha256,
        body_manifest_sha256=source.body_manifest_sha256,
        retained_manifest_sha256=source.retained_manifest_sha256,
        observed_notes=source.note_count,
        observed_calls=source.call_count,
        retained_notes=len(source.entries),
        retained_calls=len(source.calls),
        coverage=source.coverage,
        contents="prefix_retained",
    )
    return ExtractedPartSummary.model_validate(
        summary().model_copy(
            update={
                "physical_index": stored.physical_index,
                "manifest_sha256": source.body_manifest_sha256,
                "odt_note_status": status,
                "odt_note_prefix": stored,
            }
        )
    )


def test_full_captured_graph_survives_result_json_with_same_capture_hash() -> None:
    source = captured()
    value = result(part(prefix(source)))
    restored = ExtractionResult.model_validate_json(value.model_dump_json())
    assert restored == value and restored.result_id == value.result_id
    stored = restored.selected_parts[0].odt_note_prefix
    assert stored is not None, "the new prospective reader retains the explicit prefix"
    assert stored.origins.retained_manifest_sha256 == source.retained_manifest_sha256
    assert (
        len(json.dumps(stored.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":")))
        < 4096
    )


def test_forward_reference_loses_resolution_when_definition_is_outside_prefix() -> None:
    definitions = (entry(1, 1),)
    reference = call(0, 0, definitions)
    source = origins(definitions, (reference,))
    assert source.calls[0].association == "resolved"
    stored = OdtNoteOrigins.model_validate(
        source.model_copy(
            update={
                "entries": (),
                "calls": (
                    reference.model_copy(update={"association": "unverified", "target_path": None}),
                ),
                "coverage": "partial",
            }
        )
    )
    value = part(prefix(stored), source)
    assert value.odt_note_status is not None, "captured status remains independently known"
    assert value.odt_note_status.coverage == "complete"
    assert value.odt_note_status.retained_manifest_sha256 != stored.retained_manifest_sha256
    assert value.odt_note_prefix is not None, "the smaller actual event prefix is retained"
    assert value.odt_note_prefix.origins.coverage == "partial"
    broken = prefix(stored).model_copy(
        update={"origins": stored.model_copy(update={"calls": (reference,)})}
    )
    with pytest.raises(ValidationError):
        OdtNotePrefix.model_validate(broken)


def test_emitted_range_uses_original_block_bound_instead_of_trimmed_summary_chars() -> None:
    source = origins(
        (
            entry().model_copy(
                update={
                    "emitted": OdtEmittedSpan(start=3, end=10, evidence="observed-legacy-appends")
                }
            ),
        )
    )
    valid = part(prefix(source)).model_copy(update={"text_chars": 1})
    assert ExtractedPartSummary.model_validate(valid) == valid
    with pytest.raises(ValidationError):
        OdtNotePrefix.model_validate(
            prefix(source).model_copy(
                update={"blocks": (OdtNoteBlockBound(path=(0,), kind="paragraph", chars=9),)}
            )
        )


@pytest.mark.parametrize(
    "field,value", [("physical_index", 1), ("sha256", "b" * 64), ("manifest_sha256", "e" * 64)]
)
def test_stored_prefix_requires_the_same_actual_part_and_manifests(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        ExtractedPartSummary.model_validate(
            part(prefix(captured())).model_copy(update={field: value})
        )


@pytest.mark.parametrize(
    "damage",
    ["missing_prefix", "missing_status", "wrong_contents", "capture_hash", "capture_count"],
)
def test_contents_capture_identity_and_counts_are_coherent(damage: str) -> None:
    valid = part(prefix(captured()))
    status = valid.odt_note_status
    assert status is not None, "fixture has original captured status"
    updates: dict[str, object]
    if damage == "missing_prefix":
        updates = {"odt_note_prefix": None}
    elif damage == "missing_status":
        updates = {"odt_note_status": None}
    elif damage == "wrong_contents":
        updates = {"odt_note_status": status.model_copy(update={"contents": "not_retained"})}
    elif damage == "capture_hash":
        updates = {
            "odt_note_status": status.model_copy(update={"retained_manifest_sha256": "f" * 64})
        }
    else:
        updates = {
            "odt_note_status": status.model_copy(
                update={"retained_notes": 0, "coverage": "partial"}
            )
        }
    with pytest.raises(ValidationError):
        ExtractedPartSummary.model_validate(valid.model_copy(update=updates))


@pytest.mark.parametrize(
    "damage",
    ["dict_prefix", "dict_origins", "dict_block", "dict_entry", "negative_count", "negative_bound"],
)
def test_outer_typed_result_revalidates_known_nested_prefix_slots(damage: str) -> None:
    stored = prefix(captured())
    if damage == "dict_prefix":
        broken: object = stored.model_dump()
    elif damage == "dict_origins":
        broken = stored.model_copy(update={"origins": stored.origins.model_dump()})
    elif damage == "dict_block":
        broken = stored.model_copy(update={"blocks": (stored.blocks[0].model_dump(),)})
    elif damage == "dict_entry":
        broken = stored.model_copy(
            update={
                "origins": stored.origins.model_copy(
                    update={"entries": (stored.origins.entries[0].model_dump(),)}
                )
            }
        )
    elif damage == "negative_count":
        broken = stored.model_copy(
            update={"origins": stored.origins.model_copy(update={"note_count": -1})}
        )
    else:
        broken = stored.model_copy(
            update={"blocks": (stored.blocks[0].model_copy(update={"chars": -1}),)}
        )
    valid = result(part(stored))
    damaged = valid.model_copy(
        update={
            "selected_parts": (
                valid.selected_parts[0].model_copy(update={"odt_note_prefix": broken}),
            )
        }
    )
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(damaged)


@pytest.mark.parametrize("iterator", [False, True])
def test_aggregate_cap_runs_before_outer_serialization_even_when_each_prefix_fits(
    iterator: bool,
) -> None:
    source = captured(5, "é" * 25)
    one = part(prefix(source))
    two = part(prefix(source, 1))
    stored = one.odt_note_prefix
    assert stored is not None, "fixture retains a known typed origin prefix"
    size = len(json.dumps(stored.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":")))
    assert 2048 < size < 4096
    raw = result(one).model_dump()
    raw.update(
        physical_part_count=2,
        selected_part_count=2,
        selected_parts=[one.model_dump(), two.model_dump()],
    )
    if iterator:
        raw["selected_parts"] = iter(raw["selected_parts"])
    with (
        patch.object(
            ExtractionResult,
            "model_dump_json",
            side_effect=RuntimeError("serialization must not start"),
        ),
        pytest.raises(ValidationError, match="aggregate"),
    ):
        ExtractionResult.model_validate(raw)


@pytest.mark.parametrize("damage", ["events", "path", "string"])
def test_oversized_raw_prefix_refused_before_prefix_serialization(damage: str) -> None:
    raw = prefix(captured()).model_dump()
    origins_raw = raw["origins"]
    if damage == "events":
        origins_raw["entries"] = [origins_raw["entries"][0]] * 10000
    elif damage == "path":
        origins_raw["entries"][0]["path"] = [0] * 10000
    else:
        origins_raw["entries"][0]["identity"] = "x" * 100000
    with (
        patch.object(
            OdtNotePrefix,
            "model_dump_json",
            side_effect=RuntimeError("serialization must not start"),
        ),
        pytest.raises(ValidationError),
    ):
        OdtNotePrefix.model_validate(raw)


def test_prefix_absence_keeps_old_defaults_and_explicit_none_identical() -> None:
    value = result(summary())
    assert "odt_note_prefix" not in value.model_dump_json()
    explicit = result(summary().model_copy(update={"odt_note_prefix": None}))
    assert value.model_dump_json() == explicit.model_dump_json()
    assert value.result_id == explicit.result_id
