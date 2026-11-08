import hashlib

import pytest

from lexinform.document_selection import DOCUMENT_SELECTION_POLICY_VERSION
from lexinform.models import DocumentPart, DocumentSelection, TextDocument
from lexinform.models.documents import ExtractedDocument
from lexinform.services.documents import TextLoader
from tests.fakes import FakeTextExtractor
from tests.scenario.test_document_selection_contract import law

EARLIER = "https://source.test/earlier.pdf"
NEWER = "https://source.test/newer.pdf"


@pytest.mark.parametrize(
    ("failed_text", "waiting"),
    [(None, False), (law(day=2, days=60), True), ("", True)],
)
def test_competing_pdf_read_failure_defers_the_entire_comparison(
    failed_text: str | None, waiting: bool
) -> None:
    class PdfText(FakeTextExtractor):
        def extract_document(self, data: bytes) -> ExtractedDocument:
            if data == b"newer" and failed_text is not None:
                part = DocumentPart(
                    role="bill", losses=("PDF text extraction failed on 1 page(s)",)
                )
                return ExtractedDocument(
                    text=failed_text, selection=DocumentSelection(parts=(part,))
                )
            return ExtractedDocument(
                text=self.extract(data),
                selection=DocumentSelection(parts=(DocumentPart(role="bill"),)),
            )

    reads: list[str] = []

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        reads.append(url)
        return b"earlier" if url == EARLIER else b"newer"

    loader = TextLoader(
        {"source.test": download},
        PdfText(by_content={b"earlier": law(), b"newer": law(day=2, days=60)}, page_count=0),
        max_bytes=100_000,
    )
    document = TextDocument(
        url=EARLIER,
        kind="rcl",
        compare_versions=True,
        alternatives=(TextDocument(url=NEWER, kind="rcl"),),
    )

    resolved, file = loader.select_document(document)

    assert reads == [EARLIER, NEWER]
    trace = file.candidate_trace
    assert trace is not None, "the comparison retains the exact candidate decisions"
    if waiting:
        assert resolved == document and file.text is None and file.selection.state == "waiting"
        assert (trace.result, trace.reason) == ("waiting", "version_input_incomplete")
        assert [(row.phase, row.reason) for row in trace.dispositions] == [
            ("version_comparison", "version_comparison_deferred"),
            ("version_comparison", "pdf_page_extraction_failed"),
        ]
        assert trace.dispositions[1].observed_sha256 == hashlib.sha256(b"newer").hexdigest()
        assert [part.url for part in file.selection.parts] == [NEWER]
        assert [part.url for part in file.selection.observed_parts] == [EARLIER, NEWER]
        assert file.selection.policy_version == DOCUMENT_SELECTION_POLICY_VERSION
    else:
        assert resolved.url == NEWER
        assert (trace.result, trace.reason) == ("selected", "version_choice")
