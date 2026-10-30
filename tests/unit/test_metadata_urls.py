import hashlib

import pytest

from lexinform.metadata_urls import MAX_METADATA_URL_BYTES, metadata_url_decision
from lexinform.models.rcl_letter_use import RclLetterUrl
from lexinform.models.sejm import (
    committee_letter_url,
    committee_web_url,
    consultation_web_url,
    print_web_url,
    process_web_url,
)
from lexinform.models.senate_inspection import SenateInspectionUrl


@pytest.mark.parametrize(
    "url",
    [
        "https://api.sejm.gov.pl/sejm/term10/prints/1/1.pdf",
        "http://orka.sejm.gov.pl/file.pdf",
        "https://legislacja.rcl.gov.pl/docs/2/123/letter.pdf",
        "https://www.senat.gov.pl/druk/1.pdf",
        "https://eli.gov.pl/eli/DU/2026/1/ogl",
        "https://custom.example:9443/zażółć%20gęślą.pdf",
        "http://sejm.test/files/1.pdf",
        "http://localhost:12345/file.pdf",
        "http://127.0.0.1:12345/file.pdf",
        "https://[::1]:12345/file.pdf",
        "https://żółć.example/a%2525.pdf",
        "HTTPS://CUSTOM.EXAMPLE/a b.pdf?#",
    ],
)
def test_queryless_urls_preserve_official_custom_and_fake_origins(url: str) -> None:
    decision = metadata_url_decision(url)
    assert decision.admitted
    assert decision.requested_url_sha256 == hashlib.sha256(url.encode()).hexdigest()


