from collections import Counter
from hashlib import sha256

import pytest

from lexinform.models import TextDocument
from lexinform.services.document_history import DocumentSourceRead
from lexinform.services.documents import TextLoader
from tests.fakes import FakeTextExtractor
from tests.unit.test_physical_extraction import docx, extractor
from tests.unit.test_required_part_writer import BODY, MAIN, REQUIRED

ANNEX = "Załącznik nr 1 do projektu ustawy\n" + "Wykaz uprawnionych placówek. " * 30


@pytest.mark.parametrize("native", [True, False])
def test_only_native_annex_still_in_final_selection_is_recorded(native: bool) -> None:
    duplicate = REQUIRED + "-duplicate"
    bodies = (
        {MAIN: docx(BODY), REQUIRED: docx(ANNEX)}
        if native
        else {MAIN: BODY.encode(), REQUIRED: ANNEX.encode()}
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
    document = TextDocument(url=MAIN, kind="rcl", normative_urls=(REQUIRED, duplicate))
    selected, loaded = loader.select_document(document, retain_routine_trace=True)

    assert selected == document
    assert calls == {MAIN: 1, REQUIRED: 1, duplicate: 1}
    trace = loaded.candidate_trace
    assert trace is not None, "the caller retains the ordinary trace"
    if not native:
        assert trace.admitted_normative_annexes is None
        return
    assert trace.admitted_normative_annexes is not None
    (row,) = trace.admitted_normative_annexes.rows
    assert row.normative_index == 0 and row.observed_kind == "unknown"
    assert row.file_sha256 == sha256(bodies[REQUIRED]).hexdigest()
    assert [part.url for part in loaded.selection.parts] == [MAIN, REQUIRED]


def test_alternative_annex_is_retained_only_for_the_resolved_source() -> None:
    alternative = MAIN + "-alternate"
    bodies = {MAIN: b"", alternative: docx(BODY), REQUIRED: docx(ANNEX)}
    calls: Counter[str] = Counter()

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        calls[url] += 1
        return bodies[url]

    loader = TextLoader({"source.test": download}, extractor(), max_bytes=100_000)
    alternate = TextDocument(url=alternative, kind="rcl", normative_urls=(REQUIRED,))
    requested = TextDocument(url=MAIN, kind="rcl", alternatives=(alternate,))
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
        and analysis.candidate_trace.admitted_normative_annexes is not None
    )
    assert analysis.candidate_trace.admitted_normative_annexes.index == 1
    assert prefilter.candidate_trace == analysis.candidate_trace.model_copy(
        update={"admitted_normative_annexes": None}
    )
