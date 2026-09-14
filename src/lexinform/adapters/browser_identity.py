"""The identity this bot presents to every Polish government site it reads: a browser's.

Two of the four hosts judge their clients. legislacja.rcl.gov.pl answers a client it dislikes
with "Request Rejected" under HTTP 200, and orka.sejm.gov.pl, behind Imperva, answers the name of
a tool ("curl/8.x") with 403 from a home address and with its challenge page — again HTTP 200,
text/html — from a GitHub runner. What those WAFs look at is the kind of client, not its version:
measured against both hosts on 2026-09-12, Chrome 128, Chrome 140 and Firefox 142 all get the
document. A version going stale is therefore not a thing that breaks; sending no browser is.

**The shape of the request is part of the identity, and the header order is the shape.** Measured
on 2026-09-14, when every orka request of four production runs was answered 403 while curl from
the same runners was served the file: it is not the address (ten runners, ten addresses, all
refused), not the protocol (HTTP/2 refused too), not the cookies or the redirect, and not the TLS
hello — a raw socket on Python's own `ssl`, sending curl's bytes, was let through. It is that
httpx puts its own defaults first, so the request said `Accept-Encoding` and `Connection` before
`User-Agent`, which no Chrome has ever done. Sent in the browser's own order and casing, the same
client on three cold addresses was served the file three times out of three.

So `BROWSER_HEADERS` is an **ordered** mapping and the order is the point: httpx only fills in
`Accept-Encoding` and `Connection` when the caller has not, and it keeps what it is given where
it is given. `browser_headers(accept=...)` replaces `Accept` in place, because that one header is
each client's own business (the API speaks JSON, the register is a CSV) and moving it would undo
the shape. `Accept-Encoding` names only what httpx can decode without another dependency; a
browser also offers `br`, and an answer we cannot read is worse than one byte of difference.

The other two hosts (api.sejm.gov.pl, www.gov.pl) have nothing in front of them and would take
any identity. They are told the same one all the same: one string to keep current, and no host
that starts scoring its callers can catch this bot out for the sake of a line of code.
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
