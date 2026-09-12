"""Downloads from orka.sejm.gov.pl: the file of a bill the Sejm has received but not numbered.

Its own client rather than the Sejm API's, because it is its own site: Lotus Domino behind
Imperva, built for people with browsers. Three things follow, and all three were measured on
2026-09-12 (`.github/workflows/orka-probe.yml`):

- it is told a browser's identity (`browser_identity`), the one legislacja.rcl.gov.pl is told;
- it is given a cookie jar and follows redirects, because the first answer is a 302 to the same
  URL that sets `visid_incap_*` / `incap_ses_*`, and a client that keeps neither loops until it
  gives up — which is what "the file is not downloadable" turned out to mean all along;
- every failure is an `OrkaUnreachableError`, a per-bill problem: this host judges callers by
  address as well as identity, and everything else the analysis reads comes from api.sejm.gov.pl.
"""

import logging
import time
from collections.abc import Callable

import httpx2 as httpx

from lexinform.adapters.browser_identity import BROWSER_HEADERS
from lexinform.errors import AttachmentTooLargeError, OrkaUnreachableError

__all__ = ["OrkaClient"]

log = logging.getLogger(__name__)

_CHALLENGE_MARKERS = (b"incapsula", b"<title>loading</title>", b"request unsuccessful")


class OrkaClient:
    """One `download(url, max_bytes=...)`, which is all this host is asked for."""

    def __init__(
        self,
        *,
        timeout: float = 60.0,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = httpx.Client(
            timeout=timeout,
            headers=BROWSER_HEADERS,
            transport=transport,
            follow_redirects=True,
        )
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        self._sleep = sleep

    def download(self, url: str, *, max_bytes: int | None = None) -> bytes:
        """The file, streamed, stopping as soon as `max_bytes` is exceeded.

        A challenge page is not a file, and it arrives with HTTP 200 and `text/html`, so the
        answer is judged by what it is rather than by its status.
        """
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._client.send(self._client.build_request("GET", url), stream=True)
            except httpx.TransportError as exc:
                if attempt > self._max_retries:
                    raise OrkaUnreachableError(
                        f"GET {url} failed after {attempt} attempt(s): {exc}"
                    ) from exc
                self._wait(attempt, f"GET {url}: {exc}")
                continue
            try:
                if response.status_code >= 500 or response.status_code == 429:
                    if attempt <= self._max_retries:
                        self._wait(attempt, f"GET {url}: HTTP {response.status_code}")
                        continue
                    raise OrkaUnreachableError(
                        f"GET {url}: HTTP {response.status_code} after {attempt} attempts"
                    )
                if response.status_code >= 400:
                    raise OrkaUnreachableError(f"GET {url}: HTTP {response.status_code}")
                return self._body(url, response, max_bytes)
            finally:
                response.close()

    def close(self) -> None:
        self._client.close()

    def _body(self, url: str, response: httpx.Response, max_bytes: int | None) -> bytes:
        chunks: list[bytes] = []
        received = 0
        for chunk in response.iter_bytes():
            received += len(chunk)
            if max_bytes is not None and received > max_bytes:
                raise AttachmentTooLargeError(url, max_bytes)
            chunks.append(chunk)
        body = b"".join(chunks)
        if _is_challenge(response, body):
            raise OrkaUnreachableError(f"GET {url}: the WAF answered with its challenge page")
        return body

    def _wait(self, attempt: int, reason: str) -> None:
        delay = self._backoff * (2 ** (attempt - 1))
        log.warning("orka retry %d in %.1fs (%s)", attempt, delay, reason)
        self._sleep(delay)


def _is_challenge(response: httpx.Response, body: bytes) -> bool:
    """Whether what came back is Imperva's page instead of the document.

    The files are PDFs; anything served as HTML is the WAF talking, and the markers tell its
    pages apart from a Domino error page, which is a plain 404 and never a 200.
    """
    if "html" not in response.headers.get("content-type", "").lower():
        return False
    head = body[:4096].lower()
    return any(marker in head for marker in _CHALLENGE_MARKERS)
