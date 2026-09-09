"""The Sejm API client against recorded responses: parsing, pagination, retries, downloads."""

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx2 as httpx
import pytest

from lexinform.adapters.sejm_api import SejmApiClient, SejmApiError
from lexinform.errors import AttachmentTooLargeError
from lexinform.models import ApplicantType, DocumentType, current_term
from tests.conftest import FIXTURES

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler, **options: Any) -> SejmApiClient:
    return SejmApiClient(
        "https://api.test", transport=httpx.MockTransport(handler), sleep=lambda s: None, **options
    )


def _fixture(name: str) -> httpx.Response:
    return httpx.Response(200, content=(FIXTURES / name).read_bytes())


def _by_path(routes: dict[str, str]) -> Handler:
    """Serve fixture files by the suffix of the request path; 404 otherwise."""

    def handler(request: httpx.Request) -> httpx.Response:
        for suffix, name in routes.items():
            if request.url.path.endswith(suffix):
                return _fixture(name)
        return httpx.Response(404)

    return handler


# --------------------------------------------------------------------------- processes


def test_processes_are_paginated_by_offset_with_filters_in_warsaw_time() -> None:
    page = json.loads((FIXTURES / "processes_page.json").read_text())
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        offset = int(params["offset"])
        return httpx.Response(200, json=page[offset : offset + 8])

    items = list(
        _client(handler, page_size=8).iter_processes(
            10, modified_since=datetime(2026, 9, 1, tzinfo=UTC), document_type="projekt ustawy"
        )
    )

    assert len(items) == len(page)
    assert [p["offset"] for p in seen] == ["0", "8", "16"]
    assert seen[0]["modifiedSince"] == "2026-09-01T02:00:00"  # 00:00 UTC is 02:00 CEST
    assert seen[0]["documentType"] == "projekt ustawy"
    assert items[0].document_type_enum is DocumentType.BILL


def test_pagination_stops_on_an_empty_page() -> None:
    page = json.loads((FIXTURES / "processes_page.json").read_text())[:8]

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        return httpx.Response(200, json=page if offset == 0 else [])

    assert len(list(_client(handler, page_size=8).iter_processes(10))) == 8


def test_pagination_stops_when_the_server_ignores_the_offset() -> None:
    page = json.loads((FIXTURES / "processes_page.json").read_text())[:8]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page)

    assert len(list(_client(handler, page_size=8).iter_processes(10))) == 8


def test_process_and_print_parse_from_fixtures() -> None:
    client = _client(
        _by_path({"/processes/3039": "process_3039.json", "/prints/3039": "print_3039.json"})
    )

    detail = client.get_process(10, "3039")
    info = client.get_print(10, "3039")

    assert detail.number == "3039"
    assert detail.stages[1].children[0].committee_code
    assert info.main_pdf is not None
    # Attachments are downloaded from the client's own host (a proxy or a mock included).
    assert info.main_pdf.url == "https://api.test/sejm/term10/prints/3039/3039.pdf"
    assert info.additional_prints[0].number == "3039-001"


def test_change_dates_are_converted_from_warsaw_time_and_ue_status_parsed() -> None:
    raw = json.loads((FIXTURES / "process_3039.json").read_text())
    naive = datetime.fromisoformat(raw["changeDate"])

    detail = _client(_by_path({"/processes/3039": "process_3039.json"})).get_process(10, "3039")

    assert detail.change_date.tzinfo is not None
    assert detail.change_date.astimezone(ZoneInfo("Europe/Warsaw")).replace(tzinfo=None) == naive
    assert detail.eu_related is (raw.get("UE", "NO") != "NO")


def test_process_carries_publication_fields_and_the_eli_act_is_parsed() -> None:
    client = _client(
        _by_path(
            {
                "/processes/2699": "process_2699.json",
                "/eli/acts/DU/2026/1099": "eli_act_DU_2026_1099.json",
            }
        )
    )

    detail = client.get_process(10, "2699")
    act = client.get_act("DU/2026/1099")
    missing = client.get_act("DU/2026/999999")

    assert (detail.eli, detail.display_address) == ("DU/2026/1099", "Dz.U. 2026 poz. 1099")
    assert detail.isap_url == "https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=WDU20260001099"
    assert detail.passed and detail.closure_date == date(2026, 7, 17)
    assert act is not None
    assert act.display_address == "Dz.U. 2026 poz. 1099" and act.title.startswith("Ustawa z dnia")
    assert act.act_date == date(2026, 7, 17)  # `announcementDate` is the date in the title
    assert act.promulgation_date == date(2026, 8, 18)  # the Dziennik Ustaw date
    assert (act.entry_into_force, act.in_force) == (date(2026, 11, 19), "IN_FORCE")
    assert act.text_pdf_url == "https://api.test/eli/acts/DU/2026/1099/text.pdf"
    assert act.isap_url and act.isap_url.endswith("WDU20260001099")
    assert missing is None  # not indexed (yet): no error


# --------------------------------------------------------------------------- bills


