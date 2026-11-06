import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import CandidateDispositionTrace, TextDocument
from lexinform.models import candidate_trace as trace_model
from lexinform.models.candidate_trace import ArchiveVersionConflictEvidence, VersionDecisionEvidence
from lexinform.physical_observations import PhysicalObservationCollector
from lexinform.services.document_history import (
    DocumentSourceRead,
    record_artifact_reads,
    record_source_read,
)
from lexinform.services.documents import TextLoader
from lexinform.services.physical_history import PhysicalHistoryPersistence
from tests.fakes import FakeSejmGateway
from tests.unit.test_archive_version_decision_writer import CONTENT
from tests.unit.test_candidate_disposition_trace import NOW
from tests.unit.test_physical_extraction import archive, docx, extractor

ROOT = "https://api.test"


def test_outer_archive_conflict_does_not_displace_nested_proof_at_cap(
    repo: SqliteBillRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = docx(CONTENT)
    second = docx(CONTENT.replace("200 zł", "400 zł"))
    gateway = FakeSejmGateway(
        files={
            f"{ROOT}/first.zip": archive({"a.docx": first}),
            f"{ROOT}/second.zip": archive({"b.docx": second}),
            f"{ROOT}/nested.zip": archive({"a.docx": first, "b.docx": second}),
        }
    )
    loader = TextLoader(
        {"api.test": gateway.download},
        extractor(),
        max_bytes=100_000,
        physical_factory=PhysicalObservationCollector,
    )
    request = TextDocument(
        url=f"{ROOT}/first.zip",
        kind="rcl",
        compare_versions=True,
        alternatives=(
            TextDocument(url=f"{ROOT}/second.zip", kind="rcl"),
            TextDocument(url=f"{ROOT}/nested.zip", kind="rcl"),
        ),
    )
    selected, file = loader.select_document(request)
    initial = loader.capture_source_read(request, selected, file)
    assert isinstance(initial, DocumentSourceRead), "the fixture retains its source selection"
    both_base = initial.candidate_trace
    nested = file.archive_version_evidence
    assert both_base is not None, "the fixture retains a complete candidate trace"
    assert isinstance(nested, ArchiveVersionConflictEvidence), "the third archive conflicts inside"
    assert both_base.version_decision == VersionDecisionEvidence(
        compared_indices=(0, 1), retained_indices=(0, 1), outcome="distinct_policy_identity"
    )
    both = CandidateDispositionTrace.model_validate(
        {**both_base.model_dump(), "archive_version_conflict": nested}
    )
    combined_bytes = len(both.model_dump_json(exclude_defaults=True).encode())
    monkeypatch.setattr(trace_model, "MAX_CANDIDATE_TRACE_BYTES", combined_bytes - 1)
    loader.reset_read_metrics()

    selected, file = loader.select_document(request)
    capped = loader.capture_source_read(request, selected, file)
    assert isinstance(capped, DocumentSourceRead), "the capped route retains its source selection"
    record_source_read(repo, capped, term=10, number="archive", purpose="analysis", recorded_at=NOW)
    record_artifact_reads(repo, loader.read_artifact_reads(), recorded_at=NOW)
    PhysicalHistoryPersistence(loader, loader.deferred_archive_traces).persist_source_history(
        repo, recorded_at=NOW
    )

    assert capped.selection == initial.selection
    (observation,) = repo.list_source_observations(10, "archive")
    traces = repo.list_candidate_disposition_traces(observation.observation_id)
    assert len(traces) == 2
    assert all(trace.version_decision is None for trace in traces)
    assert all(trace.dispositions == both_base.dispositions for trace in traces)
    assert [
        trace.archive_version_conflict for trace in traces if trace.archive_version_conflict
    ] == [nested]
