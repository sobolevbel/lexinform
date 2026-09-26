"""Scraper for senat.gov.pl, which has no API: the act's page, its committees and their e-mail.

Verified behaviour of the site (September 2026):
- "Ustawy uchwalone przez Sejm" lists ten acts a page, newest first (`page,N.html`), with the
  Senate's print number and the title "Ustawa o …", which is the Sejm's `titleFinal`.
- The act's page is a timeline: the hand-over with the print, the referral (`p[data-komisja]`,
  the committee id and its name in the dative), then each committee sitting that took it.
- A committee's page names its secretariat's e-mail behind one of two obfuscations: Cloudflare's
  `data-cfemail` or an inline `SendTo('', '', user, domain…)` script.
"""

import logging
import re
import time
from collections.abc import Callable
from datetime import date

import httpx2 as httpx
from bs4 import BeautifulSoup, Tag

from lexinform.adapters.browser_identity import BROWSER_HEADERS
from lexinform.adapters.retries import backoff_delay
from lexinform.errors import SenateUnavailableError
from lexinform.models import SENATE_BASE_URL, SenateAct, SenateCommittee, senate_title_matches

__all__ = ["SenateClient", "SenatePageError", "parse_act", "parse_committee", "parse_listing"]

log = logging.getLogger(__name__)

LISTING_PATH = "/prace/proces-legislacyjny-w-senacie/ustawy-uchwalone-przez-sejm/"
# The Senate has thirty days and receives some twenty acts in a busy week.
MAX_LISTING_PAGES = 5

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
_DATE = re.compile(r"(\d{1,2})\s+(\w+)\s+(\d{4})")
_PRINT = re.compile(r"Druk nr\s*(\d+)\s*$")
_SEND_TO = re.compile(r"SendTo\('[^']*',\s*'[^']*',\s*'([^']+)',\s*'([^'?]+)")


class SenatePageError(RuntimeError):
    """A page that does not look like what we expect (listing without rows, act without title)."""


def parse_listing(html: str) -> list[tuple[str, str]]:
    """(title, absolute url) of every act on one listing page, newest first."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for anchor in soup.select("table.results-resolution td.txt-left a[href]"):
        rows.append((anchor.get_text(" ", strip=True), _absolute(str(anchor["href"]))))
    if not rows:
        raise SenatePageError("the listing of acts has no rows")
    return rows


def parse_act(html: str, url: str) -> SenateAct:
    """The act's page; committees carry their id and name, not yet their e-mail."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one("div.timeline div.title")
    if title is None:
        raise SenatePageError(f"{url}: no act title")
    received: date | None = None
    print_number: str | None = None
    committees: list[SenateCommittee] = []
    sittings: list[date] = []
    for item in soup.select("ul.elementy > li"):
        when = _date(item.select_one("span.small"))
        heading = item.select_one("h3, h4")
        label = heading.get_text(" ", strip=True) if heading else ""
        if label.startswith("Przekazanie ustawy do Senatu"):
            received = when
            desc = item.select_one("a.file-desc")
            match = _PRINT.search(desc.get_text(" ", strip=True)) if desc else None
            print_number = match.group(1) if match else None
        for referral in item.select("p[data-komisja]"):
            name = str(referral.get("title") or referral.get_text(" ", strip=True))
            name = re.sub(r"^[–\s]*Komisji\b", "Komisja", name.strip())
            committees.append(SenateCommittee(id=int(str(referral["data-komisja"])), name=name))
        link = item.select_one('h4 a[href*="komisje-senackie/posiedzenia"]')
        if link is not None and when is not None:
            sittings.append(when)
    return SenateAct(
        url=url,
        title=title.get_text(" ", strip=True),
        print_number=print_number,
        received=received,
        committees=tuple(committees),
        committee_sittings=tuple(sorted(set(sittings))),
    )


def parse_committee(html: str, committee: SenateCommittee) -> SenateCommittee:
    """The committee with its nominative name and its secretariat's e-mail, where given."""
    soup = BeautifulSoup(html, "html.parser")
    header = soup.select_one("h2.nazwa-komisji-header")
    name = header.get_text(" ", strip=True) if header else committee.name
    return committee.model_copy(update={"name": name, "email": _email(soup, html)})


def _email(soup: BeautifulSoup, html: str) -> str | None:
    protected = soup.select_one("[data-cfemail]")
    if protected is not None:
        return _decode_cfemail(str(protected["data-cfemail"]))
    match = _SEND_TO.search(html)
    return f"{match.group(1)}@{match.group(2)}" if match else None


