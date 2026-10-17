import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import pytest
from tools import probe_document_evidence as probe


def question() -> dict[str, Any]:
    return {
        "official_project": "123",
        "candidates": [
            {
                "id": "A",
                "official_project": "123",
                "blocks": [{"id": "A.head", "text": "USTAWA\nProjekt z dnia 1 maja 2026 r."}],
            },
            {
                "id": "B",
                "official_project": "456",
                "blocks": [{"id": "B.head", "text": "USTAWA\nProjekt z dnia 2 maja 2026 r."}],
            },
        ],
    }


def answer() -> probe.EvidenceAnswer:
    return probe.EvidenceAnswer(
        decision="prefer",
        preferred_ids=["A"],
        facts=[
            probe.CandidateFact(
                candidate_id=identity,
                role="bill",
                draft_date=f"2026-05-0{day}",
                evidence=[probe.Citation(block_id=f"{identity}.head", quote="USTAWA")],
            )
            for identity, day in (("A", 1), ("B", 2))
        ],
        coverage="not_assessed",
        reason="Officially linked bill",
    )


def test_evidence_cannot_quote_a_different_candidate() -> None:
    result = answer()
    result.facts[0].evidence = [probe.Citation(block_id="B.head", quote="USTAWA")]

    issues = probe.validate_answer(result, question())

    assert "invalid citation" in issues


def test_a_real_quote_does_not_override_a_foreign_project_link() -> None:
    result = answer()
    result.preferred_ids = ["B"]

    issues = probe.validate_answer(result, question())

    assert "foreign candidate preference" in issues


def test_research_requests_never_include_expected_answers() -> None:
    cases = [{"id": "case", "question": question(), "expected": {"secret_label": "A"}}]

    requests = probe.payload(cases, ["claude-sonnet-5-5"])

    assert "secret_label" not in str(requests)
    assert "expected" not in str(requests)


def test_quote_recovery_returns_the_original_span_without_rewriting_source() -> None:
    source = "Projekt\nUSTAWA\nz dnia 7 maja 2026 r.\nArt. 1. Termin wynosi 30 dni."

    span = probe.recover_quote_span(source, "USTAWA z dnia 7 maja 2026 r.")

    assert span is not None
    assert source[span[0] : span[1]] == "USTAWA\nz dnia 7 maja 2026 r."


@pytest.mark.parametrize(
    "quote",
    [
        "Termin wynosi 60 dni.",
        "Termin wynosi 30 miesięcy.",
        "Organ nie może odmówić.",
        "Art. 2. Termin wynosi 30 dni.",
        "Projekt z dnia 7 maja 2027 r.",
    ],
)
def test_quote_recovery_never_repairs_semantic_changes(quote: str) -> None:
    source = "Projekt z dnia 7 maja 2026 r.\nArt. 1. Termin wynosi 30 dni.\nOrgan może odmówić."

    assert probe.recover_quote_span(source, quote) is None


def test_quote_recovery_refuses_ambiguous_locations() -> None:
    assert probe.recover_quote_span("USTAWA\nUSTAWA", "USTAWA") is None


def test_assessment_rejects_labels_changed_after_submission(tmp_path: Path) -> None:
    probe.save(tmp_path / "cases.json", [{"expected": "A"}])
    probe.save(tmp_path / "requests.json", [])
    probe.save(
        tmp_path / "batch-intent.json",
        {
            "payload_sha256": hashlib.sha256((tmp_path / "requests.json").read_bytes()).hexdigest(),
            "labels_sha256": hashlib.sha256((tmp_path / "cases.json").read_bytes()).hexdigest(),
        },
    )
    probe.save(tmp_path / "cases.json", [{"expected": "B"}])

    with pytest.raises(RuntimeError, match="Precommitted labels changed"):
        probe.assess(tmp_path)


def test_truncated_thinking_output_is_charged_without_becoming_a_model_verdict(
    tmp_path: Path,
) -> None:
    probe.save(tmp_path / "cases.json", [{"id": "case", "group": "123", "variant": "base"}])
    probe.save(
        tmp_path / "requests.json",
        [{"custom_id": "m0-case", "params": {"model": "claude-haiku-5-5"}}],
    )
    probe.save(
        tmp_path / "batch-intent.json",
        {
            "payload_sha256": hashlib.sha256((tmp_path / "requests.json").read_bytes()).hexdigest(),
            "labels_sha256": hashlib.sha256((tmp_path / "cases.json").read_bytes()).hexdigest(),
        },
    )
    probe.save(
        tmp_path / "results.json",
        [
            {
                "custom_id": "m0-case",
                "result": {
                    "type": "succeeded",
                    "message": {
                        "stop_reason": "max_tokens",
                        "content": [{"type": "thinking", "signature": "test"}],
                        "usage": {"input_tokens": 100, "output_tokens": 800},
                    },
                },
            }
        ],
    )

    probe.assess(tmp_path)

    result = probe.read_json(tmp_path / "assessed.json")
    assert result["actual_cost_usd"] == pytest.approx(0.000205)
    assert result["counts"]["claude-haiku-5-5"]["truncated"] == 1
    assert result["rows"][0]["state"] == "invalid_output"
    assert "matches" not in result["rows"][0]


class FakeClient:
    def __init__(self, *, api_key: str, max_retries: int, timeout: int) -> None:
        self.messages = self
        self.batches = self
        self.creates = 0

    def count_tokens(self, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(input_tokens=100)

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.creates += 1
        raise RuntimeError("Submission outcome unknown")


def test_total_budget_is_checked_before_any_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe.save(tmp_path / "cases.json", [{"id": "case", "question": question()}])
    prior = tmp_path / "prior.json"
    probe.save(prior, {"actual_cost_usd": 1.9999})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-value")
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)

    with pytest.raises(RuntimeError, match="Budget refused before submission"):
        probe.submit(tmp_path, tmp_path / "unused", prior, ["claude-sonnet-5-5"], 0.5)

    assert not (tmp_path / "batch-intent.json").exists()


def test_uncertain_intent_cannot_be_resubmitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe.save(tmp_path / "cases.json", [{"id": "case", "question": question()}])
    prior = tmp_path / "prior.json"
    probe.save(prior, {"actual_cost_usd": 0.884275})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-value")
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)

    with pytest.raises(RuntimeError, match="Submission outcome unknown"):
        probe.submit(tmp_path, tmp_path / "unused", prior, ["claude-sonnet-5-5"], 0.5)
    intent = probe.read_json(tmp_path / "batch-intent.json")
    assert intent["state"] == "unknown"
    with pytest.raises(RuntimeError, match="Existing or uncertain intent"):
        probe.submit(tmp_path, tmp_path / "unused", prior, ["claude-sonnet-5-5"], 0.5)

    assert probe.read_json(tmp_path / "batch-intent.json") == intent


def test_another_uncertain_study_reserves_its_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe.save(tmp_path / "cases.json", [{"id": "case", "question": question()}])
    prior = tmp_path / "prior.json"
    probe.save(
        prior,
        {"actual_cost_usd": 1.2, "open_intents": [{"state": "unknown", "reserved_usd": 0.7999}]},
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-value")
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)

    with pytest.raises(RuntimeError, match="Budget refused before submission"):
        probe.submit(tmp_path, tmp_path / "unused", prior, ["claude-sonnet-5-5"], 0.5)

    assert not (tmp_path / "batch-intent.json").exists()
    assert probe.read_json(tmp_path / "budget.json")["prior_reserved_usd"] == 0.7999
