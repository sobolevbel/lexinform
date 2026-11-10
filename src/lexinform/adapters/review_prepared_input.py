"""Check exact decoded review fields in a frozen provider request."""

from dataclasses import dataclass

from pydantic import JsonValue

from lexinform.adapters.review_prompts import review_prompt
from lexinform.json_storage import canonical_json, read_json
from lexinform.models.batch import (
    BatchItemMeta,
    BatchProvider,
    BatchRequest,
    DigestItemMeta,
    DocumentReviewQuestion,
    FrozenRequestManifest,
)
from lexinform.models.review_prepared_input import (
    MAX_REVIEW_PREPARED_DOCUMENTS,
    ReviewPreparedDocument,
    ReviewPreparedInputWitness,
    review_context_sha256,
)


@dataclass(frozen=True)
class ReviewProviderFields:
    context_sha256: str
    pointer: str
    documents: tuple[ReviewPreparedDocument, ...]
    document_fields: tuple[str, ...]


def _user_prompt(payload: JsonValue, provider: BatchProvider) -> tuple[str, str]:
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


def verify_review_provider_fields(
    request: BatchRequest, manifest: FrozenRequestManifest
) -> ReviewProviderFields:
    question = request.question
    if not isinstance(question, DocumentReviewQuestion):
        raise ValueError("A frozen document review question is required")
    context = question.ctx
    if (request.number, question.kind) != (context.number, context.purpose):
        raise ValueError("Frozen review question differs from its owner or purpose")
    payload = request.payload_json
    if payload is None:
        raise ValueError("Exact frozen provider request is unavailable")
    manifest.require_request(request.call_kind, request.payload_sha256)
    pointer, prompt = _user_prompt(read_json(payload), manifest.provider)
    expected = review_prompt(context, version=request.prompt_version)
    decoded = read_json(prompt)
    if canonical_json(decoded) != canonical_json(read_json(expected)):
        raise ValueError("Frozen provider review context differs from its question")
    if not isinstance(decoded, dict) or not isinstance(documents := decoded.get("documents"), list):
        raise ValueError("Frozen provider review document list is missing")
    document_fields: list[str] = []
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("Frozen provider review document is invalid")
        document_fields.append(
            "same_text_as"
            if "same_text_as" in document
            else "segments"
            if "segments" in document
            else "text"
        )
    return ReviewProviderFields(
        review_context_sha256(context),
        pointer,
        tuple(ReviewPreparedDocument.from_document(document) for document in context.documents),
        tuple(document_fields),
    )


def capture_submitted_review_input(
    request: BatchRequest,
    manifest: FrozenRequestManifest,
    queued: BatchItemMeta | DigestItemMeta,
    submitted: DigestItemMeta,
) -> ReviewPreparedInputWitness | None:
    question = request.question
    if not isinstance(question, DocumentReviewQuestion) or question.kind != "consultation_review":
        return None
    context = question.ctx
    binding = submitted.input_binding
    if (
        not isinstance(queued, DigestItemMeta)
        or binding is None
        or binding != queued.input_binding
        or (binding.term, binding.number) != (request.term, request.number)
        or submitted.memo_key != queued.memo_key
        or submitted.scan_input is not None
        or queued.scan_input is not None
        or submitted.request_sha256 != manifest.request_sha256
        or context != queued.review_context
        or context != submitted.review_context
        or not 1 <= len(context.documents) <= MAX_REVIEW_PREPARED_DOCUMENTS
    ):
        return None
    fields = verify_review_provider_fields(request, manifest)
    return ReviewPreparedInputWitness(
        term=request.term,
        number=request.number,
        request_sha256=manifest.request_sha256,
        manifest_sha256=manifest.manifest_sha256,
        context_sha256=fields.context_sha256,
        documents=fields.documents,
    )
