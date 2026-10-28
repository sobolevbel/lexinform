import datetime as dt

import pytest

from lexinform.models import BatchIntent, DigestItemMeta
from tests.unit.test_batch_payload_identity import request_for

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
