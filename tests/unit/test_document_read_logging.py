import base64
import logging

import pytest

from lexinform.models import DocumentPart, DocumentSelection, ExtractedDocument
from lexinform.models.documents import SelectionState
from lexinform.services.documents import TextLoader
from tests.fakes import FakeSejmGateway, FakeTextExtractor

URL = "https://api.test/package.zip"
TEXT = "§ 1. Rozporządzenie określa warunki finansowania zadań obronnych. " * 10


class RetainedTextExtractor(FakeTextExtractor):
    def __init__(
        self, selection: DocumentSelection, *, pages: int = 0, unpack_blocked: bool = False
    ) -> None:
        super().__init__("", page_count=pages)
        self.selection = selection
        self.unpack_blocked = unpack_blocked

    def extract_document(self, data: bytes) -> ExtractedDocument:
        return ExtractedDocument(
            selection=self.selection, archive_unpack_blocked=self.unpack_blocked
        )


@pytest.mark.parametrize("state", ["unsupported", "ambiguous", "waiting"])
@pytest.mark.parametrize("scanned_attachment", [False, True])
def test_retained_package_text_is_not_reported_as_an_extraction_failure(
    state: SelectionState, scanned_attachment: bool, caplog: pytest.LogCaptureFixture
) -> None:
    parts: tuple[DocumentPart, ...] = (DocumentPart(text=TEXT, members=("project.docx",)),)
    if scanned_attachment:
        parts += (
            DocumentPart(members=("attachment.pdf",), pages=2, losses=("no extracted text",)),
        )
    selection = DocumentSelection(
        state=state,
        observed_parts=parts,
        rejected=("project.docx: regulation",),
    )
    gateway = FakeSejmGateway(files={URL: b"package"})
    loader = TextLoader(
        {"api.test": gateway.download}, RetainedTextExtractor(selection), max_bytes=1000
    )

    with caplog.at_level(logging.INFO, logger="lexinform.services.documents"):
        loaded = loader.read(URL)

    assert loaded.text is None and loaded.selection.state == state
    assert loaded.selection.observed_parts[0].text == TEXT
    assert f"retained {len(TEXT)} chars across {len(parts)} parts" in caplog.text
    assert "no combined text" in caplog.text
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_textless_pdf_pages_are_retained_as_a_scan_without_a_failure_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gateway = FakeSejmGateway(files={URL: b"scan"})
    loader = TextLoader(
        {"api.test": gateway.download},
        RetainedTextExtractor(
            DocumentSelection(parts=(DocumentPart(pages=12, losses=("no extracted text",)),)),
            pages=12,
        ),
        max_bytes=1000,
    )

    with caplog.at_level(logging.INFO, logger="lexinform.services.documents"):
        loaded = loader.read(URL)
        scan = loader.load_scan(URL, cover_letter=False)

    assert loaded.text is None and loaded.pages == 12
    assert scan is not None, "Readable PDF pages must remain available for scan processing"
    assert scan.pages == 12 and base64.b64decode(scan.data) == b"scan"
    assert "retained for scan processing" in caplog.text
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


@pytest.mark.parametrize("pages", [0, 12])
def test_empty_or_failed_extraction_still_warns(
    pages: int, caplog: pytest.LogCaptureFixture
) -> None:
    gateway = FakeSejmGateway(files={URL: b"broken"})
    selection = DocumentSelection(
        parts=(DocumentPart(losses=("PDF text extraction failed on page 1",)),)
        if pages
        else (DocumentPart(losses=("no extracted text",)),),
    )
    loader = TextLoader(
        {"api.test": gateway.download},
        RetainedTextExtractor(selection, pages=pages),
        max_bytes=1000,
    )

    loaded = loader.read(URL)

    assert loaded.text is None
    assert "yielded almost no text" in caplog.text
    assert any(record.levelno == logging.WARNING for record in caplog.records)


@pytest.mark.parametrize("unpack_blocked", [False, True])
def test_partial_package_read_failure_still_warns(
    unpack_blocked: bool, caplog: pytest.LogCaptureFixture
) -> None:
    selection = DocumentSelection(
        state="waiting",
        observed_parts=(
            DocumentPart(
                text=TEXT,
                members=("project.pdf",),
                losses=() if unpack_blocked else ("PDF text extraction failed on page 1",),
            ),
        ),
    )
    gateway = FakeSejmGateway(files={URL: b"partial"})
    loader = TextLoader(
        {"api.test": gateway.download},
        RetainedTextExtractor(selection, unpack_blocked=unpack_blocked),
        max_bytes=1000,
    )

    loaded = loader.read(URL)

    assert loaded.text is None
    assert loaded.archive_unpack_blocked is unpack_blocked
    assert loaded.selection.observed_parts[0].text == TEXT
    assert "no combined text" in caplog.text
    assert any(record.levelno == logging.WARNING for record in caplog.records)
