"""The identity this bot presents to every Polish government site it reads: a browser's.

legislacja.rcl.gov.pl and orka.sejm.gov.pl judge their clients, and both score the *kind* of
client and not its version: Chrome 128, Chrome 140 and Firefox 142 all get the document. A stale
version is not what breaks; sending no browser is.

**The header order is part of the identity.** Four production runs were answered 403 while curl
from the same runners was served the file, and it was neither the address, the protocol, the
cookies nor the TLS hello: httpx wrote its own defaults first, so the request said
`Accept-Encoding` and `Connection` before `User-Agent`, which no Chrome does. So `BROWSER_HEADERS`
is an **ordered** mapping, and `browser_headers(accept=…)` replaces that one header in place
rather than moving it. `Accept-Encoding` names only what httpx can decode unaided.

api.sejm.gov.pl and www.gov.pl would take any identity and are told the same one anyway.
"""

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/140.0.0.0 Safari/537.36"
)

# Chrome's own order for a top-level GET over HTTP/1.1, `Host` aside, which the client writes.
BROWSER_HEADERS = {
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": BROWSER_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "pl-PL,pl;q=0.9",
}


def browser_headers(*, accept: str) -> dict[str, str]:
    """The browser's headers with `Accept` saying what this client asks for, in its own place."""
    return {key: accept if key == "Accept" else value for key, value in BROWSER_HEADERS.items()}
