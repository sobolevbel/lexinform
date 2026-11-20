import pytest

from lexinform.document_candidates import (
    AmbiguousCandidates,
    CandidateSet,
    DocumentCandidate,
    SelectedCandidate,
    select_candidate_versions,
    select_regulation_archive,
)
from lexinform.models import DocumentPart, DocumentSelection

NOTE = "Minister kieruje działem administracji na podstawie § 1 ust. 2 ustawy z 2026 r."
TEXT = (
    "Projekt z dnia 10 listopada 2026 r.\nROZPORZĄDZENIE\nMINISTRA\nz dnia … 2026 r.\n"
    "w sprawie opłat za zezwolenia\nNa podstawie art. 4 ustawy z 2026 r. zarządza się:\n"
    "§ 1. Cudzoziemiec nie wnosi opłaty 200,50 zł za wniosek z art. 2 ust. 1.\n"
    "§ 2. Rozporządzenie wchodzi w życie dnia 1 grudnia 2026 r.\nMINISTER\n"
)


def choose(first: str, second: str) -> SelectedCandidate | AmbiguousCandidates | None:
    candidates = CandidateSet(
        (DocumentCandidate("first", first), DocumentCandidate("second", second))
    )
    return select_candidate_versions(candidates, regulation=True)


def test_regulation_layout_and_note_placement_do_not_create_competing_drafts() -> None:
    pdf = (
        TEXT.replace("ROZPORZĄDZENIE", "R O Z P O R Z Ą D Z E N I E").replace(
            "MINISTRA\n", "MINISTRA 1)\n"
        )
        + f"1) {NOTE}\n\f– 2 –\n"
    )
    word = TEXT.replace("MINISTRA\n", f"Ministra [przypis 1: ) {NOTE}] )\n").replace(
        "§ 1.", "§\u00a01."
    )

    selected = choose(pdf, word)

    assert isinstance(selected, SelectedCandidate)
    assert selected.comparison.matching_indices == (0, 1)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("200,50", "300,50"),
        ("200,50", "200.50"),
        ("nie wnosi", "wnosi"),
        ("Cudzoziemiec", "Obywatel"),
        ("art. 2 ust. 1", "art. 3 ust. 1"),
        ("1 grudnia", "2 grudnia"),
        ("ustawy z 2026", "ustawy z 2027"),
    ],
)
def test_regulation_changes_remain_ambiguous(before: str, after: str) -> None:
    selected = choose(TEXT, TEXT.replace(before, after))

    assert isinstance(selected, AmbiguousCandidates)


def test_changed_footnote_is_not_hidden_by_relocating_notes() -> None:
    pdf = TEXT + f"1) {NOTE}\n"
    word = TEXT + f" [przypis 1: ) {NOTE.replace('ust. 2', 'ust. 3')}] "

    assert isinstance(choose(pdf, word), AmbiguousCandidates)


def test_a_footnote_cannot_move_to_another_provision() -> None:
    first = TEXT.replace("wniosek", f"wniosek [przypis 1: {NOTE}]")
    second = TEXT.replace("zezwolenia", f"zezwolenia [przypis 1: {NOTE}]")

    assert isinstance(choose(first, second), AmbiguousCandidates)


def test_regulation_newer_explicit_draft_wins_for_the_same_issuer_and_subject() -> None:
    old = TEXT.replace("10 listopada", "11 maja").replace("200,50", "100,50")

    selected = choose(old, TEXT)

    assert isinstance(selected, SelectedCandidate)
    assert selected.index == 1 and selected.comparison.date_filter is not None


@pytest.mark.parametrize(
    "changed",
    [
        TEXT.replace("MINISTRA\n", "RADY MINISTRÓW\n"),
        TEXT.replace("w sprawie opłat", "w sprawie podatków"),
        TEXT.replace("Projekt z dnia 10 listopada 2026 r.", "Projekt"),
    ],
)
def test_regulation_date_does_not_choose_an_unrelated_or_undated_draft(changed: str) -> None:
    old = TEXT.replace("10 listopada", "11 maja").replace("200,50", "100,50")

    assert isinstance(choose(old, changed), AmbiguousCandidates)


def test_only_a_split_year_in_the_date_formula_is_normalised() -> None:
    assert isinstance(choose(TEXT, TEXT.replace("z 2026 r.", "z 202 6 r.")), SelectedCandidate)
    assert isinstance(choose(TEXT, TEXT.replace("200,50", "200 50")), AmbiguousCandidates)


def test_an_archive_with_real_concurrent_regulation_variants_stays_ambiguous() -> None:
    selected = select_regulation_archive(
        DocumentSelection(
            observed_parts=(
                DocumentPart(text=TEXT, members=("wariant1.docx",)),
                DocumentPart(text=TEXT.replace("nie wnosi", "wnosi"), members=("wariant2.docx",)),
            )
        )
    )

    assert selected.selection.state == "ambiguous" and selected.text == ""


def test_archive_dates_cannot_assign_shared_annexes_to_an_edition() -> None:
    selected = select_regulation_archive(
        DocumentSelection(
            observed_parts=(
                DocumentPart(text=TEXT, members=("current.docx",)),
                DocumentPart(text=TEXT.replace("10 listopada", "11 maja"), members=("old.docx",)),
            )
        )
    )

    assert selected.selection.state == "ambiguous"
