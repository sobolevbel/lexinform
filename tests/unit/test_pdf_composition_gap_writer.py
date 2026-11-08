import hashlib

import pytest

from lexinform.models import DocumentPart, DocumentSelection, TextDocument
from lexinform.models.documents import ExtractedDocument
from lexinform.services.documents import TextLoader
from tests.fakes import FakeTextExtractor
from tests.unit.test_required_part_writer import BODY, MAIN, REQUIRED

LATER = REQUIRED + "/later"
FAILED = b"returned PDF with an incomplete text layer"


@pytest.mark.parametrize(
    ("field", "gap_field", "position"),
    [
        ("required_urls", "required_part_gap", "required_index"),
        ("normative_urls", "normative_annex_gap", "normative_index"),
        ("amendment_urls", "amendment_part_gap", "amendment_index"),
    ],
)
def test_required_pdf_page_failure_waits_before_reading_later_parts(
    field: str, gap_field: str, position: str
) -> None:
    class PartialPdf(FakeTextExtractor):
        def extract_document(self, data: bytes) -> ExtractedDocument:
            text = self.extract(data)
            if data == FAILED:
                part = DocumentPart(losses=("PDF text extraction failed on 1 page(s)",))
                return ExtractedDocument(text=text, selection=DocumentSelection(parts=(part,)))
            return ExtractedDocument(text=text)

    requested: list[str] = []

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        requested.append(url)
        return FAILED if url == REQUIRED else BODY.encode()

    loader = TextLoader({"source.test": download}, PartialPdf(BODY, page_count=0), max_bytes=100000)
    document = TextDocument.model_validate({"url": MAIN, "kind": "rcl", field: (REQUIRED, LATER)})

    _, file = loader.select_document(document)

    assert file.text is None and file.selection.state == "waiting"
    assert requested == [MAIN, REQUIRED]
    trace = file.candidate_trace
    assert trace is not None, "the selected outer candidate keeps its blocked composition trace"
    assert trace.model_dump(exclude_none=True)[gap_field] == {
        "phase": "fallback",
        "index": 0,
        position: 0,
        "reason": "pdf_page_extraction_failed",
        "file_sha256": hashlib.sha256(FAILED).hexdigest(),
    }
