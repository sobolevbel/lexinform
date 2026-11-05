"""Resolve review memo pointers with exact retained input and actual caller context."""

import hashlib
import json
from collections.abc import Sequence

from lexinform.json_storage import canonical_json, read_json
from lexinform.models import (
    ComputationResult,
    DocumentReviewRecord,
    ReviewContext,
    ReviewDocument,
    ReviewInputReference,
    ReviewResultRecord,
)
from lexinform.models.review_memos import ReviewMemoReference
from lexinform.models.reviews import ReviewPurpose


class ReviewMemoError(ValueError):
    def __init__(self) -> None:
        super().__init__("invalid_review_memo")


def _has_top_level_marker(raw: str) -> bool:
    if not raw.lstrip().startswith("{"):
        return False
    decoder = json.JSONDecoder()
    position, depth = 0, 0
    key_expected = False
    while position < len(raw):
        char = raw[position]
        if char == '"':
            try:
                value, end = decoder.raw_decode(raw, position)
            except ValueError:
                return False
            if depth == 1 and key_expected:
                following = end
                while following < len(raw) and raw[following].isspace():
                    following += 1
                if value == "modern_review_ref" and following < len(raw) and raw[following] == ":":
                    return True
                key_expected = False
            position = end
            continue
        if char in "{[":
            depth += 1
            if depth == 1:
                key_expected = True
        elif char in "}]":
            depth -= 1
        elif char == "," and depth == 1:
            key_expected = True
        position += 1
    return False


def read_review_memo(raw: object) -> ReviewMemoReference | None:
    if not isinstance(raw, str):
        if isinstance(raw, bytes) and _has_top_level_marker(raw.decode("utf-8", errors="replace")):
            raise ReviewMemoError()
        return None
    if ("modern_review_ref" not in raw and "\\u" not in raw) or not _has_top_level_marker(raw):
        return None
    try:
        value = read_json(raw)
        if len(raw.encode()) > 4096:
            raise ReviewMemoError()
        return ReviewMemoReference.model_validate(value)
    except ValueError, TypeError, RecursionError:
        raise ReviewMemoError() from None


def review_record_sha256(result: ComputationResult) -> str:
    """Version1 hashes canonical saved schema3 record fields, excluding future unset defaults."""
    if not isinstance(result.record, ReviewResultRecord):
        raise ReviewMemoError()
    return hashlib.sha256(
        canonical_json(result.record.model_dump(mode="json", exclude_unset=True)).encode()
    ).hexdigest()


def review_memo_parent(
    reference: ReviewMemoReference, result: ComputationResult | None
) -> ReviewResultRecord:
    if (
        result is None
        or result.result_id != reference.modern_review_ref
        or result.schema_version != 3
        or result.validation != "validated"
        or result.input_binding is None
        or result.review_input is None
        or not isinstance(result.record, ReviewResultRecord)
        or result.request_sha256 != reference.request_sha256
        or result.record.request_sha256 != result.request_sha256
        or review_record_sha256(result) != reference.retained_record_sha256
    ):
        raise ReviewMemoError()
    return result.record


def _review_text_matches(document: ReviewDocument, purpose: ReviewPurpose) -> bool:
    """Accept exact text or the commission producer's explicit unavailable-input sentinel."""
    if (
        purpose == "commission_review"
        and document.text == ""
        and document.sha256 == ""
        and document.normalized_sha256 == ""
        and document.complete is False
        and document.file_sha256 is None
    ):
        return True
    return hashlib.sha256(document.text.encode("utf-8")).hexdigest() == document.sha256


def matching_review(
    results: Sequence[ComputationResult], term: int, context: ReviewContext
) -> DocumentReviewRecord | None:
    try:
        if any(
            not _review_text_matches(document, context.purpose) for document in context.documents
        ):
            return None
    except UnicodeEncodeError:
        return None
    reference = ReviewInputReference.from_context(context, term=term)
    for result in reversed(results):
        binding = result.input_binding
        record = result.record
        if (
            result.schema_version != 3
            or result.validation != "validated"
            or (result.term, result.number, result.purpose)
            != (term, context.number, context.purpose)
            or binding is None
            or (binding.term, binding.number, binding.reuse_key)
            != (term, context.number, reference.key)
            or result.review_input != reference
            or not isinstance(record, ReviewResultRecord)
            or record.review.purpose != context.purpose
            or record.computation_id != result.result_id
            or result.request_sha256 is None
            or record.request_sha256 != result.request_sha256
        ):
            continue
        return DocumentReviewRecord(context=context, **record.model_dump())
    return None


def resolve_review_memo(
    reference: ReviewMemoReference, result: ComputationResult | None, context: ReviewContext
) -> DocumentReviewRecord:
    review_memo_parent(reference, result)
    if result is None or result.input_binding is None:
        raise ReviewMemoError()
    record = matching_review((result,), result.input_binding.term, context)
    if record is None:
        raise ReviewMemoError()
    projection = reference.projection
    update = projection.model_dump(exclude={"accounting"})
    if projection.accounting == "reuse":
        update.update(
            input_tokens=0,
            output_tokens=0,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
    return record.model_copy(update=update)
