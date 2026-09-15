"""What a Sejm print (druk) is made of, and which parts the model needs to see.

A print is the bill, its uzasadnienie, the OSR (a 13-point form) and then hundreds of pages of
appendices — 55-80% of the text, and nothing about who the bill affects. `trim_print` drops those
and keeps the bill, the uzasadnienie and OSR points 1-4. `excerpts` builds the short digest the
cheap triage reads; `TextBudget` is the last cap before the model call.

`carries_the_document` comes before all of them: whether the file holds the document at all. Much
of what the Sejm publishes is scanned paper whose only text layer is the transmittal letter.

`PAGE_BREAK` is the form feed the PDF extractor puts between pages, and a section header opens a
page rather than sitting inside it. RCL's Word files carry the OSR's point number as list
formatting rather than text, so the heading is accepted without it.
"""

import re
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

PAGE_BREAK = "\f"

# A page break is a line break, and Python's `^`/`$` do not know it: in MULTILINE they turn on
# `\n` alone, so a heading that opens a page is invisible the moment the pages are joined. It is
# the commonest layout there is — the extractor emits the page's text from its first glyph, so
# "UZASADNIENIE" as the first line of its page stands in the joined text as `…\fUZASADNIENIE\n`,
# with no `\n` in front of it for `^` to match. Measured over term 10 (14 Sept 2026): 683 of the
# 819 prints with a text layer hide at least one heading behind the form feed this way, and in
# **279 of them `_JUSTIFICATION_RE` then finds no justification at all** — so `excerpts` hands
# the model the head of the bill and not one line of the reasons for it, which is what the
# triage judges on and what the per-bill cost guard cuts a long text down to. Adding the page
# break to both anchors recovers the justification in all 279 and 744,693 characters with it.
# The same holds for 525 documents of RCL, where DOCX is worse still: `document_text` collapses
# `\n\f\n` to `\f` on purpose, removing the very newline that would have saved the match.
_BOL = r"(?:^|(?<=\f))"
_EOL = r"(?:$|(?=\f))"

_OPENING_LINES = 2
"""How many lines of a page may carry the heading that opens a section.

A heading that starts a section stands at the top of its page; a heading-shaped line further
down is prose that happened to wrap that way. Druk 810 page 40 is the bill — "Art. 156q. 1.
Prezes Urzędu … w części A" — and its third line begins "załącznika do rozporządzenia nr
2019/947/UE", which read as the start of an appendix and threw away 119,420 characters of the
bill. One line is too few (the corpus loses 620k characters of appendices that announce
themselves on the second line, such as "Projekt" over "R O Z P O R Z Ą D Z E N I E"), three is
already enough to reach druk 810's wrapped line.
"""

