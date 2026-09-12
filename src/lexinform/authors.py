"""Who signed a deputies' bill: parsed from the cover letter on the first page of the print.

The Sejm API does not expose signatories, but every print starts with a letter to the Marshal:
"niżej podpisani posłowie wnoszą projekt ustawy ... Do reprezentowania wnioskodawców ...
upoważniamy posła X. (-) A; (-) B; ...". Names are matched against the MP directory (/MP) to
show which clubs stand behind a bill.

A signature ends at a ";", at a final ".", at a blank line, at the end of the letter, or at the
next "(-)"; a single line break inside a name, which is how a PDF wraps one, does not end it.
"""

import re
from dataclasses import dataclass, field
from typing import Self

from lexinform.models import BillAuthors, Mp

_REPRESENTATIVE_RE = re.compile(
    r"upoważnia(?:my|ą|\s+się)?\s+(?:pos[łl]a|pos[łl]ank[ęe])\s+([^.\n;]+?)\s*\.|"
    r"upoważnion[ya]\s+(?:pos[łl]a|pos[łl]ank[ęe]|pose[łl])\s+([^.\n;]+?)\s*\.",
    re.IGNORECASE,
)
_SIGNATURE_RE = re.compile(
    r"\(\s*-\s*\)\s*([^;()]+?)(?=\s*;|\s*\.\s*(?:\n|$)|\s*\n\s*\n|\s*$|\s*\(\s*-\s*\))"
)
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


def parse_cover_letter(text: str) -> CoverLetter:
    head = text[:6000]
    for marker in _END_MARKERS:
        cut = head.find(marker)
        if cut > 200:
            head = head[:cut]
    representative = None
    if match := _REPRESENTATIVE_RE.search(head):
        representative = _clean(match.group(1) or match.group(2) or "")
    signatories = tuple(_clean(m) for m in _SIGNATURE_RE.findall(head) if _clean(m))
    return CoverLetter(representative=representative or None, signatories=signatories)


def _key(name: str) -> str:
    return re.sub(r"[\s\-]+", " ", name).strip().lower()


@dataclass
class MpDirectory:
    """Name to club lookup built from GET /MP; `display` maps the same keys to "First Last"."""

    by_name: dict[str, str] = field(default_factory=dict)
    by_accusative: dict[str, str] = field(default_factory=dict)
    display: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_mps(cls, mps: tuple[Mp, ...]) -> Self:
        d = cls()
        for mp in mps:
            for name in (mp.first_last_name, mp.full_name):
                d.by_name[_key(name)] = mp.club
                d.display[_key(name)] = mp.first_last_name
            if mp.accusative_name:
                d.by_accusative[_key(mp.accusative_name)] = mp.club
                d.display[_key(mp.accusative_name)] = mp.first_last_name
        return d

    def resolve(self, letter: CoverLetter) -> BillAuthors:
        counts: dict[str, int] = {}
        unresolved = 0
        for name in letter.signatories:
            club = self.by_name.get(_key(name))
            if club is None:
                unresolved += 1
                continue
            counts[club] = counts.get(club, 0) + 1
        rep_name = rep_club = None
        if letter.representative:
            key = _key(letter.representative)
            rep_club = self.by_accusative.get(key) or self.by_name.get(key)
            rep_name = self.display.get(key, letter.representative)
        clubs = tuple(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
        return BillAuthors(
            representative=rep_name,
            representative_club=rep_club,
            clubs=clubs,
            signatories=len(letter.signatories),
            unresolved=unresolved,
        )
