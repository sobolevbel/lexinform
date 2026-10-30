from dataclasses import replace
from datetime import UTC, datetime
from typing import Never

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    CandidateDispositionTrace,
    DocumentSelection,
    DocumentSelectionRecord,
    TextDocument,
)
from lexinform.services.document_history import (
    DocumentSourceRead,
    PhysicalSourceRead,
    record_source_read,
)
from lexinform.services.documents import TextLoader
from tests.fakes import FakeSejmGateway, FakeTextExtractor
from tests.unit.test_physical_extraction import archive, docx, extractor

URL = "https://source.test/draft.docx"
UNSAFE = URL + "?token=PRIVATE"
TEXT = "USTAWA\nArt. 1. Cudzoziemiec sklada wniosek osobiscie. " * 30
NOW = datetime(2026, 10, 30, tzinfo=UTC)


def test_unsafe_unattempted_alternative_omits_ids_but_keeps_actual_physical_metadata(
    repo: SqliteBillRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def forbidden(*args: object, **kwargs: object) -> Never:
        calls.append("identity factory")
        raise RuntimeError("unsafe graph must be checked before an identity factory")

    monkeypatch.setattr(CandidateDispositionTrace, "from_decisions", forbidden)
    monkeypatch.setattr(DocumentSelectionRecord, "from_selection", forbidden)
    data = docx(TEXT)
    physical = extractor()
    expected = physical.extract_document_with_provenance(data).result
    gateway = FakeSejmGateway(files={URL: data})
    loader = TextLoader({"source.test": gateway.download}, physical, max_bytes=len(data))
    requested = TextDocument(
        url=URL, kind="print", alternatives=(TextDocument(url=UNSAFE, kind="print"),)
    )

    resolved, loaded = loader.select_document(requested)
    captured = loader.capture_source_read(requested, resolved, loaded)
    with repo.atomic():
        record_source_read(repo, captured, term=10, number="1", purpose="analysis", recorded_at=NOW)

    assert isinstance(captured, PhysicalSourceRead) and not calls
    assert gateway.calls == [f"download:{URL}"]
    assert loaded.text == physical.extract_document(data).text
    assert loaded.candidate_trace is None and len(captured.reads) == 1
    assert captured.extractions == (expected,)
    assert repo.get_extraction_result(expected.result_id) == expected
    assert repo.get_artifact_metadata(expected.artifact_sha256) is not None
    assert not repo.list_source_observations(10, "1") and "PRIVATE" not in repo.dump()
    diagnostic = loader.read_metadata_url_omissions()
    assert diagnostic is not None and diagnostic.total == 2
    assert diagnostic.reasons[0].reason == "unrecognized_query"
    assert not loaded.opaque_failure_prose


def test_safe_graph_keeps_previous_selection_identity_and_prefilter_catalog_context() -> None:
    data = docx(TEXT)
    gateway = FakeSejmGateway(files={URL: data})
    loader = TextLoader({"source.test": gateway.download}, extractor(), max_bytes=len(data))
    requested = TextDocument(url=URL, kind="print")
    resolved, loaded = loader.select_document(requested)
    previous = DocumentSourceRead(
        resolved,
        DocumentSelectionRecord.from_selection(loaded.selection),
        loader.artifacts_for_selection(loaded.selection),
        loader.reads_for_selection(loaded.selection),
        loader.extractions_for_selection(loaded.selection),
        loaded.candidate_trace,
    )

    captured = loader.capture_source_read(requested, resolved, loaded)

    assert captured == previous and isinstance(captured, DocumentSourceRead)
    assert captured.selection.selection_id == previous.selection.selection_id
    catalog = requested.model_copy(update={"extra_urls": (URL + "/not-read",)})
    prefilter = loader.capture_source_read(
        catalog, resolved, loaded, record_requested_document=True
    )
    assert isinstance(prefilter, DocumentSourceRead) and prefilter.document == catalog
    assert prefilter.selection == previous.selection
    assert loader.read_metadata_url_omissions() is None


def test_class_only_support_failure_keeps_safe_graph_and_business_strings() -> None:
    data = docx(TEXT)
    support = URL + "/missing"
    gateway = FakeSejmGateway(files={URL: data})
    loader = TextLoader({"source.test": gateway.download}, extractor(), max_bytes=len(data))
    requested = TextDocument(url=URL, kind="print", extra_urls=(support,))

    resolved, loaded = loader.select_document(requested)
    captured = loader.capture_source_read(requested, resolved, loaded)

    assert not loaded.opaque_failure_prose
    assert loaded.text == extractor().extract_document(data).text
    assert loaded.selection.rejected == (f"{support}: KeyError",)
    assert loaded.candidate_trace is None and isinstance(captured, DocumentSourceRead)
    assert captured.artifacts and captured.reads and captured.extractions
    assert loader.read_metadata_url_omissions() is None


def test_direct_review_keeps_known_physical_origin_when_metadata_selection_is_empty(
    repo: SqliteBillRepository,
) -> None:
    data = archive({})
    gateway = FakeSejmGateway(files={UNSAFE: data})
    loader = TextLoader({"source.test": gateway.download}, extractor(), max_bytes=len(data))
    requested = TextDocument(url=UNSAFE, kind="print")

    loaded = replace(loader.read_document(requested), selection=DocumentSelection())
    captured = loader.capture_source_read(requested, requested, loaded)
    with repo.atomic():
        record_source_read(repo, captured, term=10, number="1", purpose="review", recorded_at=NOW)

    assert not loaded.selection.parts and isinstance(captured, PhysicalSourceRead)
    assert len(captured.artifacts) == len(captured.extractions) == 1
    assert not captured.reads and not repo.list_source_observations(10, "1")
    assert repo.get_extraction_result(captured.extractions[0].result_id) == captured.extractions[0]


def test_warm_cache_rechecks_graph_fields_without_new_download_or_trace_counting() -> None:
    data = docx(TEXT)
    gateway = FakeSejmGateway(files={URL: data})
    loader = TextLoader({"source.test": gateway.download}, extractor(), max_bytes=len(data))
    requested = TextDocument(
        url=URL, kind="print", alternatives=(TextDocument(url=UNSAFE, kind="print"),)
    )
    for _ in range(2):
        resolved, loaded = loader.select_document(requested)
        assert loader.read_metadata_url_omissions() is None
        assert isinstance(
            loader.capture_source_read(requested, resolved, loaded), PhysicalSourceRead
        )
        diagnostic = loader.read_metadata_url_omissions()
        assert diagnostic is not None and diagnostic.total == 2
        loader.reset_download_diagnostics()
    assert gateway.calls == [f"download:{URL}"]


def test_failed_direct_selection_keeps_original_parser_exception_and_cause() -> None:
    cause = RuntimeError("parser cause")
    error = ValueError("PRIVATE parser detail")
    error.__cause__ = cause
    gateway = FakeSejmGateway(files={URL: b"complete"})
    loader = TextLoader(
        {"source.test": gateway.download}, FakeTextExtractor(error=error), max_bytes=1000
    )

    with pytest.raises(ValueError) as caught:
        loader.select_document(TextDocument(url=URL, kind="print"))

    assert caught.value is error and caught.value.__cause__ is cause
    assert len(loader.read_artifacts()) == len(loader.read_artifact_reads()) == 1
    assert loader.read_metadata_url_omissions() is None


def test_trace_keeps_closed_failure_facts_without_retaining_opaque_rejection_prose() -> None:
    gateway = FakeSejmGateway(files={URL: b"complete"})
    loader = TextLoader(
        {"source.test": gateway.download},
        FakeTextExtractor(error=ValueError("PRIVATE arbitrary parser detail")),
        max_bytes=1000,
    )
    requested = TextDocument(
        url=URL, kind="print", alternatives=(TextDocument(url=URL + "/missing", kind="print"),)
    )

    resolved, loaded = loader.select_document(requested)
    capture = loader.capture_source_read(requested, resolved, loaded)

    assert loaded.opaque_failure_prose and "PRIVATE" in loaded.selection.rejected[0]
    trace = loaded.candidate_trace
    assert trace is not None and trace.requested_document == requested
    assert all(item.outcome == "read_failure" for item in trace.dispositions)
    assert "PRIVATE" not in trace.model_dump_json() and "rejected" not in trace.model_dump()
    assert isinstance(capture, PhysicalSourceRead)
    diagnostic = loader.read_metadata_url_omissions()
    assert diagnostic is not None and diagnostic.total == 1
    assert diagnostic.reasons[0].reason == "opaque_failure_prose"
