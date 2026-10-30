import datetime as dt
from typing import Literal

import pytest

from lexinform.models import BatchIntent, BatchItemMeta, DigestItemMeta, LlmBatchItem
from tests.unit.test_batch_payload_identity import request_for
from tests.unit.test_input_receipts import meta, request

NOW = dt.datetime(2026, 10, 27, tzinfo=dt.UTC)


def test_new_intent_preserves_request_and_plain_legacy_constructor_keeps_unknown_scope() -> None:
    request = request_for()
    meta = DigestItemMeta(memo_key="recorded-question")

    fresh = BatchIntent.from_request(request=request, meta=meta, provider="openai", created_at=NOW)
    legacy = BatchIntent(request=request, meta=meta, provider="openai", created_at=NOW)

    binding = fresh.meta.input_binding
    assert binding is not None, "the new-intent factory freezes the known original scope"
    assert (binding.term, binding.number, binding.reuse_key) == (10, "3039", meta.memo_key)
    assert fresh.request == legacy.request == request
    assert legacy.meta.input_binding is None and "input_binding" not in legacy.meta.model_dump()
    assert BatchIntent.model_validate_json(fresh.model_dump_json()) == fresh
    assert BatchIntent.model_validate_json(legacy.model_dump_json()) == legacy


def test_an_existing_binding_cannot_be_frozen_again_for_a_different_receiver() -> None:
    request = request_for()
    original = BatchIntent.from_request(
        request=request,
        meta=DigestItemMeta(memo_key="recorded-question"),
        provider="openai",
        created_at=NOW,
    )

    with pytest.raises(ValueError, match="cannot rebind"):
        BatchIntent.from_request(
            request=request.model_copy(update={"number": "3040"}),
            meta=original.meta,
            provider="openai",
            created_at=NOW,
        )


def test_a_retargeted_legacy_request_cannot_claim_a_new_original_subject() -> None:
    with pytest.raises(ValueError, match="question subject"):
        BatchIntent.from_request(
            request=request_for().model_copy(update={"number": "3040"}),
            meta=DigestItemMeta(memo_key="recorded-question"),
            provider="openai",
            created_at=NOW,
        )


def test_new_input_binding_requires_a_recorded_question_key() -> None:
    with pytest.raises(ValueError, match="recorded question key"):
        BatchIntent.from_request(
            request=request_for(),
            meta=DigestItemMeta(memo_key=""),
            provider="openai",
            created_at=NOW,
        )


@pytest.mark.parametrize("kind", ["analysis", "reanalysis"])
def test_new_primary_intent_freezes_the_supplied_queue_time_without_changing_request(
    kind: Literal["analysis", "reanalysis"],
) -> None:
    frozen = request()
    frozen = frozen.model_copy(
        update={"question": frozen.question.model_copy(update={"kind": kind})}
    )
    source = meta()
    old_meta = source.model_dump_json()
    old_request = frozen.model_dump_json()
    created = NOW + dt.timedelta(hours=7)

    intent = BatchIntent.from_request(
        request=frozen, meta=source, provider="openai", created_at=created
    )

    assert isinstance(intent.meta, BatchItemMeta), (
        "primary intent creation preserves primary metadata"
    )
    assert intent.meta.queued_at == intent.created_at == created
    assert intent.meta.memo_key == source.memo_key and source.model_dump_json() == old_meta
    assert intent.request.model_dump_json() == frozen.model_dump_json() == old_request
    assert intent.request.payload_sha256 == frozen.payload_sha256
    item = LlmBatchItem.from_intent("original-batch", intent)
    assert item.meta.queued_at == intent.meta.queued_at
    assert item.meta.model_dump(exclude={"request_sha256"}) == intent.meta.model_dump(
        exclude={"request_sha256"}
    )
    assert item.meta.request_sha256 == frozen.payload_sha256
    assert BatchIntent.model_validate_json(intent.model_dump_json()) == intent


def test_primary_intent_accepts_an_equal_stamp_and_rejects_conflicting_or_naive_time() -> None:
    equal = meta().model_copy(update={"queued_at": NOW})
    intent = BatchIntent.from_request(
        request=request(), meta=equal, provider="openai", created_at=NOW
    )
    assert intent.meta.queued_at == NOW
    with pytest.raises(ValueError, match="original intent time"):
        BatchIntent.from_request(
            request=request(),
            meta=equal,
            provider="openai",
            created_at=NOW + dt.timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="aware queue time"):
        BatchIntent.from_request(
            request=request(), meta=meta(), provider="openai", created_at=NOW.replace(tzinfo=None)
        )


def test_capture_does_not_stamp_other_primary_question_kinds() -> None:
    frozen = request()
    frozen = frozen.model_copy(
        update={"question": frozen.question.model_copy(update={"kind": "explanation"})}
    )
    intent = BatchIntent.from_request(
        request=frozen, meta=meta(), provider="openai", created_at=NOW
    )
    assert intent.meta.queued_at is None
    assert "queued_at" not in intent.meta.model_dump_json()
