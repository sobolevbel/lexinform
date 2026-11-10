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
