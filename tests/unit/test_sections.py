"""Section trimming, document kinds and excerpt building for Sejm prints."""

import json

import pytest

from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.keywords import KeywordPrefilter
from lexinform.sections import (
    _JUSTIFICATION_RE,
    PAGE_BREAK,
    TextBudget,
    carries_the_document,
    document_kind,
    excerpts,
    page_kind,
    scan_page_window,
    trim_print,
    without_cover_letter,
)
from tests.conftest import FIXTURES, RCL_FIXTURES

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
        "OSR pkt 5-13",
        "raport z konsultacji",
        "tabela zgodności",
        "projekty rozporządzeń",
    ]
    assert result.dropped[1].chars == len(CONSULTATION) + len(ANNEX)  # annex belongs to the report
    assert result.dropped[3].chars == len(REGULATION) + len(REGULATION_JUSTIFICATION)
    assert result.text.count("[pominięto:") == 4


def test_the_kept_pages_keep_their_page_breaks() -> None:
    """The page is the unit every rule in `sections` works in, and a text that reaches the model
    without its form feeds cannot be cut down by the page any more."""
    text = _print("Art. 1. " + "b" * 100, "UZASADNIENIE\n" + "c" * 100)

    result = trim_print(text)

    assert result.text.count(PAGE_BREAK) == 1


def test_rcl_osr_form_is_cut_at_point_5_even_without_the_number() -> None:
    # RCL publishes the OSR as a Word file: the form starts with "Nazwa projektu" and Word keeps
    # the point numbers as list formatting, so the text has no "5." before the heading.
    osr = "Nazwa projektu\nUstawa o ...\nMinisterstwo wiodące\n" + "z" * 300
    tail = "Informacje na temat zakresu, czasu trwania\ni podsumowanie wyników\n" + "0" * 400

    result = trim_print(_print(BILL, osr + "\n" + tail))

    assert "z" * 300 in result.text and "0" * 50 not in result.text
    assert [d.name for d in result.dropped] == ["OSR pkt 5-13"]


def test_an_osr_without_a_point_5_is_still_cut_at_point_6() -> None:
    """Point 6 stays as the fallback: not every form words point 5 the way the template does."""
    osr = "Nazwa projektu\nUstawa o ...\nMinisterstwo wiodące\n" + "z" * 300
    tail = "Wpływ na sektor finansów publicznych\n(ceny stałe z 2026 r.)\n" + "0" * 400

    result = trim_print(_print(BILL, osr + "\n" + tail))

    assert "z" * 300 in result.text and "0" * 50 not in result.text
    assert [d.name for d in result.dropped] == ["OSR pkt 5-13"]


def test_a_heading_on_a_wrapped_line_does_not_open_a_section() -> None:
    """Druk 810 page 40 is the bill — "Art. 156q. 1. Prezes Urzędu … w części A" — and wraps so
    that its third line begins "załącznika do rozporządzenia nr 2019/947/UE". Read as the start
    of an appendix it threw away 119,420 characters of the bill."""
    page = (
        "Art. 156q. 1. Prezes Urzędu, przy użyciu systemu teleinformatycznego BSP,\n"
        "przeprowadza szkolenie oraz egzamin online na warunkach określonych w części A\n"
        "załącznika do rozporządzenia nr 2019/947/UE, w podkategorii A1 i A3\n" + "b" * 400
    )

    result = trim_print(_print(BILL, page, JUSTIFICATION))

    assert result.dropped == () and "b" * 400 in result.text


def test_after_the_osr_no_page_is_the_bill_again() -> None:
    """A table of submitted comments labels every row "Uzasadnienie", so a page of one reads as
    the bill's own justification and re-opened the kept run: druk 1424 sent 270,989 characters
    of a consultation table to the model that way, druk 1677 sent 317,546."""
    comments = "Uzasadnienie\n1. Problemy obecnego systemu\n" + "k" * 400

    result = trim_print(_print(BILL, JUSTIFICATION, OSR + OSR_TAIL, CONSULTATION, comments))

    assert "k" * 400 not in result.text
    assert [d.name for d in result.dropped] == ["OSR pkt 5-13", "raport z konsultacji"]


