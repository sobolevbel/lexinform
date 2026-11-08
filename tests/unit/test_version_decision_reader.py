import json
from typing import Any

import pytest

from lexinform.models import (
    CandidateDisposition,
    CandidateDispositionTrace,
    TextDocument,
    candidate_trace,
)
from lexinform.models.candidate_trace import VersionDecisionEvidence, VersionPackageInput


def comparison(*, conflict: bool = False, chosen: int = 1) -> CandidateDispositionTrace:
    document = TextDocument(
        url="https://example.test/a.pdf",
        kind="rcl",
        compare_versions=True,
        alternatives=(
            TextDocument(url="https://example.test/b.pdf", kind="rcl"),
            TextDocument(url="https://example.test/c.pdf", kind="rcl"),
        ),
    )
    return CandidateDispositionTrace.from_decisions(
        document,
        (
            CandidateDisposition(
                phase="version_comparison",
                index=0,
                outcome="non_bill",
                reason="non_bill_body",
            ),
            *(
                CandidateDisposition(
                    phase="version_comparison",
                    index=index,
                    outcome="selected" if not conflict and index == chosen else "policy_excluded",
                    reason=(
                        "version_conflict"
                        if conflict
                        else "version_choice"
                        if index == chosen
                        else "version_preference"
                    ),
                )
                for index in (1, 2)
            ),
        ),
        result="ambiguous" if conflict else "selected",
        reason="version_conflict" if conflict else "version_choice",
        selection_state="ambiguous" if conflict else "selected",
    )


def attach(trace: CandidateDispositionTrace, evidence: dict[str, Any]) -> CandidateDispositionTrace:
    raw = json.loads(trace.model_dump_json(exclude_defaults=True))
    raw["version_decision"] = evidence
    return CandidateDispositionTrace.model_validate_json(json.dumps(raw))


def test_version_decision_retains_real_outer_positions_and_legacy_id() -> None:
    old = comparison()
    encoded = old.model_dump_json(exclude_defaults=True)
    new = attach(
        old,
        {
            "compared_indices": [1, 2],
            "retained_indices": [1],
            "outcome": "single_survivor",
            "date_filter": ["2026-09-02", "2026-09-01"],
        },
    )
    assert new.version_decision == VersionDecisionEvidence(
        compared_indices=(1, 2),
        retained_indices=(1,),
        outcome="single_survivor",
        date_filter=("2026-09-02", "2026-09-01"),
    )
    assert new.trace_id != old.trace_id
    restored = CandidateDispositionTrace.model_validate_json(
        new.model_dump_json(exclude_defaults=True)
    )
    assert restored == new
    assert (
        CandidateDispositionTrace.model_validate(
            json.loads(new.model_dump_json(exclude_defaults=True))
        )
        == new
    )
    assert restored.model_copy(update={"version_decision": None}).trace_id == old.trace_id
    assert old.model_dump_json(exclude_defaults=True) == encoded
    assert "version_decision" not in encoded


def test_package_evidence_requires_exact_ordered_source_hashes() -> None:
    trace = comparison()
    trace = trace.model_copy(
        update={
            "dispositions": (
                trace.dispositions[0],
                trace.dispositions[1].model_copy(update={"observed_sha256": "a" * 64}),
                trace.dispositions[2].model_copy(update={"observed_sha256": "b" * 64}),
            )
        }
    )
    first = VersionPackageInput(
        source_sha256="a" * 64, body_sha256="c" * 64, annex_text_sha256s=("d" * 64,)
    )
    second = first.model_copy(update={"source_sha256": "b" * 64})
    evidence = {
        "compared_indices": (1, 2),
        "retained_indices": (1, 2),
        "outcome": "matching_policy_identity",
    }

    restored = attach(
        trace, {**evidence, "package_inputs": [item.model_dump() for item in (first, second)]}
    )

    assert restored.version_decision is not None
    assert restored.version_decision.package_inputs == (first, second)
    with pytest.raises(ValueError, match="observed candidate sources"):
        attach(
            trace, {**evidence, "package_inputs": [item.model_dump() for item in (second, first)]}
        )
    with pytest.raises(ValueError, match="one input per compared candidate"):
        attach(trace, {**evidence, "package_inputs": [first.model_dump()]})


