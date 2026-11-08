import sqlite3
from contextlib import closing

import pytest
from tools.source_history_doctor import candidate_trace_inventory

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    CandidateDispositionTrace,
    DocumentPart,
    DocumentSelection,
    TextDocument,
)
from lexinform.models.candidate_trace import (
    AdmittedSupport,
    AdmittedSupportPart,
    CandidateDisposition,
)
from lexinform.models.document_history import DocumentSelectionRecord, SourceObservation
from tests.unit.test_input_receipts import NOW
from tests.unit.test_required_part_writer import MAIN, REQUIRED

FILE_SHA = "a" * 64
TEXT_SHA = "b" * 64


@pytest.mark.parametrize("control", ["exact", "position", "hash", "role"])
def test_admitted_support_reader_preserves_legacy_and_binds_selection(control: str) -> None:
    document = TextDocument(url=MAIN, kind="print", extra_urls=(REQUIRED,))
    old = CandidateDispositionTrace.from_decisions(
        document,
        (
            CandidateDisposition(
                phase="fallback", index=0, outcome="selected", reason="first_acceptable_candidate"
            ),
        ),
        result="selected",
        reason="first_acceptable_candidate",
        selection_state="unverified",
    )
    before_json = old.model_dump_json(exclude_defaults=True)
    old_id = old.trace_id
    evidence = AdmittedSupport(
        phase="fallback",
        index=0,
        rows=(
            AdmittedSupportPart(
                extra_index=int(control == "position"),
                role="osr" if control == "role" else "justification",
                file_sha256="c" * 64 if control == "hash" else FILE_SHA,
                text_sha256=TEXT_SHA,
            ),
        ),
    )
    payload = {**old.model_dump(), "admitted_support": evidence}
    if control == "position":
        with pytest.raises(ValueError, match="extra URL namespace"):
            CandidateDispositionTrace.model_validate(payload)
        return
    trace = CandidateDispositionTrace.model_validate(payload)
    assert old.model_dump_json(exclude_defaults=True) == before_json and old.trace_id == old_id
    assert CandidateDispositionTrace.model_validate_json(before_json) == old
    assert "admitted_support" not in before_json
    assert not trace.is_routine_singleton
    assert trace.trace_id != old_id
    selection = DocumentSelectionRecord.from_selection(
        DocumentSelection(
            state="unverified",
            parts=(
                DocumentPart(
                    url=REQUIRED, role="justification", sha256=FILE_SHA, text_sha256=TEXT_SHA
                ),
            ),
        )
    )
    observation = SourceObservation(
        term=10,
        number="3039",
        purpose="analysis",
        document=document,
        selection_id=selection.selection_id,
        recorded_at=NOW,
    )
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_source_observation(selection, observation)
        repo.save_candidate_disposition_trace(observation.observation_id, old)
        baseline = repo.dump()
        if control != "exact":
            with pytest.raises(
                ValueError, match="candidate_trace_admitted_support_parent_mismatch"
            ):
                repo.save_candidate_disposition_trace(observation.observation_id, trace)
            assert repo.dump() == baseline
            return
        repo.save_candidate_disposition_trace(observation.observation_id, trace)
        updated = repo.dump()
        for _ in range(2):
            repo.restore(updated)
            assert repo.dump() == updated
            assert repo.get_candidate_disposition_trace(old_id) == old
            assert repo.get_candidate_disposition_trace(trace.trace_id) == trace

    with closing(sqlite3.connect(":memory:")) as snapshot:
        snapshot.executescript(baseline)
        assert "admitted_support" not in candidate_trace_inventory(snapshot)
    with closing(sqlite3.connect(":memory:")) as snapshot:
        snapshot.executescript(updated)
        report = candidate_trace_inventory(snapshot, include_gaps=True)

    assert report["findings"] == []
    assert report["admitted_support"] == {
        "trace_rows_with_evidence": 1,
        "retained_parts_by_role": {"justification": 1},
        "omitted_parts": 0,
    }

    assert report["gap_coverage"]["supporting_positions"] == {
        "denominator": "selected_candidate_extra_url_positions",
        "selected_traces_with_request": 2,
        "selected_traces_without_request": 0,
        "declared_positions": 2,
        "admitted_positions": 1,
        "excluded_positions": 0,
        "unknown_positions": 1,
    }
