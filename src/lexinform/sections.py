"""What a Sejm print (druk) is made of, and which parts the model needs to see.

A government print is the bill itself, its justification (uzasadnienie), the regulatory impact
assessment (OSR, a fixed 13-point form) and then hundreds of pages of appendices: the report from
public consultations with tables of comments, EU compliance tables (tabela zgodności) and drafts
of executive regulations, each with its own justification and OSR. Measured on real prints the
appendices are 55-80% of the text and say nothing about who the bill affects. `trim_print` drops
them and keeps the bill, the justification and OSR points 1-5 (problem, solution, affected
parties, consultations).

`excerpts` builds the short digest used for the cheap relevance triage: the beginning of the bill,
the beginning of the justification and windows of text around every keyword hit. `TextBudget` is
the last safety cap before the model call.

`carries_the_document` asks the question that comes before all of them: whether the file has the
document in it at all. Much of what the Sejm publishes is scanned paper whose only text layer is
the letter that hands it to the Marshal.

The PDF extractor separates pages with a form feed, which is what `PAGE_BREAK` is, and a section
header sits within the first few hundred characters of a page. The OSR form's point 6 is where
the trim cuts; RCL's Word files carry that number as list formatting rather than as text, so the
heading is accepted without it.
"""

import re
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

PAGE_BREAK = "\f"

_HEAD = 600

_JUSTIFICATION_RE = re.compile(
    r"^\s*u\s?z\s?a\s?s\s?a\s?d\s?n\s?i\s?e\s?n\s?i\s?e\s*$", re.IGNORECASE | re.MULTILINE
)
# Two of the six bills measured head themselves "Ustawa" rather than "USTAWA" (UD439, UC145).
# The whole-line anchor is what keeps it honest: the word is everywhere in a bill's prose and
# nowhere alone on a line but in its title.
_BILL_HEADING_RE = re.compile(r"^\s*U\s?S\s?T\s?A\s?W\s?A\s*$", re.IGNORECASE | re.MULTILINE)
_OSR_RE = re.compile(
    r"^\s*(Nazwa|Tytuł)\s+projektu\b(?!\s+dokumentu)|^\s*DEKLAROWANE\s+SKUTKI", re.MULTILINE
)
_OSR_CUT_RE = re.compile(r"^\s*(?:6\.\s*)?Wpływ na sektor finans", re.MULTILINE)
# What makes this safe is the anchor, not the case: the justification of every act implementing
# an EU regulation wraps onto lines beginning "rozporządzenia 2018/1240", and none of them is the
# word alone. Measured over the 147 openings of the corpus, ignoring case moves exactly one
# document, and it moves it right — a draft headed "Rozporządzenie" that had passed for a bill's
# uzasadnienie because its file name said so.
_REGULATION_RE = re.compile(
    r"^\s*R\s?O\s?Z\s?P\s?O\s?R\s?Z\s?Ą\s?D\s?Z\s?E\s?N\s?I\s?E\s*$", re.IGNORECASE | re.MULTILINE
)
_CONSULTATION_RE = re.compile(
    r"^\s*Raport\s+z\s+(konsultacji|opiniowania|uzgodnie)"
    r"|Zgodnie\s+z\s+art\.\s*5\s+ustawy.{0,120}?działalności\s+lobbingowej",
    re.I | re.M | re.S,
)
_REMARKS_RE = re.compile(
    r"^\s*(Zestawienie|Tabela)\s+(z\s+)?(uwag|nieuwzględnionych|uzgodnień)", re.IGNORECASE | re.M
)
_COMPLIANCE_RE = re.compile(
    r"^\s*(Odwrócona\s+)?Tabela\s+zgodności\b"
    r"|^\s*Tabelaryczne\s+zestawienie\s+przepisów"
    # The columns of a table of provisions, whatever it calls itself. Druk 1430 heads its
    # derivation table "Tabelaryczne zestawienie przepisów …" and then, further down the same
    # page, "Tytuł projektu:" — which is how an OSR form opens, and it was read as one.
    r"|^\s*Jedn\.\s*red\b|Treść\s+przepisu",
    re.IGNORECASE | re.M,
)
# Case-sensitive on purpose, and the one discriminator there is: the compliance table opens with
# "TYTUŁ PROJEKTU" as a column header, the OSR form with "Tytuł projektu" as a field label
# (UD439's OSR, 13 Sept 2026). A digit may be glued to the header by the table's numbering.
_COMPLIANCE_HEADER_RE = re.compile(r"^\s*\d*\s*TYTUŁ\s+PROJEKTU\b", re.MULTILINE)
_DISCREPANCIES_RE = re.compile(r"^\s*Protok[oó][łl]\s+rozbie[żz]no[śs]ci", re.IGNORECASE | re.M)
_LEGISLATIVE_TABLE_RE = re.compile(r"^\s*Nazwa\s+projektu\s+dokumentu\b", re.IGNORECASE | re.M)
_CHECKLIST_RE = re.compile(r"^\s*(WZ[ÓO]R\s*)?LISTA\s+KONTROLNA\b", re.MULTILINE)
# "Załącznik do …" names what it hangs on; a bare "Załącznik nr 2" is deliberately not here,
# because a bill carries its own schedules under that heading and dropping from one would take
# the rest of the bill with it (druk 2673 has one on page 25 of 60).
_ANNEX_RE = re.compile(
    r"^\s*Załącznik\w*\s+do\s+(uchwały|rozporządzenia|raportu)\b", re.IGNORECASE | re.M
)

