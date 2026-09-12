"""The register reader against a recorded CSV: columns, duplicates, the register id, outages."""

import datetime as dt
from collections.abc import Callable

import httpx2 as httpx
import pytest

from lexinform.adapters.wykaz_csv import (
    DEFAULT_REGISTER_ID,
    WykazClient,
    WykazPageError,
    parse_register,
)
from lexinform.errors import WykazUnavailableError
from tests.conftest import WYKAZ_FIXTURES

Handler = Callable[[httpx.Request], httpx.Response]

PAGE = '<div id="registerVue-99887766" class="article-area--register">…</div>'


def _csv() -> str:
    return (WYKAZ_FIXTURES / "rejestr.csv").read_text(encoding="utf-8")


def _serving(csv_text: str | None = None, *, page: str = PAGE) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".csv"):
            return httpx.Response(200, text=csv_text if csv_text is not None else _csv())
        return httpx.Response(200, text=page)

    return handler


def _client(handler: Handler) -> WykazClient:
    return WykazClient(
        "https://gov.test", transport=httpx.MockTransport(handler), sleep=lambda s: None
    )


def test_every_row_is_read_with_its_wykaz_number_normalised() -> None:
    entries = parse_register(_csv())

    assert [e.number for e in entries] == ["UC168", "UD408", "RD199", "UA4"]  # newest first
    ud408 = entries[1]
    assert ud408.title == "Projekt ustawy o zmianie ustawy o cudzoziemcach"
    assert (ud408.kind, ud408.organ) == ("Projekty ustaw", "MSWiA")
    assert ud408.published_at == dt.datetime(2026, 5, 12, 13, 21, tzinfo=dt.UTC)  # 15:21 in Warsaw
    assert ud408.web_url.endswith("/projekt-ustawy-o-zmianie-ustawy-o-cudzoziemcach")
    assert ud408.planned_quarter == (2026, 3)
    assert ud408.description is not None
    assert "milczącego zakończenia postępowania" in ud408.description
    assert ud408.is_open


def test_a_number_entered_twice_keeps_the_newer_entry() -> None:
    entries = parse_register(_csv())

    duplicated = [e for e in entries if e.number == "UC168"]

    assert len(duplicated) == 1
    assert duplicated[0].web_url.endswith("karnego12")  # published 09-09, not 08-09


def test_withdrawal_and_document_kind_are_kept_as_published() -> None:
    entries = {e.number: e for e in parse_register(_csv())}

    assert entries["UA4"].is_withdrawn and entries["UA4"].resignation.startswith("Wykreślenie")
    assert not entries["RD199"].is_bill  # a rozporządzenie: read, not followed
    assert entries["RD199"].is_adopted


def test_a_register_without_the_columns_we_need_is_an_error() -> None:
    with pytest.raises(WykazPageError, match="Numer projektu"):
        parse_register('"Tytuł";"Rodzaj dokumentu";"Data publikacji";"Podgląd"\n"a";"b";"c";"d"\n')


def test_a_register_with_no_readable_row_is_an_error() -> None:
    header = _csv().splitlines()[0]

    with pytest.raises(WykazPageError, match="no readable rows"):
        parse_register(header + "\n")


def test_a_paragraph_longer_than_the_csv_modules_own_limit_is_still_read() -> None:
    """`Istota rozwiązań` is free prose; the csv module refuses a field over 128 KB."""
    huge = _csv().replace("Polska przekształciła", "X" * 200_000 + " Polska przekształciła", 1)

    entries = parse_register(huge)

    assert [e.number for e in entries] == ["UC168", "UD408", "RD199", "UA4"]


def test_rows_the_reader_had_to_drop_are_counted_in_the_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A register whose date format moved drops every row, and says only "no readable rows"."""
    moved = _csv().replace(" 15:21", "T15:21:00")

    with caplog.at_level("WARNING"):
        parse_register(moved)

    assert "1 register row(s) dropped" in caplog.text
    assert "2026-05-12T15:21:00" in caplog.text


def test_the_register_id_comes_from_the_page() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return _serving()(request)

    entries = _client(handler).entries()

    assert requested == ["/web/premier/wplip-rm", "/register-file/Rejestr_99887766.csv"]
    assert len(entries) == 4


def test_a_page_that_lost_its_register_id_falls_back_to_the_known_one() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return _serving(page="<div>przebudowa</div>")(request)

    _client(handler).entries()

    assert requested[-1] == f"/register-file/Rejestr_{DEFAULT_REGISTER_ID}.csv"


def test_the_register_is_downloaded_once_per_client() -> None:
    downloads: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        downloads.append(request.url.path)
        return _serving()(request)

    client = _client(handler)
    client.entries()
    client.entries()

    assert client.find("UD 408") is not None
    assert downloads.count("/register-file/Rejestr_99887766.csv") == 1


def test_gov_pl_not_answering_ends_the_phase() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="")

    with pytest.raises(WykazUnavailableError, match="503"):
        _client(handler).entries()


def test_a_transport_error_ends_the_phase() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("no route", request=request)

    with pytest.raises(WykazUnavailableError, match="attempt"):
        _client(handler).entries()
