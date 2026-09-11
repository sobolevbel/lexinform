"""Word documents (.docx and legacy .doc) and the format-sniffing extractor."""

import io
import zipfile

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import (
    DocumentTextExtractor,
    DocxTextExtractor,
    OdtTextExtractor,
)
from lexinform.sections import PAGE_BREAK
from tests.conftest import RCL_FIXTURES
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


def test_a_table_nested_in_a_cell_is_read_once_as_part_of_the_cell() -> None:
    # The OSR form is a table; some ministries put a table of affected parties inside a cell.
    data = _docx(
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Podmioty</w:t></w:r></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>cudzoziemcy</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>2 mln</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:tc>"
        "<w:tc><w:p><w:r><w:t>Wpływ</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    )

    assert DocxTextExtractor().extract(data) == "Podmioty cudzoziemcy\t2 mln\tWpływ"


def test_a_text_box_is_read_once_although_word_stores_it_twice() -> None:
    # Word writes every drawing as mc:AlternateContent with a Choice (new Word) and a Fallback
    # (old Word) that carry the same text.
    mc = (
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"'
    )
    data = _docx(
        f"<w:p {mc}><w:r><w:t>Ramka: </w:t></w:r><w:r><mc:AlternateContent>"
        '<mc:Choice Requires="wps"><w:drawing><wps:txbx><w:txbxContent>'
        "<w:p><w:r><w:t>tekst ramki</w:t></w:r></w:p></w:txbxContent></wps:txbx></w:drawing>"
        "</mc:Choice><mc:Fallback><w:pict><w:txbxContent>"
        "<w:p><w:r><w:t>tekst ramki</w:t></w:r></w:p></w:txbxContent></w:pict></mc:Fallback>"
        "</mc:AlternateContent></w:r></w:p>"
    )

    assert DocxTextExtractor().extract(data) == "Ramka: tekst ramki"


def test_deleted_text_of_tracked_changes_is_not_read() -> None:
    data = _docx(
        "<w:p><w:del><w:r><w:delText>stare</w:delText></w:r></w:del>"
        "<w:ins><w:r><w:t>nowe</w:t></w:r></w:ins></w:p>"
    )

    assert DocxTextExtractor().extract(data) == "nowe"


def test_strict_open_xml_uses_another_namespace_and_is_read_too() -> None:
    strict = 'xmlns:w="http://purl.oclc.org/ooxml/wordprocessingml/main"'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            f"<w:document {strict}><w:body><w:p><w:r><w:t>Art. 1.</w:t></w:r>"
            '<w:r><w:br w:type="page"/><w:t>Art. 2.</w:t></w:r></w:p></w:body></w:document>',
        )
    foreign = io.BytesIO()
    with zipfile.ZipFile(foreign, "w") as archive:
        archive.writestr("word/document.xml", '<x:doc xmlns:x="urn:x"><x:body/></x:doc>')

    assert DocxTextExtractor().extract(buffer.getvalue()) == f"Art. 1.{PAGE_BREAK}Art. 2."
    assert DocxTextExtractor().extract(foreign.getvalue()) == ""


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _odt(content_xml: str) -> bytes:
    return _zip(
        {
            "mimetype": b"application/vnd.oasis.opendocument.text",
            "content.xml": (
                '<?xml version="1.0"?><office:document-content'
                ' xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
                ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
                ' xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">'
                f"<office:body><office:text>{content_xml}</office:text></office:body>"
                "</office:document-content>"
            ).encode(),
        }
    )


def _router() -> DocumentTextExtractor:
    return DocumentTextExtractor(
        FakeTextExtractor("from pdf"), DocxTextExtractor(), FakeTextExtractor("from doc")
    )