_JUSTIFICATION_RE = re.compile(
    rf"{_BOL}\s*u\s?z\s?a\s?s\s?a\s?d\s?n\s?i\s?e\s?n\s?i\s?e\s*{_EOL}",
    re.IGNORECASE | re.MULTILINE,
)
# Two of the six bills measured head themselves "Ustawa" rather than "USTAWA" (UD439, UC145).
# The whole-line anchor is what keeps it honest: the word is everywhere in a bill's prose and
# nowhere alone on a line but in its title.
_BILL_HEADING_RE = re.compile(
    rf"{_BOL}\s*U\s?S\s?T\s?A\s?W\s?A\s*{_EOL}", re.IGNORECASE | re.MULTILINE
)
_OSR_RE = re.compile(
    rf"{_BOL}\s*(Nazwa|Tytuł)\s+projektu\b(?!\s+dokumentu)|{_BOL}\s*DEKLAROWANE\s+SKUTKI",
    re.MULTILINE,
)
# The deputies' DSR form is itself an attachment to a resolution of the Presidium of the Sejm, so
# it opens "Załącznik do uchwały nr 51 Prezydium Sejmu z dnia 26 sierpnia 2024 r." and the annex
# pattern swallowed it on its own masthead. Measured over term 10 (14 Sept 2026): 14 of the 173
# prints carrying a DSR were read as an appendix that way, and `trim_print` then dropped the form
# — 40,758 characters of druk 1963, over half the print, and with them the one count of the
# affected a deputies' bill gives.
_DSR_RE = re.compile(rf"{_BOL}\s*DEKLAROWANE\s+SKUTKI", re.MULTILINE)
# Point 5 of the 13-point form, not point 6. Point 5 ("Informacje na temat zakresu, czasu trwania
# i podsumowanie wyników konsultacji") is the roll of organisations the draft was sent to — 5,136
# characters in druk 1677, 10,187 in druk 1479 — and names nobody the bill affects. Point 4
# ("Podmioty, na które oddziałuje projekt") is the one count of the affected a print gives, and it
# survives in all 26 prints of the corpus that have a point 5 (measured 13 Sept 2026). Point 6
# stays as the fallback for a form that words point 5 differently or does not carry it.
_OSR_CUT_RE = re.compile(
    rf"{_BOL}\s*(?:5\.\s*)?Informacje\s+na\s+temat\s+zakresu"
    rf"|{_BOL}\s*(?:6\.\s*)?Wpływ na sektor finans",
    re.MULTILINE,
)
# What makes this safe is the anchor, not the case: the justification of every act implementing
# an EU regulation wraps onto lines beginning "rozporządzenia 2018/1240", and none of them is the
# word alone. Measured over the 147 openings of the corpus, ignoring case moves exactly one
# document, and it moves it right — a draft headed "Rozporządzenie" that had passed for a bill's
# uzasadnienie because its file name said so.
_REGULATION_RE = re.compile(
    rf"{_BOL}\s*R\s?O\s?Z\s?P\s?O\s?R\s?Z\s?Ą\s?D\s?Z\s?E\s?N\s?I\s?E\s*{_EOL}",
    re.IGNORECASE | re.MULTILINE,
)
_CONSULTATION_RE = re.compile(
    rf"{_BOL}\s*Raport\s+z\s+(konsultacji|opiniowania|uzgodnie)"
    r"|Zgodnie\s+z\s+art\.\s*5\s+ustawy.{0,120}?działalności\s+lobbingowej",
    re.I | re.M | re.S,
)
_REMARKS_RE = re.compile(
    rf"{_BOL}\s*(Zestawienie|Tabela)\s+(z\s+)?(uwag|nieuwzględnionych|uzgodnień)",
    re.IGNORECASE | re.M,
)
_COMPLIANCE_RE = re.compile(
    rf"{_BOL}\s*(Odwrócona\s+)?Tabela\s+zgodności\b"
    rf"|{_BOL}\s*Tabelaryczne\s+zestawienie\s+przepisów"
    # The columns of a table of provisions, whatever it calls itself. Druk 1430 heads its
    # derivation table "Tabelaryczne zestawienie przepisów …" and then, further down the same
    # page, "Tytuł projektu:" — which is how an OSR form opens, and it was read as one.
    rf"|{_BOL}\s*Jedn\.\s*red\b|Treść\s+przepisu",
    re.IGNORECASE | re.M,
)
# Case-sensitive on purpose, and the one discriminator there is: the compliance table opens with
# "TYTUŁ PROJEKTU" as a column header, the OSR form with "Tytuł projektu" as a field label
# (UD439's OSR, 13 Sept 2026). A digit may be glued to the header by the table's numbering.
_COMPLIANCE_HEADER_RE = re.compile(rf"{_BOL}\s*\d*\s*TYTUŁ\s+PROJEKTU\b", re.MULTILINE)
_DISCREPANCIES_RE = re.compile(
    rf"{_BOL}\s*Protok[oó][łl]\s+rozbie[żz]no[śs]ci", re.IGNORECASE | re.M
)
_LEGISLATIVE_TABLE_RE = re.compile(
    rf"{_BOL}\s*Nazwa\s+projektu\s+dokumentu\b", re.IGNORECASE | re.M
)
_CHECKLIST_RE = re.compile(rf"{_BOL}\s*(WZ[ÓO]R\s*)?LISTA\s+KONTROLNA\b", re.MULTILINE)
# "Załącznik do …" names what it hangs on; a bare "Załącznik nr 2" is deliberately not here,
# because a bill carries its own schedules under that heading and dropping from one would take
# the rest of the bill with it (druk 2673 has one on page 25 of 60).
_ANNEX_RE = re.compile(
    rf"{_BOL}\s*Załącznik\w*\s+do\s+(uchwały|rozporządzenia|raportu)\b", re.IGNORECASE | re.M
)

