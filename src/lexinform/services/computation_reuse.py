"""Match recorded logical input scopes without changing paid results."""

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
    SupplementRecord,
)
from lexinform.models.batch import BatchKind
from lexinform.ports import BillRepository
from lexinform.review_memos import matching_review as matching_review
from lexinform.review_memos import read_review_memo, resolve_review_memo


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


def retained_review(
    repo: BillRepository,
    term: int,
    context: ReviewContext,
    *,
    government: bool = False,
    result_id: str | None = None,
) -> DocumentReviewRecord | None:
    selected = repo.get_computation_result(result_id) if result_id is not None else None
    candidates = (
        ([selected] if selected is not None else [])
        if result_id is not None
        else repo.list_computation_results(term, context.number, purpose=context.purpose)
    )
    local = matching_review(candidates, term, context)
    if local is not None or not government:
        return local
    seen: set[str] = set()
    for application in reversed(repo.list_government_review_applications(term, context.number)):
        if result_id is not None and application.result_id != result_id:
            continue
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


def review_memo(
    repo: BillRepository, term: int, context: ReviewContext, raw: str, *, government: bool = False
) -> DocumentReviewRecord:
    reference = read_review_memo(raw)
    if reference is None:
        return DocumentReviewRecord.model_validate_json(raw)
    result = repo.get_computation_result(reference.modern_review_ref)
    record = resolve_review_memo(reference, result, context)
    if result is None:
        raise ValueError("invalid_review_memo")
    if (result.term, result.number) != (term, context.number) and retained_review(
        repo, term, context, government=government, result_id=result.result_id
    ) is None:
        raise ValueError("invalid_review_memo")
    return record
