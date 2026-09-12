"""The identity this bot presents to every Polish government site it reads: a browser's.

Two of the four hosts judge their clients. legislacja.rcl.gov.pl answers a client it dislikes
with "Request Rejected" under HTTP 200, and orka.sejm.gov.pl, behind Imperva, answers the name of
a tool ("curl/8.x") with 403 from a home address and with its challenge page — again HTTP 200,
text/html — from a GitHub runner. What those WAFs look at is the kind of client, not its version:
measured against both hosts on 2026-09-12, Chrome 128, Chrome 140 and Firefox 142 all get the
document. A version going stale is therefore not a thing that breaks; sending no browser is.

The other two (api.sejm.gov.pl, www.gov.pl) have nothing in front of them and would take any
identity. They are told the same one all the same: one string to keep current, and no host that
starts scoring its callers can catch this bot out for the sake of a line of code. `Accept` is
each client's own business — the API speaks JSON and the register is a CSV.
"""

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/140.0.0.0 Safari/537.36"
)

BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9",
    "User-Agent": BROWSER_USER_AGENT,
}
