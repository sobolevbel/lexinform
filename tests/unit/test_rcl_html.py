"""The RCL scraper against recorded pages: parsing, the modified-since walk, WAF, redirects."""

from collections.abc import Callable
from datetime import date

import httpx2 as httpx
import pytest

from lexinform.adapters.rcl_html import (
    RclClient,
    RclPageError,
    parse_list,
    parse_project,
    parse_stage_catalog,
)
from lexinform.errors import AttachmentTooLargeError, RclUnavailableError
from tests.conftest import RCL_FIXTURES

Handler = Callable[[httpx.Request], httpx.Response]

REJECTED = "<html><head><title>Request Rejected</title></head><body>Żądanie…</body></html>"


def _page(name: str) -> str:
    return (RCL_FIXTURES / name).read_text(encoding="utf-8")


def _client(handler: Handler, *, page_size: int = 100, max_retries: int = 3) -> RclClient:
    return RclClient(
        "https://rcl.test",
        page_size=page_size,
        max_retries=max_retries,
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )


# --------------------------------------------------------------------------- list


def test_list_rows_carry_id_title_applicant_wykaz_number_and_dates() -> None:
    rows = parse_list(_page("lista.html"))

    assert [r.id for r in rows] == [12411600, 12413507, 12409656]
    first = rows[0]
    assert first.title.startswith("Projekt ustawy o zmianie niektórych ustaw w celu rozwoju")
    assert (first.applicant, first.wykaz_number) == ("Minister Finansów", "UC132")
    assert (first.created, first.modified) == (date(2026, 6, 22), date(2026, 9, 8))
    assert rows[2].wykaz_number == "UD247"  # "UD 247" on the page
    assert first.web_url == "https://legislacja.rcl.gov.pl/projekt/12411600"


def test_listing_walks_pages_newest_first_and_stops_at_the_first_older_row() -> None:
    older = _page("lista.html").replace("08-09-2026", "01-09-2026")
    pages = {"1": _page("lista.html"), "2": older}
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        return httpx.Response(200, text=pages[params["pNumber"]])

    rows = list(_client(handler, page_size=3).list_projects(modified_since=date(2026, 9, 5)))

    assert [r.id for r in rows] == [12411600, 12413507, 12409656]
    assert [p["pNumber"] for p in seen] == ["1", "2"]
    assert seen[0]["sKey"] == "modifiedDate" and seen[0]["sOrder"] == "desc"
    assert seen[0]["typeId"] == "2" and seen[0]["pSize"] == "3"


def test_listing_stops_when_a_page_is_shorter_than_the_page_size() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text=_page("lista.html"))

    rows = list(_client(handler, page_size=100).list_projects(modified_since=date(2026, 1, 1)))

    assert (len(rows), calls) == (3, 1)


# --------------------------------------------------------------------------- project page


def test_project_page_gives_metadata_and_the_timeline() -> None:
    project = parse_project(_page("projekt_12414100.html"), 12414100)

    assert project.title.startswith("Projekt ustawy o zmianie ustawy o udziale Rzeczypospolitej")
    assert project.applicant == "Minister Spraw Wewnętrznych i Administracji"
    assert (project.created, project.modified) == (date(2026, 8, 31), date(2026, 9, 1))
    assert project.departments == ("sprawy wewnętrzne",)
    assert project.keywords == ("SYSTEM INFORMACYJNY SCHENGEN ORAZ WIZOWY SYSTEM INFORMACYJNY",)
    assert (project.status, project.is_open, project.term_label) == ("otwarty", True, "X")
    assert (project.wykaz_number, project.wykaz_url) == (
        "UC164",
        "https://www.gov.pl/web/premier/wplip-rm",
    )
    assert project.eu_note is not None and "2022/1190" in project.eu_note
    assert project.comment_url == "https://legislacja.rcl.gov.pl/projekt/12414100/komentarz"
    assert len(project.stages) == 14
    assert [(st.number, st.state) for st in project.reached_stages] == [
        (2, "reached"),
        (3, "reached"),
        (4, "active"),
    ]
    consultation = project.consultation_stage
    assert consultation is not None
    assert (consultation.id, consultation.name) == (13223895, "Konsultacje publiczne")
    assert consultation.modified == date(2026, 9, 1)
    assert project.current_stage is not None and project.current_stage.number == 4
    assert not project.sent_to_sejm


def test_project_sent_to_the_sejm_exposes_the_rm_number() -> None:
    project = parse_project(_page("projekt_12414050.html"), 12414050)

    assert project.sent_to_sejm
    assert project.rm_number == "RM-0610-139-26"
    assert project.sejm_url is not None and "symbol=RPL" in project.sejm_url
    assert [(st.number, st.state) for st in project.reached_stages] == [
        (12, "reached"),
        (14, "active"),
    ]
    assert project.modified == date(2026, 9, 2)


def test_stage_start_and_end_dates_are_read_when_present() -> None:
    project = parse_project(_page("projekt_12408900.html"), 12408900)

    uzgodnienia = project.stages[1]
    assert (uzgodnienia.started, uzgodnienia.ended) == (date(2026, 5, 18), date(2026, 5, 18))
    assert project.stages[0].started is None


def test_a_page_without_the_project_block_is_a_page_error() -> None:
    with pytest.raises(RclPageError):
        parse_project("<html><head><title>Projekty</title></head><body></body></html>", 1)


