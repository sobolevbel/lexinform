import pytest

from lexinform.concurrency import fan_out
from lexinform.services.documents import TextLoader
from tests.fakes import FakeSejmGateway, FakeTextExtractor

URL = "https://one.test/draft.pdf"
BODY = b"%PDF-a"


def loader(gateway: FakeSejmGateway, *, extractor: FakeTextExtractor | None = None) -> TextLoader:
    return TextLoader(
        {"one.test": gateway.download, "two.test": gateway.download},
        extractor or FakeTextExtractor(),
        max_bytes=1000,
    )


def test_same_bytes_across_urls_and_hosts_are_counted_once_as_distinct_bodies() -> None:
    urls = [URL, URL + "?new=1", "https://two.test/draft.pdf"]
    gateway = FakeSejmGateway(files=dict.fromkeys(urls, BODY))
    reader = loader(gateway)

    for url in (*urls, URL):
        reader.read(url)

    metrics = reader.read_metrics()
    assert metrics.completed == 3 and metrics.cache_hits == 1
    assert metrics.body_bytes == len(BODY) * 3
    assert metrics.distinct_bodies == 1 and metrics.distinct_body_bytes == len(BODY)
    assert metrics.duplicate_body_bytes == len(BODY) * 2
    assert metrics.hosts["one.test"].completed == 2
    assert metrics.hosts["two.test"].completed == 1
    assert "draft.pdf" not in metrics.model_dump_json()
    assert BODY.decode() not in metrics.model_dump_json()


def test_reset_discards_observations_without_invalidating_cached_files() -> None:
    gateway = FakeSejmGateway(files={URL: BODY})
    reader = loader(gateway)
    first = reader.read(URL)

    reader.reset_read_metrics()
    again = reader.read(URL)

    assert first == again
    assert gateway.calls.count(f"download:{URL}") == 1
    metrics = reader.read_metrics()
    assert metrics.cache_hits == 1 and not metrics.hosts
    assert metrics.distinct_bodies == metrics.body_bytes == metrics.distinct_body_bytes == 0


def test_incomplete_and_failed_attempts_do_not_claim_known_response_sizes() -> None:
    gateway = FakeSejmGateway(files={URL: b"x" * 1001})
    reader = loader(gateway)

    assert reader.read(URL).oversize
    with pytest.raises(KeyError):
        reader.read(URL + ".missing")

    metrics = reader.read_metrics()
    host = metrics.hosts["one.test"]
    assert (host.attempts, host.over_limit, host.failed, host.completed) == (2, 1, 1, 0)
    assert metrics.body_bytes == metrics.distinct_body_bytes == 0
    assert metrics.distinct_bodies == 0


def test_completed_bytes_are_observed_even_when_extraction_fails() -> None:
    gateway = FakeSejmGateway(files={URL: BODY})
    reader = loader(gateway, extractor=FakeTextExtractor(error=ValueError("unsupported")))

    with pytest.raises(ValueError, match="unsupported"):
        reader.read(URL)

    metrics = reader.read_metrics()
    assert metrics.completed == 1 and metrics.body_bytes == len(BODY)
    assert metrics.hosts["one.test"].failed == 0


def test_derived_scan_pages_do_not_create_another_download_measurement() -> None:
    gateway = FakeSejmGateway(files={URL: b"%PDF-" + b"x" * 400})
    reader = loader(gateway, extractor=FakeTextExtractor("", page_count=30))
    scan = reader.load_scan(URL, cover_letter=False)
    assert scan is not None, "the fake PDF supplies thirty pages below the byte limit"
    before = reader.read_metrics()

    window = reader.first_pages(scan, 8)

    assert window is not None, "the thirty-page source has an eight-page triage window"
    assert reader.read_metrics() == before
    assert before.completed == 1 and before.body_bytes == len(gateway.files[URL])


@pytest.mark.parametrize("workers", [1, 4])
def test_parallel_observations_keep_every_completed_download(workers: int) -> None:
    urls = [f"https://one.test/{index}.pdf" for index in range(20)]
    gateway = FakeSejmGateway(
        files={url: BODY + bytes([index % 2]) for index, url in enumerate(urls)}
    )
    reader = loader(gateway)

    for outcome in fan_out(urls, reader.read, workers=workers):
        outcome.result()

    metrics = reader.read_metrics()
    assert metrics.completed == metrics.hosts["one.test"].attempts == len(urls)
    assert metrics.body_bytes == (len(BODY) + 1) * len(urls)
    assert metrics.distinct_bodies == 2
    assert metrics.distinct_body_bytes == 2 * (len(BODY) + 1)
    metrics.hosts.clear()
    assert reader.read_metrics().completed == len(urls)
