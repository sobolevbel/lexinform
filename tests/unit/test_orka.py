"""The orka.sejm.gov.pl client: the cookie dance, the browser identity, the WAF's own answers."""

from collections.abc import Callable

import httpx2 as httpx
import pytest

from lexinform.adapters.browser_identity import BROWSER_USER_AGENT
from lexinform.adapters.orka import OrkaClient
from lexinform.errors import AttachmentTooLargeError, OrkaUnreachableError

Handler = Callable[[httpx.Request], httpx.Response]

URL = "https://orka.test/Druki10ka.nsf/Projekty/10-RPW-29075-2026/$file/10-RPW-29075-2026.pdf"

# Imperva's challenge, as measured from a GitHub runner on 2026-09-12: HTTP 200, text/html.
CHALLENGE = (
    b'<html style="height:100%"><head><META NAME="ROBOTS" CONTENT="NOINDEX, NOFOLLOW">'
    b'<script type="text/javascript" src="/_Incapsula_Resource?SWJIYLWA=719d34d31c8e3a6e6fffd425f7e032f3">'
    b"</script></head><body></body></html>"
)


def _client(handler: Handler) -> OrkaClient:
    return OrkaClient(transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_the_302_that_sets_the_cookies_is_followed_and_the_file_comes_back() -> None:
    # The first answer redirects to the same URL and sets visid_incap_*/incap_ses_*; a client
    # that follows neither loops for ever, which is what "the file is not downloadable" meant.
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("cookie", ""))
        if len(seen) == 1:
            return httpx.Response(
                302,
                headers={
                    "location": str(request.url),
                    "set-cookie": "visid_incap_3078757=2VJn7Rh; path=/",
                },
                content=b"<HTML><HEAD><TITLE>Loading</TITLE></HEAD></HTML>",
            )
        return httpx.Response(
            200, content=b"%PDF-1.7 ...", headers={"content-type": "application/pdf"}
        )

    assert _client(handler).download(URL) == b"%PDF-1.7 ..."
    assert "visid_incap_3078757" in seen[1]


def test_the_request_says_it_is_a_browser() -> None:
    agents: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        agents.append(request.headers.get("user-agent", ""))
        return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})

    _client(handler).download(URL)

    assert agents == [BROWSER_USER_AGENT]
    assert "curl" not in agents[0].lower()


def test_the_challenge_page_is_not_a_file_although_it_answers_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=CHALLENGE, headers={"content-type": "text/html"})

    with pytest.raises(OrkaUnreachableError, match="challenge"):
        _client(handler).download(URL)


def test_a_missing_file_is_this_host_failing_and_not_a_phase_ending_outage() -> None:
    client = _client(lambda request: httpx.Response(404, content=b"not found"))

    with pytest.raises(OrkaUnreachableError, match="404"):
        client.download(URL)


def test_a_server_error_is_retried_and_then_given_up_on() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(503)

    with pytest.raises(OrkaUnreachableError, match="after 3 attempts"):
        _client(handler).download(URL)
    assert len(attempts) == 3


def test_a_file_over_the_limit_stops_mid_stream() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"%PDF" + b"x" * 5000, headers={"content-type": "application/pdf"}
        )

    with pytest.raises(AttachmentTooLargeError):
        _client(handler).download(URL, max_bytes=100)
