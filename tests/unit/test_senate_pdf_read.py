import datetime as dt
from pathlib import Path
from unittest.mock import create_autospec

import pytest

from lexinform.models import Attachment, DocumentPart, DocumentSelection, PrintInfo
from lexinform.services.documents import LoadedFile, TextLoader
from lexinform.services.senate_documents import resolve_senate_document, verified_senate_text

FIXTURES = Path(__file__).parents[1] / "fixtures/sejm"


def test_failed_pdf_page_cannot_qualify_as_complete_senate_text() -> None:
    text = (FIXTURES / "senate_3213_resolution.txt").read_text()
    adopted_on = dt.date(2026, 10, 13)
    url = "https://api.test/3213.pdf"
    info = PrintInfo(
        term=10,
        number="3213",
        title="Uchwała Senatu w sprawie ustawy o asystencji osobistej osób z niepełnosprawnościami",
        attachments=(Attachment(print_number="3213", name="3213.pdf", url=url),),
    )
    loader = create_autospec(TextLoader, instance=True)
    loader.read.return_value = LoadedFile(text, pages=3, sha256="a" * 64)
    document = resolve_senate_document(loader, info, adopted_on=adopted_on, titles=())
    assert document is not None, "the fixture contains the entire adopted resolution"
    assert document.senate is not None, "the resolver supplies the verified identity"
    assert not document.senate.scan
    assert verified_senate_text(loader, document) == text

    loader.read.return_value = LoadedFile(
        text,
        pages=3,
        sha256="a" * 64,
        selection=DocumentSelection(
            parts=(DocumentPart(losses=("PDF text extraction failed on 1 page(s)",)),)
        ),
    )

    fallback = resolve_senate_document(loader, info, adopted_on=adopted_on, titles=())
    assert fallback is not None, "the pinned PDF remains available for visual verification"
    assert fallback.senate is not None and fallback.senate.scan
    with pytest.raises(ValueError, match="полный текст поправок"):
        verified_senate_text(loader, document)

    alternate_url = "https://api.test/alternate.pdf"
    competing = info.model_copy(
        update={
            "attachments": (
                *info.attachments,
                Attachment(print_number="3213", name="alternate.pdf", url=alternate_url),
            )
        }
    )
    loader.read.side_effect = [loader.read.return_value, LoadedFile(text, sha256="b" * 64)]
    assert resolve_senate_document(loader, competing, adopted_on=adopted_on, titles=()) is None