def _decode_cfemail(encoded: str) -> str:
    key = int(encoded[:2], 16)
    return "".join(chr(int(encoded[i : i + 2], 16) ^ key) for i in range(2, len(encoded), 2))


def _date(node: Tag | None) -> date | None:
    match = _DATE.search(node.get_text(" ", strip=True)) if node else None
    if match is None or match.group(2).lower() not in _MONTHS:
        return None
    return date(int(match.group(3)), _MONTHS[match.group(2).lower()], int(match.group(1)))


def _absolute(href: str) -> str:
    return href if href.startswith("http") else f"{SENATE_BASE_URL}{href}"


class SenateClient:
    def __init__(
        self,
        base_url: str = SENATE_BASE_URL,
        *,
        timeout: float = 30.0,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
        proxy: str | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            timeout=timeout,
            headers=BROWSER_HEADERS,
            proxy=proxy,
            transport=transport,
            follow_redirects=True,
        )
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        self._sleep = sleep
        # A client lives for one run: the listing and a committee are read once however many
        # acts ask for them.
        self._pages: dict[int, list[tuple[str, str]]] = {}
        self._committees: dict[int, SenateCommittee] = {}

    def find_act(self, title_final: str, *, passed_on: date) -> SenateAct | None:
        """The first act of this title received on or after the vote: titles repeat (five acts of
        term 10 are "o zmianie ustawy o podatku akcyzowym"), so neither neighbour is it."""
        found: list[SenateAct] = []
        for page in range(1, MAX_LISTING_PAGES + 1):
            for title, url in self._listing(page):
                if senate_title_matches(title, title_final):
                    act = self.read_act(url)
                    if act.received is not None and act.received >= passed_on:
                        found.append(act)
        return min(found, key=lambda a: a.received or passed_on, default=None)

    def read_act(self, url: str) -> SenateAct:
        html = self._get(url)
        assert html is not None, "only a listing page may be missing"
        act = parse_act(html, url)
        return act.model_copy(
            update={"committees": tuple(self._committee(c) for c in act.committees)}
        )

    def close(self) -> None:
        self._client.close()

    def _listing(self, page: int) -> list[tuple[str, str]]:
        if page not in self._pages:
            path = LISTING_PATH if page == 1 else f"{LISTING_PATH}page,{page}.html"
            html = self._get(f"{self._base_url}{path}", missing_ok=page > 1)
            self._pages[page] = parse_listing(html) if html is not None else []
        return self._pages[page]

    def _committee(self, committee: SenateCommittee) -> SenateCommittee:
        if committee.id not in self._committees:
            url = f"{self._base_url}/prace/komisje-senackie/komisja,{committee.id}.html"
            try:
                html = self._get(url)
                assert html is not None, "only a listing page may be missing"
                self._committees[committee.id] = parse_committee(html, committee)
            except SenateUnavailableError as exc:
                log.warning("senate committee %d unreadable: %s", committee.id, exc)
                return committee
        return self._committees[committee.id]

    def _get(self, url: str, *, missing_ok: bool = False) -> str | None:
        """The page's text; None for a 404 where `missing_ok` (a listing page past the last)."""
        if url.startswith(SENATE_BASE_URL) and self._base_url != SENATE_BASE_URL:
            url = self._base_url + url.removeprefix(SENATE_BASE_URL)
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._client.get(url)
            except httpx.TransportError as exc:
                if attempt > self._max_retries:
                    raise SenateUnavailableError(
                        f"GET {url} failed after {attempt} attempt(s): {exc}"
                    ) from exc
                self._wait(attempt, f"GET {url}: {exc}")
                continue
            if response.status_code == 404 and missing_ok:
                return None
            if response.status_code >= 400:
                if response.status_code < 500 and response.status_code != 429:
                    raise SenateUnavailableError(f"GET {url}: HTTP {response.status_code}")
                if attempt > self._max_retries:
                    raise SenateUnavailableError(
                        f"GET {url}: HTTP {response.status_code} after {attempt} attempts"
                    )
                self._wait(attempt, f"GET {url}: HTTP {response.status_code}")
                continue
            return response.text

    def _wait(self, attempt: int, reason: str) -> None:
        delay = backoff_delay(self._backoff, attempt)
        log.warning("senate retry %d in %.1fs (%s)", attempt, delay, reason)
        self._sleep(delay)
