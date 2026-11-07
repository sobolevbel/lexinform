import pytest
from tools.corpus_artifacts import digest
from tools.replay_pdf_page_citation import RawPageSpanPin, verify_pdf_page
from tools.replay_primary_claims import technical_answer

from lexinform.models import EvidenceCitation, PdfPageText, PdfTextExtraction, ReviewDocument
from lexinform.pdf_projection import raw_pdf_page_span
from lexinform.primary_claims import KeyChangeEvidence

ARTIFACT = "a" * 64
QUOTE = "Art. 7. Ustawa wchodzi w życie."


def check(
    pages: tuple[str, ...],
    *,
    quote: str = QUOTE,
    page: int = 2,
    artifact_pin: str = ARTIFACT,
    extraction_pin: str | None = None,
    raw_span: RawPageSpanPin | None = None,
) -> dict[str, object]:
    extraction = PdfTextExtraction(
        input_sha256=ARTIFACT,
        pypdf_version="offline fixture",
        pages=tuple(PdfPageText(number=i, text=text) for i, text in enumerate(pages, start=1)),
    )
    text = extraction.legacy_text
    source = ReviewDocument(
        identity="source",
        url="https://source.test/print.pdf",
        role="project_text",
        text=text,
        sha256=digest(text.encode()),
        normalized_sha256=digest(text.encode()),
        file_sha256=ARTIFACT,
    )
    record = technical_answer(text)
    evidence = KeyChangeEvidence(
        0,
        digest(record.analysis.key_changes[0].encode()),
        source.sha256,
        EvidenceCitation(document_id="source", locator="Art. 7", quote=quote),
    )
    return verify_pdf_page(
        record,
        evidence,
        source,
        extraction,
        artifact_sha256=artifact_pin,
        extraction_sha256=extraction_pin or extraction.manifest_sha256,
        expected_page=page,
        raw_page_span=raw_span,
    )


def test_physical_page_block_maps_the_literal_quote_with_normalized_coordinates() -> None:
    result = check(("Cover", "\n  Header\r\n" + QUOTE.replace("w życie", "w\xa0życie") + "\n"))

    assert result["page"] == 2
    assert result["block_id"] == "pdf-page:2:0"
    assert result["normalized_page_span"] == [7, 7 + len(QUOTE)]
    assert result["normalized_page_text_sha256"] == digest(("Header\n" + QUOTE).encode())


@pytest.mark.parametrize(
    ("pages", "message"),
    [
        (("", QUOTE), "collapsed physical page boundaries"),
        ((QUOTE, ""), "collapsed physical page boundaries"),
        (("Cover\finside one page", QUOTE), "Pinned PDF artifact"),
        ((QUOTE, QUOTE), "quote_ambiguous"),
    ],
)
def test_ambiguous_or_collapsed_page_maps_are_rejected(
    pages: tuple[str, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        check(pages)


def test_quote_crossing_physical_page_boundary_cannot_gain_one_page_locator() -> None:
    with pytest.raises(ValueError, match="crosses a physical page boundary"):
        check(("Art. 7. Ustawa", "wchodzi w życie."), quote="Art. 7. Ustawa\fwchodzi w życie.")


@pytest.mark.parametrize("artifact_changed", [True, False])
def test_pdf_artifact_and_manifest_require_independent_pins(artifact_changed: bool) -> None:
    with pytest.raises(ValueError, match="Pinned PDF artifact"):
        check(
            ("Cover", QUOTE),
            artifact_pin="b" * 64 if artifact_changed else ARTIFACT,
            extraction_pin=None if artifact_changed else "b" * 64,
        )


def test_supplied_page_locator_must_match_the_observed_page() -> None:
    with pytest.raises(ValueError, match="expected physical page"):
        check(("Cover", QUOTE), page=1)


def test_raw_extractor_span_is_opt_in_and_requires_exact_transform_and_pin() -> None:
    raw_quote = "Art. 7.\r\nUstawa w\xa0życie.  \nKoniec."
    prefix = "\n  Header\r\n"
    page = prefix + raw_quote + "\n"
    quote = "Art. 7.\nUstawa w życie.\nKoniec."
    span = RawPageSpanPin(
        start=len(prefix), end=len(prefix + raw_quote), sha256=digest(raw_quote.encode())
    )
    assert "raw_page_span" not in check(("Cover", page), quote=quote)
    assert check(("Cover", page), quote=quote, raw_span=span)["raw_page_span"] == {
        "coordinates": "pdf-extractor-page-python-string",
        "span": [span.start, span.end],
        "text_sha256": span.sha256,
    }
    with pytest.raises(ValueError, match="span hash differs"):
        check(("Cover", page), quote=quote, raw_span=span.model_copy(update={"sha256": "0" * 64}))
    with pytest.raises(ValueError, match="ambiguous or differs"):
        check(("Cover", page), quote=quote, raw_span=span.model_copy(update={"start": 0}))


def test_raw_page_span_will_not_split_a_collapsed_newline_run() -> None:
    page = "first\n\n\nsecond"
    assert raw_pdf_page_span(page, 0, len("first\n\nsecond")) == (0, len(page))
    assert raw_pdf_page_span(page, 0, len("first\n")) is None
    assert raw_pdf_page_span(page, len("first\n"), len("first\n\nsecond")) is None
