import pytest

from lexinform.adapters.consultation_documents import parse_consultation_documents
from lexinform.errors import SejmApiUnavailableError
from tests.harness import submission


def test_sejm_reports_are_positions_and_external_or_statistical_links_are_excluded() -> None:
    sub = submission()
    page = "https://www.sejm.gov.pl/Sejm10.nsf/agent.xsp?symbol=KONSULTACJE_WYNIKI"
    body = f"""<html><main><h1>Wyniki {sub.number}</h1>
        <a href="https://orka.sejm.gov.pl/report.pdf">Raport z uwag</a>
        <a href="https://orka.sejm.gov.pl/stats.pdf">Raport statystyczny</a>
        <a href="https://example.test/accepted.pdf">Raport</a></main></html>"""
    sources = parse_consultation_documents(body, submission=sub, page=page)
    assert len(sources) == 1
    assert sources[0].role == "position"
    assert sources[0].document.url == "https://orka.sejm.gov.pl/report.pdf"
    assert not parse_consultation_documents(
        body.replace(sub.number, "other"), submission=sub, page=page
    )


@pytest.mark.parametrize("text", ["Human Verification", "Request Rejected", "Your support ID: 123"])
def test_sejm_waf_is_a_source_outage(text: str) -> None:
    with pytest.raises(SejmApiUnavailableError):
        parse_consultation_documents(text, submission=submission(), page="https://www.sejm.gov.pl/")
