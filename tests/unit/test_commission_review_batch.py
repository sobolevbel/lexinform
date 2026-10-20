import json

import anthropic
import openai

from lexinform.adapters.llm_anthropic import AnthropicAnalyzer
from lexinform.adapters.llm_openai import OpenAiAnalyzer
from lexinform.models import BatchRequest, DocumentReview, ReviewContext
from lexinform.models.batch import DocumentReviewQuestion, batch_output_model


def unused_client() -> openai.OpenAI:
    raise AssertionError("batch preparation must not call the provider")


def request() -> BatchRequest:
    return BatchRequest(
        custom_id="commission",
        term=10,
        number="RCL/12405805",
        question=DocumentReviewQuestion(
            kind="commission_review",
            ctx=ReviewContext(
                purpose="commission_review",
                number="RCL/12405805",
                title="Projekt ustawy",
                documents=(),
            ),
        ),
    )


def test_openai_commission_batch_uses_its_own_prompt_and_evidence_schema() -> None:
    prepared = OpenAiAnalyzer(unused_client).prepare_request(request())
    assert prepared.payload_json is not None
    body = json.loads(prepared.payload_json)["body"]
    assert "complete response" in body["input"][0]["content"]
    assert (
        "comparison_evidence"
        in body["text"]["format"]["schema"]["properties"]["commission_findings"]["items"][
            "properties"
        ]
    )
    assert json.loads(body["input"][1]["content"][0]["text"])["purpose"] == "commission_review"
    assert batch_output_model("commission_review") is DocumentReview
    assert prepared.estimated_cost_usd > 0


def test_anthropic_commission_batch_uses_typed_output_without_a_model_call() -> None:
    client = anthropic.Anthropic(api_key="test")
    try:
        prepared = AnthropicAnalyzer(client).prepare_request(request())
    finally:
        client.close()
    assert prepared.payload_json is not None
    params = json.loads(prepared.payload_json)["params"]
    assert "complete response" in params["system"][0]["text"]
    assert "commission_findings" in params["output_config"]["format"]["schema"]["properties"]
    assert json.loads(params["messages"][0]["content"][0]["text"])["purpose"] == "commission_review"
    assert prepared.estimated_cost_usd > 0
