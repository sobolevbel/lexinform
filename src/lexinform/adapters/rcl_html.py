"""Scraper for legislacja.rcl.gov.pl (Rządowy Proces Legislacyjny), which has no API.

Verified behaviour of the site (September 2026):
- `/lista?typeId=2` lists bills; `sKey=modifiedDate&sOrder=desc` sorts by the last change, so a
  "modified since" walk stops at the first older row. `pSize` accepts 10/50/100, `pNumber` pages.
- Unknown query parameters (`pSize=all`, `modifiedDateFrom`) and blocked clients get HTTP 200
  with a "Request Rejected" page from the WAF: we treat it as the site being unavailable.
- `/projekt/{id}` is the timeline only; folders and documents of a stage need
  `/projekt/{id}/katalog/{stageId}` (one request per stage, 5-10 s each).
- `/getIdFromLegislacja?number=RM-0610-139-26` redirects (302) to the project page: the join
  between a Sejm print (`rclNum`) and the RCL project.
"""

import logging
import re
import time
from collections.abc import Callable, Iterator
from datetime import date, datetime
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx2 as httpx
from bs4 import BeautifulSoup, Tag

from lexinform.errors import AttachmentTooLargeError, RclUnavailableError
from lexinform.models import (
    RclDocument,
    RclFolder,
    RclProject,
    RclProjectSummary,
    RclStage,
    normalize_wykaz_number,
)
from lexinform.models.rcl import RCL_BASE_URL, StageState, parse_stage_label

__all__ = ["RclClient", "RclPageError", "parse_list", "parse_project", "parse_stage_catalog"]

log = logging.getLogger(__name__)

BILLS_TYPE_ID = 2
_REJECTED_TITLE = "request rejected"
_DOCUMENT_ID = re.compile(r"dokument(\d+)\.", re.IGNORECASE)
_PROJECT_ID = re.compile(r"/projekt/(\d+)")


class RclPageError(RuntimeError):
    """A page that does not look like what we expect (missing project, changed markup)."""


class RclClient:
    def __init__(
        self,
        base_url: str = RCL_BASE_URL,
        *,
        page_size: int = 100,
        timeout: float = 60.0,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            headers={
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "pl-PL,pl;q=0.9",
                "User-Agent": "lexinform (+github)",
            },
            transport=transport,
            follow_redirects=False,
        )
        self._page_size = page_size
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        self._sleep = sleep

    # ------------------------------------------------------------------ public API

    def list_projects(self, *, modified_since: date) -> Iterator[RclProjectSummary]:
        page = 1
        previous_first: int | None = None
        while True:
            params: dict[str, str | int] = {
                "typeId": BILLS_TYPE_ID,
                "sKey": "modifiedDate",
                "sOrder": "desc",
                "pSize": self._page_size,
                "pNumber": page,
            }
            rows = parse_list(self._get_text("/lista", params=params), base_url=self._base_url)
            if not rows or rows[0].id == previous_first:
                return
            previous_first = rows[0].id
            for row in rows:
                if row.modified < modified_since:
                    return
                yield row
            if len(rows) < self._page_size:
                return
            page += 1

    def get_project(self, project_id: int) -> RclProject:
        html = self._get_text(f"/projekt/{project_id}")
        return parse_project(html, project_id, base_url=self._base_url)

    def get_stage(self, project_id: int, stage_id: int) -> RclStage:
        html = self._get_text(f"/projekt/{project_id}/katalog/{stage_id}")
        return parse_stage_catalog(html, stage_id, base_url=self._base_url)

    def resolve_project_id(self, rm_number: str) -> int | None:
        response = self._request(
            "GET", "/getIdFromLegislacja", params={"number": rm_number}, allow_404=True
        )
        response.close()
        location = response.headers.get("location", "")
        match = _PROJECT_ID.search(location)
        return int(match.group(1)) if match else None

    def download(self, url: str, *, max_bytes: int | None = None) -> bytes:
        """Stream a document; stop as soon as `max_bytes` is exceeded."""
        response = self._request("GET", url, stream=True)
        try:
            chunks: list[bytes] = []
            received = 0
            for chunk in response.iter_bytes():
                received += len(chunk)
                if max_bytes is not None and received > max_bytes:
                    raise AttachmentTooLargeError(url, max_bytes)
                chunks.append(chunk)
            body = b"".join(chunks)
        finally:
            response.close()
        if _is_rejected_html(body):
            raise RclUnavailableError(f"GET {url}: request rejected by the WAF")
        return body

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------ internals

    def _get_text(self, path: str, params: dict[str, str | int] | None = None) -> str:
        response = self._request("GET", path, params=params)
        text = response.text
        if _is_rejected_html(text.encode("utf-8", "ignore")):
            raise RclUnavailableError(f"GET {path}: request rejected by the WAF")
        return text

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str | int] | None = None,
        allow_404: bool = False,
        stream: bool = False,
    ) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            try:
                request = self._client.build_request(method, url, params=params)
                response = self._client.send(request, stream=stream)
            except httpx.TransportError as exc:
                if attempt > self._max_retries:
                    raise RclUnavailableError(
                        f"{method} {url} failed after {attempt} attempts: {exc}"
                    ) from exc
                self._wait(attempt, f"{method} {url}: {exc}")
                continue
            if response.status_code >= 500 or response.status_code == 429:
                response.close()
                if attempt <= self._max_retries:
                    self._wait(attempt, f"{method} {url}: HTTP {response.status_code}")
                    continue
                raise RclUnavailableError(
                    f"{method} {url}: HTTP {response.status_code} after {attempt} attempts"
                )
            if response.status_code == 404 and allow_404:
                return response
            if response.status_code >= 400:
                response.close()
                raise RclPageError(f"{method} {url}: HTTP {response.status_code}")
            return response

    def _wait(self, attempt: int, reason: str) -> None:
        delay = self._backoff * (2 ** (attempt - 1))
        log.warning("RCL retry %d in %.1fs (%s)", attempt, delay, reason)
        self._sleep(delay)


