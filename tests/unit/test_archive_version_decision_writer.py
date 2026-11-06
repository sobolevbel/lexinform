from datetime import timedelta

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import CandidateDispositionTrace, TextDocument
from lexinform.physical_observations import PhysicalObservationCollector
from lexinform.services.document_history import DocumentSourceRead, record_source_read
from lexinform.services.documents import TextLoader
from lexinform.services.physical_history import PhysicalHistoryPersistence
from tests.fakes import FakeSejmGateway
from tests.unit.test_candidate_disposition_trace import NOW
from tests.unit.test_document_candidates import BILL
from tests.unit.test_physical_extraction import archive, docx, extractor

URL = "https://api.test/archive.zip"
CONTENT = (
    "Projekt z dnia 1 stycznia 2026 r.\n"
    + BILL
    + "Art. 2. Przepis stosuje się w postępowaniu. " * 12
)


def source_loader(peer: str) -> tuple[TextLoader, DocumentSourceRead]:
    body = archive({"a.docx": docx(CONTENT), "b.docx": docx(peer)})
    gateway = FakeSejmGateway(files={URL: body})
    loader = TextLoader(
        {"api.test": gateway.download},
        extractor(),
        max_bytes=100_000,
        physical_factory=PhysicalObservationCollector,
    )
    request = TextDocument(url=URL, kind="rcl", compare_versions=True)
    selected, file = loader.select_document(request)
    source = loader.capture_source_read(request, selected, file)
    assert isinstance(source, DocumentSourceRead), "the fixture retains its source selection"
    return loader, source


@pytest.mark.parametrize(
    ("peer", "order", "dates"),
    [
        (CONTENT, "selected_first", None),
        (
            CONTENT.replace("1 stycznia", "2 stycznia", 1),
            "peer_first",
            ("2026-01-01", "2026-01-02"),
        ),
        (CONTENT.replace("200 zł", "400 zł"), None, None),
    ],
)
def test_writes_only_successful_native_archive_comparison_after_physical_history(
    repo: SqliteBillRepository, peer: str, order: str | None, dates: tuple[str, str] | None
) -> None:
    loader, source = source_loader(peer)
    record_source_read(repo, source, term=10, number="archive", purpose="analysis", recorded_at=NOW)
    (observation,) = repo.list_source_observations(10, "archive")
    baseline = repo.list_candidate_disposition_traces(observation.observation_id)
    assert baseline == [source.candidate_trace]
    history = PhysicalHistoryPersistence(loader, loader.deferred_archive_traces)
    finished = NOW + timedelta(minutes=10)

    history.persist_source_history(repo, recorded_at=finished)

    traces = repo.list_candidate_disposition_traces(observation.observation_id)
    if order is None:
        assert traces == baseline
        return
    (extra,) = [trace for trace in traces if trace not in baseline]
    proof = extra.archive_version_decision
    assert proof is not None, "the supported native branch has persisted its exact peer"
    assert (proof.order, proof.date_filter) == (order, dates)
    (capture,) = repo.iter_physical_captures(proof.peer.root_sha256)
    assert (capture.capture_id, capture.recorded_at) == (proof.capture_id, finished)
    dump = repo.dump()
    history.persist_source_history(repo, recorded_at=finished + timedelta(days=1))
    assert repo.dump() == dump


def test_failed_optional_append_preserves_normal_physical_and_source_history(
    repo: SqliteBillRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    loader, source = source_loader(CONTENT)
    record_source_read(repo, source, term=10, number="archive", purpose="analysis", recorded_at=NOW)
    (observation,) = repo.list_source_observations(10, "archive")
    baseline = repo.list_candidate_disposition_traces(observation.observation_id)
    original = repo.save_candidate_disposition_trace

    def fail_after_save(observation_id: str, trace: CandidateDispositionTrace) -> None:
        original(observation_id, trace)
        raise RuntimeError("injected optional trace persistence failure")

    finished = NOW + timedelta(minutes=10)
    with monkeypatch.context() as patch:
        patch.setattr(repo, "save_candidate_disposition_trace", fail_after_save)
        PhysicalHistoryPersistence(loader, loader.deferred_archive_traces).persist_source_history(
            repo, recorded_at=finished
        )

    assert repo.list_candidate_disposition_traces(observation.observation_id) == baseline
    members = baseline[0].archive_members
    assert members is not None, "the ordinary trace contains its selected root"
    (capture,) = repo.iter_physical_captures(members.rows[0].root_sha256)
    assert capture.recorded_at == finished
