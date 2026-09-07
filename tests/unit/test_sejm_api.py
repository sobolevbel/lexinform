from __future__ import annotations

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx2 as httpx
import pytest

from lexinform.adapters.sejm_api import SejmApiClient, SejmApiError
from lexinform.models import DocumentType
from tests.conftest import FIXTURES


def _client(handler, **kw):  # type: ignore[no-untyped-def]
    return SejmApiClient(
        "https://api.test", transport=httpx.MockTransport(handler), sleep=lambda s: None, **kw
    )


def test_iter_processes_paginates_and_passes_filters() -> None:
    page = json.loads((FIXTURES / "processes_page.json").read_text())
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        offset = int(params["offset"])
        chunk = page[offset : offset + 8]
        return httpx.Response(200, json=chunk)

    client = _client(handler, page_size=8)
    items = list(
        client.iter_processes(
            10, modified_since=datetime(2026, 9, 1, tzinfo=UTC), document_type="projekt ustawy"
        )
    )
    assert len(items) == len(page)
    assert [p["offset"] for p in seen] == ["0", "8", "16"]
    # the API speaks naive Europe/Warsaw time: 00:00 UTC on 1 September is 02:00 CEST
    assert seen[0]["modifiedSince"] == "2026-09-01T02:00:00"
    assert seen[0]["documentType"] == "projekt ustawy"
    assert items[0].document_type_enum is DocumentType.BILL


def test_get_process_and_print_parse_fixture() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/processes/3039"):
            return httpx.Response(200, content=(FIXTURES / "process_3039.json").read_bytes())
        if request.url.path.endswith("/prints/3039"):
            return httpx.Response(200, content=(FIXTURES / "print_3039.json").read_bytes())
        return httpx.Response(404)

    client = _client(handler)
    detail = client.get_process(10, "3039")
    assert detail.number == "3039"
    assert detail.stages[1].children[0].committee_code
    info = client.get_print(10, "3039")
    assert info.main_pdf is not None
    assert info.main_pdf.url == "https://api.sejm.gov.pl/sejm/term10/prints/3039/3039.pdf"
    assert info.additional_prints[0].number == "3039-001"


def test_retries_on_5xx_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, content=b"%PDF", headers={"content-length": "4"})

    client = _client(handler)
    assert client.download("https://api.test/x.pdf") == b"%PDF"
    assert calls["n"] == 3


def test_4xx_raises_without_retry() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    with pytest.raises(SejmApiError):
        _client(handler).get_process(10, "999999")
    assert calls["n"] == 1


def test_attachment_size_uses_head() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "HEAD"
        return httpx.Response(200, headers={"content-length": "12345"})

    assert _client(handler).attachment_size("https://api.test/a.pdf") == 12345


def test_pagination_stops_on_empty_page_and_on_repeated_page() -> None:
    page = json.loads((FIXTURES / "processes_page.json").read_text())[:8]

    def full_pages_then_empty(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        return httpx.Response(200, json=page if offset == 0 else [])

    assert len(list(_client(full_pages_then_empty, page_size=8).iter_processes(10))) == 8

    def ignores_offset(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page)

    # the server keeps returning the same page: yield it once, never loop forever
    assert len(list(_client(ignores_offset, page_size=8).iter_processes(10))) == 8


def test_429_is_retried_like_an_outage() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, content=(FIXTURES / "print_3039.json").read_bytes())

    assert _client(handler).get_print(10, "3039").number == "3039"
    assert calls["n"] == 2


def test_change_dates_are_converted_from_warsaw_time_and_ue_status_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=(FIXTURES / "process_3039.json").read_bytes())

    detail = _client(handler).get_process(10, "3039")
    raw = json.loads((FIXTURES / "process_3039.json").read_text())
    naive = datetime.fromisoformat(raw["changeDate"])
    assert detail.change_date.tzinfo is not None
    assert detail.change_date.replace(tzinfo=None) != naive  # shifted by the Warsaw offset
    assert detail.change_date.astimezone(ZoneInfo("Europe/Warsaw")).replace(tzinfo=None) == naive
    assert detail.eu_related is (raw.get("UE", "NO") != "NO")
