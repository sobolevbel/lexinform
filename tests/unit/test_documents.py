"""TextLoader: one download per URL per run, routing by host, and the cases without text."""

import pytest

from lexinform.errors import SejmApiUnavailableError
from lexinform.models import TextDocument
from lexinform.sections import PAGE_BREAK
from lexinform.services.documents import MIN_TEXT_CHARS, TextLoader
from tests.fakes import FakeSejmGateway, FakeTextExtractor

HOST = "api.test"
URL = f"https://{HOST}/sejm/term10/prints/1/1.pdf"
LONG_TEXT = "Art. 1. " * 100


def _loader(
    gateway: FakeSejmGateway,
    text: str = LONG_TEXT,
    *,
    error: Exception | None = None,
    max_bytes: int = 1000,
    cache_size: int = 32,
) -> TextLoader:
    return TextLoader(
        {HOST: gateway.download},
        FakeTextExtractor(text, error=error),
        max_bytes=max_bytes,
        cache_size=cache_size,
    )


def test_text_is_extracted_once_and_served_from_the_cache() -> None:
    gateway = FakeSejmGateway(files={URL: b"%PDF"})
    loader = _loader(gateway)

    first = loader.load(URL)
    second = loader.load(URL)

    assert first == second == LONG_TEXT
    assert gateway.calls.count(f"download:{URL}") == 1


def test_cache_evicts_the_oldest_url_when_full() -> None:
    urls = [f"https://{HOST}/{i}.pdf" for i in range(3)]
    gateway = FakeSejmGateway(files={u: b"%PDF" for u in urls})
    loader = _loader(gateway, cache_size=2)

    for url in urls:
        loader.load(url)
    loader.load(urls[0])  # evicted by then: downloaded again

    assert gateway.calls.count(f"download:{urls[0]}") == 2
    assert gateway.calls.count(f"download:{urls[2]}") == 1


def test_oversized_file_yields_no_text() -> None:
    gateway = FakeSejmGateway(files={URL: b"%" * 2000})

    assert _loader(gateway, max_bytes=1000).load(URL) is None


def test_file_without_a_text_layer_yields_no_text() -> None:
    gateway = FakeSejmGateway(files={URL: b"%PDF"})

    assert _loader(gateway, text="x" * (MIN_TEXT_CHARS - 1)).load(URL) is None


def test_broken_file_propagates_as_an_ordinary_error() -> None:
    gateway = FakeSejmGateway(files={URL: b"%PDF"})

    with pytest.raises(ValueError, match="not a PDF"):
        _loader(gateway, error=ValueError("not a PDF")).load(URL)


def test_outage_of_the_host_propagates_as_unavailable() -> None:
    gateway = FakeSejmGateway(files={URL: b"%PDF"}, outages={"download"})

    with pytest.raises(SejmApiUnavailableError):
        _loader(gateway).load(URL)


def test_a_host_without_a_downloader_is_an_ordinary_error() -> None:
    loader = _loader(FakeSejmGateway())

    with pytest.raises(ValueError, match="no downloader for host 'elsewhere.test'"):
        loader.load("https://elsewhere.test/x.pdf")


def test_document_text_appends_the_extra_files_and_skips_unreadable_ones() -> None:
    urls = [f"https://{HOST}/{name}" for name in ("projekt.pdf", "uzasadnienie.pdf", "osr.pdf")]
    gateway = FakeSejmGateway(files={urls[0]: b"%PDF", urls[2]: b"%PDF"})  # uzasadnienie: 404
    document = TextDocument(url=urls[0], kind="rcl", extra_urls=(urls[1], urls[2]))

    text = _loader(gateway).load_document(document)

    assert text == PAGE_BREAK.join([LONG_TEXT, LONG_TEXT])


def test_document_without_a_readable_main_file_has_no_text() -> None:
    gateway = FakeSejmGateway(files={URL: b"%" * 2000})
    document = TextDocument(url=URL, kind="print", extra_urls=(URL,))

    assert _loader(gateway, max_bytes=1000).load_document(document) is None
