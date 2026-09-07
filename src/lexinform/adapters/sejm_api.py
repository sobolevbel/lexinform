"""Client for the official Sejm REST API (https://api.sejm.gov.pl).

Verified behaviour of the API (September 2026):
- `/processes` honours `limit`, `offset`, `modifiedSince` (full ISO datetime, date-only is an
  error) and `documentType` (the Polish display string, e.g. "projekt ustawy"); `sort_by` is
  ignored, results come in ascending print-number order. We therefore paginate by offset until a
  short page.
- `/prints` ignores `limit`/`modifiedSince`, so we never list it; we only fetch single prints.
- Attachments are served from `/prints/{number}/{attachment name}`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from lexinform.models import (
    Attachment,
    DocumentType,
    PrintInfo,
    ProcessDetail,
    ProcessSummary,
    Stage,
)

log = logging.getLogger(__name__)

BILL_DOCUMENT_TYPE = "projekt ustawy"


class SejmApiError(RuntimeError):
    """Raised for non-retryable HTTP failures (4xx) or after retries are exhausted."""


class SejmApiClient:
    def __init__(
        self,
        base_url: str = "https://api.sejm.gov.pl",
        *,
        page_size: int = 100,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"Accept": "application/json", "User-Agent": "lexinform (+github)"},
            transport=transport,
            follow_redirects=True,
        )
        self._page_size = page_size
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        self._sleep = sleep

    # ------------------------------------------------------------------ public API

    def iter_processes(
        self,
        term: int,
        *,
        modified_since: datetime | None = None,
        document_type: str | None = None,
    ) -> Iterator[ProcessSummary]:
        params: dict[str, str | int] = {"limit": self._page_size}
        if modified_since is not None:
            params["modifiedSince"] = modified_since.replace(tzinfo=None, microsecond=0).isoformat()
        if document_type:
            params["documentType"] = document_type
        offset = 0
        while True:
            page = self._get_json(
                f"/sejm/term{term}/processes", params={**params, "offset": offset}
            )
            if not isinstance(page, list):
                raise SejmApiError(f"Unexpected /processes payload: {type(page).__name__}")
            for item in page:
                yield parse_process_summary(item)
            if len(page) < self._page_size:
                return
            offset += self._page_size

    def get_process(self, term: int, number: str) -> ProcessDetail:
        data = self._get_json(f"/sejm/term{term}/processes/{quote(number)}")
        return parse_process_detail(data)

    def get_print(self, term: int, number: str) -> PrintInfo:
        data = self._get_json(f"/sejm/term{term}/prints/{quote(number)}")
        return parse_print(data, term=term)

    def attachment_size(self, url: str) -> int | None:
        response = self._request("HEAD", url)
        length = response.headers.get("content-length")
        return int(length) if length and length.isdigit() else None

    def download(self, url: str) -> bytes:
        return self._request("GET", url).content

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------ internals

    def _get_json(self, path: str, params: dict[str, str | int] | None = None) -> Any:
        return self._request("GET", path, params=params).json()

    def _request(
        self, method: str, url: str, *, params: dict[str, str | int] | None = None
    ) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._client.request(method, url, params=params)
            except httpx.TransportError as exc:
                if attempt > self._max_retries:
                    raise SejmApiError(
                        f"{method} {url} failed after {attempt} attempts: {exc}"
                    ) from exc
                self._wait(attempt, f"{method} {url}: {exc}")
                continue
            if response.status_code >= 500 and attempt <= self._max_retries:
                self._wait(attempt, f"{method} {url}: HTTP {response.status_code}")
                continue
            if response.status_code >= 400:
                raise SejmApiError(f"{method} {url}: HTTP {response.status_code}")
            return response

    def _wait(self, attempt: int, reason: str) -> None:
        delay = self._backoff * (2 ** (attempt - 1))
        log.warning("Sejm API retry %d in %.1fs (%s)", attempt, delay, reason)
        self._sleep(delay)


# ---------------------------------------------------------------------- parsing helpers


def _date(value: Any) -> Any:
    if not value:
        return None
    return datetime.fromisoformat(str(value)).date() if "T" in str(value) else str(value)


def _datetime(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value))


def _document_type_enum(value: Any) -> DocumentType:
    try:
        return DocumentType(str(value))
    except ValueError:
        return DocumentType.OTHER


def _summary_fields(item: dict[str, Any]) -> dict[str, Any]:
    change_date = _datetime(item.get("changeDate")) or _datetime(item.get("webGeneratedDate"))
    if change_date is None:
        raise SejmApiError(f"Process {item.get('number')} has no changeDate")
    return {
        "term": int(item["term"]),
        "number": str(item["number"]),
        "title": str(item.get("title") or "").strip(),
        "description": (item.get("description") or None),
        "document_type": str(item.get("documentType") or ""),
        "document_type_enum": _document_type_enum(item.get("documentTypeEnum")),
        "process_start_date": _date(item.get("processStartDate")),
        "document_date": _date(item.get("documentDate")),
        "change_date": change_date,
        "closure_date": _date(item.get("closureDate")),
        "passed": item.get("passed"),
        "urgency_status": item.get("urgencyStatus"),
        "eu_related": str(item.get("UE") or "NO").upper() == "YES",
        "rcl_num": item.get("rclNum"),
        "rcl_link": item.get("rclLink"),
        "prints_considered_jointly": tuple(
            str(p) for p in item.get("printsConsideredJointly") or ()
        ),
    }


def parse_process_summary(item: dict[str, Any]) -> ProcessSummary:
    return ProcessSummary(**_summary_fields(item))


def parse_stage(item: dict[str, Any]) -> Stage:
    return Stage(
        stage_name=str(item.get("stageName") or ""),
        stage_type=str(item.get("stageType") or ""),
        date=_date(item.get("date")),
        print_number=item.get("printNumber"),
        sitting_num=item.get("sittingNum"),
        decision=item.get("decision"),
        committee_code=item.get("committeeCode"),
        report_file=item.get("reportFile"),
        text_after3=item.get("textAfter3"),
        children=tuple(parse_stage(c) for c in item.get("children") or ()),
    )


def parse_process_detail(item: dict[str, Any]) -> ProcessDetail:
    return ProcessDetail(
        **_summary_fields(item),
        stages=tuple(parse_stage(s) for s in item.get("stages") or ()),
        title_final=item.get("titleFinal"),
        eli=item.get("ELI"),
    )


def attachment_url(base_url: str, term: int, number: str, name: str) -> str:
    return f"{base_url.rstrip('/')}/sejm/term{term}/prints/{quote(number)}/{quote(name)}"


def parse_print(
    item: dict[str, Any], *, term: int, base_url: str = "https://api.sejm.gov.pl"
) -> PrintInfo:
    number = str(item["number"])
    attachments = tuple(
        Attachment(
            print_number=number, name=str(n), url=attachment_url(base_url, term, number, str(n))
        )
        for n in item.get("attachments") or ()
    )
    additional = tuple(
        parse_print(p, term=term, base_url=base_url) for p in item.get("additionalPrints") or ()
    )
    return PrintInfo(
        term=term,
        number=number,
        title=str(item.get("title") or "").strip(),
        document_date=_date(item.get("documentDate")),
        delivery_date=_date(item.get("deliveryDate")),
        change_date=_datetime(item.get("changeDate")),
        attachments=attachments,
        additional_prints=additional,
    )
