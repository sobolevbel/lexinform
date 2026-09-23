import datetime as dt
from collections.abc import Callable

import httpx2 as httpx
import pytest

from lexinform.adapters.senat_html import (
    SenateClient,
    SenatePageError,
    parse_act,
    parse_committee,
    parse_listing,
)
from lexinform.errors import SenateUnavailableError
from lexinform.models import SenateCommittee, senate_title_matches
from tests.conftest import SENAT_FIXTURES

Handler = Callable[[httpx.Request], httpx.Response]

JAPAN = (
    "o ratyfikacji Umowy między Rzecząpospolitą Polską a Japonią o zabezpieczeniu społecznym,"
    " podpisanej w Tokio dnia 15 kwietnia 2026 r."
)
LISTING = "/prace/proces-legislacyjny-w-senacie/ustawy-uchwalone-przez-sejm/"
ACT_PATH = f"{LISTING}ustawy-uchwalone-przez-sejm/ustawa,2169.html"


def _fixture(name: str) -> str:
    return (SENAT_FIXTURES / name).read_text(encoding="utf-8")


def _site(pages: dict[str, str], requested: list[str] | None = None) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if requested is not None:
            requested.append(request.url.path)
        text = pages.get(request.url.path)
        return httpx.Response(404) if text is None else httpx.Response(200, text=text)

    return handler


def _client(handler: Handler) -> SenateClient:
    return SenateClient(
        "https://senat.test", transport=httpx.MockTransport(handler), sleep=lambda s: None
    )


def _pages(act: str | None = None) -> dict[str, str]:
    return {
        LISTING: _fixture("lista.html"),
        ACT_PATH: act or _fixture("ustawa_2169.html"),
        "/prace/komisje-senackie/komisja,233.html": _fixture("komisja_233.html"),
        "/prace/komisje-senackie/komisja,231.html": _fixture("komisja_231.html"),
    }


def test_the_act_page_gives_the_print_the_committees_and_their_sitting() -> None:
    act = parse_act(_fixture("ustawa_2169.html"), "https://www.senat.gov.pl/x")

    assert (act.print_number, act.received) == ("849", dt.date(2026, 9, 18))
    assert [(c.id, c.name) for c in act.committees] == [
        (233, "Komisja Rodziny, Polityki Senioralnej i Społecznej"),
        (231, "Komisja Spraw Zagranicznych"),
    ]
    assert act.committee_sittings == (dt.date(2026, 9, 23),)


def test_a_committee_email_is_read_behind_either_obfuscation() -> None:
    blank = SenateCommittee(id=0, name="?")

    cloudflare = parse_committee(_fixture("komisja_233.html"), blank)
    script = parse_committee(_fixture("komisja_231.html"), blank)

    assert (cloudflare.name, cloudflare.email) == (
        "Komisja Rodziny, Polityki Senioralnej i Społecznej",
        "krpss@senat.gov.pl",
    )
    assert (script.name, script.email) == ("Komisja Spraw Zagranicznych", "ksz@senat.gov.pl")


def test_the_listing_gives_ten_acts_newest_first() -> None:
    rows = parse_listing(_fixture("lista.html"))

    assert len(rows) == 10
    assert rows[0][1].endswith("/ustawa,2169.html")
    assert senate_title_matches(rows[0][0], JAPAN)


def test_a_page_without_its_structure_is_an_error_not_an_empty_act() -> None:
    with pytest.raises(SenatePageError):
        parse_listing("<html><body>przebudowa</body></html>")
    with pytest.raises(SenatePageError):
        parse_act("<html><body>przebudowa</body></html>", "https://www.senat.gov.pl/x")


def test_the_act_is_found_by_its_final_title_with_every_committee_email() -> None:
    act = _client(_site(_pages())).find_act(JAPAN, passed_on=dt.date(2026, 9, 18))

    assert act is not None
    assert act.url == f"https://www.senat.gov.pl{ACT_PATH}"
    assert [c.email for c in act.committees] == ["krpss@senat.gov.pl", "ksz@senat.gov.pl"]


def test_an_act_of_that_title_received_before_the_vote_is_another_act() -> None:
    requested: list[str] = []

    act = _client(_site(_pages(), requested)).find_act(JAPAN, passed_on=dt.date(2026, 9, 19))

    assert act is None
    assert ACT_PATH in requested  # read, and turned down on its date


def test_titles_are_compared_across_the_dashes_and_prefixes_the_two_houses_use() -> None:
    assert senate_title_matches(
        "Ustawa o zmianie ustawy – Prawo wodne", "o zmianie ustawy - Prawo wodne"
    )
    assert senate_title_matches(
        "Ustawa – Przepisy wprowadzające ustawę o statusie osoby najbliższej",
        "Przepisy wprowadzające ustawę o statusie osoby najbliższej",
    )
    assert senate_title_matches(
        "Ustawa o zmianie ustawy o samorządach",
        "Senacki projekt ustawy o zmianie ustawy o samorządach",
    )
    assert not senate_title_matches(
        "Ustawa o zmianie ustawy – Prawo wodne", "o zmianie ustawy - Prawo oświatowe"
    )


def test_an_unreachable_site_is_an_outage() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(SenateUnavailableError):
        _client(down).find_act(JAPAN, passed_on=dt.date(2026, 9, 18))
