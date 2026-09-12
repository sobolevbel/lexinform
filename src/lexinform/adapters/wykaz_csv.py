"""The wykaz prac legislacyjnych i programowych RM, as gov.pl publishes it: one CSV.

`https://www.gov.pl/web/premier/wplip-rm` renders the register client-side and offers the whole
of it as `/register-file/Rejestr_{id}.csv` (10.5 MB, 2.8 MB compressed, 1454 rows in September
2026). The id is in the page (`registerVue-20874195`); it is read from there so that a new
register does not need a release, and falls back to the known one when the page cannot be read.

Semicolon-separated, quoted, with multi-line paragraphs in two of the columns. The header names
are the statutory wording of art. 3 ust. 2 of the lobbying act and are matched by prefix, because
they are long and the register's editors have changed their punctuation before.
"""

import csv
import datetime as dt
import io
import logging
import re
import time
from collections.abc import Callable, Iterable
from zoneinfo import ZoneInfo

import httpx2 as httpx

from lexinform.errors import WykazUnavailableError
from lexinform.models import WykazEntry, normalize_wykaz_number

__all__ = ["DEFAULT_REGISTER_ID", "WykazClient", "WykazPageError", "parse_register"]

log = logging.getLogger(__name__)

WYKAZ_BASE_URL = "https://www.gov.pl"
REGISTER_PATH = "/web/premier/wplip-rm"
DEFAULT_REGISTER_ID = 20874195  # the id on the page in September 2026; only a fallback
WYKAZ_TZ = ZoneInfo("Europe/Warsaw")  # `Data publikacji` is a local wall clock

_REGISTER_ID = re.compile(r"registerVue-(\d+)")
_PUBLISHED = "%Y-%m-%d %H:%M"
# `Cele projektu` and `Istota rozwiązań` are free prose in quoted, multi-line fields, and the csv
# module refuses a field over 128 KB by default. The whole register is one download of ~10 MB, so
# a field cannot be bigger than that; anything larger is a different file, not a long paragraph.
_MAX_FIELD_CHARS = 16 * 1024 * 1024
csv.field_size_limit(_MAX_FIELD_CHARS)

# Column header -> field. Matched by prefix against the header row, longest header first, so that
# "Organ odpowiedzialny za opracowanie projektu" is not taken for "Organ odpowiedzialny".
_COLUMNS = {
    "Numer projektu": "number",
    "Tytuł": "title",
    "Rodzaj dokumentu": "kind",
    "Typ dokumentu": "doc_type",
    "Cele projektu": "goals",
    "Istota rozwiązań": "essence",
    "Organ odpowiedzialny za opracowanie projektu": "organ",
    "Osoba odpowiedzialna": "person",
    "Planowane przyjęcie przez RM": "planned_adoption",
    "Informacja o rezygnacji": "resignation",
    "Status realizacji": "status",
    "Data publikacji": "published_at",
    "Podgląd": "web_url",
}
_REQUIRED = frozenset({"number", "title", "kind", "published_at", "web_url"})


class WykazPageError(RuntimeError):
    """A register that does not look like what we expect (columns gone, nothing parsable)."""


def parse_register(text: str) -> tuple[WykazEntry, ...]:
    """Every readable row of the CSV, newest publication first, one entry per number.

    The same project is occasionally entered twice under one number on consecutive days (UC168 in
    September 2026, two rows differing only in their `Podgląd` URL); the later publication wins,
    so that one number always names one entry.
    """
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    fields = _map_columns(reader.fieldnames or [])
    entries: dict[str, WykazEntry] = {}
    dropped: list[str] = []
    try:
        rows = list(reader)
    except csv.Error as exc:  # not a WykazPageError on its own: it would escape the phase
        raise WykazPageError(f"the register could not be read: {exc}") from exc
    for row in rows:
        entry = _entry(row, fields)
        if entry is None:
            dropped.append(_row_label(row, fields))
            continue
        known = entries.get(entry.number)
        if known is not None and known.published_at >= entry.published_at:
            log.info("wykaz %s entered twice; keeping the entry published later", entry.number)
            continue
        entries[entry.number] = entry
    if dropped:
        # Silence here is what hides a format change: the register would simply get shorter.
        log.warning("%d register row(s) dropped, e.g. %s", len(dropped), "; ".join(dropped[:3]))
    if not entries:
        raise WykazPageError("the register has no readable rows")
    return tuple(sorted(entries.values(), key=lambda e: e.published_at, reverse=True))


