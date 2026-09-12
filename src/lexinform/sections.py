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

PAGE_BREAK = "\f"

_HEAD = 600

_JUSTIFICATION_RE = re.compile(
    r"^\s*u\s?z\s?a\s?s\s?a\s?d\s?n\s?i\s?e\s?n\s?i\s?e\s*$", re.IGNORECASE | re.MULTILINE
)
_OSR_RE = re.compile(r"^\s*Nazwa projektu\b", re.MULTILINE)
_OSR_CUT_RE = re.compile(r"^\s*(?:6\.\s*)?Wpływ na sektor finans", re.MULTILINE)
_REGULATION_RE = re.compile(
    r"^\s*R\s?O\s?Z\s?P\s?O\s?R\s?Z\s?Ą\s?D\s?Z\s?E\s?N\s?I\s?E\s*$", re.MULTILINE
)
_CONSULTATION_RE = re.compile(r"^\s*Raport\s+z\s+(konsultacji|opiniowania)\b", re.I | re.M)
_REMARKS_RE = re.compile(r"^\s*Zestawienie\s+uwag\b", re.IGNORECASE | re.MULTILINE)
_COMPLIANCE_RE = re.compile(
    r"^\s*(Odwrócona\s+)?Tabela\s+zgodności\b|^\s*TYTUŁ\s+PROJEKTU\b", re.IGNORECASE | re.MULTILINE
)

KEEP = "keep"
OSR = "osr"
OSR_TAIL = "OSR pkt 6-13"
CONSULTATION = "raport z konsultacji"
REMARKS = "zestawienie uwag"
COMPLIANCE = "tabela zgodności"
REGULATIONS = "projekty rozporządzeń"

_DROPPED = frozenset({OSR_TAIL, CONSULTATION, REMARKS, COMPLIANCE, REGULATIONS})


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
    head = page[:_HEAD]
    if current == REGULATIONS:
        return REGULATIONS
    if _REGULATION_RE.search(head):
        return REGULATIONS
    if _CONSULTATION_RE.search(head):
        return CONSULTATION
    if _REMARKS_RE.search(head):
        return REMARKS
    if _COMPLIANCE_RE.search(head):
        return COMPLIANCE
    if _OSR_RE.search(head):
        return OSR
    if _JUSTIFICATION_RE.search(head):
        return KEEP
    return current


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


def carries_the_document(text: str, *, min_chars: int) -> bool:
    """Whether the extracted text is the document at all, or only the letter transmitting it.

    Sejm papers are scanned, signed on paper and filed as images: of 66 documents filed to
    prints (term 10, 12 Sept 2026) 55 have no text layer whatsoever and the remaining 11 carry
    the Prime Minister's covering letter and nothing else — 700-820 characters that name the
    bill and say who will present the government's position, never what the position is. Of 39
    prints, 11 are scans and one (druk 604) is its cover letter and the signatures under it.

    Those 800 characters are the trap this answers: they read as a document, they pass any
    length threshold, and a model asked what the government makes of a bill would answer from
    a polite transmittal note.
    """
    return len(without_cover_letter(text).strip()) >= min_chars


@dataclass(frozen=True)
class PageWindow:
    """The pages of a scan worth their tokens: `count` pages from `first` (0-based)."""

    first: int
    count: int


def scan_page_window(pages: int, *, cover_letter: bool, budget: int | None) -> PageWindow:
    """Which pages of a scanned document to put before the model.

    The letter that hands the document to the Marshal is one page — in all 15 government prints
    measured on 12 Sept 2026 — and says nothing the model needs, so it goes when the text layer
    proved it is there. A `budget` bounds what follows: a scanned OSR is the 13-point form, whose
    points 1-5 (problem, solution, affected parties, consultations) took 2 to 19 pages of those
    prints, a median of 8, while points 6-13 are the public-finance tables `trim_print` drops
    from a printed OSR. A bill and a government position have no such tail and take no budget.
    """
    first = 1 if cover_letter and pages > 1 else 0
    count = pages - first
    return PageWindow(first, min(count, budget) if budget else count)


def trim_print(text: str) -> TrimmedText:
    """Drop the appendices of a print; unknown layouts (Senate texts, reports) pass unchanged.

    Every dropped run is replaced by one Polish marker line so the model knows the text is not
    complete there.
    """
    pages = text.split(PAGE_BREAK)
    kept: list[str] = []
    dropped: list[DroppedSection] = []
    current = KEEP
    for page in pages:
        current = _section_start(page, current)
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
