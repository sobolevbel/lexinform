"""The consultation letter (pismo kierujące projekt do konsultacji publicznych), read for the
deadline and the address for comments.

Ministries write these letters by hand, but the phrasing is fixed by the Regulamin pracy Rady
Ministrów: "w terminie 14 dni od dnia otrzymania niniejszego pisma" (relative to the letter,
whose date is in its heading or, with an electronic signature, missing) or "do dnia 30 września
2026 r." (absolute), and "na adres: sekretariat@ministerstwo.gov.pl". Pure text parsing; no I/O.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta

_MONTHS = {
    "stycznia": 1,
    "lutego": 2,
    "marca": 3,
    "kwietnia": 4,
    "maja": 5,
    "czerwca": 6,
    "lipca": 7,
    "sierpnia": 8,
    "września": 9,
    "października": 10,
    "listopada": 11,
    "grudnia": 12,
}
_MONTH_NAMES = "|".join(_MONTHS)
_WORD_DATE = rf"(\d{{1,2}})\s+({_MONTH_NAMES})\s+(\d{{4}})"
_NUMERIC_DATE = r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})"
_DATE = rf"(?:{_WORD_DATE}|{_NUMERIC_DATE})"

_LETTER_DATE = re.compile(rf"Warszawa,?\s*(?:dnia\s+)?{_DATE}", re.IGNORECASE)
_DAYS = re.compile(r"w\s+(?:terminie|ciągu)\s+(\d{1,3})\s+dni", re.IGNORECASE)
_UNTIL = re.compile(rf"(?:w\s+terminie\s+)?do\s+(?:dnia\s+)?{_DATE}", re.IGNORECASE)
_EMAIL = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
_ADDRESS_EMAIL = re.compile(rf"adres[^@\n]{{0,80}}?({_EMAIL})", re.IGNORECASE)
_ANY_EMAIL = re.compile(_EMAIL)


@dataclass(frozen=True)
class LetterInfo:
    """What the letter says about the consultation; every field may be unknown."""

    letter_date: date | None = None
    days: int | None = None  # "w terminie N dni od dnia otrzymania"
    deadline: date | None = None  # an absolute date, when the letter gives one
    email: str | None = None


def parse_letter(text: str) -> LetterInfo:
    flat = " ".join(text.split())
    letter_date = _date_of(_LETTER_DATE.search(flat))
    days_match = _DAYS.search(flat)
    until = _UNTIL.search(flat)
    address = _ADDRESS_EMAIL.search(flat) or _ANY_EMAIL.search(flat)
    return LetterInfo(
        letter_date=letter_date,
        days=int(days_match.group(1)) if days_match else None,
        deadline=_date_of(until),
        email=address.group(1) if address and address.re is _ADDRESS_EMAIL else _first_email(flat),
    )


def deadline_of(info: LetterInfo, *, published: date) -> date | None:
    """The last day for comments: the absolute date when given, else the letter date (or the
    day the letter was published on RCL) plus the number of days."""
    if info.deadline is not None:
        return info.deadline
    if info.days is None:
        return None
    return (info.letter_date or published) + timedelta(days=info.days)


def _first_email(flat: str) -> str | None:
    match = _ANY_EMAIL.search(flat)
    return match.group(0) if match else None


def _date_of(match: re.Match[str] | None) -> date | None:
    if match is None:
        return None
    groups = match.groups()[-6:]  # word date: day, month name, year; numeric: day, month, year
    try:
        if groups[0] is not None:
            return date(int(groups[2]), _MONTHS[groups[1].lower()], int(groups[0]))
        return date(int(groups[5]), int(groups[4]), int(groups[3]))
    except ValueError:
        return None
