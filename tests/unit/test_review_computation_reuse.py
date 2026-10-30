import pytest

from lexinform.models import (
    ComputationInputBinding,
    ComputationResult,
    DocumentReviewRecord,
    ReviewContext,
    ReviewDocument,
    ReviewInputReference,
    ReviewResultRecord,
)
from lexinform.services.computation_reuse import matching_review
from tests.computations import retained_result
from tests.scenario.test_commission_reviews import context as commission_context
from tests.scenario.test_document_reviews import context


def paid_review(source: ReviewContext) -> ComputationResult:
    original = retained_result(source.purpose)
    record = original.record
    assert isinstance(record, DocumentReviewRecord), "the review fixture supplies its full context"
    record = record.model_copy(update={"context": source})
    return ComputationResult.model_validate(
        {
            **original.model_dump(),
            "schema_version": 3,
            "record": ReviewResultRecord.from_record(record),
            "review_input": ReviewInputReference.from_context(source, term=10),
            "input_binding": ComputationInputBinding(
                term=10, number=source.number, reuse_key=source.memo_key(10)
            ),
        }
    )


def test_compact_history_uses_only_the_exact_caller_context_and_preserves_paid_record() -> None:
    source = context()
    result = paid_review(source)
    before = result.model_dump_json()

    record = matching_review((result,), 10, source)

    assert record is not None, (
        "the caller supplies the full original context and exact paid binding"
    )
    assert record.context is source
    assert record.computation_id == result.result_id
    assert record.request_sha256 == result.request_sha256
    assert record.prompt_version == "pinned-prompt" and record.input_tokens == 50
    assert result.model_dump_json() == before


@pytest.mark.parametrize(
    "case",
    [
        "schema",
        "validation",
        "owner",
        "binding_unknown",
        "binding_wrong",
        "input_unknown",
        "input_wrong",
        "request_unknown",
        "record_identity",
    ],
)
def test_unknown_or_mismatched_paid_binding_cannot_supply_a_review(case: str) -> None:
    source = context()
    result = paid_review(source)
    if case == "schema":
        result = result.model_copy(update={"schema_version": 2})
    elif case == "validation":
        result = result.model_copy(update={"validation": "error"})
    elif case == "owner":
        result = result.model_copy(update={"term": 11})
    elif case == "binding_unknown":
        result = result.model_copy(update={"input_binding": None})
    elif case == "binding_wrong":
        result = result.model_copy(
            update={
                "input_binding": ComputationInputBinding(
                    term=10, number="other", reuse_key=source.memo_key(10)
                )
            }
        )
    elif case == "input_unknown":
        result = result.model_copy(update={"review_input": None})
    elif case == "input_wrong":
        reference = ReviewInputReference.from_context(source, term=10)
        result = result.model_copy(
            update={"review_input": reference.model_copy(update={"comparison_sha256": "f" * 64})}
        )
    elif case == "request_unknown":
        result = result.model_copy(update={"request_sha256": None})
    else:
        record = result.record
        assert isinstance(record, ReviewResultRecord), "the fixture uses the current compact schema"
        result = result.model_copy(
            update={"record": record.model_copy(update={"computation_id": "result-v1:" + "f" * 64})}
        )

    assert matching_review((result,), 10, source) is None


@pytest.mark.parametrize("change", ["text", "identity", "comparison", "term", "title"])
def test_caller_changes_cannot_be_hidden_by_a_shared_logical_key(change: str) -> None:
    source = context()
    result = paid_review(source)
    term = 10
    if change in ("text", "identity"):
        document = source.documents[0].model_copy(
            update={"text": source.documents[0].text + " ZMIANA"}
            if change == "text"
            else {"identity": "another-source"}
        )
        current = source.model_copy(update={"documents": (document,)})
        assert current.memo_key(10) == source.memo_key(10)
    elif change == "comparison":
        current = source.model_copy(update={"comparison_sha256": "f" * 64})
    elif change == "title":
        current = source.model_copy(update={"title": "A different live Bill title"})
        assert current.memo_key(10) == source.memo_key(10)
    else:
        current, term = source, 11

    assert matching_review((result,), term, current) is None


@pytest.mark.parametrize(
    "update",
    [
        {"text": "a hidden nonempty body"},
        {"sha256": "f" * 64},
        {"normalized_sha256": "f" * 64},
        {"complete": True},
        {"file_sha256": "f" * 64},
    ],
)
def test_missing_input_exception_does_not_accept_forged_known_or_partial_hashes(
    update: dict[str, object],
) -> None:
    missing = ReviewDocument.model_validate(
        {
            "identity": "missing",
            "url": "https://example.test/unavailable",
            "role": "comparison_text",
            "text": "",
            "sha256": "",
            "normalized_sha256": "",
            "complete": False,
            **update,
        }
    )
    source = commission_context().model_copy(
        update={"number": "3039", "documents": (*commission_context().documents, missing)}
    )
    assert matching_review((paid_review(source),), 10, source) is None


def test_missing_input_sentinel_is_not_inferred_for_another_review_producer() -> None:
    missing = ReviewDocument(
        identity="missing",
        url="https://example.test/unavailable",
        role="response",
        text="",
        sha256="",
        normalized_sha256="",
        complete=False,
    )
    source = context().model_copy(update={"documents": (missing,)})
    assert matching_review((paid_review(source),), 10, source) is None
