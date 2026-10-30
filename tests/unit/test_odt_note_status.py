from unittest.mock import patch

import pytest
from pydantic import ValidationError

from lexinform.models import OdtNoteStatus
from lexinform.models import extractions as models
from lexinform.models.extractions import (
    ExtractedPartSummary,
    ExtractionComponent,
    ExtractionOptions,
    ExtractionResult,
)


def summary() -> ExtractedPartSummary:
    return ExtractedPartSummary(
        physical_index=0,
        sha256="a" * 64,
        member_path_sha256="b" * 64,
        text_sha256="c" * 64,
        text_chars=30,
        format="odt",
        coordinates="odt-blocks-v1",
        text_view="legacy-odt-body-v1",
        manifest_sha256="d" * 64,
        block_count=1,
        error_count=0,
        limitations=(
            "odt_notes_unmapped",
            "tracked_changes_unresolved",
            "table_repetitions_unexpanded",
        ),
        loss_count=0,
        losses_sha256="e" * 64,
    )


def status() -> OdtNoteStatus:
    return OdtNoteStatus(
        known_walk="finished",
        input_sha256="a" * 64,
        body_manifest_sha256="d" * 64,
        retained_manifest_sha256="f" * 64,
        observed_notes=2,
        observed_calls=2,
        retained_notes=2,
        retained_calls=2,
        coverage="complete",
    )


def result(part: ExtractedPartSummary) -> ExtractionResult:
    return ExtractionResult(
        artifact_sha256="a" * 64,
        components=(ExtractionComponent(role="package", qualified_name_sha256="b" * 64),),
        dependencies=(),
        options=ExtractionOptions(
            archive_depth=1, archive_bytes=1024, archive_members=10, xml_bytes=1024
        ),
        physical_manifest_sha256="c" * 64,
        output_text_sha256="c" * 64,
        output_chars=30,
        physical_part_count=1,
        selected_part_count=1,
        rejected_count=0,
        rejected_sha256="d" * 64,
        selected_parts=(part,),
        omitted_selected_parts=0,
    )


def test_finished_retained_observations_are_not_saved_coordinates_or_full_xml() -> None:
    value = status()
    assert value.contents == "not_retained" and value.xml_completeness == "not_asserted"
    assert value.origins_version == "odt-note-origins-v1"
    assert OdtNoteStatus.model_validate_json(value.model_dump_json()) == value
    bound = summary().model_copy(update={"odt_note_status": value})
    assert (
        ExtractionResult.model_validate_json(result(bound).model_dump_json())
        .selected_parts[0]
        .odt_note_status
        == value
    )


@pytest.mark.parametrize(
    "walk,observed,retained,coverage",
    [
        ("finished", 4, 2, "partial"),
        ("interrupted", 2, 2, "partial"),
        ("unavailable", 0, 0, "unknown"),
    ],
)
def test_partial_interrupted_and_unavailable_are_explicit(
    walk: str, observed: int, retained: int, coverage: str
) -> None:
    payload = status().model_dump() | {
        "known_walk": walk,
        "observed_notes": observed,
        "observed_calls": observed,
        "retained_notes": retained,
        "retained_calls": retained,
        "coverage": coverage,
    }
    value = OdtNoteStatus.model_validate(payload)
    assert value.coverage == coverage and value.contents == "not_retained"