KEEP = "keep"
OSR = "osr"
OSR_TAIL = "OSR pkt 5-13"
CONSULTATION = "raport z konsultacji"
REMARKS = "zestawienie uwag"
COMPLIANCE = "tabela zgodności"
REGULATIONS = "projekty rozporządzeń"
ANNEX = "załącznik"

_DROPPED = frozenset({OSR_TAIL, CONSULTATION, REMARKS, COMPLIANCE, REGULATIONS, ANNEX})

Kind = Literal[
    "bill",
    "regulation",
    "justification",
    "osr",
    "compliance_table",
    "consultation_report",
    "remarks_table",
    "discrepancies",
    "legislative_table",
    "checklist",
    "annex",
    "letter",
    "unknown",
]

HEAD_CHARS = 1200
"""How much of a document says what it is: its own heading sits in the first lines."""

# Order matters: a narrow pattern comes before the wide one it would otherwise be swallowed by.
# "Nazwa projektu dokumentu" opens a tabela legislacyjna and differs from the OSR form's
# "Nazwa projektu" by one word; "TYTUŁ PROJEKTU" differs from "Tytuł projektu" by case alone.
_KINDS: tuple[tuple[Kind, re.Pattern[str]], ...] = (
    ("legislative_table", _LEGISLATIVE_TABLE_RE),
    ("compliance_table", _COMPLIANCE_HEADER_RE),
    ("compliance_table", _COMPLIANCE_RE),
    ("discrepancies", _DISCREPANCIES_RE),
    ("consultation_report", _CONSULTATION_RE),
    ("remarks_table", _REMARKS_RE),
    ("checklist", _CHECKLIST_RE),
    ("regulation", _REGULATION_RE),
    ("osr", _DSR_RE),
    ("annex", _ANNEX_RE),
    ("osr", _OSR_RE),
    ("justification", _JUSTIFICATION_RE),
    ("bill", _BILL_HEADING_RE),
)


def document_kind(text: str) -> Kind:
    """What a document is, read from its own opening rather than from its file name.

    The name is what a ministry typed; the opening is what the document says it is. Drafts of
    executive regulations travel with a bill under the same file names and say ROZPORZĄDZENIE
    where the bill says USTAWA.

    **The opening is a page, and the first page that speaks wins.** `HEAD_CHARS` is a budget, not
    the window: a flat 1,200 reads into the second page where the uzasadnienie begins, and
    `_KINDS` tries `justification` first, which cost 94 bills of the corpus. A page saying nothing
    about itself is not an opening, so the budget carries to the next (26 documents open on a
    stamp).

    Headings are matched case-sensitively and anchored to the whole line, so a wrapped line
    beginning "rozporządzenia 2018/1240" is not a regulation. `unknown` is the honest answer.
    """
    budget = HEAD_CHARS
    for page in text.split(PAGE_BREAK):
        if not page.strip():
            continue
        for kind, pattern in _KINDS:
            if pattern.search(page[:budget]):
                return kind
        budget -= len(page)
        if budget <= 0:
            break
    return "letter" if has_cover_letter(text) else "unknown"


