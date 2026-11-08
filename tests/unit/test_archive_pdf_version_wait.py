import hashlib

import pytest

from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.models import PdfPageText, PdfTextExtraction, TextDocument
from lexinform.physical_observations import PhysicalObservationCollector
from lexinform.services.documents import TextLoader
from tests.fakes import FakeTextExtractor
from tests.scenario.test_document_selection_contract import law
from tests.unit.test_physical_extraction import archive

ROOT = "https://source.test/versions.zip"
ALTERNATIVE = "https://source.test/older.pdf"


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("compare_versions", [False, True])
def test_failed_pdf_inside_an_archive_defers_selection_of_the_draft(
    failed: bool, compare_versions: bool
) -> None:
    fault_active = failed

    class Pdf(FakeTextExtractor):
        def extract_pages(self, data: bytes) -> PdfTextExtraction:
            return PdfTextExtraction(
                input_sha256=hashlib.sha256(data).hexdigest(),
                pypdf_version="fixture",
                pages=(
                    PdfPageText(number=1, text=law(day=2 if data == b"%PDF-new" else 1)),
                    PdfPageText(
                        number=2,
                        error_type="ValueError" if fault_active and data == b"%PDF-new" else None,
                    ),
                ),
            )

    reader = DocumentTextExtractor(Pdf(page_count=2), DocxTextExtractor(), FakeTextExtractor())
    data = archive({"projekt-stary.pdf": b"%PDF-old", "projekt-nowy.pdf": b"%PDF-new"})
    downloads: list[str] = []

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        downloads.append(url)
        return data if url == ROOT else b"%PDF-old"

    loader = TextLoader(
        {"source.test": download},
        reader,
        max_bytes=100_000,
        physical_factory=PhysicalObservationCollector,
    )
    document = TextDocument(
        url=ROOT,
        kind="rcl",
        compare_versions=compare_versions,
        alternatives=(TextDocument(url=ALTERNATIVE, kind="rcl"),),
    )

    resolved, file = loader.select_document(document)

    expected_downloads = [ROOT, ALTERNATIVE] if compare_versions else [ROOT]
    assert resolved.url == ROOT and downloads == expected_downloads
    assert loader.select_document(document) == (resolved, file)
    assert downloads == expected_downloads
    if failed:
        assert file.selection.state == "waiting" and file.text is None
        assert file.selection.has_normative_pdf_page_failure
        assert len(file.selection.parts) == 2
        trace = file.candidate_trace
        assert trace is not None and trace.archive_members is not None
        assert trace.archive_version_conflict is None
        decisions = {
            row.leaf_sha256: row for row in trace.archive_members.rows if row.stage == "policy"
        }
        for data, reason in (
            (b"%PDF-old", "archive_comparison_deferred"),
            (b"%PDF-new", "archive_pdf_page_failure"),
        ):
            row = decisions[hashlib.sha256(data).hexdigest()]
            assert row.outcome == "unclassified" and row.reason == reason
    else:
        assert file.text is not None and "2 września" in file.text

    if failed:
        fault_active = False
        restarted = TextLoader({"source.test": download}, reader, max_bytes=100_000)
        recovered, readable = restarted.select_document(document)
        assert recovered.url == ROOT
        assert readable.text is not None and "2 września" in readable.text
        assert downloads == expected_downloads * 2


@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.parametrize("same_folder", [False, True])
def test_unreadable_named_pdf_only_defers_its_own_archive_family(
    same_folder: bool, confirmed: bool
) -> None:
    readable = law() if confirmed else "Treść załączonego pliku. " * 20

    class Pdf(FakeTextExtractor):
        def extract_pages(self, data: bytes) -> PdfTextExtraction:
            failed = data == b"%PDF-new"
            return PdfTextExtraction(
                input_sha256=hashlib.sha256(data).hexdigest(),
                pypdf_version="fixture",
                pages=(
                    PdfPageText(
                        number=1,
                        text="" if failed else readable,
                        error_type="ValueError" if failed else None,
                    ),
                ),
            )

    reader = DocumentTextExtractor(Pdf(page_count=1), DocxTextExtractor(), FakeTextExtractor())
    folder = "draft" if same_folder else "other"
    data = archive(
        {"draft/projekt-stary.pdf": b"%PDF-old", f"{folder}/projekt-nowy.pdf": b"%PDF-new"}
    )

    loaded = reader.extract_document_with_provenance(data).document

    assert (loaded.selection.state == "waiting") == same_folder
    if same_folder:
        assert not loaded.text
        assert loaded.selection.parts[-1].role == "unknown"
        assert loaded.selection.parts[-1].has_pdf_page_failure
    else:
        assert readable.strip() in loaded.text


