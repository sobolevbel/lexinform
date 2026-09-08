"""Section trimming and excerpt building for Sejm prints."""

from __future__ import annotations

from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.keywords import KeywordPrefilter
from lexinform.sections import PAGE_BREAK, excerpts, trim_print
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
    assert result.dropped_chars == sum(d.chars for d in result.dropped)


def test_unknown_layout_passes_unchanged() -> None:
    text = _print("SPRAWOZDANIE KOMISJI\n" + "a" * 100, "Art. 1. " + "b" * 100)
    result = trim_print(text)
    assert result.dropped == () and "a" * 100 in result.text and "b" * 100 in result.text
    assert PAGE_BREAK not in result.text


def test_real_deputies_print_is_not_trimmed() -> None:
    text = PypdfTextExtractor().extract((FIXTURES / "print_3039.pdf").read_bytes())
    assert PAGE_BREAK in text
    result = trim_print(text)
    assert result.dropped == () and "Druk nr 3039" in result.text


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


def test_excerpts_respect_the_budget_but_always_keep_the_heads() -> None:
    text = "start " * 100 + " cudzoziemiec " * 200
    spans = KeywordPrefilter().spans(text)
    digest = excerpts(text, spans, head_chars=50, window=10, max_chars=60)
    assert digest.startswith("start start")
    assert len(digest) < 200
