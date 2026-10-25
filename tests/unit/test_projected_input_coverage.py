import hashlib

import pytest

from lexinform.document_selection import selection_for_input
from lexinform.models import DocumentPart, DocumentSelection
from lexinform.sections import PAGE_BREAK, TextBudget, trim_print
from lexinform.text_projection import SourcePiece, TextProjection, TextSegment, project_pieces


def test_budget_ranges_intersect_each_selected_part_without_a_contiguous_envelope() -> None:
    body = "USTAWA\nArt. 1. " + "a" * 600
    annex = "Załącznik\n" + "x" * 300 + "cudzoziemcy" + "z" * 600
    selection = DocumentSelection(
        parts=(
            DocumentPart(role="bill", text=body),
            DocumentPart(role="normative_annex", text=annex),
        )
    )
    source = PAGE_BREAK.join((body, annex))
    hit = source.index("cudzoziemcy")
    budget = TextBudget(500).apply(source, [(hit, hit + 11)])
    assert budget.projection is not None, "TextBudget supplies an exact map"

    result = selection_for_input(selection, budget.text, projection=budget.projection)

    assert [(s.part_index, s.start, s.end) for s in result.coverage] == [(0, 0, 365), (1, 248, 376)]
    assert not any("input coverage unknown" in s.losses for s in result.coverage)
    assert selection_for_input(selection, budget.text).limitations == result.limitations


def test_repeated_text_in_another_part_does_not_supply_the_missing_part_range() -> None:
    text = "USTAWA\nArt. 1. Same text."
    selection = DocumentSelection(
        parts=(
            DocumentPart(role="bill", text=text),
            DocumentPart(role="normative_annex", text=text),
        )
    )
    source = PAGE_BREAK.join((text, text))
    projection = project_pieces(source, (SourcePiece(text, len(text) + 1),))

    result = selection_for_input(selection, text, projection=projection)

    assert [(s.part_index, s.start, s.end) for s in result.coverage] == [
        (0, 0, 0),
        (1, 0, len(text)),
    ]


def test_trimmed_appendix_stays_outside_budget_coverage() -> None:
    source = "USTAWA\nArt. 1. " + "x" * 700 + "\fRaport z konsultacji\nREMOVED"
    trimmed = trim_print(source)
    budget = TextBudget(200).apply(trimmed.text)
    assert trimmed.projection is not None, "trim_print supplies its exact map"
    assert budget.projection is not None, "TextBudget supplies its exact map"
    projection = budget.projection.compose(trimmed.projection)

    result = selection_for_input(
        DocumentSelection(parts=(DocumentPart(role="bill", text=source),)),
        budget.text,
        projection=projection,
    )

    assert all(s.end <= source.index("Raport") for s in result.coverage)
    assert not any("input coverage unknown" in s.losses for s in result.coverage)


@pytest.mark.parametrize("wrong", ["output", "source", "copy"])
def test_input_map_cannot_use_a_foreign_identity_or_forged_copied_text(wrong: str) -> None:
    text = "USTAWA\nArt. 1. Treść."
    selection = DocumentSelection(parts=(DocumentPart(role="bill", text=text),))
    if wrong == "source":
        projection = project_pieces("FOREIGN " + text, (SourcePiece(text, 8),))
    elif wrong == "output":
        projection = project_pieces(text, (SourcePiece("other"),))
    else:
        projection = TextProjection(
            text,
            hashlib.sha256(text.encode()).hexdigest(),
            len(text),
            (TextSegment(0, 6, 1), TextSegment(6, len(text))),
        )

    with pytest.raises(ValueError, match="selected text assembly"):
        selection_for_input(selection, text, projection=projection)
