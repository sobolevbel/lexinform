"""Where `FakeSejmGateway` has to work something out rather than look it up, it must work it out
the way `SejmApiClient` does.

Most of the fake is a dictionary: `get_process` returns what a test put there, and nothing can
drift. `find_process_by_rcl_num` is the exception — `rclNum` is in a process's detail and never in
the listing, so the real client walks the listing, narrows by the hand-over date and reads details
one by one, and the fake has to answer the same questions about the same rows. Three hundred
scenario tests rest on it agreeing.

Each case is stated once, as the JSON the API would serve, and run through both.
"""

import datetime as dt
from collections.abc import Callable, Iterator
from typing import Any

import httpx2 as httpx
import pytest

from lexinform.adapters.sejm_api import SejmApiClient, parse_process_summary
from lexinform.ports import SejmGateway
from tests.fakes import FakeSejmGateway

HANDOVER = dt.date(2026, 1, 20)

LISTING: list[dict[str, Any]] = [
    {
        "term": 10,
        "number": "2100",
        "title": "a bill of last summer",
        "changeDate": "2025-06-01T10:00:00",
        "documentDate": "2025-06-01",
        "rclNum": "RM-0610-7-26",
    },
    {
        "term": 10,
        "number": "2171",
        "title": "another bill of the same week",
        "changeDate": "2026-01-19T10:00:00",
        "documentDate": "2026-01-19",
    },
    {
        "term": 10,
        "number": "2172",
        "title": "the print this project became",
        "changeDate": "2026-01-22T10:00:00",
        "documentDate": "2026-01-22",
        "rclNum": "RM-0610-7-26",
    },
    {  # an entry the listing has not dated: the change date is what places it in the window
        "term": 10,
        "number": "2180",
        "title": "an undated row",
        "changeDate": "2026-02-01T10:00:00",
        "rclNum": "RM-0610-99-26",
    },
]


def _real() -> Iterator[SejmGateway]:
    """The client over a transport that serves LISTING, with `rclNum` only in the details."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/processes"):
            offset = int(request.url.params.get("offset", 0))
            listed = [{k: v for k, v in row.items() if k != "rclNum"} for row in LISTING]
            return httpx.Response(200, json=listed if offset == 0 else [])
        number = request.url.path.rsplit("/", 1)[-1]
        row = next(r for r in LISTING if r["number"] == number)
        return httpx.Response(200, json={**row, "stages": []})

    client = SejmApiClient(
        "https://api.test", transport=httpx.MockTransport(handler), sleep=lambda _: None
    )
    yield client
    client.close()


def _fake() -> Iterator[SejmGateway]:
    gateway = FakeSejmGateway()
    gateway.processes = [parse_process_summary(row) for row in LISTING]
    yield gateway


@pytest.fixture(params=[_real, _fake], ids=["client", "fake"])
def gateway(request: pytest.FixtureRequest) -> Iterator[SejmGateway]:
    build: Callable[[], Iterator[SejmGateway]] = request.param
    yield from build()


@pytest.mark.parametrize(
    "written", ["RM-0610-7-26", "rm-0610-7-26", " RM-0610-7-26 ", "RM-0610-7-26\n"]
)
def test_the_rm_number_is_matched_however_it_is_written(gateway: SejmGateway, written: str) -> None:
    """The number reaches us from an RCL page, where it is whatever the ministry typed into the
    field, and is compared against what the API holds."""
    found = gateway.find_process_by_rcl_num(10, written, since=HANDOVER)

    assert found is not None and found.number == "2172"


def test_a_print_filed_before_the_handover_is_not_the_one(gateway: SejmGateway) -> None:
    """Druk 2100 carries the same number and is seven months too old; a print follows the
    hand-over within days, and the window is that week less a little slack."""
    found = gateway.find_process_by_rcl_num(10, "RM-0610-7-26", since=dt.date(2026, 6, 1))

    assert found is None


def test_a_row_the_listing_never_dated_is_placed_by_when_it_last_changed(
    gateway: SejmGateway,
) -> None:
    """`documentDate` is missing on some rows. Falling back to the hand-over date itself would
    keep every such row in every window; the change date at least says when the API last saw it."""
    inside = gateway.find_process_by_rcl_num(10, "RM-0610-99-26", since=HANDOVER)
    outside = gateway.find_process_by_rcl_num(10, "RM-0610-99-26", since=dt.date(2026, 3, 1))

    assert inside is not None and inside.number == "2180"
    assert outside is None


def test_a_number_on_no_print_is_not_found(gateway: SejmGateway) -> None:
    assert gateway.find_process_by_rcl_num(10, "RM-0610-1-26", since=HANDOVER) is None


def test_an_empty_number_is_not_matched_against_the_rows_that_have_none(
    gateway: SejmGateway,
) -> None:
    """Druk 2171 has no `rclNum`; normalising both sides makes "" equal "", so without a guard
    the first undated print in the window would come back as every project's print."""
    assert gateway.find_process_by_rcl_num(10, "", since=HANDOVER) is None
