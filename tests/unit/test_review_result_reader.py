import datetime as dt
import hashlib

import pytest
from pydantic import ValidationError

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    ComputationResult,
    DocumentReviewRecord,
    ReviewDocument,
    ReviewInputReference,
    ReviewResultRecord,
    computation_id,
)
from lexinform.models.reviews import ReviewPurpose
from tests.computations import retained_result

PURPOSES: tuple[ReviewPurpose, ...] = (
    "consultation_review",
    "commission_review",
    "act_application",
)
NOW = dt.datetime(2026, 10, 27, tzinfo=dt.UTC)


def compact_result(purpose: ReviewPurpose) -> dict[str, object]:
    record = retained_result(purpose).record
    assert isinstance(record, DocumentReviewRecord), (
        "the schema fixture has its full review context"
    )
    raw = retained_result(purpose).model_dump(mode="json")
    raw["schema_version"] = 3
    raw["review_input"] = {
        "key": record.context.memo_key(10),
        "term": 10,
        "number": "3039",
        "title": "Projekt",
        "purpose": purpose,
        "documents": [],
        "act_eli": None,
        "promulgation_date": None,
        "canonical_date": None,
        "comparison_sha256": None,
    }
    raw["record"] = {
        "review": record.review.model_dump(mode="json"),
        "model": record.model,
        "prompt_version": record.prompt_version,
        "created_at": NOW.isoformat(),
        "computation_id": computation_id("openai", "provider-batch", purpose),
        "request_sha256": "a" * 64,
        "input_tokens": 50,
        "output_tokens": 3,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    return raw


@pytest.mark.parametrize("purpose", PURPOSES)
def test_compact_review_result_reader_preserves_normalized_answer_and_input_identity(
    purpose: ReviewPurpose,
) -> None:
    result = ComputationResult.model_validate(compact_result(purpose))
    repository = SqliteBillRepository(":memory:")
    repository.migrate()
    repository.save_computation_result(result)

    repository.restore(repository.dump())

    assert repository.get_computation_result(result.result_id) == result
    assert result.record is not None, "the compact computation retains its validated answer"
    assert result.record.computation_id == result.result_id
    repository.close()


@pytest.mark.parametrize("purpose", PURPOSES)
def test_compact_review_cannot_be_silently_written_with_the_prior_reader_schema(
    purpose: ReviewPurpose,
) -> None:
    raw = compact_result(purpose)
    ComputationResult.model_validate(raw)
    raw["schema_version"] = 2

    with pytest.raises(ValidationError, match="compact.*schema"):
        ComputationResult.model_validate(raw)


@pytest.mark.parametrize("purpose", PURPOSES)
@pytest.mark.parametrize("number", ["3039", "RCL/12414100"])
def test_review_input_reference_keeps_source_metadata_and_unknowns_without_source_text(
    purpose: ReviewPurpose, number: str
) -> None:
    original = retained_result(purpose).record
    assert isinstance(original, DocumentReviewRecord), "the reader fixture has its original context"
    text = "Synthetic source text outside the normalized answer. " * 5000
    document = ReviewDocument(
        identity="source/1",
        url="https://official.test/one",
        role="commission_minutes",
        text=text,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        normalized_sha256=hashlib.sha256(" ".join(text.split()).encode()).hexdigest(),
        file_sha256="b" * 64,
        author="Committee",
        draft_date=dt.date(2026, 10, 1),
        body="Commission",
        forum="sejm",
    )
    absent = ReviewDocument(
        identity="source/2",
        url="https://official.test/missing",
        role="comparison_text",
        text="",
        sha256="",
        normalized_sha256="",
        complete=False,
    )
    context = original.context.model_copy(
        update={"number": number, "documents": (document, absent)}
    )

    reference = ReviewInputReference.from_context(context, term=10)

    assert reference.key == context.memo_key(10)
    assert reference.term == 10 and reference.number == number
    assert reference.purpose == purpose and len(reference.documents) == 2
    known, unknown = reference.documents
    assert known.model_dump() == document.model_dump(exclude={"text"})
    assert unknown.model_dump() == absent.model_dump(exclude={"text"})
    assert not unknown.complete and unknown.sha256 == "" and unknown.file_sha256 is None
    assert len(reference.model_dump_json()) < 2000 and text not in reference.model_dump_json()
    assert context.documents[0].text == text


@pytest.mark.parametrize("purpose", PURPOSES)
def test_failed_compact_review_preserves_usage_without_an_answer(purpose: ReviewPurpose) -> None:
    raw = compact_result(purpose)
    raw.update(record=None, validation="error", error="provider output could not be validated")

    result = ComputationResult.model_validate(raw)

    assert result.record is None and result.usage.batch_input == 50
    assert result.review_input is not None, "the failed answer retains its known original input"
    assert result.review_input.purpose == purpose
    assert ComputationResult.model_validate_json(result.model_dump_json()) == result


def test_compact_review_never_accepts_a_full_input_text_field() -> None:
    result = ComputationResult.model_validate(compact_result("commission_review"))
    reference = result.review_input
    assert reference is not None, "the validated compact review retains its original input"
    data = reference.model_dump(mode="json")
    data["text"] = "This input must not be copied into immutable result history"

    with pytest.raises(ValidationError, match="extra_forbidden"):
        ReviewInputReference.model_validate(data)


@pytest.mark.parametrize("purpose", PURPOSES)
def test_compact_review_keeps_original_paid_fields_without_projection_metadata(
    purpose: ReviewPurpose,
) -> None:
    record = retained_result(purpose).record
    assert isinstance(record, DocumentReviewRecord), (
        "the provider fixture retains a detailed review"
    )
    record = record.model_copy(
        update={"baseline_day": dt.date(2026, 10, 1), "source_key": "projection", "checked_at": NOW}
    )

    compact = ReviewResultRecord.from_record(record)

    assert compact.review == record.review
    assert compact.computation_id == record.computation_id
    assert compact.request_sha256 == record.request_sha256
    assert compact.model == record.model and compact.prompt_version == record.prompt_version
    assert compact.created_at == record.created_at
    assert (compact.input_tokens, compact.output_tokens) == (
        record.input_tokens,
        record.output_tokens,
    )
    assert "baseline_day" not in compact.model_dump() and "source_key" not in compact.model_dump()
    assert "context" not in compact.model_dump() and "checked_at" not in compact.model_dump()
    with pytest.raises(ValueError, match="known computation ID"):
        ReviewResultRecord.from_record(record.model_copy(update={"computation_id": None}))


@pytest.mark.parametrize("field,value", [("purpose", "joint"), ("number", "3040"), ("term", 11)])
def test_compact_review_input_must_belong_to_the_original_computation(
    field: str, value: str | int
) -> None:
    raw = compact_result("commission_review")
    ComputationResult.model_validate(raw)
    context = raw["review_input"]
    assert isinstance(context, dict), "the compact record contains an input reference"
    context[field] = value

    with pytest.raises(ValidationError, match="purpose|owner"):
        ComputationResult.model_validate(raw)


@pytest.mark.parametrize("purpose", PURPOSES)
def test_failed_review_without_legacy_context_preserves_unknown_provenance(
    purpose: ReviewPurpose,
) -> None:
    raw = compact_result(purpose)
    raw.pop("review_input")
    raw.update(record=None, validation="error", error="legacy input context is missing")

    result = ComputationResult.model_validate(raw)

    assert result.review_input is None and "review_input" not in result.model_dump()
    assert result.record is None and result.usage.batch_input == 50
