"""Section trimming and excerpt building for Sejm prints."""

import pytest

from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.keywords import KeywordPrefilter
from lexinform.sections import (
    PAGE_BREAK,
    TextBudget,
    carries_the_document,
    excerpts,
    trim_print,
    without_cover_letter,
)
from tests.conftest import FIXTURES

BILL = "Projekt\nU S T AWA\nz dnia ... o zmianie ustawy o cudzoziemcach\nArt. 1. " + "x" * 500
JUSTIFICATION = "UZASADNIENIE\nProjekt ma na celu ... " + "y" * 500
OSR = (
    "Nazwa projektu\nProjekt ustawy ...\nOCENA SKUTKÓW REGULACJI\n1. Jaki problem jest rozwiązywany?\n"
    + "z" * 300
    + "\n4. Podmioty, na które oddziałuje projekt\ncudzoziemcy\n"
)
OSR_TAIL = "6. Wpływ na sektor finansów publicznych\n" + "0" * 400
CONSULTATION = "Raport z konsultacji publicznych\n" + "c" * 400
ANNEX = "Załącznik nr 1 – Tabela uwag\n" + "t" * 400
COMPLIANCE = "TYTUŁ PROJEKTU\nTabela zgodności\n" + "u" * 400
REGULATION = "Projekt\nROZPORZĄDZENIE\nMINISTRA ...\n" + "r" * 400
REGULATION_JUSTIFICATION = "UZASADNIENIE\nrozporządzenie ... " + "q" * 400


def _print(*pages: str) -> str:
    return PAGE_BREAK.join(pages)


def test_appendices_are_dropped_and_the_core_is_kept() -> None:
    text = _print(
        "Druk nr 1\nWarszawa",
        BILL,
        JUSTIFICATION,
        OSR + OSR_TAIL,
        CONSULTATION,
        ANNEX,
        COMPLIANCE,
        REGULATION,
        REGULATION_JUSTIFICATION,
    )
    result = trim_print(text)
    assert "Art. 1." in result.text and "Projekt ma na celu" in result.text
    assert "4. Podmioty" in result.text and "cudzoziemcy" in result.text
    for gone in ("0" * 50, "c" * 50, "t" * 50, "u" * 50, "r" * 50, "q" * 50):
        assert gone not in result.text
    assert [d.name for d in result.dropped] == [
        "OSR pkt 6-13",
        "raport z konsultacji",
        "tabela zgodności",
        "projekty rozporządzeń",
    ]
    assert result.dropped[1].chars == len(CONSULTATION) + len(ANNEX)  # annex belongs to the report
    assert result.dropped[3].chars == len(REGULATION) + len(REGULATION_JUSTIFICATION)
    assert result.text.count("[pominięto:") == 4
    assert PAGE_BREAK not in result.text


def test_rcl_osr_form_is_cut_at_point_6_even_without_the_number() -> None:
    # RCL publishes the OSR as a Word file: the form starts with "Nazwa projektu" and Word keeps
    # the point numbers as list formatting, so the text has no "6." before the heading.
    osr = "Nazwa projektu\nUstawa o ...\nMinisterstwo wiodące\n" + "z" * 300
    tail = "Wpływ na sektor finansów publicznych\n(ceny stałe z 2026 r.)\n" + "0" * 400

    result = trim_print(_print(BILL, osr + "\n" + tail))

    assert "z" * 300 in result.text and "0" * 50 not in result.text
    assert [d.name for d in result.dropped] == ["OSR pkt 6-13"]


def test_unknown_layout_passes_unchanged() -> None:
    text = _print("SPRAWOZDANIE KOMISJI\n" + "a" * 100, "Art. 1. " + "b" * 100)
    result = trim_print(text)
    assert result.dropped == () and "a" * 100 in result.text and "b" * 100 in result.text
    assert PAGE_BREAK not in result.text


def test_a_trim_that_would_keep_nothing_keeps_everything() -> None:
    """A Word file from RCL often has no page break at all, so the whole document is one page.
    If that page opens with a heading the trimmer drops, the model would be sent the marker
    line and nothing else."""
    text = "ROZPORZĄDZENIE\n\nArt. 1. W ustawie o cudzoziemcach wprowadza się zmiany. " * 50

    result = trim_print(text)

    assert result.dropped == ()
    assert result.text == text.strip()


def test_real_deputies_print_is_not_trimmed() -> None:
    text = PypdfTextExtractor().extract((FIXTURES / "print_3039.pdf").read_bytes())
    assert PAGE_BREAK in text
    result = trim_print(text)
    assert result.dropped == () and "Druk nr 3039" in result.text


