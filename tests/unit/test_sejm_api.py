from __future__ import annotations

import json
from datetime import UTC, datetime

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
    assert seen[0]["modifiedSince"] == "2026-09-01T00:00:00"
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
