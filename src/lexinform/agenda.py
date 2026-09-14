"""Agendas of committee and Sejm sittings: plain text out of the API's HTML, and which prints
an agenda mentions. Pure functions, no I/O.

The Sejm API returns agendas as HTML fragments: `<li>` items for a Sejm sitting, consecutive
`<div class="agenda-indent-N">` lines for a committee sitting (the presenter, "– uzasadnia poseł
…", is a line of its own). Prints are named in the text as "druk nr 3035" or "druki nr 3010 i
3055"; the `PrzebiegProc.xsp?nr=` links next to them are not reliable (observed: the link of one
print pointing at another), so the text is what counts.
"""

import html
import re

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