BILLS = [
    {
        "applicantType": "DEPUTIES",
        "consultationResults": False,
        "dateOfReceipt": "2026-08-31",
        "description": "odejścia od sztywnego ograniczenia",
        "euRelated": False,
        "number": "RPW/29075/2026",
        "publicConsultation": True,
        "publicConsultationEndDate": "2026-09-30",
        "publicConsultationStartDate": "2026-08-31",
        "status": "ACTIVE",
        "submissionType": "BILL",
        "term": 10,
        "title": "Poselski projekt ustawy o zmianie ustawy o udzielaniu cudzoziemcom ochrony",
    },
    {
        "applicantType": "GOVERNMENT",
        "dateOfReceipt": "2026-08-03",
        "number": "RPW/26666/2026",
        "print": "3039",
        "status": "WITHDRAWN",
        "withdrawnDate": "2026-09-01",
        "submissionType": "BILL",
        "term": 10,
        "title": "Rządowy projekt",
    },
]


def _bills_handler(seen: list[dict[str, str]]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        if params.get("print") == "3039":
            return httpx.Response(200, json=[BILLS[1]])
        return httpx.Response(200, json=BILLS if params.get("offset", "0") == "0" else [])

    return handler


def test_bills_endpoint_parses_submissions_with_and_without_a_print() -> None:
    seen: list[dict[str, str]] = []

    subs = list(_client(_bills_handler(seen)).iter_bills(10, received_from=date(2026, 9, 1)))

    assert (seen[0]["dateOfReceiptFrom"], seen[0]["limit"]) == ("2026-09-01", "500")
    assert [s.number for s in subs] == ["RPW/29075/2026", "RPW/26666/2026"]
    first, second = subs
    assert first.applicant is ApplicantType.DEPUTIES and first.print_number is None
    assert first.public_consultation and first.consultation_end == date(2026, 9, 30)
    assert first.pdf_url.endswith("/10-RPW-29075-2026/$file/10-RPW-29075-2026.pdf")
    assert second.print_number == "3039" and second.is_closed and second.withdrawn_date


def test_submission_lookup_by_print_number() -> None:
    client = _client(_bills_handler([]))

    found = client.find_submission(10, "3039")
    missing = client.find_submission(10, "0")

    assert found is not None and found.number == "RPW/26666/2026"
    assert missing is None


# --------------------------------------------------------------------------- sittings


def test_committee_sittings_and_sejm_sittings_are_parsed() -> None:
    client = _client(
        _by_path(
            {
                "/committees/ASW/sittings": "committee_sittings_ASW.json",
                "/proceedings/65": "proceeding_65.json",
                "/proceedings": "proceedings.json",
            }
        )
    )

    planned = [s for s in client.list_committee_sittings(10, "ASW") if s.status == "PLANNED"]
    listed = client.list_sittings(10)
    full = client.get_sitting(10, 65)

    assert [s.num for s in planned] == [135, 136]
    last = planned[-1]
    assert last.date == date(2026, 9, 17)
    assert last.start_time is not None and last.start_time.strftime("%H:%M") == "09:00"
    assert last.room is not None and last.room.startswith("sala im. Olgi Krzyżanowskiej")
    assert "druk nr 3035" in last.agenda
    assert last.video_url is not None and "transmisje_arch.xsp" in last.video_url
    assert [s.number for s in listed if s.number] == [64, 65]
    assert all(not s.agenda for s in listed)  # the listing carries no agenda
    assert next(s for s in listed if s.number == 0).first_date == date(2025, 4, 25)
    assert (full.first_date, full.last_date) == (date(2026, 9, 15), date(2026, 9, 18))
    assert "druki nr" in full.agenda
    with pytest.raises(SejmApiError):
        client.get_sitting(10, 66)


# --------------------------------------------------------------------------- transport


def test_server_errors_are_retried_then_the_request_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, content=b"%PDF")

    body = _client(handler).download("https://api.test/x.pdf")

    assert (body, calls["n"]) == (b"%PDF", 3)


def test_rate_limiting_is_retried_like_an_outage() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429) if calls["n"] == 1 else _fixture("print_3039.json")

    info = _client(handler).get_print(10, "3039")

    assert (info.number, calls["n"]) == ("3039", 2)


def test_client_errors_raise_without_a_retry() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    with pytest.raises(SejmApiError):
        _client(handler).get_process(10, "999999")
    assert calls["n"] == 1


def test_download_streams_and_stops_once_the_limit_is_exceeded() -> None:
    body = b"x" * 1000
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(200, content=body)

    client = _client(handler)

    within = client.download("https://api.test/a.pdf", max_bytes=1000)
    with pytest.raises(AttachmentTooLargeError):
        client.download("https://api.test/a.pdf", max_bytes=999)

    assert within == body
    assert methods == ["GET", "GET"]  # no HEAD: the Sejm API sends no Content-Length anyway


# --------------------------------------------------------------------------- terms


def test_terms_are_listed_with_the_running_one_flagged_current() -> None:
    client = _client(_by_path({"/sejm/term": "terms.json"}))

    terms = client.list_terms()

    assert [(t.num, t.current) for t in terms] == [(8, False), (9, False), (10, True)]
    assert (terms[1].start, terms[1].end) == (date(2019, 11, 12), date(2023, 11, 12))
    assert terms[2].end is None  # the running term has no end yet
    assert current_term(terms) == 10
