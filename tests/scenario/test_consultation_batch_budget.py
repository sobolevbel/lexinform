import pytest

from lexinform.consultation_batches import ConsultationInputTooLargeError
from lexinform.models import BatchResult, LlmBatch, LlmBatchItem
from lexinform.services.analysis import Waiting
from tests.harness import World
from tests.scenario.test_document_reviews import context, finding, review


def test_consultation_preflight_refuses_the_plan_before_saving_any_batch_intent() -> None:
    world = World(
        batch=True,
        analysis_model="gpt-6.1-sol",
        batch_kinds=frozenset({"consultation_review"}),
        consultation_reviews_enabled=True,
        max_bill_cost_usd=0.05,
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()

    with pytest.raises(ConsultationInputTooLargeError, match="above its"):
        world.analysis.request_partitioned_consultation(
            world.bill("3039"), context(), requested_at=world.clock.now()
        )

    assert not world.repo.list_queued_batch_intents()
    assert not world.batch.submitted


def test_consultation_reuses_completed_partition_when_rebuilding_the_review() -> None:
    world = World(
        batch=True,
        analysis_model="gpt-6.1-sol",
        batch_kinds=frozenset({"consultation_review"}),
        consultation_reviews_enabled=True,
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    world.batch.secondary_script[("3039", "consultation_review")] = review(finding())
    bill = world.bill("3039")
    source = context()

    waiting = world.analysis.request_partitioned_consultation(
        bill, source, requested_at=world.clock.now()
    )
    assert isinstance(waiting, Waiting)
    world.run()
    world.batch.resolve()
    world.run()
    result = world.analysis.request_partitioned_consultation(
        bill, source, requested_at=world.clock.now()
    )

    assert not isinstance(result, Waiting)
    assert result.review.consultation_findings[0].outcome == "accepted"
    assert result.context == source
    assert len(result.part_computation_ids) == 1
    assert len(world.batch.submitted) == 1
    assert not world.repo.list_queued_batch_intents()


def test_consultation_retries_an_output_limit_only_once_within_the_reservation() -> None:
    world = World(
        batch=True,
        analysis_model="gpt-6.1-sol",
        batch_kinds=frozenset({"consultation_review"}),
        consultation_reviews_enabled=True,
        max_bill_cost_usd=0.21,
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    bill = world.bill("3039")
    source = context()
    now = world.clock.now()
    world.analysis.request_partitioned_consultation(bill, source, requested_at=now)

    def fail_queued(batch_id: str) -> None:
        (intent,) = world.repo.list_queued_batch_intents()
        key = intent.request.custom_id
        world.repo.mark_batch_intents_submitting([key])
        submitted = world.repo.list_submitting_batch_intents()[0]
        batch = LlmBatch(
            batch_id=batch_id,
            provider="openai",
            call_kind="consultation_review",
            submitted_at=now,
            status="ended",
            request_count=1,
            estimated_cost_usd=intent.request.estimated_cost_usd,
        )
        item = LlmBatchItem.from_intent(batch_id, submitted)
        world.repo.save_llm_batch(batch, [item])
        world.repo.delete_batch_intents([key])
        world.repo.mark_llm_batch_item_consumed(
            batch_id,
            key,
            consumed_at=now,
            result=BatchResult(
                custom_id=key, error="response is incomplete", incomplete_reason="max_output_tokens"
            ),
        )

    fail_queued("first")
    world.analysis.request_partitioned_consultation(bill, source, requested_at=now)
    (retry,) = world.repo.list_queued_batch_intents()
    assert retry.request.consultation_retry

    fail_queued("retry")
    with pytest.raises(
        ConsultationInputTooLargeError, match="Every consultation batch part failed"
    ):
        world.analysis.request_partitioned_consultation(bill, source, requested_at=now)
    assert not world.repo.list_queued_batch_intents()