def test_a_justification_before_the_osr_is_still_kept() -> None:
    """Druk 810's uzasadnienie stands before its OSR with an appendix wrongly detected in front
    of it, so the rule is tied to the OSR and not to the first dropped section."""
    result = trim_print(_print(BILL, COMPLIANCE, JUSTIFICATION, OSR + OSR_TAIL))

    assert "y" * 500 in result.text and "u" * 400 not in result.text


def test_unknown_layout_passes_unchanged() -> None:
    text = _print("SPRAWOZDANIE KOMISJI\n" + "a" * 100, "Art. 1. " + "b" * 100)
    result = trim_print(text)
    assert result.dropped == () and "a" * 100 in result.text and "b" * 100 in result.text


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


def test_a_long_thin_text_is_a_long_scan_and_not_an_appendix_heavy_print() -> None:
    """Druk 703 is 155 pages whose text layer covers three: 6,865 characters, the diacritics
    gone. It used to pass as a document because a ceiling of 4,000 characters turned the density
    test off above it, and the model then read the whole bill as that fragment with nothing on
    the card to say so. Over term 10 that ceiling let 30 prints through, druk 204 (268 pages with
    ten) and druk 348 (362 with sixteen) among them.

    The separation is clean without it: the thinnest print that really is a document runs 314
    characters a page, and the thickest of the 30 runs 215.
    """
    body = "t" * 7_000
    thin = body + PAGE_BREAK.join("" for _ in range(155))
    dense = body + PAGE_BREAK.join("" for _ in range(10))

    assert not carries_the_document(thin, min_chars=200, pages=155)
    assert carries_the_document(dense, min_chars=200, pages=10)


def test_the_same_letter_followed_by_the_bill_carries_the_document() -> None:
    whole = DEPUTIES_COVER + PAGE_BREAK + BILL + PAGE_BREAK + JUSTIFICATION

    assert carries_the_document(whole, min_chars=200)
    assert without_cover_letter(whole).lstrip().startswith("Projekt")


def test_a_scan_loses_its_covering_page_and_nothing_else() -> None:
    """Druk 1273's government position is 10 pages and says "Strona 1 z 9" on the second: the
    first is the letter. The rest of a scanned document is its own substance."""
    position = scan_page_window(10, cover_letter=True)
    unproven = scan_page_window(30, cover_letter=False)
    single = scan_page_window(1, cover_letter=True)

    assert (position.first, position.count) == (1, 9)
    assert (unproven.first, unproven.count) == (0, 30)
    assert (single.first, single.count) == (0, 1)  # nothing else is in there to read


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
    assert "Uzasadnienie" in result.text
    assert len(result.text) <= 2000


def test_budget_keeps_the_keyword_windows_it_is_given() -> None:
    """The passages a bill is relevant for are usually not in its first pages, so a cap that
    only kept the head threw away the very lines that made the bill worth reading.

    The justification heading is what makes this the production shape: every print has one, and
    when the two heads were given half the cap each they filled it between them, so the first
    window measured was always the one over budget and no hit was ever kept.
    """
    text = (
        "Art. 1. " * 500
        + "\nUzasadnienie\n"
        + "Projekt ma na celu. " * 100
        + "Art. 99. Zezwolenie na pobyt czasowy dla cudzoziemca. "
        + "x" * 4000
    )
    spans = KeywordPrefilter().spans(text)

    result = TextBudget(2000).apply(text, spans)

    assert result.truncated and "pobyt czasowy dla cudzoziemca" in result.text


def test_budget_spends_its_whole_cap_when_there_is_no_justification() -> None:
    """With no justification heading and no keyword hit there is nothing to share the cap with,
    and the head takes all of it: a cap that handed back a fraction of what it allows would send
    the model less of the bill than it is paid to read."""
    result = TextBudget(50).apply("x" * 500)

    assert result.truncated and len(result.text) == 50


def test_budget_rejects_a_non_positive_cap() -> None:
    with pytest.raises(ValueError):
        TextBudget(0)


