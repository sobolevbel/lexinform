"""Match recorded logical input scopes without changing paid results."""

import hashlib
import re
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
    repo: BillRepository, term: int, context: ReviewContext, *, government: bool = False
) -> DocumentReviewRecord | None:
    local = matching_review(
        repo.list_computation_results(term, context.number, purpose=context.purpose), term, context
    )
    if local is not None or not government:
        return local
    seen: set[str] = set()
    for application in reversed(repo.list_government_review_applications(term, context.number)):
        target = application.target
        if (
            (application.term, application.number) != (term, context.number)
            or application.phase != "projection"
            or application.outcome != "applied"
            or application.generation is not None
            or target is None
            or target.kind != "document_review"
        ):
            raise ValueError("invalid_government_review_application")
        address = re.fullmatch(
            r"(consultation_review|commission_review|act_application)/"
            r"((?:review-input|document-review):[0-9a-f]{64})",
            target.ref,
        )
        if address is None:
            continue
        result = repo.get_computation_result(application.result_id)
        if result is None or result.purpose != address[1]:
            raise ValueError("invalid_government_review_application")
        if result.purpose != context.purpose or result.result_id in seen:
            continue
        seen.add(result.result_id)
        binding = result.input_binding
        if (
            binding is None
            or result.schema_version != 3
            or result.validation != "validated"
            or result.review_input is None
            or result.request_sha256 is None
        ):
            continue
        if binding.number != context.number or result.number != context.number:
            raise ValueError("invalid_government_review_application")
        if address[2].startswith("document-review:") and address[2] != binding.reuse_key:
            raise ValueError("invalid_government_review_application")
        matched = matching_review((result,), binding.term, context)
        if matched is not None:
            return matched
    return None