APPENDIX_KINDS: frozenset[Kind] = frozenset(
    {
        "regulation",
        "compliance_table",
        "consultation_report",
        "remarks_table",
        "discrepancies",
        "legislative_table",
        "checklist",
        "annex",
    }
)
"""Kinds that are never the bill and never open a document that holds it.

`letter` is deliberately not among them: every Sejm print opens with the letter that hands it to
the Marshal, so a document beginning as one may still be the bill. `unknown` is not among them
either — a layout we do not know is not an appendix, it is a layout we do not know.
"""


_SECTION_OF_KIND: dict[Kind, str] = {
    "bill": KEEP,
    "justification": KEEP,
    "osr": OSR,
    "regulation": REGULATIONS,
    "consultation_report": CONSULTATION,
    "remarks_table": REMARKS,
    "compliance_table": COMPLIANCE,
    "annex": ANNEX,
}


@dataclass(frozen=True)
class DroppedSection:
    name: str
    chars: int


@dataclass(frozen=True)
class TrimmedText:
    text: str
    dropped: tuple[DroppedSection, ...] = ()


def _section_start(page: str, current: str, *, after_osr: bool = False) -> str:
    """The section a page opens, or the one it continues: everything after the first draft
    regulation belongs to the drafts.

    `after_osr` says the OSR has been reached, and then no page is the bill or its justification
    again: a print runs letter, bill, uzasadnienie, OSR, appendices, once each in that order.
    Otherwise a consultation table, which labels every row "Uzasadnienie", re-opens the kept run —
    druki 1424 and 1677 sent 270,989 and 317,546 characters of one. It is tied to the OSR and not
    to the first dropped section because druk 810's uzasadnienie stands before its OSR.
    """
    if current == REGULATIONS:
        return REGULATIONS
    started = _SECTION_OF_KIND.get(page_kind(page), current)
    return current if after_osr and started == KEEP else started


def page_kind(page: str, running_head: str | None = None) -> Kind:
    """What a page announces itself as: the printer's furniture off the top, then its opening.

    One rule in one place, measured against `tests/fixtures/sejm/page_starts.json`. Stripping is
    idempotent, so an already stripped page may be passed in as it stands.

    The DSR form is the one heading looked for past the two-line window: the deputies' form opens
    "Załącznik / do uchwały nr 51 / Prezydium Sejmu" and names itself only on the next line, so
    `_ANNEX_RE` read it as an appendix and `trim_print` dropped it (14 of the 173 DSR prints of
    term 10). Widening the window would undo what it is for.
    """
    stripped = strip_page_furniture(page, running_head)
    if _DSR_RE.search(stripped[:HEAD_CHARS]):
        return "osr"
    return document_kind(_page_opening(stripped))


def _page_opening(page: str) -> str:
    """The first `_OPENING_LINES` non-empty lines of a page: where a section heading can be."""
    lines: list[str] = []
    seen = 0
    for line in page.split("\n"):
        lines.append(line)
        if line.strip():
            seen += 1
            if seen == _OPENING_LINES:
                break
    return "\n".join(lines)


_PAGE_NUMBER_LINE = re.compile(r"^\s*[–\-—]?\s*\d{1,4}\s*[–\-—]?\s*$", re.MULTILINE)

_DRAFT_STAMP_LINE = re.compile(r"^\s*(Projekt\s+z\s+(dnia\s+)?\d|Etap\s*:)", re.IGNORECASE)
"""The date and stage a ministry stamps over a draft, above its heading.

"Projekt z dnia 9 lipca 2026 r." and "Etap: materiał informacyjny na SKRM" stand between the top
of the page and the ROZPORZĄDZENIE or USTAWA that opens it. Two such lines push the heading out
of the opening window, and for a draft regulation that is expensive: the page then reads as
`unknown`, the sticky regulations rule never engages, and the draft's own OSR form re-opens the
kept run. Nine of the corpus's openings carry the stamp, five of them regulations.
"""


