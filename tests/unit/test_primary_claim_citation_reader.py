import datetime as dt
import hashlib
import json
from typing import Any

import pytest

from lexinform.adapters.computation_history import ComputationRowError, parse_computation_result
from lexinform.models import Analysis, AnalysisRecord, ComputationResult, TokenUsage, computation_id
from tests.fakes import make_analysis


def primary_result() -> ComputationResult:
    collected_at = dt.datetime(2026, 11, 8, tzinfo=dt.UTC)
    request = "a" * 64
    return ComputationResult(
        provider="openai",
        batch_id="batch",
        custom_id="item",
        purpose="analysis",
        term=10,
        number="1605",
        generation=1,
        collected_at=collected_at,
        request_sha256=request,
        validation="validated",
        record=AnalysisRecord(
            analysis=make_analysis(),
            model="model",
            prompt_version="fixture",
            input_chars=100,
            truncated=False,
            text_source="pdf",
            created_at=collected_at,
            computation_id=computation_id("openai", "batch", "item"),
            request_sha256=request,
        ),
        model="model",
        usage=TokenUsage(),
    )


def citation(result: ComputationResult) -> dict[str, Any]:
    record = result.record
    assert isinstance(record, AnalysisRecord), "the fixture is a retained primary result"
    return {
        "field": "key_changes",
        "index": 1,
        "claim_text_sha256": hashlib.sha256(record.analysis.key_changes[1].encode()).hexdigest(),
        "source_text_sha256": "b" * 64,
        "citation": {
            "document_id": "body",
            "locator": "Art. 6",
            "quote": "Ustawa wchodzi w życie.",
        },
    }


def test_optional_claim_citation_round_trips_without_changing_the_provider_schema() -> None:
    legacy = primary_result()
    raw = legacy.model_dump_json()
    payload = json.loads(raw)
    assert "claim_citations" not in payload
    assert parse_computation_result(raw).model_dump_json() == raw

    payload["claim_citations"] = [citation(legacy)]
    retained = parse_computation_result(json.dumps(payload))

    assert json.loads(retained.model_dump_json()) == payload
    assert retained.record == legacy.record
    assert "claim_citations" not in Analysis.model_json_schema()["properties"]


@pytest.mark.parametrize(
    "override",
    [
        {"index": True},
        {"index": 2},
        {"index": None},
        {"field": "summary"},
        {"claim_text_sha256": "c" * 64},
        {"source_text_sha256": "invalid"},
        {"citation": {"document_id": "body", "locator": "Art. 6", "quote": " "}},
    ],
)
def test_reader_rejects_malformed_or_misbound_claim_citation(override: dict[str, Any]) -> None:
    legacy = primary_result()
    payload = json.loads(legacy.model_dump_json())
    payload["claim_citations"] = [{**citation(legacy), **override}]

    with pytest.raises(ComputationRowError, match="invalid_computation_result"):
        parse_computation_result(json.dumps(payload))


def test_reader_accepts_distinct_citations_for_one_claim_and_rejects_repeated_entries() -> None:
    legacy = primary_result()
    payload = json.loads(legacy.model_dump_json())
    first = citation(legacy)
    second = {**first, "citation": {**first["citation"], "locator": "Art. 7"}}
    payload["claim_citations"] = [first, second]

    assert len(parse_computation_result(json.dumps(payload)).claim_citations) == 2

    payload["claim_citations"].append(first)
    with pytest.raises(ComputationRowError, match="invalid_computation_result"):
        parse_computation_result(json.dumps(payload))


def test_reader_rejects_claim_citations_without_an_original_request_hash() -> None:
    legacy = primary_result()
    payload = json.loads(legacy.model_dump_json())
    payload["claim_citations"] = [citation(legacy)]
    del payload["request_sha256"]
    del payload["record"]["request_sha256"]

    with pytest.raises(ComputationRowError, match="invalid_computation_result"):
        parse_computation_result(json.dumps(payload))
