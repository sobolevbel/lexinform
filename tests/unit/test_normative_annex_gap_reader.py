import datetime as dt
import json
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
from lexinform.models.candidate_trace import MAX_CANDIDATE_TRACE_BYTES, NormativeAnnexGap

FILE_SHA = "a" * 64
LEGACY_ID = "candidate-trace-v1:2b7e0170248ef58cba73e4d56369fda2b7bdde1ba28e4161ee4a80d95cee9909"


def legacy_trace(
    *,
    state: Literal["waiting", "ambiguous"] = "waiting",
    compare: bool = False,
    annex_url: str = "https://official.test/annex",
    padding: str = "",
) -> CandidateDispositionTrace:
    reason: Literal["version_choice", "first_acceptable_candidate"] = (
        "version_choice" if compare else "first_acceptable_candidate"
    )
    return CandidateDispositionTrace.from_decisions(
        TextDocument(
            url="https://official.test/main",
            kind="rcl",
            compare_versions=compare,
            required_urls=("https://official.test/required",),
            normative_urls=(annex_url,),
            version_basis=(padding,) if padding else (),
        ),
        (
            CandidateDisposition(
                phase="version_comparison" if compare else "fallback",
                index=0,
                outcome="selected",
                reason=reason,
            ),
        ),
        result="selected",
        reason=reason,
        selection_state=state,
    )


def with_gap(trace: CandidateDispositionTrace, **changes: Any) -> CandidateDispositionTrace:
    return CandidateDispositionTrace.model_validate(
        {
            **trace.model_dump(mode="json", exclude_defaults=True),
            "normative_annex_gap": {
                "phase": "fallback",
                "index": 0,
                "normative_index": 0,
                "reason": "no_returned_file",
                **changes,
            },
        }
    )


def test_historical_trace_keeps_id_and_omits_absent_gap() -> None:
    trace = legacy_trace()
    encoded = trace.model_dump_json(exclude_defaults=True)
    restored = CandidateDispositionTrace.model_validate(
        {**json.loads(encoded), "normative_annex_gap": None}
    )

    assert trace.trace_id == restored.trace_id == LEGACY_ID
    assert restored.model_dump_json(exclude_defaults=True) == encoded
    assert "normative_annex_gap" not in encoded


@pytest.mark.parametrize(
    ("state", "fields"),
    [
        ("waiting", {}),
        ("waiting", {"phase": "version_comparison", "reason": "returned_no_text"}),
        ("waiting", {"reason": "returned_no_text", "file_sha256": FILE_SHA}),
        (
            "ambiguous",
            {"reason": "non_annex_appendix", "kind": "regulation", "file_sha256": FILE_SHA},
        ),
    ],
)
def test_terminal_gap_round_trips_under_both_composition_phases(
    state: Literal["waiting", "ambiguous"], fields: dict[str, Any]
) -> None:
    legacy = legacy_trace(state=state, compare=fields.get("phase") == "version_comparison")
    trace = with_gap(legacy, **fields)
    raw = trace.model_dump_json(exclude_defaults=True)

    assert trace.trace_id != legacy.trace_id
    assert trace.normative_annex_gap is not None
    assert trace.normative_annex_gap.file_sha256 == fields.get("file_sha256")
    assert CandidateDispositionTrace.model_validate_json(raw) == trace


@pytest.mark.parametrize(
    "fields",
    [
        {"phase": "version_comparison"},
        {"index": 1},
        {"index": True},
        {"normative_index": "0"},
        {"normative_index": 1},
        {"file_sha256": FILE_SHA},
        {"reason": "returned_no_text", "file_sha256": "invalid"},
        {"reason": "returned_no_text", "kind": "regulation"},
        {"reason": "download_size_limit", "file_sha256": FILE_SHA},
        {"reason": "non_annex_appendix"},
        {"reason": "non_annex_appendix", "kind": "annex"},
        {"reason": "non_annex_appendix", "kind": "regulation"},
        {"reason": "missing_own_annex"},
        {"url": "https://official.test/annex"},
        {"text": "body"},
    ],
)
def test_gap_rejects_false_identity_or_non_observed_facts(fields: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        with_gap(legacy_trace(), **fields)


def test_gap_cannot_bind_to_an_attempted_but_unselected_parent() -> None:
    other = TextDocument(
        url="https://official.test/other",
        kind="rcl",
        normative_urls=("https://official.test/other-annex",),
    )
    requested = other.model_copy(
        update={"url": "https://official.test/main", "alternatives": (other,)}
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

    assert with_gap(trace, index=1).normative_annex_gap is not None
    with pytest.raises(ValueError, match="selected outer disposition"):
        with_gap(trace)


def test_gap_rejects_unadmitted_namespace_and_omitted_parent() -> None:
    with pytest.raises(ValueError, match="outside metadata admission"):
        with_gap(legacy_trace(annex_url="https://official.test/annex?token=credential"))
    omitted = legacy_trace(padding="x" * MAX_CANDIDATE_TRACE_BYTES)
    assert omitted.coverage == "omitted_over_limit"
    with pytest.raises(ValueError, match="retained outer request"):
        with_gap(omitted)


def test_gap_respects_existing_whole_trace_byte_cap() -> None:
    seed = legacy_trace(padding="x").model_dump_json(exclude_defaults=True)
    legacy = legacy_trace(padding="x" * (MAX_CANDIDATE_TRACE_BYTES - len(seed)))
    assert legacy.coverage == "complete"

    with pytest.raises(ValueError, match="trace exceeds its metadata limit"):
        with_gap(legacy)


def test_save_restore_preserves_legacy_and_exact_gap_and_rejects_corruption() -> None:
    legacy = legacy_trace()
    trace = with_gap(legacy, reason="returned_no_text", file_sha256=FILE_SHA)
    assert trace.requested_document is not None, "the fixture retains its composed request"
    selection = DocumentSelectionRecord.from_selection(DocumentSelection(state="waiting"))
    observation = SourceObservation(
        term=10,
        number="technical",
        purpose="analysis",
        document=trace.requested_document,
        selection_id=selection.selection_id,
        recorded_at=dt.datetime(2026, 11, 5, tzinfo=dt.UTC),
    )
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_source_observation(selection, observation)
        for value in (legacy, trace):
            repo.save_candidate_disposition_trace(observation.observation_id, value)
        original = repo.dump()
        repo.restore(original)
        repo.restore(original)
        assert repo.dump() == original
        assert repo.get_candidate_disposition_trace(LEGACY_ID) == legacy
        assert repo.get_candidate_disposition_trace(trace.trace_id) == trace
        assert len(repo.list_candidate_disposition_traces(observation.observation_id)) == 2
        corrupt = original.replace('"normative_index":0', '"normative_index":1')
        assert corrupt != original

        with pytest.raises(ValueError, match="invalid_candidate_trace"):
            repo.restore(corrupt)

        assert repo.dump() == original


def test_validated_gap_cannot_bypass_the_reader_with_model_copy() -> None:
    value = NormativeAnnexGap(
        phase="fallback", index=0, normative_index=0, reason="no_returned_file"
    )
    with pytest.raises(ValueError, match="known file hash"):
        CandidateDispositionTrace.model_validate(
            {
                **legacy_trace().model_dump(),
                "normative_annex_gap": value.model_copy(update={"file_sha256": FILE_SHA}),
            }
        )
