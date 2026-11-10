import pytest
from pydantic import ValidationError

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    BatchItemMeta,
    ComputationInputBinding,
    ComputationResult,
    DigestItemMeta,
    DocumentReviewRecord,
)
from lexinform.models.batch import BatchKind
from lexinform.models.reviews import ReviewPurpose
from tests.computations import KINDS, REVIEW_PURPOSES, retained_result
from tests.harness import RCL


@pytest.mark.parametrize("kind", KINDS)
def test_reader_keeps_known_input_binding_separate_from_collection_coordinates(
    kind: BatchKind,
) -> None:
    original = retained_result(kind)
    key = (
        original.record.context.memo_key(10)
        if isinstance(original.record, DocumentReviewRecord)
        else "original-question"
    )
    binding = ComputationInputBinding(
        term=10,
        number=RCL if kind in ("analysis", "reanalysis") else original.number,
        reuse_key=key,
    )
    result = ComputationResult.model_validate_json(
        original.model_copy(update={"input_binding": binding}).model_dump_json()
    )
    repository = SqliteBillRepository(":memory:")
    repository.migrate()
    repository.save_computation_result(result)
    repository.restore(repository.dump())

    loaded = repository.get_computation_result(result.result_id)

    assert loaded is not None, "the repository retains the explicitly saved future result"
    assert loaded == result and loaded.input_binding == binding
    assert loaded.number == original.number
    assert original.input_binding is None and "input_binding" not in original.model_dump()
    repository.close()


@pytest.mark.parametrize("purpose", REVIEW_PURPOSES)
def test_full_review_binding_keeps_its_recorded_key_without_recomputing_policy(
    purpose: ReviewPurpose,
) -> None:
    original = retained_result(purpose)
    binding = ComputationInputBinding(
        term=10, number="3039", reuse_key="recorded-historical-policy-key"
    )

    loaded = ComputationResult.model_validate_json(
        original.model_copy(update={"input_binding": binding}).model_dump_json()
    )

    assert loaded.input_binding == binding


@pytest.mark.parametrize("primary", [False, True])
def test_metadata_rejects_a_binding_with_a_different_frozen_question_key(primary: bool) -> None:
    metadata: BatchItemMeta | DigestItemMeta = (
        BatchItemMeta(
            input_chars=1,
            truncated=False,
            text_source="pdf",
            source_kind="print",
            source_url=None,
            revision=1,
            text_sha256=None,
            memo_key="original",
        )
        if primary
        else DigestItemMeta(memo_key="original")
    )
    binding = ComputationInputBinding(term=10, number="3039", reuse_key="different")

    with pytest.raises(ValidationError, match="frozen memo key"):
        type(metadata).model_validate(
            {**metadata.model_dump(), "input_binding": binding.model_dump()}
        )

    assert "input_binding" not in metadata.model_dump()
