import hashlib

import pytest

from lexinform.sections import TextBudget, excerpt_projection, excerpts, trim_print
from lexinform.text_projection import TextProjection, TextSegment


def assert_round_trip(projection: TextProjection, source: str) -> None:
    assert projection.source_sha256 == hashlib.sha256(source.encode()).hexdigest()
    assert projection.source_length == len(source)
    for segment in projection.segments:
        if segment.source_start is not None:
            assert (
                projection.text[segment.start : segment.end]
                == source[segment.source_start : segment.source_start + segment.end - segment.start]
            )


def test_identical_keyword_windows_keep_their_actual_positions() -> None:
    text = " head  " + "x" * 100 + " same " + "x" * 100 + " same "
    positions = [(text.index("same"), text.index("same") + 4), (text.rindex("same"), len(text))]

    projection = excerpt_projection(text, positions, head_chars=7, window=0)

    assert projection.text == "head\n[...]\nsame\n[...]\nsame"
    origins = [segment.source_start for segment in projection.segments]
    assert origins == [1, None, positions[0][0], None, positions[1][0]]
    assert_round_trip(projection, text)


def test_inserted_excerpt_marker_is_generated_even_if_the_omitted_source_is_identical() -> None:
    text = "a\n[...]\nb"

    projection = excerpt_projection(text, [(8, 9)], head_chars=1, window=0)

    assert projection.text == text
    assert projection.segments == (TextSegment(0, 1, 0), TextSegment(1, 8), TextSegment(8, 9, 8))


@pytest.mark.parametrize("cap", [1, 2, 3, 4, 5, 8, 10, 30, 100, 1000])
def test_budget_clips_omission_markers_without_attributing_them_to_the_source(cap: int) -> None:
    text = "ab " + "x" * 200 + "\nUZASADNIENIE\n  same " + "y" * 200

    result = TextBudget(cap).apply(text, [(210, 215)])

    assert result.truncated == (len(text) > cap)
    assert len(result.text) <= cap
    assert result.projection is not None, "TextBudget supplies its exact transformation"
    assert result.projection.text == result.text
    assert_round_trip(result.projection, text)


@pytest.mark.parametrize("text", ["", " \n\t", " a\u00a0zażółć "])
def test_untruncated_budget_preserves_whitespace_and_identity(text: str) -> None:
    result = TextBudget(100).apply(text)

    assert result.text == text and not result.truncated
    assert result.projection is not None, "TextBudget supplies identity for unchanged text"
    assert_round_trip(result.projection, text)


def test_trim_budget_and_second_cost_fit_compose_back_to_original_characters() -> None:
    text = "USTAWA\nArt. 1. Termin 14 dni. " + "x" * 300
    text += "\nUZASADNIENIE\nCudzoziemcy płacą 100 zł. " + "y" * 300
    text += "\fRaport z konsultacji\nOMITTED"
    trimmed = trim_print(text)
    budgeted = TextBudget(160).apply(trimmed.text)
    cost_fit = excerpt_projection(budgeted.text, [], head_chars=25, max_chars=70)
    second_fit = excerpt_projection(cost_fit.text, [], head_chars=10, max_chars=30)

    assert trimmed.projection is not None, "trim_print supplies its exact transformation"
    assert budgeted.projection is not None, "TextBudget supplies its exact transformation"
    source = budgeted.projection.compose(trimmed.projection)
    first = cost_fit.compose(source)
    final = second_fit.compose(first)

    assert_round_trip(final, text)
    assert "OMITTED" not in final.text
    assert final.text == excerpts(
        excerpts(budgeted.text, [], head_chars=25, max_chars=70), [], head_chars=10, max_chars=30
    )