def strip_page_furniture(page: str, running_head: str | None) -> str:
    """A page without what the printer put on it: its running head, its number, its draft stamp.

    A section heading is looked for in the opening of a page, and whatever the printer put above
    it stands in front — druk 1764 carries "Konfederacja Wolność i Niepodległość |
    konfederacja.pl" as the first line of all thirty of its pages. Left in place it hides
    whatever the page really opens.
    """
    lines = page.split("\n")
    start = 0
    while start < len(lines) and _is_furniture(lines[start], running_head):
        start += 1
    return "\n".join(lines[start:])


def _is_furniture(line: str, running_head: str | None) -> bool:
    stripped = line.strip()
    if not stripped or _PAGE_NUMBER_LINE.fullmatch(line) or _DRAFT_STAMP_LINE.match(stripped):
        return True
    return running_head is not None and stripped == running_head


def _running_head(pages: Sequence[str]) -> str | None:
    """The line that opens more than half the pages, when it is not their numbering."""
    firsts = [first for page in pages if (first := _first_line(page))]
    if len(firsts) < 4:
        return None
    candidate = max(set(firsts), key=firsts.count)
    return candidate if firsts.count(candidate) * 2 > len(firsts) else None


def _first_line(page: str) -> str:
    for line in page.split("\n"):
        stripped = line.strip()
        if stripped and not _PAGE_NUMBER_LINE.fullmatch(line):
            return stripped
    return ""


_COVER_LIMIT = 3000
_TRANSMITTAL_RE = re.compile(
    r"art\.\s*118\s*ust\.\s*1\s*Konstytucji"
    r"|wnosz[ąa]\s+projekt\s+ustawy"
    r"|przekazuj[ęe]\s+(?:przyj[ęe]te|w\s+za[łl][ąa]czeniu)",
    re.IGNORECASE,
)
_BODY_RE = re.compile(
    rf"{_BOL}\s*U\s?S\s?T\s?A\s?W\s?A\b"
    rf"|{_BOL}\s*Art\.\s*1\s*[.)]"
    rf"|{_BOL}\s*u\s?z\s?a\s?s\s?a\s?d\s?n\s?i\s?e\s?n\s?i\s?e\s*{_EOL}"
    rf"|{_BOL}\s*Nazwa projektu\b"
    rf"|{_BOL}\s*Projekt\s*{_EOL}",
    re.IGNORECASE | re.MULTILINE,
)


def without_cover_letter(text: str) -> str:
    """The document itself: what follows the letter that hands it to the Marshal.

    The document proper starts on the next page or at its own heading, whichever comes first. A
    text with no such opening (a committee report, an RCL file) is returned unchanged.
    """
    match = _TRANSMITTAL_RE.search(text[:_COVER_LIMIT])
    if match is None:
        return text
    body = _BODY_RE.search(text, match.end())
    page = text.find(PAGE_BREAK, match.end())
    cuts = [cut for cut in (body.start() if body else -1, page) if cut >= 0]
    return text[min(cuts) :] if cuts else ""


MIN_CHARS_PER_PAGE = 300
"""Below this a PDF's text layer is not its document, however well it reads.

Measured over 30 prints drawn at random from term 10 (2026-09-12, this project's extractor):
the scan among them (druk 603, 14 pages) yields 139 characters a page and nothing at all once
its covering letter is cut, while the thinnest real document (druk 325, 50 pages of a sparse
print) yields 477 and the median is ~2,200. The threshold sits between the two with room on
both sides, because what it separates is a text layer from a photograph of paper.
"""

SCANNED_TEXT_CEILING = 4_000
"""Kept for the record: density is now judged whatever the length, and this bounds nothing.

It used to mean "above this many characters the text is the document whatever its density", on
the reasoning that a print can be appendix-heavy and text that long is still text to read. Over
the whole of term 10 that reasoning does not hold: it is the OCR layer of a scan that runs long
and thin, not an appendix-heavy document. Of the 804 prints classified `text` that are PDFs,
**26 ran under 300 characters a page** — druk 703 is 155 pages with text on three (6,865
characters, and the diacritics gone: "norki amerykanskiej"), druk 204 is 268 pages with ten,
druk 348 is 362 with sixteen — and each went to the model as that fragment with nothing on the
card to say so. The separation is clean without a ceiling: the thinnest print that is really a
document runs 314 characters a page, the thickest of the 26 runs 215.
"""