def _row_label(row: dict[str, str | None], fields: dict[str, str]) -> str:
    """What a dropped row offers to identify it by: its number and the date that was unreadable."""
    parts = [(row.get(fields[f]) or "").strip() for f in ("number", "published_at") if f in fields]
    return " / ".join(p for p in parts if p) or "(empty row)"


def _map_columns(header: Iterable[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for column in header:
        name = column.strip().lstrip("﻿")
        for prefix, field in sorted(_COLUMNS.items(), key=lambda kv: -len(kv[0])):
            if name.startswith(prefix) and field not in fields:
                fields[field] = column
                break
    missing = sorted(
        prefix for prefix, field in _COLUMNS.items() if field in _REQUIRED - fields.keys()
    )
    if missing:
        raise WykazPageError(f"the register is missing the column(s): {', '.join(missing)}")
    return fields


def _entry(row: dict[str, str | None], fields: dict[str, str]) -> WykazEntry | None:
    def value(field: str) -> str:
        return (row.get(fields[field]) or "").strip() if field in fields else ""

    number = normalize_wykaz_number(value("number"))
    published = _published_at(value("published_at"))
    if number is None or published is None:
        return None
    return WykazEntry(
        number=number,
        title=" ".join(value("title").split()),
        kind=value("kind"),
        doc_type=value("doc_type") or None,
        goals=value("goals"),
        essence=value("essence"),
        organ=value("organ") or None,
        person=value("person") or None,
        planned_adoption=value("planned_adoption"),
        status=value("status"),
        resignation=value("resignation"),
        published_at=published,
        web_url=value("web_url"),
    )


def _published_at(value: str) -> dt.datetime | None:
    try:
        naive = dt.datetime.strptime(value, _PUBLISHED)
    except ValueError:
        return None
    return naive.replace(tzinfo=WYKAZ_TZ).astimezone(dt.UTC)


class WykazClient:
    def __init__(
        self,
        base_url: str = WYKAZ_BASE_URL,
        *,
        timeout: float = 60.0,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
        proxy: str | None = None,  # an EU address, when the runner cannot reach gov.pl directly
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"Accept": "text/csv,text/html;q=0.9,*/*;q=0.8", "Accept-Language": "pl-PL"},
            proxy=proxy,
            transport=transport,
            follow_redirects=True,
        )
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        self._sleep = sleep
        # The whole register in one file: discovery and tracking of one run share this download,
        # and a client lives for one run. Re-reading it per phase would cost 10 MB each time.
        self._entries: tuple[WykazEntry, ...] | None = None

    def entries(self) -> tuple[WykazEntry, ...]:
        if self._entries is None:
            self._entries = parse_register(self._download())
        return self._entries

    def find(self, number: str) -> WykazEntry | None:
        wanted = normalize_wykaz_number(number)
        return next((e for e in self.entries() if e.number == wanted), None)

    def close(self) -> None:
        self._client.close()

    def _download(self) -> str:
        response = self._request(f"/register-file/Rejestr_{self._register_id()}.csv")
        return response.text

    def _register_id(self) -> int:
        """The register's id from its page; the known one when the page cannot be read, so that a
        changed layout degrades to a stale id instead of ending the phase."""
        try:
            html = self._request(REGISTER_PATH).text
        except WykazUnavailableError as exc:
            log.warning(
                "wykaz register page unreadable (%s); using id %d", exc, DEFAULT_REGISTER_ID
            )
            return DEFAULT_REGISTER_ID
        match = _REGISTER_ID.search(html)
        if match is None:
            log.warning("wykaz register page has no register id; using %d", DEFAULT_REGISTER_ID)
            return DEFAULT_REGISTER_ID
        return int(match.group(1))

    def _request(self, url: str) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._client.get(url)
            except httpx.TransportError as exc:
                if attempt > self._max_retries:
                    raise WykazUnavailableError(
                        f"GET {url} failed after {attempt} attempt(s): {exc}"
                    ) from exc
                self._wait(attempt, f"GET {url}: {exc}")
                continue
            if response.status_code >= 400:
                response.close()
                if response.status_code < 500 and response.status_code != 429:
                    raise WykazUnavailableError(f"GET {url}: HTTP {response.status_code}")
                if attempt > self._max_retries:
                    raise WykazUnavailableError(
                        f"GET {url}: HTTP {response.status_code} after {attempt} attempts"
                    )
                self._wait(attempt, f"GET {url}: HTTP {response.status_code}")
                continue
            return response

    def _wait(self, attempt: int, reason: str) -> None:
        delay = self._backoff * (2 ** (attempt - 1))
        log.warning("wykaz retry %d in %.1fs (%s)", attempt, delay, reason)
        self._sleep(delay)
