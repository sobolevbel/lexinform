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
        _clip(item, ITEM_MAX_CHARS)
        for item in agenda_items(fragment)
        if print_numbers(item) & wanted
    ]


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:") + "…"