def test_the_dsr_form_is_the_osr_and_not_the_resolution_it_hangs_on() -> None:
    """The deputies' form is an attachment to a resolution of the Presidium of the Sejm, so it
    opens with that and names itself only afterwards. Read by its first two lines it is an
    appendix, and `trim_print` then drops the one document that counts who a deputies' bill
    affects: 14 of the 173 prints of term 10 that carry a DSR, 40,758 characters of druk 1963.

    A page that hangs on a resolution and says nothing else is still an appendix — that branch of
    the rule is not what went wrong, and removing it is not the fix.
    """
    dsr = (
        "Załącznik\ndo uchwały nr 51\nPrezydium Sejmu\nz dnia 26 sierpnia 2024 r.\n"
        "DEKLAROWANE SKUTKI REGULACJI (DSR)\nprojektu ustawy\n" + "t" * 300
    )
    plain = "Załącznik\ndo uchwały nr 51\nPrezydium Sejmu\nz dnia 26 sierpnia 2024 r.\n" + "t" * 300

    assert page_kind(dsr) == "osr"
    assert page_kind(plain) == "annex"


def test_every_page_of_the_corpus_opens_the_section_it_should() -> None:
    """The pages that decided the rule, as they really are in the prints and in the RCL packages
    (`page_starts.json`): the top of the page, and the kind `page_kind` must read there. A new
    failure is one row.

    Druk 1677 page 98 is the pair that shows why two rules are needed and not one: it genuinely
    opens "Uzasadnienie", so no window narrow enough saves it — what keeps that page of a
    consultation table out of the analysis is that the OSR has already been passed.

    The last rows are the draft regulations of the ETIAS package with the ministry's stamp above
    their heading. Left in the opening window it pushed ROZPORZĄDZENIE out of it, the page read
    as `unknown`, and then the sticky regulations rule never caught the block — so each draft's
    own OSR form re-opened the kept run and went to the model.
    """
    rows = json.loads((FIXTURES / "page_starts.json").read_text(encoding="utf-8"))

    wrong = [
        (row["druk"], row["page"], row["kind"], got)
        for row in rows
        if (got := page_kind(row["opening"])) != row["kind"]
    ]

    assert not wrong


def test_every_document_of_the_corpus_is_recognised_by_its_opening() -> None:
    """The whole collected corpus, one row per real document (`openings.json`).

    The rule is only as good as what it was measured on, so what it was measured on is checked
    in: 126 openings taken from the packages of seven followed RCL projects and from 25 Sejm
    prints of term 10 (13 Sept 2026). A new case is one row.
    """
    corpus = json.loads((RCL_FIXTURES / "openings.json").read_text(encoding="utf-8"))

    wrong = [
        f"{row['source']}/{row['name']}: {document_kind(row['opening'])} != {row['kind']}"
        for row in corpus
        if document_kind(row["opening"]) != row["kind"]
    ]

    assert not wrong
    assert len(corpus) > 100


def test_a_bill_heads_itself_in_either_case_and_a_regulation_never_passes_for_one() -> None:
    # Two of the six bills measured write "Ustawa", the rest "USTAWA"; the drafts of executive
    # regulations filed beside a bill are told apart by this line and by nothing else.
    assert document_kind("Projekt z dnia 20.08.2026 r.\nUstawa\nz dnia ……..…….") == "bill"
    assert document_kind("Projekt z dnia 10 lipca 2026 r.\nEtap: SKRM\nUSTAWA") == "bill"
    assert document_kind("Projekt z dnia 9 lipca 2026 r.\nROZPORZĄDZENIE\nMINISTRA") == "regulation"


def test_a_justification_that_wraps_onto_the_word_rozporzadzenia_is_not_a_regulation() -> None:
    # The heading is anchored to its whole line for this reason: the justification of every act
    # implementing an EU regulation says the word, and a line may begin with it.
    text = "UZASADNIENIE\nI. Potrzeba i cel uchwalenia ustawy\nProjektowana ustawa służy\n"
    text += "rozporządzenia 2018/1240 Parlamentu Europejskiego i Rady"

    assert document_kind(text) == "justification"


def test_the_osr_is_recognised_under_each_of_its_three_headings() -> None:
    # "Nazwa projektu" is the usual one; UD439's OSR opens "Tytuł projektu", and the form a
    # deputies' print carries is headed "DEKLAROWANE SKUTKI REGULACJI" (3 of 25 prints measured).
    assert document_kind("Nazwa projektu Ustawa o udziale RP") == "osr"
    assert document_kind("Tytuł projektu Projekt ustawy o zmianie ustawy") == "osr"
    assert document_kind("DEKLAROWANE SKUTKI REGULACJI (DSR)\nprojektu ustawy") == "osr"
    assert document_kind("Nazwa projektu dokumentu: Ustawa o udziale") == "legislative_table"