def has_cover_letter(text: str) -> bool:
    """Whether the text opens with the letter that hands the document to the Marshal.

    What decides whether the first page of a scan is dropped: any text at all is not the same
    finding — a running head or a title page left by an OCR pass is text too, and dropping the
    first page because of one would throw away a page of the document.
    """
    return _TRANSMITTAL_RE.search(text[:_COVER_LIMIT]) is not None


def carries_the_document(text: str, *, min_chars: int, pages: int = 0) -> bool:
    """Whether the extracted text is the document at all, or a scrap of paper around it.

    Sejm papers are scanned and filed as images: of 66 documents filed to prints, 55 have no text
    layer and the other 11 carry the covering letter alone — 700-820 characters that read as a
    document and pass any length threshold. A letter is not the only such scrap (a title page, a
    running head), so a short text is measured against `pages` as well: "long enough to read"
    becomes "enough for a document of this many pages".
    """
    body = without_cover_letter(text).strip()
    if len(body) < min_chars:
        return False
    return not _too_thin_for_its_pages(body, pages)


def _too_thin_for_its_pages(body: str, pages: int) -> bool:
    # No ceiling: a long thin text is a long scan, not an appendix-heavy document (see
    # SCANNED_TEXT_CEILING). `pages` is 0 for a format that has none, and then there is nothing
    # to measure the text against.
    if pages <= 0:
        return False
    return len(body) / pages < MIN_CHARS_PER_PAGE


@dataclass(frozen=True)
class PageWindow:
    """The pages of a scan worth their tokens: `count` pages from `first` (0-based)."""

    first: int
    count: int


def scan_page_window(pages: int, *, cover_letter: bool) -> PageWindow:
    """Which pages of a scanned document to put before the model.

    The transmittal letter is one page (all 15 government prints measured) and goes when the text
    layer proved it is there. Nothing else does: a scan is substance from the first page to the
    last, and nothing short of reading it can tell which page is chaff.
    """
    first = 1 if cover_letter and pages > 1 else 0
    return PageWindow(first, pages - first)


def trim_print(text: str) -> TrimmedText:
    """Drop the appendices of a print; unknown layouts (Senate texts, reports) pass unchanged.

    Every dropped run is replaced by one Polish marker line so the model knows the text is not
    complete there. The pages that survive are rejoined with the form feed they were split on:
    the page is the unit every rule here works in, and a text that arrives at the model without
    its page breaks cannot be reduced by the page again further down.
    """
    pages = text.split(PAGE_BREAK)
    running_head = _running_head(pages)
    kept: list[str] = []
    dropped: list[DroppedSection] = []
    current = KEEP
    after_osr = False
    for page in pages:
        current = _section_start(
            strip_page_furniture(page, running_head), current, after_osr=after_osr
        )
        if current in (OSR, OSR_TAIL):
            after_osr = True
        if current == OSR and (cut := _OSR_CUT_RE.search(page)):
            kept.append(page[: cut.start()].rstrip())
            _drop(dropped, kept, OSR_TAIL, len(page) - cut.start())
            current = OSR_TAIL
        elif current in _DROPPED:
            _drop(dropped, kept, current, len(page))
        else:
            kept.append(page)
    if not any(page for page in kept if not page.startswith("\n[pominięto: ")):
        return TrimmedText(text=text.strip())
    return TrimmedText(text=PAGE_BREAK.join(kept).strip(), dropped=tuple(dropped))


def _drop(dropped: list[DroppedSection], kept: list[str], name: str, chars: int) -> None:
    if dropped and dropped[-1].name == name and kept and kept[-1].startswith("\n[pominięto: "):
        dropped[-1] = DroppedSection(name, dropped[-1].chars + chars)
        kept[-1] = _marker(dropped[-1])
        return
    dropped.append(DroppedSection(name, chars))
    kept.append(_marker(dropped[-1]))


