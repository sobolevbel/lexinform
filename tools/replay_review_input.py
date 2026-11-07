"""Verify ordered review-document text fields in a frozen provider request."""

import argparse
from pathlib import Path
from typing import Literal

from pydantic import JsonValue

from lexinform.adapters.input_receipts import capture_frozen_request_manifest, parse_batch_request
from lexinform.json_storage import canonical_json, read_json
from lexinform.models.batch import BatchRequest, DocumentReviewQuestion
from tools.corpus_artifacts import digest, read_artifact, write_json

Provider = Literal["openai", "anthropic"]


def _user_prompt(payload: JsonValue, provider: Provider) -> tuple[str, str]:
    if not isinstance(payload, dict):
        raise ValueError("Frozen review payload must be an object")
    container = "body" if provider == "openai" else "params"
    body = payload.get(container)
    if not isinstance(body, dict):
        raise ValueError("Frozen review provider body is missing")
    if provider == "openai":
        if set(payload) != {"custom_id", "method", "url", "body"}:
            raise ValueError("Unexpected OpenAI review envelope")
        messages = body.get("input")
        if not isinstance(messages, list) or len(messages) != 2:
            raise ValueError("OpenAI review requires one system and one user message")
        system, user = messages
        if not isinstance(system, dict) or system.get("role") != "system":
            raise ValueError("OpenAI review system message is missing")
        pointer, block_type = "/body/input/1/content/0/text", "input_text"
    else:
        if set(payload) != {"custom_id", "params"}:
            raise ValueError("Unexpected Anthropic review envelope")
        messages = body.get("messages")
        if not isinstance(messages, list) or len(messages) != 1:
            raise ValueError("Anthropic review requires one user message")
        (user,) = messages
        pointer, block_type = "/params/messages/0/content/0/text", "text"
    if not isinstance(user, dict) or user.get("role") != "user" or set(user) != {"role", "content"}:
        raise ValueError("Frozen review user message is invalid")
    blocks = user["content"]
    if not isinstance(blocks, list) or len(blocks) != 1:
        raise ValueError("Frozen review requires exactly one user text block")
    (block,) = blocks
    if (
        not isinstance(block, dict)
        or set(block) != {"type", "text"}
        or block["type"] != block_type
        or not isinstance(text := block["text"], str)
    ):
        raise ValueError("Frozen review user text block is invalid")
    return pointer, text


def verify_review_input(request: BatchRequest, provider: Provider) -> dict[str, object]:
    question = request.question
    if not isinstance(question, DocumentReviewQuestion):
        raise ValueError("A frozen document review question is required")
    context = question.ctx
    if (request.number, question.kind) != (context.number, context.purpose):
        raise ValueError("Frozen review question differs from its owner or purpose")
    payload = request.payload_json
    manifest = capture_frozen_request_manifest(request, provider)
    if payload is None or manifest is None:
        raise ValueError("Exact frozen provider request is unavailable")
    pointer, prompt = _user_prompt(read_json(payload), provider)
    canonical_context = canonical_json(context.model_dump(mode="json"))
    if canonical_json(read_json(prompt)) != canonical_context:
        raise ValueError("Frozen provider review context differs from its question")
    documents = []
    for index, document in enumerate(context.documents):
        checksum = digest(document.text.encode())
        if document.sha256 != checksum and (document.text or document.sha256 or document.complete):
            raise ValueError("Frozen review document text differs from its recorded hash")
        documents.append(
            {
                "pointer": f"/documents/{index}/text",
                "identity_sha256": digest(document.identity.encode()),
                "role": document.role,
                "source_text_sha256": checksum,
                "span": [0, len(document.text)],
            }
        )
    return {
        "version": "review-provider-text-v1",
        "coordinates": "decoded-review-json-field-python-string",
        "request_manifest": manifest.model_dump(mode="json"),
        "context_sha256": digest(canonical_context.encode()),
        "provider_prompt_pointer": pointer,
        "documents": documents,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("openai", "anthropic"), required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--request-file-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        raw = read_artifact(args.request)
        if digest(raw) != args.request_file_sha256:
            raise ValueError("Pinned review request file SHA-256 differs")
        proof = verify_review_input(parse_batch_request(raw.decode()), args.provider)
        write_json(args.output, proof)
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
