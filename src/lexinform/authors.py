"""Who signed a deputies' bill: parsed from the cover letter on the first page of the print.

The API does not expose signatories, but every print opens with a letter to the Marshal: "niżej
podpisani posłowie wnoszą projekt ustawy … upoważniamy posła X. (-) A; (-) B; …". Names are
matched against /MP to show which clubs stand behind a bill.

A signature ends at ";", a final ".", a blank line, the end of the letter or the next "(-)" —
never at a single line break or a page break, which is how a PDF wraps a name.
"""

import re
from dataclasses import dataclass, field
from typing import Self

from lexinform.models import BillAuthors, Mp

_REPRESENTATIVE_RE = re.compile(
    r"upowa[żz]ni(?:amy|am|ono|ł[aoy]?|a|ą|eni[ay]?|ony|ona)?\s*(?:si[ęe]\s+)?"
    r"(?:pan(?:a|i[ąa]?|i|ów|ie)?\s+)?"
    r"pos(?:[łl]a|[łl]ank[aięe]|e[łl]|[łl]ów|[łl]y)\s*:?\s+"
    r"([^.;\n]+?)\s*(?:\.|;|\n)",
    re.IGNORECASE,
)
_ANOTHER_NAME = re.compile(r"\s+(?:i|oraz|a\s+także)\s+|,\s*", re.IGNORECASE)
_HONORIFIC = re.compile(
    r"^(?:pan(?:a|i[ąa]?|i|ów|ie)?\s+)?pos(?:[łl]a|[łl]ank[aięe]|e[łl]|[łl]ów|[łl]y)\s+",
    re.IGNORECASE,
)
_SIGNATURE_RE = re.compile(
    r"\(\s*-\s*\)\s*([^;()]+?)(?=\s*;|\s*\.\s*(?:\n|$)|\s*\n\s*\n|\s*$|\s*\(\s*-\s*\))"
)
# A page break arrives as a blank line and a form feed, which the blank-line rule above takes for
# the end of a signature: "(-)  Barbara\n\n\fOliwiecka" lost the surname on 17 prints of term 10.
_WRAPPED_AT_PAGE_END = re.compile(r"\n\s*\n(\s*\f\s*)")
_END_MARKERS = ("Tłoczono z polecenia", "\nProjekt\n", "\nU S T AWA", "\nUSTAWA")


@dataclass(frozen=True)
class CoverLetter:
    """The names the letter carries: the representative as written, in the accusative case the
    letters use ("Darię Gosek-Popiołek"), and the signatories as printed, in the nominative."""

    representative: str | None = None
    signatories: tuple[str, ...] = ()


def _clean(name: str) -> str:
    """One name on one line: a PDF wraps a double-barrelled name across two, as
    "Gosek -\nPopiołek"."""
    name = re.sub(r"\s*-\s*", "-", name)
    return re.sub(r"\s+", " ", name).strip(" ,;.")


def _first_named(names: str) -> str:
    """The first person of "Pawła Śliza i Michała Gramatykę": a letter may authorise two or three,
    and the card names the one the letter names first."""
    for part in _ANOTHER_NAME.split(names):
        if name := _clean(_HONORIFIC.sub("", part)):
            return name
    return ""


def parse_cover_letter(text: str) -> CoverLetter:
    head = _WRAPPED_AT_PAGE_END.sub(r"\1", text[:6000])
    for marker in _END_MARKERS:
        cut = head.find(marker)
        if cut > 200:
            head = head[:cut]
    representative = None
    if match := _REPRESENTATIVE_RE.search(head):
        representative = _first_named(match.group(1))
    signatories = tuple(_clean(m) for m in _SIGNATURE_RE.findall(head) if _clean(m))
    return CoverLetter(representative=representative or None, signatories=signatories)


def _key(name: str) -> str:
    return re.sub(r"[\s\-]+", " ", name).strip().lower()


def _squashed(name: str) -> str:
    """The same name with the spaces gone, for a surname a PDF extractor split: pypdf puts
    "Osma lak" and "Siekiersk i" on the page, and 130 of the 152 signatures of term 10 that did
    not resolve were this. Over all 499 members no two squashed names collide."""
    return re.sub(r"[\s\-]+", "", name).strip().lower()


@dataclass
class MpDirectory:
    """Name to club lookup built from GET /MP; `display` maps the same keys to "First Last"."""

    by_name: dict[str, str] = field(default_factory=dict)
    by_accusative: dict[str, str] = field(default_factory=dict)
    by_squashed: dict[str, str] = field(default_factory=dict)
    display: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_mps(cls, mps: tuple[Mp, ...]) -> Self:
        d = cls()
        for mp in mps:
            for name in (mp.first_last_name, mp.full_name, mp.accusative_name or ""):
                if not name:
                    continue
                index = d.by_accusative if name == mp.accusative_name else d.by_name
                index[_key(name)] = mp.club
                d.by_squashed[_squashed(name)] = mp.club
                d.display[_key(name)] = mp.first_last_name
                d.display[_squashed(name)] = mp.first_last_name
        return d

    def _club(self, name: str, *, declined: bool = False) -> str | None:
        indexes = (self.by_accusative, self.by_name) if declined else (self.by_name,)
        key = _key(name)
        for index in indexes:
            if club := index.get(key):
                return club
        return self.by_squashed.get(_squashed(name))

    def resolve(self, letter: CoverLetter) -> BillAuthors:
        counts: dict[str, int] = {}
        unresolved = 0
        for name in letter.signatories:
            club = self._club(name)
            if club is None:
                unresolved += 1
                continue
            counts[club] = counts.get(club, 0) + 1
        rep_name = rep_club = None
        if letter.representative:
            key = _key(letter.representative)
            rep_club = self._club(letter.representative, declined=True)
            rep_name = self.display.get(key) or self.display.get(
                _squashed(letter.representative), letter.representative
            )
        clubs = tuple(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
        return BillAuthors(
            representative=rep_name,
            representative_club=rep_club,
            clubs=clubs,
            signatories=len(letter.signatories),
            unresolved=unresolved,
        )
