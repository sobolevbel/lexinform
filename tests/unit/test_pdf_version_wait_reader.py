import datetime as dt
from contextlib import closing
from typing import Any, Literal

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

IncompleteReason = Literal[
    "pdf_page_extraction_failed", "archive_member_unpack_failed", "normative_part_read_incomplete"
]
INCOMPLETE_REASONS: tuple[IncompleteReason, ...] = (
    "pdf_page_extraction_failed",
    "archive_member_unpack_failed",
    "normative_part_read_incomplete",
)


def waiting_trace(
    reason: IncompleteReason = "pdf_page_extraction_failed",
) -> CandidateDispositionTrace:
    document = TextDocument(
        url="https://official.test/earlier.pdf",
        kind="rcl",
        compare_versions=True,
        alternatives=(
            TextDocument(
                url="https://official.test/newer.pdf",
                kind="rcl",
                normative_urls=("https://official.test/annex.pdf",)
                if reason == "normative_part_read_incomplete"
                else (),
            ),
        ),
    )
    return CandidateDispositionTrace.from_decisions(
        document,
        (
            CandidateDisposition(
                phase="version_comparison",
                index=0,
                outcome="unclassified",
                reason="version_comparison_deferred",
                observed_sha256="a" * 64,
            ),
            CandidateDisposition(
                phase="version_comparison",
                index=1,
                outcome="unclassified",
                reason=reason,
                observed_sha256="b" * 64,
            ),
        ),
        result="waiting",
        reason="version_input_incomplete",
        selection_state="waiting",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"selection_state": "ambiguous"},
        {"result": "ambiguous", "reason": "version_conflict", "selection_state": "ambiguous"},
        {"reason": "all_rejected"},
    ],
)
def test_deferred_comparison_rejects_inconsistent_outcomes(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        CandidateDispositionTrace.model_validate({**waiting_trace().model_dump(), **changes})


@pytest.mark.parametrize("reason", INCOMPLETE_REASONS)
def test_deferred_comparison_requires_a_failed_read_and_its_hash(reason: IncompleteReason) -> None:
    trace = waiting_trace(reason)
    raw = trace.model_dump()
    first, failed = raw["dispositions"]
    with pytest.raises(ValueError, match="failed read without a winner"):
        CandidateDispositionTrace.model_validate(
            {**raw, "dispositions": (first, {**failed, "reason": "version_comparison_deferred"})}
        )
    with pytest.raises(ValueError, match="requires the observed body"):
        CandidateDispositionTrace.model_validate(
            {**raw, "dispositions": (first, {**failed, "observed_sha256": None})}
        )


def test_normative_read_failure_requires_a_declared_part() -> None:
    trace = waiting_trace("normative_part_read_incomplete")
    request = waiting_trace().requested_document
    assert request is not None, "the reader fixture retains both candidate URLs"

    with pytest.raises(ValueError, match="requires a declared part"):
        CandidateDispositionTrace.from_decisions(
            request,
            trace.dispositions,
            result="waiting",
            reason="version_input_incomplete",
            selection_state="waiting",
        )


@pytest.mark.parametrize("reason", INCOMPLETE_REASONS)
def test_deferred_comparison_survives_dump_restore(reason: IncompleteReason) -> None:
    trace = waiting_trace(reason)
    assert trace.requested_document is not None, "the waiting trace retains its request"
    selection = DocumentSelectionRecord.from_selection(DocumentSelection(state="waiting"))
    observation = SourceObservation(
        term=10,
        number="technical",
        purpose="analysis",
        document=trace.requested_document,
        selection_id=selection.selection_id,
        recorded_at=dt.datetime(2026, 11, 8, tzinfo=dt.UTC),
    )
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_source_observation(selection, observation)
        repo.save_candidate_disposition_trace(observation.observation_id, trace)
        dump = repo.dump()
        repo.restore(dump)
        assert repo.dump() == dump
        assert repo.get_candidate_disposition_trace(trace.trace_id) == trace