@pytest.mark.parametrize(
    "change",
    [
        {"retained_notes": 3},
        {"observed_notes": 64, "retained_notes": 64},
        {"coverage": "unknown"},
        {"known_walk": "interrupted"},
        {"known_walk": "unavailable", "coverage": "unknown"},
    ],
)
def test_false_counts_or_completeness_are_rejected(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        OdtNoteStatus.model_validate(status().model_dump() | change)


@pytest.mark.parametrize("invalid", [-1, 2**63, True, 1.5, "1"])
def test_invalid_counter_rejected_before_serialization(invalid: object) -> None:
    with (
        patch.object(
            OdtNoteStatus, "model_dump_json", side_effect=AssertionError("serializer must not run")
        ),
        pytest.raises(ValidationError),
    ):
        OdtNoteStatus.model_validate(status().model_dump() | {"observed_notes": invalid})


def test_maximum_known_counts_and_prefix_stay_compact() -> None:
    value = OdtNoteStatus.model_validate(
        status().model_dump()
        | {
            "observed_notes": 2**63 - 1,
            "observed_calls": 2**63 - 1,
            "retained_notes": 32,
            "retained_calls": 32,
            "coverage": "partial",
        }
    )
    assert len(value.model_dump_json().encode()) <= 1024


@pytest.mark.parametrize(
    "field,value",
    [
        ("origins_version", "future"),
        ("known_walk", "x" * 100_000),
        ("input_sha256", "a" * 100_000),
        ("contents", "retained"),
        ("xml_completeness", "complete"),
        ("xml_sha256", "a" * 64),
        ("entries", [{}] * 1000),
    ],
)
def test_closed_bounded_profile_cannot_claim_unavailable_witnesses(
    field: str, value: object
) -> None:
    with (
        patch.object(
            OdtNoteStatus, "model_dump_json", side_effect=AssertionError("serializer must not run")
        ),
        pytest.raises(ValidationError),
    ):
        OdtNoteStatus.model_validate(status().model_dump() | {field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("sha256", "e" * 64),
        ("manifest_sha256", "e" * 64),
        ("manifest_sha256", None),
        ("format", "word"),
        ("coordinates", "unknown"),
        ("text_view", "unlocated"),
        ("block_count", None),
    ],
)
def test_summary_requires_actual_existing_input_body_and_odt_view(
    field: str, value: object
) -> None:
    part = summary().model_copy(update={field: value, "odt_note_status": status()})
    with pytest.raises(ValidationError):
        ExtractedPartSummary.model_validate(part)


def test_typed_child_corruption_and_untyped_slot_cannot_bypass_parent() -> None:
    with pytest.raises(ValidationError):
        ExtractedPartSummary.model_validate(
            summary().model_copy(
                update={"odt_note_status": status().model_copy(update={"observed_notes": -1})}
            )
        )
    with pytest.raises(ValidationError):
        ExtractedPartSummary.model_validate(
            summary().model_copy(update={"odt_note_status": status().model_dump()})
        )
    raw = summary().model_dump() | {"odt_note_status": status().model_dump()}
    assert ExtractedPartSummary.model_validate(raw).odt_note_status == status()


def test_byte_limit_checks_actual_validated_serialization(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = status().model_dump()
    size = len(status().model_dump_json().encode())
    monkeypatch.setattr(models, "MAX_ODT_NOTE_STATUS_BYTES", size)
    assert OdtNoteStatus.model_validate(payload) == status()
    monkeypatch.setattr(models, "MAX_ODT_NOTE_STATUS_BYTES", size - 1)
    with pytest.raises(ValidationError, match="1024"):
        OdtNoteStatus.model_validate(payload)


def test_absence_preserves_old_json_ids_and_outer_limits() -> None:
    part = summary()
    before = result(part)
    assert "odt_note_status" not in part.model_dump_json()
    assert (
        before.model_dump_json()
        == result(part.model_copy(update={"odt_note_status": None})).model_dump_json()
    )
    assert (
        before.result_id
        == result(ExtractedPartSummary.model_validate_json(part.model_dump_json())).result_id
    )
    assert (
        models.MAX_EXTRACTION_METADATA_BYTES == 16384 and models.MAX_EXTRACTION_PART_SUMMARIES == 16
    )


@pytest.mark.parametrize("kind", ["typed", "dict"])
def test_outer_result_rejects_invalid_known_odt_status_before_serialization(kind: str) -> None:
    valid_part = summary().model_copy(update={"odt_note_status": status()})
    valid = result(valid_part)
    broken = (
        status().model_copy(update={"observed_notes": -1})
        if kind == "typed"
        else status().model_dump()
    )
    part = valid_part.model_copy(update={"odt_note_status": broken})
    damaged = valid.model_copy(update={"selected_parts": (part,)})
    with (
        patch.object(
            ExtractionResult,
            "model_dump_json",
            side_effect=RuntimeError("outer serializer must not run"),
        ),
        pytest.raises(ValidationError),
    ):
        ExtractionResult.model_validate(damaged)


def test_outer_result_revalidates_a_valid_typed_odt_status_without_changing_identity() -> None:
    valid = result(summary().model_copy(update={"odt_note_status": status()}))
    assert ExtractionResult.model_validate(valid) == valid
    assert (
        ExtractionResult.model_validate_json(valid.model_dump_json()).result_id == valid.result_id
    )