DEPUTIES_COVER = """Druk nr 604       Warszawa, 23 lipca 2024 r.
SEJM
RZECZYPOSPOLITEJ POLSKIEJ
X kadencja
 Pan
 Szymon Hołownia
 Marszałek Sejmu
 Rzeczypospolitej Polskiej
Na podstawie art. 118 ust. 1 Konstytucji Rzeczypospolitej Polskiej i na podstawie
art. 32 ust. 2 regulaminu Sejmu niżej podpisani posłowie wnoszą projekt ustawy:
 - o zmianie ustawy o Krajowej Administracji
Skarbowej.
Do reprezentowania wnioskodawców w pracach nad projektem ustawy
upoważniamy pana posła Ryszarda Petru.
 (-)  Elżbieta Burkiewicz;  (-)  Żaneta Cwalina-Śliwowska;  (-)  Sławomir
Ćwik;  (-)  Piotr Górnikiewicz;  (-)  Paulina Hennig-Kloska
"""
GOVERNMENT_LETTER = """Warszawa, 21 sierpnia 2025 r.
SEJM
RZECZYPOSPOLITEJ POLSKIEJ
X kadencja

Prezes Rady Ministrów
DSP.WPP.0640.57.2025

 Pan
 Szymon Hołownia
 Marszałek Sejmu
 Rzeczypospolitej Polskiej

Szanowny Panie Marszałku,

przekazuję przyjęte przez Radę Ministrów stanowisko  w sprawie
poselskiego projektu ustawy

- o zmianie ustawy o obywatelstwie
polskim (druk nr 1273).

Jednocześnie informuję, że Rada Ministrów upoważniła Ministra Spraw
Wewnętrznych i Administracji  do prezentowania stanowiska Rządu w tej sprawie
w toku prac parlamentarnych.

Z wyrazami szacunku,
            Donald Tusk
"""
COMMITTEE_REPORT = (
    """Tłoczono z polecenia Marszałka Sejmu Rzeczypospolitej Polskiej

Druk nr 2715
S P R A W O Z D A N I E
KOMISJI KULTURY, DZIEDZICTWA NARODOWEGO I ŚRODKÓW PRZEKAZU
o poselskim projekcie uchwały (druk nr 2508)
Marszałek Sejmu skierował w dniu 6 maja 2026 r. powyższy projekt uchwały do Komisji
"""
    + "s" * 300
)


def test_a_print_that_is_a_scan_of_its_cover_letter_carries_no_document() -> None:
    """Druk 604: 37 pages, and the only text layer is page one — the letter and the signatures."""
    scanned = DEPUTIES_COVER + PAGE_BREAK + PAGE_BREAK.join("" for _ in range(36))

    assert not carries_the_document(scanned, min_chars=200)
    assert not carries_the_document(GOVERNMENT_LETTER, min_chars=200)


def test_the_same_letter_followed_by_the_bill_carries_the_document() -> None:
    whole = DEPUTIES_COVER + PAGE_BREAK + BILL + PAGE_BREAK + JUSTIFICATION

    assert carries_the_document(whole, min_chars=200)
    assert without_cover_letter(whole).lstrip().startswith("Projekt")


def test_a_document_with_no_covering_letter_is_its_own_first_page() -> None:
    assert carries_the_document(COMMITTEE_REPORT, min_chars=200)
    assert without_cover_letter(COMMITTEE_REPORT) == COMMITTEE_REPORT


def test_excerpts_take_heads_and_keyword_windows_in_order() -> None:
    filler = "lorem ipsum " * 400  # 4800 chars
    text = (
        "Projekt ustawy o podatku\n"
        + filler
        + "\nUZASADNIENIE\nCel projektu.\n"
        + filler
        + " kontrolę przeprowadza Straż Graniczna oraz "
        + filler
        + " zezwolenie na pobyt czasowy "
        + filler
    )
    spans = KeywordPrefilter().spans(text)
    digest = excerpts(text, spans, head_chars=100, window=40, max_chars=2000)
    assert digest.startswith("Projekt ustawy o podatku")
    assert "UZASADNIENIE\nCel projektu." in digest
    assert "Straż Graniczna" in digest and "zezwolenie na pobyt czasowy" in digest
    assert digest.index("Straż Graniczna") < digest.index("zezwolenie na pobyt")
    assert digest.count("[...]") == 3
    assert len(digest) < 600


def test_a_text_dense_with_hits_still_fills_the_budget() -> None:
    """Windows overlap, and each one used to be charged in full, so the text the triage judges
    a keyword-dense bill by shrank to its first page."""
    text = "Art. 1. Cudzoziemiec składa wniosek o zezwolenie na pobyt czasowy. " * 4000
    spans = KeywordPrefilter().spans(text)

    digest = excerpts(text, spans, max_chars=24_000)

    assert len(spans) > 1000
    assert 20_000 < len(digest) <= 24_000


def test_excerpts_respect_the_budget_but_always_keep_the_heads() -> None:
    text = "start " * 100 + " cudzoziemiec " * 200
    spans = KeywordPrefilter().spans(text)

    digest = excerpts(text, spans, head_chars=50, window=10, max_chars=60)

    assert digest.startswith("start start")
    assert len(digest) < 200


def test_budget_passes_short_texts_through() -> None:
    result = TextBudget(100).apply("short")

    assert (result.text, result.truncated) == ("short", False)


def test_budget_keeps_the_head_and_the_start_of_the_justification() -> None:
    act = "Art. 1. " * 500
    justification = "\nUzasadnienie\n" + "Projekt ma na celu. " * 500

    result = TextBudget(2000).apply(act + justification)

    assert result.truncated
    assert result.text.startswith("Art. 1.")
    assert "Uzasadnienie" in result.text and TextBudget.MARKER in result.text
    assert len(result.text) <= 2000 + len(TextBudget.MARKER)


def test_budget_cuts_the_head_when_there_is_no_justification() -> None:
    result = TextBudget(50).apply("x" * 500)

    assert result.truncated and len(result.text) == 50


def test_budget_rejects_a_non_positive_cap() -> None:
    with pytest.raises(ValueError):
        TextBudget(0)
