import json
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import httpx2 as httpx
import pytest

from lexinform.adapters.sejm_api import SejmApiClient, SejmApiError
from lexinform.errors import AttachmentTooLargeError
from lexinform.models import ApplicantType, DocumentType
from tests.conftest import FIXTURES


def _client(handler, **kw):  # type: ignore[no-untyped-def]
    return SejmApiClient(
        "https://api.test", transport=httpx.MockTransport(handler), sleep=lambda s: None, **kw
    )


def test_iter_processes_paginates_and_passes_filters() -> None:
    page = json.loads((FIXTURES / "processes_page.json").read_text())
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        offset = int(params["offset"])
        chunk = page[offset : offset + 8]
        return httpx.Response(200, json=chunk)

    client = _client(handler, page_size=8)
    items = list(
        client.iter_processes(
            10, modified_since=datetime(2026, 9, 1, tzinfo=UTC), document_type="projekt ustawy"
        )
    )
    assert len(items) == len(page)
    assert [p["offset"] for p in seen] == ["0", "8", "16"]
    # the API speaks naive Europe/Warsaw time: 00:00 UTC on 1 September is 02:00 CEST
    assert seen[0]["modifiedSince"] == "2026-09-01T02:00:00"
    assert seen[0]["documentType"] == "projekt ustawy"
    assert items[0].document_type_enum is DocumentType.BILL


def test_get_process_and_print_parse_fixture() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/processes/3039"):
            return httpx.Response(200, content=(FIXTURES / "process_3039.json").read_bytes())
        if request.url.path.endswith("/prints/3039"):
            return httpx.Response(200, content=(FIXTURES / "print_3039.json").read_bytes())
        return httpx.Response(404)

    client = _client(handler)
    detail = client.get_process(10, "3039")
    assert detail.number == "3039"
    assert detail.stages[1].children[0].committee_code
    info = client.get_print(10, "3039")
    assert info.main_pdf is not None
    assert info.main_pdf.url == "https://api.sejm.gov.pl/sejm/term10/prints/3039/3039.pdf"
    assert info.additional_prints[0].number == "3039-001"


def test_retries_on_5xx_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, content=b"%PDF", headers={"content-length": "4"})

    client = _client(handler)
    assert client.download("https://api.test/x.pdf") == b"%PDF"
    assert calls["n"] == 3


def test_4xx_raises_without_retry() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    with pytest.raises(SejmApiError):
        _client(handler).get_process(10, "999999")
    assert calls["n"] == 1


def test_download_stops_once_the_limit_is_exceeded() -> None:
    body = b"x" * 1000

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"  # no HEAD: the Sejm API sends no Content-Length anyway
        return httpx.Response(200, content=body)

    client = _client(handler)
    assert client.download("https://api.test/a.pdf", max_bytes=1000) == body
    with pytest.raises(AttachmentTooLargeError):
        client.download("https://api.test/a.pdf", max_bytes=999)


def test_pagination_stops_on_empty_page_and_on_repeated_page() -> None:
    page = json.loads((FIXTURES / "processes_page.json").read_text())[:8]

    def full_pages_then_empty(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        return httpx.Response(200, json=page if offset == 0 else [])

    assert len(list(_client(full_pages_then_empty, page_size=8).iter_processes(10))) == 8

    def ignores_offset(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page)

    # the server keeps returning the same page: yield it once, never loop forever
    assert len(list(_client(ignores_offset, page_size=8).iter_processes(10))) == 8


def test_429_is_retried_like_an_outage() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, content=(FIXTURES / "print_3039.json").read_bytes())

    assert _client(handler).get_print(10, "3039").number == "3039"
    assert calls["n"] == 2


def test_change_dates_are_converted_from_warsaw_time_and_ue_status_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=(FIXTURES / "process_3039.json").read_bytes())

    detail = _client(handler).get_process(10, "3039")
    raw = json.loads((FIXTURES / "process_3039.json").read_text())
    naive = datetime.fromisoformat(raw["changeDate"])
    assert detail.change_date.tzinfo is not None
    assert detail.change_date.replace(tzinfo=None) != naive  # shifted by the Warsaw offset
    assert detail.change_date.astimezone(ZoneInfo("Europe/Warsaw")).replace(tzinfo=None) == naive
    assert detail.eu_related is (raw.get("UE", "NO") != "NO")


