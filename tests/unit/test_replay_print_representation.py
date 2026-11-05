from typing import Any

import pytest
from tools.replay_print_representation import exact_core, official_original

CORE = (
    "Art. 1. W ustawie zmienianej niniejszą ustawą stosuje się przepisy dotychczasowe. "
    "Art. 2. Osoba składająca wniosek przedstawia dokument w ciągu 30 dni od dnia wezwania. "
    "Art. 3. Ustawa wchodzi w życie po upływie 14 dni od dnia ogłoszenia."
)
PDF = "Strona tytułowa\fUSTAWA\n" + CORE + "\fUZASADNIENIE\nOddzielny tekst"
DOCX = "Projekt\nUSTAWA\n" + CORE
ROW = {
    "term": 10,
    "number": "1608",
    "processPrint": ["1608"],
    "attachments": ["1608.pdf", "1608-ustawa.docx", "1608-uzasadnienie.docx"],
}


def test_pdf_and_draft_keep_exact_core_and_leave_support_outside() -> None:
    assert exact_core(PDF, pdf=True) == exact_core(DOCX.replace(". ", ".\n"), pdf=False)
    assert exact_core(DOCX.replace("14 dni", "15 dni"), pdf=False) != exact_core(DOCX, pdf=False)


@pytest.mark.parametrize(
    ("text", "pdf"),
    [
        (PDF.replace("UZASADNIENIE", "Dalsza część"), True),
        (PDF.replace("ogłoszenia.\f", "ogłoszenia. 1\f"), True),
        (PDF + "\nZałącznik nr 1", True),
        (PDF.replace("dotychczasowe", "dotychczasowe z załącznika"), True),
        (DOCX + "\nUZASADNIENIE\nDalszy tekst", False),
        (DOCX.replace("życie", "użycie"), False),
    ],
)
def test_unproven_boundary_or_annex_does_not_establish_representation(text: str, pdf: bool) -> None:
    with pytest.raises(ValueError):
        exact_core(text, pdf=pdf)


def test_official_listing_names_distinct_original_attachments() -> None:
    original, word_url = official_original([ROW], term=10, number="1608")

    assert original.url == "https://api.sejm.gov.pl/sejm/term10/prints/1608/1608.pdf"
    assert word_url == "https://api.sejm.gov.pl/sejm/term10/prints/1608/1608-ustawa.docx"
    assert not original.alternatives


@pytest.mark.parametrize(
    "overrides",
    [
        {"term": 9},
        {"processPrint": ["1608", "1609"]},
        {"processPrint": "1608"},
        {"attachments": ["1608.pdf", "1608-ustawa.docx", "1608-ustawa.docx"]},
        {"attachments": ["1608.pdf", "1608-ustawa.docx", "1608-uzasadnienie.docx", "annex.pdf"]},
    ],
)
def test_filename_and_title_cannot_replace_official_singleton_scope(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        official_original([{**ROW, **overrides}], term=10, number="1608")


def test_actual_official_self_amendment_blocks_original_only_profile() -> None:
    amendment = {
        "term": 10,
        "number": "1608-A",
        "title": "Autopoprawka do projektu ustawy",
        "processPrint": ["1608"],
    }
    with pytest.raises(ValueError):
        official_original([ROW, amendment], term=10, number="1608")
