"""Agendas of committee and Sejm sittings: plain text out of the API's HTML, which prints an
agenda mentions, and what a sitting's `notes` say about whether it happens at all. Pure
functions, no I/O.

The Sejm API returns agendas as HTML fragments: `<li>` items for a Sejm sitting, consecutive
`<div class="agenda-indent-N">` lines for a committee sitting (the presenter, "– uzasadnia poseł
…", is a line of its own). Prints are named in the text as "druk nr 3035" or "druki nr 3010 i
3055"; the `PrzebiegProc.xsp?nr=` links next to them are not reliable (observed: the link of one
print pointing at another), so the text is what counts.
"""

import html
import re
from dataclasses import dataclass
from datetime import date

from lexinform.rcl_letters import parse_letter

_BLOCK_START = re.compile(r'<li\b[^>]*>|<div class="agenda-indent-\d+">', re.I)
_LINE_BREAK = re.compile(r"<br\s*/?>|</div>|</li>|</p>|</tr>", re.I)
_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t ]+")
_DRUK = re.compile(
    r"druk(?:i|u|ów|ach|ami|iem)?\s+nr\s+"
    r"((?:\d+(?:-[A-Z])?)(?:\s*(?:,|i|oraz)\s*\d+(?:-[A-Z])?)*)",
    re.I,
)
_NUMBER = re.compile(r"\d+(?:-[A-Z])?")
_CONTINUATION = ("–", "-", "—")

ITEM_MAX_CHARS = 400

_CONDITIONAL = re.compile(
    r"(?:aktualn\w*|zostan\w*\s+zrealizowan\w*)\s+w\s+przypadku\s+(?P<what>[^.]+)", re.I
)
_POINTS = re.compile(r"\bpkt\.?\s*(?P<first>[IVX]+)(?:\s*[-–—]\s*(?P<last>[IVX]+))?", re.I)
_ITEM_POINT = re.compile(r"^(?P<numeral>[IVX]+)\s*[.)]\s")
_ROMAN = {"I": 1, "V": 5, "X": 10}
_CONDITIONS = (
    ("second_reading_amendments", re.compile(r"poprawek\s+w\s+czasie\s+drugiego\s+czytania", re.I)),
    ("senate_amendments", re.compile(r"przez\s+Senat", re.I)),
    ("first_reading_referral", re.compile(r"pierwszego\s+czytania", re.I)),
    ("referral", re.compile(r"skierowani", re.I)),
)
"""What a conditional sitting waits for, most specific first. Over the 4,387 committee sittings
of term 10 these four cover 20 of the 21 notes that make a sitting or one of its points
conditional; the twenty-first is a subcommittee waiting to be created ("w przypadku jej
powołania") and falls through to the generic wording."""

_HEARING = re.compile(r"przes[łl]uchani|wys[łl]uchani", re.I)


