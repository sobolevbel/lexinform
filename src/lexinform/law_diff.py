"""Where two texts of one act differ, the noise of their extraction left out."""

import difflib
import re
from dataclasses import dataclass

from lexinform.law_digest import readable_law

_WORD = re.compile(r"[^\W_]+")
_UNIT_END = re.compile(r"(?<=[.;:])\s+")
_CONTEXT_WORDS = 12
_SHORT = 2
"""A token this short alone is a list mark or a footnote reference when it appears or vanishes."""


@dataclass(frozen=True)
class _Token:
    key: str
    start: int
    end: int


@dataclass(frozen=True)
class Hunk:
    """One changed passage: the old and new wording with a few words around it."""

    old: str
    new: str
    before: str
    after: str
    words: int


@dataclass(frozen=True)
class LawDiff:
    hunks: tuple[Hunk, ...]
    total_words: int

    @property
    def changed_words(self) -> int:
        return sum(h.words for h in self.hunks)

    @property
    def ratio(self) -> float:
        return self.changed_words / self.total_words if self.total_words else 1.0

    @property
    def chars(self) -> int:
        return sum(len(h.old) + len(h.new) + len(h.before) + len(h.after) for h in self.hunks)


def diff_laws(old_text: str, new_text: str) -> LawDiff | None:
    """The act's changes between two texts; None when either carries no act to compare."""
    old_body, new_body = readable_law(old_text), readable_law(new_text)
    if old_body is None or new_body is None:
        return None
    old, new = _Text(old_body), _Text(new_body)
    hunks: list[Hunk] = []
    matcher = difflib.SequenceMatcher(None, old.unit_keys, new.unit_keys, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            hunks.extend(_word_hunks(old, old.span(i1, i2), new, new.span(j1, j2)))
    return LawDiff(hunks=tuple(hunks), total_words=max(len(old.tokens), len(new.tokens)))


class _Text:
    """The body as tokens grouped into sentences: a change stays inside the provision it touched."""

    def __init__(self, body: str) -> None:
        self.body = body
        self.tokens: list[_Token] = []
        self.units: list[tuple[int, int]] = []
        offset = 0
        for piece in _UNIT_END.split(body):
            start = body.index(piece, offset) if piece else offset
            first = len(self.tokens)
            self.tokens.extend(
                _Token(m.group().lower(), start + m.start(), start + m.end())
                for m in _WORD.finditer(piece)
            )
            if len(self.tokens) > first:
                self.units.append((first, len(self.tokens)))
            offset = start + len(piece)
        self.unit_keys = [" ".join(t.key for t in self.tokens[a:b]) for a, b in self.units]

    def span(self, first_unit: int, end_unit: int) -> tuple[int, int]:
        """Token indices of units `first_unit`..`end_unit`; an empty run keeps its place."""
        if first_unit < end_unit:
            return self.units[first_unit][0], self.units[end_unit - 1][1]
        at = self.units[first_unit][0] if first_unit < len(self.units) else len(self.tokens)
        return at, at

    def quote(self, first: int, end: int) -> str:
        end = min(end, len(self.tokens))
        if first >= end:
            return ""
        return " ".join(self.body[self.tokens[first].start : self.tokens[end - 1].end].split())


def _word_hunks(
    old: _Text, old_span: tuple[int, int], new: _Text, new_span: tuple[int, int]
) -> list[Hunk]:
    (a0, a1), (b0, b1) = old_span, new_span
    hunks = []
    matcher = difflib.SequenceMatcher(
        None,
        [t.key for t in old.tokens[a0:a1]],
        [t.key for t in new.tokens[b0:b1]],
        autojunk=False,
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        i1, i2, j1, j2 = a0 + i1, a0 + i2, b0 + j1, b0 + j2
        if tag == "equal" or _is_noise(old.tokens[i1:i2], new.tokens[j1:j2]):
            continue
        hunks.append(
            Hunk(
                old=old.quote(i1, i2),
                new=new.quote(j1, j2),
                before=old.quote(max(0, i1 - _CONTEXT_WORDS), i1),
                after=old.quote(i2, i2 + _CONTEXT_WORDS),
                words=max(i2 - i1, j2 - j1),
            )
        )
    return hunks


def _is_noise(old: list[_Token], new: list[_Token]) -> bool:
    """A word split or joined by the text layer, or a list mark one side lacks; "2"→"3" counts."""
    if "".join(t.key for t in old) == "".join(t.key for t in new):
        return True
    one_sided = not old or not new
    return one_sided and all(len(t.key) <= _SHORT for t in old + new)
