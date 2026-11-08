import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from lexinform.adapters.candidate_trace_history import CandidateTraceRowError
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import CandidateDispositionTrace, TextDocument
from lexinform.models.candidate_trace import ArchiveVersionGroupEvidence
from lexinform.physical_observations import PhysicalObservationCollector
from lexinform.services.document_history import (
    DocumentSourceRead,
    record_artifact_reads,
    record_source_read,
)
from lexinform.services.documents import TextLoader
from lexinform.services.physical_history import PhysicalHistoryPersistence
from tests.fakes import FakeSejmGateway
from tests.unit.test_archive_version_decision_writer import CONTENT, URL
from tests.unit.test_candidate_disposition_trace import NOW
from tests.unit.test_physical_extraction import archive, docx, extractor


@pytest.mark.parametrize("conflict", [False, True])
def test_archive_version_group_requires_exact_physical_parents(
    tmp_path: Path, conflict: bool
) -> None:
    third = CONTENT.replace("200 zł", "400 zł") if conflict else CONTENT
    body = archive({"a.docx": docx(CONTENT), "b.docx": docx(CONTENT), "c.docx": docx(third)})
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
    base = source.candidate_trace
    assert base is not None, "the fixture retains the actual outer decision"
    parent = (
        next(row for row in base.dispositions if row.reason == "ambiguous_extraction")
        if conflict
        else next(row for row in base.dispositions if row.outcome == "selected")
    )
    assert parent.phase in ("version_comparison", "fallback"), "the outer attempt read the ZIP"
    assert parent.observed_sha256 is not None, "the outer attempt retains the raw archive hash"
    database = tmp_path / "archive-group.db"

    with closing(SqliteBillRepository(database)) as repo:
        repo.migrate()
        record_source_read(
            repo, source, term=10, number="archive", purpose="analysis", recorded_at=NOW
        )
        record_artifact_reads(repo, loader.read_artifact_reads(), recorded_at=NOW)
        PhysicalHistoryPersistence(loader).persist_source_history(repo, recorded_at=NOW)
        (observation,) = repo.list_source_observations(10, "archive")
        (capture,) = repo.iter_physical_captures(parent.observed_sha256)
        assert base.archive_members is not None, "the fixture retains archive policy rows"
        group_members = tuple(
            row
            for row in base.archive_members.rows
            if row.stage == "policy" and (row.phase, row.index) == (parent.phase, parent.index)
        )
        evidence = ArchiveVersionGroupEvidence(
            capture_id=capture.capture_id,
            phase=parent.phase,
            index=parent.index,
            candidate_slots=(0, 1, 2),
            members=group_members,
            retained_slots=(0, 1, 2),
            matching_slots=() if conflict else (0, 1, 2),
            selected_slot=None if conflict else 0,
            outcome="text_conflict" if conflict else "selected",
        )
        trace = CandidateDispositionTrace.model_validate(
            {**base.model_dump(), "archive_version_group": evidence}
        )
        repo.save_candidate_disposition_trace(observation.observation_id, trace)
        assert repo.get_candidate_disposition_trace(trace.trace_id) == trace
        good = repo.dump()
        repo.restore(good)
        assert repo.dump() == good
        with closing(sqlite3.connect(database)) as conn, conn:
            conn.execute("DELETE FROM artifact_reads")
        invalid = repo.dump()
        code = "candidate_trace_archive_group_parent_mismatch"
        with pytest.raises(CandidateTraceRowError, match=code):
            repo.get_candidate_disposition_trace(trace.trace_id)
        repo.restore(good)
        with pytest.raises(CandidateTraceRowError, match=code):
            repo.restore(invalid)
        assert repo.dump() == good