def test_bills_endpoint_parses_pre_print_submissions_and_pages() -> None:
    items = [
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
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        if params.get("print") == "3039":
            return httpx.Response(200, json=[items[1]])
        return httpx.Response(200, json=items if params.get("offset", "0") == "0" else [])

    client = _client(handler)
    subs = list(client.iter_bills(10, received_from=datetime(2026, 9, 1, tzinfo=UTC).date()))
    assert seen[0]["dateOfReceiptFrom"] == "2026-09-01" and seen[0]["limit"] == "500"
    assert [s.number for s in subs] == ["RPW/29075/2026", "RPW/26666/2026"]
    first, second = subs
    assert first.applicant is ApplicantType.DEPUTIES and first.print_number is None
    assert first.consultation_end == date(2026, 9, 30) and first.public_consultation
    assert first.pdf_url.endswith("/10-RPW-29075-2026/$file/10-RPW-29075-2026.pdf")
    assert second.print_number == "3039" and second.is_closed and second.withdrawn_date
    found = client.find_submission(10, "3039")
    assert found is not None and found.number == "RPW/26666/2026"
    assert client.find_submission(10, "0") is None


def test_process_carries_publication_fields_and_eli_act_is_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/processes/2699"):
            return httpx.Response(200, content=(FIXTURES / "process_2699.json").read_bytes())
        if request.url.path == "/eli/acts/DU/2026/1099":
            return httpx.Response(
                200, content=(FIXTURES / "eli_act_DU_2026_1099.json").read_bytes()
            )
        return httpx.Response(404)

    client = _client(handler)
    detail = client.get_process(10, "2699")
    assert detail.eli == "DU/2026/1099" and detail.display_address == "Dz.U. 2026 poz. 1099"
    assert detail.isap_url == "https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=WDU20260001099"
    assert detail.passed and detail.closure_date == date(2026, 7, 17)

    act = client.get_act("DU/2026/1099")
    assert act is not None
    assert act.display_address == "Dz.U. 2026 poz. 1099" and act.title.startswith("Ustawa z dnia")
    assert act.act_date == date(2026, 7, 17)  # `announcementDate` is the date in the title
    assert act.promulgation_date == date(2026, 8, 18)  # the Dziennik Ustaw date
    assert act.entry_into_force == date(2026, 11, 19) and act.in_force == "IN_FORCE"
    assert act.text_pdf_url == "https://api.test/eli/acts/DU/2026/1099/text.pdf"
    assert act.isap_url and act.isap_url.endswith("WDU20260001099")
    assert client.get_act("DU/2026/999999") is None  # not indexed (yet): no error


def test_committee_sittings_and_proceedings_are_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/committees/ASW/sittings"):
            return httpx.Response(
                200, content=(FIXTURES / "committee_sittings_ASW.json").read_bytes()
            )
        if path.endswith("/proceedings"):
            return httpx.Response(200, content=(FIXTURES / "proceedings.json").read_bytes())
        if path.endswith("/proceedings/65"):
            return httpx.Response(200, content=(FIXTURES / "proceeding_65.json").read_bytes())
        return httpx.Response(404)

    client = _client(handler)
    sittings = client.list_committee_sittings(10, "ASW")
    planned = [s for s in sittings if s.status == "PLANNED"]
    assert [s.num for s in planned] == [135, 136]
    last = planned[-1]
    assert last.date == date(2026, 9, 17) and last.start_time is not None
    assert last.start_time.strftime("%H:%M") == "09:00"
    assert last.room is not None and last.room.startswith("sala im. Olgi Krzyżanowskiej")
    assert "druk nr 3035" in last.agenda
    assert last.video_url is not None and "transmisje_arch.xsp" in last.video_url
    listed = client.list_sittings(10)
    assert [s.number for s in listed if s.number] == [64, 65]
    assert all(not s.agenda for s in listed)  # the listing carries no agenda
    planned_only = [s for s in listed if s.number == 0]
    assert planned_only and planned_only[0].first_date == date(2025, 4, 25)
    full = client.get_sitting(10, 65)
    assert full.first_date == date(2026, 9, 15) and full.last_date == date(2026, 9, 18)
    assert "druki nr" in full.agenda
    with pytest.raises(SejmApiError):
        client.get_sitting(10, 66)