def test_the_compliance_table_and_the_osr_are_told_apart_by_case_alone() -> None:
    assert document_kind("1TYTUŁ PROJEKTU\tUstawa o udziale") == "compliance_table"
    assert document_kind("Tytuł projektu: ustawa o zmianie ustawy – Kodeks wyborczy") == "osr"


def test_a_running_head_does_not_hide_what_a_page_opens() -> None:
    # Druk 1764 carries its club's name and site as the first line of all thirty of its pages.
    head = "Konfederacja Wolność i Niepodległość  |  konfederacja.pl"
    text = _print(
        f"{head}\n{BILL}",
        f"{head}\n- 2 -\n{JUSTIFICATION}",
        f"{head}\n{CONSULTATION}",
        f"{head}\n{COMPLIANCE}",
    )

    result = trim_print(text)

    assert "Art. 1." in result.text and "Projekt ma na celu" in result.text
    assert [d.name for d in result.dropped] == ["raport z konsultacji", "tabela zgodności"]


def test_a_heading_that_opens_a_page_is_found_after_the_pages_are_joined() -> None:
    """`^` in MULTILINE turns on `\\n` alone, and the pages are joined with a form feed.

    The extractor emits a page from its first glyph, so a print whose justification starts a page
    reads `…\\fUZASADNIENIE\\n` with no newline in front of the heading. Over term 10 that layout
    hides a heading in 683 of the 819 prints with a text layer, and in 279 of them it hides the
    justification entirely — `excerpts` then sends the triage the head of the bill and not one
    line of the reasons for it. Druk 545 is one: page 4 opens "UZASADNIENIE" with nothing above
    it.
    """
    text = BILL + PAGE_BREAK + "UZASADNIENIE\nI. CEL PROJEKTOWANEJ USTAWY\n" + "Cel. " * 200

    digest = excerpts(text, [], head_chars=300, max_chars=2000)

    assert _JUSTIFICATION_RE.search(text) is not None
    assert "UZASADNIENIE\nI. CEL PROJEKTOWANEJ USTAWY" in digest


def test_a_heading_that_ends_a_page_is_found_too() -> None:
    """The other anchor: a heading may be the last line of its page, and then `$` has a form feed
    after it rather than a newline."""
    text = "Projekt ustawy\n\nUZASADNIENIE" + PAGE_BREAK + "Cel projektu. " * 100

    assert _JUSTIFICATION_RE.search(text) is not None
    assert document_kind("Strona tytułowa" + PAGE_BREAK + "R O Z P O R Z Ą D Z E N I E\n") == (
        "regulation"
    )


def test_a_bill_is_not_its_own_justification_because_page_two_says_so() -> None:
    """`HEAD_CHARS` is a budget, not a window: the opening is a page.

    A bill's first page on RCL runs 550-1,150 characters, so a flat 1,200-character window reads
    on into the second page, where the uzasadnienie begins — and `_KINDS` tries `justification`
    before `bill`. Over the corpus that cost 94 documents their kind, every one headed USTAWA on
    its own first page (dokument 716581, 778816, 674571 …), and `_pick_parts` then had no bill.
    """
    first = "Projekt z dnia 21 maja 2026 r.\nUSTAWA\nz dnia 2026 r.\no zmianie ustawy\n" + "x" * 700
    text = first + PAGE_BREAK + "UZASADNIENIE\nCel projektu.\n" + "y" * 700

    assert len(first) < 1200  # the second page is inside a flat window
    assert document_kind(text) == "bill"


def test_a_page_that_says_nothing_hands_the_budget_to_the_next_one() -> None:
    """26 documents of the corpus open on a stamp or a title sheet and name themselves after it
    (dokument 631755 opens 485 characters of heading and is an OSR on its second page)."""
    text = "Ministerstwo Zdrowia\nWarszawa\n" + PAGE_BREAK + "Nazwa projektu\nUstawa o badaniach\n"

    assert document_kind(text) == "osr"
