import pytest
from pydantic import ValidationError

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    AmendmentsRecord,
    ComputationResult,
    DocumentReviewRecord,
    SupplementRecord,
    computation_id,
)
from lexinform.models.batch import BatchKind
from tests.computations import KINDS, result_record, retained_result


@pytest.mark.parametrize("kind", KINDS)
def test_reader_preserves_each_normalized_result_across_dump_restore(kind: BatchKind) -> None:
    result = retained_result(kind)
    repository = SqliteBillRepository(":memory:")
    repository.migrate()
    repository.save_computation_result(result)
    repository.restore(repository.dump())

    loaded = repository.get_computation_result(result.result_id)

    assert loaded == result
    assert loaded is not None, "the repository retains the stored computation"
    assert loaded.record is not None and isinstance(loaded.record, type(result.record)), (
        "the result reader preserves its purpose-specific record type"
    )
    assert loaded.usage.batch_input == 50 and loaded.record.computation_id == loaded.result_id
    repository.close()


@pytest.mark.parametrize("kind", KINDS)
def test_legacy_record_has_no_invented_computation_ref(kind: BatchKind) -> None:
    record = result_record(kind)

    assert record.computation_id is None
    assert "computation_id" not in record.model_dump()


def test_primary_generation_and_legacy_format_are_not_reused_for_secondary() -> None:
    raw = retained_result("amendments").model_dump(mode="json")
    raw["schema_version"] = 1
    with pytest.raises(ValidationError, match="secondary"):
        ComputationResult.model_validate(raw)
    raw = retained_result("analysis").model_dump(mode="json")
    raw["generation"] = None
    with pytest.raises(ValidationError, match="generation"):
        ComputationResult.model_validate(raw)


def test_result_record_cannot_change_purpose_or_review_context() -> None:
    raw = retained_result("amendments").model_dump(mode="json")
    raw["purpose"] = "joint"
    with pytest.raises(ValidationError, match="purpose"):
        ComputationResult.model_validate(raw)
    review = retained_result("commission_review")
    record = review.record
    assert isinstance(record, DocumentReviewRecord), "the commission fixture has its review record"
    changed = record.model_copy(
        update={"context": record.context.model_copy(update={"purpose": "act_application"})}
    )
    with pytest.raises(ValidationError, match="purpose"):
        ComputationResult.model_validate_json(
            review.model_copy(update={"record": changed}).model_dump_json()
        )


@pytest.mark.parametrize("kind", KINDS)
def test_reader_keeps_failed_result_usage_without_inventing_an_answer(kind: BatchKind) -> None:
    raw = retained_result(kind).model_dump(mode="json")
    raw.update(validation="error", record=None, error="provider result failed validation")

    result = ComputationResult.model_validate(raw)

    assert result.record is None and result.usage.batch_input == 50
    assert ComputationResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("field", ["computation_id", "request_sha256"])
def test_secondary_result_rejects_a_record_from_another_request(field: str) -> None:
    result = retained_result("amendments")
    record = result.record
    assert isinstance(record, AmendmentsRecord), "the amendments fixture has its normalized record"
    changed = record.model_copy(
        update={
            field: computation_id("openai", "another-batch", "amendments")
            if field == "computation_id"
            else "b" * 64
        }
    )

    with pytest.raises(ValidationError, match="computation|original request"):
        ComputationResult.model_validate_json(
            result.model_copy(update={"record": changed}).model_dump_json()
        )


def test_secondary_result_does_not_claim_a_primary_generation_or_a_missing_digest() -> None:
    raw = retained_result("supplement").model_dump(mode="json")
    raw["generation"] = 1
    with pytest.raises(ValidationError, match="generation"):
        ComputationResult.model_validate(raw)
    result = retained_result("supplement")
    record = result.record
    assert isinstance(record, SupplementRecord), "the supplement fixture has a parsed digest"
    changed = record.model_copy(update={"digest": None})
    with pytest.raises(ValidationError, match="digest"):
        ComputationResult.model_validate_json(
            result.model_copy(update={"record": changed}).model_dump_json()
        )
