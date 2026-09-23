"""An act the Sejm passed, as senat.gov.pl shows it: its print, committees and their sittings."""

import datetime as dt
import re

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


def senate_title_matches(senate_title: str, title_final: str) -> bool:
    """Whether the Senate's "Ustawa o …" names the act the Sejm called `titleFinal` ("o …")."""
    return _normalized(senate_title) == _normalized(title_final)


def _normalized(title: str) -> str:
    # The Senate writes "ustawy – Prawo wodne" with an en or figure dash, the Sejm with a hyphen.
    text = re.sub(r"[‐‑‒–—]", "-", title.replace("\xa0", " "))
    text = " ".join(text.split()).casefold().rstrip(".")
    # `titleFinal` sometimes keeps the print's "Senacki projekt ustawy o …" whole.
    text = re.sub(r"^.*?\bprojekt ustawy ", "", text)
    return re.sub(r"^ustawa\b[\s-]*", "", text)
