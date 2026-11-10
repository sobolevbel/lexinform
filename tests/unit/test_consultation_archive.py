import io
from zipfile import ZipFile

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.models import TextDocument
from lexinform.services.documents import TextLoader
from tests.unit.test_physical_extraction import docx


def test_consultation_archive_selects_report_without_project_or_osr() -> None:
    answer = "Raport z konsultacji publicznych. Pkt 1. Uwaga uwzględniona."
    output = io.BytesIO()
    with ZipFile(output, "w") as package:
        package.writestr("projekt.docx", docx("USTAWA. Art. 1. Zmienia się przepisy ustawy."))
        package.writestr("OSR.docx", docx("Ocena skutków regulacji"))
        package.writestr("raport_z_konsultacji.docx", docx(answer))
    raw = output.getvalue()

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        return raw

    loader = TextLoader(
        {"rcl.test": download},
        DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor()),
        max_bytes=1_000_000,
    )
    source = TextDocument(url="https://rcl.test/late-project.zip", kind="rcl")

    read = loader.read_consultation_response(source)

    assert read.text == answer
    assert [part.members for part in read.selection.parts] == [("raport_z_konsultacji.docx",)]
    assert len(read.selection.observed_parts) == 3
    assert not read.consultation_archive_no_report


def test_ordinary_draft_archive_is_not_a_missing_consultation_report() -> None:
    output = io.BytesIO()
    with ZipFile(output, "w") as package:
        package.writestr("projekt.docx", docx("USTAWA. Art. 1. Zmienia się przepisy ustawy."))
    raw = output.getvalue()

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        return raw

    loader = TextLoader(
        {"rcl.test": download},
        DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor()),
        max_bytes=1_000_000,
    )
    read = loader.read_consultation_response(
        TextDocument(url="https://rcl.test/late-project.zip", kind="rcl")
    )
    assert read.consultation_archive_no_report
    assert read.text is None


def test_consultation_archive_keeps_rows_bound_to_their_own_members() -> None:
    def report(answer: str) -> bytes:
        word = io.BytesIO()
        with ZipFile(word, "w") as package:
            package.writestr(
                "word/document.xml",
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body><w:tbl><w:tr><w:tc><w:p><w:r><w:t>1</w:t></w:r></w:p></w:tc>"
                f"<w:tc><w:p><w:r><w:t>{answer}</w:t></w:r></w:p></w:tc></w:tr>"
                "</w:tbl></w:body></w:document>",
            )
        return word.getvalue()

    output = io.BytesIO()
    with ZipFile(output, "w") as package:
        package.writestr("raport_z_konsultacji_1.docx", report("Uwaga została uwzględniona."))
        package.writestr("raport_z_konsultacji_2.docx", report("Uwaga nie została uwzględniona."))
        package.writestr("projekt.docx", report("Projekt ustawy."))

    downloads: list[str] = []

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        downloads.append(url)
        return output.getvalue()

    word = DocxTextExtractor()
    loader = TextLoader(
        {"rcl.test": download},
        DocumentTextExtractor(PypdfTextExtractor(), word, DocTextExtractor()),
        max_bytes=1_000_000,
        capture_consultation_archive_rows=word.consultation_archive_row_spans,
    )
    source = TextDocument(url="https://rcl.test/archive.zip", kind="rcl")
    loader.read(source.url)
    read = loader.read_consultation_response(source)

    assert downloads == [source.url]
    assert read.text is not None, "the archive contains two selected consultation reports"
    assert [row.label for row in read.consultation_rows] == ["1", "1"]
    assert len({row.locator.rsplit("/", 1)[0] for row in read.consultation_rows}) == 2
    assert [read.text[row.start : row.end] for row in read.consultation_rows] == [
        "1\tUwaga została uwzględniona.",
        "1\tUwaga nie została uwzględniona.",
    ]