def test_a_project_page_whose_timeline_vanished_is_a_page_error() -> None:
    page = _page("projekt_12414100.html").replace("cbp_tmtimeline", "cbp_renamed")

    with pytest.raises(RclPageError, match="markup changed"):
        parse_project(page, 12414100)


def test_a_stage_node_without_a_label_is_skipped_but_not_all_of_them() -> None:
    page = _page("projekt_12414100.html")
    one_broken = page.replace(" 1. Zgłoszenia lobbingowe", "Zgłoszenia lobbingowe", 1)
    all_broken = page.replace("cbp_tmlabel", "cbp_other")

    project = parse_project(one_broken, 12414100)

    assert len(project.stages) == 13
    with pytest.raises(RclPageError, match="no stage node"):
        parse_project(all_broken, 12414100)


def test_a_list_page_that_announces_rows_but_shows_none_is_a_page_error() -> None:
    page = _page("lista.html").replace('id="table"', 'id="renamed"')

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=page)

    with pytest.raises(RclPageError, match="2619 projects but no rows"):
        list(_client(handler).list_projects(modified_since=date(2026, 9, 1)))


# --------------------------------------------------------------------------- stage catalog


def test_stage_catalog_lists_folders_and_documents() -> None:
    stage = parse_stage_catalog(_page("katalog_13223895.html"), 13223895)

    assert (stage.number, stage.name, stage.state) == (3, "Konsultacje publiczne", "reached")
    assert [f.kind for f in stage.folders] == [
        "project",
        "letters",
        "positions",
        "response",
        "conference",
    ]
    project_folder = stage.folder("project")
    assert project_folder is not None
    assert (project_folder.id, project_folder.name, project_folder.modified) == (
        13223896,
        "Projekt",
        date(2026, 9, 1),
    )
    assert [d.name for d in project_folder.documents] == [
        "projekt_ustawy_o_udziale_RP_w_SIS_i_VIS_28.08.DOCX",
        "OSR.DOCX",
        "uzasadnienie.doc",
        "odwrócona_tabela_zgodności_WPISY_INFORMACYJNE_v_2.DOCX",
        "tabela_zgodności_WPISY_INFORMACYJNE.DOCX",
    ]
    letter = stage.documents("letters")[0]
    assert letter.id == 794894
    assert letter.url == (
        "https://legislacja.rcl.gov.pl/docs//2/12414100/13223895/13223897/dokument794894.DOCX"
    )
    assert (letter.created, letter.author) == (
        date(2026, 9, 1),
        "Minister Spraw Wewnętrznych i Administracji",
    )
    assert stage.documents("positions") == ()


def test_stage_catalog_of_an_unknown_stage_is_a_page_error() -> None:
    with pytest.raises(RclPageError):
        parse_stage_catalog(_page("katalog_13223895.html"), 1)


# --------------------------------------------------------------------------- client


def test_request_rejected_page_means_the_site_is_unavailable() -> None:
    client = _client(lambda request: httpx.Response(200, text=REJECTED))

    with pytest.raises(RclUnavailableError):
        client.get_project(12414100)


def test_server_errors_are_retried_then_reported_as_an_outage() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    with pytest.raises(RclUnavailableError):
        _client(handler, max_retries=2).get_project(1)

    assert calls == 3


def test_first_list_page_is_a_single_short_probe_project_pages_keep_their_retries() -> None:
    calls: list[tuple[str, float | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timeout = request.extensions.get("timeout", {}).get("read")
        calls.append((request.url.path, timeout))
        raise httpx.ConnectTimeout("timed out", request=request)

    client = RclClient(
        "https://rcl.test",
        timeout=60.0,
        probe_timeout=5.0,
        max_retries=2,
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )

    with pytest.raises(RclUnavailableError, match="after 1 attempt"):
        list(client.list_projects(modified_since=date(2026, 9, 1)))
    with pytest.raises(RclUnavailableError, match="after 3 attempt"):
        client.get_project(1)

    assert calls[0] == ("/lista", 5.0)
    assert [c for c in calls[1:]] == [("/projekt/1", 60.0)] * 3


def test_missing_project_is_a_page_error_not_an_outage() -> None:
    client = _client(lambda request: httpx.Response(404))

    with pytest.raises(RclPageError):
        client.get_project(1)


def test_rm_number_resolves_through_the_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/getIdFromLegislacja"
        if request.url.params["number"] == "RM-0610-139-26":
            return httpx.Response(
                302, headers={"location": "http://legislacja.rcl.gov.pl/projekt/12414050"}
            )
        return httpx.Response(404)

    client = _client(handler)

    assert client.resolve_project_id("RM-0610-139-26") == 12414050
    assert client.resolve_project_id("RM-0610-999-26") is None


def test_download_stops_at_the_size_limit() -> None:
    client = _client(lambda request: httpx.Response(200, content=b"x" * 1000))

    assert len(client.download("https://rcl.test/docs//2/1/2/3/dokument1.pdf")) == 1000
    with pytest.raises(AttachmentTooLargeError):
        client.download("https://rcl.test/docs//2/1/2/3/dokument1.pdf", max_bytes=100)


def test_download_of_a_rejected_document_is_an_outage() -> None:
    client = _client(lambda request: httpx.Response(200, text=REJECTED))

    with pytest.raises(RclUnavailableError):
        client.download("https://rcl.test/docs//2/1/2/3/dokument1.pdf")
