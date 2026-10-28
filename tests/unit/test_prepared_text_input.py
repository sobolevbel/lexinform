import hashlib
from typing import Any

import pytest

from lexinform.models import (
    DocumentCoverage,
    DocumentPart,
    DocumentSelection,
    PreparedTextInput,
    PreparedTextSegment,
)
from lexinform.prepared_input import prepared_text_input
from lexinform.sections import PAGE_BREAK, trim_print
from lexinform.text_projection import SourcePiece, TextProjection, TextSegment, project_pieces


def source_selection() -> DocumentSelection:
    return DocumentSelection(
        parts=(
            DocumentPart(role="bill", text="USTAWA Art. 1. Kwota 300 zł."),
            DocumentPart(role="normative_annex", text="Załącznik: cudzoziemcy."),
        )
    )


def test_prepared_input_partitions_source_parts_and_system_separators_without_bodies() -> None:
    selection = source_selection()
    source = PAGE_BREAK.join(part.text for part in selection.parts)
    projection = project_pieces(source, (SourcePiece(source, 0),))

    result = prepared_text_input(selection, source, projection=projection)

    assert result.mapping == "exact" and result.physical_origins == "unmapped"
    assert [part.role for part in result.parts] == ["bill", "normative_annex"]
    assert [segment.origin for segment in result.segments] == ["source", "generated", "source"]
    assert result.segments[1].system_origin == "part_separator"
    assert result.segments[1].part_index is None
    assert result.text_sha256 == hashlib.sha256(source.encode()).hexdigest()
    assert result.source_chars == result.text_chars == len(source)
    encoded = result.model_dump_json()
    assert all(part.text not in encoded for part in selection.parts)
    assert PreparedTextInput.model_validate_json(encoded) == result
    assert PreparedTextInput.model_validate_json(encoded).input_id == result.input_id


def test_same_text_in_another_part_keeps_its_actual_origin() -> None:
    text = "Art. 1. Kwota 300 zł."
    selection = DocumentSelection(
        parts=(DocumentPart(role="bill", text=text), DocumentPart(role="justification", text=text))
    )
    source = PAGE_BREAK.join((text, text))
    projection = project_pieces(source, (SourcePiece(text, len(text) + len(PAGE_BREAK)),))

    result = prepared_text_input(selection, text, projection=projection)

    assert len(result.segments) == 1 and result.segments[0].part_index == 1
    assert result.parts[1].role == "justification"


def test_a_missing_map_is_unknown_even_when_the_whole_input_matches() -> None:
    selection = source_selection()
    text = selection.parts[0].text

    result = prepared_text_input(selection, text, projection=None)

    assert result.mapping == "unknown" and result.segments[0].origin == "unknown"
    assert result.segments[0].part_index is None and result.segments[0].source_start is None


def test_trim_markers_have_system_origin_and_no_source_quote() -> None:
    source = "USTAWA\nArt. 1. " + "x" * 512 + PAGE_BREAK + "Raport z konsultacji\nREMOVED"
    selection = DocumentSelection(parts=(DocumentPart(role="bill", text=source),))
    trimmed = trim_print(source)
    assert trimmed.projection is not None, "trim_print supplies source positions"

    result = prepared_text_input(selection, trimmed.text, projection=trimmed.projection)

    generated = [segment for segment in result.segments if segment.origin == "generated"]
    assert generated and all(segment.system_origin == "input_transform" for segment in generated)
    assert all(segment.source_start is None for segment in generated)
    assert all(
        segment.source_end <= source.index("Raport")
        for segment in result.segments
        if segment.source_end is not None
    )


@pytest.mark.parametrize("fault", ["text", "source", "copy"])
def test_prepared_input_rejects_foreign_or_forged_projection(fault: str) -> None:
    selection = DocumentSelection(parts=(DocumentPart(role="bill", text="ABCD"),))
    if fault == "source":
        projection = project_pieces("foreign", (SourcePiece("ABCD"),))
    elif fault == "copy":
        projection = TextProjection(
            "ABCD",
            hashlib.sha256(b"ABCD").hexdigest(),
            4,
            (TextSegment(0, 2, 1), TextSegment(2, 4)),
        )
    else:
        projection = project_pieces("ABCD", (SourcePiece("other"),))

    with pytest.raises(ValueError):
        prepared_text_input(selection, "ABCD", projection=projection)


def test_fitted_selection_cannot_masquerade_as_the_source_decision() -> None:
    selection = source_selection().model_copy(
        update={"coverage": (DocumentCoverage(part_index=0, stage="analysis", start=0, end=0),)}
    )

    with pytest.raises(ValueError, match="before input fitting"):
        prepared_text_input(selection, "", projection=None)


def test_declared_part_hash_must_match_the_supplied_source() -> None:
    selection = DocumentSelection(parts=(DocumentPart(text="body", text_sha256="0" * 64),))

    with pytest.raises(ValueError, match="declared part hash"):
        prepared_text_input(selection, "body", projection=None)


@pytest.mark.parametrize(
    "fields",
    [
        {"origin": "source"},
        {"origin": "unknown", "part_index": 0},
        {"origin": "generated"},
        {"origin": "unknown", "system_origin": "input_transform"},
        {"origin": "source", "part_index": 0, "source_start": 0, "source_end": 2},
    ],
)
def test_segment_origins_do_not_invent_missing_source_coordinates(fields: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        PreparedTextSegment.model_validate({"start": 0, "end": 1, **fields})


@pytest.mark.parametrize("fault", ["gap", "length", "part", "range", "source_length"])
def test_serialized_manifest_checks_its_partition_and_part_bounds(fault: str) -> None:
    selection = source_selection()
    source = PAGE_BREAK.join(part.text for part in selection.parts)
    result = prepared_text_input(
        selection, source, projection=project_pieces(source, (SourcePiece(source, 0),))
    )
    value = result.model_dump(mode="json")
    if fault == "gap":
        value["segments"][0]["start"] = 1
        value["segments"][0]["source_start"] = 1
    elif fault == "length":
        value["text_chars"] += 1
    elif fault == "part":
        value["segments"][0]["part_index"] = 99
    elif fault == "range":
        value["segments"][0]["source_start"] = 1
        value["segments"][0]["source_end"] += 1
    else:
        value["source_chars"] += 1

    with pytest.raises(ValueError):
        PreparedTextInput.model_validate(value)


def test_exact_empty_map_does_not_claim_document_completeness() -> None:
    selection = source_selection()
    source = PAGE_BREAK.join(part.text for part in selection.parts)

    result = prepared_text_input(selection, "", projection=project_pieces(source, ()))

    assert result.mapping == "exact" and result.text_chars == 0 and not result.segments
    assert result.source_chars > 0 and result.parts