@pytest.mark.parametrize("conflict", [False, True])
def test_policy_identity_outcome_does_not_claim_a_date_exclusion(conflict: bool) -> None:
    trace = comparison(conflict=conflict)
    evidence = {
        "compared_indices": [1, 2],
        "retained_indices": [1, 2],
        "outcome": "distinct_policy_identity" if conflict else "matching_policy_identity",
    }

    result = attach(trace, evidence)

    assert result.version_decision is not None, "the supplied evidence is retained"
    assert result.version_decision.date_filter is None


@pytest.mark.parametrize(
    "changes",
    [
        {"compared_indices": [0, 1, 2], "retained_indices": [0, 1, 2]},
        {"compared_indices": [2, 1]},
        {"compared_indices": [1, 1]},
        {"compared_indices": [True, 2]},
        {"compared_indices": ["1", 2]},
        {"compared_indices": list(range(9))},
        {"retained_indices": [2, 1]},
        {"retained_indices": [1], "outcome": "single_survivor"},
        {"date_filter": ["2026-09-02", "2026-09-02"]},
        {"date_filter": ["2026-09-02"]},
        {"date_filter": ["2026-09-02T00:00:00", "2026-09-01"]},
        {"date_filter": [1771234567, 1771234568]},
        {"date_filter": ["2026-02-30", "2026-01-01"]},
        {"date_filter": ["2026-09-01", "2026-09-02"], "retained_indices": [1]},
        {"outcome": "distinct_policy_identity"},
        {"title": "unsupported"},
    ],
)
def test_version_decision_rejects_unproved_comparison_details(changes: dict[str, Any]) -> None:
    evidence = {
        "compared_indices": [1, 2],
        "retained_indices": [1, 2],
        "outcome": "matching_policy_identity",
        **changes,
    }

    with pytest.raises(ValueError):
        attach(comparison(), evidence)


def test_equivalent_candidates_require_first_retained_outer_position() -> None:
    with pytest.raises(ValueError, match="selected or rejected"):
        attach(
            comparison(chosen=2),
            {
                "compared_indices": [1, 2],
                "retained_indices": [1, 2],
                "outcome": "matching_policy_identity",
            },
        )


def test_attachment_overflow_does_not_require_changing_the_existing_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = comparison()
    encoded = old.model_dump_json(exclude_defaults=True)
    monkeypatch.setattr(candidate_trace, "MAX_CANDIDATE_TRACE_BYTES", len(encoded.encode()))

    with pytest.raises(ValueError, match="metadata limit"):
        attach(
            old,
            {
                "compared_indices": [1, 2],
                "retained_indices": [1, 2],
                "outcome": "matching_policy_identity",
            },
        )

    assert CandidateDispositionTrace.model_validate_json(encoded) == old


def test_version_result_does_not_override_later_bundle_waiting_state() -> None:
    old = comparison().model_copy(update={"selection_state": "waiting"})
    new = attach(
        old,
        {
            "compared_indices": [1, 2],
            "retained_indices": [1, 2],
            "outcome": "matching_policy_identity",
        },
    )
    assert new.selection_state == "waiting"


def test_version_detail_cannot_enrich_an_omitted_outer_trace() -> None:
    old = comparison()
    omitted = old.model_copy(
        update={
            "coverage": "omitted_over_limit",
            "requested_document": None,
            "omitted_dispositions": len(old.dispositions),
            "dispositions": (),
        }
    )
    with pytest.raises(ValueError, match="completed comparison phase"):
        attach(
            omitted,
            {
                "compared_indices": [1, 2],
                "retained_indices": [1, 2],
                "outcome": "matching_policy_identity",
            },
        )
