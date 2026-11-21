import datetime as dt

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
FINAL = TEXT.split("\n", 1)[1].replace("… 2026", "13 listopada 2026")


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


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("date", ["13 listopada 2026 r.", "13.11.2026 r.", "13-11-2026"])
def test_regulation_heading_date_orders_a_final_text_after_its_draft(
    reverse: bool, date: str
) -> None:
    final = FINAL.replace("13 listopada 2026 r.", date)
    texts = [TEXT, final]
    if reverse:
        texts.reverse()

    selected = choose(*texts)

    assert isinstance(selected, SelectedCandidate), "the dated heading identifies a newer edition"
    assert selected.index == (0 if reverse else 1)
    expected = (dt.date(2026, 11, 10), dt.date(2026, 11, 13))
    assert selected.comparison.date_filter == (expected[::-1] if reverse else expected)


def test_explicit_draft_date_takes_priority_over_a_proposed_act_date() -> None:
    proposed = "Projekt z dnia 9 listopada 2026 r.\n" + FINAL

    selected = choose(proposed, TEXT)

    assert isinstance(selected, SelectedCandidate), "the explicit draft date orders these editions"
    assert selected.index == 1


@pytest.mark.parametrize(
    "prefix",
    [
        "Projekt",
        "P R O J E K T",
        "Projekt z dnia 31 listopada 2026 r.",
        "Projekt z dnia 9 listopada 2026 r.\nProjekt z dnia 12 listopada 2026 r.",
        "Wersja robocza",
        "Etap: uzgodnienia",
    ],
)
def test_undated_or_conflicting_draft_marker_blocks_the_heading_date(prefix: str) -> None:
    assert isinstance(choose(TEXT, prefix + "\n" + FINAL), AmbiguousCandidates)


@pytest.mark.parametrize("marker", ["Projekt", "P R O J E K T", "Wersja robocza"])
def test_draft_marker_below_the_heading_prevents_using_the_proposed_act_date(marker: str) -> None:
    text = FINAL.replace("ROZPORZĄDZENIE\n", f"ROZPORZĄDZENIE\n{marker}\n")
    old = text.replace("13 listopada", "10 listopada").replace("200,50", "100,50")

    assert isinstance(choose(old, text), AmbiguousCandidates)


@pytest.mark.parametrize(
    "date",
    ["… 2026 r.", "<data wydania aktu> r.", "31 listopada 2026 r.", "13.11.2026 / 14.11.2026"],
)
def test_body_and_footer_dates_cannot_replace_an_unknown_heading_date(date: str) -> None:
    text = FINAL.replace("13 listopada 2026 r.", date)
    text += "\nProjekt z dnia 20 listopada 2026 r.\nPodpisano dnia 21 listopada 2026 r."

    assert isinstance(choose(TEXT, text), AmbiguousCandidates)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("200,50", "300,50"),
        ("nie wnosi", "wnosi"),
        ("Cudzoziemiec", "Obywatel"),
        ("art. 2 ust. 1", "art. 3 ust. 1"),
        ("1 grudnia", "2 grudnia"),
    ],
)
def test_final_texts_of_the_same_date_retain_material_conflicts(before: str, after: str) -> None:
    assert isinstance(choose(FINAL, FINAL.replace(before, after)), AmbiguousCandidates)


@pytest.mark.parametrize(
    ("before", "after"),
    [("MINISTRA\n", "RADY MINISTRÓW\n"), ("w sprawie opłat", "w sprawie podatków")],
)
def test_heading_date_cannot_choose_a_different_issuer_or_subject(before: str, after: str) -> None:
    assert isinstance(choose(TEXT, FINAL.replace(before, after)), AmbiguousCandidates)


@pytest.mark.parametrize(
    "remove", ["ROZPORZĄDZENIE\n", "MINISTRA\n", "w sprawie opłat za zezwolenia\n"]
)
def test_final_date_needs_a_complete_regulation_heading(remove: str) -> None:
    old = FINAL.replace("13 listopada", "10 listopada").replace(remove, "")
    new = FINAL.replace("200,50", "300,50").replace(remove, "")

    assert not isinstance(choose(old, new), SelectedCandidate)


def test_final_date_cannot_assign_shared_archive_annexes() -> None:
    selected = select_regulation_archive(
        DocumentSelection(
            observed_parts=(
                DocumentPart(text=TEXT, members=("draft.docx",)),
                DocumentPart(text=FINAL, members=("final.docx",)),
            )
        )
    )

    assert selected.selection.state == "ambiguous"
