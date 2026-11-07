from unittest.mock import patch

import pytest
from pydantic import ValidationError

from lexinform import extraction_records
from lexinform.document_candidates import select_document_parts
from lexinform.models import WordViewStatus
from lexinform.models import extractions as models
from lexinform.models.extractions import (
    ExtractedPartSummary,
    ExtractionComponent,
    ExtractionOptions,
    ExtractionResult,
    WordInlineMarkers,
)
from tests.unit.test_physical_extraction import docx, extractor
from tests.unit.test_word_blocks import paragraph, word
from tests.unit.test_word_note_origins import package


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
    physical = extractor().extract_parts(docx("USTAWA Art. 1. Cudzoziemiec."))
    return extraction_records.summarize_extraction(
        physical,
        select_document_parts(physical, max_part_chars=None),
        components=(ExtractionComponent(role="package", qualified_name_sha256="a" * 64),),
        options=ExtractionOptions(
            archive_depth=1, archive_bytes=1024, archive_members=10, xml_bytes=1024
        ),
    )


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
        observed_textbox_nodes=2**63 - 1,
        observed_inline=WordInlineMarkers(fields=2**32 - 1, revisions=2**32 - 1),
    )
    assert len(value.model_dump_json().encode()) <= 512


@pytest.mark.parametrize("counter", ["observed_skipped_blocks", "observed_textbox_nodes"])
@pytest.mark.parametrize("invalid", [-1, 2**63, True, 1.5, "1"])
def test_invalid_counter_is_rejected_before_serialization(counter: str, invalid: object) -> None:
    payload = status().model_dump(mode="json")
    payload[counter] = invalid
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


def test_optional_reader_field_does_not_invent_status_on_the_legacy_fixture() -> None:
    observed = result()
    payload = observed.model_dump(mode="json")
    payload["selected_parts"][0]["word_view_status"] = status().model_dump(mode="json")
    offered = ExtractionResult.model_validate(payload)
    assert offered.selected_parts[0].word_view_status == status()
    assert offered.result_id != observed.result_id
    assert ExtractionResult.model_validate_json(offered.model_dump_json()) == offered
    assert result().selected_parts[0].word_view_status is None


def test_textbox_reader_distinguishes_unknown_and_observed_zero() -> None:
    legacy = status()
    payload = legacy.model_dump(mode="json")
    assert "observed_textbox_nodes" not in payload
    assert legacy.observed_textbox_nodes is None

    observed = WordViewStatus.model_validate({**payload, "observed_textbox_nodes": 0})

    assert observed.observed_textbox_nodes == 0
    assert WordViewStatus.model_validate_json(observed.model_dump_json()) == observed


def test_inline_marker_reader_preserves_legacy_bytes_and_reads_known_counts() -> None:
    legacy = status()
    payload = legacy.model_dump(mode="json")
    assert "observed_inline" not in payload
    assert WordViewStatus.model_validate(
        {**payload, "observed_inline": None}
    ).model_dump_json() == (legacy.model_dump_json())

    markers = WordInlineMarkers(fields=0, revisions=3)
    payload["observed_inline"] = markers.model_dump()
    extraction = result().model_dump(mode="json")
    extraction["selected_parts"][0]["word_view_status"] = payload
    restored = ExtractionResult.model_validate(extraction)

    assert restored.selected_parts[0].word_view_status == legacy.model_copy(
        update={"observed_inline": markers}
    )
    assert ExtractionResult.model_validate_json(restored.model_dump_json()) == restored