def _is_rejected_html(body: bytes) -> bool:
    head = body[:2048].lower()
    return b"<title>" in head and _REJECTED_TITLE.encode() in head


# ---------------------------------------------------------------------- parsers


def parse_list(html: str, *, base_url: str = RCL_BASE_URL) -> list[RclProjectSummary]:
    """Rows of `/lista`: id, title, applicant, wykaz number, created and modified dates."""
    soup = _soup(html)
    rows: list[RclProjectSummary] = []
    for tr in soup.select("table#table tbody tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 5:
            continue
        link = cells[0].find("a")
        if not isinstance(link, Tag):
            continue
        match = _PROJECT_ID.search(_href(link))
        if match is None:
            continue
        rows.append(
            RclProjectSummary(
                id=int(match.group(1)),
                title=_text(link),
                applicant=_text(cells[1]),
                wykaz_number=normalize_wykaz_number(_text(cells[2])),
                created=_parse_date(_text(cells[3])) or date.min,
                modified=_parse_date(_text(cells[4])) or date.min,
            )
        )
    return rows


def parse_project(html: str, project_id: int, *, base_url: str = RCL_BASE_URL) -> RclProject:
    """The project page: metadata rows and the stage timeline (folders are not on this page)."""
    soup = _soup(html)
    title_tag = soup.select_one("div.rcl-title")
    info = soup.select_one("div.info")
    if title_tag is None or info is None:
        raise RclPageError(f"project {project_id}: no title or info block on the page")
    fields = _info_fields(info)
    wykaz = fields.get("Numer z wykazu")
    stages = tuple(_parse_timeline(soup, base_url=base_url))
    sejm_url, rm_number = _sejm_reference(soup)
    created = _parse_date(_text(fields["Data utworzenia"])) if "Data utworzenia" in fields else None
    if created is None:
        raise RclPageError(f"project {project_id}: no creation date")
    modified = max([created, *(st.modified for st in stages if st.modified)])
    return RclProject(
        id=project_id,
        title=_text(title_tag),
        applicant=_text(fields["Wnioskodawca"]) if "Wnioskodawca" in fields else "",
        wykaz_number=normalize_wykaz_number(_text(wykaz)) if wykaz else None,
        wykaz_url=_first_href(wykaz) if wykaz else None,
        created=created,
        modified=modified,
        departments=_links_text(fields.get("Działy")),
        keywords=_links_text(fields.get("Hasła")),
        status=_text(fields["Status projektu"]) if "Status projektu" in fields else "",
        eu_note=_text(fields["Projekt realizuje przepisy prawa Unii Europejskiej"])
        if "Projekt realizuje przepisy prawa Unii Europejskiej" in fields
        else None,
        term_label=_text(fields["Kadencja"]) if "Kadencja" in fields else None,
        rm_number=rm_number,
        sejm_url=sejm_url,
        stages=stages,
    )


def parse_stage_catalog(html: str, stage_id: int, *, base_url: str = RCL_BASE_URL) -> RclStage:
    """One stage with its folders and documents, from `/projekt/{id}/katalog/{stageId}`."""
    soup = _soup(html)
    node = soup.find("li", id=str(stage_id))
    if not isinstance(node, Tag):
        raise RclPageError(f"stage {stage_id}: not on the page")
    stage = _parse_stage_node(node)
    folders: list[RclFolder] = []
    for box in node.select("div.clearbox"):
        head = box.select_one("li.childdir")
        if head is None or not head.get("id"):
            continue
        folder_id = int(str(head.get("id")))
        name = _own_text(head)
        modified_tag = head.select_one("div.small2")
        modified = _date_after_colon(_text(modified_tag)) if modified_tag else None
        documents = tuple(
            doc for doc in (_parse_document(li, base_url) for li in box.select("li.doc")) if doc
        )
        folders.append(RclFolder(id=folder_id, name=name, modified=modified, documents=documents))
    return stage.model_copy(update={"folders": tuple(folders)})


# ---------------------------------------------------------------------- pieces


def _soup(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True).lower() if soup.title else ""
    if title == _REJECTED_TITLE:
        raise RclUnavailableError("request rejected by the WAF")
    return soup


def _parse_timeline(soup: BeautifulSoup, *, base_url: str) -> Iterator[RclStage]:
    for node in soup.select("ul.cbp_tmtimeline > li[id]"):
        try:
            yield _parse_stage_node(node)
        except ValueError:
            continue  # a decorative node without a "N. name" label


def _parse_stage_node(node: Tag) -> RclStage:
    label = node.select_one("div[class^=cbp_tmlabel] > div")
    if label is None:
        raise ValueError("stage without a label")
    link = label.find("a", recursive=False)
    number, name = parse_stage_label(_text(link) if isinstance(link, Tag) else _own_text(label))
    started = ended = modified = None
    for p in node.select("span.cbp_tmtime p"):
        text = _text(p).lower()
        if text.startswith("rozpoczęcie"):
            started = _date_after_colon(text)
        elif text.startswith("zakończenie"):
            ended = _date_after_colon(text)
    small = label.select_one("div.small2")
    if small is not None:
        modified = _date_after_colon(_text(small))
    return RclStage(
        id=int(str(node.get("id"))),
        number=number,
        name=name,
        state=_state_of(node),
        started=started,
        ended=ended,
        modified=modified,
    )


def _state_of(node: Tag) -> StageState:
    icon = node.select_one("div[class^=cbp_tmicon]")
    classes = " ".join(icon.get_attribute_list("class")) if icon else ""
    if "notstart" in classes:
        return "not_started"
    if "active" in classes:
        return "active"
    return "reached"


def _parse_document(li: Tag, base_url: str) -> RclDocument | None:
    link = li.find("a")
    if not isinstance(link, Tag):
        return None
    href = _href(link)
    match = _DOCUMENT_ID.search(href)
    if match is None:
        return None
    author = created = None
    for small in li.select("div.small3"):
        text = _text(small)
        if "Autor dokumentu:" in text:
            author = text.split("Autor dokumentu:", 1)[1].split(", wprowadzony", 1)[0].strip()
        if "Data utworzenia:" in text:
            created = _date_after_colon(text)
    return RclDocument(
        id=int(match.group(1)),
        name=_text(link),
        url=urljoin(base_url + "/", href),
        created=created,
        author=author or None,
    )


def _info_fields(info: Tag) -> dict[str, Tag]:
    """`div.info` rows: label ("Wnioskodawca") -> the value cell."""
    fields: dict[str, Tag] = {}
    for row in info.select("div.row"):
        cells = row.find_all("div", recursive=False)
        if len(cells) >= 2:
            fields[_text(cells[0]).rstrip(":")] = cells[1]
    return fields


def _sejm_reference(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    for link in soup.select("ul.cbp_tmtimeline a[href]"):
        href = _href(link)
        if "sejm.gov.pl" not in href:
            continue
        ids = parse_qs(urlparse(href).query).get("Id", [])
        return href, (ids[0] if ids else None)
    return None, None


def _links_text(cell: Tag | None) -> tuple[str, ...]:
    if cell is None:
        return ()
    return tuple(_text(a) for a in cell.find_all("a") if _text(a))


def _first_href(cell: Tag) -> str | None:
    link = cell.find("a")
    return _href(link) or None if isinstance(link, Tag) else None


def _href(tag: Tag) -> str:
    value: Any = tag.get("href")
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value) if value else ""


def _text(tag: Tag | None) -> str:
    return " ".join(tag.get_text(" ").split()) if tag is not None else ""


def _own_text(tag: Tag) -> str:
    """Text of the element without its child elements (the folder name before its dates)."""
    return " ".join(" ".join(s for s in tag.find_all(string=True, recursive=False)).split())


def _date_after_colon(text: str) -> date | None:
    return _parse_date(text.rsplit(":", 1)[-1]) if ":" in text else None


def _parse_date(text: str) -> date | None:
    text = text.strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%d-%m-%Y").date()
    except ValueError:
        return None