def test_extractor_is_chosen_by_the_magic_bytes() -> None:
    extractor = DocumentTextExtractor(
        FakeTextExtractor("from pdf"), FakeTextExtractor("from docx"), FakeTextExtractor("from doc")
    )
    docx = _docx("<w:p><w:r><w:t>x</w:t></w:r></w:p>")

    assert extractor.extract(b"%PDF-1.7 ...") == "from pdf"
    assert extractor.extract(b"\n\n%PDF-1.4 with a preamble") == "from pdf"
    assert extractor.extract(docx) == "from docx"
    assert extractor.extract(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 OLE container") == "from doc"
    assert extractor.extract(b"{\\rtf1 not supported}") == ""


def test_zip_package_is_read_member_by_member_bill_first() -> None:
    package = _zip(
        {
            "__MACOSX/._projekt.pdf": b"junk",
            "OSR do projektu.docx": _docx("<w:p><w:r><w:t>Nazwa projektu</w:t></w:r></w:p>"),
            "tabela zgodnosci.xlsx": b"PK\x03\x04 not a document",
            "Uzasadnienie.docx": _docx("<w:p><w:r><w:t>UZASADNIENIE</w:t></w:r></w:p>"),
            "Projekt ustawy.pdf": b"%PDF-1.7 bill",
        }
    )

    text = _router().extract(package)

    assert text == f"from pdf{PAGE_BREAK}UZASADNIENIE{PAGE_BREAK}Nazwa projektu"


def test_zip_reads_one_level_of_nesting_and_gives_up_on_junk() -> None:
    # As published on RCL: a cover letter next to a zip that holds the bill itself. The letter
    # and the compliance table are not bill text and stay out, as they do in a "Projekt" folder.
    inner = _zip({"projekt ustawy.pdf": b"%PDF-1.7 bill"})
    package = _zip(
        {
            "pismo.pdf": b"%PDF-1.7 letter",
            "tabela zgodności.docx": _docx("<w:p><w:r><w:t>TYTUŁ PROJEKTU</w:t></w:r></w:p>"),
            "projekt na RM.zip": inner,
        }
    )
    deeper = _zip({"outer.zip": _zip({"middle.zip": inner})})

    assert _router().extract(package) == "from pdf"
    assert _router().extract(deeper) == ""  # two levels down is too deep
    assert _router().extract(_zip({"uwagi.xlsx": b"x", "notes.txt": b"y"})) == ""


def test_an_archive_member_over_the_unpacked_size_limit_is_skipped() -> None:
    small = _docx("<w:p><w:r><w:t>UZASADNIENIE</w:t></w:r></w:p>")
    package = _zip({"projekt.pdf": b"%PDF-1.7 " + b"x" * 500, "uzasadnienie.docx": small})
    extractor = DocumentTextExtractor(
        FakeTextExtractor("from pdf"),
        DocxTextExtractor(),
        FakeTextExtractor("from doc"),
        max_member_bytes=500,  # the small .docx unpacks to ~430 bytes, the PDF to 509
    )

    assert extractor.extract(package) == "UZASADNIENIE"


def test_zip_members_in_folders_and_of_every_format_are_routed() -> None:
    package = _zip(
        {
            "pakiet/projekt ustawy.doc": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 legacy word",
            "pakiet/uzasadnienie.docx": _docx("<w:p><w:r><w:t>UZASADNIENIE</w:t></w:r></w:p>"),
            "pakiet/osr.pdf": b"%PDF-1.7 osr",
        }
    )

    assert _router().extract(package) == f"from doc{PAGE_BREAK}UZASADNIENIE{PAGE_BREAK}from pdf"


def test_a_zip_whose_stored_member_starts_with_pdf_is_still_a_zip() -> None:
    # The PDF-behind-a-preamble heuristic must not fire on the stored bytes of a member.
    package = _zip({"a.pdf": b"%PDF-1.7 " + b"x" * 10, "b.pdf": b"%PDF-1.7 y"})

    assert _router().extract(package) == f"from pdf{PAGE_BREAK}from pdf"


def test_a_corrupt_zip_yields_no_text_instead_of_an_error() -> None:
    assert _router().extract(b"PK\x03\x04" + b"\0" * 200) == ""


def test_odt_paragraphs_headings_and_tables_become_plain_text() -> None:
    data = _odt(
        "<text:h>USTAWA</text:h>"
        "<text:p>Art. 1.<text:tab/>Cudzoziemiec<text:s text:c='2'/>x<text:line-break/>y</text:p>"
        "<table:table><table:table-row><table:table-cell><text:p>Lp.</text:p></table:table-cell>"
        "<table:table-cell><text:p>Podmiot</text:p></table:table-cell></table:table-row>"
        "</table:table>"
        "<text:p>strona 1<text:soft-page-break/>strona 2</text:p>"
    )

    assert OdtTextExtractor().extract(data) == (
        f"USTAWA\nArt. 1.\tCudzoziemiec  x\ny\nLp.\tPodmiot\nstrona 1{PAGE_BREAK}strona 2"
    )
    assert _router().extract(data).startswith("USTAWA\n")  # routed by the mimetype entry


def test_odt_lists_and_sections_are_flattened_and_an_empty_body_gives_nothing() -> None:
    nested = _odt(
        "<text:section><text:p>w sekcji</text:p></text:section>"
        "<text:list><text:list-item><text:p>punkt 1</text:p></text:list-item>"
        "<text:list-item><text:p>punkt 2</text:p></text:list-item></text:list>"
    )
    empty = _zip(
        {
            "mimetype": b"application/vnd.oasis.opendocument.text",
            "content.xml": b'<?xml version="1.0"?><office:document-content '
            b'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"/>',
        }
    )

    assert OdtTextExtractor().extract(nested) == "w sekcji\npunkt 1\npunkt 2"
    assert OdtTextExtractor().extract(empty) == ""


def test_odt_text_keeps_its_order_around_spans_and_nested_tables_are_read_once() -> None:
    data = _odt(
        "<text:p>Art. <text:span>1<text:span>. </text:span>Cudzo<text:tab/>x</text:span>"
        "ziemiec</text:p>"
        "<table:table><table:table-header-rows><table:table-row>"
        "<table:table-cell><text:p>Lp.</text:p></table:table-cell>"
        "<table:table-cell><text:p>Podmiot</text:p></table:table-cell>"
        "</table:table-row></table:table-header-rows>"
        "<table:table-row><table:table-cell><text:p>1</text:p></table:table-cell>"
        "<table:table-cell><text:p>zewn.</text:p><table:table><table:table-row>"
        "<table:table-cell><text:p>wewn.</text:p></table:table-cell>"
        "</table:table-row></table:table></table:table-cell></table:table-row></table:table>"
    )

    assert OdtTextExtractor().extract(data) == (
        "Art. 1. Cudzo\txziemiec\nLp.\tPodmiot\n1\tzewn. wewn."
    )


def test_legacy_doc_yields_its_paragraphs_in_order_with_polish_letters_intact() -> None:
    # The uzasadnienie of project UC164 as published on RCL (Word 97-2003, mixed 8-bit and
    # UTF-16 pieces in the piece table).
    data = (RCL_FIXTURES / "uzasadnienie_uc164.doc").read_bytes()

    text = DocTextExtractor().extract(data)

    assert text.startswith("UZASADNIENIE\n\n1. Potrzeba i cel wydania projektowanej ustawy\n")
    assert "Systemie Informacyjnym Schengen oraz Wizowym Systemie Informacyjnym" in text
    assert "zwanej dalej „ustawą o SIS i VIS”" in text  # UTF-16 piece: Polish letters and quotes
    assert text.endswith("Regulamin pracy Rady Ministrów.")
    assert 25_000 < len(text) < 30_000
    assert not any(ord(ch) < 32 and ch not in "\n\t\f" for ch in text)  # no Word control chars
    assert "�" not in text


def test_damaged_or_foreign_ole_files_yield_no_text_instead_of_an_error() -> None:
    data = (RCL_FIXTURES / "uzasadnienie_uc164.doc").read_bytes()

    assert DocTextExtractor().extract(data[:4096]) == ""  # truncated: streams missing
    assert DocTextExtractor().extract(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600) == ""
    assert DocTextExtractor().extract(b"not even an OLE file") == ""