@pytest.mark.parametrize(
    "markers",
    [
        {"fields": -1, "revisions": 0},
        {"fields": 0, "revisions": 2**32},
        {"fields": True, "revisions": 0},
        {"fields": 0, "revisions": "1"},
        {"fields": 0},
        {"fields": 0, "revisions": 0, "view": "accepted"},
    ],
)
def test_inline_marker_reader_rejects_invalid_or_semantic_claims(
    markers: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        WordViewStatus.model_validate({**status().model_dump(), "observed_inline": markers})


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


def test_native_textbox_count_tracks_visited_nodes_without_expanding_fallback() -> None:
    choice = "<w:txbxContent>" + paragraph("Choice") + "</w:txbxContent>"
    fallback = "<w:txbxContent>" + paragraph("Fallback") + "</w:txbxContent>"
    data = word(
        "<w:p><w:r><mc:AlternateContent>"
        "<mc:Choice>" + choice + "</mc:Choice><mc:Fallback>" + fallback + "</mc:Fallback>"
        "</mc:AlternateContent></w:r></w:p>"
    )
    reader = extractor()

    actual = reader.extract_document_with_provenance(data)

    assert actual.document == reader.extract_document(data)
    assert "Fallback" not in actual.document.text
    view = actual.result.selected_parts[0].word_view_status
    assert view is not None, "native Word extraction binds its visited walk to the selected part"
    assert view.observed_textbox_nodes == 1


def test_textbox_count_yields_to_the_legacy_status_at_the_byte_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = extractor()
    data = docx("USTAWA Art. 1. Cudzoziemiec.")
    actual = reader.extract_document_with_provenance(data).result
    (part,) = actual.selected_parts
    view = part.word_view_status
    assert view is not None, "native Word extraction records its visited walk"
    assert view.observed_textbox_nodes == 0
    legacy_part = part.model_copy(
        update={"word_view_status": view.model_copy(update={"observed_textbox_nodes": None})}
    )
    legacy = actual.model_copy(update={"selected_parts": (legacy_part,)})
    monkeypatch.setattr(
        extraction_records, "MAX_EXTRACTION_METADATA_BYTES", len(legacy.model_dump_json().encode())
    )

    capped = reader.extract_document_with_provenance(data).result

    assert capped == legacy


_INLINE_MARKERS = (
    '<w:p><w:r><w:fldChar w:fldCharType="begin"/><w:instrText>DATE</w:instrText>'
    '<w:fldChar w:fldCharType="end"/></w:r>'
    '<w:fldSimple w:instr="DATE"><w:r><w:t>cached</w:t></w:r></w:fldSimple>'
    "<w:ins><w:r><w:t>added</w:t></w:r></w:ins>"
    "<w:del><w:r><w:delInstrText>old code</w:delInstrText><w:delText>old</w:delText></w:r></w:del>"
    "<w:moveFrom><w:r><w:delText>from</w:delText></w:r></w:moveFrom>"
    "<w:moveTo><w:r><w:t>to</w:t></w:r></w:moveTo>"
    '<x:fldSimple xmlns:x="urn:foreign"/><x:ins xmlns:x="urn:foreign"/>'
    "<mc:AlternateContent><mc:Choice><w:txbxContent><w:p><w:r><w:t>Choice</w:t></w:r></w:p>"
    "</w:txbxContent></mc:Choice>"
    '<mc:Fallback><w:fldSimple w:instr="DATE"/><w:ins><w:r><w:t>fallback</w:t></w:r></w:ins>'
    "</mc:Fallback></mc:AlternateContent></w:p>"
)


@pytest.mark.parametrize(
    "namespace",
    [
        "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "http://purl.oclc.org/ooxml/wordprocessingml/main",
    ],
)
def test_native_inline_markers_count_only_visited_word_nodes(namespace: str) -> None:
    data = word(_INLINE_MARKERS, namespace=namespace)
    reader = extractor()

    actual = reader.extract_document_with_provenance(data)

    assert actual.document == reader.extract_document(data)
    assert actual.document.text == "cachedaddedtoChoice"
    (part,) = actual.result.selected_parts
    view = part.word_view_status
    assert view is not None, "native Word extraction records its visited walk"
    assert view.observed_inline == WordInlineMarkers(fields=5, revisions=4)
    assert view.observed_textbox_nodes == 1


def test_native_zero_inline_marker_counts_stay_sparse() -> None:
    actual = extractor().extract_document_with_provenance(docx("USTAWA Art. 1. Cudzoziemiec."))
    (part,) = actual.result.selected_parts
    view = part.word_view_status
    assert view is not None, "native Word extraction records its visited walk"
    assert view.observed_inline is None
    assert "observed_inline" not in view.model_dump()


def test_inline_marker_count_yields_to_textboxes_at_the_byte_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = word(_INLINE_MARKERS)
    reader = extractor()
    actual = reader.extract_document_with_provenance(data)
    (part,) = actual.result.selected_parts
    view = part.word_view_status
    assert view is not None, "native Word extraction records its visited walk"
    assert view.observed_inline is not None and view.observed_textbox_nodes == 1
    baseline_part = part.model_copy(
        update={
            "word_view_status": view.model_copy(update={"observed_inline": None}),
            "word_field_prefix": None,
        }
    )
    baseline = actual.result.model_copy(update={"selected_parts": (baseline_part,)})
    monkeypatch.setattr(
        extraction_records,
        "MAX_EXTRACTION_METADATA_BYTES",
        len(baseline.model_dump_json().encode()),
    )

    capped = reader.extract_document_with_provenance(data)

    assert capped.document == actual.document
    assert capped.result == baseline


def test_word_removal_text_count_is_optional_and_round_trips() -> None:
    legacy = WordInlineMarkers(fields=0, revisions=1)
    assert legacy.model_dump() == {"fields": 0, "revisions": 1}
    observed = WordViewStatus.model_validate(
        {
            **status().model_dump(),
            "observed_inline": {"fields": 0, "revisions": 1, "removal_text_nodes": 2},
        }
    )
    assert WordViewStatus.model_validate_json(observed.model_dump_json()) == observed
    assert observed.observed_inline == WordInlineMarkers(
        fields=0, revisions=1, removal_text_nodes=2
    )


@pytest.mark.parametrize(("revisions", "count"), [(1, 0), (1, True), (0, 1)])
def test_word_removal_text_count_requires_positive_count_and_visited_wrapper(
    revisions: int, count: object
) -> None:
    with pytest.raises(ValidationError):
        WordInlineMarkers.model_validate(
            {"fields": 0, "revisions": revisions, "removal_text_nodes": count}
        )


@pytest.mark.parametrize("limit", ["none", "status", "result"])
def test_observed_removal_text_preserves_legacy_text_and_prior_metadata(
    monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    data = package(
        "<w:p><w:r><w:t>Ordinary</w:t></w:r>"
        '<w:del w:id="1"><w:r><w:t>Still emitted</w:t></w:r></w:del>'
        '<w:del w:id="2"><w:r><w:delText>Omitted</w:delText></w:r></w:del>'
        '<w:moveFrom w:id="3"><w:r><w:t>Also emitted</w:t></w:r></w:moveFrom></w:p>',
        {},
    )
    reader = extractor()
    original = reader.extract_document(data)
    complete = reader.extract_document_with_provenance(data)
    (part,) = complete.result.selected_parts
    view = part.word_view_status
    assert view is not None and view.observed_inline is not None, (
        "the native reader visited revisions"
    )
    assert view.observed_inline.removal_text_nodes == 2
    old_markers = view.observed_inline.model_copy(update={"removal_text_nodes": None})
    old_view = view.model_copy(update={"observed_inline": old_markers})
    old_part = part.model_copy(update={"word_view_status": old_view})
    old_result = complete.result.model_copy(update={"selected_parts": (old_part,)})
    if limit == "status":
        monkeypatch.setattr(
            models, "MAX_WORD_VIEW_STATUS_BYTES", len(old_view.model_dump_json().encode())
        )
    elif limit == "result":
        monkeypatch.setattr(
            extraction_records,
            "MAX_EXTRACTION_METADATA_BYTES",
            len(old_result.model_dump_json().encode()),
        )

    observed = reader.extract_document_with_provenance(data)

    assert observed.document == original
    assert "Still emitted" in original.text and "Omitted" not in original.text
    assert observed.result == (complete.result if limit == "none" else old_result)
