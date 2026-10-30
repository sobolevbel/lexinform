"""Match recorded logical input scopes without changing paid results."""

import hashlib
from collections.abc import Sequence

from pydantic import BaseModel

from lexinform.models import (
    AmendmentsRecord,
    ComputationResult,
    DocumentReviewRecord,
    JointRecord,
    NameRecord,
    ReviewContext,
    ReviewDocument,
    ReviewInputReference,
    ReviewResultRecord,
    SupplementRecord,
)
from lexinform.models.batch import BatchKind
from lexinform.models.reviews import ReviewPurpose
from lexinform.ports import BillRepository


def matching_record[Record: BaseModel](
    results: Sequence[ComputationResult],
    term: int,
    number: str,
    purpose: BatchKind,
    request_key: str,
    record_model: type[Record],
) -> Record | None:
    for result in reversed(results):
        if result.purpose != purpose:
            continue
        binding = result.input_binding
        if binding is not None:
            matches = (binding.term, binding.number, binding.reuse_key) == (
                term,
                number,
                request_key,
            )
        else:
            matches = purpose in ("amendments", "supplement", "joint", "name") and (
                result.term,
                result.number,
                result.custom_id,
            ) == (term, number, request_key)
        if matches and isinstance(result.record, record_model):
            return result.record
    return None


def retained_secondary[Record: (AmendmentsRecord, SupplementRecord, JointRecord, NameRecord)](
    repo: BillRepository,
    term: int,
    number: str,
    purpose: BatchKind,
    request_key: str,
    record_model: type[Record],
) -> Record | None:
    return matching_record(
        repo.list_computation_results(term, number, purpose=purpose),
        term,
        number,
        purpose,
        request_key,
        record_model,
    )


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


def retained_review(
    repo: BillRepository, term: int, context: ReviewContext
) -> DocumentReviewRecord | None:
    return matching_review(
        repo.list_computation_results(term, context.number, purpose=context.purpose), term, context
    )
