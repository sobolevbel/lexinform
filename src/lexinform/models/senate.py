"""An act the Sejm passed, as senat.gov.pl shows it: its print, committees and their sittings."""

import datetime as dt
import difflib
import re
import unicodedata

from pydantic import BaseModel, ConfigDict

SENATE_BASE_URL = "https://www.senat.gov.pl"


class SenateCommittee(BaseModel):
    """A Senate committee: the id in the site's addresses, its name and its secretariat's e-mail."""

    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    email: str | None = None

    @property
    def url(self) -> str:
        return f"{SENATE_BASE_URL}/prace/komisje-senackie/komisja,{self.id}.html"


class SenateAct(BaseModel):
    """The act's own page under "Ustawy uchwalone przez Sejm", as last read."""

    model_config = ConfigDict(frozen=True)

    url: str
    title: str
    print_number: str | None = None
    received: dt.date | None = None
    committees: tuple[SenateCommittee, ...] = ()
    committee_sittings: tuple[dt.date, ...] = ()

    def next_committee_sitting(self, today: dt.date) -> dt.date | None:
        return min((d for d in self.committee_sittings if d >= today), default=None)

    def committees_done(self, today: dt.date) -> bool:
        """The committees have met on it and no further sitting is listed."""
        return bool(self.committee_sittings) and self.next_committee_sitting(today) is None


# Words the Senate drops or adds in a title it retypes: "w niektóre inne dni", "oraz o zmianie".
_FILLER_WORDS = frozenset({"w", "o", "zmianie", "niektorych"})
_MAX_TITLE_EDITS = 2
# The Senate cut "…na terytorium Rzeczypospolitej [Polskiej]"; "oraz niektórych innych ustaw" is 4.
_MAX_CUT_WORDS = 2
_MIN_CUT_TITLE_WORDS = 8
_MIN_TYPO_LETTERS = 6
_MIN_TYPO_SIMILARITY = 0.8


def senate_title_matches(senate_title: str, title_final: str) -> bool:
    """Whether the Senate's "Ustawa o …" names the act the Sejm called `titleFinal` ("o …")."""
    return senate_title_distance(senate_title, title_final) is not None


def senate_title_distance(senate_title: str, title_final: str) -> int | None:
    """Words the Senate retyped in `titleFinal`: 0 for the same title, None for another act."""
    senate, sejm = _title_words(senate_title), _title_words(title_final)
    if senate == sejm:
        return 0
    edits = 0
    matcher = difflib.SequenceMatcher(None, senate, sejm, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        if op == "replace":
            if i2 - i1 != 1 or j2 - j1 != 1 or not _misspelt(senate[i1], sejm[j1]):
                return None
            edits += 1
            continue
        words = senate[i1:i2] if op == "delete" else sejm[j1:j2]
        cut = op == "insert" and i1 == len(senate) and j2 == len(sejm)
        if not (cut and _cut_short(senate, words)) and not _FILLER_WORDS.issuperset(words):
            return None
        edits += len(words)
    return edits if edits <= _MAX_TITLE_EDITS else None


def _cut_short(senate: list[str], dropped: list[str]) -> bool:
    return len(dropped) <= _MAX_CUT_WORDS and len(senate) >= _MIN_CUT_TITLE_WORDS


def _misspelt(a: str, b: str) -> bool:
    """A typo in a word ("budowalne" for "budowlane"), never a different number."""
    return (
        a.isalpha()
        and len(a) >= _MIN_TYPO_LETTERS
        and difflib.SequenceMatcher(None, a, b).ratio() >= _MIN_TYPO_SIMILARITY
    )


def _title_words(title: str) -> list[str]:
    # Quotes, dashes, commas and diacritics ("postepowaniu") are retyped, so only words count.
    text = unicodedata.normalize("NFKD", title.casefold().replace("ł", "l"))
    text = " ".join(re.findall(r"\w+", "".join(c for c in text if not unicodedata.combining(c))))
    # `titleFinal` sometimes keeps the print's "Senacki projekt ustawy o …" whole.
    text = re.sub(r"^.*?\bprojekt ustawy ", "", text)
    text = re.sub(r"^(rozpatrzenie )?ustaw[ay]\b ?", "", text)
    return text.replace("niektorych innych", "niektorych").split()
