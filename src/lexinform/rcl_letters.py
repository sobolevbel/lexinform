"""The consultation letter (pismo kierujące projekt do konsultacji publicznych), read for the
deadline and the address for comments.

Ministries write them by hand but the Regulamin pracy RM fixes the phrasing: "w terminie 14 dni
od dnia otrzymania niniejszego pisma" (relative to a date that an electronic signature leaves
out), "do dnia 30 września 2026 r.", "na adres: sekretariat@ministerstwo.gov.pl". No I/O.

`MAX_CONSULTATION` bounds a parsed deadline: the longest term set is 30 days, 60 for a few big
projects, so six months is outside it and inside any sunset clause a bill might quote.
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
MAX_CONSULTATION = timedelta(days=180)
_EMAIL = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
# An address counts when something tells the reader to send comments there. The instruction is
# the whole discriminator: a ministry's letterhead labels its own switchboard with the same noun
# ("telefon: 22 245 59 15 adres: ul. Królewska 27 adres e-mail: sekretariat.DP@cyfra.gov.pl"),
# and matching "adres" without a verb or the preposition handed the reader that label in 17
# letters of the corpus. The vocabulary is wider than "na adres" because the letters are written
# by hand — "uwagi prosimy kierować do:", "proszę o przesłanie na" — and over the 1,727 letters
# measured (14 Sept 2026) widening it admits no letterhead at all: both the narrow and the wide
# form find the address in exactly the same 1,060 letters.
_INSTRUCTION = r"na\s+adres\w*|kierowa[ćc]|przes[łl]a\w*|przekaza\w*|zg[łl]asza\w*|nadsy[łl]a\w*"
_ADDRESS_EMAIL = re.compile(
    rf"\b(?:{_INSTRUCTION})\s*(?:do|na)?[:\s][^@\n]{{0,80}}?({_EMAIL})", re.IGNORECASE
)


@dataclass(frozen=True)
class LetterInfo:
    """What the letter says about the consultation; every field may be unknown.

    `days` is the relative term ("w terminie N dni od dnia otrzymania") and `deadline` the
    absolute date, when the letter gives one instead.
    """

    letter_date: date | None = None
    days: int | None = None
    deadline: date | None = None
    email: str | None = None


def parse_letter(text: str) -> LetterInfo:
    """What the letter says about the consultation. Only what it says: a field left unknown is
    the honest answer, and for the address it is the only safe one.

    A fallback to the first e-mail anywhere in the letter fired 426 times over the corpus's 1,727
    letters and was the ministry's switchboard every time, 394 of them inside the letterhead's
    first 600 characters: those letters give no address for comments at all. A guessed address is
    worse than none, because the reader acts on it.
    """
    flat = " ".join(text.split())
    letter_date = _date_of(_LETTER_DATE.search(flat))
    days_match = _DAYS.search(flat)
    until = _UNTIL.search(flat)
    address = _ADDRESS_EMAIL.search(flat)
    return LetterInfo(
        letter_date=letter_date,
        days=int(days_match.group(1)) if days_match else None,
        deadline=_date_of(until),
        email=address.group(1) if address else None,
    )


def deadline_of(info: LetterInfo, *, published: date) -> date | None:
    """The last day for comments: the absolute date when given, else the letter date (or the
    day the letter was published on RCL) plus the number of days.

    A letter also quotes the dates of the bill it carries ("przepis obowiązuje do dnia 31
    grudnia 2030 r."), and the first "do dnia …" in it need not be the consultation's. One
    beyond `MAX_CONSULTATION_DAYS` is not a day anyone may still send an opinion by.
    """
    start = info.letter_date or published
    if info.deadline is not None and start <= info.deadline <= start + MAX_CONSULTATION:
        return info.deadline
    if info.days is None:
        return None
    return start + timedelta(days=info.days)


def _date_of(match: re.Match[str] | None) -> date | None:
    """The date a `_DATE` match found: its last six groups are the two spellings the letters
    use — day, month name, year for a written date, and day, month, year for a numeric one."""
    if match is None:
        return None
    groups = match.groups()[-6:]
    try:
        if groups[0] is not None:
            return date(int(groups[2]), _MONTHS[groups[1].lower()], int(groups[0]))
        return date(int(groups[5]), int(groups[4]), int(groups[3]))
    except ValueError:
        return None
