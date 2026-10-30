from unittest.mock import patch

import pytest
from pydantic import ValidationError

from lexinform.models import WordViewStatus
from lexinform.models import extractions as models
from lexinform.models.extractions import ExtractedPartSummary, ExtractionResult
from tests.unit.test_physical_extraction import docx, extractor


def status(known_walk: str = "finished") -> WordViewStatus:
    return WordViewStatus.model_validate(
        {
            "known_walk": known_walk,
            "observed_skipped_blocks": 3,
            "observed_unclassified_nodes": 4,
            "observed_legacy_recursion": 5,
            "observed_ignored_fallbacks": 6,
        }
    )


def result() -> ExtractionResult:
    return extractor().extract_document_with_provenance(docx("USTAWA Art. 1. Cudzoziemiec.")).result


@pytest.mark.parametrize("walk", ["finished", "interrupted"])
def test_known_walk_state_only_describes_actual_visited_dispositions(walk: str) -> None:
    value = status(walk)
    assert value.known_walk == walk and value.observed_skipped_blocks == 3
    assert value.xml_completeness == "not_asserted"
    assert value.fields_view == value.changes_view == "legacy_unresolved"
    assert WordViewStatus.model_validate_json(value.model_dump_json()) == value
    assert len(value.model_dump_json().encode()) <= 512


def test_maximum_counters_remain_within_the_compact_bound() -> None:
    value = WordViewStatus(
        known_walk="interrupted",
        observed_skipped_blocks=2**63 - 1,
        observed_unclassified_nodes=2**63 - 1,
        observed_legacy_recursion=2**63 - 1,
        observed_ignored_fallbacks=2**63 - 1,
    )
    assert len(value.model_dump_json().encode()) <= 512


@pytest.mark.parametrize("invalid", [-1, 2**63, True, 1.5, "1"])
def test_invalid_counter_is_rejected_before_serialization(invalid: object) -> None:
    payload = status().model_dump(mode="json")
    payload["observed_skipped_blocks"] = invalid
    with (
        patch.object(WordViewStatus, "model_dump_json", side_effect=AssertionError("serializer")),
        pytest.raises(ValidationError),
    ):
        WordViewStatus.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    [
        "version",
        "scope",
        "known_walk",
        "xml_completeness",
        "fields_view",
        "changes_view",
        "block_origins",
    ],
)
def test_closed_profile_rejects_fake_completeness_coordinates_or_unrecognized_states(
    field: str,
) -> None:
    payload = status().model_dump(mode="json")
    payload[field] = "complete_full_xml"
    with pytest.raises(ValidationError):
        WordViewStatus.model_validate(payload)


def test_oversized_state_is_rejected_before_serialization() -> None:
    payload = status().model_dump(mode="json")
    payload["known_walk"] = "x" * 100000
    with (
        patch.object(WordViewStatus, "model_dump_json", side_effect=AssertionError("serializer")),
        pytest.raises(ValidationError),
    ):
        WordViewStatus.model_validate(payload)


def test_byte_guard_checks_the_exact_serialized_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = status().model_dump(mode="json")
    size = len(status().model_dump_json().encode())
    monkeypatch.setattr(models, "MAX_WORD_VIEW_STATUS_BYTES", size)
    assert WordViewStatus.model_validate(payload) == status()
    monkeypatch.setattr(models, "MAX_WORD_VIEW_STATUS_BYTES", size - 1)
    with pytest.raises(ValidationError, match="serialized byte"):
        WordViewStatus.model_validate(payload)


def test_absent_status_is_unknown_and_preserves_old_json_and_result_identity() -> None:
    observed = result()
    raw = observed.model_dump_json()
    payload = observed.model_dump(mode="json")
    assert "word_view_status" not in payload["selected_parts"][0]
    assert observed.selected_parts[0].word_view_status is None
    payload["selected_parts"][0]["word_view_status"] = None
    restored = ExtractionResult.model_validate(payload)
    assert restored.model_dump_json() == raw and restored.result_id == observed.result_id


def test_optional_status_is_reader_only_and_never_changes_default_producer() -> None:
    observed = result()
    payload = observed.model_dump(mode="json")
    payload["selected_parts"][0]["word_view_status"] = status().model_dump(mode="json")
    offered = ExtractionResult.model_validate(payload)
    assert offered.selected_parts[0].word_view_status == status()
    assert offered.result_id != observed.result_id
    assert ExtractionResult.model_validate_json(offered.model_dump_json()) == offered
    assert result().selected_parts[0].word_view_status is None


@pytest.mark.parametrize("format_name", ["pdf", "odt", "unknown"])
def test_word_status_cannot_be_attached_to_other_format_summaries(format_name: str) -> None:
    payload = result().selected_parts[0].model_dump(mode="json")
    payload.update(format=format_name, word_view_status=status().model_dump(mode="json"))
    with pytest.raises(ValidationError, match="actual Word"):
        ExtractedPartSummary.model_validate(payload)


def test_constructed_corrupt_status_does_not_bypass_nested_reader_validation() -> None:
    payload = result().selected_parts[0].model_dump()
    payload["word_view_status"] = status().model_copy(update={"observed_unclassified_nodes": -1})
    with pytest.raises(ValidationError):
        ExtractedPartSummary.model_validate(payload)


def test_outer_byte_cap_and_selected_omission_accounting_remain_unchanged() -> None:
    payload = result().model_dump(mode="json")
    first = payload["selected_parts"][0]
    first.update(
        word_view_status=status().model_dump(mode="json"),
        limitations=["tracked_changes_unknown"] * 8,
    )
    payload.update(
        physical_part_count=16,
        selected_part_count=16,
        selected_parts=[first | {"physical_index": index} for index in range(16)],
        omitted_selected_parts=0,
    )
    with pytest.raises(ValidationError, match="serialized byte limit"):
        ExtractionResult.model_validate(payload)
    payload.update(selected_parts=payload["selected_parts"][:8], omitted_selected_parts=8)
    trimmed = ExtractionResult.model_validate(payload)
    assert (
        trimmed.selected_part_count == len(trimmed.selected_parts) + trimmed.omitted_selected_parts
    )
    assert len(trimmed.model_dump_json().encode()) <= 16384
    assert ExtractionResult.model_validate_json(trimmed.model_dump_json()) == trimmed


@pytest.mark.parametrize("field", ["coordinates", "text_view", "manifest_sha256", "block_count"])
def test_known_status_requires_its_native_legacy_manifest_scope(field: str) -> None:
    payload = result().selected_parts[0].model_dump(mode="json")
    payload["word_view_status"] = status().model_dump(mode="json")
    payload[field] = "unknown" if field in ("coordinates", "text_view") else None
    with pytest.raises(ValidationError, match="legacy manifest"):
        ExtractedPartSummary.model_validate(payload)


def test_typed_summary_cannot_hide_a_corrupt_status_inside_extraction_result() -> None:
    original = result()
    corrupt = status().model_copy(update={"observed_unclassified_nodes": -1})
    summary = original.selected_parts[0].model_copy(update={"word_view_status": corrupt})
    payload = original.model_dump()
    payload["selected_parts"] = (summary,)
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(payload)


def test_constructed_summary_cannot_retain_an_untyped_status_dictionary() -> None:
    original = result()
    summary = original.selected_parts[0].model_copy(
        update={"word_view_status": status().model_dump(mode="json")}
    )
    payload = original.model_dump()
    payload["selected_parts"] = (summary,)
    with pytest.raises(ValidationError, match="typed view status"):
        ExtractionResult.model_validate(payload)