def _marker(section: DroppedSection) -> str:
    chars = f"{section.chars:,}".replace(",", " ")
    return f"\n[pominięto: {section.name}, {chars} znaków]\n"


_WINDOW = 1200
"""How much text around a keyword hit reads as its context: a provision and what frames it."""


def excerpts(
    text: str,
    spans: Sequence[tuple[int, int]],
    *,
    head_chars: int = 3000,
    window: int = _WINDOW,
    max_chars: int = 24_000,
    fill_head: bool = False,
) -> str:
    """The start of the bill, the start of the justification and text around each keyword hit.

    Segments are merged when they overlap and returned in document order. Windows are added until
    `max_chars` is reached; the two heads are always included. `fill_head` gives what the windows
    left unspent back to the head, and only a cap asks for it — the triage digest is paid for by
    the character, so few keywords should mean less read, not more.
    """
    merged: list[tuple[int, int]] = [(0, min(head_chars, len(text)))]
    if match := _JUSTIFICATION_RE.search(text):
        _merge_in(merged, (match.start(), min(match.start() + head_chars, len(text))))
    used = sum(end - start for start, end in merged)
    for start, end in sorted(spans):
        piece = (max(0, start - window), min(len(text), end + window))
        added = _uncovered(merged, piece)
        if used + added > max_chars:
            break
        _merge_in(merged, piece)
        used += added
    if fill_head and used < max_chars:
        _merge_in(merged, (0, min(merged[0][1] + max_chars - used, len(text))))
    return "\n[...]\n".join(text[start:end].strip() for start, end in merged)


def _merge_in(merged: list[tuple[int, int]], piece: tuple[int, int]) -> None:
    """Add one segment to a list kept sorted and non-overlapping."""
    start, end = piece
    index = bisect_left(merged, (start, end))
    merged.insert(index, piece)
    joined: list[tuple[int, int]] = []
    for begin, stop in merged:
        if joined and begin <= joined[-1][1]:
            joined[-1] = (joined[-1][0], max(stop, joined[-1][1]))
        else:
            joined.append((begin, stop))
    merged[:] = joined


def _uncovered(merged: Sequence[tuple[int, int]], piece: tuple[int, int]) -> int:
    """How many characters of `piece` no segment covers yet."""
    start, end = piece
    covered = sum(
        min(end, stop) - max(start, begin) for begin, stop in merged if begin < end and start < stop
    )
    return max(0, end - start - covered)


@dataclass(frozen=True)
class BudgetedText:
    text: str
    truncated: bool


class TextBudget:
    """Safety cap on the characters sent to the model.

    `excerpts` keeps the head, the start of the justification and a window around each of
    `spans`; passing the keyword hits as `spans` makes the cut a choice rather than a guillotine,
    the relevant passages usually not being in the first pages. Half the cap goes to the two
    heads, and each window is narrowed with the cap, or a fixed width would never fit a small one.
    """

    def __init__(self, max_chars: int) -> None:
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        self._max = max_chars

    def apply(self, text: str, spans: Sequence[tuple[int, int]] = ()) -> BudgetedText:
        if len(text) <= self._max:
            return BudgetedText(text=text, truncated=False)
        cut = excerpts(
            text,
            spans,
            # A quarter each: `excerpts` keeps both heads whatever it is asked for, so a half
            # each fills the cap before the first window is measured and no keyword hit is ever
            # kept. What the windows do not take goes back to the head, or a text with no
            # justification heading would come back a quarter of the length the cap allows —
            # a cap gives what it is asked for.
            head_chars=self._max // 4,
            window=min(_WINDOW, self._max // 8),
            max_chars=self._max,
            fill_head=True,
        )
        # `excerpts` counts the text it keeps and not the markers it joins it with, and this is a
        # cap: what it is asked for is what it gives.
        return BudgetedText(text=cut[: self._max], truncated=True)
