from collections import Counter
from hashlib import sha256

import pytest

from lexinform.models import TextDocument
from lexinform.services.documents import TextLoader
from tests.fakes import FakeTextExtractor
from tests.unit.test_physical_extraction import docx, extractor
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