def html_to_text(fragment: str) -> str:
    """Tags stripped, entities decoded, one line per block, whitespace collapsed."""
    text = _LINE_BREAK.sub("\n", fragment)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    lines = [_SPACES.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def agenda_items(fragment: str) -> list[str]:
    """The agenda as a list of items, each a single line of plain text.

    A line that starts with a dash continues the previous item (the presenter or rapporteur).
    """
    items: list[str] = []
    for block in _BLOCK_START.split(fragment):
        text = " ".join(html_to_text(block).splitlines()).strip()
        if not text:
            continue
        if items and text.startswith(_CONTINUATION):
            items[-1] = f"{items[-1]} {text}"
        else:
            items.append(text)
    return items


def print_numbers(text: str) -> set[str]:
    """Print numbers named in an agenda text; "-A" additional reports count as their base print."""
    found: set[str] = set()
    for match in _DRUK.finditer(text):
        for number in _NUMBER.findall(match.group(1)):
            found.add(number.split("-")[0])
    return found


def items_mentioning(fragment: str, numbers: set[str] | frozenset[str]) -> list[str]:
    """Agenda items naming any of `numbers`, clipped for a Telegram post."""
    wanted = {n.split("-")[0] for n in numbers}
    return [
        _clip(item, ITEM_MAX_CHARS, keeping=wanted)
        for item in agenda_items(fragment)
        if print_numbers(item) & wanted
    ]


@dataclass(frozen=True)
class SittingCondition:
    """A sitting the committee called off in advance: it happens only if something else does.

    `kind` is one of `_CONDITIONS` (or "other"), `points` the agenda points the condition covers
    — empty when it covers the whole sitting. The distinction is the whole value of parsing this:
    "Pkt III aktualny w przypadku zgłoszenia poprawek" says nothing about a bill that is point I,
    and hedging its announcement would be as wrong as stating point III as a fact.
    """

    kind: str
    points: frozenset[int] = frozenset()


@dataclass(frozen=True)
class HearingApplication:
    """Where and by when to apply to take part in a przesłuchanie.

    A committee's `notes` is the only place the Sejm API publishes this — four sittings of term
    10, all of the Rzecznik Finansowy hearing of November 2025 — and without it the channel can
    say a hearing is happening but not how a reader gets in.
    """

    email: str
    deadline: date | None = None


def sitting_condition(notes: str) -> SittingCondition | None:
    """What a sitting's `notes` say it waits for, when they make it conditional at all.

    Of the 226 committee sittings of term 10 that carry `notes`, 181 only record the procedure
    the sitting was called under (art. 152 ust. 2 regulaminu Sejmu) and say nothing a reader
    acts on; 21 say the sitting, or some of its points, happens only if the Sejm refers
    something to the committee first. The channel announced all of them as settled fact.
    """
    match = _CONDITIONAL.search(notes)
    if match is None:
        return None
    what = match.group("what")
    kind = next((name for name, pattern in _CONDITIONS if pattern.search(what)), "other")
    return SittingCondition(kind=kind, points=_points_in(notes[: match.start()]))


def hearing_application(notes: str) -> HearingApplication | None:
    """The address and deadline for applying to a hearing, read from the same fixed prose as a
    consultation letter ("na adres e-mail: … w terminie do 12 listopada 2025 r."), which is why
    `rcl_letters.parse_letter` reads it and no second parser of Polish dates exists here."""
    if not _HEARING.search(notes):
        return None
    info = parse_letter(notes)
    if info.email is None:
        return None
    return HearingApplication(email=info.email, deadline=info.deadline)


def condition_covers(
    fragment: str, numbers: set[str] | frozenset[str], points: frozenset[int]
) -> bool:
    """Whether a condition scoped to `points` covers the agenda items naming `numbers`.

    A committee numbers its points in the agenda text ("III. Rozpatrzenie poprawek…") or not at
    all, in which case the note counts them from the top, so both are read: the numeral the item
    carries, else its position.
    """
    if not points:
        return True
    wanted = {n.split("-")[0] for n in numbers}
    for position, item in enumerate(agenda_items(fragment), start=1):
        if not print_numbers(item) & wanted:
            continue
        numeral = _ITEM_POINT.match(item)
        point = _roman(numeral.group("numeral")) if numeral else position
        if point in points:
            return True
    return False


def _points_in(text: str) -> frozenset[int]:
    points: set[int] = set()
    for match in _POINTS.finditer(text):
        first = _roman(match.group("first"))
        last = _roman(match.group("last")) if match.group("last") else first
        points.update(range(first, max(first, last) + 1))
    return frozenset(points)


def _roman(numeral: str) -> int:
    """Agenda points never go past a dozen, so the three smallest symbols are the whole alphabet."""
    total, previous = 0, 0
    for symbol in reversed(numeral.upper()):
        value = _ROMAN.get(symbol, 0)
        total += -value if value < previous else value
        previous = max(previous, value)
    return total


def _clip(text: str, limit: int, *, keeping: set[str] | None = None) -> str:
    """The item cut to `limit`, with the print reference that made it worth showing still in it.

    The Sejm names the print at the end of the item ("… oraz niektórych innych ustaw (druk nr
    1234)"), and a long title pushes it past the cut: 65 of the 4,040 agenda items of term 10 that
    name a print were shown to the reader with the number gone, so the quoted line no longer said
    what the post was about. Where that happens the tail is kept beside the head.
    """
    if len(text) <= limit:
        return text
    head = _cut_at_a_space(text, limit)
    if not keeping or print_numbers(head) & keeping:
        return head.rstrip(" ,;:") + "…"
    tail = _tail_with_a_number(text, keeping, limit // 2)
    if not tail:
        return head.rstrip(" ,;:") + "…"
    head = _cut_at_a_space(text, limit - len(tail) - 2)
    return f"{head.rstrip(' ,;:')}… {tail}"


def _cut_at_a_space(text: str, limit: int) -> str:
    cut = text[: max(limit - 1, 1)]
    space = cut.rfind(" ")
    return cut[:space] if space > limit // 2 else cut


def _tail_with_a_number(text: str, wanted: set[str], limit: int) -> str:
    """A window of at most `limit` characters around the first wanted print reference."""
    for match in _DRUK.finditer(text):
        if not {n.split("-")[0] for n in _NUMBER.findall(match.group(1))} & wanted:
            continue
        start = max(0, match.start() - limit // 3)
        space = text.find(" ", start)
        if 0 <= space < match.start():
            start = space + 1
        window = text[start : start + limit]
        if start + limit >= len(text):
            return window
        return _cut_at_a_space(window, limit).rstrip(" ,;:") + "…"
    return ""
