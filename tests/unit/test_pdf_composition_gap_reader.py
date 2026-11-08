import datetime as dt
from contextlib import closing

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    CandidateDisposition,
    CandidateDispositionTrace,
    DocumentSelection,
    DocumentSelectionRecord,
    SourceObservation,
    TextDocument,
)

FILE_SHA = "a" * 64


@pytest.mark.parametrize(
    ("gap_name", "index_name"),
    [
        ("required_part_gap", "required_index"),
        ("normative_annex_gap", "normative_index"),
        ("amendment_part_gap", "amendment_index"),
    ],
)
def test_pdf_page_failure_gap_requires_body_identity_and_survives_restore(
    gap_name: str, index_name: str
) -> None:
    document = TextDocument(
        url="https://official.test/main.pdf",
        kind="rcl",
        required_urls=("https://official.test/continuation.pdf",),
        normative_urls=("https://official.test/annex.pdf",),
        amendment_urls=("https://official.test/amendment.pdf",),
    )
    legacy = CandidateDispositionTrace.from_decisions(
        document,
        (
            CandidateDisposition(
                phase="fallback", index=0, outcome="selected", reason="first_acceptable_candidate"
            ),
        ),
        result="selected",
        reason="first_acceptable_candidate",
        selection_state="waiting",
    )
    gap = {
        "phase": "fallback",
        "index": 0,
        index_name: 0,
        "reason": "pdf_page_extraction_failed",
    }
    with pytest.raises(ValueError, match="requires the returned file hash"):
        CandidateDispositionTrace.model_validate({**legacy.model_dump(), gap_name: gap})
    trace = CandidateDispositionTrace.model_validate(
        {**legacy.model_dump(), gap_name: {**gap, "file_sha256": FILE_SHA}}
    )
    selection = DocumentSelectionRecord.from_selection(DocumentSelection(state="waiting"))
    observation = SourceObservation(
        term=10,
        number="technical",
        purpose="analysis",
        document=document,
        selection_id=selection.selection_id,
        recorded_at=dt.datetime(2026, 11, 8, tzinfo=dt.UTC),
    )

    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_source_observation(selection, observation)
        for row in (legacy, trace):
            repo.save_candidate_disposition_trace(observation.observation_id, row)
        original = repo.dump()
        repo.restore(original)
        assert repo.dump() == original
        assert set(repo.list_candidate_disposition_traces(observation.observation_id)) == {
            legacy,
            trace,
        }
        with pytest.raises(ValueError, match="invalid_candidate_trace"):
            repo.restore(original.replace(f'"{index_name}":0', f'"{index_name}":1'))
        assert repo.dump() == original
