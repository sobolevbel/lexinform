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


# Imperva's refusal from a home connection: HTTP 403, and the incident id is all it leaves.
REFUSAL = (
    b"<html><head><title>Request unsuccessful. Incapsula incident ID: "
    b"12-65176297-65176299</title></head><body>Incapsula</body></html>"
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


def test_the_request_is_shaped_like_a_browser_and_not_like_a_client_library() -> None:
    """The order and the casing are the identity as much as the name is.

    Measured on 2026-09-14: Imperva refused every request that named `Accept-Encoding` and
    `Connection` before `User-Agent` — httpx's own order, which no Chrome has — and served the
    same client on three cold addresses three times out of three once the browser's order was
    sent. httpx fills in those two headers only when the caller has not, so this holds as long
    as `BROWSER_HEADERS` names them itself.
    """
    sent: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append([name.decode() for name, _ in request.headers.raw])
        return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})

    _client(handler).download(URL)

    assert sent == [
        [
            "Host",
            "Connection",
            "Upgrade-Insecure-Requests",
            "User-Agent",
            "Accept",
            "Accept-Encoding",
            "Accept-Language",
        ]
    ]


def test_the_challenge_page_is_not_a_file_although_it_answers_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=CHALLENGE, headers={"content-type": "text/html"})

    with pytest.raises(OrkaUnreachableError, match="challenge"):
        _client(handler).download(URL)


def test_a_refusal_is_tried_again_because_it_is_about_the_moment() -> None:
    # Measured on 2026-09-14: three production runs were answered 403 while the same client from
    # ten other runners got the file 44 times out of 44 five minutes later.
    answers: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        answers.append(1)
        if len(answers) == 1:
            return httpx.Response(403, content=REFUSAL, headers={"content-type": "text/html"})
        return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})

    assert _client(handler).download(URL) == b"%PDF"
    assert len(answers) == 2


def test_a_refusal_that_stands_names_what_the_waf_called_it() -> None:
    """The incident id is the only thing a refusal leaves to ask about: without it a 403 is a
    fact with nothing behind it, and the last three were diagnosed by re-running the whole bot."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            content=REFUSAL,
            headers={"content-type": "text/html", "x-iinfo": "12-65176297-65176299 NNNN"},
        )

    with pytest.raises(OrkaUnreachableError) as refused:
        _client(handler).download(URL)

    assert "incident 12-65176297-65176299" in str(refused.value)
    assert "x-iinfo 12-65176297-65176299 NNNN" in str(refused.value)
    assert refused.value.status_code == 403 and not refused.value.file_is_missing


def test_the_f5_in_front_of_the_domino_server_is_named_too() -> None:
    # Two boxes stand in front of this host and either can be the one refusing us.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            content=b"<html><body>The requested URL was rejected. Your support ID is: 8765 </body></html>",
            headers={"content-type": "text/html"},
        )

    with pytest.raises(OrkaUnreachableError, match="support id 8765"):
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