KEEP = "keep"
OSR = "osr"
OSR_TAIL = "OSR pkt 6-13"
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
    ("annex", _ANNEX_RE),
    ("osr", _OSR_RE),
    ("justification", _JUSTIFICATION_RE),
    ("bill", _BILL_HEADING_RE),
)


def document_kind(text: str) -> Kind:
    """What a document is, read from its own opening rather than from its file name.

    The name is what a ministry typed; the opening is what the document says it is. Measured
    over the packages of six followed projects (13 Sept 2026), every appendix published beside
    a bill names itself in its first lines, and the drafts of executive regulations that travel
    with a bill — filed as `projekt.docx`, `uzasadnienie.docx`, `OSR.doc`, indistinguishable
    from the bill's own files by name — say ROZPORZĄDZENIE where the bill says USTAWA.

    The all-caps headings are matched case-sensitively and anchored to their whole line: a
    justification wrapped so that a line begins "rozporządzenia 2018/1240" is not a regulation.
    `unknown` is the honest answer for a layout not seen before, and callers treat it as such.
    """
    head = text[:HEAD_CHARS]
    for kind, pattern in _KINDS:
        if pattern.search(head):
            return kind
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


def _section_start(page: str, current: str) -> str:
    """The section a page opens, or the one it continues: everything after the first draft
    regulation belongs to the drafts."""
    if current == REGULATIONS:
        return REGULATIONS
    return _SECTION_OF_KIND.get(document_kind(page[:_HEAD]), current)


_PAGE_NUMBER_LINE = re.compile(r"^\s*[–\-—]?\s*\d{1,4}\s*[–\-—]?\s*$", re.MULTILINE)


def strip_page_furniture(page: str, running_head: str | None) -> str:
    """A page without what the printer put on it: its running head and its page number.

    A section heading is looked for in the opening of a page, and a running head stands in front
    of it — druk 1764 carries "Konfederacja Wolność i Niepodległość | konfederacja.pl" as the
    first line of all thirty of its pages. Left in place it hides whatever the page really opens.
    """
    lines = page.split("\n")
    start = 0
    while start < len(lines) and _is_furniture(lines[start], running_head):
        start += 1
    return "\n".join(lines[start:])


def _is_furniture(line: str, running_head: str | None) -> bool:
    stripped = line.strip()
    if not stripped or _PAGE_NUMBER_LINE.fullmatch(line):
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
    r"^\s*U\s?S\s?T\s?A\s?W\s?A\b"
    r"|^\s*Art\.\s*1\s*[.)]"
    r"|^\s*u\s?z\s?a\s?s\s?a\s?d\s?n\s?i\s?e\s?n\s?i\s?e\s*$"
    r"|^\s*Nazwa projektu\b"
    r"|^\s*Projekt\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def without_cover_letter(text: str) -> str:
    """The document itself: what follows the letter that hands it to the Marshal.

    Every print and every document filed to one opens with such a letter — "na podstawie
    art. 118 ust. 1 Konstytucji … wnoszą projekt ustawy", "przekazuję przyjęte przez Radę
    Ministrów stanowisko" — and the document proper starts on the next page or at its own
    heading, whichever comes first. A text with no such opening (a committee report, an RCL
    file) is its own document from the first line and is returned unchanged.
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
"""Above this many characters the text is the document whatever its density.

