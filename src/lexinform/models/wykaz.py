"""Planned bills: the wykaz prac legislacyjnych i programowych Rady Ministrów (KPRM, gov.pl).

The register is what art. 3 of the ustawa o działalności lobbingowej obliges the government to
publish before it drafts anything: the number (UD408, UC164), the reasons, the essence of the
planned solutions, the responsible ministry and the quarter in which the Council of Ministers
means to adopt it. There is no text yet, so an entry is an intention, not a bill — and the
earliest public trace of one: UD408 (o zmianie ustawy o cudzoziemcach) was entered on 2026-05-12
and appeared on RCL on 2026-07-06, 55 days later.

Nothing here does I/O: the CSV is turned into these models by `adapters/wykaz_csv.py`.
"""

import datetime as dt
import hashlib
import json
import re

from pydantic import BaseModel, ConfigDict

from lexinform.models.enums import (
    BILL_DOCUMENT_TYPE,
    WYKAZ_PREFIX,
    ApplicantType,
    DocumentType,
)
from lexinform.models.sejm import ProcessSummary

REGISTER_PAGE_URL = "https://www.gov.pl/web/premier/wplip-rm"
BILL_KIND = "Projekty ustaw"  # `Rodzaj dokumentu`; rozporządzenia and programmes are not followed
# `Status realizacji`. A project the government gives up on is either taken off the plan or left
# unrealised, and art. 3 ust. 3 of the lobbying act obliges the register to say so; the reason
# goes into `Informacja o rezygnacji z prac nad projektem`.
DROPPED_STATUSES = frozenset({"Wycofany", "Niezrealizowany"})
ADOPTED_STATUS = "Zrealizowany"
DESCRIPTION_LIMIT = 6000  # characters of goals + essence handed to the model (~3k tokens)

# "III kwartał 2026 r.", "II/III kwartał 2026 r." — the field is free text and often carries the
# realisation note as well ("II kwartał 2025 r. - ZREALIZOWANY Rada Ministrów przyjęła 6 maja"),
# so only the quarter is taken from it and the rest is never shown.
_QUARTER = re.compile(r"\b(IV|III|II|I)\s*(?:/\s*(IV|III|II|I))?\s*kwarta[łl]\w*\s+(\d{4})", re.I)
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}


def wykaz_number(entry_number: str) -> str:
    """The `bills.number` of a register entry: `WPL/UD408`."""
    return f"{WYKAZ_PREFIX}{entry_number}"


def wykaz_entry_number(number: str) -> str:
    """`WPL/UD408` -> `UD408`, the number RCL and the ministries use."""
    return number.removeprefix(WYKAZ_PREFIX)


class WykazEntry(BaseModel):
    """One row of the register, as published (art. 3 ust. 2 of the lobbying act)."""

    model_config = ConfigDict(frozen=True)

    number: str  # normalised wykaz number: UD408, UC164, UDER12
    title: str
    kind: str  # `Rodzaj dokumentu`: only BILL_KIND is followed
    doc_type: str | None = None  # `Typ dokumentu`: "C – projekty implementujące UE", ...
    goals: str = ""  # `Cele projektu oraz informacja o przyczynach i potrzebie…`
    essence: str = ""  # `Istota rozwiązań planowanych w projekcie…`
    organ: str | None = None  # `Organ odpowiedzialny`: MSWiA — where a zgłoszenie is filed
    person: str | None = None  # `Osoba odpowiedzialna`
    planned_adoption: str = ""  # `Planowane przyjęcie przez RM`, free text
    status: str = ""  # `Status realizacji`: "", Zrealizowany, Wycofany, Niezrealizowany
    resignation: str = ""  # `Informacja o rezygnacji z prac nad projektem`
    published_at: dt.datetime  # `Data publikacji`: the FIRST publication, never moved by an edit
    web_url: str  # `Podgląd`: the entry's own page on gov.pl
    rcl_project_id: int | None = None  # stamped by RCL discovery when the project is published

    @property
    def bill_number(self) -> str:
        return wykaz_number(self.number)

    @property
    def is_bill(self) -> bool:
        return self.kind == BILL_KIND

    @property
    def is_withdrawn(self) -> bool:
        return self.status in DROPPED_STATUSES or bool(self.resignation.strip())

    @property
    def is_adopted(self) -> bool:
        return self.status == ADOPTED_STATUS

    @property
    def is_open(self) -> bool:
        return not self.status.strip() and not self.resignation.strip()

    @property
    def eu_related(self) -> bool:
        return self.number.startswith("UC")

    @property
    def planned_quarter(self) -> tuple[int, int] | None:
        """The last quarter named by `Planowane przyjęcie przez RM`, as (year, quarter)."""
        match = _QUARTER.search(self.planned_adoption)
        if match is None:
            return None
        quarter = _ROMAN[(match.group(2) or match.group(1)).lower()]
        return int(match.group(3)), quarter

    @property
    def description(self) -> str | None:
        """What the model and the keyword prefilter see instead of a text."""
        parts = [part.strip() for part in (self.goals, self.essence) if part.strip()]
        joined = "\n\n".join(parts)
        return joined[:DESCRIPTION_LIMIT] or None


def wykaz_summary(entry: WykazEntry, *, term: int) -> ProcessSummary:
    """The `bills` row summary of a register entry (no project and no Sejm process exist yet)."""
    published = entry.published_at.date()
    return ProcessSummary(
        term=term,
        number=entry.bill_number,
        title=entry.title,
        description=entry.description,
        document_type=BILL_DOCUMENT_TYPE,
        document_type_enum=DocumentType.BILL,
        process_start_date=published,
        document_date=published,
        change_date=entry.published_at,
        closure_date=published if entry.is_withdrawn else None,
        passed=False if entry.is_withdrawn else None,
        eu_related=entry.eu_related,
        applicant=ApplicantType.GOVERNMENT,
    )


def wykaz_fingerprint(entry: WykazEntry) -> str:
    """What counts as a change: the plan itself. `Data publikacji` and the entry's URL stay out —
    the date never moves (an edit makes a new version on the gov.pl page instead) and the same
    project is occasionally entered twice under one number with two URLs."""
    payload = {
        "title": entry.title,
        "goals": entry.goals,
        "essence": entry.essence,
        "planned": entry.planned_adoption,
        "status": entry.status,
        "resignation": entry.resignation,
        "rcl": entry.rcl_project_id,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
