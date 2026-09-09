"""Word documents and the format-sniffing extractor."""

import io
import zipfile

from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.sections import PAGE_BREAK
from tests.fakes import FakeTextExtractor

_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _docx(body_xml: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "word/document.xml",
            f'<?xml version="1.0"?><w:document {_NS}><w:body>{body_xml}</w:body></w:document>',
        )
    return buffer.getvalue()


def test_paragraphs_tables_and_page_breaks_become_plain_text() -> None:
    data = _docx(
        "<w:p><w:r><w:t>Art. 1.</w:t></w:r><w:r><w:tab/><w:t>Ustawa</w:t></w:r></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Lp.</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>Podmiot</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        '<w:p><w:r><w:br w:type="page"/><w:t>UZASADNIENIE</w:t></w:r></w:p>'
        "<w:sdt><w:sdtContent><w:p><w:r><w:t>OSR</w:t></w:r></w:p></w:sdtContent></w:sdt>"
    )

    text = DocxTextExtractor().extract(data)

    assert text == f"Art. 1.\tUstawa\nLp.\tPodmiot{PAGE_BREAK}UZASADNIENIE\nOSR"


def test_rendered_page_break_markers_separate_pages() -> None:
    data = _docx(
        "<w:p><w:r><w:t>strona 1</w:t></w:r></w:p>"
        "<w:p><w:r><w:lastRenderedPageBreak/><w:t>strona 2</w:t></w:r></w:p>"
    )

    assert DocxTextExtractor().extract(data) == f"strona 1{PAGE_BREAK}strona 2"


def test_extractor_is_chosen_by_the_magic_bytes() -> None:
    extractor = DocumentTextExtractor(FakeTextExtractor("from pdf"), FakeTextExtractor("from docx"))

    assert extractor.extract(b"%PDF-1.7 ...") == "from pdf"
    assert extractor.extract(b"\n\n%PDF-1.4 with a preamble") == "from pdf"
    assert extractor.extract(b"PK\x03\x04 zip") == "from docx"
    assert extractor.extract(b"\xd0\xcf\x11\xe0 legacy .doc") == ""