@pytest.mark.parametrize(
    "url",
    [
        print_web_url(10, "1-A"),
        process_web_url(10, "1"),
        consultation_web_url(10, "RPW/1234/2026"),
        committee_web_url(10, "ASW"),
        committee_letter_url(10, "ASW"),
        "https://www.sejm.gov.pl/Sejm10.nsf/agent.xsp?symbol=KONSULTACJE_WYNIKI&NrProjektu=RPW/1234/2026",
        "https://www.sejm.gov.pl/Sejm10.nsf/agent.xsp?symbol=RPL&Id=RM-0610-12-26",
        "https://www.sejm.gov.pl/Sejm10.nsf/agent.xsp?Id=RM%2f0610%2f12%2f26&symbol=RPL",
        "https://legislacja.rcl.gov.pl/getIdFromLegislacja?number=RM-0610-12-26",
        "https://legislacja.rcl.gov.pl/getIdFromLegislacja?number=rm+-0610-12-26",
        "https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=WDU20260000123",
        "http://isap.sejm.gov.pl:80/isap.nsf/DocDetails.xsp?id=WMP20260000123",
        "https://www.sejm.gov.pl:443/Sejm10.nsf/druk.xsp?nr=%31-A",
    ],
)
def test_closed_official_query_contracts_preserve_exact_original_bytes(url: str) -> None:
    decision = metadata_url_decision(url)
    assert decision.admitted
    assert decision.requested_url_sha256 == hashlib.sha256(url.encode()).hexdigest()


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://www.sejm.gov.pl/Sejm10.nsf/druk.xsp?nr=1&token=secret", "unrecognized_query"),
        ("https://www.sejm.gov.pl/Sejm10.nsf/druk.xsp?nr=1&nr=2", "unrecognized_query"),
        ("https://www.sejm.gov.pl/Sejm10.nsf/druk.xsp?nr=secret", "unrecognized_query"),
        (
            "https://www.sejm.gov.pl/Sejm10.nsf/agent.xsp?symbol=UNKNOWN&Id=RM-1-26",
            "unrecognized_query",
        ),
        (
            "https://www.sejm.gov.pl/Sejm10.nsf/agent.xsp?symbol=KOMISJAST&NrKadencji=9&KodKom=ASW",
            "unrecognized_query",
        ),
        ("https://www.sejm.gov.pl/Sejm10.nsf/druk.xsp?nr=", "unrecognized_query"),
        ("https://www.sejm.gov.pl:9443/Sejm10.nsf/druk.xsp?nr=1", "unrecognized_query"),
        ("https://www.sejm.gov.pl.other.test/Sejm10.nsf/druk.xsp?nr=1", "unrecognized_query"),
        ("https://www.sejm.gov.pl./Sejm10.nsf/druk.xsp?nr=1", "unrecognized_query"),
        ("https://sejm.test/Sejm10.nsf/druk.xsp?nr=1", "unrecognized_query"),
        (
            "https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=WDU20260000123+123+45",
            "unrecognized_query",
        ),
        (
            "https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=WDU20260000123+-+123",
            "unrecognized_query",
        ),
        ("https://custom.example/unknown?typeId=1", "unrecognized_query"),
        ("https://user:secret@sejm.test/a.pdf", "userinfo"),
        ("https://@sejm.test/a.pdf", "userinfo"),
        ("https://sejm.test/a.pdf#secret", "fragment"),
        ("https://sejm.test/a\n.pdf", "control_character"),
        ("https://sejm.test/a%0A.pdf", "control_character"),
        ("https://sejm.test/a%250d.pdf", "control_character"),
        ("https://sejm.test/a%C2%85.pdf", "control_character"),
        ("https://sejm.test/a%25E2%2580%25AE.pdf", "control_character"),
        ("https://sejm.test/a\u200b.pdf", "control_character"),
        ("https://sejm.test:0/a.pdf", "invalid_url"),
        ("https://sejm.test:65536/a.pdf", "invalid_url"),
        ("https://sejm.test:bad/a.pdf", "invalid_url"),
        ("https://bad host/a.pdf", "invalid_url"),
        ("https://sejm.test/a%xx.pdf", "invalid_url"),
        ("https://sejm.test\\a.pdf", "invalid_url"),
        (" https://sejm.test/a.pdf", "invalid_url"),
        ("file:///a.pdf", "invalid_url"),
        ("https:///a.pdf", "invalid_url"),
        ("https://sejm.test/unknown?flag", "invalid_url"),
        ("https://sejm.test/unknown?key=%FF", "invalid_url"),
        ("https://sejm.test/unknown?" + "a=1&" * 17, "invalid_url"),
        ("https://sejm.test/" + "a" * MAX_METADATA_URL_BYTES, "oversized_url"),
    ],
)
def test_omissions_have_closed_reasons_and_exact_url_hash(url: str, reason: str) -> None:
    decision = metadata_url_decision(url)
    assert not decision.admitted and decision.reason == reason
    assert decision.requested_url_sha256 == hashlib.sha256(url.encode()).hexdigest()


def test_unencodable_unicode_is_explicitly_unavailable_without_fabricated_hash() -> None:
    decision = metadata_url_decision("https://sejm.test/\ud800.pdf")
    assert decision.reason == "invalid_unicode" and decision.requested_url_sha256 is None


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "%E2%80%A8", "%E2%80%A9"])
@pytest.mark.parametrize("query", [False, True])
def test_line_and_paragraph_separators_are_omitted_before_query_normalization(
    separator: str, query: bool
) -> None:
    url = (
        "https://legislacja.rcl.gov.pl/getIdFromLegislacja?number=RM" + separator + "-1-26"
        if query
        else "https://custom.example/a" + separator + ".pdf"
    )
    decision = metadata_url_decision(url)
    assert decision.reason == "control_character" and not decision.admitted
    assert decision.requested_url_sha256 == hashlib.sha256(url.encode()).hexdigest()


def test_historical_domain_envelopes_keep_their_original_validation_and_json() -> None:
    rcl = "https://legislacja.rcl.gov.pl/docs/letter%0A.pdf"
    senate = "https://api.sejm.gov.pl/sejm/term10/prints/1/file%0A.pdf"
    for model, url in ((RclLetterUrl, rcl), (SenateInspectionUrl, senate)):
        legacy = model.of(url)
        raw = legacy.model_dump_json()
        assert url in raw and model.model_validate_json(raw).model_dump_json() == raw
        assert metadata_url_decision(url).reason == "control_character"
