"""What must hold of `sections` for any document, not only for the layouts the examples use.

Every rule in `sections` decides what the model is shown and what it is charged for, and its
input is a PDF or a Word file from outside this repo: a ministry's stamp, an OCR layer with the
diacritics gone, a page that is one wrapped line, a form feed where a newline was expected. The
example tests pin the layouts the corpus showed; these pin the guarantees that must survive a
layout nobody has seen.

The strategies are built from the shapes the corpus actually holds — headings with the letter
spacing the Sejm prints them in, page numbers, a ministry's draft stamp, a transmittal formula,
Polish diacritics and the runs of whitespace an extractor leaves behind — because a guarantee
tested on `"aaa"` is a guarantee about `"aaa"`.
"""

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from lexinform.sections import (
    HEAD_CHARS,
    PAGE_BREAK,
    TextBudget,
    document_kind,
    excerpts,
    page_kind,
    strip_page_furniture,
    trim_print,
    without_cover_letter,
)

MARKER = "[pominięto: "

# The fragments a real page is made of: headings as the Sejm letterspaces them, the printer's
# furniture, a transmittal formula, prose with Polish diacritics, and whitespace runs.
FRAGMENTS = st.sampled_from(
    [
        "USTAWA",
        "U S T A W A",
        "UZASADNIENIE",
        "U Z A S A D N I E N I E",
        "ROZPORZĄDZENIE",
        "R O Z P O R Z Ą D Z E N I E",
        "Nazwa projektu",
        "Tytuł projektu",
        "TYTUŁ PROJEKTU",
        "DEKLAROWANE SKUTKI REGULACJI",
        "Załącznik do uchwały nr 51",
        "Załącznik nr 2",
        "Raport z konsultacji publicznych",
        "Zestawienie uwag",
        "Tabela zgodności",
        "Protokół rozbieżności",
        "LISTA KONTROLNA",
        "Jedn. red.",
        "Projekt z dnia 9 lipca 2026 r.",
        "Etap: materiał informacyjny na SKRM",
        "Art. 1. W ustawie z dnia 6 czerwca 1997 r.",
        "5. Informacje na temat zakresu, czasu trwania",
        "6. Wpływ na sektor finansów publicznych",
        "na podstawie art. 118 ust. 1 Konstytucji wnoszą projekt ustawy",
        "– 3 –",
        "12",
        "Konfederacja Wolność i Niepodległość | konfederacja.pl",
        "cudzoziemiec składa wniosek o zezwolenie na pobyt czasowy",
        "norki amerykanskiej",
        "\t",
        "  ",
        "\n",
        "",
    ]
)
LINES = st.lists(FRAGMENTS, max_size=12).map("\n".join)
PAGES = st.lists(LINES, min_size=1, max_size=8).map(PAGE_BREAK.join)
SLOW = settings(max_examples=250, suppress_health_check=[HealthCheck.too_slow], deadline=None)


@given(PAGES)
@SLOW
def test_trimming_never_invents_text(text: str) -> None:
    """Every page that survives is a page of the input, or a prefix of one where the OSR was cut.

    The model is told the text is the bill; a page the trimmer composed would be a sentence no
    one wrote, attributed to the Sejm.
    """
    result = trim_print(text)

    for part in result.text.split(PAGE_BREAK):
        if part.lstrip().startswith(MARKER) or not part.strip():
            continue
        assert part.strip() in text


@given(PAGES)
@SLOW
def test_trimming_never_grows_the_text(text: str) -> None:
    result = trim_print(text)

    assert len(result.text) <= len(text.strip()) + len(result.dropped) * 60


@given(PAGES)
@SLOW
def test_the_kinds_are_total(text: str) -> None:
    """`document_kind` and `page_kind` answer for any input; `unknown` is the honest answer and
    an exception here would cost the bill an analysis attempt."""
    assert document_kind(text)
    assert page_kind(text)
    assert page_kind(text, "Konfederacja Wolność i Niepodległość | konfederacja.pl")


@given(PAGES, st.one_of(st.none(), FRAGMENTS))
@SLOW
def test_stripping_furniture_is_idempotent(text: str, head: str | None) -> None:
    """`page_kind` may be handed a page `trim_print` has already stripped."""
    once = strip_page_furniture(text, head)

    assert strip_page_furniture(once, head) == once
    assert once in text


@given(PAGES)
@SLOW
def test_the_document_is_a_suffix_of_the_text_it_came_in(text: str) -> None:
    """Cutting the covering letter takes off a head and nothing else: an appendix cut from the
    tail would be trimming disguised as letter removal."""
    body = without_cover_letter(text)

    assert body == "" or text.endswith(body)


@given(PAGES, st.integers(min_value=200, max_value=5000))
@SLOW
def test_excerpts_are_excerpts(text: str, cap: int) -> None:
    """Each segment is a piece of the source, in document order, and the cap is kept."""
    digest = excerpts(text, [], head_chars=cap // 4, max_chars=cap)

    position = -1
    for segment in digest.split("\n[...]\n"):
        if not segment:
            continue
        assert segment in text
        found = text.find(segment, position + 1)
        assert found > position
        position = found


@given(PAGES, st.integers(min_value=200, max_value=5000))
@SLOW
def test_a_budget_gives_no_more_than_its_cap(text: str, cap: int) -> None:
    budget = TextBudget(cap)

    result = budget.apply(text)

    assert len(result.text) <= cap
    assert result.truncated == (len(text) > cap)


@given(PAGES)
@SLOW
def test_a_budget_over_the_cap_spends_most_of_it(text: str) -> None:
    """A cap gives what it is asked for. Halving the head between the two heads used to fill the
    cap before the first keyword window was measured, and a text with no justification heading
    came back a quarter of the length allowed."""
    cap = max(200, len(text) // 2)

    result = TextBudget(cap).apply(text)

    if result.truncated:
        assert len(result.text) >= cap // 2


@given(st.text(max_size=HEAD_CHARS * 2))
@SLOW
def test_arbitrary_text_is_still_answered(text: str) -> None:
    """The extractor hands over whatever the file held: lone surrogates' escapes, NULs, a page of
    combining marks. None of it may raise on the way to the model."""
    assert document_kind(text)
    assert page_kind(text)
    trim_print(text)
    without_cover_letter(text)
    excerpts(text, [])
