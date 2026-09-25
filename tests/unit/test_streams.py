from collections.abc import Iterator

import httpx2 as httpx
import pytest

from lexinform.adapters.orka import OrkaClient
from lexinform.adapters.rcl_html import RclClient
from lexinform.adapters.sejm_api import SejmApiClient
from lexinform.adapters.streams import read_bounded
from lexinform.errors import AttachmentTooLargeError


@pytest.mark.parametrize(
    ("chunks", "limit", "expected"),
    [((), 0, b""), ((b"ab", b"c"), 3, b"abc"), ((b"ab", b"c"), None, b"abc")],
)
def test_read_bounded(chunks: tuple[bytes, ...], limit: int | None, expected: bytes) -> None:
    assert read_bounded(chunks, "https://example.test/file", limit) == expected


def test_limit_stops_consuming_the_stream() -> None:
    def chunks() -> Iterator[bytes]:
        yield b"ab"
        yield b"cd"
        pytest.fail("read beyond the size limit")

    with pytest.raises(AttachmentTooLargeError):
        read_bounded(chunks(), "https://example.test/file", 3)


def test_read_error_propagates() -> None:
    def chunks() -> Iterator[bytes]:
        yield b"ab"
        raise OSError("stream interrupted")

    with pytest.raises(OSError, match="stream interrupted"):
        read_bounded(chunks(), "https://example.test/file", None)


@pytest.mark.parametrize("client_type", [SejmApiClient, RclClient, OrkaClient])
@pytest.mark.parametrize("outcome", ["success", "limit", "error"])
def test_downloader_closes_stream(
    client_type: type[SejmApiClient] | type[RclClient] | type[OrkaClient], outcome: str
) -> None:
    closed: list[bool] = []

    class Stream(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield b"%PDF"
            if outcome == "error":
                raise OSError("stream interrupted")
            yield b" data"

        def close(self) -> None:
            closed.append(True)

    client = client_type(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Stream()))
    )
    try:
        if outcome == "success":
            assert client.download("https://example.test/file") == b"%PDF data"
        else:
            error = AttachmentTooLargeError if outcome == "limit" else OSError
            with pytest.raises(error):
                client.download("https://example.test/file", max_bytes=5)
        assert closed == [True]
    finally:
        client.close()
