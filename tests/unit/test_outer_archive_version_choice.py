from unittest.mock import Mock

import pytest

from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.models import TextDocument
from lexinform.models import candidate_trace as trace_model
from lexinform.models.candidate_trace import VersionDecisionEvidence
from lexinform.services.documents import TextLoader
from tests.fakes import FakeSejmGateway, FakeTextExtractor
from tests.unit.test_archive_version_decision_writer import CONTENT
from tests.unit.test_physical_extraction import archive, docx, extractor

FIRST = "https://api.test/first"
SECOND = "https://api.test/second"


@pytest.mark.parametrize("unsupported", [False, True])
def test_outer_archive_choice_requires_exact_native_roots(
    monkeypatch: pytest.MonkeyPatch, unsupported: bool
) -> None:
    first = archive({"ustawa.docx": docx(CONTENT)})
    second = archive({"ustawa.docx": docx(CONTENT.replace("1 stycznia", "2 stycznia", 1))})
    gateway = FakeSejmGateway(files={FIRST: first, SECOND: second})
    reader = extractor()
    if unsupported:
        monkeypatch.setattr(reader, "extract_parts", Mock(wraps=reader.extract_parts))
    loader = TextLoader({"api.test": gateway.download}, reader, max_bytes=100_000)
    request = TextDocument(
        url=FIRST,
        kind="rcl",
        compare_versions=True,
        alternatives=(TextDocument(url=SECOND, kind="rcl"),),
    )

    selected, file = loader.select_document(request)
    cached_selected, cached = loader.select_document(request)

    trace = file.candidate_trace
    assert trace is not None, "the outer comparison retains its ordinary dispositions"
    assert selected.url == SECOND and cached_selected == selected and cached == file
    assert trace.archive_members is None
    if unsupported:
        assert trace.version_decision is None
        return
    assert trace.version_decision == VersionDecisionEvidence(
        compared_indices=(0, 1),
        retained_indices=(1,),
        outcome="single_survivor",
        date_filter=("2026-01-01", "2026-01-02"),
    )

    cap = len(trace.model_dump_json(exclude_defaults=True).encode()) - 1
    monkeypatch.setattr(trace_model, "MAX_CANDIDATE_TRACE_BYTES", cap)
    capped_selected, capped = loader.select_document(request)

    capped_trace = capped.candidate_trace
    assert capped_trace is not None, "the ordinary dispositions fit the reduced limit"
    assert capped_selected == selected and capped.selection == file.selection
    assert capped_trace.version_decision is None
    assert capped_trace.dispositions == trace.dispositions


@pytest.mark.parametrize("native_standalone", [True, False])
def test_mixed_choice_requires_the_standalone_extraction(native_standalone: bool) -> None:
    newer = CONTENT.replace("1 stycznia", "2 stycznia", 1)
    first = archive({"ustawa.docx": docx(CONTENT)})
    standalone = docx(newer) if native_standalone else b"%PDF-1.7 opaque fixture"
    gateway = FakeSejmGateway(files={FIRST: first, SECOND: standalone})
    reader = DocumentTextExtractor(
        FakeTextExtractor(newer, page_count=1), DocxTextExtractor(), FakeTextExtractor()
    )
    loader = TextLoader({"api.test": gateway.download}, reader, max_bytes=100_000)
    request = TextDocument(
        url=FIRST,
        kind="rcl",
        compare_versions=True,
        alternatives=(TextDocument(url=SECOND, kind="rcl"),),
    )

    selected, file = loader.select_document(request)
    cached_selected, cached = loader.select_document(request)

    assert selected.url == SECOND and cached_selected == selected and cached == file
    trace = file.candidate_trace
    assert trace is not None, "the exact comparison ran even when a receipt cannot be kept"
    assert trace.dispositions[0].reason == "version_preference"
    assert trace.version_decision == (
        VersionDecisionEvidence(
            compared_indices=(0, 1),
            retained_indices=(1,),
            outcome="single_survivor",
            date_filter=("2026-01-01", "2026-01-02"),
        )
        if native_standalone
        else None
    )
