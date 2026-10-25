import hashlib

import pytest

from lexinform.sections import PAGE_BREAK, trim_print
from lexinform.text_projection import SourcePiece, TextProjection, TextSegment, project_pieces


@pytest.mark.parametrize("source", ["", " \n\t", "  Zażółć\n\f\fGęślą  ", "a\fb\fc"])
def test_identity_projection_preserves_original_codepoint_positions(source: str) -> None:
    projection = project_pieces(source, (SourcePiece(source, 0),), strip=True)

    assert projection.text == source.strip()
    assert projection.source_sha256 == hashlib.sha256(source.encode()).hexdigest()
    assert projection.source_length == len(source)
    for segment in projection.segments:
        assert segment.source_start is not None, "identity projection copies the source only"
        assert (
            projection.text[segment.start : segment.end]
            == source[segment.source_start : segment.source_start + segment.end - segment.start]
        )


def test_generated_text_is_not_relabelled_when_the_source_contains_the_same_characters() -> None:
    projection = project_pieces("marker", (SourcePiece("marker"),))

    assert projection.segments == (TextSegment(0, 6),)


def test_copied_ranges_are_verified_at_the_declared_position() -> None:
    with pytest.raises(ValueError, match="declared source position"):
        project_pieces("same DIFFERENT same", (SourcePiece("same", 5),))


def test_nonadjacent_copies_keep_the_joiner_generated() -> None:
    projection = project_pieces(
        "left OMITTED right", (SourcePiece("left", 0), SourcePiece("right", 13)), separator=" "
    )

    assert projection.text == "left right"
    assert projection.segments == (TextSegment(0, 4, 0), TextSegment(4, 5), TextSegment(5, 10, 13))


def test_adjacent_pages_and_empty_pages_coalesce_without_one_stored_range_per_page() -> None:
    source = "USTAWA" + PAGE_BREAK * 2 + "Art. 1."
    projection = project_pieces(
        source,
        (SourcePiece("USTAWA", 0), SourcePiece("", 7), SourcePiece("Art. 1.", 8)),
        separator=PAGE_BREAK,
    )

    assert projection.text == source
    assert projection.segments == (TextSegment(0, len(source), 0),)


def test_slice_clips_source_and_generated_segments_without_guessing_new_origins() -> None:
    projection = project_pieces(
        "abcXYZdef", (SourcePiece("abc", 0), SourcePiece("--"), SourcePiece("def", 6))
    )

    clipped = projection.slice(1, 7)

    assert clipped.text == "bc--de"
    assert clipped.segments == (TextSegment(0, 2, 1), TextSegment(2, 4), TextSegment(4, 6, 6))
    assert clipped.source_sha256 == projection.source_sha256


@pytest.mark.parametrize("bounds", [(-1, 1), (2, 1), (0, 10)])
def test_slice_cannot_escape_or_reverse_its_input(bounds: tuple[int, int]) -> None:
    projection = project_pieces("abc", (SourcePiece("abc", 0),))

    with pytest.raises(ValueError, match="slice escapes"):
        projection.slice(*bounds)


@pytest.mark.parametrize(
    "segments", [(TextSegment(1, 3, 0),), (TextSegment(0, 2, 0),), (TextSegment(0, 3, 1),)]
)
def test_projection_rejects_gaps_and_source_overruns(segments: tuple[TextSegment, ...]) -> None:
    with pytest.raises(ValueError, match="Projection segment"):
        TextProjection("abc", hashlib.sha256(b"abc").hexdigest(), 3, segments)


def test_osr_cut_and_surviving_pages_keep_their_actual_offsets() -> None:
    bill = "USTAWA\nArt. 1. Przepis."
    osr = "Nazwa projektu\nProjekt ustawy\n4. Podmioty\nCudzoziemcy\n5. Informacje na temat zakresu konsultacji\nDROP"
    source = PAGE_BREAK.join((bill, osr))

    prepared = trim_print(source)

    assert "DROP" not in prepared.text
    assert prepared.projection is not None, "trim_print supplies its exact copy map"
    for segment in prepared.projection.segments:
        if segment.source_start is not None:
            assert (
                prepared.text[segment.start : segment.end]
                == source[segment.source_start : segment.source_start + segment.end - segment.start]
            )
            assert segment.source_start + segment.end - segment.start <= source.index(
                "5. Informacje"
            )
