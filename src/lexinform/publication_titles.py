"""Short Polish titles for publication; official source titles stay intact."""

import re

_DATE = (
    r"z\s+dnia\s+\d{1,2}\s+"
    r"(?:stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|sierpnia|września|"
    r"października|listopada|grudnia)\s+\d{4}\s+(?:r\.|roku)\s+"
)
_WRAPPER = re.compile(
    r"^(?:(?:(?:poselski|rządowy|senacki|komisyjny|obywatelski|prezydencki)\s+|"
    r"przedstawiony\s+przez\s+Prezydenta\s+Rzeczypospolitej\s+Polskiej\s+)?"
    r"projekt\s+ustawy\b|ustawa\b)\s*",
    re.IGNORECASE,
)
_AMENDMENT = re.compile(
    r"^(?:o\s+zmianie\s+ustawy\b|zmieniając(?:ej|a|y|ą)\s+ustawę\b)\s*",
    re.IGNORECASE,
)
_ACT_DATE = re.compile(r"^" + _DATE, re.IGNORECASE)
_SUBJECT = re.compile(r"^(?:o\s+\S|(?:Kodeks|Prawo|Ordynacja|Karta|Przepisy)\b)", re.IGNORECASE)
_DASHES = "-–—‒"


def publication_title(title: str) -> str:
    text = " ".join(title.split())
    shortened = _WRAPPER.sub("", text, count=1)
    if shortened != text:
        shortened = _ACT_DATE.sub("", shortened, count=1).lstrip(_DASHES + " ")
    while match := _AMENDMENT.match(shortened):
        shortened = shortened[match.end() :]
        shortened = _ACT_DATE.sub("", shortened, count=1).lstrip(_DASHES + " ")
    if shortened == text or not _SUBJECT.match(shortened):
        return title
    return shortened[0].upper() + shortened[1:]
