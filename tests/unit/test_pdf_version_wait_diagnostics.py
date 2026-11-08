import datetime as dt
from contextlib import closing
from pathlib import Path

from tools.source_history_doctor import audit

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    CandidateDisposition,
    CandidateDispositionTrace,
    DocumentSelection,
    DocumentSelectionRecord,
    SourceObservation,
)
from tests.unit.test_pdf_version_wait_reader import waiting_trace


def test_candidate_gap_report_distinguishes_waiting_conflict_and_omitted_rows(
    tmp_path: Path,
) -> None:
    waiting = waiting_trace()
    document = waiting.requested_document
    assert document is not None, "the waiting trace retains its full request"
    omitted = CandidateDispositionTrace.model_validate(
        {
            **waiting.model_dump(),
            "coverage": "omitted_over_limit",
            "requested_document": None,
            "omitted_dispositions": len(waiting.dispositions),
            "dispositions": (),
        }
    )
    conflict = CandidateDispositionTrace.from_decisions(
        document,
        tuple(
            CandidateDisposition(
                phase=row.phase,
                index=row.index,
                outcome="policy_excluded",
                reason="version_conflict",
                observed_sha256=row.observed_sha256,
            )
            for row in waiting.dispositions
        ),
        result="ambiguous",
        reason="version_conflict",
        selection_state="ambiguous",
    )
    path = tmp_path / "state.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        for trace in (waiting, omitted, conflict):
            selection = DocumentSelectionRecord.from_selection(
                DocumentSelection(state=trace.selection_state)
            )
            observation = SourceObservation(
                term=10,
                number="technical",
                purpose="analysis",
                document=document,
                selection_id=selection.selection_id,
                recorded_at=dt.datetime(2026, 11, 8, tzinfo=dt.UTC),
            )
            repo.save_source_observation(selection, observation)
            repo.save_candidate_disposition_trace(observation.observation_id, trace)
    before = path.read_bytes()

    report = audit(path, include_candidate_gaps=True)

    assert not report["findings"]
    assert report["candidate_gap_coverage"]["version_comparison_wait"] == {
        "deferred_comparisons": 2,
        "deferred_without_dispositions": 1,
        "retained_pdf_failure_positions": 1,
        "version_conflicts": 1,
    }
    assert "candidate_gap_coverage" not in audit(path)
    assert path.read_bytes() == before

    with closing(SqliteBillRepository(path)) as repo:
        dump = repo.dump()
    restored = tmp_path / "restored.db"
    with closing(SqliteBillRepository(restored)) as repo:
        repo.migrate()
        repo.restore(dump)
        assert repo.dump() == dump

    restored_report = audit(restored, include_candidate_gaps=True)
    assert not restored_report["findings"]
    assert restored_report["candidate_gap_coverage"] == report["candidate_gap_coverage"]