A print can be appendix-heavy — hundreds of pages of tables that extract to little — and text
that long is still text to read, not a caption on an image. Only a short text can be the stray
header, stamp or title page that the page count then exposes for what it is.
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

    Sejm papers are scanned, signed on paper and filed as images: of 66 documents filed to
    prints (term 10, 12 Sept 2026) 55 have no text layer whatsoever and the remaining 11 carry
    the Prime Minister's covering letter and nothing else — 700-820 characters that name the
    bill and say who will present the government's position, never what the position is. Of 39
    prints, 11 are scans and one (druk 604) is its cover letter and the signatures under it.

    Those 800 characters are the trap this answers: they read as a document, they pass any
    length threshold, and a model asked what the government makes of a bill would answer from
    a polite transmittal note. A covering letter is not the only such scrap, though — a title
    page, a running head, whatever a stray OCR pass left behind — so a short text is measured
    against the paper it came from as well: `pages` (0 for a format that has none) turns
    "long enough to read" into "enough for a document of this many pages".
    """
    body = without_cover_letter(text).strip()
    if len(body) < min_chars:
        return False
    return not _too_thin_for_its_pages(body, pages)


def _too_thin_for_its_pages(body: str, pages: int) -> bool:
    if pages <= 0 or len(body) >= SCANNED_TEXT_CEILING:
        return False
    return len(body) / pages < MIN_CHARS_PER_PAGE


@dataclass(frozen=True)
class PageWindow:
    """The pages of a scan worth their tokens: `count` pages from `first` (0-based)."""

    first: int
    count: int


def scan_page_window(pages: int, *, cover_letter: bool) -> PageWindow:
    """Which pages of a scanned document to put before the model.

    The letter that hands the document to the Marshal is one page — in all 15 government prints
    measured on 12 Sept 2026 — and says nothing the model needs, so it goes when the text layer
    proved it is there. Everything else goes: the documents that reach us as scans are their own
    substance from the first page to the last (druk 1273's OSR quantifies the affected on page
    10 of 30), and nothing short of reading them can tell which page is chaff.
    """
    first = 1 if cover_letter and pages > 1 else 0
    return PageWindow(first, pages - first)


def trim_print(text: str) -> TrimmedText:
    """Drop the appendices of a print; unknown layouts (Senate texts, reports) pass unchanged.

    Every dropped run is replaced by one Polish marker line so the model knows the text is not
    complete there.
    """
    pages = text.split(PAGE_BREAK)
    running_head = _running_head(pages)
    kept: list[str] = []
    dropped: list[DroppedSection] = []
    current = KEEP
    for page in pages:
        current = _section_start(strip_page_furniture(page, running_head), current)
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
    return TrimmedText(text="\n".join(kept).strip(), dropped=tuple(dropped))


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


def excerpts(
    text: str,
    spans: Sequence[tuple[int, int]],
    *,
    head_chars: int = 3000,
    window: int = 1200,
    max_chars: int = 24_000,
) -> str:
    """The start of the bill, the start of the justification and text around each keyword hit.

    Segments are merged when they overlap and returned in document order, separated by an
    ellipsis marker. Keyword windows are added in order until `max_chars` is reached; the two
    heads are always included.
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

    A text over the cap keeps its head (the act) and the start of the justification, which
    explains the purpose in plain language; the cut is marked in Polish.
    """

    MARKER = "\n\n[... fragment pominięty ...]\n\n"

    def __init__(self, max_chars: int, *, justification_share: float = 0.35) -> None:
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        self._max = max_chars
        self._justification_share = justification_share

    def apply(self, text: str) -> BudgetedText:
        if len(text) <= self._max:
            return BudgetedText(text=text, truncated=False)
        match = _JUSTIFICATION_RE.search(text)
        if match is None or match.start() < self._max:
            return BudgetedText(text=text[: self._max], truncated=True)
        justification_chars = int(self._max * self._justification_share)
        head = text[: self._max - justification_chars]
        justification = text[match.start() : match.start() + justification_chars]
        return BudgetedText(text=head + self.MARKER + justification, truncated=True)
