import hashlib
import json
from unittest.mock import MagicMock

import pytest
from tools.replay_review_input import Provider, verify_review_input

from lexinform.adapters.llm_anthropic import AnthropicAnalyzer
from lexinform.adapters.llm_openai import OpenAiAnalyzer
from lexinform.models.batch import BatchRequest, DocumentReviewQuestion
from lexinform.models.reviews import ConsultationRow
from tests.scenario.test_document_reviews import FINAL, context


def review_request(provider: Provider, *, wrong_source_hash: bool = False) -> BatchRequest:
    response = context()
    comparison = context(FINAL + '\nZażółć „gęślą” i "cytat".', role="comparison_text").documents[0]
    comparison = comparison.model_copy(
        update={
            "identity": "comparison/retained",
            "url": "https://source.test/retained.pdf",
            "sha256": "a" * 64 if wrong_source_hash else comparison.sha256,
        }
    )
    fixture = response.model_copy(
        update={
            "documents": (*response.documents, comparison),
            "comparison_sha256": comparison.normalized_sha256,
        }
    )
    request = BatchRequest(
        custom_id="offline-review",
        term=10,
        number=fixture.number,
        question=DocumentReviewQuestion(kind="consultation_review", ctx=fixture),
    )
    if provider == "openai":
        return OpenAiAnalyzer(lambda: pytest.fail("SDK used"), model="gpt-6.1-sol").prepare_request(
            request
        )
    client = MagicMock()
    frozen = AnthropicAnalyzer(client, model="claude-sonnet-5").prepare_request(request)
    assert not client.mock_calls
    return frozen


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_frozen_review_maps_decoded_document_fields_in_order(provider: Provider) -> None:
    request = review_request(provider)
    question = request.question
    assert isinstance(question, DocumentReviewQuestion), "fixture prepares a review"

    proof = verify_review_input(request, provider)

    manifest = proof["request_manifest"]
    assert isinstance(manifest, dict), "proof includes the exact provider manifest"
    assert manifest["request_sha256"] == request.payload_sha256
    assert proof["documents"] == [
        {
            "pointer": f"/documents/{index}/text",
            "identity_sha256": hashlib.sha256(document.identity.encode()).hexdigest(),
            "role": document.role,
            "source_text_sha256": document.sha256,
            "span": [0, len(document.text)],
        }
        for index, document in enumerate(question.ctx.documents)
    ]
    assert all(document.text not in json.dumps(proof) for document in question.ctx.documents)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize(
    "control",
    [
        "wrong_context",
        "reordered",
        "role",
        "identity",
        "body",
        "extra_block",
        "duplicate_key",
        "sha",
    ],
)
def test_review_verifier_rejects_contradictory_frozen_evidence(
    provider: Provider, control: str
) -> None:
    request = review_request(provider)
    assert request.payload_json is not None, "fixture freezes the native provider envelope"
    payload = json.loads(request.payload_json)
    blocks = (
        payload["body"]["input"][1]["content"]
        if provider == "openai"
        else payload["params"]["messages"][0]["content"]
    )
    prompt = json.loads(blocks[0]["text"])
    if control == "wrong_context":
        prompt["title"] += " changed"
    if control == "reordered":
        prompt["documents"].reverse()
    if control in ("role", "identity", "body"):
        field = "text" if control == "body" else control
        prompt["documents"][1][field] = "changed"
    if control == "extra_block":
        blocks.append(blocks[0].copy())
    blocks[0]["text"] = json.dumps(prompt, ensure_ascii=False)
    if control == "duplicate_key":
        blocks[0]["text"] = '{"number":"ignored",' + blocks[0]["text"][1:]
    raw = json.dumps(payload, ensure_ascii=False)
    updated = request.model_copy(
        update={
            "payload_json": raw,
            "payload_sha256": (
                "a" * 64 if control == "sha" else hashlib.sha256(raw.encode()).hexdigest()
            ),
        }
    )

    with pytest.raises(ValueError):
        verify_review_input(updated, provider)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_review_verifier_rejects_matching_envelope_with_invalid_source_hash(
    provider: Provider,
) -> None:
    with pytest.raises(ValueError, match="recorded hash"):
        verify_review_input(review_request(provider, wrong_source_hash=True), provider)


def test_frozen_review_verifies_word_rows_and_aliases() -> None:
    fixture = context()
    response = fixture.documents[0]
    source = response.model_copy(
        update={
            "file_sha256": "d" * 64,
            "consultation_rows": (
                ConsultationRow(
                    locator="word-xml:word/document.xml:0/1/2",
                    label="1",
                    start=0,
                    end=len(response.text),
                ),
            ),
        }
    )
    alias = source.model_copy(update={"identity": "doc/2"})
    fixture = fixture.model_copy(update={"documents": (source, alias)})
    request = BatchRequest(
        custom_id="offline-word-review",
        term=10,
        number=fixture.number,
        question=DocumentReviewQuestion(kind="consultation_review", ctx=fixture),
    )
    frozen = OpenAiAnalyzer(lambda: pytest.fail("SDK used"), model="gpt-6.1-sol").prepare_request(
        request
    )

    proof = verify_review_input(frozen, "openai")

    assert proof["version"] == "review-provider-text-v2"
    fields = proof["documents"]
    assert isinstance(fields, list), "the replay verifier returns ordered document fields"
    assert [item["pointer"] for item in fields] == [
        "/documents/0/segments",
        "/documents/1/same_text_as",
    ]
    assert frozen.payload_json is not None, "the request is frozen before replay"
    payload = json.loads(frozen.payload_json)
    block = payload["body"]["input"][1]["content"][0]
    prompt = json.loads(block["text"])
    prompt["documents"][0]["segments"][0]["label"] = "2"
    block["text"] = json.dumps(prompt)
    encoded = json.dumps(payload)
    bad = frozen.model_copy(
        update={
            "payload_json": encoded,
            "payload_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        }
    )
    with pytest.raises(ValueError, match="differs from its question"):
        verify_review_input(bad, "openai")
