import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from lexinform.adapters.candidate_trace_history import CandidateTraceRowError
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import CandidateDispositionTrace
from lexinform.models.candidate_trace import ArchiveVersionConflictEvidence
from lexinform.services.document_history import record_artifact_reads, record_source_read
from lexinform.services.physical_history import PhysicalHistoryPersistence
from tests.unit.test_archive_version_decision_writer import CONTENT, URL, source_loader
from tests.unit.test_candidate_disposition_trace import NOW

PARENT_MISMATCH = "candidate_trace_archive_conflict_parent_mismatch"


def conflict(
    repo: SqliteBillRepository,
) -> tuple[str, CandidateDispositionTrace, CandidateDispositionTrace]:
    loader, source = source_loader(CONTENT.replace("200 zł", "400 zł"))
    record_source_read(repo, source, term=10, number="archive", purpose="analysis", recorded_at=NOW)
    record_artifact_reads(repo, loader.read_artifact_reads(), recorded_at=NOW)
    PhysicalHistoryPersistence(loader).persist_source_history(repo, recorded_at=NOW)
    (observation,) = repo.list_source_observations(10, "archive")
    base = source.candidate_trace
    assert base is not None, "the conflicting input retains an outer trace"
    root = base.dispositions[0].observed_sha256
    assert root is not None, "the comparison actually read the archive body"
    (capture,) = repo.iter_physical_captures(root)
    trace = CandidateDispositionTrace.model_validate(
        {
            **base.model_dump(),
            "archive_version_conflict": ArchiveVersionConflictEvidence(
                capture_id=capture.capture_id, index=0, compared_slots=(0, 1)
            ),
        }
    )
    wrong = replace(source, document=source.document.model_copy(update={"url": URL + "?other"}))
    record_source_read(repo, wrong, term=10, number="other", purpose="analysis", recorded_at=NOW)
    (other,) = repo.list_source_observations(10, "other")
    with pytest.raises(CandidateTraceRowError, match=PARENT_MISMATCH):
        repo.save_candidate_disposition_trace(other.observation_id, trace)
    return observation.observation_id, base, trace


def test_conflict_proof_retains_original_fallback_and_restores(repo: SqliteBillRepository) -> None:
    observation_id, base, trace = conflict(repo)
    repo.save_candidate_disposition_trace(observation_id, trace)

    assert (trace.result, trace.reason, trace.selection_state) == (
        "fallback",
        "all_rejected",
        "ambiguous",
    )
    assert trace.dispositions == base.dispositions
    assert trace.archive_members == base.archive_members
    assert repo.get_candidate_disposition_trace(base.trace_id) == base
    assert repo.get_candidate_disposition_trace(trace.trace_id) == trace
    dump = repo.dump()
    repo.restore(dump)
    repo.restore(dump)
    assert repo.dump() == dump


@pytest.mark.parametrize("missing", ["artifact_reads", "physical_captures"])
def test_conflict_proof_rejects_missing_durable_parents(tmp_path: Path, missing: str) -> None:
    db = tmp_path / "conflict.db"
    with closing(SqliteBillRepository(db)) as repo:
        repo.migrate()
        observation_id, _, trace = conflict(repo)
        repo.save_candidate_disposition_trace(observation_id, trace)
        good = repo.dump()
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.execute(f"DELETE FROM {missing}")
        invalid = repo.dump()

        with pytest.raises(CandidateTraceRowError, match=PARENT_MISMATCH):
            repo.get_candidate_disposition_trace(trace.trace_id)
        repo.restore(good)
        with pytest.raises(CandidateTraceRowError, match=PARENT_MISMATCH):
            repo.restore(invalid)
        assert repo.dump() == good
