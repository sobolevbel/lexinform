import hashlib

import pytest
from pydantic import ValidationError

from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.models import (
    DocumentPart,
    ExtractedParts,
    PdfPageText,
    PdfPartExtraction,
    PdfTextExtraction,
)
from tests.fakes import FakeTextExtractor
from tests.unit.test_physical_extraction import archive


class PdfWithPages(FakeTextExtractor):
    def __init__(self) -> None:
        super().__init__("USTAWA", page_count=3)
        self.page_reads = 0
        self.string_reads = 0

    def extract(self, data: bytes) -> str:
        self.string_reads += 1
        return super().extract(data)

    def extract_pages(self, data: bytes) -> PdfTextExtraction:
        self.page_reads += 1
        return PdfTextExtraction(
            input_sha256=hashlib.sha256(data).hexdigest(),
            pypdf_version="fixture",
            pages=(
                PdfPageText(number=1),
                PdfPageText(number=2, text="USTAWA"),
                PdfPageText(number=3),
            ),
        )


def extractor(pdf: FakeTextExtractor) -> DocumentTextExtractor:
    return DocumentTextExtractor(pdf, DocxTextExtractor(), FakeTextExtractor("DOC text"))


def test_pdf_pages_refer_to_the_root_physical_bytes_and_keep_blank_edges() -> None:
    data = b"%PDF-1.7 fixture"
    pdf = PdfWithPages()

    result = extractor(pdf).extract_parts(data)

    assert len(result.parts) == 1 and len(result.pdf_pages) == 1
    linked = result.pdf_pages[0]
    assert linked.part_index == 0 and linked.extraction.input_sha256 == result.parts[0].sha256
    assert result.parts[0].sha256 == result.input_sha256 == hashlib.sha256(data).hexdigest()
    assert linked.extraction.pages[1].number == 2
    assert [page.text for page in linked.extraction.pages] == ["", "USTAWA", ""]
    assert result.parts[0].text == linked.extraction.legacy_text == "USTAWA"
    assert result.parts[0].losses == ("text page coverage differs from the PDF page count",)
    assert pdf.page_reads == 1 and pdf.string_reads == 0


def test_identical_bytes_in_different_members_keep_distinct_physical_links() -> None:
    data = b"%PDF-1.7 fixture"
    bundle = archive({"a.pdf": data, "nested.zip": archive({"b.pdf": data})})
    pdf = PdfWithPages()

    result = extractor(pdf).extract_parts(bundle)

    assert [part.members for part in result.parts] == [("a.pdf",), ("nested.zip", "b.pdf")]
    assert [linked.part_index for linked in result.pdf_pages] == [0, 1]
    assert result.pdf_pages[0].extraction == result.pdf_pages[1].extraction
    assert all(part.container_sha256 == hashlib.sha256(bundle).hexdigest() for part in result.parts)
    assert all(part.sha256 == hashlib.sha256(data).hexdigest() for part in result.parts)
    assert pdf.page_reads == 2 and pdf.string_reads == 0
    assert ExtractedParts.model_validate_json(result.model_dump_json()) == result


def test_legacy_selection_and_explicit_string_only_collection_do_not_retain_pdf_pages() -> None:
    data = b"%PDF-1.7 fixture"
    pdf = PdfWithPages()
    reader = extractor(pdf)

    document = reader.extract_document(data)
    physical = reader.extract_parts(data, include_pdf_pages=False)

    assert document.text == physical.parts[0].text == "USTAWA"
    assert not physical.pdf_pages
    assert pdf.page_reads == 0 and pdf.string_reads == 2


def test_a_legacy_pdf_port_has_no_invented_page_manifest() -> None:
    pdf = FakeTextExtractor("USTAWA", page_count=1)

    result = extractor(pdf).extract_parts(b"%PDF-1.7 fixture")

    assert result.parts[0].text == "USTAWA" and not result.pdf_pages


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1%PDF-1.7", "DOC text"),
        (
            b'<?xml version="1.0"?><w:wordDocument xmlns:w="http://schemas.microsoft.com/office/word/2003/wordml"><w:body><w:p><w:r><w:t>%PDF-1.7</w:t></w:r></w:p></w:body></w:wordDocument>',
            "%PDF-1.7",
        ),
    ],
)
def test_embedded_pdf_header_does_not_override_the_actual_document_format(
    data: bytes, expected: str
) -> None:
    pdf = PdfWithPages()

    result = extractor(pdf).extract_parts(data)

    assert result.parts[0].text == expected and not result.pdf_pages
    assert pdf.page_reads == 0 and pdf.string_reads == 0


@pytest.mark.parametrize("problem", ["absent", "different_bytes", "duplicate"])
def test_pdf_records_cannot_claim_an_absent_or_different_physical_part(problem: str) -> None:
    sha = hashlib.sha256(b"one file").hexdigest()
    linked = PdfPartExtraction(
        part_index=0, extraction=PdfTextExtraction(input_sha256=sha, pypdf_version="fixture")
    )
    parts = (
        ()
        if problem == "absent"
        else (DocumentPart(sha256="0" * 64 if problem == "different_bytes" else sha),)
    )
    links = (linked, linked) if problem == "duplicate" else (linked,)

    with pytest.raises(ValidationError, match="physical part|physical bytes"):
        ExtractedParts(input_sha256=sha, parts=parts, pdf_pages=links)
