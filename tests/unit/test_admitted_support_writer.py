from collections import Counter
from hashlib import sha256

import pytest

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.archive_disposition_observations import complete_standalone_root
from lexinform.models import TextDocument
from lexinform.services.document_history import DocumentSourceRead
from lexinform.services.documents import TextLoader
from tests.fakes import FakeTextExtractor
from tests.unit.test_doc_note_extraction import source_notes
from tests.unit.test_doc_note_physical_parts import ole_document
from tests.unit.test_physical_extraction import archive, docx, extractor
from tests.unit.test_required_part_writer import BODY, MAIN, REQUIRED

SUPPORT = "UZASADNIENIE\n" + "Opis skutków wprowadzenia przepisu. " * 30


@pytest.mark.parametrize("native", [True, False])
def test_only_native_support_still_in_final_selection_is_recorded(native: bool) -> None:
    duplicate = REQUIRED + "-duplicate"
    bodies = (
        {MAIN: docx(BODY), REQUIRED: docx(SUPPORT)}
        if native
        else {MAIN: BODY.encode(), REQUIRED: SUPPORT.encode()}
    )
    bodies[duplicate] = bodies[REQUIRED]
    calls: Counter[str] = Counter()

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        calls[url] += 1
        return bodies[url]

    loader = TextLoader(
        {"source.test": download},
        extractor() if native else FakeTextExtractor(),
        max_bytes=100_000,
    )
    document = TextDocument(url=MAIN, kind="print", extra_urls=(REQUIRED, duplicate))
    selected, loaded = loader.select_document(document, retain_routine_trace=True)

    assert selected == document
    assert calls == {MAIN: 1, REQUIRED: 1, duplicate: 1}
    trace = loaded.candidate_trace
    assert trace is not None, "the caller retains the ordinary trace"
    if not native:
        assert trace.admitted_support is None
        return
    assert trace.admitted_support is not None
    (row,) = trace.admitted_support.rows
    assert row.extra_index == 0 and row.role == "justification"
    assert row.file_sha256 == sha256(bodies[REQUIRED]).hexdigest()
    assert [part.url for part in loaded.selection.parts] == [MAIN, REQUIRED]


def test_alternative_support_is_retained_only_for_the_resolved_source() -> None:
    alternative = MAIN + "-alternate"
    bodies = {MAIN: b"", alternative: docx(BODY), REQUIRED: docx(SUPPORT)}
    calls: Counter[str] = Counter()

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        calls[url] += 1
        return bodies[url]

    loader = TextLoader({"source.test": download}, extractor(), max_bytes=100_000)
    alternate = TextDocument(url=alternative, kind="print", extra_urls=(REQUIRED,))
    requested = TextDocument(url=MAIN, kind="print", alternatives=(alternate,))
    chosen, loaded = loader.select_document(requested, retain_routine_trace=True)

    analysis = loader.capture_source_read(requested, chosen, loaded)
    prefilter = loader.capture_source_read(
        requested, chosen, loaded, record_requested_document=True
    )

    assert chosen == alternate
    assert calls == {MAIN: 1, alternative: 1, REQUIRED: 1}
    assert isinstance(analysis, DocumentSourceRead)
    assert isinstance(prefilter, DocumentSourceRead)
    assert (analysis.document, prefilter.document) == (alternate, requested)
    assert analysis.selection == prefilter.selection
    assert (
        analysis.candidate_trace is not None
        and analysis.candidate_trace.admitted_support is not None
    )
    assert analysis.candidate_trace.admitted_support.index == 1
    assert prefilter.candidate_trace == analysis.candidate_trace.model_copy(
        update={"admitted_support": None}
    )


def test_unlocated_doc_support_requires_the_complete_exact_extraction() -> None:
    prefix = "UZASADNIENIE\r" + "Opis skutków przepisu. " * 20
    body = ole_document(*source_notes(main=prefix + "\x02\r", reference_cp=len(prefix)))
    digest = sha256(body).hexdigest()
    reader = DocumentTextExtractor(FakeTextExtractor(), DocxTextExtractor(), DocTextExtractor())
    observed = reader.extract_document_with_provenance(body)
    (summary,) = observed.result.selected_parts
    assert summary.format == "doc" and summary.coordinates == "unknown"
    assert not complete_standalone_root(observed, digest)
    assert complete_standalone_root(observed, digest, allow_unlocated_doc=True)
    (part,) = observed.document.selection.parts
    damaged = observed.model_copy(
        update={
            "document": observed.document.model_copy(
                update={
                    "selection": observed.document.selection.model_copy(
                        update={
                            "parts": (part.model_copy(update={"text_end": len(part.text) - 1}),)
                        }
                    )
                }
            )
        }
    )
    assert not complete_standalone_root(damaged, digest, allow_unlocated_doc=True)


def test_composed_support_keeps_every_included_archive_part_after_deduplication() -> None:
    alternate = MAIN + "-alternate"
    duplicate = REQUIRED + "-duplicate"
    zipped = archive(
        {
            "uzasadnienie.docx": docx(SUPPORT),
            "osr.docx": docx("OCENA SKUTKÓW REGULACJI\n" + "Koszt i zakres przepisu. " * 30),
        }
    )
    bodies = {MAIN: b"", alternate: docx(BODY), REQUIRED: zipped, duplicate: zipped}
    reads: list[str] = []

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        reads.append(url)
        return bodies[url]

    loader = TextLoader({"source.test": download}, extractor(), max_bytes=100_000)
    document = TextDocument(url=alternate, kind="print", extra_urls=(REQUIRED, duplicate))
    request = TextDocument(url=MAIN, kind="print", alternatives=(document,))

    selected, loaded = loader.select_document(request)

    assert selected == document
    assert reads == [MAIN, alternate, REQUIRED, duplicate]
    assert [part.url for part in loaded.selection.parts] == [alternate, REQUIRED, REQUIRED]
    trace = loaded.candidate_trace
    assert trace is not None and trace.composed_support is not None
    assert trace.admitted_support is None
    (row,) = trace.composed_support.rows
    assert (trace.composed_support.index, row.extra_index, row.first_part, row.part_count) == (
        1,
        0,
        1,
        2,
    )
    requested = loader.capture_source_read(
        request, selected, loaded, record_requested_document=True
    )
    assert isinstance(requested, DocumentSourceRead) and requested.candidate_trace is not None
    assert requested.candidate_trace.composed_support is None
