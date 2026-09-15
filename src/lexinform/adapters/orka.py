"""Downloads from orka.sejm.gov.pl: the file of a bill the Sejm has received but not numbered.

Its own client rather than the Sejm API's, because it is its own site: Lotus Domino behind
Imperva, built for people with browsers. Three things follow (`.github/workflows/orka-probe.yml`):

- it is told a browser's identity, header order included (`browser_identity`);
- it is given a cookie jar and follows redirects: the first answer is a 302 to the same URL that
  sets `visid_incap_*` / `incap_ses_*`, and a client keeping neither loops until it gives up;
- every failure is an `OrkaUnreachableError`, a per-bill problem: this host judges callers by
  address as well as identity, and everything else the analysis reads is api.sejm.gov.pl.

A refusal is retried, a WAF decision being momentary as well as structural, and carries the WAF's
own identifiers, a 403 with nothing to quote costing a session to diagnose. Only a 404 is about
the bill: the address is built by convention and can be wrong.
"""

import logging
import re
import time
from collections.abc import Callable

import httpx2 as httpx

from lexinform.adapters.browser_identity import BROWSER_HEADERS
from lexinform.errors import AttachmentTooLargeError, OrkaUnreachableError

__all__ = ["OrkaClient"]

log = logging.getLogger(__name__)

_CHALLENGE_MARKERS = (b"incapsula", b"<title>loading</title>", b"request unsuccessful")
# Imperva names its refusal "Incapsula incident ID: 12-65176297-65176299"; the F5 in front of
# the Domino server names its own "Your support ID is: 1234567890".
_INCIDENT_ID = re.compile(rb"incident id:?\s*([0-9a-z\-]+)", re.IGNORECASE)
_SUPPORT_ID = re.compile(rb"support id is:?\s*([0-9]+)", re.IGNORECASE)


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
        answer is judged by what it is rather than by its status. Every refusal but a 404 is
        tried again: the same address is served the file one minute and refused the next.
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
            status = response.status_code
            try:
                if status == 404:
                    raise OrkaUnreachableError(f"GET {url}: HTTP 404", status_code=404)
                if status >= 400:
                    refusal = f"HTTP {status} ({_waf_marks(response)})"
                else:
                    body = self._body(url, response, max_bytes)
                    if not _is_challenge(response, body):
                        return body
                    marks = _waf_marks(response, body)
                    refusal = f"the WAF answered with its challenge page ({marks})"
            finally:
                response.close()
            if attempt <= self._max_retries:
                self._wait(attempt, f"GET {url}: {refusal}")
                continue
            raise OrkaUnreachableError(
                f"GET {url}: {refusal} after {attempt} attempts", status_code=status
            )

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
        return b"".join(chunks)

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


def _waf_marks(response: httpx.Response, body: bytes | None = None) -> str:
    """What the refusal says about itself, for the log and for the bill's `last_error`.

    Two boxes stand in front of this host and either can be the one refusing us; each stamps its
    answer with an identifier its operator can look up, and `x-iinfo` is Imperva's even when the
    page carries no incident id. Without them a 403 is a fact with nothing to ask about it.
    """
    if body is None:
        try:
            body = response.read()
        except Exception:  # the refusal is the news; failing to read its body is not
            body = b""
    marks = [
        f"{label} {match.group(1).decode('ascii', 'replace')}"
        for pattern, label in ((_INCIDENT_ID, "incident"), (_SUPPORT_ID, "support id"))
        if (match := pattern.search(body[:8192])) is not None
    ]
    iinfo = response.headers.get("x-iinfo")
    if iinfo:
        marks.append(f"x-iinfo {iinfo}")
    return ", ".join(marks) or "the answer names no incident"
