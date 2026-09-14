"""The three hosts outside api.sejm.gov.pl that this project has been wrong about before.

Each of them was settled once by a bespoke probe workflow and then left unwatched:

- orka.sejm.gov.pl sits behind Imperva and was believed undownloadable until 2026-09-12, when it
  turned out to answer a client that keeps the cookies of its own 302. That belief cost the
  project the text of every bill without a print number.
- the register on gov.pl is one CSV whose id lives in the page, and whose column headings get
  repunctuated between exports.
- the Sejm's `/prints` listing is not authoritative about a print's attachments; the print's own
  detail is. Three of 3,282 prints of term 10 name files in the listing that all answer 404.

legislacja.rcl.gov.pl is deliberately absent: a GitHub-hosted runner cannot open a TCP connection
to it at all (verified 2026-09-09), so a live check for it here would fail for the wrong reason.
"""

from datetime import UTC, datetime, timedelta

import pytest

from lexinform.adapters.orka import OrkaClient
from lexinform.adapters.sejm_api import SejmApiClient
from lexinform.adapters.wykaz_csv import WykazClient
from lexinform.models import BILL_KIND, submission_pdf_url

pytestmark = pytest.mark.integration

TERM = 10


@pytest.fixture(scope="module")
def sejm() -> SejmApiClient:
    return SejmApiClient()


def test_a_bill_without_a_print_number_still_has_a_downloadable_file(
    sejm: SejmApiClient,
) -> None:
    """The WAF refuses a client that names a bot and one that drops its cookies, and it answers
    the refusal with HTTP 200 and an HTML challenge page — so what proves the download worked is
    that the bytes are a PDF, never the status."""
    recent = [
        s
        for s in sejm.iter_bills(
            TERM, received_from=(datetime.now(UTC) - timedelta(days=120)).date()
        )
        if s.print_number is None
    ]
    if not recent:
        pytest.skip("no bill of the last 120 days is still without a print number")

    data = OrkaClient().download(submission_pdf_url(TERM, recent[-1].number), max_bytes=8_000_000)

    assert data.startswith(b"%PDF"), recent[-1].number


def test_the_print_detail_names_a_file_that_downloads(sejm: SejmApiClient) -> None:
    """Druk 599's listing names `599.pdf`, which is gone; its detail names `599-s.pdf`, which is
    there. A run that builds the URL from the listing loses the print whole, and the only way to
    notice is to ask the detail when the listing's files all fail."""
    info = sejm.get_print(TERM, "599")

    assert info.main_pdf is not None
    assert sejm.download(info.main_pdf.url, max_bytes=8_000_000).startswith(b"%PDF")


def test_the_register_is_one_csv_whose_columns_are_still_recognisable() -> None:
    """The id is read out of the page and the columns are matched by the longest prefix, because
    their statutory wording gets repunctuated between exports. A heading that stops matching
    turns the whole register into zero bill entries, silently."""
    client = WykazClient()
    try:
        entries = client.entries()
    finally:
        client.close()

    assert len(entries) > 1_000
    bills = [e for e in entries if e.kind == BILL_KIND]
    assert len(bills) > 500
    newest = max(bills, key=lambda e: e.published_at)
    assert newest.title and newest.organ  # the two columns every card is built from
