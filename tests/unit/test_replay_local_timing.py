from typing import Any

import pytest
from tools.replay_local_timing import packet, snapshot

from tests.unit.test_local_timing_reader import intent_for
from tests.unit.test_physical_extraction import docx


def plan() -> dict[str, Any]:
    return {
        "assets": {
            key: {
                "url": f"https://api.sejm.gov.pl/{key}.pdf",
                "owner": owner,
                "title": "Technical fixture",
                "kind": kind,
            }
            for key, owner, kind in [
                ("primary", "3039", "print"),
                ("supplement", "1319", "impact_assessment"),
                ("amendments", "2449", "committee_amendments"),
            ]
        },
        "options": {"model": "gpt-6.1-sol", "budget_chars": 120000, "triage_min_chars": 1000000},
    }


@pytest.mark.parametrize("purpose", ["name", "joint", "explanation", "act_application"])
def test_real_public_preparation_refuses_provider_and_preserves_queued_packet(purpose: str) -> None:
    data = {
        key: docx("Art. 1. Cudzoziemcy składają wniosek w terminie 30 dni. " * 40)
        for key in ("primary", "supplement", "amendments")
    }
    result = snapshot(
        plan(), data, {"id": purpose, "purpose": purpose, "mode": "fresh", "clock": "fixed"}
    )
    assert result["core"]["client_attempts"] == result["core"]["submit_attempts"] == 0
    assert result["core"]["stages"][0]["packets"]
    assert result["core"]["packet_immutability"]
    assert result["core"]["restore_exact"]


def test_existing_none_and_witness_remain_full_byte_exact_without_repreparation() -> None:
    result = snapshot(
        plan(),
        {key: b"unused" for key in ("primary", "supplement", "amendments")},
        {
            "id": "name_existing_witness",
            "purpose": "name",
            "mode": "existing_witness",
            "clock": "fixed",
        },
    )
    seeded = [
        stage for stage in result["core"]["stages"] if stage["phase"].startswith("preexisting_")
    ]
    assert len(seeded) == 2
    assert all(stage["stored_packet_exact"] and stage["prepare_calls"] == 0 for stage in seeded)
    assert all(stage["stored_packet_full_sha256"] for stage in seeded)


def test_planned_failed_read_is_an_observation_without_a_prepared_request() -> None:
    result = snapshot(
        plan(),
        {key: b"unused" for key in ("primary", "supplement", "amendments")},
        {"id": "failed", "purpose": "analysis", "mode": "failed_read", "clock": "fixed"},
    )
    assert not result["core"]["stages"][0]["packets"]
    assert result["core"]["downloads"]
    assert not result["core"]["prepared"]
    assert result["core"]["client_attempts"] == result["core"]["synchronous_attempts"] == 0


def test_fresh_binding_normalization_rejects_a_known_wrong_sha_without_mutating_legacy() -> None:
    original = intent_for("name")
    assert original.meta.request_sha256 is None
    assert packet(original)["meta_sha256"] != packet(original, fresh_origin=False)["meta_sha256"]
    assert original.meta.request_sha256 is None
    wrong = original.model_copy(
        update={"meta": original.meta.model_copy(update={"request_sha256": "f" * 64})}
    )
    with pytest.raises(ValueError, match="Stored request binding"):
        packet(wrong)


def test_persisted_review_context_has_no_invocation_read_or_second_work() -> None:
    data = {
        key: docx("Art. 1. Cudzoziemcy składają wniosek w terminie 30 dni. " * 40)
        for key in ("primary", "supplement", "amendments")
    }
    result = snapshot(
        plan(),
        data,
        {"id": "resume", "purpose": "consultation_review", "mode": "resume", "clock": "fixed"},
    )
    assert result["timing"][0]["initial_expected_read_class"] is None
    assert result["core"]["stages"][0]["read_metrics"]["cache_hits"] == 0
    assert len(result["core"]["stages"][0]["packets"]) == 1
