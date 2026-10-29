import hashlib

import pytest

from lexinform.document_selection import bill_selection, paid_input_selection, selection_for_input
from lexinform.models import DocumentPart, DocumentSelection
from lexinform.sections import PAGE_BREAK, TextBudget, trim_print
from lexinform.text_projection import SourcePiece, TextProjection, TextSegment, project_pieces
from tests.scenario.test_document_selection_contract import law


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
    assert "normative_text_omitted" in result.limitations
    assert result.coverage[0].losses == ("normative part omitted from prepared input",)


@pytest.mark.parametrize("prefix", [0, 1, 20])
def test_support_copy_cannot_preserve_selected_completeness_for_an_omitted_bill(
    prefix: int,
) -> None:
    body = law()
    support = "Uzasadnienie\n" + body
    selection = bill_selection(
        DocumentSelection(
            parts=tuple(
                DocumentPart(
                    role=role,
                    url=f"https://fixture.invalid/{index}",
                    text=text,
                    text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    text_end=len(text),
                )
                for index, (role, text) in enumerate((("bill", body), ("justification", support)))
            )
        ),
        source_basis=("synthetic official source relation",),
    )
    assert selection.state == "selected", "law and the fixture source relation provide four facts"
    source = PAGE_BREAK.join((body, support))
    projection = project_pieces(
        source,
        (
            SourcePiece(body[:prefix], 0),
            SourcePiece(body, len(body) + len(PAGE_BREAK) + len("Uzasadnienie\n")),
        ),
    )

    result = selection_for_input(selection, projection.text, projection=projection)

    assert result.state == "unverified"
    assert "normative_text_omitted" in result.limitations
    assert result.normative_sha256 == selection.normative_sha256
    assert all(
        "normative part omitted from prepared input" in span.losses
        for span in result.coverage
        if span.stage == "analysis"
    )


def test_adjacent_copied_ranges_keep_full_part_coverage_without_requiring_one_segment() -> None:
    text = law()
    selection = DocumentSelection(parts=(DocumentPart(role="bill", text=text),))
    split = len(text) // 2
    projection = TextProjection(
        text,
        hashlib.sha256(text.encode()).hexdigest(),
        len(text),
        (TextSegment(0, split, 0), TextSegment(split, len(text), split)),
    )

    result = selection_for_input(selection, text, projection=projection)

    assert "normative_text_omitted" not in result.limitations
    assert not any(span.losses for span in result.coverage)


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


@pytest.mark.parametrize("change", [None, "text", "unhashed", "role", "unpaid"])
def test_paid_ranges_are_reused_only_for_the_same_part_texts(change: str | None) -> None:
    body = "USTAWA\nArt. 1. " + "a" * 600
    digest = hashlib.sha256(body.encode()).hexdigest()
    part = DocumentPart(role="bill", text=body, text_sha256=digest)
    budget = TextBudget(300).apply(body, [])
    assert budget.projection is not None, "TextBudget supplies an exact map"
    paid = selection_for_input(
        DocumentSelection(state="selected", parts=(part,)),
        budget.text,
        projection=budget.projection,
    ).without_text()
    if change == "unpaid":
        paid = paid.model_copy(update={"coverage": ()})
    current = part.model_copy(
        update={
            "text_sha256": {"text": "0" * 64, "unhashed": ""}.get(change or "", digest),
            "role": "normative_annex" if change == "role" else "bill",
        }
    )

    reused = paid_input_selection(DocumentSelection(state="selected", parts=(current,)), paid)

    if change is not None:
        assert reused is None
    else:
        assert reused is not None and reused.coverage == paid.coverage
        assert reused.limitations == paid.limitations == ("normative_text_omitted",)
        assert reused.state == "unverified" and reused.parts[0].text == body
