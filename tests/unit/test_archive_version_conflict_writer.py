from datetime import timedelta

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import TextDocument
from lexinform.physical_observations import PhysicalObservationCollector
from lexinform.services.document_history import (
    DocumentSourceRead,
    record_artifact_reads,
    record_source_read,
)
from lexinform.services.documents import TextLoader
from lexinform.services.physical_history import PhysicalHistoryPersistence
from tests.fakes import FakeSejmGateway
from tests.unit.test_archive_version_decision_writer import CONTENT, URL, source_loader
from tests.unit.test_candidate_disposition_trace import NOW
from tests.unit.test_physical_extraction import archive, docx, extractor

PEER = CONTENT.replace("200 zł", "400 zł")


def test_deferred_conflict_requires_the_exact_body_receipt(
    repo: SqliteBillRepository,
) -> None:
    loader, source = source_loader(PEER)
    record_source_read(repo, source, term=10, number="archive", purpose="analysis", recorded_at=NOW)
    (observation,) = repo.list_source_observations(10, "archive")
    history = PhysicalHistoryPersistence(loader, loader.deferred_archive_traces)
    finished = NOW + timedelta(minutes=10)

    history.persist_source_history(repo, recorded_at=finished)

    assert repo.list_candidate_disposition_traces(observation.observation_id) == [
        source.candidate_trace
    ]
    record_artifact_reads(repo, loader.read_artifact_reads(), recorded_at=finished)
    history.persist_source_history(repo, recorded_at=finished + timedelta(days=1))
    (proof,) = [
        row.archive_version_conflict
        for row in repo.list_candidate_disposition_traces(observation.observation_id)
        if row.archive_version_conflict is not None
    ]
    assert proof is not None, "the exact root body receipt now authorizes the queued proof"
    assert (proof.index, proof.compared_slots) == (0, (0, 1))
    baseline = source.candidate_trace
    assert baseline is not None, "the original ambiguous extraction has a base trace"
    root = baseline.dispositions[0].observed_sha256
    assert root is not None, "the base trace retains the exact ambiguous root"
    (capture,) = repo.iter_physical_captures(root)
    assert capture.recorded_at == finished


def test_multiple_conflicting_roots_do_not_arbitrarily_choose_one(
    repo: SqliteBillRepository,
) -> None:
    another = "https://api.test/another.zip"
    body = archive({"a.docx": docx(CONTENT), "b.docx": docx(PEER)})
    gateway = FakeSejmGateway(files={URL: body, another: body})
    loader = TextLoader(
        {"api.test": gateway.download},
        extractor(),
        max_bytes=100_000,
        physical_factory=PhysicalObservationCollector,
    )
    request = TextDocument(
        url=URL,
        kind="rcl",
        compare_versions=True,
        alternatives=(TextDocument(url=another, kind="rcl"),),
    )
    selected, file = loader.select_document(request)
    source = loader.capture_source_read(request, selected, file)
    assert isinstance(source, DocumentSourceRead), "the fixture retains its source observation"
    record_source_read(repo, source, term=10, number="archive", purpose="analysis", recorded_at=NOW)

    record_artifact_reads(repo, loader.read_artifact_reads(), recorded_at=NOW)
    PhysicalHistoryPersistence(loader, loader.deferred_archive_traces).persist_source_history(
        repo, recorded_at=NOW
    )

    assert loader.deferred_archive_traces() == ()
    (observation,) = repo.list_source_observations(10, "archive")
    (trace,) = repo.list_candidate_disposition_traces(observation.observation_id)
    assert [row.reason for row in trace.dispositions[:2]] == [
        "ambiguous_extraction",
        "ambiguous_extraction",
    ]
    assert trace.archive_version_conflict is None
