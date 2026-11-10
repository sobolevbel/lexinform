import pytest

from lexinform.consultation_batches import ConsultationInputTooLargeError
from lexinform.models import BatchResult, LlmBatch, LlmBatchItem, ReviewContext
from lexinform.services.analysis import Waiting
from tests.document_review_cases import context, finding, review
from tests.harness import World


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


def test_consultation_synthesis_waits_for_all_parts_and_only_applies_the_selected_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World(
        batch=True,
        analysis_model="gpt-6.1-sol",
        batch_kinds=frozenset({"consultation_review"}),
        consultation_reviews_enabled=True,
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    original = context()
    response = original.documents[0]
    alternative = response.model_copy(update={"identity": "doc/2"})
    source = original.model_copy(update={"documents": (response, alternative)})

    def split(ctx: ReviewContext, term: int, *, input_limit: int) -> tuple[ReviewContext, ...]:
        return tuple(
            ctx.model_copy(
                update={
                    "documents": (doc,),
                    "consultation_partition": f"{ctx.memo_key(term)}/{index}",
                }
            )
            for index, doc in enumerate(ctx.documents, 1)
        )

    monkeypatch.setattr("lexinform.consultation_batches.plan_consultation_parts", split)
    first = finding()
    assert first.evidence is not None, "the fixture cites a source"
    second = first.model_copy(
        update={
            "proposal": "Документы на карту",
            "evidence": first.evidence.model_copy(update={"document_id": "doc/2"}),
        }
    )
    answer = review(first).model_copy(update={"consultation_findings": (first, second)})
    world.batch.secondary_script[("3039", "consultation_review")] = answer
    bill = world.bill("3039")
    now = world.clock.now()

    assert isinstance(
        world.analysis.request_partitioned_consultation(bill, source, requested_at=now), Waiting
    )
    world.run()
    world.batch.resolve()
    world.run()
    assert isinstance(
        world.analysis.request_partitioned_consultation(bill, source, requested_at=now), Waiting
    )
    (synthesis,) = world.repo.list_queued_batch_intents()
    assert isinstance(synthesis.request.ctx, ReviewContext), "the queued review is typed"
    assert [candidate.id for candidate in synthesis.request.ctx.consultation_candidates] == [
        "1",
        "2",
    ]
    world.batch.secondary_script[("3039", "consultation_review")] = answer.model_copy(
        update={"consultation_findings": (), "selected_candidates": ("2",)}
    )
    world.run()
    world.batch.resolve()
    world.run()

    record = world.analysis.request_partitioned_consultation(bill, source, requested_at=now)
    assert not isinstance(record, Waiting)
    assert record.review.consultation_findings[0].proposal == second.proposal
    assert len(record.review.consultation_findings) == 1
    assert len(record.part_computation_ids) == 3
