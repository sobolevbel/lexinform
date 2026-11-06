import datetime as dt
from contextlib import closing
from typing import Any

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
from lexinform.models.candidate_trace import MAX_CANDIDATE_TRACE_BYTES
from tests.unit.test_normative_annex_gap_reader import LEGACY_ID, legacy_trace, with_gap
from tests.unit.test_required_part_trace_reader import extended_raw

SHA = "a" * 64


def bind(trace: CandidateDispositionTrace, **changes: Any) -> CandidateDispositionTrace:
    return CandidateDispositionTrace.model_validate(
        {
            **trace.model_dump(),
            "required_part_gap": {
                "phase": "fallback",
                "index": 0,
                "required_index": 0,
                "reason": "returned_no_text",
                **changes,
            },
        }
    )


@pytest.mark.parametrize("compare", [True, False])
def test_terminal_gap_round_trips_for_each_selected_phase(compare: bool) -> None:
    legacy = legacy_trace(compare=compare)
    phase = "version_comparison" if compare else "fallback"
    trace = bind(legacy, phase=phase, file_sha256=SHA if compare else None)

    assert trace.required_part_gap is not None
    assert trace.required_part_gap.file_sha256 == (SHA if compare else None)
    assert trace.trace_id != legacy.trace_id
    assert CandidateDispositionTrace.model_validate_json(trace.model_dump_json()) == trace
    assert "required_part_gap" not in legacy.model_dump_json()
    if not compare:
        assert legacy.trace_id == LEGACY_ID


@pytest.mark.parametrize(
    "changes",
    [
        {"index": 1},
        {"index": True},
        {"phase": "version_comparison"},
        {"required_index": 1},
        {"required_index": "0"},
        {"reason": "no_returned_file"},
        {"reason": "download_size_limit", "file_sha256": SHA},
        {"file_sha256": "unknown"},
        {"text": "body"},
    ],
)
def test_gap_rejects_non_observed_or_unbound_facts(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        bind(legacy_trace(), **changes)


def test_gap_requires_the_selected_candidates_actual_required_url() -> None:
    alternative = TextDocument(
        url="https://official.test/alternative",
        kind="print",
        required_urls=("https://official.test/continuation?token=credential",),
    )
    requested = TextDocument(
        url="https://official.test/original",
        kind="print",
        required_urls=("https://official.test/continuation",),
        alternatives=(alternative,),
    )
    trace = CandidateDispositionTrace.from_decisions(
        requested,
        (
            CandidateDisposition(
                phase="fallback", index=0, outcome="read_failure", reason="read_error"
            ),
            CandidateDisposition(
                phase="fallback", index=1, outcome="selected", reason="first_acceptable_candidate"
            ),
        ),
        result="selected",
        reason="first_acceptable_candidate",
        selection_state="waiting",
    )

    with pytest.raises(ValueError, match="selected outer disposition"):
        bind(trace)
    with pytest.raises(ValueError, match="outside metadata admission"):
        bind(trace, index=1)


def test_gap_requires_its_own_terminal_waiting_state() -> None:
    with pytest.raises(ValueError, match="terminal waiting state"):
        bind(legacy_trace(state="ambiguous"))
    with pytest.raises(ValueError, match="terminal waiting state"):
        bind(with_gap(legacy_trace()))


def test_prior_required_exclusion_can_precede_but_cannot_claim_the_gap() -> None:
    raw = extended_raw()
    raw["selection_state"] = "waiting"
    prior = CandidateDispositionTrace.model_validate(raw)

    assert bind(prior, required_index=1).required_part_gap is not None
    with pytest.raises(ValueError, match="terminal required part gap"):
        bind(prior)


def test_gap_requires_retained_trace_with_space_in_the_existing_budget() -> None:
    seed = legacy_trace(padding="x").model_dump_json(exclude_defaults=True)
    tight = legacy_trace(padding="x" * (MAX_CANDIDATE_TRACE_BYTES - len(seed)))
    omitted = legacy_trace(padding="x" * MAX_CANDIDATE_TRACE_BYTES)

    with pytest.raises(ValueError, match="trace exceeds its metadata limit"):
        bind(tight)
    with pytest.raises(ValueError, match="retained outer request"):
        bind(omitted)


def test_sqlite_round_trip_keeps_old_and_new_and_rejects_wrong_required_index() -> None:
    legacy = legacy_trace()
    trace = bind(legacy, file_sha256=SHA)
    assert trace.requested_document is not None, "fixture keeps the actual required URL"
    selection = DocumentSelectionRecord.from_selection(DocumentSelection(state="waiting"))
    observation = SourceObservation(
        term=8,
        number="technical",
        purpose="analysis",
        document=trace.requested_document,
        selection_id=selection.selection_id,
        recorded_at=dt.datetime(2026, 11, 6, tzinfo=dt.UTC),
    )
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_source_observation(selection, observation)
        for value in (legacy, trace):
            repo.save_candidate_disposition_trace(observation.observation_id, value)
        dump = repo.dump()
        repo.restore(dump)
        assert set(repo.list_candidate_disposition_traces(observation.observation_id)) == {
            legacy,
            trace,
        }
        assert repo.get_candidate_disposition_trace(LEGACY_ID) == legacy
        with pytest.raises(ValueError, match="invalid_candidate_trace"):
            repo.restore(dump.replace('"required_index":0', '"required_index":1'))
        assert repo.dump() == dump
