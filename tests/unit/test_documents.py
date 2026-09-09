"""PdfTextLoader: one download per URL per run, and the cases that yield no text."""

import pytest

from lexinform.errors import SejmApiUnavailableError
from lexinform.services.documents import MIN_TEXT_CHARS, PdfTextLoader
from tests.fakes import FakeSejmGateway, FakeTextExtractor

URL = "https://api.test/sejm/term10/prints/1/1.pdf"
LONG_TEXT = "Art. 1. " * 100


def _loader(
    gateway: FakeSejmGateway, text: str = LONG_TEXT, *, max_bytes: int = 1000, cache_size: int = 32
) -> PdfTextLoader:
    return PdfTextLoader(
        gateway, FakeTextExtractor(text), max_bytes=max_bytes, cache_size=cache_size
    )


def test_text_is_extracted_once_and_served_from_the_cache() -> None:
    gateway = FakeSejmGateway(files={URL: b"%PDF"})
    loader = _loader(gateway)

    first = loader.load(URL)
    second = loader.load(URL)

    assert first == second == LONG_TEXT
    assert gateway.calls.count(f"download:{URL}") == 1


def test_cache_evicts_the_oldest_url_when_full() -> None:
    urls = [f"https://api.test/{i}.pdf" for i in range(3)]
    gateway = FakeSejmGateway(files={u: b"%PDF" for u in urls})
    loader = _loader(gateway, cache_size=2)

    for url in urls:
        loader.load(url)
    loader.load(urls[0])  # evicted by then: downloaded again

    assert gateway.calls.count(f"download:{urls[0]}") == 2
    assert gateway.calls.count(f"download:{urls[2]}") == 1


def test_oversized_pdf_yields_no_text() -> None:
    gateway = FakeSejmGateway(files={URL: b"%" * 2000})

    assert _loader(gateway, max_bytes=1000).load(URL) is None


def test_pdf_without_a_text_layer_yields_no_text() -> None:
    gateway = FakeSejmGateway(files={URL: b"%PDF"})

    assert _loader(gateway, text="x" * (MIN_TEXT_CHARS - 1)).load(URL) is None


def test_broken_pdf_propagates_as_an_ordinary_error() -> None:
    gateway = FakeSejmGateway(files={URL: b"%PDF"})
    loader = PdfTextLoader(
        gateway, FakeTextExtractor(error=ValueError("not a PDF")), max_bytes=1000
    )

    with pytest.raises(ValueError, match="not a PDF"):
        loader.load(URL)


def test_sejm_outage_propagates_as_unavailable() -> None:
    gateway = FakeSejmGateway(files={URL: b"%PDF"}, outages={"download"})

    with pytest.raises(SejmApiUnavailableError):
        _loader(gateway).load(URL)