@pytest.mark.parametrize(
    ("name", "error", "expected"),
    [
        ("projekt.pdf", "ValueError", "waiting"),
        ("projekt.pdf", None, "unsupported"),
        ("uzasadnienie.pdf", "ValueError", "unsupported"),
    ],
)
def test_unreadable_only_named_draft_waits_after_explicit_page_failure(
    name: str, error: str | None, expected: str
) -> None:
    class Pdf(FakeTextExtractor):
        def extract_pages(self, data: bytes) -> PdfTextExtraction:
            return PdfTextExtraction(
                input_sha256=hashlib.sha256(data).hexdigest(),
                pypdf_version="fixture",
                pages=(PdfPageText(number=1, error_type=error),),
            )

    reader = DocumentTextExtractor(Pdf(page_count=1), DocxTextExtractor(), FakeTextExtractor())
    data = archive({name: b"%PDF-draft"})

    loaded = reader.extract_document_with_provenance(data).document

    assert loaded.selection.state == expected
    if expected == "waiting":
        (part,) = loaded.selection.parts
        assert part.role == "unknown" and part.has_pdf_page_failure
        assert not loaded.text


@pytest.mark.parametrize("recognised", [False, True])
def test_partially_read_named_draft_has_one_physical_slot(recognised: bool) -> None:
    text = law() if recognised else "Treść załączonego pliku. " * 20

    class Pdf(FakeTextExtractor):
        def extract_pages(self, data: bytes) -> PdfTextExtraction:
            return PdfTextExtraction(
                input_sha256=hashlib.sha256(data).hexdigest(),
                pypdf_version="fixture",
                pages=(
                    PdfPageText(number=1, text=text),
                    PdfPageText(number=2, error_type="ValueError"),
                ),
            )

    reader = DocumentTextExtractor(Pdf(page_count=2), DocxTextExtractor(), FakeTextExtractor())

    loaded = reader.extract_document_with_provenance(
        archive({"projekt.pdf": b"%PDF-draft"})
    ).document

    assert loaded.selection.state == "waiting" and not loaded.text
    (part,) = loaded.selection.parts
    assert part.role == ("bill" if recognised else "unknown") and part.has_pdf_page_failure
    (observed,) = loaded.selection.observed_parts
    assert observed.sha256 == part.sha256


@pytest.mark.parametrize(
    ("name", "error", "state"),
    [
        ("projekt.pdf", "ValueError", "waiting"),
        ("projekt.pdf", None, "unsupported"),
        ("pismo.pdf", "ValueError", "unsupported"),
    ],
)
def test_cover_letter_in_partially_read_draft_defers_selection(
    name: str, error: str | None, state: str
) -> None:
    class Pdf(FakeTextExtractor):
        def extract_pages(self, data: bytes) -> PdfTextExtraction:
            return PdfTextExtraction(
                input_sha256=hashlib.sha256(data).hexdigest(),
                pypdf_version="fixture",
                pages=(
                    PdfPageText(number=1, text="Posłowie wnoszą projekt ustawy."),
                    PdfPageText(number=2, error_type=error),
                ),
            )

    reader = DocumentTextExtractor(Pdf(page_count=2), DocxTextExtractor(), FakeTextExtractor())
    loaded = reader.extract_document_with_provenance(archive({name: b"%PDF-draft"})).document

    assert loaded.selection.state == state
    assert not loaded.text
    if state == "waiting":
        (part,) = loaded.selection.parts
        assert part.has_pdf_page_failure and part.role == "unknown"


@pytest.mark.parametrize(
    ("name", "state"), [("projekt.pdf", "waiting"), ("pismo.pdf", "unsupported")]
)
def test_archive_pdf_page_tree_failure_is_distinct_from_blank_pages(name: str, state: str) -> None:
    reader = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), FakeTextExtractor())

    loaded = reader.extract_document_with_provenance(
        archive({name: b"%PDF-1.7\ninvalid page tree"})
    ).document

    assert loaded.selection.state == state and not loaded.text
    (observed,) = loaded.selection.observed_parts
    assert observed.has_pdf_page_failure and observed.pages == 0


@pytest.mark.parametrize("failed_folder", ["b", "other"])
def test_named_pdf_failure_considers_every_confirmed_comparison_family(failed_folder: str) -> None:
    class Pdf(FakeTextExtractor):
        def extract_pages(self, data: bytes) -> PdfTextExtraction:
            pages = (
                (PdfPageText(number=1, error_type="ValueError"),)
                if data == b"%PDF-failed"
                else (PdfPageText(number=1, text=law(day=2 if data == b"%PDF-new" else 1)),)
            )
            return PdfTextExtraction(
                input_sha256=hashlib.sha256(data).hexdigest(), pypdf_version="fixture", pages=pages
            )

    reader = DocumentTextExtractor(Pdf(page_count=1), DocxTextExtractor(), FakeTextExtractor())
    data = archive(
        {
            "a/projekt.pdf": b"%PDF-old",
            "b/projekt.pdf": b"%PDF-new",
            f"{failed_folder}/projekt-dodatkowy.pdf": b"%PDF-failed",
        }
    )

    loaded = reader.extract_document_with_provenance(data).document

    if failed_folder == "b":
        assert loaded.selection.state == "waiting" and not loaded.text
        assert len(loaded.selection.parts) == 3
        (failed,) = tuple(part for part in loaded.selection.parts if part.has_pdf_page_failure)
        assert failed.members[-1] == "b/projekt-dodatkowy.pdf"
    else:
        assert "2 września" in loaded.text
