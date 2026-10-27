import pytest

from lexinform.models import (
    BatchIntent,
    BatchResult,
    DigestItemMeta,
    DocumentReviewRecord,
    LlmBatch,
    LlmBatchItem,
    PublicationKind,
    ReviewContext,
    ReviewInputReference,
    ReviewResultRecord,
)
from lexinform.models.reviews import ReviewPurpose
from lexinform.services.analysis import Waiting
from tests.fakes import FakeLlm
from tests.harness import World
from tests.scenario.test_commission_reviews import context as commission_context
from tests.scenario.test_document_reviews import FINAL, context

PURPOSES: tuple[ReviewPurpose, ...] = (
    "consultation_review",
    "commission_review",
    "act_application",
)


def queued_review(
    purpose: ReviewPurpose, *, model: str = "gpt-5.1", workers: int = 1
) -> tuple[World, ReviewContext, BatchIntent]:
    world = World(
        batch=True,
        workers=workers,
        analysis_model=model,
        batch_kinds=frozenset({purpose}),
        consultation_reviews_enabled=True,
        commission_reviews_enabled=True,
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    world.analysis.start_run()
    source = (
        commission_context().model_copy(update={"number": "3039"})
        if purpose == "commission_review"
        else context(FINAL, role="final_act")
        if purpose == "act_application"
        else context()
    )
    assert isinstance(world.analysis.request_document_review(world.bill("3039"), source), Waiting)
    intent = world.repo.list_queued_batch_intents()[0]
    return world, source, intent


def submit_review(world: World, intent: BatchIntent) -> None:
    if intent.request.call_kind != "act_application":
        world.analysis.submit_queued_batches()
        return
    world.analysis.submit_queued_batches()
    assert not world.batch.submitted, "new act review submissions remain paused"
    batch_id = world.batch.submit([intent.request])
    with world.repo.atomic():
        world.repo.save_llm_batch(
            LlmBatch(
                batch_id=batch_id,
                provider=intent.provider,
                call_kind="act_application",
                submitted_at=world.clock.now(),
                status="submitted",
                request_count=1,
                estimated_cost_usd=intent.request.estimated_cost_usd,
            ),
            [LlmBatchItem.from_intent(batch_id, intent)],
        )
        world.repo.delete_batch_intents([intent.request.custom_id])


@pytest.mark.parametrize("purpose", PURPOSES)
@pytest.mark.parametrize("model", ["gpt-5.1", "claude-opus-5-5"])
@pytest.mark.parametrize("workers", [1, 4])
def test_review_history_retains_paid_output_and_compact_input_across_reuse_and_cleanup(
    purpose: ReviewPurpose, model: str, workers: int
) -> None:
    world, source, intent = queued_review(purpose, model=model, workers=workers)
    before = world.bill("3039")
    submit_review(world, intent)
    world.batch.resolve()

    world.analysis.collect_batches()
    world.analysis.collect_batches()
    world.repo.restore(world.repo.dump())
    world.analysis.start_run()
    reused = world.analysis.request_document_review(world.bill("3039"), source)

    results = world.repo.list_computation_results(10, "3039")
    assert len(results) == 1
    result = results[0]
    assert isinstance(result.record, ReviewResultRecord), (
        "the paid review has a compact normalized answer"
    )
    assert isinstance(reused, DocumentReviewRecord), (
        "memo reuse keeps the detailed consumer contract"
    )
    assert result.record.review == reused.review
    assert result.review_input == ReviewInputReference.from_context(source, term=10)
    assert result.record.computation_id == reused.computation_id == result.result_id
    assert result.record.request_sha256 == result.request_sha256 == intent.request.payload_sha256
    assert result.record.input_tokens == result.usage.batch_input == FakeLlm.ANALYSIS_TOKENS[0]
    assert reused.input_tokens == 0 and result.schema_version == 3 and result.generation is None
    assert result.provider == intent.provider
    assert world.bill("3039").status == before.status
    assert world.bill("3039").analysis_generation == before.analysis_generation
    assert not world.repo.list_result_applications(10, "3039")
    assert "context" not in result.record.model_dump()
    assert result.review_input is not None, "the collected item retains its original input metadata"
    assert all("text" not in document.model_dump() for document in result.review_input.documents)
    world.run(track=False)
    world.clock.advance(days=200)
    world.repo.prune_history(before=world.clock.now(), memo_before=world.clock.now())
    world.repo.restore(world.repo.dump())

    assert world.repo.get_computation_result(result.result_id) == result
    assert not world.repo.load_analysis_memo()
    assert len(world.batch.submitted) == 1
    assert not world.publisher.texts(PublicationKind.ACT_APPLICATION)
    assert not world.publisher.texts(PublicationKind.EFFECTIVE_RULES)


@pytest.mark.parametrize("purpose", PURPOSES)
@pytest.mark.parametrize("known_input", [True, False])
def test_failed_review_keeps_original_usage_and_known_or_unknown_input(
    purpose: ReviewPurpose, known_input: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, source, intent = queued_review(purpose)
    if not known_input:
        assert isinstance(intent.meta, DigestItemMeta), "the pending review uses digest metadata"
        world.repo.delete_batch_intents([intent.request.custom_id])
        intent = intent.model_copy(
            update={"meta": intent.meta.model_copy(update={"review_context": None})}
        )
        world.repo.save_batch_intent(intent)
    submit_review(world, intent)
    world.batch.resolve()

    def failed(batch_id: str, kind: str = "analysis") -> list[BatchResult]:
        return [
            BatchResult(
                custom_id=intent.request.custom_id,
                model="gpt-5.1",
                error="invalid reviewed answer",
                input_tokens=53,
                output_tokens=7,
            )
        ]

    monkeypatch.setattr(world.batch, "fetch_results", failed)

    world.analysis.collect_batches()
    world.repo.restore(world.repo.dump())

    results = world.repo.list_computation_results(10, "3039")
    assert len(results) == 1
    result = results[0]
    assert result.record is None and result.validation == "error" and result.schema_version == 3
    assert result.review_input == (
        ReviewInputReference.from_context(source, term=10) if known_input else None
    )
    assert result.error == "invalid reviewed answer"
    assert result.usage.batch_input == 53 and result.usage.batch_output == 7


@pytest.mark.parametrize(
    "fault", ["save_computation_result", "save_analysis_memo", "mark_llm_batch_item_consumed"]
)
def test_review_result_input_memo_and_consumed_checkpoint_roll_back_together(
    fault: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, source, intent = queued_review("commission_review")
    submit_review(world, intent)
    world.batch.resolve()
    memo = world.repo.load_analysis_memo()

    def crash(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected review checkpoint failure")

    with monkeypatch.context() as patch:
        patch.setattr(world.repo, fault, crash)
        with pytest.raises(RuntimeError, match="injected review"):
            world.analysis.collect_batches()

    assert not world.repo.list_computation_results(10, "3039")
    assert world.repo.load_analysis_memo() == memo
    assert world.repo.list_open_llm_batches()
    world.repo.restore(world.repo.dump())
    world.analysis.start_run()
    world.analysis.collect_batches()

    assert len(world.repo.list_computation_results(10, "3039")) == 1
    assert isinstance(
        world.analysis.request_document_review(world.bill("3039"), source), DocumentReviewRecord
    )
    assert len(world.batch.submitted) == 1
